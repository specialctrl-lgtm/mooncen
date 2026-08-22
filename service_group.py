from __future__ import annotations

from typing import Any, Iterable


SERVICE_GROUP_CULTURE_CENTER = "문화센터"
SERVICE_GROUP_EXPERIENCE = "체험"
SERVICE_GROUP_PUBLIC_COURSE = "공공강좌"
SERVICE_GROUP_OTHER = "기타"

SERVICE_GROUPS = {
    SERVICE_GROUP_CULTURE_CENTER,
    SERVICE_GROUP_EXPERIENCE,
    SERVICE_GROUP_PUBLIC_COURSE,
    SERVICE_GROUP_OTHER,
}

# Accepted source labels. Values are compared after removing ordinary spaces;
# these sets are also rendered into the database fallback classifier so direct
# SQL writers and Python ingestion normalize explicit labels identically.
CULTURE_CENTER_ALIASES = {
    SERVICE_GROUP_CULTURE_CENTER,
}
EXPERIENCE_ALIASES = {
    SERVICE_GROUP_EXPERIENCE,
    "경험",
    "교육체험",
    "체험행사",
    "체험견학",
}
PUBLIC_COURSE_ALIASES = {
    SERVICE_GROUP_PUBLIC_COURSE,
    "교육",
    "공공교육",
    "공공강의",
}
OTHER_ALIASES = {
    SERVICE_GROUP_OTHER,
}

SERVICE_GROUP_RULE_VERSION = "2026-07-29.1"

CULTURE_CENTER_PROVIDERS = {
    "HOMEPLUS",
    "LOTTE",
    "EMART",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
}

EXPERIENCE_SOURCE_GROUPS = {
    "museum_science",
    "science_museum",
    "museum",
    "arts_culture",
    "national_institution",
}

# Institution-level targets are stronger evidence than a generic municipal
# reservation bucket.  This helper is intentionally target-only: course titles
# are not inspected, so an incidental "도서관" mention inside an ordinary
# public lecture does not move that row into the experience scope.
EXPERIENCE_INSTITUTION_SOURCE_GROUPS = {
    "library",
    "museum",
    "museum_science",
    "science_museum",
}
LIBRARY_INSTITUTION_KEYWORDS = ("도서관",)
MUSEUM_SCIENCE_INSTITUTION_KEYWORDS = ("미술관", "박물관", "과학관")

# The public "education" navigation scope is intentionally narrower than the
# ingestion-level public-course service group. Only administrative facilities
# run from a city/county/district office or a local community office belong in
# that scope.
LOCAL_GOVERNMENT_EDUCATION_BRANCH_TOKENS = (
    "주민센터",
    "주민자치",
    "행정복지센터",
    "행정복지센타",
    "동사무소",
    "읍사무소",
    "면사무소",
    "자치회관",
)
LOCAL_GOVERNMENT_EDUCATION_OFFICE_TOKEN_RULES = (
    ("시청", ("시청각", "시청자", "시청소년", "시청년")),
    ("군청", ("군청소년", "군청년")),
    ("구청", ("구청소년", "구청년")),
)
LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS = (
    "도서관",
    "박물관",
    "미술관",
    "과학관",
    "문화회관",
    "문화센터",
    "문화재단",
    "문화의집",
    "문화공간",
    "아트센터",
    "체육관",
    "체육센터",
    "체육회관",
    "스포츠센터",
    "종합운동장",
    "복지관",
    "복지회관",
    "청소년수련관",
    "청소년센터",
    "청소년문화의집",
    "청년센터",
    "청년지원센터",
    "수련원",
    "공연장",
    "극장",
    "전시관",
    "수목원",
    "생태관",
)
PUBLIC_NON_ADMIN_EXPERIENCE_SOURCE_GROUPS = {
    *EXPERIENCE_INSTITUTION_SOURCE_GROUPS,
    "arts_culture",
    "national_institution",
    "sports_facility",
    "sports_reservation",
    "welfare",
    "youth",
    "arboretum_ecology",
}

# Rows under these source buckets are intentionally disabled and must not be
# resurrected merely because an old title still contains an experience word.
EXCLUDED_SOURCE_GROUPS = {
    "deprecated",
}

EXCLUDED_DOMAIN_CATEGORIES = {
    "제외",
}

# ``program_type`` is normalized by the collectors before service grouping.
# Exact program evidence is intentionally kept separate from provider/source
# metadata so a public reservation target can contain both ordinary courses
# and real experience programs.
EXPERIENCE_PROGRAM_TYPES = {
    "체험",
    "견학",
    "탐방",
    "관람",
    "전시",
    "공연",
    "캠프",
}

EXPERIENCE_EXCLUDED_PROGRAM_TYPES = {
    "숙박",
    "대관",
}

PUBLIC_COURSE_SOURCE_GROUPS = {
    "public_reservation",
    "municipal_reservation",
    "lifelong_learning",
    "municipal_lifelong_learning",
    "education_office_reservation",
    "sports_reservation",
    "library",
}

EXPERIENCE_KEYWORDS = (
    "체험",
    "교육체험",
    "교육·체험",
    "체험·견학",
    "체험/견학",
    "체험행사",
    "견학",
    "탐방",
    "박물관",
    "과학관",
    "미술관",
    "문화재단",
    "예술/공연",
    "예술공연",
    "전시",
    "공연",
    "문화행사",
    "관람",
    "수목원",
    "생태",
)

# Row-level content requires a direct activity signal. Institution words such
# as "도서관" or "박물관" belong to source metadata; treating any title or
# venue that mentions them as an experience moves ordinary lectures and even
# notices into the experience tab.
EXPERIENCE_CONTENT_KEYWORDS = (
    "체험",
    "교육체험",
    "교육·체험",
    "체험·견학",
    "체험/견학",
    "체험행사",
    "견학",
    "탐방",
    "전시",
    "공연",
    "문화행사",
    "관람",
)

EXPERIENCE_URL_TOKENS = (
    "/experience",
    "/exprn",
    "/exp/",
)

PUBLIC_COURSE_KEYWORDS = (
    "시청",
    "구청",
    "군청",
    "주민센터",
    "주민자치",
    "행정복지센터",
    "평생학습",
    "평생교육",
    "공공예약",
    "공공강좌",
    "통합예약",
    "도서관",
    "복지관",
    "노인복지관",
    "사회복지관",
    "체육문화회관",
    "체육",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _source_text(*values: Any) -> str:
    return " ".join(_clean(value) for value in values if _clean(value)).lower()


def is_local_government_education_facility(*values: Any) -> bool:
    """Return whether facility metadata identifies an administrative office."""

    source = _source_text(*values)
    if not source:
        return False
    if any(
        token.lower() in source
        for token in LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS
    ):
        return False
    if any(
        token.lower() in source
        for token in LOCAL_GOVERNMENT_EDUCATION_BRANCH_TOKENS
    ):
        return True
    return any(
        token.lower() in source
        and not any(fragment.lower() in source for fragment in false_fragments)
        for token, false_fragments in LOCAL_GOVERNMENT_EDUCATION_OFFICE_TOKEN_RULES
    )


def normalize_service_group(value: Any) -> str:
    compact = _clean(value).replace(" ", "")
    if compact in CULTURE_CENTER_ALIASES:
        return SERVICE_GROUP_CULTURE_CENTER
    if compact in EXPERIENCE_ALIASES:
        return SERVICE_GROUP_EXPERIENCE
    if compact in PUBLIC_COURSE_ALIASES:
        return SERVICE_GROUP_PUBLIC_COURSE
    if compact in OTHER_ALIASES:
        return SERVICE_GROUP_OTHER
    return ""


def infer_experience_institution_source_group(
    *,
    source_group: Any = "",
    name: Any = "",
    branch_name: Any = "",
    collection_category: Any = "",
    domain_category: Any = "",
) -> str:
    """Return the canonical source bucket for an institution-level target."""

    source_group_code = _clean(source_group).lower()
    if source_group_code == "library":
        return "library"
    if source_group_code in EXPERIENCE_INSTITUTION_SOURCE_GROUPS:
        return "museum_science"

    metadata_source = _source_text(
        name,
        branch_name,
        collection_category,
        domain_category,
    )
    if any(keyword in metadata_source for keyword in LIBRARY_INSTITUTION_KEYWORDS):
        return "library"
    if any(
        keyword in metadata_source
        for keyword in MUSEUM_SCIENCE_INSTITUTION_KEYWORDS
    ):
        return "museum_science"
    return ""


def infer_service_group(
    *,
    provider: Any = "",
    collection_category: Any = "",
    domain_category: Any = "",
    source_group: Any = "",
    operator_type: Any = "",
    branch_name: Any = "",
    venue_name: Any = "",
    raw_url: Any = "",
    title: Any = "",
    category_raw: Any = "",
    program_type: Any = "",
    service_group: Any = "",
) -> str:
    explicit = normalize_service_group(service_group)
    provider_code = _clean(provider).upper()
    source_group_code = _clean(source_group).lower()
    collection_group = normalize_service_group(collection_category)
    domain_group = normalize_service_group(domain_category)
    domain_category_clean = _clean(domain_category)
    metadata_source = _source_text(
        collection_category,
        domain_category,
        source_group,
        operator_type,
    )
    content_source = _source_text(
        title,
        category_raw,
    )
    raw_url_clean = _clean(raw_url).lower()
    program_type_clean = _clean(program_type)

    if (
        provider_code in CULTURE_CENTER_PROVIDERS
        or explicit == SERVICE_GROUP_CULTURE_CENTER
        or collection_group == SERVICE_GROUP_CULTURE_CENTER
        or domain_group == SERVICE_GROUP_CULTURE_CENTER
        or source_group_code == "retail_culture"
    ):
        return SERVICE_GROUP_CULTURE_CENTER

    if source_group_code in EXCLUDED_SOURCE_GROUPS or domain_category_clean in EXCLUDED_DOMAIN_CATEGORIES:
        return SERVICE_GROUP_OTHER

    if program_type_clean in EXPERIENCE_EXCLUDED_PROGRAM_TYPES:
        if explicit == SERVICE_GROUP_PUBLIC_COURSE:
            return SERVICE_GROUP_PUBLIC_COURSE
        if (
            source_group_code in PUBLIC_COURSE_SOURCE_GROUPS
            or source_group_code.startswith("municipal_")
            or provider_code.startswith("MUNI_")
        ):
            return SERVICE_GROUP_PUBLIC_COURSE
        return SERVICE_GROUP_OTHER

    if explicit == SERVICE_GROUP_EXPERIENCE:
        return SERVICE_GROUP_EXPERIENCE

    if (
        source_group_code in EXPERIENCE_SOURCE_GROUPS
        or program_type_clean in EXPERIENCE_PROGRAM_TYPES
        or any(keyword.lower() in metadata_source for keyword in EXPERIENCE_KEYWORDS)
        or any(keyword.lower() in content_source for keyword in EXPERIENCE_CONTENT_KEYWORDS)
        or any(token in raw_url_clean for token in EXPERIENCE_URL_TOKENS)
    ):
        return SERVICE_GROUP_EXPERIENCE

    if explicit == SERVICE_GROUP_PUBLIC_COURSE:
        return SERVICE_GROUP_PUBLIC_COURSE

    if (
        source_group_code in PUBLIC_COURSE_SOURCE_GROUPS
        or source_group_code.startswith("municipal_")
        or provider_code.startswith("MUNI_")
        or any(keyword.lower() in metadata_source for keyword in PUBLIC_COURSE_KEYWORDS)
    ):
        return SERVICE_GROUP_PUBLIC_COURSE

    if explicit:
        return explicit

    return SERVICE_GROUP_OTHER


def _sql_literal(value: str) -> str:
    """Return a PostgreSQL text literal for generated rule SQL."""
    return "'" + value.replace("'", "''") + "'"


def _sql_text_array(values: Iterable[str]) -> str:
    return "ARRAY[" + ", ".join(_sql_literal(value) for value in sorted(values)) + "]::text[]"


def render_service_group_sql() -> str:
    """Render the DB fallback classifier from the Python rule constants.

    Python ingestion/apply code is authoritative.  The generated SQL keeps direct
    database writers and legacy maintenance commands on the exact same keyword
    contract.  ``DB/service_group.sql`` is a checked-in generated artifact and is
    verified against this renderer by tests.
    """
    culture_providers = _sql_text_array(CULTURE_CENTER_PROVIDERS)
    experience_sources = _sql_text_array(EXPERIENCE_SOURCE_GROUPS)
    excluded_sources = _sql_text_array(EXCLUDED_SOURCE_GROUPS)
    excluded_domains = _sql_text_array(EXCLUDED_DOMAIN_CATEGORIES)
    experience_program_types = _sql_text_array(EXPERIENCE_PROGRAM_TYPES)
    experience_excluded_program_types = _sql_text_array(EXPERIENCE_EXCLUDED_PROGRAM_TYPES)
    experience_content_keywords = _sql_text_array(EXPERIENCE_CONTENT_KEYWORDS)
    experience_url_tokens = _sql_text_array(EXPERIENCE_URL_TOKENS)
    public_sources = _sql_text_array(PUBLIC_COURSE_SOURCE_GROUPS)
    experience_keywords = _sql_text_array(EXPERIENCE_KEYWORDS)
    public_keywords = _sql_text_array(PUBLIC_COURSE_KEYWORDS)
    valid_groups = _sql_text_array(SERVICE_GROUPS)
    culture_aliases = _sql_text_array(CULTURE_CENTER_ALIASES)
    experience_aliases = _sql_text_array(EXPERIENCE_ALIASES)
    public_aliases = _sql_text_array(PUBLIC_COURSE_ALIASES)
    other_aliases = _sql_text_array(OTHER_ALIASES)

    return f"""-- GENERATED from service_group.py; rule_version={SERVICE_GROUP_RULE_VERSION}
-- Do not edit keyword lists here. Update service_group.py and regenerate this file.

ALTER TABLE courses ADD COLUMN IF NOT EXISTS service_group VARCHAR(50);

CREATE INDEX IF NOT EXISTS idx_courses_service_group ON courses(service_group);
CREATE INDEX IF NOT EXISTS idx_courses_active_service_group
    ON courses(service_group) WHERE is_active IS TRUE;

CREATE OR REPLACE FUNCTION mooncen_text_contains_any(
    p_source TEXT,
    p_keywords TEXT[]
)
RETURNS BOOLEAN AS $$
    SELECT EXISTS (
        SELECT 1
        FROM unnest(p_keywords) AS keyword
        WHERE strpos(lower(coalesce(p_source, '')), lower(keyword)) > 0
    );
$$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

CREATE OR REPLACE FUNCTION mooncen_infer_course_service_group(
    p_provider TEXT,
    p_collection_category TEXT,
    p_domain_category TEXT,
    p_source_group TEXT,
    p_operator_type TEXT,
    p_branch_name TEXT,
    p_venue_name TEXT,
    p_raw_url TEXT,
    p_title TEXT,
    p_category_raw TEXT,
    p_program_type TEXT
)
RETURNS TEXT AS $$
DECLARE
    provider_norm TEXT := upper(btrim(coalesce(p_provider, '')));
    source_group_norm TEXT := lower(btrim(coalesce(p_source_group, '')));
    domain_category_norm TEXT := btrim(coalesce(p_domain_category, ''));
    collection_compact TEXT := replace(btrim(coalesce(p_collection_category, '')), ' ', '');
    domain_compact TEXT := replace(domain_category_norm, ' ', '');
    program_type_norm TEXT := btrim(coalesce(p_program_type, ''));
    metadata_source_norm TEXT := lower(concat_ws(
        ' ',
        p_collection_category,
        p_domain_category,
        p_source_group,
        p_operator_type
    ));
    content_source_norm TEXT := lower(concat_ws(
        ' ',
        p_title,
        p_category_raw
    ));
    raw_url_norm TEXT := lower(coalesce(p_raw_url, ''));
BEGIN
    IF provider_norm = ANY({culture_providers})
       OR collection_compact = ANY({culture_aliases})
       OR domain_compact = ANY({culture_aliases})
       OR source_group_norm = 'retail_culture' THEN
        RETURN '문화센터';
    END IF;

    IF source_group_norm = ANY({excluded_sources})
       OR domain_category_norm = ANY({excluded_domains}) THEN
        RETURN '기타';
    END IF;

    IF program_type_norm = ANY({experience_excluded_program_types}) THEN
        IF source_group_norm = ANY({public_sources})
           OR left(source_group_norm, 10) = 'municipal_'
           OR left(provider_norm, 5) = 'MUNI_' THEN
            RETURN '공공강좌';
        END IF;
        RETURN '기타';
    END IF;

    IF source_group_norm = ANY({experience_sources})
       OR program_type_norm = ANY({experience_program_types})
       OR mooncen_text_contains_any(metadata_source_norm, {experience_keywords})
       OR mooncen_text_contains_any(content_source_norm, {experience_content_keywords})
       OR mooncen_text_contains_any(raw_url_norm, {experience_url_tokens}) THEN
        RETURN '체험';
    END IF;

    IF source_group_norm = ANY({public_sources})
       OR left(source_group_norm, 10) = 'municipal_'
       OR left(provider_norm, 5) = 'MUNI_'
       OR mooncen_text_contains_any(metadata_source_norm, {public_keywords}) THEN
        RETURN '공공강좌';
    END IF;

    RETURN '기타';
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;

-- Backward-compatible metadata-only overload used by older maintenance SQL.
CREATE OR REPLACE FUNCTION mooncen_infer_course_service_group(
    p_provider TEXT,
    p_collection_category TEXT,
    p_domain_category TEXT,
    p_source_group TEXT,
    p_operator_type TEXT,
    p_branch_name TEXT,
    p_venue_name TEXT,
    p_raw_url TEXT
)
RETURNS TEXT AS $$
    SELECT mooncen_infer_course_service_group(
        p_provider,
        p_collection_category,
        p_domain_category,
        p_source_group,
        p_operator_type,
        p_branch_name,
        p_venue_name,
        p_raw_url,
        NULL,
        NULL,
        NULL
    );
$$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

CREATE OR REPLACE FUNCTION mooncen_resolve_course_service_group(
    p_provider TEXT,
    p_collection_category TEXT,
    p_domain_category TEXT,
    p_source_group TEXT,
    p_operator_type TEXT,
    p_branch_name TEXT,
    p_venue_name TEXT,
    p_raw_url TEXT,
    p_title TEXT,
    p_category_raw TEXT,
    p_program_type TEXT,
    p_service_group TEXT
)
RETURNS TEXT AS $$
DECLARE
    inferred TEXT := mooncen_infer_course_service_group(
        p_provider,
        p_collection_category,
        p_domain_category,
        p_source_group,
        p_operator_type,
        p_branch_name,
        p_venue_name,
        p_raw_url,
        p_title,
        p_category_raw,
        p_program_type
    );
    source_group_norm TEXT := lower(btrim(coalesce(p_source_group, '')));
    domain_category_norm TEXT := btrim(coalesce(p_domain_category, ''));
    explicit_compact TEXT := replace(btrim(coalesce(p_service_group, '')), ' ', '');
    explicit_group TEXT;
BEGIN
    explicit_group := CASE
        WHEN explicit_compact = ANY({culture_aliases}) THEN '{SERVICE_GROUP_CULTURE_CENTER}'
        WHEN explicit_compact = ANY({experience_aliases}) THEN '{SERVICE_GROUP_EXPERIENCE}'
        WHEN explicit_compact = ANY({public_aliases}) THEN '{SERVICE_GROUP_PUBLIC_COURSE}'
        WHEN explicit_compact = ANY({other_aliases}) THEN '{SERVICE_GROUP_OTHER}'
        ELSE ''
    END;

    -- Same precedence as infer_service_group(): culture metadata, intentional
    -- exclusion, explicit experience, row/source experience evidence, explicit
    -- public, inferred public, explicit OTHER, fallback.
    IF inferred = '문화센터' THEN
        RETURN inferred;
    END IF;
    IF explicit_group = '문화센터' THEN
        RETURN explicit_group;
    END IF;
    IF source_group_norm = ANY({excluded_sources})
       OR domain_category_norm = ANY({excluded_domains}) THEN
        RETURN '기타';
    END IF;
    IF btrim(coalesce(p_program_type, '')) = ANY({experience_excluded_program_types}) THEN
        RETURN inferred;
    END IF;
    IF explicit_group = '체험' THEN
        RETURN explicit_group;
    END IF;
    IF inferred = '체험' THEN
        RETURN inferred;
    END IF;
    IF explicit_group = '공공강좌' THEN
        RETURN explicit_group;
    END IF;
    IF inferred <> '기타' THEN
        RETURN inferred;
    END IF;
    IF explicit_group = ANY({valid_groups}) THEN
        RETURN explicit_group;
    END IF;
    RETURN inferred;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;

-- Backward-compatible explicit-group overload used by older callers.
CREATE OR REPLACE FUNCTION mooncen_resolve_course_service_group(
    p_provider TEXT,
    p_collection_category TEXT,
    p_domain_category TEXT,
    p_source_group TEXT,
    p_operator_type TEXT,
    p_branch_name TEXT,
    p_venue_name TEXT,
    p_raw_url TEXT,
    p_service_group TEXT
)
RETURNS TEXT AS $$
    SELECT mooncen_resolve_course_service_group(
        p_provider,
        p_collection_category,
        p_domain_category,
        p_source_group,
        p_operator_type,
        p_branch_name,
        p_venue_name,
        p_raw_url,
        NULL,
        NULL,
        NULL,
        p_service_group
    );
$$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

CREATE OR REPLACE FUNCTION mooncen_set_course_service_group()
RETURNS TRIGGER AS $$
DECLARE
    explicit_input TEXT;
BEGIN
    -- Crawler-level locked metadata is persisted in raw_fields because legacy
    -- schemas do not have a dedicated service_group_policy column. Honor the
    -- explicit canonical group before applying row-content inference.
    IF lower(btrim(coalesce(NEW.raw_fields ->> 'service_group_policy', ''))) = 'locked'
       AND NEW.service_group = ANY({valid_groups}) THEN
        RETURN NEW;
    END IF;

    -- On UPDATE, an unchanged stored group is derived state rather than fresh
    -- source evidence. Only a caller-supplied change gets explicit precedence.
    IF TG_OP = 'INSERT' OR NEW.service_group IS DISTINCT FROM OLD.service_group THEN
        explicit_input := NEW.service_group;
    END IF;
    NEW.service_group = mooncen_resolve_course_service_group(
        NEW.provider,
        NEW.collection_category,
        NEW.domain_category,
        NEW.source_group,
        NEW.operator_type,
        NULL,
        NEW.venue_name,
        NEW.raw_url,
        concat_ws(' ', NEW.title, NEW.title_raw),
        NEW.category_raw,
        NEW.program_type,
        explicit_input
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_courses_service_group ON courses;
CREATE TRIGGER set_courses_service_group
    BEFORE INSERT OR UPDATE OF provider, collection_category, domain_category,
        source_group, operator_type, venue_name, raw_url, title, title_raw,
        category_raw, program_type, service_group, raw_fields
    ON courses
    FOR EACH ROW
    EXECUTE FUNCTION mooncen_set_course_service_group();
"""
