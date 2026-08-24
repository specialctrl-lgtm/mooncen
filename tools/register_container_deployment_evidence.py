#!/usr/bin/env python3
"""Append one fixed-root Docker release and PASS receipt to Ops evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import psycopg2
from sqlalchemy import URL, create_engine, text

from backend.ops.service import (
    register_container_release_evidence,
    register_container_validation_evidence,
)
from deploy.docker.release_manifest import (
    ManifestError,
    bind_promotion_evidence,
    load_json_evidence,
)
from deploy.docker.verify_release_bundle import VerificationError, verify_release_artifacts
from ops_agent.container_deployment import (
    container_release_directory_metadata_ready,
    container_release_file_metadata_ready,
    container_transport_service_boundary_ready,
)
from ops_agent.deployment_worker import queue_database_config


SOURCE_TREE_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
EXPECTED_FILES = frozenset(
    {"release.json", "validation.json", "images.tar", "compose.production.yaml"}
)
FIXED_WORKER_ENV = Path("/etc/mooncen-an2p/deployment-worker.env")
REQUIRED_WORKER_ENV_NAMES = frozenset(
    {
        "ENVIRONMENT",
        "DB_OWNER_USER",
        "DB_SSLMODE",
        "DB_CONNECT_TIMEOUT",
        "DB_STATEMENT_TIMEOUT_MS",
        "DB_LOCK_TIMEOUT_MS",
        "OPS_DEPLOY_QUEUE_DB_HOST",
        "OPS_DEPLOY_QUEUE_DB_PORT",
        "OPS_DEPLOY_QUEUE_DB_NAME",
        "OPS_DEPLOY_QUEUE_DB_USER",
        "OPS_DEPLOY_QUEUE_DB_PASSWORD",
        "OPS_DEPLOY_AGENT_EXCLUSIVE",
        "OPS_DEPLOY_REQUIRED_AGENT_HOSTNAME",
        "OPS_CONTAINER_DEV_TARGET_IDENTITY",
        "OPS_CONTAINER_RELEASE_ROOT",
    }
)
ENV_NAME_PATTERN = re.compile(r"\A[A-Z][A-Z0-9_]{1,63}\Z")
DEDICATED_WORKER_LOGIN = "mooncen_deployment_worker_login"
MAX_OTHER_DATABASES = 32
MAX_DATABASE_NAME_BYTES = 63
MAX_PASSWORD_INPUT_BYTES = 512
DATABASE_NAME_PATTERN = re.compile(r"\A[a-z_][a-z0-9_]{0,62}\Z")
DATABASE_PASSWORD_PATTERN = re.compile(r"\A[A-Za-z0-9._!@%+=,:/-]{16,256}\Z")
DATABASE_BOUNDARY_FIELDS = (
    "current_user_matches",
    "session_user_matches",
    "database_matches",
    "tls_active",
    "login_flags_safe",
    "permission_group_flags_safe",
    "role_settings_safe",
    "direct_membership_count_exact",
    "direct_membership_options_safe",
    "permission_group_has_no_parent",
    "worker_membership_effective",
    "api_membership_absent",
    "crawler_membership_absent",
    "database_connect_allowed",
    "database_create_denied",
    "database_temporary_denied",
    "schema_usage_allowed",
    "schema_create_denied",
    "unrelated_schema_privileges_absent",
    "system_schema_inventory_exact",
    "system_schema_privileges_exact",
    "release_select_allowed",
    "release_insert_allowed",
    "release_update_denied",
    "release_delete_denied",
    "receipt_select_allowed",
    "receipt_insert_allowed",
    "receipt_update_denied",
    "receipt_delete_denied",
    "approval_select_allowed",
    "approval_insert_denied",
    "approval_update_denied",
    "approval_delete_denied",
    "agent_select_allowed",
    "agent_insert_allowed",
    "agent_update_allowed",
    "agent_delete_denied",
    "agent_other_privileges_denied",
    "job_select_allowed",
    "job_insert_denied",
    "job_delete_denied",
    "job_update_columns_exact",
    "job_lease_token_update_allowed",
    "job_lease_epoch_update_allowed",
    "job_leased_until_update_allowed",
    "job_type_update_denied",
    "job_parameters_update_denied",
    "deployment_select_allowed",
    "deployment_insert_denied",
    "deployment_delete_denied",
    "deployment_update_columns_exact",
    "deployment_runtime_generation_update_allowed",
    "deployment_mode_update_denied",
    "job_log_select_allowed",
    "job_log_insert_allowed",
    "job_log_update_denied",
    "job_log_delete_denied",
    "job_log_other_privileges_denied",
    "job_log_sequence_usage_allowed",
    "job_log_sequence_select_allowed",
    "job_log_sequence_update_denied",
    "lease_sequence_usage_allowed",
    "lease_sequence_select_allowed",
    "lease_sequence_update_denied",
    "extension_inventory_exact",
    "extension_relation_write_privileges_absent",
    "extension_sequence_privileges_absent",
    "unrelated_table_privileges_absent",
    "unrelated_sequence_privileges_absent",
    "system_relation_privileges_exact",
    "user_defined_system_objects_absent",
    "large_objects_absent",
    "large_object_entry_points_denied",
    "pg_catalog_routine_privileges_exact",
    "parameter_privileges_absent",
    "foreign_data_access_denied",
    "application_routine_execute_absent",
)

DATABASE_BOUNDARY_SQL = """
WITH managed_system_principals AS MATERIALIZED (
    SELECT role.oid
    FROM pg_catalog.pg_roles role
    WHERE role.rolname IN (
        'mooncen_api', 'mooncen_crawler', 'mooncen_deployment_worker',
        'mooncen_crawler_worker', 'mooncen_crawler_control',
        'mooncen_crawler_publisher', 'mooncen_crawler_finalizer',
        'mooncen_crawler_approver', 'mooncen_crawler_release_approver',
        'mooncen_crawler_reporter', 'mooncen_crawler_observer',
        'mooncen_crawler_release_admin', 'mooncen_crawler_api',
        'mooncen_applier', 'mooncen_ai', 'mooncen_check', 'mooncen_readonly'
    )
    UNION
    SELECT member.oid
    FROM pg_catalog.pg_auth_members membership
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
)
SELECT
    current_user = :expected_user AS current_user_matches,
    session_user = :expected_user AS session_user_matches,
    current_database() = :expected_database AS database_matches,
    COALESCE(
        (SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid = pg_backend_pid()),
        FALSE
    ) AS tls_active,
    EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles login
        WHERE login.rolname = current_user
          AND login.rolcanlogin
          AND login.rolinherit
          AND NOT login.rolsuper
          AND NOT login.rolcreatedb
          AND NOT login.rolcreaterole
          AND NOT login.rolreplication
          AND NOT login.rolbypassrls
    ) AS login_flags_safe,
    EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles permission_group
        WHERE permission_group.rolname = 'mooncen_deployment_worker'
          AND NOT permission_group.rolcanlogin
          AND permission_group.rolinherit
          AND NOT permission_group.rolsuper
          AND NOT permission_group.rolcreatedb
          AND NOT permission_group.rolcreaterole
          AND NOT permission_group.rolreplication
          AND NOT permission_group.rolbypassrls
    ) AS permission_group_flags_safe,
    current_setting('session_replication_role') = 'origin'
      AND current_setting('search_path') = 'pg_catalog,public'
      AND EXISTS (
          SELECT 1
          FROM pg_catalog.pg_roles login
          WHERE login.rolname = current_user
            AND login.rolconfig IS NULL
      )
      AND EXISTS (
          SELECT 1
          FROM pg_catalog.pg_roles permission_group
          WHERE permission_group.rolname = 'mooncen_deployment_worker'
            AND permission_group.rolconfig IS NULL
      )
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_db_role_setting setting
          WHERE setting.setrole IN (
              SELECT role.oid
              FROM pg_catalog.pg_roles role
              WHERE role.rolname IN (
                  current_user,
                  'mooncen_deployment_worker'
              )
          )
      ) AS role_settings_safe,
    1 = (
        SELECT COUNT(*)
        FROM pg_catalog.pg_auth_members membership
        JOIN pg_catalog.pg_roles member ON member.oid = membership.member
        WHERE member.rolname = current_user
    ) AS direct_membership_count_exact,
    EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members membership
        JOIN pg_catalog.pg_roles parent ON parent.oid = membership.roleid
        JOIN pg_catalog.pg_roles member ON member.oid = membership.member
        WHERE member.rolname = current_user
          AND parent.rolname = 'mooncen_deployment_worker'
          AND NOT membership.admin_option
          AND membership.inherit_option
          AND membership.set_option
    ) AS direct_membership_options_safe,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members membership
        JOIN pg_catalog.pg_roles member ON member.oid = membership.member
        WHERE member.rolname = 'mooncen_deployment_worker'
    ) AS permission_group_has_no_parent,
    pg_has_role(current_user, 'mooncen_deployment_worker', 'member')
        AS worker_membership_effective,
    NOT pg_has_role(current_user, 'mooncen_api', 'member')
        AS api_membership_absent,
    NOT pg_has_role(current_user, 'mooncen_crawler', 'member')
        AS crawler_membership_absent,
    has_database_privilege(current_user, current_database(), 'CONNECT')
        AS database_connect_allowed,
    NOT has_database_privilege(current_user, current_database(), 'CREATE')
        AS database_create_denied,
    NOT has_database_privilege(current_user, current_database(), 'TEMPORARY')
        AS database_temporary_denied,
    has_schema_privilege(current_user, 'public', 'USAGE')
        AS schema_usage_allowed,
    NOT has_schema_privilege(current_user, 'public', 'CREATE')
        AS schema_create_denied,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace namespace
        WHERE namespace.nspname <> 'public'
          AND namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND (
              has_schema_privilege(current_user, namespace.oid, 'CREATE')
              OR (
                  NOT EXISTS (
                      SELECT 1
                      FROM pg_catalog.pg_depend dependency
                      WHERE dependency.classid = 'pg_catalog.pg_namespace'::regclass
                        AND dependency.objid = namespace.oid
                        AND dependency.deptype = 'e'
                  )
                  AND has_schema_privilege(current_user, namespace.oid, 'USAGE')
              )
          )
    ) AS unrelated_schema_privileges_absent,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace namespace
        WHERE namespace.nspname ~ '^pg_'
          AND namespace.nspname NOT IN ('pg_catalog', 'pg_toast')
          AND namespace.nspname !~ '^pg_(toast_)?temp_[0-9]+$'
    ) AS system_schema_inventory_exact,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace namespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
                namespace.nspacl,
                pg_catalog.acldefault('n', namespace.nspowner)
            )
        ) current_acl
        WHERE namespace.nspname IN (
                'pg_catalog',
                'pg_toast',
                'information_schema'
              )
          AND (
              namespace.nspowner IN (
                  SELECT principal.oid FROM managed_system_principals principal
              )
              OR current_acl.grantee IN (
                  SELECT principal.oid FROM managed_system_principals principal
              )
          )
    )
      AND NOT EXISTS (
          SELECT 1
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
          WHERE namespace.nspname IN (
                  'pg_catalog',
                  'pg_toast',
                  'information_schema'
                )
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
      ) AS system_schema_privileges_exact,
    has_table_privilege(current_user, 'public.ops_container_releases', 'SELECT')
        AS release_select_allowed,
    has_table_privilege(current_user, 'public.ops_container_releases', 'INSERT')
        AS release_insert_allowed,
    NOT has_any_column_privilege(
        current_user,
        'public.ops_container_releases',
        'UPDATE'
    )
        AS release_update_denied,
    NOT has_table_privilege(current_user, 'public.ops_container_releases', 'DELETE')
      AND NOT has_table_privilege(current_user, 'public.ops_container_releases', 'TRUNCATE')
      AND NOT has_table_privilege(current_user, 'public.ops_container_releases', 'TRIGGER')
      AND NOT has_any_column_privilege(
          current_user,
          'public.ops_container_releases',
          'REFERENCES'
      )
        AS release_delete_denied,
    has_table_privilege(current_user, 'public.ops_container_validation_receipts', 'SELECT')
        AS receipt_select_allowed,
    has_table_privilege(current_user, 'public.ops_container_validation_receipts', 'INSERT')
        AS receipt_insert_allowed,
    NOT has_any_column_privilege(
        current_user,
        'public.ops_container_validation_receipts',
        'UPDATE'
    )
        AS receipt_update_denied,
    NOT has_table_privilege(current_user, 'public.ops_container_validation_receipts', 'DELETE')
      AND NOT has_table_privilege(
          current_user,
          'public.ops_container_validation_receipts',
          'TRUNCATE'
      )
      AND NOT has_table_privilege(
          current_user,
          'public.ops_container_validation_receipts',
          'TRIGGER'
      )
      AND NOT has_any_column_privilege(
          current_user,
          'public.ops_container_validation_receipts',
          'REFERENCES'
      )
        AS receipt_delete_denied,
    has_table_privilege(current_user, 'public.ops_container_approval_evidence', 'SELECT')
        AS approval_select_allowed,
    NOT has_any_column_privilege(
        current_user,
        'public.ops_container_approval_evidence',
        'INSERT'
    )
        AS approval_insert_denied,
    NOT has_any_column_privilege(
        current_user,
        'public.ops_container_approval_evidence',
        'UPDATE'
    )
        AS approval_update_denied,
    NOT has_table_privilege(current_user, 'public.ops_container_approval_evidence', 'DELETE')
      AND NOT has_table_privilege(
          current_user,
          'public.ops_container_approval_evidence',
          'TRUNCATE'
      )
      AND NOT has_table_privilege(
          current_user,
          'public.ops_container_approval_evidence',
          'TRIGGER'
      )
      AND NOT has_any_column_privilege(
          current_user,
          'public.ops_container_approval_evidence',
          'REFERENCES'
      )
        AS approval_delete_denied,
    has_table_privilege(current_user, 'public.ops_agents', 'SELECT')
        AS agent_select_allowed,
    has_table_privilege(current_user, 'public.ops_agents', 'INSERT')
        AS agent_insert_allowed,
    has_table_privilege(current_user, 'public.ops_agents', 'UPDATE')
        AS agent_update_allowed,
    NOT has_table_privilege(current_user, 'public.ops_agents', 'DELETE')
        AS agent_delete_denied,
    NOT has_table_privilege(current_user, 'public.ops_agents', 'TRUNCATE')
      AND NOT has_any_column_privilege(current_user, 'public.ops_agents', 'REFERENCES')
      AND NOT has_table_privilege(current_user, 'public.ops_agents', 'TRIGGER')
        AS agent_other_privileges_denied,
    has_table_privilege(current_user, 'public.ops_jobs', 'SELECT')
        AS job_select_allowed,
    NOT has_any_column_privilege(current_user, 'public.ops_jobs', 'INSERT')
        AS job_insert_denied,
    NOT has_table_privilege(current_user, 'public.ops_jobs', 'DELETE')
      AND NOT has_table_privilege(current_user, 'public.ops_jobs', 'TRUNCATE')
      AND NOT has_table_privilege(current_user, 'public.ops_jobs', 'TRIGGER')
      AND NOT has_any_column_privilege(current_user, 'public.ops_jobs', 'REFERENCES')
        AS job_delete_denied,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_attribute attribute
        WHERE attribute.attrelid = 'public.ops_jobs'::regclass
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND has_column_privilege(
              current_user,
              attribute.attrelid,
              attribute.attnum,
              'UPDATE'
          ) IS DISTINCT FROM (
              attribute.attname::text = ANY (ARRAY[
                  'status', 'agent_id', 'assigned_at', 'started_at',
                  'heartbeat_at', 'progress', 'result', 'error_code',
                  'error_message', 'cancel_requested_at', 'finished_at',
                  'updated_at', 'lease_token', 'lease_epoch', 'leased_until'
              ])
          )
    ) AS job_update_columns_exact,
    has_column_privilege(current_user, 'public.ops_jobs', 'lease_token', 'UPDATE')
        AS job_lease_token_update_allowed,
    has_column_privilege(current_user, 'public.ops_jobs', 'lease_epoch', 'UPDATE')
        AS job_lease_epoch_update_allowed,
    has_column_privilege(current_user, 'public.ops_jobs', 'leased_until', 'UPDATE')
        AS job_leased_until_update_allowed,
    NOT has_column_privilege(current_user, 'public.ops_jobs', 'job_type', 'UPDATE')
        AS job_type_update_denied,
    NOT has_column_privilege(current_user, 'public.ops_jobs', 'parameters', 'UPDATE')
        AS job_parameters_update_denied,
    has_table_privilege(current_user, 'public.ops_deployments', 'SELECT')
        AS deployment_select_allowed,
    NOT has_any_column_privilege(
        current_user,
        'public.ops_deployments',
        'INSERT'
    ) AS deployment_insert_denied,
    NOT has_table_privilege(current_user, 'public.ops_deployments', 'DELETE')
      AND NOT has_table_privilege(current_user, 'public.ops_deployments', 'TRUNCATE')
      AND NOT has_table_privilege(current_user, 'public.ops_deployments', 'TRIGGER')
      AND NOT has_any_column_privilege(
          current_user,
          'public.ops_deployments',
          'REFERENCES'
      )
        AS deployment_delete_denied,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_attribute attribute
        WHERE attribute.attrelid = 'public.ops_deployments'::regclass
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND has_column_privilege(
              current_user,
              attribute.attrelid,
              attribute.attnum,
              'UPDATE'
          ) IS DISTINCT FROM (
              attribute.attname::text = ANY (ARRAY[
                  'target_version', 'target_commit', 'deployment_status',
                  'started_at', 'finished_at', 'runtime_generation',
                  'activated_release_digest',
                  'runtime_previous_release_digest',
                  'controller_state_sha256', 'runtime_target_kind',
                  'runtime_native_baseline_identity'
              ])
          )
    ) AS deployment_update_columns_exact,
    has_column_privilege(
        current_user,
        'public.ops_deployments',
        'runtime_generation',
        'UPDATE'
    ) AS deployment_runtime_generation_update_allowed,
    NOT has_column_privilege(
        current_user,
        'public.ops_deployments',
        'deployment_mode',
        'UPDATE'
    ) AS deployment_mode_update_denied,
    has_table_privilege(current_user, 'public.ops_job_logs', 'SELECT')
        AS job_log_select_allowed,
    has_table_privilege(current_user, 'public.ops_job_logs', 'INSERT')
        AS job_log_insert_allowed,
    NOT has_any_column_privilege(current_user, 'public.ops_job_logs', 'UPDATE')
        AS job_log_update_denied,
    NOT has_table_privilege(current_user, 'public.ops_job_logs', 'DELETE')
        AS job_log_delete_denied,
    NOT has_table_privilege(current_user, 'public.ops_job_logs', 'TRUNCATE')
      AND NOT has_any_column_privilege(current_user, 'public.ops_job_logs', 'REFERENCES')
      AND NOT has_table_privilege(current_user, 'public.ops_job_logs', 'TRIGGER')
        AS job_log_other_privileges_denied,
    has_sequence_privilege(current_user, 'public.ops_job_logs_id_seq', 'USAGE')
        AS job_log_sequence_usage_allowed,
    has_sequence_privilege(current_user, 'public.ops_job_logs_id_seq', 'SELECT')
        AS job_log_sequence_select_allowed,
    NOT has_sequence_privilege(current_user, 'public.ops_job_logs_id_seq', 'UPDATE')
        AS job_log_sequence_update_denied,
    has_sequence_privilege(
        current_user,
        'public.ops_container_deployment_lease_epoch_seq',
        'USAGE'
    ) AS lease_sequence_usage_allowed,
    has_sequence_privilege(
        current_user,
        'public.ops_container_deployment_lease_epoch_seq',
        'SELECT'
    ) AS lease_sequence_select_allowed,
    NOT has_sequence_privilege(
        current_user,
        'public.ops_container_deployment_lease_epoch_seq',
        'UPDATE'
    ) AS lease_sequence_update_denied,
    COALESCE(
        (
            SELECT array_agg(
                extension.extname || '|' || extension.extversion || '|' ||
                namespace.nspname || '|' || owner.rolname
                ORDER BY extension.extname
            )
            FROM pg_catalog.pg_extension extension
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = extension.extnamespace
            JOIN pg_catalog.pg_roles owner ON owner.oid = extension.extowner
        ),
        ARRAY[]::text[]
    ) = ARRAY[
        'pg_trgm|1.6|public|postgres',
        'pgcrypto|1.3|public|postgres',
        'plpgsql|1.0|pg_catalog|postgres',
        'postgis|3.4.2|public|postgres',
        'uuid-ossp|1.1|public|postgres'
    ]::text[] AS extension_inventory_exact,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class relation
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend dependency
              WHERE dependency.classid = 'pg_catalog.pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
          AND (
              has_any_column_privilege(current_user, relation.oid, 'INSERT')
              OR has_any_column_privilege(current_user, relation.oid, 'UPDATE')
              OR has_any_column_privilege(current_user, relation.oid, 'REFERENCES')
              OR has_table_privilege(current_user, relation.oid, 'DELETE')
              OR has_table_privilege(current_user, relation.oid, 'TRUNCATE')
              OR has_table_privilege(current_user, relation.oid, 'TRIGGER')
          )
    ) AS extension_relation_write_privileges_absent,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class sequence
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = sequence.relnamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND sequence.relkind = 'S'
          AND EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend dependency
              WHERE dependency.classid = 'pg_catalog.pg_class'::regclass
                AND dependency.objid = sequence.oid
                AND dependency.deptype = 'e'
          )
          AND (
              has_sequence_privilege(current_user, sequence.oid, 'USAGE')
              OR has_sequence_privilege(current_user, sequence.oid, 'SELECT')
              OR has_sequence_privilege(current_user, sequence.oid, 'UPDATE')
          )
    ) AS extension_sequence_privileges_absent,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class relation
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend dependency
              WHERE dependency.classid = 'pg_catalog.pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
          AND (
              namespace.nspname <> 'public'
              OR relation.relname::text <> ALL (ARRAY[
                  'ops_container_releases',
                  'ops_container_validation_receipts',
                  'ops_container_approval_evidence',
                  'ops_agents',
                  'ops_jobs',
                  'ops_deployments',
                  'ops_job_logs'
              ])
          )
          AND (
              has_any_column_privilege(current_user, relation.oid, 'SELECT')
              OR has_any_column_privilege(current_user, relation.oid, 'INSERT')
              OR has_any_column_privilege(current_user, relation.oid, 'UPDATE')
              OR has_any_column_privilege(current_user, relation.oid, 'REFERENCES')
              OR has_table_privilege(current_user, relation.oid, 'DELETE')
              OR has_table_privilege(current_user, relation.oid, 'TRUNCATE')
              OR has_table_privilege(current_user, relation.oid, 'TRIGGER')
          )
    ) AS unrelated_table_privileges_absent,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class sequence
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = sequence.relnamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND sequence.relkind = 'S'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend dependency
              WHERE dependency.classid = 'pg_catalog.pg_class'::regclass
                AND dependency.objid = sequence.oid
                AND dependency.deptype = 'e'
          )
          AND (
              namespace.nspname <> 'public'
              OR sequence.relname::text <> ALL (ARRAY[
                  'ops_job_logs_id_seq',
                  'ops_container_deployment_lease_epoch_seq'
              ])
          )
          AND (
              has_sequence_privilege(current_user, sequence.oid, 'USAGE')
              OR has_sequence_privilege(current_user, sequence.oid, 'SELECT')
              OR has_sequence_privilege(current_user, sequence.oid, 'UPDATE')
          )
    ) AS unrelated_sequence_privileges_absent,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class relation
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = relation.relnamespace
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
          AND (
              namespace.nspname ~ '^pg_'
              OR namespace.nspname = 'information_schema'
          )
          AND (
              relation.relowner IN (
                  SELECT principal.oid FROM managed_system_principals principal
              )
              OR current_acl.grantee IN (
                  SELECT principal.oid FROM managed_system_principals principal
              )
          )
    )
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_class relation
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid = relation.relnamespace
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
            AND (
                namespace.nspname ~ '^pg_'
                OR namespace.nspname = 'information_schema'
            )
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
      )
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_attribute attribute
          JOIN pg_catalog.pg_class relation ON relation.oid = attribute.attrelid
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid = relation.relnamespace
          CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(
                  attribute.attacl,
                  pg_catalog.acldefault('c', relation.relowner)
              )
          ) current_acl
          WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f', 't')
            AND attribute.attnum > 0
            AND NOT attribute.attisdropped
            AND (
                namespace.nspname ~ '^pg_'
                OR namespace.nspname = 'information_schema'
            )
            AND current_acl.grantee IN (
                SELECT principal.oid FROM managed_system_principals principal
            )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_attribute attribute
          JOIN pg_catalog.pg_class relation ON relation.oid = attribute.attrelid
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid = relation.relnamespace
          LEFT JOIN pg_catalog.pg_init_privs initial_acl
            ON initial_acl.classoid = 'pg_catalog.pg_class'::regclass
           AND initial_acl.objoid = relation.oid
           AND initial_acl.objsubid = attribute.attnum
          CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(
                  attribute.attacl,
                  pg_catalog.acldefault('c', relation.relowner)
              )
          ) current_acl
          WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f', 't')
            AND attribute.attnum > 0
            AND NOT attribute.attisdropped
            AND (
                namespace.nspname ~ '^pg_'
                OR namespace.nspname = 'information_schema'
            )
            AND current_acl.grantee = 0
            AND (
                namespace.nspname = 'information_schema'
                OR NOT EXISTS (
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
            )
      ) AS system_relation_privileges_exact,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc procedure
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = procedure.pronamespace
        WHERE (
                namespace.nspname ~ '^pg_'
                OR namespace.nspname = 'information_schema'
              )
          AND namespace.nspname !~ '^pg_(toast_)?temp_[0-9]+$'
          AND procedure.oid >= 16384
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend dependency
              WHERE dependency.classid = 'pg_catalog.pg_proc'::regclass
                AND dependency.objid = procedure.oid
                AND dependency.deptype = 'e'
          )
        UNION ALL
        SELECT 1
        FROM pg_catalog.pg_class relation
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname IN ('pg_catalog', 'information_schema')
          AND relation.oid >= 16384
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend dependency
              WHERE dependency.classid = 'pg_catalog.pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
    ) AS user_defined_system_objects_absent,
    NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_largeobject_metadata
    ) AS large_objects_absent,
    NOT has_function_privilege(
          current_user,
          'pg_catalog.lo_creat(integer)',
          'EXECUTE'
      )
      AND NOT has_function_privilege(
          current_user,
          'pg_catalog.lo_create(oid)',
          'EXECUTE'
      )
      AND NOT has_function_privilege(
          current_user,
          'pg_catalog.lo_from_bytea(oid,bytea)',
          'EXECUTE'
      )
      AND NOT has_function_privilege(
          current_user,
          'pg_catalog.lo_import(text)',
          'EXECUTE'
      )
      AND NOT has_function_privilege(
          current_user,
          'pg_catalog.lo_import(text,oid)',
          'EXECUTE'
      )
      AND NOT has_function_privilege(
          current_user,
          'pg_catalog.lo_export(oid,text)',
          'EXECUTE'
      ) AS large_object_entry_points_denied,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc procedure
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = procedure.pronamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
                procedure.proacl,
                pg_catalog.acldefault('f', procedure.proowner)
            )
        ) current_acl
        WHERE (
                namespace.nspname ~ '^pg_'
                OR namespace.nspname = 'information_schema'
              )
          AND current_acl.privilege_type = 'EXECUTE'
          AND (
              procedure.proowner IN (
                  SELECT principal.oid FROM managed_system_principals principal
              )
              OR current_acl.grantee IN (
                  SELECT principal.oid FROM managed_system_principals principal
              )
          )
    )
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_proc procedure
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid = procedure.pronamespace
          LEFT JOIN pg_catalog.pg_init_privs initial_acl
            ON initial_acl.classoid = 'pg_catalog.pg_proc'::regclass
           AND initial_acl.objoid = procedure.oid
           AND initial_acl.objsubid = 0
          CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(
                  procedure.proacl,
                  pg_catalog.acldefault('f', procedure.proowner)
              )
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
      ) AS pg_catalog_routine_privileges_exact,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_parameter_acl parameter
        CROSS JOIN LATERAL pg_catalog.aclexplode(parameter.paracl) acl
        WHERE acl.grantee = 0
           OR acl.grantee IN (
               SELECT principal.oid FROM managed_system_principals principal
           )
    ) AS parameter_privileges_absent,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_foreign_data_wrapper wrapper
        CROSS JOIN managed_system_principals principal
        WHERE has_foreign_data_wrapper_privilege(
            principal.oid,
            wrapper.oid,
            'USAGE'
        )
    )
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_foreign_server server
          CROSS JOIN managed_system_principals principal
          WHERE has_server_privilege(principal.oid, server.oid, 'USAGE')
      )
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_user_mappings mapping
          WHERE mapping.umuser = 0
             OR mapping.umuser IN (
                 SELECT principal.oid FROM managed_system_principals principal
             )
      ) AS foreign_data_access_denied,
    NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc procedure
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND procedure.prokind IN ('f', 'p', 'a', 'w')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend dependency
              WHERE dependency.classid = 'pg_catalog.pg_proc'::regclass
                AND dependency.objid = procedure.oid
                AND dependency.deptype = 'e'
          )
          AND has_function_privilege(current_user, procedure.oid, 'EXECUTE')
    ) AS application_routine_execute_absent
"""


class RegistrationError(RuntimeError):
    """Raised when local evidence or the dedicated database boundary is unsafe."""


def load_fixed_worker_environment(path: Path | None = None) -> None:
    """Load only the fixed private an2p worker environment; never accept a path argument."""

    path = path or FIXED_WORKER_ENV
    try:
        metadata = path.lstat()
        parent_metadata = path.parent.lstat()
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RegistrationError("fixed deployment worker environment is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or not (
            (
                metadata.st_uid == 0
                and metadata.st_gid == os.getegid()
                and stat.S_IMODE(metadata.st_mode) == 0o640
            )
            or (
                metadata.st_uid == os.geteuid()
                and metadata.st_gid == os.getegid()
                and stat.S_IMODE(metadata.st_mode) == 0o600
            )
        )
        or path.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or not (
            (
                parent_metadata.st_uid == 0
                and not (stat.S_IMODE(parent_metadata.st_mode) & 0o022)
            )
            or (
                parent_metadata.st_uid == os.geteuid()
                and stat.S_IMODE(parent_metadata.st_mode) == 0o700
            )
        )
        or len(content.encode("utf-8")) > 64 * 1024
    ):
        raise RegistrationError("fixed deployment worker environment is unsafe")
    parsed: dict[str, str] = {}
    for line in content.splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if (
            not separator
            or ENV_NAME_PATTERN.fullmatch(name) is None
            or name in parsed
            or not value
            or "\x00" in value
        ):
            raise RegistrationError("fixed deployment worker environment is invalid")
        parsed[name] = value
    if REQUIRED_WORKER_ENV_NAMES.difference(parsed) or parsed.get("ENVIRONMENT") != "production":
        raise RegistrationError("fixed deployment worker environment is incomplete")
    if (
        parsed["OPS_DEPLOY_AGENT_EXCLUSIVE"] != "true"
        or parsed["OPS_DEPLOY_REQUIRED_AGENT_HOSTNAME"] != "an2p"
        or parsed["OPS_DEPLOY_QUEUE_DB_HOST"] != "127.0.0.1"
        or parsed["OPS_DEPLOY_QUEUE_DB_PORT"] != "15432"
        or parsed["OPS_DEPLOY_QUEUE_DB_USER"] != DEDICATED_WORKER_LOGIN
        or parsed["DB_SSLMODE"] != "require"
    ):
        raise RegistrationError("fixed deployment worker boundary is invalid")
    for name in REQUIRED_WORKER_ENV_NAMES:
        os.environ[name] = parsed[name]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _configured_release_root() -> Path:
    value = os.getenv("OPS_CONTAINER_RELEASE_ROOT", "").strip()
    if not value:
        raise RegistrationError("OPS_CONTAINER_RELEASE_ROOT is not configured")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise RegistrationError("OPS_CONTAINER_RELEASE_ROOT must be absolute")
    try:
        metadata = candidate.lstat()
        root = candidate.resolve(strict=True)
    except OSError as exc:
        raise RegistrationError("container release root is unavailable") from exc
    if (
        candidate.is_symlink()
        or not container_release_directory_metadata_ready(metadata)
    ):
        raise RegistrationError("container release root must be immutable root-owned or private test evidence")
    return root


def load_fixed_release_evidence(source_tree: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if SOURCE_TREE_PATTERN.fullmatch(source_tree) is None:
        raise RegistrationError("source tree must be exactly 40 lowercase hexadecimal characters")
    root = _configured_release_root()
    candidate = root / source_tree
    try:
        metadata = candidate.lstat()
        release_directory = candidate.resolve(strict=True)
        names = {entry.name for entry in release_directory.iterdir()}
    except OSError as exc:
        raise RegistrationError("fixed release directory is unavailable") from exc
    if (
        release_directory.parent != root
        or candidate.is_symlink()
        or not container_release_directory_metadata_ready(metadata)
        or names != EXPECTED_FILES
    ):
        raise RegistrationError("fixed release directory is unsafe or not exact")
    for name in EXPECTED_FILES:
        path = release_directory / name
        try:
            file_metadata = path.lstat()
        except OSError as exc:
            raise RegistrationError(f"fixed release file is unavailable: {name}") from exc
        if (
            path.is_symlink()
            or not container_release_file_metadata_ready(file_metadata)
        ):
            raise RegistrationError(f"fixed release file is unsafe: {name}")
    try:
        verified = verify_release_artifacts(release_directory)
        release = load_json_evidence(release_directory / "release.json")
        receipt = load_json_evidence(release_directory / "validation.json", receipt=True)
        bound = bind_promotion_evidence(release, receipt, now=_utc_now())
    except (ManifestError, VerificationError, OSError) as exc:
        raise RegistrationError("release bundle or PASS receipt is invalid") from exc
    expected_identity = os.getenv("OPS_CONTAINER_DEV_TARGET_IDENTITY", "").strip().lower()
    if SHA256_PATTERN.fullmatch(expected_identity) is None:
        raise RegistrationError("OPS_CONTAINER_DEV_TARGET_IDENTITY is invalid")
    if (
        verified.get("source_tree") != source_tree
        or bound.release["source_tree"] != source_tree
        or bound.receipt["target"] != "an2p-dev"
        or bound.receipt["target_identity"] != expected_identity
    ):
        raise RegistrationError("release evidence is not bound to the configured an2p-dev identity")
    return bound.release, bound.receipt


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source-tree")
    mode.add_argument("--verify-database-boundary", action="store_true")
    parser.add_argument("--database")
    parser.add_argument("--user")
    return parser.parse_args(argv)


def _verify_database_boundary(
    connection: Any,
    *,
    expected_user: str,
    expected_database: str,
) -> None:
    """Prove the live LOGIN and least-privilege ACL before any evidence INSERT."""

    if expected_user != DEDICATED_WORKER_LOGIN or not expected_database:
        raise RegistrationError("configured deployment database identity is invalid")
    result = connection.execute(
        text(DATABASE_BOUNDARY_SQL),
        {
            "expected_user": expected_user,
            "expected_database": expected_database,
        },
    )
    try:
        contract = dict(result.mappings().one())
    except (AttributeError, TypeError, ValueError) as exc:
        raise RegistrationError("database authorization contract is unreadable") from exc
    if frozenset(contract) != frozenset(DATABASE_BOUNDARY_FIELDS) or any(
        contract.get(field) is not True for field in DATABASE_BOUNDARY_FIELDS
    ):
        raise RegistrationError(
            "database login is not the exact isolated deployment worker"
        )


def _other_database_names(connection: Any, expected_database: str) -> tuple[str, ...]:
    result = connection.execute(
        text(
            "SELECT datname FROM pg_catalog.pg_database "
            "WHERE datallowconn "
            "AND datname <> :expected_database ORDER BY datname"
        ),
        {"expected_database": expected_database},
    )
    try:
        names = tuple(result.scalars().all())
    except (AttributeError, TypeError, ValueError) as exc:
        raise RegistrationError("database inventory is unreadable") from exc
    if len(names) > MAX_OTHER_DATABASES or any(
        not isinstance(name, str)
        or not name
        or len(name.encode("utf-8")) > MAX_DATABASE_NAME_BYTES
        or any(ord(character) < 0x20 for character in name)
        for name in names
    ):
        raise RegistrationError("database inventory is invalid")
    return names


def _verify_other_database_rejections(
    database: dict[str, Any],
    other_databases: Sequence[str],
) -> None:
    """Prove the dedicated credential cannot cross the exact DB HBA fence."""

    for database_name in other_databases:
        attempt = {**database, "database": database_name, "channel_binding": "require"}
        started = time.monotonic()
        try:
            escaped = psycopg2.connect(**attempt)
        except psycopg2.Error as exc:
            elapsed = time.monotonic() - started
            sqlstate = getattr(exc, "pgcode", None)
            message = str(exc).lower()
            authoritative_rejection = (
                sqlstate in {"28000", "42501"}
                or "pg_hba.conf rejects connection" in message
                or "permission denied for database" in message
            )
            if elapsed > 4.0 or not authoritative_rejection:
                raise RegistrationError(
                    "other-database rejection was not authoritative"
                ) from exc
            continue
        except (OSError, TypeError, ValueError) as exc:
            raise RegistrationError(
                "other-database rejection could not be verified"
            ) from exc
        try:
            escaped.close()
        finally:
            raise RegistrationError(
                "deployment worker credential can connect to another database"
            )


def _read_boundary_password() -> str:
    try:
        metadata = os.fstat(sys.stdin.fileno())
    except (AttributeError, OSError) as exc:
        raise RegistrationError("database boundary password pipe is unavailable") from exc
    if sys.stdin.isatty() or not stat.S_ISFIFO(metadata.st_mode):
        raise RegistrationError("database boundary password requires a pipe")
    payload = sys.stdin.buffer.read(MAX_PASSWORD_INPUT_BYTES + 1)
    if (
        len(payload) > MAX_PASSWORD_INPUT_BYTES
        or not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
    ):
        raise RegistrationError("database boundary password input is invalid")
    try:
        password = payload[:-1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise RegistrationError("database boundary password input is invalid") from exc
    if DATABASE_PASSWORD_PATTERN.fullmatch(password) is None:
        raise RegistrationError("database boundary password input is invalid")
    return password


def _create_database_engine(database: dict[str, Any]):
    connection_options = {
        name: value
        for name, value in database.items()
        if name not in {"user", "password", "host", "port", "database"}
    }
    return create_engine(
        URL.create(
            "postgresql+psycopg2",
            username=database["user"],
            password=database["password"],
            host=database["host"],
            port=database["port"],
            database=database["database"],
        ),
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        connect_args=connection_options,
    )


def verify_database_authorization(database: dict[str, Any]) -> None:
    expected_user = str(database.get("user", ""))
    expected_database = str(database.get("database", ""))
    if (
        database.get("host") != "127.0.0.1"
        or database.get("port") not in {5432, 15432}
        or database.get("sslmode") != "require"
        or database.get("channel_binding") != "require"
        or not str(database.get("options", "")).startswith(
            "-c search_path=pg_catalog,public "
        )
        or expected_user != DEDICATED_WORKER_LOGIN
        or DATABASE_NAME_PATTERN.fullmatch(expected_database) is None
    ):
        raise RegistrationError("configured deployment database transport is invalid")
    engine = _create_database_engine(database)
    try:
        with engine.begin() as connection:
            _verify_database_boundary(
                connection,
                expected_user=expected_user,
                expected_database=expected_database,
            )
            other_databases = _other_database_names(connection, expected_database)
            _verify_other_database_rejections(database, other_databases)
    finally:
        engine.dispose()


def register(source_tree: str) -> dict[str, Any]:
    if socket.gethostname().split(".", 1)[0].lower() != "an2p":
        raise RegistrationError("container evidence may be registered only by an2p")
    release, receipt = load_fixed_release_evidence(source_tree)
    database = queue_database_config()
    expected_user = str(database.get("user", ""))
    expected_database = str(database.get("database", ""))
    if (
        database.get("host") != "127.0.0.1"
        or database.get("port") != 15432
        or database.get("sslmode") != "require"
        or not str(database.get("options", "")).startswith(
            "-c search_path=pg_catalog,public "
        )
        or expected_user != DEDICATED_WORKER_LOGIN
        or not expected_database
    ):
        raise RegistrationError("configured deployment database transport is invalid")
    # `sslmode=require` proves encryption, while channel binding proves that
    # the password exchange is SCRAM-SHA-256-PLUS over that exact TLS channel.
    # This prevents a permissive/trust HBA drift from satisfying registration.
    database["channel_binding"] = "require"
    engine = _create_database_engine(database)
    try:
        with engine.begin() as connection:
            _verify_database_boundary(
                connection,
                expected_user=expected_user,
                expected_database=expected_database,
            )
            other_databases = _other_database_names(connection, expected_database)
            _verify_other_database_rejections(database, other_databases)
            registered_release = register_container_release_evidence(
                connection,  # type: ignore[arg-type]
                release,
                builder_target_identity=receipt["target_identity"],
                builder_hostname="an2p",
            )
            registered_receipt = register_container_validation_evidence(
                connection,  # type: ignore[arg-type]
                receipt,
            )
    finally:
        engine.dispose()
    return {
        "schema_version": 1,
        "release_id": registered_release["id"],
        "release_digest": release["release_digest"],
        "receipt_id": registered_receipt["id"],
        "receipt_digest": receipt["receipt_digest"],
        "source_tree": source_tree,
        "target": "an2p-dev",
        "target_identity": receipt["target_identity"],
        "status": receipt["status"],
        "expires_at": receipt["expires_at"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.verify_database_boundary:
        if (
            args.source_tree is not None
            or args.user != DEDICATED_WORKER_LOGIN
            or not isinstance(args.database, str)
            or DATABASE_NAME_PATTERN.fullmatch(args.database) is None
        ):
            raise RegistrationError("database boundary verification identity is invalid")
        verify_database_authorization(
            {
                "application_name": "mooncen-native-worker-boundary-check",
                "channel_binding": "require",
                "connect_timeout": 5,
                "database": args.database,
                "host": "127.0.0.1",
                "options": (
                    "-c search_path=pg_catalog,public "
                    "-c statement_timeout=15000 -c lock_timeout=3000"
                ),
                "password": _read_boundary_password(),
                "port": 5432,
                "sslmode": "require",
                "user": args.user,
            }
        )
        print(
            json.dumps(
                {"schema_version": 1, "status": "authorized"},
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    if args.database is not None or args.user is not None or args.source_tree is None:
        raise RegistrationError("evidence registration arguments are invalid")
    if not container_transport_service_boundary_ready(profile="deploy"):
        raise RegistrationError(
            "run as the isolated mooncen_deployment_worker account on an2p"
        )
    load_fixed_worker_environment()
    result = register(args.source_tree.strip())
    print(
        json.dumps(
            result,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RegistrationError, RuntimeError, ValueError) as exc:
        print(f"container evidence registration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
