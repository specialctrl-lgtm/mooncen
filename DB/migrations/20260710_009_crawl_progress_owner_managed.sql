-- Move crawler progress bootstrap out of the runtime LOGIN path.  This is
-- deliberately owner-managed so mooncen_crawler never needs CREATE on public.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';

CREATE TABLE IF NOT EXISTS crawl_progress (
    run_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    elapsed_seconds NUMERIC,
    exit_code INTEGER,
    latest_report TEXT,
    error TEXT,
    PRIMARY KEY (run_id, provider)
);
