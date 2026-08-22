from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.ops.service import mapped_one, mapped_rows, sanitize_for_audit
from tools.ops_redaction import redact_text


logger = logging.getLogger(__name__)

QUALITY_STALE_HOURS = 168
QUALITY_DATA_ENVIRONMENT = "staging"


def _quality_environment_reason(environment: str) -> dict[str, Any] | None:
    if environment == QUALITY_DATA_ENVIRONMENT:
        return None
    return {
        "code": "environment_dimension_unavailable",
        "message": (
            "Quality tables describe the shared staging data plane and have no "
            "per-row environment dimension"
        ),
        "requested_environment": environment,
        "supported_environment": QUALITY_DATA_ENVIRONMENT,
        "data_scope": "shared_staging",
    }

_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "ops_crawler_control_database_marker": frozenset({"singleton", "database_name"}),
    "ops_agents": frozenset(
        {
            "id",
            "name",
            "hostname",
            "environment",
            "status",
            "maintenance_mode",
            "last_seen_at",
        }
    ),
    "ops_crawler_agent_bindings": frozenset({"agent_id", "environment", "binding_type"}),
    "ops_crawler_batches": frozenset(
        {
            "id",
            "environment",
            "status",
            "scheduled_slot",
            "expected_task_count",
            "code_version",
            "artifact_digest",
            "config_revision",
            "started_at",
            "finished_at",
            "created_at",
        }
    ),
    "ops_crawler_batch_tasks": frozenset(
        {
            "batch_id",
            "job_id",
            "task_key",
            "provider",
            "allowed_output_providers",
            "required",
            "shard_index",
            "shard_count",
            "created_at",
        }
    ),
    "ops_crawler_rollout_worker_snapshots": frozenset(
        {
            "environment",
            "rollout_id",
            "generation",
            "worker_key",
            "agent_id",
            "desired_status",
            "cohort",
            "artifact_digest",
            "code_version",
            "config_revision",
            "created_at",
        }
    ),
    "ops_crawler_release_artifacts": frozenset(
        {"artifact_digest", "code_version", "config_revision", "size_bytes", "key_id", "created_at"}
    ),
    "ops_crawler_release_reports": frozenset(
        {
            "id",
            "rollout_id",
            "environment",
            "worker_key",
            "agent_id",
            "desired_generation",
            "status",
            "artifact_digest",
            "code_version",
            "config_revision",
            "health",
            "error_code",
            "error_message",
            "reported_at",
            "created_at",
        }
    ),
    "ops_crawler_release_rollouts": frozenset(
        {
            "id",
            "environment",
            "rollout_epoch",
            "artifact_digest",
            "previous_artifact_digest",
            "status",
            "requested_worker_count",
            "strategy",
            "worker_snapshot_required",
            "created_at",
            "started_at",
            "finished_at",
        }
    ),
    "ops_crawler_runs": frozenset(
        {
            "id",
            "job_id",
            "provider",
            "status",
            "total_count",
            "processed_count",
            "success_count",
            "failed_count",
            "new_count",
            "updated_count",
            "deleted_candidate_count",
            "started_at",
            "finished_at",
            "created_at",
        }
    ),
    "ops_crawler_task_attempts": frozenset(
        {
            "id",
            "job_id",
            "attempt_no",
            "lease_epoch",
            "agent_id",
            "status",
            "worker_code_version",
            "artifact_digest",
            "config_revision",
            "rollout_id",
            "release_generation",
            "started_at",
            "finished_at",
            "exit_code",
            "error_code",
            "created_at",
        }
    ),
    "ops_crawler_task_observations": frozenset(
        {
            "id",
            "attempt_id",
            "job_id",
            "attempt_no",
            "lease_epoch",
            "observation_kind",
            "observed_at",
            "created_at",
        }
    ),
    "ops_crawler_worker_desired_state": frozenset(
        {
            "environment",
            "worker_key",
            "agent_id",
            "rollout_id",
            "generation",
            "desired_status",
            "cohort",
            "artifact_digest",
            "code_version",
            "config_revision",
            "not_before",
            "updated_at",
        }
    ),
    "ops_jobs": frozenset(
        {
            "id",
            "job_type",
            "status",
            "environment",
            "available_at",
            "cancel_requested_at",
            "leased_until",
            "retry_count",
            "max_retries",
            "queued_at",
            "started_at",
            "finished_at",
        }
    ),
    "course_quality_score": frozenset(
        {"provider", "total_score", "grade", "missing_fields", "checked_at"}
    ),
    "ops_quality_issues": frozenset(
        {"provider", "status", "severity", "blocked_sync", "detected_at"}
    ),
    "crawl_batches": frozenset(
        {
            "crawl_batch_id",
            "total_courses",
            "valid_courses",
            "invalid_courses",
            "result",
        }
    ),
}


@dataclass(frozen=True)
class SchemaInventory:
    columns: dict[str, frozenset[str]]

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]]) -> SchemaInventory:
        collected: dict[str, set[str]] = {}
        for row in rows:
            table_name = str(row.get("table_name") or "")
            column_name = str(row.get("column_name") or "")
            if table_name in _REQUIRED_COLUMNS and column_name:
                collected.setdefault(table_name, set()).add(column_name)
        return cls({name: frozenset(columns) for name, columns in collected.items()})

    def reasons_for(self, *table_names: str) -> list[dict[str, Any]]:
        reasons: list[dict[str, Any]] = []
        for table_name in table_names:
            actual = self.columns.get(table_name)
            if actual is None:
                reasons.append(
                    {
                        "code": "missing_table",
                        "relation": f"public.{table_name}",
                    }
                )
                continue
            missing = sorted(_REQUIRED_COLUMNS[table_name] - actual)
            if missing:
                reasons.append(
                    {
                        "code": "missing_columns",
                        "relation": f"public.{table_name}",
                        "columns": missing,
                    }
                )
        return reasons


def _schema_inventory(db: Session) -> SchemaInventory:
    rows = mapped_rows(
        db.execute(
            text(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ANY(CAST(:table_names AS text[]))
                ORDER BY table_name, ordinal_position
                """
            ),
            {"table_names": sorted(_REQUIRED_COLUMNS)},
        )
    )
    return SchemaInventory.from_rows(rows)


def _control_database_authority_reason(
    db: Session,
    inventory: SchemaInventory,
    *,
    environment: str,
) -> dict[str, Any] | None:
    marker_reasons = inventory.reasons_for("ops_crawler_control_database_marker")
    if marker_reasons:
        return {
            "code": "crawler_control_database_unavailable",
            "message": (
                "The API database pool is not connected to the marked shared crawler "
                "staging/control database"
            ),
            "required_connection": "dedicated_crawler_analytics_readonly_pool",
            "details": marker_reasons,
        }
    marker = mapped_one(
        db.execute(
            text(
                """
                SELECT database_name::text
                FROM ops_crawler_control_database_marker
                WHERE singleton IS TRUE
                  AND database_name = current_database()
                  AND (SELECT COUNT(*) FROM ops_crawler_control_database_marker) = 1
                LIMIT 1
                """
            )
        )
    )
    if marker is None:
        return {
            "code": "crawler_control_database_marker_mismatch",
            "message": "The crawler control database marker does not identify the connected database",
            "required_connection": "dedicated_crawler_analytics_readonly_pool",
        }
    binding = mapped_one(
        db.execute(
            text(
                """
                SELECT current_crawler_api_environment() AS environment
                """
            )
        )
    )
    if binding is None or binding.get("environment") != environment:
        return {
            "code": "crawler_api_environment_binding_mismatch",
            "message": "The crawler-control API login is not bound to this Ops environment",
            "required_connection": "dedicated_crawler_analytics_readonly_pool",
        }
    return None


def _component(reasons: list[dict[str, Any]], **payload: Any) -> dict[str, Any]:
    if reasons:
        return {"available": False, "has_data": None, "reasons": reasons, **payload}
    return {"available": True, "has_data": False, "reasons": [], **payload}


def _section(components: dict[str, dict[str, Any]], **payload: Any) -> dict[str, Any]:
    unavailable = [name for name, component in components.items() if not component["available"]]
    available = [name for name, component in components.items() if component["available"]]
    reasons = [
        {"component": name, **reason}
        for name, component in components.items()
        for reason in component.get("reasons", [])
    ]
    return {
        "available": bool(available),
        "complete": not unavailable,
        "has_data": any(component.get("has_data") is True for component in components.values()),
        "reasons": reasons,
        "components": components,
        **payload,
    }


def _int_metrics(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    normalized = dict(row)
    for key in keys:
        if key in normalized and normalized[key] is not None:
            normalized[key] = int(normalized[key])
    return normalized


def _deployment(
    db: Session,
    inventory: SchemaInventory,
    *,
    environment: str,
    worker_limit: int,
    fresh_seconds: int,
) -> dict[str, Any]:
    rollout_reasons = inventory.reasons_for("ops_crawler_release_rollouts", "ops_crawler_release_artifacts")
    rollout = _component(rollout_reasons, latest=None)
    if not rollout_reasons:
        latest = mapped_one(
            db.execute(
                text(
                    """
                    SELECT r.id::text, r.rollout_epoch, r.status,
                           r.requested_worker_count, r.strategy,
                           r.artifact_digest, artifact.code_version,
                           artifact.config_revision, artifact.size_bytes,
                           artifact.key_id, artifact.created_at AS artifact_created_at,
                           r.previous_artifact_digest, r.created_at,
                           r.started_at, r.finished_at
                    FROM ops_crawler_release_rollouts r
                    JOIN ops_crawler_release_artifacts artifact
                      ON artifact.artifact_digest = r.artifact_digest
                    WHERE r.environment = :environment
                    ORDER BY r.rollout_epoch DESC, r.created_at DESC
                    LIMIT 1
                    """
                ),
                {"environment": environment},
            )
        )
        if latest is not None:
            latest["requested_worker_count"] = int(latest["requested_worker_count"])
            latest["rollout_epoch"] = int(latest["rollout_epoch"])
            latest["size_bytes"] = int(latest["size_bytes"])
            latest["strategy"] = sanitize_for_audit(latest.get("strategy") or {})
        rollout.update({"has_data": latest is not None, "latest": latest})

    version_reasons = inventory.reasons_for(
        "ops_crawler_worker_desired_state",
        "ops_crawler_release_reports",
    )
    versions = _component(version_reasons, summary=None, items=None, limit=worker_limit, truncated=None)
    if not version_reasons:
        summary = mapped_one(
            db.execute(
                text(
                    """
                    WITH worker_state AS (
                        SELECT desired.environment, desired.worker_key, desired.agent_id,
                               desired.rollout_id, desired.generation, desired.desired_status,
                               desired.artifact_digest, desired.code_version,
                               desired.config_revision,
                               report.id AS report_id,
                               report.rollout_id AS reported_rollout_id,
                               report.desired_generation AS reported_generation,
                               report.status AS reported_status,
                               report.artifact_digest AS reported_artifact_digest,
                               report.code_version AS reported_code_version,
                               report.config_revision AS reported_config_revision,
                               report.health AS reported_health,
                               report.reported_at AS reported_at,
                               agent.status AS agent_status,
                               agent.maintenance_mode AS agent_maintenance_mode,
                               agent.last_seen_at AS agent_last_seen_at
                        FROM ops_crawler_worker_desired_state desired
                        LEFT JOIN LATERAL (
                            SELECT report.id, report.rollout_id,
                                   report.desired_generation, report.status,
                                   report.artifact_digest, report.code_version,
                                   report.config_revision, report.health,
                                   report.error_code, report.error_message,
                                   report.reported_at, report.created_at
                            FROM ops_crawler_release_reports report
                            WHERE report.environment = desired.environment
                              AND report.worker_key = desired.worker_key
                            ORDER BY report.reported_at DESC, report.created_at DESC
                            LIMIT 1
                        ) report ON true
                        LEFT JOIN ops_agents agent ON agent.id = desired.agent_id
                        WHERE desired.environment = :environment
                    )
                    SELECT COUNT(*) AS desired_workers,
                           COUNT(*) FILTER (WHERE desired_status = 'active') AS active_workers,
                           COUNT(*) FILTER (WHERE desired_status = 'draining') AS draining_workers,
                           COUNT(*) FILTER (WHERE desired_status = 'disabled') AS disabled_workers,
                           COUNT(*) FILTER (WHERE report_id IS NULL) AS unreported_workers,
                           COUNT(*) FILTER (
                               WHERE reported_rollout_id = rollout_id
                                 AND reported_generation = generation
                                 AND reported_artifact_digest = artifact_digest
                                 AND reported_code_version = code_version
                                 AND reported_config_revision = config_revision
                                 AND reported_status IN ('ready', 'rolled_back')
                                 AND reported_health->'healthy' = 'true'::jsonb
                                 AND reported_at >= CURRENT_TIMESTAMP
                                     - (:fresh_seconds * INTERVAL '1 second')
                                 AND agent_status = 'healthy'
                                 AND agent_maintenance_mode IS FALSE
                                 AND agent_last_seen_at >= CURRENT_TIMESTAMP
                                     - (:fresh_seconds * INTERVAL '1 second')
                           ) AS ready_current_workers,
                           COUNT(*) FILTER (
                               WHERE report_id IS NOT NULL
                                 AND NOT (
                                     reported_rollout_id = rollout_id
                                     AND reported_generation = generation
                                     AND reported_artifact_digest = artifact_digest
                                     AND reported_code_version = code_version
                                     AND reported_config_revision = config_revision
                                 )
                           ) AS outdated_workers,
                           COUNT(*) FILTER (WHERE reported_status = 'failed') AS failed_workers,
                           COUNT(*) FILTER (WHERE reported_status = 'drifted') AS drifted_workers
                    FROM worker_state
                    """
                ),
                {"environment": environment, "fresh_seconds": fresh_seconds},
            )
        ) or {}
        summary = _int_metrics(
            summary,
            (
                "desired_workers",
                "active_workers",
                "draining_workers",
                "disabled_workers",
                "unreported_workers",
                "ready_current_workers",
                "outdated_workers",
                "failed_workers",
                "drifted_workers",
            ),
        )
        items = mapped_rows(
            db.execute(
                text(
                    """
                    SELECT desired.worker_key, desired.agent_id::text,
                           desired.rollout_id::text, desired.generation,
                           desired.desired_status, desired.cohort,
                           desired.artifact_digest AS desired_artifact_digest,
                           desired.code_version AS desired_code_version,
                           desired.config_revision AS desired_config_revision,
                           desired.not_before, desired.updated_at,
                           report.rollout_id::text AS reported_rollout_id,
                           report.desired_generation AS reported_generation,
                           report.status AS reported_status,
                           report.artifact_digest AS reported_artifact_digest,
                           report.code_version AS reported_code_version,
                           report.config_revision AS reported_config_revision,
                           report.health, report.error_code, report.error_message,
                           report.reported_at,
                           report.reported_at >= CURRENT_TIMESTAMP
                               - (:fresh_seconds * INTERVAL '1 second') AS report_fresh,
                           agent.status = 'healthy'
                               AND agent.maintenance_mode IS FALSE
                               AND agent.last_seen_at >= CURRENT_TIMESTAMP
                                   - (:fresh_seconds * INTERVAL '1 second') AS agent_fresh
                    FROM ops_crawler_worker_desired_state desired
                    LEFT JOIN ops_agents agent ON agent.id = desired.agent_id
                    LEFT JOIN LATERAL (
                        SELECT report.id, report.rollout_id,
                               report.desired_generation, report.status,
                               report.artifact_digest, report.code_version,
                               report.config_revision, report.health,
                               report.error_code, report.error_message,
                               report.reported_at, report.created_at
                        FROM ops_crawler_release_reports report
                        WHERE report.environment = desired.environment
                          AND report.worker_key = desired.worker_key
                        ORDER BY report.reported_at DESC, report.created_at DESC
                        LIMIT 1
                    ) report ON true
                    WHERE desired.environment = :environment
                    ORDER BY CASE desired.cohort WHEN 'canary' THEN 0 ELSE 1 END,
                             desired.worker_key
                    LIMIT :worker_limit
                    """
                ),
                {
                    "environment": environment,
                    "worker_limit": worker_limit,
                    "fresh_seconds": fresh_seconds,
                },
            )
        )
        for item in items:
            item["generation"] = int(item["generation"])
            if item.get("reported_generation") is not None:
                item["reported_generation"] = int(item["reported_generation"])
            reported_health = item.get("health") or {}
            healthy = isinstance(reported_health, dict) and reported_health.get("healthy") is True
            fresh = item.get("report_fresh") is True and item.get("agent_fresh") is True
            item["health"] = sanitize_for_audit(reported_health)
            if item.get("error_message"):
                item["error_message"] = redact_text(item["error_message"], maximum=500)
            exact_identity = bool(
                item.get("reported_rollout_id") == item.get("rollout_id")
                and item.get("reported_generation") == item.get("generation")
                and item.get("reported_artifact_digest") == item.get("desired_artifact_digest")
                and item.get("reported_code_version") == item.get("desired_code_version")
                and item.get("reported_config_revision") == item.get("desired_config_revision")
            )
            if item.get("reported_status") is None:
                state = "unreported"
            elif not exact_identity:
                state = "outdated"
            elif item.get("reported_status") in {"ready", "rolled_back"}:
                state = "ready" if healthy and fresh else "unhealthy"
            else:
                state = str(item["reported_status"])
            item["matches_desired_release"] = exact_identity
            item["healthy_current_release"] = bool(
                exact_identity
                and healthy
                and fresh
                and item.get("reported_status") in {"ready", "rolled_back"}
            )
            item["version_state"] = state
        desired_workers = int(summary.get("desired_workers") or 0)
        versions.update(
            {
                "has_data": desired_workers > 0,
                "summary": summary,
                "items": items,
                "truncated": desired_workers > len(items),
            }
        )

    return _section({"rollout": rollout, "versions": versions})


def _collection(
    db: Session,
    inventory: SchemaInventory,
    *,
    environment: str,
    window_hours: int,
) -> dict[str, Any]:
    params = {"environment": environment, "window_hours": window_hours}
    run_reasons = inventory.reasons_for("ops_crawler_runs", "ops_jobs")
    runs = _component(run_reasons, totals=None)
    if not run_reasons:
        totals = mapped_one(
            db.execute(
                text(
                    """
                    SELECT COUNT(*) AS run_count,
                           COUNT(*) FILTER (WHERE run.status = 'success') AS successful_runs,
                           COUNT(*) FILTER (WHERE run.status = 'partial_success') AS partial_runs,
                           COUNT(*) FILTER (
                               WHERE run.status IN ('failed', 'blocked', 'cancelled')
                           ) AS failed_runs,
                           COUNT(*) FILTER (
                               WHERE run.status IN ('queued', 'running', 'stopping')
                           ) AS in_progress_runs,
                           COALESCE(SUM(run.total_count), 0) AS collected_count,
                           COALESCE(SUM(run.processed_count), 0) AS processed_count,
                           COALESCE(SUM(run.success_count), 0) AS successful_item_count,
                           COALESCE(SUM(run.failed_count), 0) AS failed_item_count,
                           COALESCE(SUM(run.new_count), 0) AS new_count,
                           COALESCE(SUM(run.updated_count), 0) AS updated_count,
                           COALESCE(SUM(run.deleted_candidate_count), 0) AS deleted_candidate_count,
                           MAX(COALESCE(run.finished_at, run.started_at, run.created_at)) AS last_run_at
                    FROM ops_crawler_runs run
                    JOIN ops_jobs job ON job.id = run.job_id
                    WHERE job.environment = :environment
                      AND COALESCE(run.finished_at, run.started_at, run.created_at)
                          >= CURRENT_TIMESTAMP - (:window_hours * INTERVAL '1 hour')
                    """
                ),
                params,
            )
        ) or {}
        totals = _int_metrics(
            totals,
            (
                "run_count",
                "successful_runs",
                "partial_runs",
                "failed_runs",
                "in_progress_runs",
                "collected_count",
                "processed_count",
                "successful_item_count",
                "failed_item_count",
                "new_count",
                "updated_count",
                "deleted_candidate_count",
            ),
        )
        runs.update({"has_data": bool(totals.get("run_count")), "totals": totals})

    batch_reasons = inventory.reasons_for("ops_crawler_batches")
    batches = _component(batch_reasons, outcomes=None)
    if not batch_reasons:
        outcomes = mapped_one(
            db.execute(
                text(
                    """
                    SELECT COUNT(*) AS batch_count,
                           COUNT(*) FILTER (WHERE status = 'success') AS successful_batches,
                           COUNT(*) FILTER (WHERE status = 'partial_success') AS partial_batches,
                           COUNT(*) FILTER (
                               WHERE status IN ('failed', 'cancelled', 'dead_lettered')
                           ) AS failed_batches,
                           COUNT(*) FILTER (
                               WHERE status IN ('planning', 'queued', 'running', 'finalizing')
                           ) AS active_batches,
                           COALESCE(SUM(expected_task_count), 0) AS expected_tasks,
                           MAX(finished_at) AS last_finished_at,
                           MAX(scheduled_slot) AS last_scheduled_at
                    FROM ops_crawler_batches
                    WHERE environment = :environment
                      AND scheduled_slot >= CURRENT_TIMESTAMP - (:window_hours * INTERVAL '1 hour')
                    """
                ),
                params,
            )
        ) or {}
        outcomes = _int_metrics(
            outcomes,
            (
                "batch_count",
                "successful_batches",
                "partial_batches",
                "failed_batches",
                "active_batches",
                "expected_tasks",
            ),
        )
        batches.update({"has_data": bool(outcomes.get("batch_count")), "outcomes": outcomes})

    validation_reasons = inventory.reasons_for("ops_crawler_batches", "crawl_batches")
    validation = _component(validation_reasons, totals=None)
    if not validation_reasons:
        validation_totals = mapped_one(
            db.execute(
                text(
                    """
                    SELECT COUNT(*) AS sealed_batch_count,
                           COALESCE(SUM(staging.total_courses), 0) AS total_courses,
                           COALESCE(SUM(staging.valid_courses), 0) AS valid_courses,
                           COALESCE(SUM(staging.invalid_courses), 0) AS invalid_courses,
                           COUNT(*) FILTER (
                               WHERE staging.result ->> 'promotion_policy' = 'held'
                           ) AS held_for_approval_batches,
                           COUNT(*) FILTER (
                               WHERE lower(COALESCE(staging.result ->> 'promotion_eligible', 'false')) = 'true'
                           ) AS promotion_eligible_batches
                    FROM crawl_batches staging
                    JOIN ops_crawler_batches control
                      ON control.id::text = staging.crawl_batch_id
                    WHERE control.environment = :environment
                      AND control.scheduled_slot
                          >= CURRENT_TIMESTAMP - (:window_hours * INTERVAL '1 hour')
                    """
                ),
                params,
            )
        ) or {}
        validation_totals = _int_metrics(
            validation_totals,
            (
                "sealed_batch_count",
                "total_courses",
                "valid_courses",
                "invalid_courses",
                "held_for_approval_batches",
                "promotion_eligible_batches",
            ),
        )
        validation.update(
            {"has_data": bool(validation_totals.get("sealed_batch_count")), "totals": validation_totals}
        )

    return _section(
        {"runs": runs, "batches": batches, "validation": validation},
        window_hours=window_hours,
    )


def _providers(
    db: Session,
    inventory: SchemaInventory,
    *,
    environment: str,
    window_hours: int,
    provider_limit: int,
) -> dict[str, Any]:
    reasons = inventory.reasons_for("ops_crawler_runs", "ops_jobs")
    component = _component(reasons, items=None, total=None, limit=provider_limit, truncated=None)
    if reasons:
        return _section({"collection": component}, window_hours=window_hours)
    items = mapped_rows(
        db.execute(
            text(
                """
                SELECT run.provider,
                       COUNT(*) AS run_count,
                       COUNT(*) FILTER (WHERE run.status = 'success') AS successful_runs,
                       COUNT(*) FILTER (WHERE run.status = 'partial_success') AS partial_runs,
                       COUNT(*) FILTER (
                           WHERE run.status IN ('failed', 'blocked', 'cancelled')
                       ) AS failed_runs,
                       COALESCE(SUM(run.total_count), 0) AS collected_count,
                       COALESCE(SUM(run.new_count), 0) AS new_count,
                       COALESCE(SUM(run.updated_count), 0) AS updated_count,
                       COALESCE(SUM(run.failed_count), 0) AS failed_item_count,
                       ROUND(
                           100.0 * COUNT(*) FILTER (WHERE run.status = 'success')
                           / NULLIF(COUNT(*), 0),
                           1
                       ) AS success_rate,
                       MAX(COALESCE(run.finished_at, run.started_at, run.created_at)) AS last_run_at,
                       COUNT(*) OVER () AS total_providers
                FROM ops_crawler_runs run
                JOIN ops_jobs job ON job.id = run.job_id
                WHERE job.environment = :environment
                  AND btrim(COALESCE(run.provider, '')) <> ''
                  AND COALESCE(run.finished_at, run.started_at, run.created_at)
                      >= CURRENT_TIMESTAMP - (:window_hours * INTERVAL '1 hour')
                GROUP BY run.provider
                ORDER BY failed_runs DESC, success_rate ASC NULLS FIRST, run.provider
                LIMIT :provider_limit
                """
            ),
            {
                "environment": environment,
                "window_hours": window_hours,
                "provider_limit": provider_limit,
            },
        )
    )
    total = int(items[0].pop("total_providers")) if items else 0
    for item in items:
        item.pop("total_providers", None)
        normalized = _int_metrics(
            item,
            (
                "run_count",
                "successful_runs",
                "partial_runs",
                "failed_runs",
                "collected_count",
                "new_count",
                "updated_count",
                "failed_item_count",
            ),
        )
        item.clear()
        item.update(normalized)
        if item.get("success_rate") is not None:
            item["success_rate"] = float(item["success_rate"])
    component.update(
        {
            "has_data": total > 0,
            "items": items,
            "total": total,
            "truncated": total > len(items),
        }
    )
    return _section({"collection": component}, window_hours=window_hours)


def _queue(
    db: Session,
    inventory: SchemaInventory,
    *,
    environment: str,
    window_hours: int,
) -> dict[str, Any]:
    reasons = inventory.reasons_for("ops_jobs")
    health = _component(reasons, metrics=None)
    if not reasons:
        metrics = mapped_one(
            db.execute(
                text(
                    """
                    SELECT COUNT(*) AS tracked_jobs,
                           COUNT(*) FILTER (
                               WHERE status = 'queued'
                                 AND cancel_requested_at IS NULL
                                 AND available_at <= CURRENT_TIMESTAMP
                           ) AS ready_jobs,
                           COUNT(*) FILTER (
                               WHERE status = 'queued'
                                 AND cancel_requested_at IS NULL
                                 AND available_at > CURRENT_TIMESTAMP
                           ) AS delayed_jobs,
                           COUNT(*) FILTER (WHERE status = 'assigned') AS assigned_jobs,
                           COUNT(*) FILTER (WHERE status = 'running') AS running_jobs,
                           COUNT(*) FILTER (
                               WHERE status IN ('queued', 'assigned', 'running')
                                 AND cancel_requested_at IS NOT NULL
                           ) AS cancellation_requested_jobs,
                           COUNT(*) FILTER (
                               WHERE status IN ('assigned', 'running')
                                 AND leased_until <= CURRENT_TIMESTAMP
                           ) AS expired_leases,
                           COUNT(*) FILTER (WHERE status = 'dead_lettered') AS dead_lettered_jobs,
                           COUNT(*) FILTER (
                               WHERE status = 'failed' AND retry_count >= max_retries
                           ) AS exhausted_failed_jobs,
                           EXTRACT(EPOCH FROM (
                               CURRENT_TIMESTAMP - MIN(queued_at) FILTER (
                                   WHERE status = 'queued'
                                     AND cancel_requested_at IS NULL
                                     AND available_at <= CURRENT_TIMESTAMP
                               )
                           ))::bigint AS oldest_ready_age_seconds
                    FROM ops_jobs
                    WHERE environment = :environment
                      AND job_type IN ('crawler_run', 'crawler_retry')
                      AND (
                          status IN ('queued', 'assigned', 'running', 'dead_lettered')
                          OR (
                              status = 'failed'
                              AND finished_at >= CURRENT_TIMESTAMP
                                  - (:window_hours * INTERVAL '1 hour')
                          )
                      )
                    """
                ),
                {"environment": environment, "window_hours": window_hours},
            )
        ) or {}
        metrics = _int_metrics(
            metrics,
            (
                "tracked_jobs",
                "ready_jobs",
                "delayed_jobs",
                "assigned_jobs",
                "running_jobs",
                "cancellation_requested_jobs",
                "expired_leases",
                "dead_lettered_jobs",
                "exhausted_failed_jobs",
                "oldest_ready_age_seconds",
            ),
        )
        health.update({"has_data": bool(metrics.get("tracked_jobs")), "metrics": metrics})
    return _section({"health": health}, window_hours=window_hours)


def _workers(
    db: Session,
    inventory: SchemaInventory,
    *,
    environment: str,
    heartbeat_timeout_seconds: int,
    worker_limit: int,
) -> dict[str, Any]:
    reasons = inventory.reasons_for("ops_agents", "ops_crawler_agent_bindings")
    health = _component(reasons, summary=None, items=None, limit=worker_limit, truncated=None)
    if reasons:
        return _section({"health": health}, heartbeat_timeout_seconds=heartbeat_timeout_seconds)
    params = {
        "environment": environment,
        "heartbeat_timeout_seconds": heartbeat_timeout_seconds,
        "worker_limit": worker_limit,
    }
    summary = mapped_one(
        db.execute(
            text(
                """
                SELECT COUNT(*) AS worker_count,
                       COUNT(*) FILTER (WHERE agent.maintenance_mode) AS maintenance_workers,
                       COUNT(*) FILTER (WHERE agent.status = 'disabled') AS disabled_workers,
                       COUNT(*) FILTER (
                           WHERE agent.last_seen_at IS NULL
                              OR agent.last_seen_at < CURRENT_TIMESTAMP
                                  - (:heartbeat_timeout_seconds * INTERVAL '1 second')
                       ) AS stale_workers,
                       COUNT(*) FILTER (WHERE agent.status = 'critical') AS critical_workers,
                       COUNT(*) FILTER (WHERE agent.status IN ('warning', 'unknown')) AS warning_workers,
                       COUNT(*) FILTER (
                           WHERE agent.status = 'healthy'
                             AND NOT agent.maintenance_mode
                             AND agent.last_seen_at >= CURRENT_TIMESTAMP
                                 - (:heartbeat_timeout_seconds * INTERVAL '1 second')
                       ) AS healthy_workers,
                       MAX(agent.last_seen_at) AS latest_heartbeat_at
                FROM ops_agents agent
                JOIN ops_crawler_agent_bindings binding
                  ON binding.agent_id = agent.id
                 AND binding.environment = agent.environment
                 AND binding.binding_type = 'worker'
                WHERE agent.environment = :environment
                """
            ),
            params,
        )
    ) or {}
    summary = _int_metrics(
        summary,
        (
            "worker_count",
            "maintenance_workers",
            "disabled_workers",
            "stale_workers",
            "critical_workers",
            "warning_workers",
            "healthy_workers",
        ),
    )
    items = mapped_rows(
        db.execute(
            text(
                """
                SELECT agent.id::text, agent.name, agent.hostname, agent.status,
                       agent.maintenance_mode, agent.last_seen_at,
                       (agent.last_seen_at IS NULL
                        OR agent.last_seen_at < CURRENT_TIMESTAMP
                           - (:heartbeat_timeout_seconds * INTERVAL '1 second')) AS heartbeat_stale,
                       COUNT(*) OVER () AS total_workers
                FROM ops_agents agent
                JOIN ops_crawler_agent_bindings binding
                  ON binding.agent_id = agent.id
                 AND binding.environment = agent.environment
                 AND binding.binding_type = 'worker'
                WHERE agent.environment = :environment
                ORDER BY agent.maintenance_mode DESC,
                         CASE agent.status
                             WHEN 'critical' THEN 0
                             WHEN 'warning' THEN 1
                             WHEN 'unknown' THEN 2
                             WHEN 'healthy' THEN 3
                             ELSE 4
                         END,
                         agent.last_seen_at ASC NULLS FIRST,
                         agent.hostname
                LIMIT :worker_limit
                """
            ),
            params,
        )
    )
    total = int(items[0].pop("total_workers")) if items else 0
    for item in items:
        item.pop("total_workers", None)
        if item["maintenance_mode"] or item["status"] == "disabled":
            item["health_state"] = "maintenance"
        elif item["heartbeat_stale"]:
            item["health_state"] = "stale"
        elif item["status"] == "healthy":
            item["health_state"] = "healthy"
        else:
            item["health_state"] = str(item["status"])
    health.update(
        {
            "has_data": total > 0,
            "summary": summary,
            "items": items,
            "truncated": total > len(items),
        }
    )
    return _section({"health": health}, heartbeat_timeout_seconds=heartbeat_timeout_seconds)


def _quality(
    db: Session,
    inventory: SchemaInventory,
    *,
    environment: str,
    provider_limit: int,
) -> dict[str, Any]:
    environment_reason = _quality_environment_reason(environment)
    environment_reasons = [environment_reason] if environment_reason else []

    score_reasons = [*environment_reasons, *inventory.reasons_for("course_quality_score")]
    scores = _component(score_reasons, summary=None, providers=None, provider_limit=provider_limit, truncated=None)
    if not score_reasons:
        summary = mapped_one(
            db.execute(
                text(
                    """
                    SELECT COUNT(*) AS scored_courses,
                           ROUND(AVG(total_score)::numeric, 2) AS average_score,
                           COUNT(*) FILTER (WHERE grade = 'good') AS good_courses,
                           COUNT(*) FILTER (WHERE grade = 'warning') AS warning_courses,
                           COUNT(*) FILTER (WHERE grade = 'bad') AS bad_courses,
                           COUNT(*) FILTER (WHERE cardinality(missing_fields) > 0) AS incomplete_courses,
                           COUNT(*) FILTER (
                               WHERE checked_at < CURRENT_TIMESTAMP
                                   - (:quality_stale_hours * INTERVAL '1 hour')
                           ) AS stale_scores,
                           MAX(checked_at) AS latest_checked_at
                    FROM course_quality_score
                    """
                ),
                {"quality_stale_hours": QUALITY_STALE_HOURS},
            )
        ) or {}
        summary = _int_metrics(
            summary,
            (
                "scored_courses",
                "good_courses",
                "warning_courses",
                "bad_courses",
                "incomplete_courses",
                "stale_scores",
            ),
        )
        if summary.get("average_score") is not None:
            summary["average_score"] = float(summary["average_score"])
        providers = mapped_rows(
            db.execute(
                text(
                    """
                    SELECT provider, COUNT(*) AS scored_courses,
                           ROUND(AVG(total_score)::numeric, 2) AS average_score,
                           COUNT(*) FILTER (WHERE grade = 'good') AS good_courses,
                           COUNT(*) FILTER (WHERE grade = 'warning') AS warning_courses,
                           COUNT(*) FILTER (WHERE grade = 'bad') AS bad_courses,
                           MAX(checked_at) AS latest_checked_at,
                           COUNT(*) OVER () AS total_providers
                    FROM course_quality_score
                    WHERE btrim(COALESCE(provider, '')) <> ''
                    GROUP BY provider
                    ORDER BY bad_courses DESC, average_score ASC NULLS FIRST, provider
                    LIMIT :provider_limit
                    """
                ),
                {"provider_limit": provider_limit},
            )
        )
        total_providers = int(providers[0].pop("total_providers")) if providers else 0
        for provider in providers:
            provider.pop("total_providers", None)
            normalized = _int_metrics(
                provider,
                ("scored_courses", "good_courses", "warning_courses", "bad_courses"),
            )
            provider.clear()
            provider.update(normalized)
            if provider.get("average_score") is not None:
                provider["average_score"] = float(provider["average_score"])
        scores.update(
            {
                "has_data": bool(summary.get("scored_courses")),
                "summary": summary,
                "providers": providers,
                "truncated": total_providers > len(providers),
            }
        )

    issue_reasons = [*environment_reasons, *inventory.reasons_for("ops_quality_issues")]
    issues = _component(issue_reasons, summary=None)
    if not issue_reasons:
        issue_summary = mapped_one(
            db.execute(
                text(
                    """
                    SELECT COUNT(*) AS issue_count,
                           COUNT(*) FILTER (WHERE status IN ('open', 'reviewing')) AS active_issues,
                           COUNT(*) FILTER (
                               WHERE status IN ('open', 'reviewing') AND severity = 'critical'
                           ) AS active_critical_issues,
                           COUNT(*) FILTER (
                               WHERE status IN ('open', 'reviewing') AND severity = 'warning'
                           ) AS active_warning_issues,
                           COUNT(*) FILTER (
                               WHERE status IN ('open', 'reviewing') AND blocked_sync
                           ) AS blocked_sync_issues,
                           MAX(detected_at) AS latest_detected_at
                    FROM ops_quality_issues
                    """
                )
            )
        ) or {}
        issue_summary = _int_metrics(
            issue_summary,
            (
                "issue_count",
                "active_issues",
                "active_critical_issues",
                "active_warning_issues",
                "blocked_sync_issues",
            ),
        )
        issues.update({"has_data": bool(issue_summary.get("issue_count")), "summary": issue_summary})

    return _section(
        {"scores": scores, "issues": issues},
        data_scope="current_database",
        quality_stale_hours=QUALITY_STALE_HOURS,
    )


_CORRELATION_CORE_TABLES = (
    "ops_crawler_rollout_worker_snapshots",
    "ops_crawler_batches",
    "ops_crawler_batch_tasks",
    "ops_jobs",
    "ops_crawler_task_attempts",
    "ops_crawler_task_observations",
    "ops_crawler_runs",
    "crawl_batches",
)


def _comparison_rows(
    rows: list[dict[str, Any]],
    *,
    metric_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Attach a bounded prior-generation comparison without inventing a baseline."""

    for index, row in enumerate(rows):
        previous = rows[index + 1] if index + 1 < len(rows) else None
        row["previous_generation"] = (
            {
                "rollout_id": previous.get("rollout_id"),
                "generation": previous.get("generation"),
            }
            if previous is not None
            else None
        )
        row["deltas"] = {
            key: (
                row[key] - previous[key]
                if previous is not None
                and isinstance(row.get(key), (int, float))
                and not isinstance(row.get(key), bool)
                and isinstance(previous.get(key), (int, float))
                and not isinstance(previous.get(key), bool)
                else None
            )
            for key in metric_keys
        }
    return rows


_GENERATION_FENCE_REASON = {
    "code": "generation_attribution_evidence_unavailable",
    "message": (
        "Legacy task-attempt evidence has no exact rollout_id and release_generation"
    ),
    "required_evidence": "immutable_attempt_rollout_generation_fence",
}

_GENERATION_QUALITY_FENCE_REASON = {
    "code": "generation_quality_attribution_unavailable",
    "message": (
        "Shared quality rows have no immutable attempt or batch release-generation "
        "identity; timestamp windows can overlap and are not used as attribution"
    ),
    "required_evidence": "immutable_quality_attempt_or_batch_generation_fence",
}


def _identity_only_correlations(
    db: Session,
    inventory: SchemaInventory,
    *,
    environment: str,
    window_hours: int,
    correlation_limit: int,
) -> dict[str, Any]:
    """Return bounded evidence without inferring a release generation from timestamps."""

    core_reasons = inventory.reasons_for(*_CORRELATION_CORE_TABLES)
    generations = _component(
        [*core_reasons, _GENERATION_FENCE_REASON],
        items=None,
        total=None,
        limit=correlation_limit,
        truncated=None,
    )
    quality = _component(
        [
            *core_reasons,
            *inventory.reasons_for("course_quality_score", "ops_quality_issues"),
            *(
                [quality_environment_reason]
                if (quality_environment_reason := _quality_environment_reason(environment))
                else []
            ),
            _GENERATION_FENCE_REASON,
        ],
        items=None,
        total=None,
        limit=correlation_limit,
        truncated=None,
        score_semantics=None,
        issue_semantics=None,
    )
    batches = _component(
        core_reasons,
        items=None,
        total=None,
        limit=correlation_limit,
        truncated=None,
    )
    attribution = _component(
        [*core_reasons, _GENERATION_FENCE_REASON],
        summary=None,
        semantics="artifact_code_config_agent_identity_match_only",
    )
    if core_reasons:
        return _section(
            {
                "generations": generations,
                "batches": batches,
                "attribution": attribution,
                "quality": quality,
            },
            window_hours=window_hours,
        )

    params = {
        "environment": environment,
        "window_hours": window_hours,
        "limit": correlation_limit + 1,
    }
    identity_summary = mapped_one(
        db.execute(
            text(
                """
                WITH evidence AS (
                    SELECT attempt.id,
                           EXISTS (
                               SELECT 1
                               FROM ops_crawler_rollout_worker_snapshots snapshot
                               WHERE snapshot.environment = batch.environment
                                 AND snapshot.agent_id = attempt.agent_id
                                 AND snapshot.artifact_digest = attempt.artifact_digest
                                 AND snapshot.code_version = attempt.worker_code_version
                                 AND snapshot.config_revision = attempt.config_revision
                           ) AS identity_matches_known_snapshot
                    FROM ops_crawler_batches batch
                    JOIN ops_crawler_batch_tasks task ON task.batch_id = batch.id
                    JOIN ops_crawler_task_attempts attempt ON attempt.job_id = task.job_id
                    WHERE batch.environment = :environment
                      AND attempt.started_at >= CURRENT_TIMESTAMP
                          - (:window_hours * INTERVAL '1 hour')
                )
                SELECT COUNT(*) AS total_attempts,
                       COUNT(*) FILTER (WHERE identity_matches_known_snapshot)
                           AS identity_matched_attempts,
                       COUNT(*) FILTER (WHERE NOT identity_matches_known_snapshot)
                           AS unmatched_attempts
                FROM evidence
                """
            ),
            params,
        )
    ) or {}
    identity_summary = _int_metrics(
        identity_summary,
        ("total_attempts", "identity_matched_attempts", "unmatched_attempts"),
    )
    attribution.update(
        {
            "available": False,
            "has_data": bool(identity_summary.get("total_attempts")),
            "reasons": [_GENERATION_FENCE_REASON],
            "summary": identity_summary,
        }
    )

    batch_rows = mapped_rows(
        db.execute(
            text(
                """
                WITH recent_batches AS (
                    SELECT batch.id, batch.environment, batch.scheduled_slot,
                           batch.status, batch.expected_task_count,
                           batch.code_version, batch.artifact_digest,
                           batch.config_revision, batch.started_at,
                           batch.finished_at, batch.created_at,
                           COUNT(*) OVER () AS total_batches
                    FROM ops_crawler_batches batch
                    WHERE batch.environment = :environment
                      AND batch.scheduled_slot >= CURRENT_TIMESTAMP
                          - (:window_hours * INTERVAL '1 hour')
                    ORDER BY batch.scheduled_slot DESC, batch.id DESC
                    LIMIT :limit
                ),
                task_evidence AS (
                    SELECT task.batch_id, task.job_id, task.provider,
                           job.retry_count,
                           run.total_count, run.new_count, run.updated_count,
                           run.failed_count,
                           latest.id AS latest_attempt_id,
                           CASE WHEN latest.id IS NULL THEN NULL ELSE EXISTS (
                               SELECT 1
                               FROM ops_crawler_rollout_worker_snapshots snapshot
                               WHERE snapshot.environment = batch.environment
                                 AND snapshot.agent_id = latest.agent_id
                                 AND snapshot.artifact_digest = latest.artifact_digest
                                 AND snapshot.code_version = latest.worker_code_version
                                 AND snapshot.config_revision = latest.config_revision
                           ) END AS identity_matches_known_snapshot
                    FROM recent_batches batch
                    JOIN ops_crawler_batch_tasks task ON task.batch_id = batch.id
                    JOIN ops_jobs job ON job.id = task.job_id
                    LEFT JOIN ops_crawler_runs run ON run.job_id = job.id
                    LEFT JOIN LATERAL (
                        SELECT attempt.id, attempt.agent_id,
                               attempt.worker_code_version, attempt.artifact_digest,
                               attempt.config_revision
                        FROM ops_crawler_task_attempts attempt
                        WHERE attempt.job_id = task.job_id
                        ORDER BY attempt.attempt_no DESC, attempt.lease_epoch DESC
                        LIMIT 1
                    ) latest ON true
                ),
                task_rollup AS (
                    SELECT evidence.batch_id, COUNT(*) AS task_count,
                           ARRAY_AGG(DISTINCT evidence.provider ORDER BY evidence.provider)
                               AS providers,
                           COUNT(evidence.latest_attempt_id) AS latest_attempts,
                           COUNT(evidence.latest_attempt_id) FILTER (
                               WHERE evidence.identity_matches_known_snapshot
                           ) AS identity_matched_tasks,
                           SUM(evidence.total_count) AS collected_count,
                           SUM(evidence.new_count) AS new_count,
                           SUM(evidence.updated_count) AS updated_count,
                           SUM(evidence.failed_count) AS failed_item_count
                    FROM task_evidence evidence
                    GROUP BY evidence.batch_id
                ),
                attempt_rollup AS (
                    SELECT task.batch_id, COUNT(*) AS attempt_count,
                           COUNT(*) FILTER (WHERE attempt.attempt_no > 1)
                               AS retry_attempts,
                           COUNT(*) FILTER (
                               WHERE attempt.status = 'lease_lost'
                                  OR EXISTS (
                                      SELECT 1
                                      FROM ops_crawler_task_observations observation
                                      WHERE observation.attempt_id = attempt.id
                                        AND observation.job_id = attempt.job_id
                                        AND observation.attempt_no = attempt.attempt_no
                                        AND observation.lease_epoch = attempt.lease_epoch
                                        AND observation.observation_kind = 'lease_lost'
                                  )
                           ) AS lease_lost_attempts,
                           ROUND(SUM(EXTRACT(EPOCH FROM (
                               COALESCE(attempt.finished_at, CURRENT_TIMESTAMP)
                               - attempt.started_at
                           )))::numeric, 2) AS duration_seconds
                    FROM recent_batches batch
                    JOIN ops_crawler_batch_tasks task ON task.batch_id = batch.id
                    JOIN ops_crawler_task_attempts attempt ON attempt.job_id = task.job_id
                    GROUP BY task.batch_id
                )
                SELECT batch.id::text, batch.scheduled_slot, batch.status,
                       batch.expected_task_count, batch.code_version,
                       batch.artifact_digest, batch.config_revision,
                       batch.started_at, batch.finished_at, batch.created_at,
                       task.task_count, task.providers, task.latest_attempts,
                       task.identity_matched_tasks,
                       task.collected_count, task.new_count, task.updated_count,
                       task.failed_item_count,
                       attempt.attempt_count, attempt.retry_attempts,
                       attempt.lease_lost_attempts, attempt.duration_seconds,
                       staging.total_courses, staging.valid_courses,
                       staging.invalid_courses,
                       staging.result ->> 'promotion_policy' AS promotion_policy,
                       lower(COALESCE(staging.result ->> 'promotion_eligible', 'false')) = 'true'
                           AS promotion_eligible,
                       batch.total_batches
                FROM recent_batches batch
                LEFT JOIN task_rollup task ON task.batch_id = batch.id
                LEFT JOIN attempt_rollup attempt ON attempt.batch_id = batch.id
                LEFT JOIN crawl_batches staging ON staging.crawl_batch_id = batch.id::text
                ORDER BY batch.scheduled_slot DESC, batch.id DESC
                """
            ),
            params,
        )
    )
    batch_total = int(batch_rows[0].pop("total_batches")) if batch_rows else 0
    for row in batch_rows:
        row.pop("total_batches", None)
        normalized = _int_metrics(
            row,
            (
                "expected_task_count",
                "task_count",
                "latest_attempts",
                "identity_matched_tasks",
                "collected_count",
                "new_count",
                "updated_count",
                "failed_item_count",
                "attempt_count",
                "retry_attempts",
                "lease_lost_attempts",
                "total_courses",
                "valid_courses",
                "invalid_courses",
            ),
        )
        row.clear()
        row.update(normalized)
        if row.get("duration_seconds") is not None:
            row["duration_seconds"] = float(row["duration_seconds"])
        latest_attempts = int(row.get("latest_attempts") or 0)
        identity_matched = int(row.get("identity_matched_tasks") or 0)
        task_count = int(row.get("task_count") or 0)
        if latest_attempts == 0:
            row["attribution_state"] = "pending"
        elif identity_matched == task_count and latest_attempts == task_count:
            row["attribution_state"] = "identity_match_unattributed"
        elif identity_matched:
            row["attribution_state"] = "partial_identity_match_unattributed"
        else:
            row["attribution_state"] = "unmatched"
        row["rollout_id"] = None
        row["generation"] = None
    truncated = len(batch_rows) > correlation_limit
    batches.update(
        {
            "has_data": batch_total > 0,
            "items": batch_rows[:correlation_limit],
            "total": batch_total,
            "truncated": truncated,
            "attribution_semantics": "identity_match_without_generation_fence",
        }
    )
    return _section(
        {
            "generations": generations,
            "batches": batches,
            "attribution": attribution,
            "quality": quality,
        },
        window_hours=window_hours,
    )


def _correlations(
    db: Session,
    inventory: SchemaInventory,
    *,
    environment: str,
    window_hours: int,
    correlation_limit: int,
) -> dict[str, Any]:
    core_reasons = inventory.reasons_for(*_CORRELATION_CORE_TABLES)
    quality_reasons = [
        *core_reasons,
        *inventory.reasons_for("course_quality_score", "ops_quality_issues"),
        *(
            [quality_environment_reason]
            if (quality_environment_reason := _quality_environment_reason(environment))
            else []
        ),
        _GENERATION_QUALITY_FENCE_REASON,
    ]
    generations = _component(
        core_reasons,
        items=None,
        total=None,
        limit=correlation_limit,
        truncated=None,
    )
    batches = _component(
        core_reasons,
        items=None,
        total=None,
        limit=correlation_limit,
        truncated=None,
    )
    attribution = _component(
        core_reasons,
        summary=None,
        semantics="immutable_attempt_rollout_generation_exact_snapshot_match",
    )
    quality = _component(
        quality_reasons,
        items=None,
        total=None,
        limit=correlation_limit,
        truncated=None,
        score_semantics=None,
        issue_semantics=None,
    )
    if core_reasons:
        return _section(
            {
                "generations": generations,
                "batches": batches,
                "attribution": attribution,
                "quality": quality,
            },
            window_hours=window_hours,
        )

    params = {
        "environment": environment,
        "window_hours": window_hours,
        "limit": correlation_limit + 1,
    }
    attribution_summary = mapped_one(
        db.execute(
            text(
                """
                WITH recent AS (
                    SELECT attempt.id, attempt.rollout_id,
                           attempt.release_generation,
                           EXISTS (
                               SELECT 1
                               FROM ops_crawler_rollout_worker_snapshots snapshot
                               WHERE snapshot.environment = batch.environment
                                 AND snapshot.rollout_id = attempt.rollout_id
                                 AND snapshot.generation = attempt.release_generation
                                 AND snapshot.agent_id = attempt.agent_id
                                 AND snapshot.artifact_digest = attempt.artifact_digest
                                 AND snapshot.code_version = attempt.worker_code_version
                                 AND snapshot.config_revision = attempt.config_revision
                           ) AS exact_snapshot_match
                    FROM ops_crawler_batches batch
                    JOIN ops_crawler_batch_tasks task ON task.batch_id = batch.id
                    JOIN ops_crawler_task_attempts attempt ON attempt.job_id = task.job_id
                    WHERE batch.environment = :environment
                      AND attempt.started_at >= CURRENT_TIMESTAMP
                          - (:window_hours * INTERVAL '1 hour')
                ), recent_batches AS (
                    SELECT batch.id
                    FROM ops_crawler_batches batch
                    WHERE batch.environment = :environment
                      AND batch.scheduled_slot >= CURRENT_TIMESTAMP
                          - (:window_hours * INTERVAL '1 hour')
                ), latest_task_evidence AS (
                    SELECT task.batch_id, latest.id AS latest_attempt_id,
                           latest.rollout_id, latest.release_generation,
                           CASE WHEN latest.id IS NULL THEN FALSE ELSE EXISTS (
                               SELECT 1
                               FROM ops_crawler_rollout_worker_snapshots snapshot
                               WHERE snapshot.environment = :environment
                                 AND snapshot.rollout_id = latest.rollout_id
                                 AND snapshot.generation = latest.release_generation
                                 AND snapshot.agent_id = latest.agent_id
                                 AND snapshot.artifact_digest = latest.artifact_digest
                                 AND snapshot.code_version = latest.worker_code_version
                                 AND snapshot.config_revision = latest.config_revision
                           ) END AS exact_snapshot_match
                    FROM recent_batches batch
                    JOIN ops_crawler_batch_tasks task ON task.batch_id = batch.id
                    LEFT JOIN LATERAL (
                        SELECT attempt.id, attempt.agent_id,
                               attempt.rollout_id, attempt.release_generation,
                               attempt.worker_code_version,
                               attempt.artifact_digest, attempt.config_revision
                        FROM ops_crawler_task_attempts attempt
                        WHERE attempt.job_id = task.job_id
                        ORDER BY attempt.attempt_no DESC, attempt.lease_epoch DESC
                        LIMIT 1
                    ) latest ON true
                ), batch_evidence AS (
                    SELECT evidence.batch_id, COUNT(*) AS task_count,
                           COUNT(evidence.latest_attempt_id) AS latest_attempts,
                           COUNT(evidence.latest_attempt_id) FILTER (
                               WHERE evidence.rollout_id IS NULL
                                 AND evidence.release_generation IS NULL
                           ) AS legacy_tasks,
                           COUNT(evidence.latest_attempt_id) FILTER (
                               WHERE evidence.rollout_id IS NOT NULL
                                 AND evidence.release_generation IS NOT NULL
                                 AND evidence.exact_snapshot_match
                           ) AS exact_tasks,
                           COUNT(evidence.latest_attempt_id) FILTER (
                               WHERE evidence.rollout_id IS NOT NULL
                                 AND evidence.release_generation IS NOT NULL
                                 AND NOT evidence.exact_snapshot_match
                           ) AS mismatched_tasks,
                           COUNT(DISTINCT ROW(
                               evidence.rollout_id, evidence.release_generation
                           )) FILTER (
                               WHERE evidence.rollout_id IS NOT NULL
                                 AND evidence.release_generation IS NOT NULL
                                 AND evidence.exact_snapshot_match
                           ) AS exact_generations
                    FROM latest_task_evidence evidence
                    GROUP BY evidence.batch_id
                )
                SELECT COUNT(*) AS total_attempts,
                       COUNT(*) FILTER (
                           WHERE rollout_id IS NOT NULL
                             AND release_generation IS NOT NULL
                             AND exact_snapshot_match
                       ) AS attributed_attempts,
                       COUNT(*) FILTER (
                           WHERE rollout_id IS NULL
                             AND release_generation IS NULL
                       ) AS legacy_unattributed_attempts,
                       COUNT(*) FILTER (
                           WHERE rollout_id IS NOT NULL
                             AND release_generation IS NOT NULL
                             AND NOT exact_snapshot_match
                       ) AS rejected_mismatched_attempts,
                       (SELECT COUNT(*) FROM batch_evidence batch
                        WHERE batch.latest_attempts = batch.task_count
                          AND batch.exact_tasks = batch.task_count
                          AND batch.exact_generations = 1)
                           AS validation_attributed_batches,
                       (SELECT COUNT(*) FROM batch_evidence batch
                        WHERE batch.legacy_tasks > 0)
                           AS validation_legacy_excluded_batches,
                       (SELECT COUNT(*) FROM batch_evidence batch
                        WHERE batch.mismatched_tasks > 0
                           OR batch.exact_generations > 1)
                           AS validation_conflicting_excluded_batches,
                       (SELECT COUNT(*) FROM batch_evidence batch
                        WHERE NOT (
                            batch.latest_attempts = batch.task_count
                            AND batch.exact_tasks = batch.task_count
                            AND batch.exact_generations = 1
                        )
                          AND batch.legacy_tasks = 0
                          AND batch.mismatched_tasks = 0
                          AND batch.exact_generations <= 1)
                           AS validation_pending_or_partial_excluded_batches
                FROM recent
                """
            ),
            params,
        )
    ) or {}
    attribution_summary = _int_metrics(
        attribution_summary,
        (
            "total_attempts",
            "attributed_attempts",
            "legacy_unattributed_attempts",
            "rejected_mismatched_attempts",
            "validation_attributed_batches",
            "validation_legacy_excluded_batches",
            "validation_conflicting_excluded_batches",
            "validation_pending_or_partial_excluded_batches",
        ),
    )
    attribution.update(
        {
            "has_data": bool(attribution_summary.get("total_attempts")),
            "summary": attribution_summary,
        }
    )
    if attribution_summary.get("legacy_unattributed_attempts"):
        attribution["reasons"] = [_GENERATION_FENCE_REASON]
    excluded_validation_batches = sum(
        int(attribution_summary.get(key) or 0)
        for key in (
            "validation_legacy_excluded_batches",
            "validation_conflicting_excluded_batches",
            "validation_pending_or_partial_excluded_batches",
        )
    )
    if excluded_validation_batches:
        attribution["reasons"] = [
            *attribution.get("reasons", []),
            {
                "code": "validation_batch_generation_attribution_excluded",
                "message": (
                    "Validation totals exclude batches whose latest tasks do not all "
                    "share one exact release generation"
                ),
                "excluded_batches": excluded_validation_batches,
            },
        ]

    generation_rows = mapped_rows(
        db.execute(
            text(
                """
                WITH exact_attempts AS (
                    SELECT DISTINCT attempt.id, attempt.job_id,
                           attempt.rollout_id, attempt.release_generation,
                           attempt.attempt_no, attempt.status,
                           attempt.started_at, attempt.finished_at,
                           attempt.worker_code_version
                    FROM ops_crawler_batches batch
                    JOIN ops_crawler_batch_tasks task ON task.batch_id = batch.id
                    JOIN ops_crawler_task_attempts attempt ON attempt.job_id = task.job_id
                    JOIN ops_crawler_rollout_worker_snapshots snapshot
                      ON snapshot.environment = batch.environment
                     AND snapshot.rollout_id = attempt.rollout_id
                     AND snapshot.generation = attempt.release_generation
                     AND snapshot.agent_id = attempt.agent_id
                     AND snapshot.artifact_digest = attempt.artifact_digest
                     AND snapshot.code_version = attempt.worker_code_version
                     AND snapshot.config_revision = attempt.config_revision
                    WHERE batch.environment = :environment
                      AND attempt.rollout_id IS NOT NULL
                      AND attempt.release_generation IS NOT NULL
                      AND attempt.started_at >= CURRENT_TIMESTAMP
                          - (:window_hours * INTERVAL '1 hour')
                ), exact_latest_jobs AS (
                    SELECT exact.rollout_id, exact.release_generation,
                           exact.job_id
                    FROM exact_attempts exact
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM ops_crawler_task_attempts later
                        WHERE later.job_id = exact.job_id
                          AND later.attempt_no > exact.attempt_no
                    )
                ), generation_rollup AS (
                    SELECT exact.rollout_id, exact.release_generation,
                           MIN(exact.started_at) AS generation_started_at,
                           MAX(COALESCE(exact.finished_at, CURRENT_TIMESTAMP))
                               AS generation_finished_at,
                           ARRAY_AGG(DISTINCT exact.worker_code_version
                               ORDER BY exact.worker_code_version) AS code_versions,
                           COUNT(*) AS attempt_count,
                           COUNT(*) FILTER (
                               WHERE exact.status IN (
                                   'failed', 'timed_out', 'cancelled',
                                   'lease_lost', 'dead_lettered'
                               )
                           ) AS failed_attempts,
                           COUNT(DISTINCT exact.job_id) FILTER (
                               WHERE exact.attempt_no > 1
                           ) AS retried_tasks,
                           COUNT(*) FILTER (
                               WHERE exact.status = 'lease_lost'
                                  OR EXISTS (
                                      SELECT 1
                                      FROM ops_crawler_task_observations observation
                                      WHERE observation.attempt_id = exact.id
                                        AND observation.job_id = exact.job_id
                                        AND observation.observation_kind = 'lease_lost'
                                  )
                           ) AS lease_lost_attempts,
                           ROUND(SUM(EXTRACT(EPOCH FROM (
                               COALESCE(exact.finished_at, CURRENT_TIMESTAMP)
                               - exact.started_at
                           )))::numeric, 2) AS duration_seconds
                    FROM exact_attempts exact
                    GROUP BY exact.rollout_id, exact.release_generation
                ), collection AS (
                    SELECT exact.rollout_id, exact.release_generation,
                           SUM(run.total_count) AS collected_count,
                           SUM(run.new_count) AS new_count,
                           SUM(run.updated_count) AS updated_count,
                           SUM(run.failed_count) AS failed_item_count
                    FROM exact_latest_jobs exact
                    LEFT JOIN ops_crawler_runs run ON run.job_id = exact.job_id
                    GROUP BY exact.rollout_id, exact.release_generation
                ), candidate_batches AS (
                    SELECT DISTINCT task.batch_id
                    FROM exact_attempts exact
                    JOIN ops_crawler_batch_tasks task ON task.job_id = exact.job_id
                ), latest_batch_tasks AS (
                    SELECT task.batch_id, latest.id AS latest_attempt_id,
                           latest.rollout_id, latest.release_generation,
                           CASE WHEN latest.id IS NULL THEN FALSE ELSE EXISTS (
                               SELECT 1
                               FROM ops_crawler_rollout_worker_snapshots snapshot
                               WHERE snapshot.environment = :environment
                                 AND snapshot.rollout_id = latest.rollout_id
                                 AND snapshot.generation = latest.release_generation
                                 AND snapshot.agent_id = latest.agent_id
                                 AND snapshot.artifact_digest = latest.artifact_digest
                                 AND snapshot.code_version = latest.worker_code_version
                                 AND snapshot.config_revision = latest.config_revision
                           ) END AS exact_snapshot_match
                    FROM candidate_batches batch
                    JOIN ops_crawler_batch_tasks task ON task.batch_id = batch.batch_id
                    LEFT JOIN LATERAL (
                        SELECT attempt.id, attempt.agent_id,
                               attempt.rollout_id, attempt.release_generation,
                               attempt.worker_code_version,
                               attempt.artifact_digest, attempt.config_revision
                        FROM ops_crawler_task_attempts attempt
                        WHERE attempt.job_id = task.job_id
                        ORDER BY attempt.attempt_no DESC, attempt.lease_epoch DESC
                        LIMIT 1
                    ) latest ON true
                ), exact_validation_batches AS (
                    SELECT task.batch_id,
                           MIN(task.rollout_id::text)::uuid AS rollout_id,
                           MIN(task.release_generation) AS release_generation
                    FROM latest_batch_tasks task
                    GROUP BY task.batch_id
                    HAVING COUNT(task.latest_attempt_id) = COUNT(*)
                       AND COUNT(task.latest_attempt_id) FILTER (
                           WHERE task.rollout_id IS NOT NULL
                             AND task.release_generation IS NOT NULL
                             AND task.exact_snapshot_match
                       ) = COUNT(*)
                       AND COUNT(DISTINCT ROW(
                           task.rollout_id, task.release_generation
                       )) = 1
                ), validation AS (
                    SELECT exact.rollout_id, exact.release_generation,
                           SUM(staging.total_courses) AS total_courses,
                           SUM(staging.valid_courses) AS valid_courses,
                           SUM(staging.invalid_courses) AS invalid_courses
                    FROM exact_validation_batches exact
                    LEFT JOIN crawl_batches staging
                      ON staging.crawl_batch_id = exact.batch_id::text
                    GROUP BY exact.rollout_id, exact.release_generation
                )
                SELECT generation.rollout_id::text,
                       generation.release_generation AS generation,
                       generation.generation_started_at,
                       generation.generation_finished_at,
                       generation.code_versions, generation.attempt_count,
                       generation.failed_attempts, generation.retried_tasks,
                       generation.lease_lost_attempts,
                       generation.duration_seconds,
                       collection.collected_count, collection.new_count,
                       collection.updated_count, collection.failed_item_count,
                       validation.total_courses, validation.valid_courses,
                       validation.invalid_courses,
                       COUNT(*) OVER () AS total_generations
                FROM generation_rollup generation
                LEFT JOIN collection
                  ON collection.rollout_id = generation.rollout_id
                 AND collection.release_generation = generation.release_generation
                LEFT JOIN validation
                  ON validation.rollout_id = generation.rollout_id
                 AND validation.release_generation = generation.release_generation
                ORDER BY generation.generation_started_at DESC,
                         generation.rollout_id DESC,
                         generation.release_generation DESC
                LIMIT :limit
                """
            ),
            params,
        )
    )
    generation_total = (
        int(generation_rows[0].pop("total_generations")) if generation_rows else 0
    )
    generation_metrics = (
        "attempt_count",
        "failed_attempts",
        "retried_tasks",
        "lease_lost_attempts",
        "collected_count",
        "new_count",
        "updated_count",
        "failed_item_count",
        "total_courses",
        "valid_courses",
        "invalid_courses",
    )
    for row in generation_rows:
        row.pop("total_generations", None)
        normalized = _int_metrics(row, generation_metrics)
        row.clear()
        row.update(normalized)
        if row.get("duration_seconds") is not None:
            row["duration_seconds"] = float(row["duration_seconds"])
    _comparison_rows(
        generation_rows,
        metric_keys=(
            "attempt_count",
            "failed_attempts",
            "collected_count",
            "new_count",
            "updated_count",
            "invalid_courses",
        ),
    )
    generations.update(
        {
            "has_data": generation_total > 0,
            "items": generation_rows[:correlation_limit],
            "total": generation_total,
            "truncated": len(generation_rows) > correlation_limit,
        }
    )

    batch_rows = mapped_rows(
        db.execute(
            text(
                """
                WITH recent_batches AS (
                    SELECT batch.id, batch.scheduled_slot, batch.status,
                           batch.expected_task_count, batch.code_version,
                           batch.artifact_digest, batch.config_revision,
                           batch.started_at, batch.finished_at, batch.created_at,
                           COUNT(*) OVER () AS total_batches
                    FROM ops_crawler_batches batch
                    WHERE batch.environment = :environment
                      AND batch.scheduled_slot >= CURRENT_TIMESTAMP
                          - (:window_hours * INTERVAL '1 hour')
                    ORDER BY batch.scheduled_slot DESC, batch.id DESC
                    LIMIT :limit
                ), task_evidence AS (
                    SELECT task.batch_id, task.provider,
                           run.total_count, run.new_count, run.updated_count,
                           run.failed_count,
                           latest.id AS latest_attempt_id,
                           latest.rollout_id,
                           latest.release_generation,
                           CASE WHEN latest.id IS NULL THEN FALSE ELSE EXISTS (
                               SELECT 1
                               FROM ops_crawler_rollout_worker_snapshots snapshot
                               WHERE snapshot.environment = :environment
                                 AND snapshot.rollout_id = latest.rollout_id
                                 AND snapshot.generation = latest.release_generation
                                 AND snapshot.agent_id = latest.agent_id
                                 AND snapshot.artifact_digest = latest.artifact_digest
                                 AND snapshot.code_version = latest.worker_code_version
                                 AND snapshot.config_revision = latest.config_revision
                           ) END AS exact_snapshot_match
                    FROM recent_batches batch
                    JOIN ops_crawler_batch_tasks task ON task.batch_id = batch.id
                    LEFT JOIN ops_crawler_runs run ON run.job_id = task.job_id
                    LEFT JOIN LATERAL (
                        SELECT attempt.id, attempt.attempt_no,
                               attempt.lease_epoch, attempt.agent_id,
                               attempt.rollout_id, attempt.release_generation,
                               attempt.worker_code_version,
                               attempt.artifact_digest, attempt.config_revision
                        FROM ops_crawler_task_attempts attempt
                        WHERE attempt.job_id = task.job_id
                        ORDER BY attempt.attempt_no DESC, attempt.lease_epoch DESC
                        LIMIT 1
                    ) latest ON true
                ), task_rollup AS (
                    SELECT evidence.batch_id, COUNT(*) AS task_count,
                           ARRAY_AGG(DISTINCT evidence.provider
                               ORDER BY evidence.provider) AS providers,
                           COUNT(evidence.latest_attempt_id) AS latest_attempts,
                           COUNT(evidence.latest_attempt_id) FILTER (
                               WHERE evidence.rollout_id IS NULL
                                 AND evidence.release_generation IS NULL
                           ) AS legacy_unattributed_tasks,
                           COUNT(evidence.latest_attempt_id) FILTER (
                               WHERE evidence.rollout_id IS NOT NULL
                                 AND evidence.release_generation IS NOT NULL
                                 AND evidence.exact_snapshot_match
                           ) AS attributed_tasks,
                           COUNT(DISTINCT ROW(
                               evidence.rollout_id, evidence.release_generation
                           )) FILTER (
                               WHERE evidence.rollout_id IS NOT NULL
                                 AND evidence.release_generation IS NOT NULL
                                 AND evidence.exact_snapshot_match
                           ) AS attributed_generations,
                           MIN(evidence.rollout_id::text) FILTER (
                               WHERE evidence.exact_snapshot_match
                           ) AS rollout_id,
                           MIN(evidence.release_generation) FILTER (
                               WHERE evidence.exact_snapshot_match
                           ) AS release_generation,
                           SUM(evidence.total_count) AS collected_count,
                           SUM(evidence.new_count) AS new_count,
                           SUM(evidence.updated_count) AS updated_count,
                           SUM(evidence.failed_count) AS failed_item_count
                    FROM task_evidence evidence
                    GROUP BY evidence.batch_id
                ), attempt_rollup AS (
                    SELECT task.batch_id, COUNT(*) AS attempt_count,
                           COUNT(*) FILTER (WHERE attempt.attempt_no > 1)
                               AS retry_attempts,
                           COUNT(*) FILTER (
                               WHERE attempt.status = 'lease_lost'
                                  OR EXISTS (
                                      SELECT 1
                                      FROM ops_crawler_task_observations observation
                                      WHERE observation.attempt_id = attempt.id
                                        AND observation.job_id = attempt.job_id
                                        AND observation.observation_kind = 'lease_lost'
                                  )
                           ) AS lease_lost_attempts,
                           ROUND(SUM(EXTRACT(EPOCH FROM (
                               COALESCE(attempt.finished_at, CURRENT_TIMESTAMP)
                               - attempt.started_at
                           )))::numeric, 2) AS duration_seconds
                    FROM recent_batches batch
                    JOIN ops_crawler_batch_tasks task ON task.batch_id = batch.id
                    JOIN ops_crawler_task_attempts attempt ON attempt.job_id = task.job_id
                    GROUP BY task.batch_id
                )
                SELECT batch.id::text, batch.scheduled_slot, batch.status,
                       batch.expected_task_count, batch.code_version,
                       batch.artifact_digest, batch.config_revision,
                       batch.started_at, batch.finished_at, batch.created_at,
                       task.task_count, task.providers, task.latest_attempts,
                       task.legacy_unattributed_tasks, task.attributed_tasks,
                       task.attributed_generations, task.rollout_id,
                       task.release_generation AS generation,
                       task.collected_count, task.new_count, task.updated_count,
                       task.failed_item_count, attempt.attempt_count,
                       attempt.retry_attempts, attempt.lease_lost_attempts,
                       attempt.duration_seconds, staging.total_courses,
                       staging.valid_courses, staging.invalid_courses,
                       staging.result ->> 'promotion_policy' AS promotion_policy,
                       lower(COALESCE(
                           staging.result ->> 'promotion_eligible', 'false'
                       )) = 'true' AS promotion_eligible,
                       batch.total_batches
                FROM recent_batches batch
                LEFT JOIN task_rollup task ON task.batch_id = batch.id
                LEFT JOIN attempt_rollup attempt ON attempt.batch_id = batch.id
                LEFT JOIN crawl_batches staging
                  ON staging.crawl_batch_id = batch.id::text
                ORDER BY batch.scheduled_slot DESC, batch.id DESC
                """
            ),
            params,
        )
    )
    batch_total = int(batch_rows[0].pop("total_batches")) if batch_rows else 0
    batch_ints = (
        "expected_task_count",
        "task_count",
        "latest_attempts",
        "legacy_unattributed_tasks",
        "attributed_tasks",
        "attributed_generations",
        "generation",
        "collected_count",
        "new_count",
        "updated_count",
        "failed_item_count",
        "attempt_count",
        "retry_attempts",
        "lease_lost_attempts",
        "total_courses",
        "valid_courses",
        "invalid_courses",
    )
    for row in batch_rows:
        row.pop("total_batches", None)
        normalized = _int_metrics(row, batch_ints)
        row.clear()
        row.update(normalized)
        if row.get("duration_seconds") is not None:
            row["duration_seconds"] = float(row["duration_seconds"])
        task_count = int(row.get("task_count") or 0)
        attributed = int(row.get("attributed_tasks") or 0)
        generations_count = int(row.get("attributed_generations") or 0)
        legacy = int(row.get("legacy_unattributed_tasks") or 0)
        if int(row.get("latest_attempts") or 0) == 0:
            row["attribution_state"] = "pending"
        elif attributed == task_count and generations_count == 1:
            row["attribution_state"] = "attributed"
        elif attributed and generations_count == 1:
            row["attribution_state"] = "partial_attributed"
            row["rollout_id"] = None
            row["generation"] = None
        elif legacy:
            row["attribution_state"] = "legacy_unattributed"
            row["rollout_id"] = None
            row["generation"] = None
        else:
            row["attribution_state"] = "conflicting_or_unmatched"
            row["rollout_id"] = None
            row["generation"] = None
    batches.update(
        {
            "has_data": batch_total > 0,
            "items": batch_rows[:correlation_limit],
            "total": batch_total,
            "truncated": len(batch_rows) > correlation_limit,
            "attribution_semantics": "all_latest_tasks_share_one_exact_generation",
        }
    )

    return _section(
        {
            "generations": generations,
            "batches": batches,
            "attribution": attribution,
            "quality": quality,
        },
        window_hours=window_hours,
    )


def _all_unavailable(
    *,
    environment: str,
    window_hours: int,
    provider_limit: int,
    worker_limit: int,
    correlation_limit: int,
    heartbeat_timeout_seconds: int,
    reason: dict[str, Any],
) -> dict[str, Any]:
    section_names = (
        "deployment",
        "collection",
        "providers",
        "quality",
        "workers",
        "queue",
        "correlations",
    )
    sections = {
        name: {
            "available": False,
            "complete": False,
            "has_data": None,
            "reasons": [reason],
            "components": {},
        }
        for name in section_names
    }
    return {
        "schema_version": 2,
        "available": False,
        "complete": False,
        "partial": False,
        "environment": environment,
        "generated_at": datetime.now(timezone.utc),
        "data_source": {
            "kind": "crawler_control_database",
            "pool": "dedicated_readonly",
            "authority_verified": False,
        },
        "window_hours": window_hours,
        "limits": {
            "providers": provider_limit,
            "workers": worker_limit,
            "correlations": correlation_limit,
        },
        "heartbeat_timeout_seconds": heartbeat_timeout_seconds,
        "reasons": [reason],
        **sections,
    }


def _build_crawler_analytics_in_session(
    db: Session | None,
    *,
    environment: str,
    window_hours: int,
    provider_limit: int,
    worker_limit: int,
    correlation_limit: int,
    heartbeat_timeout_seconds: int,
) -> dict[str, Any]:
    """Build a bounded, environment-scoped central crawler operations snapshot."""

    if environment not in {"production", "staging", "development"}:
        raise ValueError("unsupported crawler analytics environment")
    if not 1 <= window_hours <= 720:
        raise ValueError("crawler analytics window is out of bounds")
    if not 1 <= provider_limit <= 200:
        raise ValueError("crawler analytics provider limit is out of bounds")
    if not 1 <= worker_limit <= 500:
        raise ValueError("crawler analytics worker limit is out of bounds")
    if not 1 <= correlation_limit <= 100:
        raise ValueError("crawler analytics correlation limit is out of bounds")
    if not 30 <= heartbeat_timeout_seconds <= 3_600:
        raise ValueError("crawler analytics heartbeat timeout is out of bounds")

    if db is None:
        return _all_unavailable(
            environment=environment,
            window_hours=window_hours,
            provider_limit=provider_limit,
            worker_limit=worker_limit,
            correlation_limit=correlation_limit,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            reason={
                "code": "crawler_control_database_not_configured",
                "message": "The dedicated crawler-control read-only API pool is not configured",
                "required_connection": "dedicated_crawler_analytics_readonly_pool",
            },
        )

    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
    except SQLAlchemyError:
        logger.warning("Crawler analytics read-only transaction could not be established", exc_info=True)
        db.rollback()
        return _all_unavailable(
            environment=environment,
            window_hours=window_hours,
            provider_limit=provider_limit,
            worker_limit=worker_limit,
            correlation_limit=correlation_limit,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            reason={
                "code": "crawler_control_readonly_boundary_unavailable",
                "message": "The crawler-control API could not establish a read-only transaction",
                "required_connection": "dedicated_crawler_analytics_readonly_pool",
            },
        )

    try:
        inventory = _schema_inventory(db)
    except SQLAlchemyError:
        logger.warning("Crawler analytics schema inventory is unavailable", exc_info=True)
        return _all_unavailable(
            environment=environment,
            window_hours=window_hours,
            provider_limit=provider_limit,
            worker_limit=worker_limit,
            correlation_limit=correlation_limit,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            reason={
                "code": "schema_inventory_unavailable",
                "message": "Crawler analytics schema could not be inspected",
            },
        )

    try:
        authority_reason = _control_database_authority_reason(
            db,
            inventory,
            environment=environment,
        )
    except SQLAlchemyError:
        logger.warning("Crawler analytics database marker is unreadable", exc_info=True)
        authority_reason = {
            "code": "crawler_control_database_marker_unreadable",
            "message": "The API login cannot verify the crawler control database marker",
            "required_connection": "dedicated_crawler_analytics_readonly_pool",
        }
    if authority_reason is not None:
        return _all_unavailable(
            environment=environment,
            window_hours=window_hours,
            provider_limit=provider_limit,
            worker_limit=worker_limit,
            correlation_limit=correlation_limit,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            reason=authority_reason,
        )

    builders: dict[str, Callable[[], dict[str, Any]]] = {
        "deployment": lambda: _deployment(
            db,
            inventory,
            environment=environment,
            worker_limit=worker_limit,
            fresh_seconds=heartbeat_timeout_seconds,
        ),
        "collection": lambda: _collection(
            db,
            inventory,
            environment=environment,
            window_hours=window_hours,
        ),
        "providers": lambda: _providers(
            db,
            inventory,
            environment=environment,
            window_hours=window_hours,
            provider_limit=provider_limit,
        ),
        "quality": lambda: _quality(
            db,
            inventory,
            environment=environment,
            provider_limit=provider_limit,
        ),
        "workers": lambda: _workers(
            db,
            inventory,
            environment=environment,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            worker_limit=worker_limit,
        ),
        "queue": lambda: _queue(
            db,
            inventory,
            environment=environment,
            window_hours=window_hours,
        ),
        "correlations": lambda: _correlations(
            db,
            inventory,
            environment=environment,
            window_hours=window_hours,
            correlation_limit=correlation_limit,
        ),
    }
    sections: dict[str, dict[str, Any]] = {}
    builder_items = list(builders.items())
    for index, (name, builder) in enumerate(builder_items):
        try:
            sections[name] = builder()
        except SQLAlchemyError:
            logger.warning("Crawler analytics section is unavailable section=%s", name, exc_info=True)
            db.rollback()
            sections[name] = {
                "available": False,
                "complete": False,
                "has_data": None,
                "reasons": [
                    {
                        "code": "query_unavailable",
                        "message": "The crawler analytics section could not be queried",
                    }
                ],
                "components": {},
            }
            try:
                db.execute(text("SET TRANSACTION READ ONLY"))
            except SQLAlchemyError:
                logger.warning(
                    "Crawler analytics read-only transaction could not be re-established section=%s",
                    name,
                    exc_info=True,
                )
                db.rollback()
                for remaining_name, _remaining_builder in builder_items[index + 1 :]:
                    sections[remaining_name] = {
                        "available": False,
                        "complete": False,
                        "has_data": None,
                        "reasons": [
                            {
                                "code": "crawler_control_readonly_boundary_unavailable",
                                "message": (
                                    "The crawler-control API could not re-establish "
                                    "a read-only transaction"
                                ),
                            }
                        ],
                        "components": {},
                    }
                break
    available_names = [name for name, section in sections.items() if section["available"]]
    complete = all(section["complete"] for section in sections.values())
    reasons = [
        {"section": name, **reason}
        for name, section in sections.items()
        for reason in section.get("reasons", [])
    ]
    return {
        "schema_version": 2,
        "available": bool(available_names),
        "complete": complete,
        "partial": bool(available_names) and not complete,
        "environment": environment,
        "generated_at": datetime.now(timezone.utc),
        "data_source": {
            "kind": "crawler_control_database",
            "pool": "dedicated_readonly",
            "authority_verified": True,
        },
        "window_hours": window_hours,
        "limits": {
            "providers": provider_limit,
            "workers": worker_limit,
            "correlations": correlation_limit,
        },
        "heartbeat_timeout_seconds": heartbeat_timeout_seconds,
        "reasons": reasons,
        **sections,
    }


def build_crawler_analytics(
    db: Session | None,
    *,
    environment: str,
    window_hours: int,
    provider_limit: int,
    worker_limit: int,
    correlation_limit: int,
    heartbeat_timeout_seconds: int,
) -> dict[str, Any]:
    """Build one snapshot and always release any API-owned DB transaction."""

    if db is None:
        return _build_crawler_analytics_in_session(
            db,
            environment=environment,
            window_hours=window_hours,
            provider_limit=provider_limit,
            worker_limit=worker_limit,
            correlation_limit=correlation_limit,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
        )
    try:
        return _build_crawler_analytics_in_session(
            db,
            environment=environment,
            window_hours=window_hours,
            provider_limit=provider_limit,
            worker_limit=worker_limit,
            correlation_limit=correlation_limit,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
        )
    finally:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.warning("Crawler analytics transaction cleanup failed", exc_info=True)


def _batch_detail_unavailable(
    *,
    environment: str,
    batch_id: str,
    task_limit: int,
    task_offset: int,
    reason: dict[str, Any],
) -> dict[str, Any]:
    return {
        "available": False,
        "environment": environment,
        "batch_id": batch_id,
        "item": None,
        "tasks": None,
        "total_tasks": None,
        "task_limit": task_limit,
        "task_offset": task_offset,
        "truncated": None,
        "attribution": {
            "available": False,
            "has_data": None,
            "reasons": [reason],
        },
        "reasons": [reason],
    }


def _sanitized_batch_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "collection_outcome",
        "collection_complete",
        "providers_total",
        "providers_completed",
        "providers_failed",
        "failed_providers",
        "promotion_policy",
        "promotion_eligible",
        "finalizer_reason",
        "control_plane_rejected",
    }
    return sanitize_for_audit({key: value[key] for key in sorted(allowed & value.keys())})


def _build_crawler_batch_detail_in_session(
    db: Session | None,
    *,
    environment: str,
    batch_id: str,
    task_limit: int,
    task_offset: int,
) -> dict[str, Any]:
    if environment not in {"production", "staging", "development"}:
        raise ValueError("unsupported crawler analytics environment")
    if not 1 <= task_limit <= 200:
        raise ValueError("crawler analytics task detail limit is out of bounds")
    if not 0 <= task_offset <= 100_000:
        raise ValueError("crawler analytics task detail offset is out of bounds")

    missing_pool = {
        "code": "crawler_control_database_not_configured",
        "message": "The dedicated crawler-control read-only API pool is not configured",
        "required_connection": "dedicated_crawler_analytics_readonly_pool",
    }
    if db is None:
        return _batch_detail_unavailable(
            environment=environment,
            batch_id=batch_id,
            task_limit=task_limit,
            task_offset=task_offset,
            reason=missing_pool,
        )
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
    except SQLAlchemyError:
        db.rollback()
        return _batch_detail_unavailable(
            environment=environment,
            batch_id=batch_id,
            task_limit=task_limit,
            task_offset=task_offset,
            reason={
                "code": "crawler_control_readonly_boundary_unavailable",
                "message": "The crawler-control API could not establish a read-only transaction",
            },
        )
    try:
        inventory = _schema_inventory(db)
        authority_reason = _control_database_authority_reason(
            db,
            inventory,
            environment=environment,
        )
        if authority_reason is not None:
            return _batch_detail_unavailable(
                environment=environment,
                batch_id=batch_id,
                task_limit=task_limit,
                task_offset=task_offset,
                reason=authority_reason,
            )
        schema_reasons = inventory.reasons_for(*_CORRELATION_CORE_TABLES)
        if schema_reasons:
            return _batch_detail_unavailable(
                environment=environment,
                batch_id=batch_id,
                task_limit=task_limit,
                task_offset=task_offset,
                reason={
                    "code": "crawler_correlation_schema_unavailable",
                    "message": "Central crawler correlation evidence schema is incomplete",
                    "details": schema_reasons,
                },
            )
        item = mapped_one(
            db.execute(
                text(
                    """
                    SELECT batch.id::text, batch.environment, batch.scheduled_slot,
                           batch.status, batch.expected_task_count, batch.code_version,
                           batch.artifact_digest, batch.config_revision,
                           batch.started_at, batch.finished_at, batch.created_at,
                           ROUND(EXTRACT(EPOCH FROM (
                               COALESCE(batch.finished_at, CURRENT_TIMESTAMP)
                               - COALESCE(batch.started_at, batch.created_at)
                           ))::numeric, 2) AS duration_seconds,
                           staging.total_courses, staging.valid_courses,
                           staging.invalid_courses, staging.result AS validation_result
                    FROM ops_crawler_batches batch
                    LEFT JOIN crawl_batches staging
                      ON staging.crawl_batch_id = batch.id::text
                    WHERE batch.id = CAST(:batch_id AS uuid)
                      AND batch.environment = :environment
                    """
                ),
                {"batch_id": batch_id, "environment": environment},
            )
        )
        if item is None:
            return {
                "available": True,
                "environment": environment,
                "batch_id": batch_id,
                "item": None,
                "tasks": [],
                "total_tasks": 0,
                "task_limit": task_limit,
                "task_offset": task_offset,
                "truncated": False,
                "attribution": {"available": True, "has_data": False, "reasons": []},
                "reasons": [],
            }
        item = _int_metrics(
            item,
            ("expected_task_count", "total_courses", "valid_courses", "invalid_courses"),
        )
        if item.get("duration_seconds") is not None:
            item["duration_seconds"] = float(item["duration_seconds"])
        item["validation"] = _sanitized_batch_result(item.pop("validation_result", None))

        total_tasks = int(
            db.execute(
                text(
                    "SELECT COUNT(*) FROM ops_crawler_batch_tasks "
                    "WHERE batch_id = CAST(:batch_id AS uuid)"
                ),
                {"batch_id": batch_id},
            ).scalar()
            or 0
        )
        tasks = mapped_rows(
            db.execute(
                text(
                    """
                    SELECT task.task_key, task.provider, task.allowed_output_providers,
                           task.required, task.shard_index, task.shard_count,
                           task.job_id::text, job.status AS job_status,
                           job.retry_count, job.queued_at, job.started_at AS job_started_at,
                           job.finished_at AS job_finished_at,
                           run.id::text AS run_id, run.status AS run_status,
                           run.total_count, run.processed_count, run.success_count,
                           run.failed_count, run.new_count, run.updated_count,
                           latest.id::text AS attempt_id, latest.attempt_no,
                           latest.lease_epoch, latest.agent_id::text,
                           latest.status AS attempt_status,
                           latest.worker_code_version, latest.artifact_digest,
                           latest.config_revision, latest.rollout_id::text,
                           latest.release_generation AS generation,
                           latest.started_at AS attempt_started_at,
                           latest.finished_at AS attempt_finished_at,
                           latest.exit_code, latest.error_code,
                           ROUND(EXTRACT(EPOCH FROM (
                               COALESCE(latest.finished_at, CURRENT_TIMESTAMP)
                               - latest.started_at
                           ))::numeric, 2) AS attempt_duration_seconds,
                           COALESCE(attempt_stats.attempt_count, 0) AS attempt_count,
                           COALESCE(attempt_stats.lease_lost_attempts, 0)
                               AS lease_lost_attempts,
                           COALESCE(identity.known_identity_count, 0)
                               AS known_identity_count,
                           identity.worker_key
                    FROM ops_crawler_batch_tasks task
                    JOIN ops_jobs job ON job.id = task.job_id
                    LEFT JOIN ops_crawler_runs run ON run.job_id = job.id
                    LEFT JOIN LATERAL (
                        SELECT attempt.id, attempt.job_id, attempt.attempt_no,
                               attempt.lease_epoch, attempt.agent_id, attempt.status,
                               attempt.worker_code_version, attempt.artifact_digest,
                               attempt.config_revision, attempt.rollout_id,
                               attempt.release_generation, attempt.started_at,
                               attempt.finished_at, attempt.exit_code,
                               attempt.error_code
                        FROM ops_crawler_task_attempts attempt
                        WHERE attempt.job_id = task.job_id
                        ORDER BY attempt.attempt_no DESC, attempt.lease_epoch DESC
                        LIMIT 1
                    ) latest ON true
                    LEFT JOIN LATERAL (
                        SELECT COUNT(*) AS attempt_count,
                               COUNT(*) FILTER (
                                   WHERE attempt.status = 'lease_lost'
                                      OR EXISTS (
                                          SELECT 1
                                          FROM ops_crawler_task_observations observation
                                          WHERE observation.attempt_id = attempt.id
                                            AND observation.job_id = attempt.job_id
                                            AND observation.attempt_no = attempt.attempt_no
                                            AND observation.lease_epoch = attempt.lease_epoch
                                            AND observation.observation_kind = 'lease_lost'
                                      )
                               ) AS lease_lost_attempts
                        FROM ops_crawler_task_attempts attempt
                        WHERE attempt.job_id = task.job_id
                    ) attempt_stats ON true
                    LEFT JOIN LATERAL (
                        SELECT COUNT(*)::integer AS known_identity_count,
                               MIN(snapshot.worker_key) AS worker_key
                        FROM ops_crawler_rollout_worker_snapshots snapshot
                        WHERE latest.id IS NOT NULL
                          AND snapshot.environment = :environment
                          AND snapshot.rollout_id = latest.rollout_id
                          AND snapshot.generation = latest.release_generation
                          AND snapshot.agent_id = latest.agent_id
                          AND snapshot.artifact_digest = latest.artifact_digest
                          AND snapshot.code_version = latest.worker_code_version
                          AND snapshot.config_revision = latest.config_revision
                    ) identity ON true
                    WHERE task.batch_id = CAST(:batch_id AS uuid)
                    ORDER BY task.provider, task.shard_index, task.task_key
                    LIMIT :task_limit OFFSET :task_offset
                    """
                ),
                {
                    "batch_id": batch_id,
                    "environment": environment,
                    "task_limit": task_limit,
                    "task_offset": task_offset,
                },
            )
        )
        attribution_reasons: list[dict[str, Any]] = []
        for task in tasks:
            normalized = _int_metrics(
                task,
                (
                    "retry_count",
                    "total_count",
                    "processed_count",
                    "success_count",
                    "failed_count",
                    "new_count",
                    "updated_count",
                    "attempt_no",
                    "lease_epoch",
                    "attempt_count",
                    "lease_lost_attempts",
                    "known_identity_count",
                    "generation",
                ),
            )
            task.clear()
            task.update(normalized)
            if task.get("attempt_duration_seconds") is not None:
                task["attempt_duration_seconds"] = float(task["attempt_duration_seconds"])
            identity_matches = int(task.get("known_identity_count") or 0)
            if task.get("attempt_id") is None:
                task["attribution_state"] = "pending"
            elif (
                identity_matches == 1
                and task.get("rollout_id") is not None
                and task.get("generation") is not None
            ):
                task["attribution_state"] = "attributed"
            elif task.get("rollout_id") is None and task.get("generation") is None:
                task["attribution_state"] = "legacy_unattributed"
                task["worker_key"] = None
            else:
                task["attribution_state"] = "conflicting_or_unmatched"
                task["rollout_id"] = None
                task["generation"] = None
                task["worker_key"] = None
        attempted = sum(task.get("attempt_id") is not None for task in tasks)
        legacy_unattributed = sum(
            task.get("attribution_state") == "legacy_unattributed" for task in tasks
        )
        attributed = sum(task.get("attribution_state") == "attributed" for task in tasks)
        conflicting = sum(
            task.get("attribution_state") == "conflicting_or_unmatched" for task in tasks
        )
        if legacy_unattributed:
            attribution_reasons.append(_GENERATION_FENCE_REASON)
        return {
            "available": True,
            "environment": environment,
            "batch_id": batch_id,
            "item": item,
            "tasks": tasks,
            "total_tasks": total_tasks,
            "task_limit": task_limit,
            "task_offset": task_offset,
            "truncated": task_offset + len(tasks) < total_tasks,
            "attribution": {
                "available": True,
                "has_data": any(task.get("attempt_id") for task in tasks),
                "reasons": attribution_reasons,
                "semantics": "immutable_attempt_rollout_generation_exact_snapshot_match",
                "summary": {
                    "attempted_tasks": attempted,
                    "attributed_tasks": attributed,
                    "legacy_unattributed_tasks": legacy_unattributed,
                    "conflicting_or_unmatched_tasks": conflicting,
                },
            },
            "reasons": [],
        }
    except SQLAlchemyError:
        logger.warning("Crawler analytics batch detail query failed", exc_info=True)
        return _batch_detail_unavailable(
            environment=environment,
            batch_id=batch_id,
            task_limit=task_limit,
            task_offset=task_offset,
            reason={
                "code": "query_unavailable",
                "message": "Central crawler batch evidence could not be queried",
            },
        )


def build_crawler_batch_detail(
    db: Session | None,
    *,
    environment: str,
    batch_id: str,
    task_limit: int,
    task_offset: int,
) -> dict[str, Any]:
    """Build a bounded batch drill-down from the same dedicated control DB."""

    if db is None:
        return _build_crawler_batch_detail_in_session(
            db,
            environment=environment,
            batch_id=batch_id,
            task_limit=task_limit,
            task_offset=task_offset,
        )
    try:
        return _build_crawler_batch_detail_in_session(
            db,
            environment=environment,
            batch_id=batch_id,
            task_limit=task_limit,
            task_offset=task_offset,
        )
    finally:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.warning("Crawler analytics batch detail transaction cleanup failed", exc_info=True)
