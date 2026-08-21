DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler') THEN
        GRANT SELECT, INSERT, UPDATE ON TABLE ops_agents TO mooncen_crawler;
    END IF;
END
$$;
