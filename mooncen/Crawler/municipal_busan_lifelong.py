"""Fail-closed collector for the Busan Lifelong Learning Platform.

The platform is a federation rather than a single institution.  Its office
directory currently contains 35 institutions.  Twenty-two entries belong to
Busan or one of its districts/counties: one remains in this shared owner and
twenty-one are atomically assigned to dedicated district collectors.  Thirteen
universities remain outside the municipal ownership boundary.

The legacy collector fetched a fixed number of pages for every office and
published the resulting partial union (historically 740 rows).  This module
instead enumerates the authoritative ``l_search_ch=0`` archive for every
owned office, validates the displayed last page, the immediate post-last
sentinel, the complete descending source sequence, page signatures, and a
stable boundary recheck.  Only courses whose education end date is current or
future are enriched from their detail page and returned.  Any failed contract
causes an empty result.

No phone number, instructor name, email address, or unfiltered source payload
is persisted.  An application URL is emitted only when the verified detail
page contains an actual application control.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


BUSAN_LIFELONG_PROVIDER = "MUNI_LLL_BUSAN_GO_KR_944C621B"
BUSAN_LIFELONG_HOST = "lll.busan.go.kr"
BUSAN_LIFELONG_OFFICE_PATH = "/yeyak/ilms/learning/officeList.do"
BUSAN_LIFELONG_LIST_PATH = "/yeyak/ilms/learning/learningList.do"
BUSAN_LIFELONG_DETAIL_PATH = "/yeyak/ilms/learning/learningDetail.do"
BUSAN_LIFELONG_URL = (
    f"https://{BUSAN_LIFELONG_HOST}{BUSAN_LIFELONG_OFFICE_PATH}"
)
BUSAN_LIFELONG_LIST_URL = (
    f"https://{BUSAN_LIFELONG_HOST}{BUSAN_LIFELONG_LIST_PATH}"
)
BUSAN_LIFELONG_PAGE_SIZE = 100
BUSAN_LIFELONG_MAX_WORKERS = 4
BUSAN_LIFELONG_FETCH_ATTEMPTS = 2
BUSAN_LIFELONG_LEGACY_PARTIAL_COUNT = 740
BUSAN_LIFELONG_PARSER = (
    "busan_lifelong_office35_exact+municipal1+junggu_seogu_yeongdo_namgu_donggu_dongnae_busanjin_bukgu_saha_gangseo_haeundae_geumjeong_sasang_yeonje_suyeong_gijang_offices_duplicate_suppressed+all_archive_complete_pages+"
    "sentinel+stable_boundary_recheck+current_detail+pii_allowlist"
)

BUSAN_LIFELONG_OWNERSHIP_SCOPE = (
    "busan_city_office_except_all_dedicated_district_and_county_owners_current_future"
)
BUSAN_LIFELONG_LEGACY_HTTP_HOST = "www.saha.go.kr"
BUSAN_LIFELONG_LEGACY_HTTP_PATH = "/edu/lecture/view.do"
BUSAN_LIFELONG_LEGACY_HTTP_MID = "0201010000"


@dataclass(frozen=True)
class BusanOffice:
    code: str
    name: str
    municipality_code: str = ""
    municipality_name: str = ""
    ownership: str = "excluded_university"

    @property
    def owned(self) -> bool:
        return bool(self.municipality_code)


def _owned(
    code: str, name: str, municipality_code: str, municipality_name: str
) -> BusanOffice:
    return BusanOffice(
        code,
        name,
        municipality_code,
        municipality_name,
        "municipal",
    )


# Order is the official selector order observed at the canonical office page.
# An addition/removal/rename is a material ownership change and therefore must
# be reviewed instead of silently entering or leaving the production snapshot.
BUSAN_LIFELONG_EXPECTED_OFFICES: tuple[BusanOffice, ...] = (
    BusanOffice(
        "OFFICE_00002686",
        "강서구청",
        ownership="duplicate_dedicated_gangseo_owner",
    ),
    BusanOffice("OFFICE_00002760", "거제1동 행정복지센터", ownership="duplicate_dedicated_yeonje_owner"),
    BusanOffice("OFFICE_00002910", "거제2동 행정복지센터", ownership="duplicate_dedicated_yeonje_owner"),
    BusanOffice("OFFICE_00002770", "거제4동 행정복지센터", ownership="duplicate_dedicated_yeonje_owner"),
    BusanOffice("OFFICE_00002832", "경남정보대학교"),
    BusanOffice("OFFICE_00002871", "경성대학교"),
    BusanOffice(
        "OFFICE_00002660",
        "금정구청",
        ownership="duplicate_dedicated_geumjeong_owner",
    ),
    BusanOffice(
        "OFFICE_00002631",
        "기장군청",
        ownership="duplicate_dedicated_gijang_owner",
    ),
    BusanOffice(
        "OFFICE_00002634",
        "남구청",
        ownership="duplicate_dedicated_namgu_owner",
    ),
    BusanOffice(
        "OFFICE_00002642",
        "동구청",
        ownership="duplicate_dedicated_donggu_owner",
    ),
    BusanOffice(
        "OFFICE_00002682",
        "동래구청",
        ownership="duplicate_dedicated_dongnae_owner",
    ),
    BusanOffice("OFFICE_00002880", "동명대학교"),
    BusanOffice("OFFICE_00002820", "동서대학교"),
    BusanOffice("OFFICE_00002861", "동의대학교"),
    BusanOffice("OFFICE_00002860", "부산경상대학교"),
    BusanOffice("OFFICE_00002830", "부산과학기술대학교"),
    BusanOffice("OFFICE_00002831", "부산대학교"),
    BusanOffice("OFFICE_00002900", "부산디지털대학교 평생교육원"),
    _owned(
        "OFFICE_00002731",
        "부산여성가족과 평생교육진흥원",
        "2600000000",
        "부산광역시",
    ),
    BusanOffice("OFFICE_00002840", "부산예술대학교"),
    BusanOffice("OFFICE_00002870", "부산외국어대학교"),
    BusanOffice(
        "OFFICE_00002710",
        "부산진구청",
        ownership="duplicate_dedicated_busanjin_owner",
    ),
    BusanOffice(
        "OFFICE_00002650",
        "북구청",
        ownership="duplicate_dedicated_bukgu_owner",
    ),
    BusanOffice(
        "OFFICE_00002800",
        "북구평생학습관",
        ownership="duplicate_dedicated_bukgu_owner",
    ),
    BusanOffice("OFFICE_00002633", "사상구청", ownership="duplicate_dedicated_sasang_owner"),
    BusanOffice(
        "OFFICE_00002632",
        "사하구청",
        ownership="duplicate_dedicated_saha_owner",
    ),
    BusanOffice(
        "OFFICE_00002641",
        "서구청",
        ownership="duplicate_dedicated_seogu_owner",
    ),
    BusanOffice(
        "OFFICE_00002661",
        "수영구청",
        ownership="duplicate_dedicated_suyeong_owner",
    ),
    BusanOffice("OFFICE_00002850", "신라대학교"),
    BusanOffice("OFFICE_00002670", "연제구청", ownership="duplicate_dedicated_yeonje_owner"),
    BusanOffice(
        "OFFICE_00002680",
        "영도구청",
        ownership="duplicate_dedicated_yeongdo_owner",
    ),
    BusanOffice("OFFICE_00002740", "영산대학교 부산RISE사업단"),
    BusanOffice(
        "OFFICE_00002681",
        "중구청",
        ownership="duplicate_dedicated_junggu_owner",
    ),
    BusanOffice(
        "OFFICE_00002790",
        "하단2동 행정복지센터",
        ownership="duplicate_dedicated_saha_owner",
    ),
    BusanOffice(
        "OFFICE_00002635",
        "해운대구청",
        ownership="duplicate_dedicated_haeundae_owner",
    ),
)

BUSAN_LIFELONG_OWNED_OFFICES = tuple(
    office for office in BUSAN_LIFELONG_EXPECTED_OFFICES if office.owned
)
BUSAN_LIFELONG_EXCLUDED_OFFICES = tuple(
    office for office in BUSAN_LIFELONG_EXPECTED_OFFICES if not office.owned
)
BUSAN_LIFELONG_OFFICE_BY_CODE = {
    office.code: office for office in BUSAN_LIFELONG_EXPECTED_OFFICES
}
BUSAN_LIFELONG_DUPLICATE_OWNER_REASONS = {
    "duplicate_dedicated_junggu_owner": (
        "identity-equivalent duplicate of dedicated provider "
        "MUNI_WWW_BSJUNGGU_GO_KR_C443BFF0 BBS_0000078"
    ),
    "duplicate_dedicated_seogu_owner": (
        "identity-equivalent duplicate of dedicated provider "
        "MUNI_WWW_BSSEOGU_GO_KR_AACF30BC el_code ledger"
    ),
    "duplicate_dedicated_yeongdo_owner": (
        "native LEARNING rows and identity-equivalent external duplicates are "
        "atomically owned by dedicated provider MUNI_WWW_YEONGDO_GO_KR_33400564"
    ),
    "duplicate_dedicated_namgu_owner": (
        "native LEARNING rows and the audited external test row are atomically "
        "owned by dedicated provider MUNI_WWW_BSNAMGU_GO_KR_664BF631"
    ),
    "duplicate_dedicated_dongnae_owner": (
        "native LEARNING rows and identity-equivalent external docNo duplicates "
        "are atomically owned by dedicated provider MUNI_WWW_DONGNAE_GO_KR_742D8C71"
    ),
    "duplicate_dedicated_busanjin_owner": (
        "all identity-equivalent external idx rows are atomically owned by dedicated "
        "provider MUNI_WWW_BUSANJIN_GO_KR_5881F59A"
    ),
    "duplicate_dedicated_bukgu_owner": (
        "native LEARNING rows and identity-equivalent external programIdx duplicates "
        "are atomically owned by dedicated provider MUNI_WWW_BSBUKGU_GO_KR_E60701D6"
    ),
    "duplicate_dedicated_donggu_owner": (
        "native LEARNING rows and identity-equivalent external data_Sid duplicates "
        "are atomically owned by dedicated provider BUSAN_DONGGU_RESERVATION"
    ),
    "duplicate_dedicated_saha_owner": (
        "native LEARNING rows and identity-equivalent external seq duplicates are "
        "atomically owned by dedicated provider MUNI_WWW_SAHA_GO_KR_ED7CDFC9"
    ),
    "duplicate_dedicated_gangseo_owner": (
        "all identity-equivalent external idx rows and future native rows are "
        "atomically owned by dedicated provider MUNI_LLL_BSGANGSEO_GO_KR_0691B6EB"
    ),
    "duplicate_dedicated_haeundae_owner": (
        "all identity-equivalent external res_no rows and future native rows are "
        "atomically owned by dedicated provider MUNI_WWW_HAEUNDAE_GO_KR_E2AD27FA"
    ),
    "duplicate_dedicated_geumjeong_owner": (
        "the complete Geumjeong office archive is atomically owned by dedicated "
        "provider MUNI_RESERVE_BUSAN_GO_KR_2CB22A99"
    ),
    "duplicate_dedicated_sasang_owner": (
        "native LEARNING rows and exact external couIdx republications are "
        "atomically owned by dedicated provider SASANG_RESERVATION"
    ),
    "duplicate_dedicated_yeonje_owner": (
        "native LEARNING rows and exact external lecIdx/list-only republications "
        "across all four offices are atomically owned by dedicated provider "
        "MUNI_WWW_YEONJE_GO_KR_73BA35A2"
    ),
    "duplicate_dedicated_suyeong_owner": (
        "native LEARNING rows and exact external dataSid republications are "
        "atomically owned by dedicated provider "
        "MUNI_WWW_SUYEONG_GO_KR_41E9DDEB"
    ),
    "duplicate_dedicated_gijang_owner": (
        "native LEARNING rows and exact external idx republications are "
        "atomically owned by dedicated provider "
        "MUNI_WWW_GIJANG_GO_KR_592C4B5E"
    ),
}


def _covered_municipalities() -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for office in BUSAN_LIFELONG_OWNED_OFFICES:
        if office.municipality_code in seen:
            continue
        seen.add(office.municipality_code)
        if office.municipality_code == "2600000000":
            sigungu = ""
        else:
            sigungu = office.municipality_name.removeprefix("부산광역시 ")
        result.append(
            {
                "code": office.municipality_code,
                "sido": "부산광역시",
                "sigungu": sigungu,
                "full_name": office.municipality_name,
            }
        )
    return tuple(result)


BUSAN_LIFELONG_COVERED_MUNICIPALITIES = _covered_municipalities()


@dataclass(frozen=True)
class BusanOwnershipAlias:
    provider: str
    url: str
    relationship: str


# These configured sources overlap the federated platform.  Root-level target
# policy decides which entries to disable; the collector exposes exact evidence
# so the decision does not depend on fuzzy title matching.
BUSAN_LIFELONG_OWNERSHIP_ALIASES: tuple[BusanOwnershipAlias, ...] = (
    BusanOwnershipAlias(
        "BUSAN_LIFELONG_PLATFORM",
        "https://lll.busan.go.kr",
        "generic landing-page scrape; superseded by the canonical office federation",
    ),
    BusanOwnershipAlias(
        "MUNI_WWW_GIJANG_GO_KR_592C4B5E",
        "https://www.gijang.go.kr/lll/index.gijang?menuCd=DOM_000000702008000000",
        "member catalogue represented by the 기장군청 office feed",
    ),
    BusanOwnershipAlias(
        "MUNI_WWW_BSBUKGU_GO_KR_2BDDF955",
        "https://www.bsbukgu.go.kr/reservation/index.bsbukgu?menuCd=DOM_000001801003000000",
        "member catalogue represented by the 북구청 office feed",
    ),
    BusanOwnershipAlias(
        "MUNI_WWW_BSBUKGU_GO_KR_141AA5C4",
        "https://www.bsbukgu.go.kr/reservation/index.bsbukgu?menuCd=DOM_000001801002000000&mode=view&programIdx=2142",
        "fixed member-course detail already represented by the 북구청 office feed",
    ),
    BusanOwnershipAlias(
        "MUNI_WWW_BSSEOGU_GO_KR_AACF30BC",
        "https://www.bsseogu.go.kr/edu/index.bsseogu",
        "member catalogue represented by the 서구청 office feed",
    ),
    BusanOwnershipAlias(
        "MUNI_WWW_SAHA_GO_KR_ED7CDFC9",
        "https://www.saha.go.kr/edu/main.do",
        "deprecated member catalogue represented by the 사하구청 office feed",
    ),
)


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"LEARNING_[A-Za-z0-9_-]+\Z")
_INTERNAL_ONCLICK_RE = re.compile(
    r"fn_learning_detail\(\s*['\"](LEARNING_[A-Za-z0-9_-]+)['\"]\s*\)"
)
_PAGE_RE = re.compile(r"(?:pageIndex=|fn_list\(\s*)(\d+)")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2}|\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PII_KEY_PARTS = (
    "phone",
    "telephone",
    "telno",
    "mobile",
    "moblphon",
    "email",
    "instructor",
    "teacher",
    "강사",
    "전화",
)
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "대기": "SCHEDULED",
    "대기접수": "SCHEDULED",
    "접수종료": "CLOSED",
    "마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
    "교육완료": "CLOSED",
    "폐강": "CANCELLED",
    # A small number of historical federation rows publish an empty status
    # span.  Their education dates still participate in the complete archive
    # and current rows remain subject to detail verification.
    "": "CLOSED",
}
_APPLICATION_LABELS = frozenset(
    {
        "신청",
        "신청하기",
        "접수하기",
        "수강신청",
        "온라인신청",
        "일반모집신청",
        "대기자신청",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return "".join(ch.lower() for ch in _clean(value) if ch.isalnum())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_busan_lifelong_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == BUSAN_LIFELONG_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == BUSAN_LIFELONG_HOST
        and parsed.port is None
        and parsed.path == BUSAN_LIFELONG_OFFICE_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_busan_lifelong_target


def busan_lifelong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return (
        f"https://{BUSAN_LIFELONG_HOST}{BUSAN_LIFELONG_DETAIL_PATH}?"
        + urlencode({"lng_id": value})
    )


def _list_payload(office_code: str, page: int) -> dict[str, str]:
    return {
        "display_type": "2",
        "pageUnit": str(BUSAN_LIFELONG_PAGE_SIZE),
        "l_search_ch": "0",
        "inst_id": office_code,
        "pageIndex": str(page),
    }


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


def _response_soup(
    response: Any,
    *,
    expected_host: str = "",
    expected_path: str = "",
    allow_redirects: bool = False,
) -> tuple[BeautifulSoup, str]:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if not allow_redirects and getattr(response, "history", None):
        raise ValueError("HTTP redirects are not accepted for source pages")
    final_url = _clean(getattr(response, "url", ""))
    if final_url:
        parsed = urlparse(final_url)
        if parsed.scheme.lower() != "https" or parsed.username or parsed.password:
            raise ValueError("unsafe final response URL")
        if expected_host and (parsed.hostname or "").rstrip(".").lower() != expected_host:
            raise ValueError("response host changed")
        if expected_path and parsed.path != expected_path:
            raise ValueError("response path changed")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    return BeautifulSoup(content, "lxml"), final_url


def _request_soup(
    current: Any,
    method: str,
    url: str,
    *,
    timeout: int,
    data: Optional[Mapping[str, str]] = None,
    expected_host: str = "",
    expected_path: str = "",
    allow_redirects: bool = False,
) -> tuple[BeautifulSoup, str]:
    messages: list[str] = []
    for attempt in range(1, BUSAN_LIFELONG_FETCH_ATTEMPTS + 1):
        try:
            if method == "POST":
                response = current.post(url, data=dict(data or {}), timeout=timeout)
            else:
                response = current.get(url, timeout=timeout)
            return _response_soup(
                response,
                expected_host=expected_host,
                expected_path=expected_path,
                allow_redirects=allow_redirects,
            )
        except Exception as exc:
            messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
    raise ValueError("; ".join(messages))


def _office_contract(soup: BeautifulSoup) -> tuple[list[BusanOffice], list[str]]:
    errors: list[str] = []
    options: list[tuple[str, str]] = []
    for option in soup.select("#o_search_ch option[value]"):
        code = _clean(option.get("value"))
        if not code:
            continue
        options.append((code, _clean(option.get_text(" ", strip=True))))
    expected = [(office.code, office.name) for office in BUSAN_LIFELONG_EXPECTED_OFFICES]
    if options != expected:
        errors.append(
            f"office selector drift: expected {len(expected)} exact entries, got {len(options)}"
        )
    if len({code for code, _ in options}) != len(options):
        errors.append("office selector contains duplicate codes")

    # The status cards are a subset (offices with a published reception state).
    # Validate their identities but intentionally discard phone text.
    card_codes: list[str] = []
    for link in soup.select("a[onclick*='fn_learning_list']"):
        match = re.search(
            r"fn_learning_list\(\s*['\"]([^'\"]+)['\"]\s*\)",
            _clean(link.get("onclick")),
        )
        if not match:
            errors.append("malformed office status-card action")
            continue
        code = _clean(match.group(1))
        if code not in BUSAN_LIFELONG_OFFICE_BY_CODE:
            errors.append(f"unknown office status-card code {code}")
        card_codes.append(code)
    if len(card_codes) != len(set(card_codes)):
        errors.append("duplicate office status-card code")
    return list(BUSAN_LIFELONG_EXPECTED_OFFICES), errors


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year_raw, month_raw, day_raw in _DATE_RE.findall(_clean(value)):
        year = int(year_raw)
        if year < 100:
            year += 2000
        try:
            result.append(date(year, int(month_raw), int(day_raw)))
        except ValueError:
            return []
    return result


def _node_text_without(node: Any, selectors: tuple[str, ...]) -> str:
    if node is None:
        return ""
    clone = BeautifulSoup(str(node), "lxml")
    for selector in selectors:
        for part in clone.select(selector):
            part.extract()
    return _clean(clone.get_text(" ", strip=True))


def _safe_external_url(value: Any) -> str:
    raw = _clean(value)
    parsed = urlparse(raw)
    host = (parsed.hostname or "").rstrip(".").lower()
    scheme = parsed.scheme.lower()
    if not host or parsed.username or parsed.password or parsed.fragment or parsed.params:
        return ""
    if scheme == "http":
        # Audited 2026-07-20: exactly ten ended Saha rows expose this legacy
        # route, all with mId=0201010000 and a numeric course seq.  The same URL
        # responds 200 over HTTPS without a redirect.  No other cleartext
        # source is eligible for upgrading.
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            host != BUSAN_LIFELONG_LEGACY_HTTP_HOST
            or parsed.port not in (None, 80)
            or parsed.path != BUSAN_LIFELONG_LEGACY_HTTP_PATH
            or set(query) != {"mId", "seq"}
            or query.get("mId") != [BUSAN_LIFELONG_LEGACY_HTTP_MID]
            or len(query.get("seq", [])) != 1
            or not _clean(query["seq"][0]).isdigit()
        ):
            return ""
    elif (
        scheme != "https"
        or parsed.port not in (None, 443)
        or not (host.endswith(".go.kr") or host == "go.kr")
    ):
        return ""
    return urlunparse(
        (
            "https",
            host,
            parsed.path or "/",
            "",
            parsed.query,
            "",
        )
    )


def _advertised_last(soup: BeautifulSoup) -> tuple[int, list[str]]:
    values: set[int] = set()
    for link in soup.select("a.page_nextend"):
        text = " ".join(
            part
            for part in (_clean(link.get("href")), _clean(link.get("onclick")))
            if part
        )
        match = _PAGE_RE.search(text)
        if match:
            values.add(int(match.group(1)))
    if len(values) != 1:
        return 0, ["missing or ambiguous displayed last page"]
    value = values.pop()
    if value < 1:
        return 0, ["displayed last page is less than one"]
    return value, []


def _form_errors(soup: BeautifulSoup, office: BusanOffice, page: int) -> list[str]:
    errors: list[str] = []
    forms = soup.select("form#learningVO")
    if len(forms) != 1:
        return ["expected one learningVO list form"]
    form = forms[0]
    action = urlparse(_clean(form.get("action"))).path
    if _clean(form.get("method")).lower() != "post" or action != BUSAN_LIFELONG_LIST_PATH:
        errors.append("unexpected list form method/action")
    required = {
        "inst_id": office.code,
        "display_type": "2",
        "pageIndex": str(page),
        "l_search_ch": "0",
    }
    for name, expected in required.items():
        field = form.select_one(f"[name='{name}']")
        if field is None or _clean(field.get("value")) != expected:
            errors.append(f"list form {name} mismatch")
    selected_office = form.select_one("#o_search_ch option[selected]")
    if selected_office is None or _clean(selected_office.get("value")) != office.code:
        errors.append("list form selected office mismatch")
    selected_state = form.select_one("#learning_state option[selected]")
    if selected_state is None or _clean(selected_state.get("value")) != "0":
        errors.append("list form is not the all-status partition")
    return errors


def _provider_course_id(identity: str) -> str:
    if _IDENTITY_RE.fullmatch(identity):
        token = identity
    else:
        token = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"{BUSAN_LIFELONG_PROVIDER}:course:{token}"


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    office: BusanOffice,
    page: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    # Empty responses contain an additional mobile placeholder table without
    # headers.  The canonical source table is the unique seven-column table.
    tables = [
        table for table in soup.select("table") if len(table.select("thead th")) == 7
    ]
    if len(tables) != 1:
        return [], [f"{office.code} page {page}: expected one course table"]
    table = tables[0]
    headings = [_clean(node.get_text(" ", strip=True)) for node in table.select("thead th")]
    required_heading_tokens = ("번호", "강좌명", "재료비", "교육기간", "신청기간", "상태", "보기")
    if len(headings) != 7 or any(
        token not in headings[index]
        for index, token in enumerate(required_heading_tokens)
    ):
        errors.append(f"{office.code} page {page}: unexpected table headers")

    body_rows = table.select("tbody tr") or [
        row for row in table.select("tr") if row.select("td")
    ]
    for source_row in body_rows:
        cells = source_row.select("td")
        title_link = source_row.select_one("td.subject a")
        if title_link is None:
            empty_text = _clean(source_row.get_text(" ", strip=True))
            if not empty_text or "등록" in empty_text and "없" in empty_text:
                continue
            errors.append(f"{office.code} page {page}: non-course table row")
            continue
        if len(cells) != 7:
            errors.append(f"{office.code} page {page}: course row does not have seven cells")
            continue

        sequence_raw = _clean(cells[0].get_text(" ", strip=True)).replace(",", "")
        title_node = title_link.select_one(".tit")
        office_node = title_link.select_one(".org")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        source_office = _clean(office_node.get_text(" ", strip=True) if office_node else "")
        row_errors: list[str] = []
        if not sequence_raw.isdigit() or int(sequence_raw) < 1:
            row_errors.append("malformed list sequence")
        if not title:
            row_errors.append("empty title")
        if source_office != office.name:
            row_errors.append(
                f"source office mismatch ({source_office!r} != {office.name!r})"
            )

        onclick = _clean(title_link.get("onclick"))
        internal = _INTERNAL_ONCLICK_RE.search(onclick)
        identity = ""
        raw_url = ""
        identity_kind = ""
        if internal:
            identity = _clean(internal.group(1))
            raw_url = busan_lifelong_detail_url(identity)
            identity_kind = "internal"
        else:
            source_href = _clean(title_link.get("href"))
            raw_url = _safe_external_url(source_href)
            if raw_url:
                identity = raw_url
                identity_kind = "external"
            elif not source_href and not onclick:
                identity_kind = "list_only_semantic_v1"
            else:
                row_errors.append("unsafe external detail URL")

        period_node = cells[3].select_one(".s_type.blue")
        period_text = _node_text_without(period_node, ("em.hidden", "pre"))
        period_dates = _dates(period_text)
        reversed_period = bool(
            len(period_dates) == 2 and period_dates[1] < period_dates[0]
        )
        if len(period_dates) != 2:
            row_errors.append("invalid education period")
        schedule_node = cells[3].select_one("pre")
        schedule = _clean(schedule_node.get_text(" ", strip=True) if schedule_node else "")

        apply_node = cells[4].select_one(".s_type.red1")
        apply_text = _node_text_without(apply_node, ("em.hidden",))
        apply_dates = _dates(apply_text)
        if apply_text and len(apply_dates) not in (0, 2):
            row_errors.append("ambiguous application period")
        reversed_apply_period = bool(
            len(apply_dates) == 2 and apply_dates[1] < apply_dates[0]
        )

        fee_parts = [
            _clean(node.get_text(" ", strip=True)) for node in cells[2].select("span")
        ]
        fee = next((value for value in fee_parts if value), "")
        if not fee:
            row_errors.append("empty fee")
        capacity_node = cells[4].select_one(".s_type.indigo1")
        capacity = _node_text_without(capacity_node, ("em.hidden",))
        if not capacity:
            row_errors.append("empty capacity")

        status_values = [
            _clean(node.get_text(" ", strip=True)) for node in cells[5].select(".s_btn")
        ]
        status_values = [value for value in status_values if value]
        source_status = status_values[0] if status_values else ""
        if source_status not in _STATUS_MAP:
            row_errors.append(f"unknown source status {source_status!r}")
        selection_node = cells[5].select_one(".s_type2 em.hidden")
        selection_method = _clean(
            selection_node.get_text(" ", strip=True) if selection_node else ""
        )

        action = cells[6].select_one("a, button, input[type='submit'], input[type='button']")
        action_label = _clean(
            action.get_text(" ", strip=True) if action is not None else ""
        )
        if action is not None and not action_label:
            action_label = _clean(action.get("value"))
        list_application_control = action_label in {"수강신청", "신청하기"}

        if row_errors:
            errors.extend(
                f"{office.code} page {page} row {sequence_raw or '?'}: {message}"
                for message in row_errors
            )
            continue

        source_start, source_end = period_dates
        start, end = sorted((source_start, source_end))
        normalized_apply_dates = sorted(apply_dates) if len(apply_dates) == 2 else []
        apply_start = (
            normalized_apply_dates[0].isoformat() if normalized_apply_dates else ""
        )
        apply_end = (
            normalized_apply_dates[1].isoformat() if normalized_apply_dates else ""
        )
        if identity_kind == "list_only_semantic_v1":
            # The audited Yeonje rows have no hidden/data/onclick identity and
            # no detail route in either display mode.  The immutable list
            # contract below deliberately excludes the shifting display number.
            semantic_fields = (
                office.code,
                title,
                start.isoformat(),
                end.isoformat(),
                apply_start,
                apply_end,
                schedule,
            )
            if not all(semantic_fields[:4]):
                errors.append(
                    f"{office.code} page {page} row {sequence_raw}: "
                    "list-only course lacks stable identity fields"
                )
                continue
            digest = hashlib.sha256(
                "\x1f".join(semantic_fields).encode("utf-8")
            ).hexdigest()[:32]
            identity = f"LIST_ONLY_V1:{digest}"
            raw_url = f"{BUSAN_LIFELONG_LIST_URL}?" + urlencode(
                {"inst_id": office.code, "pageIndex": page}
            )
        sigungu = office.municipality_name.removeprefix("부산광역시 ")
        if office.municipality_code == "2600000000":
            sigungu = ""
        row: dict[str, Any] = {
            "provider": BUSAN_LIFELONG_PROVIDER,
            "provider_course_id": _provider_course_id(identity),
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "branch": office.municipality_name,
            "branch_code": office.municipality_code,
            "municipality_code": office.municipality_code,
            "municipality_name": office.municipality_name,
            "sido": "부산광역시",
            "sigungu": sigungu,
            "provider_organizer": office.name,
            "venue_name": office.name,
            "category": "평생학습",
            "program_type": "강좌",
            "raw_url": raw_url,
            "application_url": "",
            "application_type": "INFO_ONLY",
            "reservation_available": False,
            "status": _STATUS_MAP[source_status],
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": (
                f"{apply_start} ~ {apply_end}" if apply_start and apply_end else ""
            ),
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": schedule,
            "fee": fee,
            "capacity": capacity,
            "target": "",
            "description": title,
            "source_group": "municipal_reservation",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "operator_type": "지자체/공공기관",
            "collection_type": "static_html+detail_html",
            "raw_fields": {
                "identity": identity,
                "identity_kind": identity_kind,
                "list_page": page,
                "list_sequence": int(sequence_raw),
                "source_status": source_status,
                "source_status_values": status_values,
                "selection_method": selection_method,
                "source_office_code": office.code,
                "source_office_name": office.name,
                "municipality_code": office.municipality_code,
                "list_application_control": list_application_control,
                "source_reversed_education_period": reversed_period,
                "source_reversed_application_period": reversed_apply_period,
                "source_period_start": source_start.isoformat(),
                "source_period_end": source_end.isoformat(),
                "source_apply_start": (
                    apply_dates[0].isoformat() if len(apply_dates) == 2 else ""
                ),
                "source_apply_end": (
                    apply_dates[1].isoformat() if len(apply_dates) == 2 else ""
                ),
                "no_detail_route_contract": identity_kind == "list_only_semantic_v1",
                "external_host": (
                    (urlparse(raw_url).hostname or "").lower()
                    if identity_kind == "external"
                    else ""
                ),
            },
        }
        rows.append(row)
    return rows, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            int(row.get("raw_fields", {}).get("list_sequence") or 0),
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for definition in soup.select("div.form_group dl"):
        heading = definition.find("dt")
        value = definition.find("dd")
        if heading is not None and value is not None:
            key = _clean(heading.get_text(" ", strip=True))
            if key and key not in pairs:
                pairs[key] = _clean(value.get_text(" ", strip=True))
    return pairs


def _application_control(soup: BeautifulSoup, base_url: str) -> tuple[bool, str]:
    for node in soup.select(
        "a, button, input[type='submit'], input[type='button']"
    ):
        label = _clean(
            node.get_text(" ", strip=True)
            or node.get("value")
            or node.get("title")
        )
        compact = label.replace(" ", "")
        if compact not in _APPLICATION_LABELS:
            continue
        href = _clean(node.get("href") or node.get("formaction"))
        resolved = ""
        if href and not href.lower().startswith(("javascript:", "#")):
            resolved = _safe_external_url(urljoin(base_url, href))
        if resolved:
            return True, resolved
        # A verified button may submit a form or open authentication through
        # JavaScript.  In that case the detail page is the safe user entry URL.
        return True, base_url
    return False, ""


def _validate_internal_detail(
    row: dict[str, Any], soup: BeautifulSoup
) -> list[str]:
    raw_fields = row.get("raw_fields", {})
    identity = _clean(raw_fields.get("identity"))
    office_code = _clean(raw_fields.get("source_office_code"))
    office_name = _clean(raw_fields.get("source_office_name"))
    errors: list[str] = []
    identity_fields = {
        _clean(node.get("value")) for node in soup.select("input[name='lng_id']")
    }
    if identity_fields != {identity}:
        errors.append(f"{identity}: detail identity mismatch")
    office_fields = {
        _clean(node.get("value")) for node in soup.select("input[name='inst_id']")
    }
    if office_fields != {office_code}:
        errors.append(f"{identity}: detail office mismatch")
    headings = soup.select("h2.enrolTit")
    if len(headings) != 1:
        errors.append(f"{identity}: expected one detail title")
    else:
        heading = headings[0]
        prefix = heading.select_one("span")
        prefix_text = _clean(prefix.get_text(" ", strip=True) if prefix else "")
        if prefix_text != f"[{office_name}]":
            errors.append(f"{identity}: detail office-title prefix mismatch")
        clone = BeautifulSoup(str(heading), "lxml")
        for span in clone.select("span"):
            span.extract()
        detail_title = _clean(clone.get_text(" ", strip=True))
        if detail_title != _clean(row.get("title")):
            errors.append(f"{identity}: detail/list title mismatch")

    pairs = _detail_pairs(soup)
    detail_dates = _dates(pairs.get("교육기간"))
    expected_dates = [
        date.fromisoformat(_clean(row.get("start_date"))),
        date.fromisoformat(_clean(row.get("end_date"))),
    ]
    if detail_dates != expected_dates:
        errors.append(f"{identity}: detail/list education period mismatch")
    if row.get("apply_start") and row.get("apply_end"):
        detail_apply = _dates(
            pairs.get("일반모집기간") or pairs.get("접수기간")
        )
        expected_apply = [
            date.fromisoformat(_clean(row.get("apply_start"))),
            date.fromisoformat(_clean(row.get("apply_end"))),
        ]
        if detail_apply != expected_apply:
            errors.append(f"{identity}: detail/list application period mismatch")

    control = soup.select_one("#learning_aply_btn")
    control_label = _clean(control.get_text(" ", strip=True) if control else "")
    detail_status = _clean(pairs.get("신청상태"))
    application_active = bool(
        control is not None
        and "접수중" in detail_status
        and "fn_learning_apply" in _clean(control.get("onclick"))
    )
    if application_active:
        row["application_url"] = _clean(row.get("raw_url"))
        row["application_type"] = (
            "WAITLIST_APPLY"
            if control_label == "대기자신청"
            or _clean(raw_fields.get("source_status")) == "대기접수"
            else "ONLINE_RESERVATION"
        )
        row["reservation_available"] = True
        row["status"] = "OPEN"
    else:
        row["application_url"] = ""
        row["application_type"] = "INFO_ONLY"
        row["reservation_available"] = False
        if "접수대기" in detail_status:
            row["status"] = "SCHEDULED"
        elif detail_status:
            row["status"] = "CLOSED"
    if pairs.get("교육대상"):
        row["target"] = _clean(pairs["교육대상"])
    if pairs.get("교육장소"):
        row["venue_name"] = _clean(pairs["교육장소"])
    if pairs.get("수강료"):
        row["fee"] = _clean(pairs["수강료"])
    row["raw_fields"] = {
        **raw_fields,
        "detail_verified": not errors,
        "detail_application_control": application_active,
        "detail_application_control_label": control_label,
        "detail_source_status": detail_status,
    }
    return errors


def _validate_external_detail(
    row: dict[str, Any], soup: BeautifulSoup, final_url: str
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    errors: list[str] = []
    text = _clean(soup.get_text(" ", strip=True))
    normalized_text = _normalized(text)
    normalized_title = _normalized(row.get("title"))
    if not normalized_title or normalized_title not in normalized_text:
        errors.append(f"{identity}: external detail/list title mismatch")
    found_dates = set(_dates(text))
    expected_dates = {
        date.fromisoformat(_clean(row.get("start_date"))),
        date.fromisoformat(_clean(row.get("end_date"))),
    }
    if not expected_dates.issubset(found_dates):
        errors.append(f"{identity}: external detail/list education period mismatch")
    effective_url = final_url or _clean(row.get("raw_url"))
    has_control, application_url = _application_control(soup, effective_url)
    if has_control and application_url:
        row["application_url"] = application_url
        row["application_type"] = (
            "WAITLIST_APPLY"
            if _clean(row.get("raw_fields", {}).get("source_status")) == "대기접수"
            else "ONLINE_RESERVATION"
        )
        row["reservation_available"] = True
        row["status"] = "OPEN"
    else:
        row["application_url"] = ""
        row["application_type"] = "INFO_ONLY"
        row["reservation_available"] = False
        if row.get("status") == "OPEN":
            row["status"] = "CLOSED"
    row["raw_fields"] = {
        **row.get("raw_fields", {}),
        "detail_verified": not errors,
        "detail_application_control": bool(has_control and application_url),
    }
    return errors


def _pii_key(value: Any) -> bool:
    compact = _normalized(value)
    return any(_normalized(part) in compact for part in _PII_KEY_PARTS)


def _scrub_text(value: Any) -> tuple[str, int]:
    text = _clean(value)
    updated, phone_count = _PHONE_RE.subn("[redacted]", text)
    updated, email_count = _EMAIL_RE.subn("[redacted]", updated)
    return updated, phone_count + email_count


def _sanitize_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redactions = 0

    def visit(value: Any) -> Any:
        nonlocal redactions
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if _pii_key(key):
                    redactions += 1
                    continue
                result[str(key)] = visit(item)
            return result
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return tuple(visit(item) for item in value)
        if isinstance(value, str):
            cleaned, count = _scrub_text(value)
            redactions += count
            return cleaned
        return value

    return visit(row), redactions


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _parallel_fetch(
    items: list[tuple[Any, str, str, Optional[Mapping[str, str]], bool]],
    *,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, tuple[BeautifulSoup, str]], list[str]]:
    fetched: dict[Any, tuple[BeautifulSoup, str]] = {}
    errors: list[str] = []
    sessions: list[Any] = []
    lock = threading.Lock()
    local = threading.local()

    def thread_session() -> Any:
        current = getattr(local, "session", None)
        if current is None:
            current = session_factory()
            local.session = current
            with lock:
                sessions.append(current)
        return current

    def fetch(
        item: tuple[Any, str, str, Optional[Mapping[str, str]], bool]
    ) -> tuple[Any, tuple[BeautifulSoup, str]]:
        key, method, url, data, external = item
        if external:
            result = _request_soup(
                thread_session(),
                method,
                url,
                timeout=timeout,
                data=data,
                allow_redirects=True,
            )
        else:
            expected_path = urlparse(url).path
            result = _request_soup(
                thread_session(),
                method,
                url,
                timeout=timeout,
                data=data,
                expected_host=BUSAN_LIFELONG_HOST,
                expected_path=expected_path,
            )
        return key, result

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_keys = {executor.submit(fetch, item): item[0] for item in items}
            for future in as_completed(future_keys):
                key = future_keys[future]
                try:
                    result_key, result = future.result()
                    fetched[result_key] = result
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}: {_clean(exc)}")
    finally:
        for current in sessions:
            _close_quietly(current)
    return fetched, errors


def _parallel_detail_fetch(
    rows: list[dict[str, Any]],
    *,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[str, tuple[BeautifulSoup, str]], list[str], int]:
    """Fetch details while preserving the platform's per-office session contract.

    Internal ``learningDetail.do`` requests return an eGov error unless the
    same session has first posted that office's list form.  Grouping by office
    needs one bootstrap page per office instead of one bootstrap per course.
    """

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        code = _clean(row.get("raw_fields", {}).get("source_office_code"))
        grouped.setdefault(code, []).append(row)
    results: dict[str, tuple[BeautifulSoup, str]] = {}
    errors: list[str] = []
    bootstrap_pages = 0

    def fetch_group(
        item: tuple[str, list[dict[str, Any]]]
    ) -> tuple[dict[str, tuple[BeautifulSoup, str]], list[str], int]:
        code, office_rows = item
        current = session_factory()
        current_results: dict[str, tuple[BeautifulSoup, str]] = {}
        current_errors: list[str] = []
        bootstrapped = 0
        try:
            internal_rows = [
                row
                for row in office_rows
                if row.get("raw_fields", {}).get("identity_kind") == "internal"
            ]
            if internal_rows:
                try:
                    bootstrap, _ = _request_soup(
                        current,
                        "POST",
                        BUSAN_LIFELONG_LIST_URL,
                        timeout=timeout,
                        data=_list_payload(code, 1),
                        expected_host=BUSAN_LIFELONG_HOST,
                        expected_path=BUSAN_LIFELONG_LIST_PATH,
                    )
                    office = BUSAN_LIFELONG_OFFICE_BY_CODE[code]
                    form_errors = _form_errors(bootstrap, office, 1)
                    if form_errors:
                        raise ValueError("; ".join(form_errors))
                    bootstrapped = 1
                except Exception as exc:
                    return {}, [
                        f"{code} detail bootstrap: {type(exc).__name__}: {_clean(exc)}"
                    ], 0
            for row in office_rows:
                identity = _clean(row.get("raw_fields", {}).get("identity"))
                internal = (
                    row.get("raw_fields", {}).get("identity_kind") == "internal"
                )
                try:
                    if internal:
                        page = int(row.get("raw_fields", {}).get("list_page") or 1)
                        value = _request_soup(
                            current,
                            "POST",
                            _clean(row.get("raw_url")),
                            timeout=timeout,
                            data=_list_payload(code, page),
                            expected_host=BUSAN_LIFELONG_HOST,
                            expected_path=BUSAN_LIFELONG_DETAIL_PATH,
                        )
                    else:
                        value = _request_soup(
                            current,
                            "GET",
                            _clean(row.get("raw_url")),
                            timeout=timeout,
                            allow_redirects=True,
                        )
                    current_results[identity] = value
                except Exception as exc:
                    current_errors.append(
                        f"{identity}: {type(exc).__name__}: {_clean(exc)}"
                    )
        finally:
            _close_quietly(current)
        return current_results, current_errors, bootstrapped

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_groups = {
            executor.submit(fetch_group, item): item[0] for item in grouped.items()
        }
        for future in as_completed(future_groups):
            code = future_groups[future]
            try:
                group_results, group_errors, group_bootstraps = future.result()
                results.update(group_results)
                errors.extend(group_errors)
                bootstrap_pages += group_bootstraps
            except Exception as exc:
                errors.append(f"{code}: {type(exc).__name__}: {_clean(exc)}")
    return results, errors, bootstrap_pages


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "main_discovery_pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "office_count": 0,
        "expected_office_count": len(BUSAN_LIFELONG_EXPECTED_OFFICES),
        "owned_office_count": len(BUSAN_LIFELONG_OWNED_OFFICES),
        "excluded_office_count": len(BUSAN_LIFELONG_EXCLUDED_OFFICES),
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
        "ownership_scope": BUSAN_LIFELONG_OWNERSHIP_SCOPE,
        "legacy_partial_count": BUSAN_LIFELONG_LEGACY_PARTIAL_COUNT,
        "legacy_partial_reason": (
            "fixed pages per office omitted declared archive pages and stored raw phone fields"
        ),
        "ownership_aliases": [
            {
                "provider": alias.provider,
                "url": alias.url,
                "relationship": alias.relationship,
            }
            for alias in BUSAN_LIFELONG_OWNERSHIP_ALIASES
        ],
        "superseded_providers": [
            alias.provider for alias in BUSAN_LIFELONG_OWNERSHIP_ALIASES
        ],
        "covered_municipalities": list(BUSAN_LIFELONG_COVERED_MUNICIPALITIES),
        "excluded_offices": [
            {
                "code": office.code,
                "name": office.name,
                "reason": BUSAN_LIFELONG_DUPLICATE_OWNER_REASONS.get(
                    office.ownership,
                    "university/external institution outside municipal ownership",
                ),
            }
            for office in BUSAN_LIFELONG_EXCLUDED_OFFICES
        ],
    }


def collect_busan_lifelong_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 1200,
    detail_limit: int = 1200,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = BUSAN_LIFELONG_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future municipal Busan snapshot."""

    meta = _base_meta()
    if not is_busan_lifelong_target(target):
        meta["configured_collection_error"] = (
            "target does not match the canonical Busan office-directory route"
        )
        return [], BUSAN_LIFELONG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = (
                "managed session_factory injection is required"
            )
            return [], BUSAN_LIFELONG_PARSER, meta
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        workers = min(max(1, int(max_workers)), BUSAN_LIFELONG_MAX_WORKERS)
        cutoff = _today(today)
        request_timeout = max(1, int(timeout))
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], BUSAN_LIFELONG_PARSER, meta

    errors: list[str] = []
    source_cap_reached = False
    office_session = session_factory()
    try:
        office_soup, _ = _request_soup(
            office_session,
            "GET",
            BUSAN_LIFELONG_URL,
            timeout=request_timeout,
            expected_host=BUSAN_LIFELONG_HOST,
            expected_path=BUSAN_LIFELONG_OFFICE_PATH,
        )
        meta["pages"] = 1
        meta["main_discovery_pages"] = 1
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"office directory: {type(exc).__name__}: {_clean(exc)}"
        )
        return [], BUSAN_LIFELONG_PARSER, meta
    finally:
        _close_quietly(office_session)

    offices, office_errors = _office_contract(office_soup)
    errors.extend(office_errors)
    meta["office_count"] = len(
        [
            option
            for option in office_soup.select("#o_search_ch option[value]")
            if _clean(option.get("value"))
        ]
    )
    owned_offices = [office for office in offices if office.owned]
    # Each office requires page 1, an immediate sentinel, and a stable page-1
    # recheck even when the archive is empty/single-page.
    minimum_required_pages = 1 + 3 * len(owned_offices)
    if allowed_pages < minimum_required_pages:
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of at least "
            f"{minimum_required_pages} required source page requests"
        )
    if errors:
        meta["source_cap_reached"] = source_cap_reached
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], BUSAN_LIFELONG_PARSER, meta

    first_items = [
        (
            (office.code, 1, "data"),
            "POST",
            BUSAN_LIFELONG_LIST_URL,
            _list_payload(office.code, 1),
            False,
        )
        for office in owned_offices
    ]
    fetched, fetch_errors = _parallel_fetch(
        first_items,
        session_factory=session_factory,
        timeout=request_timeout,
        max_workers=workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(fetched)
    meta["list_requests"] += len(fetched)

    totals: dict[str, int] = {}
    data_pages: dict[str, int] = {}
    advertised_pages: dict[str, int] = {}
    first_rows: dict[str, list[dict[str, Any]]] = {}
    first_signatures: dict[str, str] = {}
    for office in owned_offices:
        key = (office.code, 1, "data")
        value = fetched.get(key)
        if value is None:
            errors.append(f"{office.code}: missing first archive page")
            continue
        soup = value[0]
        errors.extend(
            f"{office.code} page 1: {message}"
            for message in _form_errors(soup, office, 1)
        )
        rows, row_errors = _parse_list_page(soup, office=office, page=1)
        errors.extend(row_errors)
        last, last_errors = _advertised_last(soup)
        errors.extend(
            f"{office.code} page 1: {message}" for message in last_errors
        )
        total = int(rows[0]["raw_fields"]["list_sequence"]) if rows else 0
        expected_pages = max(1, math.ceil(total / BUSAN_LIFELONG_PAGE_SIZE))
        if last and last != expected_pages:
            errors.append(
                f"{office.code}: displayed last {last} != sequence-derived {expected_pages}"
            )
        expected_first_count = min(BUSAN_LIFELONG_PAGE_SIZE, total)
        if len(rows) != expected_first_count:
            errors.append(f"{office.code}: first page row count mismatch")
        expected_sequences = list(range(total, total - len(rows), -1))
        actual_sequences = [
            int(row["raw_fields"]["list_sequence"]) for row in rows
        ]
        if actual_sequences != expected_sequences:
            errors.append(f"{office.code}: first page source sequence mismatch")
        totals[office.code] = total
        data_pages[office.code] = expected_pages
        advertised_pages[office.code] = last
        first_rows[office.code] = rows
        first_signatures[office.code] = _page_signature(rows)

    required_page_requests = 1
    for office in owned_offices:
        last = data_pages.get(office.code, 1)
        required_page_requests += last + 1 + 1 + (1 if last > 1 else 0)
    meta["required_page_requests"] = required_page_requests
    if required_page_requests > allowed_pages:
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of "
            f"{required_page_requests} required source page requests"
        )
    if errors:
        meta.update(
            {
                "source_cap_reached": source_cap_reached,
                "source_totals_by_office": totals,
                "declared_pages_by_office": data_pages,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return [], BUSAN_LIFELONG_PARSER, meta

    remaining_items: list[
        tuple[Any, str, str, Optional[Mapping[str, str]], bool]
    ] = []
    for office in owned_offices:
        last = data_pages[office.code]
        for page in range(2, last + 1):
            remaining_items.append(
                (
                    (office.code, page, "data"),
                    "POST",
                    BUSAN_LIFELONG_LIST_URL,
                    _list_payload(office.code, page),
                    False,
                )
            )
        remaining_items.append(
            (
                (office.code, last + 1, "sentinel"),
                "POST",
                BUSAN_LIFELONG_LIST_URL,
                _list_payload(office.code, last + 1),
                False,
            )
        )
    remaining, remaining_errors = _parallel_fetch(
        remaining_items,
        session_factory=session_factory,
        timeout=request_timeout,
        max_workers=workers,
    )
    fetched.update(remaining)
    errors.extend(remaining_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)

    listed_rows: list[dict[str, Any]] = []
    page_counts: dict[str, dict[int, int]] = {}
    page_signatures: dict[str, dict[int, str]] = {}
    sentinel_modes: dict[str, str] = {}
    office_by_code = {office.code: office for office in owned_offices}
    for office in owned_offices:
        code = office.code
        last = data_pages[code]
        office_rows: list[dict[str, Any]] = []
        page_counts[code] = {}
        page_signatures[code] = {}
        for page in range(1, last + 1):
            value = fetched.get((code, page, "data"))
            if value is None:
                errors.append(f"{code} page {page}: missing archive response")
                continue
            soup = value[0]
            errors.extend(
                f"{code} page {page}: {message}"
                for message in _form_errors(soup, office, page)
            )
            advertised, advertised_errors = _advertised_last(soup)
            errors.extend(
                f"{code} page {page}: {message}" for message in advertised_errors
            )
            if advertised != last:
                errors.append(f"{code} page {page}: displayed last page changed")
            if page == 1:
                rows = first_rows[code]
            else:
                rows, row_errors = _parse_list_page(soup, office=office, page=page)
                errors.extend(row_errors)
            expected_count = (
                BUSAN_LIFELONG_PAGE_SIZE
                if page < last
                else totals[code] - BUSAN_LIFELONG_PAGE_SIZE * (last - 1)
            )
            if totals[code] == 0:
                expected_count = 0
            if len(rows) != expected_count:
                errors.append(f"{code} page {page}: row count mismatch")
            start_sequence = totals[code] - BUSAN_LIFELONG_PAGE_SIZE * (page - 1)
            expected_sequences = list(
                range(start_sequence, start_sequence - len(rows), -1)
            )
            actual_sequences = [
                int(row["raw_fields"]["list_sequence"]) for row in rows
            ]
            if actual_sequences != expected_sequences:
                errors.append(f"{code} page {page}: source sequence gap/reorder")
            signature = _page_signature(rows)
            page_counts[code][page] = len(rows)
            page_signatures[code][page] = signature
            office_rows.extend(rows)

        nonempty_signatures = [
            page_signatures[code][page]
            for page in range(1, last + 1)
            if page_counts[code].get(page, 0)
        ]
        if len(nonempty_signatures) != len(set(nonempty_signatures)):
            errors.append(f"{code}: duplicate non-empty page signature")
        if len(office_rows) != totals[code]:
            errors.append(
                f"{code}: declared total {totals[code]} != parsed {len(office_rows)}"
            )
        listed_rows.extend(office_rows)

        sentinel_page = last + 1
        sentinel_value = fetched.get((code, sentinel_page, "sentinel"))
        if sentinel_value is None:
            errors.append(f"{code}: missing immediate post-last sentinel")
            continue
        sentinel_soup = sentinel_value[0]
        sentinel_rows, sentinel_errors = _parse_list_page(
            sentinel_soup, office=office, page=sentinel_page
        )
        errors.extend(sentinel_errors)
        sentinel_last, sentinel_last_errors = _advertised_last(sentinel_soup)
        errors.extend(
            f"{code} sentinel: {message}" for message in sentinel_last_errors
        )
        if sentinel_last != last:
            errors.append(f"{code}: sentinel advertised last page changed")
        sentinel_form_errors = _form_errors(sentinel_soup, office, sentinel_page)
        if not sentinel_rows and not sentinel_form_errors:
            sentinel_modes[code] = "empty"
        else:
            # Some eGov deployments clamp an out-of-range request to their last
            # page.  Accept only an explicit form-page clamp with an exact last-
            # page signature; never accept an arbitrary repeated page.
            clamped_form_errors = _form_errors(sentinel_soup, office, last)
            if (
                not clamped_form_errors
                and _page_signature(sentinel_rows) == page_signatures[code][last]
            ):
                sentinel_modes[code] = "clamped_last"
            else:
                errors.append(f"{code}: immediate post-last page is not empty/clamped-last")

    identities = [
        _clean(row.get("raw_fields", {}).get("identity")) for row in listed_rows
    ]
    duplicate_identity_count = len(identities) - len(set(identities))
    if duplicate_identity_count:
        errors.append(f"{duplicate_identity_count} duplicate source identities")
    course_ids = [_clean(row.get("provider_course_id")) for row in listed_rows]
    duplicate_course_id_count = len(course_ids) - len(set(course_ids))
    if duplicate_course_id_count:
        errors.append(f"{duplicate_course_id_count} duplicate provider course IDs")

    if not errors:
        recheck_items: list[
            tuple[Any, str, str, Optional[Mapping[str, str]], bool]
        ] = []
        for office in owned_offices:
            pages = [1]
            if data_pages[office.code] > 1:
                pages.append(data_pages[office.code])
            for page in pages:
                recheck_items.append(
                    (
                        (office.code, page, "recheck"),
                        "POST",
                        BUSAN_LIFELONG_LIST_URL,
                        _list_payload(office.code, page),
                        False,
                    )
                )
        rechecks, recheck_fetch_errors = _parallel_fetch(
            recheck_items,
            session_factory=session_factory,
            timeout=request_timeout,
            max_workers=workers,
        )
        errors.extend(recheck_fetch_errors)
        meta["pages"] += len(rechecks)
        meta["list_requests"] += len(rechecks)
        for key, (soup, _) in rechecks.items():
            code, page, _phase = key
            office = office_by_code[code]
            errors.extend(
                f"{code} recheck page {page}: {message}"
                for message in _form_errors(soup, office, page)
            )
            rows, row_errors = _parse_list_page(soup, office=office, page=page)
            errors.extend(row_errors)
            advertised, last_errors = _advertised_last(soup)
            errors.extend(
                f"{code} recheck page {page}: {message}"
                for message in last_errors
            )
            if advertised != data_pages[code]:
                errors.append(f"{code} recheck: displayed last page changed")
            if _page_signature(rows) != page_signatures[code][page]:
                errors.append(f"{code} recheck page {page}: page signature changed")

    list_complete = bool(
        not errors
        and len(listed_rows) == sum(totals.values())
        and len(sentinel_modes) == len(owned_offices)
    )
    current_rows: list[dict[str, Any]] = []
    expired_count = 0
    historical_reversed_period_count = 0
    historical_reversed_apply_period_count = 0
    for row in listed_rows:
        try:
            end = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
            continue
        reversed_period = bool(
            row.get("raw_fields", {}).get("source_reversed_education_period")
        )
        reversed_apply_period = bool(
            row.get("raw_fields", {}).get("source_reversed_application_period")
        )
        if end >= cutoff:
            if reversed_period:
                errors.append(
                    f"{_clean(row.get('provider_course_id'))}: "
                    "current course has a reversed source education period"
                )
            if reversed_apply_period:
                errors.append(
                    f"{_clean(row.get('provider_course_id'))}: "
                    "current course has a reversed source application period"
                )
            current_rows.append(row)
        else:
            expired_count += 1
            if reversed_period:
                historical_reversed_period_count += 1
            if reversed_apply_period:
                historical_reversed_apply_period_count += 1

    required_details = len(current_rows)
    detail_errors: list[str] = []
    detail_attempts = 0
    detail_pages = 0
    detail_verified_count = 0
    detail_bootstrap_pages = 0
    list_only_rows = [
        row
        for row in current_rows
        if row.get("raw_fields", {}).get("identity_kind")
        == "list_only_semantic_v1"
    ]
    linkable_rows = [row for row in current_rows if row not in list_only_rows]
    internal_detail_offices = {
        _clean(row.get("raw_fields", {}).get("source_office_code"))
        for row in linkable_rows
        if row.get("raw_fields", {}).get("identity_kind") == "internal"
    }
    if required_details > allowed_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of "
            f"{required_details} required current/future details"
        )
    elif required_page_requests + len(internal_detail_offices) > allowed_pages:
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of "
            f"{required_page_requests + len(internal_detail_offices)} required "
            "source/detail-bootstrap page requests"
        )
    elif list_complete and current_rows:
        rows_by_identity: dict[str, dict[str, Any]] = {}
        for row in linkable_rows:
            identity = _clean(row.get("raw_fields", {}).get("identity"))
            rows_by_identity[identity] = row
        for row in list_only_rows:
            raw_fields = row.get("raw_fields", {})
            required_contract = (
                _clean(raw_fields.get("identity")),
                _clean(row.get("title")),
                _clean(row.get("provider_organizer")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
            )
            if (
                not all(required_contract)
                or not _clean(raw_fields.get("identity")).startswith("LIST_ONLY_V1:")
                or not raw_fields.get("no_detail_route_contract")
            ):
                detail_errors.append(
                    f"{_clean(raw_fields.get('identity')) or '?'}: "
                    "invalid INFO_ONLY no-detail contract"
                )
            else:
                row["raw_fields"] = {
                    **raw_fields,
                    "detail_verified": True,
                    "detail_verification_mode": "complete_list_contract_no_source_route",
                    "detail_application_control": False,
                }
                row["application_url"] = ""
                row["application_type"] = "INFO_ONLY"
                row["reservation_available"] = False
                detail_verified_count += 1

        detail_attempts = len(linkable_rows)
        detail_results, detail_fetch_errors, detail_bootstrap_pages = _parallel_detail_fetch(
            linkable_rows,
            session_factory=session_factory,
            timeout=request_timeout,
            max_workers=workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["pages"] += len(detail_results) + detail_bootstrap_pages
        meta["list_requests"] += detail_bootstrap_pages
        for identity, (soup, final_url) in detail_results.items():
            row = rows_by_identity[identity]
            if row.get("raw_fields", {}).get("identity_kind") == "internal":
                item_errors = _validate_internal_detail(row, soup)
            else:
                item_errors = _validate_external_detail(row, soup, final_url)
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detail_pages += 1
                detail_verified_count += 1
    errors.extend(detail_errors)

    details_complete = bool(
        list_complete
        and detail_attempts == len(linkable_rows)
        and detail_pages == len(linkable_rows)
        and detail_verified_count == required_details
        and not detail_errors
    )
    result: list[dict[str, Any]] = []
    privacy_redactions = 0
    if list_complete and details_complete and not errors:
        safe_rows: list[dict[str, Any]] = []
        for row in current_rows:
            safe, count = _sanitize_row(row)
            safe_rows.append(safe)
            privacy_redactions += count
        deduper = dedupe_rows or _dedupe_default
        result = list(deduper(safe_rows))
        if len(result) != len(current_rows):
            errors.append(
                f"dedupe changed complete row count {len(current_rows)} to {len(result)}"
            )
            result = []

    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []
    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    office_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_office_name")) for row in result
    )
    source_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in listed_rows
    )
    meta.update(
        {
            "source_totals_by_office": totals,
            "declared_pages_by_office": data_pages,
            "advertised_pages_by_office": advertised_pages,
            "page_counts": page_counts,
            "sentinel_modes": sentinel_modes,
            "stable_recheck_count": sum(
                1 + (1 if data_pages.get(office.code, 1) > 1 else 0)
                for office in owned_offices
            ),
            "source_total": sum(totals.values()),
            "source_rows": len(listed_rows),
            "expired_count": expired_count,
            "historical_reversed_period_count": historical_reversed_period_count,
            "historical_reversed_apply_period_count": (
                historical_reversed_apply_period_count
            ),
            "current_count": len(current_rows),
            "returned_count": len(result),
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_verified_count": detail_verified_count,
            "detail_bootstrap_pages": detail_bootstrap_pages,
            "detail_errors": len(detail_errors),
            "internal_detail_count": sum(
                row.get("raw_fields", {}).get("identity_kind") == "internal"
                for row in current_rows
            ),
            "external_detail_count": sum(
                row.get("raw_fields", {}).get("identity_kind") == "external"
                for row in current_rows
            ),
            "list_only_detail_count": len(list_only_rows),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "source_status_counts": dict(source_status_counts),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "office_counts": dict(office_counts),
            "duplicate_identity_count": duplicate_identity_count,
            "duplicate_course_id_count": duplicate_course_id_count,
            "privacy_redactions": privacy_redactions,
            "pagination_detected": any(value > 1 for value in data_pages.values()),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "all complete municipal office archives have ended"
                if snapshot_complete and not current_rows
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, BUSAN_LIFELONG_PARSER, meta


collect = collect_busan_lifelong_courses


__all__ = [
    "BUSAN_LIFELONG_COVERED_MUNICIPALITIES",
    "BUSAN_LIFELONG_EXPECTED_OFFICES",
    "BUSAN_LIFELONG_EXCLUDED_OFFICES",
    "BUSAN_LIFELONG_HOST",
    "BUSAN_LIFELONG_LEGACY_PARTIAL_COUNT",
    "BUSAN_LIFELONG_LIST_PATH",
    "BUSAN_LIFELONG_LIST_URL",
    "BUSAN_LIFELONG_OFFICE_PATH",
    "BUSAN_LIFELONG_OWNED_OFFICES",
    "BUSAN_LIFELONG_OWNERSHIP_ALIASES",
    "BUSAN_LIFELONG_OWNERSHIP_SCOPE",
    "BUSAN_LIFELONG_PAGE_SIZE",
    "BUSAN_LIFELONG_PARSER",
    "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_LIFELONG_URL",
    "BusanOffice",
    "BusanOwnershipAlias",
    "busan_lifelong_detail_url",
    "collect",
    "collect_busan_lifelong_courses",
    "is_busan_lifelong_target",
    "is_target",
]
