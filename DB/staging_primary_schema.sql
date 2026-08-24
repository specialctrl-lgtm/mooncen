-- Primary-side objects used by tools/apply_staging_batch.py.
--
-- Deliberately excludes crawler-session triggers, crawl_batch_id columns, and
-- service-group classifier functions. Those belong to the staging database or
-- the canonical DB/service_group.sql migration and must never be replaced by a
-- batch apply operation.

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

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
