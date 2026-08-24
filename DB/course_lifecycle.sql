ALTER TABLE courses ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS removed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS change_detected_at TIMESTAMP WITH TIME ZONE;

UPDATE courses
SET is_active = TRUE,
    first_seen_at = COALESCE(first_seen_at, created_at, CURRENT_TIMESTAMP),
    last_seen_at = COALESCE(last_seen_at, updated_at, created_at, CURRENT_TIMESTAMP)
WHERE first_seen_at IS NULL
   OR last_seen_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_courses_is_active ON courses(is_active);
CREATE INDEX IF NOT EXISTS idx_courses_last_seen_at ON courses(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_courses_removed_at ON courses(removed_at) WHERE removed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_courses_content_hash ON courses(content_hash);
