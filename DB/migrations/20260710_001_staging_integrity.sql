-- Expand staging apply states and add forward-only integrity checks safely.
-- CHECK constraints are NOT VALID: existing crawler debt remains readable while
-- all new/changed rows must satisfy the contract. Validate after the cleanup
-- queries in DB/UNKNOWN_NULL_TRANSITION.md are complete.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '10min';

DO $$
BEGIN
    IF to_regclass('public.crawl_batch_apply_logs') IS NOT NULL THEN
        ALTER TABLE crawl_batch_apply_logs
            DROP CONSTRAINT IF EXISTS chk_crawl_batch_apply_status;
        ALTER TABLE crawl_batch_apply_logs
            ADD CONSTRAINT chk_crawl_batch_apply_status
            CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'DRY_RUN'))
            NOT VALID;
    END IF;
END $$;

ALTER TABLE courses ALTER COLUMN fee DROP DEFAULT;
ALTER TABLE courses ALTER COLUMN material_fee DROP DEFAULT;
ALTER TABLE courses ALTER COLUMN schedule_frequency DROP DEFAULT;

-- Repair the small, deterministic legacy debt before enforcing constraints.
-- Swapping preserves both published dates; impossible capacity totals become
-- unknown instead of fabricating availability.
UPDATE courses
SET start_date = end_date,
    end_date = start_date
WHERE start_date IS NOT NULL AND end_date IS NOT NULL AND start_date > end_date;

UPDATE courses
SET apply_start = apply_end,
    apply_end = apply_start
WHERE apply_start IS NOT NULL AND apply_end IS NOT NULL AND apply_start > apply_end;

UPDATE courses
SET capacity_total = NULL,
    capacity_remaining = NULL
WHERE capacity_total IS NOT NULL
  AND capacity_current IS NOT NULL
  AND capacity_current > capacity_total;

UPDATE courses
SET application_url = NULL
WHERE application_url IS NOT NULL
  AND (btrim(application_url) = '' OR length(application_url) > 4096 OR application_url !~* '^https?://');

UPDATE courses
SET raw_url = NULL
WHERE raw_url IS NOT NULL
  AND (btrim(raw_url) = '' OR length(raw_url) > 4096 OR raw_url !~* '^https?://');

-- A branch delete must never silently erase its course history. PostgreSQL
-- treats NO ACTION and RESTRICT similarly for ordinary statements, but an
-- explicit RESTRICT contract documents the intended lifecycle and blocks the
-- destructive CASCADE used by the legacy schema. NOT VALID avoids a table scan;
-- existing rows are already protected by the previous FK.
ALTER TABLE courses DROP CONSTRAINT IF EXISTS courses_branch_id_fkey;
ALTER TABLE courses ADD CONSTRAINT courses_branch_id_fkey
    FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE RESTRICT
    NOT VALID;

-- Lifecycle repair is deterministic and prevents old soft-deleted rows from
-- failing the new-row constraint on their next unrelated update.
UPDATE courses
SET removed_at = COALESCE(removed_at, last_seen_at, updated_at, CURRENT_TIMESTAMP)
WHERE is_active IS FALSE
  AND removed_at IS NULL;

UPDATE courses
SET removed_at = NULL
WHERE is_active IS TRUE
  AND removed_at IS NOT NULL;

ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_course_date_order;
ALTER TABLE courses ADD CONSTRAINT chk_course_date_order
    CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date)
    NOT VALID;

ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_course_apply_date_order;
ALTER TABLE courses ADD CONSTRAINT chk_course_apply_date_order
    CHECK (apply_start IS NULL OR apply_end IS NULL OR apply_start <= apply_end)
    NOT VALID;

ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_course_capacity_nonnegative;
ALTER TABLE courses ADD CONSTRAINT chk_course_capacity_nonnegative
    CHECK (
        (capacity_total IS NULL OR capacity_total >= 0)
        AND (capacity_current IS NULL OR capacity_current >= 0)
        AND (capacity_remaining IS NULL OR capacity_remaining >= 0)
        AND (waitlist_total IS NULL OR waitlist_total >= 0)
    ) NOT VALID;

ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_course_capacity_remaining;
ALTER TABLE courses ADD CONSTRAINT chk_course_capacity_remaining
    CHECK (
        capacity_total IS NULL
        OR capacity_remaining IS NULL
        OR capacity_remaining <= capacity_total
    ) NOT VALID;

ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_course_capacity_current;
ALTER TABLE courses ADD CONSTRAINT chk_course_capacity_current
    CHECK (
        capacity_total IS NULL
        OR capacity_current IS NULL
        OR capacity_current <= capacity_total
    ) NOT VALID;

ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_course_lifecycle;
ALTER TABLE courses ADD CONSTRAINT chk_course_lifecycle
    CHECK (
        (is_active IS TRUE AND removed_at IS NULL)
        OR (is_active IS FALSE AND removed_at IS NOT NULL)
    ) NOT VALID;

ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_course_seen_order;
ALTER TABLE courses ADD CONSTRAINT chk_course_seen_order
    CHECK (
        first_seen_at IS NULL
        OR last_seen_at IS NULL
        OR first_seen_at <= last_seen_at
    ) NOT VALID;

ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_course_url_shape;
ALTER TABLE courses ADD CONSTRAINT chk_course_url_shape
    CHECK (
        (raw_url IS NULL OR (btrim(raw_url) <> '' AND length(raw_url) <= 4096 AND raw_url ~* '^https?://'))
        AND (application_url IS NULL OR (length(application_url) <= 4096 AND application_url ~* '^https?://'))
    ) NOT VALID;

ALTER TABLE courses VALIDATE CONSTRAINT courses_branch_id_fkey;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_date_order;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_apply_date_order;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_capacity_nonnegative;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_capacity_remaining;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_capacity_current;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_lifecycle;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_seen_order;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_url_shape;
