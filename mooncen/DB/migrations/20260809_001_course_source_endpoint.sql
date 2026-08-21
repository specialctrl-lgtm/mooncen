-- Scope lifecycle cleanup to the exact collection entry point.
--
-- Existing rows remain NULL on purpose: a detail URL is not reliable evidence
-- of the catalogue endpoint that owned a row. They become scoped only after a
-- crawler writes an explicitly reviewed source_endpoint.

ALTER TABLE courses
    ADD COLUMN IF NOT EXISTS source_endpoint TEXT;

CREATE INDEX IF NOT EXISTS idx_courses_provider_source_endpoint
    ON courses(provider, source_endpoint)
    WHERE source_endpoint IS NOT NULL;

COMMENT ON COLUMN courses.source_endpoint IS
    'Canonical crawler catalogue entry point used to isolate stale cleanup within a provider';
