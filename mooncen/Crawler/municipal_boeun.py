"""Fail-closed collector for Boeun County's official lifelong-course ledger."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BOEUN_PROVIDER = "MUNI_BOEUN_GO_KR_41FDD929"
BOEUN_MUNICIPALITY_CODE = "4372000000"
BOEUN_MUNICIPALITY_NAME = "충청북도 보은군"
BOEUN_HOST = "boeun.go.kr"
BOEUN_LIST_PATH = "/boeunlonglife/selectLftmLrnListContentsType.do"
BOEUN_DETAIL_PATH = "/boeunlonglife/selectLftmLrnView.do"
BOEUN_HOME_PATH = "/boeunlonglife/index.do"
BOEUN_CANONICAL_URL = f"https://{BOEUN_HOST}{BOEUN_LIST_PATH}?key=1349"
BOEUN_HOME_URL = f"https://{BOEUN_HOST}{BOEUN_HOME_PATH}"
BOEUN_IEUM_NOTICE_URL = (
    "https://boeun.go.kr/ieum/www/selectBbsNttList.do?bbsNo=2&key=16"
)
BOEUN_MAX_WORKERS = 5
BOEUN_MAX_HTML_BYTES = 3_000_000
BOEUN_REQUIRED_SOURCE_REQUESTS = 3
BOEUN_PARSER = (
    "boeun_two_authoritative_current_tables+stable_full_snapshot+"
    "homepage_expired_highlight_reconciliation+current_details+"
    "identity_bound_application_controls+facility_branches+"
    "ieum_editorial_notice_excluded+pii_allowlist"
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class BoeunContractError(ValueError):
    pass


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE = re.compile(r"20\d{2}-\d{1,2}-\d{1,2}")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_OPEN_STATUS_TOKENS = ("접수중", "신청중", "모집중", "접수가능", "신청가능")
_OPEN_HEADERS = (
    "교육과정",
    "교육기간",
    "교육시간",
    "접수기간",
    "신청/모집인원",
    "접수상태",
)
_PROGRESS_HEADERS = ("교육과정", "교육기간", "강의시간")
_EMPTY_TEXT = {
    "open": "접수중인 교육과정이 없습니다.",
    "progress": "진행중 또는 진행예정인 교육과정이 없습니다.",
}
_SAFE_RAW = frozenset(
    {
        "identity",
        "source_scopes",
        "source_status",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_venue",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "service_family",
    }
)
_FORBIDDEN = frozenset(
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


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(
        urlparse(url).query,
        keep_blank_values=True,
        strict_parsing=True,
    )


def is_boeun_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != BOEUN_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        query = _query(parsed.geturl())
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == BOEUN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == BOEUN_LIST_PATH
        and query == [("key", "1349")]
        and not parsed.fragment
    )


is_target = is_boeun_education_target


def _session() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return value


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


def _detail_url(identity: str) -> str:
    return (
        f"https://{BOEUN_HOST}{BOEUN_DETAIL_PATH}?"
        f"{urlencode({'key': '1349', 'lftmLrnNo': identity})}"
    )


def _allowed_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
        query = _query(url)
    except ValueError:
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == BOEUN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        return False
    if parsed.path == BOEUN_HOME_PATH:
        return not query
    if parsed.path == BOEUN_LIST_PATH:
        return query == [("key", "1349")]
    if parsed.path != BOEUN_DETAIL_PATH:
        return False
    values = dict(query)
    return bool(
        len(query) == 2
        and set(values) == {"key", "lftmLrnNo"}
        and values["key"] == "1349"
        and _IDENTITY.fullmatch(values["lftmLrnNo"])
    )


def _soup(
    url: str,
    timeout: int,
    factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    if not _allowed_fetch_url(url):
        raise BoeunContractError("non-canonical fetch URL refused")
    last: Optional[Exception] = None
    for _ in range(2):
        session = factory()
        try:
            response = fetcher(session, url, timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", response)).encode("utf-8")
            if len(content) > BOEUN_MAX_HTML_BYTES:
                raise BoeunContractError("HTML size cap exceeded")
            final_url = str(getattr(response, "url", url))
            if not _allowed_fetch_url(final_url):
                raise BoeunContractError("redirect outside the canonical no-www owner")
            return BeautifulSoup(content, "html.parser")
        except (requests.RequestException, TimeoutError) as exc:
            last = exc
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    assert last is not None
    raise last


def _header(value: Any) -> str:
    return re.sub(r"\s*/\s*", "/", _clean(value))


def _period(value: str, label: str) -> tuple[date, date]:
    tokens = _DATE.findall(value)
    if len(tokens) != 2:
        raise BoeunContractError(f"{label}: exact two-date period missing")
    start, end = date.fromisoformat(tokens[0]), date.fromisoformat(tokens[1])
    if end < start:
        raise BoeunContractError(f"{label}: reversed period")
    return start, end


def _course_identity(anchor: Any) -> tuple[str, str]:
    href = urljoin(BOEUN_CANONICAL_URL, _clean(anchor.get("href")))
    parsed = urlparse(href)
    try:
        port = parsed.port
        query = _query(href)
    except ValueError as exc:
        raise BoeunContractError("invalid course detail URL") from exc
    values = dict(query)
    identity = values.get("lftmLrnNo", "")
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == BOEUN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == BOEUN_DETAIL_PATH
        and len(query) == 2
        and set(values) == {"key", "lftmLrnNo"}
        and values["key"] == "1349"
        and _IDENTITY.fullmatch(identity)
        and not parsed.fragment
    ):
        raise BoeunContractError("course detail identity is not canonical")
    title = _clean(anchor.get_text(" ", strip=True))
    if not title:
        raise BoeunContractError(f"course {identity}: title missing")
    return identity, title


def _application_url(anchor: Any, identity: str) -> str:
    href = urljoin(BOEUN_CANONICAL_URL, _clean(anchor.get("href")))
    parsed = urlparse(href)
    try:
        port = parsed.port
        pairs = _query(href)
    except ValueError as exc:
        raise BoeunContractError(f"course {identity}: invalid application URL") from exc
    values = dict(pairs)
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == BOEUN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path.startswith("/boeunlonglife/")
        and parsed.path not in {BOEUN_LIST_PATH, BOEUN_DETAIL_PATH}
        and "lftmlrn" in parsed.path.lower()
        and ("reqst" in parsed.path.lower() or "apply" in parsed.path.lower())
        and values.get("key") == "1349"
        and values.get("lftmLrnNo") == identity
        and len(pairs) == 2
        and set(values) == {"key", "lftmLrnNo"}
        and not parsed.fragment
    ):
        raise BoeunContractError(
            f"course {identity}: application control is not identity-bound"
        )
    return href


def _controls(root: Any, identity: str) -> tuple[str, ...]:
    result: set[str] = set()
    for anchor in root.select("a[href]"):
        text = _clean(anchor.get_text(" ", strip=True))
        if not any(token in text for token in ("신청", "접수")):
            continue
        result.add(_application_url(anchor, identity))
    if len(result) > 1:
        raise BoeunContractError(f"course {identity}: multiple application controls")
    return tuple(sorted(result))


def _table_rows(table: Any, scope: str) -> list[dict[str, Any]]:
    expected = _OPEN_HEADERS if scope == "open" else _PROGRESS_HEADERS
    headers = tuple(
        _header(node.get_text(" ", strip=True)) for node in table.select("thead th")
    )
    if headers != expected:
        raise BoeunContractError(f"{scope}: table header contract changed")
    body_rows = table.select("tbody > tr")
    if not body_rows:
        raise BoeunContractError(f"{scope}: table body missing")
    sentinel = _EMPTY_TEXT[scope]
    if len(body_rows) == 1:
        cells = body_rows[0].find_all("td", recursive=False)
        if (
            len(cells) == 1
            and _clean(cells[0].get_text(" ", strip=True)) == sentinel
            and _clean(cells[0].get("colspan")) == str(len(expected))
            and not cells[0].select("a,button,input")
        ):
            return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tr in body_rows:
        cells = tr.find_all("td", recursive=False)
        if len(cells) != len(expected) or sentinel in _clean(tr.get_text(" ", strip=True)):
            raise BoeunContractError(f"{scope}: row shape changed")
        anchors = cells[0].select(
            "a[href*='selectLftmLrnView.do'][href*='lftmLrnNo=']"
        )
        if len(anchors) != 1:
            raise BoeunContractError(f"{scope}: unique course identity link missing")
        identity, title = _course_identity(anchors[0])
        if identity in seen:
            raise BoeunContractError(f"{scope}: duplicate identity {identity}")
        seen.add(identity)
        start, end = _period(
            _clean(cells[1].get_text(" ", strip=True)),
            f"course {identity} education",
        )
        schedule = _clean(cells[2].get_text(" ", strip=True))
        if not schedule:
            raise BoeunContractError(f"course {identity}: schedule missing")
        row: dict[str, Any] = {
            "identity": identity,
            "title": title,
            "start": start,
            "end": end,
            "schedule": schedule,
            "source_scopes": [scope],
            "source_status": "",
            "source_apply_period": "",
            "apply_start": None,
            "apply_end": None,
            "capacity_current": None,
            "capacity_total": None,
            "list_control": "",
        }
        if scope == "open":
            apply_period = _clean(cells[3].get_text(" ", strip=True))
            apply_start, apply_end = _period(
                apply_period,
                f"course {identity} application",
            )
            capacities = [
                int(token.replace(",", ""))
                for token in re.findall(
                    r"\d[\d,]*", _clean(cells[4].get_text(" ", strip=True))
                )
            ]
            if len(capacities) != 2 or capacities[0] > capacities[1]:
                raise BoeunContractError(f"course {identity}: capacity contract changed")
            status_text = _clean(cells[5].get_text(" ", strip=True))
            source_status = next(
                (token for token in _OPEN_STATUS_TOKENS if token in status_text),
                "",
            )
            if not source_status:
                raise BoeunContractError(f"course {identity}: open status missing")
            controls = _controls(cells[5], identity)
            row.update(
                {
                    "source_status": source_status,
                    "source_apply_period": apply_period,
                    "apply_start": apply_start,
                    "apply_end": apply_end,
                    "capacity_current": capacities[0],
                    "capacity_total": capacities[1],
                    "list_control": controls[0] if controls else "",
                }
            )
        rows.append(row)
    return rows


def _catalogue(soup: BeautifulSoup) -> dict[str, Any]:
    tables = soup.select("table.table.responsive")
    if len(tables) != 2:
        raise BoeunContractError("exactly two authoritative course tables required")
    captions = [_clean(table.select_one("caption").get_text(" ", strip=True)) for table in tables if table.select_one("caption")]
    if len(captions) != 2 or not captions[0].startswith("접수중인 교육과정") or not captions[1].startswith("진행중인 교육과정"):
        raise BoeunContractError("authoritative table captions changed")
    if soup.select(".pagination,.p-pagination,.paging,.p-page,.board_pager,.pager"):
        raise BoeunContractError("unhandled pagination appeared on complete-table owner")
    scoped = {
        "open": _table_rows(tables[0], "open"),
        "progress": _table_rows(tables[1], "progress"),
    }
    merged: dict[str, dict[str, Any]] = {}
    raw_count = 0
    for scope in ("open", "progress"):
        for row in scoped[scope]:
            raw_count += 1
            identity = row["identity"]
            existing = merged.get(identity)
            if existing is None:
                merged[identity] = row
                continue
            if scope in existing["source_scopes"]:
                raise BoeunContractError(f"duplicate identity {identity} in {scope}")
            for field in ("title", "start", "end", "schedule"):
                if existing[field] != row[field]:
                    raise BoeunContractError(
                        f"course {identity}: cross-table {field} drift"
                    )
            existing["source_scopes"].append(scope)
            if scope == "open":
                for field in (
                    "source_status",
                    "source_apply_period",
                    "apply_start",
                    "apply_end",
                    "capacity_current",
                    "capacity_total",
                    "list_control",
                ):
                    existing[field] = row[field]
    rows = [merged[key] for key in sorted(merged, key=int)]
    return {
        "rows": rows,
        "raw_scope_rows": raw_count,
        "scope_counts": {key: len(value) for key, value in scoped.items()},
        "scope_duplicate_count": raw_count - len(rows),
    }


def _catalogue_signature(value: Mapping[str, Any]) -> tuple[Any, ...]:
    rows = tuple(
        (
            row["identity"],
            row["title"],
            row["start"],
            row["end"],
            row["schedule"],
            tuple(row["source_scopes"]),
            row["source_status"],
            row["source_apply_period"],
            row["capacity_current"],
            row["capacity_total"],
            row["list_control"],
        )
        for row in value["rows"]
    )
    return (
        tuple(value["scope_counts"].items()),
        value["raw_scope_rows"],
        value["scope_duplicate_count"],
        rows,
    )


def _homepage(soup: BeautifulSoup) -> list[dict[str, Any]]:
    lists = soup.select(".program_wrap .program_list")
    if len(lists) != 1:
        raise BoeunContractError("homepage programme-highlight ledger missing")
    items = lists[0].select(":scope > .program_slide_item")
    all_links = lists[0].select(
        "a[href*='selectLftmLrnView.do'][href*='lftmLrnNo=']"
    )
    if len(all_links) != len(items):
        raise BoeunContractError("homepage highlight identity coverage changed")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        anchors = item.select(
            ":scope > a.program_anchor[href*='selectLftmLrnView.do']"
            "[href*='lftmLrnNo=']"
        )
        if len(anchors) != 1:
            raise BoeunContractError("homepage highlight identity link missing")
        identity, _anchor_title = _course_identity(anchors[0])
        if identity in seen:
            raise BoeunContractError("homepage duplicate course identity")
        seen.add(identity)
        title_node = item.select_one(".town_title")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        fields: dict[str, str] = {}
        for li in item.select("ul.bu.dl > li"):
            key = li.select_one(".title")
            value = li.select_one(".text")
            if key is not None and value is not None:
                fields[_clean(key.get_text(" ", strip=True))] = _clean(
                    value.get_text(" ", strip=True)
                )
        if (
            not title
            or title not in _anchor_title
            or not {"교육기간", "교육비", "장소"} <= set(fields)
        ):
            raise BoeunContractError(f"homepage course {identity}: fields missing")
        start, end = _period(fields["교육기간"], f"homepage course {identity}")
        flag = item.select_one(".flag")
        flag_text = _clean(flag.get_text(" ", strip=True) if flag else "")
        if not flag_text:
            raise BoeunContractError(f"homepage course {identity}: status flag missing")
        rows.append(
            {
                "identity": identity,
                "title": title,
                "start": start,
                "end": end,
                "flag": flag_text,
            }
        )
    return rows


def _detail_fields(soup: BeautifulSoup, identity: str) -> tuple[Any, dict[str, str]]:
    matches: list[tuple[Any, dict[str, str]]] = []
    for table in soup.select("table.table.type2"):
        fields: dict[str, str] = {}
        for tr in table.select("tbody > tr"):
            headers = tr.find_all("th", recursive=False)
            cells = tr.find_all("td", recursive=False)
            if len(headers) != 1 or len(cells) != 1:
                raise BoeunContractError(f"course {identity}: detail row shape changed")
            key = _clean(headers[0].get_text(" ", strip=True))
            if key in fields:
                raise BoeunContractError(f"course {identity}: duplicate detail field")
            fields[key] = _clean(cells[0].get_text(" ", strip=True))
        if "교육명" in fields:
            matches.append((table.parent, fields))
    if len(matches) != 1:
        raise BoeunContractError(f"course {identity}: unique detail table missing")
    return matches[0]


def _branch(venue: str) -> str:
    value = _EMAIL.sub("", _PHONE.sub("", _clean(venue))).strip(" ()-/")
    if not value:
        raise BoeunContractError("detail venue missing")
    if _PHONE.search(value) or _EMAIL.search(value):
        raise BoeunContractError("detail venue contains contact data")
    if value.startswith("보은군"):
        return value
    return f"보은군 {value}"


def _branch_code(value: str) -> str:
    return "BOEUN_" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:12].upper()


def _derived_status(start: date, end: date, cutoff: date) -> str:
    if cutoff < start:
        return "SCHEDULED"
    if start <= cutoff <= end:
        return "OPEN"
    return "CLOSED"


def _detail(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
) -> dict[str, Any]:
    identity = str(listed["identity"])
    root, fields = _detail_fields(soup, identity)
    required = {
        "교육명",
        "접수방법",
        "정원",
        "교육비",
        "장소",
        "접수 시작/종료 날짜",
        "교육 시작/종료 날짜",
        "교육 시간",
        "요일",
    }
    if not required <= set(fields):
        raise BoeunContractError(f"course {identity}: required detail fields missing")
    title = fields["교육명"]
    if not title or title != listed["title"]:
        raise BoeunContractError(f"course {identity}: detail identity/title drift")
    apply_start, apply_end = _period(
        fields["접수 시작/종료 날짜"],
        f"course {identity} detail application",
    )
    start, end = _period(
        fields["교육 시작/종료 날짜"],
        f"course {identity} detail education",
    )
    if (start, end) != (listed["start"], listed["end"]) or end < cutoff:
        raise BoeunContractError(f"course {identity}: detail education period drift")
    scopes = tuple(listed["source_scopes"])
    if "open" in scopes:
        if (apply_start, apply_end) != (
            listed["apply_start"],
            listed["apply_end"],
        ):
            raise BoeunContractError(f"course {identity}: application period drift")
        if not apply_start <= cutoff <= apply_end:
            raise BoeunContractError(
                f"course {identity}: open table/application dates disagree"
            )
        status = "OPEN"
        source_status = str(listed["source_status"])
    else:
        status = _derived_status(apply_start, apply_end, cutoff)
        source_status = f"derived_{status.lower()}"
    capacity_text = _clean(fields["정원"])
    if not re.fullmatch(r"\d[\d,]*\s*명?", capacity_text):
        raise BoeunContractError(f"course {identity}: detail capacity changed")
    capacity = int(re.search(r"\d[\d,]*", capacity_text).group().replace(",", ""))
    if listed["capacity_total"] is not None and capacity != listed["capacity_total"]:
        raise BoeunContractError(f"course {identity}: list/detail capacity drift")
    application_method = _clean(
        _EMAIL.sub("", _PHONE.sub("", fields["접수방법"]))
    ).strip(" ()-/")
    fee = _clean(fields["교육비"])
    if not application_method or not fee:
        raise BoeunContractError(f"course {identity}: method/fee missing")
    controls = set(_controls(root, identity))
    if listed["list_control"]:
        controls.add(str(listed["list_control"]))
    if len(controls) > 1:
        raise BoeunContractError(f"course {identity}: list/detail control drift")
    control = next(iter(controls), "")
    online = "온라인" in application_method
    if status == "OPEN" and online and not control:
        raise BoeunContractError(
            f"course {identity}: open online course lacks identity-bound control"
        )
    branch = _branch(fields["장소"])
    raw_url = _detail_url(identity)
    schedule = " ".join(
        value
        for value in (_clean(fields["요일"]), _clean(fields["교육 시간"]))
        if value
    )
    if not schedule:
        raise BoeunContractError(f"course {identity}: detail schedule missing")
    application_url = control if control and status == "OPEN" else raw_url
    if status == "OPEN" and control:
        application_type = "ONLINE_RESERVATION"
    elif status == "OPEN":
        application_type = "OFFLINE_APPLICATION"
    else:
        application_type = "INFO_ONLY"
    row = {
        "provider": BOEUN_PROVIDER,
        "provider_course_id": f"{BOEUN_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": "평생학습",
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": application_url,
        "application_type": application_type,
        "application_method": application_method,
        "application_methods": [application_method],
        "reservation_available": bool(status == "OPEN" and control),
        "status": status,
        "fee": fee,
        "fee_amount": 0 if fee == "무료" else None,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "schedule_raw": schedule,
        "capacity": f"{capacity}명",
        "capacity_current": listed["capacity_current"],
        "capacity_total": capacity,
        "target": "",
        "venue": branch,
        "venue_name": branch,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": BOEUN_PARSER,
        "municipality_code": BOEUN_MUNICIPALITY_CODE,
        "municipality_full_name": BOEUN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_scopes": list(scopes),
            "source_status": source_status,
            "source_apply_period": fields["접수 시작/종료 날짜"],
            "source_education_period": fields["교육 시작/종료 날짜"],
            "source_schedule": schedule,
            "source_venue": branch,
            "detail_verified": True,
            "application_control_present": bool(control),
            "application_control_verified": True,
            "service_family": "education",
        },
    }
    return row


def _privacy(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN:
        errors.append("forbidden detail/PII key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW:
        errors.append("raw field allowlist exceeded")
    payload = repr(
        {key: value for key, value in row.items() if key not in {"raw_url", "application_url"}}
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


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now().astimezone().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def collect_boeun_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    max_workers: int = BOEUN_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "municipality_code": BOEUN_MUNICIPALITY_CODE,
        "owner_provider": BOEUN_PROVIDER,
        "canonical_url": BOEUN_CANONICAL_URL,
        "parser": BOEUN_PARSER,
        "source_scope_contract": "two_authoritative_current_course_tables",
        "homepage_role": "expired_highlight_reconciliation_only",
        "excluded_notice_url": BOEUN_IEUM_NOTICE_URL,
        "excluded_notice_reason": (
            "editorial_notice_board_uses_nttNo_and_image_attachments_without_"
            "lftmLrnNo_or_identity_bound_application_control"
        ),
        "source_requests": 0,
        "authoritative_requests": 0,
        "homepage_requests": 0,
        "boundary_rechecks": 0,
        "authoritative_table_count": 0,
        "stable_snapshot_recheck": False,
        "detail_pages": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }
    if not is_boeun_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical no-www Boeun owner"
        )
        return [], BOEUN_PARSER, meta
    try:
        cutoff = _today(today)
        if any(
            isinstance(value, bool) or int(value) < 1
            for value in (timeout, max_pages, max_workers)
        ) or isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("invalid limits")
        if int(max_pages) < BOEUN_REQUIRED_SOURCE_REQUESTS:
            raise BoeunContractError(
                f"max_pages {max_pages} below required "
                f"{BOEUN_REQUIRED_SOURCE_REQUESTS} source proof requests"
            )
    except Exception as exc:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], BOEUN_PARSER, meta
    factory, current_fetcher = session_factory or _session, fetcher or _request
    workers = min(int(max_workers), BOEUN_MAX_WORKERS)
    try:
        first = _catalogue(
            _soup(BOEUN_CANONICAL_URL, int(timeout), factory, current_fetcher)
        )
        meta.update({"source_requests": 1, "authoritative_requests": 1})
        highlights = _homepage(
            _soup(BOEUN_HOME_URL, int(timeout), factory, current_fetcher)
        )
        meta.update({"source_requests": 2, "homepage_requests": 1})
        second = _catalogue(
            _soup(BOEUN_CANONICAL_URL, int(timeout), factory, current_fetcher)
        )
        meta.update(
            {
                "source_requests": 3,
                "authoritative_requests": 2,
                "boundary_rechecks": 1,
            }
        )
        if _catalogue_signature(first) != _catalogue_signature(second):
            raise BoeunContractError("authoritative table stability recheck failed")
        listed = list(first["rows"])
        current = [row for row in listed if row["end"] >= cutoff]
        current_by_id = {row["identity"]: row for row in current}
        highlight_current = [row for row in highlights if row["end"] >= cutoff]
        for highlighted in highlight_current:
            owner = current_by_id.get(highlighted["identity"])
            if owner is None:
                raise BoeunContractError(
                    f"homepage current course {highlighted['identity']} missing from owner tables"
                )
            if any(
                owner[field] != highlighted[field]
                for field in ("title", "start", "end")
            ):
                raise BoeunContractError(
                    f"homepage course {highlighted['identity']} identity drift"
                )
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], BOEUN_PARSER, meta
    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "scope_counts": first["scope_counts"],
            "authoritative_table_count": 2,
            "structural_empty_scopes": [
                scope for scope, count in first["scope_counts"].items() if count == 0
            ],
            "stable_snapshot_recheck": True,
            "raw_scope_rows": first["raw_scope_rows"],
            "scope_duplicate_count": first["scope_duplicate_count"],
            "source_rows": len(listed),
            "source_total": len(listed),
            "current_source_count": len(current),
            "expired_authoritative_count": len(listed) - len(current),
            "homepage_highlight_count": len(highlights),
            "homepage_current_count": len(highlight_current),
            "homepage_expired_count": len(highlights) - len(highlight_current),
            "homepage_status_counts": dict(Counter(row["flag"] for row in highlights)),
            "homepage_reconciliation_complete": True,
            "pagination_complete": True,
        }
    )
    if len(current) > int(detail_limit):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit {detail_limit} below required {len(current)}"
                ),
            }
        )
        return [], BOEUN_PARSER, meta
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                lambda item=item: _detail(
                    item,
                    _soup(
                        _detail_url(item["identity"]),
                        int(timeout),
                        factory,
                        current_fetcher,
                    ),
                    cutoff,
                )
            ): item["identity"]
            for item in current
        }
        for future in as_completed(futures):
            try:
                rows.append(future.result())
                meta["detail_pages"] += 1
            except Exception as exc:
                errors.append(
                    f"{futures[future]}: {type(exc).__name__}: {_clean(exc)}"
                )
    if errors:
        meta["configured_collection_error"] = "; ".join(errors[:5])
        return [], BOEUN_PARSER, meta
    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy = [error for row in rows for error in _privacy(row)]
    if privacy or len(rows) != len(current):
        meta["configured_collection_error"] = (
            "; ".join(privacy[:5]) or "dedupe changed identity set"
        )
        return [], BOEUN_PARSER, meta
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
    return rows, BOEUN_PARSER, meta


collect = collect_boeun_education
