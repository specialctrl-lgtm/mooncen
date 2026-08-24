-- Restrict user-controlled course URL storage to HTTP(S) or UUID-backed internal references.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';

DELETE FROM user_favorite_courses invalid
 USING user_favorite_courses canonical
 WHERE invalid.id <> canonical.id
   AND invalid.user_id = canonical.user_id
   AND invalid.course_id IS NOT NULL
   AND canonical.course_url = 'course:' || invalid.course_id::text
   AND (
       length(invalid.course_url) > 4096
       OR (
           invalid.course_url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
           AND invalid.course_url !~* '^course:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       )
   );

WITH ranked_invalid_favorites AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY user_id, course_id
            ORDER BY created_at ASC NULLS LAST, id ASC
        ) AS duplicate_rank
    FROM user_favorite_courses
    WHERE course_id IS NOT NULL
      AND (
          length(course_url) > 4096
          OR (
              course_url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
              AND course_url !~* '^course:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          )
      )
)
DELETE FROM user_favorite_courses duplicate
 USING ranked_invalid_favorites ranked
 WHERE duplicate.id = ranked.id
   AND ranked.duplicate_rank > 1;

UPDATE user_favorite_courses
   SET course_url = 'course:' || course_id::text
 WHERE course_id IS NOT NULL
   AND (
       length(course_url) > 4096
       OR (
           course_url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
           AND course_url !~* '^course:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       )
   );

DELETE FROM user_favorite_courses
 WHERE course_id IS NULL
   AND (
       length(course_url) > 4096
       OR (
           course_url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
           AND course_url !~* '^course:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       )
   );

UPDATE course_alerts
   SET course_url = NULL
 WHERE course_url IS NOT NULL
   AND btrim(course_url) <> ''
   AND (
       length(course_url) > 4096
       OR (
           course_url !~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
           AND course_url !~* '^course:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       )
   );

ALTER TABLE user_favorite_courses DROP CONSTRAINT IF EXISTS chk_user_favorite_course_url_shape;
ALTER TABLE user_favorite_courses ADD CONSTRAINT chk_user_favorite_course_url_shape
    CHECK (
        length(course_url) <= 4096
        AND (
            course_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
            OR course_url ~* '^course:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
    ) NOT VALID;

ALTER TABLE course_alerts DROP CONSTRAINT IF EXISTS chk_course_alert_url_shape;
ALTER TABLE course_alerts ADD CONSTRAINT chk_course_alert_url_shape
    CHECK (
        course_url IS NULL
        OR btrim(course_url) = ''
        OR (
            length(course_url) <= 4096
            AND (
                course_url ~* '^https?://(\[[0-9a-f:.]+\]|[^/?#@:[:space:]]+)(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
                OR course_url ~* '^course:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            )
        )
    ) NOT VALID;

ALTER TABLE user_favorite_courses VALIDATE CONSTRAINT chk_user_favorite_course_url_shape;
ALTER TABLE course_alerts VALIDATE CONSTRAINT chk_course_alert_url_shape;
