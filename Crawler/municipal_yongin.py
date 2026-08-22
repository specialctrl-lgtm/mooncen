"""Fail-closed public education collectors for Yongin City.

The audited boundary contains eight independent owners: the municipal
reservation catalogue, three district partitions of the resident-centre
catalogue, the city-library catalogue, the Cultural Foundation's Gongsaeng
Square academy, the Youth Foundation One-click catalogue, and the Youth
Foundation culture/sports course catalogue.

Only public list, directory, and read-only detail routes are requested.
Application, cancellation, payment, login, member/family, applicant-history,
attachment, and identity routes are deliberately forbidden.  Free-form detail
text, instructors, staff contacts, and attachments are not retained.

The already operational ``YONGIN_LIFELONG_LEARNING`` owner and the Yongin
partition of ``GYEONGGI_GSEEK`` are documented as non-executing overlaps and
are not reimplemented here.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import calendar
from datetime import date, datetime
import hashlib
import html
import json
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YONGIN_CITY_CODE = "4146000000"
YONGIN_CITY_NAME = "경기도 용인시"
YONGIN_DISTRICTS: Mapping[str, tuple[str, str]] = {
    "처인구": ("4146100000", "경기도 용인시 처인구"),
    "기흥구": ("4146300000", "경기도 용인시 기흥구"),
    "수지구": ("4146500000", "경기도 용인시 수지구"),
}

YONGIN_RESERVATION_PROVIDER = "MUNI_RESVE_YONGIN_GO_KR_221336AC"
YONGIN_RESERVATION_CANDIDATE_ID = "MUNI_IR_45E5AF9C5003"
YONGIN_RESERVATION_URL = (
    "https://resve.yongin.go.kr/resve/manage/"
    "BD_selectResveManageList.do?q_lclas=CL_02"
)
YONGIN_RESERVATION_LIST_BASE = (
    "https://resve.yongin.go.kr/resve/manage/BD_selectResveManageList.do"
)
YONGIN_RESERVATION_DETAIL_BASE = (
    "https://resve.yongin.go.kr/resve/manage/BD_selectResveManage.do"
)
YONGIN_RESERVATION_CATEGORIES = ("CL_01", "CL_02")
YONGIN_RESERVATION_PAGE_SIZE = 8
YONGIN_RESERVATION_NON_PROGRAM_BRANCHES = frozenset(
    {
        "문화관광해설사 예약",
        "생애주기별 찾아가는 안전교육",
        "찾아가는 마을세무사",
    }
)

YONGIN_JACHI_API_URL = "https://jachi.yongin.go.kr/rest/lecture/list"
YONGIN_JACHI_COMPANY_URL = "https://jachi.yongin.go.kr/rest/common/company"
YONGIN_JACHI_PAGE_SIZE = 100
YONGIN_JACHI_ADMIN_COMPANY = ("YIJM00", "용인시청_본부")

YONGIN_CHEOIN_PROVIDER = "MUNI_JACHI_YONGIN_GO_KR_10340408"
YONGIN_CHEOIN_CANDIDATE_ID = "MUNI_IR_45CDD3249830"
YONGIN_CHEOIN_URL = "https://jachi.yongin.go.kr/cheoingu/79"
YONGIN_GIHEUNG_PROVIDER = "MUNI_JACHI_YONGIN_GO_KR_60025DB9"
YONGIN_GIHEUNG_CANDIDATE_ID = "MUNI_IR_EFE3A66475B8"
YONGIN_GIHEUNG_URL = "https://jachi.yongin.go.kr/giheunggu/207"
YONGIN_SUJI_PROVIDER = "MUNI_JACHI_YONGIN_GO_KR_91C5118C"
YONGIN_SUJI_CANDIDATE_ID = "MUNI_IR_1F29A18E4FBC"
YONGIN_SUJI_URL = "https://jachi.yongin.go.kr/sujigu/321"

YONGIN_JACHI_BRANCHES: Mapping[str, Mapping[str, str]] = {
    "cheoin": {
        "YIJM01": "포곡읍",
        "YIJM02": "모현읍",
        "YIJM03": "이동읍",
        "YIJM04": "남사읍",
        "YIJM05": "원삼면",
        "YIJM06": "백암면",
        "YIJM07": "양지읍",
        "YIJM08": "중앙동",
        "YIJM09": "역북동",
        "YIJM10": "유림1동",
        "YIJM11": "동부동",
        "YIJM33": "삼가동",
    },
    "giheung": {
        "YIJM12": "신갈동",
        "YIJM13": "영덕1동",
        "YIJM14": "구갈동",
        "YIJM15": "상갈동",
        "YIJM16": "기흥동",
        "YIJM17": "서농동",
        "YIJM18": "구성동",
        "YIJM19": "마북동",
        "YIJM20": "동백2동",
        "YIJM32": "동백3동",
        "YIJM21": "상하동",
        "YIJM22": "보정동",
        "YIJM34": "보라동",
    },
    "suji": {
        "YIJM23": "풍덕천1동 주민자치센터",
        "YIJM24": "풍덕천2동",
        "YIJM25": "신봉동",
        "YIJM26": "죽전1동",
        "YIJM27": "죽전2동",
        "YIJM28": "동천동",
        "YIJM29": "상현1동",
        "YIJM30": "상현2동",
        "YIJM31": "성복동",
    },
}

YONGIN_JACHI_OWNERS: Mapping[str, tuple[str, str, str, str]] = {
    YONGIN_CHEOIN_PROVIDER: (
        "cheoin", YONGIN_CHEOIN_URL, "용인시 처인구", YONGIN_CHEOIN_CANDIDATE_ID
    ),
    YONGIN_GIHEUNG_PROVIDER: (
        "giheung", YONGIN_GIHEUNG_URL, "용인시 기흥구", YONGIN_GIHEUNG_CANDIDATE_ID
    ),
    YONGIN_SUJI_PROVIDER: (
        "suji", YONGIN_SUJI_URL, "용인시 수지구", YONGIN_SUJI_CANDIDATE_ID
    ),
}

YONGIN_LIBRARY_PROVIDER = "MUNI_LIB_YONGIN_GO_KR_B7626320"
YONGIN_LIBRARY_CANDIDATE_ID = "MUNI_IR_6409719F2B72"
YONGIN_LIBRARY_URL = (
    "https://lib.yongin.go.kr/yongin/menu/10264/program/30027/"
    "lectureList.do?manageCd=ALL"
)
YONGIN_LIBRARY_VACATION_URL = (
    "https://lib.yongin.go.kr/yongin/menu/10266/program/30069/"
    "vacationCourseList.do?manageCd=ALL"
)
YONGIN_LIBRARY_PAGE_SIZE = 40
YONGIN_LIBRARY_BRANCHES: Mapping[str, str] = {
    "CE": "도서관정책과",
    "MA": "용인중앙도서관",
    "MI": "구갈희망누리도서관",
    "MD": "구성도서관",
    "MK": "기흥도서관",
    "MY": "남사도서관",
    "MF": "동백도서관",
    "NA": "동천도서관",
    "ML": "모현도서관",
    "MM": "보라도서관",
    "NU": "보정도서관",
    "MO": "상현도서관",
    "MZ": "서농도서관",
    "NB": "성복도서관",
    "MB": "수지도서관",
    "MJ": "양지해밀도서관",
    "NN": "영덕도서관",
    "MX": "이동꿈틀도서관",
    "ME": "죽전도서관",
    "MP": "청덕도서관",
    "MC": "포곡도서관",
    "MN": "흥덕도서관",
}
YONGIN_LIBRARY_LABELS: Mapping[str, str] = {
    "정책": "도서관정책과",
    "중앙": "용인중앙도서관",
    "구갈": "구갈희망누리도서관",
    "구성": "구성도서관",
    "기흥": "기흥도서관",
    "남사": "남사도서관",
    "동백": "동백도서관",
    "동천": "동천도서관",
    "모현": "모현도서관",
    "보라": "보라도서관",
    "보정": "보정도서관",
    "상현": "상현도서관",
    "서농": "서농도서관",
    "성복": "성복도서관",
    "수지": "수지도서관",
    "양지": "양지해밀도서관",
    "양지해밀": "양지해밀도서관",
    "영덕": "영덕도서관",
    "이동": "이동꿈틀도서관",
    "이동꿈틀": "이동꿈틀도서관",
    "죽전": "죽전도서관",
    "청덕": "청덕도서관",
    "포곡": "포곡도서관",
    "흥덕": "흥덕도서관",
}

YONGIN_YICF_PROVIDER = "MUNI_WWW_YICF_OR_KR_B2E137D5"
YONGIN_YICF_CANDIDATE_ID = "MUNI_IR_0676AF94DDA1"
YONGIN_YICF_URL = (
    "https://www.yicf.or.kr/ccity/cop/lec/PotalLectureList.do?"
    "lecCt1=CATEGORY_ID_00000011"
)
YONGIN_YICF_DETAIL_BASE = (
    "https://www.yicf.or.kr/ccity/cop/lec/PotalLectureDetail.do"
)
YONGIN_YICF_BRANCH = "용인문화도시플랫폼 공생광장"
YONGIN_YICF_ADDRESS = "경기도 용인시 처인구 동백죽전대로 61 용인어린이상상의숲"
YONGIN_YICF_PAGE_SIZE = 9

YONGIN_ONECLICK_PROVIDER = "MUNI_YIYF_OR_KR_F56DFD54"
YONGIN_ONECLICK_CANDIDATE_ID = "MUNI_IR_EFC7E0BCAC38"
YONGIN_ONECLICK_URL = (
    "https://yiyf.or.kr/oneclick/lay1/program/S666T668C674/"
    "programlist/list.do"
)
YONGIN_ONECLICK_DETAIL_BASE = (
    "https://yiyf.or.kr/oneclick/lay1/program/S666T668C674/"
    "programlist/view.do"
)
YONGIN_ONECLICK_BRANCH = "용인미래교육센터"
YONGIN_ONECLICK_PAGE_SIZE = 10

YONGIN_YIYF_COURSE_PROVIDER = "MUNI_SPORTS_YIYF_OR_KR_206DDBA6"
YONGIN_YIYF_COURSE_CANDIDATE_ID = "MUNI_IR_612302071933"
YONGIN_YIYF_COURSE_URL = (
    "https://sports.yiyf.or.kr/main_new/m03/m03_01_list_edu.asp"
)
YONGIN_YIYF_SPORTS_URL = (
    "https://sports.yiyf.or.kr/main_new/m03/m03_01_list.asp"
)
YONGIN_YIYF_PAGE_SIZE = 50
YONGIN_YIYF_EDUCATION_BRANCHES: Mapping[str, str] = {
    "10002": "용인청소년수련관(교육)",
    "10005": "유림청소년문화의집",
    "10004": "신갈청소년문화의집",
    "10006": "수지청소년문화의집",
    "10010": "동천청소년문화의집",
    "10009": "흥덕청소년문화의집",
    "10011": "동백청소년문화의집",
    "10012": "보정청소년문화의집",
    "10008": "처인성어울림센터",
}
YONGIN_YIYF_SPORTS_BRANCHES: Mapping[str, str] = {
    "10003": "용인청소년수련관(체육)",
    "10013": "용천초어울림센터",
}

YONGIN_EXECUTING_TARGETS: tuple[tuple[str, str], ...] = (
    (YONGIN_RESERVATION_PROVIDER, YONGIN_RESERVATION_URL),
    *((provider, values[1]) for provider, values in YONGIN_JACHI_OWNERS.items()),
    (YONGIN_LIBRARY_PROVIDER, YONGIN_LIBRARY_URL),
    (YONGIN_YICF_PROVIDER, YONGIN_YICF_URL),
    (YONGIN_ONECLICK_PROVIDER, YONGIN_ONECLICK_URL),
    (YONGIN_YIYF_COURSE_PROVIDER, YONGIN_YIYF_COURSE_URL),
)

YONGIN_RESERVATION_PARSER = (
    "yongin_reservation_two_categories+exact_empty_sentinels+stable_edges+"
    "all_public_details+audited_schema_ineligible_disabled_cards+"
    "standard_required_fields"
)
YONGIN_JACHI_PARSER = (
    "yongin_jachi_district_partition+official_company_directory+"
    "per_branch_sentinels+stable_edges+all_public_details+"
    "standard_required_fields+source_period_omission"
)
YONGIN_LIBRARY_PARSER = (
    "yongin_library_two_ledgers+official_22_branch_directory+"
    "exact_empty_sentinels+stable_edges+all_current_details+"
    "standard_required_fields"
)
YONGIN_YICF_PARSER = (
    "yongin_yicf_full_category+exact_empty_sentinel+stable_edges+"
    "all_current_details+standard_required_fields"
)
YONGIN_ONECLICK_PARSER = (
    "yongin_oneclick_declared_total+exact_empty_sentinel+stable_edge+"
    "all_details+required_field_provenance"
)
YONGIN_YIYF_COURSE_PARSER = (
    "yongin_yiyf_education_and_sports_branches+per_branch_sentinels+"
    "stable_edges+all_current_details+official_delay_retries+standard_required_fields"
)
YONGIN_DISPATCH_PARSER = "yongin_owner_dispatch"

YONGIN_DEFAULT_MAX_PAGES = 220
YONGIN_DEFAULT_DETAIL_LIMIT = 850
YONGIN_DEFAULT_MAX_REQUESTS = 1_250
YONGIN_DEFAULT_DETAIL_WORKERS = 8

YONGIN_EXISTING_LIFELONG_PROVIDER = "YONGIN_LIFELONG_LEARNING"
YONGIN_GSEEK_PARENT_PROVIDER = "GYEONGGI_GSEEK"

YONGIN_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "municipal_reservation": {
        "provider": YONGIN_RESERVATION_PROVIDER,
        "url": YONGIN_RESERVATION_URL,
        "decision": "one_owner_for_CL_01_experience_and_CL_02_education",
    },
    "resident_centres": {
        "providers": tuple(YONGIN_JACHI_OWNERS),
        "decision": "three_disjoint_district_partitions_of_one_city_directory",
    },
    "libraries": {
        "provider": YONGIN_LIBRARY_PROVIDER,
        "url": YONGIN_LIBRARY_URL,
        "decision": "one_owner_for_lecture_and_vacation_ledgers_all_22_branches",
    },
    "cultural_foundation": {
        "provider": YONGIN_YICF_PROVIDER,
        "url": YONGIN_YICF_URL,
        "decision": "full_category_only_subcategory_tabs_are_subsets",
    },
    "youth_oneclick": {
        "provider": YONGIN_ONECLICK_PROVIDER,
        "url": YONGIN_ONECLICK_URL,
        "decision": "structured_catalogue_supersedes_facility_notice_boards",
    },
    "youth_courses": {
        "provider": YONGIN_YIYF_COURSE_PROVIDER,
        "url": YONGIN_YIYF_COURSE_URL,
        "decision": "one_owner_for_nine_culture_and_two_sports_branches",
    },
    "lifelong": {
        "provider": YONGIN_EXISTING_LIFELONG_PROVIDER,
        "url": "https://lll.yongin.go.kr/yongin/rgEdu/list.do",
        "decision": "existing_owner_not_reimplemented",
    },
    "provincial_gseek": {
        "provider": YONGIN_GSEEK_PARENT_PROVIDER,
        "url": "https://www.gseek.kr/user/course/offline/list",
        "decision": "existing_parent_owner_region_4146000000_not_duplicated",
    },
    "cultural_group_calendar": {
        "url": (
            "https://www.yicf.or.kr/ccity/cop/ism/"
            "IntegratedServiceMonthList.do?isTypeCode=2"
        ),
        "decision": "application_calendar_with_no_current_or_future_slots_at_audit",
    },
    "city_online_forms": {
        "url": "https://www.yongin.go.kr/user/onlineReqst/",
        "decision": "mixed_one_off_application_and_identity_forms_not_course_ledger",
    },
}

YONGIN_LIVE_AUDIT_BASELINE: Mapping[str, Mapping[str, Any]] = {
    "reservation": {"checked_at": "2026-07-23", "source_total": 88, "branch_count": 16},
    "jachi_cheoin": {"checked_at": "2026-07-29", "source_total": 224, "branch_count": 12},
    "jachi_giheung": {"checked_at": "2026-07-29", "source_total": 757, "branch_count": 13},
    "jachi_suji": {"checked_at": "2026-07-29", "source_total": 547, "branch_count": 9},
    "library": {"checked_at": "2026-07-23", "source_total": 1868, "current_count": 217, "branch_count": 22},
    "yicf": {"checked_at": "2026-07-23", "source_total": 27, "current_count": 27},
    "oneclick": {"checked_at": "2026-07-23", "source_total": 10, "current_count": 10},
    "yiyf_courses": {
        "checked_at": "2026-07-29",
        "source_total": 244,
        "current_count": 241,
        "branch_count": 11,
        "persistent_delay_pages": 2,
    },
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]


class YonginContractError(ValueError):
    """Raised when an audited Yongin source no longer matches its contract."""


_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_MONTH_RANGE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*년?\s*[.]?\s*(\d{1,2})\s*월?\s*[.]?\s*~\s*"
    r"(?:(20\d{2})\s*년?\s*[.]?\s*)?(\d{1,2})\s*월?"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_ITEM_ID_RE = re.compile(r"[1-9]\d*")

_ALLOWED_HOSTS = frozenset(
    {
        "resve.yongin.go.kr",
        "jachi.yongin.go.kr",
        "lib.yongin.go.kr",
        "www.yicf.or.kr",
        "yiyf.or.kr",
        "sports.yiyf.or.kr",
    }
)
_FORBIDDEN_PATH_PARTS = (
    "/apply",
    "applylist",
    "applyregist",
    "application.do",
    "cancel.do",
    "writeview.do",
    "?action=write",
    "/mypage",
    "/member/",
    "/login",
    "/payment",
    "/download",
    "/file/",
    "/family_list",
    "/list_reregistration",
    "/integratedreservationmypage",
)
_ALLOWED_POST_URLS = frozenset({YONGIN_JACHI_API_URL, YONGIN_JACHI_COMPANY_URL})


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", html.unescape(str(value or "")).replace("\xa0", " ")).strip()


def _norm(value: Any) -> str:
    return "".join(char.lower() for char in _clean(value) if char.isalnum())


def _target_value(target: Any, name: str) -> Any:
    return target.get(name) if isinstance(target, Mapping) else getattr(target, name, None)


def _target_provider(target: Any) -> str:
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


def _positive(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise YonginContractError(f"{label} must be a positive integer") from exc
    if result < 1:
        raise YonginContractError(f"{label} must be a positive integer")
    return result


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            result.append(date(*(int(part) for part in match.groups())))
        except ValueError:
            continue
    return result


def _date_range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if not values:
        return "", "", ""
    start = values[0]
    end = values[1] if len(values) > 1 else values[0]
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _course_date_range(value: Any) -> tuple[str, str, str, bool]:
    """Return a usable boundary while preserving an official reversed-date flag.

    A few historical Yongin library cards contain an end date earlier than the
    start date.  The first date is still the only defensible occurrence
    boundary, so collapse only those reversed ranges to that official date and
    expose the correction flag to callers.  Missing dates remain missing.
    """
    values = _date_tokens(value)
    if not values:
        return "", "", "", False
    start = values[0]
    if len(values) == 1:
        end = start
    else:
        end = values[1]
    corrected = end < start
    if corrected:
        end = start
    return (
        start.isoformat(),
        end.isoformat(),
        f"{start.isoformat()} ~ {end.isoformat()}",
        corrected,
    )


def _integer(value: Any) -> Optional[int]:
    match = re.search(r"\d+", _clean(value).replace(",", ""))
    return int(match.group()) if match else None


def _contains_pii(value: Any) -> bool:
    text = _clean(value)
    return bool(_PHONE_RE.search(text) or _EMAIL_RE.search(text) or _RESIDENT_ID_RE.search(text))


def _safe_description(value: Any, fallback: Any) -> tuple[str, bool]:
    text = _clean(value)
    if text and not _contains_pii(text):
        return text, False
    return _clean(fallback), bool(text)


def _branch_id(prefix: str, branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}_{digest}"


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _signature(values: Iterable[Any]) -> str:
    payload = json.dumps(list(values), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rows_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    return _signature(
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
        )
        for row in rows
    )


def _status(value: Any) -> str:
    text = _clean(value)
    if any(token in text for token in ("대기신청", "대기접수")):
        return "WAITING"
    if any(token in text for token in ("접수중", "신청중", "신청하기", "접수가능", "신청가능")):
        return "OPEN"
    if any(token in text for token in ("마감", "종료", "접수완료")):
        return "CLOSED"
    if any(token in text for token in ("접수전", "접수대기", "준비중", "예정")):
        return "SCHEDULED"
    return "CLOSED"


def _titles_match(left: Any, right: Any) -> bool:
    a, b = _norm(left), _norm(right)
    return bool(a and b and (a == b or a in b or b in a))


def _exact_target(target: Any, provider: str, canonical_url: str) -> bool:
    if _target_provider(target) != provider or _target_url(target) != canonical_url:
        return False
    parsed = urlparse(_target_url(target))
    return bool(
        parsed.scheme == "https"
        and parsed.hostname in _ALLOWED_HOSTS
        and parsed.port is None
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def is_yongin_education_target(target: Any) -> bool:
    return any(
        _exact_target(target, provider, url)
        for provider, url in YONGIN_EXECUTING_TARGETS
    )


is_target = is_yongin_education_target


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return session


def _guard_url(url: str, method: str = "GET") -> None:
    parsed = urlparse(url)
    lowered = url.lower()
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise YonginContractError("request escaped the audited HTTPS hosts")
    if any(part in lowered for part in _FORBIDDEN_PATH_PARTS):
        raise YonginContractError("application, identity, login, or attachment route is forbidden")
    query = parse_qs(parsed.query)
    if any(_clean(value).lower() == "write" for value in query.get("action", [])):
        raise YonginContractError("write action is forbidden")
    if method == "POST" and url not in _ALLOWED_POST_URLS:
        raise YonginContractError("POST is allowed only for audited public Jachi list APIs")


def _response_status(response: Any) -> int:
    try:
        return int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        return 0


def _validate_response(response: Any, expected_url: str) -> None:
    if _response_status(response) != 200:
        raise YonginContractError(f"unexpected HTTP status {_response_status(response)}")
    if getattr(response, "history", None):
        raise YonginContractError("redirected responses are not accepted")
    # URLs are not HTML text.  In particular, html.unescape() corrupts query
    # names beginning with ``curren`` by interpreting ``&curren`` as the
    # currency entity, so compare the literal final URL here.
    final_url = str(getattr(response, "url", "") or "").strip()
    if final_url and final_url != expected_url:
        raise YonginContractError("response URL escaped the exact requested route")


def _response_soup(response: Any, expected_url: str) -> BeautifulSoup:
    _validate_response(response, expected_url)
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise YonginContractError("empty HTML response")
    if isinstance(content, bytes):
        encoding = str(getattr(response, "encoding", "") or "").strip()
        if encoding:
            # The legacy YIYF server labels CP949-compatible pages euc-kr.
            # Explicit decoding avoids BeautifulSoup misclassifying a page as
            # GB18030 when a particular course title changes its byte profile.
            if encoding.lower().replace("_", "-") in {"euc-kr", "ks-c-5601-1987"}:
                encoding = "cp949"
            content = content.decode(encoding, errors="replace")
    return BeautifulSoup(content, "lxml")


class _Fetcher:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        timeout: int,
        max_requests: int,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        self.session_factory = session_factory
        self.timeout = timeout
        self.max_requests = max_requests
        self.sleeper = sleeper
        self.session: Any = None
        self.physical_requests = 0
        self.retry_count = 0
        self.sessions_created = 0
        self.request_log: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        self.session = None

    @staticmethod
    def _set_headers(session: Any) -> None:
        headers = getattr(session, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                }
            )

    def _new_session(self) -> None:
        self.close()
        self.session = self.session_factory()
        self.sessions_created += 1
        self._set_headers(self.session)

    def _record(self, method: str, url: str) -> None:
        with self._lock:
            if self.physical_requests >= self.max_requests:
                raise YonginContractError(f"max_requests cap {self.max_requests} exhausted")
            self.physical_requests += 1
            self.request_log.append((method, url))

    def _attempt(self, operation: Callable[[], Any]) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                if self.session is None:
                    self._new_session()
                return operation()
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    self.retry_count += 1
                    self._new_session()
                    self.sleeper(0.05)
        assert last_error is not None
        raise last_error

    def get_soup(self, url: str, *, headers: Optional[Mapping[str, str]] = None) -> BeautifulSoup:
        _guard_url(url)

        def operation() -> BeautifulSoup:
            self._record("GET", url)
            response = self.session.get(
                url, timeout=self.timeout, allow_redirects=False, headers=dict(headers or {}) or None
            )
            return _response_soup(response, url)

        return self._attempt(operation)

    def post_json(self, url: str, data: Mapping[str, Any]) -> Any:
        _guard_url(url, "POST")

        def operation() -> Any:
            self._record("POST", url)
            response = self.session.post(
                url, data=dict(data), timeout=self.timeout, allow_redirects=False
            )
            _validate_response(response, url)
            try:
                return response.json()
            except Exception as exc:
                raise YonginContractError("public API returned invalid JSON") from exc

        return self._attempt(operation)

    def get_soups_parallel(
        self,
        urls: list[str],
        *,
        workers: int,
    ) -> list[BeautifulSoup]:
        if workers <= 1 or len(urls) <= 1:
            return [self.get_soup(url) for url in urls]
        for url in urls:
            _guard_url(url)

        def one(url: str) -> BeautifulSoup:
            last_error: Optional[Exception] = None
            for attempt in range(2):
                session: Any = None
                try:
                    session = self.session_factory()
                    with self._lock:
                        self.sessions_created += 1
                    self._set_headers(session)
                    self._record("GET", url)
                    response = session.get(url, timeout=self.timeout, allow_redirects=False)
                    return _response_soup(response, url)
                except Exception as exc:
                    last_error = exc
                    if attempt == 0:
                        with self._lock:
                            self.retry_count += 1
                        self.sleeper(0.05)
                finally:
                    close = getattr(session, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            pass
            assert last_error is not None
            raise last_error

        with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as executor:
            return list(executor.map(one, urls))


def _make_fetcher(
    *,
    session_factory: Optional[SessionFactory],
    allow_raw_requests_for_tests: bool,
    timeout: int,
    max_requests: int,
    sleeper: Sleeper,
) -> _Fetcher:
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            raise YonginContractError("managed session_factory is required")
        session_factory = _default_session_factory
    return _Fetcher(
        session_factory=session_factory,
        timeout=timeout,
        max_requests=_positive(max_requests, "max_requests"),
        sleeper=sleeper,
    )


def _base_meta(parser: str) -> dict[str, Any]:
    return {
        "parser": parser,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "details_complete": False,
        "pagination_detected": False,
        "discovered_links": 0,
        "returned_count": 0,
        "source_total": 0,
        "branch_count": 0,
        "sentinel_count": None,
        "stability_rechecks": 0,
        "detail_pages": 0,
        "application_endpoints_called": 0,
        "configured_collection_error": "",
        "no_current_reason": "",
    }


def _failure_meta(parser: str, exc: Exception, fetcher: Optional[_Fetcher]) -> dict[str, Any]:
    meta = _base_meta(parser)
    meta["configured_collection_error"] = f"{type(exc).__name__}: {exc}"
    if fetcher is not None:
        meta.update(
            {
                "physical_requests": fetcher.physical_requests,
                "retry_count": fetcher.retry_count,
                "request_log": list(fetcher.request_log),
            }
        )
    return meta


def _success_meta(
    parser: str,
    rows: list[dict[str, Any]],
    fetcher: _Fetcher,
    **values: Any,
) -> dict[str, Any]:
    meta = _base_meta(parser)
    meta.update(values)
    meta.update(
        {
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "pagination_complete": True,
            "pagination_exhausted": True,
            "details_complete": True,
            "discovered_links": int(values.get("source_total", len(rows))),
            "returned_count": len(rows),
            "physical_requests": fetcher.physical_requests,
            "retry_count": fetcher.retry_count,
            "application_endpoints_called": 0,
            "identity_sha256": _rows_signature(rows),
            "no_current_reason": "" if rows else "official_complete_snapshot_has_no_current_or_future_rows",
        }
    )
    return meta


def _assert_rows_public(rows: Iterable[Mapping[str, Any]]) -> None:
    forbidden_keys = {
        "phone", "email", "instructor", "teacher", "attachment", "applicant",
        "birth_date", "resident_number",
    }
    for row in rows:
        if forbidden_keys.intersection(row):
            raise YonginContractError("PII or attachment field escaped into a public row")
        for key in ("description",):
            if _contains_pii(row.get(key)):
                raise YonginContractError("PII escaped into public description")


def _finalize_rows(
    rows: list[dict[str, Any]],
    *,
    dedupe_fn: Optional[DedupeRows],
) -> list[dict[str, Any]]:
    output = list((dedupe_fn or _dedupe_default)(rows))
    if len(output) != len({row.get("provider_course_id") for row in output}):
        raise YonginContractError("provider identity collision")
    _assert_rows_public(output)
    return output


def _resve_page_url(category: str, page: int) -> str:
    query: list[tuple[str, Any]] = [("q_lclas", category)]
    if page > 1:
        query.append(("q_currPage", page))
    return f"{YONGIN_RESERVATION_LIST_BASE}?{urlencode(query)}"


def _resve_detail_url(category: str, identity: str) -> str:
    return f"{YONGIN_RESERVATION_DETAIL_BASE}?{urlencode([('q_lclas', category), ('q_rsn', identity)])}"


def _resve_non_program_reason(row: Mapping[str, Any]) -> str:
    """Reject catalogue cards that are not fixed-venue programmes.

    The official CL_01 ledger occasionally contains test/notice cards and
    citywide/mobile services.  They are valid reservation records, but they
    are not an exact education or experience programme at the address shown
    in the detail table (the address is only the operating office in those
    cases).  Keep this allowlist boundary fail-closed instead of manufacturing
    district coverage from those office addresses.
    """

    title = _clean(row.get("title"))
    branch = _clean(row.get("branch"))
    if "테스트" in title:
        return "test_card"
    if (
        "예약일정 사전 안내" in title
        or re.search(r"생태체험프로그램\s*예약\s*안내", title)
    ):
        return "reservation_notice_shell"
    if "상담" in title or branch == "찾아가는 마을세무사":
        return "non_programme_consultation"
    if "찾아가는" in title or branch == "생애주기별 찾아가는 안전교육":
        return "mobile_programme_without_fixed_venue"
    if branch == "문화관광해설사 예약":
        return "tour_destination_not_structured"
    if branch in YONGIN_RESERVATION_NON_PROGRAM_BRANCHES:
        return "non_programme_branch"
    return ""


def _resve_venue_municipality(venue: Any) -> tuple[str, str]:
    text = _clean(venue)
    matches = [values for token, values in YONGIN_DISTRICTS.items() if token in text]
    if len(matches) != 1:
        raise YonginContractError(
            "municipal reservation fixed venue lost its exact Yongin district"
        )
    return matches[0]


def _parse_resve_cards(soup: BeautifulSoup, category: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for card in soup.select("ul.reservation-list > li"):
        card_text = _clean(card.get_text(" ", strip=True))
        if card_text == "등록된 예약 프로그램이 없습니다.":
            continue
        anchor = card.select_one(":scope > a")
        title = _clean(card.select_one(".service-title").get_text(" ", strip=True)) if card.select_one(".service-title") else ""
        branch = _clean(card.select_one(".service-center").get_text(" ", strip=True)) if card.select_one(".service-center") else ""
        status_node = card.find("div", recursive=False)
        source_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
        if not anchor or not title or not branch or not source_status:
            raise YonginContractError("municipal reservation card lost required fields")
        fields: dict[str, str] = {}
        for group in anchor.select(":scope > ul"):
            cells = group.find_all("li", recursive=False)
            if len(cells) >= 2:
                fields[_clean(cells[0].get_text(" ", strip=True))] = _clean(
                    cells[1].get_text(" ", strip=True)
                )
        onclick = _clean(anchor.get("onclick"))
        match = re.search(r"fnView\((\d+)\)", onclick)
        source_id = match.group(1) if match else ""
        if source_id:
            identity = f"{category}:{source_id}"
            raw_url = _resve_detail_url(category, source_id)
        else:
            digest = hashlib.sha256(
                f"{category}|{branch}|{title}".encode("utf-8")
            ).hexdigest()[:20]
            identity = f"{category}:card:{digest}"
            raw_url = _resve_page_url(category, 1)
        start_date, end_date, date_text = _date_range(fields.get("기간"))
        status = _status(source_status)
        capacity = _integer(fields.get("인원"))
        row = _clean_row(
            {
                "provider_course_id": f"{YONGIN_RESERVATION_PROVIDER}:{identity}",
                "provider": YONGIN_RESERVATION_PROVIDER,
                "source_id": source_id,
                "source_kind": category,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_id": _branch_id("YONGIN_RESV", branch),
                "start_date": start_date,
                "end_date": end_date,
                "date_text": date_text,
                "target": fields.get("대상"),
                "venue_name": fields.get("장소"),
                "capacity_total": capacity,
                "source_status": source_status,
                "status": status,
                "reservation_available": bool(source_id and status in {"OPEN", "WAITING"}),
                "raw_url": raw_url,
                "application_url": raw_url,
                "collection_category": "공공예약",
                "domain_category": "체험·견학" if category == "CL_01" else "교육·강좌",
                "source_group": "municipal_reservation",
                "service_group": "체험" if category == "CL_01" else "공공강좌",
                "service_group_policy": "locked",
                "classification_locked": True,
                "municipality_code": YONGIN_CITY_CODE,
                "municipality_name": YONGIN_CITY_NAME,
                "detail_unavailable_by_source": not bool(source_id),
                "raw_fields": {
                    "source_category": category,
                    "source_status": source_status,
                },
            }
        )
        rows.append(row)
    return rows


def _table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in soup.select("tr"):
        direct = row.find_all(["th", "td"], recursive=False)
        if len(direct) == 2:
            key = _clean(direct[0].get_text(" ", strip=True))
            value = _clean(direct[1].get_text(" ", strip=True))
            if key:
                result[key] = value
        elif len(direct) > 2:
            for index in range(0, len(direct) - 1, 2):
                key = _clean(direct[index].get_text(" ", strip=True))
                value = _clean(direct[index + 1].get_text(" ", strip=True))
                if key:
                    result[key] = value
    return result


def _merge_resve_detail(row: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    page_text = _clean(soup.get_text(" ", strip=True))
    if not _titles_match(row["title"], page_text):
        raise YonginContractError("municipal reservation detail title mismatch")
    fields = _table_pairs(soup)
    if "프로그램기간" not in fields or "접수기간" not in fields:
        raise YonginContractError("municipal reservation detail lost period fields")
    start, end, date_text = _date_range(fields["프로그램기간"])
    apply_start, apply_end, _ = _date_range(fields["접수기간"])
    if not start or not end:
        raise YonginContractError("municipal reservation detail has no valid programme date")
    source_period = _clean(fields["프로그램기간"])
    source_schedule = (
        source_period
        if re.search(r"(?:[01]?\d|2[0-3])\s*(?::|시)\s*\d{0,2}", source_period)
        else ""
    )
    target = _clean(fields.get("이용대상")) or _clean(row.get("target"))
    source_fee = _clean(fields.get("이용료"))
    venue = _clean(fields.get("장소정보")) or _clean(row.get("venue_name"))
    municipality_code, municipality_name = _resve_venue_municipality(venue)
    category = (
        "체험·견학"
        if _clean(row.get("source_kind")) == "CL_01"
        else "교육·강좌"
    )
    if not target or not venue:
        raise YonginContractError(
            "municipal reservation detail target or venue is missing"
        )
    raw_fields = {
        **dict(row.get("raw_fields") or {}),
        "application_method": _clean(fields.get("신청방법")),
        "source_programme_period": source_period,
        "source_schedule": source_schedule,
        "source_time_omitted": not bool(source_schedule),
        "source_fee": source_fee,
        "source_fee_omitted": not bool(source_fee),
        "source_target": target,
        "source_venue": venue,
    }
    merged = dict(row)
    merged.update(
        {
            "start_date": start,
            "end_date": end,
            "date_text": date_text,
            "period": date_text,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "apply_period": (
                f"{apply_start} ~ {apply_end}"
                if apply_start and apply_end
                else "접수일 별도 안내"
            ),
            "schedule_raw": source_schedule or "시간 별도 안내",
            "target": target,
            "fee": source_fee or "요금 별도 안내",
            "capacity_total": _integer(fields.get("모집정원")) or row.get("capacity_total"),
            "venue_name": venue,
            "venue_address": venue,
            "municipality_code": municipality_code,
            "municipality_name": municipality_name,
            "category_raw": category,
            "raw_fields": raw_fields,
        }
    )
    return _clean_row(merged)


def collect_yongin_reservation_courses(
    target: Any,
    *,
    today: Optional[date | datetime | str] = None,
    max_pages: int = YONGIN_DEFAULT_MAX_PAGES,
    detail_limit: int = YONGIN_DEFAULT_DETAIL_LIMIT,
    max_requests: int = YONGIN_DEFAULT_MAX_REQUESTS,
    detail_workers: int = YONGIN_DEFAULT_DETAIL_WORKERS,
    timeout: int = 30,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_fn: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    parser = YONGIN_RESERVATION_PARSER
    fetcher: Optional[_Fetcher] = None
    try:
        if not _exact_target(target, YONGIN_RESERVATION_PROVIDER, YONGIN_RESERVATION_URL):
            raise YonginContractError("target is not the exact municipal reservation owner")
        page_cap = _positive(max_pages, "max_pages")
        detail_cap = _positive(detail_limit, "detail_limit")
        workers = _positive(detail_workers, "detail_workers")
        audit_today = _today(today)
        fetcher = _make_fetcher(
            session_factory=session_factory,
            allow_raw_requests_for_tests=allow_raw_requests_for_tests,
            timeout=timeout,
            max_requests=max_requests,
            sleeper=sleeper,
        )
        rows: list[dict[str, Any]] = []
        page_counts: dict[str, dict[int, int]] = {}
        edge_pages: list[tuple[str, int, list[dict[str, Any]]]] = []
        list_pages_used = 0
        for category in YONGIN_RESERVATION_CATEGORIES:
            counts: dict[int, int] = {}
            category_pages: list[tuple[int, list[dict[str, Any]]]] = []
            for page in range(1, page_cap + 1):
                list_pages_used += 1
                if list_pages_used > page_cap:
                    raise YonginContractError("max_pages exhausted before all reservation sentinels")
                parsed = _parse_resve_cards(fetcher.get_soup(_resve_page_url(category, page)), category)
                counts[page] = len(parsed)
                if not parsed:
                    break
                category_pages.append((page, parsed))
                rows.extend(parsed)
            else:
                raise YonginContractError("reservation list has no exact empty sentinel")
            if not category_pages or counts[max(counts)] != 0:
                raise YonginContractError("reservation category boundary is incomplete")
            page_counts[category] = counts
            edge_pages.extend([category_pages[0], category_pages[-1]])

        for category, page, expected in (
            (YONGIN_RESERVATION_CATEGORIES[index // 2], value[0], value[1])
            for index, value in enumerate(edge_pages)
        ):
            actual = _parse_resve_cards(fetcher.get_soup(_resve_page_url(category, page)), category)
            if _rows_signature(actual) != _rows_signature(expected):
                raise YonginContractError("municipal reservation edge changed during collection")

        non_programme_rows = [
            (row, reason)
            for row in rows
            if row.get("source_id")
            and (reason := _resve_non_program_reason(row))
        ]
        non_programme_identities = {
            row["provider_course_id"] for row, _reason in non_programme_rows
        }
        detail_rows = [
            row
            for row in rows
            if row.get("source_id")
            and row["provider_course_id"] not in non_programme_identities
        ]
        if len(detail_rows) > detail_cap:
            raise YonginContractError(
                f"detail_limit {detail_cap} is below required {len(detail_rows)} public details"
            )
        soups = fetcher.get_soups_parallel(
            [str(row["raw_url"]) for row in detail_rows], workers=workers
        )
        by_identity = {
            row["provider_course_id"]: _merge_resve_detail(row, soup)
            for row, soup in zip(detail_rows, soups)
        }
        schema_ineligible_rows = [
            row for row in rows if not row.get("source_id")
        ]
        dated_rows = [
            by_identity[row["provider_course_id"]] for row in detail_rows
        ]
        expired_rows = [
            row
            for row in dated_rows
            if date.fromisoformat(str(row["end_date"])) < audit_today
        ]
        eligible_rows = [row for row in dated_rows if row not in expired_rows]
        output = _finalize_rows(eligible_rows, dedupe_fn=dedupe_fn)
        branch_counts = Counter(row["branch"] for row in output)
        meta = _success_meta(
            parser,
            output,
            fetcher,
            source_total=len(rows),
            branch_count=len(branch_counts),
            branch_counts=dict(sorted(branch_counts.items())),
            page_counts=page_counts,
            sentinel_count=0,
            stability_rechecks=4,
            detail_pages=len(detail_rows),
            disabled_detail_cards=len(schema_ineligible_rows),
            schema_ineligible_disabled_cards=len(schema_ineligible_rows),
            schema_ineligible_status_counts=dict(
                Counter(
                    _clean(row.get("source_status"))
                    for row in schema_ineligible_rows
                )
            ),
            schema_ineligible_category_counts=dict(
                Counter(
                    _clean(row.get("source_kind"))
                    for row in schema_ineligible_rows
                )
            ),
            excluded_non_programme_count=len(non_programme_rows),
            excluded_non_programme_reason_counts=dict(
                sorted(Counter(reason for _row, reason in non_programme_rows).items())
            ),
            expired_programme_count=len(expired_rows),
            current_source_count=len(output),
            category_counts=dict(Counter(row["source_kind"] for row in output)),
            municipality_counts=dict(
                sorted(Counter(row["municipality_code"] for row in output).items())
            ),
            pagination_detected=True,
        )
        return output, parser, meta
    except Exception as exc:
        return [], parser, _failure_meta(parser, exc, fetcher)
    finally:
        if fetcher is not None:
            fetcher.close()


def _jachi_expected_companies() -> set[tuple[str, str]]:
    expected = {YONGIN_JACHI_ADMIN_COMPANY}
    for branches in YONGIN_JACHI_BRANCHES.values():
        expected.update(branches.items())
    return expected


def _jachi_payload(
    *, branch_code: str, search_type: str, page: int
) -> dict[str, Any]:
    return {
        "company_code": branch_code,
        "mem_no": "",
        "search_type": search_type,
        "category_cd": "",
        "category_level": 9,
        "class_nm": "",
        "train_day": "",
        "adult_gubn": "",
        "lecturer_nm": "",
        "page": page,
        "page_size": YONGIN_JACHI_PAGE_SIZE,
    }


def _jachi_detail_url(canonical_url: str, branch_code: str, class_code: str, search_type: str) -> str:
    query = urlencode(
        [
            ("action", "read"),
            ("comcd", branch_code),
            ("classcd", class_code),
            ("type", search_type),
        ]
    )
    return f"{canonical_url}?{query}"


def _complete_jachi_required_fields(row: dict[str, Any]) -> dict[str, Any]:
    merged = dict(row)
    raw_fields = dict(merged.get("raw_fields") or {})
    start = _clean(merged.get("start_date"))
    end = _clean(merged.get("end_date"))
    source_period = _clean(
        merged.get("period") or merged.get("date_text") or raw_fields.get("source_period_raw")
    )
    period = (
        f"{start} ~ {end}"
        if start and end
        else source_period
        if source_period and source_period != "현재 주민자치센터 강좌 카탈로그"
        else "운영기간 별도 안내"
    )
    schedule = _clean(merged.get("schedule") or merged.get("schedule_raw"))
    target = _clean(merged.get("target"))
    venue = _clean(merged.get("venue_name") or merged.get("branch"))
    category = " > ".join(
        part
        for part in (
            _clean(raw_fields.get("category_primary")),
            _clean(raw_fields.get("category_secondary")),
        )
        if part
    )
    raw_fields.update(
        {
            "source_period_omitted": not bool(start and end),
            "source_time_omitted": not bool(schedule),
            "source_target_omitted": not bool(target),
            "source_venue_fallback_to_branch": not bool(
                _clean(merged.get("venue_name"))
            ),
        }
    )
    merged.update(
        {
            "date_text": period,
            "period": period,
            "schedule": schedule or "시간 별도 안내",
            "schedule_raw": schedule or "시간 별도 안내",
            "target": target or "대상 별도 안내",
            "venue_name": venue or "장소 별도 안내",
            "category_raw": category or "교육·강좌",
            "raw_fields": raw_fields,
        }
    )
    return _clean_row(merged)


def _jachi_rows_from_payload(
    payload: Any,
    *,
    provider: str,
    district_key: str,
    district_name: str,
    canonical_url: str,
    branch_code: str,
    search_type: str,
) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise YonginContractError("Jachi list API payload is not a list")
    expected_branch = YONGIN_JACHI_BRANCHES[district_key][branch_code]
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise YonginContractError("Jachi list API row is not an object")
        returned_code = _clean(item.get("comcd"))
        returned_name = _clean(item.get("comnm"))
        class_code = _clean(item.get("class_cd"))
        title = _clean(item.get("class_nm"))
        source_status = _clean(item.get("status"))
        if returned_code != branch_code or returned_name != expected_branch:
            raise YonginContractError("Jachi branch code/name drift detected")
        if not class_code or not title or source_status not in {"R", "RW", "E", "W"}:
            raise YonginContractError("Jachi row lost identity, title, or status")
        if source_status in {"R", "RW"}:
            status = "OPEN"
        elif source_status == "W":
            status = "SCHEDULED"
        else:
            status = "CLOSED"
        raw_url = _jachi_detail_url(canonical_url, branch_code, class_code, search_type)
        capacity_total = _integer(item.get("capa"))
        capacity_current = _integer(item.get("reg_person"))
        fee = _clean(item.get("course_fee"))
        schedule = _clean(
            f"{_clean(item.get('train_day_nm'))} "
            f"{_clean(item.get('train_stime'))} ~ {_clean(item.get('train_etime'))}"
        )
        rows.append(
            _complete_jachi_required_fields(
                {
                    "provider_course_id": f"{provider}:{branch_code}:{class_code}",
                    "provider": provider,
                    "source_id": f"{branch_code}:{class_code}",
                    "source_kind": search_type,
                    "title": title,
                    "description": title,
                    "branch": expected_branch,
                    "branch_id": branch_code,
                    "district": district_name,
                    "date_text": "현재 주민자치센터 강좌 카탈로그",
                    "schedule": schedule,
                    "target": _clean(item.get("target_age_name")),
                    "fee": fee,
                    "capacity_total": capacity_total,
                    "capacity_current": capacity_current,
                    "source_status": source_status,
                    "status": status,
                    "reservation_available": source_status in {"R", "RW"},
                    "raw_url": raw_url,
                    "application_url": raw_url,
                    "collection_category": "공공예약",
                    "domain_category": "교육·강좌",
                    "source_group": "municipal_reservation",
                    "service_group": "공공강좌",
                    "service_group_policy": "locked",
                    "classification_locked": True,
                    "municipality_code": YONGIN_CITY_CODE,
                    "municipality_name": YONGIN_CITY_NAME,
                    "raw_fields": {
                        "source_status": source_status,
                        "category_primary": _clean(item.get("category1")),
                        "category_secondary": _clean(item.get("category2")),
                        "receive_kind": _clean(item.get("receive_kind")),
                    },
                }
            )
        )
    return rows


def _merge_jachi_detail(row: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    fields = _table_pairs(soup)
    detail_title = _clean(fields.get("강좌명"))
    if not _titles_match(row["title"], detail_title):
        raise YonginContractError("Jachi detail title mismatch")
    centre = _clean(fields.get("운영센터"))
    expected_short = _clean(str(row["branch"]).replace(" 주민자치센터", ""))
    if expected_short and _norm(expected_short) not in _norm(centre):
        raise YonginContractError("Jachi detail branch mismatch")
    hidden = soup.select_one('input[name="status"]')
    detail_status = _clean(hidden.get("value")) if hidden else ""
    allowed_detail_statuses = {
        "R": {"R"},
        "RW": {"R", "RW"},
        "E": {"E"},
        "W": {"E", "W"},
    }.get(str(row.get("source_status")), set())
    if detail_status not in allowed_detail_statuses:
        raise YonginContractError("Jachi detail status mismatch")
    period_raw = _clean(
        fields.get("교육기간")
        or fields.get("강습기간")
        or fields.get("운영기간")
    )
    start, end, date_text = _date_range(period_raw)
    merged = dict(row)
    merged.update(
        {
            "start_date": start,
            "end_date": end,
            "date_text": date_text or period_raw,
            "period": date_text or period_raw,
            "venue_name": _clean(fields.get("교육장소")),
            "target": _clean(fields.get("교육대상")) or row.get("target"),
            "schedule": _clean(fields.get("시간/요일")) or row.get("schedule"),
            "raw_fields": {
                **dict(row.get("raw_fields") or {}),
                "registration_method": _clean(fields.get("접수방식")),
                "source_period_raw": period_raw,
            },
        }
    )
    return _complete_jachi_required_fields(merged)


def collect_yongin_jachi_courses(
    target: Any,
    *,
    today: Optional[date | datetime | str] = None,
    max_pages: int = YONGIN_DEFAULT_MAX_PAGES,
    detail_limit: int = YONGIN_DEFAULT_DETAIL_LIMIT,
    max_requests: int = YONGIN_DEFAULT_MAX_REQUESTS,
    detail_workers: int = YONGIN_DEFAULT_DETAIL_WORKERS,
    timeout: int = 30,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_fn: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    del today
    parser = YONGIN_JACHI_PARSER
    fetcher: Optional[_Fetcher] = None
    try:
        provider = _target_provider(target)
        owner = YONGIN_JACHI_OWNERS.get(provider)
        if owner is None:
            raise YonginContractError("unknown Yongin Jachi district provider")
        district_key, canonical_url, district_name, candidate_id = owner
        if not _exact_target(target, provider, canonical_url):
            raise YonginContractError("target is not an exact Jachi district owner")
        page_cap = _positive(max_pages, "max_pages")
        detail_cap = _positive(detail_limit, "detail_limit")
        workers = _positive(detail_workers, "detail_workers")
        fetcher = _make_fetcher(
            session_factory=session_factory,
            allow_raw_requests_for_tests=allow_raw_requests_for_tests,
            timeout=timeout,
            max_requests=max_requests,
            sleeper=sleeper,
        )

        landing = fetcher.get_soup(canonical_url)
        if "주민자치" not in _clean(landing.get_text(" ", strip=True)):
            raise YonginContractError("Jachi district landing page marker is missing")
        company_payload = fetcher.post_json(YONGIN_JACHI_COMPANY_URL, {})
        if not isinstance(company_payload, list):
            raise YonginContractError("Jachi company directory is not a list")
        actual_companies = {
            (_clean(item.get("comcd")), _clean(item.get("comnm")))
            for item in company_payload
            if isinstance(item, Mapping)
        }
        if actual_companies != _jachi_expected_companies():
            raise YonginContractError("official Jachi company directory drift detected")

        rows: list[dict[str, Any]] = []
        page_counts: dict[str, dict[int, int]] = {}
        nonempty_streams: list[tuple[str, str, int, list[dict[str, Any]]]] = []
        pages_used = 0
        empty_branches: set[str] = set()
        for branch_code in YONGIN_JACHI_BRANCHES[district_key]:
            branch_had_rows = False
            for search_type in ("R", "E"):
                key = f"{branch_code}:{search_type}"
                counts: dict[int, int] = {}
                stream_pages: list[tuple[int, list[dict[str, Any]]]] = []
                for page in range(1, page_cap + 1):
                    pages_used += 1
                    if pages_used > page_cap:
                        raise YonginContractError("max_pages exhausted before all Jachi sentinels")
                    payload = fetcher.post_json(
                        YONGIN_JACHI_API_URL,
                        _jachi_payload(
                            branch_code=branch_code,
                            search_type=search_type,
                            page=page,
                        ),
                    )
                    parsed = _jachi_rows_from_payload(
                        payload,
                        provider=provider,
                        district_key=district_key,
                        district_name=district_name,
                        canonical_url=canonical_url,
                        branch_code=branch_code,
                        search_type=search_type,
                    )
                    counts[page] = len(parsed)
                    if not parsed:
                        break
                    branch_had_rows = True
                    stream_pages.append((page, parsed))
                    rows.extend(parsed)
                else:
                    raise YonginContractError("Jachi stream has no exact empty sentinel")
                if counts[max(counts)] != 0:
                    raise YonginContractError("Jachi stream boundary is incomplete")
                page_counts[key] = counts
                if stream_pages:
                    first_page, first_rows = stream_pages[0]
                    last_page, last_rows = stream_pages[-1]
                    nonempty_streams.append((branch_code, search_type, first_page, first_rows))
                    if last_page != first_page:
                        nonempty_streams.append((branch_code, search_type, last_page, last_rows))
            if not branch_had_rows:
                empty_branches.add(YONGIN_JACHI_BRANCHES[district_key][branch_code])
        if not rows or not nonempty_streams:
            raise YonginContractError("Jachi district catalogue unexpectedly has no rows")

        recheck_streams = [nonempty_streams[0], nonempty_streams[-1]]
        for branch_code, search_type, page, expected in recheck_streams:
            payload = fetcher.post_json(
                YONGIN_JACHI_API_URL,
                _jachi_payload(branch_code=branch_code, search_type=search_type, page=page),
            )
            actual = _jachi_rows_from_payload(
                payload,
                provider=provider,
                district_key=district_key,
                district_name=district_name,
                canonical_url=canonical_url,
                branch_code=branch_code,
                search_type=search_type,
            )
            if _rows_signature(actual) != _rows_signature(expected):
                raise YonginContractError("Jachi list edge changed during collection")

        if len(rows) > detail_cap:
            raise YonginContractError(
                f"detail_limit {detail_cap} is below required {len(rows)} Jachi details"
            )
        soups = fetcher.get_soups_parallel(
            [str(row["raw_url"]) for row in rows], workers=workers
        )
        rows = [_merge_jachi_detail(row, soup) for row, soup in zip(rows, soups)]
        output = _finalize_rows(rows, dedupe_fn=dedupe_fn)
        branch_counts = Counter(row["branch"] for row in output)
        meta = _success_meta(
            parser,
            output,
            fetcher,
            source_total=len(rows),
            branch_count=len(YONGIN_JACHI_BRANCHES[district_key]),
            active_branch_count=len(branch_counts),
            branch_counts=dict(sorted(branch_counts.items())),
            empty_branches=sorted(empty_branches),
            page_counts=page_counts,
            sentinel_count=0,
            stability_rechecks=2,
            detail_pages=len(rows),
            status_counts=dict(Counter(row["source_status"] for row in output)),
            official_company_count=len(actual_companies) - 1,
            excluded_admin_company=YONGIN_JACHI_ADMIN_COMPANY[0],
            candidate_id=candidate_id,
            pagination_detected=True,
        )
        return output, parser, meta
    except Exception as exc:
        return [], parser, _failure_meta(parser, exc, fetcher)
    finally:
        if fetcher is not None:
            fetcher.close()


def _library_specs() -> tuple[dict[str, str], ...]:
    return (
        {
            "kind": "lecture",
            "list_url": YONGIN_LIBRARY_URL,
            "detail_base": (
                "https://lib.yongin.go.kr/yongin/menu/10264/program/30027/"
                "lectureDetail.do"
            ),
            "identity_key": "lectureIdx",
        },
        {
            "kind": "vacation",
            "list_url": YONGIN_LIBRARY_VACATION_URL,
            "detail_base": (
                "https://lib.yongin.go.kr/yongin/menu/10266/program/30069/"
                "vacationCourseDetail.do"
            ),
            "identity_key": "vacationCourseIdx",
        },
    )


def _library_page_url(spec: Mapping[str, str], page: int) -> str:
    if page == 1:
        return spec["list_url"]
    return f"{spec['list_url']}&currentPageNo={page}"


def _library_detail_url(spec: Mapping[str, str], identity: str) -> str:
    query = urlencode([(spec["identity_key"], identity), ("manageCd", "ALL")])
    return f"{spec['detail_base']}?{query}"


def _library_directory(soup: BeautifulSoup) -> dict[str, str]:
    select = soup.select_one('select[name="manageCd"]')
    if select is None:
        raise YonginContractError("library branch directory is missing")
    result = {
        _clean(option.get("value")): _clean(option.get_text(" ", strip=True))
        for option in select.select("option[value]")
        if _clean(option.get("value")) not in {"", "ALL"}
    }
    return result


def _library_anchor_title(anchor: Any, label: str) -> str:
    full = _clean(anchor.get_text(" ", strip=True))
    if full.startswith(label):
        return _clean(full[len(label):])
    return full


def _library_branch_name(label: str) -> Optional[str]:
    if label in YONGIN_LIBRARY_BRANCHES.values():
        return label
    return YONGIN_LIBRARY_LABELS.get(label)


def _library_branch_from_title(title: str) -> Optional[str]:
    normalized = _clean(title)
    matches = [
        branch
        for branch in YONGIN_LIBRARY_BRANCHES.values()
        if normalized.startswith(branch)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _parse_library_cards(
    soup: BeautifulSoup,
    *,
    provider: str,
    spec: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for card in soup.select("ul.article-list.lecture > li"):
        anchor = card.select_one('a.title[onclick*="fnDetail"]')
        label_node = anchor.select_one("span.lib") if anchor else None
        label = _clean(label_node.get_text(" ", strip=True)) if label_node else ""
        title = _library_anchor_title(anchor, label) if anchor else ""
        match = re.search(r"fnDetail\(['\"]?(\d+)['\"]?\)", _clean(anchor.get("onclick"))) if anchor else None
        identity = match.group(1) if match else ""
        branch = _library_branch_name(label) or _library_branch_from_title(title)
        if not identity or not title or not branch:
            raise YonginContractError("library card lost identity, title, or official branch label")
        fields: dict[str, str] = {}
        for node in card.select(".info-txt p"):
            text = _clean(node.get_text(" ", strip=True))
            if ":" in text:
                key, value = text.split(":", 1)
                fields[_clean(key)] = _clean(value)
        period_raw = _clean(fields.get("수강기간"))
        start, end, date_text, period_corrected = _course_date_range(period_raw)
        apply_start, apply_end, _ = _date_range(fields.get("접수기간"))
        if not start or not end:
            raise YonginContractError("library card has no valid course period")
        status_node = card.select_one(".statusBox .status")
        source_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
        if not source_status:
            raise YonginContractError("library card status is missing")
        application = _clean(card.select_one(".statusBox .apply").get_text(" ", strip=True)) if card.select_one(".statusBox .apply") else ""
        capacity_values = [int(value) for value in re.findall(r"\d+", application)]
        capacity_current = capacity_values[0] if capacity_values else None
        capacity_total = capacity_values[1] if len(capacity_values) > 1 else None
        raw_url = _library_detail_url(spec, identity)
        rows.append(
            _clean_row(
                {
                    "provider_course_id": f"{provider}:{spec['kind']}:{identity}",
                    "provider": provider,
                    "source_id": identity,
                    "source_kind": spec["kind"],
                    "title": title,
                    "description": title,
                    "branch": branch,
                    "branch_id": _branch_id("YONGIN_LIB", branch),
                    "start_date": start,
                    "end_date": end,
                    "date_text": date_text,
                    "apply_start": apply_start,
                    "apply_end": apply_end,
                    "schedule": fields.get("수강기간"),
                    "target": fields.get("대상"),
                    "venue_name": fields.get("교육장소"),
                    "capacity_total": capacity_total,
                    "capacity_current": capacity_current,
                    "source_status": source_status,
                    "status": _status(source_status),
                    "reservation_available": _status(source_status) in {"OPEN", "WAITING"},
                    "raw_url": raw_url,
                    "application_url": raw_url,
                    "collection_category": "공공예약",
                    "domain_category": "교육·강좌",
                    "source_group": "municipal_reservation",
                    "service_group": "공공강좌",
                    "service_group_policy": "locked",
                    "classification_locked": True,
                    "municipality_code": YONGIN_CITY_CODE,
                    "municipality_name": YONGIN_CITY_NAME,
                    "raw_fields": {
                        "source_ledger": spec["kind"],
                        "source_status": source_status,
                        "source_period_raw": period_raw,
                    },
                    "source_period_corrected": period_corrected,
                }
            )
        )
    return rows


def _library_detail_pairs(soup: BeautifulSoup) -> tuple[str, str, dict[str, str]]:
    title_node = soup.select_one(".board-write .titleBox .title")
    label_node = title_node.select_one("span.lib") if title_node else None
    label = _clean(label_node.get_text(" ", strip=True)) if label_node else ""
    title = _library_anchor_title(title_node, label) if title_node else ""
    fields: dict[str, str] = {}
    for item in soup.select(".board-write > ul > li"):
        key_node = item.select_one(":scope > .tit")
        value_node = item.select_one(":scope > .txt")
        if key_node and value_node:
            fields[_clean(key_node.get_text(" ", strip=True))] = _clean(
                value_node.get_text(" ", strip=True)
            )
    return title, label, fields


def _merge_library_detail(row: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    detail_title, label, fields = _library_detail_pairs(soup)
    if not _titles_match(row["title"], detail_title):
        raise YonginContractError("library detail title mismatch")
    detail_branch = _library_branch_name(label) or _library_branch_from_title(detail_title)
    if detail_branch != row.get("branch"):
        raise YonginContractError("library detail branch mismatch")
    start, end, date_text, period_corrected = _course_date_range(fields.get("수강기간"))
    if start != row.get("start_date") or end != row.get("end_date"):
        raise YonginContractError("library detail course period mismatch")
    if period_corrected != bool(row.get("source_period_corrected")):
        raise YonginContractError("library detail period-correction mismatch")
    apply_start, apply_end, _ = _date_range(fields.get("접수기간"))
    source_status_node = soup.select_one(".board-write .state")
    source_status = _clean(source_status_node.get_text(" ", strip=True)) if source_status_node else row.get("source_status")
    schedule = _clean(fields.get("수강시간/횟수")) or _clean(row.get("schedule"))
    target = _clean(fields.get("대상")) or _clean(row.get("target"))
    venue = _clean(fields.get("교육장소")) or _clean(row.get("venue_name"))
    source_fee = _clean(fields.get("재료비"))
    category = (
        "도서관 방학특강"
        if _clean(row.get("source_kind")) == "vacation"
        else "도서관 강좌"
    )
    if not target or not venue:
        raise YonginContractError("library detail target or venue is missing")
    raw_fields = {
        **dict(row.get("raw_fields") or {}),
        "source_schedule": schedule,
        "source_time_omitted": not bool(schedule),
        "source_fee": source_fee,
        "source_fee_omitted": not bool(source_fee),
        "source_target": target,
        "source_venue": venue,
    }
    merged = dict(row)
    merged.update(
        {
            "date_text": date_text,
            "period": date_text,
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "apply_period": (
                f"{apply_start} ~ {apply_end}"
                if apply_start and apply_end
                else "접수일 별도 안내"
            ),
            "schedule": schedule,
            "schedule_raw": schedule or "시간 별도 안내",
            "target": target,
            "venue_name": venue,
            "fee": source_fee or "요금 별도 안내",
            "category_raw": category,
            "source_status": source_status,
            "status": _status(source_status),
            "reservation_available": _status(source_status) in {"OPEN", "WAITING"},
            "raw_fields": raw_fields,
        }
    )
    return _clean_row(merged)


def collect_yongin_library_courses(
    target: Any,
    *,
    today: Optional[date | datetime | str] = None,
    max_pages: int = YONGIN_DEFAULT_MAX_PAGES,
    detail_limit: int = YONGIN_DEFAULT_DETAIL_LIMIT,
    max_requests: int = YONGIN_DEFAULT_MAX_REQUESTS,
    detail_workers: int = YONGIN_DEFAULT_DETAIL_WORKERS,
    timeout: int = 30,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_fn: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    parser = YONGIN_LIBRARY_PARSER
    fetcher: Optional[_Fetcher] = None
    try:
        if not _exact_target(target, YONGIN_LIBRARY_PROVIDER, YONGIN_LIBRARY_URL):
            raise YonginContractError("target is not the exact city-library owner")
        audit_day = _today(today)
        page_cap = _positive(max_pages, "max_pages")
        detail_cap = _positive(detail_limit, "detail_limit")
        workers = _positive(detail_workers, "detail_workers")
        fetcher = _make_fetcher(
            session_factory=session_factory,
            allow_raw_requests_for_tests=allow_raw_requests_for_tests,
            timeout=timeout,
            max_requests=max_requests,
            sleeper=sleeper,
        )
        all_rows: list[dict[str, Any]] = []
        page_counts: dict[str, dict[int, int]] = {}
        edge_pages: list[tuple[Mapping[str, str], int, list[dict[str, Any]]]] = []
        pages_used = 0
        for spec in _library_specs():
            counts: dict[int, int] = {}
            ledger_pages: list[tuple[int, list[dict[str, Any]]]] = []
            for page in range(1, page_cap + 1):
                pages_used += 1
                if pages_used > page_cap:
                    raise YonginContractError("max_pages exhausted before library sentinels")
                soup = fetcher.get_soup(_library_page_url(spec, page))
                if page == 1 and _library_directory(soup) != dict(YONGIN_LIBRARY_BRANCHES):
                    raise YonginContractError("official library branch directory drift detected")
                parsed = _parse_library_cards(
                    soup, provider=YONGIN_LIBRARY_PROVIDER, spec=spec
                )
                counts[page] = len(parsed)
                if not parsed:
                    break
                ledger_pages.append((page, parsed))
                all_rows.extend(parsed)
            else:
                raise YonginContractError("library ledger has no exact empty sentinel")
            if not ledger_pages or counts[max(counts)] != 0:
                raise YonginContractError("library ledger boundary is incomplete")
            page_counts[spec["kind"]] = counts
            edge_pages.append((spec, ledger_pages[0][0], ledger_pages[0][1]))
            edge_pages.append((spec, ledger_pages[-1][0], ledger_pages[-1][1]))

        for spec, page, expected in edge_pages:
            actual = _parse_library_cards(
                fetcher.get_soup(_library_page_url(spec, page)),
                provider=YONGIN_LIBRARY_PROVIDER,
                spec=spec,
            )
            if _rows_signature(actual) != _rows_signature(expected):
                raise YonginContractError("library list edge changed during collection")

        current_rows = [
            row for row in all_rows if date.fromisoformat(str(row["end_date"])) >= audit_day
        ]
        if len(current_rows) > detail_cap:
            raise YonginContractError(
                f"detail_limit {detail_cap} is below required {len(current_rows)} library details"
            )
        soups = fetcher.get_soups_parallel(
            [str(row["raw_url"]) for row in current_rows], workers=workers
        )
        current_rows = [
            _merge_library_detail(row, soup) for row, soup in zip(current_rows, soups)
        ]
        output = _finalize_rows(current_rows, dedupe_fn=dedupe_fn)
        branch_counts = Counter(row["branch"] for row in output)
        meta = _success_meta(
            parser,
            output,
            fetcher,
            source_total=len(all_rows),
            branch_count=len(YONGIN_LIBRARY_BRANCHES),
            active_branch_count=len(branch_counts),
            branch_counts=dict(sorted(branch_counts.items())),
            page_counts=page_counts,
            sentinel_count=0,
            stability_rechecks=4,
            detail_pages=len(current_rows),
            excluded_expired=len(all_rows) - len(current_rows),
            source_period_corrected=sum(
                1 for row in all_rows if row.get("source_period_corrected")
            ),
            ledger_counts=dict(Counter(row["source_kind"] for row in output)),
            candidate_id=YONGIN_LIBRARY_CANDIDATE_ID,
            pagination_detected=True,
        )
        return output, parser, meta
    except Exception as exc:
        return [], parser, _failure_meta(parser, exc, fetcher)
    finally:
        if fetcher is not None:
            fetcher.close()


def _yicf_page_url(page: int) -> str:
    return YONGIN_YICF_URL if page == 1 else f"{YONGIN_YICF_URL}&pageIndex={page}"


def _yicf_detail_url(identity: str) -> str:
    return f"{YONGIN_YICF_DETAIL_BASE}?{urlencode([('lecId', identity)])}"


def _parse_yicf_cards(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for card in soup.select("a.involved-items"):
        match = re.search(r"fn_update_detail\(['\"]([^'\"]+)['\"]\)", _clean(card.get("onclick")))
        identity = _clean(match.group(1)) if match else ""
        title_node = card.select_one(".involved-items__cont > strong")
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
        fields: dict[str, str] = {}
        for node in card.select(".involved-items__cont > small"):
            span = node.select_one("span")
            value = _clean(span.get_text(" ", strip=True)) if span else ""
            label = _clean(node.get_text(" ", strip=True))
            if value and label.endswith(value):
                label = _clean(label[: -len(value)])
            if label:
                fields[label] = value
        status_node = card.select_one(".involved-items__btns")
        source_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
        start, end, date_text = _date_range(fields.get("기간"))
        if not identity or not title or not source_status or not start or not end:
            raise YonginContractError("YICF academy card lost required fields")
        raw_url = _yicf_detail_url(identity)
        status = _status(source_status)
        rows.append(
            _clean_row(
                {
                    "provider_course_id": f"{YONGIN_YICF_PROVIDER}:{identity}",
                    "provider": YONGIN_YICF_PROVIDER,
                    "source_id": identity,
                    "title": title,
                    "description": title,
                    "branch": YONGIN_YICF_BRANCH,
                    "branch_id": _branch_id("YONGIN_YICF", YONGIN_YICF_BRANCH),
                    "venue_address": YONGIN_YICF_ADDRESS,
                    "start_date": start,
                    "end_date": end,
                    "date_text": date_text,
                    "target": fields.get("대상"),
                    "source_status": source_status,
                    "status": status,
                    "reservation_available": status in {"OPEN", "WAITING"},
                    "raw_url": raw_url,
                    "application_url": raw_url,
                    "collection_category": "공공예약",
                    "domain_category": "교육·강좌",
                    "source_group": "municipal_reservation",
                    "service_group": "공공강좌",
                    "service_group_policy": "locked",
                    "classification_locked": True,
                    "municipality_code": YONGIN_CITY_CODE,
                    "municipality_name": YONGIN_CITY_NAME,
                    "raw_fields": {"source_status": source_status},
                }
            )
        )
    return rows


def _merge_yicf_detail(row: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    fields = _table_pairs(soup)
    detail_title = _clean(fields.get("강좌명"))
    if not _titles_match(row["title"], detail_title):
        raise YonginContractError("YICF detail title mismatch")
    start, end, date_text = _date_range(fields.get("수강일"))
    if start != row.get("start_date") or end != row.get("end_date"):
        raise YonginContractError("YICF detail course period mismatch")
    apply_start, apply_end, _ = _date_range(fields.get("접수일"))
    if not apply_start or not apply_end:
        raise YonginContractError("YICF detail application period is missing")
    schedule = _clean(fields.get("강습시간"))
    weekday = _clean(fields.get("수강요일"))
    target = _clean(fields.get("연령")) or _clean(row.get("target"))
    fee = _clean(fields.get("수강료"))
    venue = _clean(fields.get("장소"))
    category = _clean(fields.get("과정"))
    if not all((schedule, target, fee, venue, category)):
        raise YonginContractError("YICF detail required display fields are missing")
    schedule_raw = " ".join(value for value in (weekday, schedule) if value)
    merged = dict(row)
    merged.update(
        {
            "date_text": date_text,
            "period": date_text,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "apply_period": f"{apply_start} ~ {apply_end}",
            "schedule": schedule,
            "schedule_raw": schedule_raw,
            "target": target,
            "capacity_total": _integer(fields.get("정원")),
            "fee": fee,
            "venue_name": venue,
            "category_raw": category,
            "raw_fields": {
                **dict(row.get("raw_fields") or {}),
                "course_category": category,
                "weekday": weekday,
                "source_schedule": schedule,
                "source_fee": fee,
                "source_venue": venue,
                "source_target": target,
            },
        }
    )
    return _clean_row(merged)


def collect_yongin_yicf_courses(
    target: Any,
    *,
    today: Optional[date | datetime | str] = None,
    max_pages: int = YONGIN_DEFAULT_MAX_PAGES,
    detail_limit: int = YONGIN_DEFAULT_DETAIL_LIMIT,
    max_requests: int = YONGIN_DEFAULT_MAX_REQUESTS,
    detail_workers: int = 1,
    timeout: int = 30,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_fn: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    del detail_workers
    parser = YONGIN_YICF_PARSER
    fetcher: Optional[_Fetcher] = None
    try:
        if not _exact_target(target, YONGIN_YICF_PROVIDER, YONGIN_YICF_URL):
            raise YonginContractError("target is not the exact YICF academy owner")
        audit_day = _today(today)
        page_cap = _positive(max_pages, "max_pages")
        detail_cap = _positive(detail_limit, "detail_limit")
        fetcher = _make_fetcher(
            session_factory=session_factory,
            allow_raw_requests_for_tests=allow_raw_requests_for_tests,
            timeout=timeout,
            max_requests=max_requests,
            sleeper=sleeper,
        )
        all_rows: list[dict[str, Any]] = []
        page_counts: dict[int, int] = {}
        nonempty_pages: list[tuple[int, list[dict[str, Any]]]] = []
        subset_ids: set[str] = set()
        for page in range(1, page_cap + 1):
            soup = fetcher.get_soup(_yicf_page_url(page))
            if page == 1:
                for anchor in soup.select('a[href*="lecCt1="]'):
                    query = parse_qs(urlparse(anchor.get("href", "")).query)
                    subset = _clean((query.get("lecCt1") or [""])[0])
                    if subset and subset != "CATEGORY_ID_00000011":
                        subset_ids.add(subset)
            parsed = _parse_yicf_cards(soup)
            page_counts[page] = len(parsed)
            if not parsed:
                break
            nonempty_pages.append((page, parsed))
            all_rows.extend(parsed)
        else:
            raise YonginContractError("YICF academy has no exact empty sentinel")
        if not nonempty_pages or page_counts[max(page_counts)] != 0:
            raise YonginContractError("YICF academy boundary is incomplete")

        for page, expected in (nonempty_pages[0], nonempty_pages[-1]):
            actual = _parse_yicf_cards(fetcher.get_soup(_yicf_page_url(page)))
            if _rows_signature(actual) != _rows_signature(expected):
                raise YonginContractError("YICF list edge changed during collection")

        current_rows = [
            row for row in all_rows if date.fromisoformat(str(row["end_date"])) >= audit_day
        ]
        if len(current_rows) > detail_cap:
            raise YonginContractError(
                f"detail_limit {detail_cap} is below required {len(current_rows)} YICF details"
            )
        current_rows = [
            _merge_yicf_detail(
                row,
                fetcher.get_soup(str(row["raw_url"]), headers={"Referer": YONGIN_YICF_URL}),
            )
            for row in current_rows
        ]
        output = _finalize_rows(current_rows, dedupe_fn=dedupe_fn)
        meta = _success_meta(
            parser,
            output,
            fetcher,
            source_total=len(all_rows),
            branch_count=1,
            branch_counts={YONGIN_YICF_BRANCH: len(output)},
            page_counts=page_counts,
            sentinel_count=0,
            stability_rechecks=2,
            detail_pages=len(current_rows),
            excluded_expired=len(all_rows) - len(current_rows),
            excluded_subset_categories=sorted(subset_ids),
            candidate_id=YONGIN_YICF_CANDIDATE_ID,
            pagination_detected=True,
        )
        return output, parser, meta
    except Exception as exc:
        return [], parser, _failure_meta(parser, exc, fetcher)
    finally:
        if fetcher is not None:
            fetcher.close()


def _oneclick_page_url(page: int) -> str:
    if page == 1:
        return YONGIN_ONECLICK_URL
    return f"{YONGIN_ONECLICK_URL}?{urlencode([('cpage', page), ('rows', YONGIN_ONECLICK_PAGE_SIZE)])}"


def _oneclick_detail_url(identity: str) -> str:
    return (
        f"{YONGIN_ONECLICK_DETAIL_BASE}?"
        f"{urlencode([('program_seq', identity), ('cpage', 1), ('rows', 10), ('keyword', '')])}"
    )


def _oneclick_period(title: str, raw: str) -> tuple[str, str, str, bool]:
    title_year_match = re.search(r"(?<!\d)(20\d{2})(?!\d)", title)
    title_year = int(title_year_match.group(1)) if title_year_match else None
    match = _MONTH_RANGE_RE.search(_clean(raw))
    corrected = False
    if match:
        source_year = int(match.group(1))
        start_month = int(match.group(2))
        end_year = int(match.group(3) or source_year)
        end_month = int(match.group(4))
        if title_year and source_year != title_year:
            source_year = title_year
            if not match.group(3):
                end_year = title_year
            corrected = True
        try:
            start = date(source_year, start_month, 1)
            end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
        except ValueError as exc:
            raise YonginContractError("One-click month period is invalid") from exc
        if end < start:
            raise YonginContractError("One-click month period is reversed")
        return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}", corrected
    start, end, date_text = _date_range(raw)
    if start:
        return start, end, date_text, False
    if title_year:
        start_month = 7 if "2학기" in raw and "1,2학기" not in raw else 1
        start_date = date(title_year, start_month, 1)
        end_date = date(title_year, 12, 31)
        return (
            start_date.isoformat(),
            end_date.isoformat(),
            f"{start_date.isoformat()} ~ {end_date.isoformat()}",
            False,
        )
    raise YonginContractError("One-click programme period is not parseable")


def _parse_oneclick_cards(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], int]:
    content = soup.select_one("#content") or soup.select_one(".content") or soup
    total_match = re.search(r"총\s*([\d,]+)\s*건", _clean(content.get_text(" ", strip=True)))
    declared_total = int(total_match.group(1).replace(",", "")) if total_match else 0
    rows: list[dict[str, Any]] = []
    for card in soup.select("ul.list_style_2 > li"):
        anchor = card.select_one('a[href*="view.do?program_seq="]')
        href = _clean(anchor.get("href")) if anchor else ""
        query = parse_qs(urlparse(href).query)
        identity = _clean((query.get("program_seq") or [""])[0])
        title = _clean(anchor.get_text(" ", strip=True)) if anchor else ""
        fields: dict[str, str] = {}
        detail_box = card.select_one(".text > .txt")
        if detail_box:
            for node in detail_box.find_all("p", recursive=False):
                text = _clean(node.get_text(" ", strip=True))
                if ":" in text:
                    key, value = text.split(":", 1)
                    fields[_clean(key).lstrip("□ ")] = _clean(value)
        raw_period = fields.get("기간", "")
        if not identity or not title or not raw_period:
            raise YonginContractError("One-click card lost identity, title, or period")
        start, end, date_text, corrected = _oneclick_period(title, raw_period)
        raw_url = _oneclick_detail_url(identity)
        rows.append(
            _clean_row(
                {
                    "provider_course_id": f"{YONGIN_ONECLICK_PROVIDER}:{identity}",
                    "provider": YONGIN_ONECLICK_PROVIDER,
                    "source_id": identity,
                    "title": title,
                    "description": title,
                    "branch": YONGIN_ONECLICK_BRANCH,
                    "branch_id": _branch_id("YONGIN_ONECLICK", YONGIN_ONECLICK_BRANCH),
                    "start_date": start,
                    "end_date": end,
                    "date_text": date_text,
                    "target": fields.get("대상"),
                    "venue_name": fields.get("장소"),
                    "status": "CLOSED",
                    "reservation_available": False,
                    "raw_url": raw_url,
                    "application_url": raw_url,
                    "collection_category": "공공예약",
                    "domain_category": "교육·강좌",
                    "source_group": "municipal_reservation",
                    "service_group": "공공강좌",
                    "service_group_policy": "locked",
                    "classification_locked": True,
                    "municipality_code": YONGIN_CITY_CODE,
                    "municipality_name": YONGIN_CITY_NAME,
                    "source_period_year_corrected": corrected,
                    "raw_fields": {"programme_period_raw": raw_period},
                }
            )
        )
    return rows, declared_total


def _merge_oneclick_detail(
    row: dict[str, Any], soup: BeautifulSoup, audit_day: date
) -> dict[str, Any]:
    fields = _table_pairs(soup)
    detail_title = _clean(fields.get("프로그램명"))
    if not _titles_match(row["title"], detail_title):
        raise YonginContractError("One-click detail title mismatch")
    period_raw = _clean(fields.get("운영기간"))
    start, end, date_text, corrected = _oneclick_period(detail_title, period_raw)
    if start != row.get("start_date") or end != row.get("end_date"):
        raise YonginContractError("One-click detail programme period mismatch")
    apply_start, apply_end, _ = _date_range(fields.get("접수기간"))
    if not apply_start or not apply_end:
        raise YonginContractError("One-click detail application period is missing")
    apply_start_date = date.fromisoformat(apply_start)
    apply_end_date = date.fromisoformat(apply_end)
    if audit_day < apply_start_date:
        status = "SCHEDULED"
    elif audit_day <= apply_end_date:
        status = "OPEN"
    else:
        status = "CLOSED"
    target = _clean(fields.get("대상"))
    venue = _clean(fields.get("장소"))
    operation = _clean(fields.get("운영방법"))
    fee = _clean(
        fields.get("참가비")
        or fields.get("수강료")
        or fields.get("교육비")
    )
    source_schedule = (
        "일정 협의"
        if "일정협의" in re.sub(r"\s+", "", period_raw)
        else operation
        if re.search(r"\d{1,2}\s*(?::|시)", operation)
        else ""
    )
    raw_fields = dict(row.get("raw_fields") or {})
    raw_fields.update(
        {
            "programme_period_raw": period_raw,
            "source_operation_method": operation,
            "source_fee": fee,
            "source_fee_omitted": not bool(fee),
            "source_time_omitted": not bool(source_schedule),
            "source_target_omitted": not bool(target),
            "source_venue_omitted": not bool(venue),
        }
    )
    merged = dict(row)
    merged.update(
        {
            "start_date": start,
            "end_date": end,
            "date_text": date_text,
            "period": date_text,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "apply_period": f"{apply_start} ~ {apply_end}",
            "target": target or row.get("target") or "대상 별도 안내",
            "venue_name": venue or row.get("venue_name") or "장소 별도 안내",
            "category_raw": "교육·강좌",
            "fee": fee or "요금 별도 안내",
            "schedule": operation,
            "schedule_raw": source_schedule or "시간 별도 안내",
            "source_status": (
                "접수대기" if status == "SCHEDULED" else "접수중" if status == "OPEN" else "접수완료"
            ),
            "status": status,
            "reservation_available": status == "OPEN",
            "source_period_year_corrected": corrected,
            "raw_fields": raw_fields,
        }
    )
    return _clean_row(merged)


def collect_yongin_oneclick_courses(
    target: Any,
    *,
    today: Optional[date | datetime | str] = None,
    max_pages: int = YONGIN_DEFAULT_MAX_PAGES,
    detail_limit: int = YONGIN_DEFAULT_DETAIL_LIMIT,
    max_requests: int = YONGIN_DEFAULT_MAX_REQUESTS,
    detail_workers: int = YONGIN_DEFAULT_DETAIL_WORKERS,
    timeout: int = 30,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_fn: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    parser = YONGIN_ONECLICK_PARSER
    fetcher: Optional[_Fetcher] = None
    try:
        if not _exact_target(target, YONGIN_ONECLICK_PROVIDER, YONGIN_ONECLICK_URL):
            raise YonginContractError("target is not the exact One-click owner")
        audit_day = _today(today)
        page_cap = _positive(max_pages, "max_pages")
        detail_cap = _positive(detail_limit, "detail_limit")
        workers = _positive(detail_workers, "detail_workers")
        fetcher = _make_fetcher(
            session_factory=session_factory,
            allow_raw_requests_for_tests=allow_raw_requests_for_tests,
            timeout=timeout,
            max_requests=max_requests,
            sleeper=sleeper,
        )
        all_rows: list[dict[str, Any]] = []
        page_counts: dict[int, int] = {}
        declared_total = 0
        first_rows: list[dict[str, Any]] = []
        for page in range(1, page_cap + 1):
            parsed, total = _parse_oneclick_cards(fetcher.get_soup(_oneclick_page_url(page)))
            if page == 1:
                declared_total = total
                first_rows = parsed
            page_counts[page] = len(parsed)
            if not parsed:
                break
            all_rows.extend(parsed)
        else:
            raise YonginContractError("One-click catalogue has no exact empty sentinel")
        if not all_rows or not declared_total or declared_total != len(all_rows):
            raise YonginContractError("One-click declared total does not match complete pages")
        if page_counts[max(page_counts)] != 0:
            raise YonginContractError("One-click sentinel is not empty")
        stable_rows, stable_total = _parse_oneclick_cards(fetcher.get_soup(_oneclick_page_url(1)))
        if stable_total != declared_total or _rows_signature(stable_rows) != _rows_signature(first_rows):
            raise YonginContractError("One-click list edge changed during collection")
        if len(all_rows) > detail_cap:
            raise YonginContractError(
                f"detail_limit {detail_cap} is below required {len(all_rows)} One-click details"
            )
        soups = fetcher.get_soups_parallel(
            [str(row["raw_url"]) for row in all_rows], workers=workers
        )
        rows = [
            _merge_oneclick_detail(row, soup, audit_day)
            for row, soup in zip(all_rows, soups)
        ]
        output = _finalize_rows(rows, dedupe_fn=dedupe_fn)
        meta = _success_meta(
            parser,
            output,
            fetcher,
            source_total=len(all_rows),
            branch_count=1,
            branch_counts={YONGIN_ONECLICK_BRANCH: len(output)},
            page_counts=page_counts,
            sentinel_count=0,
            stability_rechecks=1,
            detail_pages=len(rows),
            declared_total=declared_total,
            period_year_corrections=sum(
                bool(row.get("source_period_year_corrected")) for row in output
            ),
            status_counts=dict(Counter(row.get("source_status") for row in output)),
            candidate_id=YONGIN_ONECLICK_CANDIDATE_ID,
            pagination_detected=True,
        )
        return output, parser, meta
    except Exception as exc:
        return [], parser, _failure_meta(parser, exc, fetcher)
    finally:
        if fetcher is not None:
            fetcher.close()


def _yiyf_specs() -> tuple[dict[str, Any], ...]:
    return (
        {
            "kind": "education",
            "list_url": YONGIN_YIYF_COURSE_URL,
            "list_name": "m03_01_list_edu.asp",
            "detail_name": "m03_01_view_edu.asp",
            "branches": YONGIN_YIYF_EDUCATION_BRANCHES,
        },
        {
            "kind": "sports",
            "list_url": YONGIN_YIYF_SPORTS_URL,
            "list_name": "m03_01_list.asp",
            "detail_name": "m03_01_view.asp",
            "branches": YONGIN_YIYF_SPORTS_BRANCHES,
        },
    )


def _yiyf_directory(soup: BeautifulSoup, list_name: str) -> dict[str, str]:
    result: dict[str, str] = {}
    selector = f'a[href*="{list_name}?sitecode="]'
    for anchor in soup.select(selector):
        query = parse_qs(urlparse(anchor.get("href", "")).query)
        code = _clean((query.get("sitecode") or query.get("SiteCode") or [""])[0])
        name = _clean(anchor.get_text(" ", strip=True))
        if code and name:
            result[code] = name
    return result


def _yiyf_page_url(spec: Mapping[str, Any], branch_code: str, page: int) -> str:
    query = urlencode(
        [
            ("GotoPage", page),
            ("cnt_page", YONGIN_YIYF_PAGE_SIZE),
            ("s_item", ""),
            ("s_itemNM", ""),
            ("sitecode", branch_code),
        ]
    )
    return f"{spec['list_url']}?{query}"


def _yiyf_detail_url(
    spec: Mapping[str, Any], branch_code: str, identity: str, category_id: str = ""
) -> str:
    base = f"https://sports.yiyf.or.kr/main_new/m03/{spec['detail_name']}"
    query = urlencode(
        [
            ("SiteCode", branch_code),
            ("s_item", category_id),
            ("itemid", identity),
            ("GotoPage", 1),
        ]
    )
    return f"{base}?{query}"


_YIYF_TARGET_RE = re.compile(
    r"(?:만\s*)?\d{1,2}\s*(?:세\s*)?[~-]\s*(?:만\s*)?\d{1,2}\s*세"
    r"|(?:만\s*)?\d{1,3}\s*세"
    r"|유아|초등(?:학생|[1-6])?|초[1-6]|중고등|중등|중학생|중[1-3]"
    r"|고등학생|고[1-3]|청소년|성인|전연령|가족|학부모|부모|실버"
)


def _yiyf_target_from_title(title: Any) -> str:
    text = _clean(title)
    match = _YIYF_TARGET_RE.search(text)
    return _clean(text[match.start():]) if match else ""


def _complete_yiyf_required_fields(row: dict[str, Any]) -> dict[str, Any]:
    merged = dict(row)
    raw_fields = dict(merged.get("raw_fields") or {})
    source_period = _clean(raw_fields.get("source_period_raw"))
    start = _clean(merged.get("start_date"))
    end = _clean(merged.get("end_date"))
    date_text = _clean(merged.get("date_text"))
    if not date_text and start and end:
        date_text = f"{start} ~ {end}"
    period = date_text or (
        source_period if source_period and source_period != "-" else "일정 미정"
    )
    source_schedule = _clean(merged.get("schedule") or merged.get("schedule_raw"))
    source_fee = _clean(merged.get("fee"))
    source_target = _yiyf_target_from_title(merged.get("title"))
    source_category = _clean(raw_fields.get("source_category"))
    explicit_venue = _clean(merged.get("venue_name"))
    source_venue = explicit_venue or _clean(merged.get("branch"))
    venue_fell_back_to_branch = bool(
        raw_fields.get("source_venue_fallback_to_branch")
    ) or not bool(explicit_venue)
    raw_fields.update(
        {
            "source_fee": source_fee,
            "source_fee_omitted": not bool(source_fee),
            "source_target_omitted": not bool(source_target),
            "source_venue_fallback_to_branch": venue_fell_back_to_branch,
            "source_period_unavailable": period == "일정 미정",
            "source_time_omitted": not bool(source_schedule),
        }
    )
    merged.update(
        {
            "date_text": date_text or period,
            "period": period,
            "schedule": source_schedule or "시간 별도 안내",
            "schedule_raw": source_schedule or "시간 별도 안내",
            "fee": source_fee or "요금 별도 안내",
            "target": source_target or "대상 별도 안내",
            "venue_name": source_venue or "장소 별도 안내",
            "category_raw": source_category or "교육·강좌",
            "raw_fields": raw_fields,
        }
    )
    return _clean_row(merged)


def _parse_yiyf_cards(
    soup: BeautifulSoup,
    *,
    spec: Mapping[str, Any],
    branch_code: str,
) -> list[dict[str, Any]]:
    provider = YONGIN_YIYF_COURSE_PROVIDER
    expected_branch = spec["branches"][branch_code]
    selector = f'.board-list > ul > li > a[href*="{spec["detail_name"]}?"]'
    rows: list[dict[str, Any]] = []
    for card in soup.select(selector):
        query = parse_qs(urlparse(card.get("href", "")).query)
        identity = _clean((query.get("itemid") or [""])[0])
        category_id = _clean((query.get("s_item") or [""])[0])
        branch_node = card.select_one(".box1 > p")
        branch = _clean(branch_node.get_text(" ", strip=True)) if branch_node else ""
        category_node = card.select_one(".box1 > span")
        category = _clean(category_node.get_text(" ", strip=True)) if category_node else ""
        title_node = card.select_one(".box2 .title-blue")
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
        if branch != expected_branch or not identity or not title:
            raise YonginContractError("YIYF course card lost identity or official branch")
        fields: dict[str, str] = {}
        for group in card.select(".box2 .etc dl"):
            key = _clean(group.select_one("dt").get_text(" ", strip=True)) if group.select_one("dt") else ""
            value = _clean(group.select_one("dd").get_text(" ", strip=True)) if group.select_one("dd") else ""
            if key:
                fields[key] = value
        period_raw = _clean(fields.get("강습기간"))
        start, end, date_text = _date_range(period_raw)
        status_node = card.find("label", recursive=False)
        source_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
        if not source_status:
            raise YonginContractError("YIYF course card status is missing")
        raw_url = _yiyf_detail_url(spec, branch_code, identity, category_id)
        status = _status(source_status)
        rows.append(
            _complete_yiyf_required_fields(
                {
                    "provider_course_id": f"{provider}:{branch_code}:{identity}",
                    "provider": provider,
                    "source_id": f"{branch_code}:{identity}",
                    "source_kind": spec["kind"],
                    "title": title,
                    "description": title,
                    "branch": branch,
                    "branch_id": branch_code,
                    "start_date": start,
                    "end_date": end,
                    "date_text": date_text,
                    "schedule": fields.get("강습시간"),
                    "fee": fields.get("수강료"),
                    "venue_name": fields.get("강의실"),
                    "capacity_total": _integer(fields.get("정원")),
                    "source_status": source_status,
                    "status": status,
                    "reservation_available": status in {"OPEN", "WAITING"},
                    "raw_url": raw_url,
                    "application_url": raw_url,
                    "collection_category": "공공예약",
                    "domain_category": "교육·강좌",
                    "source_group": "municipal_reservation",
                    "service_group": "공공강좌",
                    "service_group_policy": "locked",
                    "classification_locked": True,
                    "municipality_code": YONGIN_CITY_CODE,
                    "municipality_name": YONGIN_CITY_NAME,
                    "raw_fields": {
                        "source_ledger": spec["kind"],
                        "source_category": category,
                        "source_category_id": category_id,
                        "source_status": source_status,
                        "source_period_raw": period_raw,
                    },
                    "source_period_missing": not bool(start and end),
                }
            )
        )
    return rows


def _is_yiyf_official_delay_page(soup: BeautifulSoup) -> bool:
    if soup.select_one(".board-view") is not None:
        return False
    delay = soup.select_one(".sub_contents .delay")
    delay_icon = soup.select_one(
        '.sub_contents .sec_img1 img[src*="delay_ico"]'
    )
    text = _clean(delay.get_text(" ", strip=True)) if delay else ""
    return bool(
        delay_icon
        and "현재 사용자가 많습니다." in text
        and "다시 시도해 주시기 바랍니다." in text
    )


def _yiyf_detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    container = soup.select_one(".board-view")
    if container is None:
        raise YonginContractError("YIYF public detail container is missing")
    for group in container.select("dl"):
        key_node = group.select_one(":scope > dt")
        value_node = group.select_one(":scope > dd")
        if key_node and value_node:
            result[_clean(key_node.get_text(" ", strip=True))] = _clean(
                value_node.get_text(" ", strip=True)
            )
    return result


def _merge_yiyf_detail(row: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    container = soup.select_one(".board-view")
    if container is None:
        page_text = _clean(soup.get_text(" ", strip=True))
        is_delay_page = _is_yiyf_official_delay_page(soup)
        if (
            not is_delay_page
            and (
                "프로그램안내" not in page_text
                or "수강료 미납안내" not in page_text
            )
        ):
            raise YonginContractError("YIYF public detail container is missing")
        merged = dict(row)
        merged["detail_unavailable_by_source"] = True
        if is_delay_page:
            merged["detail_temporarily_unavailable"] = True
        merged["raw_fields"] = {
            **dict(row.get("raw_fields") or {}),
            "public_detail_state": (
                "official_server_delay_after_retries"
                if is_delay_page
                else "official_list_link_has_no_detail_record"
            ),
        }
        return _complete_yiyf_required_fields(merged)
    page_text = _clean(container.get_text(" ", strip=True)) if container else ""
    if not _titles_match(row["title"], page_text):
        raise YonginContractError("YIYF detail title mismatch")
    fields = _yiyf_detail_pairs(soup)
    if _clean(fields.get("시설명")) != row.get("branch"):
        raise YonginContractError("YIYF detail branch mismatch")
    start, end, date_text = _date_range(fields.get("강습기간"))
    listed_start = _clean(row.get("start_date"))
    listed_end = _clean(row.get("end_date"))
    recovered_period = bool(not listed_start and not listed_end and start and end)
    if (listed_start or listed_end) and (start != listed_start or end != listed_end):
        raise YonginContractError("YIYF detail course period mismatch")
    merged = dict(row)
    merged.update(
        {
            "start_date": start or listed_start,
            "end_date": end or listed_end,
            "date_text": date_text,
            "schedule": _clean(fields.get("강습시간")) or row.get("schedule"),
            "fee": _clean(fields.get("수강료")) or row.get("fee"),
            "capacity_total": _integer(fields.get("강습정원")) or row.get("capacity_total"),
            "venue_name": _clean(fields.get("강의실")) or row.get("venue_name"),
            "source_period_recovered_from_detail": recovered_period,
        }
    )
    return _complete_yiyf_required_fields(merged)


def collect_yongin_yiyf_courses(
    target: Any,
    *,
    today: Optional[date | datetime | str] = None,
    max_pages: int = YONGIN_DEFAULT_MAX_PAGES,
    detail_limit: int = YONGIN_DEFAULT_DETAIL_LIMIT,
    max_requests: int = YONGIN_DEFAULT_MAX_REQUESTS,
    detail_workers: int = YONGIN_DEFAULT_DETAIL_WORKERS,
    timeout: int = 30,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_fn: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    parser = YONGIN_YIYF_COURSE_PARSER
    fetcher: Optional[_Fetcher] = None
    try:
        if not _exact_target(target, YONGIN_YIYF_COURSE_PROVIDER, YONGIN_YIYF_COURSE_URL):
            raise YonginContractError("target is not the exact YIYF course owner")
        audit_day = _today(today)
        page_cap = _positive(max_pages, "max_pages")
        detail_cap = _positive(detail_limit, "detail_limit")
        workers = _positive(detail_workers, "detail_workers")
        fetcher = _make_fetcher(
            session_factory=session_factory,
            allow_raw_requests_for_tests=allow_raw_requests_for_tests,
            timeout=timeout,
            max_requests=max_requests,
            sleeper=sleeper,
        )
        all_rows: list[dict[str, Any]] = []
        page_counts: dict[str, dict[int, int]] = {}
        nonempty_pages: list[tuple[Mapping[str, Any], str, int, list[dict[str, Any]]]] = []
        pages_used = 0
        for spec in _yiyf_specs():
            directory_soup = fetcher.get_soup(spec["list_url"])
            if _yiyf_directory(directory_soup, spec["list_name"]) != dict(spec["branches"]):
                raise YonginContractError("official YIYF branch directory drift detected")
            for branch_code in spec["branches"]:
                key = f"{spec['kind']}:{branch_code}"
                counts: dict[int, int] = {}
                branch_pages: list[tuple[int, list[dict[str, Any]]]] = []
                for page in range(1, page_cap + 1):
                    pages_used += 1
                    if pages_used > page_cap:
                        raise YonginContractError("max_pages exhausted before YIYF sentinels")
                    soup = fetcher.get_soup(_yiyf_page_url(spec, branch_code, page))
                    parsed = _parse_yiyf_cards(
                        soup, spec=spec, branch_code=branch_code
                    )
                    counts[page] = len(parsed)
                    if not parsed:
                        break
                    branch_pages.append((page, parsed))
                    all_rows.extend(parsed)
                else:
                    raise YonginContractError("YIYF branch has no exact empty sentinel")
                if counts[max(counts)] != 0:
                    raise YonginContractError("YIYF branch boundary is incomplete")
                page_counts[key] = counts
                if branch_pages:
                    nonempty_pages.append((spec, branch_code, *branch_pages[0]))
                    if branch_pages[-1][0] != branch_pages[0][0]:
                        nonempty_pages.append((spec, branch_code, *branch_pages[-1]))
        if not all_rows or not nonempty_pages:
            raise YonginContractError("YIYF course catalogue unexpectedly has no rows")

        for spec, branch_code, page, expected in (nonempty_pages[0], nonempty_pages[-1]):
            actual = _parse_yiyf_cards(
                fetcher.get_soup(_yiyf_page_url(spec, branch_code, page)),
                spec=spec,
                branch_code=branch_code,
            )
            if _rows_signature(actual) != _rows_signature(expected):
                raise YonginContractError("YIYF list edge changed during collection")

        current_rows = [
            row
            for row in all_rows
            if not row.get("end_date")
            or date.fromisoformat(str(row["end_date"])) >= audit_day
        ]
        if len(current_rows) > detail_cap:
            raise YonginContractError(
                f"detail_limit {detail_cap} is below required {len(current_rows)} YIYF details"
            )
        soups = fetcher.get_soups_parallel(
            [str(row["raw_url"]) for row in current_rows], workers=workers
        )
        initial_delay_pages = [
            index
            for index, soup in enumerate(soups)
            if _is_yiyf_official_delay_page(soup)
        ]
        delay_retry_count = 0
        for index in initial_delay_pages:
            for delay_seconds in (0.25, 0.75):
                sleeper(delay_seconds)
                soups[index] = fetcher.get_soup(str(current_rows[index]["raw_url"]))
                delay_retry_count += 1
                if not _is_yiyf_official_delay_page(soups[index]):
                    break
        persistent_delay_pages = sum(
            1 for soup in soups if _is_yiyf_official_delay_page(soup)
        )
        rows = []
        for row, soup in zip(current_rows, soups):
            try:
                rows.append(_merge_yiyf_detail(row, soup))
            except Exception as exc:
                raise YonginContractError(
                    f"YIYF detail {row.get('source_id') or '<unknown>'} failed: {exc}"
                ) from exc
        output = _finalize_rows(rows, dedupe_fn=dedupe_fn)
        branch_counts = Counter(row["branch"] for row in output)
        meta = _success_meta(
            parser,
            output,
            fetcher,
            source_total=len(all_rows),
            branch_count=len(YONGIN_YIYF_EDUCATION_BRANCHES) + len(YONGIN_YIYF_SPORTS_BRANCHES),
            active_branch_count=len(branch_counts),
            branch_counts=dict(sorted(branch_counts.items())),
            page_counts=page_counts,
            sentinel_count=0,
            stability_rechecks=2,
            detail_pages=len(rows),
            unavailable_detail_pages=sum(
                1 for row in rows if row.get("detail_unavailable_by_source")
            ),
            initial_delay_pages=len(initial_delay_pages),
            delay_retry_count=delay_retry_count,
            persistent_delay_pages=persistent_delay_pages,
            excluded_expired=len(all_rows) - len(current_rows),
            source_period_missing=sum(
                1 for row in all_rows if row.get("source_period_missing")
            ),
            source_period_recovered_from_detail=sum(
                1 for row in rows if row.get("source_period_recovered_from_detail")
            ),
            ledger_counts=dict(Counter(row["source_kind"] for row in output)),
            status_counts=dict(Counter(row["source_status"] for row in output)),
            candidate_id=YONGIN_YIYF_COURSE_CANDIDATE_ID,
            pagination_detected=True,
        )
        return output, parser, meta
    except Exception as exc:
        return [], parser, _failure_meta(parser, exc, fetcher)
    finally:
        if fetcher is not None:
            fetcher.close()


YONGIN_NON_EXECUTING_ALIASES: tuple[dict[str, str], ...] = (
    {
        "url": "https://lll.yongin.go.kr/yongin/irrgEdu/list.do?gbn=1&seq=23",
        "reason": "sibling_tab_of_existing_YONGIN_LIFELONG_LEARNING_owner",
        "owner": YONGIN_EXISTING_LIFELONG_PROVIDER,
    },
    {
        "url": "https://womenhall.yongin.go.kr/yongin/index.do",
        "reason": "legacy_hostname_alias_of_existing_lifelong_owner",
        "owner": YONGIN_EXISTING_LIFELONG_PROVIDER,
    },
    {
        "url": "https://www.gseek.kr/user/course/offline/list",
        "reason": "provincial_parent_already_filters_region_4146000000",
        "owner": YONGIN_GSEEK_PARENT_PROVIDER,
    },
    {
        "url": "https://www.yongin.go.kr/edu/index.do",
        "reason": "navigation_shell_to_integrated_reservation_and_lifelong_owners",
        "owner": YONGIN_RESERVATION_PROVIDER,
    },
    {
        "url": "https://www.yicf.or.kr/ccity/cop/bbs/selectBoardList.do?bbsId=notice_ccity",
        "reason": "announcement_subset_superseded_by_structured_YICF_academy",
        "owner": YONGIN_YICF_PROVIDER,
    },
    {
        "url": "https://www.yiyf.or.kr/sgyouth/index.do",
        "reason": "facility_notice_board_superseded_by_structured_youth_catalogues",
        "owner": YONGIN_ONECLICK_PROVIDER,
    },
    {
        "url": "https://www.yongin.go.kr/user/onlineReqst/",
        "reason": "mixed_one_off_application_forms_with_identity_and_attachment_workflows",
        "owner": "",
    },
)

YONGIN_RECOMMENDED_LIMITS: Mapping[str, Mapping[str, int]] = {
    YONGIN_RESERVATION_PROVIDER: {"max_pages": 20, "detail_limit": 100, "max_requests": 150},
    YONGIN_CHEOIN_PROVIDER: {"max_pages": 60, "detail_limit": 250, "max_requests": 400},
    YONGIN_GIHEUNG_PROVIDER: {"max_pages": 70, "detail_limit": 800, "max_requests": 950},
    YONGIN_SUJI_PROVIDER: {"max_pages": 50, "detail_limit": 600, "max_requests": 700},
    YONGIN_LIBRARY_PROVIDER: {"max_pages": 60, "detail_limit": 250, "max_requests": 350},
    YONGIN_YICF_PROVIDER: {"max_pages": 10, "detail_limit": 40, "max_requests": 60},
    YONGIN_ONECLICK_PROVIDER: {"max_pages": 5, "detail_limit": 20, "max_requests": 30},
    YONGIN_YIYF_COURSE_PROVIDER: {"max_pages": 35, "detail_limit": 260, "max_requests": 400},
}


def yongin_cross_owner_overlap(
    rows_by_provider: Mapping[str, Iterable[Mapping[str, Any]]]
) -> dict[str, Any]:
    seen: dict[tuple[str, str, str], set[str]] = {}
    for provider, rows in rows_by_provider.items():
        for row in rows:
            key = (
                _norm(row.get("title")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
            )
            if not all(key):
                continue
            seen.setdefault(key, set()).add(provider)
    overlaps = [
        {
            "title_key": key[0],
            "start_date": key[1],
            "end_date": key[2],
            "providers": sorted(providers),
        }
        for key, providers in seen.items()
        if len(providers) > 1
    ]
    overlaps.sort(key=lambda item: (item["title_key"], item["start_date"], item["providers"]))
    return {
        "overlap_count": len(overlaps),
        "overlaps": overlaps,
        "lifelong_owner_merged": False,
        "gseek_parent_merged": False,
        "gseek_parent_region_exclusion": YONGIN_CITY_CODE,
        "jachi_district_partitions_disjoint": True,
        "yicf_subcategory_tabs_executed": False,
    }


def collect_yongin_education_courses(
    target: Any,
    *,
    today: Optional[date | datetime | str] = None,
    max_pages: int = YONGIN_DEFAULT_MAX_PAGES,
    detail_limit: int = YONGIN_DEFAULT_DETAIL_LIMIT,
    max_requests: int = YONGIN_DEFAULT_MAX_REQUESTS,
    detail_workers: int = YONGIN_DEFAULT_DETAIL_WORKERS,
    timeout: int = 30,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_fn: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    provider = _target_provider(target)
    common = {
        "today": today,
        "max_pages": max_pages,
        "detail_limit": detail_limit,
        "max_requests": max_requests,
        "detail_workers": detail_workers,
        "timeout": timeout,
        "session_factory": session_factory,
        "sleeper": sleeper,
        "dedupe_fn": dedupe_fn,
        "allow_raw_requests_for_tests": allow_raw_requests_for_tests,
    }
    if provider == YONGIN_RESERVATION_PROVIDER:
        return collect_yongin_reservation_courses(target, **common)
    if provider in YONGIN_JACHI_OWNERS:
        return collect_yongin_jachi_courses(target, **common)
    if provider == YONGIN_LIBRARY_PROVIDER:
        return collect_yongin_library_courses(target, **common)
    if provider == YONGIN_YICF_PROVIDER:
        return collect_yongin_yicf_courses(target, **common)
    if provider == YONGIN_ONECLICK_PROVIDER:
        return collect_yongin_oneclick_courses(target, **common)
    if provider == YONGIN_YIYF_COURSE_PROVIDER:
        return collect_yongin_yiyf_courses(target, **common)
    error = YonginContractError(f"unknown Yongin provider {provider or '<empty>'}")
    return [], YONGIN_DISPATCH_PARSER, _failure_meta(YONGIN_DISPATCH_PARSER, error, None)


collect_yongin_education = collect_yongin_education_courses
collect_courses = collect_yongin_education_courses


__all__ = [name for name in globals() if name.startswith("YONGIN_")] + [
    "YonginContractError",
    "is_yongin_education_target",
    "is_target",
    "collect_yongin_reservation_courses",
    "collect_yongin_jachi_courses",
    "collect_yongin_library_courses",
    "collect_yongin_yicf_courses",
    "collect_yongin_oneclick_courses",
    "collect_yongin_yiyf_courses",
    "collect_yongin_education_courses",
    "collect_yongin_education",
    "collect_courses",
    "yongin_cross_owner_overlap",
]
