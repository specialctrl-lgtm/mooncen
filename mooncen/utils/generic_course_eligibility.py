from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import unquote_plus, urlparse

from utils.course_title_quality import semantic_course_title_rejection_reason
from utils.course_semantic_eligibility import (
    course_semantic_eligibility_decision,
    notice_link_can_be_course_candidate,
)


COURSE_INTENT_TOKENS = (
    "강좌",
    "강의",
    "교실",
    "교육",
    "과정",
    "특강",
    "아카데미",
    "학교",
    "수업",
    "체험",
    "캠프",
    "워크숍",
    "프로그램",
    "전시",
    "공연",
    "관람",
)
REGISTRATION_INTENT_TOKENS = (
    "수강신청",
    "교육신청",
    "강좌신청",
    "프로그램신청",
    "신청하기",
    "신청가능",
    "접수가능",
    "접수중",
    "접수예정",
    "모집중",
    "예약하기",
    "예약가능",
    "예매하기",
    "예매가능",
)
NOTICE_CONTEXT_TOKENS = (
    "공지사항",
    "보도자료",
    "언론보도",
    "새소식",
    "알림마당",
    "일반게시판",
    "자유게시판",
)
NON_LEARNER_TITLE_PATTERNS = (
    r"(?:강사|직원|기간제|업체|도우미)\s*(?:채용|모집)",
    r"(?:자원봉사자?|서포터즈)\s*모집",
    r"(?:입찰|용역|계약)\s*(?:공고|안내)",
    r"(?:합격자|선정자?)\s*(?:발표|공고)",
)

# These shapes identify editorial articles, not merely sites whose course
# catalogue happens to be implemented with a board component. Dedicated
# provider parsers may intentionally consume notices and do not call this
# generic-only policy.
HARD_EDITORIAL_URL_PATTERNS = (
    r"(?:^|/)news/article",
    r"articleview",
    r"(?:^|/)press(?:/|$)",
    r"(?:^|/)notice(?:/|$|\?)",
    r"notice/detail",
    r"(?:^|[?&])bo_table=notice(?:&|$)",
    r"(?:^|[?&])bid=notice(?:&|$)",
    r"selecteminwonnewsview",
    r"applylecturer",
    r"(?:^|/)recruit(?:/|$)",
    r"(?:^|/)volunteer(?:/|$)",
)
BOARD_SURFACE_TOKENS = (
    "/bbs/",
    "/board/",
    "board.do",
    "board.php",
    "mode=view",
    "selectbbs",
    "selectntt",
)
COURSE_CATALOG_URL_TOKENS = (
    "edu_app",
    "liblecture",
    "lecturelist",
    "programlist",
    "courselist",
    "lctre",
    "/education/",
    "/edu/",
    "/course/",
    "/program/",
    "/reservation/",
    "/reserve/",
    "/apply/",
    "sugang",
    "yeyak",
    "exprn",
)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _url_haystack(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return unquote_plus(text).casefold()
    return unquote_plus(f"{parsed.path}?{parsed.query}").casefold()


def is_course_catalog_url(value: Any) -> bool:
    haystack = _url_haystack(value)
    return any(token in haystack for token in COURSE_CATALOG_URL_TOKENS)


def editorial_surface_reason(
    raw_url: Any,
    *,
    source_url: Any = "",
    context: Any = "",
) -> str:
    """Classify an editorial surface without rejecting every board URL."""

    urls = [_url_haystack(raw_url), _url_haystack(source_url)]
    urls = [value for value in urls if value]
    for value in urls:
        if any(re.search(pattern, value, re.IGNORECASE) for pattern in HARD_EDITORIAL_URL_PATTERNS):
            return "editorial_article_url"

    context_text = _text(context).casefold()
    board_surface = any(token in value for value in urls for token in BOARD_SURFACE_TOKENS)
    course_catalog = any(is_course_catalog_url(value) for value in (raw_url, source_url))
    if board_surface and not course_catalog and any(token in context_text for token in NOTICE_CONTEXT_TOKENS):
        return "notice_board_context"
    return ""


def generic_link_is_editorial(text: Any, url: Any, *, page_context: Any = "") -> bool:
    context = _text(f"{page_context} {text}")
    reason = editorial_surface_reason(url, context=context)
    if reason and notice_link_can_be_course_candidate(text, url):
        return False
    return bool(reason)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _has_value(row: Mapping[str, Any], *keys: str) -> bool:
    return any(_text(row.get(key)) for key in keys)


def _pair_text(raw_fields: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in ("pairs", "detail_pairs"):
        pairs = _mapping(raw_fields.get(key))
        for label, value in pairs.items():
            values.append(f"{_text(label)} {_text(value)}")
    return _text(" ".join(values))


def generic_course_row_decision(row: Mapping[str, Any]) -> tuple[bool, str]:
    """Return whether a generic-parser row is safe to publish as a course.

    URL/menu/status/date tokens are discovery hints. Publication requires a
    meaningful identity plus independent course fields, or a clearly scoped
    catalogue detail carrying learner-registration intent.
    """

    title = _text(row.get("title"))
    title_rejection = semantic_course_title_rejection_reason(title)
    if title_rejection:
        return False, title_rejection
    if any(re.search(pattern, title, re.IGNORECASE) for pattern in NON_LEARNER_TITLE_PATTERNS):
        return False, "non_learner_recruitment"

    raw_fields = _mapping(row.get("raw_fields"))
    source_url = _text(raw_fields.get("source_url") or row.get("source_url"))
    surface_context = _text(
        raw_fields.get("surface_context")
        or raw_fields.get("page_context")
        or row.get("surface_context")
    )
    surface_reason = editorial_surface_reason(
        row.get("raw_url"),
        source_url=source_url,
        context=f"{surface_context} {title}",
    )
    semantic_decision = course_semantic_eligibility_decision(row)
    if surface_reason and not (
        semantic_decision.eligible
        and semantic_decision.reason
        == "notice_course_with_schedule_and_application_evidence"
        and notice_link_can_be_course_candidate(title, row.get("raw_url"))
    ):
        return False, surface_reason
    if not semantic_decision.eligible:
        return False, semantic_decision.reason
    if semantic_decision.reason == "notice_course_with_schedule_and_application_evidence":
        return True, semantic_decision.reason
    if "structured_course_fields" in semantic_decision.evidence:
        return True, "structured_course_fields"
    return True, "scoped_course_fields"
