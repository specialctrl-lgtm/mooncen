-- Reconcile course scope after row-level experience evidence was added to the
-- generated service-group contract. DB/setup_db.py installs service_group.sql
-- before executing this immutable migration.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30min';

DO $$
BEGIN
    IF to_regprocedure(
        'public.mooncen_resolve_course_service_group(text,text,text,text,text,text,text,text,text,text,text,text)'
    ) IS NULL THEN
        RAISE EXCEPTION 'row-aware service-group classifier is not installed';
    END IF;
END $$;

WITH classified AS (
    SELECT
        id,
        mooncen_resolve_course_service_group(
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
            program_type,
            service_group
        ) AS next_service_group
    FROM courses
)
UPDATE courses AS course
SET service_group = classified.next_service_group,
    updated_at = CURRENT_TIMESTAMP
FROM classified
WHERE course.id = classified.id
  AND course.service_group IS DISTINCT FROM classified.next_service_group;

-- Deprecated/excluded target buckets are non-executable in the crawler
-- registry and must not remain searchable merely because legacy rows were
-- never marked stale.
UPDATE courses
SET is_active = FALSE,
    status = CASE
        WHEN status IN ('OPEN', 'WAITING', 'SCHEDULED', 'DEADLINE') THEN 'CLOSED'
        ELSE status
    END,
    removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
    updated_at = CURRENT_TIMESTAMP
WHERE is_active IS TRUE
  AND (
      lower(btrim(COALESCE(source_group, ''))) = 'deprecated'
      OR btrim(COALESCE(domain_category, '')) = '제외'
  );

-- Older Google-geocoding maintenance persisted candidates even when they
-- failed its own confidence threshold.  A guessed country/city centroid is
-- worse than no pin because radius search then exposes unrelated programmes
-- (for example a Tongyeong room appeared in Seoul).  The collector now skips
-- these candidates by default; clear only the matching legacy provenance.
UPDATE branches
SET address = NULL,
    lat = NULL,
    lon = NULL,
    address_source = NULL,
    coordinate_source = NULL,
    location_confidence = 0,
    location_verified = FALSE,
    location_checked_at = CURRENT_TIMESTAMP,
    location_query = NULL,
    updated_at = CURRENT_TIMESTAMP
WHERE coordinate_source = 'GOOGLE_GEOCODING'
  AND address_source = 'GOOGLE_GEOCODING'
  AND location_verified IS FALSE
  AND COALESCE(location_confidence, 0) < 75
  AND lat IS NOT NULL
  AND lon IS NOT NULL;

-- Reuse the authoritative culture-facility registry when a crawler branch has
-- the same unique normalized institution name. This is deterministic and does
-- not guess from a city-wide/default address, which avoids the false map pins
-- produced by broad geocoding queries.
WITH facility_locations AS (
    SELECT
        id,
        name,
        address,
        lat,
        lon,
        coordinate_source,
        location_confidence,
        location_verified,
        regexp_replace(lower(btrim(name)), '[^0-9A-Za-z가-힣]+', '', 'g') AS normalized_name
    FROM branches
    WHERE provider = 'CULTURE_FACILITY'
      AND location IS NOT NULL
), candidate_matches AS (
    SELECT
        target.id AS target_id,
        facility.id AS facility_id,
        facility.address,
        facility.lat,
        facility.lon,
        facility.coordinate_source,
        facility.location_confidence,
        facility.location_verified,
        count(*) OVER (PARTITION BY target.id) AS candidate_count
    FROM branches AS target
    JOIN facility_locations AS facility
      ON facility.normalized_name <> ''
     AND facility.normalized_name = regexp_replace(
         lower(btrim(target.name)),
         '[^0-9A-Za-z가-힣]+',
         '',
         'g'
     )
    WHERE target.provider <> 'CULTURE_FACILITY'
      AND target.location IS NULL
), unique_matches AS (
    SELECT *
    FROM candidate_matches
    WHERE candidate_count = 1
)
UPDATE branches AS target
SET address = COALESCE(NULLIF(target.address, ''), unique_matches.address),
    lat = unique_matches.lat,
    lon = unique_matches.lon,
    coordinate_source = concat('FACILITY_REGISTRY_MATCH:', unique_matches.facility_id),
    location_confidence = GREATEST(
        COALESCE(target.location_confidence, 0),
        COALESCE(unique_matches.location_confidence, 0),
        80
    ),
    location_verified = COALESCE(unique_matches.location_verified, FALSE),
    location_checked_at = CURRENT_TIMESTAMP,
    location_query = unique_matches.address,
    updated_at = CURRENT_TIMESTAMP
FROM unique_matches
WHERE target.id = unique_matches.target_id
  AND target.location IS NULL;
