DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler')
       AND to_regclass('public.ops_deployments') IS NOT NULL THEN
        -- UPDATE statements filter on job_id/deployment_status and therefore
        -- require SELECT in addition to UPDATE.
        GRANT SELECT, UPDATE ON TABLE ops_deployments TO mooncen_crawler;
    END IF;
END
$$;
