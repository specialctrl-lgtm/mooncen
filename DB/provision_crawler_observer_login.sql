-- Provision the one LOGIN used by the crawler control-plane metrics collector.
-- Required psql variables:
--   crawler_observer_user
--   crawler_observer_password_verifier_b64
--
-- The observer receives column-level SELECT grants only.  In particular it
-- cannot read ops job parameters/results, agent credential hints, release
-- health payloads, or error messages.

\set ON_ERROR_STOP on

SELECT btrim(:'crawler_observer_user') <> ''
   AND :'crawler_observer_user' NOT IN (
       'postgres', 'mooncen_api', 'mooncen_crawler', 'mooncen_crawler_worker',
       'mooncen_crawler_control', 'mooncen_crawler_observer',
       'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
   ) AS crawler_observer_user_valid
\gset
\if :crawler_observer_user_valid
\else
    \echo 'crawler_observer_user must be a distinct non-empty LOGIN role'
    \quit 3
\endif

SELECT convert_from(
    decode(:'crawler_observer_password_verifier_b64', 'base64'),
    'UTF8'
) ~ '^SCRAM-SHA-256[$]4096:[A-Za-z0-9+/=]{24}[$][A-Za-z0-9+/=]{44}:[A-Za-z0-9+/=]{44}$'
    AS crawler_observer_verifier_valid
\gset
\if :crawler_observer_verifier_valid
\else
    \echo 'crawler observer password must be supplied as a client-generated SCRAM verifier'
    \quit 3
\endif

BEGIN;
SET LOCAL search_path = pg_catalog, public;

SELECT 'CREATE ROLE mooncen_crawler_observer NOLOGIN'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_observer'
)
\gexec

ALTER ROLE mooncen_crawler_observer WITH
    NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE mooncen_crawler_observer RESET ALL;

SELECT format('CREATE ROLE %I LOGIN', :'crawler_observer_user')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = :'crawler_observer_user'
)
\gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4 PASSWORD %L',
    :'crawler_observer_user',
    convert_from(decode(:'crawler_observer_password_verifier_b64', 'base64'), 'UTF8')
)
\gexec
SELECT format('ALTER ROLE %I RESET ALL', :'crawler_observer_user') \gexec

-- Ownership bypasses ACLs.  Neither the permission group nor its LOGIN may
-- own application/database objects.
SELECT NOT EXISTS (
    SELECT 1
    FROM pg_roles candidate
    WHERE candidate.rolname IN ('mooncen_crawler_observer', :'crawler_observer_user')
      AND (
          EXISTS (SELECT 1 FROM pg_database WHERE datdba = candidate.oid)
          OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = candidate.oid)
          OR EXISTS (
              SELECT 1
              FROM pg_class relation
              JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
              WHERE relation.relowner = candidate.oid
                AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')
          )
          OR EXISTS (
              SELECT 1
              FROM pg_proc routine
              JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
              WHERE routine.proowner = candidate.oid
                AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')
          )
          OR EXISTS (
              SELECT 1
              FROM pg_type data_type
              JOIN pg_namespace namespace ON namespace.oid = data_type.typnamespace
              WHERE data_type.typowner = candidate.oid
                AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')
          )
          OR EXISTS (SELECT 1 FROM pg_extension WHERE extowner = candidate.oid)
      )
) AS crawler_observer_ownership_safe
\gset
\if :crawler_observer_ownership_safe
\else
    \echo 'crawler observer roles own objects and cannot be safely converged'
    \quit 3
\endif

-- The group inherits no broader application role.  The LOGIN inherits only
-- this observer group.
SELECT format('REVOKE %I FROM mooncen_crawler_observer', parent.rolname)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname = 'mooncen_crawler_observer'
\gexec

SELECT format('REVOKE mooncen_crawler_observer FROM %I', member.rolname)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE parent.rolname = 'mooncen_crawler_observer'
\gexec

SELECT format('REVOKE %I FROM %I', parent.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname = :'crawler_observer_user'
\gexec

SELECT format('REVOKE %I FROM %I', parent.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE parent.rolname = :'crawler_observer_user'
\gexec

-- Remove stale direct ACLs from both identities before applying the reviewed
-- column-only contract.
SELECT format(
    'REVOKE ALL PRIVILEGES (%s) ON TABLE %I.%I FROM %I',
    string_agg(format('%I', privilege.column_name), ', ' ORDER BY privilege.column_name),
    privilege.table_schema,
    privilege.table_name,
    privilege.grantee
)
FROM (
    SELECT DISTINCT table_schema, table_name, column_name, grantee
    FROM information_schema.column_privileges
    WHERE grantee IN ('mooncen_crawler_observer', :'crawler_observer_user')
      AND table_schema IN ('public', 'crawl_staging')
) privilege
GROUP BY privilege.table_schema, privilege.table_name, privilege.grantee
\gexec

SELECT format(
    'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM %I',
    namespace.nspname,
    relation.relname,
    principal.role_name
)
FROM pg_class relation
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
CROSS JOIN (
    SELECT 'mooncen_crawler_observer'::text AS role_name
    UNION ALL SELECT :'crawler_observer_user'
) principal
WHERE namespace.nspname IN ('public', 'crawl_staging')
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND NOT EXISTS (
      SELECT 1
      FROM pg_depend dependency
      WHERE dependency.classid = 'pg_class'::regclass
        AND dependency.objid = relation.oid
        AND dependency.deptype = 'e'
  )
\gexec

SELECT format('REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I', principal.role_name)
FROM (
    SELECT 'mooncen_crawler_observer'::text AS role_name
    UNION ALL SELECT :'crawler_observer_user'
) principal
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON SCHEMA crawl_staging FROM %I', principal.role_name)
FROM (
    SELECT 'mooncen_crawler_observer'::text AS role_name
    UNION ALL SELECT :'crawler_observer_user'
) principal
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec

SELECT format(
    'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM %I',
    namespace.nspname,
    relation.relname,
    principal.role_name
)
FROM pg_class relation
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
CROSS JOIN (
    SELECT 'mooncen_crawler_observer'::text AS role_name
    UNION ALL SELECT :'crawler_observer_user'
) principal
WHERE namespace.nspname IN ('public', 'crawl_staging')
  AND relation.relkind = 'S'
  AND NOT EXISTS (
      SELECT 1
      FROM pg_depend dependency
      WHERE dependency.classid = 'pg_class'::regclass
        AND dependency.objid = relation.oid
        AND dependency.deptype = 'e'
  )
\gexec

SELECT format(
    'REVOKE ALL PRIVILEGES ON %s %I.%I(%s) FROM %I',
    CASE routine.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
    namespace.nspname,
    routine.proname,
    pg_get_function_identity_arguments(routine.oid),
    principal.role_name
)
FROM pg_proc routine
JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
CROSS JOIN (
    SELECT 'mooncen_crawler_observer'::text AS role_name
    UNION ALL SELECT :'crawler_observer_user'
) principal
WHERE namespace.nspname IN ('public', 'crawl_staging')
  AND routine.prokind IN ('f', 'p')
  AND NOT EXISTS (
      SELECT 1
      FROM pg_depend dependency
      WHERE dependency.classid = 'pg_proc'::regclass
        AND dependency.objid = routine.oid
        AND dependency.deptype = 'e'
  )
\gexec

GRANT USAGE ON SCHEMA public TO mooncen_crawler_observer;
GRANT SELECT (
    status, environment, job_type, available_at, cancel_requested_at,
    leased_until, retry_count, max_retries
) ON TABLE public.ops_jobs TO mooncen_crawler_observer;
GRANT SELECT (id, environment, status, last_seen_at)
    ON TABLE public.ops_agents TO mooncen_crawler_observer;
GRANT SELECT (environment, status, scheduled_slot)
    ON TABLE public.ops_crawler_batches TO mooncen_crawler_observer;
GRANT SELECT (environment, worker_key, agent_id, generation, desired_status)
    ON TABLE public.ops_crawler_worker_desired_state TO mooncen_crawler_observer;
GRANT SELECT (environment, worker_key, agent_id, desired_generation, status, reported_at, created_at)
    ON TABLE public.ops_crawler_release_reports TO mooncen_crawler_observer;

SELECT format('GRANT mooncen_crawler_observer TO %I', :'crawler_observer_user') \gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON DATABASE %I FROM mooncen_crawler_observer',
    current_database()
) \gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I',
    current_database(),
    :'crawler_observer_user'
) \gexec
SELECT format(
    'GRANT CONNECT ON DATABASE %I TO mooncen_crawler_observer',
    current_database()
) \gexec
SELECT format(
    'ALTER ROLE %I IN DATABASE %I RESET ALL',
    :'crawler_observer_user',
    current_database()
) \gexec
SELECT format(
    'ALTER ROLE %I IN DATABASE %I SET default_transaction_read_only = on',
    :'crawler_observer_user',
    current_database()
) \gexec
SELECT format(
    'ALTER ROLE %I IN DATABASE %I SET statement_timeout = %L',
    :'crawler_observer_user',
    current_database(),
    '5s'
) \gexec
SELECT format(
    'ALTER ROLE %I IN DATABASE %I SET lock_timeout = %L',
    :'crawler_observer_user',
    current_database(),
    '1s'
) \gexec
SELECT format(
    'ALTER ROLE %I IN DATABASE %I SET idle_in_transaction_session_timeout = %L',
    :'crawler_observer_user',
    current_database(),
    '10s'
) \gexec

SELECT (
    login.rolcanlogin
    AND login.rolinherit
    AND NOT login.rolsuper
    AND NOT login.rolcreatedb
    AND NOT login.rolcreaterole
    AND NOT login.rolreplication
    AND NOT login.rolbypassrls
    AND login.rolconnlimit = 4
    AND ARRAY(
        SELECT parent.rolname
        FROM pg_auth_members membership
        JOIN pg_roles parent ON parent.oid = membership.roleid
        WHERE membership.member = login.oid
        ORDER BY parent.rolname
    ) = ARRAY['mooncen_crawler_observer']::text[]
    AND NOT EXISTS (
        SELECT 1 FROM pg_auth_members membership WHERE membership.roleid = login.oid
    )
    AND ARRAY(
        SELECT member.rolname
        FROM pg_auth_members membership
        JOIN pg_roles member ON member.oid = membership.member
        WHERE membership.roleid = 'mooncen_crawler_observer'::regrole
        ORDER BY member.rolname
    ) = ARRAY[:'crawler_observer_user']::text[]
    AND has_column_privilege(:'crawler_observer_user', 'public.ops_jobs', 'status', 'SELECT')
    AND has_column_privilege(
        :'crawler_observer_user', 'public.ops_crawler_release_reports', 'status', 'SELECT'
    )
    AND NOT has_column_privilege(
        :'crawler_observer_user', 'public.ops_jobs', 'parameters', 'SELECT'
    )
    AND NOT has_column_privilege(
        :'crawler_observer_user', 'public.ops_crawler_release_reports', 'health', 'SELECT'
    )
    AND NOT has_table_privilege(
        :'crawler_observer_user', 'public.ops_jobs', 'INSERT,UPDATE,DELETE,TRUNCATE'
    )
    AND NOT has_table_privilege(
        :'crawler_observer_user',
        'public.ops_crawler_release_reports',
        'INSERT,UPDATE,DELETE,TRUNCATE'
    )
) AS crawler_observer_contract_verified
FROM pg_roles AS login
WHERE login.rolname = :'crawler_observer_user'
\gset
\if :crawler_observer_contract_verified
\else
    \echo 'crawler observer convergence verification failed'
    \quit 3
\endif

COMMIT;
