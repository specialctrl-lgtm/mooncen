-- Extend the HTTP(S) storage perimeter to operational and refresh-request URLs.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';

UPDATE course_update_requests
   SET source_url = NULL
 WHERE source_url IS NOT NULL
   AND btrim(source_url) <> ''
   AND (
       length(source_url) > 4096
       OR source_url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
   );

UPDATE course_quality_score
   SET url = NULL
 WHERE url IS NOT NULL
   AND btrim(url) <> ''
   AND (
       length(url) > 4096
       OR url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
   );

ALTER TABLE course_update_requests DROP CONSTRAINT IF EXISTS chk_course_update_source_url_shape;
ALTER TABLE course_update_requests ADD CONSTRAINT chk_course_update_source_url_shape
    CHECK (
        source_url IS NULL
        OR btrim(source_url) = ''
        OR (
            length(source_url) <= 4096
            AND source_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
        )
    ) NOT VALID;

ALTER TABLE course_quality_score DROP CONSTRAINT IF EXISTS chk_course_quality_url_shape;
ALTER TABLE course_quality_score ADD CONSTRAINT chk_course_quality_url_shape
    CHECK (
        url IS NULL
        OR btrim(url) = ''
        OR (
            length(url) <= 4096
            AND url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
        )
    ) NOT VALID;

ALTER TABLE course_update_requests VALIDATE CONSTRAINT chk_course_update_source_url_shape;
ALTER TABLE course_quality_score VALIDATE CONSTRAINT chk_course_quality_url_shape;
