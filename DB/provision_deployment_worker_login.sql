-- Provision the one LOGIN used by the an2p deployment queue worker.
-- Run only after DB/roles.sql.  Required psql variables are supplied over
-- stdin and must never be committed:
--   db_deployment_worker_user
--   db_deployment_worker_password_b64

\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'mooncen_deployment_worker' AND NOT rolcanlogin
    ) THEN
        RAISE EXCEPTION 'mooncen_deployment_worker NOLOGIN group is unavailable';
    END IF;
END
$$;

SELECT 'DO $guard$ BEGIN RAISE EXCEPTION ''invalid deployment worker LOGIN name''; END $guard$;'
WHERE :'db_deployment_worker_user' <> 'mooncen_deployment_worker_login'
\gexec

SELECT format('CREATE ROLE %I LOGIN', :'db_deployment_worker_user')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = :'db_deployment_worker_user'
)
\gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'db_deployment_worker_user',
    convert_from(
        decode(:'db_deployment_worker_password_b64', 'base64'),
        'UTF8'
    )
)
\gexec

SELECT format('REVOKE %I FROM %I', parent.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname = :'db_deployment_worker_user'
\gexec

-- Large-object and pg_catalog ACL convergence requires the PostgreSQL
-- bootstrap superuser.  The guarded native installer always pipes this file
-- through sudo -u postgres; reject any weaker/manual execution.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = current_user AND rolsuper
    ) THEN
        RAISE EXCEPTION 'deployment worker boundary requires a PostgreSQL superuser';
    END IF;
END
$$;

-- A role/database setting survives ordinary ALTER ROLE attribute convergence
-- and can change trigger, preload, or search-path behaviour at login time.
-- Remove both global and every per-database setting for the fixed LOGIN and
-- its only permission group before any ACL is granted back.
SELECT format('ALTER ROLE %I RESET ALL', role_name)
FROM (VALUES
    (:'db_deployment_worker_user'),
    ('mooncen_api'),
    ('mooncen_crawler'),
    ('mooncen_deployment_worker'),
    ('mooncen_crawler_worker'),
    ('mooncen_crawler_control'),
    ('mooncen_crawler_publisher'),
    ('mooncen_crawler_finalizer'),
    ('mooncen_crawler_approver'),
    ('mooncen_crawler_release_approver'),
    ('mooncen_crawler_reporter'),
    ('mooncen_crawler_observer'),
    ('mooncen_crawler_release_admin'),
    ('mooncen_crawler_api'),
    ('mooncen_applier'),
    ('mooncen_ai'),
    ('mooncen_check'),
    ('mooncen_readonly')
) AS managed_role(role_name)
\gexec
SELECT format(
    'ALTER ROLE %I IN DATABASE %I RESET ALL',
    role.rolname,
    database.datname
)
FROM pg_catalog.pg_db_role_setting setting
JOIN pg_catalog.pg_roles role ON role.oid = setting.setrole
JOIN pg_catalog.pg_database database ON database.oid = setting.setdatabase
WHERE role.rolname IN (
    :'db_deployment_worker_user',
    'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
    'mooncen_crawler_worker', 'mooncen_crawler_control',
    'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
    'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
    'mooncen_crawler_reporter', 'mooncen_crawler_observer',
    'mooncen_crawler_release_admin', 'mooncen_crawler_api',
    'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
)
\gexec

-- System namespaces are never an application persistence surface.  Remove
-- CREATE plus every direct relation/sequence/column/routine ACL held by the
-- permission group or fixed LOGIN.  Table-level REVOKE does not clear
-- pg_attribute.attacl, hence the explicit per-column convergence.
SELECT format(
    'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly, %I',
    namespace.nspname,
    :'db_deployment_worker_user'
)
FROM pg_catalog.pg_namespace namespace
WHERE namespace.nspname ~ '^pg_'
   OR namespace.nspname = 'information_schema'
\gexec
SELECT DISTINCT format(
    'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I',
    namespace.nspname,
    member.rolname
)
FROM pg_catalog.pg_namespace namespace
CROSS JOIN pg_catalog.pg_auth_members membership
JOIN pg_catalog.pg_roles parent ON parent.oid = membership.roleid
JOIN pg_catalog.pg_roles member ON member.oid = membership.member
WHERE (namespace.nspname ~ '^pg_' OR namespace.nspname = 'information_schema')
  AND parent.rolname IN (
      'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
      'mooncen_crawler_worker', 'mooncen_crawler_control',
      'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
      'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
      'mooncen_crawler_reporter', 'mooncen_crawler_observer',
      'mooncen_crawler_release_admin', 'mooncen_crawler_api',
      'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
  )
\gexec
SELECT format(
    '%s %s ON SCHEMA %I FROM PUBLIC',
    CASE
        WHEN namespace.nspname = 'information_schema'
             AND current_acl.privilege_type = 'USAGE'
          OR EXISTS (
              SELECT 1
              FROM pg_catalog.aclexplode(
                  COALESCE(
                      initial_acl.initprivs,
                      pg_catalog.acldefault('n', namespace.nspowner)
                  )
              ) baseline_acl
              WHERE baseline_acl.grantee = 0
                AND baseline_acl.privilege_type = current_acl.privilege_type
          )
        THEN 'REVOKE GRANT OPTION FOR'
        ELSE 'REVOKE'
    END,
    current_acl.privilege_type,
    namespace.nspname
)
FROM pg_catalog.pg_namespace namespace
LEFT JOIN pg_catalog.pg_init_privs initial_acl
  ON initial_acl.classoid = 'pg_catalog.pg_namespace'::regclass
 AND initial_acl.objoid = namespace.oid
 AND initial_acl.objsubid = 0
CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(
        namespace.nspacl,
        pg_catalog.acldefault('n', namespace.nspowner)
    )
) current_acl
WHERE namespace.nspname IN ('pg_catalog', 'pg_toast', 'information_schema')
  AND current_acl.grantee = 0
  AND (
      (
          namespace.nspname = 'information_schema'
          AND (
              current_acl.privilege_type <> 'USAGE'
              OR current_acl.is_grantable
          )
      )
      OR (
          namespace.nspname <> 'information_schema'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.aclexplode(
                  COALESCE(
                      initial_acl.initprivs,
                      pg_catalog.acldefault('n', namespace.nspowner)
                  )
              ) baseline_acl
              WHERE baseline_acl.grantee = 0
                AND baseline_acl.privilege_type = current_acl.privilege_type
                AND (
                    NOT current_acl.is_grantable
                    OR baseline_acl.is_grantable
                )
          )
      )
  )
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly, %I',
    namespace.nspname,
    :'db_deployment_worker_user'
)
FROM pg_catalog.pg_namespace namespace
WHERE namespace.nspname ~ '^pg_'
   OR namespace.nspname = 'information_schema'
\gexec
SELECT DISTINCT format(
    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM %I',
    namespace.nspname,
    member.rolname
)
FROM pg_catalog.pg_namespace namespace
CROSS JOIN pg_catalog.pg_auth_members membership
JOIN pg_catalog.pg_roles parent ON parent.oid = membership.roleid
JOIN pg_catalog.pg_roles member ON member.oid = membership.member
WHERE (namespace.nspname ~ '^pg_' OR namespace.nspname = 'information_schema')
  AND parent.rolname IN (
      'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
      'mooncen_crawler_worker', 'mooncen_crawler_control',
      'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
      'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
      'mooncen_crawler_reporter', 'mooncen_crawler_observer',
      'mooncen_crawler_release_admin', 'mooncen_crawler_api',
      'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
  )
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly, %I',
    namespace.nspname,
    :'db_deployment_worker_user'
)
FROM pg_catalog.pg_namespace namespace
WHERE namespace.nspname ~ '^pg_'
   OR namespace.nspname = 'information_schema'
\gexec
SELECT DISTINCT format(
    'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM %I',
    namespace.nspname,
    member.rolname
)
FROM pg_catalog.pg_namespace namespace
CROSS JOIN pg_catalog.pg_auth_members membership
JOIN pg_catalog.pg_roles parent ON parent.oid = membership.roleid
JOIN pg_catalog.pg_roles member ON member.oid = membership.member
WHERE (namespace.nspname ~ '^pg_' OR namespace.nspname = 'information_schema')
  AND parent.rolname IN (
      'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
      'mooncen_crawler_worker', 'mooncen_crawler_control',
      'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
      'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
      'mooncen_crawler_reporter', 'mooncen_crawler_observer',
      'mooncen_crawler_release_admin', 'mooncen_crawler_api',
      'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
  )
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %I',
    attribute.attname,
    namespace.nspname,
    relation.relname,
    grantee.rolname
)
FROM pg_catalog.pg_attribute attribute
JOIN pg_catalog.pg_class relation ON relation.oid = attribute.attrelid
JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) acl
JOIN pg_catalog.pg_roles grantee ON grantee.oid = acl.grantee
WHERE attribute.attnum > 0
  AND NOT attribute.attisdropped
  AND (namespace.nspname ~ '^pg_' OR namespace.nspname = 'information_schema')
  AND (
      grantee.rolname IN (
          :'db_deployment_worker_user',
          'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
          'mooncen_crawler_worker', 'mooncen_crawler_control',
          'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
          'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
          'mooncen_crawler_reporter', 'mooncen_crawler_observer',
          'mooncen_crawler_release_admin', 'mooncen_crawler_api',
          'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
      )
      OR EXISTS (
           SELECT 1
           FROM pg_catalog.pg_auth_members membership
           JOIN pg_catalog.pg_roles parent ON parent.oid = membership.roleid
           WHERE membership.member = grantee.oid
             AND parent.rolname IN (
                 'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
                 'mooncen_crawler_worker', 'mooncen_crawler_control',
                 'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
                 'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
                 'mooncen_crawler_reporter', 'mooncen_crawler_observer',
                 'mooncen_crawler_release_admin', 'mooncen_crawler_api',
                 'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
             )
      )
  )
\gexec
REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA pg_catalog
FROM mooncen_api, mooncen_crawler, mooncen_deployment_worker,
    mooncen_crawler_worker, mooncen_crawler_control,
    mooncen_crawler_publisher, mooncen_crawler_finalizer,
    mooncen_crawler_approver, mooncen_crawler_release_approver,
    mooncen_crawler_reporter, mooncen_crawler_observer,
    mooncen_crawler_release_admin, mooncen_crawler_api,
    mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly;
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA %I FROM mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly, %I',
    namespace.nspname,
    :'db_deployment_worker_user'
)
FROM pg_catalog.pg_namespace namespace
WHERE namespace.nspname ~ '^pg_'
   OR namespace.nspname = 'information_schema'
\gexec
SELECT DISTINCT format(
    'REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA %I FROM %I',
    namespace.nspname,
    member.rolname
)
FROM pg_catalog.pg_namespace namespace
CROSS JOIN pg_catalog.pg_auth_members membership
JOIN pg_catalog.pg_roles parent ON parent.oid = membership.roleid
JOIN pg_catalog.pg_roles member ON member.oid = membership.member
WHERE (namespace.nspname ~ '^pg_' OR namespace.nspname = 'information_schema')
  AND parent.rolname IN (
      'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
      'mooncen_crawler_worker', 'mooncen_crawler_control',
      'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
      'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
      'mooncen_crawler_reporter', 'mooncen_crawler_observer',
      'mooncen_crawler_release_admin', 'mooncen_crawler_api',
      'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
  )
\gexec

-- Restore PUBLIC catalog ACLs to the PostgreSQL installation baseline rather
-- than blindly removing normal built-in access.  pg_init_privs records both
-- initdb and extension initial ACLs.  information_schema has no such rows, so
-- only its normal non-grantable PUBLIC SELECT is retained.
SELECT format(
    '%s %s ON %s %I.%I FROM PUBLIC',
    CASE
        WHEN namespace.nspname = 'information_schema'
             AND current_acl.privilege_type = 'SELECT'
          OR EXISTS (
              SELECT 1
              FROM pg_catalog.aclexplode(
                  COALESCE(
                      initial_acl.initprivs,
                      pg_catalog.acldefault(
                          CASE
                              WHEN relation.relkind = 'S' THEN 's'::"char"
                              ELSE 'r'::"char"
                          END,
                          relation.relowner
                      )
                  )
              ) baseline_acl
              WHERE baseline_acl.grantee = 0
                AND baseline_acl.privilege_type = current_acl.privilege_type
          )
        THEN 'REVOKE GRANT OPTION FOR'
        ELSE 'REVOKE'
    END,
    current_acl.privilege_type,
    CASE WHEN relation.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
    namespace.nspname,
    relation.relname
)
FROM pg_catalog.pg_class relation
JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
LEFT JOIN pg_catalog.pg_init_privs initial_acl
  ON initial_acl.classoid = 'pg_catalog.pg_class'::regclass
 AND initial_acl.objoid = relation.oid
 AND initial_acl.objsubid = 0
CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(
        relation.relacl,
        pg_catalog.acldefault(
            CASE
                WHEN relation.relkind = 'S' THEN 's'::"char"
                ELSE 'r'::"char"
            END,
            relation.relowner
        )
    )
) current_acl
WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f', 'S', 't')
  AND (namespace.nspname ~ '^pg_' OR namespace.nspname = 'information_schema')
  AND current_acl.grantee = 0
  AND (
      (
          namespace.nspname = 'information_schema'
          AND (
              current_acl.privilege_type <> 'SELECT'
              OR current_acl.is_grantable
          )
      )
      OR (
          namespace.nspname ~ '^pg_'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.aclexplode(
                  COALESCE(
                      initial_acl.initprivs,
                      pg_catalog.acldefault(
                          CASE
                              WHEN relation.relkind = 'S' THEN 's'::"char"
                              ELSE 'r'::"char"
                          END,
                          relation.relowner
                      )
                  )
              ) baseline_acl
              WHERE baseline_acl.grantee = 0
                AND baseline_acl.privilege_type = current_acl.privilege_type
                AND (
                    NOT current_acl.is_grantable
                    OR baseline_acl.is_grantable
                )
          )
      )
  )
\gexec
SELECT format(
    '%s %s (%I) ON TABLE %I.%I FROM PUBLIC',
    CASE
        WHEN EXISTS (
            SELECT 1
            FROM pg_catalog.aclexplode(
                COALESCE(
                    initial_acl.initprivs,
                    pg_catalog.acldefault('c', relation.relowner)
                )
            ) baseline_acl
            WHERE baseline_acl.grantee = 0
              AND baseline_acl.privilege_type = current_acl.privilege_type
        )
        THEN 'REVOKE GRANT OPTION FOR'
        ELSE 'REVOKE'
    END,
    current_acl.privilege_type,
    attribute.attname,
    namespace.nspname,
    relation.relname
)
FROM pg_catalog.pg_attribute attribute
JOIN pg_catalog.pg_class relation ON relation.oid = attribute.attrelid
JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
LEFT JOIN pg_catalog.pg_init_privs initial_acl
  ON initial_acl.classoid = 'pg_catalog.pg_class'::regclass
 AND initial_acl.objoid = relation.oid
 AND initial_acl.objsubid = attribute.attnum
CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(attribute.attacl, pg_catalog.acldefault('c', relation.relowner))
) current_acl
WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f', 't')
  AND attribute.attnum > 0
  AND NOT attribute.attisdropped
  AND (
      namespace.nspname ~ '^pg_'
      OR namespace.nspname = 'information_schema'
  )
  AND current_acl.grantee = 0
  AND NOT EXISTS (
      SELECT 1
      FROM pg_catalog.aclexplode(
          COALESCE(
              initial_acl.initprivs,
              pg_catalog.acldefault('c', relation.relowner)
          )
      ) baseline_acl
      WHERE baseline_acl.grantee = 0
        AND baseline_acl.privilege_type = current_acl.privilege_type
        AND (
            NOT current_acl.is_grantable
            OR baseline_acl.is_grantable
        )
  )
\gexec
SELECT format(
    '%s EXECUTE ON ROUTINE %I.%I(%s) FROM PUBLIC',
    CASE
        WHEN EXISTS (
            SELECT 1
            FROM pg_catalog.aclexplode(
                COALESCE(
                    initial_acl.initprivs,
                    pg_catalog.acldefault('f', procedure.proowner)
                )
            ) baseline_acl
            WHERE baseline_acl.grantee = 0
              AND baseline_acl.privilege_type = 'EXECUTE'
        )
        THEN 'REVOKE GRANT OPTION FOR'
        ELSE 'REVOKE'
    END,
    namespace.nspname,
    procedure.proname,
    pg_catalog.pg_get_function_identity_arguments(procedure.oid)
)
FROM pg_catalog.pg_proc procedure
JOIN pg_catalog.pg_namespace namespace ON namespace.oid = procedure.pronamespace
LEFT JOIN pg_catalog.pg_init_privs initial_acl
  ON initial_acl.classoid = 'pg_catalog.pg_proc'::regclass
 AND initial_acl.objoid = procedure.oid
 AND initial_acl.objsubid = 0
CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(procedure.proacl, pg_catalog.acldefault('f', procedure.proowner))
) current_acl
WHERE (
        namespace.nspname ~ '^pg_'
        OR namespace.nspname = 'information_schema'
      )
  AND current_acl.grantee = 0
  AND current_acl.privilege_type = 'EXECUTE'
  AND (
      (
          namespace.nspname = 'information_schema'
          AND current_acl.is_grantable
      )
      OR (
          namespace.nspname ~ '^pg_'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.aclexplode(
                  COALESCE(
                      initial_acl.initprivs,
                      pg_catalog.acldefault('f', procedure.proowner)
                  )
              ) baseline_acl
              WHERE baseline_acl.grantee = 0
                AND baseline_acl.privilege_type = 'EXECUTE'
                AND (
                    NOT current_acl.is_grantable
                    OR baseline_acl.is_grantable
                )
          )
      )
  )
\gexec

-- Parameter and foreign-data ACLs are independent PostgreSQL object classes.
-- Remove any PUBLIC/runtime access and any persistent worker user mapping.
SELECT format(
    'REVOKE ALL PRIVILEGES ON PARAMETER %I FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly, %I',
    parameter.parname,
    :'db_deployment_worker_user'
)
FROM pg_catalog.pg_parameter_acl parameter
\gexec
SELECT DISTINCT format(
    'REVOKE ALL PRIVILEGES ON PARAMETER %I FROM %I',
    parameter.parname,
    member.rolname
)
FROM pg_catalog.pg_parameter_acl parameter
CROSS JOIN pg_catalog.pg_auth_members membership
JOIN pg_catalog.pg_roles parent ON parent.oid = membership.roleid
JOIN pg_catalog.pg_roles member ON member.oid = membership.member
WHERE parent.rolname IN (
    'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
    'mooncen_crawler_worker', 'mooncen_crawler_control',
    'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
    'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
    'mooncen_crawler_reporter', 'mooncen_crawler_observer',
    'mooncen_crawler_release_admin', 'mooncen_crawler_api',
    'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON FOREIGN DATA WRAPPER %I FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly, %I',
    wrapper.fdwname,
    :'db_deployment_worker_user'
)
FROM pg_catalog.pg_foreign_data_wrapper wrapper
\gexec
SELECT DISTINCT format(
    'REVOKE ALL PRIVILEGES ON FOREIGN DATA WRAPPER %I FROM %I',
    wrapper.fdwname,
    member.rolname
)
FROM pg_catalog.pg_foreign_data_wrapper wrapper
CROSS JOIN pg_catalog.pg_auth_members membership
JOIN pg_catalog.pg_roles parent ON parent.oid = membership.roleid
JOIN pg_catalog.pg_roles member ON member.oid = membership.member
WHERE parent.rolname IN (
    'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
    'mooncen_crawler_worker', 'mooncen_crawler_control',
    'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
    'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
    'mooncen_crawler_reporter', 'mooncen_crawler_observer',
    'mooncen_crawler_release_admin', 'mooncen_crawler_api',
    'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON FOREIGN SERVER %I FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly, %I',
    server.srvname,
    :'db_deployment_worker_user'
)
FROM pg_catalog.pg_foreign_server server
\gexec
SELECT DISTINCT format(
    'REVOKE ALL PRIVILEGES ON FOREIGN SERVER %I FROM %I',
    server.srvname,
    member.rolname
)
FROM pg_catalog.pg_foreign_server server
CROSS JOIN pg_catalog.pg_auth_members membership
JOIN pg_catalog.pg_roles parent ON parent.oid = membership.roleid
JOIN pg_catalog.pg_roles member ON member.oid = membership.member
WHERE parent.rolname IN (
    'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
    'mooncen_crawler_worker', 'mooncen_crawler_control',
    'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
    'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
    'mooncen_crawler_reporter', 'mooncen_crawler_observer',
    'mooncen_crawler_release_admin', 'mooncen_crawler_api',
    'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
)
\gexec
SELECT format(
    'DROP USER MAPPING IF EXISTS FOR %s SERVER %I',
    CASE
        WHEN mapping.umuser = 0 THEN 'PUBLIC'
        ELSE format('%I', role.rolname)
    END,
    server.srvname
)
FROM pg_catalog.pg_user_mapping mapping
JOIN pg_catalog.pg_foreign_server server ON server.oid = mapping.umserver
LEFT JOIN pg_catalog.pg_roles role ON role.oid = mapping.umuser
WHERE mapping.umuser = 0
   OR role.rolname IN (
       :'db_deployment_worker_user',
       'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
       'mooncen_crawler_worker', 'mooncen_crawler_control',
       'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
       'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
       'mooncen_crawler_reporter', 'mooncen_crawler_observer',
       'mooncen_crawler_release_admin', 'mooncen_crawler_api',
       'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
   )
   OR EXISTS (
       SELECT 1
       FROM pg_catalog.pg_auth_members membership
       JOIN pg_catalog.pg_roles parent ON parent.oid = membership.roleid
       WHERE membership.member = mapping.umuser
         AND parent.rolname IN (
             'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
             'mooncen_crawler_worker', 'mooncen_crawler_control',
             'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
             'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
             'mooncen_crawler_reporter', 'mooncen_crawler_observer',
             'mooncen_crawler_release_admin', 'mooncen_crawler_api',
             'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
         )
   )
\gexec

-- The application does not use PostgreSQL large objects.  Quarantine every
-- historical object under the database owner, clear all runtime ACLs, remove
-- arbitrary historical pg_catalog routine grants, and close every LO
-- creation/server-file entry point before restoring the worker membership.
SELECT format(
    'ALTER LARGE OBJECT %s OWNER TO %I',
    large_object.oid,
    database_owner.rolname
)
FROM pg_largeobject_metadata large_object
JOIN pg_database database ON database.datname = current_database()
JOIN pg_roles database_owner ON database_owner.oid = database.datdba
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON LARGE OBJECT %s FROM PUBLIC, mooncen_api, mooncen_crawler, mooncen_deployment_worker, mooncen_crawler_worker, mooncen_crawler_control, mooncen_crawler_publisher, mooncen_crawler_finalizer, mooncen_crawler_approver, mooncen_crawler_release_approver, mooncen_crawler_reporter, mooncen_crawler_observer, mooncen_crawler_release_admin, mooncen_crawler_api, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly, %I',
    large_object.oid,
    :'db_deployment_worker_user'
)
FROM pg_largeobject_metadata large_object
\gexec
SELECT format(
    'REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',
    entry_point_signature
)
FROM (VALUES
    ('pg_catalog.lo_creat(integer)'),
    ('pg_catalog.lo_create(oid)'),
    ('pg_catalog.lo_from_bytea(oid,bytea)'),
    ('pg_catalog.lo_import(text)'),
    ('pg_catalog.lo_import(text,oid)'),
    ('pg_catalog.lo_export(oid,text)')
) AS entry_point(entry_point_signature)
\gexec

SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I',
    :'db_deployment_worker_user'
)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I',
    :'db_deployment_worker_user'
)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM %I',
    :'db_deployment_worker_user'
)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I',
    :'db_deployment_worker_user'
)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA crawl_staging FROM %I',
    :'db_deployment_worker_user'
)
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec
-- A table-level REVOKE does not clear historical pg_attribute ACLs granted
-- directly to the LOGIN.  Converge every column before adding the reviewed
-- group membership so no latent REFERENCES/UPDATE grant survives.
SELECT format(
    'REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %I',
    column_name,
    table_schema,
    table_name,
    :'db_deployment_worker_user'
)
FROM information_schema.column_privileges
WHERE table_schema IN ('public', 'crawl_staging')
  AND grantee = :'db_deployment_worker_user'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA crawl_staging FROM %I',
    :'db_deployment_worker_user'
)
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA crawl_staging FROM %I',
    :'db_deployment_worker_user'
)
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON %s %I.%I(%s) FROM %I',
    CASE procedure.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
    namespace.nspname,
    procedure.proname,
    pg_get_function_identity_arguments(procedure.oid),
    :'db_deployment_worker_user'
)
FROM pg_proc procedure
JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
WHERE namespace.nspname IN ('public', 'crawl_staging')
  AND procedure.prokind IN ('f', 'p', 'a', 'w')
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON SCHEMA crawl_staging FROM %I',
    :'db_deployment_worker_user'
)
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I',
    current_database(),
    :'db_deployment_worker_user'
)
\gexec

SELECT format(
    'GRANT mooncen_deployment_worker TO %I',
    :'db_deployment_worker_user'
)
\gexec
SELECT format(
    'GRANT CONNECT ON DATABASE %I TO %I',
    current_database(),
    :'db_deployment_worker_user'
)
\gexec

COMMIT;
