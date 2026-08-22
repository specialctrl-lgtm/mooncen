-- Per-user JWT revocation generation. Incrementing auth_token_version makes
-- previously issued access/refresh tokens fail the backend version check.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS auth_token_version INTEGER NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.users'::regclass
          AND conname = 'chk_users_auth_token_version_positive'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT chk_users_auth_token_version_positive
            CHECK (auth_token_version > 0) NOT VALID;
    END IF;
END $$;

ALTER TABLE users
    VALIDATE CONSTRAINT chk_users_auth_token_version_positive;
