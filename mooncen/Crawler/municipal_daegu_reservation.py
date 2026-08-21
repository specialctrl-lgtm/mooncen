"""Fail-closed collector for Daegu's integrated education catalogue.

The existing ``DAEGU_RESERVATION`` collector owns the separate
``/expr/list`` (experience/visit) catalogue.  The official ``/lect/list``
route is a different education catalogue and must not be routed through that
experience API.

The education API is public, but it requires the same NetFUNNEL admission
step used by the official web application.  This collector obtains a fresh
official admission key for every API request, stores it only in the request
session cookie jar, and never persists or reports the key.

A snapshot is returned only after all three official status partitions,
their declared pages, empty sentinel pages, stable first-page rechecks, and
every current/future detail response have passed their contracts.  Any
partial or drifting snapshot returns no rows.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import html
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


DAEGU_EDUCATION_PROVIDER = "DAEGU_RESERVATION"
DAEGU_EXPERIENCE_PROVIDER = DAEGU_EDUCATION_PROVIDER
DAEGU_DISCOVERY_ALIAS_PROVIDER = "MUNI_YEYAK_DAEGU_GO_KR_2F6A050A"
DAEGU_HOST = "yeyak.daegu.go.kr"
DAEGU_WAIT_HOST = "yeyakwait.daegu.go.kr"
DAEGU_EDUCATION_URL = "https://yeyak.daegu.go.kr/lect/list"
DAEGU_EXPERIENCE_URL = "https://yeyak.daegu.go.kr/expr/list"
DAEGU_LIST_API = (
    "https://yeyak.daegu.go.kr/api/v1/res/lect/user/user-lect-rsvt-list"
)
DAEGU_DETAIL_API = (
    "https://yeyak.daegu.go.kr/api/v1/res/lect/user/user-lect-rsvt-detail"
)
DAEGU_NETFUNNEL_URL = (
    "https://yeyakwait.daegu.go.kr/ts.wseq?"
    + urlencode(
        {
            "opcode": "5101",
            "sid": "service_1",
            "aid": "segKey_1375",
        }
    )
)
DAEGU_NETFUNNEL_PROJECT = "service_1"
DAEGU_NETFUNNEL_SEGMENT = "segKey_1375"
DAEGU_PAGE_SIZE = 50
DAEGU_FETCH_ATTEMPTS = 2
DAEGU_MAX_WORKERS = 6
DAEGU_EDUCATION_PARSER = (
    "daegu_lect_api_status_partitions+sentinels+stable_recheck+current_details"
)
DAEGU_OWNERSHIP_SCOPE = "daegu_integrated_lect_all_institutions_current_future"

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_INST_ID_RE = re.compile(r"DSS_INST_\d{8}")
_GDS_ID_RE = re.compile(r"GDS_\d{8}")
_LSN_ID_RE = re.compile(r"CRS_\d{8}")
_KEY_RE = re.compile(r"[A-Za-z0-9_-]{32,512}")
_PORT_RE = re.compile(r"\d{2,5}")
_AMOUNT_RE = re.compile(r"\d+")

_PARTITIONS: Mapping[str, str] = {
    "1": "open",
    "2": "scheduled",
    "3": "closed",
}
_PARTITION_STATUSES: Mapping[str, frozenset[str]] = {
    "1": frozenset({"PER_ING", "ING", "ADD_ING"}),
    "2": frozenset({"READY"}),
    "3": frozenset({"END"}),
}
_NORMALIZED_STATUS: Mapping[str, str] = {
    "PER_ING": "OPEN",
    "ING": "OPEN",
    "ADD_ING": "OPEN",
    "READY": "SCHEDULED",
    "END": "CLOSED",
}
_YES_NO_FIELDS = (
    "chrgYn",
    "grndsRcptYn",
    "rtrcnLsnYn",
    "ddlnYn",
    "telRcptYn",
    "onlnYn",
)
_INTEGER_FIELDS = (
    "rcrtCnt",
    "rsvtAplyCnt",
    "rsvtWaitAplyCnt",
    "rsvtLotteryAplyCnt",
)
_WEEKDAYS = {
    "0": "월",
    "1": "화",
    "2": "수",
    "3": "목",
    "4": "금",
    "5": "토",
    "6": "일",
}

# Administrative-district evidence is taken from the official detail address.
# The fallback to description text is needed for DSS_INST_00000175, whose API
# address is null but whose official course description gives a Seo-gu venue.
_MUNICIPALITIES: tuple[tuple[str, str, str], ...] = (
    ("달성군", "2771000000", "대구광역시 달성군"),
    ("군위군", "2772000000", "대구광역시 군위군"),
    ("달서구", "2729000000", "대구광역시 달서구"),
    ("수성구", "2726000000", "대구광역시 수성구"),
    ("중구", "2711000000", "대구광역시 중구"),
    ("동구", "2714000000", "대구광역시 동구"),
    ("서구", "2717000000", "대구광역시 서구"),
    ("남구", "2720000000", "대구광역시 남구"),
    ("북구", "2723000000", "대구광역시 북구"),
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


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


def is_daegu_integrated_education_target(target: Any) -> bool:
    """Match only the canonical unfiltered education list owner."""

    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == DAEGU_EDUCATION_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == DAEGU_HOST
        and parsed.port is None
        and parsed.path == "/lect/list"
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_daegu_integrated_education_target


def daegu_education_detail_url(inst_id: Any, gds_id: Any, lsn_id: Any) -> str:
    inst = _clean(inst_id)
    goods = _clean(gds_id)
    lesson = _clean(lsn_id)
    if not (
        _INST_ID_RE.fullmatch(inst)
        and _GDS_ID_RE.fullmatch(goods)
        and _LSN_ID_RE.fullmatch(lesson)
    ):
        return ""
    return f"https://{DAEGU_HOST}/lect/detail/{inst}/{goods}/{lesson}"


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
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


def _response_status(response: Any) -> int:
    try:
        return int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        return 0


def _strict_response(response: Any, label: str) -> None:
    status = _response_status(response)
    if status != 200:
        raise ValueError(f"{label}: unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError(f"{label}: redirects are not accepted")


def _response_text(response: Any) -> str:
    value = getattr(response, "text", None)
    if value is not None:
        return str(value)
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="strict")
    return str(content or "")


def _set_cookie(session: Any, name: str, value: str) -> None:
    cookies = getattr(session, "cookies", None)
    setter = getattr(cookies, "set", None)
    if not callable(setter):
        raise ValueError("session cookie jar is unavailable")
    setter(name, value, domain=DAEGU_HOST, path="/")


def _acquire_netfunnel_key(session: Any, timeout: int) -> None:
    response = session.get(
        DAEGU_NETFUNNEL_URL,
        headers={"Referer": DAEGU_EDUCATION_URL, "Accept": "text/plain,*/*"},
        timeout=timeout,
        verify=True,
        allow_redirects=False,
    )
    _strict_response(response, "NetFUNNEL admission")
    body = _response_text(response).strip()
    if not body.startswith("300:"):
        raise ValueError("NetFUNNEL admission was not immediately passed")
    fields = dict(parse_qsl(body[4:], keep_blank_values=True))
    key = _clean(fields.get("key"))
    port = _clean(fields.get("port"))
    sticky = _clean(fields.get("sticky"))
    if not _KEY_RE.fullmatch(key):
        raise ValueError("NetFUNNEL returned an invalid admission key")
    if not _PORT_RE.fullmatch(port) or not (1 <= int(port) <= 65535):
        raise ValueError("NetFUNNEL returned an invalid port")
    if _clean(fields.get("nwait")) != "0" or _clean(fields.get("nnext")) != "0":
        raise ValueError("NetFUNNEL admission unexpectedly entered a queue")
    if not sticky or len(sticky) > 128:
        raise ValueError("NetFUNNEL returned an invalid sticky value")
    _set_cookie(
        session,
        f"_nfbasic:{DAEGU_NETFUNNEL_PROJECT}:{DAEGU_NETFUNNEL_SEGMENT}:{port}",
        key,
    )
    _set_cookie(
        session,
        f"_nfsticky:{DAEGU_NETFUNNEL_PROJECT}:{DAEGU_NETFUNNEL_SEGMENT}",
        sticky,
    )


def _post_json_once(
    url: str,
    payload: Mapping[str, Any],
    *,
    session_factory: SessionFactory,
    timeout: int,
    label: str,
    referer: str,
) -> Mapping[str, Any]:
    current = session_factory()
    try:
        _acquire_netfunnel_key(current, timeout)
        response = current.post(
            url,
            json=dict(payload),
            headers={
                "Referer": referer,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=timeout,
            verify=True,
            allow_redirects=False,
        )
        _strict_response(response, label)
        try:
            decoded = response.json()
        except Exception as exc:
            raise ValueError(f"{label}: invalid JSON response") from exc
        if not isinstance(decoded, Mapping):
            raise ValueError(f"{label}: JSON root is not an object")
        if decoded.get("result") is not True or _clean(decoded.get("code")) != "SUCCESS":
            raise ValueError(f"{label}: API result is not SUCCESS")
        data = decoded.get("data")
        if not isinstance(data, Mapping):
            raise ValueError(f"{label}: API data is not an object")
        return data
    finally:
        _close_quietly(current)


def _post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    session_factory: SessionFactory,
    timeout: int,
    attempts: int,
    label: str,
    referer: str,
) -> Mapping[str, Any]:
    errors: list[str] = []
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return _post_json_once(
                url,
                payload,
                session_factory=session_factory,
                timeout=timeout,
                label=label,
                referer=referer,
            )
        except Exception as exc:
            errors.append(f"attempt {attempt}: {_clean(exc)}")
    raise ValueError(f"{label}: " + " | ".join(errors))


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} is not an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not an integer") from exc
    if result < 0:
        raise ValueError(f"{label} is negative")
    return result


def _source_date(value: Any, label: str) -> date:
    raw = _clean(value)
    for pattern in ("%y.%m.%d", "%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"{label} has invalid date {raw!r}")


def _identity(item: Mapping[str, Any]) -> tuple[str, str, str]:
    inst = _clean(item.get("instId"))
    goods = _clean(item.get("gdsId"))
    lesson = _clean(item.get("lsnId"))
    if not _INST_ID_RE.fullmatch(inst):
        raise ValueError("invalid instId")
    if not _GDS_ID_RE.fullmatch(goods):
        raise ValueError("invalid gdsId")
    if not _LSN_ID_RE.fullmatch(lesson):
        raise ValueError("invalid lsnId")
    return inst, goods, lesson


def _list_payload(partition: str, page: int) -> dict[str, Any]:
    return {
        "instId": "",
        "searchText": "",
        "searchGbn2": partition,
        "pageIndex": page,
        "pageSize": DAEGU_PAGE_SIZE,
    }


def _list_page(
    partition: str,
    page: int,
    *,
    session_factory: SessionFactory,
    timeout: int,
    attempts: int,
) -> tuple[list[Mapping[str, Any]], int, int]:
    data = _post_json(
        DAEGU_LIST_API,
        _list_payload(partition, page),
        session_factory=session_factory,
        timeout=timeout,
        attempts=attempts,
        label=f"list partition {partition} page {page}",
        referer=DAEGU_EDUCATION_URL,
    )
    items = data.get("items")
    if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
        raise ValueError(f"list partition {partition} page {page}: invalid items")
    response_page = _integer(data.get("page"), "response page")
    response_size = _integer(data.get("pageSize"), "response pageSize")
    total = _integer(data.get("totalElements"), "response totalElements")
    pages = _integer(data.get("toalPages"), "response toalPages")
    expected_pages = math.ceil(total / DAEGU_PAGE_SIZE) if total else 0
    if response_page != page:
        raise ValueError(
            f"list partition {partition} page {page}: response page mismatch"
        )
    if response_size != DAEGU_PAGE_SIZE:
        raise ValueError(
            f"list partition {partition} page {page}: pageSize mismatch"
        )
    if pages != expected_pages:
        raise ValueError(
            f"list partition {partition} page {page}: declared pages mismatch"
        )
    return items, total, pages


def _validate_list_item(
    item: Mapping[str, Any], partition: str, page: int
) -> dict[str, Any]:
    inst, goods, lesson = _identity(item)
    title = _clean(item.get("gdsNm"))
    branch = _clean(item.get("instNm"))
    classification = _clean(item.get("gdsClsfCd"))
    classification_group = _clean(item.get("gdsClsfDcd"))
    if not title or not branch or not classification or not classification_group:
        raise ValueError(f"{goods}: empty title, institution, or classification")
    status = _clean(item.get("rcptStatus"))
    if status not in _PARTITION_STATUSES[partition]:
        raise ValueError(
            f"{goods}: status {status!r} does not belong to partition {partition}"
        )
    for field in _YES_NO_FIELDS:
        if _clean(item.get(field)) not in {"Y", "N"}:
            raise ValueError(f"{goods}: {field} is not Y/N")
    # The official API uses a single blank for older fixed-time courses.  Its
    # own UI deliberately treats every non-"Y" value as fixed-time mode.
    if _clean(item.get("dowEduTmMngYn")) not in {"", "Y", "N"}:
        raise ValueError(f"{goods}: invalid dowEduTmMngYn")
    for field in _INTEGER_FIELDS:
        _integer(item.get(field), f"{goods}.{field}")
    start = _source_date(item.get("eduBgngYmd"), f"{goods}.eduBgngYmd")
    end = _source_date(item.get("eduEndYmd"), f"{goods}.eduEndYmd")
    apply_start = _source_date(item.get("rcptBgngYmd"), f"{goods}.rcptBgngYmd")
    apply_end = _source_date(item.get("rcptEndYmd"), f"{goods}.rcptEndYmd")
    if end < start:
        raise ValueError(f"{goods}: reversed education period")
    if apply_end < apply_start:
        raise ValueError(f"{goods}: reversed application period")
    return {
        **dict(item),
        "_identity": (inst, goods, lesson),
        "_title": title,
        "_branch": branch,
        "_status": status,
        "_start": start,
        "_end": end,
        "_apply_start": apply_start,
        "_apply_end": apply_end,
        "_partition": partition,
        "_list_page": page,
    }


def _first_page_signature(
    items: Iterable[Mapping[str, Any]], total: int, pages: int
) -> tuple[int, int, tuple[tuple[str, str, str], ...]]:
    return (
        total,
        pages,
        tuple(_identity(item) for item in items),
    )


def _html_text(value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    return _clean(BeautifulSoup(raw, "lxml").get_text(" ", strip=True))


def _municipality(detail: Mapping[str, Any]) -> tuple[str, str, str, str]:
    address = _clean(detail.get("instAddr"))
    if address:
        for marker, code, full_name in _MUNICIPALITIES:
            if marker in address:
                return code, full_name, address, "instAddr"
    evidence = _clean(
        " ".join(
            part
            for part in (
                detail.get("eduPlc"),
                _html_text(detail.get("lsnIntro")),
            )
            if _clean(part)
        )
    )
    for marker, code, full_name in _MUNICIPALITIES:
        if marker in evidence:
            return code, full_name, address or evidence[:500], "course_venue"
    raise ValueError("official detail has no unambiguous Daegu municipality evidence")


def _fee(detail: Mapping[str, Any], listed: Mapping[str, Any]) -> tuple[str, int]:
    charged = _clean(listed.get("chrgYn"))
    source_name = _clean(detail.get("chrgYnNm"))
    if charged == "N":
        if source_name != "무료":
            raise ValueError("free/charged flags disagree")
        return "무료", 0
    if charged != "Y" or source_name != "유료":
        raise ValueError("free/charged flags disagree")
    digits = "".join(_AMOUNT_RE.findall(_clean(detail.get("ntslAmt"))))
    if not digits:
        raise ValueError("paid course has no numeric fee")
    amount = int(digits)
    return f"{amount:,}원", amount


def _schedule(detail: Mapping[str, Any]) -> str:
    if _clean(detail.get("dowEduTmMngYn")) == "Y":
        rendered: list[str] = []
        for chunk in _clean(detail.get("dayEduTm")).split(","):
            day, separator, time_text = chunk.partition("|")
            if not separator or _clean(day) not in _WEEKDAYS or not _clean(time_text):
                raise ValueError("invalid day-specific education schedule")
            rendered.append(f"{_WEEKDAYS[_clean(day)]} {_clean(time_text)}")
        if not rendered:
            raise ValueError("empty day-specific education schedule")
        return ", ".join(rendered)
    days = [
        _WEEKDAYS[value]
        for value in _clean(detail.get("utztnPsbltyDow")).split(",")
        if value in _WEEKDAYS
    ]
    start = _clean(detail.get("eduBgngTm"))
    end = _clean(detail.get("eduEndTm"))
    if not days or not start or not end:
        raise ValueError("invalid fixed education schedule")
    return f"{','.join(days)} {start} ~ {end}"


def _application_available(detail: Mapping[str, Any], listed: Mapping[str, Any]) -> bool:
    if _clean(detail.get("rcptStatus")) not in {"PER_ING", "ING", "ADD_ING"}:
        return False
    if _clean(detail.get("onlnYn")) != "Y":
        return False
    if _clean(listed.get("rtrcnLsnYn")) == "Y" or _clean(listed.get("ddlnYn")) == "Y":
        return False
    capacity = _integer(detail.get("rcrtCnt"), "detail.rcrtCnt")
    applied = _integer(detail.get("rsvtAplyCnt"), "detail.rsvtAplyCnt")
    if applied < capacity:
        return True
    if _clean(detail.get("waitprsUseYn")) != "Y":
        return False
    wait_capacity = _integer(detail.get("waitprsNope"), "detail.waitprsNope")
    wait_applied = _integer(
        detail.get("rsvtWaitAplyCnt"), "detail.rsvtWaitAplyCnt"
    )
    return wait_applied < wait_capacity


def _detail_row(listed: Mapping[str, Any], detail: Mapping[str, Any]) -> dict[str, Any]:
    inst, goods, lesson = listed["_identity"]
    if _identity(detail) != (inst, goods, lesson):
        raise ValueError(f"{goods}: detail identity mismatch")
    title = _clean(detail.get("gdsNm"))
    branch = _clean(detail.get("instNm"))
    if title != listed["_title"] or branch != listed["_branch"]:
        raise ValueError(f"{goods}: detail title or institution mismatch")
    status = _clean(detail.get("rcptStatus"))
    if status != listed["_status"] or status not in _NORMALIZED_STATUS:
        raise ValueError(f"{goods}: detail status mismatch")
    for field in ("onlnYn", "grndsRcptYn", "telRcptYn"):
        if _clean(detail.get(field)) not in {"Y", "N"}:
            raise ValueError(f"{goods}: detail {field} is not Y/N")
        if _clean(detail.get(field)) != _clean(listed.get(field)):
            raise ValueError(f"{goods}: detail/list {field} mismatch")
    start = _source_date(detail.get("eduBgngYmd"), f"{goods}.detail.eduBgngYmd")
    end = _source_date(detail.get("eduEndYmd"), f"{goods}.detail.eduEndYmd")
    apply_start = _source_date(
        detail.get("rcptBgngYmd"), f"{goods}.detail.rcptBgngYmd"
    )
    apply_end = _source_date(
        detail.get("rcptEndYmd"), f"{goods}.detail.rcptEndYmd"
    )
    if (
        start != listed["_start"]
        or end != listed["_end"]
        or apply_start != listed["_apply_start"]
        or apply_end != listed["_apply_end"]
    ):
        raise ValueError(f"{goods}: detail/list period mismatch")
    if not _clean(detail.get("gdsClsfNm")) or not _clean(detail.get("eduPlc")):
        raise ValueError(f"{goods}: empty classification or education place")
    capacity = _integer(detail.get("rcrtCnt"), f"{goods}.detail.rcrtCnt")
    applied = _integer(detail.get("rsvtAplyCnt"), f"{goods}.detail.rsvtAplyCnt")
    if capacity != _integer(listed.get("rcrtCnt"), f"{goods}.list.rcrtCnt"):
        raise ValueError(f"{goods}: detail/list capacity mismatch")
    if applied != _integer(listed.get("rsvtAplyCnt"), f"{goods}.list.rsvtAplyCnt"):
        raise ValueError(f"{goods}: detail/list application count mismatch")
    fee, fee_amount = _fee(detail, listed)
    municipality_code, municipality_name, municipality_evidence, evidence_source = (
        _municipality(detail)
    )
    raw_url = daegu_education_detail_url(inst, goods, lesson)
    if not raw_url:
        raise ValueError(f"{goods}: invalid detail URL identity")
    application_available = _application_available(detail, listed)
    description = _html_text(detail.get("lsnIntro")) or title
    warning = _html_text(detail.get("cutnMttr"))
    schedule = _schedule(detail)
    category = _clean(detail.get("gdsClsfNm"))
    return {
        "provider": DAEGU_EDUCATION_PROVIDER,
        "provider_course_id": (
            f"{DAEGU_EDUCATION_PROVIDER}:lect:{inst}:{goods}:{lesson}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": branch,
        "branch_code": inst,
        "preserve_branch": True,
        "provider_organizer": branch,
        "category": category,
        "program_type": "강좌",
        "raw_url": raw_url,
        "application_url": raw_url if application_available else "",
        "application_type": (
            "ONLINE_RESERVATION" if application_available else "INFO_ONLY"
        ),
        "reservation_available": application_available,
        "application_method_raw": ", ".join(
            label
            for flag, label in (
                (detail.get("onlnYn"), "온라인"),
                (detail.get("grndsRcptYn"), "현장"),
                (detail.get("telRcptYn"), "전화"),
            )
            if _clean(flag) == "Y"
        ),
        "status": _NORMALIZED_STATUS[status],
        "fee": fee,
        "fee_amount": fee_amount,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": schedule,
        "target": _clean(detail.get("eduTrgt")) or _clean(listed.get("utztnTrgt")),
        "instructor": _clean(detail.get("instrNm")),
        "capacity": f"{applied}/{capacity}",
        "capacity_current": applied,
        "capacity_total": capacity,
        "venue_name": _clean(detail.get("eduPlc")),
        "venue_address": _clean(detail.get("instAddr")),
        "address": _clean(detail.get("instAddr")),
        "phone": _clean(detail.get("inqryTelNo")),
        "description": description,
        "warning": warning,
        "collection_category": "평생학습",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": DAEGU_EDUCATION_PARSER,
        "municipality_code": municipality_code,
        "municipality_full_name": municipality_name,
        "raw_fields": {
            "inst_id": inst,
            "gds_id": goods,
            "lsn_id": lesson,
            "list_partition": listed["_partition"],
            "list_page": listed["_list_page"],
            "source_status": status,
            "classification_code": _clean(listed.get("gdsClsfCd")),
            "classification_group_code": _clean(listed.get("gdsClsfDcd")),
            "classification_name": category,
            "online_flag": _clean(detail.get("onlnYn")),
            "walk_in_flag": _clean(detail.get("grndsRcptYn")),
            "telephone_flag": _clean(detail.get("telRcptYn")),
            "deadline_flag": _clean(listed.get("ddlnYn")),
            "cancelled_flag": _clean(listed.get("rtrcnLsnYn")),
            "application_control_present": application_available,
            "municipality_evidence": municipality_evidence,
            "municipality_evidence_source": evidence_source,
        },
    }


def _detail_fetch(
    listed: Mapping[str, Any],
    *,
    session_factory: SessionFactory,
    timeout: int,
    attempts: int,
) -> dict[str, Any]:
    inst, goods, lesson = listed["_identity"]
    detail_url = daegu_education_detail_url(inst, goods, lesson)
    data = _post_json(
        DAEGU_DETAIL_API,
        {"instId": inst, "gdsId": goods, "lsnId": lesson},
        session_factory=session_factory,
        timeout=timeout,
        attempts=attempts,
        label=f"detail {inst}/{goods}/{lesson}",
        referer=detail_url,
    )
    return _detail_row(listed, data)


def _failed_meta(error: str = "") -> dict[str, Any]:
    errors = [error] if error else []
    return {
        "source_total": 0,
        "source_rows": 0,
        "partition_totals": {},
        "partition_pages": {},
        "page_counts": {},
        "pages": 0,
        "required_list_pages": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "list_api_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "stable_recheck_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": error,
        "errors": errors,
        "no_current_data": False,
        "no_current_reason": "",
        "parser": DAEGU_EDUCATION_PARSER,
        "netfunnel_gate": "official_initial_entry_per_api_request",
        "ownership_scope": DAEGU_OWNERSHIP_SCOPE,
        "separate_experience_owner": DAEGU_EXPERIENCE_PROVIDER,
        "separate_experience_url": DAEGU_EXPERIENCE_URL,
        "legacy_misrouted_target": (
            f"{DAEGU_EXPERIENCE_PROVIDER}@{DAEGU_EDUCATION_URL}"
        ),
        "discovery_alias_provider": DAEGU_DISCOVERY_ALIAS_PROVIDER,
    }


def collect_daegu_integrated_education(
    target: Any,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    *,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DAEGU_MAX_WORKERS,
    fetch_attempts: int = DAEGU_FETCH_ATTEMPTS,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future education snapshot.

    ``max_pages`` includes the required immediate empty sentinel page.
    ``detail_limit`` is a safety cap, not a partial-results limit; if it is
    lower than the current/future count, the entire snapshot fails closed.
    """

    if not is_daegu_integrated_education_target(target):
        meta = _failed_meta("target does not match the canonical Daegu education owner")
        return [], DAEGU_EDUCATION_PARSER, meta
    if timeout < 1 or max_pages < 1 or detail_limit < 0:
        meta = _failed_meta("invalid timeout, max_pages, or detail_limit")
        return [], DAEGU_EDUCATION_PARSER, meta
    if max_workers < 1 or fetch_attempts < 1:
        meta = _failed_meta("invalid max_workers or fetch_attempts")
        return [], DAEGU_EDUCATION_PARSER, meta

    factory = session_factory or _default_session_factory
    reference_day = _today(today)
    meta = _failed_meta()
    errors: list[str] = []
    source_rows: list[dict[str, Any]] = []
    first_signatures: dict[str, tuple[int, int, tuple[tuple[str, str, str], ...]]] = {}
    partition_totals: dict[str, int] = {}
    partition_pages: dict[str, int] = {}
    page_counts: dict[str, int] = {}
    required_list_pages = 0
    sentinel_requests = 0
    stability_rechecks = 0
    list_api_requests = 0

    try:
        for partition in _PARTITIONS:
            first_items, total, pages = _list_page(
                partition,
                1,
                session_factory=factory,
                timeout=timeout,
                attempts=fetch_attempts,
            )
            list_api_requests += 1
            first_signatures[partition] = _first_page_signature(first_items, total, pages)
            partition_totals[partition] = total
            partition_pages[partition] = pages
            if pages + 1 > max_pages:
                raise ValueError(
                    f"partition {partition} requires page {pages + 1} sentinel beyond max_pages"
                )
            expected_first = min(DAEGU_PAGE_SIZE, total)
            if len(first_items) != expected_first:
                raise ValueError(
                    f"partition {partition} page 1 returned {len(first_items)} of expected {expected_first}"
                )
            page_counts[f"{partition}:1"] = len(first_items)
            source_rows.extend(
                _validate_list_item(item, partition, 1) for item in first_items
            )
            required_list_pages += 1 if pages == 0 else pages

            for page in range(2, pages + 1):
                items, page_total, page_last = _list_page(
                    partition,
                    page,
                    session_factory=factory,
                    timeout=timeout,
                    attempts=fetch_attempts,
                )
                list_api_requests += 1
                if page_total != total or page_last != pages:
                    raise ValueError(f"partition {partition} pagination totals drifted")
                expected = min(DAEGU_PAGE_SIZE, total - ((page - 1) * DAEGU_PAGE_SIZE))
                if len(items) != expected:
                    raise ValueError(
                        f"partition {partition} page {page} returned {len(items)} of expected {expected}"
                    )
                page_counts[f"{partition}:{page}"] = len(items)
                source_rows.extend(
                    _validate_list_item(item, partition, page) for item in items
                )

            # For an empty partition page 1 is already the empty sentinel.
            if pages:
                sentinel_page = pages + 1
                items, sentinel_total, sentinel_last = _list_page(
                    partition,
                    sentinel_page,
                    session_factory=factory,
                    timeout=timeout,
                    attempts=fetch_attempts,
                )
                list_api_requests += 1
                sentinel_requests += 1
                page_counts[f"{partition}:{sentinel_page}"] = len(items)
                if items or sentinel_total != total or sentinel_last != pages:
                    raise ValueError(f"partition {partition} sentinel contract failed")

        identities = [row["_identity"] for row in source_rows]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate official identity across status partitions/pages")
        if len(source_rows) != sum(partition_totals.values()):
            raise ValueError("combined rows do not match combined declared totals")

        # Recheck first pages after all list pages so a changing source cannot
        # be mistaken for one coherent snapshot.
        for partition in _PARTITIONS:
            items, total, pages = _list_page(
                partition,
                1,
                session_factory=factory,
                timeout=timeout,
                attempts=fetch_attempts,
            )
            list_api_requests += 1
            stability_rechecks += 1
            if _first_page_signature(items, total, pages) != first_signatures[partition]:
                raise ValueError(f"partition {partition} changed during stable recheck")

        current = [row for row in source_rows if row["_end"] >= reference_day]
        if len(current) > detail_limit:
            meta = {
                **_failed_meta(
                    f"current/future detail count {len(current)} exceeds detail_limit {detail_limit}"
                ),
                "source_total": len(source_rows),
                "source_rows": len(source_rows),
                "partition_totals": partition_totals,
                "partition_pages": partition_pages,
                "page_counts": page_counts,
                "pages": required_list_pages,
                "required_list_pages": required_list_pages,
                "sentinel_requests": sentinel_requests,
                "stability_rechecks": stability_rechecks,
                "list_api_requests": list_api_requests,
                "current_count": len(current),
                "expired_count": len(source_rows) - len(current),
                "pagination_detected": any(value > 1 for value in partition_pages.values()),
                "pagination_complete": True,
                "stable_recheck_complete": True,
                "source_cap_reached": True,
            }
            return [], DAEGU_EDUCATION_PARSER, meta

        rows_by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
        detail_errors: list[str] = []
        lock = threading.Lock()

        def fetch_one(listed: Mapping[str, Any]) -> tuple[tuple[str, str, str], dict[str, Any]]:
            return listed["_identity"], _detail_fetch(
                listed,
                session_factory=factory,
                timeout=timeout,
                attempts=fetch_attempts,
            )

        with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(current)))) as executor:
            future_map = {executor.submit(fetch_one, row): row for row in current}
            for future in as_completed(future_map):
                listed = future_map[future]
                identity = listed["_identity"]
                try:
                    returned_identity, row = future.result()
                    if returned_identity != identity:
                        raise ValueError("worker identity mismatch")
                    with lock:
                        if identity in rows_by_identity:
                            raise ValueError("duplicate detail identity")
                        rows_by_identity[identity] = row
                except Exception as exc:
                    detail_errors.append(
                        f"detail {'/'.join(identity)}: {_clean(exc)}"
                    )
        if detail_errors:
            raise ValueError(" | ".join(sorted(detail_errors)))
        if len(rows_by_identity) != len(current):
            raise ValueError("not every current/future detail was collected")

        rows = [rows_by_identity[row["_identity"]] for row in current]
        if dedupe_rows is not None:
            rows = list(dedupe_rows(rows))
            if len(rows) != len(current):
                raise ValueError("shared row dedupe changed official identity cardinality")
        course_ids = [_clean(row.get("provider_course_id")) for row in rows]
        raw_urls = [_clean(row.get("raw_url")) for row in rows]
        if len(course_ids) != len(set(course_ids)) or len(raw_urls) != len(set(raw_urls)):
            raise ValueError("generated course IDs or detail URLs are not unique")

        municipality_counts = dict(
            Counter(_clean(row.get("municipality_full_name")) for row in rows)
        )
        branch_counts = dict(Counter(_clean(row.get("branch")) for row in rows))
        status_counts = dict(Counter(_clean(row.get("status")) for row in rows))
        meta = {
            **_failed_meta(),
            "source_total": len(source_rows),
            "source_rows": len(source_rows),
            "partition_totals": partition_totals,
            "partition_pages": partition_pages,
            "page_counts": page_counts,
            "pages": required_list_pages,
            "required_list_pages": required_list_pages,
            "sentinel_requests": sentinel_requests,
            "stability_rechecks": stability_rechecks,
            "list_api_requests": list_api_requests,
            "detail_attempts": len(current),
            "detail_pages": len(current),
            "current_count": len(current),
            "expired_count": len(source_rows) - len(current),
            "returned_count": len(rows),
            "pagination_detected": any(value > 1 for value in partition_pages.values()),
            "pagination_complete": True,
            "details_complete": True,
            "stable_recheck_complete": True,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "configured_collection_error": "",
            "errors": [],
            "no_current_data": not rows,
            "no_current_reason": (
                "all declared courses ended before the reference day" if not rows else ""
            ),
            "municipality_counts": municipality_counts,
            "branch_counts": branch_counts,
            "status_counts": status_counts,
            "actual_municipality_codes": sorted(
                {_clean(row.get("municipality_code")) for row in rows}
            ),
            "netfunnel_api_admissions": list_api_requests + len(current),
            "pii_payload_persisted": False,
        }
        return rows, DAEGU_EDUCATION_PARSER, meta
    except Exception as exc:
        error = _clean(exc)
        errors.append(error)
        meta = {
            **_failed_meta(error),
            "source_total": sum(partition_totals.values()),
            "source_rows": len(source_rows),
            "partition_totals": partition_totals,
            "partition_pages": partition_pages,
            "page_counts": page_counts,
            "pages": required_list_pages,
            "required_list_pages": required_list_pages,
            "sentinel_requests": sentinel_requests,
            "stability_rechecks": stability_rechecks,
            "list_api_requests": list_api_requests,
            "pagination_detected": any(value > 1 for value in partition_pages.values()),
            "errors": errors,
        }
        return [], DAEGU_EDUCATION_PARSER, meta


collect = collect_daegu_integrated_education
