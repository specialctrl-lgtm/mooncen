"""Fail-closed collector for Seoul Jung-gu's official education aggregate.

The public ``/booking/content.do?cmsid=16554`` list cannot be used as a
complete snapshot: its declared count, exposed cards, and official IDs do not
reconcile.  The same official service has two independently reconcilable
upstreams, however:

* Jung-gu's native information-education list, queried with ``type1`` on every
  page (the site's generated ``type=...`` pagination links lose the filter);
* AI내편중구's non-ended business API, followed by every EDU/PROGRAM detail
  and restricted to the education fields or official community-centre business
  code used by the Jung-gu aggregate.

This isolated module intentionally has no dependency on
``Crawler_MunicipalYaml``.  The parent crawler can inject its managed session
factory and row deduplicator without creating a circular import.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import html
import json
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SEOUL_JUNGGU_EDUCATION_PROVIDER = "MUNI_WWW_JUNGGU_SEOUL_KR_DC13188E"
SEOUL_JUNGGU_EDUCATION_URL = (
    "https://www.junggu.seoul.kr/booking/content.do?cmsid=16554"
)
SEOUL_JUNGGU_EDUCATION_PARSER = (
    "seoul_junggu_native_information_full+myhand_current_business_api+details"
)
SEOUL_JUNGGU_MUNICIPALITY_CODE = "1114000000"
SEOUL_JUNGGU_MUNICIPALITY_NAME = "서울특별시 중구"

JUNGGU_HOST = "www.junggu.seoul.kr"
JUNGGU_LIST_PATH = "/booking/content.do"
JUNGGU_LIST_CMSID = "16554"
JUNGGU_NATIVE_DETAIL_PATH = "/content.do"
JUNGGU_NATIVE_DETAIL_CMSID = "14235"
JUNGGU_NATIVE_CATEGORY = "정보화교육"
JUNGGU_NATIVE_PAGE_SIZE = 8

MYHAND_HOST = "myhand.junggu.seoul.kr"
MYHAND_LIST_PATH = "/user/business/list"
MYHAND_DETAIL_PREFIX = "/user/business/detail/"
MYHAND_PAGE_SIZE = 500
MYHAND_CANDIDATE_TYPES = frozenset({"EDU", "PROGRAM"})
MYHAND_KNOWN_TYPES = frozenset({"EDU", "PROGRAM", "WLF", "EVENT", "SURVEY"})
MYHAND_EDUCATION_FIELDS = frozenset({"여가체육", "진로진학", "취미교육"})
MYHAND_COMMUNITY_CENTRE_CODE = "54"

NON_COURSE_TITLE_TOKENS = (
    "노인일자리",
    "진로체험카드 신청",
    "공간 대여",
    "공간대여",
    "물품공유",
    "공유물품",
    "대관",
)
MAX_WORKERS = 8

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:년|[./-])\s*(\d{1,2})\s*"
    r"(?:월|[./-])\s*(\d{1,2})\s*(?:일)?"
)


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


def is_seoul_junggu_target(target: Any) -> bool:
    return (
        _provider(target) == SEOUL_JUNGGU_EDUCATION_PROVIDER
        and _target_url(target) == SEOUL_JUNGGU_EDUCATION_URL
    )


is_target = is_seoul_junggu_target


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
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not getattr(response, "content", b""):
        raise ValueError("empty HTTP response")
    return response


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


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


def _coerce_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    json_method = getattr(value, "json", None)
    if callable(json_method):
        return json_method()
    if isinstance(value, bytes):
        return json.loads(value.decode("utf-8"))
    if isinstance(value, str):
        return json.loads(value)
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return JSON")
    return json.loads(content.decode("utf-8"))


def _fetch_soup(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _fetch_json(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> Any:
    return _coerce_json(fetcher(current_session, url, timeout))


def native_list_url(page: int) -> str:
    query = urlencode(
        (
            ("cmsid", JUNGGU_LIST_CMSID),
            ("type1", JUNGGU_NATIVE_CATEGORY),
            ("page", str(max(1, int(page)))),
        )
    )
    return f"https://{JUNGGU_HOST}{JUNGGU_LIST_PATH}?{query}"


def native_detail_url(lecture_id: str) -> str:
    identity = _clean(lecture_id)
    if not identity.isdigit():
        return ""
    query = urlencode(
        (
            ("cmsid", JUNGGU_NATIVE_DETAIL_CMSID),
            ("command", "view"),
            ("lec_idx", identity),
        )
    )
    return f"https://{JUNGGU_HOST}{JUNGGU_NATIVE_DETAIL_PATH}?{query}"


def myhand_list_url() -> str:
    query = urlencode(
        (
            ("pageNum", "0"),
            ("pageSize", str(MYHAND_PAGE_SIZE)),
            ("categoryTargetId", "0"),
            ("categoryNoTargetArr", "[]"),
            ("ageCodeArr", "[]"),
            ("keywordArr", "[]"),
            ("incomeArr", "[]"),
            ("familyArr", "[]"),
            ("businessTypeArr", "[]"),
            ("ableApply", "[]"),
            ("ableCommunity", "[]"),
            ("q", ""),
            ("qt", "all"),
            ("organization", ""),
            ("endBusinessIncude", "1"),
            ("sorted", "1"),
        )
    )
    return f"https://{MYHAND_HOST}{MYHAND_LIST_PATH}?{query}"


def myhand_detail_url(business_id: str) -> str:
    identity = _clean(business_id)
    if not identity.isdigit():
        return ""
    return f"https://{MYHAND_HOST}{MYHAND_DETAIL_PREFIX}{identity}"


def _native_identity(value: Any) -> tuple[str, str]:
    candidate = _clean(value)
    parsed = urlparse(candidate)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _clean((query.get("lec_idx") or [""])[0])
    if (
        parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == JUNGGU_HOST
        and parsed.path == JUNGGU_NATIVE_DETAIL_PATH
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
        and set(query) == {"cmsid", "command", "lec_idx"}
        and query.get("cmsid") == [JUNGGU_NATIVE_DETAIL_CMSID]
        and query.get("command") == ["view"]
        and identity.isdigit()
    ):
        return identity, native_detail_url(identity)
    return "", ""


def _declared_total(soup: BeautifulSoup) -> Optional[int]:
    node = soup.select_one(".page_num span") or soup.select_one(".page_num")
    match = re.search(r"\d[\d,]*", _clean(node.get_text(" ", strip=True) if node else ""))
    return int(match.group(0).replace(",", "")) if match else None


def _list_pairs(card: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in card.select(".dl_wrap dl"):
        label = node.select_one("dt")
        value = node.select_one("dd")
        key = re.sub(r"[\s:：]", "", _clean(label.get_text(" ", strip=True) if label else ""))
        text = _clean(value.get_text(" ", strip=True) if value else "")
        if key and text:
            result[key] = text
    return result


def _date_values(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(str(value or "")):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return result


def _iso_date(value: Any) -> Optional[date]:
    text = _clean(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _date_range(value: Any) -> tuple[Optional[date], Optional[date]]:
    values = _date_values(value)
    if not values:
        return None, None
    return values[0], values[-1]


def _capacity(value: Any) -> Optional[int]:
    match = re.search(r"\d[\d,]*", _clean(value))
    return int(match.group(0).replace(",", "")) if match else None


def _stable_branch_code(provider: str, branch: str) -> str:
    normalized = re.sub(r"\s+", "", _clean(branch)).lower()
    digest = hashlib.sha1(f"{provider}|{normalized}".encode("utf-8")).hexdigest()[:12].upper()
    return f"JUNGGU_BRANCH_{digest}"


def _base_row(
    target: Any,
    *,
    provider_course_id: str,
    title: str,
    branch: str,
    raw_url: str,
) -> dict[str, Any]:
    provider = _provider(target)
    return {
        "provider": provider,
        "provider_course_id": provider_course_id[:100],
        "prefer_incoming_provider_course_id": True,
        "title": _clean(title),
        "branch": _clean(branch),
        "branch_code": _stable_branch_code(provider, branch),
        "preserve_branch": True,
        "branch_url": _target_url(target),
        "raw_url": raw_url,
        "program_type": "강좌",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "collection_type": "official_upstream_complete+detail",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "reservation_available": False,
    }


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _application_state(
    apply_start: Optional[date],
    apply_end: Optional[date],
    actionable_url: str,
    cutoff: date,
) -> tuple[str, str, bool]:
    if apply_start and apply_start > cutoff:
        return "SCHEDULED", "", False
    if actionable_url and apply_end and (not apply_start or apply_start <= cutoff) and apply_end >= cutoff:
        return "OPEN", actionable_url, True
    return "CLOSED", "", False


def _safe_external_url(value: Any) -> str:
    candidate = _clean(value)
    parsed = urlparse(candidate)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        return ""
    return candidate


def _native_apply_url(identity: str, soup: BeautifulSoup) -> str:
    node = soup.select_one("a.btn_write[href]")
    candidate = urljoin(f"https://{JUNGGU_HOST}/", node.get("href") if node else "")
    parsed = urlparse(candidate)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == JUNGGU_HOST
        and parsed.path == JUNGGU_NATIVE_DETAIL_PATH
        and set(query) == {"cmsid", "command", "lec_idx"}
        and query.get("cmsid") == [JUNGGU_NATIVE_DETAIL_CMSID]
        and query.get("command") == ["write"]
        and query.get("lec_idx") == [identity]
    ):
        return candidate
    return ""


def _native_detail_row(
    target: Any,
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
) -> tuple[Optional[dict[str, Any]], str, list[str]]:
    identity = _clean(listed.get("official_id"))
    errors: list[str] = []
    pairs: dict[str, str] = {}
    for label_node in soup.select("th"):
        value_node = label_node.find_next_sibling("td")
        label = re.sub(r"[\s:：]", "", _clean(label_node.get_text(" ", strip=True)))
        value = _clean(value_node.get_text(" ", strip=True) if value_node else "")
        if label and value:
            pairs[label] = value
    title = _clean(pairs.get("강좌명"))
    branch = _clean(pairs.get("담당부서"))
    venue = _clean(pairs.get("교육장소"))
    period = _clean(pairs.get("교육기간"))
    apply_period = _clean(pairs.get("접수기간"))
    start_date, end_date = _date_range(period)
    apply_start, apply_end = _date_range(apply_period)
    if title != _clean(listed.get("title")):
        errors.append(f"native {identity}: list/detail title mismatch")
    if not all((identity, title, branch, venue, start_date, end_date, apply_start, apply_end)):
        errors.append(f"native {identity}: required detail fields are missing")
    if errors:
        return None, "invalid", errors
    action_url = _native_apply_url(identity, soup)
    status, application_url, available = _application_state(
        apply_start, apply_end, action_url, cutoff
    )
    row = _base_row(
        target,
        provider_course_id=f"{_provider(target)}:junggu-lecture:{identity}",
        title=title,
        branch=branch,
        raw_url=_clean(listed.get("raw_url")),
    )
    row.update(
        {
            "category": _clean(pairs.get("구분")) or JUNGGU_NATIVE_CATEGORY,
            "instructor": _clean(pairs.get("강사명")),
            "target": _clean(pairs.get("교육대상")) or _clean(listed.get("target")),
            "eligibility_raw": _clean(pairs.get("교육대상")) or _clean(listed.get("target")),
            "period": period,
            "start_date": start_date,
            "end_date": end_date,
            "apply_period": apply_period,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": _clean(pairs.get("교육시간")),
            "fee": _clean(pairs.get("수강료")),
            "capacity_total": _capacity(pairs.get("정원")),
            "venue_name": venue,
            "room": venue,
            "phone": _clean(pairs.get("문의전화")),
            "application_method_raw": _clean(pairs.get("접수방법")),
            "status": status,
            "reservation_available": available,
            "description": _clean(pairs.get("강좌소개")),
            "raw_fields": {
                "parser": SEOUL_JUNGGU_EDUCATION_PARSER,
                "source": "junggu-native-information",
                "official_id": identity,
                "list_status": _clean(listed.get("list_status")),
                "list_pairs": dict(listed.get("list_pairs") or {}),
                "detail_pairs": pairs,
            },
        }
    )
    if application_url:
        row["application_url"] = application_url
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row["raw_fields"]["clear_application_url"] = True
    if end_date < cutoff:
        return None, "expired", []
    return _clean_row(row), "current", []


def _embedded_business(soup: BeautifulSoup) -> dict[str, Any]:
    for node in soup.select("script"):
        value = node.string if isinstance(node.string, str) else node.get_text("", strip=False)
        marker = "businessData:"
        offset = value.find(marker)
        if offset < 0:
            continue
        data, _end = json.JSONDecoder().raw_decode(value[offset + len(marker) :].lstrip())
        if isinstance(data, dict):
            return data
    raise ValueError("myhand detail did not expose businessData")


def _myhand_apply_url(data: Mapping[str, Any], identity: str) -> str:
    organization_code = data.get("organizationCodeId")
    if organization_code in {1, 2, 6}:
        return _safe_external_url(data.get("link"))
    form_id = _clean(data.get("applyFormId"))
    if identity.isdigit() and form_id.isdigit() and int(form_id) > 0:
        return f"https://{MYHAND_HOST}/user/business/detail/apply/{identity}/{form_id}"
    return ""


def _myhand_description(data: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("content", "info", "proce", "note", "clientRule", "etc"):
        value = _clean(data.get(key))
        if value:
            chunks.append(_clean(BeautifulSoup(html.unescape(value), "lxml").get_text(" ", strip=True)))
    return _clean(" ".join(chunks))


def _myhand_detail_row(
    target: Any,
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
) -> tuple[Optional[dict[str, Any]], str, list[str]]:
    identity = _clean(listed.get("official_id"))
    errors: list[str] = []
    try:
        data = _embedded_business(soup)
    except Exception as exc:
        return None, "invalid", [f"myhand {identity}: {type(exc).__name__}"]
    if _clean(data.get("businessId")) != identity:
        errors.append(f"myhand {identity}: detail identity mismatch")
    title = _clean(data.get("businessName") or data.get("name"))
    branch = _clean(data.get("host") or data.get("organizationName"))
    business_type = _clean(data.get("businessType"))
    if title != _clean(listed.get("title")):
        errors.append(f"myhand {identity}: list/detail title mismatch")
    if business_type != _clean(listed.get("business_type")):
        errors.append(f"myhand {identity}: list/detail business type mismatch")
    if _clean(data.get("useBusinessSubYn")) == "Y":
        errors.append(f"myhand {identity}: unexpected sub-item business")
    if _clean(data.get("endDate")) != _clean(listed.get("end_date")):
        errors.append(f"myhand {identity}: list/detail end date mismatch")
    source_start = _iso_date(data.get("startDate"))
    source_end = _iso_date(data.get("endDate"))
    apply_start = _iso_date(data.get("applyStartDate"))
    apply_end = _iso_date(data.get("applyEndDate"))
    study_start = _iso_date(data.get("studyStartDate"))
    study_end = _iso_date(data.get("studyEndDate"))
    if not all((identity, title, branch, business_type, source_end)):
        errors.append(f"myhand {identity}: required detail fields are missing")
    if errors:
        return None, "invalid", errors

    tags = {_clean(item) for item in str(data.get("categoryNames") or "").split(",") if _clean(item)}
    education_fields = tags.intersection(MYHAND_EDUCATION_FIELDS)
    community_centre = _clean(data.get("businessCodeId")) == MYHAND_COMMUNITY_CENTRE_CODE
    if not education_fields and not community_centre:
        return None, "excluded", []
    if any(token in title for token in NON_COURSE_TITLE_TOKENS):
        return None, "excluded", []

    effective_start = study_start or source_start
    effective_end = study_end or source_end
    if not effective_end or effective_end < cutoff:
        return None, "expired", []

    action_url = _myhand_apply_url(data, identity)
    status, application_url, available = _application_state(
        apply_start, apply_end, action_url, cutoff
    )
    place = _clean(data.get("place"))
    apply_period = " ~ ".join(
        part for part in (_clean(data.get("applyStartDate")), _clean(data.get("applyEndDate"))) if part
    )
    period = " ~ ".join(
        part for part in (
            _clean(data.get("studyStartDate") or data.get("startDate")),
            _clean(data.get("studyEndDate") or data.get("endDate")),
        )
        if part
    )
    schedule = " / ".join(
        part
        for part in (
            _clean(data.get("studyWeekend")),
            " ~ ".join(
                part
                for part in (
                    _clean(data.get("studyStartTime"))[:5],
                    _clean(data.get("studyEndTime"))[:5],
                )
                if part
            ),
        )
        if part
    )
    target_text = _clean(data.get("clientRule"))
    row = _base_row(
        target,
        provider_course_id=f"{_provider(target)}:myhand-business:{identity}",
        title=title,
        branch=branch,
        raw_url=_clean(listed.get("raw_url")),
    )
    row.update(
        {
            "category": ",".join(
                [*sorted(education_fields), *(["자치회관"] if community_centre else [])]
            ),
            "target": target_text,
            "eligibility_raw": target_text,
            "period": period,
            "start_date": effective_start,
            "end_date": effective_end,
            "apply_period": apply_period,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": schedule,
            "instructor": _clean(data.get("teacher")),
            "fee": _clean(data.get("cost")),
            "capacity_total": _capacity(data.get("clientCount")),
            "venue_name": place or branch,
            "room": place,
            "phone": _clean(data.get("contact")),
            "application_method_raw": "온라인" if action_url else "",
            "status": status,
            "reservation_available": available,
            "description": _myhand_description(data),
            "raw_fields": {
                "parser": SEOUL_JUNGGU_EDUCATION_PARSER,
                "source": "myhand-current-business-api",
                "official_id": identity,
                "business_type": business_type,
                "business_code_id": _clean(data.get("businessCodeId")),
                "business_code_name": _clean(data.get("businessCodeName")),
                "api_name": _clean(data.get("apiName")),
                "organization_name": _clean(data.get("organizationName")),
                "organization_code_id": data.get("organizationCodeId"),
                "apply_form_id": _clean(data.get("applyFormId")),
                "category_names": sorted(tags),
                "source_start_date": source_start,
                "source_end_date": source_end,
            },
        }
    )
    if application_url:
        row["application_url"] = application_url
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row["raw_fields"]["clear_application_url"] = True
    return _clean_row(row), "current", []


def _native_records(soup: BeautifulSoup, page_url: str) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    invalid = 0
    for card in soup.select(".edu_list_wrap > ul > li"):
        title_node = card.select_one(".tit")
        branch_node = card.select_one(".small_tit")
        link = card.select_one("a.now_type[href]")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        branch = _clean(branch_node.get_text(" ", strip=True) if branch_node else "")
        identity, raw_url = _native_identity(urljoin(page_url, link.get("href") if link else ""))
        if not all((title, branch, link, identity, raw_url)):
            invalid += 1
            continue
        pairs = _list_pairs(card)
        records.append(
            {
                "kind": "native",
                "official_id": identity,
                "title": title,
                "branch": branch,
                "raw_url": raw_url,
                "list_status": _clean(link.get_text(" ", strip=True)),
                "list_pairs": pairs,
                "target": _clean(pairs.get("지원대상")),
            }
        )
    return records, invalid


def _myhand_records(payload: Any) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    errors: list[str] = []
    if not isinstance(payload, dict) or payload.get("code") != "success":
        return [], {}, ["myhand list response is not a successful object"]
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        return [], {}, ["myhand list response lacks PageInfo data"]
    rows = data["list"]
    metrics = {
        "total": int(data.get("total") or 0),
        "pages": int(data.get("pages") or 0),
        "page_size": int(data.get("pageSize") or 0),
        "size": int(data.get("size") or 0),
    }
    if metrics != {
        "total": len(rows),
        "pages": 1,
        "page_size": MYHAND_PAGE_SIZE,
        "size": len(rows),
    }:
        errors.append(f"myhand PageInfo does not reconcile: {metrics}, rows={len(rows)}")
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    unknown_types: Counter[str] = Counter()
    for item in rows:
        if not isinstance(item, dict):
            errors.append("myhand list exposed a non-object item")
            continue
        identity = _clean(item.get("businessId"))
        title = _clean(item.get("name"))
        business_type = _clean(item.get("typeName"))
        if not identity.isdigit() or not title or not business_type:
            errors.append("myhand list item lacks identity, title, or business type")
            continue
        if identity in seen:
            errors.append(f"myhand list duplicated businessId {identity}")
            continue
        seen.add(identity)
        if business_type not in MYHAND_KNOWN_TYPES:
            unknown_types[business_type] += 1
        if business_type not in MYHAND_CANDIDATE_TYPES:
            continue
        records.append(
            {
                "kind": "myhand",
                "official_id": identity,
                "title": title,
                "branch": _clean(item.get("host") or item.get("oname")),
                "raw_url": myhand_detail_url(identity),
                "business_type": business_type,
                "start_date": _clean(item.get("startDate")),
                "end_date": _clean(item.get("endDate")),
            }
        )
    if len(seen) != metrics.get("total"):
        errors.append(
            f"myhand total {metrics.get('total')} does not match {len(seen)} unique IDs"
        )
    if unknown_types:
        errors.append(f"myhand list exposed unknown business types: {dict(unknown_types)}")
    return records, metrics, errors


def _parallel_details(
    target: Any,
    records: list[dict[str, Any]],
    *,
    timeout: int,
    detail_limit: int,
    max_workers: int,
    cutoff: date,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> tuple[list[dict[str, Any]], Counter[str], int, int, list[str], bool]:
    allowed = max(0, int(detail_limit))
    selected = records[:allowed]
    capped = len(selected) < len(records)
    local = threading.local()
    sessions: list[Any] = []
    sessions_lock = threading.Lock()

    def current_session() -> Any:
        value = getattr(local, "session", None)
        if value is None:
            value = session_factory()
            local.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def one(record: dict[str, Any]) -> tuple[Optional[dict[str, Any]], str, list[str], bool]:
        identity = _clean(record.get("official_id"))
        try:
            soup = _fetch_soup(
                fetcher, current_session(), _clean(record.get("raw_url")), timeout
            )
        except Exception as exc:
            return None, "invalid", [f"{record.get('kind')} {identity}: detail fetch {type(exc).__name__}"], False
        if record.get("kind") == "native":
            row, state, errors = _native_detail_row(target, record, soup, cutoff)
        else:
            row, state, errors = _myhand_detail_row(target, record, soup, cutoff)
        return row, state, errors, True

    results: list[tuple[Optional[dict[str, Any]], str, list[str], bool]] = []
    try:
        if selected:
            workers = min(MAX_WORKERS, max(1, int(max_workers)), len(selected))
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="seoul-junggu-education-detail"
            ) as pool:
                results = list(pool.map(one, selected))
    finally:
        for value in sessions:
            _close_quietly(value)
    rows = [row for row, _state, _errors, _fetched in results if row is not None]
    states = Counter(state for _row, state, _errors, _fetched in results)
    errors = [error for _row, _state, item_errors, _fetched in results for error in item_errors]
    fetched = sum(fetched for _row, _state, _errors, fetched in results)
    return rows, states, len(selected), fetched, errors, capped


def collect_seoul_junggu_education(
    target: Any,
    timeout: int = 25,
    max_pages: int = 20,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect and validate both reconcilable official upstreams."""

    errors: list[str] = []
    if not is_seoul_junggu_target(target):
        errors.append("target does not match the provider-owned Seoul Jung-gu education route")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    primary_session: Any = None
    native_records: list[dict[str, Any]] = []
    myhand_records: list[dict[str, Any]] = []
    native_total = 0
    native_pages = 0
    native_invalid = 0
    native_duplicates = 0
    myhand_metrics: dict[str, int] = {}
    source_cap_reached = False

    try:
        if not errors:
            primary_session = make_session()
            try:
                first = _fetch_soup(fetch, primary_session, native_list_url(1), timeout)
            except Exception as exc:
                errors.append(f"native page 1 fetch {type(exc).__name__}")
                first = None
            declared_native_pages = 0
            if first is not None:
                declared = _declared_total(first)
                if declared is None:
                    errors.append("native list did not declare its total")
                else:
                    native_total = declared
                    declared_native_pages = max(1, math.ceil(declared / JUNGGU_NATIVE_PAGE_SIZE))
                required_pages = declared_native_pages + 1
                allowed_pages = max(1, int(max_pages))
                if required_pages > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap allows {allowed_pages} of {required_pages} upstream pages"
                    )
                allowed_native = min(declared_native_pages, max(0, allowed_pages - 1))
                seen_native: set[str] = set()
                for page in range(1, allowed_native + 1):
                    if page == 1:
                        soup = first
                    else:
                        try:
                            soup = _fetch_soup(
                                fetch, primary_session, native_list_url(page), timeout
                            )
                        except Exception as exc:
                            errors.append(f"native page {page} fetch {type(exc).__name__}")
                            break
                    native_pages += 1
                    if _declared_total(soup) != native_total:
                        errors.append(f"native page {page} changed the declared total")
                    records, invalid = _native_records(soup, native_list_url(page))
                    native_invalid += invalid
                    expected = min(
                        JUNGGU_NATIVE_PAGE_SIZE,
                        max(0, native_total - ((page - 1) * JUNGGU_NATIVE_PAGE_SIZE)),
                    )
                    if len(records) + invalid != expected:
                        errors.append(
                            f"native page {page} exposed {len(records) + invalid} cards; expected {expected}"
                        )
                    for record in records:
                        identity = _clean(record.get("official_id"))
                        if identity in seen_native:
                            native_duplicates += 1
                            continue
                        seen_native.add(identity)
                        native_records.append(record)
                if len(seen_native) != native_total:
                    errors.append(
                        f"native total {native_total} does not match {len(seen_native)} unique IDs"
                    )

                if allowed_pages >= 1:
                    try:
                        payload = _fetch_json(fetch, primary_session, myhand_list_url(), timeout)
                    except Exception as exc:
                        errors.append(f"myhand list fetch {type(exc).__name__}")
                    else:
                        myhand_records, myhand_metrics, myhand_errors = _myhand_records(payload)
                        errors.extend(myhand_errors)
    finally:
        _close_quietly(primary_session)

    if native_invalid:
        errors.append(f"native list exposed {native_invalid} malformed cards")
    if native_duplicates:
        errors.append(f"native list exposed {native_duplicates} duplicate official IDs")

    required_native_pages = max(1, math.ceil(native_total / JUNGGU_NATIVE_PAGE_SIZE)) if native_total else 0
    list_complete = bool(
        not errors
        and native_pages == required_native_pages
        and len(native_records) == native_total
        and myhand_metrics.get("total", 0) > 0
        and myhand_metrics.get("pages") == 1
    )
    detail_records = [*native_records, *myhand_records]
    (
        rows,
        detail_states,
        detail_attempts,
        detail_pages,
        detail_errors,
        detail_capped,
    ) = _parallel_details(
        target,
        detail_records,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        cutoff=cutoff,
        fetcher=fetch,
        session_factory=make_session,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {detail_attempts} of {len(detail_records)} required details"
        )
    if detail_errors:
        errors.append(f"detail validation failed for {len(detail_errors)} fields/items")

    provider_ids = [_clean(row.get("provider_course_id")) for row in rows]
    if len(provider_ids) != len(set(provider_ids)):
        errors.append("current rows contain duplicate provider_course_id values")
    if dedupe_rows is not None:
        try:
            rows = list(dedupe_rows(rows))
        except Exception as exc:
            errors.append(f"dedupe_rows {type(exc).__name__}")

    details_complete = bool(
        detail_attempts == len(detail_records)
        and detail_pages == len(detail_records)
        and not detail_errors
    )
    snapshot_complete = list_complete and details_complete and not errors
    no_current_data = snapshot_complete and not rows
    unique_errors = list(dict.fromkeys([*errors, *detail_errors]))
    meta: dict[str, Any] = {
        "pages": native_pages + (1 if myhand_metrics else 0),
        "declared_pages": required_native_pages + 1,
        "detail_pages": detail_pages,
        "detail_attempts": detail_attempts,
        "detail_required_count": len(detail_records),
        "required_detail_count": len(detail_records),
        "pagination_detected": required_native_pages > 1,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "full_snapshot_required": True,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "source_total": native_total + myhand_metrics.get("total", 0),
        "native_total": native_total,
        "native_pages": native_pages,
        "native_invalid_count": native_invalid,
        "native_duplicate_count": native_duplicates,
        "myhand_total": myhand_metrics.get("total", 0),
        "myhand_pages": myhand_metrics.get("pages", 0),
        "myhand_detail_candidates": len(myhand_records),
        "discovered_links": native_total + myhand_metrics.get("total", 0),
        "candidate_count": len(detail_records),
        "expired_count": detail_states.get("expired", 0),
        "excluded_non_course_count": detail_states.get("excluded", 0),
        "invalid_detail_count": detail_states.get("invalid", 0),
        "current_count": len(rows),
        "reservation_discovery_links": sum(
            bool(_clean(row.get("application_url"))) for row in rows
        ),
        "source_counts": dict(
            Counter(_clean(row.get("raw_fields", {}).get("source")) for row in rows)
        ),
        "branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
        "no_current_data": no_current_data,
        "no_current_reason": (
            "official Jung-gu current/future education upstreams are empty"
            if no_current_data
            else ""
        ),
    }
    if unique_errors:
        shown = unique_errors[:50]
        message = "; ".join(shown)
        if len(unique_errors) > len(shown):
            message += f"; ... {len(unique_errors) - len(shown)} more errors"
        meta["configured_collection_error"] = message
    return rows, SEOUL_JUNGGU_EDUCATION_PARSER, meta


collect_seoul_junggu_target = collect_seoul_junggu_education


__all__ = [
    "JUNGGU_NATIVE_CATEGORY",
    "JUNGGU_NATIVE_PAGE_SIZE",
    "MYHAND_EDUCATION_FIELDS",
    "MYHAND_COMMUNITY_CENTRE_CODE",
    "MYHAND_PAGE_SIZE",
    "SEOUL_JUNGGU_EDUCATION_PARSER",
    "SEOUL_JUNGGU_EDUCATION_PROVIDER",
    "SEOUL_JUNGGU_EDUCATION_URL",
    "collect_seoul_junggu_education",
    "collect_seoul_junggu_target",
    "is_seoul_junggu_target",
    "is_target",
    "myhand_detail_url",
    "myhand_list_url",
    "native_detail_url",
    "native_list_url",
]
