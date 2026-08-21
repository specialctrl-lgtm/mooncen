from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session


# LIMIT 0 keeps readiness independent of whether production currently has rows,
# while still proving that the runtime login can resolve the columns and SELECT
# from every relation required by the public API.
PUBLIC_API_READINESS_QUERIES = (
    (
        "SELECT id, provider, branch_code, name, lat, lon, geocode_status, "
        "geocode_reason_code, geocode_attempt_count, geocode_candidates, "
        "geocode_next_retry_at, geocode_last_error, geocode_last_attempt_at "
        "FROM branches LIMIT 0"
    ),
    (
        "SELECT id, provider, branch_id, title, status, source_endpoint, is_active "
        "FROM courses LIMIT 0"
    ),
    "SELECT id, email, provider, auth_token_version FROM users LIMIT 0",
    (
        "SELECT version, notice_type, legal_basis, notice_hash, notice_json, "
        "effective_date, created_at FROM privacy_notice_versions LIMIT 0"
    ),
    (
        "SELECT id, user_id, notice_version, acceptance_type, acquisition_method, "
        "accepted_at FROM user_privacy_acceptances LIMIT 0"
    ),
)

OPS_API_READINESS_QUERIES = (
    *PUBLIC_API_READINESS_QUERIES,
    "SELECT id, job_type, status, progress FROM ops_jobs LIMIT 0",
    "SELECT id, job_id, log_level, message FROM ops_job_logs LIMIT 0",
)


def assert_database_ready(
    db: Session,
    *,
    queries: Iterable[str] = PUBLIC_API_READINESS_QUERIES,
) -> None:
    """Raise when a critical relation, column, or SELECT privilege is missing."""

    for statement in queries:
        db.execute(text(statement))
