-- MoonCen ops monitoring tables.
-- Safe to re-run.

CREATE TABLE IF NOT EXISTS crawler_run_log (
    id BIGSERIAL PRIMARY KEY,
    target_key TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    crawler_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMPTZ,
    duration_seconds NUMERIC,
    collected_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_crawler_run_log_status
        CHECK (status IN ('running', 'success', 'failed', 'stopped', 'skipped'))
);

ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS target_key TEXT NOT NULL DEFAULT '';
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT '';
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS crawler_name TEXT NOT NULL DEFAULT '';
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'running';
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS duration_seconds NUMERIC;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS collected_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS inserted_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS updated_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS skipped_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS error_type TEXT;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_crawler_run_log_started
    ON crawler_run_log(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crawler_run_log_status_started
    ON crawler_run_log(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crawler_run_log_source_started
    ON crawler_run_log(source_type, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crawler_run_log_target_key
    ON crawler_run_log(target_key);

CREATE TABLE IF NOT EXISTS course_quality_score (
    id BIGSERIAL PRIMARY KEY,
    course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
    url TEXT,
    provider TEXT,
    title TEXT,
    total_score INTEGER NOT NULL DEFAULT 0,
    grade TEXT NOT NULL DEFAULT 'bad',
    missing_fields TEXT[] NOT NULL DEFAULT '{}'::text[],
    checked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_course_quality_score_grade
        CHECK (grade IN ('good', 'warning', 'bad'))
);

ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS course_id UUID REFERENCES courses(id) ON DELETE CASCADE;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS url TEXT;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS provider TEXT;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS total_score INTEGER NOT NULL DEFAULT 0;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS grade TEXT NOT NULL DEFAULT 'bad';
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS missing_fields TEXT[] NOT NULL DEFAULT '{}'::text[];
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS checked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

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
