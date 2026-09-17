-- Migration: 20260917_001_crawler_task_queue.sql
-- Description: Distributed crawler task queue table supporting atomic FOR UPDATE SKIP LOCKED
--              work claiming across multiple worker nodes (e.g. gen1crawler, mac).

CREATE TABLE IF NOT EXISTS crawler_task_queue (
    id BIGSERIAL PRIMARY KEY,
    batch_date DATE NOT NULL DEFAULT CURRENT_DATE,
    provider_code VARCHAR(64) NOT NULL,
    priority INT NOT NULL DEFAULT 10,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    worker_node VARCHAR(64),
    required_code_version VARCHAR(64),
    worker_code_version VARCHAR(64),
    attempt_count INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    started_at TIMESTAMPTZ,
    last_heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    exit_code INT,
    error_message TEXT,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_crawler_task_batch_provider UNIQUE (batch_date, provider_code),
    CONSTRAINT chk_crawler_task_status CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    CONSTRAINT chk_crawler_task_priority CHECK (priority >= 0 AND priority <= 100),
    CONSTRAINT chk_crawler_task_attempts CHECK (attempt_count >= 0 AND max_attempts > 0)
);

CREATE INDEX IF NOT EXISTS idx_crawler_task_queue_fetch
ON crawler_task_queue (batch_date, priority DESC, id)
WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_crawler_task_queue_heartbeat
ON crawler_task_queue (status, last_heartbeat_at)
WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_crawler_task_queue_batch_status
ON crawler_task_queue (batch_date, status);
