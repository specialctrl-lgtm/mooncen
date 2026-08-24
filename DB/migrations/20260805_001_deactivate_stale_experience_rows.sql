-- A transient Goyang service-wait page was previously parsed as 32 courses.
--
-- This migration deliberately does not deactivate the legacy Ansan provider:
-- its canonical replacement must first complete a full production snapshot.
-- Keep the Goyang predicate provider- and title-exact so valid CL_01 rows and
-- the separate canonical education provider remain untouched.
LOCK TABLE courses IN SHARE ROW EXCLUSIVE MODE;

DO $$
DECLARE
    provider_active integer;
    exact_active integer;
    id_hash text;
BEGIN
    SELECT count(*)
      INTO provider_active
      FROM courses
     WHERE provider = 'MUNI_WWW_GOYANG_GO_KR_9C1A7354'
       AND is_active IS TRUE;

    -- A zero count makes the cleanup safely idempotent when the stale rows
    -- were already removed by an operator before this migration is applied.
    IF provider_active = 0 THEN
        RETURN;
    END IF;

    SELECT count(*), md5(string_agg(id::text, ',' ORDER BY id))
      INTO exact_active, id_hash
      FROM courses
     WHERE provider = 'MUNI_WWW_GOYANG_GO_KR_9C1A7354'
       AND title = '서비스 접속 대기 중입니다.'
       AND is_active IS TRUE;

    IF provider_active <> 32
       OR exact_active <> 32
       OR id_hash <> '644148f9aa45f0323a02ee513ced1897'
    THEN
        RAISE EXCEPTION
          'Goyang cleanup guard failed: provider=%, exact=%, hash=%',
          provider_active, exact_active, id_hash;
    END IF;
END $$;

CREATE TABLE ops_course_backup_goyang_wait_20260805
AS
SELECT *
  FROM courses
 WHERE provider = 'MUNI_WWW_GOYANG_GO_KR_9C1A7354'
   AND title = '서비스 접속 대기 중입니다.'
   AND is_active IS TRUE;

DO $$
DECLARE
    affected integer;
BEGIN
    IF (SELECT count(*) FROM ops_course_backup_goyang_wait_20260805) = 0 THEN
        RETURN;
    END IF;

    IF (SELECT count(*) FROM ops_course_backup_goyang_wait_20260805) <> 32 THEN
        RAISE EXCEPTION 'Goyang backup count is not 32';
    END IF;

    UPDATE courses
       SET is_active = FALSE,
           status = CASE
               WHEN status IN ('OPEN', 'WAITING', 'SCHEDULED', 'DEADLINE')
               THEN 'CLOSED'
               ELSE status
           END,
           removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
           updated_at = CURRENT_TIMESTAMP
     WHERE provider = 'MUNI_WWW_GOYANG_GO_KR_9C1A7354'
       AND title = '서비스 접속 대기 중입니다.'
       AND is_active IS TRUE;

    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 32 THEN
        RAISE EXCEPTION 'Updated %, expected 32', affected;
    END IF;
END $$;
