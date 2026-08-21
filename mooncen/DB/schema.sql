-- MoonCen canonical PostgreSQL schema
-- Applies the schema expected by the current FastAPI backend, crawlers, and AI worker.

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

-- =====================================================
-- Branches
-- =====================================================
CREATE TABLE IF NOT EXISTS branches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider VARCHAR(50) NOT NULL,
    branch_code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    address TEXT,
    phone VARCHAR(50),
    lat NUMERIC(10, 7),
    lon NUMERIC(10, 7),
    location GEOGRAPHY(Point, 4326),
    operating_hours TEXT,
    website_url TEXT,
    facility_type VARCHAR(80),
    facility_category VARCHAR(80),
    facility_source TEXT,
    facility_source_sheet TEXT,
    facility_service_group VARCHAR(50),
    facility_collection_category VARCHAR(50),
    region_sido VARCHAR(50),
    region_sigungu VARCHAR(80),
    regular_holiday TEXT,
    admission_fee TEXT,
    basic_info JSONB DEFAULT '{}'::jsonb,
    address_source TEXT,
    coordinate_source TEXT,
    location_confidence INTEGER DEFAULT 0,
    location_verified BOOLEAN DEFAULT FALSE,
    location_checked_at TIMESTAMP WITH TIME ZONE,
    location_query TEXT,
    geocode_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    geocode_reason_code VARCHAR(100),
    geocode_attempt_count INTEGER NOT NULL DEFAULT 0,
    geocode_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
    geocode_next_retry_at TIMESTAMP WITH TIME ZONE,
    geocode_last_error TEXT,
    geocode_last_attempt_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_provider_branch UNIQUE(provider, branch_code),
    CONSTRAINT chk_branch_geocode_status
        CHECK (geocode_status IN (
            'pending', 'resolved', 'no_result', 'low_confidence',
            'invalid_address', 'region_mismatch', 'quota_exhausted',
            'request_error', 'manual_review'
        )),
    CONSTRAINT chk_branch_geocode_attempt_count CHECK (geocode_attempt_count >= 0),
    CONSTRAINT chk_branch_geocode_candidates_array CHECK (jsonb_typeof(geocode_candidates) = 'array'),
    CONSTRAINT chk_branch_website_url_shape
        CHECK (
            website_url IS NULL
            OR btrim(website_url) = ''
            OR (
                length(website_url) <= 4096
                AND website_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
            )
        )
);

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
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_status VARCHAR(32) NOT NULL DEFAULT 'pending';
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_reason_code VARCHAR(100);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_candidates JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_next_retry_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_last_error TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_last_attempt_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'branches'::regclass
          AND conname = 'chk_branch_geocode_status'
    ) THEN
        ALTER TABLE branches ADD CONSTRAINT chk_branch_geocode_status
            CHECK (geocode_status IN (
                'pending', 'resolved', 'no_result', 'low_confidence',
                'invalid_address', 'region_mismatch', 'quota_exhausted',
                'request_error', 'manual_review'
            ));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'branches'::regclass
          AND conname = 'chk_branch_geocode_attempt_count'
    ) THEN
        ALTER TABLE branches ADD CONSTRAINT chk_branch_geocode_attempt_count
            CHECK (geocode_attempt_count >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'branches'::regclass
          AND conname = 'chk_branch_geocode_candidates_array'
    ) THEN
        ALTER TABLE branches ADD CONSTRAINT chk_branch_geocode_candidates_array
            CHECK (jsonb_typeof(geocode_candidates) = 'array');
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_branches_provider ON branches(provider);
CREATE INDEX IF NOT EXISTS idx_branches_facility_service_group ON branches(facility_service_group);
CREATE INDEX IF NOT EXISTS idx_branches_facility_collection_category ON branches(facility_collection_category);
CREATE INDEX IF NOT EXISTS idx_branches_name_trgm ON branches USING gin(name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_branches_address_trgm ON branches USING gin(address gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_branches_code_trgm ON branches USING gin(branch_code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_branches_location ON branches USING gist(location);
CREATE INDEX IF NOT EXISTS idx_branches_lat_lon ON branches(lat, lon);
CREATE INDEX IF NOT EXISTS idx_branches_geocode_retry_queue
    ON branches(geocode_status, geocode_next_retry_at)
    WHERE geocode_status <> 'resolved';

-- Keep PostGIS location in sync with lat/lon when coordinates are available.
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
    BEFORE INSERT OR UPDATE OF lat, lon, name, address, phone, operating_hours, website_url, facility_type, facility_category, facility_source, facility_source_sheet, facility_service_group, facility_collection_category, region_sido, region_sigungu, regular_holiday, admission_fee, basic_info, address_source, coordinate_source, location_confidence, location_verified, location_checked_at, location_query, geocode_status, geocode_reason_code, geocode_attempt_count, geocode_candidates, geocode_next_retry_at, geocode_last_error, geocode_last_attempt_at
    ON branches
    FOR EACH ROW
    EXECUTE FUNCTION sync_branch_location();

-- =====================================================
-- Courses
-- =====================================================
CREATE TABLE IF NOT EXISTS courses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider VARCHAR(50) NOT NULL,
    provider_course_id VARCHAR(100) NOT NULL,
    -- Branch metadata cannot be deleted while courses still reference it.
    -- Lifecycle cleanup is handled by course soft deletion, never FK cascades.
    branch_id UUID REFERENCES branches(id) ON DELETE RESTRICT,

    title VARCHAR(255) NOT NULL,
    title_raw VARCHAR(255),
    title_prefix_removed TEXT,
    instructor VARCHAR(100),
    target VARCHAR(100),
    category_raw VARCHAR(100),
    collection_category VARCHAR(50),
    domain_category VARCHAR(50),
    standard_category_key VARCHAR(80),
    standard_category_label VARCHAR(80),
    source_group VARCHAR(50),
    operator_type VARCHAR(50),
    service_group VARCHAR(50),
    collection_type VARCHAR(50),

    -- NULL means the source did not publish a value; zero means explicitly free.
    fee NUMERIC,
    material_fee INTEGER,
    sessions INTEGER,
    schedule_raw TEXT,
    schedule_days TEXT[],
    schedule_dates JSONB,
    schedule_time_start TIME,
    schedule_time_end TIME,
    -- NULL means an unknown cadence. Never infer WEEKLY merely because it is absent.
    schedule_frequency VARCHAR(20),
    schedule_duration_minutes INTEGER,

    start_date DATE,
    end_date DATE,
    apply_start DATE,
    apply_end DATE,
    apply_period_raw TEXT,

    capacity_total INTEGER,
    capacity_current INTEGER,
    capacity_remaining INTEGER,
    waitlist_total INTEGER,

    venue_name VARCHAR(150),
    venue_address TEXT,
    application_url TEXT,
    application_type VARCHAR(30),
    application_method_raw TEXT,
    reservation_available BOOLEAN DEFAULT FALSE,
    discovery_status VARCHAR(50),
    program_type VARCHAR(50),
    eligibility_raw TEXT,
    raw_fields JSONB,

    status VARCHAR(50),
    -- Canonical collection entry point. Lifecycle cleanup must never cross
    -- between different endpoints owned by the same provider.
    source_endpoint TEXT,
    raw_url TEXT,
    description TEXT,
    image_url TEXT,
    view_count INTEGER DEFAULT 0,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    removed_at TIMESTAMP WITH TIME ZONE,
    content_hash TEXT,
    change_detected_at TIMESTAMP WITH TIME ZONE,

    ai_category VARCHAR(100),
    ai_tags TEXT,
    ai_summary TEXT,
    search_document TSVECTOR,
    is_ai_processed BOOLEAN DEFAULT FALSE,
    ai_title_processed BOOLEAN DEFAULT FALSE,
    ai_title_confidence NUMERIC(4, 3),
    ai_title_result JSONB,

    target_age_group VARCHAR(20),
    target_min_age INTEGER,
    target_max_age INTEGER,
    target_with_parent BOOLEAN DEFAULT FALSE,
    target_tags TEXT[],
    target_age_is_explicit BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_provider_course UNIQUE(provider, provider_course_id),
    CONSTRAINT chk_course_status
        CHECK (status IN ('OPEN', 'SCHEDULED', 'CLOSED', 'WAITING', 'DEADLINE') OR status IS NULL),
    CONSTRAINT chk_age_group
        CHECK (target_age_group IN ('INFANT', 'TODDLER', 'CHILD', 'TEEN', 'ADULT', 'SENIOR', 'ALL') OR target_age_group IS NULL),
    CONSTRAINT chk_age_range
        CHECK (
            (target_min_age IS NULL OR target_min_age >= 0)
            AND (target_max_age IS NULL OR target_max_age <= 1440)
            AND (target_min_age IS NULL OR target_max_age IS NULL OR target_min_age <= target_max_age)
        ),
    CONSTRAINT chk_schedule_frequency
        CHECK (schedule_frequency IN ('WEEKLY', 'BIWEEKLY', 'MONTHLY', 'IRREGULAR') OR schedule_frequency IS NULL),
    CONSTRAINT chk_duration
        CHECK (schedule_duration_minutes IS NULL OR schedule_duration_minutes > 0),
    CONSTRAINT chk_course_date_order
        CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date),
    CONSTRAINT chk_course_apply_date_order
        CHECK (apply_start IS NULL OR apply_end IS NULL OR apply_start <= apply_end),
    CONSTRAINT chk_course_capacity_nonnegative
        CHECK (
            (capacity_total IS NULL OR capacity_total >= 0)
            AND (capacity_current IS NULL OR capacity_current >= 0)
            AND (capacity_remaining IS NULL OR capacity_remaining >= 0)
            AND (waitlist_total IS NULL OR waitlist_total >= 0)
        ),
    CONSTRAINT chk_course_capacity_remaining
        CHECK (capacity_total IS NULL OR capacity_remaining IS NULL OR capacity_remaining <= capacity_total),
    CONSTRAINT chk_course_capacity_current
        CHECK (capacity_total IS NULL OR capacity_current IS NULL OR capacity_current <= capacity_total),
    CONSTRAINT chk_course_lifecycle
        CHECK ((is_active IS TRUE AND removed_at IS NULL) OR (is_active IS FALSE AND removed_at IS NOT NULL)),
    CONSTRAINT chk_course_seen_order
        CHECK (first_seen_at IS NULL OR last_seen_at IS NULL OR first_seen_at <= last_seen_at),
    CONSTRAINT chk_course_url_shape
        CHECK (
            (
                raw_url IS NULL
                OR (
                    btrim(raw_url) <> ''
                    AND length(raw_url) <= 4096
                    AND raw_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
                )
            )
            AND (
                application_url IS NULL
                OR btrim(application_url) = ''
                OR (
                    length(application_url) <= 4096
                    AND application_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
                )
            )
            AND (
                image_url IS NULL
                OR btrim(image_url) = ''
                OR (
                    length(image_url) <= 4096
                    AND image_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
                )
            )
        )
);

ALTER TABLE courses ADD COLUMN IF NOT EXISTS standard_category_key VARCHAR(80);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS standard_category_label VARCHAR(80);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS search_document TSVECTOR;

CREATE OR REPLACE FUNCTION mooncen_search_ngrams(p_text TEXT)
RETURNS TEXT AS $$
DECLARE
    token TEXT;
    token_length INTEGER;
    position INTEGER;
    output TEXT := '';
BEGIN
    FOR token IN
        SELECT match[1]
        FROM regexp_matches(lower(left(COALESCE(p_text, ''), 1000)), '([[:alnum:]가-힣]+)', 'g') AS match
    LOOP
        output := output || ' ' || token;
        token_length := char_length(token);
        IF token_length >= 2 THEN
            FOR position IN 1..(token_length - 1) LOOP
                output := output || ' ' || substring(token FROM position FOR 2);
            END LOOP;
        END IF;
        IF token_length >= 3 THEN
            FOR position IN 1..(token_length - 2) LOOP
                output := output || ' ' || substring(token FROM position FOR 3);
            END LOOP;
        END IF;
    END LOOP;
    RETURN output;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;

CREATE OR REPLACE FUNCTION mooncen_update_course_search_document()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_document :=
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.title)), 'A') ||
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.category_raw)), 'B') ||
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.description)), 'C') ||
        setweight(to_tsvector('simple', COALESCE(NEW.description, '')), 'C') ||
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.ai_summary)), 'C') ||
        setweight(to_tsvector('simple', COALESCE(NEW.ai_summary, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_courses_search_document ON courses;
CREATE TRIGGER trg_courses_search_document
    BEFORE INSERT OR UPDATE OF title, category_raw, description, ai_summary
    ON courses
    FOR EACH ROW EXECUTE FUNCTION mooncen_update_course_search_document();

UPDATE courses
SET search_document =
    setweight(to_tsvector('simple', mooncen_search_ngrams(title)), 'A') ||
    setweight(to_tsvector('simple', mooncen_search_ngrams(category_raw)), 'B') ||
    setweight(to_tsvector('simple', mooncen_search_ngrams(description)), 'C') ||
    setweight(to_tsvector('simple', COALESCE(description, '')), 'C') ||
    setweight(to_tsvector('simple', mooncen_search_ngrams(ai_summary)), 'C') ||
    setweight(to_tsvector('simple', COALESCE(ai_summary, '')), 'C')
WHERE search_document IS NULL;

-- unique_provider_course already provides the same btree key order.
CREATE INDEX IF NOT EXISTS idx_courses_branch_id ON courses(branch_id);
CREATE INDEX IF NOT EXISTS idx_courses_status ON courses(status);
CREATE INDEX IF NOT EXISTS idx_courses_is_active ON courses(is_active);
CREATE INDEX IF NOT EXISTS idx_courses_last_seen_at ON courses(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_courses_provider_source_endpoint
    ON courses(provider, source_endpoint)
    WHERE source_endpoint IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_courses_removed_at ON courses(removed_at) WHERE removed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_courses_content_hash ON courses(content_hash);
CREATE INDEX IF NOT EXISTS idx_courses_start_date ON courses(start_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_courses_schedule_dates ON courses USING gin(schedule_dates);
CREATE INDEX IF NOT EXISTS idx_courses_title_trgm ON courses USING gin(title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_courses_search_document ON courses USING gin(search_document);
CREATE INDEX IF NOT EXISTS idx_courses_description_trgm ON courses USING gin(description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_courses_ai_summary_trgm ON courses USING gin(ai_summary gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_courses_active_popular ON courses(view_count DESC, updated_at DESC) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_active_created ON courses(created_at DESC) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_active_deadline ON courses(apply_end ASC NULLS LAST) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_collection_category ON courses(collection_category);
CREATE INDEX IF NOT EXISTS idx_courses_domain_category ON courses(domain_category);
CREATE INDEX IF NOT EXISTS idx_courses_standard_category_key ON courses(standard_category_key);
CREATE INDEX IF NOT EXISTS idx_courses_standard_category_label ON courses(standard_category_label);
CREATE INDEX IF NOT EXISTS idx_courses_source_group ON courses(source_group);
CREATE INDEX IF NOT EXISTS idx_courses_service_group ON courses(service_group);
CREATE INDEX IF NOT EXISTS idx_courses_active_provider ON courses(provider) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_active_service_group ON courses(service_group) WHERE is_active IS TRUE;
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

-- =====================================================
-- Notifications and user-facing support tables
-- =====================================================
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
    fcm_token TEXT NOT NULL,
    notification_type VARCHAR(20),
    send_at TIMESTAMP WITH TIME ZONE NOT NULL,
    is_sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_notification_type
        CHECK (notification_type IN ('OPEN', 'START', 'DEADLINE') OR notification_type IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_notifications_send_at
    ON notifications(send_at)
    WHERE is_sent = FALSE;

CREATE TABLE IF NOT EXISTS user_favorites (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL,
    course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_user_favorite UNIQUE(user_id, course_id)
);

CREATE INDEX IF NOT EXISTS idx_user_favorites_user ON user_favorites(user_id);

CREATE TABLE IF NOT EXISTS user_course_marks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    mark_type VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_user_course_mark_type
        CHECK (mark_type IN ('favorite', 'applied')),
    CONSTRAINT unique_user_course_mark UNIQUE(user_id, course_id, mark_type)
);

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

    CONSTRAINT chk_user_course_start_alarm_minutes
        CHECK (start_alarm_minutes_before BETWEEN 0 AND 43200),
    CONSTRAINT chk_user_course_registration_alarm_minutes
        CHECK (registration_alarm_minutes_before BETWEEN 0 AND 43200),
    CONSTRAINT unique_user_course_notification_setting UNIQUE(user_id, course_id)
);

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
    CONSTRAINT unique_user_favorite_course_url UNIQUE(user_id, course_url),
    CONSTRAINT chk_user_favorite_course_url_shape
        CHECK (
            length(course_url) <= 4096
            AND (
                course_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
                OR course_url ~* '^course:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            )
        )
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
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_course_alert_type
        CHECK (alert_type IN ('registration_open', 'registration_closing', 'seat_available', 'new_course')),
    CONSTRAINT chk_course_alert_status
        CHECK (alert_status IN ('pending', 'sent', 'skipped', 'failed')),
    CONSTRAINT chk_course_alert_url_shape
        CHECK (
            course_url IS NULL
            OR btrim(course_url) = ''
            OR (
                length(course_url) <= 4096
                AND (
                    course_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
                    OR course_url ~* '^course:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                )
            )
        )
);

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
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_course_update_request_status
        CHECK (status IN ('pending', 'processing', 'checked', 'failed', 'expired')),
    CONSTRAINT chk_course_update_source_url_shape
        CHECK (
            source_url IS NULL
            OR btrim(source_url) = ''
            OR (
                length(source_url) <= 4096
                AND source_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_course_update_requests_pending_course
    ON course_update_requests(course_id)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_course_update_requests_active
    ON course_update_requests(status, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_course_update_requests_requested_at
    ON course_update_requests(requested_at DESC);

CREATE TABLE IF NOT EXISTS search_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    search_query TEXT NOT NULL,
    filters JSONB,
    result_count INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_search_logs_created ON search_logs(created_at DESC);

-- =====================================================
-- Ops monitoring
-- =====================================================
CREATE TABLE IF NOT EXISTS crawler_run_log (
    id BIGSERIAL PRIMARY KEY,
    target_key TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    crawler_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP WITH TIME ZONE,
    duration_seconds NUMERIC,
    collected_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_crawler_run_log_status
        CHECK (status IN ('running', 'success', 'failed', 'stopped', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_crawler_run_log_started
    ON crawler_run_log(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crawler_run_log_status_started
    ON crawler_run_log(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crawler_run_log_source_started
    ON crawler_run_log(source_type, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crawler_run_log_target_key
    ON crawler_run_log(target_key);

-- Runtime processes must not create/alter production objects.  Keep crawler
-- progress state in the owner-managed schema so the crawler LOGIN only needs
-- DML privileges granted by DB/roles.sql.
CREATE TABLE IF NOT EXISTS crawl_progress (
    run_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    elapsed_seconds NUMERIC,
    exit_code INTEGER,
    latest_report TEXT,
    error TEXT,
    PRIMARY KEY (run_id, provider)
);

CREATE TABLE IF NOT EXISTS course_quality_score (
    id BIGSERIAL PRIMARY KEY,
    course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
    url TEXT,
    provider TEXT,
    title TEXT,
    total_score INTEGER NOT NULL DEFAULT 0,
    grade TEXT NOT NULL DEFAULT 'bad',
    missing_fields TEXT[] NOT NULL DEFAULT '{}'::text[],
    checked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_course_quality_score_grade
        CHECK (grade IN ('good', 'warning', 'bad')),
    CONSTRAINT chk_course_quality_url_shape
        CHECK (
            url IS NULL
            OR btrim(url) = ''
            OR (
                length(url) <= 4096
                AND url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_course_quality_score_course_id
    ON course_quality_score(course_id)
    WHERE course_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_course_quality_score_url
    ON course_quality_score(url)
    WHERE url IS NOT NULL AND btrim(url) <> '';
CREATE INDEX IF NOT EXISTS idx_course_quality_score_checked
    ON course_quality_score(checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_course_quality_score_grade
    ON course_quality_score(grade);
CREATE INDEX IF NOT EXISTS idx_course_quality_score_provider_grade
    ON course_quality_score(provider, grade);

-- =====================================================
-- Service group normalization
-- =====================================================
/* Legacy embedded classifier disabled.
   DB/setup_db.py applies the generated DB/service_group.sql contract after the
   base schema. Keep classification rules in service_group.py only.
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

-- =====================================================
-- Convenience views
-- =====================================================
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

COMMENT ON TABLE branches IS 'Culture center branch information';
COMMENT ON TABLE courses IS 'Unified culture center course information';
COMMENT ON TABLE notifications IS 'User notification schedule';
COMMENT ON TABLE user_favorites IS 'User favorite courses';
COMMENT ON TABLE user_course_marks IS 'User saved course marks such as favorites and my courses';
COMMENT ON TABLE user_course_notification_settings IS 'Per-user notification preferences for my courses';
COMMENT ON TABLE user_favorite_courses IS 'User favorite courses keyed by course URL for notification generation';
COMMENT ON TABLE course_alerts IS 'Pending and sent course alert queue; external sending is handled separately';
COMMENT ON TABLE course_update_requests IS 'Short-lived course refresh queue created by user course clicks';
COMMENT ON TABLE search_logs IS 'Search logs for analytics';
COMMENT ON TABLE crawler_run_log IS 'Crawler execution history for ops monitoring';
COMMENT ON TABLE course_quality_score IS 'Per-course completeness score for ops monitoring';
