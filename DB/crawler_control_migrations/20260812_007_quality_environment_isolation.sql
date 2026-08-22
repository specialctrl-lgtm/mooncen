-- Environment isolation for shared staging quality read models.
--
-- Quality rows do not carry an environment dimension.  They belong to the
-- marked shared staging data plane, so a production/development crawler API
-- login must not be able to read them directly even if it retains the reviewed
-- column grants used by the staging analytics endpoint.

DO $$
BEGIN
    IF to_regclass('public.ops_crawler_control_database_marker') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM public.ops_crawler_control_database_marker
           WHERE singleton IS TRUE
             AND database_name = current_database()::name
       ) THEN
        RAISE EXCEPTION 'quality environment isolation requires the marked crawler-control database';
    END IF;
    IF to_regclass('public.course_quality_score') IS NULL
       OR to_regclass('public.ops_quality_issues') IS NULL
       OR to_regprocedure('public.current_crawler_api_environment()') IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_api'
       ) THEN
        RAISE EXCEPTION 'quality environment isolation requires the quality and crawler API contract';
    END IF;
END;
$$;

ALTER TABLE course_quality_score ENABLE ROW LEVEL SECURITY;
ALTER TABLE course_quality_score FORCE ROW LEVEL SECURITY;
CREATE POLICY crawler_quality_score_acl_access
    ON course_quality_score
    USING (true)
    WITH CHECK (true);
CREATE POLICY crawler_api_quality_score_staging_scope
    ON course_quality_score AS RESTRICTIVE FOR SELECT
    USING (
        CASE
            WHEN pg_has_role(session_user, 'mooncen_crawler_api', 'member')
            THEN current_crawler_api_environment() = 'staging'
            ELSE TRUE
        END
    );

ALTER TABLE ops_quality_issues ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_quality_issues FORCE ROW LEVEL SECURITY;
CREATE POLICY crawler_quality_issue_acl_access
    ON ops_quality_issues
    USING (true)
    WITH CHECK (true);
CREATE POLICY crawler_api_quality_issue_staging_scope
    ON ops_quality_issues AS RESTRICTIVE FOR SELECT
    USING (
        CASE
            WHEN pg_has_role(session_user, 'mooncen_crawler_api', 'member')
            THEN current_crawler_api_environment() = 'staging'
            ELSE TRUE
        END
    );

COMMENT ON POLICY crawler_api_quality_score_staging_scope
    ON course_quality_score IS
    'Quality scores have shared-staging scope and are hidden from non-staging crawler API bindings.';
COMMENT ON POLICY crawler_api_quality_issue_staging_scope
    ON ops_quality_issues IS
    'Quality issues have shared-staging scope and are hidden from non-staging crawler API bindings.';
