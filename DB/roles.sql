-- Least-privilege role groups for MoonCen.
-- Run as a PostgreSQL role with CREATEROLE after schema migrations. Login roles
-- and passwords are intentionally managed outside this repository.

-- Keep the revoke/grant convergence invisible to already-running API and Ops
-- sessions.  Without one transaction, a deployment worker can lose its own
-- queue privileges between the REVOKE and the matching GRANT and terminate the
-- deployment before rollback runs.
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_api') THEN
        CREATE ROLE mooncen_api NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler') THEN
        CREATE ROLE mooncen_crawler NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_deployment_worker') THEN
        CREATE ROLE mooncen_deployment_worker NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_worker') THEN
        CREATE ROLE mooncen_crawler_worker NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_control') THEN
        CREATE ROLE mooncen_crawler_control NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_publisher') THEN
        CREATE ROLE mooncen_crawler_publisher NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_finalizer') THEN
        CREATE ROLE mooncen_crawler_finalizer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_approver') THEN
        CREATE ROLE mooncen_crawler_approver NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_release_approver') THEN
        CREATE ROLE mooncen_crawler_release_approver NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_reporter') THEN
        CREATE ROLE mooncen_crawler_reporter NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_observer') THEN
        CREATE ROLE mooncen_crawler_observer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_release_admin') THEN
        CREATE ROLE mooncen_crawler_release_admin NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_api') THEN
        CREATE ROLE mooncen_crawler_api NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_applier') THEN
        CREATE ROLE mooncen_applier NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_ai') THEN
        CREATE ROLE mooncen_ai NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_check') THEN
        CREATE ROLE mooncen_check NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_readonly') THEN
        CREATE ROLE mooncen_readonly NOLOGIN;
    END IF;
END $$;

ALTER ROLE mooncen_api WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_deployment_worker WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_worker WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_control WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_publisher WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_finalizer WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_approver WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_release_approver WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_reporter WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_observer WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_release_admin WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_api WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_applier WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_ai WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_check WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_readonly WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Permission groups must never inherit an unrelated operator/owner role left
-- behind by an older installation.
DO $$
DECLARE
    membership record;
BEGIN
    FOR membership IN
        SELECT parent.rolname AS parent_name, member.rolname AS member_name
        FROM pg_auth_members am
        JOIN pg_roles parent ON parent.oid = am.roleid
        JOIN pg_roles member ON member.oid = am.member
        WHERE member.rolname IN (
            'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
            'mooncen_crawler_worker',
            'mooncen_crawler_control', 'mooncen_crawler_publisher',
            'mooncen_crawler_finalizer', 'mooncen_crawler_approver',
            'mooncen_crawler_release_approver',
            'mooncen_crawler_reporter', 'mooncen_crawler_observer',
            'mooncen_crawler_release_admin',
            'mooncen_crawler_api',
            'mooncen_applier',
            'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
        )
    LOOP
        EXECUTE format('REVOKE %I FROM %I', membership.parent_name, membership.member_name);
    END LOOP;
END $$;

-- Distributed workers are deliberately not members of the legacy crawler
-- group.  The legacy role can maintain crawl_batches for the old single-host
-- pipeline; a pull worker must never inherit that ability because it could
-- otherwise make its own snapshot eligible for primary promotion.  Explicit
-- worker grants below cover only fenced collection and lease reporting.

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON SCHEMA public
    FROM mooncen_api, mooncen_crawler, mooncen_deployment_worker,
        mooncen_crawler_worker,
        mooncen_crawler_control, mooncen_crawler_publisher,
        mooncen_crawler_finalizer, mooncen_crawler_approver,
        mooncen_crawler_release_approver,
        mooncen_crawler_reporter, mooncen_crawler_observer,
        mooncen_crawler_release_admin,
        mooncen_crawler_api,
        mooncen_applier,
        mooncen_ai, mooncen_check, mooncen_readonly;
-- Strip accidental PUBLIC grants from application-owned relations while
-- preserving extension-owned metadata such as PostGIS spatial_ref_sys.
DO $$
DECLARE
    item record;
BEGIN
    FOR item IN
        SELECT n.nspname AS schema_name, c.relname AS object_name, c.relkind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend d
              WHERE d.classid = 'pg_class'::regclass
                AND d.objid = c.oid
                AND d.deptype = 'e'
          )
    LOOP
        IF item.relkind = 'S' THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM PUBLIC',
                item.schema_name,
                item.object_name
            );
        ELSE
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM PUBLIC',
                item.schema_name,
                item.object_name
            );
        END IF;
    END LOOP;
END $$;

GRANT USAGE ON SCHEMA public
    TO mooncen_api, mooncen_crawler, mooncen_crawler_worker,
        mooncen_crawler_control,
        mooncen_crawler_publisher, mooncen_crawler_finalizer,
        mooncen_crawler_approver, mooncen_crawler_release_approver,
        mooncen_crawler_reporter,
        mooncen_crawler_observer, mooncen_crawler_release_admin,
        mooncen_crawler_api,
        mooncen_applier, mooncen_ai,
        mooncen_check, mooncen_readonly;
GRANT USAGE ON SCHEMA public TO mooncen_deployment_worker;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public
    FROM mooncen_deployment_worker;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public
    FROM mooncen_deployment_worker;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging') THEN
        REVOKE ALL PRIVILEGES ON SCHEMA crawl_staging
            FROM mooncen_deployment_worker;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA crawl_staging
            FROM mooncen_deployment_worker;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA crawl_staging
            FROM mooncen_deployment_worker;
    END IF;
END $$;
-- Converge old installations that previously granted every runtime role broad
-- table access. These group roles never own objects; the migration owner does.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public
    FROM mooncen_api, mooncen_crawler, mooncen_crawler_worker,
        mooncen_crawler_control, mooncen_crawler_publisher,
        mooncen_crawler_finalizer, mooncen_crawler_approver,
        mooncen_crawler_release_approver,
        mooncen_crawler_reporter, mooncen_crawler_observer,
        mooncen_crawler_release_admin,
        mooncen_crawler_api,
        mooncen_applier,
        mooncen_ai, mooncen_check;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM mooncen_readonly;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public
    FROM mooncen_api, mooncen_crawler, mooncen_crawler_worker,
        mooncen_crawler_control, mooncen_crawler_publisher,
        mooncen_crawler_finalizer, mooncen_crawler_approver,
        mooncen_crawler_release_approver,
        mooncen_crawler_reporter, mooncen_crawler_observer,
        mooncen_crawler_release_admin,
        mooncen_crawler_api,
        mooncen_applier,
        mooncen_ai, mooncen_check;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM mooncen_readonly;
-- Table-level REVOKE does not remove pg_attribute column ACLs.  Clear PUBLIC
-- and every distributed role's historical column grants before applying the
-- canonical control-DB contract below; this also prevents latent primary
-- privileges from surviving a later schema/CONNECT mistake.
DO $$
DECLARE
    column_item record;
BEGIN
    FOR column_item IN
        SELECT DISTINCT table_schema, table_name, column_name, grantee
        FROM information_schema.column_privileges
        WHERE table_schema IN ('public', 'crawl_staging')
          AND grantee IN (
              'PUBLIC',
              'mooncen_deployment_worker',
              'mooncen_crawler_worker', 'mooncen_crawler_control',
              'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
              'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
              'mooncen_crawler_reporter',
              'mooncen_crawler_observer',
              'mooncen_crawler_release_admin',
              'mooncen_crawler_api'
          )
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %s',
            column_item.column_name, column_item.table_schema,
            column_item.table_name,
            CASE
                WHEN column_item.grantee = 'PUBLIC' THEN 'PUBLIC'
                ELSE quote_ident(column_item.grantee)
            END
        );
    END LOOP;
END $$;
-- PostgreSQL grants EXECUTE on new routines to PUBLIC by default.  A direct
-- revoke from the runtime groups is therefore ineffective unless PUBLIC is
-- revoked as well.  Converge every MoonCen-owned routine here, including
-- aggregates and window functions;
-- extension members (PostGIS, pgcrypto, uuid-ossp, pg_trgm, ...) retain the
-- ACLs managed by their extension and remain usable by expressions/triggers.
DO $$
DECLARE
    routine record;
    routine_kind text;
BEGIN
    FOR routine IN
        SELECT
            namespace.nspname AS schema_name,
            procedure.proname AS routine_name,
            procedure.prokind,
            pg_get_function_identity_arguments(procedure.oid) AS identity_arguments
        FROM pg_proc procedure
        JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname IN ('public', 'crawl_staging')
          AND procedure.prokind IN ('f', 'p', 'a', 'w')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend dependency
              WHERE dependency.classid = 'pg_proc'::regclass
                AND dependency.objid = procedure.oid
                AND dependency.deptype = 'e'
          )
    LOOP
        routine_kind := CASE routine.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END;
        -- ALTER ... SECURITY is defined for functions/procedures, not
        -- aggregates or window functions.  EXECUTE ACLs, however, apply to
        -- all four pg_proc kinds and must be converged for each of them.
        IF routine.prokind IN ('f', 'p') THEN
            EXECUTE format(
                'ALTER %s %I.%I(%s) SECURITY INVOKER',
                routine_kind,
                routine.schema_name,
                routine.routine_name,
                routine.identity_arguments
            );
        END IF;
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON %s %I.%I(%s) FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly',
            routine_kind,
            routine.schema_name,
            routine.routine_name,
            routine.identity_arguments
        );
    END LOOP;
END $$;

-- These are the reviewed SECURITY DEFINER exceptions.  Identity resolvers are
-- also referenced by RLS policies, so converting them to SECURITY INVOKER
-- would recursively re-enter the ops_agents policy.  Trigger entry points are
-- not directly executable by runtimes; every function has a fixed search_path.
DO $$
DECLARE
    reviewed_signature TEXT;
    reviewed_routine regprocedure;
BEGIN
    FOREACH reviewed_signature IN ARRAY ARRAY[
        'enforce_crawler_worker_agent_heartbeat()',
        'current_crawler_worker_agent_id()',
        'current_crawler_worker_environment()',
        'current_crawler_reporter_agent_id()',
        'current_crawler_reporter_environment()',
        'is_crawler_managed_agent(uuid)',
        'is_crawler_control_job(uuid)',
        'is_current_crawler_worker_job(uuid)',
        'is_live_crawler_worker_job(uuid)',
        'enforce_crawler_worker_job_transition()',
        'enforce_crawler_worker_active_attempt()',
        'enforce_crawler_worker_attempt_insert()',
        'enforce_crawler_attempt_release_generation_insert()',
        'enforce_crawler_worker_attempt_transition()',
        'enforce_crawler_worker_observation_insert()',
        'enforce_crawler_worker_terminal_job_commit()',
        'enforce_crawler_worker_terminal_attempt_commit()',
        'enforce_crawler_promotion_role_separation()',
        'enforce_crawler_worker_runtime_evidence()',
        'enforce_crawler_release_report_timestamp()',
        'current_crawler_api_environment()',
        'stamp_crawler_release_action_request_digest()',
        'approve_crawler_release_action(uuid,text,text,text,integer)',
        'preview_crawler_release_action_for_approval(uuid,text)',
        'heartbeat_crawler_release_action_consumer(text,text)',
        'crawler_release_approval_contract_is_valid(text)',
        'crawler_release_action_runtime_is_ready(text)',
        'enforce_current_crawler_lease()',
        'capture_fenced_crawler_snapshot()'
    ]
    LOOP
        reviewed_routine := to_regprocedure('public.' || reviewed_signature);
        IF reviewed_routine IS NOT NULL THEN
            EXECUTE format('ALTER FUNCTION %s SECURITY DEFINER', reviewed_routine);
            EXECUTE format(
                'ALTER FUNCTION %s SET search_path = pg_catalog, public',
                reviewed_routine
            );
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', reviewed_routine);
        END IF;
    END LOOP;
END $$;
DO $$
DECLARE
    reviewed_routine regprocedure;
BEGIN
    reviewed_routine := to_regprocedure(
        'public.enforce_crawler_attempt_release_generation_immutable()'
    );
    IF reviewed_routine IS NOT NULL THEN
        EXECUTE format('ALTER FUNCTION %s SECURITY INVOKER', reviewed_routine);
        EXECUTE format(
            'ALTER FUNCTION %s SET search_path = pg_catalog, public',
            reviewed_routine
        );
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', reviewed_routine);
    END IF;
END $$;
DO $$
BEGIN
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON DATABASE %I FROM mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly',
        current_database()
    );
    EXECUTE format(
        'REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC',
        current_database()
    );
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly',
        current_database()
    );
    -- Batch identity guards and the reviewed staging applier use temporary
    -- tables. No API, worker, AI, monitoring, or read-only runtime does.
    EXECUTE format(
        'GRANT TEMPORARY ON DATABASE %I TO mooncen_crawler, mooncen_applier',
        current_database()
    );
END $$;
-- Monitoring is the only runtime group allowed to read every table. The other
-- groups receive explicit relation grants below so auth data does not leak to
-- crawlers/appliers and staging metadata does not leak to the API.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mooncen_readonly;
GRANT SELECT ON branches, courses
    TO mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_applier;
GRANT SELECT ON branches, courses TO mooncen_check;
GRANT SELECT ON users, oauth_accounts, user_course_marks,
    user_course_notification_settings, user_favorite_courses,
    course_alerts, course_update_requests,
    privacy_notice_versions, user_privacy_acceptances
    TO mooncen_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON users,
    user_course_marks, user_course_notification_settings,
    user_favorite_courses, course_alerts, course_update_requests
    TO mooncen_api;
GRANT INSERT, DELETE ON oauth_accounts TO mooncen_api;
-- Enrollment evidence is append-only for the application.  Column-scoped
-- INSERT keeps the UUID and accepted_at timestamp server-generated.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON privacy_notice_versions FROM mooncen_api;
REVOKE UPDATE, DELETE, TRUNCATE ON user_privacy_acceptances FROM mooncen_api;
GRANT INSERT (
    user_id, notice_version, acceptance_type, acquisition_method
) ON user_privacy_acceptances TO mooncen_api;
-- The public detail endpoint increments only this primary-owned counter.
GRANT UPDATE(view_count) ON courses TO mooncen_api;
GRANT INSERT, UPDATE, DELETE ON branches, courses TO mooncen_crawler;
-- Distributed workers append/upsert fenced staging rows.  Lifecycle closure is
-- an applier decision; DELETE has no snapshot trigger and is never granted.
GRANT INSERT, UPDATE ON branches, courses TO mooncen_crawler_worker;
GRANT INSERT, UPDATE ON branches, courses TO mooncen_applier;
-- AI workers can inspect only the course fields used by run_ai_pipeline and
-- its fixed status/quality reports. They cannot read branches or write source,
-- URL, lifecycle, pricing, or ownership fields.
GRANT SELECT (
    id, provider, title, title_raw, title_prefix_removed, target,
    target_age_group, target_min_age, target_max_age, target_with_parent,
    target_tags, target_age_is_explicit, description, category_raw,
    schedule_raw, is_ai_processed, ai_title_processed, ai_title_confidence,
    ai_title_result, ai_category, ai_tags, ai_summary, is_active,
    updated_at, created_at
) ON courses TO mooncen_ai;
GRANT UPDATE (
    title, target, target_age_group, target_min_age, target_max_age,
    target_with_parent, target_tags, target_age_is_explicit,
    title_prefix_removed, ai_title_processed, ai_title_confidence,
    ai_title_result, ai_category, ai_tags, ai_summary, is_ai_processed,
    updated_at
) ON courses TO mooncen_ai;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public
    TO mooncen_api, mooncen_crawler, mooncen_applier;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO mooncen_readonly;
-- Trigger entry points themselves do not require EXECUTE from the DML role.
-- These are only the nested/default/expression functions evaluated as the
-- invoker along the currently deployed INSERT/UPDATE paths.
DO $$
BEGIN
    IF to_regprocedure('public.mooncen_raw_url_fingerprint(text)') IS NOT NULL THEN
        -- PostgreSQL evaluates this courses expression-index function for every
        -- UPDATE, including API view counts and the restricted AI columns.
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.mooncen_raw_url_fingerprint(text) TO mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_applier, mooncen_ai';
    END IF;
    IF to_regprocedure('public.mooncen_search_ngrams(text)') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.mooncen_search_ngrams(text) TO mooncen_crawler, mooncen_crawler_worker, mooncen_applier, mooncen_ai';
    END IF;
    IF to_regprocedure('public.mooncen_text_contains_any(text,text[])') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.mooncen_text_contains_any(text,text[]) TO mooncen_crawler, mooncen_crawler_worker, mooncen_applier, mooncen_ai';
    END IF;
    IF to_regprocedure('public.mooncen_infer_course_service_group(text,text,text,text,text,text,text,text)') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.mooncen_infer_course_service_group(text,text,text,text,text,text,text,text) TO mooncen_crawler, mooncen_crawler_worker, mooncen_applier';
    END IF;
    IF to_regprocedure('public.mooncen_infer_course_service_group(text,text,text,text,text,text,text,text,text,text,text)') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.mooncen_infer_course_service_group(text,text,text,text,text,text,text,text,text,text,text) TO mooncen_crawler, mooncen_crawler_worker, mooncen_applier, mooncen_ai';
    END IF;
    IF to_regprocedure('public.mooncen_resolve_course_service_group(text,text,text,text,text,text,text,text,text)') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.mooncen_resolve_course_service_group(text,text,text,text,text,text,text,text,text) TO mooncen_crawler, mooncen_crawler_worker, mooncen_applier';
    END IF;
    IF to_regprocedure('public.mooncen_resolve_course_service_group(text,text,text,text,text,text,text,text,text,text,text,text)') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.mooncen_resolve_course_service_group(text,text,text,text,text,text,text,text,text,text,text,text) TO mooncen_crawler, mooncen_crawler_worker, mooncen_applier, mooncen_ai';
    END IF;
    IF to_regprocedure('public.current_crawl_batch_id()') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.current_crawl_batch_id() TO mooncen_crawler, mooncen_crawler_worker';
    END IF;
    IF to_regprocedure('public.current_crawler_worker_agent_id()') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.current_crawler_worker_agent_id() TO mooncen_crawler_worker';
    END IF;
    IF to_regprocedure('public.current_crawler_worker_environment()') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.current_crawler_worker_environment() TO mooncen_crawler_worker';
    END IF;
    IF to_regprocedure('public.current_crawler_reporter_agent_id()') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.current_crawler_reporter_agent_id() TO mooncen_crawler_reporter';
    END IF;
    IF to_regprocedure('public.current_crawler_reporter_environment()') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.current_crawler_reporter_environment() TO mooncen_crawler_reporter';
    END IF;
    IF to_regprocedure('public.is_crawler_managed_agent(uuid)') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.is_crawler_managed_agent(uuid) TO mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_applier, mooncen_check, mooncen_readonly';
    END IF;
    IF to_regprocedure('public.is_crawler_control_job(uuid)') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.is_crawler_control_job(uuid) TO mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_applier, mooncen_check, mooncen_readonly';
    END IF;
    IF to_regprocedure('public.is_current_crawler_worker_job(uuid)') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.is_current_crawler_worker_job(uuid) TO mooncen_crawler_worker';
    END IF;
    IF to_regprocedure('public.is_live_crawler_worker_job(uuid)') IS NOT NULL THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.is_live_crawler_worker_job(uuid) TO mooncen_crawler_worker';
    END IF;
END $$;
DO $$
BEGIN
    IF to_regclass('public.crawler_run_log') IS NOT NULL THEN
        GRANT SELECT ON crawler_run_log
            TO mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_applier;
        GRANT INSERT, UPDATE ON crawler_run_log TO mooncen_crawler;
        GRANT INSERT, UPDATE ON crawler_run_log TO mooncen_crawler_worker;
        IF to_regclass('public.crawler_run_log_id_seq') IS NOT NULL THEN
            GRANT USAGE, SELECT ON SEQUENCE crawler_run_log_id_seq
                TO mooncen_crawler, mooncen_crawler_worker;
        END IF;
    END IF;
    IF to_regclass('public.course_quality_score') IS NOT NULL THEN
        GRANT SELECT ON course_quality_score TO mooncen_api, mooncen_readonly;
    END IF;
    IF to_regclass('public.ops_agents') IS NOT NULL THEN
        GRANT SELECT ON ops_agents, ops_services, ops_deployments, ops_alerts,
            ops_crawler_runs, ops_crawler_errors, ops_quality_issues,
            ops_content_overrides
            TO mooncen_api;
        GRANT SELECT, INSERT, UPDATE ON ops_jobs TO mooncen_api;
        GRANT SELECT, INSERT ON ops_job_logs TO mooncen_api;
        GRANT SELECT, INSERT ON ops_audit_logs TO mooncen_api;
        -- The API creates deployment rows and terminalizes queued/stale
        -- assignments when an operator cancels an unowned deployment.
        GRANT INSERT ON ops_deployments TO mooncen_api;
        GRANT UPDATE (deployment_status, finished_at)
            ON ops_deployments TO mooncen_api;
        GRANT INSERT, UPDATE ON ops_crawler_runs, ops_quality_issues,
            ops_content_overrides
            TO mooncen_api;

        -- The central scheduler can only inspect/enqueue crawler work. Release
        -- rollout mutation remains with the authenticated API/operator path;
        -- the publisher below receives read-only release state.
        GRANT SELECT, INSERT, UPDATE ON ops_jobs, ops_crawler_runs
            TO mooncen_crawler_control;
        GRANT SELECT, INSERT ON ops_job_logs TO mooncen_crawler_control;

        -- Crawler/check workers can claim only already-created work and report
        -- its progress. They cannot create jobs, deployments, or audit rows.
        GRANT SELECT, INSERT, UPDATE ON ops_agents TO mooncen_crawler;
        GRANT SELECT ON ops_agents TO mooncen_crawler_worker;
        GRANT SELECT ON ops_agents TO mooncen_crawler_reporter;
        GRANT UPDATE (status, version, last_seen_at, updated_at)
            ON ops_agents TO mooncen_crawler_worker;
        GRANT SELECT, UPDATE ON ops_jobs TO mooncen_crawler, mooncen_check;
        GRANT SELECT, UPDATE ON ops_jobs TO mooncen_crawler_worker;
        GRANT SELECT, INSERT ON ops_job_logs TO mooncen_crawler, mooncen_check;
        GRANT SELECT, INSERT ON ops_job_logs TO mooncen_crawler_worker;
        -- Deployment workers filter updates by job_id/deployment_status, so
        -- PostgreSQL also requires SELECT on the referenced columns.
        GRANT SELECT, UPDATE ON ops_deployments TO mooncen_crawler;
        GRANT SELECT, INSERT, UPDATE ON ops_crawler_runs, ops_crawler_errors
            TO mooncen_crawler;
        GRANT SELECT, UPDATE ON ops_crawler_runs TO mooncen_crawler_worker;
        GRANT SELECT, INSERT, UPDATE ON ops_quality_issues TO mooncen_check;
        IF to_regclass('public.ops_job_logs_id_seq') IS NOT NULL THEN
            GRANT USAGE, SELECT ON SEQUENCE ops_job_logs_id_seq
                TO mooncen_api, mooncen_crawler, mooncen_crawler_worker,
                    mooncen_crawler_control, mooncen_check;
        END IF;
        IF to_regclass('public.ops_audit_logs_id_seq') IS NOT NULL THEN
            GRANT USAGE, SELECT ON SEQUENCE ops_audit_logs_id_seq
                TO mooncen_api;
        END IF;
        IF to_regclass('public.ops_crawler_errors_id_seq') IS NOT NULL THEN
            GRANT USAGE, SELECT ON SEQUENCE ops_crawler_errors_id_seq
                TO mooncen_crawler;
        END IF;
    END IF;
    IF to_regclass('public.ops_container_releases') IS NOT NULL THEN
        GRANT SELECT ON ops_container_releases,
            ops_container_validation_receipts,
            ops_container_approval_evidence
            TO mooncen_api;
        GRANT INSERT ON ops_container_approval_evidence TO mooncen_api;
        IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_deployment_worker'
        ) THEN
            REVOKE ALL PRIVILEGES ON ops_container_releases,
                ops_container_validation_receipts,
                ops_container_approval_evidence
                FROM mooncen_deployment_worker;
            GRANT SELECT, INSERT ON ops_container_releases,
                ops_container_validation_receipts
                TO mooncen_deployment_worker;
            GRANT SELECT ON ops_container_approval_evidence
                TO mooncen_deployment_worker;
            GRANT SELECT, INSERT, UPDATE ON ops_agents
                TO mooncen_deployment_worker;
            GRANT SELECT ON ops_jobs, ops_deployments, ops_job_logs
                TO mooncen_deployment_worker;
            GRANT UPDATE (
                status, agent_id, assigned_at, started_at, heartbeat_at,
                progress, result, error_code, error_message,
                cancel_requested_at, finished_at, updated_at,
                lease_token, lease_epoch, leased_until
            ) ON ops_jobs TO mooncen_deployment_worker;
            GRANT UPDATE (
                target_version, target_commit, deployment_status, started_at,
                finished_at, runtime_generation, activated_release_digest,
                runtime_previous_release_digest, controller_state_sha256,
                runtime_target_kind, runtime_native_baseline_identity
            ) ON ops_deployments TO mooncen_deployment_worker;
            GRANT INSERT ON ops_job_logs TO mooncen_deployment_worker;
            IF to_regclass('public.ops_job_logs_id_seq') IS NOT NULL THEN
                GRANT USAGE, SELECT ON SEQUENCE ops_job_logs_id_seq
                    TO mooncen_deployment_worker;
            END IF;
            IF to_regclass('public.ops_container_deployment_lease_epoch_seq') IS NOT NULL THEN
                GRANT USAGE, SELECT ON SEQUENCE
                    ops_container_deployment_lease_epoch_seq
                    TO mooncen_deployment_worker;
            END IF;
        END IF;
    END IF;
    IF to_regclass('public.crawl_progress') IS NOT NULL THEN
        GRANT SELECT, INSERT, UPDATE ON crawl_progress TO mooncen_crawler;
        GRANT SELECT, INSERT, UPDATE ON crawl_progress TO mooncen_crawler_worker;
    END IF;
    IF to_regclass('public.crawl_batches') IS NOT NULL THEN
        GRANT SELECT, INSERT, UPDATE ON crawl_batches TO mooncen_crawler;
        GRANT SELECT, INSERT ON crawl_batches TO mooncen_crawler_finalizer;
        GRANT UPDATE (
            status, finished_at, total_branches, total_courses,
            valid_courses, invalid_courses, result, updated_at
        ) ON crawl_batches TO mooncen_crawler_finalizer;
        GRANT SELECT ON crawl_batches TO mooncen_crawler_approver;
        GRANT UPDATE (result, updated_at)
            ON crawl_batches TO mooncen_crawler_approver;
        GRANT SELECT ON crawl_batches TO mooncen_applier;
    END IF;
    IF to_regclass('public.ops_crawler_batches') IS NOT NULL THEN
        IF to_regclass('public.ops_crawler_control_database_marker') IS NOT NULL THEN
            GRANT SELECT ON mooncen_schema_migrations,
                ops_crawler_control_database_marker
                TO mooncen_crawler_worker, mooncen_crawler_control,
                    mooncen_crawler_publisher, mooncen_crawler_finalizer,
                    mooncen_crawler_approver, mooncen_crawler_release_approver,
                    mooncen_crawler_reporter,
                    mooncen_crawler_observer, mooncen_crawler_release_admin,
                    mooncen_crawler_api;
        END IF;
        GRANT SELECT, INSERT, UPDATE ON ops_crawler_batches TO mooncen_api;
        GRANT SELECT, INSERT ON ops_crawler_batch_tasks TO mooncen_api;
        GRANT SELECT ON ops_crawler_task_attempts,
            ops_crawler_task_observations
            TO mooncen_api;
        -- Release state and action requests use mooncen_crawler_api below;
        -- the normal application API role retains no release-plane access.

        GRANT SELECT, INSERT ON ops_crawler_batches,
            ops_crawler_batch_tasks TO mooncen_crawler_control;
        GRANT SELECT ON ops_crawler_task_attempts,
            ops_crawler_task_observations, ops_crawler_release_artifacts,
            ops_crawler_release_rollouts, ops_crawler_worker_desired_state,
            ops_crawler_release_reports TO mooncen_crawler_control;
        IF to_regclass('public.ops_crawler_rollout_worker_snapshots') IS NOT NULL THEN
            GRANT SELECT ON ops_crawler_rollout_worker_snapshots
                TO mooncen_crawler_control;
        END IF;
        GRANT UPDATE (status, finished_at, error_code, error_message)
            ON ops_crawler_task_attempts TO mooncen_crawler_control;
        GRANT INSERT ON ops_crawler_task_observations
            TO mooncen_crawler_control;
        GRANT SELECT (id, environment, status, maintenance_mode, last_seen_at)
            ON ops_agents TO mooncen_crawler_control;
        GRANT SELECT (agent_id, environment, binding_type)
            ON ops_crawler_agent_bindings TO mooncen_crawler_control;

        GRANT SELECT ON ops_crawler_release_artifacts,
            ops_crawler_release_rollouts, ops_crawler_worker_desired_state
            TO mooncen_crawler_publisher;

        -- Root-invoked release administration can register immutable signed
        -- artifacts and advance only rollout desired state. It cannot enqueue
        -- jobs, seal batches, approve promotion, or write worker reports.
        GRANT SELECT, INSERT ON ops_crawler_release_artifacts
            TO mooncen_crawler_release_admin;
        GRANT SELECT, INSERT, UPDATE ON ops_crawler_release_rollouts,
            ops_crawler_worker_desired_state
            TO mooncen_crawler_release_admin;
        IF to_regclass('public.ops_crawler_rollout_worker_snapshots') IS NOT NULL THEN
            GRANT SELECT, INSERT ON ops_crawler_rollout_worker_snapshots
                TO mooncen_crawler_release_admin;
        END IF;
        GRANT SELECT ON ops_crawler_release_reports
            TO mooncen_crawler_release_admin;
        GRANT SELECT (id, name, hostname, environment, status,
            maintenance_mode, capabilities, last_seen_at)
            ON ops_agents TO mooncen_crawler_release_admin;
        GRANT SELECT (agent_id, environment, binding_type)
            ON ops_crawler_agent_bindings TO mooncen_crawler_release_admin;
        IF to_regclass('public.ops_crawler_release_action_requests') IS NOT NULL THEN
            GRANT SELECT ON ops_crawler_release_action_requests
                TO mooncen_crawler_release_admin;
            GRANT SELECT (database_login, environment)
                ON ops_crawler_api_bindings TO mooncen_crawler_release_admin;
            GRANT UPDATE (
                status, attempt_count, reconcile_only, lease_owner, lease_token, leased_until,
                result, error_code, error_message, started_at, finished_at, updated_at
            ) ON ops_crawler_release_action_requests
                TO mooncen_crawler_release_admin;
        END IF;

        IF to_regclass('public.ops_crawler_release_action_requests') IS NOT NULL THEN
            GRANT SELECT (artifact_digest, code_version, config_revision,
                size_bytes, key_id, metadata, created_at)
                ON ops_crawler_release_artifacts TO mooncen_crawler_api;
            GRANT SELECT (id, environment, rollout_epoch, artifact_digest,
                previous_artifact_digest, status, requested_worker_count,
                strategy, requested_by, worker_snapshot_required, created_at,
                started_at, finished_at)
                ON ops_crawler_release_rollouts TO mooncen_crawler_api;
            GRANT SELECT (environment, worker_key, agent_id, rollout_id,
                generation, desired_status, cohort, artifact_digest,
                code_version, config_revision, not_before, updated_at)
                ON ops_crawler_worker_desired_state TO mooncen_crawler_api;
            IF to_regclass('public.ops_crawler_rollout_worker_snapshots') IS NOT NULL THEN
                GRANT SELECT (environment, rollout_id, generation, worker_key,
                    agent_id, desired_status, cohort, artifact_digest,
                    code_version, config_revision, created_at)
                    ON ops_crawler_rollout_worker_snapshots TO mooncen_crawler_api;
            END IF;
            GRANT SELECT (id, rollout_id, environment, worker_key, agent_id,
                desired_generation, status, artifact_digest, code_version,
                config_revision, health, error_code, error_message,
                reported_at, created_at)
                ON ops_crawler_release_reports TO mooncen_crawler_api;
            GRANT SELECT (id, environment, status, scheduled_slot,
                expected_task_count, code_version, artifact_digest,
                config_revision, started_at, finished_at, created_at)
                ON ops_crawler_batches TO mooncen_crawler_api;
            GRANT SELECT (batch_id, job_id, task_key, provider,
                allowed_output_providers, required, shard_index, shard_count,
                created_at) ON ops_crawler_batch_tasks TO mooncen_crawler_api;
            GRANT SELECT (id, job_id, attempt_no, lease_epoch, agent_id,
                status, worker_code_version, artifact_digest, config_revision,
                rollout_id, release_generation, started_at, finished_at,
                exit_code, error_code, created_at)
                ON ops_crawler_task_attempts TO mooncen_crawler_api;
            GRANT SELECT (id, attempt_id, job_id, attempt_no, lease_epoch,
                observation_kind, observed_at, created_at)
                ON ops_crawler_task_observations TO mooncen_crawler_api;
            GRANT SELECT (id, job_id, provider, status, total_count,
                processed_count, success_count, failed_count, new_count,
                updated_count, deleted_candidate_count, started_at,
                finished_at, created_at)
                ON ops_crawler_runs TO mooncen_crawler_api;
            GRANT SELECT (id, job_type, status, environment, available_at,
                cancel_requested_at, leased_until, retry_count, max_retries,
                queued_at, started_at, finished_at)
                ON ops_jobs TO mooncen_crawler_api;
            GRANT SELECT (crawl_batch_id, total_courses, valid_courses,
                invalid_courses, result) ON crawl_batches TO mooncen_crawler_api;
            GRANT SELECT (provider, status, severity, blocked_sync, detected_at)
                ON ops_quality_issues TO mooncen_crawler_api;
            GRANT SELECT (id, name, hostname, environment, status,
                maintenance_mode, last_seen_at)
                ON ops_agents TO mooncen_crawler_api;
            GRANT SELECT (agent_id, environment, binding_type)
                ON ops_crawler_agent_bindings TO mooncen_crawler_api;
            GRANT SELECT (singleton, database_name)
                ON ops_crawler_control_database_marker TO mooncen_crawler_api;
            GRANT SELECT (version, checksum)
                ON mooncen_schema_migrations TO mooncen_crawler_api;
            GRANT SELECT (id, action, environment, status, idempotency_key,
                expected_generation, request_payload, requested_by,
                requester_role, reason, confirmation, attempt_count,
                reconcile_only, leased_until, result, error_code, error_message,
                created_at, started_at, finished_at, updated_at), INSERT (
                action, environment, idempotency_key, expected_generation,
                request_payload, requested_by, requester_role, reason, confirmation
            ) ON ops_crawler_release_action_requests TO mooncen_crawler_api;
            IF to_regclass('public.ops_crawler_studio_drafts') IS NOT NULL
               AND to_regclass('public.ops_crawler_studio_revisions') IS NOT NULL
               AND to_regclass('public.ops_crawler_studio_reviews') IS NOT NULL
               AND to_regclass('public.ops_crawler_studio_provider_paths') IS NOT NULL THEN
                GRANT SELECT ON ops_crawler_studio_provider_paths
                    TO mooncen_crawler_api;
                GRANT SELECT, INSERT ON ops_crawler_studio_drafts,
                    ops_crawler_studio_revisions, ops_crawler_studio_reviews
                    TO mooncen_crawler_api;
                GRANT UPDATE (status, latest_revision)
                    ON ops_crawler_studio_drafts TO mooncen_crawler_api;
                IF to_regprocedure('public.crawler_studio_contract_is_valid()')
                   IS NOT NULL THEN
                    GRANT EXECUTE ON FUNCTION crawler_studio_contract_is_valid()
                        TO mooncen_crawler_api;
                END IF;
            END IF;
            GRANT INSERT (user_id, action, resource_type, resource_id,
                ip_address, user_agent, before_data, after_data, result, job_id)
                ON ops_audit_logs TO mooncen_crawler_api;
            IF to_regclass('public.ops_audit_logs_id_seq') IS NOT NULL THEN
                GRANT USAGE ON SEQUENCE ops_audit_logs_id_seq
                    TO mooncen_crawler_api;
            END IF;
            GRANT EXECUTE ON FUNCTION current_crawler_api_environment()
                TO mooncen_crawler_api;
            IF to_regclass('public.ops_crawler_release_action_approvals') IS NOT NULL
               AND to_regclass('public.ops_crawler_release_approver_bindings') IS NOT NULL
               AND to_regclass('public.ops_crawler_release_action_consumers') IS NOT NULL
               AND to_regclass('public.ops_crawler_release_policy_contract') IS NOT NULL
               AND to_regprocedure(
                   'public.approve_crawler_release_action(uuid,text,text,text,integer)'
               ) IS NOT NULL
               AND to_regprocedure(
                   'public.crawler_release_approval_contract_is_valid(text)'
               ) IS NOT NULL THEN
                GRANT SELECT ON ops_crawler_release_action_approvals
                    TO mooncen_crawler_api, mooncen_crawler_release_admin;
                GRANT SELECT (request_digest)
                    ON ops_crawler_release_action_requests TO mooncen_crawler_api;
                GRANT EXECUTE ON FUNCTION approve_crawler_release_action(
                    UUID, TEXT, TEXT, TEXT, INTEGER
                ) TO mooncen_crawler_release_approver;
                GRANT EXECUTE ON FUNCTION preview_crawler_release_action_for_approval(
                    UUID, TEXT
                ) TO mooncen_crawler_release_approver;
                GRANT EXECUTE ON FUNCTION heartbeat_crawler_release_action_consumer(
                    TEXT, TEXT
                ) TO mooncen_crawler_release_admin;
                GRANT EXECUTE ON FUNCTION crawler_release_approval_contract_is_valid(TEXT)
                    TO mooncen_crawler_api, mooncen_crawler_release_approver,
                       mooncen_crawler_release_admin;
                GRANT EXECUTE ON FUNCTION crawler_release_action_runtime_is_ready(TEXT)
                    TO mooncen_crawler_api, mooncen_crawler_release_admin;
            END IF;
            IF to_regclass('public.course_quality_score') IS NOT NULL THEN
                GRANT SELECT (provider, total_score, grade, missing_fields,
                    checked_at) ON course_quality_score TO mooncen_crawler_api;
            END IF;
        END IF;

        GRANT SELECT ON ops_jobs, ops_crawler_batches,
            ops_crawler_batch_tasks, ops_crawler_task_attempts,
            ops_crawler_task_observations TO mooncen_crawler_finalizer;
        GRANT UPDATE (status, started_at, finished_at)
            ON ops_crawler_batches TO mooncen_crawler_finalizer;

        GRANT SELECT ON ops_crawler_batches, ops_crawler_batch_tasks,
            ops_crawler_task_attempts, ops_crawler_task_observations
            TO mooncen_crawler_approver;

        GRANT SELECT ON ops_crawler_worker_desired_state,
            ops_crawler_release_reports TO mooncen_crawler_reporter;
        GRANT INSERT ON ops_crawler_release_reports
            TO mooncen_crawler_reporter;

        -- The observer sees only bounded, non-secret metric dimensions.  Keep
        -- this contract here as well as in its login provisioner so a later
        -- roles.sql convergence cannot silently remove metrics access.
        GRANT SELECT (
            status, environment, job_type, available_at,
            cancel_requested_at, leased_until, retry_count, max_retries
        ) ON ops_jobs TO mooncen_crawler_observer;
        GRANT SELECT (id, environment, status, last_seen_at)
            ON ops_agents TO mooncen_crawler_observer;
        GRANT SELECT (environment, status, scheduled_slot)
            ON ops_crawler_batches TO mooncen_crawler_observer;
        GRANT SELECT (
            environment, worker_key, agent_id, generation, desired_status
        ) ON ops_crawler_worker_desired_state TO mooncen_crawler_observer;
        GRANT SELECT (
            environment, worker_key, agent_id, desired_generation,
            status, reported_at, created_at
        ) ON ops_crawler_release_reports TO mooncen_crawler_observer;

        GRANT SELECT ON ops_crawler_worker_desired_state
            TO mooncen_crawler_worker;
        GRANT SELECT, INSERT ON ops_crawler_task_attempts
            TO mooncen_crawler_worker;
        GRANT UPDATE (status, finished_at, exit_code, error_code, error_message, metrics)
            ON ops_crawler_task_attempts TO mooncen_crawler_worker;
        GRANT SELECT, INSERT ON ops_crawler_task_observations
            TO mooncen_crawler_worker;

        GRANT SELECT ON ops_jobs TO mooncen_applier;
        GRANT SELECT ON ops_crawler_batches TO mooncen_applier;
        GRANT SELECT ON ops_crawler_batch_tasks, ops_crawler_task_attempts,
            ops_crawler_task_observations TO mooncen_applier;
    END IF;
    IF to_regclass('public.crawl_batch_validation_errors') IS NOT NULL THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON crawl_batch_validation_errors TO mooncen_applier;
    END IF;
    IF to_regclass('public.crawl_batch_apply_logs') IS NOT NULL THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON crawl_batch_apply_logs TO mooncen_applier;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging') THEN
        REVOKE CREATE ON SCHEMA crawl_staging
            FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_applier,
                mooncen_crawler_control, mooncen_crawler_publisher,
                mooncen_crawler_finalizer, mooncen_crawler_approver,
                mooncen_crawler_release_approver,
                mooncen_crawler_reporter, mooncen_crawler_observer,
                mooncen_crawler_release_admin,
                mooncen_crawler_api,
                mooncen_ai, mooncen_check, mooncen_readonly;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA crawl_staging
            FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_applier,
                mooncen_crawler_control, mooncen_crawler_publisher,
                mooncen_crawler_finalizer, mooncen_crawler_approver,
                mooncen_crawler_release_approver,
                mooncen_crawler_reporter, mooncen_crawler_observer,
                mooncen_crawler_release_admin,
                mooncen_crawler_api,
                mooncen_ai, mooncen_check, mooncen_readonly;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA crawl_staging
            FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_applier,
                mooncen_crawler_control, mooncen_crawler_publisher,
                mooncen_crawler_finalizer, mooncen_crawler_approver,
                mooncen_crawler_release_approver,
                mooncen_crawler_reporter, mooncen_crawler_observer,
                mooncen_crawler_release_admin,
                mooncen_crawler_api,
                mooncen_ai, mooncen_check, mooncen_readonly;
        GRANT USAGE ON SCHEMA crawl_staging
            TO mooncen_crawler_finalizer, mooncen_crawler_approver,
                mooncen_applier, mooncen_readonly;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA crawl_staging TO mooncen_applier;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA crawl_staging TO mooncen_applier;
        GRANT SELECT ON ALL TABLES IN SCHEMA crawl_staging TO mooncen_readonly;
        GRANT SELECT ON ALL SEQUENCES IN SCHEMA crawl_staging TO mooncen_readonly;
        IF to_regclass('crawl_staging.fenced_branch_snapshots') IS NOT NULL THEN
            REVOKE INSERT, UPDATE, DELETE ON crawl_staging.fenced_branch_snapshots,
                crawl_staging.fenced_course_snapshots FROM mooncen_applier;
            GRANT SELECT ON crawl_staging.fenced_branch_snapshots,
                crawl_staging.fenced_course_snapshots
                TO mooncen_crawler_finalizer, mooncen_crawler_approver,
                    mooncen_applier;
        END IF;
    END IF;
END $$;

-- Distributed-crawler roles are valid only in the explicitly marked shared
-- staging/control database.  roles.sql is also used by primary DB setup, so
-- converge every distributed role back to zero database/schema/object access
-- when the marker is absent or does not exactly name the current database.
DO $$
DECLARE
    is_control_database BOOLEAN := FALSE;
    column_item record;
    relation_item record;
    routine_item record;
    object_kind TEXT;
BEGIN
    IF to_regclass('public.ops_crawler_control_database_marker') IS NOT NULL THEN
        EXECUTE
            'SELECT count(*) = 1 AND bool_and(singleton IS TRUE '
            'AND database_name = current_database()) '
            'FROM public.ops_crawler_control_database_marker'
        INTO is_control_database;
    END IF;
    IF COALESCE(is_control_database, FALSE) THEN
        RETURN;
    END IF;

    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON DATABASE %I FROM mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api',
        current_database()
    );
    REVOKE ALL PRIVILEGES ON SCHEMA public
        FROM mooncen_crawler_worker, mooncen_crawler_control,
            mooncen_crawler_publisher, mooncen_crawler_finalizer,
            mooncen_crawler_approver, mooncen_crawler_release_approver,
            mooncen_crawler_reporter,
            mooncen_crawler_observer, mooncen_crawler_release_admin,
            mooncen_crawler_api;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging') THEN
        REVOKE ALL PRIVILEGES ON SCHEMA crawl_staging
            FROM mooncen_crawler_worker, mooncen_crawler_control,
                mooncen_crawler_publisher, mooncen_crawler_finalizer,
                mooncen_crawler_approver, mooncen_crawler_release_approver,
                mooncen_crawler_reporter,
                mooncen_crawler_observer, mooncen_crawler_release_admin,
                mooncen_crawler_api;
    END IF;

    FOR column_item IN
        SELECT DISTINCT table_schema, table_name, column_name, grantee
        FROM information_schema.column_privileges
        WHERE table_schema IN ('public', 'crawl_staging')
          AND grantee IN (
              'mooncen_crawler_worker', 'mooncen_crawler_control',
              'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
              'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
              'mooncen_crawler_reporter',
              'mooncen_crawler_observer', 'mooncen_crawler_release_admin',
              'mooncen_crawler_api'
          )
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %I',
            column_item.column_name, column_item.table_schema,
            column_item.table_name, column_item.grantee
        );
    END LOOP;

    FOR relation_item IN
        SELECT namespace.nspname AS schema_name, class.relname AS object_name,
               class.relkind
        FROM pg_class class
        JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
        WHERE namespace.nspname IN ('public', 'crawl_staging')
          AND class.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = class.oid
                AND dependency.deptype = 'e'
          )
    LOOP
        object_kind := CASE relation_item.relkind WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE' END;
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON %s %I.%I FROM mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api',
            object_kind, relation_item.schema_name, relation_item.object_name
        );
    END LOOP;

    FOR routine_item IN
        SELECT namespace.nspname AS schema_name, procedure.proname AS routine_name,
               procedure.prokind,
               pg_get_function_identity_arguments(procedure.oid) AS identity_arguments
        FROM pg_proc procedure
        JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname IN ('public', 'crawl_staging')
          AND procedure.prokind IN ('f', 'p', 'a', 'w')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_proc'::regclass
                AND dependency.objid = procedure.oid
                AND dependency.deptype = 'e'
          )
    LOOP
        object_kind := CASE routine_item.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END;
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON %s %I.%I(%s) FROM mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api',
            object_kind, routine_item.schema_name, routine_item.routine_name,
            routine_item.identity_arguments
        );
    END LOOP;
END $$;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO mooncen_readonly;
-- Default routine ACLs belong to the role that creates the object, not to the
-- administrator running this file.  Cover every current MoonCen object owner
-- so future migration-created routines start with no PUBLIC/runtime EXECUTE.
-- This revoke must be owner-global: a per-schema default REVOKE cannot subtract
-- PostgreSQL's global PUBLIC EXECUTE default.  Required runtime calls are
-- granted explicitly above after each migration run.
DO $$
DECLARE
    owner_item record;
BEGIN
    FOR owner_item IN
        WITH application_owners AS (
            SELECT namespace.nspname AS schema_name, procedure.proowner AS owner_oid
            FROM pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname IN ('public', 'crawl_staging')
              AND procedure.prokind IN ('f', 'p', 'a', 'w')
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_depend dependency
                  WHERE dependency.classid = 'pg_proc'::regclass
                    AND dependency.objid = procedure.oid
                    AND dependency.deptype = 'e'
              )
            UNION
            SELECT namespace.nspname AS schema_name, class.relowner AS owner_oid
            FROM pg_class class
            JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
            WHERE namespace.nspname IN ('public', 'crawl_staging')
              AND class.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_depend dependency
                  WHERE dependency.classid = 'pg_class'::regclass
                    AND dependency.objid = class.oid
                    AND dependency.deptype = 'e'
              )
        )
        SELECT DISTINCT role.rolname AS owner_name
        FROM application_owners
        JOIN pg_roles role ON role.oid = application_owners.owner_oid
    LOOP
        -- Future relations/types must start closed as well.  Current-object
        -- REVOKEs cannot remove a stale pg_default_acl entry left by an
        -- earlier owner session.
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly',
            owner_item.owner_name
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly',
            owner_item.owner_name
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL PRIVILEGES ON TYPES FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly',
            owner_item.owner_name
        );
        IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging') THEN
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA crawl_staging REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly',
                owner_item.owner_name
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA crawl_staging REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly',
                owner_item.owner_name
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA crawl_staging REVOKE ALL PRIVILEGES ON TYPES FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly',
                owner_item.owner_name
            );
        END IF;
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I REVOKE EXECUTE ON ROUTINES FROM PUBLIC',
            owner_item.owner_name
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I REVOKE EXECUTE ON ROUTINES FROM mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly',
            owner_item.owner_name
        );
    END LOOP;
END $$;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging') THEN
        ALTER DEFAULT PRIVILEGES IN SCHEMA crawl_staging
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mooncen_applier;
        ALTER DEFAULT PRIVILEGES IN SCHEMA crawl_staging
            GRANT SELECT ON TABLES TO mooncen_readonly;
        ALTER DEFAULT PRIVILEGES IN SCHEMA crawl_staging
            GRANT SELECT ON SEQUENCES TO mooncen_readonly;
    END IF;
END $$;

-- Recovery receipt history is root/schema-admin audit evidence, not runtime
-- application data.  This explicit revoke must follow every broad
-- GRANT ... ON ALL TABLES convergence above.
DO $$
BEGIN
    IF to_regclass('public.ops_crawler_control_install_receipt_consumptions') IS NOT NULL THEN
        REVOKE ALL PRIVILEGES
        ON TABLE public.ops_crawler_control_install_receipt_consumptions
        FROM PUBLIC,
             mooncen_api,
             mooncen_crawler,
             mooncen_crawler_worker,
             mooncen_crawler_control,
             mooncen_crawler_publisher,
             mooncen_crawler_finalizer,
             mooncen_crawler_approver,
             mooncen_crawler_release_approver,
             mooncen_crawler_reporter,
             mooncen_crawler_observer,
             mooncen_crawler_release_admin,
             mooncen_crawler_api,
             mooncen_applier,
             mooncen_ai,
             mooncen_check,
             mooncen_readonly;
    END IF;
END $$;

COMMIT;

-- Example operator commands (replace login role names):
-- GRANT mooncen_api TO mooncen_api_login;
-- GRANT mooncen_crawler TO mooncen_crawler_login;
-- GRANT mooncen_deployment_worker TO mooncen_deployment_worker_login;
-- GRANT mooncen_applier TO mooncen_applier_login;
-- GRANT mooncen_ai TO mooncen_ai_login;
-- GRANT mooncen_check TO mooncen_check_login;
-- GRANT mooncen_readonly TO mooncen_monitor_login;
