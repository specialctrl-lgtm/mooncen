-- MoonCen crawler staging support.
--
-- Apply this after DB/schema.sql on the N100 crawler staging database.
-- Never apply this file on primary: it installs crawler-only columns/triggers.
-- Primary upload/apply metadata lives in DB/staging_primary_schema.sql.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

-- The crawler's canonical raw-URL upsert guard is introduced on primary by
-- the versioned URL cleanup migration.  The isolated staging cluster is
-- rebuilt from schema.sql + this file rather than the primary migration
-- ledger, so it must expose the same immutable function before crawler writes.
CREATE OR REPLACE FUNCTION mooncen_raw_url_fingerprint(p_url TEXT)
RETURNS TEXT AS $$
    SELECT CASE
        WHEN NULLIF(btrim(p_url), '') IS NULL THEN NULL
        ELSE encode(public.digest(btrim(p_url), 'sha256'::text), 'hex')
    END;
$$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

CREATE TABLE IF NOT EXISTS crawl_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    crawl_batch_id TEXT NOT NULL UNIQUE,
    source_host TEXT,
    mode TEXT NOT NULL DEFAULT 'staging',
    providers TEXT[],
    status TEXT NOT NULL DEFAULT 'RUNNING',
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITH TIME ZONE,
    total_branches INTEGER NOT NULL DEFAULT 0,
    total_courses INTEGER NOT NULL DEFAULT 0,
    valid_courses INTEGER NOT NULL DEFAULT 0,
    invalid_courses INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_crawl_batch_status
        CHECK (status IN ('RUNNING', 'COLLECTED', 'VALIDATED', 'APPLIED', 'FAILED', 'ROLLED_BACK'))
);

CREATE INDEX IF NOT EXISTS idx_crawl_batches_status ON crawl_batches(status);
CREATE INDEX IF NOT EXISTS idx_crawl_batches_started_at ON crawl_batches(started_at DESC);

CREATE TABLE IF NOT EXISTS crawl_batch_validation_errors (
    id BIGSERIAL PRIMARY KEY,
    crawl_batch_id TEXT NOT NULL,
    provider TEXT,
    provider_course_id TEXT,
    error_code TEXT NOT NULL,
    error_message TEXT NOT NULL,
    row_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_crawl_batch_validation_errors_batch
    ON crawl_batch_validation_errors(crawl_batch_id);

CREATE TABLE IF NOT EXISTS crawl_batch_apply_logs (
    id BIGSERIAL PRIMARY KEY,
    crawl_batch_id TEXT NOT NULL,
    source_host TEXT,
    target_host TEXT,
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    closed_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_crawl_batch_apply_status
        CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'DRY_RUN'))
);

CREATE INDEX IF NOT EXISTS idx_crawl_batch_apply_logs_batch
    ON crawl_batch_apply_logs(crawl_batch_id);
CREATE INDEX IF NOT EXISTS idx_crawl_batch_apply_logs_started_at
    ON crawl_batch_apply_logs(started_at DESC);

CREATE SCHEMA IF NOT EXISTS crawl_staging;

CREATE TABLE IF NOT EXISTS crawl_staging.branch_snapshots (
    id BIGSERIAL PRIMARY KEY,
    crawl_batch_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    branch_code TEXT NOT NULL,
    row_data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(crawl_batch_id, provider, branch_code)
);

CREATE TABLE IF NOT EXISTS crawl_staging.course_snapshots (
    id BIGSERIAL PRIMARY KEY,
    crawl_batch_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_course_id TEXT NOT NULL,
    row_data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(crawl_batch_id, provider, provider_course_id)
);

ALTER TABLE branches ADD COLUMN IF NOT EXISTS crawl_batch_id TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS crawl_batch_id TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS target_age_is_explicit BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS service_group VARCHAR(50);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS standard_category_key VARCHAR(80);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS standard_category_label VARCHAR(80);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS source_endpoint TEXT;

CREATE INDEX IF NOT EXISTS idx_branches_crawl_batch_id ON branches(crawl_batch_id);
CREATE INDEX IF NOT EXISTS idx_courses_crawl_batch_id ON courses(crawl_batch_id);
CREATE INDEX IF NOT EXISTS idx_courses_provider_batch ON courses(provider, crawl_batch_id);
CREATE INDEX IF NOT EXISTS idx_courses_service_group ON courses(service_group);
CREATE INDEX IF NOT EXISTS idx_courses_standard_category_key ON courses(standard_category_key);
CREATE INDEX IF NOT EXISTS idx_courses_standard_category_label ON courses(standard_category_label);
CREATE INDEX IF NOT EXISTS idx_courses_provider_source_endpoint
    ON courses(provider, source_endpoint)
    WHERE source_endpoint IS NOT NULL;

/* Classifier intentionally not installed by staging_schema.sql.
   Staging rows are normalized by the Python service_group.py contract during
   apply. The primary DB classifier is installed separately by setup_db.py.
   Legacy embedded implementation retained as a disabled migration reference.
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
DECLARE
    provider_norm TEXT := upper(btrim(coalesce(p_provider, '')));
    source_group_norm TEXT := lower(btrim(coalesce(p_source_group, '')));
    source_norm TEXT := lower(concat_ws(
        ' ',
        p_provider,
        p_collection_category,
        p_domain_category,
        p_source_group,
        p_operator_type,
        p_branch_name,
        p_venue_name,
        p_raw_url
    ));
BEGIN
    IF provider_norm IN (
        'HOMEPLUS',
        'LOTTE',
        'EMART',
        'HYUNDAI_DEPT',
        'GALLERIA',
        'AK_PLAZA',
        'ELAND_RETAIL',
        'SHINSEGAE_ACADEMY',
        'LOTTE_MART'
    ) OR source_norm LIKE '%문화센터%' OR source_norm LIKE '%문화 센터%' THEN
        RETURN '문화센터';
    END IF;

    IF source_group_norm IN (
        'museum_science',
        'science_museum',
        'museum',
        'library',
        'arts_culture',
        'national_institution'
    ) OR source_norm ~ '(도서관|박물관|과학관|미술관|문화재단)' THEN
        RETURN '체험';
    END IF;

    IF source_group_norm IN (
        'public_reservation',
        'municipal_reservation',
        'lifelong_learning',
        'municipal_lifelong_learning',
        'education_office_reservation',
        'sports_reservation'
    )
       OR source_group_norm LIKE 'municipal\_%' ESCAPE '\'
       OR provider_norm LIKE 'MUNI\_%' ESCAPE '\'
       OR source_norm ~ '(시청|구청|군청|주민센터|주민자치|행정복지센터|평생학습|평생교육|공공예약|공공강좌|통합예약)' THEN
        RETURN '공공강좌';
    END IF;

    RETURN '기타';
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION mooncen_set_course_service_group()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT'
       AND NEW.service_group IN ('문화센터', '체험', '공공강좌', '기타') THEN
        RETURN NEW;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.service_group IS DISTINCT FROM OLD.service_group
           AND NEW.service_group IN ('문화센터', '체험', '공공강좌', '기타') THEN
            RETURN NEW;
        END IF;
    END IF;

    NEW.service_group = mooncen_infer_course_service_group(
        NEW.provider,
        NEW.collection_category,
        NEW.domain_category,
        NEW.source_group,
        NEW.operator_type,
        NULL,
        NEW.venue_name,
        NEW.raw_url
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_courses_service_group ON courses;
CREATE TRIGGER set_courses_service_group
    BEFORE INSERT OR UPDATE ON courses
    FOR EACH ROW
    EXECUTE FUNCTION mooncen_set_course_service_group();

UPDATE courses
SET service_group = mooncen_infer_course_service_group(
    provider,
    collection_category,
    domain_category,
    source_group,
    operator_type,
    NULL,
    venue_name,
    raw_url
)
WHERE service_group IS NULL
   OR btrim(service_group) = ''
   OR service_group NOT IN ('문화센터', '체험', '공공강좌', '기타');
*/

CREATE OR REPLACE FUNCTION current_crawl_batch_id()
RETURNS TEXT AS $$
DECLARE
    value TEXT;
BEGIN
    value := current_setting('mooncen.crawl_batch_id', true);
    IF value IS NULL OR btrim(value) = '' THEN
        value := current_setting('application_name', true);
    END IF;
    IF value IS NULL OR btrim(value) = '' THEN
        value := 'manual-' || to_char(now(), 'YYYYMMDDHH24MISS');
    END IF;
    RETURN value;
END;
$$ LANGUAGE plpgsql STABLE;

ALTER TABLE branches ALTER COLUMN crawl_batch_id SET DEFAULT current_crawl_batch_id();
ALTER TABLE courses ALTER COLUMN crawl_batch_id SET DEFAULT current_crawl_batch_id();

CREATE OR REPLACE FUNCTION set_current_crawl_batch_id()
RETURNS TRIGGER AS $$
DECLARE
    value TEXT;
BEGIN
    value := current_setting('mooncen.crawl_batch_id', true);
    IF value IS NOT NULL AND btrim(value) <> '' THEN
        NEW.crawl_batch_id = value;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_branches_crawl_batch_id ON branches;
CREATE TRIGGER set_branches_crawl_batch_id
    BEFORE INSERT OR UPDATE ON branches
    FOR EACH ROW
    EXECUTE FUNCTION set_current_crawl_batch_id();

DROP TRIGGER IF EXISTS set_courses_crawl_batch_id ON courses;
CREATE TRIGGER set_courses_crawl_batch_id
    BEFORE INSERT OR UPDATE ON courses
    FOR EACH ROW
    EXECUTE FUNCTION set_current_crawl_batch_id();

CREATE OR REPLACE FUNCTION update_crawl_batch_modtime()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_crawl_batches_modtime ON crawl_batches;
CREATE TRIGGER update_crawl_batches_modtime
    BEFORE UPDATE ON crawl_batches
    FOR EACH ROW
    EXECUTE FUNCTION update_crawl_batch_modtime();
