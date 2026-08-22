-- Bring an existing MoonCen database up to the current application schema.
-- Safe to run repeatedly.

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

DROP VIEW IF EXISTS adult_courses;
DROP VIEW IF EXISTS kids_courses;
DROP VIEW IF EXISTS weekend_courses;
DROP VIEW IF EXISTS evening_courses;
DROP INDEX IF EXISTS idx_courses_tags;
DROP TRIGGER IF EXISTS sync_branches_location ON branches;
DROP TRIGGER IF EXISTS update_courses_modtime ON courses;
-- The generated classification trigger depends on provider/source columns.
-- Drop it before idempotent type normalization and recreate it below.
DROP TRIGGER IF EXISTS set_courses_service_group ON courses;

ALTER TABLE branches ADD COLUMN IF NOT EXISTS lat NUMERIC(10, 7);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS lon NUMERIC(10, 7);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS location GEOGRAPHY(Point, 4326);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS operating_hours TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS website_url TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS facility_type VARCHAR(80);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS facility_category VARCHAR(80);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS facility_source TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS facility_source_sheet TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS facility_service_group VARCHAR(50);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS facility_collection_category VARCHAR(50);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS region_sido VARCHAR(50);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS region_sigungu VARCHAR(80);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS regular_holiday TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS admission_fee TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS basic_info JSONB DEFAULT '{}'::jsonb;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS address_source TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS coordinate_source TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS location_confidence INTEGER DEFAULT 0;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS location_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS location_checked_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS location_query TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE courses ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS image_url TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS view_count INTEGER DEFAULT 0;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS apply_start DATE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS apply_end DATE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS apply_period_raw TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS capacity_total INTEGER;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS capacity_current INTEGER;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS capacity_remaining INTEGER;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS waitlist_total INTEGER;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS venue_name VARCHAR(150);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS venue_address TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS application_url TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS application_type VARCHAR(30);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS application_method_raw TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS reservation_available BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS discovery_status VARCHAR(50);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS program_type VARCHAR(50);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS eligibility_raw TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS raw_fields JSONB;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS ai_category VARCHAR(100);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS ai_summary TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS is_ai_processed BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS ai_title_processed BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS ai_title_confidence NUMERIC(4, 3);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS ai_title_result JSONB;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS collection_category VARCHAR(50);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS domain_category VARCHAR(50);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS standard_category_key VARCHAR(80);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS standard_category_label VARCHAR(80);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS source_group VARCHAR(50);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS operator_type VARCHAR(50);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS service_group VARCHAR(50);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS collection_type VARCHAR(50);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS target_age_group VARCHAR(20);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS target_min_age INTEGER;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS target_max_age INTEGER;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS target_with_parent BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS target_tags TEXT[];
ALTER TABLE courses ADD COLUMN IF NOT EXISTS target_age_is_explicit BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS schedule_days TEXT[];
ALTER TABLE courses ADD COLUMN IF NOT EXISTS schedule_dates JSONB;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS schedule_time_start TIME;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS schedule_time_end TIME;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS schedule_frequency VARCHAR(20);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS schedule_duration_minutes INTEGER;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

-- Some legacy lifecycle backfills used updated_at as last_seen_at even when it
-- predated the recorded first observation. Preserve both observations by
-- swapping the endpoints before the versioned integrity migration validates
-- first_seen_at <= last_seen_at.
UPDATE courses
SET first_seen_at = last_seen_at,
    last_seen_at = first_seen_at
WHERE first_seen_at IS NOT NULL
  AND last_seen_at IS NOT NULL
  AND first_seen_at > last_seen_at;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'courses'
          AND column_name = 'ai_tags'
    ) THEN
        ALTER TABLE courses ADD COLUMN ai_tags TEXT;
    ELSIF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'courses'
          AND column_name = 'ai_tags'
          AND data_type <> 'text'
    ) THEN
        ALTER TABLE courses ALTER COLUMN ai_tags TYPE TEXT USING ai_tags::text;
    END IF;
END $$;

ALTER TABLE courses
    ALTER COLUMN fee TYPE NUMERIC USING fee::numeric;

ALTER TABLE courses
    ALTER COLUMN provider TYPE VARCHAR(50),
    ALTER COLUMN provider_course_id TYPE VARCHAR(100);

ALTER TABLE branches
    ALTER COLUMN provider TYPE VARCHAR(50),
    ALTER COLUMN branch_code TYPE VARCHAR(50),
    ALTER COLUMN phone TYPE VARCHAR(50);

CREATE OR REPLACE FUNCTION sync_branch_location()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        NEW.location = ST_SetSRID(ST_MakePoint(NEW.lon::float, NEW.lat::float), 4326)::geography;
    ELSE
        NEW.location = NULL;
    END IF;
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sync_branches_location ON branches;
CREATE TRIGGER sync_branches_location
    BEFORE INSERT OR UPDATE OF lat, lon, name, address, phone, operating_hours, website_url, facility_type, facility_category, facility_source, facility_source_sheet, facility_service_group, facility_collection_category, region_sido, region_sigungu, regular_holiday, admission_fee, basic_info, address_source, coordinate_source, location_confidence, location_verified, location_checked_at, location_query
    ON branches
    FOR EACH ROW
    EXECUTE FUNCTION sync_branch_location();

UPDATE branches
SET location = ST_SetSRID(ST_MakePoint(lon::float, lat::float), 4326)::geography
WHERE lat IS NOT NULL AND lon IS NOT NULL;

CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    -- Detail-page views update only view_count and must not make stale
    -- course content appear freshly collected or edited.
    IF NEW.view_count IS DISTINCT FROM OLD.view_count
       AND (to_jsonb(NEW) - 'view_count' - 'updated_at')
           IS NOT DISTINCT FROM
           (to_jsonb(OLD) - 'view_count' - 'updated_at') THEN
        NEW.updated_at = OLD.updated_at;
    ELSE
        NEW.updated_at = now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_courses_modtime ON courses;
CREATE TRIGGER update_courses_modtime
    BEFORE UPDATE ON courses
    FOR EACH ROW
    EXECUTE FUNCTION update_modified_column();

ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_course_status;
ALTER TABLE courses ADD CONSTRAINT chk_course_status
    CHECK (status IN ('OPEN', 'SCHEDULED', 'CLOSED', 'WAITING', 'DEADLINE') OR status IS NULL);

ALTER TABLE courses DROP CONSTRAINT IF EXISTS check_age_group;
ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_age_group;
ALTER TABLE courses ADD CONSTRAINT chk_age_group
    CHECK (target_age_group IN ('INFANT', 'TODDLER', 'CHILD', 'TEEN', 'ADULT', 'SENIOR', 'ALL') OR target_age_group IS NULL);

ALTER TABLE courses DROP CONSTRAINT IF EXISTS check_age_range;
ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_age_range;
ALTER TABLE courses ADD CONSTRAINT chk_age_range
    CHECK (
        (target_min_age IS NULL OR target_min_age >= 0)
        AND (target_max_age IS NULL OR target_max_age <= 1440)
        AND (target_min_age IS NULL OR target_max_age IS NULL OR target_min_age <= target_max_age)
    );

ALTER TABLE courses DROP CONSTRAINT IF EXISTS check_schedule_frequency;
ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_schedule_frequency;
ALTER TABLE courses ADD CONSTRAINT chk_schedule_frequency
    CHECK (schedule_frequency IN ('WEEKLY', 'BIWEEKLY', 'MONTHLY', 'IRREGULAR') OR schedule_frequency IS NULL);

ALTER TABLE courses DROP CONSTRAINT IF EXISTS check_duration;
ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_duration;
ALTER TABLE courses ADD CONSTRAINT chk_duration
    CHECK (schedule_duration_minutes IS NULL OR schedule_duration_minutes > 0);

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
    fcm_token TEXT NOT NULL,
    notification_type VARCHAR(20),
    send_at TIMESTAMP WITH TIME ZONE NOT NULL,
    is_sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE notifications DROP CONSTRAINT IF EXISTS chk_notification_type;
ALTER TABLE notifications ADD CONSTRAINT chk_notification_type
    CHECK (notification_type IN ('OPEN', 'START', 'DEADLINE') OR notification_type IS NULL);

CREATE TABLE IF NOT EXISTS user_favorites (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL,
    course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_user_favorite UNIQUE(user_id, course_id)
);

CREATE TABLE IF NOT EXISTS user_course_marks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    mark_type VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_user_course_mark UNIQUE(user_id, course_id, mark_type)
);

ALTER TABLE user_course_marks DROP CONSTRAINT IF EXISTS chk_user_course_mark_type;
ALTER TABLE user_course_marks ADD CONSTRAINT chk_user_course_mark_type
    CHECK (mark_type IN ('favorite', 'applied'));

CREATE TABLE IF NOT EXISTS search_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    search_query TEXT NOT NULL,
    filters JSONB,
    result_count INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_branches_provider ON branches(provider);
CREATE INDEX IF NOT EXISTS idx_branches_facility_service_group ON branches(facility_service_group);
CREATE INDEX IF NOT EXISTS idx_branches_facility_collection_category ON branches(facility_collection_category);
CREATE INDEX IF NOT EXISTS idx_branches_name_trgm ON branches USING gin(name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_branches_location ON branches USING gist(location);
CREATE INDEX IF NOT EXISTS idx_branches_lat_lon ON branches(lat, lon);
-- unique_provider_course already provides the same btree key order.
CREATE INDEX IF NOT EXISTS idx_courses_branch_id ON courses(branch_id);
CREATE INDEX IF NOT EXISTS idx_courses_status ON courses(status);
CREATE INDEX IF NOT EXISTS idx_courses_start_date ON courses(start_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_courses_schedule_dates ON courses USING gin(schedule_dates);
CREATE INDEX IF NOT EXISTS idx_courses_title_trgm ON courses USING gin(title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_courses_collection_category ON courses(collection_category);
CREATE INDEX IF NOT EXISTS idx_courses_domain_category ON courses(domain_category);
CREATE INDEX IF NOT EXISTS idx_courses_standard_category_key ON courses(standard_category_key);
CREATE INDEX IF NOT EXISTS idx_courses_standard_category_label ON courses(standard_category_label);
CREATE INDEX IF NOT EXISTS idx_courses_source_group ON courses(source_group);
CREATE INDEX IF NOT EXISTS idx_courses_active_provider ON courses(provider) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_active_collection_category ON courses(collection_category) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_active_domain_category ON courses(domain_category) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_active_standard_category_label ON courses(standard_category_label) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_active_source_group ON courses(source_group) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_program_type ON courses(program_type);
CREATE INDEX IF NOT EXISTS idx_courses_application_type ON courses(application_type);
CREATE INDEX IF NOT EXISTS idx_courses_application_url ON courses(application_url) WHERE application_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_courses_ai_category ON courses(ai_category);
CREATE INDEX IF NOT EXISTS idx_courses_category_raw_trgm ON courses USING gin(category_raw gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_courses_venue_name_trgm ON courses USING gin(venue_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_courses_is_ai_processed
    ON courses(is_ai_processed)
    WHERE is_ai_processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_courses_ai_title_processed
    ON courses(ai_title_processed)
    WHERE ai_title_processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_courses_age_group ON courses(target_age_group);
CREATE INDEX IF NOT EXISTS idx_courses_age_range ON courses(target_min_age, target_max_age);
CREATE INDEX IF NOT EXISTS idx_courses_schedule_days ON courses USING gin(schedule_days);
CREATE INDEX IF NOT EXISTS idx_courses_schedule_time ON courses(schedule_time_start);
CREATE INDEX IF NOT EXISTS idx_courses_target_tags ON courses USING gin(target_tags);
CREATE INDEX IF NOT EXISTS idx_notifications_send_at
    ON notifications(send_at)
    WHERE is_sent = FALSE;
CREATE INDEX IF NOT EXISTS idx_user_favorites_user ON user_favorites(user_id);
CREATE INDEX IF NOT EXISTS idx_user_course_marks_user_type
    ON user_course_marks(user_id, mark_type);
CREATE INDEX IF NOT EXISTS idx_user_course_marks_course
    ON user_course_marks(course_id);

CREATE TABLE IF NOT EXISTS user_course_notification_settings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    start_alarm_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    start_alarm_minutes_before INTEGER NOT NULL DEFAULT 1440,
    registration_alarm_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    registration_alarm_minutes_before INTEGER NOT NULL DEFAULT 1440,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_user_course_notification_setting UNIQUE(user_id, course_id)
);

ALTER TABLE user_course_notification_settings DROP CONSTRAINT IF EXISTS chk_user_course_start_alarm_minutes;
ALTER TABLE user_course_notification_settings ADD CONSTRAINT chk_user_course_start_alarm_minutes
    CHECK (start_alarm_minutes_before BETWEEN 0 AND 43200);
ALTER TABLE user_course_notification_settings DROP CONSTRAINT IF EXISTS chk_user_course_registration_alarm_minutes;
ALTER TABLE user_course_notification_settings ADD CONSTRAINT chk_user_course_registration_alarm_minutes
    CHECK (registration_alarm_minutes_before BETWEEN 0 AND 43200);

CREATE INDEX IF NOT EXISTS idx_user_course_notification_settings_user
    ON user_course_notification_settings(user_id);
CREATE INDEX IF NOT EXISTS idx_user_course_notification_settings_course
    ON user_course_notification_settings(course_id);

CREATE TABLE IF NOT EXISTS user_favorite_courses (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE SET NULL,
    course_url TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_user_favorite_course_url UNIQUE(user_id, course_url)
);

CREATE INDEX IF NOT EXISTS idx_user_favorite_courses_user
    ON user_favorite_courses(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_favorite_courses_course
    ON user_favorite_courses(course_id)
    WHERE course_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS course_alerts (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE SET NULL,
    course_url TEXT,
    alert_type TEXT NOT NULL,
    alert_status TEXT NOT NULL DEFAULT 'pending',
    scheduled_at TIMESTAMP WITH TIME ZONE,
    sent_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE course_alerts DROP CONSTRAINT IF EXISTS chk_course_alert_type;
ALTER TABLE course_alerts ADD CONSTRAINT chk_course_alert_type
    CHECK (alert_type IN ('registration_open', 'registration_closing', 'seat_available', 'new_course'));
ALTER TABLE course_alerts DROP CONSTRAINT IF EXISTS chk_course_alert_status;
ALTER TABLE course_alerts ADD CONSTRAINT chk_course_alert_status
    CHECK (alert_status IN ('pending', 'sent', 'skipped', 'failed'));

CREATE INDEX IF NOT EXISTS idx_course_alerts_user_status
    ON course_alerts(user_id, alert_status, scheduled_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_course_alerts_scheduled_pending
    ON course_alerts(scheduled_at)
    WHERE alert_status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS ux_course_alerts_user_course_type
    ON course_alerts(user_id, course_url, alert_type)
    WHERE course_url IS NOT NULL AND btrim(course_url) <> '';

CREATE TABLE IF NOT EXISTS course_update_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    reason VARCHAR(40) NOT NULL DEFAULT 'click',
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    source_url TEXT,
    request_count INTEGER NOT NULL DEFAULT 1,
    requested_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '1 hour'),
    last_checked_at TIMESTAMP WITH TIME ZONE,
    check_result JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE course_update_requests DROP CONSTRAINT IF EXISTS chk_course_update_request_status;
ALTER TABLE course_update_requests ADD CONSTRAINT chk_course_update_request_status
    CHECK (status IN ('pending', 'processing', 'checked', 'failed', 'expired'));

CREATE UNIQUE INDEX IF NOT EXISTS ux_course_update_requests_pending_course
    ON course_update_requests(course_id)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_course_update_requests_active
    ON course_update_requests(status, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_course_update_requests_requested_at
    ON course_update_requests(requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_logs_created ON search_logs(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_courses_service_group ON courses(service_group);
CREATE INDEX IF NOT EXISTS idx_courses_active_service_group ON courses(service_group) WHERE is_active IS TRUE;

/* Legacy embedded classifier disabled.
   DB/setup_db.py applies DB/service_group.sql after this base migration.
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
    ) OR source_norm ~ '(체험|교육체험|교육·체험|체험·견학|체험/견학|체험행사|견학|탐방|도서관|박물관|과학관|미술관|문화재단|예술/공연|예술공연|전시|공연|문화행사|관람)' THEN
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
   OR service_group NOT IN ('문화센터', '체험', '공공강좌', '기타')
   OR service_group IS DISTINCT FROM mooncen_infer_course_service_group(
        provider,
        collection_category,
        domain_category,
        source_group,
        operator_type,
        NULL,
        venue_name,
        raw_url
    );
*/

-- Repair legacy branch rows that were saved with site/store codes instead of the
-- branch names shown by the source sites.
UPDATE branches
SET name = CASE
        WHEN provider = 'AK_PLAZA' AND branch_code = '03' THEN '분당점'
        WHEN provider = 'GALLERIA' AND branch_code = 'gwanggyo' THEN '광교점'
        WHEN provider = 'LOTTE_MART' AND branch_code = '322' THEN '송파점'
        ELSE name
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE (provider = 'AK_PLAZA' AND branch_code = '03' AND name = '03')
   OR (provider = 'GALLERIA' AND branch_code = 'gwanggyo' AND lower(name) = 'gwanggyo')
   OR (provider = 'LOTTE_MART' AND branch_code = '322' AND name = '322');

CREATE OR REPLACE VIEW adult_courses AS
SELECT *
FROM courses
WHERE target_age_group IN ('ADULT', 'SENIOR', 'ALL')
  AND status = 'OPEN';

CREATE OR REPLACE VIEW kids_courses AS
SELECT *
FROM courses
WHERE target_age_group IN ('INFANT', 'TODDLER', 'CHILD')
  AND status = 'OPEN';

CREATE OR REPLACE VIEW weekend_courses AS
SELECT *
FROM courses
WHERE ('토' = ANY(schedule_days) OR '일' = ANY(schedule_days))
  AND status = 'OPEN';

CREATE OR REPLACE VIEW evening_courses AS
SELECT *
FROM courses
WHERE schedule_time_start >= '19:00'
  AND status = 'OPEN';
