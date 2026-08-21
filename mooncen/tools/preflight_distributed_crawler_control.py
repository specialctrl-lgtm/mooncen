"""Fail-closed preflight for distributed crawler control-plane services.

This command is intentionally read-only.  It validates a protected systemd
EnvironmentFile, connects with that component's real database credential, and
checks the shared staging schema and least-privilege role contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import stat
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg2

from DB.connection_settings import database_connect_options


COMPONENTS = frozenset(
    {
        "scheduler",
        "publisher",
        "finalizer",
        "approver",
        "release_approver",
        "release_admin",
        "crawler_api",
        "worker",
        "reporter",
        "observer",
    }
)
ENVIRONMENT_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
DATABASE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = (
    ROOT / "DB" / "crawler_control_migrations" / "20260810_001_crawler_control_plane.sql"
)
RELEASE_ACTION_MIGRATION_FILE = (
    ROOT / "DB" / "crawler_control_migrations" / "20260812_002_release_action_requests.sql"
)
ROLLOUT_SNAPSHOT_MIGRATION_FILE = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_004_rollout_worker_snapshots.sql"
)
ATTEMPT_RELEASE_GENERATION_MIGRATION_FILE = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_005_attempt_release_generation.sql"
)
RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_006_release_operator_approvals.sql"
)
QUALITY_ENVIRONMENT_ISOLATION_MIGRATION_FILE = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_007_quality_environment_isolation.sql"
)
DATABASE_MARKER_FILE = ROOT / "DB" / "crawler_control_database_marker.sql"
STAGING_CONTROL_FILE = ROOT / "DB" / "staging_control_plane.sql"
ROLES_FILE = ROOT / "DB" / "roles_body.sql"
REVIEWED_FUNCTION_FILES = {
    "mooncen_reject_immutable_crawler_evidence": MIGRATION_FILE,
    "enforce_crawler_rollout_snapshot_insert": ROLLOUT_SNAPSHOT_MIGRATION_FILE,
    "enforce_crawler_rollout_snapshot_commit": ROLLOUT_SNAPSHOT_MIGRATION_FILE,
    "enforce_crawler_rollout_snapshot_requirement": ROLLOUT_SNAPSHOT_MIGRATION_FILE,
    "enforce_crawler_attempt_release_generation_insert": ATTEMPT_RELEASE_GENERATION_MIGRATION_FILE,
    "enforce_crawler_attempt_release_generation_immutable": ATTEMPT_RELEASE_GENERATION_MIGRATION_FILE,
    "enforce_current_crawler_lease": STAGING_CONTROL_FILE,
    "capture_fenced_crawler_snapshot": STAGING_CONTROL_FILE,
    "enforce_crawler_worker_agent_heartbeat": STAGING_CONTROL_FILE,
    "current_crawler_worker_agent_id": STAGING_CONTROL_FILE,
    "current_crawler_worker_environment": STAGING_CONTROL_FILE,
    "current_crawler_reporter_agent_id": STAGING_CONTROL_FILE,
    "current_crawler_reporter_environment": STAGING_CONTROL_FILE,
    "is_crawler_managed_agent": STAGING_CONTROL_FILE,
    "is_crawler_control_job": STAGING_CONTROL_FILE,
    "is_current_crawler_worker_job": STAGING_CONTROL_FILE,
    "is_live_crawler_worker_job": STAGING_CONTROL_FILE,
    "enforce_crawler_worker_job_transition": STAGING_CONTROL_FILE,
    "enforce_crawler_worker_active_attempt": STAGING_CONTROL_FILE,
    "enforce_crawler_worker_attempt_insert": STAGING_CONTROL_FILE,
    "enforce_crawler_worker_attempt_transition": STAGING_CONTROL_FILE,
    "enforce_crawler_worker_observation_insert": STAGING_CONTROL_FILE,
    "enforce_crawler_worker_terminal_job_commit": STAGING_CONTROL_FILE,
    "enforce_crawler_worker_terminal_attempt_commit": STAGING_CONTROL_FILE,
    "enforce_crawler_promotion_role_separation": STAGING_CONTROL_FILE,
    "enforce_crawler_worker_runtime_evidence": STAGING_CONTROL_FILE,
    "enforce_crawler_release_report_timestamp": STAGING_CONTROL_FILE,
    "enforce_crawler_release_action_transition": RELEASE_ACTION_MIGRATION_FILE,
    "current_crawler_api_environment": RELEASE_ACTION_MIGRATION_FILE,
    "crawler_release_action_request_digest": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
    "crawler_release_action_proposal_is_valid": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
    "stamp_crawler_release_action_request_digest": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
    "require_crawler_release_action_approval": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
    "reject_crawler_release_approval_mutation": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
    "approve_crawler_release_action": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
    "preview_crawler_release_action_for_approval": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
    "heartbeat_crawler_release_action_consumer": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
    "crawler_release_approval_catalog_is_valid": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
    "crawler_release_approval_contract_is_valid": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
    "crawler_release_action_runtime_is_ready": RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE,
}
WORKER_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
WORKER_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62})(?:\.[a-z0-9](?:[a-z0-9-]{0,62}))*$"
)
WORKER_RELEASE_ENVIRONMENT = Path("/opt/mooncen-crawler/current/release.env")
WORKER_RELEASE_KEYS = (
    "OPS_CRAWLER_CODE_VERSION",
    "OPS_CRAWLER_ARTIFACT_DIGEST",
    "OPS_CRAWLER_CONFIG_REVISION",
)
SECURITY_DEFINER_FUNCTIONS = frozenset(
    set(REVIEWED_FUNCTION_FILES)
    - {
        "mooncen_reject_immutable_crawler_evidence",
        "enforce_crawler_release_action_transition",
        "enforce_crawler_rollout_snapshot_insert",
        "enforce_crawler_rollout_snapshot_commit",
        "enforce_crawler_rollout_snapshot_requirement",
        "enforce_crawler_attempt_release_generation_immutable",
        "crawler_release_action_request_digest",
        "crawler_release_action_proposal_is_valid",
        "crawler_release_approval_catalog_is_valid",
        "require_crawler_release_action_approval",
        "reject_crawler_release_approval_mutation",
    }
)
RLS_TABLES = (
    "ops_agents",
    "ops_jobs",
    "ops_job_logs",
    "ops_crawler_runs",
    "ops_crawler_batch_tasks",
    "ops_crawler_task_attempts",
    "ops_crawler_task_observations",
    "ops_crawler_release_reports",
    "ops_crawler_release_rollouts",
    "ops_crawler_worker_desired_state",
    "ops_crawler_rollout_worker_snapshots",
    "ops_crawler_release_action_requests",
    "ops_crawler_release_approver_bindings",
    "ops_crawler_release_action_approvals",
    "ops_crawler_release_action_consumers",
    "ops_crawler_batches",
    "ops_crawler_agent_bindings",
    "crawl_batches",
    "crawler_run_log",
    "crawl_progress",
    "course_quality_score",
    "ops_quality_issues",
)
MANAGED_PERMISSION_GROUPS = (
    "mooncen_crawler_control",
    "mooncen_crawler_publisher",
    "mooncen_crawler_finalizer",
    "mooncen_crawler_approver",
    "mooncen_crawler_release_approver",
    "mooncen_crawler_release_admin",
    "mooncen_crawler_api",
    "mooncen_crawler_worker",
    "mooncen_crawler_reporter",
    "mooncen_crawler_observer",
)


class PreflightError(RuntimeError):
    pass


def _compact_sql(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _assert_rollout_snapshot_catalog(cursor: Any) -> None:
    """Verify the historical roster cannot be weakened behind its ledger row."""

    cursor.execute(
        """
        SELECT attribute.attname,
               pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
               attribute.attnotnull,
               pg_get_expr(default_row.adbin, default_row.adrelid)
        FROM pg_attribute attribute
        JOIN pg_class relation ON relation.oid = attribute.attrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_attrdef default_row
          ON default_row.adrelid = relation.oid
         AND default_row.adnum = attribute.attnum
        WHERE namespace.nspname = 'public'
          AND relation.relname = 'ops_crawler_rollout_worker_snapshots'
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        ORDER BY attribute.attnum
        """
    )
    if cursor.fetchall() != [
        ("environment", "text", True, None),
        ("rollout_id", "uuid", True, None),
        ("generation", "bigint", True, None),
        ("worker_key", "text", True, None),
        ("agent_id", "uuid", True, None),
        ("desired_status", "text", True, None),
        ("cohort", "text", True, None),
        ("artifact_digest", "text", True, None),
        ("code_version", "text", True, None),
        ("config_revision", "text", True, None),
        ("created_at", "timestamp with time zone", True, "CURRENT_TIMESTAMP"),
    ]:
        raise PreflightError("rollout worker snapshot column contract has drifted")

    cursor.execute(
        """
        SELECT relation.relowner = public_namespace.nspowner,
               NOT owner.rolcanlogin
                   AND NOT owner.rolsuper
                   AND NOT owner.rolcreaterole
                   AND NOT owner.rolcreatedb
                   AND NOT owner.rolreplication
                   AND NOT owner.rolbypassrls
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        JOIN pg_namespace public_namespace ON public_namespace.nspname = 'public'
        JOIN pg_roles owner ON owner.oid = relation.relowner
        WHERE namespace.nspname = 'public'
          AND relation.relname = 'ops_crawler_rollout_worker_snapshots'
          AND relation.relkind = 'r'
        """
    )
    if cursor.fetchone() != (True, True):
        raise PreflightError("rollout worker snapshot owner contract has drifted")

    cursor.execute(
        """
        SELECT constraint.conname, constraint.contype, constraint.convalidated,
               constraint.condeferrable, constraint.condeferred,
               ARRAY(
                   SELECT attribute.attname
                   FROM unnest(constraint.conkey)
                       WITH ORDINALITY AS key(attnum, ordinality)
                   JOIN pg_attribute attribute
                     ON attribute.attrelid = constraint.conrelid
                    AND attribute.attnum = key.attnum
                   ORDER BY key.ordinality
               ),
               COALESCE(referenced_namespace.nspname || '.' || referenced.relname, ''),
               ARRAY(
                   SELECT attribute.attname
                   FROM unnest(constraint.confkey)
                       WITH ORDINALITY AS key(attnum, ordinality)
                   JOIN pg_attribute attribute
                     ON attribute.attrelid = constraint.confrelid
                    AND attribute.attnum = key.attnum
                   ORDER BY key.ordinality
               ),
               constraint.confupdtype, constraint.confdeltype, constraint.confmatchtype
        FROM pg_constraint constraint
        JOIN pg_class relation ON relation.oid = constraint.conrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_class referenced ON referenced.oid = constraint.confrelid
        LEFT JOIN pg_namespace referenced_namespace
          ON referenced_namespace.oid = referenced.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = 'ops_crawler_rollout_worker_snapshots'
          AND constraint.contype IN ('p', 'f')
        ORDER BY constraint.contype DESC, constraint.conname
        """
    )
    identity_constraints = cursor.fetchall()
    primary = [row for row in identity_constraints if row[1] == "p"]
    foreign = [row for row in identity_constraints if row[1] == "f"]
    if len(primary) != 1 or primary[0][0:6] != (
        "pk_ops_crawler_rollout_worker_snapshots",
        "p",
        True,
        False,
        False,
        ["environment", "rollout_id", "generation", "worker_key"],
    ):
        raise PreflightError("rollout worker snapshot primary key has drifted")
    expected_foreign = {
        (("rollout_id",), "public.ops_crawler_release_rollouts", ("id",), "a", "r", "s"),
        (("agent_id",), "public.ops_agents", ("id",), "a", "r", "s"),
        (
            ("artifact_digest",),
            "public.ops_crawler_release_artifacts",
            ("artifact_digest",),
            "a",
            "r",
            "s",
        ),
    }
    normalized_foreign = {
        (tuple(row[5]), row[6], tuple(row[7]), row[8], row[9], row[10])
        for row in foreign
        if row[2] is True and row[3] is False and row[4] is False
    }
    if len(foreign) != 3 or normalized_foreign != expected_foreign:
        raise PreflightError("rollout worker snapshot foreign keys have drifted")

    cursor.execute(
        """
        SELECT constraint.conname, constraint.convalidated,
               pg_get_constraintdef(constraint.oid, true)
        FROM pg_constraint constraint
        JOIN pg_class relation ON relation.oid = constraint.conrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = 'ops_crawler_rollout_worker_snapshots'
          AND constraint.contype = 'c'
        ORDER BY constraint.conname
        """
    )
    checks = cursor.fetchall()
    expected_checks = {
        "chk_ops_crawler_rollout_worker_snapshot_cohort":
            "check(cohort=any(array['canary'::text,'stable'::text]))",
        "chk_ops_crawler_rollout_worker_snapshot_code":
            "check(code_version=btrim(code_version)andchar_length(code_version)>=1andchar_length(code_version)<=200)",
        "chk_ops_crawler_rollout_worker_snapshot_config":
            "check(config_revision=btrim(config_revision)andchar_length(config_revision)>=1andchar_length(config_revision)<=255)",
        "chk_ops_crawler_rollout_worker_snapshot_environment":
            "check(environment=any(array['production'::text,'staging'::text,'development'::text]))",
        "chk_ops_crawler_rollout_worker_snapshot_generation": "check(generation>0)",
        "chk_ops_crawler_rollout_worker_snapshot_key":
            "check(worker_key=btrim(worker_key)andchar_length(worker_key)>=1andchar_length(worker_key)<=200)",
        "chk_ops_crawler_rollout_worker_snapshot_status":
            "check(desired_status=any(array['active'::text,'draining'::text,'disabled'::text]))",
    }
    actual_checks = {
        name: _compact_sql(definition).replace("(", "").replace(")", "")
        for name, validated, definition in checks
        if validated is True
    }
    normalized_expected_checks = {
        name: definition.replace("(", "").replace(")", "")
        for name, definition in expected_checks.items()
    }
    if len(checks) != len(expected_checks) or actual_checks != normalized_expected_checks:
        raise PreflightError("rollout worker snapshot CHECK constraints have drifted")

    cursor.execute(
        """
        SELECT crawler_index.indisvalid,
               crawler_index.indisready,
               crawler_index.indislive,
               crawler_index.indisunique,
               crawler_index.indisprimary,
               crawler_index.indisexclusion,
               crawler_index.indexprs IS NULL,
               crawler_index.indpred IS NULL,
               crawler_index.indnkeyatts,
               crawler_index.indnatts,
               ARRAY(
                   SELECT attribute.attname
                   FROM unnest(crawler_index.indkey::smallint[])
                       WITH ORDINALITY AS key(attnum, ordinality)
                   JOIN pg_attribute attribute
                     ON attribute.attrelid = table_relation.oid
                    AND attribute.attnum = key.attnum
                   ORDER BY key.ordinality
               ),
               crawler_index.indoption::smallint[],
               access_method.amname,
               index_relation.relowner = table_relation.relowner
        FROM pg_index crawler_index
        JOIN pg_class index_relation ON index_relation.oid = crawler_index.indexrelid
        JOIN pg_namespace index_namespace ON index_namespace.oid = index_relation.relnamespace
        JOIN pg_class table_relation ON table_relation.oid = crawler_index.indrelid
        JOIN pg_namespace table_namespace ON table_namespace.oid = table_relation.relnamespace
        JOIN pg_am access_method ON access_method.oid = index_relation.relam
        WHERE index_namespace.nspname = 'public'
          AND index_relation.relname = 'idx_ops_crawler_rollout_worker_snapshots_latest'
          AND table_namespace.nspname = 'public'
          AND table_relation.relname = 'ops_crawler_rollout_worker_snapshots'
        """
    )
    if cursor.fetchone() != (
        True,
        True,
        True,
        False,
        False,
        False,
        True,
        True,
        4,
        4,
        ["environment", "rollout_id", "worker_key", "generation"],
        [0, 0, 0, 1],
        "btree",
        True,
    ):
        raise PreflightError("rollout worker snapshot latest index has drifted")

    cursor.execute(
        """
        SELECT attribute.attnotnull,
               pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
               pg_get_expr(default_row.adbin, default_row.adrelid)
        FROM pg_attribute attribute
        JOIN pg_class relation ON relation.oid = attribute.attrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_attrdef default_row
          ON default_row.adrelid = relation.oid
         AND default_row.adnum = attribute.attnum
        WHERE namespace.nspname = 'public'
          AND relation.relname = 'ops_crawler_release_rollouts'
          AND attribute.attname = 'worker_snapshot_required'
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        """
    )
    if cursor.fetchone() != (True, "boolean", "true"):
        raise PreflightError("rollout worker snapshot boundary column has drifted")


def _assert_quality_environment_catalog(cursor: Any) -> None:
    """Verify shared quality rows are forced behind the staging API boundary."""

    cursor.execute(
        """
        SELECT relation.relname,
               relation.relkind,
               relation.relrowsecurity,
               relation.relforcerowsecurity,
               relation.relowner = public_namespace.nspowner,
               NOT owner.rolcanlogin
                   AND NOT owner.rolsuper
                   AND NOT owner.rolcreaterole
                   AND NOT owner.rolcreatedb
                   AND NOT owner.rolreplication
                   AND NOT owner.rolbypassrls
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        JOIN pg_namespace public_namespace ON public_namespace.nspname = 'public'
        JOIN pg_roles owner ON owner.oid = relation.relowner
        WHERE namespace.nspname = 'public'
          AND relation.relname IN ('course_quality_score', 'ops_quality_issues')
        ORDER BY relation.relname
        """
    )
    if cursor.fetchall() != [
        ("course_quality_score", "r", True, True, True, True),
        ("ops_quality_issues", "r", True, True, True, True),
    ]:
        raise PreflightError("shared staging quality RLS or owner contract has drifted")


def _assert_managed_permission_groups_own_nothing(cursor: Any) -> None:
    cursor.execute(
        """
        WITH managed AS (
            SELECT oid, rolname
            FROM pg_roles
            WHERE rolname = ANY(%s::text[])
        ), owned AS (
            SELECT managed.rolname, 'database'::text AS kind, database.datname::text AS name
            FROM pg_database database
            JOIN managed ON managed.oid = database.datdba
            UNION ALL
            SELECT managed.rolname, 'schema', namespace.nspname
            FROM pg_namespace namespace
            JOIN managed ON managed.oid = namespace.nspowner
            UNION ALL
            SELECT managed.rolname, 'relation',
                   namespace.nspname || '.' || relation.relname
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            JOIN managed ON managed.oid = relation.relowner
            UNION ALL
            SELECT managed.rolname, 'routine',
                   namespace.nspname || '.' || procedure.proname
            FROM pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
            JOIN managed ON managed.oid = procedure.proowner
            UNION ALL
            SELECT managed.rolname, 'type', namespace.nspname || '.' || type.typname
            FROM pg_type type
            JOIN pg_namespace namespace ON namespace.oid = type.typnamespace
            JOIN managed ON managed.oid = type.typowner
            UNION ALL
            SELECT managed.rolname, 'extension', extension.extname
            FROM pg_extension extension
            JOIN managed ON managed.oid = extension.extowner
        )
        SELECT rolname, kind, name
        FROM owned
        ORDER BY rolname, kind, name
        LIMIT 1
        """,
        (list(MANAGED_PERMISSION_GROUPS),),
    )
    owned = cursor.fetchone()
    if owned is not None:
        raise PreflightError(f"managed crawler permission group owns {owned[1]} {owned[2]}")


def _crawler_policy_digest(cursor: Any) -> str:
    """Hash PostgreSQL's canonical form of every managed crawler RLS policy."""

    cursor.execute(
        """
        SELECT relation.relname,
               policy.polname,
               policy.polpermissive,
               policy.polcmd,
               policy.polroles::text,
               COALESCE(pg_get_expr(policy.polqual, policy.polrelid), ''),
               COALESCE(pg_get_expr(policy.polwithcheck, policy.polrelid), '')
        FROM pg_policy policy
        JOIN pg_class relation ON relation.oid = policy.polrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = ANY(%s::text[])
        ORDER BY relation.relname, policy.polname
        """,
        (list(RLS_TABLES),),
    )
    encoded = json.dumps(
        cursor.fetchall(),
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _crawler_acl_digest(cursor: Any) -> str:
    """Hash every direct DB/application ACL held by a managed permission group."""

    cursor.execute(
        """
        WITH application_owners AS (
            SELECT namespace.nspowner AS owner_oid
            FROM pg_namespace namespace
            WHERE namespace.nspname IN ('public', 'crawl_staging')
            UNION
            SELECT relation.relowner
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname IN ('public', 'crawl_staging')
              AND NOT EXISTS (
                  SELECT 1 FROM pg_depend dependency
                  WHERE dependency.classid = 'pg_class'::regclass
                    AND dependency.objid = relation.oid
                    AND dependency.deptype = 'e'
              )
            UNION
            SELECT procedure.proowner
            FROM pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname IN ('public', 'crawl_staging')
              AND NOT EXISTS (
                  SELECT 1 FROM pg_depend dependency
                  WHERE dependency.classid = 'pg_proc'::regclass
                    AND dependency.objid = procedure.oid
                    AND dependency.deptype = 'e'
              )
        ), managed AS (
            SELECT oid FROM pg_roles WHERE rolname = ANY(%s::text[])
        )
        SELECT owner.rolname, COALESCE(namespace.nspname, '*'),
               defaults.defaclobjtype, privilege.privilege_type
        FROM pg_default_acl defaults
        JOIN application_owners application_owner
          ON application_owner.owner_oid = defaults.defaclrole
        JOIN pg_roles owner ON owner.oid = defaults.defaclrole
        LEFT JOIN pg_namespace namespace ON namespace.oid = defaults.defaclnamespace
        CROSS JOIN LATERAL aclexplode(defaults.defaclacl) privilege
        WHERE (defaults.defaclnamespace = 0
               OR namespace.nspname IN ('public', 'crawl_staging'))
          AND (privilege.grantee = 0
               OR privilege.grantee IN (SELECT oid FROM managed))
        ORDER BY owner.rolname, COALESCE(namespace.nspname, '*'),
                 defaults.defaclobjtype, privilege.privilege_type
        """,
        (list(MANAGED_PERMISSION_GROUPS),),
    )
    unsafe_default_acl = cursor.fetchall()
    if unsafe_default_acl:
        raise PreflightError(
            "application owner retains an unsafe PUBLIC/runtime default ACL: "
            f"{unsafe_default_acl[0]}"
        )

    cursor.execute(
        """
        SELECT namespace.nspname, relation.relname, attribute.attname,
               privilege.privilege_type
        FROM pg_attribute attribute
        JOIN pg_class relation ON relation.oid = attribute.attrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        CROSS JOIN LATERAL aclexplode(attribute.attacl) privilege
        WHERE namespace.nspname IN ('public', 'crawl_staging')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND privilege.grantee = 0
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY namespace.nspname, relation.relname, attribute.attname,
                 privilege.privilege_type
        """
    )
    public_column_privileges = cursor.fetchall()
    if public_column_privileges:
        raise PreflightError(
            "PUBLIC retains a direct application-column privilege: "
            f"{public_column_privileges[0]}"
        )

    cursor.execute(
        """
        WITH managed AS (
            SELECT oid, rolname
            FROM pg_roles
            WHERE rolname = ANY(%s::text[])
        ), grants AS (
            SELECT 'database'::text AS kind, database.datname AS schema_name,
                   ''::text AS object_name, ''::text AS subobject_name,
                   managed.rolname, privilege.privilege_type, privilege.is_grantable
            FROM pg_database database
            CROSS JOIN LATERAL aclexplode(database.datacl) privilege
            JOIN managed ON managed.oid = privilege.grantee
            WHERE database.datname = current_database()
            UNION ALL
            SELECT 'schema', namespace.nspname, '', '', managed.rolname,
                   privilege.privilege_type, privilege.is_grantable
            FROM pg_namespace namespace
            CROSS JOIN LATERAL aclexplode(namespace.nspacl) privilege
            JOIN managed ON managed.oid = privilege.grantee
            WHERE namespace.nspname IN ('public', 'crawl_staging')
            UNION ALL
            SELECT 'relation', namespace.nspname, relation.relname, '', managed.rolname,
                   privilege.privilege_type, privilege.is_grantable
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL aclexplode(relation.relacl) privilege
            JOIN managed ON managed.oid = privilege.grantee
            WHERE namespace.nspname IN ('public', 'crawl_staging')
            UNION ALL
            SELECT 'column', namespace.nspname, relation.relname, attribute.attname,
                   managed.rolname, privilege.privilege_type, privilege.is_grantable
            FROM pg_attribute attribute
            JOIN pg_class relation ON relation.oid = attribute.attrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL aclexplode(attribute.attacl) privilege
            JOIN managed ON managed.oid = privilege.grantee
            WHERE namespace.nspname IN ('public', 'crawl_staging')
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
            UNION ALL
            SELECT 'routine', namespace.nspname, procedure.proname,
                   pg_get_function_identity_arguments(procedure.oid), managed.rolname,
                   privilege.privilege_type, privilege.is_grantable
            FROM pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
            CROSS JOIN LATERAL aclexplode(procedure.proacl) privilege
            JOIN managed ON managed.oid = privilege.grantee
            WHERE namespace.nspname IN ('public', 'crawl_staging')
        )
        SELECT kind, schema_name, object_name, subobject_name,
               rolname, privilege_type, is_grantable
        FROM grants
        ORDER BY kind, schema_name, object_name, subobject_name,
                 rolname, privilege_type, is_grantable
        """,
        (list(MANAGED_PERMISSION_GROUPS),),
    )
    encoded = json.dumps(
        cursor.fetchall(),
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reviewed_contract_markers(cursor: Any) -> None:
    try:
        migration_checksum = hashlib.sha256(MIGRATION_FILE.read_bytes()).hexdigest()
        release_action_checksum = hashlib.sha256(
            RELEASE_ACTION_MIGRATION_FILE.read_bytes()
        ).hexdigest()
        rollout_snapshot_checksum = hashlib.sha256(
            ROLLOUT_SNAPSHOT_MIGRATION_FILE.read_bytes()
        ).hexdigest()
        attempt_release_generation_checksum = hashlib.sha256(
            ATTEMPT_RELEASE_GENERATION_MIGRATION_FILE.read_bytes()
        ).hexdigest()
        release_operator_approval_checksum = hashlib.sha256(
            RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE.read_bytes()
        ).hexdigest()
        quality_environment_isolation_checksum = hashlib.sha256(
            QUALITY_ENVIRONMENT_ISOLATION_MIGRATION_FILE.read_bytes()
        ).hexdigest()
        marker_checksum = hashlib.sha256(DATABASE_MARKER_FILE.read_bytes()).hexdigest()
        staging_checksum = hashlib.sha256(STAGING_CONTROL_FILE.read_bytes()).hexdigest()
        roles_checksum = hashlib.sha256(ROLES_FILE.read_bytes()).hexdigest()
    except OSError as exc:
        raise PreflightError("reviewed crawler database contract files are unavailable") from exc
    migration_version = MIGRATION_FILE.stem
    policy_digest = _crawler_policy_digest(cursor)
    acl_digest = _crawler_acl_digest(cursor)
    expected = {
        migration_version: migration_checksum,
        RELEASE_ACTION_MIGRATION_FILE.stem: release_action_checksum,
        ROLLOUT_SNAPSHOT_MIGRATION_FILE.stem: rollout_snapshot_checksum,
        ATTEMPT_RELEASE_GENERATION_MIGRATION_FILE.stem: attempt_release_generation_checksum,
        RELEASE_OPERATOR_APPROVAL_MIGRATION_FILE.stem: release_operator_approval_checksum,
        QUALITY_ENVIRONMENT_ISOLATION_MIGRATION_FILE.stem: quality_environment_isolation_checksum,
        f"{migration_version}_marker_{marker_checksum[:16]}": marker_checksum,
        f"{migration_version}_staging_{staging_checksum[:16]}": staging_checksum,
        f"{migration_version}_roles_{roles_checksum[:16]}": roles_checksum,
        f"{migration_version}_policies_{staging_checksum[:12]}_{policy_digest[:12]}": policy_digest,
        f"{migration_version}_acls_{roles_checksum[:12]}_{acl_digest[:12]}": acl_digest,
    }
    cursor.execute(
        """
        SELECT version, checksum
        FROM mooncen_schema_migrations
        WHERE version = ANY(%s::text[])
        ORDER BY version
        """,
        (sorted(expected),),
    )
    if dict(cursor.fetchall()) != expected:
        raise PreflightError("applied crawler database contract differs from this release")


def _reviewed_function_sources() -> dict[str, str]:
    reviewed: dict[str, str] = {}
    for function_name, path in REVIEWED_FUNCTION_FILES.items():
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError("not a regular reviewed SQL file")
            sql_source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PreflightError(f"reviewed function contract is unavailable: {path}") from exc
        match = re.search(
            rf"CREATE OR REPLACE FUNCTION (?:public\.)?{re.escape(function_name)}\([^)]*\)"
            rf".*?\bAS \$\$(.*?)\$\$;",
            sql_source,
            flags=re.DOTALL,
        )
        if match is None:
            raise PreflightError(f"reviewed function body is missing: {function_name}")
        reviewed[function_name] = match.group(1).strip()
    return reviewed


def _protected_environment(path: Path, *, owner_only: bool = False) -> dict[str, str]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PreflightError(f"environment file is unavailable: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise PreflightError(f"environment file must be a regular non-symlink: {path}")
    if metadata.st_mode & 0o022:
        raise PreflightError(f"environment file must not be group/world writable: {path}")
    if owner_only and metadata.st_mode & 0o077:
        raise PreflightError(f"environment input must have mode 0600 or stricter: {path}")
    if not owner_only and metadata.st_mode & 0o007:
        raise PreflightError(f"runtime environment file must not be world-accessible: {path}")
    if os.name == "posix" and metadata.st_uid != 0:
        raise PreflightError(f"environment file must be owned by root: {path}")

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PreflightError(f"environment file is unreadable: {path}") from exc
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PreflightError(f"invalid environment entry at {path}:{line_number}")
        key, value = line.split("=", 1)
        if not ENVIRONMENT_KEY.fullmatch(key) or key in values:
            raise PreflightError(f"invalid or duplicate environment key at {path}:{line_number}")
        if not value or "\x00" in value or value != value.strip():
            raise PreflightError(f"invalid environment value for {key}")
        if (
            not value
            or "\n" in value
            or "\r" in value
            or any(character.isspace() for character in value)
            or any(character in value for character in ("\\", '"', "'"))
        ):
            raise PreflightError(f"invalid environment value for {key}")
        values[key] = value
    return values


def _assert_component_environment_permissions(
    path: Path,
    component: str,
    *,
    allow_root_input: bool = False,
) -> None:
    if os.name != "posix":
        return
    import grp

    metadata = path.lstat()
    if (
        allow_root_input
        and metadata.st_uid == 0
        and metadata.st_gid == 0
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    ):
        return
    if component in {"approver", "release_approver", "release_admin"}:
        expected_gid = 0
        expected_mode = 0o600
    else:
        expected_group = {
            "scheduler": "mooncen-crawler-control",
            "publisher": "mooncen-crawler-publisher",
            "finalizer": "mooncen-crawler-finalizer",
            "worker": "mooncen-crawler-worker",
            "reporter": "mooncen-crawler-reporter",
            "observer": "mooncen-crawler-observer",
            "crawler_api": "mooncen-api",
        }[component]
        try:
            expected_gid = grp.getgrnam(expected_group).gr_gid
        except KeyError as exc:
            raise PreflightError(f"dedicated environment group is missing: {expected_group}") from exc
        expected_mode = 0o640
    if (
        metadata.st_uid != 0
        or metadata.st_gid != expected_gid
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != expected_mode
    ):
        raise PreflightError(f"{component} environment ownership or mode has drifted")


def _required(environment: dict[str, str], key: str) -> str:
    value = environment.get(key, "").strip()
    if not value:
        raise PreflightError(f"{key} is required")
    return value


def _installed_worker_release_environment() -> dict[str, str]:
    release_root = Path("/opt/mooncen-crawler/releases").resolve(strict=True)
    current_link = Path("/opt/mooncen-crawler/current")
    try:
        link_metadata = current_link.lstat()
        resolved_environment = WORKER_RELEASE_ENVIRONMENT.resolve(strict=True)
        metadata = resolved_environment.stat()
        resolved_environment.relative_to(release_root)
    except (OSError, ValueError) as exc:
        raise PreflightError("installed crawler release identity is unavailable") from exc
    if (
        not stat.S_ISLNK(link_metadata.st_mode)
        or (os.name == "posix" and link_metadata.st_uid != 0)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o444
        or (os.name == "posix" and (metadata.st_uid != 0 or metadata.st_gid != 0))
    ):
        raise PreflightError("installed crawler release identity path is unsafe")
    values: dict[str, str] = {}
    try:
        raw = resolved_environment.read_bytes()
    except OSError as exc:
        raise PreflightError("installed crawler release identity cannot be read") from exc
    if len(raw) > 4096:
        raise PreflightError("installed crawler release identity is oversized")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise PreflightError("installed crawler release identity is not ASCII") from exc
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key in values or key not in WORKER_RELEASE_KEYS or not value:
            raise PreflightError("installed crawler release identity has invalid fields")
        values[key] = value
    if set(values) != set(WORKER_RELEASE_KEYS):
        raise PreflightError("installed crawler release identity is incomplete")
    if (
        re.fullmatch(r"[0-9a-f]{64}", values["OPS_CRAWLER_ARTIFACT_DIGEST"]) is None
        or not values["OPS_CRAWLER_CODE_VERSION"].strip()
        or not values["OPS_CRAWLER_CONFIG_REVISION"].strip()
    ):
        raise PreflightError("installed crawler release identity values are invalid")
    for key, value in values.items():
        if os.environ.get(key, "") != value:
            raise PreflightError("systemd release environment differs from installed release.env")
    return values


def _port(environment: dict[str, str], key: str) -> int:
    raw = _required(environment, key)
    try:
        value = int(raw)
    except ValueError as exc:
        raise PreflightError(f"{key} must be an integer") from exc
    if not 1 <= value <= 65_535:
        raise PreflightError(f"{key} must be between 1 and 65535")
    return value


def _connection_config(component: str, environment: dict[str, str]) -> dict[str, Any]:
    host = _required(environment, "OPS_CRAWLER_SHARED_DB_HOST")
    port = _port(environment, "OPS_CRAWLER_SHARED_DB_PORT")
    database = _required(environment, "OPS_CRAWLER_SHARED_DB_NAME")
    if not DATABASE_IDENTIFIER.fullmatch(database):
        raise PreflightError("OPS_CRAWLER_SHARED_DB_NAME is not a safe database identifier")

    if component == "scheduler":
        user_key = "OPS_CRAWLER_CONTROL_DB_USER"
        password_key = "OPS_CRAWLER_CONTROL_DB_PASSWORD"
    elif component == "publisher":
        user_key = "OPS_CRAWLER_PUBLISHER_DB_USER"
        password_key = "OPS_CRAWLER_PUBLISHER_DB_PASSWORD"
    elif component == "finalizer":
        user_key = "OPS_CRAWLER_FINALIZER_DB_USER"
        password_key = "OPS_CRAWLER_FINALIZER_DB_PASSWORD"
    elif component == "approver":
        user_key = "OPS_CRAWLER_APPROVER_DB_USER"
        password_key = "OPS_CRAWLER_APPROVER_DB_PASSWORD"
    elif component == "release_approver":
        user_key = "OPS_CRAWLER_RELEASE_APPROVER_DB_USER"
        password_key = "OPS_CRAWLER_RELEASE_APPROVER_DB_PASSWORD"
    elif component == "release_admin":
        user_key = "OPS_CRAWLER_RELEASE_ADMIN_DB_USER"
        password_key = "OPS_CRAWLER_RELEASE_ADMIN_DB_PASSWORD"
    elif component == "crawler_api":
        user_key = "OPS_CRAWLER_API_DB_USER"
        password_key = "OPS_CRAWLER_API_DB_PASSWORD"
    elif component == "reporter":
        user_key = "OPS_CRAWLER_REPORTER_DB_USER"
        password_key = "OPS_CRAWLER_REPORTER_DB_PASSWORD"
    elif component == "observer":
        user_key = "OPS_CRAWLER_METRICS_DB_USER"
        password_key = "OPS_CRAWLER_METRICS_DB_PASSWORD"
    else:
        user_key = "OPS_QUEUE_DB_USER"
        password_key = "OPS_QUEUE_DB_PASSWORD"
        queue_endpoint = (
            _required(environment, "OPS_QUEUE_DB_HOST"),
            _port(environment, "OPS_QUEUE_DB_PORT"),
            _required(environment, "OPS_QUEUE_DB_NAME"),
        )
        staging_endpoint = (
            _required(environment, "CRAWL_STAGING_DB_HOST"),
            _port(environment, "CRAWL_STAGING_DB_PORT"),
            _required(environment, "CRAWL_STAGING_DB_NAME"),
        )
        if queue_endpoint != (host, port, database) or staging_endpoint != (host, port, database):
            raise PreflightError("worker queue, staging, and shared control endpoints must be identical")
        if environment.get("CRAWL_WRITE_MODE", "").strip().lower() != "staging":
            raise PreflightError("worker/reporter requires CRAWL_WRITE_MODE=staging")
        if (
            _required(environment, "OPS_QUEUE_DB_USER")
            != _required(environment, "CRAWL_STAGING_DB_USER")
            or _required(environment, "OPS_QUEUE_DB_PASSWORD")
            != _required(environment, "CRAWL_STAGING_DB_PASSWORD")
        ):
            raise PreflightError("worker queue and staging writes must use the identical credential")

    user = _required(environment, user_key)
    password = _required(environment, password_key)
    if not DATABASE_IDENTIFIER.fullmatch(user):
        raise PreflightError(f"{user_key} is not a safe role identifier")

    prior_environment = os.environ.copy()
    try:
        os.environ.update(environment)
        options = database_connect_options(host, f"mooncen-crawler-{component}-preflight")
    finally:
        os.environ.clear()
        os.environ.update(prior_environment)
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        **options,
    }


def _check_required_paths(
    component: str,
    environment: dict[str, str],
    *,
    installation_validation: bool = False,
) -> None:
    runtime_environment = environment.get("ENVIRONMENT", "").strip().lower()
    if runtime_environment not in {"production", "staging"}:
        raise PreflightError("ENVIRONMENT must be production or staging")
    if component == "scheduler":
        if environment.get("OPS_CRAWLER_CONTROL_ENABLED", "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise PreflightError("scheduler requires OPS_CRAWLER_CONTROL_ENABLED=true")
        manifest = Path(_required(environment, "OPS_CRAWLER_PROVIDER_MANIFEST"))
        if not manifest.is_absolute() or manifest.is_symlink() or not manifest.is_file():
            raise PreflightError("OPS_CRAWLER_PROVIDER_MANIFEST must be an absolute regular file")
        from ops_agent.crawler_control_scheduler import load_config, load_provider_manifest

        try:
            _, manifest_revision = load_provider_manifest(manifest)
        except (OSError, RuntimeError, ValueError) as exc:
            raise PreflightError("crawler provider manifest failed its reviewed parser") from exc
        artifact_digest = _required(environment, "OPS_CRAWLER_ARTIFACT_DIGEST").lower()
        configured_revision = _required(environment, "OPS_CRAWLER_CONFIG_REVISION").lower()
        _required(environment, "OPS_CRAWLER_CODE_VERSION")
        if (
            re.fullmatch(r"[0-9a-f]{64}", artifact_digest) is None
            or len(set(artifact_digest)) == 1
            or configured_revision != manifest_revision
        ):
            raise PreflightError("scheduler artifact/config revision contract is invalid")
        prior_environment = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(environment)
            load_config()
        except (OSError, RuntimeError, ValueError) as exc:
            raise PreflightError("scheduler runtime configuration is invalid") from exc
        finally:
            os.environ.clear()
            os.environ.update(prior_environment)
    elif component == "publisher":
        output = Path(_required(environment, "OPS_CRAWLER_DESIRED_STATE_OUTPUT"))
        if output != Path(
            "/var/lib/mooncen-crawler-control/public/state/desired-state.json"
        ):
            raise PreflightError("desired-state output must use the isolated state directory")
        if not output.is_absolute() or output.parent.is_symlink() or not output.parent.is_dir():
            raise PreflightError("desired-state output parent must be an absolute regular directory")
        if not os.access(output.parent, os.W_OK):
            raise PreflightError("desired-state output parent is not writable by the publisher")
        release_environment = _required(environment, "OPS_CRAWLER_RELEASE_ENVIRONMENT")
        if release_environment not in {"production", "staging"}:
            raise PreflightError("OPS_CRAWLER_RELEASE_ENVIRONMENT must be production or staging")
    elif component == "finalizer":
        auto_promotion = _required(environment, "OPS_CRAWLER_AUTO_PROMOTION_ENABLED").lower()
        if auto_promotion != "false":
            raise PreflightError("OPS_CRAWLER_AUTO_PROMOTION_ENABLED must remain false")
        try:
            poll_seconds = int(_required(environment, "OPS_CRAWLER_FINALIZER_POLL_SECONDS"))
        except ValueError as exc:
            raise PreflightError("finalizer poll interval must be an integer") from exc
        if not 2 <= poll_seconds <= 3_600:
            raise PreflightError("finalizer poll interval must be between 2 and 3600")
    elif component == "release_admin":
        public_root = Path(_required(environment, "OPS_CRAWLER_RELEASE_PUBLIC_ROOT"))
        allowed_signers = Path(_required(environment, "OPS_CRAWLER_ALLOWED_SIGNERS"))
        if public_root != Path("/var/lib/mooncen-crawler-control/public"):
            raise PreflightError("release-admin public root must use the fixed served path")
        try:
            root_metadata = public_root.lstat()
            signer_metadata = allowed_signers.lstat()
        except OSError as exc:
            raise PreflightError("release-admin public root or allowed signers is unavailable") from exc
        if (
            public_root.is_symlink()
            or not stat.S_ISDIR(root_metadata.st_mode)
            or stat.S_IMODE(root_metadata.st_mode) != 0o755
            or (os.name == "posix" and (root_metadata.st_uid != 0 or root_metadata.st_gid != 0))
            or allowed_signers.is_symlink()
            or not stat.S_ISREG(signer_metadata.st_mode)
            or signer_metadata.st_nlink != 1
            or signer_metadata.st_mode & 0o022
            or (os.name == "posix" and signer_metadata.st_uid != 0)
        ):
            raise PreflightError("release-admin publication path permissions are unsafe")
    elif component == "observer":
        output = Path(_required(environment, "OPS_CRAWLER_METRICS_OUTPUT"))
        if output != Path("/var/lib/mooncen-crawler-observer/mooncen_crawler_control.prom"):
            raise PreflightError("observer output must use the fixed StateDirectory textfile")
        from ops_agent.crawler_control_metrics import load_config as load_metrics_config

        prior_environment = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(environment)
            metrics_config = load_metrics_config()
        except (RuntimeError, ValueError) as exc:
            raise PreflightError("observer runtime configuration is invalid") from exc
        finally:
            os.environ.clear()
            os.environ.update(prior_environment)
        if metrics_config.environment not in {"production", "staging"}:
            raise PreflightError("observer runtime environment is invalid")
    elif component in {"worker", "reporter"}:
        worker_key = _required(environment, "OPS_CRAWLER_WORKER_ID")
        agent_text = _required(environment, "OPS_AGENT_ID")
        configured_hostname = _required(
            environment, "OPS_CRAWLER_WORKER_HOSTNAME"
        ).lower().rstrip(".")
        try:
            agent_id = UUID(agent_text)
        except ValueError as exc:
            raise PreflightError("OPS_AGENT_ID must be a canonical non-nil UUID") from exc
        if (
            not WORKER_KEY.fullmatch(worker_key)
            or not WORKER_HOSTNAME.fullmatch(configured_hostname)
            or str(agent_id) != agent_text
            or agent_id.int == 0
        ):
            raise PreflightError("crawler worker identity values are invalid")
        if not installation_validation:
            local_hostname = socket.gethostname().lower().rstrip(".")
            if configured_hostname != local_hostname:
                raise PreflightError(
                    "OPS_CRAWLER_WORKER_HOSTNAME does not match this runtime host"
                )
        if component == "worker":
            try:
                poll_interval = float(_required(environment, "OPS_JOB_POLL_INTERVAL_SECONDS"))
                timeout_seconds = int(_required(environment, "OPS_CRAWLER_JOB_TIMEOUT_SECONDS"))
                lease_seconds = int(_required(environment, "OPS_CRAWLER_LEASE_SECONDS"))
            except ValueError as exc:
                raise PreflightError("worker poll, timeout, and lease values must be numeric") from exc
            if (
                not 0.5 <= poll_interval <= 60
                or not 60 <= timeout_seconds <= 86_400
                or not 30 <= lease_seconds <= 900
            ):
                raise PreflightError("worker poll, timeout, or lease value is out of range")


def _database_contract(
    component: str,
    connection: Any,
    expected_database: str,
    environment: dict[str, str] | None = None,
    *,
    require_runtime_readiness: bool = True,
) -> dict[str, str]:
    required_relations = (
        "public.ops_jobs",
        "public.ops_job_logs",
        "public.ops_crawler_runs",
        "public.ops_crawler_batches",
        "public.ops_crawler_batch_tasks",
        "public.ops_crawler_task_attempts",
        "public.ops_crawler_task_observations",
        "public.ops_crawler_release_artifacts",
        "public.ops_crawler_release_rollouts",
        "public.ops_crawler_worker_desired_state",
        "public.ops_crawler_rollout_worker_snapshots",
        "public.ops_crawler_agent_bindings",
        "public.ops_crawler_release_reports",
        "public.ops_crawler_release_action_requests",
        "public.ops_crawler_api_bindings",
        "public.ops_crawler_control_database_marker",
        "public.course_quality_score",
        "public.ops_quality_issues",
        "public.crawl_batches",
        "public.crawler_run_log",
        "public.crawl_progress",
        "crawl_staging.fenced_branch_snapshots",
        "crawl_staging.fenced_course_snapshots",
    )
    with connection.cursor() as cursor:
        _assert_managed_permission_groups_own_nothing(cursor)
        cursor.execute(
            """
            SELECT current_database(), current_user, session_user,
                   rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls,
                   rolinherit, rolconnlimit,
                   shobj_description(oid, 'pg_authid')
            FROM pg_roles
            WHERE rolname = current_user
            """
        )
        identity = cursor.fetchone()
        if not identity or identity[0] != expected_database or identity[1] != identity[2]:
            raise PreflightError("database identity or target database differs from the environment")
        expected_managed_component = {
            "scheduler": "control",
            "publisher": "publisher",
            "finalizer": "finalizer",
            "approver": "approver",
            "release_approver": "release_approver",
            "release_admin": "release_admin",
            "crawler_api": "crawler_api",
            "worker": "worker",
            "reporter": "reporter",
            "observer": "observer",
        }[component]
        expected_marker = f"mooncen-managed-crawler-login:v1:{expected_managed_component}"
        expected_connection_limit = (
            4 if component in {"observer", "crawler_api", "release_approver"} else 32
        )
        if (
            any(identity[3:8])
            or identity[8] is not True
            or identity[9] != expected_connection_limit
            or identity[10] != expected_marker
        ):
            raise PreflightError("service credential capabilities or connection limit are unsafe")
        expected_group = {
            "scheduler": "mooncen_crawler_control",
            "publisher": "mooncen_crawler_publisher",
            "finalizer": "mooncen_crawler_finalizer",
            "approver": "mooncen_crawler_approver",
            "release_approver": "mooncen_crawler_release_approver",
            "release_admin": "mooncen_crawler_release_admin",
            "crawler_api": "mooncen_crawler_api",
            "worker": "mooncen_crawler_worker",
            "reporter": "mooncen_crawler_reporter",
            "observer": "mooncen_crawler_observer",
        }[component]
        cursor.execute(
            """
            SELECT NOT rolcanlogin AND NOT rolsuper AND NOT rolcreaterole
                   AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls
                   AND rolinherit
            FROM pg_roles
            WHERE rolname = %s
            """,
            (expected_group,),
        )
        permission_group = cursor.fetchone()
        if not permission_group or permission_group[0] is not True:
            raise PreflightError(f"{component} permission group capabilities are unsafe")
        cursor.execute(
            """
            SELECT parent.rolname,
                   membership.admin_option,
                   COALESCE((to_jsonb(membership)->>'inherit_option')::boolean, true),
                   COALESCE((to_jsonb(membership)->>'set_option')::boolean, true)
            FROM pg_auth_members membership
            JOIN pg_roles parent ON parent.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE member.rolname = current_user
            ORDER BY parent.rolname
            """
        )
        if cursor.fetchall() != [(expected_group, False, True, True)]:
            raise PreflightError(f"{component} login has an unexpected direct role membership")
        cursor.execute(
            """
            SELECT parent.rolname,
                   membership.admin_option,
                   COALESCE((to_jsonb(membership)->>'inherit_option')::boolean, true),
                   COALESCE((to_jsonb(membership)->>'set_option')::boolean, true)
            FROM pg_auth_members membership
            JOIN pg_roles parent ON parent.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE member.rolname = %s
            ORDER BY parent.rolname
            """,
            (expected_group,),
        )
        if cursor.fetchall():
            raise PreflightError(f"{component} permission group inherits an unexpected parent role")
        cursor.execute(
            """
            SELECT member.rolname
            FROM pg_auth_members membership
            JOIN pg_roles parent ON parent.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE parent.rolname = current_user
            ORDER BY member.rolname
            """
        )
        if cursor.fetchall():
            raise PreflightError(f"{component} login is inherited by another role")
        if component == "crawler_api":
            if environment is None:
                raise PreflightError("crawler API preflight requires its environment contract")
            runtime_environment = _required(environment, "ENVIRONMENT")
            cursor.execute(
                """
                SELECT current_crawler_api_environment(),
                       has_function_privilege(
                           current_user, 'current_crawler_api_environment()', 'EXECUTE'
                       ),
                       NOT has_table_privilege(
                           current_user, 'ops_crawler_api_bindings', 'SELECT'
                       ),
                       NOT has_table_privilege(
                           current_user, 'ops_crawler_api_bindings', 'INSERT'
                       ),
                       NOT has_table_privilege(
                           current_user, 'ops_crawler_api_bindings', 'UPDATE'
                       )
                """
            )
            api_binding = cursor.fetchone()
            if api_binding != (runtime_environment, True, True, True, True):
                raise PreflightError("crawler API login environment binding is unavailable or unsafe")
        if component in {"release_approver", "release_admin"}:
            if environment is None:
                raise PreflightError(
                    f"{component} preflight requires its environment contract"
                )
            runtime_environment = _required(environment, "ENVIRONMENT")
            cursor.execute(
                "SELECT crawler_release_approval_contract_is_valid(%s)",
                (runtime_environment,),
            )
            approval_contract = cursor.fetchone()
            if not approval_contract or approval_contract[0] is not True:
                raise PreflightError(
                    "release approval credential or catalog contract is unavailable"
                )
        if component not in {"worker", "reporter", "crawler_api"}:
            cursor.execute(
                """
                SELECT member.rolname,
                       membership.admin_option,
                       COALESCE((to_jsonb(membership)->>'inherit_option')::boolean, true),
                       COALESCE((to_jsonb(membership)->>'set_option')::boolean, true)
                FROM pg_auth_members membership
                JOIN pg_roles parent ON parent.oid = membership.roleid
                JOIN pg_roles member ON member.oid = membership.member
                WHERE parent.rolname = %s
                ORDER BY member.rolname
                """,
                (expected_group,),
            )
            if cursor.fetchall() != [(identity[1], False, True, True)]:
                raise PreflightError(
                    f"{component} permission group has an unexpected managed member"
                )
        elif component in {"worker", "reporter", "crawler_api"}:
            cursor.execute(
                """
                SELECT bool_and(
                           member.rolcanlogin
                           AND member.rolinherit
                           AND NOT member.rolsuper
                           AND NOT member.rolcreaterole
                           AND NOT member.rolcreatedb
                           AND NOT member.rolreplication
                           AND NOT member.rolbypassrls
                           AND member.rolconnlimit = %s
                           AND member.rolconfig IS NULL
                           AND membership.admin_option IS FALSE
                           AND COALESCE(
                               (to_jsonb(membership)->>'inherit_option')::boolean,
                               true
                           )
                           AND COALESCE(
                               (to_jsonb(membership)->>'set_option')::boolean,
                               true
                           )
                           AND NOT EXISTS (
                               SELECT 1 FROM pg_auth_members child_edge
                               WHERE child_edge.roleid = member.oid
                           )
                           AND shobj_description(member.oid, 'pg_authid') = %s
                           AND (
                               %s <> 'crawler_api'
                               OR (
                                   SELECT count(*) = 1
                                   FROM ops_crawler_api_bindings api_binding
                                   WHERE api_binding.database_login = member.rolname::name
                                     AND api_binding.environment IN (
                                         'production', 'staging', 'development'
                                     )
                               )
                           )
                       ),
                       bool_or(member.rolname = current_user)
                FROM pg_auth_members membership
                JOIN pg_roles parent ON parent.oid = membership.roleid
                JOIN pg_roles member ON member.oid = membership.member
                WHERE parent.rolname = %s
                """,
                (expected_connection_limit, expected_marker, component, expected_group),
            )
            safe_members, current_is_member = cursor.fetchone()
            if safe_members is not True or current_is_member is not True:
                raise PreflightError(
                    f"{component} permission group has an unmanaged or unsafe member"
                )

        cursor.execute("SELECT rolconfig IS NULL FROM pg_roles WHERE rolname = current_user")
        if cursor.fetchone()[0] is not True:
            raise PreflightError(f"{component} login retains role or database session settings")
        cursor.execute(
            """
            SELECT COALESCE(array_agg(config ORDER BY config), ARRAY[]::text[])
            FROM pg_db_role_setting setting
            JOIN pg_database database ON database.oid = setting.setdatabase
            CROSS JOIN LATERAL unnest(setting.setconfig) config
            WHERE setting.setrole = current_user::regrole
              AND database.datname = current_database()
            """
        )
        database_settings = list(cursor.fetchone()[0])
        expected_database_settings = (
            [
                "default_transaction_read_only=on",
                "idle_in_transaction_session_timeout=10s",
                "lock_timeout=1s",
                "statement_timeout=5s",
            ]
            if component == "observer"
            else []
        )
        if database_settings != expected_database_settings:
            raise PreflightError(f"{component} login retains unexpected database settings")

        cursor.execute(
            """
            WITH service_role AS (
                SELECT oid FROM pg_roles WHERE rolname = current_user
            ), direct_acl AS (
                SELECT privilege.grantee
                FROM pg_database object
                CROSS JOIN LATERAL aclexplode(object.datacl) privilege
                WHERE object.datname = current_database()
                UNION ALL
                SELECT privilege.grantee
                FROM pg_namespace object
                CROSS JOIN LATERAL aclexplode(object.nspacl) privilege
                WHERE object.nspname IN ('public', 'crawl_staging')
                UNION ALL
                SELECT privilege.grantee
                FROM pg_class object
                JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
                CROSS JOIN LATERAL aclexplode(object.relacl) privilege
                WHERE namespace.nspname IN ('public', 'crawl_staging')
                UNION ALL
                SELECT privilege.grantee
                FROM pg_attribute object
                JOIN pg_class relation ON relation.oid = object.attrelid
                JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                CROSS JOIN LATERAL aclexplode(object.attacl) privilege
                WHERE namespace.nspname IN ('public', 'crawl_staging')
                UNION ALL
                SELECT privilege.grantee
                FROM pg_proc object
                JOIN pg_namespace namespace ON namespace.oid = object.pronamespace
                CROSS JOIN LATERAL aclexplode(object.proacl) privilege
                WHERE namespace.nspname IN ('public', 'crawl_staging')
            )
            SELECT NOT EXISTS (
                SELECT 1
                FROM direct_acl privilege
                JOIN service_role ON service_role.oid = privilege.grantee
            )
            """
        )
        if cursor.fetchone()[0] is not True:
            raise PreflightError(f"{component} login retains a direct application ACL")

        cursor.execute(
            "SELECT required.name, to_regclass(required.name) IS NOT NULL "
            "FROM unnest(%s::text[]) AS required(name)",
            (list(required_relations),),
        )
        missing = [name for name, exists in cursor.fetchall() if not exists]
        if missing:
            raise PreflightError(f"shared staging control schema is incomplete: {', '.join(missing)}")
        _assert_rollout_snapshot_catalog(cursor)
        _assert_quality_environment_catalog(cursor)
        cursor.execute(
            """
            SELECT count(*) = 1
               AND bool_and(singleton IS TRUE)
               AND bool_and(database_name = current_database()::name)
            FROM public.ops_crawler_control_database_marker
            """
        )
        if cursor.fetchone()[0] is not True:
            raise PreflightError("crawler control database marker does not match this database")
        cursor.execute(
            """
            SELECT crawler_index.indisunique
                   AND crawler_index.indisvalid
                   AND crawler_index.indisready
                   AND crawler_index.indislive
                   AND crawler_index.indimmediate
                   AND NOT crawler_index.indisprimary
                   AND NOT crawler_index.indisexclusion
                   AND crawler_index.indexprs IS NULL
                   AND crawler_index.indnkeyatts = 1
                   AND crawler_index.indnatts = 1
                   AND crawler_index.indkey::text = lease_token.attnum::text
                   AND access_method.amname = 'btree'
                   AND regexp_replace(
                       pg_get_expr(crawler_index.indpred, crawler_index.indrelid),
                       '\\s+', ' ', 'g'
                   ) IN ('(lease_token IS NOT NULL)', 'lease_token IS NOT NULL')
            FROM pg_index crawler_index
            JOIN pg_class index_relation ON index_relation.oid = crawler_index.indexrelid
            JOIN pg_namespace index_namespace ON index_namespace.oid = index_relation.relnamespace
            JOIN pg_class table_relation ON table_relation.oid = crawler_index.indrelid
            JOIN pg_namespace table_namespace ON table_namespace.oid = table_relation.relnamespace
            JOIN pg_attribute lease_token
              ON lease_token.attrelid = table_relation.oid
             AND lease_token.attname = 'lease_token'
             AND NOT lease_token.attisdropped
            JOIN pg_am access_method ON access_method.oid = index_relation.relam
            WHERE index_namespace.nspname = 'public'
              AND index_relation.relname = 'ux_ops_jobs_active_lease_token'
              AND table_namespace.nspname = 'public'
              AND table_relation.relname = 'ops_jobs'
            """
        )
        active_lease_index = cursor.fetchone()
        if not active_lease_index or active_lease_index[0] is not True:
            raise PreflightError("active crawler lease token unique index definition has drifted")
        cursor.execute(
            """
            SELECT crawler_index.indisunique
                   AND crawler_index.indisvalid
                   AND crawler_index.indisready
                   AND crawler_index.indislive
                   AND crawler_index.indimmediate
                   AND NOT crawler_index.indisprimary
                   AND NOT crawler_index.indisexclusion
                   AND crawler_index.indexprs IS NULL
                   AND crawler_index.indpred IS NULL
                   AND crawler_index.indnkeyatts = 1
                   AND crawler_index.indnatts = 1
                   AND crawler_index.indkey::text = code_version.attnum::text
                   AND access_method.amname = 'btree'
            FROM pg_index crawler_index
            JOIN pg_class index_relation ON index_relation.oid = crawler_index.indexrelid
            JOIN pg_namespace index_namespace ON index_namespace.oid = index_relation.relnamespace
            JOIN pg_class table_relation ON table_relation.oid = crawler_index.indrelid
            JOIN pg_namespace table_namespace ON table_namespace.oid = table_relation.relnamespace
            JOIN pg_attribute code_version
              ON code_version.attrelid = table_relation.oid
             AND code_version.attname = 'code_version'
             AND NOT code_version.attisdropped
            JOIN pg_am access_method ON access_method.oid = index_relation.relam
            WHERE index_namespace.nspname = 'public'
              AND index_relation.relname = 'ux_ops_crawler_release_artifacts_code_version'
              AND table_namespace.nspname = 'public'
              AND table_relation.relname = 'ops_crawler_release_artifacts'
            """
        )
        artifact_version_index = cursor.fetchone()
        if not artifact_version_index or artifact_version_index[0] is not True:
            raise PreflightError("crawler artifact code-version unique index definition has drifted")
        cursor.execute(
            """
            SELECT constraint.conname,
                   constraint.contype,
                   array_agg(attribute.attname ORDER BY key.ordinality)
            FROM pg_constraint constraint
            JOIN pg_class relation ON relation.oid = constraint.conrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL unnest(constraint.conkey)
                WITH ORDINALITY AS key(attnum, ordinality)
            JOIN pg_attribute attribute
              ON attribute.attrelid = relation.oid
             AND attribute.attnum = key.attnum
            WHERE namespace.nspname = 'public'
              AND relation.relname = 'ops_crawler_agent_bindings'
              AND constraint.conname IN (
                  'pk_ops_crawler_agent_bindings',
                  'ux_ops_crawler_agent_binding_agent_type'
              )
            GROUP BY constraint.conname, constraint.contype
            ORDER BY constraint.conname
            """
        )
        if cursor.fetchall() != [
            (
                "pk_ops_crawler_agent_bindings",
                "p",
                ["binding_type", "database_login"],
            ),
            (
                "ux_ops_crawler_agent_binding_agent_type",
                "u",
                ["agent_id", "binding_type"],
            ),
        ]:
            raise PreflightError("crawler agent binding uniqueness contract has drifted")
        cursor.execute(
            """
            SELECT constraint.conname,
                   constraint.contype,
                   constraint.convalidated,
                   ARRAY(
                       SELECT attribute.attname
                       FROM unnest(constraint.conkey)
                           WITH ORDINALITY AS key(attnum, ordinality)
                       JOIN pg_attribute attribute
                         ON attribute.attrelid = constraint.conrelid
                        AND attribute.attnum = key.attnum
                       ORDER BY key.ordinality
                   ),
                   COALESCE(
                       referenced_namespace.nspname || '.' || referenced.relname,
                       ''
                   ),
                   ARRAY(
                       SELECT attribute.attname
                       FROM unnest(constraint.confkey)
                           WITH ORDINALITY AS key(attnum, ordinality)
                       JOIN pg_attribute attribute
                         ON attribute.attrelid = constraint.confrelid
                        AND attribute.attnum = key.attnum
                       ORDER BY key.ordinality
                   )
            FROM pg_constraint constraint
            JOIN pg_class relation ON relation.oid = constraint.conrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            LEFT JOIN pg_class referenced ON referenced.oid = constraint.confrelid
            LEFT JOIN pg_namespace referenced_namespace
              ON referenced_namespace.oid = referenced.relnamespace
            WHERE namespace.nspname = 'public'
              AND constraint.conname IN (
                  'ux_ops_crawler_task_attempt_identity',
                  'ux_ops_crawler_task_attempt_epoch',
                  'ux_ops_crawler_task_attempt_token',
                  'ux_ops_crawler_task_attempt_observation_fk',
                  'fk_ops_crawler_task_attempt_rollout',
                  'fk_ops_crawler_task_observation_attempt'
              )
            ORDER BY constraint.conname
            """
        )
        if cursor.fetchall() != [
            (
                "fk_ops_crawler_task_attempt_rollout",
                "f",
                True,
                ["rollout_id"],
                "public.ops_crawler_release_rollouts",
                ["id"],
            ),
            (
                "fk_ops_crawler_task_observation_attempt",
                "f",
                True,
                ["attempt_id", "job_id", "attempt_no", "lease_epoch"],
                "public.ops_crawler_task_attempts",
                ["id", "job_id", "attempt_no", "lease_epoch"],
            ),
            (
                "ux_ops_crawler_task_attempt_epoch",
                "u",
                True,
                ["job_id", "lease_epoch"],
                "",
                [],
            ),
            (
                "ux_ops_crawler_task_attempt_identity",
                "u",
                True,
                ["job_id", "attempt_no"],
                "",
                [],
            ),
            (
                "ux_ops_crawler_task_attempt_observation_fk",
                "u",
                True,
                ["id", "job_id", "attempt_no", "lease_epoch"],
                "",
                [],
            ),
            (
                "ux_ops_crawler_task_attempt_token",
                "u",
                True,
                ["lease_token"],
                "",
                [],
            ),
        ]:
            raise PreflightError("crawler attempt fencing constraints have drifted")
        cursor.execute(
            """
            SELECT ARRAY(
                       SELECT attribute.attname
                       FROM pg_attribute attribute
                       WHERE attribute.attrelid = relation.oid
                         AND attribute.attname IN ('rollout_id', 'release_generation')
                         AND NOT attribute.attisdropped
                         AND attribute.attnum > 0
                         AND NOT attribute.attnotnull
                         AND (
                             (attribute.attname = 'rollout_id'
                              AND attribute.atttypid = 'uuid'::regtype)
                             OR (attribute.attname = 'release_generation'
                                 AND attribute.atttypid = 'bigint'::regtype)
                         )
                       ORDER BY attribute.attname
                   ),
                   ARRAY(
                       SELECT constraint.conname
                       FROM pg_constraint constraint
                       WHERE constraint.conrelid = relation.oid
                         AND constraint.contype = 'c'
                         AND constraint.convalidated
                         AND constraint.conname IN (
                             'chk_ops_crawler_task_attempt_release_pair',
                             'chk_ops_crawler_task_attempt_release_generation'
                         )
                       ORDER BY constraint.conname
                   ),
                   crawler_index.indisvalid
                       AND crawler_index.indisready
                       AND crawler_index.indislive
                       AND NOT crawler_index.indisunique
                       AND NOT crawler_index.indisprimary
                       AND NOT crawler_index.indisexclusion
                       AND crawler_index.indexprs IS NULL
                       AND crawler_index.indpred IS NOT NULL
                       AND crawler_index.indnkeyatts = 4
                       AND crawler_index.indnatts = 4
                       AND ARRAY(
                           SELECT attribute.attname
                           FROM unnest(crawler_index.indkey::smallint[])
                               WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute attribute
                             ON attribute.attrelid = relation.oid
                            AND attribute.attnum = key.attnum
                           ORDER BY key.ordinality
                       ) = ARRAY[
                           'rollout_id', 'release_generation', 'started_at', 'id'
                       ]::name[]
                       AND regexp_replace(
                           pg_get_expr(crawler_index.indpred, crawler_index.indrelid),
                           '\\s+', ' ', 'g'
                       ) IN ('(rollout_id IS NOT NULL)', 'rollout_id IS NOT NULL')
                       AND access_method.amname = 'btree'
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            JOIN pg_class index_relation
              ON index_relation.relname = 'idx_ops_crawler_task_attempts_release_generation'
            JOIN pg_namespace index_namespace
              ON index_namespace.oid = index_relation.relnamespace
             AND index_namespace.nspname = 'public'
            JOIN pg_index crawler_index
              ON crawler_index.indexrelid = index_relation.oid
             AND crawler_index.indrelid = relation.oid
            JOIN pg_am access_method ON access_method.oid = index_relation.relam
            WHERE namespace.nspname = 'public'
              AND relation.relname = 'ops_crawler_task_attempts'
            """
        )
        attempt_release_contract = cursor.fetchone()
        if attempt_release_contract != (
            ["release_generation", "rollout_id"],
            [
                "chk_ops_crawler_task_attempt_release_generation",
                "chk_ops_crawler_task_attempt_release_pair",
            ],
            True,
        ):
            raise PreflightError("crawler attempt release-generation contract has drifted")
        if component == "scheduler" and require_runtime_readiness:
            if environment is None:
                raise PreflightError("scheduler database preflight requires its environment contract")
            runtime_environment = _required(environment, "ENVIRONMENT")
            code_version = _required(environment, "OPS_CRAWLER_CODE_VERSION")
            artifact_digest = _required(environment, "OPS_CRAWLER_ARTIFACT_DIGEST").lower()
            config_revision = _required(environment, "OPS_CRAWLER_CONFIG_REVISION")
            cursor.execute(
                """
                SELECT EXISTS (
                           SELECT 1
                           FROM ops_crawler_release_artifacts artifact
                           WHERE artifact.artifact_digest = %s
                             AND artifact.code_version = %s
                             AND artifact.config_revision = %s
                       ),
                       EXISTS (
                           SELECT 1
                           FROM ops_crawler_worker_desired_state desired
                           JOIN ops_agents agent
                             ON agent.id = desired.agent_id
                            AND agent.environment = desired.environment
                           JOIN LATERAL (
                               SELECT report.*
                               FROM ops_crawler_release_reports report
                               WHERE report.rollout_id = desired.rollout_id
                                 AND report.environment = desired.environment
                                 AND report.worker_key = desired.worker_key
                                 AND report.agent_id = desired.agent_id
                                 AND report.desired_generation = desired.generation
                               ORDER BY report.reported_at DESC,
                                        report.created_at DESC,
                                        report.id DESC
                               LIMIT 1
                           ) latest_report ON true
                           WHERE desired.environment = %s
                             AND desired.desired_status = 'active'
                             AND desired.not_before <= CURRENT_TIMESTAMP
                             AND desired.artifact_digest = %s
                             AND desired.code_version = %s
                             AND desired.config_revision = %s
                             AND agent.status = 'healthy'
                             AND agent.maintenance_mode IS FALSE
                             AND agent.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '2 minutes'
                             AND EXISTS (
                                 SELECT 1 FROM ops_crawler_agent_bindings binding
                                 WHERE binding.agent_id = desired.agent_id
                                   AND binding.environment = desired.environment
                                   AND binding.binding_type = 'worker'
                             )
                             AND EXISTS (
                                 SELECT 1 FROM ops_crawler_agent_bindings binding
                                 WHERE binding.agent_id = desired.agent_id
                                   AND binding.environment = desired.environment
                                   AND binding.binding_type = 'reporter'
                             )
                             AND latest_report.status = 'ready'
                             AND latest_report.artifact_digest = desired.artifact_digest
                             AND latest_report.code_version = desired.code_version
                             AND latest_report.config_revision = desired.config_revision
                       )
                """,
                (
                    artifact_digest,
                    code_version,
                    config_revision,
                    runtime_environment,
                    artifact_digest,
                    code_version,
                    config_revision,
                ),
            )
            artifact_exists, compatible_worker_exists = cursor.fetchone()
            if artifact_exists is not True or compatible_worker_exists is not True:
                raise PreflightError("scheduler artifact or active compatible worker is not registered")
        if component in {"worker", "reporter"}:
            if environment is None:
                raise PreflightError("crawler agent preflight requires its environment contract")
            agent_id = _required(environment, "OPS_AGENT_ID")
            worker_key = _required(environment, "OPS_CRAWLER_WORKER_ID")
            hostname = _required(environment, "OPS_CRAWLER_WORKER_HOSTNAME").lower().rstrip(".")
            runtime_environment = _required(environment, "ENVIRONMENT")
            login = identity[1]
            helper_prefix = "worker" if component == "worker" else "reporter"
            expected_hint = f"crawler-worker:{login}" if component == "worker" else None
            cursor.execute(
                f"""
                SELECT (
                           SELECT public.current_crawler_{helper_prefix}_agent_id()::text
                       ),
                       (
                           SELECT public.current_crawler_{helper_prefix}_environment()
                       ),
                       count(*) FILTER (
                           WHERE agent.id = %s::uuid
                             AND agent.environment = %s
                             AND agent.hostname = %s
                             AND agent.status IN ('unknown', 'healthy')
                             AND agent.maintenance_mode IS FALSE
                             AND (
                                 %s::text IS NULL
                                 OR agent.credential_hint = %s
                             )
                       ),
                       count(*) FILTER (
                           WHERE agent.id = %s::uuid
                             AND agent.credential_hint LIKE 'crawler-worker:%%'
                       )
                FROM public.ops_agents agent
                """,
                (
                    agent_id,
                    runtime_environment,
                    hostname,
                    expected_hint,
                    expected_hint,
                    agent_id,
                ),
            )
            bound_agent, bound_environment, exact_agents, worker_hints = cursor.fetchone()
            if (
                bound_agent != agent_id
                or bound_environment != runtime_environment
                or exact_agents != 1
                or worker_hints != 1
            ):
                raise PreflightError(f"{component} login is not bound to its exact crawler agent")
            if require_runtime_readiness:
                if component == "worker":
                    cursor.execute(
                        """
                        SELECT count(*)
                        FROM public.ops_crawler_worker_desired_state desired
                        WHERE desired.agent_id = %s::uuid
                          AND desired.environment = %s
                          AND desired.worker_key = %s
                          AND desired.desired_status IN ('active', 'draining')
                          AND desired.not_before <= CURRENT_TIMESTAMP
                          AND desired.code_version = %s
                          AND desired.artifact_digest = %s
                          AND desired.config_revision = %s
                        """,
                        (
                            agent_id,
                            runtime_environment,
                            worker_key,
                            _required(environment, "OPS_CRAWLER_CODE_VERSION"),
                            _required(environment, "OPS_CRAWLER_ARTIFACT_DIGEST").lower(),
                            _required(environment, "OPS_CRAWLER_CONFIG_REVISION"),
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT count(*)
                        FROM public.ops_crawler_worker_desired_state desired
                        WHERE desired.agent_id = %s::uuid
                          AND desired.environment = %s
                          AND desired.worker_key = %s
                          AND desired.desired_status IN ('active', 'draining', 'disabled')
                        """,
                        (agent_id, runtime_environment, worker_key),
                    )
                if cursor.fetchone()[0] != 1:
                    raise PreflightError(
                        f"{component} agent has no exact runnable/reportable desired-state assignment"
                    )
        cursor.execute(
            """
            SELECT count(*) = 2
                   AND bool_and(procedure.prosecdef)
                   AND bool_and(
                       'search_path=pg_catalog, public' = ANY(
                           COALESCE(procedure.proconfig, ARRAY[]::text[])
                       )
                   )
                   AND bool_and(NOT EXISTS (
                       SELECT 1
                       FROM aclexplode(
                           COALESCE(
                               procedure.proacl,
                               acldefault('f', procedure.proowner)
                           )
                       ) privilege
                       WHERE privilege.grantee = 0
                         AND privilege.privilege_type = 'EXECUTE'
                   ))
            FROM pg_proc procedure
            WHERE procedure.oid IN (
                to_regprocedure('public.enforce_current_crawler_lease()'),
                to_regprocedure('public.capture_fenced_crawler_snapshot()')
            )
            """
        )
        if cursor.fetchone()[0] is not True:
            raise PreflightError("crawler lease/snapshot SECURITY DEFINER contract is invalid")
        cursor.execute(
            """
            SELECT procedure.proname,
                   procedure.prosrc,
                   procedure.proowner = public_namespace.nspowner,
                   NOT owner.rolcanlogin
                       AND NOT owner.rolsuper
                       AND NOT owner.rolcreaterole
                       AND NOT owner.rolbypassrls,
                   procedure.prosecdef,
                   'search_path=pg_catalog, public' = ANY(
                       COALESCE(procedure.proconfig, ARRAY[]::text[])
                   ),
                   NOT EXISTS (
                       SELECT 1
                       FROM aclexplode(
                           COALESCE(
                               procedure.proacl,
                               acldefault('f', procedure.proowner)
                           )
                       ) privilege
                       WHERE privilege.grantee = 0
                         AND privilege.privilege_type = 'EXECUTE'
                   )
            FROM pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
            JOIN pg_namespace public_namespace ON public_namespace.nspname = 'public'
            JOIN pg_roles owner ON owner.oid = procedure.proowner
            WHERE namespace.nspname = 'public'
              AND procedure.proname = ANY(%s::text[])
            ORDER BY procedure.proname
            """,
            (list(REVIEWED_FUNCTION_FILES),),
        )
        reviewed_sources = _reviewed_function_sources()
        live_sources = cursor.fetchall()
        if len(live_sources) != len(reviewed_sources) or any(
            reviewed_sources.get(name) != str(source).strip()
            or owner_matches is not True
            or owner_is_safe is not True
            or security_definer is not (name in SECURITY_DEFINER_FUNCTIONS)
            or (
                name != "mooncen_reject_immutable_crawler_evidence"
                and search_path_is_safe is not True
            )
            or public_execute_revoked is not True
            for (
                name,
                source,
                owner_matches,
                owner_is_safe,
                security_definer,
                search_path_is_safe,
                public_execute_revoked,
            ) in live_sources
        ):
            raise PreflightError("crawler fencing function owner or reviewed body has drifted")
        cursor.execute(
            """
            SELECT namespace.nspname,
                   relation.relname,
                   trigger.tgname,
                   procedure_namespace.nspname,
                   procedure.proname,
                   pg_get_function_identity_arguments(procedure.oid),
                   trigger.tgtype::integer,
                   trigger.tgenabled,
                   trigger.tgqual IS NULL
            FROM pg_trigger trigger
            JOIN pg_class relation ON relation.oid = trigger.tgrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            JOIN pg_proc procedure ON procedure.oid = trigger.tgfoid
            JOIN pg_namespace procedure_namespace ON procedure_namespace.oid = procedure.pronamespace
            WHERE NOT trigger.tgisinternal
              AND namespace.nspname = 'public'
              AND relation.relname IN ('branches', 'courses')
              AND procedure_namespace.nspname = 'public'
              AND procedure.proname IN (
                  'enforce_current_crawler_lease',
                  'capture_fenced_crawler_snapshot'
              )
            ORDER BY relation.relname, trigger.tgname
            """
        )
        fencing_triggers = cursor.fetchall()
        expected_fencing_triggers = {
            ("public", "branches", "zz_enforce_current_crawler_lease", "public", "enforce_current_crawler_lease", "", 23),
            ("public", "courses", "zz_enforce_current_crawler_lease", "public", "enforce_current_crawler_lease", "", 23),
            (
                "public",
                "branches",
                "zz_capture_fenced_crawler_snapshot",
                "public",
                "capture_fenced_crawler_snapshot",
                "",
                21,
            ),
            (
                "public",
                "courses",
                "zz_capture_fenced_crawler_snapshot",
                "public",
                "capture_fenced_crawler_snapshot",
                "",
                21,
            ),
        }
        if (
            {tuple(row[:-2]) for row in fencing_triggers} != expected_fencing_triggers
            or any(row[-2] not in {"O", "A"} or row[-1] is not True for row in fencing_triggers)
        ):
            raise PreflightError("crawler fencing triggers are missing or disabled")
        cursor.execute(
            """
            SELECT relation.relname,
                   trigger.tgname,
                   procedure.proname,
                   pg_get_function_identity_arguments(procedure.oid),
                   trigger.tgtype::integer,
                   trigger.tgdeferrable,
                   trigger.tginitdeferred,
                   trigger.tgenabled,
                   trigger.tgqual IS NULL
            FROM pg_trigger trigger
            JOIN pg_class relation ON relation.oid = trigger.tgrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            JOIN pg_proc procedure ON procedure.oid = trigger.tgfoid
            JOIN pg_namespace procedure_namespace
              ON procedure_namespace.oid = procedure.pronamespace
            WHERE NOT trigger.tgisinternal
              AND namespace.nspname = 'public'
              AND procedure_namespace.nspname = 'public'
              AND trigger.tgname IN (
                  'zz_enforce_crawler_worker_agent_heartbeat',
                  'zz_enforce_crawler_worker_job_transition',
                  'zz_enforce_crawler_worker_active_attempt',
                  'zz_enforce_crawler_worker_attempt_insert',
                  'zz_enforce_crawler_worker_attempt_transition',
                  'zz_enforce_crawler_worker_observation_insert',
                  'zz_enforce_crawler_worker_terminal_job_commit',
                  'zz_enforce_crawler_worker_terminal_attempt_commit',
                  'zz_enforce_crawler_promotion_role_separation',
                  'zz_enforce_crawler_worker_runtime_evidence',
                  'zz_enforce_crawler_release_report_timestamp',
                  'zz_enforce_crawler_release_action_transition',
                  'zy_require_crawler_release_action_approval',
                  'zzz_stamp_crawler_release_action_request_digest',
                  'zz_reject_crawler_release_approval_mutation',
                  'zz_enforce_crawler_rollout_snapshot_insert',
                  'zz_enforce_crawler_rollout_snapshot_commit',
                  'zz_enforce_crawler_rollout_snapshot_requirement',
                  'zy_enforce_crawler_attempt_release_generation_insert',
                  'zy_enforce_crawler_attempt_release_generation_immutable'
              )
            ORDER BY relation.relname, trigger.tgname
            """
        )
        runtime_triggers = cursor.fetchall()
        expected_runtime_triggers = {
            (
                "ops_agents",
                "zz_enforce_crawler_worker_agent_heartbeat",
                "enforce_crawler_worker_agent_heartbeat",
                "",
                19,
                False,
                False,
            ),
            (
                "ops_jobs",
                "zz_enforce_crawler_worker_job_transition",
                "enforce_crawler_worker_job_transition",
                "",
                19,
                False,
                False,
            ),
            (
                "ops_jobs",
                "zz_enforce_crawler_worker_active_attempt",
                "enforce_crawler_worker_active_attempt",
                "",
                21,
                True,
                True,
            ),
            (
                "ops_crawler_task_attempts",
                "zy_enforce_crawler_attempt_release_generation_insert",
                "enforce_crawler_attempt_release_generation_insert",
                "",
                7,
                False,
                False,
            ),
            (
                "ops_crawler_task_attempts",
                "zy_enforce_crawler_attempt_release_generation_immutable",
                "enforce_crawler_attempt_release_generation_immutable",
                "",
                19,
                False,
                False,
            ),
            (
                "ops_crawler_task_attempts",
                "zz_enforce_crawler_worker_attempt_insert",
                "enforce_crawler_worker_attempt_insert",
                "",
                7,
                False,
                False,
            ),
            (
                "ops_crawler_task_attempts",
                "zz_enforce_crawler_worker_attempt_transition",
                "enforce_crawler_worker_attempt_transition",
                "",
                19,
                False,
                False,
            ),
            (
                "ops_crawler_task_observations",
                "zz_enforce_crawler_worker_observation_insert",
                "enforce_crawler_worker_observation_insert",
                "",
                7,
                False,
                False,
            ),
            (
                "ops_crawler_release_reports",
                "zz_enforce_crawler_release_report_timestamp",
                "enforce_crawler_release_report_timestamp",
                "",
                7,
                False,
                False,
            ),
            (
                "ops_crawler_release_action_requests",
                "zz_enforce_crawler_release_action_transition",
                "enforce_crawler_release_action_transition",
                "",
                31,
                False,
                False,
            ),
            (
                "ops_crawler_release_action_requests",
                "zy_require_crawler_release_action_approval",
                "require_crawler_release_action_approval",
                "",
                19,
                False,
                False,
            ),
            (
                "ops_crawler_release_action_requests",
                "zzz_stamp_crawler_release_action_request_digest",
                "stamp_crawler_release_action_request_digest",
                "",
                23,
                False,
                False,
            ),
            (
                "ops_crawler_release_action_approvals",
                "zz_reject_crawler_release_approval_mutation",
                "reject_crawler_release_approval_mutation",
                "",
                27,
                False,
                False,
            ),
            (
                "ops_crawler_release_policy_contract",
                "zz_reject_crawler_release_policy_contract_mutation",
                "reject_crawler_release_approval_mutation",
                "",
                27,
                False,
                False,
            ),
            (
                "ops_crawler_rollout_worker_snapshots",
                "zz_enforce_crawler_rollout_snapshot_insert",
                "enforce_crawler_rollout_snapshot_insert",
                "",
                7,
                False,
                False,
            ),
            (
                "ops_crawler_release_rollouts",
                "zz_enforce_crawler_rollout_snapshot_requirement",
                "enforce_crawler_rollout_snapshot_requirement",
                "",
                23,
                False,
                False,
            ),
            (
                "ops_crawler_release_rollouts",
                "zz_enforce_crawler_rollout_snapshot_commit",
                "enforce_crawler_rollout_snapshot_commit",
                "",
                21,
                True,
                True,
            ),
            (
                "ops_jobs",
                "zz_enforce_crawler_worker_terminal_job_commit",
                "enforce_crawler_worker_terminal_job_commit",
                "",
                17,
                True,
                True,
            ),
            (
                "ops_crawler_task_attempts",
                "zz_enforce_crawler_worker_terminal_attempt_commit",
                "enforce_crawler_worker_terminal_attempt_commit",
                "",
                17,
                True,
                True,
            ),
            (
                "crawl_batches",
                "zz_enforce_crawler_promotion_role_separation",
                "enforce_crawler_promotion_role_separation",
                "",
                23,
                False,
                False,
            ),
            (
                "crawler_run_log",
                "zz_enforce_crawler_worker_runtime_evidence",
                "enforce_crawler_worker_runtime_evidence",
                "",
                23,
                False,
                False,
            ),
            (
                "crawl_progress",
                "zz_enforce_crawler_worker_runtime_evidence",
                "enforce_crawler_worker_runtime_evidence",
                "",
                23,
                False,
                False,
            ),
        }
        if (
            {tuple(row[:-2]) for row in runtime_triggers} != expected_runtime_triggers
            or any(row[-2] not in {"O", "A"} or row[-1] is not True for row in runtime_triggers)
        ):
            raise PreflightError("crawler identity, evidence, or approval trigger contract drifted")
        cursor.execute(
            """
            SELECT relation.relname, relation.relrowsecurity, relation.relforcerowsecurity
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relname = ANY(%s::text[])
            ORDER BY relation.relname
            """,
            (list(RLS_TABLES),),
        )
        rls_rows = cursor.fetchall()
        if (
            {row[0] for row in rls_rows} != set(RLS_TABLES)
            or any(
                row[1] is not True
                or row[2] is not (
                    row[0]
                    in {
                        "ops_crawler_release_action_requests",
                        "ops_crawler_release_approver_bindings",
                        "ops_crawler_release_action_approvals",
                        "ops_crawler_release_action_consumers",
                        "course_quality_score",
                        "ops_quality_issues",
                    }
                )
                for row in rls_rows
            )
        ):
            raise PreflightError("crawler runtime RLS is missing, disabled, or unexpectedly forced")
        cursor.execute(
            """
            SELECT relation.relname,
                   policy.polname,
                   policy.polpermissive,
                   policy.polcmd,
                   policy.polroles = ARRAY[0]::oid[],
                   pg_get_expr(policy.polqual, policy.polrelid),
                   pg_get_expr(policy.polwithcheck, policy.polrelid)
            FROM pg_policy policy
            JOIN pg_class relation ON relation.oid = policy.polrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relname = ANY(%s::text[])
            ORDER BY relation.relname, policy.polname
            """,
            (list(RLS_TABLES),),
        )
        policy_rows = cursor.fetchall()
        expected_policies = {
            ("ops_agents", "crawler_worker_agent_scope", True, "*", True),
            ("ops_agents", "crawler_managed_agent_isolation", False, "*", True),
            ("ops_agents", "crawler_api_agent_environment_scope", False, "r", True),
            ("ops_jobs", "crawler_worker_job_scope", True, "*", True),
            ("ops_jobs", "crawler_control_job_isolation", False, "*", True),
            ("ops_jobs", "crawler_api_job_environment_scope", False, "r", True),
            ("ops_job_logs", "crawler_worker_job_log_scope", True, "r", True),
            ("ops_job_logs", "crawler_worker_job_log_insert_scope", True, "a", True),
            ("ops_job_logs", "crawler_control_job_log_isolation", False, "*", True),
            ("ops_crawler_runs", "crawler_worker_run_scope", True, "r", True),
            ("ops_crawler_runs", "crawler_worker_run_update_scope", True, "w", True),
            ("ops_crawler_runs", "crawler_nonworker_run_insert_scope", True, "a", True),
            ("ops_crawler_runs", "crawler_control_run_isolation", False, "*", True),
            ("ops_crawler_runs", "crawler_api_run_environment_scope", False, "r", True),
            (
                "ops_crawler_task_attempts",
                "crawler_worker_attempt_scope",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_task_attempts",
                "crawler_api_task_attempt_environment_scope",
                False,
                "r",
                True,
            ),
            (
                "ops_crawler_task_observations",
                "crawler_worker_observation_scope",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_task_observations",
                "crawler_api_task_observation_environment_scope",
                False,
                "r",
                True,
            ),
            (
                "ops_crawler_release_reports",
                "crawler_worker_release_report_scope",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_release_reports",
                "crawler_api_report_environment_scope",
                False,
                "r",
                True,
            ),
            (
                "ops_crawler_worker_desired_state",
                "crawler_runtime_desired_state_scope",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_worker_desired_state",
                "crawler_api_desired_environment_scope",
                False,
                "r",
                True,
            ),
            (
                "ops_crawler_release_rollouts",
                "crawler_api_rollout_environment_scope",
                False,
                "r",
                True,
            ),
            (
                "ops_crawler_rollout_worker_snapshots",
                "crawler_api_rollout_worker_snapshot_environment_scope",
                False,
                "r",
                True,
            ),
            (
                "ops_crawler_rollout_worker_snapshots",
                "crawler_rollout_worker_snapshot_acl_access",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_release_rollouts",
                "crawler_release_rollout_acl_access",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_batches",
                "crawler_api_batch_environment_scope",
                False,
                "r",
                True,
            ),
            (
                "ops_crawler_batch_tasks",
                "crawler_api_batch_task_environment_scope",
                False,
                "r",
                True,
            ),
            (
                "ops_crawler_batch_tasks",
                "crawler_batch_task_acl_access",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_batches",
                "crawler_batch_acl_access",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_agent_bindings",
                "crawler_api_agent_binding_environment_scope",
                False,
                "r",
                True,
            ),
            (
                "ops_crawler_agent_bindings",
                "crawler_agent_binding_acl_access",
                True,
                "*",
                True,
            ),
            (
                "crawl_batches",
                "crawler_api_staging_batch_environment_scope",
                False,
                "r",
                True,
            ),
            (
                "crawl_batches",
                "crawler_staging_batch_acl_access",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_release_action_requests",
                "crawler_release_action_api_select",
                True,
                "r",
                False,
            ),
            (
                "ops_crawler_release_action_requests",
                "crawler_release_action_api_insert",
                True,
                "a",
                False,
            ),
            (
                "ops_crawler_release_action_requests",
                "crawler_release_action_admin_select",
                True,
                "r",
                False,
            ),
            (
                "ops_crawler_release_action_requests",
                "crawler_release_action_admin_update",
                True,
                "w",
                False,
            ),
            (
                "ops_crawler_release_action_requests",
                "crawler_release_action_approval_owner_select",
                True,
                "r",
                True,
            ),
            (
                "ops_crawler_release_approver_bindings",
                "crawler_release_approver_binding_owner_access",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_release_action_approvals",
                "crawler_release_approval_owner_access",
                True,
                "*",
                True,
            ),
            (
                "ops_crawler_release_action_approvals",
                "crawler_release_approval_api_select",
                True,
                "r",
                False,
            ),
            (
                "ops_crawler_release_action_approvals",
                "crawler_release_approval_admin_select",
                True,
                "r",
                False,
            ),
            (
                "ops_crawler_release_action_consumers",
                "crawler_release_action_consumer_owner_access",
                True,
                "*",
                True,
            ),
            ("crawler_run_log", "crawler_worker_run_log_scope", True, "*", True),
            ("crawler_run_log", "crawler_managed_run_log_isolation", False, "*", True),
            ("crawl_progress", "crawler_worker_progress_scope", True, "*", True),
            ("crawl_progress", "crawler_managed_progress_isolation", False, "*", True),
            (
                "course_quality_score",
                "crawler_quality_score_acl_access",
                True,
                "*",
                True,
            ),
            (
                "course_quality_score",
                "crawler_api_quality_score_staging_scope",
                False,
                "r",
                True,
            ),
            (
                "ops_quality_issues",
                "crawler_quality_issue_acl_access",
                True,
                "*",
                True,
            ),
            (
                "ops_quality_issues",
                "crawler_api_quality_issue_staging_scope",
                False,
                "r",
                True,
            ),
        }
        if {tuple(row[:5]) for row in policy_rows} != expected_policies:
            raise PreflightError("crawler runtime RLS policy set or policy mode has drifted")
        exact_quality_policy_expressions = {
            "crawler_quality_score_acl_access": ("true", "true"),
            "crawler_quality_issue_acl_access": ("true", "true"),
            "crawler_api_quality_score_staging_scope": (
                "casewhenpg_has_rolesession_user,'mooncen_crawler_api','member'"
                "thencurrent_crawler_api_environment='staging'elsetrueend",
                "",
            ),
            "crawler_api_quality_issue_staging_scope": (
                "casewhenpg_has_rolesession_user,'mooncen_crawler_api','member'"
                "thencurrent_crawler_api_environment='staging'elsetrueend",
                "",
            ),
        }
        for _, policy_name, _, _, _, using_expression, check_expression in policy_rows:
            if policy_name not in exact_quality_policy_expressions:
                continue
            actual = tuple(
                _compact_sql(expression)
                .replace("::text", "")
                .replace("::name", "")
                .replace("public.", "")
                .replace("(", "")
                .replace(")", "")
                for expression in (using_expression, check_expression)
            )
            if actual != exact_quality_policy_expressions[policy_name]:
                raise PreflightError(
                    f"shared staging quality RLS expression has drifted: {policy_name}"
                )
        policy_requirements = {
            "crawler_worker_agent_scope": (
                "mooncen_crawler_worker",
                "mooncen_crawler_reporter",
                "current_crawler_worker_agent_id",
                "current_crawler_reporter_agent_id",
            ),
            "crawler_managed_agent_isolation": ("is_crawler_managed_agent",),
            "crawler_api_agent_environment_scope": ("current_crawler_api_environment",),
            "crawler_worker_job_scope": (
                "current_crawler_worker_agent_id",
                "current_crawler_worker_environment",
            ),
            "crawler_control_job_isolation": ("is_crawler_control_job",),
            "crawler_api_job_environment_scope": ("current_crawler_api_environment",),
            "crawler_worker_job_log_scope": ("is_current_crawler_worker_job",),
            "crawler_worker_job_log_insert_scope": ("is_live_crawler_worker_job",),
            "crawler_control_job_log_isolation": (
                "is_crawler_control_job",
                "is_current_crawler_worker_job",
                "is_live_crawler_worker_job",
            ),
            "crawler_worker_run_scope": ("is_current_crawler_worker_job",),
            "crawler_worker_run_update_scope": ("is_live_crawler_worker_job",),
            "crawler_nonworker_run_insert_scope": ("mooncen_crawler_worker",),
            "crawler_control_run_isolation": (
                "is_crawler_control_job",
                "is_current_crawler_worker_job",
                "is_live_crawler_worker_job",
            ),
            "crawler_api_run_environment_scope": ("current_crawler_api_environment",),
            "crawler_worker_attempt_scope": ("current_crawler_worker_agent_id",),
            "crawler_api_task_attempt_environment_scope": (
                "current_crawler_api_environment",
            ),
            "crawler_worker_observation_scope": ("current_crawler_worker_agent_id",),
            "crawler_api_task_observation_environment_scope": (
                "current_crawler_api_environment",
            ),
            "crawler_worker_release_report_scope": (
                "current_crawler_reporter_agent_id",
                "current_crawler_reporter_environment",
            ),
            "crawler_api_report_environment_scope": ("current_crawler_api_environment",),
            "crawler_runtime_desired_state_scope": (
                "current_crawler_worker_agent_id",
                "current_crawler_reporter_agent_id",
            ),
            "crawler_api_desired_environment_scope": ("current_crawler_api_environment",),
            "crawler_api_rollout_environment_scope": ("current_crawler_api_environment",),
            "crawler_release_rollout_acl_access": ("true",),
            "crawler_api_rollout_worker_snapshot_environment_scope": (
                "current_crawler_api_environment",
            ),
            "crawler_rollout_worker_snapshot_acl_access": ("true",),
            "crawler_api_batch_environment_scope": ("current_crawler_api_environment",),
            "crawler_batch_acl_access": ("true",),
            "crawler_api_batch_task_environment_scope": (
                "current_crawler_api_environment",
            ),
            "crawler_batch_task_acl_access": ("true",),
            "crawler_api_agent_binding_environment_scope": (
                "current_crawler_api_environment",
            ),
            "crawler_agent_binding_acl_access": ("true",),
            "crawler_api_staging_batch_environment_scope": (
                "current_crawler_api_environment",
            ),
            "crawler_staging_batch_acl_access": ("true",),
            "crawler_release_action_api_select": (
                "current_crawler_api_environment",
            ),
            "crawler_release_action_api_insert": (
                "session_user",
                "requester_login",
                "current_crawler_api_environment",
                "status",
                "attempt_count",
            ),
            "crawler_release_action_admin_select": ("true",),
            "crawler_release_action_admin_update": ("true",),
            "crawler_release_action_approval_owner_select": (
                "current_user",
                "relowner",
                "session_user",
                "mooncen_crawler_release_approver",
                "requester_login",
                "ops_crawler_release_approver_bindings",
                "environment",
                "enabled",
            ),
            "crawler_release_approver_binding_owner_access": (
                "current_user",
                "relowner",
                "ops_crawler_release_approver_bindings",
            ),
            "crawler_release_approval_owner_access": (
                "current_user",
                "relowner",
                "ops_crawler_release_action_approvals",
            ),
            "crawler_release_approval_api_select": (
                "current_crawler_api_environment",
            ),
            "crawler_release_approval_admin_select": ("true",),
            "crawler_release_action_consumer_owner_access": (
                "current_user",
                "relowner",
                "ops_crawler_release_action_consumers",
            ),
            "crawler_worker_run_log_scope": ("current_crawler_worker_agent_id",),
            "crawler_managed_run_log_isolation": ("current_crawler_worker_agent_id",),
            "crawler_worker_progress_scope": ("current_crawler_worker_agent_id",),
            "crawler_managed_progress_isolation": ("current_crawler_worker_agent_id",),
            "crawler_quality_score_acl_access": ("true",),
            "crawler_api_quality_score_staging_scope": (
                "pg_has_role",
                "session_user",
                "mooncen_crawler_api",
                "current_crawler_api_environment",
                "staging",
            ),
            "crawler_quality_issue_acl_access": ("true",),
            "crawler_api_quality_issue_staging_scope": (
                "pg_has_role",
                "session_user",
                "mooncen_crawler_api",
                "current_crawler_api_environment",
                "staging",
            ),
        }
        for _, policy_name, _, command, _, using_expression, check_expression in policy_rows:
            if (
                command in {"*", "r", "w", "d"} and using_expression is None
            ) or (command in {"*", "a", "w"} and check_expression is None):
                raise PreflightError(f"crawler RLS policy is incomplete: {policy_name}")
            normalized_expression = re.sub(
                r"\s+", "", f"{using_expression or ''}{check_expression or ''}"
            ).lower()
            if any(
                required.lower() not in normalized_expression
                for required in policy_requirements[policy_name]
            ):
                raise PreflightError(f"crawler RLS policy expression has drifted: {policy_name}")
        if component == "crawler_api" and runtime_environment != "staging":
            cursor.execute(
                """
                SELECT NOT EXISTS (SELECT 1 FROM course_quality_score),
                       NOT EXISTS (SELECT 1 FROM ops_quality_issues)
                """
            )
            if cursor.fetchone() != (True, True):
                raise PreflightError(
                    "non-staging crawler API can read shared staging quality rows"
                )
        _reviewed_contract_markers(cursor)
        cursor.execute(
            """
            SELECT namespace.nspname,
                   relation.relname,
                   trigger.tgname,
                   procedure_namespace.nspname,
                   procedure.proname,
                   pg_get_function_identity_arguments(procedure.oid),
                   trigger.tgtype::integer,
                   trigger.tgenabled,
                   trigger.tgqual IS NULL
            FROM pg_trigger trigger
            JOIN pg_class relation ON relation.oid = trigger.tgrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            JOIN pg_proc procedure ON procedure.oid = trigger.tgfoid
            JOIN pg_namespace procedure_namespace ON procedure_namespace.oid = procedure.pronamespace
            WHERE NOT trigger.tgisinternal
              AND namespace.nspname IN ('public', 'crawl_staging')
              AND relation.relname IN (
                  'fenced_branch_snapshots',
                  'fenced_course_snapshots',
                   'ops_crawler_task_observations',
                   'ops_crawler_release_artifacts',
                   'ops_crawler_release_reports',
                   'ops_crawler_rollout_worker_snapshots'
              )
              AND procedure_namespace.nspname = 'public'
              AND procedure.proname = 'mooncen_reject_immutable_crawler_evidence'
            ORDER BY relation.relname, trigger.tgname
            """
        )
        immutable_triggers = cursor.fetchall()
        expected_immutable_triggers = {
            (
                "public",
                "ops_crawler_task_observations",
                "trg_ops_crawler_task_observations_immutable",
                "public",
                "mooncen_reject_immutable_crawler_evidence",
                "",
                27,
            ),
            (
                "public",
                "ops_crawler_release_artifacts",
                "trg_ops_crawler_release_artifacts_immutable",
                "public",
                "mooncen_reject_immutable_crawler_evidence",
                "",
                27,
            ),
            (
                "public",
                "ops_crawler_release_reports",
                "trg_ops_crawler_release_reports_immutable",
                "public",
                "mooncen_reject_immutable_crawler_evidence",
                "",
                27,
            ),
            (
                "public",
                "ops_crawler_rollout_worker_snapshots",
                "trg_ops_crawler_rollout_worker_snapshots_immutable",
                "public",
                "mooncen_reject_immutable_crawler_evidence",
                "",
                27,
            ),
            (
                "crawl_staging",
                "fenced_branch_snapshots",
                "trg_fenced_branch_snapshots_immutable",
                "public",
                "mooncen_reject_immutable_crawler_evidence",
                "",
                27,
            ),
            (
                "crawl_staging",
                "fenced_course_snapshots",
                "trg_fenced_course_snapshots_immutable",
                "public",
                "mooncen_reject_immutable_crawler_evidence",
                "",
                27,
            ),
        }
        if (
            {tuple(row[:-2]) for row in immutable_triggers} != expected_immutable_triggers
            or any(row[-2] not in {"O", "A"} or row[-1] is not True for row in immutable_triggers)
        ):
            raise PreflightError("fenced crawler snapshot immutability triggers are unavailable")

        privilege_sql = {
            "scheduler": """
                SELECT pg_has_role(current_user, 'mooncen_crawler_control', 'member')
                   AND has_table_privilege(current_user, 'ops_jobs', 'SELECT')
                   AND has_table_privilege(current_user, 'ops_jobs', 'INSERT')
                   AND has_table_privilege(current_user, 'ops_jobs', 'UPDATE')
                   AND has_table_privilege(current_user, 'ops_crawler_runs', 'SELECT')
                   AND has_table_privilege(current_user, 'ops_crawler_runs', 'INSERT')
                   AND has_table_privilege(current_user, 'ops_crawler_runs', 'UPDATE')
                   AND has_table_privilege(current_user, 'ops_job_logs', 'SELECT')
                   AND has_table_privilege(current_user, 'ops_job_logs', 'INSERT')
                   AND has_table_privilege(current_user, 'ops_crawler_batches', 'INSERT')
                   AND has_table_privilege(current_user, 'ops_crawler_batch_tasks', 'INSERT')
                   AND has_column_privilege(
                       current_user, 'ops_crawler_task_attempts', 'status', 'UPDATE'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_task_attempts', 'finished_at', 'UPDATE'
                   )
                   AND has_table_privilege(current_user, 'ops_crawler_task_observations', 'INSERT')
            """,
            "publisher": """
                SELECT pg_has_role(current_user, 'mooncen_crawler_publisher', 'member')
                   AND has_table_privilege(current_user, 'ops_crawler_release_artifacts', 'SELECT')
                   AND has_table_privilege(current_user, 'ops_crawler_release_rollouts', 'SELECT')
                   AND has_table_privilege(current_user, 'ops_crawler_worker_desired_state', 'SELECT')
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'ops_crawler_batches', 'INSERT')
            """,
            "finalizer": """
                SELECT pg_has_role(current_user, 'mooncen_crawler_finalizer', 'member')
                   AND has_table_privilege(current_user, 'ops_jobs', 'SELECT')
                   AND has_column_privilege(current_user, 'ops_crawler_batches', 'status', 'UPDATE')
                   AND has_column_privilege(current_user, 'ops_crawler_batches', 'started_at', 'UPDATE')
                   AND has_column_privilege(current_user, 'ops_crawler_batches', 'finished_at', 'UPDATE')
                   AND has_column_privilege(current_user, 'crawl_batches', 'status', 'UPDATE')
                   AND has_column_privilege(current_user, 'crawl_batches', 'finished_at', 'UPDATE')
                   AND has_column_privilege(current_user, 'crawl_batches', 'total_branches', 'UPDATE')
                   AND has_column_privilege(current_user, 'crawl_batches', 'total_courses', 'UPDATE')
                   AND has_column_privilege(current_user, 'crawl_batches', 'valid_courses', 'UPDATE')
                   AND has_column_privilege(current_user, 'crawl_batches', 'invalid_courses', 'UPDATE')
                   AND has_column_privilege(current_user, 'crawl_batches', 'result', 'UPDATE')
                   AND has_column_privilege(current_user, 'crawl_batches', 'updated_at', 'UPDATE')
                   AND has_table_privilege(current_user, 'crawl_staging.fenced_branch_snapshots', 'SELECT')
                   AND has_table_privilege(current_user, 'crawl_staging.fenced_course_snapshots', 'SELECT')
                   AND has_table_privilege(current_user, 'crawl_batches', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'branches', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'branches', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'courses', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'courses', 'UPDATE')
            """,
            "approver": """
                SELECT pg_has_role(current_user, 'mooncen_crawler_approver', 'member')
                   AND has_table_privilege(current_user, 'crawl_batches', 'SELECT')
                   AND has_column_privilege(current_user, 'crawl_batches', 'result', 'UPDATE')
                   AND has_table_privilege(current_user, 'crawl_staging.fenced_branch_snapshots', 'SELECT')
                   AND has_table_privilege(current_user, 'crawl_staging.fenced_course_snapshots', 'SELECT')
                   AND NOT has_table_privilege(current_user, 'crawl_batches', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'branches', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'courses', 'UPDATE')
            """,
            "release_approver": """
                SELECT pg_has_role(
                           current_user, 'mooncen_crawler_release_approver', 'member'
                       )
                   AND has_function_privilege(
                       current_user,
                       'public.approve_crawler_release_action(uuid,text,text,text,integer)',
                       'EXECUTE'
                   )
                   AND has_function_privilege(
                       current_user,
                       'public.preview_crawler_release_action_for_approval(uuid,text)',
                       'EXECUTE'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_action_approvals',
                       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_approver_bindings',
                       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_action_consumers',
                       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'SELECT'
                   )
                   AND NOT pg_has_role(
                       current_user, 'mooncen_crawler_approver', 'member'
                   )
                   AND NOT pg_has_role(
                       current_user, 'mooncen_crawler_release_admin', 'member'
                   )
                   AND NOT pg_has_role(current_user, 'mooncen_crawler_api', 'member')
            """,
            "release_admin": """
                SELECT pg_has_role(current_user, 'mooncen_crawler_release_admin', 'member')
                   AND has_table_privilege(current_user, 'ops_crawler_release_artifacts', 'SELECT')
                   AND has_table_privilege(current_user, 'ops_crawler_release_artifacts', 'INSERT')
                   AND has_table_privilege(current_user, 'ops_crawler_release_rollouts', 'SELECT')
                   AND has_table_privilege(current_user, 'ops_crawler_release_rollouts', 'INSERT')
                   AND has_table_privilege(current_user, 'ops_crawler_release_rollouts', 'UPDATE')
                   AND has_table_privilege(current_user, 'ops_crawler_worker_desired_state', 'SELECT')
                   AND has_table_privilege(current_user, 'ops_crawler_worker_desired_state', 'INSERT')
                   AND has_table_privilege(current_user, 'ops_crawler_worker_desired_state', 'UPDATE')
                   AND has_table_privilege(
                       current_user, 'ops_crawler_rollout_worker_snapshots', 'SELECT'
                   )
                   AND has_table_privilege(
                       current_user, 'ops_crawler_rollout_worker_snapshots', 'INSERT'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_rollout_worker_snapshots', 'UPDATE'
                   )
                   AND has_table_privilege(current_user, 'ops_crawler_release_reports', 'SELECT')
                   AND has_table_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'SELECT'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'INSERT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'status', 'UPDATE'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'lease_token', 'UPDATE'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'result', 'UPDATE'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'request_payload', 'UPDATE'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_api_bindings', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_api_bindings', 'database_login', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_api_bindings', 'environment', 'SELECT'
                   )
                   AND has_column_privilege(current_user, 'ops_agents', 'id', 'SELECT')
                   AND has_column_privilege(current_user, 'ops_agents', 'hostname', 'SELECT')
                   AND has_column_privilege(current_user, 'ops_agents', 'last_seen_at', 'SELECT')
                   AND has_column_privilege(current_user, 'ops_agents', 'capabilities', 'SELECT')
                   AND NOT has_column_privilege(current_user, 'ops_agents', 'credential_hint', 'SELECT')
                   AND has_column_privilege(
                       current_user, 'ops_crawler_agent_bindings', 'agent_id', 'SELECT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_crawler_agent_bindings', 'database_login', 'SELECT'
                   )
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'crawl_batches', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'branches', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'courses', 'UPDATE')
                   AND has_table_privilege(
                       current_user, 'ops_crawler_release_action_approvals', 'SELECT'
                   )
                   AND has_function_privilege(
                       current_user,
                       'public.heartbeat_crawler_release_action_consumer(text,text)',
                       'EXECUTE'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_action_consumers',
                       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
                   )
            """,
            "crawler_api": """
                SELECT pg_has_role(current_user, 'mooncen_crawler_api', 'member')
                   AND NOT pg_has_role(current_user, 'mooncen_api', 'member')
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_artifacts', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_release_artifacts',
                       'artifact_digest', 'SELECT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_crawler_release_artifacts',
                       'artifact_path', 'SELECT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_crawler_release_artifacts',
                       'signature', 'SELECT'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_rollouts', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_release_rollouts', 'id', 'SELECT'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_worker_desired_state', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_worker_desired_state',
                       'worker_key', 'SELECT'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_rollout_worker_snapshots', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_rollout_worker_snapshots',
                       'worker_key', 'SELECT'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_reports', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_release_reports', 'id', 'SELECT'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'SELECT'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'INSERT'
                   )
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'UPDATE'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_release_action_requests', 'id', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_release_action_requests',
                       'request_payload', 'SELECT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_crawler_release_action_requests',
                       'lease_owner', 'SELECT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_crawler_release_action_requests',
                       'lease_token', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_release_action_requests',
                       'action', 'INSERT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_crawler_release_action_requests',
                       'status', 'INSERT'
                   )
                   AND NOT has_table_privilege(current_user, 'ops_audit_logs', 'INSERT')
                   AND has_column_privilege(
                       current_user, 'ops_audit_logs', 'action', 'INSERT'
                   )
                   AND NOT has_table_privilege(current_user, 'ops_audit_logs', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'SELECT')
                   AND has_column_privilege(current_user, 'ops_jobs', 'id', 'SELECT')
                   AND NOT has_column_privilege(
                       current_user, 'ops_jobs', 'lease_token', 'SELECT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_jobs', 'parameters', 'SELECT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_jobs', 'result', 'SELECT'
                   )
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'UPDATE')
                   AND NOT has_table_privilege(
                       current_user, 'ops_crawler_batches', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_batches', 'id', 'SELECT'
                   )
                   AND NOT has_table_privilege(current_user, 'ops_crawler_batches', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'ops_crawler_batches', 'UPDATE')
                   AND has_column_privilege(
                       current_user, 'ops_crawler_batch_tasks', 'job_id', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_task_attempts', 'attempt_no', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_task_attempts', 'rollout_id', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_task_attempts',
                       'release_generation', 'SELECT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_crawler_task_attempts', 'lease_token', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_task_observations',
                       'observation_kind', 'SELECT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_crawler_task_observations', 'payload', 'SELECT'
                   )
                   AND has_column_privilege(current_user, 'ops_agents', 'hostname', 'SELECT')
                   AND NOT has_column_privilege(
                       current_user, 'ops_agents', 'credential_hint', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_agent_bindings', 'agent_id', 'SELECT'
                   )
                   AND NOT has_column_privilege(
                       current_user, 'ops_crawler_agent_bindings',
                       'database_login', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_quality_issues', 'provider', 'SELECT'
                   )
                   AND CASE
                       WHEN to_regclass('public.course_quality_score') IS NULL THEN TRUE
                       ELSE (
                           has_column_privilege(
                               current_user, 'course_quality_score', 'provider', 'SELECT'
                           )
                           AND NOT has_table_privilege(
                               current_user, 'course_quality_score', 'SELECT'
                           )
                       )
                   END
                   AND NOT has_table_privilege(current_user, 'branches', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'branches', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'courses', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'courses', 'UPDATE')
            """,
            "worker": """
                SELECT pg_has_role(current_user, 'mooncen_crawler_worker', 'member')
                   AND NOT pg_has_role(current_user, 'mooncen_api', 'member')
                   AND NOT pg_has_role(current_user, 'mooncen_applier', 'member')
                   AND NOT pg_has_role(current_user, 'mooncen_crawler', 'member')
                   AND has_table_privilege(current_user, 'ops_jobs', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'INSERT')
                   AND has_table_privilege(current_user, 'ops_crawler_task_attempts', 'INSERT')
                   AND has_column_privilege(
                       current_user, 'ops_crawler_task_attempts', 'rollout_id', 'INSERT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_task_attempts',
                       'release_generation', 'INSERT'
                   )
                   AND has_table_privilege(current_user, 'ops_crawler_task_observations', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'ops_crawler_batches', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'ops_crawler_batches', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'crawl_batches', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'crawl_batches', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'crawl_batches', 'DELETE')
                   AND has_table_privilege(current_user, 'branches', 'INSERT')
                   AND has_table_privilege(current_user, 'courses', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'branches', 'DELETE')
                   AND NOT has_table_privilege(current_user, 'courses', 'DELETE')
            """,
            "reporter": """
                SELECT pg_has_role(current_user, 'mooncen_crawler_reporter', 'member')
                   AND NOT has_table_privilege(current_user, 'crawl_batches', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'crawl_batches', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'branches', 'UPDATE')
                   AND NOT has_table_privilege(current_user, 'courses', 'UPDATE')
                   AND has_table_privilege(current_user, 'ops_crawler_worker_desired_state', 'SELECT')
                   AND has_table_privilege(current_user, 'ops_crawler_release_reports', 'INSERT')
            """,
            "observer": """
                SELECT pg_has_role(current_user, 'mooncen_crawler_observer', 'member')
                   AND has_column_privilege(current_user, 'ops_jobs', 'status', 'SELECT')
                   AND has_column_privilege(current_user, 'ops_agents', 'last_seen_at', 'SELECT')
                   AND has_column_privilege(current_user, 'ops_crawler_batches', 'status', 'SELECT')
                   AND has_column_privilege(
                       current_user, 'ops_crawler_worker_desired_state', 'desired_status', 'SELECT'
                   )
                   AND has_column_privilege(
                       current_user, 'ops_crawler_release_reports', 'status', 'SELECT'
                   )
                   AND NOT has_column_privilege(current_user, 'ops_jobs', 'parameters', 'SELECT')
                   AND NOT has_column_privilege(current_user, 'ops_jobs', 'result', 'SELECT')
                   AND NOT has_column_privilege(current_user, 'ops_agents', 'credential_hint', 'SELECT')
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'ops_jobs', 'UPDATE')
            """,
        }[component]
        cursor.execute(privilege_sql)
        if cursor.fetchone()[0] is not True:
            raise PreflightError(f"{component} database least-privilege contract failed")
    connection.rollback()
    return {"database": expected_database, "role": identity[1]}


def run_preflight(
    component: str,
    environment_file: Path,
    *,
    installation_validation: bool = False,
) -> dict[str, str]:
    environment = _protected_environment(environment_file)
    _assert_component_environment_permissions(
        environment_file,
        component,
        allow_root_input=installation_validation,
    )
    if component == "worker" and not installation_validation:
        environment.update(_installed_worker_release_environment())
    _check_required_paths(
        component,
        environment,
        installation_validation=installation_validation,
    )
    config = _connection_config(component, environment)
    try:
        connection = psycopg2.connect(**config)
    except Exception as exc:
        raise PreflightError(f"{component} cannot connect to the shared staging database") from exc
    try:
        identity = _database_contract(
            component,
            connection,
            config["database"],
            environment,
            require_runtime_readiness=not installation_validation,
        )
    finally:
        connection.close()
    return {"status": "ok", "component": component, **identity}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a distributed crawler component before systemd start")
    parser.add_argument("--component", required=True, choices=sorted(COMPONENTS))
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument(
        "--installation-validation",
        action="store_true",
        help="Validate structure/identity before scheduler or agent rollout bootstrap.",
    )
    args = parser.parse_args(argv)
    if args.installation_validation and args.component not in {"scheduler", "worker", "reporter"}:
        parser.error("--installation-validation is valid only for scheduler/worker/reporter bootstrap")
    try:
        result = run_preflight(
            args.component,
            args.env_file,
            installation_validation=args.installation_validation,
        )
    except PreflightError as exc:
        parser.exit(78, f"crawler control preflight failed: {exc}\n")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
