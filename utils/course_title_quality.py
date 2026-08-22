from __future__ import annotations

import html
import re
import unicodedata
from typing import Any


NON_COURSE_TITLE_KEYS = frozenset(
    {
        "조종면",
        "학습장소",
        "접수중강좌",
        "접수예정강좌",
        "접수마감강좌",
        "수강신청안내",
        "평생학습강사",
        "강사공개모집",
        "온라인접수",
        "방문접수",
        "인문교양",
        "공지사항",
        "교육강좌",
        "게시물검색",
        "접수모집",
        "민원안내",
        "구술전화신청민원",
        "성인신청",
        "제물포구청",
        "인천광역시서해구",
        "교육명장소",
        "선사체험마을",
        "선사체험마을신청",
        "디지털저장매체파기신청",
        "영천시평생학습관메인",
    }
)
SITE_SLOGAN_FRAGMENTS = (
    "오신것을환영",
    "방문을환영",
    "거침없는도약",
    "당찬당진",
    "시민이행복한",
    "군민이행복한",
    "구민이행복한",
    "살기좋은",
    "미래를여는",
    "더큰당진",
)
COURSE_INTENT_FRAGMENTS = (
    "강좌",
    "강의",
    "교실",
    "교육",
    "과정",
    "특강",
    "아카데미",
    "만들기",
    "배우기",
    "체험",
    "수업",
    "캠프",
    "워크숍",
    "세미나",
    "탐방",
    "탐험",
    "놀이",
)
PRACTICE_COURSE_TITLE_FRAGMENTS = (
    "수강신청연습용",
    "강의접수연습용",
    "접수연습용",
    "실제강좌아님",
    "실제강의아님",
)
SERVICE_ACCESS_PLACEHOLDER_KEYS = frozenset(
    {
        "서비스접속대기중",
        "서비스접속대기중입니다",
    }
)


def normalized_course_title(value: Any) -> tuple[str, str]:
    display = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    display = re.sub(r"<[^>]*>", " ", display)
    display = " ".join(display.split()).strip(" \t\r\n|/\\>·ㆍ-_:;,." )
    key = re.sub(r"[^0-9a-z가-힣]", "", display.casefold())
    return display, key


def semantic_course_title_rejection_reason(value: Any) -> str:
    """Return a stable reason when a title is a menu/site heading, not a course."""

    display, key = normalized_course_title(value)
    if not key:
        return "missing_title"
    if key in SERVICE_ACCESS_PLACEHOLDER_KEYS:
        return "service_access_placeholder"
    if any(fragment in key for fragment in PRACTICE_COURSE_TITLE_FRAGMENTS):
        return "practice_or_test_course"
    if key in NON_COURSE_TITLE_KEYS:
        return "navigation_or_category_heading"
    if re.fullmatch(
        r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|경기도|강원특별자치도|충청북도|충청남도|전북특별자치도|전라북도|전남광주통합특별시|전라남도|경상북도|경상남도|제주특별자치도)(?:[가-힣]{1,15}(?:시|군|구))?",
        key,
    ) or re.fullmatch(r"[가-힣]{2,15}(?:시|군|구)\s+[가-힣]{1,15}(?:읍|면|동)", display):
        return "administrative_area_heading"
    if re.fullmatch(
        r"[0-9a-z가-힣]{1,40}(?:시청|군청|구청|교육포털|평생학습관|통합예약시스템|통합예약)(?:메인)?",
        key,
    ) and not re.search(r"[-–—|:/()\[\]{}&]", display):
        return "site_heading"
    if key.endswith(("홈페이지메인", "사이트메인", "포털메인")):
        return "site_heading"
    if re.fullmatch(
        r"(?:전체|성인|아동|어린이|청소년|유아)?(?:교육|강좌|프로그램|체험|신청|접수|모집|검색|목록|안내|조회)+",
        key,
    ):
        return "navigation_or_category_heading"
    if re.search(r"(?:강의|강좌|교육|프로그램)정보를.*(?:자세|확인|보고|보기|찾기)", key):
        return "navigation_instruction"
    if any(fragment in key for fragment in ("클릭해주세요", "자세히보기", "바로가기")):
        return "navigation_instruction"
    if any(fragment in key for fragment in SITE_SLOGAN_FRAGMENTS) and not any(
        fragment in key for fragment in COURSE_INTENT_FRAGMENTS
    ):
        return "site_slogan"
    if re.search(r"(?:홈페이지|누리집).*(?:환영|방문)", key):
        return "site_slogan"
    return ""
