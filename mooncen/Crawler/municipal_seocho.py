"""Fail-closed collector for Seocho-gu's official education reservations.

The old YAML target points at a static landing page.  Generic recursive
discovery from that page mixes courses with consultations, booth calendars,
and unrelated animal-board posts, and it only samples the real lecture list.
This module owns the two official course lists linked by Seocho's reservation
service instead:

* the integrated ``lecture/List.do`` list; and
* the senior digital-education ``lecture/info/InfoList.do`` list.

Both lists use the same stable ``clIdx`` namespace.  A row is emitted only
after every current/future course has a matching, structurally valid detail
page and every declared list page has been consumed.  This is intentional: a
partial response must never be mistaken for an authoritative empty snapshot.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


SEOCHO_EDUCATION_PROVIDER = "MUNI_WWW_SEOCHO_GO_KR_0866A56C"
SEOCHO_EDUCATION_URL = "https://www.seocho.go.kr/site/seocho/ex/lecture/List.do"
SEOCHO_INFO_URL = (
    "https://www.seocho.go.kr/site/seocho/ex/lecture/info/InfoList.do"
)
SEOCHO_HOST = "www.seocho.go.kr"
SEOCHO_MAIN_LIST_PATH = "/site/seocho/ex/lecture/List.do"
SEOCHO_MAIN_DETAIL_PATH = "/site/seocho/ex/lecture/View.do"
SEOCHO_INFO_LIST_PATH = "/site/seocho/ex/lecture/info/InfoList.do"
SEOCHO_INFO_DETAIL_PATH = "/site/seocho/ex/lecture/info/InfoView.do"
SEOCHO_INFO_APPLICATION_PATH = (
    "/site/seocho/foffice/ex/lecture/info/InfoForm.do"
)
SEOCHO_MUNICIPALITY_CODE = "1165000000"
SEOCHO_MUNICIPALITY_NAME = "서울특별시 서초구"
SEOCHO_PARSER = "seocho_official_current_future+all_pages+detail"
# The Seocho origin starts refusing connections during sustained bursts above
# four workers.  Four concurrent detail requests completed 715/716 live pages
# before the post-batch retry described below, while eight workers lost large
# contiguous ranges.
SEOCHO_MAX_DETAIL_WORKERS = 4

SEOCHO_PROVIDERS = frozenset((SEOCHO_EDUCATION_PROVIDER,))
SEOCHO_CANONICAL_URLS = {SEOCHO_EDUCATION_PROVIDER: SEOCHO_EDUCATION_URL}

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[.\-/](?P<month>\d{1,2})[.\-/](?P<day>\d{1,2})(?!\d)"
)
_MAIN_ID_RE = re.compile(
    r"doLectureUserView\(\s*['\"](?P<id>L\d+)['\"]\s*\)", re.IGNORECASE
)
_INFO_ID_RE = re.compile(r"^L\d+$", re.IGNORECASE)
_MAIN_STATUS = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
_INFO_STATUS = {
    **_MAIN_STATUS,
    "신청하기": "OPEN",
    # This is the course phase shown after reception has closed and before the
    # first class.  It is not an incomplete row or a future reception state.
    "강좌대기": "CLOSED",
    "강좌시작": "CLOSED",
    "강좌종료": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET",)),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    current.mount("https://", adapter)
    return current


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return HTML or BeautifulSoup")
    return BeautifulSoup(content, "lxml")


def _default_fetcher(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        raise ValueError("empty HTTP response")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and (urlparse(final_url).hostname or "").lower() != SEOCHO_HOST:
        raise ValueError("unexpected cross-host redirect")
    return BeautifulSoup(response.content, "lxml")


def _fetch(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _date_tokens(value: Any) -> list[date]:
    values: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            values.append(
                date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            )
        except ValueError:
            continue
    return values


def _date_range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if len(values) < 2 or values[1] < values[0]:
        return "", "", ""
    start, end = values[:2]
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _stable_branch_code(branch: str) -> str:
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12]
    return f"SEOCHO:{digest}"


def _list_url(base: str, page_index: int) -> str:
    if page_index <= 1:
        return base
    return f"{base}?{urlencode({'pageIndex': page_index})}"


def seocho_detail_url(identity: str, *, source_kind: str = "main") -> str:
    token = _clean(identity).upper()
    if not _INFO_ID_RE.fullmatch(token):
        return ""
    path = SEOCHO_INFO_DETAIL_PATH if source_kind == "info" else SEOCHO_MAIN_DETAIL_PATH
    return f"https://{SEOCHO_HOST}{path}?{urlencode({'clIdx': token})}"


def is_seocho_education_target(target: Any) -> bool:
    if _provider(target) != SEOCHO_EDUCATION_PROVIDER:
        return False
    parsed = urlparse(_target_url(target))
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower() == SEOCHO_HOST
        and parsed.path == SEOCHO_MAIN_LIST_PATH
        and not parse_qs(parsed.query, keep_blank_values=True)
    )


def _declared_total(soup: BeautifulSoup) -> int:
    node = soup.select_one(".board-top .count em")
    text = _clean(node.get_text(" ", strip=True) if node else "")
    return int(text) if text.isdigit() else -1


def _declared_pages(soup: BeautifulSoup) -> int:
    pages = [1]
    for node in soup.select(".paging a[href], .paging a[onclick]"):
        values = re.findall(r"(?:pageIndex=|doLectureUserPag\()(?P<page>\d+)", _clean(node))
        values.extend(
            re.findall(
                r"(?:pageIndex=|doLectureUserPag\()(?P<page>\d+)",
                _clean(node.get("href")) + " " + _clean(node.get("onclick")),
            )
        )
        pages.extend(int(value) for value in values)
    return max(pages)


def _title_before_summary(link: Tag) -> str:
    parts: list[str] = []
    for child in link.children:
        if isinstance(child, Tag) and child.name in {"br", "span"}:
            break
        if isinstance(child, Tag):
            parts.append(child.get_text(" ", strip=True))
        else:
            parts.append(str(child))
    return _clean(" ".join(parts))


def _base_row(
    target: Any,
    *,
    identity: str,
    title: str,
    branch: str,
    raw_url: str,
    source_kind: str,
    status: str,
    status_raw: str,
    start_date: str,
    end_date: str,
    period: str,
    apply_start: str,
    apply_end: str,
    apply_period: str,
    schedule: str,
    page_index: int,
) -> dict[str, Any]:
    provider = _provider(target) or SEOCHO_EDUCATION_PROVIDER
    category = "정보화교육" if source_kind == "info" else "교육강좌"
    category_basis = (
        "official_ledger:시니어 정보화교육"
        if source_kind == "info"
        else "official_ledger:강좌예약"
    )
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:lecture:{identity}"[:100],
        "title": title,
        "name": _clean(_target_value(target, "name")) or "서초구청 통합예약 강좌",
        "branch": branch,
        "branch_code": _stable_branch_code(branch),
        "raw_url": raw_url,
        "status": status,
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "apply_period": apply_period,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule_raw": schedule,
        "category": category,
        "description": title,
        "municipality_code": SEOCHO_MUNICIPALITY_CODE,
        "municipality_full_name": SEOCHO_MUNICIPALITY_NAME,
        "collection_category": "교육",
        "domain_category": "교육",
        "source_group": "municipal_reservation",
        "service_group": "교육",
        "operator_type": "지자체/공공기관",
        "collection_type": "complete_numbered_pages+detail_html",
        "parser": SEOCHO_PARSER,
        "raw_fields": {
            "lecture_id": identity,
            "source_kind": source_kind,
            "source_status": status_raw,
            "page_index": page_index,
            "detail_required": True,
            "category_basis": category_basis,
        },
    }


def _parse_main_page(
    target: Any, soup: BeautifulSoup, *, page_index: int
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    table = next(
        (
            value
            for value in soup.select("table.list")
            if "강좌예약 목록" in _clean(value.select_one("caption").get_text(" ", strip=True) if value.select_one("caption") else "")
        ),
        None,
    )
    if table is None:
        return [], [f"main page {page_index}: missing lecture list table"]
    rows: list[dict[str, Any]] = []
    for position, tr in enumerate(table.select("tbody > tr"), start=1):
        cells = tr.find_all("td", recursive=False)
        if not cells or not _clean(tr.get_text(" ", strip=True)):
            continue
        if len(cells) != 6:
            errors.append(f"main page {page_index} row {position}: expected 6 cells")
            continue
        link = cells[1].select_one("a[href]")
        match = _MAIN_ID_RE.search(_clean(link.get("href")) if link else "")
        identity = match.group("id").upper() if match else ""
        title = _title_before_summary(link) if link else ""
        branch = _clean(cells[2].get_text(" ", strip=True))
        status_raw = _clean(cells[5].get_text(" ", strip=True))
        status = _MAIN_STATUS.get(status_raw, "")
        start_date, end_date, period = _date_range(cells[3].get_text(" ", strip=True))
        apply_start, apply_end, apply_period = _date_range(
            cells[4].get_text(" ", strip=True)
        )
        schedule = _clean(
            link.select_one("span").get_text(" ", strip=True)
            if link and link.select_one("span")
            else ""
        )
        raw_url = seocho_detail_url(identity, source_kind="main")
        if not all(
            (
                identity,
                title,
                branch,
                status,
                start_date,
                end_date,
                apply_start,
                apply_end,
                raw_url,
            )
        ):
            errors.append(f"main page {page_index} row {position}: incomplete list fields")
            continue
        row = _base_row(
            target,
            identity=identity,
            title=title,
            branch=branch,
            raw_url=raw_url,
            source_kind="main",
            status=status,
            status_raw=status_raw,
            start_date=start_date,
            end_date=end_date,
            period=period,
            apply_start=apply_start,
            apply_end=apply_end,
            apply_period=apply_period,
            schedule=schedule,
            page_index=page_index,
        )
        row["raw_fields"]["list_number"] = _clean(cells[0].get_text(" ", strip=True))
        rows.append(row)
    return rows, errors


def _parse_info_page(
    target: Any, soup: BeautifulSoup, *, page_index: int
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    table = next(
        (
            value
            for value in soup.select("table.list")
            if "시니어 정보화교육" in _clean(value.select_one("caption").get_text(" ", strip=True) if value.select_one("caption") else "")
        ),
        None,
    )
    if table is None:
        return [], [f"info page {page_index}: missing information-course table"]
    rows: list[dict[str, Any]] = []
    for position, tr in enumerate(table.select("tbody > tr"), start=1):
        cells = tr.find_all("td", recursive=False)
        if not cells or not _clean(tr.get_text(" ", strip=True)):
            continue
        if len(cells) != 5:
            errors.append(f"info page {page_index} row {position}: expected 5 cells")
            continue
        link = cells[0].select_one("a[href]")
        parsed = urlparse(urljoin(SEOCHO_INFO_URL, _clean(link.get("href")) if link else ""))
        identities = parse_qs(parsed.query).get("clIdx", [])
        identity = _clean(identities[0]).upper() if identities else ""
        title = _clean(link.get_text(" ", strip=True) if link else "")
        status_raw = _clean(cells[4].get_text(" ", strip=True))
        status = _INFO_STATUS.get(status_raw, "")
        start_date, end_date, period = _date_range(cells[2].get_text(" ", strip=True))
        apply_start, apply_end, apply_period = _date_range(
            cells[3].get_text(" ", strip=True)
        )
        schedule = _clean(cells[1].get_text(" ", strip=True))
        raw_url = seocho_detail_url(identity, source_kind="info")
        if not all(
            (
                identity,
                _INFO_ID_RE.fullmatch(identity),
                title,
                status,
                start_date,
                end_date,
                apply_start,
                apply_end,
                raw_url,
            )
        ):
            errors.append(f"info page {page_index} row {position}: incomplete list fields")
            continue
        rows.append(
            _base_row(
                target,
                identity=identity,
                title=title,
                branch="서초스마트시니어교육센터",
                raw_url=raw_url,
                source_kind="info",
                status=status,
                status_raw=status_raw,
                start_date=start_date,
                end_date=end_date,
                period=period,
                apply_start=apply_start,
                apply_end=apply_end,
                apply_period=apply_period,
                schedule=schedule,
                page_index=page_index,
            )
        )
    return rows, errors


def _table_pairs(table: Optional[Tag]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if table is None:
        return pairs
    for tr in table.select("tbody > tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(cells):
            cell = cells[index]
            if cell.name != "th":
                index += 1
                continue
            key = _clean(cell.get_text(" ", strip=True))
            value = ""
            if index + 1 < len(cells) and cells[index + 1].name == "td":
                value = _clean(cells[index + 1].get_text(" ", strip=True))
                index += 1
            if key:
                pairs[key] = value
            index += 1
    return pairs


def _safe_application_url(value: Any) -> str:
    url = _clean(value)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if re.search(r";j?sessionid=", parsed.path, re.IGNORECASE):
        return ""
    return url


def _info_application_url(soup: BeautifulSoup, identity: str) -> str:
    matches: list[str] = []
    for link in soup.select("a[href]"):
        parsed = urlparse(urljoin(SEOCHO_INFO_URL, _clean(link.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme.lower() == "https"
            and (parsed.hostname or "").lower() == SEOCHO_HOST
            and parsed.port is None
            and parsed.path == SEOCHO_INFO_APPLICATION_PATH
            and query == {"clIdx": [identity]}
            and not parsed.params
            and not parsed.fragment
            and not parsed.username
            and not parsed.password
        ):
            matches.append(
                f"https://{SEOCHO_HOST}{SEOCHO_INFO_APPLICATION_PATH}?"
                f"{urlencode({'clIdx': identity})}"
            )
    return matches[0] if len(matches) == 1 else ""


def _detail_title(soup: BeautifulSoup, source_kind: str) -> str:
    if source_kind == "info":
        node = soup.select_one("h4.con-title1")
        return _clean(node.get_text(" ", strip=True) if node else "")
    # Seocho emits a generic site-wide og:title before the course-specific
    # ``#mtTitle``.  A selector union returns the first node in document order,
    # not the first selector, so the exact id must be queried separately.
    node = soup.select_one("meta#mtTitle[content]")
    return _clean(node.get("content") if node else "")


def _enrich_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("lecture_id"))
    source_kind = _clean(row.get("raw_fields", {}).get("source_kind"))
    errors: list[str] = []
    table = next(
        (
            value
            for value in soup.select("table.view")
            if "강좌 상세정보" in _clean(value.select_one("caption").get_text(" ", strip=True) if value.select_one("caption") else "")
        ),
        None,
    )
    fields = _table_pairs(table)
    required = (
        (
            "교육 기관",
            "관리부서",
            "교육기간",
            "수강요일",
            "전화문의",
            "교육장소",
            "접수기간",
            "선별방법",
        )
        if source_kind == "info"
        else (
            "교육 기관",
            "관리부서",
            "교육기간",
            "교육장소",
            "접수기간",
            "접수방법",
            "강의방법",
            "전화문의",
        )
    )
    missing = [key for key in required if not _clean(fields.get(key))]
    if missing:
        errors.append(f"lecture {identity}: missing detail fields {','.join(missing)}")
    title = _detail_title(soup, source_kind)
    if not title or title != _clean(row.get("title")):
        errors.append(f"lecture {identity}: detail/list title mismatch")
    branch = _clean(fields.get("교육 기관"))
    if source_kind == "main" and branch and branch != _clean(row.get("branch")):
        errors.append(f"lecture {identity}: detail/list institution mismatch")
    detail_start, detail_end, detail_period = _date_range(fields.get("교육기간"))
    apply_start, apply_end, apply_period = _date_range(fields.get("접수기간"))
    if (detail_start, detail_end) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ):
        errors.append(f"lecture {identity}: detail/list education period mismatch")
    if (apply_start, apply_end) != (
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    ):
        errors.append(f"lecture {identity}: detail/list reception period mismatch")

    application_method = _clean(fields.get("접수방법"))
    related_url = ""
    if table is not None:
        for heading in table.select("tbody > tr > th"):
            key = _clean(heading.get_text(" ", strip=True))
            if key != "관련사이트":
                continue
            value_cell = heading.find_next_sibling("td")
            link = value_cell.select_one("a[href]") if value_cell else None
            related_url = _safe_application_url(
                urljoin(_clean(row.get("raw_url")), _clean(link.get("href")) if link else "")
            )
            break
    internal_control = next(
        (
            node
            for node in soup.select("a[onclick], button[onclick]")
            if re.search(
                rf"doMemberForm\(\s*['\"]?{re.escape(identity)}['\"]?\s*\)",
                _clean(node.get("onclick")),
                re.IGNORECASE,
            )
        ),
        None,
    )
    info_application_url = (
        _info_application_url(soup, identity) if source_kind == "info" else ""
    )
    selection_method = _clean(fields.get("선별방법"))
    target = _clean(fields.get("교육대상"))
    if (
        source_kind == "info"
        and not target
        and re.search(r"만\s*55\s*세\s*이상", selection_method)
    ):
        target = "만 55세 이상"
    if not target:
        errors.append(f"lecture {identity}: course target is missing")
    is_open = _clean(row.get("status")) == "OPEN"
    if is_open:
        if info_application_url:
            row["application_url"] = info_application_url
            row["application_type"] = "ONLINE_RESERVATION"
        elif related_url:
            row["application_url"] = related_url
            row["application_type"] = "EXTERNAL_ONLINE"
        elif "인터넷접수" in application_method and internal_control is not None:
            row["application_url"] = _clean(row.get("raw_url"))
            row["application_type"] = "ONLINE_RESERVATION"
        elif any(token in application_method for token in ("방문", "현장", "전화")):
            row["application_type"] = "OFFLINE_APPLY"
        else:
            errors.append(f"lecture {identity}: open course has no usable application route")
    else:
        row.pop("application_url", None)
        row["raw_fields"]["clear_application_url"] = True

    resolved_branch = branch or _clean(row.get("branch"))
    row.update(
        {
            "branch": resolved_branch,
            "branch_code": _stable_branch_code(resolved_branch),
            "period": detail_period or row.get("period"),
            "start_date": detail_start or row.get("start_date"),
            "end_date": detail_end or row.get("end_date"),
            "apply_period": apply_period or row.get("apply_period"),
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "schedule_raw": _clean(fields.get("수강요일")) or row.get("schedule_raw"),
            "fee": _clean(fields.get("수강료")),
            "instructor": _clean(fields.get("강사명")),
            "room": _clean(fields.get("교육장소")),
            "venue_name": _clean(fields.get("교육장소")),
            "phone": _clean(fields.get("전화문의")),
            "contact": _clean(fields.get("전화문의")),
            "target": target,
            "capacity": _clean(fields.get("교육인원")),
            "application_method_raw": application_method,
            "selection_method_raw": selection_method,
            "management_department": _clean(fields.get("관리부서")),
            "lecture_method_raw": _clean(fields.get("강의방법")),
            "reservation_available": bool(
                is_open
                and (info_application_url or related_url or internal_control)
            ),
        }
    )
    description_node = soup.select_one(".lectureContent") if source_kind == "main" else None
    description = _clean(
        description_node.get_text(" ", strip=True) if description_node else row.get("title")
    )
    if description:
        row["description"] = description[:12000]
    row["raw_fields"].update(
        {
            "detail_pairs": fields,
            "related_site": related_url,
            "info_application_url": info_application_url,
            "internal_reservation_control": bool(internal_control),
        }
    )
    return errors


def _dedupe_list_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, list[str]]:
    selected: list[dict[str, Any]] = []
    by_identity: dict[str, dict[str, Any]] = {}
    by_logical: dict[tuple[str, ...], dict[str, Any]] = {}
    duplicates = 0
    errors: list[str] = []
    for row in rows:
        identity = _clean(row.get("raw_fields", {}).get("lecture_id"))
        material = (
            _clean(row.get("title")).casefold(),
            _clean(row.get("branch")).casefold(),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("schedule_raw")).casefold(),
        )
        existing = by_identity.get(identity)
        if existing is not None:
            other_material = (
                _clean(existing.get("title")).casefold(),
                _clean(existing.get("branch")).casefold(),
                _clean(existing.get("start_date")),
                _clean(existing.get("end_date")),
                _clean(existing.get("schedule_raw")).casefold(),
            )
            if material != other_material:
                errors.append(f"lecture {identity}: conflicting duplicate list identity")
            else:
                duplicates += 1
            continue
        logical_existing = by_logical.get(material)
        if logical_existing is not None:
            duplicates += 1
            continue
        by_identity[identity] = row
        by_logical[material] = row
        selected.append(row)
    return selected, duplicates, errors


def _parallel_details(
    rows: list[dict[str, Any]],
    *,
    timeout: int,
    detail_limit: int,
    max_workers: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> tuple[int, int, int, list[str], bool]:
    required_count = len(rows)
    allowed = max(0, int(detail_limit))
    selected = rows[:allowed]
    capped = allowed < required_count
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def current_session() -> Any:
        value = getattr(local, "session", None)
        if value is None:
            value = session_factory()
            local.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def enrich(row: dict[str, Any]) -> tuple[bool, list[str]]:
        identity = _clean(row.get("raw_fields", {}).get("lecture_id"))
        try:
            soup = _fetch(fetcher, current_session(), _clean(row.get("raw_url")), timeout)
            errors = _enrich_detail(row, soup)
            return True, errors
        except Exception as exc:
            return False, [f"lecture {identity}: detail fetch {type(exc).__name__}"]

    results: list[tuple[bool, list[str]]] = []
    try:
        if selected:
            workers = min(
                SEOCHO_MAX_DETAIL_WORKERS,
                max(1, int(max_workers)),
                len(selected),
            )
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="seocho-detail"
            ) as pool:
                results = list(pool.map(enrich, selected))
            # A long live snapshot can contain one or two transient connection
            # refusals even after urllib3's immediate retries.  Retrying only
            # the final failures after the main batch gives the origin several
            # minutes to recover, without re-fetching successful details.
            for retry_round in range(2):
                failed_indexes = [
                    index for index, (success, _errors) in enumerate(results) if not success
                ]
                if not failed_indexes:
                    break
                retry_rows = [selected[index] for index in failed_indexes]
                retry_workers = min(2, len(retry_rows))
                with ThreadPoolExecutor(
                    max_workers=retry_workers,
                    thread_name_prefix=f"seocho-detail-retry-{retry_round + 1}",
                ) as pool:
                    retried = list(pool.map(enrich, retry_rows))
                for index, result in zip(failed_indexes, retried):
                    results[index] = result
    finally:
        for value in sessions:
            _close_quietly(value)
    detail_pages = sum(success for success, _errors in results)
    errors = [error for _success, item_errors in results for error in item_errors]
    return required_count, len(selected), detail_pages, errors, capped


def _empty_meta(errors: list[str]) -> dict[str, Any]:
    return {
        "pages": 0,
        "main_pages": 0,
        "info_pages": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "detail_required_count": 0,
        "required_detail_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "pagination_exhausted": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "current_count": 0,
        "configured_collection_error": "; ".join(errors),
        "branch_counts": {},
    }


def collect_seocho_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 100,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | str] = None,
    max_workers: int = 6,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Seocho education snapshot."""

    errors: list[str] = []
    if not is_seocho_education_target(target):
        errors.append("target does not match the canonical Seocho education source")
    if max_pages < 2:
        errors.append("max_pages is below the two-source minimum")
    if errors:
        return [], SEOCHO_PARSER, _empty_meta(errors)

    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    primary_session: Any = None
    main_rows: list[dict[str, Any]] = []
    info_rows: list[dict[str, Any]] = []
    pages_fetched = 0
    main_pages_fetched = 0
    info_pages_fetched = 0
    main_total = -1
    info_total = -1
    main_page_count = 0
    info_page_count = 0
    source_cap_reached = False

    try:
        primary_session = make_session()
        main_first = _fetch(fetch, primary_session, SEOCHO_EDUCATION_URL, timeout)
        pages_fetched += 1
        main_pages_fetched += 1
        info_first = _fetch(fetch, primary_session, SEOCHO_INFO_URL, timeout)
        pages_fetched += 1
        info_pages_fetched += 1
        main_total = _declared_total(main_first)
        info_total = _declared_total(info_first)
        main_page_count = _declared_pages(main_first)
        info_page_count = _declared_pages(info_first)
        if main_total < 0 or info_total < 0:
            errors.append("one or more source totals are missing")
        if main_page_count < 1 or info_page_count < 1:
            errors.append("one or more source page counts are invalid")
        required_pages = main_page_count + info_page_count
        if required_pages > max_pages:
            source_cap_reached = True
            errors.append(
                f"declared list pages exceed max_pages ({required_pages}>{max_pages})"
            )
        parsed, page_errors = _parse_main_page(target, main_first, page_index=1)
        main_rows.extend(parsed)
        errors.extend(page_errors)
        parsed, page_errors = _parse_info_page(target, info_first, page_index=1)
        info_rows.extend(parsed)
        errors.extend(page_errors)

        if not errors:
            for source_kind, base, page_count, parser in (
                ("main", SEOCHO_EDUCATION_URL, main_page_count, _parse_main_page),
                ("info", SEOCHO_INFO_URL, info_page_count, _parse_info_page),
            ):
                destination = main_rows if source_kind == "main" else info_rows
                for page_index in range(2, page_count + 1):
                    soup = _fetch(
                        fetch,
                        primary_session,
                        _list_url(base, page_index),
                        timeout,
                    )
                    pages_fetched += 1
                    if source_kind == "main":
                        main_pages_fetched += 1
                    else:
                        info_pages_fetched += 1
                    page_total = _declared_total(soup)
                    expected_total = main_total if source_kind == "main" else info_total
                    if page_total != expected_total:
                        errors.append(
                            f"{source_kind} page {page_index}: declared total changed "
                            f"({page_total}!={expected_total})"
                        )
                        break
                    parsed, page_errors = parser(target, soup, page_index=page_index)
                    destination.extend(parsed)
                    errors.extend(page_errors)
                    if page_errors:
                        break
    except Exception as exc:
        errors.append(f"list fetch {type(exc).__name__}")
    finally:
        _close_quietly(primary_session)

    if len(main_rows) != main_total:
        errors.append(f"main declared/parsed mismatch ({main_total}!={len(main_rows)})")
    if len(info_rows) != info_total:
        errors.append(f"info declared/parsed mismatch ({info_total}!={len(info_rows)})")
    listed, duplicate_count, duplicate_errors = _dedupe_list_rows(
        [*main_rows, *info_rows]
    )
    errors.extend(duplicate_errors)
    current_rows = [
        row
        for row in listed
        if _clean(row.get("end_date"))
        and date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
    ]
    expired_count = len(listed) - len(current_rows)
    list_complete = bool(
        not errors
        and main_pages_fetched == main_page_count
        and info_pages_fetched == info_page_count
        and len(main_rows) == main_total
        and len(info_rows) == info_total
    )

    required_count = len(current_rows)
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    detail_cap_reached = False
    if list_complete:
        (
            required_count,
            detail_attempts,
            detail_pages,
            detail_errors,
            detail_cap_reached,
        ) = _parallel_details(
            current_rows,
            timeout=timeout,
            detail_limit=detail_limit,
            max_workers=max_workers,
            fetcher=fetch,
            session_factory=make_session,
        )
    details_complete = bool(
        list_complete
        and detail_attempts == required_count
        and detail_pages == required_count
        and not detail_errors
        and not detail_cap_reached
    )
    all_errors = list(dict.fromkeys([*errors, *detail_errors]))
    snapshot_complete = bool(list_complete and details_complete and not all_errors)
    if dedupe_rows is not None and snapshot_complete:
        deduped = list(dedupe_rows(current_rows))
        duplicate_count += max(0, len(current_rows) - len(deduped))
        current_rows = deduped
    if not snapshot_complete:
        current_rows = []

    source_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_kind")) for row in current_rows
    )
    branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
    no_current_data = snapshot_complete and not current_rows
    meta: dict[str, Any] = {
        "pages": pages_fetched,
        "main_pages": main_pages_fetched,
        "info_pages": info_pages_fetched,
        "declared_main_pages": main_page_count,
        "declared_info_pages": info_page_count,
        "detail_pages": detail_pages,
        "detail_attempts": detail_attempts,
        "detail_required_count": required_count,
        "required_detail_count": required_count,
        "detail_errors": len(detail_errors),
        "pagination_detected": main_page_count > 1 or info_page_count > 1,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached or detail_cap_reached,
        "recursion_depth": 0,
        "source_total": max(0, main_total) + max(0, info_total),
        "main_total": max(0, main_total),
        "info_total": max(0, info_total),
        "listed_unique_count": len(listed),
        "expired_count": expired_count,
        "current_count": len(current_rows),
        "duplicate_count": duplicate_count,
        "source_counts": dict(source_counts),
        "branch_counts": dict(branch_counts),
        "no_current_data": no_current_data,
        "no_current_reason": (
            "official Seocho current/future education lists are empty"
            if no_current_data
            else ""
        ),
        "full_snapshot_required": True,
    }
    if all_errors:
        meta["configured_collection_error"] = "; ".join(all_errors)
    return current_rows, SEOCHO_PARSER, meta


__all__ = [
    "SEOCHO_CANONICAL_URLS",
    "SEOCHO_EDUCATION_PROVIDER",
    "SEOCHO_EDUCATION_URL",
    "SEOCHO_INFO_URL",
    "SEOCHO_PARSER",
    "SEOCHO_PROVIDERS",
    "collect_seocho_education_courses",
    "is_seocho_education_target",
    "seocho_detail_url",
]
