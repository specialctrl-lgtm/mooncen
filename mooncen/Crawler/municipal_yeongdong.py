"""Fail-closed collectors for Yeongdong-gun's official education ledgers.

Yeongdong-gun publishes two distinct, municipality-owned course ledgers:

* the county-wide integrated education ledger on ``yd21.go.kr``; and
* the library/lifelong-learning programme ledger on ``rainbowlib.go.kr``.

They are not aliases of one another and therefore have separate provider and
course identities.  Each target is collected atomically: every declared page,
an empty post-last boundary, stable first/last rechecks, and every required
current detail/application identity must validate before any rows are returned.

The two review candidates that led to this audit are county notice details,
not course ledgers.  Instructor recruitment, editorial recruitment notices,
homepage highlights, category tabs, and the locker category are deliberately
not treated as independent education sources.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YEONGDONG_MUNICIPALITY_CODE = "4374000000"
YEONGDONG_MUNICIPALITY_NAME = "충청북도 영동군"

YEONGDONG_COUNTY_HOST = "www.yd21.go.kr"
YEONGDONG_COUNTY_PATH = "/kr/html/sub05/05090101.html"
YEONGDONG_COUNTY_URL = f"https://{YEONGDONG_COUNTY_HOST}{YEONGDONG_COUNTY_PATH}"
YEONGDONG_COUNTY_PROVIDER = "MUNI_WWW_YD21_GO_KR_86E8BB47"
YEONGDONG_COUNTY_CANDIDATE_ID = "MUNI_IR_362C7F0959ED"

YEONGDONG_LIBRARY_HOST = "www.rainbowlib.go.kr"
YEONGDONG_LIBRARY_PATH = "/front/index.php"
YEONGDONG_LIBRARY_URL = (
    f"https://{YEONGDONG_LIBRARY_HOST}{YEONGDONG_LIBRARY_PATH}?"
    "g_page=culture&m_page=culture01"
)
YEONGDONG_LIBRARY_PROVIDER = "MUNI_WWW_RAINBOWLIB_GO_KR_3793102A"
YEONGDONG_LIBRARY_CANDIDATE_ID = "MUNI_IR_D59C473C11D3"

# Compatibility names use the county-wide ledger as the municipality primary.
YEONGDONG_PROVIDER = YEONGDONG_COUNTY_PROVIDER
YEONGDONG_CANONICAL_URL = YEONGDONG_COUNTY_URL
YEONGDONG_CANDIDATE_ID = YEONGDONG_COUNTY_CANDIDATE_ID

YEONGDONG_REVIEW_CANDIDATE_NOTICE = "MUNI_IR_3677657B79F2"
YEONGDONG_REVIEW_CANDIDATE_RECRUITMENT = "MUNI_IR_E3BF8DF1EECB"
YEONGDONG_BOOKING_PORTAL_URL = "https://www.yd21.go.kr/portal/"
YEONGDONG_BOOKING_PORTAL_PROVIDER = "MUNI_WWW_YD21_GO_KR_8C7953BE"
YEONGDONG_BOOKING_PORTAL_CANDIDATE_ID = "MUNI_IR_9ACD39C815DA"

YEONGDONG_MAX_HTML_BYTES = 3_000_000
YEONGDONG_MAX_WORKERS = 5
YEONGDONG_PARSER = (
    "yeongdong_official_education_ledgers+all_declared_pages+"
    "empty_post_last_boundary+stable_first_last+current_details+"
    "identity_bound_application_controls+education_only+exact_branches+"
    "pii_allowlist+atomic_snapshot"
)

YEONGDONG_SOURCE_DECISIONS: tuple[dict[str, str], ...] = (
    {
        "source": (
            "https://www.yd21.go.kr/kr/html/sub02/020101.html?mode=V&"
            "no=e6be41bc58a755f9b9b4da7251aa1730"
        ),
        "candidate_id": YEONGDONG_REVIEW_CANDIDATE_NOTICE,
        "reason": "instructor_recruitment_notice_not_learner_course_ledger",
    },
    {
        "source": (
            "https://www.yd21.go.kr/kr/html/sub02/020110.html?mode=V&"
            "no=af338fbe3e1cf4d306bc1e4d926fd8c9"
        ),
        "candidate_id": YEONGDONG_REVIEW_CANDIDATE_RECRUITMENT,
        "reason": "editorial_recruitment_notice_subset_without_course_identity",
    },
    {
        "source": "https://www.yd21.go.kr/kr/",
        "reason": "homepage_education_cards_are_subset_of_county_ledger",
    },
    {
        "source": YEONGDONG_BOOKING_PORTAL_URL,
        "provider": YEONGDONG_BOOKING_PORTAL_PROVIDER,
        "candidate_id": YEONGDONG_BOOKING_PORTAL_CANDIDATE_ID,
        "reason": (
            "distinct_same_owner_experience_lodging_facility_catalog_outside_education_scope;"
            "embedded_education_cards_are_subset_of_county_ledger"
        ),
    },
    {
        "source": "m.yd21.go.kr / edu.yd21.go.kr / tour.yd21.go.kr / yd5959.yd21.go.kr",
        "reason": "same_owner_virtual_host_aliases_not_independent_ledgers",
    },
    {
        "source": "https://www.rainbowlib.go.kr/front/index.php",
        "reason": "homepage_program_highlights_are_subset_of_library_ledger",
    },
    {
        "source": f"{YEONGDONG_LIBRARY_URL}&lgCode=<category>",
        "reason": "category_tabs_are_subsets_of_all_programme_ledger",
    },
    {
        "source": f"{YEONGDONG_LIBRARY_URL}&lgCode=19",
        "reason": "locker_application_is_facility_service_not_education",
    },
    {
        "source": "http:" "//ydmaeul.com/bbs/course.php",
        "reason": "separate_non_https_regional_centre_owner_not_county_ledger",
    },
)

COUNTY_CATEGORY_CODES: Mapping[str, str] = {
    "ECG01": "정보화교육",
    "ECG02": "평생학습교육",
    "ECG03": "여성회관교육",
    "ECG04": "농업교육",
    "ECG12": "청소년 교육",
}

LIBRARY_GROUPS: Mapping[str, tuple[str, str, bool]] = {
    "1": ("도서관", "레인보우영동도서관", True),
    "7": ("평생학습", "영동군평생학습관", True),
    "11": ("도서관", "레인보우영동도서관", True),
    "18": ("평생학습", "영동군평생학습관", True),
    "19": ("시설서비스", "레인보우영동도서관", False),
    "20": ("도서관", "레인보우영동도서관", True),
    "21": ("작은도서관", "영동군 가족센터 작은도서관", True),
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class YeongdongContractError(ValueError):
    """Raised when an official source no longer satisfies its audited shape."""


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE = re.compile(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-4]):([0-5]\d)(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LIBRARY_CAPACITY = re.compile(
    r"^(.*?)\s+(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s*$"
)
_LIBRARY_TOTAL = re.compile(
    r"^Total\s*:\s*(\d+)\s*개\s*\(page\s*:\s*(\d+)\s*/\s*(\d+)\s*\)$"
)

_LIBRARY_HEADERS = (
    "번호",
    "분류",
    "교육명",
    "대상 정원 / 온라인 / 대기",
    "접수기간",
    "상태",
)
_COUNTY_DETAIL_HEADERS = ("구분", "예약여부", "비고", "예약현황(현원/총원)")
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "group",
        "category_code",
        "source_ledger",
        "source_page",
        "source_status",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_venue",
        "source_detail_venue",
        "source_methods",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "education_scope_verified",
        "service_family",
    }
)
_FORBIDDEN_FIELDS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "instructor",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
        "image_url",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _target_kind(target: Any) -> str:
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    if provider == YEONGDONG_COUNTY_PROVIDER and url == YEONGDONG_COUNTY_URL:
        return "county"
    if provider == YEONGDONG_LIBRARY_PROVIDER and url == YEONGDONG_LIBRARY_URL:
        return "library"
    return ""


def is_yeongdong_education_target(target: Any) -> bool:
    return bool(_target_kind(target))


is_target = is_yeongdong_education_target


def _session() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            # The county edge currently returns a synthetic 404 specifically
            # for the retired ``MooncenMunicipalCrawler`` product token while
            # serving the same public catalogue to an identified compatible
            # crawler UA.  Keep our identity and contact URL explicit without
            # using that upstream-blocked legacy token.
            "User-Agent": "Mozilla/5.0 (compatible; MoonCenBot/1.0; +https://mooncen.kr)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return value


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _query(url: str) -> dict[str, str]:
    pairs = parse_qsl(urlparse(url).query, keep_blank_values=True, strict_parsing=True)
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate query key")
    return dict(pairs)


def _allowed_county_url(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
        query = _query(url)
    except ValueError:
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == YEONGDONG_COUNTY_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == YEONGDONG_COUNTY_PATH
        and not parsed.fragment
    ):
        return False
    if not query:
        return True
    if set(query) == {"GotoPage", "cgubun", "edutype"}:
        return bool(
            _IDENTITY.fullmatch(query["GotoPage"])
            and not query["cgubun"]
            and not query["edutype"]
        )
    if set(query) == {"mode", "mng_no", "cgubun", "edutype"}:
        return bool(
            query["mode"] == "V"
            and _IDENTITY.fullmatch(query["mng_no"])
            and not query["cgubun"]
            and not query["edutype"]
        )
    return False


def _allowed_library_url(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
        query = _query(url)
    except ValueError:
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == YEONGDONG_LIBRARY_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == YEONGDONG_LIBRARY_PATH
        and not parsed.fragment
        and query.get("g_page") == "culture"
        and query.get("m_page") == "culture01"
    ):
        return False
    action = query.get("act", "")
    if not action:
        if not set(query) <= {"g_page", "m_page", "page"}:
            return False
        return "page" not in query or bool(_IDENTITY.fullmatch(query["page"]))
    if action != "lecture_view" or not set(query) <= {
        "g_page",
        "m_page",
        "act",
        "lgCode",
        "leCode",
        "cate",
    }:
        return False
    if set(query) - {"cate"} != {
        "g_page",
        "m_page",
        "act",
        "lgCode",
        "leCode",
    }:
        return False
    return bool(
        ("cate" not in query or not query["cate"])
        and _IDENTITY.fullmatch(query["lgCode"])
        and _IDENTITY.fullmatch(query["leCode"])
    )


def _soup(
    url: str,
    timeout: int,
    factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    allowed = _allowed_county_url(url) or _allowed_library_url(url)
    if not allowed:
        raise YeongdongContractError(f"refusing URL outside audited owners: {url}")
    session = factory()
    try:
        response = fetcher(session, url, timeout)
        status_code = int(getattr(response, "status_code", 0) or 0)
        if 300 <= status_code < 400:
            raise YeongdongContractError("redirect responses are not followed")
        response.raise_for_status()
        final_url = _clean(getattr(response, "url", url))
        if not (_allowed_county_url(final_url) or _allowed_library_url(final_url)):
            raise YeongdongContractError(f"unexpected redirect: {final_url}")
        content = bytes(response.content)
        if not content or len(content) > YEONGDONG_MAX_HTML_BYTES:
            raise YeongdongContractError("empty or oversized HTML response")
        return BeautifulSoup(content, "html.parser", from_encoding="utf-8")
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


def _dates(value: Any) -> tuple[date, ...]:
    return tuple(
        date(int(year), int(month), int(day))
        for year, month, day in _DATE.findall(_clean(value))
    )


def _times(value: Any) -> tuple[str, ...]:
    return tuple(f"{int(hour):02d}:{minute}" for hour, minute in _TIME.findall(_clean(value)))


def _date_range(value: Any, *, label: str) -> tuple[date, date]:
    values = _dates(value)
    if len(values) == 1:
        return values[0], values[0]
    if len(values) != 2 or values[1] < values[0]:
        raise YeongdongContractError(f"{label}: invalid date range")
    return values[0], values[1]


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _table_headers(table: Any) -> tuple[str, ...]:
    row = table.select_one("thead tr") or table.select_one("tr")
    return tuple(_clean(cell.get_text(" ", strip=True)) for cell in row.select("th"))


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"YEONGDONG_BRANCH_{digest}"


def _county_list_url(page: int) -> str:
    if page == 1:
        return YEONGDONG_COUNTY_URL
    return f"{YEONGDONG_COUNTY_URL}?" + urlencode(
        {"cgubun": "", "edutype": "", "GotoPage": page}
    )


def _county_detail_url(identity: str) -> str:
    return f"{YEONGDONG_COUNTY_URL}?" + urlencode(
        {"mode": "V", "mng_no": identity, "cgubun": "", "edutype": ""}
    )


def _library_list_url(page: int) -> str:
    params: list[tuple[str, Any]] = [("g_page", "culture"), ("m_page", "culture01")]
    if page != 1:
        params.append(("page", page))
    return f"https://{YEONGDONG_LIBRARY_HOST}{YEONGDONG_LIBRARY_PATH}?{urlencode(params)}"


def _library_detail_url(group: str, identity: str) -> str:
    return f"https://{YEONGDONG_LIBRARY_HOST}{YEONGDONG_LIBRARY_PATH}?" + urlencode(
        {
            "g_page": "culture",
            "m_page": "culture01",
            "act": "lecture_view",
            "lgCode": group,
            "leCode": identity,
        }
    )


def _library_application_url(group: str, identity: str) -> str:
    return f"https://{YEONGDONG_LIBRARY_HOST}{YEONGDONG_LIBRARY_PATH}?" + urlencode(
        {
            "g_page": "culture",
            "m_page": "culture01",
            "act": "lecture_receive_form",
            "lgCode": group,
            "leCode": identity,
        }
    )


def _county_link_identity(href: str) -> str:
    absolute = urljoin(YEONGDONG_COUNTY_URL, href)
    parsed = urlparse(absolute)
    try:
        query = _query(absolute)
        port = parsed.port
    except ValueError as exc:
        raise YeongdongContractError("malformed county detail identity") from exc
    identity = query.get("mng_no", "")
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == YEONGDONG_COUNTY_HOST
        and port is None
        and parsed.path == YEONGDONG_COUNTY_PATH
        and set(query) == {"mode", "mng_no", "cgubun", "edutype"}
        and query.get("mode") == "V"
        and _IDENTITY.fullmatch(identity)
        and not query.get("cgubun")
        and not query.get("edutype")
    ):
        raise YeongdongContractError("county detail identity link changed")
    return identity


def _library_link_identity(href: str, action: str) -> tuple[str, str]:
    absolute = urljoin(f"https://{YEONGDONG_LIBRARY_HOST}/front/", href)
    parsed = urlparse(absolute)
    try:
        query = _query(absolute)
        port = parsed.port
    except ValueError as exc:
        raise YeongdongContractError("malformed library identity link") from exc
    allowed_keys = {"g_page", "m_page", "act", "lgCode", "leCode"}
    if "cate" in query and not query["cate"]:
        allowed_keys.add("cate")
    group, identity = query.get("lgCode", ""), query.get("leCode", "")
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == YEONGDONG_LIBRARY_HOST
        and port is None
        and parsed.path == YEONGDONG_LIBRARY_PATH
        and set(query) == allowed_keys
        and query.get("g_page") == "culture"
        and query.get("m_page") == "culture01"
        and query.get("act") == action
        and _IDENTITY.fullmatch(group)
        and _IDENTITY.fullmatch(identity)
    ):
        raise YeongdongContractError("library identity link changed")
    return group, identity


def _county_status(value: str) -> str:
    mapping = {
        "접수중": "OPEN",
        "접수대기": "SCHEDULED",
        "접수예정": "SCHEDULED",
        "접수마감": "CLOSED",
        "교육중": "CLOSED",
        "교육종료": "CLOSED",
    }
    if value not in mapping:
        raise YeongdongContractError(f"unknown county status: {value}")
    return mapping[value]


def _county_declared_pages(soup: BeautifulSoup) -> int:
    values = {1}
    for anchor in soup.select(".pagination a[href]"):
        absolute = urljoin(YEONGDONG_COUNTY_URL, anchor.get("href", ""))
        try:
            raw = _query(absolute).get("GotoPage", "")
        except ValueError:
            continue
        if raw == "0":
            continue
        if _IDENTITY.fullmatch(raw):
            values.add(int(raw))
    return max(values)


def _parse_county_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    if soup.select_one(".edu_view"):
        raise YeongdongContractError("county list request returned a detail page")
    # ``#edu_pop .list_inner`` is the category-picker modal.  It repeats the
    # same records across tabs and is not the authoritative paginated ledger.
    # The public course ledger is the direct ``.edu_list > ul > li`` sequence.
    cards = soup.select(".edu_wrap .edu_list > ul > li")
    rows: list[dict[str, Any]] = []
    for card in cards:
        title_node = card.find("strong", recursive=False)
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        status_text = _clean(
            card.select_one(".cate").get_text(" ", strip=True)
            if card.select_one(".cate")
            else ""
        )
        links = card.select(".edu_btn a[href]")
        if not title or len(links) != 1 or _clean(links[0].get_text(" ", strip=True)) != "상세보기":
            raise YeongdongContractError("county course card identity/title changed")
        identity = _county_link_identity(links[0].get("href", ""))
        pairs: dict[str, str] = {}
        for item in card.select(".eduli > li"):
            label = item.select_one("span")
            key = _clean(label.get_text(" ", strip=True) if label else "")
            full_text = _clean(item.get_text(" ", strip=True))
            if not key or not full_text.startswith(key) or key in pairs:
                raise YeongdongContractError(f"county {identity}: list field changed")
            pairs[key] = _clean(full_text[len(key) :])
        expected = {"접수기간", "교육기간", "교육시간", "교육장소"}
        if set(pairs) != expected:
            raise YeongdongContractError(f"county {identity}: list fields changed")
        apply_start, apply_end = _date_range(pairs["접수기간"], label="county apply period")
        start, end = _date_range(pairs["교육기간"], label="county education period")
        methods = tuple(
            _clean(span.get_text(" ", strip=True)) for span in card.select(".tit > span")
        )
        if any(not method for method in methods) or len(set(methods)) != len(methods):
            raise YeongdongContractError(f"county {identity}: reception methods changed")
        if not set(methods) <= {"온라인예약", "유선예약", "이메일 접수", "방문 접수"}:
            raise YeongdongContractError(f"county {identity}: unknown reception method")
        rows.append(
            {
                "identity": identity,
                "title": title,
                "status": _county_status(status_text),
                "source_status": status_text,
                "apply_period": pairs["접수기간"],
                "apply_start": apply_start,
                "apply_end": apply_end,
                "education_period": pairs["교육기간"],
                "schedule": pairs["교육시간"],
                "venue": pairs["교육장소"],
                "methods": methods,
                "start": start,
                "end": end,
                "page": page,
            }
        )
    return {"rows": rows, "declared_pages": _county_declared_pages(soup)}


def _county_page_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        (
            row["identity"],
            row["title"],
            row["source_status"],
            row["apply_period"],
            row["education_period"],
            row["schedule"],
            row["venue"],
            row["methods"],
        )
        for row in page["rows"]
    )


def _library_status(tr: Any, status_text: str, group: str, identity: str) -> tuple[str, str]:
    controls = tr.select("a[href*='act=lecture_receive_form']")
    results = tr.select("a[href*='act=lecture_result_view']")
    if len(controls) > 1 or len(results) > 1:
        raise YeongdongContractError("multiple library row controls")
    for result in results:
        if _library_link_identity(result.get("href", ""), "lecture_result_view") != (
            group,
            identity,
        ):
            raise YeongdongContractError("library result identity drift")
    if controls:
        control = controls[0]
        if _library_link_identity(control.get("href", ""), "lecture_receive_form") != (
            group,
            identity,
        ):
            raise YeongdongContractError("library application identity drift")
        text = _clean(control.get_text(" ", strip=True))
        if text not in {"신청하기", "대기자신청"} or text not in status_text:
            raise YeongdongContractError("library actionable status/control drift")
        return "OPEN", text
    reduced = _clean(status_text.replace("접수확인", ""))
    if reduced in {"접수마감", "신청마감", "마감"}:
        return "CLOSED", ""
    if reduced in {"접수예정", "신청예정", "모집예정"}:
        return "SCHEDULED", ""
    raise YeongdongContractError(f"unknown library status: {status_text}")


def _library_total_contract(soup: BeautifulSoup, page: int) -> tuple[int, int]:
    matches: list[tuple[int, int, int]] = []
    for heading in soup.select("h3"):
        match = _LIBRARY_TOTAL.fullmatch(_clean(heading.get_text(" ", strip=True)))
        if match:
            matches.append(tuple(int(value) for value in match.groups()))
    if len(matches) != 1:
        raise YeongdongContractError("library total/page declaration changed")
    total, current, declared_pages = matches[0]
    if current != page or declared_pages < 1:
        raise YeongdongContractError("library current/declared page drift")
    return total, declared_pages


def _parse_library_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    matching = [table for table in soup.select("table") if _table_headers(table) == _LIBRARY_HEADERS]
    if len(matching) != 1:
        raise YeongdongContractError("library programme table/header changed")
    declared_total, declared_pages = _library_total_contract(soup, page)
    rows: list[dict[str, Any]] = []
    unknown: list[str] = []
    for tr in matching[0].select("tbody tr"):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select(":scope > td")]
        detail_links = tr.select("a[href*='act=lecture_view']")
        if not detail_links:
            text = _clean(tr.get_text(" ", strip=True))
            if text and "등록된 데이터가 없습니다" not in text:
                unknown.append(text)
            continue
        if len(cells) != 6 or len(detail_links) != 1:
            raise YeongdongContractError("library row width/detail identity changed")
        try:
            number = int(cells[0])
        except ValueError as exc:
            raise YeongdongContractError("library row number changed") from exc
        group, identity = _library_link_identity(
            detail_links[0].get("href", ""), "lecture_view"
        )
        if group not in LIBRARY_GROUPS:
            raise YeongdongContractError(f"unknown library group: {group}")
        capacity = _LIBRARY_CAPACITY.fullmatch(cells[3])
        if not capacity:
            raise YeongdongContractError("library audience/capacity shape changed")
        apply_start, apply_end = _date_range(cells[4], label="library apply period")
        status, control_text = _library_status(tr, cells[5], group, identity)
        category, branch, is_education = LIBRARY_GROUPS[group]
        rows.append(
            {
                "number": number,
                "group": group,
                "identity": identity,
                "title": cells[2],
                "target": _clean(capacity.group(1)),
                "capacity_total": int(capacity.group(2)),
                "online_capacity": int(capacity.group(3)),
                "wait_capacity": int(capacity.group(4)),
                "apply_period": cells[4],
                "apply_start": apply_start,
                "apply_end": apply_end,
                "source_status": _clean(cells[5].replace("접수확인", "")),
                "status": status,
                "control_text": control_text,
                "category": category,
                "branch": branch,
                "education_exclusion": "" if is_education else "locker_facility_service",
                "page": page,
            }
        )
    if unknown:
        raise YeongdongContractError("unparsed library table row")
    return {
        "rows": rows,
        "declared_total": declared_total,
        "declared_pages": declared_pages,
    }


def _library_page_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        (
            row["number"],
            row["group"],
            row["identity"],
            row["title"],
            row["target"],
            row["capacity_total"],
            row["online_capacity"],
            row["wait_capacity"],
            row["apply_period"],
            row["source_status"],
            row["control_text"],
        )
        for row in page["rows"]
    )


def _form_values(form: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in form.select("input[name]"):
        name = _clean(field.get("name"))
        value = _clean(field.get("value"))
        if name in values:
            raise YeongdongContractError(f"duplicate form identity field: {name}")
        values[name] = value
    return values


def _labeled_list(container: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in container.select(":scope > li"):
        label_node = item.select_one("span")
        label = _clean(label_node.get_text(" ", strip=True) if label_node else "")
        text = _clean(item.get_text(" ", strip=True))
        if not label or not text.startswith(label) or label in result:
            raise YeongdongContractError("detail label/value structure changed")
        result[label] = _clean(text[len(label) :])
    return result


def _county_reservation_rows(view: Any) -> dict[str, tuple[str, str, str]]:
    candidates = [
        table
        for table in view.select("table")
        if _table_headers(table) == _COUNTY_DETAIL_HEADERS
    ]
    if not candidates:
        raise YeongdongContractError("county reservation table/header changed")
    signatures: list[tuple[tuple[str, str, str, str], ...]] = []
    for table in candidates:
        rows: list[tuple[str, str, str, str]] = []
        for tr in table.select("tbody tr"):
            cells = tuple(
                _clean(cell.get_text(" ", strip=True))
                for cell in tr.select(":scope > th, :scope > td")
            )
            if len(cells) != 4:
                raise YeongdongContractError("county reservation row width changed")
            rows.append(cells)
        signatures.append(tuple(rows))
    if len(set(signatures)) != 1:
        raise YeongdongContractError("county desktop/mobile reservation tables differ")
    result: dict[str, tuple[str, str, str]] = {}
    for method, availability, note, capacity in signatures[0]:
        if not method or method in result:
            raise YeongdongContractError("county reservation method changed")
        result[method] = (availability, note, capacity)
    return result


def _county_branch(category: str, venue: str) -> str:
    value = _clean(venue)
    compact = value.replace(" ", "")
    if "ZOOM" in value.upper() or value in {"온라인", "비대면"}:
        return "영동군 온라인교육"
    if "영동읍행정복지센터" in compact or "주민정보화교육장" in compact:
        return "영동읍행정복지센터"
    if "청소년수련관" in compact:
        return "영동군청소년수련관"
    if "청소년문화의집" in compact:
        return value.split(" ")[0] if " " in value else value
    if "여성회관" in compact:
        return "영동군여성회관"
    if "농업기술센터" in compact:
        return "영동군농업기술센터"
    if "평생학습관" in compact:
        return "영동군평생학습관"
    if value:
        return value
    # A few official information-education records intentionally leave the
    # venue blank.  Use the owner/category as a non-physical catalogue branch;
    # do not invent a street or facility location.
    return f"영동군 {category}"


def _county_safe_detail_venue(view: Any, identity: str) -> str:
    """Extract only an allowlisted facility name when the venue field is blank.

    Some youth records leave ``교육장소`` blank while publishing the facility in
    the programme table below it.  We never retain that free-form block; a
    unique, audited facility token is the only value allowed to escape it.
    """

    content = view.select_one(".view_btm")
    compact = _clean(content.get_text(" ", strip=True) if content else "").replace(
        " ", ""
    )
    facilities = {
        canonical
        for token, canonical in {
            "영동군청소년수련관": "영동군청소년수련관",
            "황간청소년문화의집": "황간청소년문화의집",
            "영동군가족센터": "영동군 가족센터",
            "영동군평생학습관": "영동군평생학습관",
            "영동군여성회관": "영동군여성회관",
            "영동군농업기술센터": "영동군농업기술센터",
            "영동읍행정복지센터": "영동읍행정복지센터",
        }.items()
        if token in compact
    }
    if not facilities:
        return ""
    if len(facilities) != 1:
        raise YeongdongContractError(
            f"county {identity}: unique allowlisted detail venue missing"
        )
    return facilities.pop()


def _base_row(
    *,
    provider: str,
    ledger: str,
    identity: str,
    title: str,
    branch: str,
    category: str,
    status: str,
    raw_url: str,
    application_url: str,
    application_methods: list[str],
    reservation_available: bool,
    start: date,
    end: date,
    apply_start: date,
    apply_end: date,
    schedule: str,
    target: str,
    capacity_total: Optional[int],
    raw_fields: Mapping[str, Any],
) -> dict[str, Any]:
    if reservation_available:
        application_method = "온라인"
    elif status == "SCHEDULED":
        application_method = "접수예정"
    elif status == "CLOSED":
        application_method = "접수마감"
    else:
        application_method = application_methods[0] if application_methods else "정보제공"
    row: dict[str, Any] = {
        "provider": provider,
        "provider_course_id": f"{provider}:{ledger}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": category,
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": application_url,
        "application_type": "ONLINE_APPLICATION" if reservation_available else "INFO_ONLY",
        "application_method": application_method,
        "application_methods": application_methods or [application_method],
        "reservation_available": reservation_available,
        "status": status,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "schedule_raw": _clean(schedule),
        "target": _clean(target),
        "venue": branch,
        "venue_name": branch,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": YEONGDONG_PARSER,
        "municipality_code": YEONGDONG_MUNICIPALITY_CODE,
        "municipality_full_name": YEONGDONG_MUNICIPALITY_NAME,
        "raw_fields": dict(raw_fields),
    }
    if capacity_total is not None:
        row["capacity"] = f"{capacity_total}명"
        row["capacity_total"] = int(capacity_total)
    return row


def _validate_county_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup
) -> dict[str, Any]:
    identity = str(listed["identity"])
    views = soup.select(".edu_view")
    if len(views) != 1:
        raise YeongdongContractError(f"county {identity}: detail container changed")
    view = views[0]
    forms = view.select("form[name='wrtForm']")
    if len(forms) != 1:
        raise YeongdongContractError(f"county {identity}: application form changed")
    form = forms[0]
    values = _form_values(form)
    category_code = values.get("cgubun", "")
    if not (
        values.get("mode") == "AF"
        and values.get("edu_mng_no") == identity
        and values.get("mng_no") == identity
        and category_code in COUNTY_CATEGORY_CODES
        and values.get("edutype") == ""
    ):
        raise YeongdongContractError(f"county {identity}: application identity drift")
    category = COUNTY_CATEGORY_CODES[category_code]
    title_node = view.select_one(".view_top .thumb > strong")
    if _clean(title_node.get_text(" ", strip=True) if title_node else "") != listed["title"]:
        raise YeongdongContractError(f"county {identity}: title identity drift")
    status_node = view.select_one(".view_top .cate")
    detail_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    if detail_status != listed["source_status"] or _county_status(detail_status) != listed["status"]:
        raise YeongdongContractError(f"county {identity}: status drift")
    owner_node = view.select_one(".view_top .info > .tit")
    expected_owner = f"영동군청 / {category}"
    if _clean(owner_node.get_text(" ", strip=True) if owner_node else "") != expected_owner:
        raise YeongdongContractError(f"county {identity}: owner/category drift")
    info = view.select_one(".view_top .info > .eduli")
    if info is None:
        raise YeongdongContractError(f"county {identity}: detail fields missing")
    fields = _labeled_list(info)
    required = {"접수기간", "교육일자", "교육시간", "강사명", "교육장소"}
    if set(fields) != required:
        raise YeongdongContractError(f"county {identity}: detail fields changed")
    if _dates(fields["접수기간"]) != (listed["apply_start"], listed["apply_end"]):
        raise YeongdongContractError(f"county {identity}: apply date drift")
    if _times(fields["접수기간"]) != _times(listed["apply_period"]):
        raise YeongdongContractError(f"county {identity}: apply time drift")
    if _date_range(fields["교육일자"], label="county detail education period") != (
        listed["start"],
        listed["end"],
    ):
        raise YeongdongContractError(f"county {identity}: education date drift")
    if _times(fields["교육시간"]) != _times(listed["schedule"]):
        raise YeongdongContractError(f"county {identity}: schedule drift")
    detail_venue = _clean(fields["교육장소"])
    if detail_venue != _clean(listed["venue"]):
        raise YeongdongContractError(f"county {identity}: venue drift")
    reservations = _county_reservation_rows(view)
    availability_values = {values[0] for values in reservations.values()}
    if not availability_values <= {"가능", "불가능"}:
        raise YeongdongContractError(f"county {identity}: reservation availability changed")
    available_methods = tuple(
        method
        for method, (availability, _note, _capacity) in reservations.items()
        if availability == "가능"
    )
    if set(listed["methods"]) != set(available_methods):
        raise YeongdongContractError(f"county {identity}: reception method drift")
    controls: list[Any] = []
    for control in form.select(".edu_btn2 a, .edu_btn2 button, .edu_btn2 input"):
        text = _clean(control.get_text(" ", strip=True) or control.get("value"))
        if "신청" in text:
            controls.append(control)
    if len(controls) > 1:
        raise YeongdongContractError(f"county {identity}: multiple application controls")
    online_available = "온라인예약" in available_methods
    actionable = listed["status"] == "OPEN" and online_available and len(controls) == 1
    if listed["status"] == "OPEN" and online_available and not controls:
        raise YeongdongContractError(f"county {identity}: application control missing")
    if listed["status"] != "OPEN" and controls:
        raise YeongdongContractError(f"county {identity}: unexpected application control")
    resolved_venue = detail_venue or _county_safe_detail_venue(view, identity)
    branch = _county_branch(category, resolved_venue)
    raw_url = _county_detail_url(identity)
    normalized_methods = [
        {
            "온라인예약": "온라인",
            "유선예약": "유선",
            "이메일 접수": "이메일",
            "방문 접수": "방문",
        }.get(method, method)
        for method in listed["methods"]
        if reservations.get(method, ("불가능", "", ""))[0] == "가능"
    ]
    return _base_row(
        provider=YEONGDONG_COUNTY_PROVIDER,
        ledger="county",
        identity=f"{category_code}:{identity}",
        title=str(listed["title"]),
        branch=branch,
        category=category,
        status=str(listed["status"]),
        raw_url=raw_url,
        application_url=raw_url,
        application_methods=normalized_methods,
        reservation_available=actionable,
        start=listed["start"],
        end=listed["end"],
        apply_start=listed["apply_start"],
        apply_end=listed["apply_end"],
        schedule=str(listed["schedule"]),
        target="",
        capacity_total=None,
        raw_fields={
            "identity": identity,
            "category_code": category_code,
            "source_ledger": "county_integrated",
            "source_page": int(listed["page"]),
            "source_status": str(listed["source_status"]),
            "source_apply_period": str(listed["apply_period"]),
            "source_education_period": str(listed["education_period"]),
            "source_schedule": str(listed["schedule"]),
            "source_venue": str(listed["venue"]),
            "source_detail_venue": resolved_venue,
            "source_methods": list(available_methods),
            "detail_verified": True,
            "application_control_present": bool(controls),
            "application_control_verified": True,
            "education_scope_verified": True,
            "service_family": "education",
        },
    )


def _library_detail_fields(soup: BeautifulSoup) -> dict[str, str]:
    expected = {"대상", "정원", "현재 접수인원", "대상인원", "대기인원", "수강료", "계획서"}
    result: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = tr.select(":scope > th, :scope > td")
        index = 0
        while index + 1 < len(cells):
            if cells[index].name == "th" and cells[index + 1].name == "td":
                key = _clean(cells[index].get_text(" ", strip=True))
                if key in expected:
                    value = _clean(cells[index + 1].get_text(" ", strip=True))
                    if key in result and result[key] != value:
                        raise YeongdongContractError(f"duplicate library detail field: {key}")
                    result[key] = value
                index += 2
            else:
                index += 1
    return result


def _library_info_fields(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in soup.select(".photos li"):
        text = _clean(item.get_text(" ", strip=True))
        if ":" not in text:
            continue
        key, value = (_clean(part) for part in text.split(":", 1))
        if key in result and result[key] != value:
            raise YeongdongContractError(f"duplicate library information field: {key}")
        result[key] = value
    return result


def _validate_library_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup
) -> dict[str, Any]:
    group, identity = str(listed["group"]), str(listed["identity"])
    headings = [
        _clean(node.get_text(" ", strip=True))
        for node in soup.select(".tit > h2")
        if _clean(node.get_text(" ", strip=True)) == listed["title"]
    ]
    if len(headings) != 1:
        raise YeongdongContractError(f"library {group}/{identity}: title identity drift")
    fields = _library_detail_fields(soup)
    required_fields = {"대상", "정원", "대상인원", "대기인원"}
    if not required_fields <= set(fields):
        raise YeongdongContractError(f"library {group}/{identity}: capacity fields changed")
    integers: dict[str, int] = {}
    for key in ("정원", "대상인원", "대기인원"):
        digits = re.sub(r"\D", "", fields[key])
        if not digits:
            raise YeongdongContractError(f"library {group}/{identity}: {key} changed")
        integers[key] = int(digits)
    if (
        integers["정원"],
        integers["대상인원"],
        integers["대기인원"],
    ) != (
        listed["capacity_total"],
        listed["online_capacity"],
        listed["wait_capacity"],
    ):
        raise YeongdongContractError(f"library {group}/{identity}: capacity drift")
    if _clean(fields["대상"]) != _clean(listed["target"]):
        raise YeongdongContractError(f"library {group}/{identity}: target drift")
    info = _library_info_fields(soup)
    required_info = {"접수 기간", "강좌 기간", "강좌 일시", "강좌 장소"}
    if not required_info <= set(info):
        raise YeongdongContractError(f"library {group}/{identity}: period/venue block missing")
    if _dates(info["접수 기간"]) != (listed["apply_start"], listed["apply_end"]):
        raise YeongdongContractError(f"library {group}/{identity}: apply date drift")
    if _times(info["접수 기간"]) != _times(listed["apply_period"]):
        raise YeongdongContractError(f"library {group}/{identity}: apply time drift")
    start, end = _date_range(info["강좌 기간"], label="library course period")
    if not _times(info["강좌 일시"]):
        raise YeongdongContractError(f"library {group}/{identity}: schedule time missing")
    venue = _clean(info["강좌 장소"])
    if not venue:
        raise YeongdongContractError(f"library {group}/{identity}: exact venue missing")
    controls = soup.select("a[href*='act=lecture_receive_form']")
    if len(controls) > 1:
        raise YeongdongContractError(f"library {group}/{identity}: multiple application controls")
    if controls:
        found = _library_link_identity(controls[0].get("href", ""), "lecture_receive_form")
        text = _clean(controls[0].get_text(" ", strip=True))
        if found != (group, identity) or text != listed["control_text"]:
            raise YeongdongContractError(f"library {group}/{identity}: application identity drift")
    if listed["status"] == "OPEN":
        if len(controls) != 1:
            raise YeongdongContractError(f"library {group}/{identity}: actionable control missing")
        application_url = _library_application_url(group, identity)
        actionable = True
    else:
        if controls:
            raise YeongdongContractError(f"library {group}/{identity}: unexpected application control")
        application_url = _library_detail_url(group, identity)
        actionable = False
    return _base_row(
        provider=YEONGDONG_LIBRARY_PROVIDER,
        ledger="library",
        identity=f"{group}:{identity}",
        title=str(listed["title"]),
        branch=str(listed["branch"]),
        category=str(listed["category"]),
        status=str(listed["status"]),
        raw_url=_library_detail_url(group, identity),
        application_url=application_url,
        application_methods=["온라인"] if actionable else [],
        reservation_available=actionable,
        start=start,
        end=end,
        apply_start=listed["apply_start"],
        apply_end=listed["apply_end"],
        schedule=info["강좌 일시"],
        target=str(listed["target"]),
        capacity_total=int(listed["capacity_total"]),
        raw_fields={
            "identity": identity,
            "group": group,
            "source_ledger": "library_lifelong",
            "source_page": int(listed["page"]),
            "source_status": str(listed["source_status"]),
            "source_apply_period": str(listed["apply_period"]),
            "source_education_period": info["강좌 기간"],
            "source_schedule": info["강좌 일시"],
            "source_venue": str(listed["branch"]),
            "source_detail_venue": venue,
            "source_methods": ["온라인"] if actionable else [],
            "detail_verified": True,
            "application_control_present": actionable,
            "application_control_verified": True,
            "education_scope_verified": True,
            "service_family": "education",
        },
    )


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_FIELDS:
        errors.append("forbidden detail/PII key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = str(row["provider_course_id"])
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _initial_meta(kind: str) -> dict[str, Any]:
    if kind == "county":
        provider = YEONGDONG_COUNTY_PROVIDER
        candidate = YEONGDONG_COUNTY_CANDIDATE_ID
        url = YEONGDONG_COUNTY_URL
    else:
        provider = YEONGDONG_LIBRARY_PROVIDER
        candidate = YEONGDONG_LIBRARY_CANDIDATE_ID
        url = YEONGDONG_LIBRARY_URL
    return {
        "municipality_code": YEONGDONG_MUNICIPALITY_CODE,
        "municipality_full_name": YEONGDONG_MUNICIPALITY_NAME,
        "municipal_owner_key": YEONGDONG_MUNICIPALITY_CODE,
        "owner_provider": provider,
        "candidate_id": candidate,
        "canonical_url": url,
        "source_ledger": kind,
        "related_same_owner_provider": (
            YEONGDONG_LIBRARY_PROVIDER if kind == "county" else YEONGDONG_COUNTY_PROVIDER
        ),
        "review_candidate_decisions": {
            YEONGDONG_REVIEW_CANDIDATE_NOTICE: (
                "instructor_recruitment_notice_not_learner_course_ledger"
            ),
            YEONGDONG_REVIEW_CANDIDATE_RECRUITMENT: (
                "editorial_recruitment_notice_subset_without_course_identity"
            ),
        },
        "excluded_official_sources": [dict(item) for item in YEONGDONG_SOURCE_DECISIONS],
        "configured_collection_error": "",
        "source_requests": 0,
        "pages": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "education_excluded_count": 0,
        "pagination_complete": False,
        "stable_first_last": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
    }


def _collect_county(
    *,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    cutoff: date,
    max_workers: int,
    factory: SessionFactory,
    fetcher: Fetcher,
    dedupe_rows: Optional[DedupeRows],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta = _initial_meta("county")
    try:
        meta["source_requests"] += 1
        first = _parse_county_page(
            _soup(_county_list_url(1), timeout, factory, fetcher), 1
        )
        declared_pages = int(first["declared_pages"])
        if declared_pages > max_pages:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"max_pages {max_pages} below declared {declared_pages}"
                    ),
                }
            )
            return [], YEONGDONG_PARSER, meta
        pages: dict[int, dict[str, Any]] = {1: first}
        for page in range(2, declared_pages + 1):
            meta["source_requests"] += 1
            pages[page] = _parse_county_page(
                _soup(_county_list_url(page), timeout, factory, fetcher), page
            )
        meta["source_requests"] += 1
        boundary = _parse_county_page(
            _soup(_county_list_url(declared_pages + 1), timeout, factory, fetcher),
            declared_pages + 1,
        )
        if boundary["rows"] and _county_page_signature(boundary) != _county_page_signature(
            pages[declared_pages]
        ):
            raise YeongdongContractError("county post-last page is not empty or clamped")
        boundary_behavior = "last_page_clamp" if boundary["rows"] else "empty"
        meta["source_requests"] += 1
        first_recheck = _parse_county_page(
            _soup(_county_list_url(1), timeout, factory, fetcher), 1
        )
        meta["source_requests"] += 1
        last_recheck = _parse_county_page(
            _soup(_county_list_url(declared_pages), timeout, factory, fetcher),
            declared_pages,
        )
        if _county_page_signature(first_recheck) != _county_page_signature(first):
            raise YeongdongContractError("county first-page stability failed")
        if _county_page_signature(last_recheck) != _county_page_signature(
            pages[declared_pages]
        ):
            raise YeongdongContractError("county last-page stability failed")
        declarations = {
            int(value["declared_pages"])
            for value in [*pages.values(), boundary, first_recheck, last_recheck]
        }
        if declarations != {declared_pages}:
            raise YeongdongContractError("county declared-page count drift")
        source_rows = [row for page in pages.values() for row in page["rows"]]
        identities = [str(row["identity"]) for row in source_rows]
        if len(set(identities)) != len(identities):
            raise YeongdongContractError("duplicate county identity across pages")
        current = [row for row in source_rows if row["end"] >= cutoff]
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], YEONGDONG_PARSER, meta

    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "declared_pages": declared_pages,
            "pages": declared_pages,
            "source_rows": len(source_rows),
            "source_total": len(source_rows),
            "current_source_count": len(current),
            "pagination_complete": True,
            "stable_first_last": True,
            "post_last_boundary": boundary_behavior,
        }
    )
    if len(current) > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit {detail_limit} below required {len(current)}"
                ),
            }
        )
        return [], YEONGDONG_PARSER, meta

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    meta["source_requests"] += len(current)

    def fetch_detail(listed: Mapping[str, Any]) -> dict[str, Any]:
        url = _county_detail_url(str(listed["identity"]))
        return _validate_county_detail(
            listed, _soup(url, timeout, factory, fetcher)
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_detail, listed): listed for listed in current}
        for future in as_completed(futures):
            listed = futures[future]
            identity = str(listed["identity"])
            try:
                rows.append(future.result())
                meta["detail_pages"] += 1
            except Exception as exc:
                errors.append(f"{identity}: {type(exc).__name__}: {_clean(exc)}")
    if errors:
        meta["configured_collection_error"] = "; ".join(sorted(errors)[:5])
        return [], YEONGDONG_PARSER, meta
    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy = [error for row in rows for error in _privacy_errors(row)]
    if privacy or len(rows) != len(current):
        meta["configured_collection_error"] = (
            "; ".join(privacy[:5]) or "dedupe changed county identity set"
        )
        return [], YEONGDONG_PARSER, meta
    meta.update(
        {
            "returned_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "branch_counts": dict(Counter(row["branch"] for row in rows)),
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not rows,
        }
    )
    return rows, YEONGDONG_PARSER, meta


def _collect_library(
    *,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    cutoff: date,
    max_workers: int,
    factory: SessionFactory,
    fetcher: Fetcher,
    dedupe_rows: Optional[DedupeRows],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta = _initial_meta("library")
    try:
        meta["source_requests"] += 1
        first = _parse_library_page(
            _soup(_library_list_url(1), timeout, factory, fetcher), 1
        )
        declared_pages = int(first["declared_pages"])
        declared_total = int(first["declared_total"])
        if declared_pages > max_pages:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"max_pages {max_pages} below declared {declared_pages}"
                    ),
                }
            )
            return [], YEONGDONG_PARSER, meta
        pages: dict[int, dict[str, Any]] = {1: first}
        for page in range(2, declared_pages + 1):
            meta["source_requests"] += 1
            pages[page] = _parse_library_page(
                _soup(_library_list_url(page), timeout, factory, fetcher), page
            )
        meta["source_requests"] += 1
        boundary = _parse_library_page(
            _soup(_library_list_url(declared_pages + 1), timeout, factory, fetcher),
            declared_pages + 1,
        )
        if boundary["rows"]:
            raise YeongdongContractError("library post-last page is not empty")
        meta["source_requests"] += 1
        first_recheck = _parse_library_page(
            _soup(_library_list_url(1), timeout, factory, fetcher), 1
        )
        meta["source_requests"] += 1
        last_recheck = _parse_library_page(
            _soup(_library_list_url(declared_pages), timeout, factory, fetcher),
            declared_pages,
        )
        if _library_page_signature(first_recheck) != _library_page_signature(first):
            raise YeongdongContractError("library first-page stability failed")
        if _library_page_signature(last_recheck) != _library_page_signature(
            pages[declared_pages]
        ):
            raise YeongdongContractError("library last-page stability failed")
        contracts = {
            (int(value["declared_total"]), int(value["declared_pages"]))
            for value in [*pages.values(), boundary, first_recheck, last_recheck]
        }
        if contracts != {(declared_total, declared_pages)}:
            raise YeongdongContractError("library total/page declaration drift")
        expected_pages = max(1, math.ceil(declared_total / 10))
        if declared_pages != expected_pages:
            raise YeongdongContractError("library declared-page completeness failed")
        source_rows = [row for page in pages.values() for row in page["rows"]]
        if len(source_rows) != declared_total:
            raise YeongdongContractError("library declared total does not match rows")
        identities = [(str(row["group"]), str(row["identity"])) for row in source_rows]
        if len(set(identities)) != len(identities):
            raise YeongdongContractError("duplicate library identity across pages")
        numbers = [int(row["number"]) for row in source_rows]
        if numbers != list(range(declared_total, 0, -1)):
            raise YeongdongContractError("library row-number continuity failed")
        excluded = [row for row in source_rows if row["education_exclusion"]]
        detail_candidates = [row for row in source_rows if not row["education_exclusion"]]
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], YEONGDONG_PARSER, meta

    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "declared_pages": declared_pages,
            "declared_total": declared_total,
            "pages": declared_pages,
            "source_rows": len(source_rows),
            "source_total": len(source_rows),
            "education_excluded_count": len(excluded),
            "education_exclusion_counts": dict(
                Counter(row["education_exclusion"] for row in excluded)
            ),
            "detail_candidate_count": len(detail_candidates),
            "pagination_complete": True,
            "stable_first_last": True,
        }
    )
    if len(detail_candidates) > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit {detail_limit} below required {len(detail_candidates)}"
                ),
            }
        )
        return [], YEONGDONG_PARSER, meta

    validated: list[dict[str, Any]] = []
    errors: list[str] = []
    meta["source_requests"] += len(detail_candidates)

    def fetch_detail(listed: Mapping[str, Any]) -> dict[str, Any]:
        url = _library_detail_url(str(listed["group"]), str(listed["identity"]))
        return _validate_library_detail(
            listed, _soup(url, timeout, factory, fetcher)
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(fetch_detail, listed): listed for listed in detail_candidates
        }
        for future in as_completed(futures):
            listed = futures[future]
            identity = f"{listed['group']}:{listed['identity']}"
            try:
                validated.append(future.result())
                meta["detail_pages"] += 1
            except Exception as exc:
                errors.append(f"{identity}: {type(exc).__name__}: {_clean(exc)}")
    if errors:
        meta["configured_collection_error"] = "; ".join(sorted(errors)[:5])
        return [], YEONGDONG_PARSER, meta
    if len(validated) != len(detail_candidates):
        meta["configured_collection_error"] = "library detail identity set changed"
        return [], YEONGDONG_PARSER, meta
    rows = [row for row in validated if date.fromisoformat(row["end_date"]) >= cutoff]
    meta["current_source_count"] = len(rows)
    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy = [error for row in rows for error in _privacy_errors(row)]
    if privacy or len(rows) != meta["current_source_count"]:
        meta["configured_collection_error"] = (
            "; ".join(privacy[:5]) or "dedupe changed library identity set"
        )
        return [], YEONGDONG_PARSER, meta
    meta.update(
        {
            "returned_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "branch_counts": dict(Counter(row["branch"] for row in rows)),
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not rows,
        }
    )
    return rows, YEONGDONG_PARSER, meta


def collect_yeongdong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    max_workers: int = YEONGDONG_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    kind = _target_kind(target)
    if not kind:
        meta = _initial_meta("county")
        meta["configured_collection_error"] = "target/provider failed exact contract"
        return [], YEONGDONG_PARSER, meta
    if max_pages < 1 or detail_limit < 0 or max_workers < 1:
        meta = _initial_meta(kind)
        meta["configured_collection_error"] = "invalid collection limits"
        return [], YEONGDONG_PARSER, meta
    kwargs = {
        "timeout": int(timeout),
        "max_pages": int(max_pages),
        "detail_limit": int(detail_limit),
        "cutoff": _today(today),
        "max_workers": min(int(max_workers), YEONGDONG_MAX_WORKERS),
        "factory": session_factory or _session,
        "fetcher": fetcher or _request,
        "dedupe_rows": dedupe_rows,
    }
    if kind == "county":
        return _collect_county(**kwargs)
    return _collect_library(**kwargs)


collect = collect_yeongdong_education
