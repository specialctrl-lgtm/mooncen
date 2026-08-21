"""Complete, fail-closed education collector for Gyeongnam Goseong-gun.

The official Goseong Lifelong Learning Center exposes one server-rendered
course catalogue.  Two separately configured ``view.goseong`` providers are
individual records from that catalogue, not independent sources.  This
module consequently owns only the canonical list provider and records the
detail providers as duplicate aliases.

A snapshot is returned only after every advertised list page, the immediate
post-last empty sentinel, and stable first/last boundary rechecks succeed.
Only current or future courses receive detail requests.  Detail parsing is
strictly limited to the first public course-summary table: applicant-status
tables, free-form guidance, attachments, and contact numbers are never copied
to output rows.
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
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GOSEONG_GN_HOST = "www.goseong.go.kr"
GOSEONG_GN_LIST_PATH = "/user/lecture/list.goseong"
GOSEONG_GN_DETAIL_PATH = "/user/lecture/view.goseong"
GOSEONG_GN_MENU = "DOM_000003005001000000"
GOSEONG_GN_CONTEXT = "/gsll"
GOSEONG_GN_PROVIDER = "MUNI_WWW_GOSEONG_GO_KR_62A7123B"
GOSEONG_GN_MUNICIPALITY_CODE = "4882000000"
GOSEONG_GN_MUNICIPALITY_NAME = "경상남도 고성군"
GOSEONG_GN_PAGE_SIZE = 8
GOSEONG_GN_MAX_WORKERS = 4
GOSEONG_GN_FETCH_ATTEMPTS = 2
GOSEONG_GN_URL = (
    f"https://{GOSEONG_GN_HOST}{GOSEONG_GN_LIST_PATH}?"
    + urlencode(
        (
            ("menuCd", GOSEONG_GN_MENU),
            ("link", "success"),
            ("cpath", GOSEONG_GN_CONTEXT),
        )
    )
)
GOSEONG_GN_PARSER = (
    "gyeongnam_goseong_complete_catalogue+declared_total+empty_sentinel+"
    "stable_boundaries+current_detail_summary_only+pii_allowlist"
)
GOSEONG_GN_OWNERSHIP_SCOPE = (
    "official_gyeongnam_goseong_lifelong_learning_course_catalogue"
)

GOSEONG_GN_CANDIDATE_IDS: Mapping[str, str] = {
    "official_homepage": "MUNI_IR_902DB17450BA",
    "organization_chart": "MUNI_IR_55CEC6D7A1DD",
    "canonical_course_catalogue": "MUNI_IR_6E0984388691",
    "catalogue_record_24": "MUNI_IR_CAF07F3846D3",
    "catalogue_record_36": "MUNI_IR_8D30AF6D294E",
}


@dataclass(frozen=True)
class GoseongAlias:
    provider: str
    url: str
    relationship: str


GOSEONG_GN_ALIASES = (
    GoseongAlias(
        "MUNI_WWW_GOSEONG_GO_KR_7E5D5F93",
        "https://www.goseong.go.kr/",
        "official homepage discovery page; not a course catalogue",
    ),
    GoseongAlias(
        "MUNI_WWW_GOSEONG_GO_KR_BC3AF560",
        "https://www.goseong.go.kr/index.goseong?menuCd=DOM_000000106004002000",
        "organization chart; not a course catalogue",
    ),
    GoseongAlias(
        "MUNI_WWW_GOSEONG_GO_KR_087D4B9F",
        (
            "https://www.goseong.go.kr/user/lecture/view.goseong?"
            f"menuCd={GOSEONG_GN_MENU}&lectureSid=24"
        ),
        "individual catalogue record; duplicate of canonical provider",
    ),
    GoseongAlias(
        "MUNI_WWW_GOSEONG_GO_KR_A4DA3E74",
        (
            "https://www.goseong.go.kr/user/lecture/view.goseong?"
            f"menuCd={GOSEONG_GN_MENU}&lectureSid=36"
        ),
        "individual catalogue record; duplicate of canonical provider",
    ),
)

Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_VIEW_ACTION_RE = re.compile(r"\s*goView\(\s*([1-9]\d*)\s*,\s*this\s*\)\s*")
_PAGE_ACTION_RE = re.compile(r"\s*linkPage\(\s*([1-9]\d*)\s*\)\s*")
_TOTAL_RE = re.compile(
    r"총\s*([\d,]+)건\s*\[\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\]"
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}|0\d{8,10})(?!\d)"
)
_LOCAL_PHONE_RE = re.compile(r"(?<!\d)\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MASKED_NAME_RE = re.compile(r"(?<![가-힣])[가-힣][OoＯ○*]{2}(?![가-힣])")
_GO_AGREE_RE = re.compile(
    r"\s*goAgree\(\s*['\"]\d+['\"]\s*,\s*['\"]\d+['\"]\s*\)\s*"
)
_CANCELLED_RE = re.compile(r"(?:^|[<\[(])\s*(?:폐강|취소)\s*(?:$|[>\])])")

_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
_STATUS_CLASS: Mapping[str, str] = {
    "접수대기": "wait",
    "접수중": "receipt",
    "접수마감": "deadline",
}
_LIST_LABELS = ("장소", "대상", "접수기간", "교육기간")
_DETAIL_KEYS = {
    "장소",
    "대상",
    "접수기간",
    "교육기간",
    "이용요금",
    "신청인원 / 모집인원 (대기인원)",
    "예약방법",
    "담당부서 / 문의전화",
    "첨부파일",
}
_METHODS = {"인터넷", "인터넷,방문", "방문", "방문,전화", "전화"}


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


def is_gyeongnam_goseong_education_target(target: Any) -> bool:
    """Match only the existing canonical list provider and exact list scope."""

    parsed = urlparse(_target_url(target))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        _provider(target) == GOSEONG_GN_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GOSEONG_GN_HOST
        and parsed.port is None
        and parsed.path == GOSEONG_GN_LIST_PATH
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
        and set(query) == {"menuCd", "link", "cpath"}
        and query.get("menuCd") == [GOSEONG_GN_MENU]
        and query.get("link") == ["success"]
        and query.get("cpath") == [GOSEONG_GN_CONTEXT]
    )


is_target = is_gyeongnam_goseong_education_target


def is_gyeongnam_goseong_alias_target(target: Any) -> bool:
    provider = _provider(target)
    url = _target_url(target)
    return any(provider == alias.provider and url == alias.url for alias in GOSEONG_GN_ALIASES)


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


def _response_soup(
    response: Any, *, expected_path: str
) -> tuple[BeautifulSoup, str]:
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
        or (parsed.hostname or "").rstrip(".").lower() != GOSEONG_GN_HOST
        or parsed.port is not None
        or parsed.path != expected_path
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
    method: str,
    url: str,
    *,
    timeout: int,
    expected_path: str,
    data: Optional[Mapping[str, str]] = None,
    fetcher: Optional[Fetcher] = None,
) -> tuple[BeautifulSoup, str]:
    messages: list[str] = []
    for attempt in range(1, GOSEONG_GN_FETCH_ATTEMPTS + 1):
        try:
            if fetcher is not None:
                result = fetcher(
                    current,
                    method,
                    url,
                    timeout=timeout,
                    data=dict(data or {}),
                )
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and isinstance(result[0], BeautifulSoup)
                ):
                    soup, final_url = result
                    final = urlparse(_clean(final_url or url))
                    if (
                        final.scheme.lower() != "https"
                        or (final.hostname or "").rstrip(".").lower()
                        != GOSEONG_GN_HOST
                        or final.path != expected_path
                        or final.port is not None
                    ):
                        raise ValueError("source response URL changed")
                    return soup, _clean(final_url or url)
                if isinstance(result, BeautifulSoup):
                    return result, url
                if isinstance(result, (str, bytes, bytearray)):
                    if not result:
                        raise ValueError("empty HTML response")
                    return BeautifulSoup(result, "lxml"), url
                return _response_soup(result, expected_path=expected_path)
            if method == "POST":
                response = current.post(url, data=dict(data or {}), timeout=timeout)
            else:
                response = current.get(url, timeout=timeout)
            return _response_soup(response, expected_path=expected_path)
        except Exception as exc:
            messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
    raise ValueError("; ".join(messages))


def _list_payload(page: int) -> dict[str, str]:
    return {
        "lectureCate": "",
        "pageIndex": str(page),
        "pageUnit": str(GOSEONG_GN_PAGE_SIZE),
        "pageSize": "5",
        "menuCd": GOSEONG_GN_MENU,
        "lectureStatus": "",
        "agencyCode": "",
        "lectureType": "",
        "lectureTarget": "",
        "lectureNm": "",
    }


def _detail_payload(identity: str, page: int) -> dict[str, str]:
    return {
        "pageIndex": str(page),
        "pageUnit": str(GOSEONG_GN_PAGE_SIZE),
        "pageSize": "5",
        "menuCd": GOSEONG_GN_MENU,
        "lectureSid": identity,
    }


def gyeongnam_goseong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return (
        f"https://{GOSEONG_GN_HOST}{GOSEONG_GN_DETAIL_PATH}?"
        + urlencode((("menuCd", GOSEONG_GN_MENU), ("lectureSid", value)))
    )


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


def _form_and_total(
    soup: BeautifulSoup, page: int
) -> tuple[int, int, list[str]]:
    errors: list[str] = []
    forms = soup.select("form[name='searchForm']")
    if len(forms) != 1:
        return 0, 0, ["expected one searchForm"]
    form = forms[0]
    if (
        _clean(form.get("method")).lower() != "post"
        or urlparse(_clean(form.get("action"))).path != GOSEONG_GN_LIST_PATH
    ):
        errors.append("unexpected searchForm method/action")
    expected = {
        "lectureCate": "",
        "pageIndex": str(page),
        "pageUnit": str(GOSEONG_GN_PAGE_SIZE),
        "pageSize": "5",
        "menuCd": GOSEONG_GN_MENU,
        "lectureNm": "",
    }
    for key, value in expected.items():
        field = form.select_one(f"input[name='{key}']")
        if field is None or _clean(field.get("value")) != value:
            errors.append(f"searchForm {key} mismatch")
    for name in ("lectureStatus", "agencyCode", "lectureType", "lectureTarget"):
        fields = form.select(f"select[name='{name}']")
        if len(fields) != 1 or _selected_value(fields[0]):
            errors.append(f"searchForm {name} is not the unfiltered scope")
    status = form.select_one("select[name='lectureStatus']")
    options = (
        {_clean(item.get("value")): _clean(item.get_text(" ", strip=True)) for item in status.select("option")}
        if status is not None
        else {}
    )
    if any(options.get(code) != label for code, label in (("1", "접수대기"), ("2", "접수중"), ("3", "접수마감"))):
        errors.append("searchForm status vocabulary changed")

    totals = soup.select("div.total")
    if len(totals) != 1:
        return 0, 0, [*errors, "expected one declared list total"]
    match = _TOTAL_RE.fullmatch(_clean(totals[0].get_text(" ", strip=True)))
    if not match:
        return 0, 0, [*errors, "declared list total is malformed"]
    total, displayed_page, last_page = (int(value.replace(",", "")) for value in match.groups())
    if displayed_page != page:
        errors.append("declared current page mismatch")
    expected_last = max(1, math.ceil(total / GOSEONG_GN_PAGE_SIZE))
    if last_page != expected_last:
        errors.append("declared last page does not match total/page size")
    last_controls = soup.select("a.bdNumLast[onclick]")
    # The server intentionally removes pagination controls for a requested
    # page beyond the last one while retaining ``[ requested / last ]`` in
    # the declared total.  Data pages must still expose the last control.
    if last_page > 1 and page <= last_page:
        values = {
            int(match.group(1))
            for item in last_controls
            if (match := _PAGE_ACTION_RE.fullmatch(_clean(item.get("onclick"))))
        }
        if values != {last_page}:
            errors.append("last-page control mismatch")
    return total, last_page, errors


def _detail_navigation_errors(soup: BeautifulSoup, page: int) -> list[str]:
    forms = soup.select("form[name='goLecDetail']")
    if len(forms) != 1:
        return ["expected one goLecDetail form"]
    form = forms[0]
    errors: list[str] = []
    if (
        _clean(form.get("method")).lower() != "post"
        or urlparse(_clean(form.get("action"))).path != GOSEONG_GN_DETAIL_PATH
    ):
        errors.append("unexpected goLecDetail method/action")
    expected = {
        "pageIndex": str(page),
        "pageUnit": str(GOSEONG_GN_PAGE_SIZE),
        "pageSize": "5",
        "menuCd": GOSEONG_GN_MENU,
        "lectureSid": "",
    }
    for key, value in expected.items():
        field = form.select_one(f"input[name='{key}']")
        if field is None or _clean(field.get("value")) != value:
            errors.append(f"goLecDetail {key} mismatch")
    return errors


def _parse_list_page(
    soup: BeautifulSoup, *, page: int
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    scoped = soup.select("div.board-edu-list ul.list-wrap > li > a[onclick*='goView']")
    all_actions = soup.select("a[onclick*='goView']")
    if len(scoped) != len(all_actions):
        errors.append("unscoped goView controls were found")
    for index, anchor in enumerate(scoped, 1):
        row_errors: list[str] = []
        action = _VIEW_ACTION_RE.fullmatch(_clean(anchor.get("onclick")))
        identity = action.group(1) if action else ""
        title_node = anchor.select_one("dl > dt.tit")
        state_node = anchor.select_one("span.state")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        source_status = _clean(state_node.get_text(" ", strip=True) if state_node else "")
        classes = {_clean(value) for value in (anchor.get("class") or [])}
        if not identity:
            row_errors.append("missing source identity")
        if not title:
            row_errors.append("empty title")
        if source_status not in _STATUS_MAP:
            row_errors.append(f"unknown status {source_status!r}")
        elif _STATUS_CLASS[source_status] not in classes:
            row_errors.append("status text/class mismatch")
        definitions = anchor.select("dl > dd")
        if len(definitions) != len(_LIST_LABELS):
            row_errors.append("unexpected list field count")
            values = ["", "", "", ""]
        else:
            values = []
            for expected_label, node in zip(_LIST_LABELS, definitions):
                text = _clean(node.get_text(" ", strip=True))
                label, separator, value = text.partition(":")
                if not separator or _clean(label) != expected_label:
                    row_errors.append(f"expected list field {expected_label}")
                values.append(_clean(value))
        venue, target, apply_period, period = values
        apply_dates = _dates(apply_period)
        period_dates = _dates(period)
        if len(apply_dates) != 2:
            row_errors.append("application period is malformed")
        if len(period_dates) != 2:
            row_errors.append("education period is malformed")
        if not venue or not target:
            row_errors.append("venue or target is empty")
        if row_errors:
            errors.extend(
                f"page {page} row {index}: {message}" for message in row_errors
            )
            continue
        raw_url = gyeongnam_goseong_detail_url(identity)
        rows.append(
            {
                "provider": GOSEONG_GN_PROVIDER,
                "provider_course_id": f"{GOSEONG_GN_PROVIDER}:lecture:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": GOSEONG_GN_MUNICIPALITY_NAME,
                "branch_code": "gyeongnam-goseong",
                "municipality_code": GOSEONG_GN_MUNICIPALITY_CODE,
                "municipality_name": GOSEONG_GN_MUNICIPALITY_NAME,
                "sido": "경상남도",
                "sigungu": "고성군",
                "provider_organizer": GOSEONG_GN_MUNICIPALITY_NAME,
                "venue_name": venue,
                "category": "평생학습",
                "program_type": "강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": _STATUS_MAP[source_status],
                "period": period,
                "start_date": period_dates[0].isoformat(),
                "end_date": period_dates[1].isoformat(),
                "apply_period": apply_period,
                "apply_start": apply_dates[0].isoformat(),
                "apply_end": apply_dates[1].isoformat(),
                "schedule_raw": "",
                "fee": "",
                "target": target,
                "description": title,
                "source_group": "lifelong_learning",
                "collection_category": "평생학습",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_html_pages+current_detail_summary",
                "raw_fields": {
                    "parser": GOSEONG_GN_PARSER,
                    "source_catalog": "gyeongnam_goseong_lifelong_courses",
                    "source_lecture_sid": identity,
                    "source_page": page,
                    "source_status": source_status,
                    "source_status_class": _STATUS_CLASS[source_status],
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
            _clean(row.get("apply_period")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _summary_pairs(root: Any) -> tuple[dict[str, str], list[str]]:
    wrappers = root.find_all("div", class_="table-wrap", recursive=False)
    if not wrappers:
        return {}, ["missing course summary table"]
    table = wrappers[0].select_one("table.type01")
    if table is None:
        return {}, ["missing course summary table"]
    pairs: dict[str, str] = {}
    for row in table.select("tbody > tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index + 1 < len(cells):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                index += 1
                continue
            key = _clean(cells[index].get_text(" ", strip=True))
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if not key or key in pairs:
                return {}, ["duplicate or empty course summary label"]
            pairs[key] = value
            index += 2
    if set(pairs) != _DETAIL_KEYS:
        return pairs, ["course summary field vocabulary changed"]
    return pairs, []


def _safe_organizer(value: Any) -> str:
    parts: list[str] = []
    for raw_part in re.split(r"\s*/\s*", _clean(value)):
        part = _clean(_LOCAL_PHONE_RE.sub("", _PHONE_RE.sub("", raw_part))).strip(
            " -:;,/"
        )
        if (
            part
            and not _PHONE_RE.search(part)
            and not _LOCAL_PHONE_RE.search(part)
            and part not in parts
        ):
            parts.append(part)
    return " / ".join(parts)


def _safe_external_url(value: Any) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    absolute = urljoin(f"https://{GOSEONG_GN_HOST}/", raw)
    parsed = urlparse(absolute)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
    ):
        return ""
    return urlunparse(parsed._replace(fragment=""))


def _detail_row(
    parent: dict[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any], list[str]]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_lecture_sid"))
    source_status = _clean(raw.get("source_status"))
    page = int(raw.get("source_page") or 0)
    errors: list[str] = []
    roots = soup.select("div.board-edu-view")
    if len(roots) != 1:
        return dict(parent), [f"detail {identity}: expected one course detail root"]
    root = roots[0]
    headings = root.select("h5.edu-tit")
    if len(headings) != 1:
        errors.append(f"detail {identity}: expected one title")
    else:
        heading = headings[0]
        if _clean(heading.get_text(" ", strip=True)) != _clean(parent.get("title")):
            errors.append(f"detail {identity}: list/detail title mismatch")
        classes = {_clean(value) for value in (heading.get("class") or [])}
        if _STATUS_CLASS.get(source_status) not in classes:
            errors.append(f"detail {identity}: status class mismatch")

    forms = soup.select("form[name='goAgreeForm']")
    if len(forms) != 1:
        errors.append(f"detail {identity}: expected one goAgreeForm")
    else:
        form = forms[0]
        if (
            _clean(form.get("method")).lower() != "post"
            or urlparse(_clean(form.get("action"))).path != "/user/apply/agree.goseong"
        ):
            errors.append(f"detail {identity}: unexpected application form")
        expected = {
            "pageIndex": str(page),
            "pageUnit": str(GOSEONG_GN_PAGE_SIZE),
            "pageSize": "5",
            "menuCd": GOSEONG_GN_MENU,
            "lectureSid": identity,
        }
        for key, value in expected.items():
            field = form.select_one(f"input[name='{key}']")
            if field is None or _clean(field.get("value")) != value:
                errors.append(f"detail {identity}: application form {key} mismatch")

    pairs, pair_errors = _summary_pairs(root)
    errors.extend(f"detail {identity}: {message}" for message in pair_errors)
    period_dates = _dates(pairs.get("교육기간"))
    apply_dates = _dates(pairs.get("접수기간"))
    if period_dates != [
        date.fromisoformat(_clean(parent.get("start_date"))),
        date.fromisoformat(_clean(parent.get("end_date"))),
    ]:
        errors.append(f"detail {identity}: list/detail education period mismatch")
    if apply_dates != [
        date.fromisoformat(_clean(parent.get("apply_start"))),
        date.fromisoformat(_clean(parent.get("apply_end"))),
    ]:
        errors.append(f"detail {identity}: list/detail application period mismatch")
    if _clean(pairs.get("장소")) != _clean(parent.get("venue_name")):
        errors.append(f"detail {identity}: list/detail venue mismatch")
    if _clean(pairs.get("대상")) != _clean(parent.get("target")):
        errors.append(f"detail {identity}: list/detail target mismatch")
    fee = _clean(pairs.get("이용요금"))
    method = _clean(pairs.get("예약방법")).replace(" ", "")
    organizer = _safe_organizer(pairs.get("담당부서 / 문의전화"))
    if not fee:
        errors.append(f"detail {identity}: empty fee")
    if method not in _METHODS:
        errors.append(f"detail {identity}: unknown application method {method!r}")
    if (
        not organizer
        or _PHONE_RE.search(organizer)
        or _LOCAL_PHONE_RE.search(organizer)
        or _EMAIL_RE.search(organizer)
    ):
        errors.append(f"detail {identity}: organizer PII sanitization failed")

    primary_controls: list[Any] = []
    for wrapper in root.find_all("div", class_="btn-wrap", recursive=False):
        primary_controls.extend(wrapper.select("a.bbtn.type01"))
    if len(primary_controls) != 1:
        errors.append(f"detail {identity}: expected one primary application control")
        control_label = control_kind = external_url = ""
    else:
        control = primary_controls[0]
        control_label = _clean(control.get_text(" ", strip=True))
        external_url = ""
        if control_label == "신청하기":
            if (
                _clean(control.get("href")) != "#n"
                or not _GO_AGREE_RE.fullmatch(_clean(control.get("onclick")))
            ):
                errors.append(f"detail {identity}: malformed internal application control")
            control_kind = "internal"
        elif control_label == "홈페이지":
            external_url = _safe_external_url(control.get("href"))
            control_kind = "external" if external_url else "external_empty"
        else:
            control_kind = "unknown"
            errors.append(f"detail {identity}: unknown primary application control")

    normalized_status = _STATUS_MAP.get(source_status, "")
    application_url = ""
    application_type = "INFO_ONLY"
    reservation_available = False
    if normalized_status == "OPEN":
        if "인터넷" in method:
            if control_kind == "internal":
                application_url = _clean(parent.get("raw_url"))
            elif control_kind == "external" and external_url:
                application_url = external_url
            else:
                errors.append(f"detail {identity}: open online course lacks an active control")
            if application_url:
                application_type = "ONLINE_RESERVATION"
                reservation_available = True
        elif any(token in method for token in ("방문", "전화")):
            application_type = "OFFLINE_APPLY"
        else:
            errors.append(f"detail {identity}: open course has no supported method")
    elif normalized_status not in {"SCHEDULED", "CLOSED"}:
        errors.append(f"detail {identity}: unknown normalized status")

    branch = organizer or GOSEONG_GN_MUNICIPALITY_NAME
    row = dict(parent)
    row.update(
        {
            "branch": branch,
            "branch_code": (
                "gyeongnam-goseong-"
                + hashlib.sha256(branch.encode("utf-8")).hexdigest()[:12]
            ),
            "provider_organizer": branch,
            "application_url": application_url,
            "application_type": application_type,
            "reservation_available": reservation_available,
            "status": normalized_status,
            "period": _clean(pairs.get("교육기간")),
            "apply_period": _clean(pairs.get("접수기간")),
            "fee": fee,
            "target": _clean(pairs.get("대상")),
            "venue_name": _clean(pairs.get("장소")),
        }
    )
    row["raw_fields"] = {
        **raw,
        "source_application_method": method,
        "source_application_control": control_label,
        "detail_validated": not errors,
        "applicant_table_excluded": True,
        "free_form_detail_excluded": True,
        "attachments_excluded": True,
        "contact_excluded": True,
    }
    return row, errors


def _details(
    rows: list[dict[str, Any]],
    *,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    fetcher: Optional[Fetcher],
) -> tuple[list[dict[str, Any]], list[str], int]:
    if not rows:
        return [], [], 0

    def one(parent: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        current = session_factory()
        try:
            raw = parent.get("raw_fields", {})
            identity = _clean(raw.get("source_lecture_sid"))
            page = int(raw.get("source_page") or 0)
            soup, _ = _request_soup(
                current,
                "POST",
                f"https://{GOSEONG_GN_HOST}{GOSEONG_GN_DETAIL_PATH}",
                timeout=timeout,
                expected_path=GOSEONG_GN_DETAIL_PATH,
                data=_detail_payload(identity, page),
                fetcher=fetcher,
            )
            return _detail_row(parent, soup)
        finally:
            _close_quietly(current)

    found: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    attempts = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(one, row): row for row in rows}
        for future in as_completed(futures):
            parent = futures[future]
            identity = _clean(parent.get("raw_fields", {}).get("source_lecture_sid"))
            attempts += 1
            try:
                row, item_errors = future.result()
                if item_errors:
                    errors.extend(item_errors)
                else:
                    found[identity] = row
            except Exception as exc:
                errors.append(
                    f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                )
    ordered = [
        found[_clean(row.get("raw_fields", {}).get("source_lecture_sid"))]
        for row in rows
        if _clean(row.get("raw_fields", {}).get("source_lecture_sid")) in found
    ]
    return ordered, errors, attempts


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
        violations += len(_LOCAL_PHONE_RE.findall(serialized))
        violations += len(_EMAIL_RE.findall(serialized))
        violations += len(_MASKED_NAME_RE.findall(serialized))
        violations += sum(key in row for key in forbidden)
        raw = row.get("raw_fields", {})
        if isinstance(raw, Mapping):
            violations += sum(key in raw for key in forbidden)
    return violations


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "cancelled_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "sentinel_count": None,
        "stable_rechecks": {},
        "duplicate_source_id_count": 0,
        "privacy_violations": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": GOSEONG_GN_MUNICIPALITY_CODE,
        "municipality_name": GOSEONG_GN_MUNICIPALITY_NAME,
        "ownership_scope": GOSEONG_GN_OWNERSHIP_SCOPE,
        "candidate_ids": dict(GOSEONG_GN_CANDIDATE_IDS),
        "ownership_aliases": [
            {
                "provider": alias.provider,
                "url": alias.url,
                "relationship": alias.relationship,
            }
            for alias in GOSEONG_GN_ALIASES
        ],
    }


def collect_gyeongnam_goseong_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 60,
    detail_limit: int = 200,
    *,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GOSEONG_GN_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future official Goseong course snapshot."""

    meta = _base_meta()
    if not is_gyeongnam_goseong_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the canonical Gyeongnam Goseong education route"
        )
        return [], GOSEONG_GN_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = (
                "managed session_factory injection is required"
            )
            return [], GOSEONG_GN_PARSER, meta
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        request_timeout = max(1, int(timeout))
        workers = min(max(1, int(max_workers)), GOSEONG_GN_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], GOSEONG_GN_PARSER, meta

    errors: list[str] = []
    source_cap_reached = False
    first_soup: Optional[BeautifulSoup] = None
    first_rows: list[dict[str, Any]] = []
    declared_total = last_page = 0
    initial = session_factory()
    try:
        try:
            first_soup, _ = _request_soup(
                initial,
                "GET",
                GOSEONG_GN_URL,
                timeout=request_timeout,
                expected_path=GOSEONG_GN_LIST_PATH,
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
        except Exception as exc:
            errors.append(f"first page: {type(exc).__name__}: {_clean(exc)}")
    finally:
        _close_quietly(initial)

    if first_soup is not None:
        declared_total, last_page, item_errors = _form_and_total(first_soup, 1)
        errors.extend(item_errors)
        errors.extend(_detail_navigation_errors(first_soup, 1))
        first_rows, item_errors = _parse_list_page(first_soup, page=1)
        errors.extend(item_errors)
        if declared_total and not first_rows:
            errors.append("first page contains no course rows")

    boundary_count = 1 if last_page == 1 else 2
    required_list_requests = last_page + 1 + boundary_count if last_page else 0
    meta["required_list_requests"] = required_list_requests
    if required_list_requests > allowed_pages:
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of {required_list_requests} "
            "required list/sentinel/recheck requests"
        )

    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    sentinel_count: Optional[int] = None
    stable_rechecks: dict[str, bool] = {}
    crawl_session = session_factory()
    try:
        if not errors:
            for page in range(2, last_page + 1):
                soup, _ = _request_soup(
                    crawl_session,
                    "POST",
                    f"https://{GOSEONG_GN_HOST}{GOSEONG_GN_LIST_PATH}",
                    timeout=request_timeout,
                    expected_path=GOSEONG_GN_LIST_PATH,
                    data=_list_payload(page),
                    fetcher=fetcher,
                )
                meta["pages"] += 1
                meta["list_requests"] += 1
                total, displayed_last, item_errors = _form_and_total(soup, page)
                errors.extend(item_errors)
                errors.extend(_detail_navigation_errors(soup, page))
                if total != declared_total or displayed_last != last_page:
                    errors.append(f"page {page}: declared pagination changed")
                parsed, item_errors = _parse_list_page(soup, page=page)
                errors.extend(item_errors)
                pages[page] = parsed

            sentinel_page = last_page + 1
            soup, _ = _request_soup(
                crawl_session,
                "POST",
                f"https://{GOSEONG_GN_HOST}{GOSEONG_GN_LIST_PATH}",
                timeout=request_timeout,
                expected_path=GOSEONG_GN_LIST_PATH,
                data=_list_payload(sentinel_page),
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
            total, displayed_last, item_errors = _form_and_total(soup, sentinel_page)
            errors.extend(item_errors)
            errors.extend(_detail_navigation_errors(soup, sentinel_page))
            sentinel_rows, item_errors = _parse_list_page(soup, page=sentinel_page)
            errors.extend(item_errors)
            sentinel_count = len(sentinel_rows)
            if total != declared_total or displayed_last != last_page or sentinel_rows:
                errors.append("immediate post-last sentinel is not empty/stable")

            for page in dict.fromkeys((1, last_page)):
                if page == 1:
                    soup, _ = _request_soup(
                        crawl_session,
                        "GET",
                        GOSEONG_GN_URL,
                        timeout=request_timeout,
                        expected_path=GOSEONG_GN_LIST_PATH,
                        fetcher=fetcher,
                    )
                else:
                    soup, _ = _request_soup(
                        crawl_session,
                        "POST",
                        f"https://{GOSEONG_GN_HOST}{GOSEONG_GN_LIST_PATH}",
                        timeout=request_timeout,
                        expected_path=GOSEONG_GN_LIST_PATH,
                        data=_list_payload(page),
                        fetcher=fetcher,
                    )
                meta["pages"] += 1
                meta["list_requests"] += 1
                total, displayed_last, item_errors = _form_and_total(soup, page)
                errors.extend(item_errors)
                errors.extend(_detail_navigation_errors(soup, page))
                parsed, item_errors = _parse_list_page(soup, page=page)
                errors.extend(item_errors)
                stable = bool(
                    total == declared_total
                    and displayed_last == last_page
                    and _page_signature(parsed) == _page_signature(pages.get(page, []))
                )
                stable_rechecks[str(page)] = stable
                if not stable:
                    errors.append(f"page {page}: stable boundary recheck changed")
    except Exception as exc:
        errors.append(f"catalogue traversal: {type(exc).__name__}: {_clean(exc)}")
    finally:
        _close_quietly(crawl_session)

    source_rows = [
        row for page in range(1, last_page + 1) for row in pages.get(page, [])
    ]
    for page in range(1, last_page):
        if len(pages.get(page, [])) != GOSEONG_GN_PAGE_SIZE:
            errors.append(f"page {page}: expected a full page")
    last_count = len(pages.get(last_page, [])) if last_page else 0
    if declared_total == 0:
        if last_count:
            errors.append("empty catalogue has a nonempty last page")
    elif last_page and not 1 <= last_count <= GOSEONG_GN_PAGE_SIZE:
        errors.append("last page cardinality is invalid")
    if declared_total != len(source_rows):
        errors.append(
            f"declared total {declared_total} != parsed total {len(source_rows)}"
        )
    identities = [_clean(row.get("provider_course_id")) for row in source_rows]
    duplicate_source_ids = len(identities) - len(set(identities))
    if duplicate_source_ids:
        errors.append(f"{duplicate_source_ids} duplicate source identities")
    numeric_ids = [
        int(_clean(row.get("raw_fields", {}).get("source_lecture_sid")))
        for row in source_rows
        if _clean(row.get("raw_fields", {}).get("source_lecture_sid")).isdigit()
    ]
    if numeric_ids != sorted(numeric_ids, reverse=True):
        errors.append("source identities are not in stable descending order")

    current_rows: list[dict[str, Any]] = []
    expired_count = cancelled_count = 0
    for row in source_rows:
        try:
            ended = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
            continue
        if ended < cutoff:
            expired_count += 1
        elif _CANCELLED_RE.search(_clean(row.get("title"))):
            cancelled_count += 1
        else:
            current_rows.append(row)

    if len(current_rows) > allowed_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {len(current_rows)} "
            "required current/future details"
        )

    detailed: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    detail_attempts = 0
    if not errors:
        detailed, detail_errors, detail_attempts = _details(
            current_rows,
            session_factory=session_factory,
            timeout=request_timeout,
            max_workers=workers,
            fetcher=fetcher,
        )
    errors.extend(detail_errors)
    details_complete = bool(
        not detail_errors
        and detail_attempts == len(current_rows)
        and len(detailed) == len(current_rows)
    )

    result: list[dict[str, Any]] = []
    if not errors and details_complete:
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(detailed))
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
        and sentinel_count == 0
        and len(stable_rechecks) == expected_rechecks
        and all(stable_rechecks.values())
        and meta["list_requests"] == required_list_requests
    )
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
            "declared_total": declared_total,
            "data_pages": last_page,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "expired_count": expired_count,
            "cancelled_count": cancelled_count,
            "detail_attempts": detail_attempts,
            "detail_pages": len(detailed),
            "detail_errors": len(detail_errors),
            "sentinel_count": sentinel_count,
            "stable_rechecks": stable_rechecks,
            "duplicate_source_id_count": duplicate_source_ids,
            "privacy_violations": privacy_violations,
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "application_type_counts": dict(application_counts),
            "pagination_detected": last_page > 1,
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "complete Gyeongnam Goseong catalogue contains only ended/cancelled courses"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, GOSEONG_GN_PARSER, meta


collect = collect_gyeongnam_goseong_education_courses


__all__ = [
    "GOSEONG_GN_ALIASES",
    "GOSEONG_GN_CANDIDATE_IDS",
    "GOSEONG_GN_MENU",
    "GOSEONG_GN_MUNICIPALITY_CODE",
    "GOSEONG_GN_MUNICIPALITY_NAME",
    "GOSEONG_GN_OWNERSHIP_SCOPE",
    "GOSEONG_GN_PARSER",
    "GOSEONG_GN_PROVIDER",
    "GOSEONG_GN_URL",
    "GoseongAlias",
    "collect",
    "collect_gyeongnam_goseong_education_courses",
    "gyeongnam_goseong_detail_url",
    "is_gyeongnam_goseong_alias_target",
    "is_gyeongnam_goseong_education_target",
    "is_target",
]
