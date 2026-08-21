from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.ops import service as ops_service
from backend.ops.schemas import (
    ContainerBuildRequest,
    ContainerNativeRollbackRequest,
    ContainerPromotionRequest,
    ContainerRollbackRequest,
    ContainerValidationRequest,
)
from backend.routers import ops_v2
from deploy.an2p import mooncen_register_container_evidence as registration_entrypoint
from tools import register_container_deployment_evidence as evidence_cli
from deploy.docker.release_manifest import (
    VALIDATION_CHECKS,
    create_release_manifest,
    create_validation_receipt,
)
from ops_agent.container_deployment import (
    ContainerDeploymentError,
    ContainerExecutionEvidence,
    build_container_controller_command,
    build_container_ingress_commands,
    build_container_status_command,
    build_container_worker_lease_command,
    container_runtime_cas,
    parse_container_action_result,
    parse_container_ingress_result,
    parse_container_pipeline_step_result,
    parse_container_status,
    parse_container_worker_lease_result,
    reconcile_container_status,
)


ROOT = Path(__file__).resolve().parents[1]
IDENTITY = "8" * 64
TARGET_IDENTITY = "9" * 64
TREE = "2" * 40
RELEASE_DIGEST = "d" * 64
RECEIPT_DIGEST = "e" * 64
PREVIOUS_DIGEST = "f" * 64
NATIVE_BASELINE_IDENTITY = "a" * 64
NATIVE_STATE_SHA256 = hashlib.sha256(b"null").hexdigest()
AGENT_ID = "44444444-4444-4444-8444-444444444444"
LEASE_TOKEN = "55555555-5555-4555-8555-555555555555"
LEASE_EPOCH = 42


def _release() -> dict[str, object]:
    return create_release_manifest(
        base_commit="1" * 40,
        source_tree=TREE,
        snapshot_commit="3" * 40,
        platform="linux/amd64",
        bundle_sha256="4" * 64,
        compose_sha256="5" * 64,
        build_policy_sha256="6" * 64,
        migration_ledger_sha256="7" * 64,
        images={
            "api": {
                "tag": f"mooncen/api:release-{TREE}",
                "image_id": "sha256:" + "a" * 64,
            },
            "frontend": {
                "tag": f"mooncen/frontend:release-{TREE}",
                "image_id": "sha256:" + "b" * 64,
            },
        },
        created_at="2026-08-19T12:00:00Z",
    )


def _receipt(release: dict[str, object]) -> dict[str, object]:
    return create_validation_receipt(
        release=release,
        target="an2p-dev",
        target_identity=IDENTITY,
        checks={name: True for name in VALIDATION_CHECKS},
        validated_at="2026-08-19T12:10:00Z",
        expires_at="2026-08-20T12:10:00Z",
    )


def _registration_result() -> dict[str, object]:
    return {
        "expires_at": "2026-08-20T12:10:00Z",
        "receipt_digest": RECEIPT_DIGEST,
        "receipt_id": "22222222-2222-4222-8222-222222222222",
        "release_digest": RELEASE_DIGEST,
        "release_id": "11111111-1111-4111-8111-111111111111",
        "schema_version": 1,
        "source_tree": TREE,
        "status": "passed",
        "target": "an2p-dev",
        "target_identity": IDENTITY,
    }


def _execution_evidence(action: str = "promote") -> ContainerExecutionEvidence:
    release = _release()
    initial_native = action == "promote"
    native_target = action == "rollback_native"
    return ContainerExecutionEvidence(
        job_id="11111111-1111-4111-8111-111111111111",
        agent_id=AGENT_ID,
        lease_token=LEASE_TOKEN,
        lease_epoch=LEASE_EPOCH,
        deployment_id="22222222-2222-4222-8222-222222222222",
        action=action,
        target_name="cloud",
        target_environment="production",
        target_identity=TARGET_IDENTITY,
        target_runtime_kind="native" if native_target else "container",
        native_baseline_identity=NATIVE_BASELINE_IDENTITY if native_target else None,
        approval_id="33333333-3333-4333-8333-333333333333",
        expected_runtime_generation=0 if initial_native else 1,
        expected_controller_state_sha256=(NATIVE_STATE_SHA256 if initial_native else "c" * 64),
        expected_active_release_digest=None if initial_native else PREVIOUS_DIGEST,
        expected_previous_release_digest=(None if initial_native else str(release["release_digest"])),
        release=None if native_target else release,
        receipt=_receipt(release) if action == "promote" else None,
        previous_release=None,
    )


def _native_fallback() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "deploy_commit": "1" * 40,
        "deploy_archive_sha256": "2" * 64,
        "deploy_info_sha256": "3" * 64,
        "prebuild_sha256": "4" * 64,
        "runtime_tree_sha256": "5" * 64,
        "control_sha256": {
            "mooncen-ai-worker.service": "6" * 64,
            "mooncen-api.service": "7" * 64,
            "mooncen-frontend.service": "8" * 64,
            "mooncen-native-runtime-condition": "9" * 64,
        },
        "units": {
            unit: {"active": False, "enabled": False}
            for unit in (
                "mooncen-api.service",
                "mooncen-frontend.service",
                "mooncen-ai-worker.service",
            )
        },
    }
    value["identity"] = hashlib.sha256(_canonical(value).encode("ascii")).hexdigest()
    return value


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_container_action_confirmations_bind_full_evidence_identity() -> None:
    ContainerBuildRequest(
        target="an2p-dev",
        target_identity=IDENTITY,
        source_commit="1" * 40,
        source_tree=TREE,
        confirmation=f"BUILD {IDENTITY} {TREE}",
    )
    ContainerValidationRequest(
        target_identity=IDENTITY,
        release_digest=RELEASE_DIGEST,
        confirmation=f"VALIDATE {IDENTITY} {RELEASE_DIGEST}",
    )
    ContainerPromotionRequest(
        target="cloud",
        target_identity=TARGET_IDENTITY,
        release_digest=RELEASE_DIGEST,
        validation_receipt_digest=RECEIPT_DIGEST,
        expected_runtime_generation=0,
        expected_controller_state_sha256=NATIVE_STATE_SHA256,
        reason="reviewed development validation",
        confirmation=(f"PROMOTE {TARGET_IDENTITY} {RELEASE_DIGEST} {RECEIPT_DIGEST} 0 {NATIVE_STATE_SHA256}"),
    )
    ContainerRollbackRequest(
        target="cloud",
        target_identity=TARGET_IDENTITY,
        current_release_digest=RELEASE_DIGEST,
        rollback_release_digest=PREVIOUS_DIGEST,
        expected_runtime_generation=1,
        expected_controller_state_sha256="c" * 64,
        reason="restore reviewed previous release",
        confirmation=(f"ROLLBACK {TARGET_IDENTITY} {RELEASE_DIGEST} {PREVIOUS_DIGEST} 1 {'c' * 64}"),
    )
    ContainerNativeRollbackRequest(
        target="cloud",
        target_identity=TARGET_IDENTITY,
        current_release_digest=RELEASE_DIGEST,
        native_baseline_identity=NATIVE_BASELINE_IDENTITY,
        expected_runtime_generation=1,
        expected_controller_state_sha256="c" * 64,
        reason="enter reviewed native maintenance",
        confirmation=(
            f"ROLLBACK_NATIVE {TARGET_IDENTITY} {RELEASE_DIGEST} "
            f"{NATIVE_BASELINE_IDENTITY} 1 {'c' * 64}"
        ),
    )

    with pytest.raises(ValidationError, match="promotion confirmation"):
        ContainerPromotionRequest(
            target="cloud",
            target_identity=TARGET_IDENTITY,
            release_digest=RELEASE_DIGEST,
            validation_receipt_digest=RECEIPT_DIGEST,
            expected_runtime_generation=0,
            expected_controller_state_sha256=NATIVE_STATE_SHA256,
            reason="reviewed development validation",
            confirmation=f"PROMOTE {TARGET_IDENTITY} {RELEASE_DIGEST} {'0' * 64}",
        )
    with pytest.raises(ValidationError):
        ContainerValidationRequest(
            target="cloud",  # type: ignore[arg-type]
            target_identity=IDENTITY,
            release_digest=RELEASE_DIGEST,
            confirmation=f"VALIDATE {IDENTITY} {RELEASE_DIGEST}",
        )


def test_container_evidence_migration_is_append_only_and_fail_closed() -> None:
    migration = (ROOT / "DB/migrations/20260819_001_ops_container_deployment_pipeline.sql").read_text(encoding="utf-8")

    assert "trg_ops_container_releases_immutable" in migration
    assert "trg_ops_container_validation_receipts_immutable" in migration
    assert "trg_ops_container_approval_evidence_immutable" in migration
    assert "UPDATE ops_container_releases" not in migration
    assert "DELETE FROM ops_container_releases" not in migration

    # A compromised writer cannot claim passed with a missing/false check or
    # smuggle unknown canonical evidence keys through direct SQL INSERT.
    assert "validate_ops_container_release_manifest" in migration
    assert "validate_ops_container_receipt_binding" in migration
    assert "jsonb_object_keys(NEW.manifest_json)" in migration
    assert "jsonb_object_keys(NEW.receipt_json)" in migration
    for check_name in VALIDATION_CHECKS:
        assert f"'checks'->'{check_name}'" in migration
    assert "(NEW.status = 'passed') IS DISTINCT FROM all_checks_passed" in migration
    assert "receipt scalar columns and checks do not match canonical receipt_json" in migration

    # Approval evidence has a short lifetime, is consumed once, and binds the
    # actual target independently from the control-plane environment label.
    assert "expires_at <= approved_at + INTERVAL '15 minutes'" in migration
    assert "UNIQUE (approval_evidence_id)" in migration
    assert "TG_OP = 'INSERT' AND approval_value.expires_at <= CURRENT_TIMESTAMP" in migration
    assert "approval_value.target_environment <> NEW.target_environment" in migration
    assert "approval_value.target_name <> NEW.target_name" in migration
    assert "parameters->>'target_environment'" in migration
    assert "job_target_environment IS DISTINCT FROM NEW.target_environment" in migration
    assert "expected_runtime_generation BIGINT NOT NULL" in migration
    assert "expected_controller_state_sha256 TEXT NOT NULL" in migration
    assert "expected_previous_release_digest = release_digest" in migration
    assert "runtime_generation = expected_runtime_generation + 1" in migration
    assert "approval_value.expected_runtime_generation" in migration
    assert "job_expected_controller_state_sha256" in migration

    # Each active container job owns one fresh DB lease and one globally
    # monotonic remote fence epoch. Terminal rows cannot retain a raw token.
    assert "ops_container_deployment_lease_epoch_seq" in migration
    assert "chk_ops_jobs_container_deployment_lease" in migration
    assert "status IN ('assigned', 'running')" in migration
    assert "lease_token IS NOT NULL" in migration
    assert "lease_epoch > 0" in migration
    assert "leased_until IS NOT NULL" in migration
    assert "REVOKE ALL PRIVILEGES ON SEQUENCE" in migration
    assert "GRANT USAGE, SELECT ON SEQUENCE" in migration


def test_only_dedicated_deployment_worker_can_append_container_evidence() -> None:
    migration = (ROOT / "DB/migrations/20260819_001_ops_container_deployment_pipeline.sql").read_text(encoding="utf-8")
    roles = (ROOT / "DB/roles.sql").read_text(encoding="utf-8")

    for source in (migration, roles):
        assert "TO mooncen_deployment_worker" in source
        assert "ops_container_validation_receipts\n        TO mooncen_crawler" not in source
        assert "ops_container_validation_receipts\n            TO mooncen_crawler" not in source
    assert "GRANT INSERT ON TABLE ops_container_approval_evidence TO mooncen_api" in migration
    assert "GRANT INSERT ON TABLE ops_container_releases TO mooncen_api" not in migration
    # Re-running the shared role convergence after this additive migration
    # must not revoke the worker's lease fencing authority.
    for column in ("lease_token", "lease_epoch", "leased_until"):
        assert column in roles
    assert "ops_container_deployment_lease_epoch_seq" in roles
    assert "TO mooncen_deployment_worker" in roles


def test_operator_docs_bind_every_container_mutation_to_the_remote_claim() -> None:
    production = (ROOT / "docs/docker-production.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs/docker-ops-console.md").read_text(encoding="utf-8")

    assert "native_intent, schema_version, state, transaction, worker_lease" in production
    assert "lease-bind JOB32 EPOCH20 TOKEN32" in production
    assert "stage TREE40 JOB32 EPOCH20 TOKEN32" in production
    assert "EXPECTED_STATE_SHA25664 JOB32 EPOCH20 TOKEN32" in production
    assert "remote_claim_fencing_ready=true" in ops
    assert "global sequence epoch" in ops
    assert "recovered_previous`도 authoritative fence 전에는 terminal이 아니다" in ops

    for unsafe in (
        "ssh cloud-deploy",
        "mooncen-container-release stage $source_tree",
        'assert set(status) == {"native_intent", "schema_version", "state", "transaction"}',
    ):
        assert unsafe not in production


class _Result:
    def __init__(self, row: dict | None = None):
        self.row = row

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict | None:
        return self.row


class _RegistrationDB:
    def __init__(self, rows: list[dict | None]):
        self.rows = iter(rows)
        self.calls: list[tuple[str, dict | None]] = []

    def execute(self, statement: object, params: dict | None = None) -> _Result:
        self.calls.append((str(statement), params))
        return _Result(next(self.rows))


def test_registration_helpers_map_canonical_release_and_receipt_without_update() -> None:
    release = _release()
    release_db = _RegistrationDB([{"id": "release-id", "release_digest": release["release_digest"]}])
    registered_release = ops_service.register_container_release_evidence(
        release_db,  # type: ignore[arg-type]
        release,
        builder_target_identity=IDENTITY,
        builder_hostname="an2p",
    )
    release_sql, release_params = release_db.calls[0]
    assert registered_release["id"] == "release-id"
    assert release_params is not None
    assert release_params["api_image_digest"] == release["images"]["api"]["image_id"]
    assert release_params["bundle_sha256"] == release["bundle_sha256"]
    assert "ON CONFLICT (release_digest) DO NOTHING" in release_sql
    assert "UPDATE" not in release_sql

    receipt = _receipt(release)
    receipt_db = _RegistrationDB(
        [
            {"id": "release-id", "release_digest": release["release_digest"]},
            {"id": "receipt-id", "receipt_digest": receipt["receipt_digest"]},
        ]
    )
    registered_receipt = ops_service.register_container_validation_evidence(
        receipt_db,  # type: ignore[arg-type]
        receipt,
    )
    receipt_sql, receipt_params = receipt_db.calls[1]
    assert registered_receipt["id"] == "receipt-id"
    assert receipt_params is not None
    assert receipt_params["target"] == "an2p-dev"
    assert receipt_params["target_identity"] == IDENTITY
    assert receipt_params["status"] == "passed"
    assert "ON CONFLICT (receipt_digest) DO NOTHING" in receipt_sql
    assert "UPDATE" not in receipt_sql


def test_promotion_rejects_missing_exact_pass_before_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyDB:
        def execute(self, _statement: object, _params: dict | None = None) -> _Result:
            return _Result()

    monkeypatch.setattr(ops_v2, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(
        ops_v2,
        "_require_exact_reviewed_container_target",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        ops_v2,
        "_configured_container_development_identity",
        lambda: IDENTITY,
    )
    monkeypatch.setattr(
        ops_v2,
        "_container_release_for_transition",
        lambda *_args: {"id": "release-id", "release_digest": RELEASE_DIGEST},
    )
    payload = ContainerPromotionRequest(
        target="cloud",
        target_identity=TARGET_IDENTITY,
        release_digest=RELEASE_DIGEST,
        validation_receipt_digest=RECEIPT_DIGEST,
        expected_runtime_generation=0,
        expected_controller_state_sha256=NATIVE_STATE_SHA256,
        reason="reviewed development validation",
        confirmation=(f"PROMOTE {TARGET_IDENTITY} {RELEASE_DIGEST} {RECEIPT_DIGEST} 0 {NATIVE_STATE_SHA256}"),
    )

    with pytest.raises(HTTPException) as raised:
        ops_v2.request_container_promotion(
            payload,
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            EmptyDB(),  # type: ignore[arg-type]
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "container_validation_receipt_not_pass"


def test_container_readiness_claims_execution_only_for_exact_an2p_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_v2, "_container_pipeline_missing_tables", lambda _db: [])
    monkeypatch.setattr(
        ops_v2,
        "deployment_readiness",
        lambda: {
            "available": False,
            "default_target": "cloud",
            "targets": [
                {
                    "name": "cloud",
                    "environment": "production",
                    "deploy_profile": "full-stack",
                    "target_identity": TARGET_IDENTITY,
                }
            ],
            "snapshot": {"source_tree": TREE},
            "reasons": [{"code": "powershell_missing"}],
        },
    )
    monkeypatch.setattr(
        ops_v2,
        "_container_latest_release",
        lambda _db: {"id": "release-id", "release_digest": RELEASE_DIGEST},
    )
    monkeypatch.setattr(
        ops_v2,
        "_container_latest_pass_receipt",
        lambda _db, _release_id, _target_identity: {
            "id": "receipt-id",
            "receipt_digest": RECEIPT_DIGEST,
        },
    )
    monkeypatch.setattr(ops_v2, "_container_target_states", lambda _db: [])
    monkeypatch.setattr(
        ops_v2,
        "_container_active_promotion_approval",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        ops_v2,
        "_configured_container_development_identity",
        lambda: IDENTITY,
    )
    monkeypatch.setattr(
        ops_v2,
        "_container_deployment_agent",
        lambda _db: {"id": "agent-id", "hostname": "an2p", "status": "healthy"},
    )
    monkeypatch.setattr(
        ops_v2,
        "read_container_controller_status",
        lambda **_kwargs: {
            "schema_version": 1,
            "native_intent": None,
            "state": None,
            "transaction": None,
            "worker_lease": None,
        },
    )
    monkeypatch.setattr(
        ops_v2,
        "container_transport_service_boundary_ready",
        lambda **_kwargs: True,
    )

    readiness = ops_v2._container_deployment_readiness_payload(SimpleNamespace())  # type: ignore[arg-type]

    assert readiness["promotion_evidence_ready"] is True
    assert readiness["executor_supported"] is True
    assert readiness["remote_claim_fencing_ready"] is True
    assert readiness["can_promote"] is True
    assert readiness["approval_evidence_ready"] is False
    assert readiness["actions"]["promote"]["can_request"] is True
    assert readiness["development_target"] == {
        "target": "an2p-dev",
        "target_identity": IDENTITY,
    }


def test_fixed_container_controller_argv_never_accepts_browser_paths() -> None:
    evidence = _execution_evidence()
    transport = {
        "ssh_executable": Path("/usr/bin/ssh"),
        "ssh_config": Path("/private/cloud-deploy.ssh_config"),
        "identity_file": Path("/private/cloud-deploy-ed25519"),
        "validate_files": False,
    }
    claim = [UUID(evidence.job_id).hex, f"{LEASE_EPOCH:020d}", UUID(LEASE_TOKEN).hex]

    for action in ("stage", "load-images", "preflight"):
        command = build_container_controller_command(evidence, action, **transport)
        assert command[-9:] == [
            "/usr/bin/sudo",
            "-n",
            "--",
            "/usr/local/libexec/mooncen-container-release",
            action,
            TREE,
            *claim,
        ]
        assert "shell" not in " ".join(command)
    promote = build_container_controller_command(evidence, "promote", **transport)
    assert promote[-13:] == [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/local/libexec/mooncen-container-release",
        "promote",
        TREE,
        "0000000000",
        "0" * 64,
        "0" * 64,
        NATIVE_STATE_SHA256,
        *claim,
    ]
    lease_bind = build_container_worker_lease_command(
        evidence,
        "lease-bind",
        **transport,
    )
    assert lease_bind[-8:] == [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/local/libexec/mooncen-container-release",
        "lease-bind",
        *claim,
    ]
    status = build_container_status_command(**transport)
    assert status[-5:] == [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/local/libexec/mooncen-container-release",
        "status",
    ]
    with pytest.raises(ContainerDeploymentError, match="unsupported"):
        build_container_controller_command(evidence, "/tmp/operator-command", **transport)

    native_evidence = _execution_evidence("rollback_native")
    native_command = build_container_controller_command(
        native_evidence,
        "rollback-native",
        **transport,
    )
    assert native_command[-12:] == [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/local/libexec/mooncen-container-release",
        "rollback-native",
        "0000000001",
        PREVIOUS_DIGEST,
        str(_release()["release_digest"]),
        "c" * 64,
        *claim,
    ]


def test_ingress_uses_forced_stdin_uploads_without_scp_or_remote_paths(
    tmp_path: Path,
) -> None:
    evidence = _execution_evidence()
    local = tmp_path / TREE
    local.mkdir()
    files = {
        name: local / name
        for name in (
            "compose.production.yaml",
            "images.tar",
            "release.json",
            "validation.json",
        )
    }
    for index, path in enumerate(files.values(), start=1):
        path.write_bytes(f"payload-{index}".encode("ascii"))
        path.chmod(0o600)
    plan = build_container_ingress_commands(
        evidence,
        release_files=files,
        ssh_executable=Path("/usr/bin/ssh"),
        ssh_config=Path("/private/cloud-deploy.ssh_config"),
        identity_file=Path("/private/cloud-deploy-ed25519"),
        validate_files=False,
    )
    destination = f"/var/lib/mooncen-container-ingress/{TREE}"

    assert plan["destination"] == destination
    assert plan["prepare"][-3:] == [
        "/usr/local/libexec/mooncen-container-ingress",
        "prepare",
        TREE,
    ]
    assert plan["abort"][-3:] == [
        "/usr/local/libexec/mooncen-container-ingress",
        "abort",
        TREE,
    ]
    assert len(plan["uploads"]) == 4
    for upload in plan["uploads"]:
        assert "-n" not in upload.command
        assert upload.command[-6:] == (
            "/usr/local/libexec/mooncen-container-ingress",
            "upload",
            TREE,
            upload.name,
            str(upload.size),
            upload.sha256,
        )
        expected = {
            "name": upload.name,
            "schema_version": 1,
            "sha256": upload.sha256,
            "size": upload.size,
            "source_tree": TREE,
            "uploaded": True,
        }
        assert parse_container_ingress_result(
            _canonical(expected), evidence, "upload", upload=upload
        ) == expected
    flattened = " ".join(
        [*plan["prepare"], *plan["abort"], *(part for upload in plan["uploads"] for part in upload.command)]
    )
    assert "scp" not in flattened
    assert destination not in flattened


def test_pipeline_step_and_final_status_are_exactly_bound_to_leased_evidence() -> None:
    evidence = _execution_evidence()
    reference = {
        "release_digest": evidence.release["release_digest"],
        "source_tree": evidence.release["source_tree"],
        "image_ids": {service: evidence.release["images"][service]["image_id"] for service in ("api", "frontend")},
    }
    stage = {
        "schema_version": 1,
        "staged": True,
        **reference,
        "bundle_sha256": evidence.release["bundle_sha256"],
        "compose_sha256": evidence.release["compose_sha256"],
    }
    loaded = {"schema_version": 1, "images_loaded": True, **reference}
    preflight = {
        "schema_version": 1,
        "preflight": "passed",
        **reference,
        "migration_ledger_sha256": evidence.release["migration_ledger_sha256"],
    }
    for action, value in (("stage", stage), ("load-images", loaded), ("preflight", preflight)):
        assert parse_container_pipeline_step_result(_canonical(value), evidence, action) == value

    changed = {**stage, "unexpected": True}
    with pytest.raises(ContainerDeploymentError, match="differs"):
        parse_container_pipeline_step_result(_canonical(changed), evidence, "stage")

    state = {
        "schema_version": 1,
        "generation": 1,
        "mode": "docker",
        "active": reference,
        "previous": None,
        "native_fallback": _native_fallback(),
        "updated_at": "2026-08-19T13:00:00Z",
    }
    result = parse_container_action_result(_canonical(state), evidence)
    assert result["activated_release_digest"] == evidence.release_digest
    status = parse_container_status(
        _canonical(
            {
                "schema_version": 1,
                "native_intent": None,
                "state": state,
                "transaction": None,
                "worker_lease": None,
            }
        )
    )
    assert reconcile_container_status(status, evidence) == "success"

    stale_generation_state = {**state, "generation": 2}
    stale_generation_status = parse_container_status(
        _canonical(
            {
                "schema_version": 1,
                "native_intent": None,
                "state": stale_generation_state,
                "transaction": None,
                "worker_lease": None,
            }
        )
    )
    assert reconcile_container_status(stale_generation_status, evidence) == "recovery_required"


def test_controller_status_cas_uses_full_canonical_state_and_blocks_native_intent() -> None:
    native_status = {
        "schema_version": 1,
        "native_intent": None,
        "state": None,
        "transaction": None,
        "worker_lease": None,
    }
    assert container_runtime_cas(native_status) == {
        "expected_runtime_generation": 0,
        "expected_controller_state_sha256": NATIVE_STATE_SHA256,
        "expected_active_release_digest": None,
        "expected_previous_release_digest": None,
        "native_baseline_identity": None,
        "state": None,
    }
    with pytest.raises(ContainerDeploymentError, match="native deployment intent"):
        container_runtime_cas(
            {
                **native_status,
                "native_intent": {"schema_version": 1, "token": "a" * 32},
            }
        )


def test_controller_worker_lease_is_hash_bound_to_exact_db_claim() -> None:
    evidence = _execution_evidence()
    lease = {
        "schema_version": 1,
        "job_id": UUID(evidence.job_id).hex,
        "claim_epoch": evidence.lease_epoch,
        "claim_token_sha256": hashlib.sha256(
            UUID(evidence.lease_token).hex.encode("ascii")
        ).hexdigest(),
        "active": True,
        "expires_epoch": 1_800_000_900,
    }
    assert parse_container_worker_lease_result(
        _canonical(lease),
        evidence,
        active=True,
    ) == lease
    status = parse_container_status(
        _canonical(
            {
                "native_intent": None,
                "schema_version": 1,
                "state": None,
                "transaction": None,
                "worker_lease": lease,
            }
        )
    )
    assert status["worker_lease"] == lease

    with pytest.raises(ContainerDeploymentError, match="differs"):
        parse_container_worker_lease_result(
            _canonical({**lease, "claim_epoch": evidence.lease_epoch + 1}),
            evidence,
            active=True,
        )
    with pytest.raises(ContainerDeploymentError, match="fields"):
        parse_container_status(
            _canonical(
                {
                    "native_intent": None,
                    "schema_version": 1,
                    "state": None,
                    "transaction": None,
                    "worker_lease": {**lease, "unknown": True},
                }
            )
        )


def test_native_maintenance_result_and_status_null_are_exactly_bound() -> None:
    evidence = _execution_evidence("rollback_native")
    result = parse_container_action_result("null", evidence)
    assert result == {
        "runtime_generation": 0,
        "activated_release_digest": None,
        "runtime_previous_release_digest": None,
        "controller_state_sha256": NATIVE_STATE_SHA256,
        "runtime_target_kind": "native",
        "runtime_native_baseline_identity": NATIVE_BASELINE_IDENTITY,
    }
    status_value = parse_container_status(
        _canonical(
            {
                "native_intent": None,
                "schema_version": 1,
                "state": None,
                "transaction": None,
                "worker_lease": None,
            }
        )
    )
    assert reconcile_container_status(status_value, evidence) == "success"

    with pytest.raises(ContainerDeploymentError, match="native rollback result"):
        parse_container_action_result(_canonical({"schema_version": 1}), evidence)


def test_operator_registration_cli_uses_fixed_root_and_dedicated_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release()
    receipt = _receipt(release)
    executed: list[tuple[str, dict | None]] = []

    class Result:
        def mappings(self):
            return self

        def one(self):
            return {
                name: True for name in evidence_cli.DATABASE_BOUNDARY_FIELDS
            }

    class ScalarResult:
        def scalars(self):
            return self

        def all(self):
            return ["catalog_shadow", "postgres"]

    class Connection:
        def execute(self, statement: object, parameters: dict | None = None):
            executed.append((str(statement), parameters))
            if "FROM pg_catalog.pg_database" in str(statement):
                return ScalarResult()
            return Result()

    class Begin:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return None

    class Engine:
        disposed = False

        def begin(self):
            return Begin()

        def dispose(self):
            self.disposed = True

    engine = Engine()
    engine_arguments: dict[str, object] = {}
    rejection_probe: list[tuple[dict[str, object], tuple[str, ...]]] = []
    monkeypatch.setattr(evidence_cli.socket, "gethostname", lambda: "an2p")
    monkeypatch.setattr(evidence_cli, "load_fixed_release_evidence", lambda _tree: (release, receipt))
    monkeypatch.setattr(
        evidence_cli,
        "queue_database_config",
        lambda: {
            "host": "127.0.0.1",
            "port": 15432,
            "database": "mooncen",
            "user": "mooncen_deployment_worker_login",
            "password": "not-logged",
            "sslmode": "require",
            "options": (
                "-c search_path=pg_catalog,public "
                "-c statement_timeout=15000 -c lock_timeout=3000"
            ),
        },
    )
    def create_engine(*_args: object, **kwargs: object) -> Engine:
        engine_arguments.update(kwargs)
        return engine

    monkeypatch.setattr(evidence_cli, "create_engine", create_engine)
    monkeypatch.setattr(
        evidence_cli,
        "_verify_other_database_rejections",
        lambda database, names: rejection_probe.append((dict(database), tuple(names))),
    )
    monkeypatch.setattr(
        evidence_cli,
        "register_container_release_evidence",
        lambda *_args, **_kwargs: {"id": "release-id"},
    )
    monkeypatch.setattr(
        evidence_cli,
        "register_container_validation_evidence",
        lambda *_args, **_kwargs: {"id": "receipt-id"},
    )

    result = evidence_cli.register(TREE)

    assert result["release_id"] == "release-id"
    assert result["receipt_id"] == "receipt-id"
    assert "mooncen_deployment_worker" in executed[0][0]
    assert "mooncen_crawler" in executed[0][0]
    assert "pg_stat_ssl" in executed[0][0]
    assert "ops_container_deployment_lease_epoch_seq" in executed[0][0]
    assert executed[0][1] == {
        "expected_database": "mooncen",
        "expected_user": "mooncen_deployment_worker_login",
    }
    assert executed[1][1] == {"expected_database": "mooncen"}
    assert rejection_probe[0][0]["database"] == "mooncen"
    assert rejection_probe[0][0]["channel_binding"] == "require"
    assert rejection_probe[0][1] == ("catalog_shadow", "postgres")
    assert engine_arguments["connect_args"] == {
        "channel_binding": "require",
        "options": (
            "-c search_path=pg_catalog,public "
            "-c statement_timeout=15000 -c lock_timeout=3000"
        ),
        "sslmode": "require",
    }
    assert "parameters" in evidence_cli.DATABASE_BOUNDARY_SQL
    assert "'payload'" not in evidence_cli.DATABASE_BOUNDARY_SQL
    assert "procedure.prokind IN ('f', 'p', 'a', 'w')" in evidence_cli.DATABASE_BOUNDARY_SQL
    assert (
        "has_any_column_privilege(current_user, 'public.ops_agents', 'REFERENCES')"
        in evidence_cli.DATABASE_BOUNDARY_SQL
    )
    assert (
        "has_any_column_privilege(current_user, 'public.ops_job_logs', 'REFERENCES')"
        in evidence_cli.DATABASE_BOUNDARY_SQL
    )
    assert "pg_catalog.pg_largeobject_metadata" in evidence_cli.DATABASE_BOUNDARY_SQL
    assert "has_largeobject_privilege" not in evidence_cli.DATABASE_BOUNDARY_SQL
    assert "large_objects_absent" in evidence_cli.DATABASE_BOUNDARY_FIELDS
    assert "large_object_entry_points_denied" in evidence_cli.DATABASE_BOUNDARY_FIELDS
    assert "pg_catalog_routine_privileges_exact" in evidence_cli.DATABASE_BOUNDARY_FIELDS
    for field in (
        "role_settings_safe",
        "system_schema_inventory_exact",
        "system_schema_privileges_exact",
        "extension_inventory_exact",
        "system_relation_privileges_exact",
        "user_defined_system_objects_absent",
        "parameter_privileges_absent",
        "foreign_data_access_denied",
    ):
        assert field in evidence_cli.DATABASE_BOUNDARY_FIELDS
    assert "pg_catalog.aclexplode" in evidence_cli.DATABASE_BOUNDARY_SQL
    assert "pg_catalog.pg_init_privs" in evidence_cli.DATABASE_BOUNDARY_SQL
    assert "pg_catalog.pg_parameter_acl" in evidence_cli.DATABASE_BOUNDARY_SQL
    assert "pg_catalog.pg_db_role_setting" in evidence_cli.DATABASE_BOUNDARY_SQL
    assert "current_setting('session_replication_role') = 'origin'" in (
        evidence_cli.DATABASE_BOUNDARY_SQL
    )
    assert "pg_catalog.pg_foreign_data_wrapper" in evidence_cli.DATABASE_BOUNDARY_SQL
    assert "pg_catalog.pg_foreign_server" in evidence_cli.DATABASE_BOUNDARY_SQL
    assert "pg_catalog.pg_user_mapping" in evidence_cli.DATABASE_BOUNDARY_SQL
    for signature in (
        "pg_catalog.lo_creat(integer)",
        "pg_catalog.lo_create(oid)",
        "pg_catalog.lo_from_bytea(oid,bytea)",
        "pg_catalog.lo_import(text)",
        "pg_catalog.lo_import(text,oid)",
        "pg_catalog.lo_export(oid,text)",
    ):
        assert signature in evidence_cli.DATABASE_BOUNDARY_SQL
    assert engine.disposed is True
    with pytest.raises(SystemExit):
        evidence_cli._parse_args(["--release-directory", "/tmp/operator-path"])


def test_operator_registration_rejects_each_live_database_boundary_drift() -> None:
    class Result:
        def __init__(self, values: dict[str, bool]):
            self.values = values

        def mappings(self):
            return self

        def one(self):
            return self.values

    class Connection:
        def __init__(self, values: dict[str, bool]):
            self.values = values
            self.parameters = None

        def execute(self, statement: object, parameters: dict | None = None):
            assert "current_database() = :expected_database" in str(statement)
            self.parameters = parameters
            return Result(self.values)

    valid = {name: True for name in evidence_cli.DATABASE_BOUNDARY_FIELDS}
    connection = Connection(valid)
    evidence_cli._verify_database_boundary(
        connection,
        expected_user="mooncen_deployment_worker_login",
        expected_database="catalog_canary",
    )
    assert connection.parameters == {
        "expected_database": "catalog_canary",
        "expected_user": "mooncen_deployment_worker_login",
    }

    for field in evidence_cli.DATABASE_BOUNDARY_FIELDS:
        drifted = {**valid, field: False}
        with pytest.raises(evidence_cli.RegistrationError, match="exact isolated"):
            evidence_cli._verify_database_boundary(
                Connection(drifted),
                expected_user="mooncen_deployment_worker_login",
                expected_database="catalog_canary",
            )

    with pytest.raises(evidence_cli.RegistrationError, match="identity"):
        evidence_cli._verify_database_boundary(
            Connection(valid),
            expected_user="custom_worker_login",
            expected_database="catalog_canary",
        )


def test_operator_registration_enumerates_only_bounded_connectable_other_databases() -> None:
    class Scalars:
        def scalars(self):
            return self

        def all(self):
            return ["catalog_shadow", "postgres"]

    class Connection:
        parameters = None

        def execute(self, statement: object, parameters: dict | None = None):
            assert "WHERE datallowconn" in str(statement)
            assert "datistemplate" not in str(statement)
            self.parameters = parameters
            return Scalars()

    connection = Connection()
    assert evidence_cli._other_database_names(connection, "mooncen") == (
        "catalog_shadow",
        "postgres",
    )
    assert connection.parameters == {"expected_database": "mooncen"}

    Scalars.all = lambda _self: ["x" * 64]
    with pytest.raises(evidence_cli.RegistrationError, match="inventory is invalid"):
        evidence_cli._other_database_names(connection, "mooncen")


def test_operator_registration_requires_authoritative_other_database_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = {
        "host": "127.0.0.1",
        "port": 15432,
        "database": "mooncen",
        "user": "mooncen_deployment_worker_login",
        "password": "not-logged",
        "sslmode": "require",
        "channel_binding": "require",
    }
    observed: list[dict[str, object]] = []

    def rejected(**settings: object):
        observed.append(settings)
        raise evidence_cli.psycopg2.OperationalError(
            "FATAL: pg_hba.conf rejects connection for host"
        )

    monkeypatch.setattr(evidence_cli.psycopg2, "connect", rejected)
    evidence_cli._verify_other_database_rejections(
        database,
        ("catalog_shadow", "postgres"),
    )
    assert [settings["database"] for settings in observed] == [
        "catalog_shadow",
        "postgres",
    ]
    assert all(settings["channel_binding"] == "require" for settings in observed)

    monkeypatch.setattr(
        evidence_cli.psycopg2,
        "connect",
        lambda **_settings: (_ for _ in ()).throw(
            evidence_cli.psycopg2.OperationalError("connection timed out")
        ),
    )
    with pytest.raises(
        evidence_cli.RegistrationError,
        match="was not authoritative",
    ):
        evidence_cli._verify_other_database_rejections(database, ("postgres",))

    class EscapedConnection:
        closed = False

        def close(self) -> None:
            self.closed = True

    escaped = EscapedConnection()
    monkeypatch.setattr(
        evidence_cli.psycopg2,
        "connect",
        lambda **_settings: escaped,
    )
    with pytest.raises(
        evidence_cli.RegistrationError,
        match="can connect to another database",
    ):
        evidence_cli._verify_other_database_rejections(database, ("postgres",))
    assert escaped.closed is True


def test_native_setup_database_boundary_cli_reuses_exact_registrar_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: list[dict[str, object]] = []
    monkeypatch.setattr(evidence_cli, "_read_boundary_password", lambda: "p" * 32)
    monkeypatch.setattr(
        evidence_cli,
        "verify_database_authorization",
        lambda database: observed.append(dict(database)),
    )
    assert (
        evidence_cli.main(
            [
                "--verify-database-boundary",
                "--database",
                "catalog_canary",
                "--user",
                "mooncen_deployment_worker_login",
            ]
        )
        == 0
    )
    assert observed == [
        {
            "application_name": "mooncen-native-worker-boundary-check",
            "channel_binding": "require",
            "connect_timeout": 5,
            "database": "catalog_canary",
            "host": "127.0.0.1",
            "options": (
                "-c search_path=pg_catalog,public "
                "-c statement_timeout=15000 -c lock_timeout=3000"
            ),
            "password": "p" * 32,
            "port": 5432,
            "sslmode": "require",
            "user": "mooncen_deployment_worker_login",
        }
    ]
    assert capsys.readouterr().out == '{"schema_version":1,"status":"authorized"}\n'

    with pytest.raises(evidence_cli.RegistrationError, match="identity is invalid"):
        evidence_cli.main(
            [
                "--verify-database-boundary",
                "--database",
                "catalog_canary",
                "--user",
                "custom_worker_login",
            ]
        )


def test_operator_registration_cli_loads_only_fixed_private_worker_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "mooncen-an2p"
    config.mkdir(mode=0o700)
    environment = config / "deployment-worker.env"
    values = {
        "ENVIRONMENT": "production",
        "DB_OWNER_USER": "mooncen_admin",
        "DB_SSLMODE": "require",
        "DB_CONNECT_TIMEOUT": "5",
        "DB_STATEMENT_TIMEOUT_MS": "15000",
        "DB_LOCK_TIMEOUT_MS": "3000",
        "OPS_DEPLOY_QUEUE_DB_HOST": "127.0.0.1",
        "OPS_DEPLOY_QUEUE_DB_PORT": "15432",
        "OPS_DEPLOY_QUEUE_DB_NAME": "mooncen",
        "OPS_DEPLOY_QUEUE_DB_USER": "mooncen_deployment_worker_login",
        "OPS_DEPLOY_QUEUE_DB_PASSWORD": "worker-password-canary",
        "OPS_DEPLOY_AGENT_EXCLUSIVE": "true",
        "OPS_DEPLOY_REQUIRED_AGENT_HOSTNAME": "an2p",
        "OPS_CONTAINER_DEV_TARGET_IDENTITY": IDENTITY,
        "OPS_CONTAINER_RELEASE_ROOT": str(tmp_path / "releases"),
    }
    environment.write_text(
        "# generated\n" + "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    environment.chmod(0o600)
    for name in values:
        monkeypatch.delenv(name, raising=False)

    evidence_cli.load_fixed_worker_environment(environment)

    assert os.environ["OPS_DEPLOY_QUEUE_DB_USER"] == "mooncen_deployment_worker_login"
    assert os.environ["OPS_CONTAINER_DEV_TARGET_IDENTITY"] == IDENTITY
    environment.write_text(
        "# generated\n"
        + "".join(
            f"{name}={('custom_worker_login' if name == 'OPS_DEPLOY_QUEUE_DB_USER' else value)}\n"
            for name, value in values.items()
        ),
        encoding="utf-8",
    )
    with pytest.raises(evidence_cli.RegistrationError, match="boundary"):
        evidence_cli.load_fixed_worker_environment(environment)
    environment.write_text(
        "# generated\n"
        + "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    environment.chmod(0o640)
    with pytest.raises(evidence_cli.RegistrationError, match="unsafe"):
        evidence_cli.load_fixed_worker_environment(environment)


def test_root_registration_entrypoint_builds_only_the_fixed_worker_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "control-runtime"
    python = runtime / ".venv/bin/python"
    monkeypatch.setattr(
        registration_entrypoint.pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(
            pw_gid=987,
            pw_dir="/var/lib/mooncen-deployment-worker",
        ),
    )
    monkeypatch.setattr(
        registration_entrypoint,
        "_root_owned_executable",
        lambda path: path,
    )

    command = registration_entrypoint._registration_command(TREE, runtime, python)

    assert command == (
        "/usr/sbin/runuser",
        "--user",
        "mooncen_deployment_worker",
        "--",
        "/usr/bin/env",
        "-i",
        "HOME=/var/lib/mooncen-deployment-worker",
        "PATH=/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE=1",
        str(python),
        "-m",
        "tools.register_container_deployment_evidence",
        "--source-tree",
        TREE,
    )
    with pytest.raises(registration_entrypoint.EvidenceRegistrationError, match="source tree"):
        registration_entrypoint._registration_command("/tmp/operator-path", runtime, python)


def test_root_registration_entrypoint_requires_exact_canonical_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = _registration_result()
    canonical = (
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    runtime = tmp_path / "runtime"
    python = runtime / ".venv/bin/python"
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        registration_entrypoint,
        "_immutable_control_runtime",
        lambda _source_tree, **_kwargs: (runtime, python),
    )
    monkeypatch.setattr(
        registration_entrypoint,
        "_registration_command",
        lambda source_tree, _runtime, _python: ("fixed-register", source_tree),
    )
    monkeypatch.setattr(
        registration_entrypoint,
        "_validate_runtime_pair",
        lambda _pair, _source_tree: None,
    )

    def run(command: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=canonical, stderr=b"")

    monkeypatch.setattr(registration_entrypoint.subprocess, "run", run)

    assert registration_entrypoint.register(TREE, releases=tmp_path / "releases") == result
    assert observed["command"] == ("fixed-register", TREE)
    assert observed["cwd"] == runtime
    assert observed["shell"] is False
    assert observed["stdin"] is registration_entrypoint.subprocess.DEVNULL

    pretty = (json.dumps(result, sort_keys=True) + "\n").encode("ascii")
    with pytest.raises(registration_entrypoint.EvidenceRegistrationError, match="canonical"):
        registration_entrypoint.parse_registration_result(pretty, TREE)
    mismatch = dict(result, source_tree="3" * 40)
    mismatch_raw = (
        json.dumps(mismatch, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    with pytest.raises(registration_entrypoint.EvidenceRegistrationError, match="tuple"):
        registration_entrypoint.parse_registration_result(mismatch_raw, TREE)


def test_root_registration_entrypoint_fails_closed_on_child_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    python = runtime / ".venv/bin/python"
    monkeypatch.setattr(
        registration_entrypoint,
        "_immutable_control_runtime",
        lambda _source_tree, **_kwargs: (runtime, python),
    )
    monkeypatch.setattr(
        registration_entrypoint,
        "_registration_command",
        lambda *_args: ("fixed-register", TREE),
    )
    monkeypatch.setattr(
        registration_entrypoint,
        "_validate_runtime_pair",
        lambda _pair, _source_tree: None,
    )
    monkeypatch.setattr(
        registration_entrypoint.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                json.dumps(_registration_result(), sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("ascii"),
            stderr=b"unexpected diagnostic\n",
        ),
    )

    with pytest.raises(registration_entrypoint.EvidenceRegistrationError, match="exit 0"):
        registration_entrypoint.register(TREE, releases=tmp_path / "releases")


def test_container_promotion_approval_and_queue_are_committed_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict | None]] = []

    class DB:
        commits = 0

        def execute(self, statement: object, params: dict | None = None) -> _Result:
            sql = str(statement)
            calls.append((sql, params))
            if "INSERT INTO ops_container_approval_evidence" in sql:
                return _Result(
                    {
                        "id": "33333333-3333-4333-8333-333333333333",
                        "expires_at": "2026-08-19T12:10:00Z",
                    }
                )
            if "INSERT INTO ops_deployments" in sql:
                return _Result(
                    {
                        "id": "44444444-4444-4444-8444-444444444444",
                        "job_id": "11111111-1111-4111-8111-111111111111",
                        "deployment_status": "queued",
                    }
                )
            return _Result()

        def commit(self) -> None:
            self.commits += 1

    db = DB()
    release = {
        "id": "55555555-5555-4555-8555-555555555555",
        "release_digest": RELEASE_DIGEST,
        "source_tree": TREE,
        "snapshot_commit": "3" * 40,
        "api_image_digest": "sha256:" + "a" * 64,
        "frontend_image_digest": "sha256:" + "b" * 64,
        "bundle_sha256": "4" * 64,
    }
    receipt = {
        "id": "66666666-6666-4666-8666-666666666666",
        "receipt_digest": RECEIPT_DIGEST,
    }
    queued: list[dict[str, object]] = []
    monkeypatch.setattr(
        ops_v2,
        "_container_deployment_agent",
        lambda _db: {"id": "77777777-7777-4777-8777-777777777777", "hostname": "an2p"},
    )
    monkeypatch.setattr(ops_v2, "current_environment", lambda: "production")
    monkeypatch.setattr(
        ops_v2,
        "enqueue_job",
        lambda _db, **kwargs: (
            queued.append(kwargs)
            or {
                "id": "11111111-1111-4111-8111-111111111111",
                "status": "queued",
            }
        ),
    )
    monkeypatch.setattr(ops_v2, "append_audit", lambda *_args, **_kwargs: None)

    result = ops_v2._enqueue_container_transition(
        db,  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(id="88888888-8888-4888-8888-888888888888"),  # type: ignore[arg-type]
        action="promote",
        target_name="cloud",
        target_environment="production",
        target_identity=TARGET_IDENTITY,
        release=release,
        current_state=None,
        confirmation=(f"PROMOTE {TARGET_IDENTITY} {RELEASE_DIGEST} {RECEIPT_DIGEST} 0 {NATIVE_STATE_SHA256}"),
        reason="reviewed PASS receipt",
        receipt=receipt,
        runtime_cas={
            "expected_runtime_generation": 0,
            "expected_controller_state_sha256": NATIVE_STATE_SHA256,
            "expected_active_release_digest": None,
            "expected_previous_release_digest": None,
        },
    )

    assert db.commits == 1
    assert "INTERVAL '10 minutes'" in calls[0][0]
    assert queued[0]["target_key"] == "deployment:cloud"
    assert queued[0]["max_retries"] == 0
    parameters = queued[0]["parameters"]
    assert isinstance(parameters, dict)
    assert frozenset(parameters) == {
        "action",
        "approval_evidence_id",
        "current_release_digest",
        "deployment_mode",
        "expected_controller_state_sha256",
        "expected_previous_release_digest",
        "expected_runtime_generation",
        "native_baseline_identity",
        "release_digest",
        "required_agent_hostname",
        "service_type",
        "source_tree",
        "target",
        "target_environment",
        "target_identity",
        "target_runtime_kind",
        "validation_receipt_digest",
    }
    assert parameters["required_agent_hostname"] == "an2p"
    deployment_params = next(params for sql, params in calls if "INSERT INTO ops_deployments" in sql)
    assert deployment_params["approval_evidence_id"] == parameters["approval_evidence_id"]
    assert deployment_params["target_environment"] == "production"
    assert result["deployment"]["deployment_status"] == "queued"


def test_container_rollback_uses_current_state_aliases_and_exact_previous_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = ContainerRollbackRequest(
        target="cloud",
        target_identity=TARGET_IDENTITY,
        current_release_digest=RELEASE_DIGEST,
        rollback_release_digest=PREVIOUS_DIGEST,
        expected_runtime_generation=1,
        expected_controller_state_sha256="c" * 64,
        reason="restore reviewed previous release",
        confirmation=(f"ROLLBACK {TARGET_IDENTITY} {RELEASE_DIGEST} {PREVIOUS_DIGEST} 1 {'c' * 64}"),
    )
    current = {
        "current_release_id": "11111111-1111-4111-8111-111111111111",
        "current_release_digest": RELEASE_DIGEST,
        "previous_release_id": "22222222-2222-4222-8222-222222222222",
        "previous_release_digest": PREVIOUS_DIGEST,
    }
    rollback_release = {
        "id": current["previous_release_id"],
        "release_digest": PREVIOUS_DIGEST,
    }
    monkeypatch.setattr(ops_v2, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(
        ops_v2,
        "_require_exact_reviewed_container_target",
        lambda *_args: {"name": "cloud"},
    )
    monkeypatch.setattr(ops_v2, "_container_current_state_for_target", lambda *_args, **_kwargs: current)
    monkeypatch.setattr(
        ops_v2,
        "_live_container_runtime_cas",
        lambda *_args, **_kwargs: {
            "expected_runtime_generation": 1,
            "expected_controller_state_sha256": "c" * 64,
            "expected_active_release_digest": RELEASE_DIGEST,
            "expected_previous_release_digest": PREVIOUS_DIGEST,
        },
    )
    monkeypatch.setattr(ops_v2, "_container_release_for_transition", lambda *_args: rollback_release)
    monkeypatch.setattr(
        ops_v2,
        "_enqueue_container_transition",
        lambda *_args, **kwargs: {"action": kwargs["action"], "release": kwargs["release"]},
    )

    result = ops_v2.request_container_rollback(
        payload,
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(id="33333333-3333-4333-8333-333333333333"),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
    )

    assert result == {"action": "rollback", "release": rollback_release}


def test_container_rollback_without_previous_requires_native_maintenance_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = ContainerRollbackRequest(
        target="cloud",
        target_identity=TARGET_IDENTITY,
        current_release_digest=RELEASE_DIGEST,
        rollback_release_digest=PREVIOUS_DIGEST,
        expected_runtime_generation=1,
        expected_controller_state_sha256="c" * 64,
        reason="restore native runtime",
        confirmation=(f"ROLLBACK {TARGET_IDENTITY} {RELEASE_DIGEST} {PREVIOUS_DIGEST} 1 {'c' * 64}"),
    )
    monkeypatch.setattr(ops_v2, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(
        ops_v2,
        "_require_exact_reviewed_container_target",
        lambda *_args: {"name": "cloud"},
    )
    monkeypatch.setattr(
        ops_v2,
        "_container_current_state_for_target",
        lambda *_args, **_kwargs: {
            "current_release_digest": RELEASE_DIGEST,
            "previous_release_id": None,
            "previous_release_digest": None,
        },
    )
    monkeypatch.setattr(
        ops_v2,
        "_live_container_runtime_cas",
        lambda *_args, **_kwargs: {
            "expected_runtime_generation": 1,
            "expected_controller_state_sha256": "c" * 64,
            "expected_active_release_digest": RELEASE_DIGEST,
            "expected_previous_release_digest": None,
        },
    )

    with pytest.raises(HTTPException) as error:
        ops_v2.request_container_rollback(
            payload,
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(id="33333333-3333-4333-8333-333333333333"),  # type: ignore[arg-type]
            SimpleNamespace(),  # type: ignore[arg-type]
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "container_rollback_previous_release_unavailable"


def test_native_maintenance_endpoint_binds_live_baseline_and_null_target_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = ContainerNativeRollbackRequest(
        target="cloud",
        target_identity=TARGET_IDENTITY,
        current_release_digest=RELEASE_DIGEST,
        native_baseline_identity=NATIVE_BASELINE_IDENTITY,
        expected_runtime_generation=3,
        expected_controller_state_sha256="c" * 64,
        reason="enter reviewed native maintenance",
        confirmation=(
            f"ROLLBACK_NATIVE {TARGET_IDENTITY} {RELEASE_DIGEST} "
            f"{NATIVE_BASELINE_IDENTITY} 3 {'c' * 64}"
        ),
    )
    current = {
        "runtime_target_kind": "container",
        "current_release_id": "11111111-1111-4111-8111-111111111111",
        "current_release_digest": RELEASE_DIGEST,
        "previous_release_digest": PREVIOUS_DIGEST,
    }
    monkeypatch.setattr(ops_v2, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(
        ops_v2,
        "_require_exact_reviewed_container_target",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        ops_v2,
        "_container_current_state_for_target",
        lambda *_args, **_kwargs: current,
    )
    monkeypatch.setattr(
        ops_v2,
        "_live_container_runtime_cas",
        lambda *_args, **_kwargs: {
            "expected_runtime_generation": 3,
            "expected_controller_state_sha256": "c" * 64,
            "expected_active_release_digest": RELEASE_DIGEST,
            "expected_previous_release_digest": PREVIOUS_DIGEST,
            "native_baseline_identity": NATIVE_BASELINE_IDENTITY,
        },
    )
    captured: dict[str, object] = {}

    def enqueue(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"action": kwargs["action"]}

    monkeypatch.setattr(ops_v2, "_enqueue_container_transition", enqueue)
    result = ops_v2.request_container_native_rollback(
        payload,
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(id="33333333-3333-4333-8333-333333333333"),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
    )

    assert result == {"action": "rollback_native"}
    assert captured["release"] is None
    assert captured["target_runtime_kind"] == "native"
    assert captured["native_baseline_identity"] == NATIVE_BASELINE_IDENTITY
