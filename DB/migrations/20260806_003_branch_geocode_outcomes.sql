-- Persist every Kakao geocoding attempt as an explicit, retryable outcome.
-- Existing non-Kakao coordinates remain pending so they can be reverified
-- deliberately with --verify-existing --coordinate-source-prefix.

ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_status VARCHAR(32) NOT NULL DEFAULT 'pending';
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_reason_code VARCHAR(100);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_candidates JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_next_retry_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_last_error TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS geocode_last_attempt_at TIMESTAMP WITH TIME ZONE;

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

UPDATE branches
SET geocode_status = 'resolved',
    geocode_reason_code = COALESCE(geocode_reason_code, 'legacy_kakao_coordinates'),
    geocode_last_attempt_at = COALESCE(geocode_last_attempt_at, location_checked_at)
WHERE lat IS NOT NULL
  AND lon IS NOT NULL
  AND coordinate_source IN ('KAKAO_LOCAL_ADDRESS', 'KAKAO_LOCAL_KEYWORD')
  AND geocode_status = 'pending';

CREATE INDEX IF NOT EXISTS idx_branches_geocode_retry_queue
    ON branches(geocode_status, geocode_next_retry_at)
    WHERE geocode_status <> 'resolved';

COMMENT ON COLUMN branches.geocode_status IS
    'Latest Kakao geocoding outcome; not a proxy for whether legacy coordinates exist.';
COMMENT ON COLUMN branches.geocode_candidates IS
    'Bounded, non-secret candidate evidence retained for Ops review.';
