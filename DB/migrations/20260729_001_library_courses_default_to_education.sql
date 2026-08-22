-- Library catalogues contain ordinary lectures by default. Keep only rows
-- with direct experience, exhibition, performance, or visit evidence in the
-- experience scope.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30min';

DO $$
BEGIN
    IF to_regprocedure(
        'public.mooncen_infer_course_service_group(text,text,text,text,text,text,text,text,text,text,text)'
    ) IS NULL THEN
        RAISE EXCEPTION 'row-aware service-group classifier is not installed';
    END IF;
END $$;

WITH classified AS (
    SELECT
        id,
        mooncen_infer_course_service_group(
            provider,
            collection_category,
            domain_category,
            source_group,
            operator_type,
            NULL,
            venue_name,
            raw_url,
            concat_ws(' ', title, title_raw),
            category_raw,
            program_type
        ) AS next_service_group
    FROM courses
    WHERE lower(btrim(COALESCE(source_group, ''))) = 'library'
       OR strpos(btrim(COALESCE(collection_category, '')), '도서관') > 0
       OR strpos(btrim(COALESCE(domain_category, '')), '도서관') > 0
)
UPDATE courses AS course
SET service_group = classified.next_service_group,
    raw_fields = (
        CASE
            WHEN jsonb_typeof(COALESCE(course.raw_fields, '{}'::jsonb)) = 'object'
                THEN COALESCE(course.raw_fields, '{}'::jsonb)
            ELSE '{}'::jsonb
        END
        - 'service_group'
        - 'service_group_policy'
    ) || jsonb_build_object(
        'service_group', classified.next_service_group,
        'service_group_policy', 'inferred'
    ),
    updated_at = CURRENT_TIMESTAMP
FROM classified
WHERE course.id = classified.id
  AND (
      course.service_group IS DISTINCT FROM classified.next_service_group
      OR COALESCE(course.raw_fields ->> 'service_group', '') IS DISTINCT FROM classified.next_service_group
      OR COALESCE(course.raw_fields ->> 'service_group_policy', '') <> 'inferred'
  );
