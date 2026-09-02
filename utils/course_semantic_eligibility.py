from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping
from urllib.parse import unquote_plus, urlparse

from utils.course_title_quality import semantic_course_title_rejection_reason


POLICY_VERSION = "course_registration_v1"

COURSE_INTENT_TOKENS = (
    "강좌",
    "강의",
    "교실",
    "교육",
    "과정",
    "특강",
    "아카데미",
    "학교",
    "만들기",
    "배우기",
    "수업",
    "체험",
    "캠프",
    "워크숍",
    "세미나",
    "프로그램",
    "전시",
    "공연",
    "관람",
    "탐방",
    "놀이",
)
COURSE_CONTEXT_TOKENS = (
    "강좌",
    "교육",
    "체험",
    "프로그램",
    "공공예약",
    "문화센터",
    "평생학습",
    "교육센터",
    "교육관",
    "도서관",
    "박물관",
    "미술관",
    "과학관",
    "수련관",
)
REGISTRATION_STATUS_TOKENS = (
    "수강신청",
    "교육신청",
    "강좌신청",
    "프로그램신청",
    "신청가능",
    "신청중",
    "접수가능",
    "접수중",
    "접수예정",
    "모집중",
    "예약가능",
    "예약중",
    "예매가능",
    "대기접수",
    "마감",
)
NORMALIZED_REGISTRATION_STATUSES = frozenset(
    {
        "OPEN",
        "UPCOMING",
        "CLOSED",
        "FULL",
        "WAITLIST",
        "AVAILABLE",
        "SOLD_OUT",
    }
)

ALWAYS_REJECT_TITLE_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "non_learner_recruitment",
        r"(?:강사|직원|공무원|기간제|계약직|업체|도우미)\s*(?:채용|모집)"
        r"|(?:자원봉사자?|서포터즈)\s*모집"
        r"|채용\s*(?:공고|안내|모집|시험|계획)",
    ),
    (
        "procurement_or_contract_notice",
        r"(?:입찰|낙찰|용역|계약)\s*(?:공고|안내|결과|업체|제안)"
        r"|(?:제안서|견적서)\s*(?:제출|모집|평가)",
    ),
    (
        "result_announcement",
        r"(?:합격자|선정자?|당첨자?|선발자?)\s*(?:발표|공고|명단|결과)"
        r"|(?:선정|심사|추첨|모집|접수|신청)\s*결과(?:\s*(?:발표|공고|안내|조회))?"
        r"|^\s*결과\s*(?:발표|공고|안내|조회)",
    ),
    (
        "faq_or_general_information",
        r"(?:자주\s*묻는\s*질문|FAQ|Q\s*&\s*A|질의\s*응답|문의\s*답변|자료실|서식자료)",
    ),
    (
        "press_or_news_article",
        r"(?:보도\s*자료|언론\s*보도|뉴스|기고문|인터뷰|회의록)",
    ),
    (
        "operational_notice",
        r"(?:시스템|홈페이지|서버)\s*(?:점검|장애|중단|복구)"
        r"|(?:휴관|휴강|임시\s*폐쇄|서비스\s*중단)\s*(?:공지|안내)?"
        r"|(?:개인정보|이용약관|저작권)\s*(?:처리|변경|안내)",
    ),
)
NOTICE_TITLE_PATTERN = re.compile(
    r"(?:^|[\[\(【])\s*(?:공지(?:사항)?|알림|새소식)\s*(?:[\]\)】]|[:：-])?"
    r"|(?:공지(?:사항)?|알림)\s*$",
    re.IGNORECASE,
)
EDITORIAL_NEVER_URL_PATTERNS = (
    r"(?:^|/)news/article",
    r"articleview",
    r"(?:^|/)press(?:/|$)",
    r"selecteminwonnewsview",
    r"applylecturer",
    r"(?:^|/)recruit(?:/|$)",
    r"(?:^|/)volunteer(?:/|$)",
    r"(?:^|/)bid(?:/|$)",
)
NOTICE_URL_PATTERNS = (
    r"(?:^|/)notice(?:/|$|\?)",
    r"notice/detail",
    r"(?:^|[?&])bo_table=notice(?:&|$)",
    r"(?:^|[?&])bid=notice(?:&|$)",
)
COURSE_CATALOG_URL_TOKENS = (
    "edu_app",
    "liblecture",
    "lecturelist",
    "lecturedetail",
    "programlist",
    "programdetail",
    "courselist",
    "coursedetail",
    "lctre",
    "/education/",
    "/edu/",
    "/course/",
    "/class/",
    "/lecture/",
    "/program/",
    "/reservation/",
    "/reserve/",
    "/apply/",
    "sugang",
    "yeyak",
    "exprn",
)
APPLICATION_URL_TOKENS = (
    "apply",
    "reserve",
    "reservation",
    "receipt",
    "register",
    "booking",
    "ticket",
    "sugang",
    "yeyak",
    "신청",
    "접수",
    "예약",
    "예매",
)
DETAIL_URL_PATTERN = re.compile(
    r"(?:detail|view|read|select|lecture|lctre|course|program|class|edu)"
    r"|(?:[?&](?:id|idx|seq|no|code|courseid|lecturemasterid)=)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CourseSemanticDecision:
    eligible: bool
    reason: str
    evidence: tuple[str, ...]


class CourseSemanticEligibilityError(ValueError):
    def __init__(self, decision: CourseSemanticDecision) -> None:
        self.reason = decision.reason
        self.evidence = decision.evidence
        super().__init__(
            "Refusing to publish semantically ineligible course row: "
            f"{decision.reason}"
        )


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    adapted = getattr(value, "adapted", None)
    if isinstance(adapted, Mapping):
        return adapted
    if isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        if isinstance(decoded, Mapping):
            return decoded
    return {}


def _url_haystack(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return unquote_plus(text).casefold()
    return unquote_plus(f"{parsed.path}?{parsed.query}").casefold()


def _has_value(row: Mapping[str, Any], *keys: str) -> bool:
    return any(_text(row.get(key)) for key in keys)


def _raw_pair_text(raw_fields: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in ("pairs", "detail_pairs"):
        pairs = _mapping(raw_fields.get(key))
        values.extend(f"{_text(label)} {_text(value)}" for label, value in pairs.items())
    return _text(" ".join(values))


def _has_labeled_period(raw_fields: Mapping[str, Any], labels: tuple[str, ...]) -> bool:
    for key in ("pairs", "detail_pairs"):
        pairs = _mapping(raw_fields.get(key))
        if any(
            any(label in _text(pair_key) for label in labels) and _text(value)
            for pair_key, value in pairs.items()
        ):
            return True
    return False


def _url_matches(value: Any, patterns: tuple[str, ...]) -> bool:
    haystack = _url_haystack(value)
    return bool(haystack) and any(
        re.search(pattern, haystack, re.IGNORECASE) for pattern in patterns
    )


def _course_catalog_url(value: Any) -> bool:
    haystack = _url_haystack(value)
    return any(token in haystack for token in COURSE_CATALOG_URL_TOKENS)


def notice_link_can_be_course_candidate(text: Any, url: Any) -> bool:
    """Allow a bounded notice detail probe only when its title is course-like.

    This is a discovery exception, not publication approval. The fully parsed
    row must still pass :func:`course_semantic_eligibility_decision`.
    """

    title = _text(text)
    if _url_matches(url, EDITORIAL_NEVER_URL_PATTERNS):
        return False
    if not title or not (
        NOTICE_TITLE_PATTERN.search(title) or _url_matches(url, NOTICE_URL_PATTERNS)
    ):
        return False
    if semantic_course_title_rejection_reason(title):
        return False
    if any(
        re.search(pattern, title, re.IGNORECASE)
        for _reason, pattern in ALWAYS_REJECT_TITLE_PATTERNS
    ):
        return False
    return any(token in title for token in COURSE_INTENT_TOKENS)


def _application_url(row: Mapping[str, Any], raw_url: str) -> bool:
    application_url = _text(
        row.get("application_url")
        or row.get("reservation_url")
        or row.get("apply_url")
        or row.get("apply_link")
    )
    if not application_url:
        return False
    haystack = _url_haystack(application_url)
    if application_url != raw_url:
        return True
    return any(token in haystack for token in APPLICATION_URL_TOKENS)


def _explicit_registration_status(row: Mapping[str, Any], combined: str) -> bool:
    status = _text(row.get("status") or row.get("status_raw"))
    if status.upper() in NORMALIZED_REGISTRATION_STATUSES:
        return True
    compact = re.sub(r"\s+", "", f"{status} {combined}")
    return any(token in compact for token in REGISTRATION_STATUS_TOKENS)


def _record_decision_metadata(
    course: MutableMapping[str, Any], decision: CourseSemanticDecision
) -> None:
    metadata = {
        "policy": POLICY_VERSION,
        "eligible": decision.eligible,
        "reason": decision.reason,
        "evidence": list(decision.evidence),
    }
    course["semantic_eligibility_reason"] = decision.reason
    course["semantic_eligibility_evidence"] = list(decision.evidence)
    raw_value = course.get("raw_fields")
    if isinstance(raw_value, MutableMapping):
        raw_value["semantic_eligibility"] = metadata
        return
    adapted = getattr(raw_value, "adapted", None)
    if isinstance(adapted, MutableMapping):
        adapted["semantic_eligibility"] = metadata
        return
    if isinstance(raw_value, str) and raw_value.lstrip().startswith("{"):
        try:
            decoded = json.loads(raw_value)
        except (TypeError, ValueError):
            return
        if isinstance(decoded, dict):
            decoded["semantic_eligibility"] = metadata
            course["raw_fields"] = json.dumps(decoded, ensure_ascii=False, default=str)


def course_semantic_eligibility_decision(
    row: Mapping[str, Any],
) -> CourseSemanticDecision:
    """Fail-closed publication policy for learner-facing course rows.

    A title or a course-looking menu URL is only discovery evidence. A row must
    also carry at least two independent registration/course-detail signals.
    Editorial notice titles are accepted only with both a class schedule and
    application evidence.
    """

    title = _text(row.get("title") or row.get("title_raw"))
    title_rejection = semantic_course_title_rejection_reason(title)
    if title_rejection:
        return CourseSemanticDecision(False, title_rejection, ())

    for reason, pattern in ALWAYS_REJECT_TITLE_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return CourseSemanticDecision(False, reason, ())

    raw_fields = _mapping(row.get("raw_fields"))
    source_url = _text(raw_fields.get("source_url") or row.get("source_url"))
    raw_url = _text(row.get("raw_url"))
    pair_text = _raw_pair_text(raw_fields)
    combined = _text(
        " ".join(
            [
                title,
                pair_text,
                *(
                    _text(row.get(key))
                    for key in (
                        "period",
                        "apply_period",
                        "apply_period_raw",
                        "schedule_raw",
                        "status",
                        "status_raw",
                        "application_method_raw",
                        "description",
                    )
                ),
            ]
        )
    )

    if _url_matches(raw_url, EDITORIAL_NEVER_URL_PATTERNS):
        return CourseSemanticDecision(False, "editorial_article_url", ())

    has_schedule = bool(
        _has_value(
            row,
            "period",
            "schedule_raw",
            "schedule_dates",
            "start_date",
            "end_date",
            "class_period",
            "education_period",
        )
        or _has_labeled_period(
            raw_fields,
            ("교육기간", "강의기간", "수업기간", "운영기간", "행사기간", "일시"),
        )
    )
    has_apply_period = bool(
        _has_value(
            row,
            "apply_period",
            "apply_period_raw",
            "apply_start",
            "apply_end",
            "reception_period",
        )
        or _has_labeled_period(
            raw_fields,
            ("접수기간", "신청기간", "모집기간", "예약기간", "예매기간"),
        )
    )
    explicit_action = bool(raw_fields.get("explicit_application_action"))
    has_application_url = _application_url(row, raw_url)
    has_registration_status = _explicit_registration_status(row, combined)
    has_detail_url = bool(
        raw_url
        and (
            (source_url and raw_url != source_url)
            or _course_catalog_url(raw_url)
            or DETAIL_URL_PATTERN.search(_url_haystack(raw_url))
        )
    )
    structured_fields = sum(
        (
            _has_value(row, "target", "eligibility_raw"),
            _has_value(row, "venue_name", "venue_address", "room", "place"),
            _has_value(row, "capacity", "capacity_total", "capacity_current"),
            _has_value(row, "instructor"),
            bool(row.get("fee") not in (None, "", 0, "0"))
            or _has_value(row, "fee_raw"),
            bool(row.get("sessions") not in (None, "", 0, "0")),
        )
    )
    context_text = _text(
        " ".join(
            _text(row.get(key))
            for key in (
                "collection_category",
                "domain_category",
                "service_group",
                "program_type",
                "category_raw",
                "branch",
                "branch_name",
            )
        )
    )
    course_context = bool(
        any(token in title for token in COURSE_INTENT_TOKENS)
        or any(token in context_text for token in COURSE_CONTEXT_TOKENS)
        or _course_catalog_url(raw_url)
        or _course_catalog_url(source_url)
    )

    evidence = tuple(
        name
        for name, present in (
            ("course_schedule", has_schedule),
            ("application_period", has_apply_period),
            ("application_link", has_application_url),
            ("explicit_application_action", explicit_action),
            ("registration_status", has_registration_status),
            ("course_detail_link", has_detail_url),
            ("structured_course_fields", structured_fields > 0),
            ("course_context", course_context),
        )
        if present
    )

    notice_shaped = bool(
        NOTICE_TITLE_PATTERN.search(title)
        or _url_matches(raw_url, NOTICE_URL_PATTERNS)
        or any(
            token in _text(raw_fields.get("surface_context"))
            for token in ("공지사항", "새소식", "알림마당")
        )
    )
    application_evidence = bool(
        has_apply_period
        or has_application_url
        or explicit_action
        or has_registration_status
    )
    if notice_shaped:
        if has_schedule and application_evidence and course_context:
            return CourseSemanticDecision(
                True,
                "notice_course_with_schedule_and_application_evidence",
                evidence,
            )
        return CourseSemanticDecision(
            False, "notice_without_course_registration_evidence", evidence
        )

    if not course_context:
        return CourseSemanticDecision(False, "missing_course_context", evidence)
    if has_schedule and (has_apply_period or has_application_url or explicit_action or has_registration_status or has_detail_url):
        return CourseSemanticDecision(True, "schedule_and_application_evidence", evidence)
    if has_apply_period and (has_application_url or explicit_action or has_registration_status or has_detail_url):
        return CourseSemanticDecision(True, "application_period_and_course_detail", evidence)
    if (has_application_url or explicit_action) and has_registration_status and (
        has_detail_url or structured_fields > 0
    ):
        return CourseSemanticDecision(True, "actionable_course_detail", evidence)
    if has_detail_url and has_registration_status and structured_fields >= 1:
        return CourseSemanticDecision(True, "structured_registration_detail", evidence)
    return CourseSemanticDecision(
        False, "insufficient_course_registration_evidence", evidence
    )


def guard_course_before_upsert(
    course: MutableMapping[str, Any],
) -> CourseSemanticDecision:
    """Record the decision and reject an unsafe row before any SQL write."""

    decision = course_semantic_eligibility_decision(course)
    _record_decision_metadata(course, decision)
    if not decision.eligible:
        raise CourseSemanticEligibilityError(decision)
    return decision
