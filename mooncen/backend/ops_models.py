"""SQLAlchemy mappings for the integrated Ops Console.

The runtime APIs intentionally tolerate a database that has not received the
Ops migration yet and report those integrations as unavailable.  These
mappings are used by workers and future write services after the migration is
applied.
"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.sql import func

from .database import Base


class OpsAgent(Base):
    __tablename__ = "ops_agents"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name = Column(Text, nullable=False)
    hostname = Column(Text, nullable=False)
    environment = Column(Text, nullable=False)
    os_type = Column(Text, nullable=False)
    ip_address = Column(INET)
    version = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'unknown'"))
    capabilities = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    credential_hint = Column(Text)
    maintenance_mode = Column(Boolean, nullable=False, server_default=text("false"))
    last_seen_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsService(Base):
    __tablename__ = "ops_services"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    agent_id = Column(UUID(as_uuid=True), ForeignKey("ops_agents.id", ondelete="SET NULL"))
    service_name = Column(Text, nullable=False)
    service_type = Column(Text, nullable=False)
    environment = Column(Text, nullable=False)
    health_url = Column(Text)
    grafana_url = Column(Text)
    current_version = Column(Text)
    current_commit = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'unknown'"))
    response_time_ms = Column(Integer)
    last_error = Column(Text)
    dependencies = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    last_checked_at = Column(DateTime(timezone=True))
    last_restarted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsJob(Base):
    __tablename__ = "ops_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    job_type = Column(Text, nullable=False)
    status = Column(Text, nullable=False, server_default=text("'queued'"))
    environment = Column(Text, nullable=False)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("ops_agents.id", ondelete="SET NULL"))
    service_id = Column(UUID(as_uuid=True), ForeignKey("ops_services.id", ondelete="SET NULL"))
    parent_job_id = Column(UUID(as_uuid=True), ForeignKey("ops_jobs.id", ondelete="SET NULL"))
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    target_key = Column(Text)
    deduplication_key = Column(Text)
    parameters = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    progress = Column(Integer, nullable=False, server_default=text("0"))
    result = Column(JSONB)
    error_code = Column(Text)
    error_message = Column(Text)
    retry_count = Column(Integer, nullable=False, server_default=text("0"))
    max_retries = Column(Integer, nullable=False, server_default=text("0"))
    queued_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    assigned_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    heartbeat_at = Column(DateTime(timezone=True))
    cancel_requested_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsJobLog(Base):
    __tablename__ = "ops_job_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("ops_jobs.id", ondelete="CASCADE"), nullable=False)
    log_level = Column(Text, nullable=False, server_default=text("'info'"))
    message = Column(Text, nullable=False)
    metadata_json = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsDeployment(Base):
    __tablename__ = "ops_deployments"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    job_id = Column(UUID(as_uuid=True), ForeignKey("ops_jobs.id", ondelete="RESTRICT"), nullable=False, unique=True)
    environment = Column(Text, nullable=False)
    service_type = Column(Text, nullable=False)
    previous_version = Column(Text)
    target_version = Column(Text, nullable=False)
    previous_commit = Column(Text)
    target_commit = Column(Text, nullable=False)
    branch = Column(Text)
    deployment_mode = Column(Text, nullable=False, server_default=text("'native'"))
    deployment_action = Column(Text, nullable=False, server_default=text("'deploy'"))
    target_environment = Column(Text)
    target_name = Column(Text)
    target_identity = Column(Text)
    container_release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ops_container_releases.id", ondelete="RESTRICT"),
    )
    container_release_digest = Column(Text)
    previous_container_release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ops_container_releases.id", ondelete="RESTRICT"),
    )
    previous_container_release_digest = Column(Text)
    validation_receipt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ops_container_validation_receipts.id", ondelete="RESTRICT"),
    )
    validation_receipt_digest = Column(Text)
    approval_evidence_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ops_container_approval_evidence.id", ondelete="RESTRICT"),
    )
    api_image_digest = Column(Text)
    frontend_image_digest = Column(Text)
    bundle_sha256 = Column(Text)
    deployment_status = Column(Text, nullable=False, server_default=text("'queued'"))
    health_check_result = Column(JSONB)
    smoke_test_result = Column(JSONB)
    rollback_deployment_id = Column(UUID(as_uuid=True), ForeignKey("ops_deployments.id", ondelete="SET NULL"))
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsContainerRelease(Base):
    __tablename__ = "ops_container_releases"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    release_digest = Column(Text, nullable=False, unique=True)
    base_commit = Column(Text, nullable=False)
    source_tree = Column(Text, nullable=False)
    snapshot_commit = Column(Text, nullable=False)
    platform = Column(Text, nullable=False)
    api_image_digest = Column(Text, nullable=False)
    frontend_image_digest = Column(Text, nullable=False)
    bundle_sha256 = Column(Text, nullable=False)
    compose_sha256 = Column(Text, nullable=False)
    build_policy_sha256 = Column(Text, nullable=False)
    migration_ledger_sha256 = Column(Text, nullable=False)
    manifest_json = Column(JSONB, nullable=False)
    builder_target_identity = Column(Text, nullable=False)
    builder_hostname = Column(Text, nullable=False)
    built_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    built_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsContainerValidationReceipt(Base):
    __tablename__ = "ops_container_validation_receipts"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    receipt_digest = Column(Text, nullable=False, unique=True)
    release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ops_container_releases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    release_digest = Column(Text, nullable=False)
    source_tree = Column(Text, nullable=False)
    target = Column(Text, nullable=False)
    target_identity = Column(Text, nullable=False)
    platform = Column(Text, nullable=False)
    bundle_sha256 = Column(Text, nullable=False)
    compose_sha256 = Column(Text, nullable=False)
    api_image_digest = Column(Text, nullable=False)
    frontend_image_digest = Column(Text, nullable=False)
    checks = Column(JSONB, nullable=False)
    status = Column(Text, nullable=False)
    receipt_json = Column(JSONB, nullable=False)
    validated_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsContainerApprovalEvidence(Base):
    __tablename__ = "ops_container_approval_evidence"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    action = Column(Text, nullable=False)
    target_environment = Column(Text, nullable=False)
    target_identity = Column(Text, nullable=False)
    target_name = Column(Text, nullable=False)
    release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ops_container_releases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    release_digest = Column(Text, nullable=False)
    current_release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ops_container_releases.id", ondelete="RESTRICT"),
    )
    current_release_digest = Column(Text)
    validation_receipt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ops_container_validation_receipts.id", ondelete="RESTRICT"),
    )
    validation_receipt_digest = Column(Text)
    typed_confirmation = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    approved_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsAuditLog(Base):
    __tablename__ = "ops_audit_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    action = Column(Text, nullable=False)
    resource_type = Column(Text, nullable=False)
    resource_id = Column(Text)
    ip_address = Column(INET)
    user_agent = Column(Text)
    before_data = Column(JSONB)
    after_data = Column(JSONB)
    result = Column(Text, nullable=False)
    job_id = Column(UUID(as_uuid=True), ForeignKey("ops_jobs.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsAlert(Base):
    __tablename__ = "ops_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    severity = Column(Text, nullable=False)
    alert_type = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    resource_type = Column(Text)
    resource_id = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'open'"))
    detected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    acknowledged_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at = Column(DateTime(timezone=True))
    resolved_at = Column(DateTime(timezone=True))
    metadata_json = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsCrawlerRun(Base):
    __tablename__ = "ops_crawler_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    crawler_name = Column(Text, nullable=False)
    content_type = Column(Text, nullable=False)
    provider = Column(Text)
    branch = Column(Text)
    source_url = Column(Text)
    current_stage = Column(Text)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("ops_agents.id", ondelete="SET NULL"))
    job_id = Column(UUID(as_uuid=True), ForeignKey("ops_jobs.id", ondelete="SET NULL"), unique=True)
    status = Column(Text, nullable=False, server_default=text("'queued'"))
    run_mode = Column(Text, nullable=False, server_default=text("'apply'"))
    total_count = Column(Integer, nullable=False, server_default=text("0"))
    processed_count = Column(Integer, nullable=False, server_default=text("0"))
    success_count = Column(Integer, nullable=False, server_default=text("0"))
    failed_count = Column(Integer, nullable=False, server_default=text("0"))
    new_count = Column(Integer, nullable=False, server_default=text("0"))
    updated_count = Column(Integer, nullable=False, server_default=text("0"))
    deleted_candidate_count = Column(Integer, nullable=False, server_default=text("0"))
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsCrawlerError(Base):
    __tablename__ = "ops_crawler_errors"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    crawler_run_id = Column(UUID(as_uuid=True), ForeignKey("ops_crawler_runs.id", ondelete="CASCADE"), nullable=False)
    error_type = Column(Text, nullable=False)
    provider = Column(Text)
    branch = Column(Text)
    source_url = Column(Text)
    message = Column(Text, nullable=False)
    stack_trace = Column(Text)
    screenshot_path = Column(Text)
    html_path = Column(Text)
    retry_count = Column(Integer, nullable=False, server_default=text("0"))
    resolved = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsQualityIssue(Base):
    __tablename__ = "ops_quality_issues"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    issue_key = Column(Text)
    issue_type = Column(Text, nullable=False)
    severity = Column(Text, nullable=False)
    content_type = Column(Text, nullable=False)
    resource_type = Column(Text, nullable=False)
    resource_id = Column(Text)
    provider = Column(Text)
    branch = Column(Text)
    field_name = Column(Text)
    current_value = Column(JSONB)
    previous_value = Column(JSONB)
    status = Column(Text, nullable=False, server_default=text("'open'"))
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    auto_fixable = Column(Boolean, nullable=False, server_default=text("false"))
    blocked_sync = Column(Boolean, nullable=False, server_default=text("false"))
    detected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at = Column(DateTime(timezone=True))
    metadata_json = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OpsContentOverride(Base):
    __tablename__ = "ops_content_overrides"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    resource_type = Column(Text, nullable=False)
    resource_id = Column(Text, nullable=False)
    field_name = Column(Text, nullable=False)
    source_value = Column(JSONB)
    normalized_value = Column(JSONB)
    manual_value = Column(JSONB)
    is_locked = Column(Boolean, nullable=False, server_default=text("false"))
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
