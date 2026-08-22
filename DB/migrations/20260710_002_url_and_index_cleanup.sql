-- Canonical URL guard and exact duplicate index cleanup.
-- The unique expression index intentionally fails if unresolved duplicate URLs
-- exist. Run the existing dedupe audit first; silently accepting duplicates
-- would leave concurrent writers unprotected.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '10min';

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION mooncen_raw_url_fingerprint(p_url TEXT)
RETURNS TEXT AS $$
    SELECT CASE
        WHEN NULLIF(btrim(p_url), '') IS NULL THEN NULL
        ELSE encode(public.digest(btrim(p_url), 'sha256'::text), 'hex')
    END;
$$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

CREATE UNIQUE INDEX IF NOT EXISTS ux_courses_provider_raw_url_fingerprint
    ON courses(provider, mooncen_raw_url_fingerprint(raw_url))
    WHERE raw_url IS NOT NULL AND btrim(raw_url) <> '';

-- Both are exact duplicates of indexes already owned by UNIQUE constraints.
DROP INDEX IF EXISTS idx_courses_provider_lookup;
DROP INDEX IF EXISTS ix_users_email;
