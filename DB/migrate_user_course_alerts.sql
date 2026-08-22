-- User favorite courses and first-pass course alert queue.
-- Current MoonCen users/courses primary keys are UUID, so reference columns use UUID.
-- Safe to re-run.

CREATE TABLE IF NOT EXISTS user_favorite_courses (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE SET NULL,
    course_url TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT unique_user_favorite_course_url UNIQUE(user_id, course_url)
);

CREATE INDEX IF NOT EXISTS idx_user_favorite_courses_user
    ON user_favorite_courses(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_favorite_courses_course
    ON user_favorite_courses(course_id)
    WHERE course_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS course_alerts (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE SET NULL,
    course_url TEXT,
    alert_type TEXT NOT NULL,
    alert_status TEXT NOT NULL DEFAULT 'pending',
    scheduled_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_course_alert_type
        CHECK (alert_type IN ('registration_open', 'registration_closing', 'seat_available', 'new_course')),
    CONSTRAINT chk_course_alert_status
        CHECK (alert_status IN ('pending', 'sent', 'skipped', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_course_alerts_user_status
    ON course_alerts(user_id, alert_status, scheduled_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_course_alerts_scheduled_pending
    ON course_alerts(scheduled_at)
    WHERE alert_status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS ux_course_alerts_user_course_type
    ON course_alerts(user_id, course_url, alert_type)
    WHERE course_url IS NOT NULL AND btrim(course_url) <> '';
