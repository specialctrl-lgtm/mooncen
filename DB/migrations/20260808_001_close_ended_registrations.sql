SET LOCAL statement_timeout = '30min';

-- A date-only period remains valid through its final day in Seoul.  Close it
-- on the following day, but keep the row active so users can still inspect the
-- course and so normal end-date retention/deactivation remains unchanged.
UPDATE courses
SET status = 'CLOSED',
    reservation_available = FALSE,
    updated_at = CURRENT_TIMESTAMP
WHERE is_active IS TRUE
  AND (
        (
            end_date IS NOT NULL
            AND end_date < (NOW() AT TIME ZONE 'Asia/Seoul')::date
        )
        OR (
            status IN ('OPEN', 'DEADLINE')
            AND apply_end IS NOT NULL
            AND apply_end < (NOW() AT TIME ZONE 'Asia/Seoul')::date
        )
  )
  AND (
        status IS DISTINCT FROM 'CLOSED'
        OR reservation_available IS DISTINCT FROM FALSE
  );
