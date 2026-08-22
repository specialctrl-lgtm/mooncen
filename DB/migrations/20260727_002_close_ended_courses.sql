SET LOCAL statement_timeout = '30min';

UPDATE courses
SET status = 'CLOSED',
    updated_at = CURRENT_TIMESTAMP
WHERE is_active IS TRUE
  AND end_date IS NOT NULL
  AND end_date < (NOW() AT TIME ZONE 'Asia/Seoul')::date
  AND status IS DISTINCT FROM 'CLOSED';

CREATE INDEX IF NOT EXISTS idx_courses_active_unclosed_end_date
    ON courses (end_date)
    WHERE is_active IS TRUE
      AND status IS DISTINCT FROM 'CLOSED';
