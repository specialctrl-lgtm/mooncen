-- GENERATED from service_group.py; rule_version=2026-07-29.1
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
    IF provider_norm = ANY(ARRAY['AK_PLAZA', 'ELAND_RETAIL', 'EMART', 'GALLERIA', 'HOMEPLUS', 'HYUNDAI_DEPT', 'LOTTE', 'LOTTE_MART', 'SHINSEGAE_ACADEMY']::text[])
       OR collection_compact = ANY(ARRAY['문화센터']::text[])
       OR domain_compact = ANY(ARRAY['문화센터']::text[])
       OR source_group_norm = 'retail_culture' THEN
        RETURN '문화센터';
    END IF;

    IF source_group_norm = ANY(ARRAY['deprecated']::text[])
       OR domain_category_norm = ANY(ARRAY['제외']::text[]) THEN
        RETURN '기타';
    END IF;

    IF program_type_norm = ANY(ARRAY['대관', '숙박']::text[]) THEN
        IF source_group_norm = ANY(ARRAY['education_office_reservation', 'library', 'lifelong_learning', 'municipal_lifelong_learning', 'municipal_reservation', 'public_reservation', 'sports_reservation']::text[])
           OR left(source_group_norm, 10) = 'municipal_'
           OR left(provider_norm, 5) = 'MUNI_' THEN
            RETURN '공공강좌';
        END IF;
        RETURN '기타';
    END IF;

    IF source_group_norm = ANY(ARRAY['arts_culture', 'museum', 'museum_science', 'national_institution', 'science_museum']::text[])
       OR program_type_norm = ANY(ARRAY['견학', '공연', '관람', '전시', '체험', '캠프', '탐방']::text[])
       OR mooncen_text_contains_any(metadata_source_norm, ARRAY['견학', '공연', '과학관', '관람', '교육·체험', '교육체험', '문화재단', '문화행사', '미술관', '박물관', '생태', '수목원', '예술/공연', '예술공연', '전시', '체험', '체험/견학', '체험·견학', '체험행사', '탐방']::text[])
       OR mooncen_text_contains_any(content_source_norm, ARRAY['견학', '공연', '관람', '교육·체험', '교육체험', '문화행사', '전시', '체험', '체험/견학', '체험·견학', '체험행사', '탐방']::text[])
       OR mooncen_text_contains_any(raw_url_norm, ARRAY['/exp/', '/experience', '/exprn']::text[]) THEN
        RETURN '체험';
    END IF;

    IF source_group_norm = ANY(ARRAY['education_office_reservation', 'library', 'lifelong_learning', 'municipal_lifelong_learning', 'municipal_reservation', 'public_reservation', 'sports_reservation']::text[])
       OR left(source_group_norm, 10) = 'municipal_'
       OR left(provider_norm, 5) = 'MUNI_'
       OR mooncen_text_contains_any(metadata_source_norm, ARRAY['공공강좌', '공공예약', '구청', '군청', '노인복지관', '도서관', '복지관', '사회복지관', '시청', '주민센터', '주민자치', '체육', '체육문화회관', '통합예약', '평생교육', '평생학습', '행정복지센터']::text[]) THEN
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
        WHEN explicit_compact = ANY(ARRAY['문화센터']::text[]) THEN '문화센터'
        WHEN explicit_compact = ANY(ARRAY['경험', '교육체험', '체험', '체험견학', '체험행사']::text[]) THEN '체험'
        WHEN explicit_compact = ANY(ARRAY['공공강의', '공공강좌', '공공교육', '교육']::text[]) THEN '공공강좌'
        WHEN explicit_compact = ANY(ARRAY['기타']::text[]) THEN '기타'
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
    IF source_group_norm = ANY(ARRAY['deprecated']::text[])
       OR domain_category_norm = ANY(ARRAY['제외']::text[]) THEN
        RETURN '기타';
    END IF;
    IF btrim(coalesce(p_program_type, '')) = ANY(ARRAY['대관', '숙박']::text[]) THEN
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
    IF explicit_group = ANY(ARRAY['공공강좌', '기타', '문화센터', '체험']::text[]) THEN
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
       AND NEW.service_group = ANY(ARRAY['공공강좌', '기타', '문화센터', '체험']::text[]) THEN
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
