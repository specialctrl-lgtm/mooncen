"""Fail-closed collector for Gangseo-gu public sports education.

The Gangseo FMCS list intentionally omits course dates.  A row is therefore
publishable only after all of the following official sources agree:

* the FMCS company API exposes only ``GANGSEO04``;
* the lecture API returns its complete, declared 26-row snapshot when asked for
  100 rows (the website's default 20 silently truncates the response);
* every ``(comcd, class_cd)`` has a reviewed mapping to the official schedules
  at ``/fmcs/102``, ``/fmcs/103`` or ``/fmcs/104``;
* the current summer-school notice supplies the dated period for the ten
  seasonal classes; and
* every FMCS course detail confirms its identity and status.

Any count, identity, date, schedule, detail or request-cap drift returns no rows
and marks the snapshot incomplete.  This module has no dependency on the main
municipal crawler, so the latter can import it without a cycle.  Callers can
inject their safe session, fetch and de-duplication helpers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GANGSEO_SPORTS_PROVIDER = "MUNI_SPORTS_GANGSEO_SEOUL_KR_8F1FDD36"
GANGSEO_SPORTS_URL = "https://sports.gangseo.seoul.kr/fmcs/27"
GANGSEO_SPORTS_HOST = "sports.gangseo.seoul.kr"
GANGSEO_SPORTS_PATH = "/fmcs/27"
GANGSEO_COMPANY_CODE = "GANGSEO04"
GANGSEO_COMPANY_NAME = "생활체육프로그램"
GANGSEO_EXPECTED_DECLARED_COUNT = 26
GANGSEO_LIST_PAGE_SIZE = 100
GANGSEO_PARSER = "gangseo_public_sports_company_api_current_future+detail+official_schedule"

GANGSEO_COMPANY_API = "https://sports.gangseo.seoul.kr/rest/common/company"
GANGSEO_LECTURE_API = "https://sports.gangseo.seoul.kr/rest/lecture/list"
GANGSEO_NOTICE_URL = "https://sports.gangseo.seoul.kr/fmcs/30"
GANGSEO_SCHEDULE_URLS = {
    "/fmcs/102": "https://sports.gangseo.seoul.kr/fmcs/102",
    "/fmcs/103": "https://sports.gangseo.seoul.kr/fmcs/103",
    "/fmcs/104": "https://sports.gangseo.seoul.kr/fmcs/104",
}

GANGSEO_MUNICIPALITY_CODE = "1150000000"
GANGSEO_MUNICIPALITY_NAME = "서울특별시 강서구"

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_SAFE_TEXT_RE = re.compile(r"[^0-9A-Za-z가-힣]+")
_CLASS_CODE_RE = re.compile(r"\d{5}")
_NOTICE_ID_RE = re.compile(r"[0-9a-f]{32}", re.IGNORECASE)
_MONTH_DAY_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*[.월]\s*(\d{1,2})\s*[.]?\s*"
    r"(?:\([^)]*\))?\s*[~∼-]\s*"
    r"(\d{1,2})\s*[.월]\s*(\d{1,2})"
)


@dataclass(frozen=True)
class ScheduleSpec:
    class_cd: str
    title: str
    category1: str
    category2: str
    train_day: str
    start_time: str
    end_time: str
    target: str
    venue: str
    address: str
    source_path: str
    source_signatures: tuple[str, ...]
    period_kind: str
    notice_signatures: tuple[str, ...] = ()
    extra_fee: str = ""


def _spec(
    class_cd: str,
    title: str,
    category1: str,
    category2: str,
    train_day: str,
    start_time: str,
    end_time: str,
    target: str,
    venue: str,
    source_path: str,
    source_signatures: tuple[str, ...],
    period_kind: str,
    *,
    address: str = "",
    notice_signatures: tuple[str, ...] = (),
    extra_fee: str = "",
) -> ScheduleSpec:
    return ScheduleSpec(
        class_cd=class_cd,
        title=title,
        category1=category1,
        category2=category2,
        train_day=train_day,
        start_time=start_time,
        end_time=end_time,
        target=target,
        venue=venue,
        address=address,
        source_path=source_path,
        source_signatures=source_signatures,
        period_kind=period_kind,
        notice_signatures=notice_signatures,
        extra_fee=extra_fee,
    )


# This is an audited identity-to-schedule contract, not a title guesser.  A new
# or repurposed class code must be reviewed before it can be published.
GANGSEO_SCHEDULE_SPECS: dict[str, ScheduleSpec] = {
    "00058": _spec("00058", "족구교실1", "생활체육교실", "족구", "토", "13:00", "15:00", "강서구민", "무궁화족구장", "/fmcs/102", ("족구", "상반기(4월~6월)/하반기(9월~11월)", "토- 13:00 ~ 15:00", "무궁화족구장"), "spring_fall"),
    "00041": _spec("00041", "족구교실2", "생활체육교실", "족구", "일", "10:00", "12:00", "강서구민", "무궁화족구장", "/fmcs/102", ("족구", "상반기(4월~6월)/하반기(9월~11월)", "일- 10:00 ~ 12:00", "무궁화족구장"), "spring_fall"),
    "00009": _spec("00009", "초보자전거교실", "생활체육교실", "자전거", "월수금", "10:00", "12:00", "강서구민", "강서구립 자전거연습장", "/fmcs/102", ("자전거", "상반기(4월~6월)/하반기(9월~11월)", "월, 수, 금", "10:00 ~ 12:00", "강서구립 자전거연습장"), "spring_fall", address="서울특별시 강서구 방화3동 54-5", extra_fee="교육실비 월 10,000원(현장납부)"),
    "00010": _spec("00010", "초보골프교실1", "생활체육교실", "골프", "수", "13:00", "14:00", "강서구민", "쇼골프 김포공항점", "/fmcs/102", ("골프", "상반기(4월~6월)/하반기(9월~11월)", "수- 13:00 ~ 14:00", "쇼골프 김포공항점"), "spring_fall", extra_fee="시설사용료 월 60,000원(현장납부)"),
    "00012": _spec("00012", "초보골프교실2", "생활체육교실", "골프", "금", "14:00", "15:00", "강서구민", "쇼골프 김포공항점", "/fmcs/102", ("골프", "상반기(4월~6월)/하반기(9월~11월)", "금- 14:00 ~ 15:00", "쇼골프 김포공항점"), "spring_fall", extra_fee="시설사용료 월 60,000원(현장납부)"),
    "00061": _spec("00061", "초보파크골프교실", "생활체육교실", "파크골프", "화목", "09:00", "10:00", "강서구민", "마실파크골프", "/fmcs/102", ("파크골프", "상반기(4월~5월)", "화, 목", "9:00 ~ 10:00", "마실파크골프"), "park_spring", extra_fee="시설사용료 월 30,000원(현장납부)"),
    "00014": _spec("00014", "초보인라인교실", "생활체육교실", "인라인", "화목", "16:30", "17:30", "관내 초등학생", "방화근린공원", "/fmcs/102", ("인라인스케이트", "상반기(4월~6월)/하반기(9월~11월)", "16:30 ~ 17:30", "방화근린공원"), "spring_fall"),
    "00047": _spec("00047", "여성풋살교실", "생활체육교실", "여성풋살", "화목", "20:00", "22:00", "강서구민", "강서개화풋살장", "/fmcs/102", ("여성풋살", "4월 ~ 11월", "20:00 ~ 22:00", "강서개화풋살장"), "apr_nov"),
    "00062": _spec("00062", "초보러닝교실", "생활체육교실", "러닝", "목", "20:00", "21:00", "강서구민", "우장산축구장", "/fmcs/102", ("러닝", "상반기(4월~6월)/하반기(9월~11월)", "20:00 ~ 21:00", "우장산축구장"), "spring_fall"),
    "00055": _spec("00055", "여성배구교실", "생활체육교실", "여성배구", "월목", "13:00", "15:00", "강서구민(여성)", "강서구민 올림픽체육센터", "/fmcs/102", ("여성배구", "1월 ∼ 12월", "13:00 ~ 16:00", "강서구민 올림픽체육센터"), "jan_dec"),
    "00060": _spec("00060", "테니스교실", "생활체육교실", "테니스", "화목", "09:00", "11:00", "강서구민", "구립 방화테니스장", "/fmcs/102", ("테니스", "상반기(4월~6월)/하반기(9월~11월)", "9:00 ~ 11:00", "구립 방화테니스장"), "spring_fall"),
    "00016": _spec("00016", "어린이축구교실(고학년)", "축구/배구교실", "어린이축구", "월수금", "16:00", "18:00", "고학년", "우장산 인조잔디구장", "/fmcs/103", ("어린이축구", "고학년", "1월 ∼ 12월", "16:00∼18:00", "우장산 인조잔디구장"), "jan_dec"),
    "00015": _spec("00015", "어린이축구교실(저학년)", "축구/배구교실", "어린이축구", "월수금", "16:00", "18:00", "저학년", "우장산 인조잔디구장", "/fmcs/103", ("어린이축구", "저학년", "1월 ∼ 12월", "16:00∼18:00", "우장산 인조잔디구장"), "jan_dec"),
    "00018": _spec("00018", "청소년풋살교실(중.고등학생)", "축구/배구교실", "청소년풋살", "월수금", "18:00", "20:00", "중.고등학생", "우장산 인조잔디구장", "/fmcs/103", ("청소년풋살", "중·고등부", "1월 ∼ 12월", "18:00∼20:00", "우장산 인조잔디구장"), "jan_dec"),
    "00017": _spec("00017", "청소년풋살교실(초등학생)", "축구/배구교실", "청소년풋살", "화목금", "16:00", "18:00", "초등학생", "서낭당근린공원", "/fmcs/103", ("청소년풋살", "초등학생", "1월 ∼ 12월", "16:00∼18:00", "서낭당근린공원"), "jan_dec"),
    "00019": _spec("00019", "여성축구교실", "축구/배구교실", "여성축구", "화목금", "10:00", "12:00", "강서구민", "우장산 인조잔디구장", "/fmcs/103", ("여성축구", "여성", "1월 ∼ 12월", "10:00∼12:00", "우장산 인조잔디구장"), "jan_dec"),
    "00023": _spec("00023", "인공암벽교실1", "청소년방학교실", "인공암벽", "월화수목금", "10:00", "11:00", "관내 초등학생", "강서클라이밍센터", "/fmcs/104", ("인공암벽A,B", "강서클라이밍센터"), "summer_notice", address="서울특별시 강서구 내발산동 646, 4층", notice_signatures=("인공암벽", "10:00 ~ 11:00", "강서클라이밍센터"), extra_fee="장비사용료 30,000원(현장납부)"),
    "00022": _spec("00022", "인공암벽교실2", "청소년방학교실", "인공암벽", "월화수목금", "11:30", "12:30", "관내 초등학생", "강서클라이밍센터", "/fmcs/104", ("인공암벽A,B", "강서클라이밍센터"), "summer_notice", address="서울특별시 강서구 내발산동 646, 4층", notice_signatures=("인공암벽", "11:30 ~ 12:30", "강서클라이밍센터"), extra_fee="장비사용료 30,000원(현장납부)"),
    "00024": _spec("00024", "인공암벽교실3", "청소년방학교실", "인공암벽", "월화수목금", "13:00", "14:00", "관내 청소년(중·고등학생)", "강서클라이밍센터", "/fmcs/104", ("인공암벽C", "강서클라이밍센터"), "summer_notice", address="서울특별시 강서구 내발산동 646, 4층", notice_signatures=("인공암벽", "13:00 ~ 14:00", "강서클라이밍센터"), extra_fee="장비사용료 30,000원(현장납부)"),
    "00029": _spec("00029", "탁구1", "청소년방학교실", "탁구", "월수금", "14:00", "15:00", "관내 초등학생(4~6학년),중학생", "가양레포츠센터", "/fmcs/104", ("탁구A", "가양레포츠센터"), "summer_notice", address="서울특별시 강서구 가양동 1493", notice_signatures=("탁 구", "월, 수, 금", "14:00 ~ 15:00", "가양레포츠센터")),
    "00028": _spec("00028", "탁구2", "청소년방학교실", "탁구", "화목", "14:00", "15:00", "관내 초등학생(4~6학년),중학생", "가양레포츠센터", "/fmcs/104", ("탁구B", "가양레포츠센터"), "summer_notice", address="서울특별시 강서구 가양동 1493", notice_signatures=("탁 구", "화, 목", "14:00 ~ 15:00", "가양레포츠센터")),
    "00049": _spec("00049", "아이스 스케이트", "청소년방학교실", "아이스스케이트", "월화수목금", "12:00", "13:00", "관내 초등학생", "아이스온", "/fmcs/104", ("아이스 스케이트", "아이스온"), "summer_notice", address="서울특별시 강서구 내발산동 646-1, 지하 1층", notice_signatures=("아이스 스케이트", "12:00 ~ 13:00", "아이스온"), extra_fee="장비사용료 30,000원(현장납부)"),
    "00040": _spec("00040", "볼링1", "청소년방학교실", "볼링", "월화수목금", "10:00", "11:00", "관내 청소년(4학년 이상)", "KBS스포츠월드 볼링장", "/fmcs/104", ("볼링A,B", "KBS스포츠월드 볼링장"), "summer_notice", address="서울특별시 강서구 화곡동 1093-76", notice_signatures=("볼 링", "10:00 ~ 11:00", "KBS 스포츠월드 볼링장"), extra_fee="장비사용료 40,000원(현장납부)"),
    "00039": _spec("00039", "볼링2", "청소년방학교실", "볼링", "월화수목금", "11:00", "12:00", "관내 청소년(4학년 이상)", "KBS스포츠월드 볼링장", "/fmcs/104", ("볼링A,B", "KBS스포츠월드 볼링장"), "summer_notice", address="서울특별시 강서구 화곡동 1093-76", notice_signatures=("볼 링", "11:00 ~ 12:00", "KBS 스포츠월드 볼링장"), extra_fee="장비사용료 40,000원(현장납부)"),
    "00045": _spec("00045", "음악줄넘기", "청소년방학교실", "댄스", "월화수목금", "13:00", "14:00", "관내 초등학생(1~4학년)", "로뎀태권도", "/fmcs/104", ("음악줄넘기", "로뎀태권도"), "summer_notice", address="서울특별시 강서구 내발산동 663-10, 2층", notice_signatures=("음악줄넘기", "13:00 ∼ 14:00", "로뎀태권도")),
    "00044": _spec("00044", "여아풋살", "청소년방학교실", "풋살", "월화수목금", "13:00", "14:00", "관내 초등학생(여아)", "청오축구", "/fmcs/104", ("여아풋살", "청오축구"), "summer_notice", address="서울특별시 강서구 등촌동 656-54, 2층", notice_signatures=("여아풋살", "13:00 ~ 14:00", "청오축구")),
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _compact(value: Any) -> str:
    return _SAFE_TEXT_RE.sub("", _clean(value)).lower()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": GANGSEO_SPORTS_URL,
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not getattr(response, "content", b""):
        raise ValueError("empty HTTP response")
    return response


def _coerce_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    json_method = getattr(value, "json", None)
    if callable(json_method):
        return json_method()
    if isinstance(value, bytes):
        import json

        return json.loads(value.decode("utf-8"))
    if isinstance(value, str):
        import json

        return json.loads(value)
    raise TypeError("fetcher did not return JSON or an HTTP response")


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return HTML or an HTTP response")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def is_gangseo_sports_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == GANGSEO_SPORTS_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.hostname is not None
        and parsed.hostname.rstrip(".").lower() == GANGSEO_SPORTS_HOST
        and parsed.netloc.lower() == GANGSEO_SPORTS_HOST
        and parsed.path == GANGSEO_SPORTS_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_gangseo_sports_target


def gangseo_detail_url(comcd: str, class_cd: str, status: str) -> str:
    company = _clean(comcd)
    identity = _clean(class_cd)
    state = _clean(status).upper()
    if company != GANGSEO_COMPANY_CODE or not _CLASS_CODE_RE.fullmatch(identity):
        return ""
    if state not in {"E", "R", "RW", "W"}:
        return ""
    query = urlencode(
        (("action", "read"), ("comcd", company), ("classcd", identity), ("type", state))
    )
    return f"{GANGSEO_SPORTS_URL}?{query}"


def _safe_detail_url(value: Any, *, comcd: str, class_cd: str, status: str) -> bool:
    parsed = urlparse(_clean(value))
    return (
        parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGSEO_SPORTS_HOST
        and parsed.path == GANGSEO_SPORTS_PATH
        and not parsed.params
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True)
        == {
            "action": ["read"],
            "comcd": [comcd],
            "classcd": [class_cd],
            "type": [status],
        }
    )


def _safe_notice_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGSEO_SPORTS_HOST
        and parsed.path == "/fmcs/30"
        and not parsed.params
        and not parsed.fragment
        and set(query) == {"action", "action-value"}
        and query.get("action") == ["read"]
        and len(query.get("action-value", [])) == 1
        and _NOTICE_ID_RE.fullmatch(query["action-value"][0]) is not None
    )


def _pairs_from_detail(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in soup.select("table tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        for index, cell in enumerate(cells):
            if cell.name != "th":
                continue
            for value_cell in cells[index + 1 :]:
                if value_cell.name == "td":
                    key = _clean(cell.get_text(" ", strip=True))
                    value = _clean(value_cell.get_text(" ", strip=True))
                    if key and value and key not in pairs:
                        pairs[key] = value
                    break
    return pairs


def _hidden_fields(soup: BeautifulSoup) -> dict[str, str]:
    return {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in soup.select("input[name]")
        if _clean(node.get("name"))
    }


def _int(value: Any) -> Optional[int]:
    text = _clean(value).replace(",", "")
    if not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def _period_segments(
    spec: ScheduleSpec,
    year: int,
    summer_range: tuple[date, date],
) -> tuple[tuple[date, date], ...]:
    if spec.period_kind == "park_spring":
        return ((date(year, 4, 1), date(year, 5, 31)),)
    if spec.period_kind == "spring_fall":
        return (
            (date(year, 4, 1), date(year, 6, 30)),
            (date(year, 9, 1), date(year, 11, 30)),
        )
    if spec.period_kind == "apr_nov":
        return ((date(year, 4, 1), date(year, 11, 30)),)
    if spec.period_kind == "jan_dec":
        return ((date(year, 1, 1), date(year, 12, 31)),)
    if spec.period_kind == "summer_notice":
        return (summer_range,)
    return ()


def _period_text(segments: tuple[tuple[date, date], ...]) -> str:
    return " / ".join(f"{start.isoformat()} ~ {end.isoformat()}" for start, end in segments)


def _status(value: Any) -> tuple[str, bool]:
    code = _clean(value).upper()
    return {
        "R": ("OPEN", True),
        "RW": ("WAITLIST", True),
        "W": ("SCHEDULED", False),
        "E": ("CLOSED", False),
    }.get(code, ("", False))


def _branch_code(venue: str) -> str:
    digest = hashlib.sha1(_clean(venue).encode("utf-8")).hexdigest()[:12].upper()
    return f"GANGSEO04_{digest}"


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _meta(
    *,
    rows: list[dict[str, Any]],
    pages: int,
    request_count: int,
    raw_count: int,
    declared_count: int,
    unique_count: int,
    expired_count: int,
    detail_attempts: int,
    detail_verified: int,
    enrichment_pages: int,
    source_cap_reached: bool,
    errors: list[str],
    detail_errors: list[str],
    enrichment_errors: list[str],
    source_year: Optional[int],
) -> dict[str, Any]:
    detail_required = GANGSEO_EXPECTED_DECLARED_COUNT
    details_complete = (
        detail_attempts == detail_required
        and detail_verified == detail_required
        and not detail_errors
    )
    all_errors = list(dict.fromkeys([*errors, *enrichment_errors, *detail_errors]))
    snapshot_complete = (
        not all_errors
        and not source_cap_reached
        and declared_count == GANGSEO_EXPECTED_DECLARED_COUNT
        and raw_count == GANGSEO_EXPECTED_DECLARED_COUNT
        and unique_count == GANGSEO_EXPECTED_DECLARED_COUNT
        and enrichment_pages == 5
        and details_complete
    )
    no_current_data = snapshot_complete and not rows
    result: dict[str, Any] = {
        "pages": pages,
        "api_pages": pages,
        "request_count": request_count,
        "detail_pages": detail_verified,
        "detail_attempts": detail_attempts,
        "detail_required_count": detail_required,
        "required_detail_count": detail_required,
        "detail_verified_count": detail_verified,
        "detail_exempt_count": 0,
        "official_list_only_exempt_count": 0,
        "detail_errors": len(detail_errors),
        "detail_error_messages": detail_errors,
        "enrichment_pages": enrichment_pages,
        "enrichment_errors": len(enrichment_errors),
        "enrichment_error_messages": enrichment_errors,
        "total_count": declared_count,
        "declared_count": declared_count,
        "raw_row_count": raw_count,
        "unique_id_count": unique_count,
        "expected_declared_count": GANGSEO_EXPECTED_DECLARED_COUNT,
        "expired_count": expired_count,
        "current_count": len(rows),
        "source_year": source_year,
        "page_size": GANGSEO_LIST_PAGE_SIZE,
        "pagination_detected": declared_count > GANGSEO_LIST_PAGE_SIZE,
        "pagination_complete": snapshot_complete,
        "pagination_exhausted": snapshot_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "no_current_data": no_current_data,
        "no_current_reason": "official dated sports-education snapshot has no current/future rows" if no_current_data else "",
        "recursion_depth": 0,
        "branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
    }
    if all_errors:
        result["configured_collection_error"] = "; ".join(all_errors)
    return result


def collect_gangseo_sports_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 2,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | str] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return the complete current/future Gangseo sports-education snapshot."""

    current_fetcher = fetcher or _default_fetcher
    current_session_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _default_dedupe
    reference_date = _today(today)

    pages = 0
    request_count = 0
    raw_count = 0
    declared_count = 0
    unique_count = 0
    expired_count = 0
    detail_attempts = 0
    detail_verified = 0
    enrichment_pages = 0
    source_cap_reached = False
    source_year: Optional[int] = None
    errors: list[str] = []
    detail_errors: list[str] = []
    enrichment_errors: list[str] = []
    current_rows: list[dict[str, Any]] = []

    def finish() -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        publishable = current_rows if not (errors or detail_errors or enrichment_errors or source_cap_reached) else []
        meta = _meta(
            rows=publishable,
            pages=pages,
            request_count=request_count,
            raw_count=raw_count,
            declared_count=declared_count,
            unique_count=unique_count,
            expired_count=expired_count,
            detail_attempts=detail_attempts,
            detail_verified=detail_verified,
            enrichment_pages=enrichment_pages,
            source_cap_reached=source_cap_reached,
            errors=errors,
            detail_errors=detail_errors,
            enrichment_errors=enrichment_errors,
            source_year=source_year,
        )
        if not meta["snapshot_complete"]:
            publishable = []
        return publishable, GANGSEO_PARSER, meta

    if not is_gangseo_sports_target(target):
        errors.append("target does not match the exact Gangseo sports provider route")
        return finish()
    if int(max_pages) < 1:
        source_cap_reached = True
        errors.append("max_pages cap prevents the required lecture API request")
        return finish()
    if int(detail_limit) < GANGSEO_EXPECTED_DECLARED_COUNT:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap {int(detail_limit)} is below required {GANGSEO_EXPECTED_DECLARED_COUNT}"
        )
        return finish()

    current_session = current_session_factory()

    def fetch_json(url: str) -> Any:
        nonlocal request_count
        request_count += 1
        return _coerce_json(current_fetcher(current_session, url, timeout))

    def fetch_soup(url: str) -> BeautifulSoup:
        nonlocal request_count
        request_count += 1
        return _coerce_soup(current_fetcher(current_session, url, timeout))

    try:
        # The source contract starts with the official company fan-out.
        try:
            company_url = f"{GANGSEO_COMPANY_API}?{urlencode({'type': 'L'})}"
            companies = fetch_json(company_url)
        except Exception as exc:
            errors.append(f"company API fetch failed: {type(exc).__name__}")
            return finish()
        company_rows = [row for row in companies if isinstance(row, Mapping)] if isinstance(companies, list) else []
        company_map = {
            _clean(row.get("comcd")): _clean(row.get("comnm"))
            for row in company_rows
            if _clean(row.get("comcd"))
        }
        if company_map != {GANGSEO_COMPANY_CODE: GANGSEO_COMPANY_NAME}:
            errors.append("official company API no longer exposes exactly GANGSEO04 생활체육프로그램")
            return finish()

        list_params = {
            "company_code": GANGSEO_COMPANY_CODE,
            "page": "1",
            "page_size": str(GANGSEO_LIST_PAGE_SIZE),
        }
        try:
            list_payload = fetch_json(f"{GANGSEO_LECTURE_API}?{urlencode(list_params)}")
        except Exception as exc:
            errors.append(f"lecture API fetch failed: {type(exc).__name__}")
            return finish()
        pages = 1
        items = [row for row in list_payload if isinstance(row, Mapping)] if isinstance(list_payload, list) else []
        raw_count = len(items)
        totals = {_int(row.get("total_count")) for row in items}
        if len(totals) != 1 or None in totals:
            errors.append("lecture API omitted or changed its declared total_count")
            return finish()
        declared_count = next(iter(totals)) or 0
        required_pages = max(1, math.ceil(declared_count / GANGSEO_LIST_PAGE_SIZE))
        if required_pages > int(max_pages):
            source_cap_reached = True
            errors.append(f"max_pages cap {int(max_pages)} is below declared {required_pages} pages")
            return finish()
        if declared_count != GANGSEO_EXPECTED_DECLARED_COUNT:
            errors.append(
                f"declared lecture count drifted from {GANGSEO_EXPECTED_DECLARED_COUNT} to {declared_count}"
            )
            return finish()
        if raw_count != declared_count:
            errors.append(
                f"page_size={GANGSEO_LIST_PAGE_SIZE} exposed {raw_count} of declared {declared_count} rows"
            )
            return finish()

        identities: list[tuple[str, str]] = []
        items_by_class: dict[str, Mapping[str, Any]] = {}
        for item in items:
            comcd = _clean(item.get("comcd"))
            class_cd = _clean(item.get("class_cd"))
            identities.append((comcd, class_cd))
            if comcd == GANGSEO_COMPANY_CODE and _CLASS_CODE_RE.fullmatch(class_cd):
                items_by_class[class_cd] = item
        unique_count = len(set(identities))
        if unique_count != declared_count or len(items_by_class) != declared_count:
            errors.append("lecture API has duplicate, invalid or foreign (comcd,class_cd) identities")
            return finish()
        if set(items_by_class) != set(GANGSEO_SCHEDULE_SPECS):
            missing = sorted(set(items_by_class) - set(GANGSEO_SCHEDULE_SPECS))
            retired = sorted(set(GANGSEO_SCHEDULE_SPECS) - set(items_by_class))
            errors.append(f"audited class-code set drifted; unreviewed={missing}, missing={retired}")
            return finish()

        # Fetch and verify all three official schedule pages.
        schedule_texts: dict[str, str] = {}
        for source_path, source_url in GANGSEO_SCHEDULE_URLS.items():
            try:
                soup = fetch_soup(source_url)
                schedule_texts[source_path] = _clean(soup.get_text(" ", strip=True))
                enrichment_pages += 1
            except Exception as exc:
                enrichment_errors.append(
                    f"schedule {source_path} fetch failed: {type(exc).__name__}"
                )
        if enrichment_errors:
            return finish()
        expected_headings = {
            "/fmcs/102": "생활체육교실 이용안내",
            "/fmcs/103": "어린이축구 · 청소년풋살 · 여성축구 · 배구교실 이용안내",
            "/fmcs/104": "여름방학 청소년 체육교실",
        }
        for path, heading in expected_headings.items():
            if _compact(heading) not in _compact(schedule_texts.get(path)):
                enrichment_errors.append(f"schedule {path} lost heading {heading}")
        year_match = re.search(r"(?<!\d)(20\d{2})년\s*여름방학\s*청소년\s*체육교실", schedule_texts["/fmcs/104"])
        if not year_match:
            enrichment_errors.append("/fmcs/104 no longer declares the summer-school year")
            return finish()
        source_year = int(year_match.group(1))
        if source_year != reference_date.year:
            enrichment_errors.append(
                f"official schedule year {source_year} is stale for collection year {reference_date.year}"
            )
            return finish()

        # Discover the dated current-year notice from the official notice board.
        try:
            notice_list = fetch_soup(GANGSEO_NOTICE_URL)
            enrichment_pages += 1
        except Exception as exc:
            enrichment_errors.append(f"notice list fetch failed: {type(exc).__name__}")
            return finish()
        wanted_notice_title = f"{source_year}년 여름방학 청소년 체육교실 모집 안내"
        notice_candidates: list[str] = []
        for anchor in notice_list.select("a[href]"):
            if _compact(anchor.get_text(" ", strip=True)) != _compact(wanted_notice_title):
                continue
            candidate = urljoin(GANGSEO_NOTICE_URL, _clean(anchor.get("href")))
            if _safe_notice_url(candidate):
                notice_candidates.append(candidate)
        notice_candidates = list(dict.fromkeys(notice_candidates))
        if len(notice_candidates) != 1:
            enrichment_errors.append("official notice list did not expose exactly one current summer recruitment notice")
            return finish()
        notice_url = notice_candidates[0]
        try:
            notice_soup = fetch_soup(notice_url)
            enrichment_pages += 1
        except Exception as exc:
            enrichment_errors.append(f"summer notice fetch failed: {type(exc).__name__}")
            return finish()
        notice_text = _clean(notice_soup.get_text(" ", strip=True))
        if _compact(wanted_notice_title) not in _compact(notice_text):
            enrichment_errors.append("summer notice title/detail mismatch")
            return finish()
        range_counts: Counter[tuple[int, int, int, int]] = Counter(
            tuple(int(part) for part in match.groups())
            for match in _MONTH_DAY_RANGE_RE.finditer(notice_text)
        )
        if not range_counts:
            enrichment_errors.append("summer notice has no explicit operating date range")
            return finish()
        most_common = range_counts.most_common()
        summer_tuple, summer_occurrences = most_common[0]
        summer_class_count = sum(
            spec.period_kind == "summer_notice" for spec in GANGSEO_SCHEDULE_SPECS.values()
        )
        if summer_occurrences != summer_class_count or (
            len(most_common) > 1 and most_common[1][1] == summer_occurrences
        ):
            enrichment_errors.append(
                "summer notice does not give one unambiguous date range for all ten classes"
            )
            return finish()
        try:
            summer_range = (
                date(source_year, summer_tuple[0], summer_tuple[1]),
                date(source_year, summer_tuple[2], summer_tuple[3]),
            )
        except ValueError:
            enrichment_errors.append("summer notice contains an invalid operating date")
            return finish()
        if summer_range[1] < summer_range[0]:
            enrichment_errors.append("summer notice operating range is reversed")
            return finish()

        # Validate list metadata and its reviewed official-schedule evidence.
        staged_rows: list[dict[str, Any]] = []
        for class_cd, spec in GANGSEO_SCHEDULE_SPECS.items():
            item = items_by_class[class_cd]
            checks = {
                "title": (_clean(item.get("class_nm")), spec.title),
                "category1": (_clean(item.get("category1")), spec.category1),
                "category2": (_clean(item.get("category2")), spec.category2),
                "train_day": (_clean(item.get("train_day_nm")), spec.train_day),
                "start_time": (_clean(item.get("train_stime")), spec.start_time),
                "end_time": (_clean(item.get("train_etime")), spec.end_time),
                "target": (_clean(item.get("target_age_name")), spec.target),
                "company_name": (_clean(item.get("comnm")), GANGSEO_COMPANY_NAME),
            }
            mismatches = [name for name, (actual, expected) in checks.items() if _compact(actual) != _compact(expected)]
            status_text, reservation_available = _status(item.get("status"))
            if mismatches or not status_text:
                errors.append(f"class {class_cd} list contract mismatch: {','.join(mismatches) or 'status'}")
                continue
            source_text = schedule_texts.get(spec.source_path, "")
            missing_source = [sig for sig in spec.source_signatures if _compact(sig) not in _compact(source_text)]
            if missing_source:
                enrichment_errors.append(
                    f"class {class_cd} lost schedule evidence: {','.join(missing_source)}"
                )
                continue
            missing_notice = [sig for sig in spec.notice_signatures if _compact(sig) not in _compact(notice_text)]
            if missing_notice:
                enrichment_errors.append(
                    f"class {class_cd} lost dated notice evidence: {','.join(missing_notice)}"
                )
                continue
            segments = _period_segments(spec, source_year, summer_range)
            if not segments or any(end < start for start, end in segments):
                enrichment_errors.append(f"class {class_cd} has no validated explicit date range")
                continue
            detail_url = gangseo_detail_url(
                GANGSEO_COMPANY_CODE, class_cd, _clean(item.get("status")).upper()
            )
            if not detail_url or not _safe_detail_url(
                detail_url,
                comcd=GANGSEO_COMPANY_CODE,
                class_cd=class_cd,
                status=_clean(item.get("status")).upper(),
            ):
                errors.append(f"class {class_cd} could not construct a safe official detail URL")
                continue
            current_count = _int(item.get("reg_person"))
            capacity_total = _int(item.get("capa"))
            fee_value = _int(item.get("course_fee"))
            fee = "무료" if fee_value == 0 else (f"{fee_value:,}원" if fee_value is not None else "")
            period = _period_text(segments)
            schedule = f"{spec.train_day} {spec.start_time}~{spec.end_time}"
            row: dict[str, Any] = {
                "provider": GANGSEO_SPORTS_PROVIDER,
                "provider_course_id": f"{GANGSEO_SPORTS_PROVIDER}:{GANGSEO_COMPANY_CODE}:{class_cd}",
                "prefer_incoming_provider_course_id": True,
                "title": spec.title,
                "branch": spec.venue,
                "branch_code": _branch_code(spec.venue),
                "branch_url": GANGSEO_SCHEDULE_URLS[spec.source_path],
                "preserve_branch": True,
                "venue_name": spec.venue,
                "room": spec.venue,
                "address": spec.address,
                "venue_address": spec.address,
                "category": f"{spec.category1} > {spec.category2}",
                "program_type": "강좌",
                "raw_url": detail_url,
                "reservation_available": reservation_available,
                "status": status_text,
                "fee": fee,
                "material_fee": spec.extra_fee,
                "period": period,
                "start_date": min(start for start, _end in segments).isoformat(),
                "end_date": max(end for _start, end in segments).isoformat(),
                "schedule_raw": schedule,
                "target": spec.target,
                "capacity": (
                    f"{current_count}/{capacity_total}"
                    if current_count is not None and capacity_total is not None
                    else ""
                ),
                "capacity_current": current_count,
                "capacity_total": capacity_total,
                "instructor": _clean(item.get("teacher_name")),
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "sports_facility",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "municipality_code": GANGSEO_MUNICIPALITY_CODE,
                "municipality_full_name": GANGSEO_MUNICIPALITY_NAME,
                "collection_type": "complete_company_api+official_schedule+full_detail",
                "description": _clean(
                    " ".join(
                        value
                        for value in (
                            spec.title,
                            spec.venue,
                            period,
                            schedule,
                            spec.target,
                            fee,
                            spec.extra_fee,
                        )
                        if value
                    )
                ),
                "raw_fields": {
                    "parser": GANGSEO_PARSER,
                    "official_company_code": GANGSEO_COMPANY_CODE,
                    "official_class_code": class_cd,
                    "stable_key": f"{GANGSEO_COMPANY_CODE}|{class_cd}",
                    "source_status": _clean(item.get("status")).upper(),
                    "source_schedule_url": GANGSEO_SCHEDULE_URLS[spec.source_path],
                    "source_notice_url": notice_url if spec.period_kind == "summer_notice" else "",
                    "period_segments": [
                        {"start": start.isoformat(), "end": end.isoformat()}
                        for start, end in segments
                    ],
                    "list_item": dict(item),
                    "detail_required": True,
                },
            }
            if reservation_available:
                row["application_url"] = detail_url
                row["application_type"] = "ONLINE_LOGIN_REQUIRED"
            else:
                row["raw_fields"]["clear_application_url"] = True
            staged_rows.append(_clean_row(row))
        if errors or enrichment_errors or len(staged_rows) != declared_count:
            return finish()

        # Detail validation is all-or-nothing, including the expired source row.
        for row in staged_rows:
            class_cd = _clean(row["raw_fields"]["official_class_code"])
            item = items_by_class[class_cd]
            detail_attempts += 1
            try:
                detail_soup = fetch_soup(_clean(row.get("raw_url")))
            except Exception as exc:
                detail_errors.append(f"class {class_cd} detail fetch failed: {type(exc).__name__}")
                continue
            pairs = _pairs_from_detail(detail_soup)
            hidden = _hidden_fields(detail_soup)
            # The official detail for class 00017 deliberately leaves
            # ``접수방식`` blank while still exposing the authoritative hidden
            # status and a closed capacity cell.  Identity/status remain fully
            # verifiable, so the optional prose field is not a detail exemption.
            required_pairs = ("강좌명", "운영센터", "시간/요일", "교육대상", "신청인원/정원")
            missing_pairs = [key for key in required_pairs if not _clean(pairs.get(key))]
            detail_status = _clean(item.get("status")).upper()
            detail_mismatches: list[str] = []
            if missing_pairs:
                detail_mismatches.append(f"missing={','.join(missing_pairs)}")
            if _compact(pairs.get("강좌명")) != _compact(item.get("class_nm")):
                detail_mismatches.append("title")
            if _compact(GANGSEO_COMPANY_NAME) not in _compact(pairs.get("운영센터")):
                detail_mismatches.append("company")
            detail_schedule = _compact(pairs.get("시간/요일"))
            for token in (
                _clean(item.get("train_stime")),
                _clean(item.get("train_etime")),
                _clean(item.get("train_day_nm")),
            ):
                if _compact(token) not in detail_schedule:
                    detail_mismatches.append(f"schedule:{token}")
            if _compact(pairs.get("교육대상")) != _compact(item.get("target_age_name")):
                detail_mismatches.append("target")
            expected_hidden = {
                "comcd": GANGSEO_COMPANY_CODE,
                "classcd": class_cd,
                "type": detail_status,
                "status": detail_status,
            }
            for key, expected in expected_hidden.items():
                if _clean(hidden.get(key)) != expected:
                    detail_mismatches.append(f"hidden:{key}")
            if detail_mismatches:
                detail_errors.append(
                    f"class {class_cd} detail contract mismatch: {','.join(detail_mismatches)}"
                )
                continue
            row.update(
                {
                    "phone": _clean(pairs.get("운영센터")).split("/")[-1].strip(),
                    "contact": _clean(pairs.get("운영센터")).split("/")[-1].strip(),
                    "application_method_raw": _clean(pairs.get("접수방식")),
                    "instructor": _clean(pairs.get("강사명")) or row.get("instructor"),
                }
            )
            row["raw_fields"]["detail_pairs"] = pairs
            row["raw_fields"]["detail_identity_verified"] = True
            row["raw_fields"]["detail_status_verified"] = True
            detail_verified += 1
        if detail_errors or detail_verified != GANGSEO_EXPECTED_DECLARED_COUNT:
            return finish()

        for row in staged_rows:
            end_dates = [
                date.fromisoformat(segment["end"])
                for segment in row["raw_fields"]["period_segments"]
            ]
            if max(end_dates) < reference_date:
                expired_count += 1
            else:
                current_rows.append(row)

        try:
            deduped = list(current_dedupe(current_rows))
        except Exception as exc:
            errors.append(f"dedupe helper failed: {type(exc).__name__}")
            return finish()
        deduped_ids = [_clean(row.get("provider_course_id")) for row in deduped]
        if (
            len(deduped) != len(current_rows)
            or len(set(deduped_ids)) != len(deduped_ids)
            or any(not identity for identity in deduped_ids)
        ):
            errors.append("dedupe changed or duplicated the audited current snapshot")
            return finish()
        current_rows[:] = deduped
        return finish()
    finally:
        _close_quietly(current_session)


# Aliases kept intentionally small for the parent crawler's dispatch wrapper.
collect_gangseo_sports = collect_gangseo_sports_courses
collect_gangseo_public_sports_education = collect_gangseo_sports_courses


__all__ = [
    "GANGSEO_SPORTS_PROVIDER",
    "GANGSEO_SPORTS_URL",
    "GANGSEO_PARSER",
    "GANGSEO_COMPANY_CODE",
    "GANGSEO_EXPECTED_DECLARED_COUNT",
    "GANGSEO_SCHEDULE_SPECS",
    "is_gangseo_sports_target",
    "is_target",
    "gangseo_detail_url",
    "collect_gangseo_sports_courses",
    "collect_gangseo_sports",
    "collect_gangseo_public_sports_education",
]
