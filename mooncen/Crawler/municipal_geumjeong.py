"""Fail-closed education collector for Busan Geumjeong-gu.

Geumjeong-gu publishes two disjoint official course catalogues:

* resident-centre courses on Busan's integrated reservation service; and
* Geumjeong lifelong-learning courses in the Geumjeong office partition of
  the Busan Lifelong Learning Platform.

The local lifelong-learning portal links to the latter platform, while its
search candidate is only a notice board.  The Geumjeong-gu education landing
page is likewise a guide/redirect rather than a course catalogue.  This
collector therefore owns the two source partitions above and exposes the
other pages as audited aliases or non-catalogue discovery pages.

Both source partitions are crawled through their displayed last page, an
immediate post-last sentinel, and stable first/last boundary rechecks.  Every
current or future course detail is then verified.  A failure in either source
causes the combined snapshot to be discarded.  Phone numbers, e-mail
addresses, instructor names, and unfiltered detail payloads are not stored.

The module deliberately does not import ``Crawler_MunicipalYaml``.  The
shared router must inject its managed session factory, avoiding an import
cycle and preserving the outbound HTTP security boundary.
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
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from Crawler import municipal_busan_lifelong as busan_lifelong


GEUMJEONG_MUNICIPALITY_CODE = "2641000000"
GEUMJEONG_MUNICIPALITY_NAME = "부산광역시 금정구"
GEUMJEONG_PROVIDER = "MUNI_RESERVE_BUSAN_GO_KR_2CB22A99"
GEUMJEONG_BRANCH = GEUMJEONG_MUNICIPALITY_NAME

GEUMJEONG_RESERVE_HOST = "reserve.busan.go.kr"
GEUMJEONG_RESERVE_PATH = "/lctre/list"
GEUMJEONG_RESERVE_DETAIL_PATH = "/lctre/view"
GEUMJEONG_RESERVE_URL = (
    "https://reserve.busan.go.kr/lctre/list?"
    "srchGugun=2&srchResveInsttCd=33"
)
GEUMJEONG_RESERVE_PAGE_SIZE = 10

GEUMJEONG_LIFELONG_HOST = busan_lifelong.BUSAN_LIFELONG_HOST
GEUMJEONG_LIFELONG_URL = busan_lifelong.BUSAN_LIFELONG_URL
GEUMJEONG_LIFELONG_LIST_URL = busan_lifelong.BUSAN_LIFELONG_LIST_URL
GEUMJEONG_LIFELONG_OFFICE_CODE = "OFFICE_00002660"
GEUMJEONG_LIFELONG_OFFICE_NAME = "금정구청"
GEUMJEONG_LIFELONG_EXPECTED_OWNERSHIP = "duplicate_dedicated_geumjeong_owner"
GEUMJEONG_LIFELONG_PAGE_SIZE = 100

GEUMJEONG_MAX_WORKERS = 4
GEUMJEONG_FETCH_ATTEMPTS = 2
GEUMJEONG_PARSER = (
    "geumjeong_reserve_complete_pages+sentinel+stable_boundaries+"
    "current_detail+lifelong_office_complete_archive+sentinel+"
    "stable_boundaries+current_detail+pii_allowlist"
)
GEUMJEONG_OWNERSHIP_SCOPE = (
    "geumjeong_resident_centre_courses_and_geumjeong_lifelong_office_courses"
)

GEUMJEONG_CANDIDATE_IDS: Mapping[str, str] = {
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
    "geumjeong_lifelong_landing": "MUNI_IR_1F10B5A64EC7",
    "geumjeong_lifelong_notice_board": "MUNI_IR_25B29F5C6E7D",
    "busan_reserve_geumjeong": "MUNI_IR_D3561ECC97DC",
    "geumjeong_resident_program_guide": "MUNI_IR_9B610BD87527",
}


@dataclass(frozen=True)
class GeumjeongAlias:
    provider: str
    url: str
    relationship: str


GEUMJEONG_OWNERSHIP_ALIASES: tuple[GeumjeongAlias, ...] = (
    GeumjeongAlias(
        "MUNI_LLL_BUSAN_GO_KR_944C621B",
        GEUMJEONG_LIFELONG_URL,
        "federated catalogue; only OFFICE_00002660 is owned by Geumjeong-gu",
    ),
    GeumjeongAlias(
        "MUNI_LLL_GEUMJEONG_GO_KR_2855EC3B",
        "https://lll.geumjeong.go.kr/",
        "local landing page whose course links point to lll.busan.go.kr",
    ),
    GeumjeongAlias(
        "MUNI_LLL_GEUMJEONG_GO_KR_07ABBEC3",
        (
            "https://lll.geumjeong.go.kr/index.geumj?"
            "menuCd=DOM_000000805001000000"
        ),
        "notice board, not a structured course catalogue",
    ),
    GeumjeongAlias(
        "MUNI_WWW_GEUMJEONG_GO_KR_C5590860",
        (
            "https://www.geumjeong.go.kr/index.geumj?"
            "menuCd=DOM_000000130003001000"
        ),
        "resident-program guide; current records live on reserve.busan.go.kr",
    ),
)


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Fetcher = Callable[..., Any]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_RESERVE_ACTION_RE = re.compile(
    r"fn_viewProgrm\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]\s*\)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_RESERVE_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "대기중": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return _SPACE_RE.sub(" ", str(value).replace("\xa0", " ")).strip()


def _provider(target: Any) -> str:
    return _clean(target.get("provider") if isinstance(target, Mapping) else getattr(target, "provider", ""))


def _target_url(target: Any) -> str:
    return _clean(target.get("url") if isinstance(target, Mapping) else getattr(target, "url", ""))


def is_geumjeong_education_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        _provider(target) == GEUMJEONG_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GEUMJEONG_RESERVE_HOST
        and parsed.port is None
        and parsed.path == GEUMJEONG_RESERVE_PATH
        and set(query) == {"srchGugun", "srchResveInsttCd"}
        and query.get("srchGugun") == ["2"]
        and query.get("srchResveInsttCd") == ["33"]
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_geumjeong_education_target


def is_geumjeong_owned_alias_target(target: Any) -> bool:
    provider = _provider(target)
    url = _target_url(target)
    for alias in GEUMJEONG_OWNERSHIP_ALIASES[1:]:
        if provider == alias.provider and url == alias.url:
            return True
    return False


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


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
    expected_host: str,
    expected_path: str,
) -> tuple[BeautifulSoup, str]:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("HTTP redirects are not accepted for source pages")
    final_url = _clean(getattr(response, "url", ""))
    parsed = urlparse(final_url)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != expected_host
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
    expected_host: str,
    expected_path: str,
    data: Optional[Mapping[str, str]] = None,
    fetcher: Optional[Fetcher] = None,
) -> tuple[BeautifulSoup, str]:
    messages: list[str] = []
    for attempt in range(1, GEUMJEONG_FETCH_ATTEMPTS + 1):
        try:
            if fetcher is not None:
                result = fetcher(
                    current,
                    method,
                    url,
                    timeout=timeout,
                    data=dict(data or {}),
                )
                if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], BeautifulSoup):
                    soup, final_url = result
                    final = urlparse(_clean(final_url or url))
                    if (
                        final.scheme.lower() != "https"
                        or (final.hostname or "").rstrip(".").lower() != expected_host
                        or final.path != expected_path
                    ):
                        raise ValueError("source response URL changed")
                    return soup, _clean(final_url or url)
                if isinstance(result, BeautifulSoup):
                    return result, url
                return _response_soup(
                    result,
                    expected_host=expected_host,
                    expected_path=expected_path,
                )
            if method == "POST":
                response = current.post(url, data=dict(data or {}), timeout=timeout)
            else:
                response = current.get(url, timeout=timeout)
            return _response_soup(
                response,
                expected_host=expected_host,
                expected_path=expected_path,
            )
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


def _pairs(container: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for definition in container.select("dl"):
        headings = definition.find_all("dt", recursive=False)
        values = definition.find_all("dd", recursive=False)
        if len(headings) != len(values):
            continue
        for heading, value in zip(headings, values):
            key = _clean(heading.get_text(" ", strip=True)).rstrip(":")
            text = _clean(value.get_text(" ", strip=True))
            if key and key not in result:
                result[key] = text
    return result


def _reserve_url(page: int) -> str:
    query = {
        "curPage": str(page),
        "srchGugun": "2",
        "srchResveInsttCd": "33",
    }
    return f"https://{GEUMJEONG_RESERVE_HOST}{GEUMJEONG_RESERVE_PATH}?{urlencode(query)}"


def _reserve_detail_url(group_id: str, program_id: str) -> str:
    if not group_id.isdigit() or not program_id.isdigit():
        return ""
    return (
        f"https://{GEUMJEONG_RESERVE_HOST}{GEUMJEONG_RESERVE_DETAIL_PATH}?"
        + urlencode({"resveGroupSn": group_id, "progrmSn": program_id})
    )


def _reserve_last_page(soup: BeautifulSoup) -> tuple[int, list[str]]:
    values: set[int] = set()
    for link in soup.select(".paginate a.pgEnd"):
        query = parse_qs(urlparse(_clean(link.get("href"))).query)
        raw = _clean((query.get("curPage") or [""])[0])
        if raw.isdigit():
            values.add(int(raw))
    if len(values) != 1:
        return 0, ["missing or ambiguous reserve last-page control"]
    value = values.pop()
    if value < 1:
        return 0, ["reserve last page is less than one"]
    return value, []


def _reserve_form_errors(soup: BeautifulSoup, page: int) -> list[str]:
    forms = soup.select("form#srchForm")
    if len(forms) != 1:
        return ["expected one reserve srchForm"]
    form = forms[0]
    errors: list[str] = []
    if _clean(form.get("method")).lower() != "get" or urlparse(_clean(form.get("action"))).path != "/lctre":
        errors.append("unexpected reserve form method/action")
    expected = {
        "curPage": str(page),
        "srchGugun": "2",
        "srchResveInsttCd": "33",
    }
    page_field = form.select_one("input[name='curPage']")
    if page_field is None or _clean(page_field.get("value")) != expected["curPage"]:
        errors.append("reserve form curPage mismatch")
    for name in ("srchGugun", "srchResveInsttCd"):
        selected = form.select_one(f"select[name='{name}'] option[selected]")
        if selected is None or _clean(selected.get("value")) != expected[name]:
            errors.append(f"reserve form {name} mismatch")
    return errors


def _split_list_dates(value: str) -> tuple[str, str, list[str]]:
    text = _clean(value)
    errors: list[str] = []
    match = re.fullmatch(r"\[신청\]\s*(.+?)\s*\[행사\]\s*(.+)", text)
    if not match:
        return "", "", ["reserve card date field is malformed"]
    apply_period = _clean(match.group(1))
    period = _clean(match.group(2))
    if len(_dates(apply_period)) != 2:
        errors.append("reserve card application period is malformed")
    if len(_dates(period)) != 2:
        errors.append("reserve card education period is malformed")
    return apply_period, period, errors


def _reserve_list_rows(
    soup: BeautifulSoup,
    *,
    page: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(soup.select("ul.reserveList > li"), 1):
        title_node = item.select_one(".tit")
        title = _clean(title_node.get("title") if title_node else "") or _clean(
            title_node.get_text(" ", strip=True) if title_node else ""
        )
        link = item.select_one("a.reserveItem")
        action = _clean(link.get("onclick") if link else "")
        action_match = _RESERVE_ACTION_RE.search(action)
        pairs = _pairs(item)
        status_node = item.select_one(".statusMark")
        source_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
        row_errors: list[str] = []
        if not title:
            row_errors.append("empty reserve title")
        if not action_match:
            row_errors.append("missing reserve source identity")
            group_id = program_id = ""
        else:
            group_id, program_id = action_match.groups()
        if source_status not in _RESERVE_STATUS_MAP:
            row_errors.append(f"unknown reserve status {source_status!r}")
        branch = _clean(pairs.get("기관"))
        if not branch.startswith("금정구 ") or "주민자치회" not in branch:
            row_errors.append("reserve course is outside the Geumjeong resident-centre scope")
        target = _clean(pairs.get("대상"))
        venue = _clean(pairs.get("장소"))
        method = _clean(pairs.get("방법"))
        if not target or not method:
            row_errors.append("reserve card lacks target or application method")
        apply_period, period, period_errors = _split_list_dates(_clean(pairs.get("일자")))
        row_errors.extend(period_errors)
        if row_errors:
            errors.extend(
                f"reserve page {page} row {index}: {message}" for message in row_errors
            )
            continue
        period_dates = _dates(period)
        apply_dates = _dates(apply_period)
        raw_url = _reserve_detail_url(group_id, program_id)
        rows.append(
            {
                "provider": GEUMJEONG_PROVIDER,
                "provider_course_id": (
                    f"{GEUMJEONG_PROVIDER}:reserve:{group_id}:{program_id}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": branch,
                "branch_code": f"reserve-{group_id}",
                "municipality_code": GEUMJEONG_MUNICIPALITY_CODE,
                "municipality_name": GEUMJEONG_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "금정구",
                "provider_organizer": branch,
                "venue_name": venue,
                "category": "주민자치 교육",
                "program_type": "강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": _RESERVE_STATUS_MAP[source_status],
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
                "source_group": "municipal_reservation",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_html_pages+detail",
                "raw_fields": {
                    "parser": GEUMJEONG_PARSER,
                    "source_catalog": "busan_reserve_geumjeong_resident_centres",
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_status": source_status,
                    "source_application_method": method,
                },
            }
        )
    return rows, errors


def _reserve_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _detail_title(soup: BeautifulSoup) -> str:
    heading = soup.select_one("h3.titPage")
    if heading is None:
        return ""
    clone = BeautifulSoup(str(heading), "lxml")
    for status in clone.select(".statusMark"):
        status.extract()
    return _clean(clone.get_text(" ", strip=True))


def _reserve_detail_row(
    parent: dict[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any], list[str]]:
    raw = dict(parent.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    identity = f"{group_id}:{program_id}"
    errors: list[str] = []
    forms = soup.select("form#viewForm")
    if len(forms) != 1:
        errors.append(f"reserve {identity}: expected one viewForm")
    else:
        form = forms[0]
        group_field = form.select_one("input[name='resveGroupSn']")
        program_field = form.select_one("input[name='progrmSn']")
        if group_field is None or _clean(group_field.get("value")) != group_id:
            errors.append(f"reserve {identity}: detail group identity mismatch")
        if program_field is None or _clean(program_field.get("value")) != program_id:
            errors.append(f"reserve {identity}: detail program identity mismatch")
    if _detail_title(soup) != _clean(parent.get("title")):
        errors.append(f"reserve {identity}: detail/list title mismatch")
    status_node = soup.select_one("h3.titPage .statusMark")
    source_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    if source_status != _clean(raw.get("source_status")):
        errors.append(f"reserve {identity}: detail/list status mismatch")

    pairs = _pairs(soup)
    period = _clean(pairs.get("운영기간"))
    apply_period = _clean(pairs.get("신청기간"))
    method = _clean(pairs.get("신청방법"))
    fee = _clean(pairs.get("수강료"))
    schedule = _clean(pairs.get("요일 /시간") or pairs.get("요일/시간"))
    branch = _clean(pairs.get("운영기관"))
    target = _clean(pairs.get("대상"))
    if _dates(period) != [
        date.fromisoformat(_clean(parent.get("start_date"))),
        date.fromisoformat(_clean(parent.get("end_date"))),
    ]:
        errors.append(f"reserve {identity}: detail/list education period mismatch")
    if _dates(apply_period) != [
        date.fromisoformat(_clean(parent.get("apply_start"))),
        date.fromisoformat(_clean(parent.get("apply_end"))),
    ]:
        errors.append(f"reserve {identity}: detail/list application period mismatch")
    if method != _clean(raw.get("source_application_method")):
        errors.append(f"reserve {identity}: detail/list application method mismatch")
    if branch != _clean(parent.get("branch")):
        errors.append(f"reserve {identity}: detail/list organizer mismatch")
    if target != _clean(parent.get("target")):
        errors.append(f"reserve {identity}: detail/list target mismatch")
    if not fee or not schedule:
        errors.append(f"reserve {identity}: detail lacks fee or schedule")

    labels = [
        _clean(node.get_text(" ", strip=True) or node.get("value") or node.get("title"))
        for node in soup.select("a, button, input[type='submit'], input[type='button']")
    ]
    labels = [label for label in labels if label]
    control_label = next(
        (
            label
            for label in labels
            if (
                label in {
                    "접수마감",
                    "접수대기",
                    "대기중",
                    "방문예약",
                    "전화예약",
                    "방문접수",
                    "전화접수",
                    "온라인예약",
                    "예약하기",
                    "신청하기",
                }
                or (
                    len(label) <= 12
                    and label.endswith(("예약", "신청"))
                    and "현황" not in label
                )
            )
        ),
        "",
    )
    normalized_status = _RESERVE_STATUS_MAP.get(source_status, "")
    online = "온라인" in method
    offline = any(token in method for token in ("방문", "전화"))
    application_url = ""
    application_type = "INFO_ONLY"
    reservation_available = False
    if normalized_status == "OPEN":
        if online:
            if not control_label or any(token in control_label for token in ("마감", "대기")):
                errors.append(f"reserve {identity}: online course lacks an active application control")
            else:
                application_url = _clean(parent.get("raw_url"))
                application_type = "ONLINE_RESERVATION"
                reservation_available = True
        elif offline:
            if not control_label or not any(token in control_label for token in ("방문", "전화", "예약")):
                errors.append(f"reserve {identity}: offline course lacks an explicit application control")
            application_type = "OFFLINE_APPLY"
        else:
            errors.append(f"reserve {identity}: open course has an unknown application method")
    elif normalized_status == "CLOSED":
        if not control_label or "마감" not in control_label:
            errors.append(f"reserve {identity}: closed course lacks a closed control")
    elif normalized_status == "SCHEDULED":
        if not control_label:
            errors.append(f"reserve {identity}: scheduled course lacks a status control")
    else:
        errors.append(f"reserve {identity}: unknown normalized status")

    row = dict(parent)
    row.update(
        {
            "application_url": application_url,
            "application_type": application_type,
            "reservation_available": reservation_available,
            "status": normalized_status,
            "period": period,
            "apply_period": apply_period,
            "schedule_raw": schedule,
            "fee": fee,
            "target": target,
            "description": _clean(
                " ".join(
                    value
                    for value in (
                        _clean(parent.get("title")),
                        period,
                        schedule,
                        target,
                        fee,
                        method,
                    )
                    if value
                )
            ),
            "raw_fields": {
                **raw,
                "detail_verified": not errors,
                "detail_application_control_label": control_label,
                "detail_application_method": method,
            },
        }
    )
    return row, errors


def _lifelong_directory_errors(soup: BeautifulSoup) -> list[str]:
    matches = [
        _clean(option.get_text(" ", strip=True))
        for option in soup.select(
            f"#o_search_ch option[value='{GEUMJEONG_LIFELONG_OFFICE_CODE}']"
        )
    ]
    if matches != [GEUMJEONG_LIFELONG_OFFICE_NAME]:
        return ["Geumjeong lifelong office directory entry is missing, duplicated, or renamed"]
    return []


def _lifelong_payload(page: int) -> dict[str, str]:
    return {
        "display_type": "2",
        "pageUnit": str(GEUMJEONG_LIFELONG_PAGE_SIZE),
        "l_search_ch": "0",
        "inst_id": GEUMJEONG_LIFELONG_OFFICE_CODE,
        "pageIndex": str(page),
    }


def _lifelong_office() -> busan_lifelong.BusanOffice:
    registry = busan_lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(
        GEUMJEONG_LIFELONG_OFFICE_CODE
    )
    if (
        registry is None
        or registry.name != GEUMJEONG_LIFELONG_OFFICE_NAME
        or registry.ownership != GEUMJEONG_LIFELONG_EXPECTED_OWNERSHIP
        or registry.municipality_code
        or registry.municipality_name
    ):
        raise ValueError("shared lifelong registry has not transferred Geumjeong ownership")
    return busan_lifelong.BusanOffice(
        GEUMJEONG_LIFELONG_OFFICE_CODE,
        GEUMJEONG_LIFELONG_OFFICE_NAME,
        GEUMJEONG_MUNICIPALITY_CODE,
        GEUMJEONG_MUNICIPALITY_NAME,
        GEUMJEONG_LIFELONG_EXPECTED_OWNERSHIP,
    )


def _lifelong_row(row: dict[str, Any]) -> dict[str, Any]:
    safe, _redactions = busan_lifelong._sanitize_row(row)
    identity = _clean(safe.get("raw_fields", {}).get("identity"))
    safe["provider"] = GEUMJEONG_PROVIDER
    safe["provider_course_id"] = f"{GEUMJEONG_PROVIDER}:lifelong:{identity}"
    safe["prefer_incoming_provider_course_id"] = True
    safe["raw_fields"] = {
        **safe.get("raw_fields", {}),
        "parser": GEUMJEONG_PARSER,
        "source_catalog": "busan_lifelong_geumjeong_office",
    }
    return safe


def _chunks(values: list[Any], count: int) -> list[list[Any]]:
    if not values:
        return []
    size = max(1, math.ceil(len(values) / max(1, count)))
    return [values[index : index + size] for index in range(0, len(values), size)]


def _reserve_details(
    parents: list[dict[str, Any]],
    *,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    fetcher: Optional[Fetcher],
) -> tuple[list[dict[str, Any]], list[str], int]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    def worker(group: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        current = session_factory()
        found: list[dict[str, Any]] = []
        found_errors: list[str] = []
        try:
            for parent in group:
                try:
                    soup, _ = _request_soup(
                        current,
                        "GET",
                        _clean(parent.get("raw_url")),
                        timeout=timeout,
                        expected_host=GEUMJEONG_RESERVE_HOST,
                        expected_path=GEUMJEONG_RESERVE_DETAIL_PATH,
                        fetcher=fetcher,
                    )
                    row, item_errors = _reserve_detail_row(parent, soup)
                    found_errors.extend(item_errors)
                    if not item_errors:
                        found.append(row)
                except Exception as exc:
                    found_errors.append(
                        f"{_clean(parent.get('provider_course_id'))}: detail fetch "
                        f"{type(exc).__name__}: {_clean(exc)}"
                    )
        finally:
            _close_quietly(current)
        return found, found_errors

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, group) for group in _chunks(parents, max_workers)]
        for future in as_completed(futures):
            found, found_errors = future.result()
            rows.extend(found)
            errors.extend(found_errors)
    return rows, errors, len(parents)


def _lifelong_details(
    parents: list[dict[str, Any]],
    *,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    fetcher: Optional[Fetcher],
) -> tuple[list[dict[str, Any]], list[str], int, int]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    office = _lifelong_office()

    def worker(
        group: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        current = session_factory()
        found: list[dict[str, Any]] = []
        found_errors: list[str] = []
        bootstrap = 0
        try:
            try:
                bootstrap_soup, _ = _request_soup(
                    current,
                    "POST",
                    GEUMJEONG_LIFELONG_LIST_URL,
                    timeout=timeout,
                    expected_host=GEUMJEONG_LIFELONG_HOST,
                    expected_path=busan_lifelong.BUSAN_LIFELONG_LIST_PATH,
                    data=_lifelong_payload(1),
                    fetcher=fetcher,
                )
                bootstrap_errors = busan_lifelong._form_errors(
                    bootstrap_soup, office, 1
                )
                if bootstrap_errors:
                    raise ValueError("; ".join(bootstrap_errors))
                bootstrap = 1
            except Exception as exc:
                return [], [f"lifelong detail bootstrap: {type(exc).__name__}: {_clean(exc)}"], 0
            for parent in group:
                raw = parent.get("raw_fields", {})
                identity = _clean(raw.get("identity"))
                page = int(raw.get("list_page") or 1)
                try:
                    soup, _ = _request_soup(
                        current,
                        "POST",
                        _clean(parent.get("raw_url")),
                        timeout=timeout,
                        expected_host=GEUMJEONG_LIFELONG_HOST,
                        expected_path=busan_lifelong.BUSAN_LIFELONG_DETAIL_PATH,
                        data=_lifelong_payload(page),
                        fetcher=fetcher,
                    )
                    item_errors = busan_lifelong._validate_internal_detail(
                        parent, soup
                    )
                    found_errors.extend(item_errors)
                    if not item_errors:
                        found.append(_lifelong_row(parent))
                except Exception as exc:
                    found_errors.append(
                        f"{identity}: detail fetch {type(exc).__name__}: {_clean(exc)}"
                    )
        finally:
            _close_quietly(current)
        return found, found_errors, bootstrap

    bootstraps = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, group) for group in _chunks(parents, max_workers)]
        for future in as_completed(futures):
            found, found_errors, bootstrap = future.result()
            rows.extend(found)
            errors.extend(found_errors)
            bootstraps += bootstrap
    return rows, errors, len(parents), bootstraps


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_count": 2,
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
        "municipality_code": GEUMJEONG_MUNICIPALITY_CODE,
        "municipality_name": GEUMJEONG_MUNICIPALITY_NAME,
        "ownership_scope": GEUMJEONG_OWNERSHIP_SCOPE,
        "candidate_ids": dict(GEUMJEONG_CANDIDATE_IDS),
        "ownership_aliases": [
            {
                "provider": alias.provider,
                "url": alias.url,
                "relationship": alias.relationship,
            }
            for alias in GEUMJEONG_OWNERSHIP_ALIASES
        ],
    }


def collect_geumjeong_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 60,
    detail_limit: int = 200,
    *,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GEUMJEONG_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return the complete current/future Geumjeong-gu education snapshot."""

    meta = _base_meta()
    if not is_geumjeong_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the canonical Geumjeong education route"
        )
        return [], GEUMJEONG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = (
                "managed session_factory injection is required"
            )
            return [], GEUMJEONG_PARSER, meta
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        workers = min(max(1, int(max_workers)), GEUMJEONG_MAX_WORKERS)
        request_timeout = max(1, int(timeout))
        cutoff = _today(today)
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], GEUMJEONG_PARSER, meta

    errors: list[str] = []
    source_cap_reached = False
    reserve_first: Optional[BeautifulSoup] = None
    lifelong_first: Optional[BeautifulSoup] = None
    directory: Optional[BeautifulSoup] = None
    initial = session_factory()
    try:
        try:
            reserve_first, _ = _request_soup(
                initial,
                "GET",
                _reserve_url(1),
                timeout=request_timeout,
                expected_host=GEUMJEONG_RESERVE_HOST,
                expected_path=GEUMJEONG_RESERVE_PATH,
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
        except Exception as exc:
            errors.append(f"reserve first page: {type(exc).__name__}: {_clean(exc)}")
        try:
            directory, _ = _request_soup(
                initial,
                "GET",
                GEUMJEONG_LIFELONG_URL,
                timeout=request_timeout,
                expected_host=GEUMJEONG_LIFELONG_HOST,
                expected_path=busan_lifelong.BUSAN_LIFELONG_OFFICE_PATH,
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
        except Exception as exc:
            errors.append(f"lifelong office directory: {type(exc).__name__}: {_clean(exc)}")
        try:
            lifelong_first, _ = _request_soup(
                initial,
                "POST",
                GEUMJEONG_LIFELONG_LIST_URL,
                timeout=request_timeout,
                expected_host=GEUMJEONG_LIFELONG_HOST,
                expected_path=busan_lifelong.BUSAN_LIFELONG_LIST_PATH,
                data=_lifelong_payload(1),
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
        except Exception as exc:
            errors.append(f"lifelong first page: {type(exc).__name__}: {_clean(exc)}")
    finally:
        _close_quietly(initial)

    reserve_first_rows: list[dict[str, Any]] = []
    reserve_last = 0
    if reserve_first is not None:
        errors.extend(_reserve_form_errors(reserve_first, 1))
        reserve_first_rows, item_errors = _reserve_list_rows(reserve_first, page=1)
        errors.extend(item_errors)
        reserve_last, item_errors = _reserve_last_page(reserve_first)
        errors.extend(item_errors)
        if not reserve_first_rows:
            errors.append("reserve first page contains no course rows")

    office = _lifelong_office()
    lifelong_first_rows: list[dict[str, Any]] = []
    lifelong_total = 0
    lifelong_last = 0
    if directory is not None:
        errors.extend(_lifelong_directory_errors(directory))
    if lifelong_first is not None:
        errors.extend(busan_lifelong._form_errors(lifelong_first, office, 1))
        lifelong_first_rows, item_errors = busan_lifelong._parse_list_page(
            lifelong_first, office=office, page=1
        )
        errors.extend(item_errors)
        advertised, item_errors = busan_lifelong._advertised_last(lifelong_first)
        errors.extend(item_errors)
        lifelong_total = (
            int(lifelong_first_rows[0]["raw_fields"]["list_sequence"])
            if lifelong_first_rows
            else 0
        )
        lifelong_last = max(1, math.ceil(lifelong_total / GEUMJEONG_LIFELONG_PAGE_SIZE))
        if advertised != lifelong_last:
            errors.append("lifelong displayed last page differs from the sequence total")
        expected = min(GEUMJEONG_LIFELONG_PAGE_SIZE, lifelong_total)
        if len(lifelong_first_rows) != expected:
            errors.append("lifelong first page row count mismatch")

    required_list_requests = (
        reserve_last
        + 1
        + 1
        + int(reserve_last > 1)
        + lifelong_last
        + 1
        + 1
        + int(lifelong_last > 1)
        + 1
        if reserve_last and lifelong_last
        else 0
    )
    meta["required_list_requests"] = required_list_requests
    if required_list_requests > allowed_pages:
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of {required_list_requests} "
            "required directory/catalogue/sentinel/recheck requests"
        )
    if errors:
        meta["source_cap_reached"] = source_cap_reached
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], GEUMJEONG_PARSER, meta

    reserve_pages: dict[int, list[dict[str, Any]]] = {1: reserve_first_rows}
    lifelong_pages: dict[int, list[dict[str, Any]]] = {1: lifelong_first_rows}
    sentinels: dict[str, int] = {}
    recheck_signatures: dict[str, bool] = {}
    crawl_session = session_factory()
    try:
        for page in range(2, reserve_last + 1):
            soup, _ = _request_soup(
                crawl_session,
                "GET",
                _reserve_url(page),
                timeout=request_timeout,
                expected_host=GEUMJEONG_RESERVE_HOST,
                expected_path=GEUMJEONG_RESERVE_PATH,
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
            errors.extend(_reserve_form_errors(soup, page))
            advertised, item_errors = _reserve_last_page(soup)
            errors.extend(item_errors)
            if advertised != reserve_last:
                errors.append(f"reserve page {page}: displayed last page changed")
            parsed, item_errors = _reserve_list_rows(soup, page=page)
            errors.extend(item_errors)
            reserve_pages[page] = parsed
        reserve_sentinel, _ = _request_soup(
            crawl_session,
            "GET",
            _reserve_url(reserve_last + 1),
            timeout=request_timeout,
            expected_host=GEUMJEONG_RESERVE_HOST,
            expected_path=GEUMJEONG_RESERVE_PATH,
            fetcher=fetcher,
        )
        meta["pages"] += 1
        meta["list_requests"] += 1
        errors.extend(_reserve_form_errors(reserve_sentinel, reserve_last + 1))
        parsed_sentinel, item_errors = _reserve_list_rows(
            reserve_sentinel, page=reserve_last + 1
        )
        errors.extend(item_errors)
        sentinel_last, item_errors = _reserve_last_page(reserve_sentinel)
        errors.extend(item_errors)
        if sentinel_last != reserve_last or parsed_sentinel:
            errors.append("reserve immediate post-last sentinel is not empty/stable")
        sentinels["busan_reserve"] = len(parsed_sentinel)

        for page in range(2, lifelong_last + 1):
            soup, _ = _request_soup(
                crawl_session,
                "POST",
                GEUMJEONG_LIFELONG_LIST_URL,
                timeout=request_timeout,
                expected_host=GEUMJEONG_LIFELONG_HOST,
                expected_path=busan_lifelong.BUSAN_LIFELONG_LIST_PATH,
                data=_lifelong_payload(page),
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
            errors.extend(busan_lifelong._form_errors(soup, office, page))
            advertised, item_errors = busan_lifelong._advertised_last(soup)
            errors.extend(item_errors)
            if advertised != lifelong_last:
                errors.append(f"lifelong page {page}: displayed last page changed")
            parsed, item_errors = busan_lifelong._parse_list_page(
                soup, office=office, page=page
            )
            errors.extend(item_errors)
            lifelong_pages[page] = parsed
        lifelong_sentinel, _ = _request_soup(
            crawl_session,
            "POST",
            GEUMJEONG_LIFELONG_LIST_URL,
            timeout=request_timeout,
            expected_host=GEUMJEONG_LIFELONG_HOST,
            expected_path=busan_lifelong.BUSAN_LIFELONG_LIST_PATH,
            data=_lifelong_payload(lifelong_last + 1),
            fetcher=fetcher,
        )
        meta["pages"] += 1
        meta["list_requests"] += 1
        parsed_sentinel, item_errors = busan_lifelong._parse_list_page(
            lifelong_sentinel, office=office, page=lifelong_last + 1
        )
        errors.extend(item_errors)
        advertised, item_errors = busan_lifelong._advertised_last(lifelong_sentinel)
        errors.extend(item_errors)
        if advertised != lifelong_last or parsed_sentinel:
            errors.append("lifelong immediate post-last sentinel is not empty/stable")
        sentinels["busan_lifelong_geumjeong"] = len(parsed_sentinel)

        for source, pages, last in (
            ("reserve", reserve_pages, reserve_last),
            ("lifelong", lifelong_pages, lifelong_last),
        ):
            for page in dict.fromkeys((1, last)):
                if source == "reserve":
                    soup, _ = _request_soup(
                        crawl_session,
                        "GET",
                        _reserve_url(page),
                        timeout=request_timeout,
                        expected_host=GEUMJEONG_RESERVE_HOST,
                        expected_path=GEUMJEONG_RESERVE_PATH,
                        fetcher=fetcher,
                    )
                    parsed, item_errors = _reserve_list_rows(soup, page=page)
                    errors.extend(_reserve_form_errors(soup, page))
                    errors.extend(item_errors)
                    signature = _reserve_signature(parsed)
                    expected_signature = _reserve_signature(pages[page])
                else:
                    soup, _ = _request_soup(
                        crawl_session,
                        "POST",
                        GEUMJEONG_LIFELONG_LIST_URL,
                        timeout=request_timeout,
                        expected_host=GEUMJEONG_LIFELONG_HOST,
                        expected_path=busan_lifelong.BUSAN_LIFELONG_LIST_PATH,
                        data=_lifelong_payload(page),
                        fetcher=fetcher,
                    )
                    parsed, item_errors = busan_lifelong._parse_list_page(
                        soup, office=office, page=page
                    )
                    errors.extend(busan_lifelong._form_errors(soup, office, page))
                    errors.extend(item_errors)
                    signature = busan_lifelong._page_signature(parsed)
                    expected_signature = busan_lifelong._page_signature(pages[page])
                meta["pages"] += 1
                meta["list_requests"] += 1
                stable = signature == expected_signature
                recheck_signatures[f"{source}:{page}"] = stable
                if not stable:
                    errors.append(f"{source} page {page}: stable boundary recheck changed")
    except Exception as exc:
        errors.append(f"catalogue traversal: {type(exc).__name__}: {_clean(exc)}")
    finally:
        _close_quietly(crawl_session)

    reserve_rows = [row for page in range(1, reserve_last + 1) for row in reserve_pages.get(page, [])]
    lifelong_rows = [row for page in range(1, lifelong_last + 1) for row in lifelong_pages.get(page, [])]
    for page in range(1, reserve_last):
        if len(reserve_pages.get(page, [])) != GEUMJEONG_RESERVE_PAGE_SIZE:
            errors.append(f"reserve page {page}: expected a full page")
    if not 1 <= len(reserve_pages.get(reserve_last, [])) <= GEUMJEONG_RESERVE_PAGE_SIZE:
        errors.append("reserve last page cardinality is invalid")
    expected_sequences = list(range(lifelong_total, 0, -1))
    actual_sequences = [int(row["raw_fields"]["list_sequence"]) for row in lifelong_rows]
    if actual_sequences != expected_sequences:
        errors.append("lifelong archive source sequence is not continuous")
    if len(lifelong_rows) != lifelong_total:
        errors.append(
            f"lifelong declared total {lifelong_total} != parsed {len(lifelong_rows)}"
        )
    all_source_ids = [
        _clean(row.get("provider_course_id")) for row in reserve_rows + lifelong_rows
    ]
    duplicate_source_id_count = len(all_source_ids) - len(set(all_source_ids))
    if duplicate_source_id_count:
        errors.append(f"{duplicate_source_id_count} duplicate source identities")

    reserve_current: list[dict[str, Any]] = []
    lifelong_current: list[dict[str, Any]] = []
    expired_by_source: dict[str, int] = {"busan_reserve": 0, "busan_lifelong_geumjeong": 0}
    for source_name, source_rows, destination in (
        ("busan_reserve", reserve_rows, reserve_current),
        ("busan_lifelong_geumjeong", lifelong_rows, lifelong_current),
    ):
        for row in source_rows:
            try:
                ended = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
                continue
            if ended < cutoff:
                expired_by_source[source_name] += 1
            else:
                destination.append(row)

    required_details = len(reserve_current) + len(lifelong_current)
    if required_details > allowed_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} "
            "required current/future details"
        )

    reserve_detailed: list[dict[str, Any]] = []
    lifelong_detailed: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    reserve_attempts = lifelong_attempts = lifelong_bootstraps = 0
    if not errors:
        reserve_detailed, found_errors, reserve_attempts = _reserve_details(
            reserve_current,
            session_factory=session_factory,
            timeout=request_timeout,
            max_workers=workers,
            fetcher=fetcher,
        )
        detail_errors.extend(found_errors)
        lifelong_detailed, found_errors, lifelong_attempts, lifelong_bootstraps = _lifelong_details(
            lifelong_current,
            session_factory=session_factory,
            timeout=request_timeout,
            max_workers=workers,
            fetcher=fetcher,
        )
        detail_errors.extend(found_errors)
    errors.extend(detail_errors)

    details_complete = bool(
        not detail_errors
        and reserve_attempts == len(reserve_current)
        and lifelong_attempts == len(lifelong_current)
        and len(reserve_detailed) == len(reserve_current)
        and len(lifelong_detailed) == len(lifelong_current)
    )
    result: list[dict[str, Any]] = []
    if not errors and details_complete:
        combined = reserve_detailed + lifelong_detailed
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(combined))
        if len(result) != len(combined):
            errors.append(
                f"dedupe changed complete row count {len(combined)} to {len(result)}"
            )
            result = []
    result.sort(
        key=lambda row: (
            _clean(row.get("start_date")),
            _clean(row.get("title")),
            _clean(row.get("provider_course_id")),
        )
    )

    privacy_violations = 0
    for row in result:
        serialized = repr(row)
        privacy_violations += len(_PHONE_RE.findall(serialized))
        privacy_violations += len(_EMAIL_RE.findall(serialized))
        privacy_violations += sum(
            key in row for key in ("phone", "email", "instructor", "teacher")
        )
    if privacy_violations:
        errors.append(f"{privacy_violations} PII allowlist violations")
        result = []

    pagination_complete = bool(
        not errors
        and len(sentinels) == 2
        and not any(sentinels.values())
        and all(recheck_signatures.values())
        and len(recheck_signatures)
        == (1 + int(reserve_last > 1) + 1 + int(lifelong_last > 1))
    )
    snapshot_complete = bool(pagination_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    source_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_catalog")) for row in result
    )
    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    status_counts = Counter(_clean(row.get("status")) for row in result)
    meta.update(
        {
            "source_totals": {
                "busan_reserve": len(reserve_rows),
                "busan_lifelong_geumjeong": len(lifelong_rows),
            },
            "source_current_counts": {
                "busan_reserve": len(reserve_current),
                "busan_lifelong_geumjeong": len(lifelong_current),
            },
            "source_returned_counts": dict(source_counts),
            "expired_counts": expired_by_source,
            "reserve_data_pages": reserve_last,
            "lifelong_data_pages": lifelong_last,
            "sentinel_counts": sentinels,
            "stable_rechecks": recheck_signatures,
            "stable_recheck_count": len(recheck_signatures),
            "source_total": len(reserve_rows) + len(lifelong_rows),
            "source_rows": len(reserve_rows) + len(lifelong_rows),
            "current_count": required_details,
            "returned_count": len(result),
            "detail_attempts": reserve_attempts + lifelong_attempts,
            "detail_pages": len(reserve_detailed) + len(lifelong_detailed),
            "detail_errors": len(detail_errors),
            "lifelong_detail_bootstrap_pages": lifelong_bootstraps,
            "duplicate_source_id_count": duplicate_source_id_count,
            "privacy_violations": privacy_violations,
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "pagination_detected": reserve_last > 1 or lifelong_last > 1,
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "both complete Geumjeong education sources contain only ended courses"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, GEUMJEONG_PARSER, meta


collect = collect_geumjeong_education_courses


__all__ = [
    "GEUMJEONG_CANDIDATE_IDS",
    "GEUMJEONG_LIFELONG_OFFICE_CODE",
    "GEUMJEONG_LIFELONG_URL",
    "GEUMJEONG_MUNICIPALITY_CODE",
    "GEUMJEONG_MUNICIPALITY_NAME",
    "GEUMJEONG_OWNERSHIP_ALIASES",
    "GEUMJEONG_OWNERSHIP_SCOPE",
    "GEUMJEONG_PARSER",
    "GEUMJEONG_PROVIDER",
    "GEUMJEONG_RESERVE_URL",
    "GeumjeongAlias",
    "collect",
    "collect_geumjeong_education_courses",
    "is_geumjeong_education_target",
    "is_geumjeong_owned_alias_target",
    "is_target",
]
