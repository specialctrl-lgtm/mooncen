-- Persist OAuth email provenance and reject unsafe external URL schemes at rest.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '10min';

ALTER TABLE oauth_accounts
    ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE courses
   SET raw_url = NULL
 WHERE raw_url IS NOT NULL
   AND btrim(raw_url) <> ''
   AND (
       length(raw_url) > 4096
       OR raw_url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
   );

UPDATE courses
   SET application_url = NULL
 WHERE application_url IS NOT NULL
   AND btrim(application_url) <> ''
   AND (
       length(application_url) > 4096
       OR application_url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
   );

UPDATE courses
   SET image_url = NULL
 WHERE image_url IS NOT NULL
   AND btrim(image_url) <> ''
   AND (
       length(image_url) > 4096
       OR image_url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
   );

UPDATE branches
   SET website_url = NULL
 WHERE website_url IS NOT NULL
   AND btrim(website_url) <> ''
   AND (
       length(website_url) > 4096
       OR website_url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
   );

ALTER TABLE courses DROP CONSTRAINT IF EXISTS chk_course_url_shape;
ALTER TABLE courses ADD CONSTRAINT chk_course_url_shape
    CHECK (
        (
            raw_url IS NULL
            OR (
                btrim(raw_url) <> ''
                AND length(raw_url) <= 4096
                AND raw_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
            )
        )
        AND (
            application_url IS NULL
            OR btrim(application_url) = ''
            OR (
                length(application_url) <= 4096
                AND application_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
            )
        )
        AND (
            image_url IS NULL
            OR btrim(image_url) = ''
            OR (
                length(image_url) <= 4096
                AND image_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
            )
        )
    ) NOT VALID;

ALTER TABLE branches DROP CONSTRAINT IF EXISTS chk_branch_website_url_shape;
ALTER TABLE branches ADD CONSTRAINT chk_branch_website_url_shape
    CHECK (
        website_url IS NULL
        OR btrim(website_url) = ''
        OR (
            length(website_url) <= 4096
            AND website_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
        )
    ) NOT VALID;

ALTER TABLE courses VALIDATE CONSTRAINT chk_course_url_shape;
ALTER TABLE branches VALIDATE CONSTRAINT chk_branch_website_url_shape;
