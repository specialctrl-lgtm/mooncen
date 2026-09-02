-- Preserve evidence-backed partial crawler completion in both the generic Job
-- record and its crawler-specific detail row.

ALTER TABLE ops_jobs
    DROP CONSTRAINT chk_ops_jobs_status;

ALTER TABLE ops_jobs
    ADD CONSTRAINT chk_ops_jobs_status
    CHECK (status IN (
        'queued', 'assigned', 'running', 'success', 'partial_success', 'failed',
        'cancelled', 'timed_out', 'blocked'
    ));
