"""Fail-closed collector for Cheongsong Youth Training Center courses.

The promoted search candidate for Cheongsong is an unofficial ``gusle.kr``
homepage guide.  It does not own course identities and must never execute.
The actual public ledger is the Youth Training Center's
``educationList.php`` catalogue, linked directly from the county homepage.

The catalogue uses fixed 15-row pages and emits an exact empty-table sentinel
after the final page.  This collector walks through that sentinel, rechecks
page one and the sentinel, and verifies every current/future detail page.  An
open application is accepted only when the detail exposes the historic,
identity-bound ``applicationForm.php?yp_id=...`` control.  That form and the
name/phone/password application lookup endpoint are deliberately never
requested.  Free-form course content, images, contacts, instructor data and
applicant data are neither returned nor retained in ``raw_fields``.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


CHEONGSONG_PROVIDER = "MUNI_WWW_FUTURECSY_OR_KR_D9EE9C9C"
CHEONGSONG_CANONICAL_CANDIDATE_ID = "MUNI_IR_353943CBEEF1"
CHEONGSONG_REJECTED_CANDIDATE_ID = "MUNI_IR_929A4889D565"
CHEONGSONG_MUNICIPALITY_CODE = "4775000000"
CHEONGSONG_MUNICIPALITY_NAME = "경상북도 청송군"
CHEONGSONG_BRANCH = "청송군청소년수련관"
CHEONGSONG_BRANCH_ADDRESS = "경북 청송군 청송읍 복지타운길 77"

CHEONGSONG_HOST = "www.futurecsy.or.kr"
CHEONGSONG_LIST_PATH = "/board/bbs/educationList.php"
CHEONGSONG_DETAIL_PATH = "/board/bbs/educationView.php"
CHEONGSONG_APPLICATION_PATH = "/board/bbs/applicationForm.php"
CHEONGSONG_CANONICAL_URL = f"https://{CHEONGSONG_HOST}{CHEONGSONG_LIST_PATH}"
CHEONGSONG_APPLICATION_CHECK_URL = f"https://{CHEONGSONG_HOST}/board/bbs/application_check.php?mcode=3"
CHEONGSONG_OFFICIAL_PARENT_URL = "https://www.cs.go.kr/main.web"
CHEONGSONG_PAGE_SIZE = 15
CHEONGSONG_RECOMMENDED_MAX_PAGES = 10
CHEONGSONG_RECOMMENDED_DETAIL_LIMIT = 150
CHEONGSONG_MAX_HTML_BYTES = 2_000_000
CHEONGSONG_PARSER = (
    "cheongsong_youth_complete_15_row_pages+exact_empty_sentinel+"
    "stable_page1_and_sentinel_rechecks+all_current_details+"
    "identity_bound_application_controls+official_single_branch+"
    "no_application_or_pii_lookup_fetch+pii_allowlist"
)

# These URLs are audit boundaries, not additional executable catalogues.
CHEONGSONG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    CHEONGSONG_CANONICAL_CANDIDATE_ID: {
        "decision": "canonical_complete_owner",
        "provider": CHEONGSONG_PROVIDER,
        "url": CHEONGSONG_CANONICAL_URL,
        "owner": CHEONGSONG_PROVIDER,
    },
    CHEONGSONG_REJECTED_CANDIDATE_ID: {
        "decision": "excluded_unofficial_third_party_homepage_guide",
        "provider": "MUNI_GUSLE_KR_1D7285A1",
        "url": "https://gusle.kr/cheongsong-gov-homepage/",
        "owner": "",
    },
    "MUNI_IR_7FF0965B1002": {
        "decision": "excluded_voucher_announcement_not_course_catalogue",
        "provider": "MUNI_WWW_CS_GO_KR_BC111689",
        "url": ("https://www.cs.go.kr/news/00002679/00006203.web?amode=view&not_ancmt_mgt_no=28917"),
        "owner": "",
    },
}

CHEONGSONG_NON_EXECUTING_ALIASES: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://www.futurecsy.or.kr/board/bbs/educationList.php",
        "reason": "legacy_redirect_alias_of_canonical_https_owner",
        "owner": CHEONGSONG_PROVIDER,
    },
    {
        "url": "https://www.futurecsy.or.kr/board/bbs/educationgalleryList.php",
        "reason": "duplicate_gallery_presentation_of_same_yp_id_set",
        "owner": CHEONGSONG_PROVIDER,
    },
    {
        "url": CHEONGSONG_APPLICATION_CHECK_URL,
        "reason": "pii_bearing_application_lookup_not_course_catalogue",
        "owner": CHEONGSONG_PROVIDER,
    },
    {
        "url": "https://www.futurecsy.or.kr/board/bbs/educationList2.php",
        "reason": "separate_currently_empty_legacy_ledger_not_youth_catalogue",
        "owner": "",
    },
)

# Registered library facilities remain separate owners.  Their courses must
# not be merged into this youth-program provider merely because they share the
# same municipality code.
CHEONGSONG_SEPARATE_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "provider": "CULTURE_PUBLIC_LIBRARY_F6D02AADB1",
        "name": "경상북도교육청 청송도서관",
        "registered_url": "https://www.gbelib.kr/cs",
        "course_url": ("https://www.gbelib.kr/cs/module/teach/index.do?menu_idx=160&searchCate1=18"),
    },
    {
        "provider": "CULTURE_PUBLIC_LIBRARY_83B9B0F1E3",
        "name": "진보공공도서관",
        "registered_url": "https://jinbolib.cs.go.kr",
        "course_url": "https://jinbolib.cs.go.kr/page.do?mid=129",
    },
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class CheongsongContractError(ValueError):
    """Raised when an official page no longer satisfies the audited contract."""


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE_TOKEN = re.compile(r"(?<!\d)(?:(20\d{2}|\d{2})[.-])?(\d{1,2})[.-](\d{1,2})(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LIST_HEADERS = (
    "번호",
    "강좌명",
    "교육대상",
    "강좌일시",
    "정원",
    "후보",
    "접수",
    "접수현황",
)
_EMPTY_SENTINEL = "등록된 프로그램이 없습니다."
_LIST_STATUS = {"접수중": "OPEN", "마감": "CLOSED"}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_page",
        "source_sequence",
        "source_status",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_venue",
        "waitlist_capacity",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "application_endpoint_fetched",
        "pii_lookup_endpoint_fetched",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "contacts",
        "instructor",
        "instructor_name",
        "attachments",
        "attachment_urls",
        "detail_description",
        "course_content",
        "source_html",
        "raw_html",
        "image_url",
        "applicant_name",
        "applicant_phone",
        "password",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlparse(url).query, keep_blank_values=True, strict_parsing=True)


def is_cheongsong_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != CHEONGSONG_PROVIDER:
        return False
    url = _clean(_target_value(target, "url"))
    try:
        parsed = urlparse(url)
        port = parsed.port
        query = _query(url)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == CHEONGSONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == CHEONGSONG_LIST_PATH
        and not query
        and not parsed.fragment
        and url == CHEONGSONG_CANONICAL_URL
    )


is_target = is_cheongsong_education_target


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


def _list_url(page: int) -> str:
    if page == 1:
        return CHEONGSONG_CANONICAL_URL
    return f"{CHEONGSONG_CANONICAL_URL}?{urlencode({'page': page})}"


def _detail_url(identity: str) -> str:
    return f"https://{CHEONGSONG_HOST}{CHEONGSONG_DETAIL_PATH}?{urlencode({'yp_id': identity})}"


def _allowed_fetch_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
        query = _query(url)
    except ValueError:
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == CHEONGSONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        return False
    if parsed.path == CHEONGSONG_LIST_PATH:
        if not query:
            return True
        return bool(
            len(query) == 1
            and query[0][0] == "page"
            and _IDENTITY.fullmatch(query[0][1])
            and str(int(query[0][1])) == query[0][1]
        )
    return bool(
        parsed.path == CHEONGSONG_DETAIL_PATH
        and len(query) == 1
        and query[0][0] == "yp_id"
        and _IDENTITY.fullmatch(query[0][1])
    )


def _application_url(value: str, identity: str, base_url: str) -> str:
    url = urljoin(base_url, value)
    try:
        parsed = urlparse(url)
        query = _query(url)
        port = parsed.port
    except ValueError as exc:
        raise CheongsongContractError("malformed application control URL") from exc
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == CHEONGSONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == CHEONGSONG_APPLICATION_PATH
        and query == [("yp_id", identity)]
        and not parsed.fragment
    ):
        raise CheongsongContractError(f"course {identity}: application control is not identity-bound")
    return url


def _soup(session: Any, url: str, timeout: int, fetcher: Fetcher) -> BeautifulSoup:
    if not _allowed_fetch_url(url):
        raise CheongsongContractError("non-canonical fetch URL refused")
    response = fetcher(session, url, timeout)
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", response)).encode("utf-8")
    if not isinstance(content, (bytes, bytearray)):
        content = bytes(content)
    if len(content) > CHEONGSONG_MAX_HTML_BYTES:
        raise CheongsongContractError("HTML size cap exceeded")
    final_url = str(getattr(response, "url", url))
    if not _allowed_fetch_url(final_url):
        raise CheongsongContractError("redirect outside canonical public pages")
    try:
        html = bytes(content).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CheongsongContractError("official page is no longer strict UTF-8") from exc
    soup = BeautifulSoup(html, "html.parser")
    page_text = _clean(soup.get_text(" ", strip=True))
    if CHEONGSONG_BRANCH not in page_text or CHEONGSONG_BRANCH_ADDRESS not in page_text:
        raise CheongsongContractError("official owner name/address evidence missing")
    return soup


def _date_period(value: str, *, default_year: Optional[int] = None) -> tuple[date, date]:
    tokens = list(_DATE_TOKEN.finditer(_clean(value)))
    if not 1 <= len(tokens) <= 2:
        raise CheongsongContractError(f"unsupported date period: {_clean(value)!r}")
    parsed: list[date] = []
    inherited_year = default_year
    for token in tokens:
        year_text, month_text, day_text = token.groups()
        if year_text:
            year = int(year_text)
            if year < 100:
                year += 2000
            inherited_year = year
        elif inherited_year is None:
            raise CheongsongContractError(f"date period lacks an anchor year: {_clean(value)!r}")
        else:
            year = inherited_year
        try:
            current = date(year, int(month_text), int(day_text))
        except ValueError as exc:
            raise CheongsongContractError(f"invalid date in period: {_clean(value)!r}") from exc
        if parsed and not year_text and current < parsed[-1]:
            try:
                current = current.replace(year=current.year + 1)
            except ValueError as exc:
                raise CheongsongContractError(f"invalid rollover date: {_clean(value)!r}") from exc
            inherited_year = current.year
        parsed.append(current)
    if len(parsed) == 1:
        parsed.append(parsed[0])
    if parsed[1] < parsed[0]:
        raise CheongsongContractError(f"reversed date period: {_clean(value)!r}")
    return parsed[0], parsed[1]


def _month_day_signature(value: str) -> tuple[tuple[int, int], ...]:
    tokens = list(_DATE_TOKEN.finditer(_clean(value)))
    if not 1 <= len(tokens) <= 2:
        raise CheongsongContractError(f"unsupported short date period: {_clean(value)!r}")
    values = tuple((int(item.group(2)), int(item.group(3))) for item in tokens)
    return values if len(values) == 2 else (values[0], values[0])


def _parse_list_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    tables = soup.select("table.ed_list")
    if len(tables) != 1:
        raise CheongsongContractError(f"page {page}: exact course table missing")
    table = tables[0]
    caption = _clean(table.caption.get_text(" ", strip=True) if table.caption else "")
    if caption != "온라인수강신청 (비회원도 신청가능합니다) 목록":
        raise CheongsongContractError(f"page {page}: catalogue caption drift")
    header_row = table.find("tr")
    headers = (
        tuple(_clean(item.get_text(" ", strip=True)) for item in header_row.find_all("th", recursive=False))
        if header_row
        else ()
    )
    if headers != _LIST_HEADERS:
        raise CheongsongContractError(f"page {page}: catalogue headers drift")

    empty_cells = table.select("td.empty_table")
    course_rows = []
    for tr in table.find_all("tr"):
        link = tr.select_one("a[href*='educationView.php']")
        if link is not None:
            course_rows.append((tr, link))
    if empty_cells:
        if len(empty_cells) != 1 or _clean(empty_cells[0].get_text(" ", strip=True)) != _EMPTY_SENTINEL or course_rows:
            raise CheongsongContractError(f"page {page}: malformed empty sentinel")
        return {"page": page, "rows": [], "empty": True}
    if not course_rows:
        raise CheongsongContractError(f"page {page}: neither courses nor exact sentinel")
    if len(course_rows) > CHEONGSONG_PAGE_SIZE:
        raise CheongsongContractError(f"page {page}: fixed page size exceeded")

    parsed_rows: list[dict[str, Any]] = []
    page_ids: set[str] = set()
    for tr, link in course_rows:
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 8:
            raise CheongsongContractError(f"page {page}: course column count drift")
        sequence = _clean(cells[0].get_text(" ", strip=True))
        title = _clean(link.get_text(" ", strip=True))
        target = _clean(cells[2].get_text(" ", strip=True))
        periods = _clean(cells[3].get_text(" ", strip=True))
        match = re.fullmatch(r"접수기간\s*(.+?)\s*교육기간\s*(.+)", periods)
        if not match:
            raise CheongsongContractError(f"page {page}: period labels drift")
        apply_text, education_text = (_clean(value) for value in match.groups())
        start, end = _date_period(education_text)
        capacity = _clean(cells[4].get_text(" ", strip=True))
        waitlist = _clean(cells[5].get_text(" ", strip=True))
        applicants = _clean(cells[6].get_text(" ", strip=True))
        source_status = _clean(cells[7].get_text(" ", strip=True))
        normalized_status = source_status.replace(" ", "")
        if not sequence.isdigit() or not title or not target:
            raise CheongsongContractError(f"page {page}: required list identity missing")
        if not capacity.isdigit() or not waitlist.isdigit() or not applicants.isdigit():
            raise CheongsongContractError(f"page {page}: capacity fields changed")
        if normalized_status not in _LIST_STATUS:
            raise CheongsongContractError(f"page {page}: unsupported source status {source_status!r}")
        detail_url = urljoin(CHEONGSONG_CANONICAL_URL, str(link.get("href") or ""))
        if not _allowed_fetch_url(detail_url) or urlparse(detail_url).path != CHEONGSONG_DETAIL_PATH:
            raise CheongsongContractError(f"page {page}: unsafe detail URL")
        identity = dict(_query(detail_url)).get("yp_id", "")
        if not _IDENTITY.fullmatch(identity) or identity in page_ids:
            raise CheongsongContractError(f"page {page}: duplicate/invalid course identity")
        page_ids.add(identity)
        parsed_rows.append(
            {
                "identity": identity,
                "page": page,
                "sequence": int(sequence),
                "title": title,
                "target": target,
                "apply_text": apply_text,
                "education_text": education_text,
                "start": start,
                "end": end,
                "capacity_total": int(capacity),
                "waitlist_capacity": int(waitlist),
                "capacity_current": int(applicants),
                "source_status": source_status,
                "status": _LIST_STATUS[normalized_status],
                "detail_url": detail_url,
            }
        )
    return {"page": page, "rows": parsed_rows, "empty": False}


def _page_signature(parsed: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        bool(parsed["empty"]),
        tuple(
            (
                row["identity"],
                row["title"],
                row["education_text"],
                row["source_status"],
                row["capacity_total"],
                row["capacity_current"],
            )
            for row in parsed["rows"]
        ),
    )


def _detail_fields(table: Any, identity: str) -> dict[str, str]:
    rows = table.find_all("tr", recursive=False)
    if len(rows) < 9:
        raise CheongsongContractError(f"course {identity}: detail table truncated")
    fields: dict[str, str] = {}
    for tr in rows[1:8]:
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 1:
            raise CheongsongContractError(f"course {identity}: detail field shape drift")
        text = _clean(cells[0].get_text(" ", strip=True))
        if ":" not in text:
            raise CheongsongContractError(f"course {identity}: detail label missing")
        label, value = (_clean(item) for item in text.split(":", 1))
        if not label or label in fields:
            raise CheongsongContractError(f"course {identity}: duplicate detail label")
        fields[label] = value
    required = {"강좌명", "교육대상", "교육기간", "교육시간", "교육장소", "접수기간", "정원"}
    if set(fields) != required:
        raise CheongsongContractError(f"course {identity}: required detail fields drift")
    return fields


def _control(foot: Any, identity: str, detail_url: str) -> tuple[bool, str, str]:
    primary = []
    for anchor in foot.find_all("a"):
        classes = set(anchor.get("class") or [])
        if classes & {"ap_btn", "ap_end"}:
            primary.append(anchor)
    if len(primary) != 1:
        raise CheongsongContractError(f"course {identity}: application state ambiguous")
    anchor = primary[0]
    classes = set(anchor.get("class") or [])
    text = _clean(anchor.get_text(" ", strip=True)).replace(" ", "")
    href = _clean(anchor.get("href"))
    if "ap_btn" in classes:
        if text != "신청하기" or not href:
            raise CheongsongContractError(f"course {identity}: open control drift")
        return True, _application_url(href, identity, detail_url), "신청하기"
    if "ap_end" in classes:
        if text != "신청마감" or href:
            raise CheongsongContractError(f"course {identity}: closed control drift")
        return False, "", "신청마감"
    raise CheongsongContractError(f"course {identity}: unsupported application state")


def _branch_code() -> str:
    digest = hashlib.sha1(CHEONGSONG_BRANCH.encode("utf-8")).hexdigest()[:12].upper()
    return f"CHEONGSONG_{digest}"


def _detail(listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    identity = str(listed["identity"])
    tables = soup.select("table.ed_view")
    feet = soup.select("div.ed_view_foot")
    if len(tables) != 1 or len(feet) != 1:
        raise CheongsongContractError(f"course {identity}: exact detail structure missing")
    table = tables[0]
    first_row = table.find("tr")
    first_cells = first_row.find_all("td", recursive=False) if first_row else []
    if len(first_cells) != 2:
        raise CheongsongContractError(f"course {identity}: detail title row drift")
    heading = _clean(first_cells[1].get_text(" ", strip=True))
    image = first_cells[0].find("img", src=True)
    if heading != listed["title"] or image is None:
        raise CheongsongContractError(f"course {identity}: detail identity/title drift")
    image_url = urljoin(str(listed["detail_url"]), str(image.get("src") or ""))
    parsed_image = urlparse(image_url)
    if not (
        parsed_image.scheme == "https"
        and (parsed_image.hostname or "").lower() == CHEONGSONG_HOST
        and parsed_image.port in {None, 443}
        and parsed_image.path == f"/board/data/education/{identity}"
        and not parsed_image.query
        and not parsed_image.fragment
    ):
        raise CheongsongContractError(f"course {identity}: detail image identity drift")

    fields = _detail_fields(table, identity)
    if fields["강좌명"] != listed["title"] or fields["교육대상"] != listed["target"]:
        raise CheongsongContractError(f"course {identity}: detail title/target drift")
    start, end = _date_period(fields["교육기간"])
    if (start, end) != (listed["start"], listed["end"]) or end < cutoff:
        raise CheongsongContractError(f"course {identity}: detail education period drift")
    apply_start, apply_end = _date_period(fields["접수기간"])
    if _month_day_signature(fields["접수기간"]) != _month_day_signature(str(listed["apply_text"])):
        raise CheongsongContractError(f"course {identity}: list/detail application period drift")

    capacity_match = re.fullmatch(
        r"(\d+)\s*/\s*후보\s*:\s*(\d+)\s*/\s*접수\s*:\s*(\d+)",
        fields["정원"],
    )
    if not capacity_match:
        raise CheongsongContractError(f"course {identity}: detail capacity shape drift")
    capacity_total, waitlist_capacity, capacity_current = map(int, capacity_match.groups())
    if (
        capacity_total != listed["capacity_total"]
        or waitlist_capacity != listed["waitlist_capacity"]
        or capacity_current != listed["capacity_current"]
    ):
        raise CheongsongContractError(f"course {identity}: list/detail capacity drift")

    control_present, control_url, control_text = _control(feet[0], identity, str(listed["detail_url"]))
    status = str(listed["status"])
    if status == "OPEN":
        if not control_present or control_text != "신청하기":
            raise CheongsongContractError(f"course {identity}: open row lacks application control")
        if not apply_start <= cutoff <= apply_end:
            raise CheongsongContractError(f"course {identity}: open status/date disagreement")
    elif status == "CLOSED":
        if control_present or control_text != "신청마감":
            raise CheongsongContractError(f"course {identity}: closed row exposes application control")
    else:
        raise CheongsongContractError(f"course {identity}: unsupported normalized status")

    schedule = _clean(fields["교육시간"])
    venue = _clean(fields["교육장소"])
    target = _clean(fields["교육대상"])
    if not schedule or not venue or not target:
        raise CheongsongContractError(f"course {identity}: schedule/venue/target missing")
    if _PHONE.search(venue) or _EMAIL.search(venue):
        raise CheongsongContractError(f"course {identity}: venue contains contact data")

    raw_url = str(listed["detail_url"])
    return {
        "provider": CHEONGSONG_PROVIDER,
        "provider_course_id": f"{CHEONGSONG_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": CHEONGSONG_BRANCH,
        "branch_code": _branch_code(),
        "preserve_branch": True,
        "category": "청소년교육",
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": control_url if status == "OPEN" else raw_url,
        "application_type": "ONLINE_RESERVATION" if status == "OPEN" else "INFO_ONLY",
        "application_method": "온라인",
        "application_methods": ["온라인"],
        "reservation_available": bool(status == "OPEN" and control_present),
        "status": status,
        "fee": "",
        "fee_amount": None,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "schedule_raw": schedule,
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "target": target,
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": CHEONGSONG_PARSER,
        "municipality_code": CHEONGSONG_MUNICIPALITY_CODE,
        "municipality_full_name": CHEONGSONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(listed["page"]),
            "source_sequence": int(listed["sequence"]),
            "source_status": str(listed["source_status"]),
            "source_apply_period": fields["접수기간"],
            "source_education_period": fields["교육기간"],
            "source_schedule": schedule,
            "source_venue": venue,
            "waitlist_capacity": waitlist_capacity,
            "detail_verified": True,
            "application_control_present": control_present,
            "application_control_verified": True,
            "application_endpoint_fetched": False,
            "pii_lookup_endpoint_fetched": False,
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden detail/PII key")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    payload = repr({key: value for key, value in row.items() if key not in {"raw_url", "application_url"}})
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


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Seoul")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def collect_cheongsong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = CHEONGSONG_RECOMMENDED_MAX_PAGES,
    detail_limit: int = CHEONGSONG_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete atomic current/future Youth Training Center snapshot."""

    meta: dict[str, Any] = {
        "municipality_code": CHEONGSONG_MUNICIPALITY_CODE,
        "owner_provider": CHEONGSONG_PROVIDER,
        "canonical_candidate_id": CHEONGSONG_CANONICAL_CANDIDATE_ID,
        "rejected_candidate_id": CHEONGSONG_REJECTED_CANDIDATE_ID,
        "canonical_url": CHEONGSONG_CANONICAL_URL,
        "official_parent_url": CHEONGSONG_OFFICIAL_PARENT_URL,
        "official_branch": CHEONGSONG_BRANCH,
        "official_branch_address": CHEONGSONG_BRANCH_ADDRESS,
        "parser": CHEONGSONG_PARSER,
        "ownership_scope": "cheongsong_youth_training_center_online_course_yp_ids",
        "excluded_candidate_reason": "unofficial_third_party_homepage_guide_not_course_catalogue",
        "non_executing_aliases": [dict(item) for item in CHEONGSONG_NON_EXECUTING_ALIASES],
        "separate_owner_boundaries": [dict(item) for item in CHEONGSONG_SEPARATE_OWNER_BOUNDARIES],
        "recommended_max_pages": CHEONGSONG_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": CHEONGSONG_RECOMMENDED_DETAIL_LIMIT,
        "source_requests": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "nonempty_pages": 0,
        "sentinel_page": 0,
        "sentinel_rechecked": False,
        "page1_rechecked": False,
        "boundary_rechecks": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "application_endpoints_called": 0,
        "pii_lookup_endpoints_called": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }
    if not is_cheongsong_education_target(target):
        meta["configured_collection_error"] = "target does not match exact canonical Cheongsong owner"
        return [], CHEONGSONG_PARSER, meta
    try:
        cutoff = _today(today)
        if any(isinstance(value, bool) or int(value) < 1 for value in (timeout, max_pages)):
            raise ValueError("timeout and max_pages must be positive integers")
        if isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("detail_limit must be a non-negative integer")
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], CHEONGSONG_PARSER, meta

    session = (session_factory or _session)()
    current_fetcher = fetcher or _request
    pages: list[dict[str, Any]] = []
    try:
        sentinel: Optional[dict[str, Any]] = None
        for page in range(1, int(max_pages) + 1):
            parsed = _parse_list_page(_soup(session, _list_url(page), int(timeout), current_fetcher), page)
            meta["source_requests"] += 1
            meta["list_requests"] += 1
            if parsed["empty"]:
                sentinel = parsed
                meta["sentinel_page"] = page
                break
            pages.append(parsed)
        if sentinel is None:
            meta["source_cap_reached"] = True
            raise CheongsongContractError(f"max_pages {max_pages} reached before exact empty sentinel")
        for parsed in pages[:-1]:
            if len(parsed["rows"]) != CHEONGSONG_PAGE_SIZE:
                raise CheongsongContractError("non-final data page is not a full 15-row page")

        first_recheck = _parse_list_page(_soup(session, _list_url(1), int(timeout), current_fetcher), 1)
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        meta["boundary_rechecks"] += 1
        initial_first = pages[0] if pages else sentinel
        if _page_signature(first_recheck) != _page_signature(initial_first):
            raise CheongsongContractError("page-one stability recheck failed")
        meta["page1_rechecked"] = True

        if int(sentinel["page"]) == 1:
            meta["sentinel_rechecked"] = True
        else:
            sentinel_recheck = _parse_list_page(
                _soup(
                    session,
                    _list_url(int(sentinel["page"])),
                    int(timeout),
                    current_fetcher,
                ),
                int(sentinel["page"]),
            )
            meta["source_requests"] += 1
            meta["list_requests"] += 1
            meta["boundary_rechecks"] += 1
            if _page_signature(sentinel_recheck) != _page_signature(sentinel):
                raise CheongsongContractError("empty-sentinel stability recheck failed")
            meta["sentinel_rechecked"] = True

        listed = [row for page in pages for row in page["rows"]]
        identities = [str(row["identity"]) for row in listed]
        if len(identities) != len(set(identities)):
            raise CheongsongContractError("course identity repeated across pages")
        current = [row for row in listed if row["end"] >= cutoff]
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "nonempty_pages": len(pages),
                "source_rows": len(listed),
                "source_total": len(listed),
                "current_source_count": len(current),
                "expired_source_count": len(listed) - len(current),
                "pagination_complete": True,
            }
        )
        if len(current) > int(detail_limit):
            meta["source_cap_reached"] = True
            raise CheongsongContractError(f"detail_limit {detail_limit} below required {len(current)}")

        rows: list[dict[str, Any]] = []
        for item in current:
            detail_soup = _soup(session, str(item["detail_url"]), int(timeout), current_fetcher)
            meta["source_requests"] += 1
            meta["detail_pages"] += 1
            rows.append(_detail(item, detail_soup, cutoff))

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        if privacy_errors:
            raise CheongsongContractError("; ".join(privacy_errors[:5]))
        if len(rows) != len(current):
            raise CheongsongContractError("dedupe changed the current identity set")
        meta.update(
            {
                "returned_count": len(rows),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "branch_counts": dict(Counter(str(row["branch"]) for row in rows)),
                "application_control_count": sum(
                    bool(row["raw_fields"]["application_control_present"]) for row in rows
                ),
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not rows,
            }
        )
        return rows, CHEONGSONG_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], CHEONGSONG_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_cheongsong_education
