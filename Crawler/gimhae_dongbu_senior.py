"""Official course collector for Gimhae Dongbu Senior Welfare Center.

The public course catalogue is an SPA backed by JSON APIs.  After online
registration closes, ``/api/v1/courses`` intentionally returns an empty list,
while the public lottery ledger continues to expose the complete posted course
identity set.  The 2026 second-half brochure is therefore used as a
term-bounded schedule contract and is joined to that public identity ledger.

The contract is deliberately fail-closed: test rows are excluded, every real
source identity must have one brochure specification, and every required field
must be present before any rows are returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

import requests


PROVIDER = "GIMHAE_DONGBU_SENIOR_NOTICE"
HOST = "gimhaedongbu.e-ncom.co.kr"
ROOT_URL = f"https://{HOST}"
COURSE_URL = f"{ROOT_URL}/course"
AGENCY_INFO_URL = f"{ROOT_URL}/api/v1/public/agency/info"
TERMS_URL = f"{ROOT_URL}/api/v1/terms"
TERM_STATUS_URL = f"{ROOT_URL}/api/v1/terms/status"
COURSES_URL = f"{ROOT_URL}/api/v1/courses?page=1&size=200"
LOTTERY_RESULTS_URL = f"{ROOT_URL}/api/v1/lottery/results"
BROCHURE_NOTICE_URL = "https://www.gimhaedongbu.or.kr/core_board2007/board/coreboard.php?i_board=notice&wr_id=1752"
BROCHURE_IMAGE_URL = (
    "https://www.gimhaedongbu.or.kr/core_board2007/data/file/notice/images/"
    "KakaoTalk_20260617_14562782899b983892094b5c6d2fc3736e15da7d1.jpg"
)

BRANCH = "김해시동부노인종합복지관"
ADDRESS = "경상남도 김해시 신어산길 46"
DEFAULT_TARGET = f"{BRANCH} 등록 회원"
MAX_RESPONSE_BYTES = 4_000_000
MAX_PUBLIC_ROWS = 500
PARSER = (
    "gimhae_dongbu_public_api+active_term+course_catalogue+"
    "lottery_identity_ledger+term_bounded_official_brochure+test_filter+"
    "required_field_contract"
)

_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
_SPACE_RE = re.compile(r"\s+")
_TIME_RE = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?!\d)")


class GimhaeDongbuContractError(RuntimeError):
    """Raised when the audited public source contract changes."""


@dataclass(frozen=True)
class CourseSpec:
    code: str
    title: str
    category: str
    schedule: str
    instructor: str
    fee: str
    capacity: int
    venue: str
    start_date: str
    target: str = DEFAULT_TARGET


def _spec(
    code: str,
    title: str,
    category: str,
    schedule: str,
    instructor: str,
    fee: str,
    capacity: int,
    venue: str,
    start_date: str,
    target: str = DEFAULT_TARGET,
) -> CourseSpec:
    return CourseSpec(
        code=code,
        title=title,
        category=category,
        schedule=schedule,
        instructor=instructor,
        fee=fee,
        capacity=capacity,
        venue=venue,
        start_date=start_date,
        target=target,
    )


TERM_6_SPECS = (
    _spec("B0105", "우리말 기초", "평생교육", "월, 수, 금 10:00~10:50", "윤화정", "무료", 20, "1실(4층)", "2026-08-03"),
    _spec("B0106", "우리말 심화", "평생교육", "월, 수, 금 11:00~11:50", "윤화정", "무료", 20, "1실(4층)", "2026-08-03"),
    _spec("B0603", "한자 기초", "평생교육", "월, 목 13:00~13:50", "윤화정", "15,000원", 20, "1실(4층)", "2026-08-03"),
    _spec("B0604", "한자 심화", "평생교육", "월, 목 14:00~14:50", "윤화정", "15,000원", 20, "1실(4층)", "2026-08-03"),
    _spec(
        "B0303",
        "일본어 기초",
        "평생교육",
        "화, 금 13:00~13:50",
        "마쯔모또 기미꼬",
        "15,000원",
        20,
        "2실(4층)",
        "2026-08-04",
    ),
    _spec(
        "B0309",
        "일본어 심화",
        "평생교육",
        "화, 금 14:00~14:50",
        "마쯔모또 기미꼬",
        "15,000원",
        20,
        "2실(4층)",
        "2026-08-04",
    ),
    _spec("B0402", "중국어 기초", "평생교육", "수, 금 11:00~11:50", "김성희", "15,000원", 20, "3실(4층)", "2026-08-05"),
    _spec("B0904", "우리역사", "평생교육", "수 13:00~14:50", "화철오", "15,000원", 20, "1실(4층)", "2026-08-05"),
    _spec("B0820", "수어교실", "평생교육", "월 13:00~14:50", "최지혜", "15,000원", 20, "2실(4층)", "2026-08-03"),
    _spec("B0204", "영어기초", "평생교육", "수, 목 10:00~10:50", "조정원", "15,000원", 20, "3실(4층)", "2026-08-05"),
    _spec(
        "R0112", "스마트폰기초1", "정보화교육", "월, 수 10:00~10:50", "허연희", "15,000원", 20, "2실(4층)", "2026-08-03"
    ),
    _spec(
        "R0113", "스마트폰기초2", "정보화교육", "월, 수 11:00~11:50", "허연희", "15,000원", 20, "2실(4층)", "2026-08-03"
    ),
    _spec(
        "R0114", "스마트폰기초3", "정보화교육", "화, 금 13:00~13:50", "박이진", "15,000원", 20, "1실(4층)", "2026-08-04"
    ),
    _spec(
        "R0116",
        "스마트폰심화1",
        "정보화교육",
        "월 13:00~14:50",
        "이정해",
        "15,000원",
        30,
        "정보화실(4층)",
        "2026-08-03",
    ),
    _spec(
        "R0117",
        "스마트폰심화2",
        "정보화교육",
        "수 13:00~14:50",
        "이정해",
        "15,000원",
        30,
        "정보화실(4층)",
        "2026-08-05",
    ),
    _spec(
        "R0115",
        "스마트폰심화3",
        "정보화교육",
        "목 10:00~11:50",
        "이정해",
        "15,000원",
        30,
        "정보화실(4층)",
        "2026-08-06",
    ),
    _spec(
        "R0118",
        "컴퓨터 활용",
        "정보화교육",
        "월, 수 10:00~11:50",
        "정정미",
        "30,000원",
        30,
        "정보화실(4층)",
        "2026-08-03",
    ),
    _spec(
        "R0120",
        "인터넷 기초",
        "정보화교육",
        "화, 금 10:00~11:50",
        "이정해",
        "30,000원",
        30,
        "정보화실(4층)",
        "2026-08-04",
    ),
    _spec(
        "R0121",
        "인터넷 심화",
        "정보화교육",
        "화, 금 13:00~14:50",
        "이정해",
        "30,000원",
        30,
        "정보화실(4층)",
        "2026-08-04",
    ),
    _spec(
        "R0122",
        "스마트폰 영상편집",
        "정보화교육",
        "금 15:00~16:50",
        "허연희",
        "15,000원",
        30,
        "정보화실(4층)",
        "2026-08-07",
    ),
    _spec(
        "R0124",
        "생성형 AI 활용",
        "정보화교육",
        "목 13:00~14:50",
        "정정미",
        "15,000원",
        30,
        "정보화실(4층)",
        "2026-08-06",
    ),
    _spec("C0404", "라인댄스 기초1", "취미여가", "월 10:00~10:50", "박해경", "7,500원", 40, "강당(4층)", "2026-08-03"),
    _spec("C0405", "라인댄스 기초2", "취미여가", "화 16:00~16:50", "박해경", "7,500원", 40, "강당(4층)", "2026-08-04"),
    _spec("C0406", "라인댄스 심화", "취미여가", "월 11:00~11:50", "박해경", "7,500원", 40, "강당(4층)", "2026-08-03"),
    _spec("C0103", "노래교실1", "취미여가", "월 13:00~14:50", "이현주", "15,000원", 150, "강당(4층)", "2026-08-03"),
    _spec("C0104", "노래교실2", "취미여가", "목 13:00~14:50", "황미영", "15,000원", 150, "강당(4층)", "2026-08-06"),
    _spec("C0703", "색소폰", "취미여가", "화 10:00~11:50", "이성욱", "15,000원", 25, "강당(4층)", "2026-08-04"),
    _spec("C0302", "볼룸댄스 기초", "취미여가", "수 15:00~15:50", "장정호", "7,500원", 40, "강당(4층)", "2026-08-05"),
    _spec("C0121", "볼룸댄스 심화", "취미여가", "수 16:00~16:50", "장정호", "7,500원", 40, "강당(4층)", "2026-08-05"),
    _spec("C0506", "고전무용", "취미여가", "수 10:00~11:50", "이승아", "15,000원", 30, "강당(4층)", "2026-08-05"),
    _spec("C0119", "트로트댄스1", "취미여가", "수 13:00~13:50", "이수경", "7,500원", 40, "강당(4층)", "2026-08-05"),
    _spec("C0120", "트로트댄스2", "취미여가", "수 14:00~14:50", "이수경", "7,500원", 40, "강당(4층)", "2026-08-05"),
    _spec("C0705", "고고장구", "취미여가", "금 14:00~14:50", "강추희", "7,500원", 30, "강당(4층)", "2026-08-07"),
    _spec("C0702", "풍물장구", "취미여가", "금 15:00~16:50", "강추희", "15,000원", 30, "강당(4층)", "2026-08-07"),
    _spec("C0802", "합창단", "취미여가", "목 10:00~11:50", "김은순, 이세나", "15,000원", 40, "강당(4층)", "2026-08-06"),
    _spec("C1007", "사군자1", "취미여가", "월 13:00~14:50", "박위남", "15,000원", 15, "서예실(4층)", "2026-08-03"),
    _spec("C1008", "사군자2", "취미여가", "금 13:00~14:50", "홍명량", "15,000원", 15, "서예실(4층)", "2026-08-07"),
    _spec("C1016", "문인화", "취미여가", "수 13:00~14:50", "홍명량", "15,000원", 15, "서예실(4층)", "2026-08-05"),
    _spec(
        "C1015", "캘리그라피(붓)", "취미여가", "수 10:00~11:50", "장아름", "15,000원", 20, "서예실(4층)", "2026-08-05"
    ),
    _spec("C1009", "서예1", "취미여가", "화 10:00~11:50", "한맹란", "15,000원", 20, "서예실(4층)", "2026-08-04"),
    _spec("C1010", "서예2", "취미여가", "화 13:00~14:50", "한맹란", "15,000원", 20, "서예실(4층)", "2026-08-04"),
    _spec("C1011", "서예3", "취미여가", "목 10:00~11:50", "박경희", "15,000원", 20, "서예실(4층)", "2026-08-06"),
    _spec("C1012", "서예4", "취미여가", "목 13:00~14:50", "박경희", "15,000원", 20, "서예실(4층)", "2026-08-06"),
    _spec("C0238", "통기타", "취미여가", "수 15:00~16:50", "박선희", "15,000원", 15, "서예실(4층)", "2026-08-05"),
    _spec("C1103", "하모니카 기초", "취미여가", "수 10:00~11:50", "박선희", "15,000원", 20, "5실(2층)", "2026-08-05"),
    _spec("C1102", "하모니카 심화", "취미여가", "수 13:00~14:50", "박선희", "15,000원", 20, "5실(2층)", "2026-08-05"),
    _spec("CC001", "숟가락난타1", "취미여가", "화 10:00~10:50", "김영둘", "7,500원", 20, "5실(2층)", "2026-08-04"),
    _spec("CC002", "숟가락난타2", "취미여가", "화 11:00~11:50", "김영둘", "7,500원", 20, "5실(2층)", "2026-08-04"),
    _spec("CC101", "연필화 기초", "취미여가", "금 10:00~11:50", "임현규", "15,000원", 20, "5실(2층)", "2026-08-07"),
    _spec("CC102", "연필화 심화", "취미여가", "금 13:00~14:50", "임현규", "15,000원", 20, "5실(2층)", "2026-08-07"),
    _spec("D0111", "요가1", "건강증진", "화 10:00~10:50", "이은정", "7,500원", 30, "4실(2층)", "2026-08-04"),
    _spec("D0112", "요가2", "건강증진", "화 11:00~11:50", "이은정", "7,500원", 30, "4실(2층)", "2026-08-04"),
    _spec("D0113", "매트필라테스1", "건강증진", "금 13:00~13:50", "변지민", "무료", 30, "4실(2층)", "2026-08-07"),
    _spec(
        "D0114",
        "매트필라테스2",
        "건강증진",
        "금 14:00~14:50",
        "변지민",
        "무료",
        30,
        "4실(2층)",
        "2026-08-07",
        f"{DEFAULT_TARGET} 중 1951~1961년생",
    ),
    _spec("D0305", "기공체조1", "건강증진", "수 13:00~13:50", "김미경", "무료", 30, "4실(2층)", "2026-08-05"),
    _spec("D0306", "기공체조2", "건강증진", "수 14:00~14:50", "김미경", "무료", 30, "4실(2층)", "2026-08-05"),
    _spec("D0105", "발건강교실1", "건강증진", "월 13:00~13:50", "이정수", "7,500원", 20, "4실(2층)", "2026-08-03"),
    _spec("D0106", "발건강교실2", "건강증진", "월 14:00~14:50", "이정수", "7,500원", 20, "4실(2층)", "2026-08-03"),
    _spec("D0103", "건강체조1", "건강증진", "금 10:00~10:50", "김나경", "7,500원", 30, "4실(2층)", "2026-08-07"),
    _spec("D0104", "건강체조2", "건강증진", "금 11:00~11:50", "김나경", "7,500원", 30, "4실(2층)", "2026-08-07"),
    _spec("D0116", "실버태권도", "건강증진", "목 11:00~11:50", "김병태", "7,500원", 30, "4실(2층)", "2026-08-06"),
    _spec("D0108", "시니어워킹1", "건강증진", "금 10:00~10:50", "박동희", "7,500원", 20, "강당(4층)", "2026-08-07"),
    _spec("D0117", "시니어워킹2", "건강증진", "금 11:00~11:50", "박동희", "7,500원", 20, "강당(4층)", "2026-08-07"),
    _spec("D0403", "탁구교실", "건강증진", "수 10:00~11:50", "강경수", "15,000원", 24, "탁구장(1층)", "2026-08-05"),
)

TERM_6_BY_CODE = {spec.code: spec for spec in TERM_6_SPECS}
TERM_6_END_DATE = "2026-12-18"
TERM_6_APPLY_START = "2026-07-06"
TERM_6_APPLY_END = "2026-07-10"
TERM_6_EXTRA_APPLY = "2026-07-20 ~ 2026-07-24"


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path.rstrip("/") in {"", "/course"}
        and not parsed.query
        and not parsed.fragment
    )


def _session_factory() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; MooncenCrawler/1.0)",
            "X-Tenant-Domain": HOST,
        }
    )
    return client


def _load_data(
    client: Any,
    url: str,
    *,
    timeout: int,
    request_counter: list[int],
) -> Any:
    response = client.get(url, timeout=timeout)
    request_counter[0] += 1
    status_code = int(getattr(response, "status_code", 0))
    if status_code != 200:
        raise GimhaeDongbuContractError(f"{url}: HTTP {status_code}")
    content = getattr(response, "content", b"")
    if len(content) > MAX_RESPONSE_BYTES:
        raise GimhaeDongbuContractError(f"{url}: response exceeds byte cap")
    try:
        payload = response.json()
    except Exception as exc:
        raise GimhaeDongbuContractError(f"{url}: invalid JSON") from exc
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        code = _clean(payload.get("errorCode") if isinstance(payload, Mapping) else "")
        raise GimhaeDongbuContractError(f"{url}: unsuccessful envelope {code}")
    return payload.get("data")


def _active_term(terms: Any) -> Mapping[str, Any]:
    if not isinstance(terms, list):
        raise GimhaeDongbuContractError("terms payload is not a list")
    active = [item for item in terms if isinstance(item, Mapping) and item.get("activeYn") == "Y"]
    if len(active) != 1:
        raise GimhaeDongbuContractError(f"expected one active term, found {len(active)}")
    term_cd = active[0].get("termCd")
    if not isinstance(term_cd, int) or isinstance(term_cd, bool) or term_cd < 1:
        raise GimhaeDongbuContractError("active term has invalid termCd")
    return active[0]


def _valid_source_id(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise GimhaeDongbuContractError("course has invalid prgCdid")
    return value


def _is_test_course(item: Mapping[str, Any]) -> bool:
    code = _clean(item.get("prgCd")).upper()
    title = _clean(item.get("prgNm"))
    return code.startswith("PP") or title.startswith("테스트")


def _category_for_code(code: str) -> str:
    upper = code.upper()
    if upper.startswith("B"):
        return "평생교육"
    if upper.startswith("R"):
        return "정보화교육"
    if upper.startswith(("C", "CC")):
        return "취미여가"
    if upper.startswith("D"):
        return "건강증진"
    raise GimhaeDongbuContractError(f"{code}: unknown category code")


def _course_detail_url(source_id: int, term_cd: int, view_gbn: str = "P001") -> str:
    query = urlencode(
        [("termCd", str(term_cd)), ("viewGbn", view_gbn)],
        doseq=False,
    )
    return f"{COURSE_URL}/{source_id}?{query}"


def _status_for_period(cutoff: date, apply_start: str, apply_end: str) -> str:
    start = date.fromisoformat(apply_start)
    end = date.fromisoformat(apply_end)
    if cutoff < start:
        return "접수예정"
    if cutoff <= end:
        return "접수중"
    return "접수마감"


def _term6_rows(
    source_rows: list[Mapping[str, Any]],
    *,
    term_cd: int,
    cutoff: date,
) -> tuple[list[dict[str, Any]], int]:
    if len(source_rows) > MAX_PUBLIC_ROWS:
        raise GimhaeDongbuContractError("lottery ledger exceeds row cap")
    advertised_totals = {
        item.get("totalRows") for item in source_rows if isinstance(item, Mapping) and item.get("totalRows") is not None
    }
    if advertised_totals and advertised_totals != {len(source_rows)}:
        raise GimhaeDongbuContractError(f"lottery total mismatch: {sorted(str(value) for value in advertised_totals)}")
    real_rows = [item for item in source_rows if isinstance(item, Mapping) and not _is_test_course(item)]
    excluded_test_count = len(source_rows) - len(real_rows)
    by_code: dict[str, Mapping[str, Any]] = {}
    source_ids: set[int] = set()
    for item in real_rows:
        code = _clean(item.get("prgCd"))
        source_id = _valid_source_id(item.get("prgCdid"))
        if not code or code in by_code:
            raise GimhaeDongbuContractError(f"duplicate or blank programme code: {code!r}")
        if source_id in source_ids:
            raise GimhaeDongbuContractError(f"duplicate source identity: {source_id}")
        by_code[code] = item
        source_ids.add(source_id)
    expected = set(TERM_6_BY_CODE)
    actual = set(by_code)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise GimhaeDongbuContractError(f"term 6 identity contract changed; missing={missing}, unknown={unknown}")

    status = _status_for_period(cutoff, TERM_6_APPLY_START, TERM_6_APPLY_END)
    rows: list[dict[str, Any]] = []
    for spec in TERM_6_SPECS:
        source = by_code[spec.code]
        source_capacity = source.get("maxPersons")
        if not isinstance(source_capacity, int) or isinstance(source_capacity, bool) or source_capacity < 1:
            raise GimhaeDongbuContractError(f"{spec.code}: invalid public-ledger capacity {source_capacity!r}")
        source_id = _valid_source_id(source.get("prgCdid"))
        raw_url = _course_detail_url(source_id, term_cd)
        rows.append(
            {
                "provider": PROVIDER,
                "provider_course_id": f"{PROVIDER}:{term_cd}:{source_id}",
                "title": spec.title,
                "title_raw": _clean(source.get("prgNm")) or spec.title,
                "branch": BRANCH,
                "branch_code": PROVIDER,
                "category": spec.category,
                "category_raw": spec.category,
                "raw_url": raw_url,
                "application_url": COURSE_URL,
                "application_type": "ONLINE_APPLY",
                "status": status,
                "period": f"{spec.start_date} ~ {TERM_6_END_DATE}",
                "apply_period": f"{TERM_6_APPLY_START} ~ {TERM_6_APPLY_END}",
                "apply_start": TERM_6_APPLY_START,
                "apply_end": TERM_6_APPLY_END,
                "schedule_raw": spec.schedule,
                "target": spec.target,
                "fee": spec.fee,
                "capacity": f"{source_capacity}명",
                "capacity_current": int(source.get("recvCount") or 0),
                "capacity_total": source_capacity,
                "instructor": spec.instructor,
                "venue_name": f"{BRANCH} {spec.venue}",
                "venue_address": ADDRESS,
                "address": ADDRESS,
                "description": (
                    f"2026년 하반기 노년사회화교육. 강사 {spec.instructor}. "
                    f"추가접수 {TERM_6_EXTRA_APPLY}, 잔여 강좌 방문접수."
                ),
                "raw_fields": {
                    "parser": PARSER,
                    "termCd": term_cd,
                    "prgCdid": source_id,
                    "prgCd": spec.code,
                    "source_prgNm": _clean(source.get("prgNm")),
                    "recvCount": int(source.get("recvCount") or 0),
                    "maxPersons": source_capacity,
                    "brochureMaxPersons": spec.capacity,
                    "brochure_notice_url": BROCHURE_NOTICE_URL,
                    "brochure_image_url": BROCHURE_IMAGE_URL,
                },
            }
        )
    return rows, excluded_test_count


def _date_range(value: Any) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) < 2:
        raise GimhaeDongbuContractError(f"invalid education date range: {_clean(value)!r}")
    parsed = [date(int(year), int(month), int(day)) for year, month, day in matches[:2]]
    if parsed[0] > parsed[1]:
        raise GimhaeDongbuContractError("education date range is reversed")
    return parsed[0], parsed[1]


def _dynamic_rows(
    source_rows: list[Mapping[str, Any]],
    *,
    term_cd: int,
    term_status: Mapping[str, Any],
    cutoff: date,
) -> list[dict[str, Any]]:
    if len(source_rows) > MAX_PUBLIC_ROWS:
        raise GimhaeDongbuContractError("course catalogue exceeds row cap")
    recv_start = _clean(term_status.get("recvSdate"))
    recv_end = _clean(term_status.get("recvEdate"))
    if not recv_start or not recv_end:
        raise GimhaeDongbuContractError("term status lacks reception dates")
    status_by_date = _status_for_period(cutoff, recv_start, recv_end)
    rows: list[dict[str, Any]] = []
    source_ids: set[int] = set()
    for item in source_rows:
        if not isinstance(item, Mapping) or _is_test_course(item):
            continue
        source_id = _valid_source_id(item.get("prgCdid"))
        if source_id in source_ids:
            raise GimhaeDongbuContractError(f"duplicate source identity: {source_id}")
        source_ids.add(source_id)
        code = _clean(item.get("prgCd"))
        title = _clean(item.get("prgNm"))
        schedule = _clean(item.get("eduTimes"))
        duration = _clean(item.get("eduDuration"))
        venue = _clean(item.get("place"))
        if not code or not title or not schedule or not duration or not venue:
            raise GimhaeDongbuContractError(f"{source_id}: catalogue row lacks code/title/time/date/place")
        if not _TIME_RE.search(schedule):
            raise GimhaeDongbuContractError(f"{source_id}: schedule lacks a clock time")
        start, end = _date_range(duration)
        if end < cutoff:
            continue
        amount = item.get("amt")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0:
            raise GimhaeDongbuContractError(f"{source_id}: invalid fee")
        capacity = item.get("maxPersons")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise GimhaeDongbuContractError(f"{source_id}: invalid capacity")
        closed = item.get("closedYn") == "Y" or int(item.get("remainCount") or 0) <= 0
        status = "접수마감" if closed else status_by_date
        view_gbn = _clean(item.get("listGbn")) or "P001"
        raw_url = _course_detail_url(source_id, term_cd, view_gbn)
        rows.append(
            {
                "provider": PROVIDER,
                "provider_course_id": f"{PROVIDER}:{term_cd}:{source_id}",
                "title": title,
                "branch": BRANCH,
                "branch_code": PROVIDER,
                "category": _category_for_code(code),
                "category_raw": _category_for_code(code),
                "raw_url": raw_url,
                "application_url": COURSE_URL,
                "application_type": "ONLINE_APPLY",
                "status": status,
                "period": f"{start.isoformat()} ~ {end.isoformat()}",
                "apply_period": f"{recv_start} ~ {recv_end}",
                "apply_start": recv_start,
                "apply_end": recv_end,
                "schedule_raw": schedule,
                "target": DEFAULT_TARGET,
                "fee": "무료" if amount == 0 else f"{int(amount):,}원",
                "capacity": f"{capacity}명",
                "capacity_current": int(item.get("recvCount") or 0),
                "capacity_total": capacity,
                "venue_name": f"{BRANCH} {venue}",
                "venue_address": ADDRESS,
                "address": ADDRESS,
                "description": f"{_clean(item.get('durationNm'))} {schedule}".strip(),
                "raw_fields": {
                    "parser": PARSER,
                    "termCd": term_cd,
                    "prgCdid": source_id,
                    "prgCd": code,
                    "source": dict(item),
                },
            }
        )
    return rows


def _base_meta(cutoff: date) -> dict[str, Any]:
    return {
        "pages": 0,
        "detail_pages": 0,
        "discovered_links": 0,
        "reservation_discovery_links": 0,
        "reservation_fallback_pages": 0,
        "pagination_detected": False,
        "recursion_depth": 0,
        "configured_collection_error": "",
        "no_current_data": False,
        "no_current_reason": "",
        "cutoff_date": cutoff.isoformat(),
        "snapshot_complete": False,
        "full_snapshot_validated": False,
    }


def collect(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 10,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[Callable[[], Any]] = None,
    dedupe_rows: Optional[Callable[[list[dict[str, Any]]], Any]] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    try:
        cutoff = _audit_date(today)
    except (TypeError, ValueError):
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _base_meta(cutoff)
        meta["configured_collection_error"] = "today is invalid"
        return [], PARSER, meta
    meta = _base_meta(cutoff)
    if not is_target(target):
        meta["configured_collection_error"] = "target does not match official course portal"
        return [], PARSER, meta
    try:
        request_timeout = int(timeout)
        page_budget = int(max_pages)
        row_budget = int(detail_limit)
        if request_timeout < 1 or page_budget < 4 or row_budget < 0:
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "timeout/max_pages/detail_limit are invalid"
        return [], PARSER, meta

    client: Any = None
    request_counter = [0]
    try:
        client = (session_factory or _session_factory)()
        agency = _load_data(
            client,
            AGENCY_INFO_URL,
            timeout=request_timeout,
            request_counter=request_counter,
        )
        if not isinstance(agency, Mapping) or agency.get("customerNo") != 1956:
            raise GimhaeDongbuContractError("agency identity changed")
        if _clean(agency.get("agencyName")) != "김해시동부노인종합복지관":
            raise GimhaeDongbuContractError("agency name changed")
        terms = _load_data(
            client,
            TERMS_URL,
            timeout=request_timeout,
            request_counter=request_counter,
        )
        active = _active_term(terms)
        term_cd = int(active["termCd"])
        term_status = _load_data(
            client,
            TERM_STATUS_URL,
            timeout=request_timeout,
            request_counter=request_counter,
        )
        if not isinstance(term_status, Mapping):
            raise GimhaeDongbuContractError("term status payload is not an object")
        course_rows = _load_data(
            client,
            COURSES_URL,
            timeout=request_timeout,
            request_counter=request_counter,
        )
        if not isinstance(course_rows, list):
            raise GimhaeDongbuContractError("course catalogue payload is not a list")

        source_mode = "live_course_catalogue"
        excluded_test_count = 0
        if term_cd == 6:
            if page_budget < 5:
                raise GimhaeDongbuContractError("max_pages does not allow lottery ledger request")
            lottery_url = f"{LOTTERY_RESULTS_URL}?{urlencode([('termCd', term_cd), ('size', 200)])}"
            lottery_rows = _load_data(
                client,
                lottery_url,
                timeout=request_timeout,
                request_counter=request_counter,
            )
            if not isinstance(lottery_rows, list):
                raise GimhaeDongbuContractError("lottery ledger payload is not a list")
            rows, excluded_test_count = _term6_rows(
                lottery_rows,
                term_cd=term_cd,
                cutoff=cutoff,
            )
            source_mode = "lottery_ledger_joined_to_official_brochure"
            source_count = len(lottery_rows)
        elif course_rows:
            rows = _dynamic_rows(
                course_rows,
                term_cd=term_cd,
                term_status=term_status,
                cutoff=cutoff,
            )
            source_count = len(course_rows)
        else:
            rows = []
            source_count = 0
            meta["no_current_data"] = True
            meta["no_current_reason"] = "official active-term course catalogue is empty"

        if len(rows) > row_budget:
            raise GimhaeDongbuContractError(f"detail_limit cap allows {row_budget} of {len(rows)} rows")
        required = ("title", "target", "fee", "period", "venue_name", "category", "schedule_raw")
        incomplete = [
            row.get("provider_course_id") for row in rows if any(not _clean(row.get(field)) for field in required)
        ]
        if incomplete:
            raise GimhaeDongbuContractError(f"{len(incomplete)} rows lack target/fee/date/place/category/time")
        identities = [_clean(row.get("provider_course_id")) for row in rows]
        if len(identities) != len(set(identities)):
            raise GimhaeDongbuContractError("duplicate output identities")
        if dedupe_rows:
            deduped = list(dedupe_rows(rows))
            if len(deduped) != len(rows):
                raise GimhaeDongbuContractError(f"dedupe changed complete row count {len(rows)} to {len(deduped)}")
            rows = deduped
        meta.update(
            {
                "pages": request_counter[0],
                "api_requests": request_counter[0],
                "source_mode": source_mode,
                "active_term_cd": term_cd,
                "active_term_title": _clean(active.get("termTitle")),
                "source_rows": source_count,
                "catalogue_rows": len(course_rows),
                "excluded_test_rows": excluded_test_count,
                "returned_count": len(rows),
                "discovered_links": len(rows),
                "reservation_discovery_links": len(rows),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "required_field_complete": len(rows),
                "brochure_notice_url": BROCHURE_NOTICE_URL,
                "brochure_image_url": BROCHURE_IMAGE_URL,
            }
        )
        return rows, PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "pages": request_counter[0],
                "api_requests": request_counter[0],
                "returned_count": 0,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], PARSER, meta
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


__all__ = [
    "ADDRESS",
    "AGENCY_INFO_URL",
    "BRANCH",
    "BROCHURE_IMAGE_URL",
    "BROCHURE_NOTICE_URL",
    "COURSES_URL",
    "COURSE_URL",
    "GimhaeDongbuContractError",
    "LOTTERY_RESULTS_URL",
    "PARSER",
    "PROVIDER",
    "ROOT_URL",
    "TERM_6_BY_CODE",
    "TERM_6_SPECS",
    "TERM_STATUS_URL",
    "TERMS_URL",
    "collect",
    "is_target",
]
