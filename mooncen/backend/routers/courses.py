import json
import ipaddress
import re
from html import unescape
from decimal import Decimal
from datetime import date, time
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Date, String, and_, any_, bindparam, case, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..provider_metadata import provider_label
from .auth import get_current_user, rate_limit, require_admin_user
from DB.course_lifecycle import effective_course_status
from description_cleaner import (
    clean_lotte_apply_period_raw,
    clean_lotte_description_text,
    has_lotte_apply_period_noise,
    has_lotte_description_noise,
)
from title_cleaner import clean_course_title
from service_group import (
    CULTURE_CENTER_PROVIDERS,
    EXPERIENCE_CONTENT_KEYWORDS,
    EXPERIENCE_EXCLUDED_PROGRAM_TYPES,
    EXPERIENCE_PROGRAM_TYPES,
    EXPERIENCE_SOURCE_GROUPS,
    LOCAL_GOVERNMENT_EDUCATION_BRANCH_TOKENS,
    LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS,
    LOCAL_GOVERNMENT_EDUCATION_OFFICE_TOKEN_RULES,
    PUBLIC_NON_ADMIN_EXPERIENCE_SOURCE_GROUPS,
    PUBLIC_COURSE_SOURCE_GROUPS,
    SERVICE_GROUP_EXPERIENCE,
    SERVICE_GROUP_PUBLIC_COURSE,
    infer_service_group,
    normalize_service_group,
)
from tools.standard_category_mapper import classify_standard_category, load_standard_categories, normalize_for_match
from utils.fee_semantics import fee_status
from utils.url_security import safe_external_http_url
from utils.text_quality import readable_text

router = APIRouter(prefix="/courses", tags=["courses"])


class CourseUpdateRequestPayload(BaseModel):
    reason: str = Field(default="click", min_length=1, max_length=40)
    source_url: Optional[str] = Field(default=None, max_length=2048)


def _public_http_url(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate or any(ord(char) < 32 for char in candidate):
        raise HTTPException(status_code=400, detail="Invalid source_url")
    try:
        parsed = urlsplit(candidate)
        _ = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid source_url") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Invalid source_url")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".localhost", ".local", ".internal")):
        raise HTTPException(status_code=400, detail="Private source_url is not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise HTTPException(status_code=400, detail="Private source_url is not allowed")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def _same_origin(left: str, right: str) -> bool:
    left_parts = urlsplit(left)
    right_parts = urlsplit(right)

    def origin(parts):
        default_port = 443 if parts.scheme.lower() == "https" else 80
        return parts.scheme.lower(), (parts.hostname or "").rstrip(".").lower(), parts.port or default_port

    return origin(left_parts) == origin(right_parts)

DAY_MAP = {
    "월": "월",
    "화": "화",
    "수": "수",
    "목": "목",
    "금": "금",
    "토": "토",
    "일": "일",
    "Mon": "월",
    "Tue": "화",
    "Wed": "수",
    "Thu": "목",
    "Fri": "금",
    "Sat": "토",
    "Sun": "일",
}

WEEKDAY_LABELS = ("월", "화", "수", "목", "금", "토", "일")
WEEKDAY_STORAGE_ALIASES = {
    "월": ("월", "월요일", "Mon"),
    "화": ("화", "화요일", "Tue"),
    "수": ("수", "수요일", "Wed"),
    "목": ("목", "목요일", "Thu"),
    "금": ("금", "금요일", "Fri"),
    "토": ("토", "토요일", "Sat"),
    "일": ("일", "일요일", "Sun"),
}
UNKNOWN_DAY_VALUES = ("요일 미정", "unknown", "day_unknown")

STATUS_LABELS = {
    "OPEN": "접수중",
    "SCHEDULED": "접수예정",
    "CLOSED": "마감",
    "WAITING": "대기접수",
    "DEADLINE": "마감임박",
}

CURRENT_COURSE_STATUSES = ("OPEN", "SCHEDULED", "DEADLINE", "WAITING")
APPLICATION_OPEN_STATUSES = ("OPEN", "DEADLINE")


def _seoul_today_expression():
    return cast(func.timezone("Asia/Seoul", func.now()), Date)


def course_closed_by_date_filter(course_model=models.Course):
    """SQL predicate equivalent of ``effective_course_status(...)=CLOSED``."""
    today = _seoul_today_expression()
    return or_(
        and_(
            course_model.end_date.isnot(None),
            course_model.end_date < today,
        ),
        and_(
            course_model.status.in_(APPLICATION_OPEN_STATUSES),
            course_model.apply_end.isnot(None),
            course_model.apply_end < today,
        ),
    )


def course_current_by_date_filter(course_model=models.Course):
    return ~course_closed_by_date_filter(course_model)


def course_effective_status_filter(statuses, course_model=models.Course):
    """Match requested statuses after applying date-based closure semantics."""
    selected = tuple(dict.fromkeys(str(status or "").strip().upper() for status in statuses))
    selected = tuple(status for status in selected if status)
    if not selected:
        return None

    closed_by_date = course_closed_by_date_filter(course_model)
    predicates = []
    if "CLOSED" in selected:
        predicates.append(or_(course_model.status == "CLOSED", closed_by_date))

    non_closed = tuple(status for status in selected if status != "CLOSED")
    if non_closed:
        predicates.append(
            and_(
                course_model.status.in_(non_closed),
                ~closed_by_date,
            )
        )
    return or_(*predicates)

AGE_GROUP_ALIASES = {
    "영아": ["INFANT"],
    "유아": ["TODDLER"],
    "아동": ["CHILD"],
    "청소년": ["TEEN"],
    "성인": ["ADULT"],
    "시니어": ["SENIOR"],
    "전체": ["ALL"],
    "전연령": ["ALL"],
    "영유아": ["INFANT", "TODDLER", "CHILD"],
}


CULTURE_CENTER_STANDARD_CATEGORY_CONFIG = str(
    Path(__file__).resolve().parents[2] / "config" / "culture_center_standard_categories.yaml"
)
UNCATEGORIZED_STANDARD_CATEGORY_KEYS = {"uncategorized"}
UNCATEGORIZED_STANDARD_CATEGORY_LABELS = {"미분류"}

EXPERIENCE_CATEGORY_NAMES = (
    "체험",
    "교육체험",
    "교육·체험",
    "체험·견학",
    "체험/견학",
    "체험행사",
    "견학/야외",
    "박물관",
    "과학관",
    "미술관",
    "박물관/과학관",
    "수목원/생태",
    "자연·생태",
    "예술/공연",
    "예술공연",
    "전시",
    "공연",
    "문화행사",
    "관람",
)

EDUCATION_CATEGORY_NAMES = (
    "교육",
    "교육·강좌",
    "공공교육",
    "공공강좌",
    "평생교육",
)


def _compatible_age_groups_for_months(months: int) -> List[str]:
    if months < 36:
        return ["INFANT", "TODDLER"]
    if months < 84:
        return ["TODDLER", "CHILD"]
    if months < 156:
        return ["CHILD"]
    if months < 216:
        return ["TEEN"]
    return ["ADULT", "SENIOR", "ALL"]


def _compact_scope_token(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _compact_text_column(column):
    return func.replace(func.lower(func.coalesce(column, "")), " ", "")


def _column_matches_value(column, value):
    return and_(column.isnot(None), column == value)


def _column_in_values(column, values):
    return and_(column.isnot(None), column.in_(tuple(values)))


def _column_starts_muni(column):
    # Avoid LIKE escape-string differences between SQLAlchemy sessions and the
    # raw psycopg2 query used by Ops while preserving a literal ``MUNI_`` prefix.
    return and_(column.isnot(None), func.upper(func.left(column, 5)) == "MUNI_")


def _columns_contain_scope_tokens(columns, tokens):
    patterns = tuple(
        f"%{compact_token}%"
        for token in tokens
        if (compact_token := _compact_scope_token(token))
    )
    pattern_array = bindparam(None, patterns, type_=ARRAY(String()))
    return or_(
        *(
            _compact_text_column(column).ilike(any_(pattern_array))
            for column in columns
        )
    )


def _columns_ilike_tokens(columns, tokens):
    patterns = tuple(f"%{token}%" for token in tokens if token)
    pattern_array = bindparam(None, patterns, type_=ARRAY(String()))
    return or_(
        *(
            and_(column.isnot(None), column.ilike(any_(pattern_array)))
            for column in columns
        )
    )


def _columns_ilike_token_without_fragments(columns, token, false_fragments):
    false_patterns = tuple(f"%{fragment}%" for fragment in false_fragments if fragment)
    false_pattern_array = bindparam(None, false_patterns, type_=ARRAY(String()))
    return or_(
        *(
            and_(
                column.isnot(None),
                column.ilike(f"%{token}%"),
                *(
                    (~column.ilike(any_(false_pattern_array)),)
                    if false_patterns
                    else ()
                ),
            )
            for column in columns
        )
    )


def _course_branch_has(course_model, predicate):
    """Build a branch EXISTS that stays correlated when the course is aliased."""

    return (
        select(1)
        .select_from(models.Branch)
        .where(models.Branch.id == course_model.branch_id, predicate)
        .correlate(course_model)
        .exists()
    )


def _course_not_explicitly_unavailable_filter(course_model=models.Course):
    """Keep unknown availability while excluding a source-confirmed false value."""

    return course_model.reservation_available.is_distinct_from(False)


def _course_culture_scope_filter(course_model=models.Course):
    return _column_in_values(course_model.provider, CULTURE_CENTER_PROVIDERS)


def _course_public_education_scope_filter(course_model=models.Course):
    public_provider_match = or_(
        _column_starts_muni(course_model.provider),
        _column_matches_value(course_model.provider, "PUBLIC"),
    )
    public_branch_match = _course_branch_has(
        course_model,
        or_(
            _column_starts_muni(models.Branch.provider),
            _column_matches_value(models.Branch.provider, "PUBLIC"),
        ),
    )
    return or_(
        public_provider_match,
        _column_in_values(course_model.source_group, tuple(sorted(PUBLIC_COURSE_SOURCE_GROUPS))),
        _column_matches_value(course_model.service_group, SERVICE_GROUP_PUBLIC_COURSE),
        public_branch_match,
    )


def _course_locked_public_course_filter(course_model=models.Course):
    """Return rows whose collector explicitly locked the public-course label."""

    locked_policy = func.lower(
        func.btrim(func.coalesce(course_model.raw_fields["service_group_policy"].astext, ""))
    )
    locked_group = func.replace(
        func.btrim(
            func.coalesce(
                course_model.raw_fields["service_group"].astext,
                course_model.service_group,
                "",
            )
        ),
        " ",
        "",
    )
    return and_(
        locked_policy == "locked",
        locked_group == SERVICE_GROUP_PUBLIC_COURSE.replace(" ", ""),
    )


def _course_locked_public_education_scope_filter(course_model=models.Course):
    """Trust explicit collector-owned education metadata over a branch-name guess."""

    explicit_education_category = or_(
        _column_in_values(course_model.domain_category, EDUCATION_CATEGORY_NAMES),
        _column_in_values(course_model.collection_category, EDUCATION_CATEGORY_NAMES),
        _column_in_values(
            course_model.raw_fields["domain_category"].astext,
            EDUCATION_CATEGORY_NAMES,
        ),
        _column_in_values(
            course_model.raw_fields["collection_category"].astext,
            EDUCATION_CATEGORY_NAMES,
        ),
    )
    return and_(
        _course_locked_public_course_filter(course_model),
        explicit_education_category,
    )


def _local_government_education_branch_filter():
    education_institution = (
        models.Branch.basic_info["education_institution"].astext
    )
    columns = (
        models.Branch.name,
        models.Branch.facility_type,
        models.Branch.facility_category,
        education_institution,
        models.Branch.basic_info["operator_address_backfill"]["target_name"].astext,
        models.Branch.basic_info["operator_address_backfill"]["matched_name"].astext,
    )
    office_match = or_(
        _columns_ilike_tokens(
            columns,
            LOCAL_GOVERNMENT_EDUCATION_BRANCH_TOKENS,
        ),
        *(
            _columns_ilike_token_without_fragments(columns, token, false_fragments)
            for token, false_fragments in LOCAL_GOVERNMENT_EDUCATION_OFFICE_TOKEN_RULES
        ),
        and_(
            education_institution.isnot(None),
            func.btrim(education_institution).op("~")(
                r"^[가-힣0-9 ]{1,40}(시|군|구|읍|면|동)$"
            ),
        ),
    )
    excluded_facility_match = _columns_ilike_tokens(
        columns,
        LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS,
    )
    return and_(office_match, ~excluded_facility_match)


def _course_local_government_education_scope_filter(course_model=models.Course):
    return and_(
        _course_public_education_scope_filter(course_model),
        or_(
            _course_locked_public_education_scope_filter(course_model),
            _course_branch_has(
                course_model,
                _local_government_education_branch_filter(),
            ),
        ),
    )


def _course_keyword_filter(keyword: str):
    keyword_text = keyword.strip()
    if len(keyword_text) < 2:
        raise ValueError("keyword must contain at least two non-whitespace characters")

    search = f"%{keyword_text}%"
    branch_keyword_filter = or_(
        models.Branch.name.ilike(search),
        models.Branch.branch_code.ilike(search),
        models.Branch.address.ilike(search),
    )
    course_keyword_filter = models.Course.search_document.op("@@")(
        func.websearch_to_tsquery(text("'simple'"), keyword_text)
    )
    matching_branch_ids = select(models.Branch.id).where(branch_keyword_filter)
    return or_(course_keyword_filter, models.Course.branch_id.in_(matching_branch_ids))


def _stringify(value) -> Optional[str]:
    return str(value) if value is not None else None


def _floatify(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _normalize_tags(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(tag).lstrip("#") for tag in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(tag).lstrip("#") for tag in parsed]
        except Exception:
            return [part.strip().lstrip("#") for part in value.split(",") if part.strip()]
    return []


def _serialize_branch(branch: Optional[models.Branch]):
    if not branch:
        return None
    return {
        "id": _stringify(branch.id),
        "provider": branch.provider,
        "provider_label": provider_label(branch.provider, branch.name),
        "branch_code": branch.branch_code,
        "name": branch.name,
        "address": branch.address,
        "phone": branch.phone,
        "lat": _floatify(branch.lat),
        "lon": _floatify(branch.lon),
        "website_url": safe_external_http_url(branch.website_url) or None,
        "operating_hours": branch.operating_hours,
        "regular_holiday": branch.regular_holiday,
        "admission_fee": branch.admission_fee,
        "facility_type": branch.facility_type,
        "facility_category": branch.facility_category,
        "facility_source": branch.facility_source,
        "facility_source_sheet": branch.facility_source_sheet,
        "facility_service_group": branch.facility_service_group,
        "facility_collection_category": branch.facility_collection_category,
        "region_sido": branch.region_sido,
        "region_sigungu": branch.region_sigungu,
        "basic_info": branch.basic_info,
    }


def _format_date(value: Optional[date]) -> Optional[str]:
    return value.strftime("%m.%d") if value else None


def _weekday_label_for_date(value: date) -> str:
    return WEEKDAY_LABELS[value.weekday()]


def _schedule_days_empty_filter():
    return func.coalesce(func.array_length(models.Course.schedule_days, 1), 0) == 0


def _schedule_day_matches(day_label: str):
    day_filters = [models.Course.schedule_days.any(alias) for alias in WEEKDAY_STORAGE_ALIASES.get(day_label, (day_label,))]
    return or_(*day_filters)


def _schedule_day_matches_or_missing(day_label: str):
    unknown_filters = [models.Course.schedule_days.any(value) for value in UNKNOWN_DAY_VALUES]
    return or_(_schedule_days_empty_filter(), *unknown_filters, _schedule_day_matches(day_label))


def _course_date_filter(course_date: date):
    course_date_text = course_date.isoformat()
    no_schedule_dates = or_(models.Course.schedule_dates.is_(None), models.Course.schedule_dates == [])
    schedule_day_matches = _schedule_day_matches_or_missing(_weekday_label_for_date(course_date))
    return or_(
        models.Course.schedule_dates.contains([course_date_text]),
        and_(
            no_schedule_dates,
            models.Course.start_date <= course_date,
            models.Course.end_date.isnot(None),
            models.Course.end_date >= course_date,
            schedule_day_matches,
        ),
        and_(
            no_schedule_dates,
            models.Course.start_date == course_date,
            models.Course.end_date.is_(None),
            schedule_day_matches,
        ),
    )


def _extract_time_range(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"(\d{1,2}:\d{2}\s*[-~]\s*\d{1,2}:\d{2})", value)
    if match:
        return re.sub(r"\s+", "", match.group(1)).replace("~", "-")
    match = re.search(r"(\d{1,2}:\d{2})", value)
    return match.group(1) if match else None


def _extract_sessions(course: models.Course) -> Optional[int]:
    if course.sessions and course.sessions > 0:
        return int(course.sessions)
    if course.schedule_raw:
        match = re.search(r"(\d+)\s*회", course.schedule_raw)
        if match:
            return int(match.group(1))
    return None


def _build_schedule_summary(course: models.Course) -> Optional[str]:
    parts: List[str] = []
    sessions = _extract_sessions(course)
    schedule_dates = sorted(str(value) for value in (course.schedule_dates or []) if value)
    if schedule_dates:
        first = schedule_dates[0]
        formatted_first = first[5:].replace("-", ".") if re.match(r"\d{4}-\d{2}-\d{2}", first) else first
        if len(schedule_dates) == 1:
            parts.append(formatted_first)
        else:
            parts.append(f"{formatted_first} 외 {len(schedule_dates) - 1}일")
    else:
        start = _format_date(course.start_date)
        end = _format_date(course.end_date)
        if sessions == 1 and start:
            parts.append(start)
        elif start and end and start != end:
            parts.append(f"{start}-{end}")
        elif start:
            parts.append(start)

    days = list(dict.fromkeys(course.schedule_days or []))
    if days:
        parts.append("/".join(days))

    time_range = _extract_time_range(course.schedule_raw)
    if time_range:
        parts.append(time_range)

    summary = " ".join(dict.fromkeys(part for part in parts if part))
    return summary or None


def _build_session_label(course: models.Course) -> Optional[str]:
    sessions = _extract_sessions(course)
    return f"{sessions}회" if sessions and sessions > 1 else None


def _display_title(course: models.Course) -> str:
    result = course.ai_title_result if isinstance(course.ai_title_result, dict) else {}
    clean_title = str(result.get("clean_title") or "").strip()
    if course.ai_title_processed and clean_title:
        cleaned_title, _removed = clean_course_title(clean_title)
        return unescape(cleaned_title or clean_title)
    cleaned_title, _removed = clean_course_title(course.title or "")
    return unescape(cleaned_title or course.title or "강좌명 미정")


def _readable_metadata(value) -> Optional[str]:
    return readable_text(value) or None


def _description_for_response(course: models.Course) -> Optional[str]:
    description = course.description
    if course.provider == "LOTTE" and description:
        cleaned = clean_lotte_description_text(description)
        if cleaned or has_lotte_description_noise(description):
            return cleaned
    return description


def _apply_period_raw_for_response(course: models.Course) -> Optional[str]:
    value = course.apply_period_raw
    if course.provider == "LOTTE" and value:
        cleaned = clean_lotte_apply_period_raw(value, course.apply_start, course.apply_end)
        if cleaned or has_lotte_apply_period_noise(value):
            return cleaned
    return value


def _service_group_for_response(course: models.Course) -> str:
    raw_fields = course.raw_fields if isinstance(course.raw_fields, dict) else {}
    if str(raw_fields.get("service_group_policy") or "").strip().lower() == "locked":
        locked_group = normalize_service_group(raw_fields.get("service_group") or course.service_group)
        if locked_group:
            return locked_group
    return infer_service_group(
        provider=course.provider,
        collection_category=course.collection_category,
        domain_category=course.domain_category,
        source_group=course.source_group,
        operator_type=course.operator_type,
        branch_name=course.branch.name if course.branch else "",
        venue_name=course.venue_name,
        raw_url=course.raw_url,
        title=" ".join(value for value in (course.title, course.title_raw) if value),
        category_raw=course.category_raw,
        program_type=course.program_type,
        service_group=course.service_group,
    )


def _is_culture_center_course(course: models.Course) -> bool:
    return course.provider in CULTURE_CENTER_PROVIDERS


def _standard_category_for_response(course: models.Course, description: Optional[str] = None) -> Optional[str]:
    stored_label = str(course.standard_category_label or "").strip()
    if stored_label:
        return stored_label

    config_path = CULTURE_CENTER_STANDARD_CATEGORY_CONFIG if _is_culture_center_course(course) else None
    result = classify_standard_category(
        {
            "title": _display_title(course),
            "title_raw": course.title_raw,
            "category_raw": course.category_raw,
            "collection_category": course.collection_category,
            "domain_category": course.domain_category,
            "source_group": course.source_group,
            "program_type": course.program_type,
            "description": description if description is not None else _description_for_response(course),
        },
        config_path,
    )
    if result.key in UNCATEGORIZED_STANDARD_CATEGORY_KEYS:
        return result.label
    return result.label


def _standard_category_candidate_filters(category: str):
    selected = (category or "").strip()
    if not selected or selected in UNCATEGORIZED_STANDARD_CATEGORY_LABELS:
        return []

    filters = []
    columns = (
        models.Course.title,
        models.Course.title_raw,
        models.Course.category_raw,
        models.Course.program_type,
    )
    for config_path in (None, CULTURE_CENTER_STANDARD_CATEGORY_CONFIG):
        categories, _unknown, source_only_terms = load_standard_categories(config_path)
        matched_categories = [
            row
            for row in categories
            if selected in {row.key, row.label} or selected.replace(" ", "") == row.label.replace(" ", "")
        ]
        for standard_category in matched_categories:
            for keyword in standard_category.keywords:
                keyword_text = str(keyword or "").strip()
                if not keyword_text or normalize_for_match(keyword_text) in source_only_terms:
                    continue
                if keyword_text.isascii() and len(keyword_text) <= 2:
                    continue
                for column in columns:
                    filters.append(and_(column.isnot(None), column.ilike(f"%{keyword_text}%")))
    return filters


def _standard_category_filters(category: str):
    selected = (category or "").strip()
    if not selected:
        return []

    exact_filters = []
    matched_any = False
    for config_path in (None, CULTURE_CENTER_STANDARD_CATEGORY_CONFIG):
        categories, unknown, _source_only_terms = load_standard_categories(config_path)
        if selected in {unknown["key"], unknown["label"]}:
            exact_filters.extend(
                [
                    models.Course.standard_category_key == unknown["key"],
                    models.Course.standard_category_label == unknown["label"],
                ]
            )
            matched_any = True
        for standard_category in categories:
            if selected in {standard_category.key, standard_category.label} or selected.replace(" ", "") == standard_category.label.replace(" ", ""):
                exact_filters.extend(
                    [
                        models.Course.standard_category_key == standard_category.key,
                        models.Course.standard_category_label == standard_category.label,
                    ]
                )
                matched_any = True

    fallback_filters = _standard_category_candidate_filters(category)
    if fallback_filters:
        exact_filters.append(
            and_(
                models.Course.standard_category_label.is_(None),
                or_(*fallback_filters),
            )
        )
    if matched_any:
        return exact_filters
    return fallback_filters


def _serialize_course(course: models.Course):
    description = _description_for_response(course)
    status = effective_course_status(course)
    reservation_available = False if status == "CLOSED" else course.reservation_available
    return {
        "id": _stringify(course.id),
        "provider": course.provider,
        "provider_label": provider_label(course.provider, course.branch.name if course.branch else course.venue_name),
        "provider_course_id": course.provider_course_id,
        "branch_id": _stringify(course.branch_id),
        "title": _display_title(course),
        "title_raw": course.title_raw,
        "title_prefix_removed": course.title_prefix_removed,
        "instructor": course.instructor,
        "fee": _floatify(course.fee),
        "fee_status": fee_status(course.fee),
        "material_fee": course.material_fee,
        "sessions": course.sessions,
        "start_date": course.start_date.isoformat() if course.start_date else None,
        "end_date": course.end_date.isoformat() if course.end_date else None,
        "apply_start": course.apply_start.isoformat() if course.apply_start else None,
        "apply_end": course.apply_end.isoformat() if course.apply_end else None,
        "apply_period_raw": _apply_period_raw_for_response(course),
        "capacity_total": course.capacity_total,
        "capacity_current": course.capacity_current,
        "capacity_remaining": course.capacity_remaining,
        "waitlist_total": course.waitlist_total,
        "venue_name": course.venue_name,
        "venue_address": course.venue_address,
        "application_url": safe_external_http_url(course.application_url) or None,
        "application_type": course.application_type,
        "application_method_raw": course.application_method_raw,
        "reservation_available": reservation_available,
        "discovery_status": course.discovery_status,
        "program_type": course.program_type,
        "eligibility_raw": course.eligibility_raw,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "target": course.target,
        "target_age_group": course.target_age_group,
        "target_min_age": course.target_min_age,
        "target_max_age": course.target_max_age,
        "target_age_is_explicit": course.target_age_is_explicit,
        "target_tags": list(course.target_tags or []),
        "category_raw": _readable_metadata(course.category_raw),
        "collection_category": _readable_metadata(course.collection_category),
        "domain_category": _readable_metadata(course.domain_category),
        "standard_category": _standard_category_for_response(course, description),
        "source_group": _readable_metadata(course.source_group),
        "operator_type": _readable_metadata(course.operator_type),
        "service_group": _service_group_for_response(course),
        "collection_type": course.collection_type,
        "schedule_raw": course.schedule_raw,
        "schedule_days": list(course.schedule_days or []),
        "schedule_dates": list(course.schedule_dates or []),
        "schedule_time_start": course.schedule_time_start.isoformat(timespec="minutes") if course.schedule_time_start else None,
        "schedule_time_end": course.schedule_time_end.isoformat(timespec="minutes") if course.schedule_time_end else None,
        "schedule_summary": _build_schedule_summary(course),
        "day_schedule": _build_schedule_summary(course),
        "session_label": _build_session_label(course),
        "description": description,
        "image_url": safe_external_http_url(course.image_url) or None,
        "view_count": course.view_count or 0,
        "raw_url": safe_external_http_url(course.raw_url) or None,
        "is_active": course.is_active,
        "first_seen_at": course.first_seen_at.isoformat() if course.first_seen_at else None,
        "last_seen_at": course.last_seen_at.isoformat() if course.last_seen_at else None,
        "removed_at": course.removed_at.isoformat() if course.removed_at else None,
        "change_detected_at": course.change_detected_at.isoformat() if course.change_detected_at else None,
        "created_at": course.created_at.isoformat() if course.created_at else None,
        "ai_category": _readable_metadata(course.ai_category),
        "ai_tags": _normalize_tags(course.ai_tags),
        "ai_summary": course.ai_summary,
        "ai_title_processed": course.ai_title_processed,
        "ai_title_confidence": _floatify(course.ai_title_confidence),
        "ai_title_result": course.ai_title_result,
        "branch": _serialize_branch(course.branch),
    }


def _serialize_update_request_row(row):
    data = dict(row._mapping)
    for key in ("requested_at", "expires_at", "last_checked_at"):
        if data.get(key):
            data[key] = data[key].isoformat()
    data["id"] = _stringify(data.get("id"))
    data["course_id"] = _stringify(data.get("course_id"))
    data["source_url"] = safe_external_http_url(data.get("source_url")) or None
    return data


def _course_experience_scope_filter(course_model=models.Course):
    """Experience covers culture-facility imports and rows classified as experience."""
    locked_public_course = _course_locked_public_course_filter(course_model)
    non_experience_program_guard = or_(
        course_model.program_type.is_(None),
        ~course_model.program_type.in_(tuple(sorted(EXPERIENCE_EXCLUDED_PROGRAM_TYPES))),
    )
    course_category_match = and_(
        non_experience_program_guard,
        or_(
            _column_matches_value(course_model.service_group, SERVICE_GROUP_EXPERIENCE),
            _column_in_values(course_model.program_type, tuple(sorted(EXPERIENCE_PROGRAM_TYPES))),
            _column_in_values(course_model.collection_category, EXPERIENCE_CATEGORY_NAMES),
            _column_in_values(course_model.domain_category, EXPERIENCE_CATEGORY_NAMES),
            _column_in_values(course_model.ai_category, EXPERIENCE_CATEGORY_NAMES),
            _column_in_values(course_model.source_group, tuple(sorted(EXPERIENCE_SOURCE_GROUPS))),
            _columns_contain_scope_tokens(
                (course_model.category_raw, course_model.program_type),
                EXPERIENCE_CONTENT_KEYWORDS,
            ),
        ),
    )
    local_government_branch_filter = _course_branch_has(
        course_model,
        _local_government_education_branch_filter(),
    )
    institution_branch_filter = _course_branch_has(
        course_model,
        or_(
            _column_matches_value(models.Branch.provider, "CULTURE_FACILITY"),
            models.Branch.facility_source.isnot(None),
            _column_matches_value(models.Branch.facility_service_group, "체험"),
            _column_matches_value(models.Branch.facility_collection_category, "체험"),
            _column_in_values(models.Branch.facility_category, EXPERIENCE_CATEGORY_NAMES),
            _column_in_values(models.Branch.facility_type, EXPERIENCE_CATEGORY_NAMES),
            _columns_ilike_tokens(
                (
                    models.Branch.name,
                    models.Branch.facility_type,
                    models.Branch.facility_category,
                ),
                LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS,
            ),
        ),
    )
    institution_experience_match = or_(
        _column_matches_value(course_model.provider, "CULTURE_FACILITY"),
        and_(
            ~local_government_branch_filter,
            or_(
                _column_in_values(
                    course_model.source_group,
                    tuple(sorted(PUBLIC_NON_ADMIN_EXPERIENCE_SOURCE_GROUPS)),
                ),
                institution_branch_filter,
            ),
        ),
    )
    return and_(
        non_experience_program_guard,
        ~locked_public_course,
        or_(institution_experience_match, course_category_match),
    )


def course_scope_filter(scope_key: str, course_model=models.Course):
    """Return the authoritative production course-scope predicate.

    The public course API and operational tooling must call this function so
    experience and education never drift into separate taxonomies.
    """

    normalized_scope = str(scope_key or "").strip().lower()
    culture_scope_filter = _course_culture_scope_filter(course_model)
    experience_scope_filter = _course_experience_scope_filter(course_model)
    local_education_scope_filter = _course_local_government_education_scope_filter(course_model)
    if normalized_scope in {"provider", "culture"}:
        return culture_scope_filter
    if normalized_scope == "experience":
        return and_(~culture_scope_filter, experience_scope_filter)
    if normalized_scope == "education":
        return and_(
            ~culture_scope_filter,
            ~experience_scope_filter,
            local_education_scope_filter,
        )
    if normalized_scope == "unmanaged":
        return and_(
            ~culture_scope_filter,
            ~experience_scope_filter,
            ~local_education_scope_filter,
        )
    raise ValueError("scope must be provider, culture, experience, education, or unmanaged")


def _csv_filter_values(
    raw: str | None,
    label: str,
    *,
    max_items: int,
    max_item_length: int = 100,
) -> list[str]:
    values = list(dict.fromkeys(value.strip() for value in (raw or "").split(",") if value.strip()))
    if len(values) > max_items:
        raise HTTPException(status_code=422, detail=f"Too many {label} filter values")
    if any(len(value) > max_item_length for value in values):
        raise HTTPException(status_code=422, detail=f"Invalid {label} filter value")
    return values


def _course_radius_clause(
    lat: float | None,
    lon: float | None,
    radius_km: float | None,
):
    provided = (lat is not None, lon is not None, radius_km is not None)
    if not any(provided):
        return None
    if not all(provided):
        raise HTTPException(status_code=422, detail="lat, lon, and radius_km must be provided together")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180 or not 0.1 <= radius_km <= 30:
        raise HTTPException(status_code=422, detail="Invalid course radius filter")

    return text(
        """
        EXISTS (
            SELECT 1
            FROM branches AS radius_branch
            WHERE radius_branch.id = courses.branch_id
              AND radius_branch.location IS NOT NULL
              AND ST_DWithin(
                  radius_branch.location,
                  ST_SetSRID(ST_MakePoint(:course_radius_lon, :course_radius_lat), 4326)::geography,
                  :course_radius_m
              )
        )
        """
    ).bindparams(
        course_radius_lat=lat,
        course_radius_lon=lon,
        course_radius_m=radius_km * 1000,
    )


@router.get(
    "/",
    response_model=schemas.CourseListResponse,
    dependencies=[Depends(rate_limit("course-list", 120, 60))],
)
def get_courses(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1, le=1_000),
    size: int = Query(20, ge=1, le=100),
    keyword: Optional[str] = Query(None, max_length=200),
    category: Optional[str] = Query(None, max_length=100),
    collection_category: Optional[str] = Query(None, max_length=1000),
    service_group: Optional[str] = Query(None, max_length=500),
    domain_category: Optional[str] = Query(None, max_length=500),
    scope: Optional[str] = Query(None, max_length=30),
    min_fee: Optional[float] = Query(None, ge=0, le=1_000_000_000),
    max_fee: Optional[float] = Query(None, ge=0, le=1_000_000_000),
    provider: Optional[str] = Query(None, max_length=2000),
    branch_id: Optional[UUID] = Query(None),
    branch_ids: Optional[str] = Query(None, max_length=8000),
    lat: Optional[float] = Query(None, ge=-90, le=90, description="Radius center latitude"),
    lon: Optional[float] = Query(None, ge=-180, le=180, description="Radius center longitude"),
    radius_km: Optional[float] = Query(None, ge=0.1, le=30, description="Course search radius in km"),
    fee_groups: Optional[str] = Query(None, max_length=100),
    age_groups: Optional[str] = Query(None, max_length=500),
    time_groups: Optional[str] = Query(None, max_length=100),
    statuses: Optional[str] = Query(None, max_length=200),
    child_age_months: Optional[int] = Query(None, ge=0, le=1800),
    days: Optional[str] = Query(None, max_length=200),
    course_date: Optional[date] = None,
    include_inactive: bool = False,
    exclude_unavailable: bool = False,
    sort: str = Query(
        "latest",
        pattern="^(latest|popular|deadline|start_date|price_asc|price_desc)$",
    ),
):
    if min_fee is not None and max_fee is not None and min_fee > max_fee:
        raise HTTPException(status_code=400, detail="min_fee cannot exceed max_fee")
    query = db.query(models.Course).options(joinedload(models.Course.branch))

    if not include_inactive:
        query = query.filter(models.Course.is_active.is_(True))

    if exclude_unavailable:
        # ``NULL`` means that the source did not expose availability.  The
        # mobile app treats only an explicit ``false`` as unavailable, so keep
        # unknown rows while filtering before pagination and total counting.
        query = query.filter(_course_not_explicitly_unavailable_filter())

    scope_key = scope.strip().lower() if scope else ""
    if scope_key:
        if scope_key not in {"provider", "experience", "education"}:
            raise HTTPException(status_code=400, detail="Invalid course scope")

        query = query.filter(course_scope_filter(scope_key))

    if keyword and keyword.strip():
        try:
            query = query.filter(_course_keyword_filter(keyword))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="검색어는 두 글자 이상 입력해 주세요.") from exc

    if category:
        standard_category_filters = _standard_category_filters(category)
        if scope_key == "provider" and standard_category_filters:
            query = query.filter(or_(*standard_category_filters))
        else:
            query = query.filter(
                or_(
                    models.Course.collection_category == category,
                    models.Course.domain_category == category,
                    models.Course.service_group == category,
                    models.Course.ai_category == category,
                    models.Course.program_type == category,
                    models.Course.category_raw.ilike(f"%{category}%"),
                    *standard_category_filters,
                )
            )

    if collection_category:
        selected_collection_categories = _csv_filter_values(
            collection_category,
            "collection category",
            max_items=20,
        )
        if selected_collection_categories:
            category_filters = []
            for selected_category in selected_collection_categories:
                standard_category_filters = _standard_category_filters(selected_category)
                if scope_key == "provider" and standard_category_filters:
                    category_filters.extend(standard_category_filters)
                    continue
                category_filters.append(models.Course.collection_category == selected_category)
                category_filters.append(models.Course.domain_category == selected_category)
                category_filters.append(models.Course.service_group == selected_category)
                category_filters.append(models.Course.ai_category == selected_category)
                category_filters.append(models.Course.program_type == selected_category)
                category_filters.append(models.Course.source_group == selected_category)
                category_filters.append(models.Course.category_raw.ilike(f"%{selected_category}%"))
                category_filters.extend(standard_category_filters)
            query = query.filter(or_(*category_filters))

    if service_group:
        selected_service_groups = [
            normalize_service_group(value) or value
            for value in _csv_filter_values(service_group, "service group", max_items=20)
        ]
        selected_service_groups = [value for value in dict.fromkeys(selected_service_groups) if value]
        if len(selected_service_groups) == 1:
            query = query.filter(models.Course.service_group == selected_service_groups[0])
        elif selected_service_groups:
            query = query.filter(models.Course.service_group.in_(selected_service_groups))

    if domain_category:
        selected_domain_categories = _csv_filter_values(domain_category, "domain category", max_items=20)
        if len(selected_domain_categories) == 1:
            query = query.filter(models.Course.domain_category == selected_domain_categories[0])
        elif selected_domain_categories:
            query = query.filter(models.Course.domain_category.in_(selected_domain_categories))

    if provider:
        selected_providers = _csv_filter_values(provider, "provider", max_items=50)
        if len(selected_providers) == 1:
            query = query.filter(models.Course.provider == selected_providers[0])
        elif selected_providers:
            query = query.filter(models.Course.provider.in_(selected_providers))

    if branch_id:
        query = query.filter(models.Course.branch_id == branch_id)
    elif branch_ids:
        raw_branch_ids = _csv_filter_values(branch_ids, "branch", max_items=100, max_item_length=36)
        try:
            selected_branch_ids = [UUID(value) for value in raw_branch_ids]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid branch filter value") from exc
        if selected_branch_ids:
            query = query.filter(models.Course.branch_id.in_(selected_branch_ids))

    radius_clause = _course_radius_clause(lat, lon, radius_km)
    if radius_clause is not None:
        query = query.filter(radius_clause)

    if min_fee is not None:
        query = query.filter(models.Course.fee >= min_fee)
    if max_fee is not None:
        query = query.filter(models.Course.fee <= max_fee)
    if fee_groups:
        fee_filters = []
        for fee_group in _csv_filter_values(fee_groups, "fee group", max_items=10, max_item_length=20):
            if fee_group == "free":
                fee_filters.append(models.Course.fee <= 0)
            elif fee_group == "under50000":
                fee_filters.append(and_(models.Course.fee > 0, models.Course.fee <= 50000))
            elif fee_group == "under100000":
                fee_filters.append(and_(models.Course.fee > 50000, models.Course.fee <= 100000))
            elif fee_group == "over100000":
                fee_filters.append(models.Course.fee > 100000)
        if fee_filters:
            query = query.filter(or_(*fee_filters))

    if age_groups:
        selected_age_groups = []
        include_unknown_age = False
        for value in _csv_filter_values(age_groups, "age group", max_items=20):
            if value in {"연령 미정", "UNKNOWN", "미정"}:
                include_unknown_age = True
                continue
            selected_age_groups.extend([value, *AGE_GROUP_ALIASES.get(value, [])])
        selected_age_groups = list(dict.fromkeys(selected_age_groups))
        age_filters = []
        if selected_age_groups:
            age_filters.append(models.Course.target_age_group.in_(selected_age_groups))
        if include_unknown_age:
            age_filters.append(models.Course.target_age_group.is_(None))
        if age_filters:
            query = query.filter(or_(*age_filters))

    if child_age_months is not None:
        age_unit_text = func.concat(
            func.coalesce(models.Course.target, ""),
            " ",
            func.coalesce(models.Course.title, ""),
            " ",
            func.coalesce(models.Course.eligibility_raw, ""),
        )
        has_month_unit = age_unit_text.ilike("%개월%")
        has_month_unit = or_(
            has_month_unit,
            age_unit_text.ilike("%\uac1c\uc6d4%"),
            age_unit_text.ilike("%month%"),
            age_unit_text.ilike("%months%"),
        )
        teen_or_adult_year_group = models.Course.target_age_group.in_(["TEEN", "ADULT", "SENIOR"])
        child_year_group = models.Course.target_age_group == "CHILD"
        infant_or_toddler_year_group = models.Course.target_age_group.in_(["INFANT", "TODDLER"])
        min_age_looks_like_years = and_(
            models.Course.target_min_age.isnot(None),
            ~has_month_unit,
            or_(
                and_(teen_or_adult_year_group, models.Course.target_min_age <= 120),
                and_(child_year_group, models.Course.target_min_age <= 18),
                and_(infant_or_toddler_year_group, models.Course.target_min_age <= 10),
            ),
        )
        max_age_looks_like_years = and_(
            models.Course.target_max_age.isnot(None),
            ~has_month_unit,
            or_(
                and_(teen_or_adult_year_group, models.Course.target_max_age <= 120),
                and_(child_year_group, models.Course.target_max_age <= 18),
                and_(infant_or_toddler_year_group, models.Course.target_max_age <= 10),
            ),
        )
        min_months = case(
            (
                min_age_looks_like_years,
                models.Course.target_min_age * 12,
            ),
            else_=models.Course.target_min_age,
        )
        max_months = case(
            (
                max_age_looks_like_years,
                models.Course.target_max_age * 12,
            ),
            else_=models.Course.target_max_age,
        )
        explicit_age_range = or_(
            models.Course.target_min_age.isnot(None),
            models.Course.target_max_age.isnot(None),
        )
        compatible_groups = _compatible_age_groups_for_months(child_age_months)
        query = query.filter(
            or_(
                and_(
                    explicit_age_range,
                    or_(min_months.is_(None), min_months <= child_age_months),
                    or_(max_months.is_(None), max_months >= child_age_months),
                ),
                and_(
                    ~explicit_age_range,
                    models.Course.target_age_group.in_(compatible_groups),
                ),
            )
        )

    if statuses:
        selected_statuses = _csv_filter_values(statuses, "status", max_items=10, max_item_length=32)
        if selected_statuses:
            query = query.filter(course_effective_status_filter(selected_statuses))

    if time_groups:
        time_filters = []
        for time_group in _csv_filter_values(time_groups, "time group", max_items=10, max_item_length=32):
            if time_group == "morning":
                time_filters.append(models.Course.schedule_time_start < time(12, 0))
            elif time_group == "afternoon":
                time_filters.append(and_(models.Course.schedule_time_start >= time(12, 0), models.Course.schedule_time_start < time(18, 0)))
            elif time_group == "evening":
                time_filters.append(models.Course.schedule_time_start >= time(18, 0))
            elif time_group in {"unknown", "time_unknown"}:
                time_filters.append(models.Course.schedule_time_start.is_(None))
        if time_filters:
            query = query.filter(or_(*time_filters))

    if days:
        day_filters = []
        for day in _csv_filter_values(days, "day", max_items=10, max_item_length=32):
            if day in {"요일 미정", "unknown", "day_unknown"}:
                day_filters.append(_schedule_days_empty_filter())
            elif day in DAY_MAP:
                day_filters.append(_schedule_day_matches(DAY_MAP[day]))
        if day_filters:
            query = query.filter(or_(*day_filters))

    if course_date:
        query = query.filter(_course_date_filter(course_date))

    if sort == "popular":
        ordering = (models.Course.view_count.desc(), models.Course.updated_at.desc().nullslast())
    elif sort == "deadline":
        ordering = (models.Course.apply_end.asc().nullslast(), models.Course.start_date.asc().nullslast())
    elif sort == "start_date":
        ordering = (models.Course.start_date.desc().nullslast(), models.Course.updated_at.desc().nullslast())
    elif sort == "price_asc":
        ordering = (models.Course.fee.asc().nullslast(), models.Course.updated_at.desc().nullslast())
    elif sort == "price_desc":
        ordering = (models.Course.fee.desc().nullslast(), models.Course.updated_at.desc().nullslast())
    else:
        ordering = (models.Course.created_at.desc().nullslast(), models.Course.updated_at.desc().nullslast())
    rows = (
        query.add_columns(func.count(models.Course.id).over().label("_total"))
        .order_by(*ordering, models.Course.id.asc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    total = int(rows[0][1]) if rows else (query.order_by(None).count() if page > 1 else 0)

    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [_serialize_course(row[0]) for row in rows],
    }


@router.get(
    "/update-requests",
    dependencies=[
        Depends(rate_limit("course-update-requests-read", 60, 60)),
        Depends(require_admin_user),
    ],
)
def get_course_update_requests(
    db: Session = Depends(get_db),
    active_only: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
):
    where_clause = "WHERE req.status = 'pending' AND req.expires_at > now()" if active_only else ""
    rows = db.execute(
        text(
            f"""
            SELECT
                req.id,
                req.course_id,
                req.reason,
                req.status,
                req.source_url,
                req.request_count,
                req.requested_at,
                req.expires_at,
                req.last_checked_at,
                req.check_result,
                c.title,
                c.provider,
                c.status AS course_status,
                b.name AS branch_name
            FROM course_update_requests req
            JOIN courses c ON c.id = req.course_id
            LEFT JOIN branches b ON b.id = c.branch_id
            {where_clause}
            ORDER BY req.requested_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).fetchall()

    return {
        "total": len(rows),
        "items": [_serialize_update_request_row(row) for row in rows],
    }


@router.get(
    "/{course_id}",
    response_model=schemas.Course,
    dependencies=[Depends(rate_limit("course-detail", 240, 60))],
)
def get_course_detail(course_id: UUID, db: Session = Depends(get_db)):
    course = (
        db.query(models.Course)
        .options(joinedload(models.Course.branch))
        .filter(models.Course.id == course_id)
        .first()
    )
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    # A view must not change the course-content freshness timestamp.
    # ORM Query.update() applies Course.updated_at's on-update expression,
    # exceeding the API role's deliberately narrow UPDATE(view_count) grant.
    db.execute(
        text(
            """
            UPDATE courses
            SET view_count = COALESCE(view_count, 0) + 1
            WHERE id = :course_id
            """
        ),
        {"course_id": course_id},
    )
    db.commit()
    course.view_count = (course.view_count or 0) + 1
    return _serialize_course(course)


@router.post(
    "/{course_id}/update-request",
    dependencies=[Depends(rate_limit("course-update-request", 20, 3600))],
)
def request_course_update(
    course_id: UUID,
    payload: CourseUpdateRequestPayload,
    db: Session = Depends(get_db),
    _user: models.User = Depends(get_current_user),
):
    course = db.query(models.Course).filter(models.Course.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    reason = payload.reason.strip() or "click"
    canonical_value = course.raw_url or course.application_url
    canonical_url = _public_http_url(canonical_value) if canonical_value else None
    source_url = canonical_url
    if payload.source_url:
        requested_url = _public_http_url(payload.source_url)
        if not canonical_url or not _same_origin(requested_url, canonical_url):
            raise HTTPException(status_code=400, detail="source_url must match the course source origin")
        source_url = requested_url

    row = db.execute(
        text(
            """
            INSERT INTO course_update_requests (
                course_id,
                reason,
                status,
                source_url,
                request_count,
                requested_at,
                expires_at,
                created_at,
                updated_at
            )
            VALUES (
                :course_id,
                :reason,
                'pending',
                :source_url,
                1,
                now(),
                now() + interval '1 hour',
                now(),
                now()
            )
            ON CONFLICT (course_id) WHERE status = 'pending'
            DO UPDATE SET
                reason = EXCLUDED.reason,
                source_url = COALESCE(EXCLUDED.source_url, course_update_requests.source_url),
                request_count = course_update_requests.request_count + 1,
                requested_at = now(),
                expires_at = now() + interval '1 hour',
                updated_at = now()
            RETURNING
                id,
                course_id,
                reason,
                status,
                source_url,
                request_count,
                requested_at,
                expires_at,
                last_checked_at,
                check_result
            """
        ),
        {"course_id": course_id, "reason": reason, "source_url": source_url},
    ).fetchone()
    db.commit()

    return _serialize_update_request_row(row)
