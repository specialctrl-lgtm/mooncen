-- Dedicated shared staging/control database marker.
--
-- This file is executed only by tools/ensure_crawler_control_schema.py after
-- it has matched --confirm-staging-database to the protected installer
-- connection.  Generic primary database setup never reads this file.

CREATE TABLE IF NOT EXISTS public.ops_crawler_control_database_marker (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    database_name NAME NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO public.ops_crawler_control_database_marker (singleton, database_name)
VALUES (TRUE, current_database())
ON CONFLICT (singleton) DO NOTHING;

DO $$
DECLARE
    marker_count INTEGER;
BEGIN
    SELECT count(*)
    INTO marker_count
    FROM public.ops_crawler_control_database_marker
    WHERE singleton IS TRUE
      AND database_name = current_database();
    IF marker_count <> 1
       OR (SELECT count(*) FROM public.ops_crawler_control_database_marker) <> 1 THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler control database marker does not match the confirmed database';
    END IF;
END;
$$;
