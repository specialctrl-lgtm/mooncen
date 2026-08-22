"""Fail-closed public catalogue collectors for Incheon Facilities Corporation.

The official integrated-reservation site has two independent public catalogue
surfaces.  Education is split across three legacy ``programInfoList``
partitions.  Experience is split across four one-page lists.  The remaining
education menu entries redirect through SSO to member-only catalogues and are
deliberately outside this collector.

Only catalogue and detail pages are fetched.  Public schedule URLs may be
reported when an exact item-bound control is present, but schedule, applicant,
agreement, SSO, login, My Page, download, and application endpoints are never
requested by either collector.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from calendar import monthrange
import re
import time
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


INSISEOL_PROVIDER = "MUNI_RESERVE_INSISEOL_OR_KR_EC4B7776"
INSISEOL_HOST = "reserve.insiseol.or.kr"
INSISEOL_EDUCATION_CANONICAL_URL = "https://reserve.insiseol.or.kr/lecture/apply_intro.jsp"
INSISEOL_EXPERIENCE_CANONICAL_URL = "https://reserve.insiseol.or.kr/experience/apply_intro.jsp"
INSISEOL_CRAWL_DELAY_SECONDS = 0.25
INSISEOL_COURSE_PAGE_SIZE = 10
INSISEOL_EDUCATION_PARSER = (
    "insiseol_fixed_3_public_course_partitions+declared_pages+"
    "clamped_last_and_first_recheck+all_current_details+education_end_filter+"
    "no_sso_or_application_calls"
)
INSISEOL_EXPERIENCE_PARSER = (
    "insiseol_fixed_4_public_experience_lists+declared_totals+"
    "normalized_page2_and_first_recheck+all_details+operation_currentness+"
    "item_region_lock+no_schedule_auth_or_application_calls"
)

INSISEOL_MUNICIPALITIES: dict[str, tuple[str, str]] = {
    "yeongjong": ("2815500000", "인천광역시 영종구"),
    "yeonsu": ("2818500000", "인천광역시 연수구"),
    "namdong": ("2820000000", "인천광역시 남동구"),
    "gyeyang": ("2824500000", "인천광역시 계양구"),
    "seohae": ("2827500000", "인천광역시 서해구"),
}
INSISEOL_COVERED_MUNICIPALITIES: tuple[dict[str, str], ...] = (
    {"code": "2815500000", "sido": "인천광역시", "sigungu": "영종구", "full_name": "인천광역시 영종구"},
    {"code": "2818500000", "sido": "인천광역시", "sigungu": "연수구", "full_name": "인천광역시 연수구"},
    {"code": "2820000000", "sido": "인천광역시", "sigungu": "남동구", "full_name": "인천광역시 남동구"},
    {"code": "2824500000", "sido": "인천광역시", "sigungu": "계양구", "full_name": "인천광역시 계양구"},
    {"code": "2827500000", "sido": "인천광역시", "sigungu": "서해구", "full_name": "인천광역시 서해구"},
)


@dataclass(frozen=True)
class InsiseolCoursePartition:
    code: str
    name: str
    institution: str
    municipality_key: str
    prgmdiv: str
    cate2: str = ""

    @property
    def query(self) -> tuple[tuple[str, str], ...]:
        values = [("prgmdiv", self.prgmdiv)]
        if self.cate2:
            values.append(("cate2", self.cate2))
        return tuple(values)


INSISEOL_COURSE_PARTITIONS: tuple[InsiseolCoursePartition, ...] = (
    InsiseolCoursePartition("child", "인천어린이과학관", "어린이과학관", "gyeyang", "child"),
    InsiseolCoursePartition(
        "youth_academy",
        "청소년문화아카데미",
        "청소년수련관",
        "namdong",
        "youthtraining",
        "441",
    ),
    InsiseolCoursePartition(
        "youth_program",
        "청소년프로그램",
        "청소년수련관",
        "namdong",
        "youthtraining",
        "362",
    ),
)
INSISEOL_COURSE_PARTITION_BY_CODE = {item.code: item for item in INSISEOL_COURSE_PARTITIONS}


@dataclass(frozen=True)
class InsiseolExperienceLeaf:
    code: str
    name: str
    path: str
    inst_cd: str = ""

    @property
    def query(self) -> tuple[tuple[str, str], ...]:
        return (("inst_cd", self.inst_cd),) if self.inst_cd else ()


INSISEOL_EXPERIENCE_LEAVES: tuple[InsiseolExperienceLeaf, ...] = (
    InsiseolExperienceLeaf("songdopark", "송도공원사업단", "/see/seeInfoList.do", "songdopark"),
    InsiseolExperienceLeaf("seaside", "씨사이드파크", "/see/seeInfoList.do", "seaside"),
    InsiseolExperienceLeaf("childsee", "인천어린이과학관", "/childsee/childSeeInfoList.do"),
    InsiseolExperienceLeaf("chongnapark", "청라공원", "/see/seeInfoList.do", "chongnapark"),
)
INSISEOL_EXPERIENCE_LEAF_BY_CODE = {item.code: item for item in INSISEOL_EXPERIENCE_LEAVES}

# Current public identities have audited row-level geography.  New identities
# fail closed until their official facility/location evidence is reviewed.
INSISEOL_EXPERIENCE_ITEM_REGIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("songdopark", "23"): ("seohae", "인천아시아드주경기장 RC 보조경기장"),
    ("songdopark", "22"): ("seohae", "인천아시아드주경기장 RC 주경기장"),
    ("songdopark", "6"): ("yeonsu", "송도공원 생태교육관"),
    ("seaside", "2"): ("yeongjong", "씨사이드파크 염전"),
    ("seaside", "19"): ("yeongjong", "씨사이드파크 염전"),
    ("seaside", "3"): ("yeongjong", "씨사이드파크 영종진"),
    ("seaside", "18"): ("yeongjong", "씨사이드파크 영종진"),
    ("childsee", "22"): ("gyeyang", "인천어린이과학관 4D영상관"),
    ("childsee", "21"): ("gyeyang", "인천어린이과학관 4D영상관"),
    ("chongnapark", "21"): ("seohae", "청라 노을공원"),
    ("chongnapark", "8"): ("seohae", "청라 노을공원"),
}


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_PROGRAM_ID_RE = re.compile(r"\d{1,12}\Z")
_EXPERIENCE_ID_RE = re.compile(r"\d{1,12}\Z")
_COURSE_SOURCE_STATUSES = frozenset(
    {
        "사전접수중",
        "사전접수마감",
        "정시접수대기",
        "정시접수중",
        "정시접수마감",
        "추가접수대기",
        "추가접수중",
        "추가접수마감",
        "접수대기",
        "접수중",
        "접수마감",
    }
)
_COURSE_LIST_REQUIRED_KEYS = frozenset(
    {"정시접수", "교육분야", "교육일정", "교육시간", "수강료", "수강정원", "접수구분"}
)
_COURSE_DETAIL_REQUIRED_KEYS = frozenset(
    {
        "교육기관",
        "분야",
        "정원",
        "대기",
        "정시 접수",
        "교육 대상",
        "교육기간",
        "교육 요일",
        "교육 시간",
        "수강료",
        "강의실",
        "문의처",
    }
)
_EXPERIENCE_DETAIL_REQUIRED_KEYS = frozenset({"관람 인원", "문의처", "기본 요금", "관람시간"})


class InsiseolHostPacer:
    """Serialize polite requests across the two sibling collectors."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_request_at = 0.0

    def wait(
        self,
        interval_seconds: float,
        *,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        interval = max(0.0, float(interval_seconds))
        if interval == 0:
            return
        with self._lock:
            now = float(monotonic_fn())
            delay = max(0.0, self._next_request_at - now)
            if delay:
                sleep_fn(delay)
                now = float(monotonic_fn())
            self._next_request_at = max(now, self._next_request_at) + interval


INSISEOL_HOST_PACER = InsiseolHostPacer()


def insiseol_paced_fetcher(
    fetcher: Fetcher,
    *,
    delay_seconds: float,
    pacer: InsiseolHostPacer = INSISEOL_HOST_PACER,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Fetcher:
    def paced(current_session: Any, url: str, timeout: int) -> Any:
        pacer.wait(
            delay_seconds,
            monotonic_fn=monotonic_fn,
            sleep_fn=sleep_fn,
        )
        return fetcher(current_session, url, timeout)

    return paced


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _exact_target(target: Any, expected_path: str) -> bool:
    parsed = urlparse(_clean(_target_value(target, "url")))
    return (
        _clean(_target_value(target, "provider")) == INSISEOL_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == INSISEOL_HOST
        and parsed.port is None
        and parsed.path == expected_path
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_insiseol_education_target(target: Any) -> bool:
    return _exact_target(target, "/lecture/apply_intro.jsp")


def is_insiseol_experience_target(target: Any) -> bool:
    return _exact_target(target, "/experience/apply_intro.jsp")


def insiseol_course_list_url(partition: InsiseolCoursePartition, page: Any = 1) -> str:
    raw_page = _clean(page)
    if partition not in INSISEOL_COURSE_PARTITIONS or not raw_page.isdigit():
        return ""
    page_number = int(raw_page)
    if page_number < 1:
        return ""
    query = list(partition.query)
    if page_number > 1:
        query.append(("pgno", str(page_number)))
    return f"https://{INSISEOL_HOST}/program/programInfoList.do?{urlencode(query)}"


def insiseol_experience_list_url(leaf: InsiseolExperienceLeaf, page: Any = 1) -> str:
    raw_page = _clean(page)
    if leaf not in INSISEOL_EXPERIENCE_LEAVES or not raw_page.isdigit():
        return ""
    page_number = int(raw_page)
    if page_number < 1:
        return ""
    query = list(leaf.query)
    if page_number > 1:
        query.append(("pgno", str(page_number)))
    suffix = f"?{urlencode(query)}" if query else ""
    return f"https://{INSISEOL_HOST}{leaf.path}{suffix}"


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response


def _soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if hasattr(value, "text"):
        value = value.text
    return BeautifulSoup(str(value or ""), "html.parser")


def _fetch(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != INSISEOL_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("unsafe Insiseol request URL")
    allowed_paths = {
        "/program/programInfoList.do",
        "/program/programInfoDetail.do",
        "/see/seeInfoList.do",
        "/see/seeInfoDetail.do",
        "/childsee/childSeeInfoList.do",
        "/childsee/childSeeInfoDetail.do",
    }
    if parsed.path not in allowed_paths:
        raise ValueError("non-catalogue Insiseol endpoint blocked")
    return _soup(fetcher(current_session, url, timeout))


def _close_quietly(current_session: Any) -> None:
    if current_session is None:
        return
    try:
        current_session.close()
    except Exception:
        pass


def _municipality(key: str) -> tuple[str, str]:
    return INSISEOL_MUNICIPALITIES[key]


def _branch_code(municipality_code: str) -> str:
    return f"INSISEOL_{municipality_code}"


def _canonical_course_detail_url(value: Any, partition: InsiseolCoursePartition) -> tuple[str, str]:
    parsed = urlparse(urljoin(f"https://{INSISEOL_HOST}", _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected_keys = {"prgm_seq", "prgmdiv"} | ({"cate2"} if partition.cate2 else set())
    allowed_keys = expected_keys | {"pgno"}
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != INSISEOL_HOST
        or parsed.path != "/program/programInfoDetail.do"
        or not expected_keys.issubset(query)
        or not set(query).issubset(allowed_keys)
        or any(len(values) != 1 for values in query.values())
        or query.get("prgmdiv") != [partition.prgmdiv]
        or (partition.cate2 and query.get("cate2") != [partition.cate2])
        or len(query.get("prgm_seq") or []) != 1
    ):
        return "", ""
    identity = _clean(query["prgm_seq"][0])
    if not _PROGRAM_ID_RE.fullmatch(identity):
        return "", ""
    canonical_query = [("prgm_seq", identity)]
    if partition.cate2:
        canonical_query.append(("cate2", partition.cate2))
    canonical_query.append(("prgmdiv", partition.prgmdiv))
    return (
        f"https://{INSISEOL_HOST}/program/programInfoDetail.do?{urlencode(canonical_query)}",
        identity,
    )


def _course_written_contract(soup: BeautifulSoup) -> tuple[int, int, int]:
    nodes = soup.select(".search_array .written")
    if len(nodes) != 1:
        return 0, 0, 0
    match = re.fullmatch(
        r"전체\s*(\d+)건,\s*현재페이지\s*(\d+)\s*/\s*(\d+)",
        _clean(nodes[0].get_text(" ", strip=True)),
    )
    if not match:
        return 0, 0, 0
    return tuple(int(value) for value in match.groups())


def _course_fields(node: Any) -> Optional[dict[str, str]]:
    values: dict[str, str] = {}
    for item in node.select("ul.lec_info > li"):
        labels = item.select("span.wfont")
        if len(labels) != 1:
            continue
        label = labels[0]
        key = _clean(label.get_text(" ", strip=True)).rstrip(":").replace(" ", "")
        label.extract()
        if not key or key in values:
            return None
        values[key] = _clean(item.get_text(" ", strip=True))
    return values


def _course_list_rows(
    target: Any,
    partition: InsiseolCoursePartition,
    soup: BeautifulSoup,
    source_url: str,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    cards = soup.select("div.board_list > ul.lecList.lecH > li")
    for card in cards:
        anchors = card.select("p.tit > a[href*='programInfoDetail.do'][href*='prgm_seq=']")
        statuses = card.select("p.tag_state")
        fields = _course_fields(card)
        if len(anchors) != 1 or len(statuses) != 1 or fields is None:
            malformed += 1
            continue
        title = _clean(anchors[0].get_text(" ", strip=True))
        source_status = _clean(statuses[0].get_text(" ", strip=True))
        raw_url, identity = _canonical_course_detail_url(anchors[0].get("href"), partition)
        if (
            not title
            or not raw_url
            or source_status not in _COURSE_SOURCE_STATUSES
            or not _COURSE_LIST_REQUIRED_KEYS.issubset(fields)
        ):
            malformed += 1
            continue
        municipality_code, municipality_name = _municipality(partition.municipality_key)
        rows.append(
            {
                "provider": INSISEOL_PROVIDER,
                "provider_course_id": f"{INSISEOL_PROVIDER}:program:{identity}",
                "title": title,
                "branch": municipality_name,
                "branch_code": _branch_code(municipality_code),
                "raw_url": raw_url,
                "application_url": "",
                "reservation_available": False,
                "application_type": "INFORMATION_ONLY",
                "status": "CLOSED",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "source_group": "municipal_reservation",
                "collection_area": "교육",
                "municipality_code": municipality_code,
                "municipality_full_name": municipality_name,
                "venue_name": partition.name,
                "raw_fields": {
                    "detail_id": identity,
                    "partition": partition.code,
                    "partition_name": partition.name,
                    "list_title": title,
                    "list_status": source_status,
                    "list_fields": fields,
                    "list_source_url": source_url,
                    "municipality_evidence": {
                        "field": "official_partition_institution",
                        "value": partition.institution,
                        "code": municipality_code,
                        "full_name": municipality_name,
                    },
                },
            }
        )
    return rows, malformed


def _course_page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            _clean(row.get("raw_fields", {}).get("detail_id")),
            _normalized(row.get("title")),
            _clean(row.get("raw_fields", {}).get("list_status")),
            tuple(
                sorted(
                    (
                        _normalized(key),
                        _normalized(value),
                    )
                    for key, value in row.get("raw_fields", {}).get("list_fields", {}).items()
                )
            ),
        )
        for row in rows
    )


def _date_range(value: Any) -> tuple[Optional[date], Optional[date], str]:
    matches = re.findall(r"20\d{2}[.-]\d{1,2}[.-]\d{1,2}", _clean(value))
    if len(matches) != 2:
        return None, None, ""
    try:
        start = date.fromisoformat(matches[0].replace(".", "-"))
        end = date.fromisoformat(matches[1].replace(".", "-"))
    except ValueError:
        return None, None, ""
    if start > end:
        return None, None, ""
    return start, end, f"{start.isoformat()} ~ {end.isoformat()}"


def _detail_pairs(root: Any) -> Optional[dict[str, str]]:
    pairs: dict[str, str] = {}
    for node in root.select("dl"):
        left = node.select(":scope > dt")
        right = node.select(":scope > dd")
        if len(left) != 1 or len(right) != 1:
            return None
        key = _clean(left[0].get_text(" ", strip=True))
        if not key or key in pairs:
            return None
        pairs[key] = _clean(right[0].get_text(" ", strip=True))
    return pairs


def _course_detail_contract(
    row: dict[str, Any],
    partition: InsiseolCoursePartition,
    soup: BeautifulSoup,
    reference_day: date,
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("detail_id"))
    views = soup.select(".board_view")
    title_nodes = soup.select(".title > p.margin_t10")
    if len(views) != 1 or len(title_nodes) != 1:
        return [f"course {identity}: detail structure mismatch"]
    pairs = _detail_pairs(views[0])
    if pairs is None or not _COURSE_DETAIL_REQUIRED_KEYS.issubset(pairs):
        return [f"course {identity}: required detail fields missing"]
    detail_title = _clean(title_nodes[0].get_text(" ", strip=True))
    if _normalized(detail_title) != _normalized(row.get("title")):
        return [f"course {identity}: detail/list title mismatch"]
    if _normalized(pairs["교육기관"]) != _normalized(partition.institution):
        return [f"course {identity}: institution/partition mismatch"]
    list_fields = row.get("raw_fields", {}).get("list_fields", {})
    if _normalized(pairs["교육기간"]) != _normalized(list_fields.get("교육일정")):
        return [f"course {identity}: detail/list education period mismatch"]
    detail_statuses = [_clean(node.get_text(" ", strip=True)) for node in soup.select(".title .tag_state")]
    source_status = _clean(row.get("raw_fields", {}).get("list_status"))
    if source_status not in detail_statuses:
        return [f"course {identity}: detail/list source status mismatch"]
    start, end, period = _date_range(pairs["교육기간"])
    if start is None or end is None:
        return [f"course {identity}: invalid detail education period"]
    if end < reference_day:
        return [f"course {identity}: list-current course is expired in detail"]

    controls = soup.select("#detail_con a.btn.btn_ok")
    is_open = source_status.endswith("접수중")
    if is_open:
        if len(controls) != 1:
            return [f"course {identity}: open status lacks one exact control"]
        control = controls[0]
        if (
            _clean(control.get_text(" ", strip=True)) != "수강신청"
            or _clean(control.get("href")) != "#"
            or _clean(control.get("onclick")) != "alert('로그인을 하신 후에 이용 가능합니다'); return false;"
        ):
            return [f"course {identity}: unsafe or changed application control"]
        status = "OPEN"
        application_url = _clean(row.get("raw_url"))
        application_type = "LOGIN_REQUIRED_ON_DETAIL"
    else:
        if controls:
            return [f"course {identity}: non-open status exposes application control"]
        status = "SCHEDULED" if source_status.endswith("접수대기") else "CLOSED"
        application_url = ""
        application_type = "INFORMATION_ONLY"

    capacity_match = re.fullmatch(r"(\d+)\s*/\s*(\d+)\s*명", pairs.get("정원", ""))
    wait_match = re.fullmatch(r"(\d+)\s*/\s*(\d+)\s*명", pairs.get("대기", ""))
    if not capacity_match or not wait_match:
        return [f"course {identity}: invalid capacity/wait detail"]
    current_capacity, capacity_total = (int(value) for value in capacity_match.groups())
    current_wait, wait_total = (int(value) for value in wait_match.groups())
    if current_capacity + current_wait > capacity_total + wait_total:
        return [f"course {identity}: applications exceed capacity plus waitlist"]
    municipality_code, municipality_name = _municipality(partition.municipality_key)
    row.update(
        {
            "title": detail_title,
            "period": period,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": pairs.get("정시 접수", ""),
            "target": pairs.get("교육 대상", ""),
            "schedule_raw": " ".join(
                value for value in (pairs.get("교육 요일", ""), pairs.get("교육 시간", "")) if value
            ),
            "venue_name": pairs.get("강의실") or partition.name,
            "fee": pairs.get("수강료", ""),
            "material_fee": pairs.get("재료비", ""),
            "capacity": pairs.get("정원", ""),
            "capacity_current": current_capacity,
            "capacity_total": capacity_total,
            "wait_capacity_current": current_wait,
            "wait_capacity_total": wait_total,
            "phone": pairs.get("문의처", ""),
            "status": status,
            "application_url": application_url,
            "reservation_available": bool(application_url and status == "OPEN"),
            "application_type": application_type,
            "branch": municipality_name,
            "branch_code": _branch_code(municipality_code),
            "municipality_code": municipality_code,
            "municipality_full_name": municipality_name,
        }
    )
    row["raw_fields"] = {
        **row["raw_fields"],
        "detail_pairs": pairs,
        "detail_statuses": detail_statuses,
        "application_control_present": bool(controls),
        "application_control_policy": ("item_detail_login_prompt_only" if controls else "no_control"),
    }
    return []


def _base_meta(kind: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "source_count": (len(INSISEOL_COURSE_PARTITIONS) if kind == "education" else len(INSISEOL_EXPERIENCE_LEAVES)),
        "required_list_requests": 0,
        "source_counts": {},
        "page_counts": {},
        "declared_counts": {},
        "declared_pages": {},
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "expired_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "duplicate_count": 0,
        "duplicate_identity_count": 0,
        "duplicate_url_count": 0,
        "source_status_counts": {},
        "municipality_counts": {},
        "current_municipality_counts": {},
        "application_open_count": 0,
        "prerequisite_count": 0,
        "temporarily_disabled_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "calendar_requests": 0,
        "application_requests": 0,
        "auth_requests": 0,
        "recursion_depth": 0,
        "configured_collection_error": "",
    }


def collect_insiseol_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 100,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    crawl_delay_seconds: float = 0.0,
    pacer: InsiseolHostPacer = INSISEOL_HOST_PACER,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete public current/future course snapshot."""

    meta = _base_meta("education")
    if not is_insiseol_education_target(target):
        meta["configured_collection_error"] = "target is not canonical Insiseol education"
        return [], INSISEOL_EDUCATION_PARSER, meta
    if fetcher is None or session_factory is None:
        meta["configured_collection_error"] = "managed fetcher/session injection is required"
        return [], INSISEOL_EDUCATION_PARSER, meta
    if max_pages < len(INSISEOL_COURSE_PARTITIONS) * 3 or detail_limit < 0:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "max_pages/detail_limit is invalid"
        return [], INSISEOL_EDUCATION_PARSER, meta

    reference_day = _today(today)
    errors: list[str] = []
    current_session: Any = None
    all_rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    fetcher = insiseol_paced_fetcher(
        fetcher,
        delay_seconds=crawl_delay_seconds,
        pacer=pacer,
        monotonic_fn=monotonic_fn,
        sleep_fn=sleep_fn,
    )
    try:
        current_session = session_factory()
        first_pages: dict[str, BeautifulSoup] = {}
        declarations: dict[str, tuple[int, int]] = {}
        for partition in INSISEOL_COURSE_PARTITIONS:
            soup = _fetch(
                fetcher,
                current_session,
                insiseol_course_list_url(partition, 1),
                timeout,
            )
            first_pages[partition.code] = soup
            declared_count, current_page, declared_last = _course_written_contract(soup)
            if current_page != 1 or declared_last < 1:
                errors.append(f"{partition.code}: first-page declaration mismatch")
                continue
            declarations[partition.code] = (declared_count, declared_last)
            meta["declared_counts"][partition.code] = declared_count
            meta["declared_pages"][partition.code] = declared_last
            meta["pagination_detected"] = bool(meta["pagination_detected"] or declared_last > 1)
        if len(declarations) != len(INSISEOL_COURSE_PARTITIONS):
            errors.append("fixed three-partition discovery is incomplete")
        required = sum(last + 2 for _count, last in declarations.values())
        meta["required_list_requests"] = required
        if required > max_pages:
            meta["source_cap_reached"] = True
            errors.append(f"max_pages allows {max_pages} of {required} required list requests")

        if not errors:
            for partition in INSISEOL_COURSE_PARTITIONS:
                declared_count, declared_last = declarations[partition.code]
                source_rows: list[dict[str, Any]] = []
                first_signature: tuple[Any, ...] = ()
                final_signature: tuple[Any, ...] = ()
                for page in range(1, declared_last + 2):
                    source_url = insiseol_course_list_url(partition, page)
                    soup = (
                        first_pages[partition.code]
                        if page == 1
                        else _fetch(fetcher, current_session, source_url, timeout)
                    )
                    meta["pages"] += 1
                    observed_count, observed_page, observed_last = _course_written_contract(soup)
                    expected_page = min(page, declared_last)
                    if (
                        observed_count != declared_count
                        or observed_last != declared_last
                        or observed_page != expected_page
                    ):
                        errors.append(f"{partition.code} page {page}: declaration mismatch")
                    rows, malformed = _course_list_rows(target, partition, soup, source_url)
                    meta["page_counts"][f"{partition.code}:{page}"] = len(rows)
                    if malformed:
                        errors.append(f"{partition.code} page {page}: {malformed} malformed rows")
                    if page < declared_last and len(rows) != INSISEOL_COURSE_PAGE_SIZE:
                        errors.append(f"{partition.code} page {page}: short non-final page")
                    signature = _course_page_signature(rows)
                    if page == 1:
                        first_signature = signature
                    if page == declared_last:
                        final_signature = signature
                    elif page == declared_last + 1 and signature != final_signature:
                        errors.append(f"{partition.code}: out-of-range page does not repeat final signature")
                    if page <= declared_last:
                        source_rows.extend(rows)
                recheck_url = insiseol_course_list_url(partition, 1)
                recheck = _fetch(fetcher, current_session, recheck_url, timeout)
                meta["pages"] += 1
                recheck_rows, malformed = _course_list_rows(target, partition, recheck, recheck_url)
                if malformed or _course_page_signature(recheck_rows) != first_signature:
                    errors.append(f"{partition.code}: first page changed during snapshot")
                if len(source_rows) != declared_count:
                    errors.append(f"{partition.code}: declared {declared_count}, parsed {len(source_rows)}")
                meta["source_counts"][partition.code] = len(source_rows)
                all_rows.extend(source_rows)

        identities = [_clean(row.get("raw_fields", {}).get("detail_id")) for row in all_rows]
        course_ids = [_clean(row.get("provider_course_id")) for row in all_rows]
        raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
        meta["source_total"] = meta["source_rows"] = len(all_rows)
        meta["duplicate_identity_count"] = len(identities) - len(set(identities))
        meta["duplicate_count"] = len(course_ids) - len(set(course_ids))
        meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
        if any(meta[key] for key in ("duplicate_identity_count", "duplicate_count", "duplicate_url_count")):
            errors.append("duplicate course identities or canonical URLs")
        meta["source_status_counts"] = dict(
            sorted(Counter(_clean(row.get("raw_fields", {}).get("list_status")) for row in all_rows).items())
        )

        for row in all_rows:
            _start, end, _period = _date_range(row.get("raw_fields", {}).get("list_fields", {}).get("교육일정"))
            if end is None:
                errors.append(f"course {row['raw_fields']['detail_id']}: invalid list education period")
            elif end >= reference_day:
                current_rows.append(row)
        meta["current_count"] = len(current_rows)
        meta["expired_count"] = len(all_rows) - len(current_rows)
        if detail_limit < len(current_rows):
            meta["source_cap_reached"] = True
            errors.append(f"detail_limit allows {detail_limit} of {len(current_rows)} current details")

        if not errors:
            for row in current_rows:
                meta["detail_attempts"] += 1
                partition = INSISEOL_COURSE_PARTITION_BY_CODE[_clean(row.get("raw_fields", {}).get("partition"))]
                try:
                    soup = _fetch(
                        fetcher,
                        current_session,
                        _clean(row.get("raw_url")),
                        timeout,
                    )
                    detail_errors = _course_detail_contract(row, partition, soup, reference_day)
                except Exception as exc:
                    detail_errors = [
                        f"course {row['raw_fields']['detail_id']}: detail fetch/parse failed ({type(exc).__name__})"
                    ]
                if detail_errors:
                    meta["detail_errors"] += 1
                    errors.extend(detail_errors)
                else:
                    meta["detail_pages"] += 1

        municipality_counts = Counter(_clean(row.get("municipality_full_name")) for row in current_rows)
        meta["municipality_counts"] = dict(sorted(municipality_counts.items()))
        meta["current_municipality_counts"] = dict(sorted(municipality_counts.items()))
        meta["application_open_count"] = sum(bool(row.get("reservation_available")) for row in current_rows)
        if dedupe_rows is not None and not errors:
            deduped = list(dedupe_rows(current_rows))
            if len(deduped) != len(current_rows):
                errors.append("dedupe changed the complete education snapshot")
            else:
                current_rows = deduped

        meta["pagination_complete"] = (
            not meta["source_cap_reached"]
            and meta["pages"] == meta["required_list_requests"]
            and len(declarations) == len(INSISEOL_COURSE_PARTITIONS)
            and not any("page" in error or "partition" in error for error in errors)
        )
        meta["details_complete"] = (
            meta["detail_pages"] == len(current_rows) and meta["detail_errors"] == 0 and not meta["source_cap_reached"]
        )
        meta["snapshot_complete"] = not errors and meta["pagination_complete"] and meta["details_complete"]
        meta["no_current_data"] = meta["snapshot_complete"] and not current_rows
        if meta["no_current_data"]:
            meta["no_current_reason"] = "complete official public course catalogue has no current/future rows"
        meta["configured_collection_error"] = "; ".join(errors)
        return (
            current_rows if meta["snapshot_complete"] else [],
            INSISEOL_EDUCATION_PARSER,
            meta,
        )
    except Exception as exc:
        errors.append(f"fixed education fan-out failed ({type(exc).__name__})")
        meta["configured_collection_error"] = "; ".join(errors)
        return [], INSISEOL_EDUCATION_PARSER, meta
    finally:
        _close_quietly(current_session)


def _canonical_experience_detail_url(value: Any, leaf: InsiseolExperienceLeaf) -> tuple[str, str]:
    parsed = urlparse(urljoin(f"https://{INSISEOL_HOST}", _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected_path = "/childsee/childSeeInfoDetail.do" if leaf.code == "childsee" else "/see/seeInfoDetail.do"
    allowed_keys = {"see_seq", "pgno"} | ({"inst_cd"} if leaf.inst_cd else set())
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != INSISEOL_HOST
        or parsed.path != expected_path
        or not set(query).issubset(allowed_keys)
        or any(len(values) != 1 for values in query.values())
        or len(query.get("see_seq") or []) != 1
        or (leaf.inst_cd and query.get("inst_cd") != [leaf.inst_cd])
        or (not leaf.inst_cd and "inst_cd" in query)
    ):
        return "", ""
    identity = _clean(query["see_seq"][0])
    if not _EXPERIENCE_ID_RE.fullmatch(identity):
        return "", ""
    canonical_query = [("see_seq", identity)]
    if leaf.inst_cd:
        canonical_query.append(("inst_cd", leaf.inst_cd))
    return (
        f"https://{INSISEOL_HOST}{expected_path}?{urlencode(canonical_query)}",
        identity,
    )


def _experience_declared_total(soup: BeautifulSoup) -> int:
    nodes = soup.select(".search_array .written, .written")
    texts = {_clean(node.get_text(" ", strip=True)) for node in nodes}
    matches = []
    for text in texts:
        match = re.fullmatch(r"전체\s*(\d+)건,\s*현재페이지\s*1\s*/\s*21", text)
        if match:
            matches.append(int(match.group(1)))
    return matches[0] if len(matches) == 1 else -1


def _experience_list_rows(
    leaf: InsiseolExperienceLeaf,
    soup: BeautifulSoup,
    source_url: str,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    for table_row in soup.select("div.board_list table.general_board > tbody > tr"):
        cells = table_row.select(":scope > td")
        anchors = table_row.select("td.title > a[href*='InfoDetail.do'][href*='see_seq=']")
        if len(cells) != 6 or len(anchors) != 1:
            malformed += 1
            continue
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        title = _clean(anchors[0].get_text(" ", strip=True))
        raw_url, identity = _canonical_experience_detail_url(anchors[0].get("href"), leaf)
        region = INSISEOL_EXPERIENCE_ITEM_REGIONS.get((leaf.code, identity))
        if not title or not raw_url or region is None:
            malformed += 1
            continue
        municipality_key, venue_name = region
        municipality_code, municipality_name = _municipality(municipality_key)
        rows.append(
            {
                "provider": INSISEOL_PROVIDER,
                "provider_course_id": f"{INSISEOL_PROVIDER}:experience:{leaf.code}:{identity}",
                "title": title,
                "branch": municipality_name,
                "branch_code": _branch_code(municipality_code),
                "raw_url": raw_url,
                "application_url": "",
                "reservation_available": False,
                "application_type": "INFORMATION_ONLY",
                "status": "SCHEDULED",
                "collection_category": "공공예약",
                "domain_category": "체험·견학",
                "service_group": "체험",
                "source_group": "municipal_reservation",
                "collection_area": "체험",
                "municipality_code": municipality_code,
                "municipality_full_name": municipality_name,
                "venue_name": venue_name,
                "phone": values[2],
                "fee": values[3],
                "capacity": values[4],
                "raw_fields": {
                    "detail_id": identity,
                    "identity": f"{leaf.code}:{identity}",
                    "leaf": leaf.code,
                    "leaf_name": leaf.name,
                    "list_title": title,
                    "list_phone": values[2],
                    "list_fee": values[3],
                    "list_individual_capacity": values[4],
                    "list_group_capacity": values[5],
                    "list_source_url": source_url,
                    "municipality_evidence": {
                        "field": "audited_item_facility",
                        "value": venue_name,
                        "code": municipality_code,
                        "full_name": municipality_name,
                    },
                },
            }
        )
    return rows, malformed


def _experience_page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _normalized(row.get("title")),
            _normalized(row.get("raw_fields", {}).get("list_phone")),
            _normalized(row.get("raw_fields", {}).get("list_fee")),
            _normalized(row.get("raw_fields", {}).get("list_individual_capacity")),
            _normalized(row.get("raw_fields", {}).get("list_group_capacity")),
        )
        for row in rows
    )


def _fee_equivalent(left: Any, right: Any) -> bool:
    normalized_left = _normalized(left)
    normalized_right = _normalized(right)
    free = {"무료", "0", "0원"}
    return normalized_left == normalized_right or (normalized_left in free and normalized_right in free)


def _experience_period_contract(
    leaf: InsiseolExperienceLeaf,
    identity: str,
    pairs: Mapping[str, str],
    reference_day: date,
) -> tuple[Optional[date], Optional[date], str, bool, str]:
    info = _clean(pairs.get("이용안내") or pairs.get("교육안내"))
    if leaf.code == "songdopark" and identity in {"22", "23"}:
        return None, None, "상시(공개 일정 운영)", True, ""
    if leaf.code == "childsee":
        return None, None, "상시(상설전시관 예약 후 신청)", True, ""
    if leaf.code == "songdopark" and identity == "6":
        match = re.search(
            r"운영기간\s*:\s*(20\d{2})년\s*(\d{1,2})월\s*~\s*(\d{1,2})월",
            info,
        )
        if not match:
            return None, None, "", False, "ecology operation period missing"
        year, start_month, end_month = (int(value) for value in match.groups())
        start = date(year, start_month, 1)
        end = date(year, end_month, monthrange(year, end_month)[1])
        return start, end, f"{start.isoformat()} ~ {end.isoformat()}", False, ""
    if leaf.code == "seaside" and identity in {"2", "19"}:
        match = re.search(
            r"운영기간\s*(\d{1,2})월\s*(\d{1,2})일\s*~\s*(\d{1,2})월\s*(\d{1,2})일",
            info,
        )
        if not match:
            return None, None, "", False, "seaside salt operation period missing"
        sm, sd, em, ed = (int(value) for value in match.groups())
        start = date(reference_day.year, sm, sd)
        end = date(reference_day.year, em, ed)
        return start, end, f"{start.isoformat()} ~ {end.isoformat()}", False, ""
    if leaf.code == "seaside" and identity in {"3", "18"}:
        matches = re.findall(
            r"(\d{1,2})월\s*(\d{1,2})일\s*~\s*(\d{1,2})월\s*(\d{1,2})일",
            info,
        )
        if len(matches) < 2:
            return None, None, "", False, "seaside forest operation windows missing"
        dates = [
            (
                date(reference_day.year, int(sm), int(sd)),
                date(reference_day.year, int(em), int(ed)),
            )
            for sm, sd, em, ed in matches[:2]
        ]
        start, end = dates[0][0], dates[-1][1]
        period = ", ".join(f"{left.isoformat()} ~ {right.isoformat()}" for left, right in dates)
        return start, end, period, False, ""
    if leaf.code == "chongnapark" and identity in {"8", "21"}:
        match = re.search(
            r"운영기간\s*:\s*(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일\s*~\s*(\d{1,2})월\s*(\d{1,2})일",
            info,
        )
        if not match:
            return None, None, "", False, "Cheongna operation period missing"
        year, sm, sd, em, ed = (int(value) for value in match.groups())
        start, end = date(year, sm, sd), date(year, em, ed)
        return start, end, f"{start.isoformat()} ~ {end.isoformat()}", False, ""
    return None, None, "", False, "unreviewed experience identity"


def _safe_schedule_control(control: Any, identity: str) -> tuple[str, str]:
    label = _clean(control.get_text(" ", strip=True))
    parsed = urlparse(urljoin(f"https://{INSISEOL_HOST}", _clean(control.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        label == "신청 하기"
        and parsed.scheme == "https"
        and (parsed.hostname or "").lower() == INSISEOL_HOST
        and parsed.path == "/see/seeScheduleMonth.do"
        and query == {"see_seq": [identity]}
        and not _clean(control.get("onclick"))
        and not parsed.fragment
    ):
        return parsed._replace(fragment="").geturl(), label
    return "", label


def _experience_detail_contract(
    row: dict[str, Any],
    leaf: InsiseolExperienceLeaf,
    soup: BeautifulSoup,
    reference_day: date,
) -> tuple[list[str], bool]:
    identity = _clean(row.get("raw_fields", {}).get("detail_id"))
    views = soup.select(".board_view")
    if len(views) != 1:
        return [f"experience {leaf.code}:{identity}: detail structure mismatch"], False
    root = views[0]
    titles = root.select(":scope > .title")
    pairs = _detail_pairs(root)
    if len(titles) != 1 or pairs is None:
        return [f"experience {leaf.code}:{identity}: detail contract mismatch"], False
    if not _EXPERIENCE_DETAIL_REQUIRED_KEYS.issubset(pairs) or not ({"이용안내", "교육안내"} & set(pairs)):
        return [f"experience {leaf.code}:{identity}: required detail fields missing"], False
    list_title = _clean(row.get("raw_fields", {}).get("list_title"))
    detail_title_text = _clean(titles[0].get_text(" ", strip=True))
    if not _normalized(detail_title_text).startswith(_normalized(list_title)):
        return [f"experience {leaf.code}:{identity}: detail/list title mismatch"], False
    if _normalized(pairs["문의처"]) != _normalized(row.get("raw_fields", {}).get("list_phone")):
        return [f"experience {leaf.code}:{identity}: detail/list phone mismatch"], False
    if not _fee_equivalent(pairs["기본 요금"], row.get("raw_fields", {}).get("list_fee")):
        return [f"experience {leaf.code}:{identity}: detail/list fee mismatch"], False
    detail_capacity = re.sub(r"[^0-9]", "", pairs["관람 인원"])
    list_capacity = re.sub(r"[^0-9]", "", _clean(row.get("raw_fields", {}).get("list_individual_capacity")))
    if not detail_capacity or detail_capacity != list_capacity:
        return [f"experience {leaf.code}:{identity}: detail/list capacity mismatch"], False

    start, end, period, ongoing, period_error = _experience_period_contract(leaf, identity, pairs, reference_day)
    if period_error:
        return [f"experience {leaf.code}:{identity}: {period_error}"], False
    is_current = ongoing or bool(end and end >= reference_day)

    controls = soup.select("#detail_con a.btn.btn_ok, .board_view a.btn.btn_ok")
    # BeautifulSoup selector overlap may return a node once; preserve exactness.
    controls = list(dict.fromkeys(controls))
    application_url = ""
    application_type = "INFORMATION_ONLY"
    reservation_available = False
    source_control = ""
    if leaf.code == "childsee":
        statuses = [_clean(node.get_text(" ", strip=True)) for node in root.select(".tag_state")]
        guide_links = {
            _clean(anchor.get("href")): _clean(anchor.get_text(" ", strip=True))
            for anchor in root.select("a.conbtn[href]")
        }
        if (
            controls
            or statuses != ["신청 가능"]
            or guide_links
            != {
                "/childsee/childSeeScheduleMonth.do?see_seq=1": "상설전시관 예약하러 가기",
                "/mypage/see.jsp": "마이페이지 관람 이력현황",
            }
        ):
            return [f"experience {leaf.code}:{identity}: prerequisite contract changed"], False
        status = "OPEN"
        application_url = _clean(row.get("raw_url"))
        application_type = "PREREQUISITE_RESERVATION"
        source_control = "permanent_exhibit_then_mypage"
    else:
        if len(controls) != 1:
            return [f"experience {leaf.code}:{identity}: expected one application control"], False
        control = controls[0]
        safe_url, label = _safe_schedule_control(control, identity)
        if safe_url:
            status = "OPEN" if is_current else "CLOSED"
            application_url = safe_url if is_current else ""
            application_type = "PUBLIC_SCHEDULE" if is_current else "INFORMATION_ONLY"
            reservation_available = bool(application_url)
            source_control = label
        elif (
            leaf.code == "seaside"
            and label == "신청 하기"
            and _clean(control.get("href")) == "#"
            and _clean(control.get("onclick")) == "fnSeaside2Alim(); return false;"
        ):
            status = "SCHEDULED" if is_current else "CLOSED"
            source_control = "seaside_temporarily_disabled"
        else:
            return [f"experience {leaf.code}:{identity}: unsafe application control"], False

    municipality_code = _clean(row.get("municipality_code"))
    municipality_name = _clean(row.get("municipality_full_name"))
    row.update(
        {
            "title": list_title,
            "period": period,
            "start_date": start.isoformat() if start else "",
            "end_date": end.isoformat() if end else "",
            "schedule_raw": pairs.get("관람시간", ""),
            "fee": pairs.get("기본 요금", ""),
            "capacity": pairs.get("관람 인원", ""),
            "phone": pairs.get("문의처", ""),
            "status": status,
            "application_url": application_url,
            "reservation_available": reservation_available,
            "application_type": application_type,
            "branch": municipality_name,
            "branch_code": _branch_code(municipality_code),
        }
    )
    row["raw_fields"] = {
        **row["raw_fields"],
        "detail_pairs": pairs,
        "operation_ongoing": ongoing,
        "source_control": source_control,
        "application_endpoint_requested": False,
        "schedule_endpoint_requested": False,
        "auth_endpoint_requested": False,
    }
    return [], is_current


def collect_insiseol_experience_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 100,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    crawl_delay_seconds: float = 0.0,
    pacer: InsiseolHostPacer = INSISEOL_HOST_PACER,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect all audited public experience catalogues without opening schedules."""

    meta = _base_meta("experience")
    if not is_insiseol_experience_target(target):
        meta["configured_collection_error"] = "target is not canonical Insiseol experience"
        return [], INSISEOL_EXPERIENCE_PARSER, meta
    if fetcher is None or session_factory is None:
        meta["configured_collection_error"] = "managed fetcher/session injection is required"
        return [], INSISEOL_EXPERIENCE_PARSER, meta
    required = len(INSISEOL_EXPERIENCE_LEAVES) * 3
    meta["required_list_requests"] = required
    if max_pages < required or detail_limit < 0:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "max_pages/detail_limit is invalid"
        return [], INSISEOL_EXPERIENCE_PARSER, meta

    reference_day = _today(today)
    errors: list[str] = []
    current_session: Any = None
    all_rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    fetcher = insiseol_paced_fetcher(
        fetcher,
        delay_seconds=crawl_delay_seconds,
        pacer=pacer,
        monotonic_fn=monotonic_fn,
        sleep_fn=sleep_fn,
    )
    try:
        current_session = session_factory()
        for leaf in INSISEOL_EXPERIENCE_LEAVES:
            signatures: list[tuple[Any, ...]] = []
            source_rows: list[dict[str, Any]] = []
            declared_total = -1
            for attempt, page in enumerate((1, 2, 1), start=1):
                source_url = insiseol_experience_list_url(leaf, page)
                soup = _fetch(fetcher, current_session, source_url, timeout)
                meta["pages"] += 1
                observed_total = _experience_declared_total(soup)
                if attempt == 1:
                    declared_total = observed_total
                    meta["declared_counts"][leaf.code] = observed_total
                elif observed_total != declared_total:
                    errors.append(f"{leaf.code}: declared total changed during snapshot")
                rows, malformed = _experience_list_rows(leaf, soup, source_url)
                meta["page_counts"][f"{leaf.code}:{attempt}"] = len(rows)
                if malformed:
                    errors.append(f"{leaf.code}: {malformed} malformed or unmapped rows")
                signatures.append(_experience_page_signature(rows))
                if attempt == 1:
                    source_rows = rows
            if declared_total < 0 or declared_total != len(source_rows):
                errors.append(f"{leaf.code}: declared {declared_total}, parsed {len(source_rows)}")
            if len(signatures) != 3 or signatures[1] != signatures[0]:
                errors.append(f"{leaf.code}: pgno=2 does not repeat normalized page-one signature")
            if len(signatures) != 3 or signatures[2] != signatures[0]:
                errors.append(f"{leaf.code}: first page changed during snapshot")
            meta["source_counts"][leaf.code] = len(source_rows)
            all_rows.extend(source_rows)

        identities = [_clean(row.get("raw_fields", {}).get("identity")) for row in all_rows]
        course_ids = [_clean(row.get("provider_course_id")) for row in all_rows]
        raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
        meta["source_total"] = meta["source_rows"] = len(all_rows)
        meta["duplicate_identity_count"] = len(identities) - len(set(identities))
        meta["duplicate_count"] = len(course_ids) - len(set(course_ids))
        meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
        if any(meta[key] for key in ("duplicate_identity_count", "duplicate_count", "duplicate_url_count")):
            errors.append("duplicate experience identities or canonical URLs")
        if detail_limit < len(all_rows):
            meta["source_cap_reached"] = True
            errors.append(f"detail_limit allows {detail_limit} of {len(all_rows)} required details")

        if not errors:
            for row in all_rows:
                meta["detail_attempts"] += 1
                leaf = INSISEOL_EXPERIENCE_LEAF_BY_CODE[_clean(row.get("raw_fields", {}).get("leaf"))]
                try:
                    soup = _fetch(
                        fetcher,
                        current_session,
                        _clean(row.get("raw_url")),
                        timeout,
                    )
                    detail_errors, is_current = _experience_detail_contract(row, leaf, soup, reference_day)
                except Exception as exc:
                    detail_errors = [
                        f"experience {row['raw_fields']['identity']}: detail fetch/parse failed ({type(exc).__name__})"
                    ]
                    is_current = False
                if detail_errors:
                    meta["detail_errors"] += 1
                    errors.extend(detail_errors)
                else:
                    meta["detail_pages"] += 1
                    if is_current:
                        current_rows.append(row)

        meta["current_count"] = len(current_rows)
        meta["expired_count"] = len(all_rows) - len(current_rows)
        municipality_counts = Counter(_clean(row.get("municipality_full_name")) for row in all_rows)
        current_municipality_counts = Counter(_clean(row.get("municipality_full_name")) for row in current_rows)
        meta["municipality_counts"] = dict(sorted(municipality_counts.items()))
        meta["current_municipality_counts"] = dict(sorted(current_municipality_counts.items()))
        meta["application_open_count"] = sum(bool(row.get("reservation_available")) for row in current_rows)
        meta["prerequisite_count"] = sum(
            row.get("application_type") == "PREREQUISITE_RESERVATION" for row in current_rows
        )
        meta["temporarily_disabled_count"] = sum(
            row.get("raw_fields", {}).get("source_control") == "seaside_temporarily_disabled" for row in current_rows
        )
        if dedupe_rows is not None and not errors:
            deduped = list(dedupe_rows(current_rows))
            if len(deduped) != len(current_rows):
                errors.append("dedupe changed the complete experience snapshot")
            else:
                current_rows = deduped

        meta["pagination_complete"] = (
            not meta["source_cap_reached"]
            and meta["pages"] == required
            and not any("page" in error or "pgno" in error for error in errors)
        )
        meta["details_complete"] = (
            meta["detail_pages"] == len(all_rows) and meta["detail_errors"] == 0 and not meta["source_cap_reached"]
        )
        meta["snapshot_complete"] = not errors and meta["pagination_complete"] and meta["details_complete"]
        meta["no_current_data"] = meta["snapshot_complete"] and not current_rows
        if meta["no_current_data"]:
            meta["no_current_reason"] = "complete official public experience catalogue has no current rows"
        meta["configured_collection_error"] = "; ".join(errors)
        return (
            current_rows if meta["snapshot_complete"] else [],
            INSISEOL_EXPERIENCE_PARSER,
            meta,
        )
    except Exception as exc:
        errors.append(f"fixed experience fan-out failed ({type(exc).__name__})")
        meta["configured_collection_error"] = "; ".join(errors)
        return [], INSISEOL_EXPERIENCE_PARSER, meta
    finally:
        _close_quietly(current_session)


__all__ = [
    "INSISEOL_COURSE_PARTITIONS",
    "INSISEOL_COVERED_MUNICIPALITIES",
    "INSISEOL_CRAWL_DELAY_SECONDS",
    "INSISEOL_EDUCATION_CANONICAL_URL",
    "INSISEOL_EDUCATION_PARSER",
    "INSISEOL_EXPERIENCE_CANONICAL_URL",
    "INSISEOL_EXPERIENCE_ITEM_REGIONS",
    "INSISEOL_EXPERIENCE_LEAVES",
    "INSISEOL_EXPERIENCE_PARSER",
    "INSISEOL_HOST",
    "INSISEOL_HOST_PACER",
    "INSISEOL_MUNICIPALITIES",
    "INSISEOL_PROVIDER",
    "InsiseolCoursePartition",
    "InsiseolExperienceLeaf",
    "InsiseolHostPacer",
    "collect_insiseol_education_courses",
    "collect_insiseol_experience_courses",
    "insiseol_course_list_url",
    "insiseol_experience_list_url",
    "insiseol_paced_fetcher",
    "is_insiseol_education_target",
    "is_insiseol_experience_target",
]
