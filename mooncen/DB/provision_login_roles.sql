-- Create/update runtime LOGIN roles after DB/roles.sql has created the NOLOGIN
-- permission groups. Passwords are supplied as base64 psql variables over
-- stdin by deploy/ubuntu/setup_project.sh, never embedded in this repository.
-- Required variables:
--   db_api_user, db_api_password_b64
--   db_crawler_user, db_crawler_password_b64
--   db_applier_user, db_applier_password_b64
--   db_ai_user, db_ai_password_b64
--   db_check_user, db_check_password_b64
--   db_backup_user, db_backup_password_b64

\set ON_ERROR_STOP on

-- Password, direct-ACL, and membership convergence is one atomic change.  A
-- controller interruption must preserve the previous working memberships
-- instead of exposing the gap between the REVOKE below and the final GRANTs.
BEGIN;

-- Container services authenticate over the host Unix socket with SCRAM.
-- Pin the verifier format even if a cluster-wide operator setting changes;
-- the deployment helper refuses to publish local SCRAM HBA rules unless all
-- three container roles already carry SCRAM verifiers.
SET LOCAL password_encryption = 'scram-sha-256';

SELECT format('CREATE ROLE %I LOGIN', role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name)
\gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'db_api_user',
    convert_from(decode(:'db_api_password_b64', 'base64'), 'UTF8')
) \gexec
SELECT format(
    'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'db_crawler_user',
    convert_from(decode(:'db_crawler_password_b64', 'base64'), 'UTF8')
) \gexec
SELECT format(
    'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'db_applier_user',
    convert_from(decode(:'db_applier_password_b64', 'base64'), 'UTF8')
) \gexec
SELECT format(
    'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'db_ai_user',
    convert_from(decode(:'db_ai_password_b64', 'base64'), 'UTF8')
) \gexec
SELECT format(
    'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'db_check_user',
    convert_from(decode(:'db_check_password_b64', 'base64'), 'UTF8')
) \gexec
SELECT format(
    'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'db_backup_user',
    convert_from(decode(:'db_backup_password_b64', 'base64'), 'UTF8')
) \gexec

-- Remove every stale membership (including operator/catalog groups) before
-- assigning the single intended permission group.
SELECT format(
    'REVOKE %I FROM %I',
    parent.rolname,
    member.rolname
)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname IN (
    :'db_api_user', :'db_crawler_user', :'db_applier_user',
    :'db_ai_user', :'db_check_user', :'db_backup_user'
)
\gexec

-- LOGIN defaults and PostgreSQL system-object ACLs survive an ordinary role
-- attribute reset.  Converge them under the bootstrap superuser before the
-- reviewed application memberships are restored.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = current_user AND rolsuper
    ) THEN
        RAISE EXCEPTION 'runtime LOGIN boundary requires a PostgreSQL superuser';
    END IF;
END
$$;
SELECT format('ALTER ROLE %I RESET ALL', role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
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
    :'db_api_user', :'db_crawler_user', :'db_applier_user',
    :'db_ai_user', :'db_check_user', :'db_backup_user'
)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I',
    namespace.nspname,
    requested.role_name
)
FROM pg_catalog.pg_namespace namespace
CROSS JOIN (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
WHERE namespace.nspname ~ '^pg_'
   OR namespace.nspname = 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM %I',
    namespace.nspname,
    requested.role_name
)
FROM pg_catalog.pg_namespace namespace
CROSS JOIN (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
WHERE namespace.nspname ~ '^pg_'
   OR namespace.nspname = 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM %I',
    namespace.nspname,
    requested.role_name
)
FROM pg_catalog.pg_namespace namespace
CROSS JOIN (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
WHERE namespace.nspname ~ '^pg_'
   OR namespace.nspname = 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA %I FROM %I',
    namespace.nspname,
    requested.role_name
)
FROM pg_catalog.pg_namespace namespace
CROSS JOIN (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
WHERE namespace.nspname ~ '^pg_'
   OR namespace.nspname = 'information_schema'
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
  AND grantee.rolname IN (
      :'db_api_user', :'db_crawler_user', :'db_applier_user',
      :'db_ai_user', :'db_check_user', :'db_backup_user'
  )
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON PARAMETER %I FROM %I',
    parameter.parname,
    requested.role_name
)
FROM pg_catalog.pg_parameter_acl parameter
CROSS JOIN (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON FOREIGN DATA WRAPPER %I FROM %I',
    wrapper.fdwname,
    requested.role_name
)
FROM pg_catalog.pg_foreign_data_wrapper wrapper
CROSS JOIN (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON FOREIGN SERVER %I FROM %I',
    server.srvname,
    requested.role_name
)
FROM pg_catalog.pg_foreign_server server
CROSS JOIN (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec
SELECT format(
    'DROP USER MAPPING IF EXISTS FOR %I SERVER %I',
    role.rolname,
    server.srvname
)
FROM pg_catalog.pg_user_mapping mapping
JOIN pg_catalog.pg_foreign_server server ON server.oid = mapping.umserver
JOIN pg_catalog.pg_roles role ON role.oid = mapping.umuser
WHERE role.rolname IN (
    :'db_api_user', :'db_crawler_user', :'db_applier_user',
    :'db_ai_user', :'db_check_user', :'db_backup_user'
)
\gexec

-- Converge large-object ownership, direct ACLs, and creation entry points for
-- every runtime LOGIN.  The application does not use PostgreSQL large objects;
-- only the database owner retains the ability to create or own them.
SELECT format(
    'ALTER LARGE OBJECT %s OWNER TO %I',
    large_object.oid,
    database_owner.rolname
)
FROM pg_largeobject_metadata large_object
JOIN pg_roles login ON login.oid = large_object.lomowner
JOIN pg_database database ON database.datname = current_database()
JOIN pg_roles database_owner ON database_owner.oid = database.datdba
WHERE login.rolname IN (
    :'db_api_user', :'db_crawler_user', :'db_applier_user',
    :'db_ai_user', :'db_check_user', :'db_backup_user'
)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON LARGE OBJECT %s FROM %I',
    large_object.oid,
    requested.role_name
)
FROM pg_largeobject_metadata large_object
CROSS JOIN (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA pg_catalog FROM %I',
    requested.role_name
)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec

SELECT format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I', role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I', role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I', role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM %I', role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec

SELECT format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA crawl_staging FROM %I', role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA crawl_staging FROM %I', role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON SCHEMA crawl_staging FROM %I', role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA crawl_staging FROM %I', role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec

SELECT format('GRANT mooncen_api TO %I', :'db_api_user') \gexec
SELECT format('GRANT mooncen_crawler TO %I', :'db_crawler_user') \gexec
SELECT format('GRANT mooncen_applier TO %I', :'db_applier_user') \gexec
SELECT format('GRANT mooncen_ai TO %I', :'db_ai_user') \gexec
SELECT format('GRANT mooncen_check TO %I', :'db_check_user') \gexec
SELECT format('GRANT mooncen_readonly TO %I', :'db_backup_user') \gexec

SELECT format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I', current_database(), role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), role_name)
FROM (VALUES
    (:'db_api_user'),
    (:'db_crawler_user'),
    (:'db_applier_user'),
    (:'db_ai_user'),
    (:'db_check_user'),
    (:'db_backup_user')
) AS requested(role_name)
\gexec

COMMIT;
