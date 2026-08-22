from __future__ import annotations

import hashlib
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from utils.url_security import safe_external_http_url


ContentType = Literal["culture_center", "experience", "education", "all"]
Environment = Literal["production", "staging", "development"]
CrawlerScope = Literal["all", "data_type", "provider", "region", "branch", "url", "failed"]
RunMode = Literal["apply", "dry_run", "review"]
COMMIT_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
OCI_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
RELEASE_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
DEPLOY_TARGET_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"


def _clean_optional(value: str | None, maximum: int) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum or "\x00" in cleaned:
        raise ValueError(f"value must be at most {maximum} characters")
    return cleaned


def crawler_release_worker_set_digest(worker_keys: list[str]) -> str:
    canonical = "\n".join(sorted(worker_keys)).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()[:12]


class CrawlerRunRequest(BaseModel):
    scope: CrawlerScope = "provider"
    content_type: ContentType = "all"
    provider: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    branch: str | None = Field(default=None, max_length=160)
    url: str | None = Field(default=None, max_length=4096)
    run_mode: RunMode = "apply"
    compare_existing: bool = True
    review_before_apply: bool = False
    save_screenshot: bool = True
    save_html: bool = False
    browser_visible: bool = False
    max_retries: int = Field(default=1, ge=0, le=5)
    concurrency: int = Field(default=1, ge=1, le=5)
    force_full_refresh: bool = False

    @field_validator("provider", "region", "branch")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return _clean_optional(value, 160)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        safe_url = safe_external_http_url(value)
        if not safe_url:
            raise ValueError("url must be a safe HTTP(S) URL without credentials or secret query parameters")
        return safe_url

    @model_validator(mode="after")
    def validate_scope_target(self) -> "CrawlerRunRequest":
        required = {
            "provider": self.provider,
            "region": self.region,
            "branch": self.branch,
            "url": self.url,
        }
        if self.scope in required and not required[self.scope]:
            raise ValueError(f"{self.scope} is required for scope={self.scope}")
        if self.scope == "branch" and not self.provider:
            raise ValueError("provider is required for scope=branch")
        if self.scope == "data_type" and self.content_type == "all":
            raise ValueError("a specific content_type is required for scope=data_type")
        if self.review_before_apply and self.run_mode == "apply":
            self.run_mode = "review"
        return self


class QualityScanRequest(BaseModel):
    content_type: ContentType = "all"
    provider: str | None = Field(default=None, max_length=100)
    branch: str | None = Field(default=None, max_length=160)
    max_retries: int = Field(default=0, ge=0, le=3)

    @field_validator("provider", "branch")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return _clean_optional(value, 160)


class ParserProbeRequest(BaseModel):
    url: str = Field(max_length=4096)
    timeout: int = Field(default=25, ge=5, le=60)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        safe_url = safe_external_http_url(value)
        if not safe_url:
            raise ValueError("url must be a safe HTTP(S) URL without credentials or secret query parameters")
        return safe_url


class DeploymentRequest(BaseModel):
    target: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
    target_commit: str = Field(pattern=COMMIT_PATTERN)
    source_tree: str = Field(pattern=COMMIT_PATTERN)
    skip_workers: bool = False
    confirmation: str = Field(min_length=1, max_length=100)

    @field_validator("target", "target_commit", "source_tree")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_confirmation(self) -> "DeploymentRequest":
        expected = f"DEPLOY {self.target} {self.source_tree[:12]}"
        if self.confirmation != expected:
            raise ValueError("deployment confirmation does not match the reviewed target and commit")
        return self


class _ContainerActionRequest(BaseModel):
    """Shared, fail-closed identity fields for future Docker actions."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=32, pattern=DEPLOY_TARGET_PATTERN)
    target_identity: str = Field(pattern=SHA256_PATTERN)
    confirmation: str = Field(min_length=1, max_length=320)

    @field_validator("target", "target_identity")
    @classmethod
    def normalize_container_identifier(cls, value: str) -> str:
        return value.strip().lower()


class ContainerBuildRequest(_ContainerActionRequest):
    source_commit: str = Field(pattern=COMMIT_PATTERN)
    source_tree: str = Field(pattern=COMMIT_PATTERN)
    platform: Literal["linux/amd64", "linux/arm64"] = "linux/amd64"

    @field_validator("source_commit", "source_tree")
    @classmethod
    def normalize_source_identifier(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_build_confirmation(self) -> "ContainerBuildRequest":
        expected = f"BUILD {self.target_identity} {self.source_tree}"
        if self.confirmation != expected:
            raise ValueError("build confirmation does not match the target identity and source tree")
        return self


class ContainerValidationRequest(_ContainerActionRequest):
    target: Literal["an2p-dev"] = "an2p-dev"
    release_digest: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_validation_confirmation(self) -> "ContainerValidationRequest":
        expected = f"VALIDATE {self.target_identity} {self.release_digest}"
        if self.confirmation != expected:
            raise ValueError("validation confirmation does not match the target identity and release")
        return self


class ContainerPromotionRequest(_ContainerActionRequest):
    target_environment: Literal["staging", "production"] = "production"
    release_digest: str = Field(pattern=SHA256_PATTERN)
    validation_receipt_digest: str = Field(pattern=SHA256_PATTERN)
    expected_runtime_generation: int = Field(ge=0, le=1_000_000_000)
    expected_controller_state_sha256: str = Field(pattern=SHA256_PATTERN)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_promotion_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("reason must contain at least 3 characters")
        return cleaned

    @model_validator(mode="after")
    def validate_promotion_confirmation(self) -> "ContainerPromotionRequest":
        expected = (
            f"PROMOTE {self.target_identity} {self.release_digest} "
            f"{self.validation_receipt_digest} {self.expected_runtime_generation} "
            f"{self.expected_controller_state_sha256}"
        )
        if self.confirmation != expected:
            raise ValueError("promotion confirmation does not match the target, release, and receipt")
        return self


class ContainerRollbackRequest(_ContainerActionRequest):
    target_environment: Literal["staging", "production"] = "production"
    current_release_digest: str = Field(pattern=SHA256_PATTERN)
    rollback_release_digest: str = Field(pattern=SHA256_PATTERN)
    expected_runtime_generation: int = Field(ge=1, le=1_000_000_000)
    expected_controller_state_sha256: str = Field(pattern=SHA256_PATTERN)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_rollback_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("reason must contain at least 3 characters")
        return cleaned

    @model_validator(mode="after")
    def validate_rollback_identity(self) -> "ContainerRollbackRequest":
        if self.current_release_digest == self.rollback_release_digest:
            raise ValueError("rollback release must differ from the current release")
        expected = (
            f"ROLLBACK {self.target_identity} {self.current_release_digest} "
            f"{self.rollback_release_digest} {self.expected_runtime_generation} "
            f"{self.expected_controller_state_sha256}"
        )
        if self.confirmation != expected:
            raise ValueError("rollback confirmation does not match the target and release transition")
        return self


class ContainerNativeRollbackRequest(_ContainerActionRequest):
    target_environment: Literal["staging", "production"] = "production"
    current_release_digest: str = Field(pattern=SHA256_PATTERN)
    native_baseline_identity: str = Field(pattern=SHA256_PATTERN)
    expected_runtime_generation: int = Field(ge=1, le=1_000_000_000)
    expected_controller_state_sha256: str = Field(pattern=SHA256_PATTERN)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_native_rollback_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("reason must contain at least 3 characters")
        return cleaned

    @model_validator(mode="after")
    def validate_native_rollback_identity(self) -> "ContainerNativeRollbackRequest":
        expected = (
            f"ROLLBACK_NATIVE {self.target_identity} {self.current_release_digest} "
            f"{self.native_baseline_identity} {self.expected_runtime_generation} "
            f"{self.expected_controller_state_sha256}"
        )
        if self.confirmation != expected:
            raise ValueError("native rollback confirmation does not match the exact baseline transition")
        return self


class IssueActionRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("reason must contain at least 3 characters")
        return cleaned


class JobActionRequest(BaseModel):
    reason: str = Field(default="", max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return value.strip()


ReleaseAction = Literal[
    "build",
    "register_artifact",
    "create_canary",
    "advance_rollout",
    "pause_rollout",
    "rollback_rollout",
    "complete_rollback",
]


class CrawlerReleaseActionRequest(BaseModel):
    """A bounded operator request; it never contains an artifact or credential."""

    model_config = ConfigDict(extra="forbid")

    action: ReleaseAction
    idempotency_key: str = Field(
        min_length=16,
        max_length=128,
        pattern=RELEASE_IDEMPOTENCY_PATTERN,
    )
    environment: Environment
    expected_generation: int = Field(ge=0, le=9_223_372_036_854_775_807)
    confirmation: str = Field(min_length=1, max_length=180)
    reason: str = Field(min_length=3, max_length=500)

    source_commit: str | None = Field(default=None, pattern=COMMIT_PATTERN)
    source_tree: str | None = Field(default=None, pattern=COMMIT_PATTERN)
    test_profile: Literal["crawler", "crawler_full"] | None = None

    build_request_id: UUID | None = None
    artifact_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    baseline_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    code_version: str | None = Field(default=None, min_length=1, max_length=200)
    config_revision: str | None = Field(default=None, min_length=1, max_length=255)

    rollout_id: UUID | None = None
    rollout_phase: Literal["rolling", "complete"] | None = None
    worker_keys: list[str] = Field(default_factory=list, max_length=200)
    target_worker_keys: list[str] = Field(default_factory=list, max_length=200)

    @field_validator(
        "idempotency_key",
        "confirmation",
        "reason",
        "source_commit",
        "source_tree",
        "artifact_digest",
        "baseline_digest",
        "code_version",
        "config_revision",
    )
    @classmethod
    def clean_release_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if "\x00" in cleaned:
            raise ValueError("release request text contains a null byte")
        return cleaned

    @field_validator("worker_keys", "target_worker_keys")
    @classmethod
    def validate_worker_keys(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw_value in values:
            value = str(raw_value).strip()
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
                raise ValueError("worker key is invalid")
            cleaned.append(value)
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("worker keys must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_action_contract(self) -> "CrawlerReleaseActionRequest":
        supplied: dict[str, Any] = {
            "source_commit": self.source_commit,
            "source_tree": self.source_tree,
            "test_profile": self.test_profile,
            "build_request_id": self.build_request_id,
            "artifact_digest": self.artifact_digest,
            "baseline_digest": self.baseline_digest,
            "code_version": self.code_version,
            "config_revision": self.config_revision,
            "rollout_id": self.rollout_id,
            "rollout_phase": self.rollout_phase,
            "worker_keys": self.worker_keys or None,
            "target_worker_keys": self.target_worker_keys or None,
        }
        allowed = {
            "build": {"source_commit", "source_tree", "test_profile"},
            "register_artifact": {
                "build_request_id",
                "artifact_digest",
                "code_version",
                "config_revision",
            },
            "create_canary": {
                "artifact_digest",
                "baseline_digest",
                "rollout_id",
                "worker_keys",
            },
            "advance_rollout": {"rollout_id", "rollout_phase", "target_worker_keys"},
            "pause_rollout": {"rollout_id"},
            "rollback_rollout": {"rollout_id"},
            "complete_rollback": {"rollout_id"},
        }[self.action]
        unexpected = sorted(key for key, value in supplied.items() if value is not None and key not in allowed)
        if unexpected:
            raise ValueError(f"fields are not valid for {self.action}: {', '.join(unexpected)}")

        required = {
            "build": ("source_commit", "source_tree", "test_profile"),
            "register_artifact": (
                "build_request_id",
                "artifact_digest",
                "code_version",
                "config_revision",
            ),
            "create_canary": ("artifact_digest", "baseline_digest", "rollout_id", "worker_keys"),
            "advance_rollout": ("rollout_id", "rollout_phase"),
            "pause_rollout": ("rollout_id",),
            "rollback_rollout": ("rollout_id",),
            "complete_rollback": ("rollout_id",),
        }[self.action]
        missing = [key for key in required if supplied[key] is None]
        if missing:
            raise ValueError(f"fields are required for {self.action}: {', '.join(missing)}")
        if self.action == "build" and self.expected_generation != 0:
            raise ValueError("build expected_generation must be zero")
        if self.action == "register_artifact" and self.expected_generation != 0:
            raise ValueError("register_artifact expected_generation must be zero")
        if self.action == "create_canary" and self.expected_generation < 1:
            raise ValueError("canary generation must be positive")
        if (
            self.action
            in {
                "advance_rollout",
                "pause_rollout",
                "rollback_rollout",
                "complete_rollback",
            }
            and self.expected_generation < 1
        ):
            raise ValueError("rollout transition expected_generation must be positive")
        if self.action == "advance_rollout":
            if self.rollout_phase == "rolling" and not self.target_worker_keys:
                raise ValueError("rolling advance requires at least one exact target worker")
            if self.rollout_phase == "complete" and self.target_worker_keys:
                raise ValueError("complete advance does not accept target workers")
        if self.artifact_digest and self.baseline_digest and self.artifact_digest == self.baseline_digest:
            raise ValueError("canary target and baseline artifacts must differ")

        confirmation_subject = {
            "build": str(self.source_tree)[:12],
            "register_artifact": str(self.artifact_digest)[:12],
            "create_canary": (
                f"{self.rollout_id} {self.expected_generation} "
                f"{str(self.artifact_digest)[:12]} {str(self.baseline_digest)[:12]} "
                f"{crawler_release_worker_set_digest(self.worker_keys)}"
            ),
            "advance_rollout": (
                f"{self.rollout_id} {self.expected_generation} {self.rollout_phase} "
                f"{crawler_release_worker_set_digest(self.target_worker_keys) if self.target_worker_keys else 'none'}"
            ),
            "pause_rollout": f"{self.rollout_id} {self.expected_generation}",
            "rollback_rollout": f"{self.rollout_id} {self.expected_generation}",
            "complete_rollback": f"{self.rollout_id} {self.expected_generation}",
        }[self.action]
        verb = {
            "build": "BUILD",
            "register_artifact": "REGISTER",
            "create_canary": "CANARY",
            "advance_rollout": "ADVANCE",
            "pause_rollout": "PAUSE",
            "rollback_rollout": "ROLLBACK",
            "complete_rollback": "COMPLETE_ROLLBACK",
        }[self.action]
        if self.confirmation != f"{verb} {confirmation_subject}":
            raise ValueError("confirmation does not match the exact release action identity")
        return self

    def request_payload(self) -> dict[str, Any]:
        excluded = {
            "action",
            "idempotency_key",
            "environment",
            "expected_generation",
            "confirmation",
            "reason",
        }
        return self.model_dump(mode="json", exclude=excluded, exclude_none=True, exclude_defaults=True)
