"""Fail-closed worker reconciler for versioned crawler release artifacts.

The desired-state document cannot provide a command, hostname, or URL.  All
network origins and the one systemd unit that may be restarted are local,
administrator-owned configuration.  ``check`` and ``dry-run`` are intentionally
side-effect free; ``apply`` performs the reviewed download/verify/drain/switch/
health/rollback sequence.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - the apply service is Linux-only
    fcntl = None  # type: ignore[assignment]

from ops_agent.crawler_release_control import (
    ArtifactMetadata,
    DesiredState,
    ReconcileDecision,
    parse_desired_state,
    reconcile_decision,
)


WORKER_CODE_VERSION_ENV = "OPS_CRAWLER_CODE_VERSION"
WORKER_ARTIFACT_DIGEST_ENV = "OPS_CRAWLER_ARTIFACT_DIGEST"
WORKER_CONFIG_REVISION_ENV = "OPS_CRAWLER_CONFIG_REVISION"

WORKER_SYSTEMD_UNIT = "mooncen-crawler-pull-worker.service"
SYSTEMCTL_PATH = Path("/usr/bin/systemctl")
SSHSIG_VERIFY_PATH = Path("/usr/bin/ssh-keygen")
LOCAL_POLICY_PATH = Path("/etc/mooncen/crawler-release-agent.env")
SSHSIG_NAMESPACE = "mooncen-crawler-release-v1"

LOCAL_STATE_SCHEMA_VERSION = 1
STATUS_DOCUMENT_SCHEMA_VERSION = 1
RELEASE_MANIFEST_NAME = "crawler-release.json"
RELEASE_METADATA_NAME = ".mooncen-crawler-release.json"
RELEASE_ENV_NAME = "release.env"
MAX_STATUS_DOCUMENT_BYTES = 64 * 1024
MAX_RELEASE_MANIFEST_BYTES = 64 * 1024
MAX_REPORT_DETAIL = 320
MAX_ARCHIVE_FILES = 30_000
MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024
MAX_TAR_EXTENSION_BYTES = 64 * 1024
MAX_TAR_EXTENSION_TOTAL_BYTES = 32 * 1024 * 1024
MAX_GLOBAL_PAX_HEADERS = 8

_DNS_NAME = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ROLLOUT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONFIG_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
_REPORT_STATUSES = frozenset(
    {"pending", "downloading", "installing", "verifying", "ready", "failed", "rolled_back", "drifted"}
)
_LOCAL_ATTEMPT_STATUSES = frozenset(
    {
        "activated",
        "blocked",
        "bootstrap_required",
        "failed",
        "no_change",
        "ready",
        "recovered_previous",
        "rollback_failed",
        "rolled_back",
    }
)


class ReleaseAgentError(RuntimeError):
    """An expected fail-closed reconciliation error."""


class RollbackFailed(ReleaseAgentError):
    """The previous release link was restored but did not become healthy."""


class _BoundedTarInfo(tarfile.TarInfo):
    """Bound tar metadata that tarfile consumes before yielding a member."""

    def _check_extension(self, archive: tarfile.TarFile) -> None:
        extension_count = int(getattr(archive, "_mooncen_extension_count", 0)) + 1
        extension_limit = int(getattr(archive, "_mooncen_extension_limit", 0))
        extension_bytes = int(getattr(archive, "_mooncen_extension_bytes", 0)) + self.size
        global_count = int(getattr(archive, "_mooncen_global_pax_count", 0))
        if self.type == tarfile.XGLTYPE:
            global_count += 1
        if (
            self.size < 0
            or self.size > MAX_TAR_EXTENSION_BYTES
            or extension_limit < 1
            or extension_count > extension_limit
            or extension_bytes > MAX_TAR_EXTENSION_TOTAL_BYTES
            or global_count > MAX_GLOBAL_PAX_HEADERS
        ):
            raise ReleaseAgentError("release archive extension metadata exceeds policy")
        setattr(archive, "_mooncen_extension_count", extension_count)
        setattr(archive, "_mooncen_extension_bytes", extension_bytes)
        setattr(archive, "_mooncen_global_pax_count", global_count)

    def _proc_pax(self, archive: tarfile.TarFile):
        self._check_extension(archive)
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive: tarfile.TarFile):
        self._check_extension(archive)
        return super()._proc_gnulong(archive)

    def _proc_sparse(self, _archive: tarfile.TarFile):
        raise ReleaseAgentError("release archive sparse metadata is unsupported")

    def _proc_gnusparse_00(self, _next: tarfile.TarInfo, _raw_headers: bytes):
        raise ReleaseAgentError("release archive sparse metadata is unsupported")

    def _proc_gnusparse_01(self, _next: tarfile.TarInfo, _pax_headers: Mapping[str, str]):
        raise ReleaseAgentError("release archive sparse metadata is unsupported")

    def _proc_gnusparse_10(
        self,
        _next: tarfile.TarInfo,
        _pax_headers: Mapping[str, str],
        _archive: tarfile.TarFile,
    ):
        raise ReleaseAgentError("release archive sparse metadata is unsupported")


@dataclass(frozen=True)
class HttpsEndpoint:
    url: str
    hostname: str
    base_path: str


@dataclass(frozen=True)
class AgentConfig:
    worker_id: str
    environment: str
    desired_state: HttpsEndpoint
    artifact_base: HttpsEndpoint
    allowed_https_hosts: frozenset[str]
    release_root: Path
    state_directory: Path
    drain_state_path: Path
    health_state_path: Path
    require_signature: bool
    allowed_key_ids: frozenset[str]
    allowed_signers_path: Path | None
    tls_ca_file: Path | None
    health_timeout_seconds: int = 90
    drain_max_age_seconds: int = 120
    max_unpacked_bytes: int = MAX_UNPACKED_BYTES
    max_archive_files: int = MAX_ARCHIVE_FILES

    @property
    def releases_directory(self) -> Path:
        return self.release_root / "releases"

    @property
    def staging_directory(self) -> Path:
        return self.release_root / ".staging"

    @property
    def current_link(self) -> Path:
        return self.release_root / "current"

    @property
    def local_state_path(self) -> Path:
        return self.state_directory / "state.json"

    @property
    def reports_directory(self) -> Path:
        return self.state_directory / "reports"

    @property
    def pending_switch_path(self) -> Path:
        return self.state_directory / "pending-switch.json"

    @property
    def terminal_failure_path(self) -> Path:
        return self.state_directory / "terminal-failure.json"

    @property
    def lock_path(self) -> Path:
        return self.state_directory / "reconcile.lock"


@dataclass(frozen=True)
class LocalState:
    worker_id: str
    observed_generation: int
    applied_generation: int
    rollout_id: str
    current_code_version: str
    current_artifact_digest: str
    current_config_revision: str
    last_attempt_status: str
    updated_at: str

    @classmethod
    def empty(cls, worker_id: str) -> "LocalState":
        return cls(
            worker_id=worker_id,
            observed_generation=0,
            applied_generation=0,
            rollout_id="bootstrap",
            current_code_version="",
            current_artifact_digest="",
            current_config_revision="",
            last_attempt_status="bootstrap_required",
            updated_at=_utc_now(),
        )


@dataclass(frozen=True)
class ReconcileResult:
    status: str
    detail: str
    generation: int
    rollout_id: str
    code_version: str
    artifact_digest: str
    config_revision: str


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request: Any, _file: Any, _code: int, _message: str, _headers: Any, _url: str) -> None:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseAgentError("JSON document contains a duplicate field")
        result[key] = value
    return result


def _json_document(data: bytes, *, label: str, max_bytes: int) -> dict[str, Any]:
    if not data or len(data) > max_bytes:
        raise ReleaseAgentError(f"{label} size is invalid")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ReleaseAgentError(f"{label} contains a non-finite number")
            ),
        )
    except ReleaseAgentError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseAgentError(f"{label} is invalid JSON") from exc
    if type(value) is not dict:
        raise ReleaseAgentError(f"{label} must be an object")
    return value


def _strict_fields(
    value: Mapping[str, Any],
    *,
    label: str,
    required: frozenset[str],
) -> None:
    if set(value) != required:
        raise ReleaseAgentError(f"{label} fields are invalid")


def _absolute_path(value: str, *, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or any(part == ".." for part in path.parts):
        raise ReleaseAgentError(f"{label} must be an absolute path")
    return path


def _managed_directory(value: str, *, label: str) -> Path:
    path = _absolute_path(value, label=label)
    meaningful_parts = tuple(part for part in path.parts if part != path.anchor)
    if len(meaningful_parts) < 2:
        raise ReleaseAgentError(f"{label} is too broad for managed release writes")
    return path


def validate_https_endpoint(
    value: str,
    *,
    allowed_hosts: frozenset[str],
    require_trailing_slash: bool,
) -> HttpsEndpoint:
    """Validate a locally configured exact HTTPS origin without resolving it."""

    if not allowed_hosts:
        raise ReleaseAgentError("HTTPS host allowlist is empty")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ReleaseAgentError("HTTPS endpoint is invalid") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
        or hostname not in allowed_hosts
        or not _DNS_NAME.fullmatch(hostname)
    ):
        raise ReleaseAgentError("HTTPS endpoint is outside the local allowlist")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ReleaseAgentError("HTTPS endpoint must use a reviewed DNS name")
    path_parts = parsed.path.split("/")
    if (
        not parsed.path.startswith("/")
        or "//" in parsed.path
        or "\\" in parsed.path
        or "%" in parsed.path
        or any(part in {".", ".."} for part in path_parts)
    ):
        raise ReleaseAgentError("HTTPS endpoint path is invalid")
    if require_trailing_slash != parsed.path.endswith("/"):
        suffix = "end with /" if require_trailing_slash else "identify one document"
        raise ReleaseAgentError(f"HTTPS endpoint must {suffix}")
    normalized_url = urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, "", ""))
    return HttpsEndpoint(url=normalized_url, hostname=hostname, base_path=parsed.path)


def artifact_url(base: HttpsEndpoint, artifact: ArtifactMetadata) -> str:
    """Join one already-validated relative artifact path without urljoin semantics."""

    relative = PurePosixPath(artifact.relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ReleaseAgentError("artifact relative path is invalid")
    path = base.base_path + "/".join(relative.parts)
    parsed = urllib.parse.urlsplit(base.url)
    return urllib.parse.urlunsplit(("https", parsed.netloc, path, "", ""))


def load_agent_config(environment: Mapping[str, str] | None = None) -> AgentConfig:
    """Load only local administrator-owned policy; desired state cannot override it."""

    env = os.environ if environment is None else environment
    hosts = frozenset(
        item.strip().lower().rstrip(".")
        for item in env.get("OPS_CRAWLER_ALLOWED_HTTPS_HOSTS", "").split(",")
        if item.strip()
    )
    if any(not _DNS_NAME.fullmatch(host) for host in hosts):
        raise ReleaseAgentError("OPS_CRAWLER_ALLOWED_HTTPS_HOSTS is invalid")
    worker_id = env.get("OPS_CRAWLER_WORKER_ID", "").strip()
    environment_name = env.get("OPS_CRAWLER_ENVIRONMENT", "production").strip()
    if not _IDENTIFIER.fullmatch(worker_id) or not _IDENTIFIER.fullmatch(environment_name):
        raise ReleaseAgentError("crawler worker identity or environment is invalid")
    desired = validate_https_endpoint(
        env.get("OPS_CRAWLER_DESIRED_STATE_URL", ""),
        allowed_hosts=hosts,
        require_trailing_slash=False,
    )
    artifact_base = validate_https_endpoint(
        env.get("OPS_CRAWLER_ARTIFACT_BASE_URL", ""),
        allowed_hosts=hosts,
        require_trailing_slash=True,
    )
    allowed_keys = frozenset(
        item.strip() for item in env.get("OPS_CRAWLER_ALLOWED_KEY_IDS", "").split(",") if item.strip()
    )
    if any(not _KEY_ID.fullmatch(item) for item in allowed_keys):
        raise ReleaseAgentError("OPS_CRAWLER_ALLOWED_KEY_IDS is invalid")
    require_signature = _environment_boolean(env, "OPS_CRAWLER_REQUIRE_SIGNATURE", True)
    signers_raw = env.get("OPS_CRAWLER_ALLOWED_SIGNERS", "").strip()
    allowed_signers = _absolute_path(signers_raw, label="allowed signers") if signers_raw else None
    if require_signature and (not allowed_keys or allowed_signers is None):
        raise ReleaseAgentError("signature policy needs allowed key ids and an allowed-signers file")
    ca_raw = env.get("OPS_CRAWLER_TLS_CA_FILE", "").strip()
    release_root = _managed_directory(
        env.get("OPS_CRAWLER_RELEASE_ROOT", "/opt/mooncen-crawler"),
        label="release root",
    )
    state_directory = _managed_directory(
        env.get("OPS_CRAWLER_RELEASE_STATE_DIR", "/var/lib/mooncen-crawler-release-agent"),
        label="state directory",
    )
    if (
        release_root == state_directory
        or release_root.is_relative_to(state_directory)
        or state_directory.is_relative_to(release_root)
    ):
        raise ReleaseAgentError("release root and state directory must be disjoint")
    drain_state_path = _absolute_path(
        env.get("OPS_CRAWLER_DRAIN_STATE", "/run/mooncen-crawler/drain.json"),
        label="drain state",
    )
    health_state_path = _absolute_path(
        env.get("OPS_CRAWLER_HEALTH_STATE", "/run/mooncen-crawler/health.json"),
        label="health state",
    )
    tls_ca_file = _absolute_path(ca_raw, label="TLS CA file") if ca_raw else None
    if drain_state_path == health_state_path:
        raise ReleaseAgentError("drain and health state paths must be distinct")
    protected_paths = tuple(
        path for path in (allowed_signers, tls_ca_file, drain_state_path, health_state_path) if path is not None
    )
    if any(path.is_relative_to(release_root) or path.is_relative_to(state_directory) for path in protected_paths):
        raise ReleaseAgentError("trust and worker status paths must be outside managed release state")
    return AgentConfig(
        worker_id=worker_id,
        environment=environment_name,
        desired_state=desired,
        artifact_base=artifact_base,
        allowed_https_hosts=hosts,
        release_root=release_root,
        state_directory=state_directory,
        drain_state_path=drain_state_path,
        health_state_path=health_state_path,
        require_signature=require_signature,
        allowed_key_ids=allowed_keys,
        allowed_signers_path=allowed_signers,
        tls_ca_file=tls_ca_file,
        health_timeout_seconds=_mapping_integer(env, "OPS_CRAWLER_HEALTH_TIMEOUT_SECONDS", 90, 10, 600),
        drain_max_age_seconds=_mapping_integer(env, "OPS_CRAWLER_DRAIN_MAX_AGE_SECONDS", 120, 10, 600),
        max_unpacked_bytes=_mapping_integer(
            env,
            "OPS_CRAWLER_MAX_UNPACKED_BYTES",
            MAX_UNPACKED_BYTES,
            1_048_576,
            8 * 1024 * 1024 * 1024,
        ),
        max_archive_files=_mapping_integer(env, "OPS_CRAWLER_MAX_ARCHIVE_FILES", MAX_ARCHIVE_FILES, 1, 100_000),
    )


def _environment_boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ReleaseAgentError(f"{name} must be a boolean")


def _mapping_integer(env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError as exc:
        raise ReleaseAgentError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ReleaseAgentError(f"{name} is outside its allowed range")
    return value


def _ssl_context(config: AgentConfig) -> ssl.SSLContext:
    if config.tls_ca_file is not None:
        _secure_existing_file(config.tls_ca_file, label="TLS CA bundle")
    try:
        context = ssl.create_default_context(cafile=str(config.tls_ca_file) if config.tls_ca_file is not None else None)
    except (OSError, ssl.SSLError) as exc:
        raise ReleaseAgentError("TLS trust policy is unavailable") from exc
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _https_opener(config: AgentConfig) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
        urllib.request.HTTPSHandler(context=_ssl_context(config)),
    )


def fetch_desired_state(config: AgentConfig) -> DesiredState:
    request = urllib.request.Request(
        config.desired_state.url,
        headers={"Accept": "application/json", "User-Agent": "MoonCen-Crawler-Release-Agent/1"},
        method="GET",
    )
    try:
        with _https_opener(config).open(request, timeout=15) as response:
            if response.status != 200 or response.geturl() != config.desired_state.url:
                raise ReleaseAgentError("desired-state endpoint returned an unexpected response")
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > 256 * 1024:
                raise ReleaseAgentError("desired-state response is too large")
            document = response.read(256 * 1024 + 1)
    except ReleaseAgentError:
        raise
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise ReleaseAgentError("desired-state HTTPS request failed") from exc
    if len(document) > 256 * 1024:
        raise ReleaseAgentError("desired-state response is too large")
    try:
        state = parse_desired_state(document)
    except ValueError as exc:
        raise ReleaseAgentError(str(exc)) from exc
    if state.environment != config.environment:
        raise ReleaseAgentError("desired-state environment does not match this worker")
    return state


def _regular_private_file(path: Path, *, max_bytes: int, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseAgentError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > max_bytes
        or (os.name == "posix" and metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    ):
        raise ReleaseAgentError(f"{label} is not a private regular file")
    return metadata


def _read_private_json(path: Path, *, max_bytes: int, label: str) -> dict[str, Any]:
    before = _regular_private_file(path, max_bytes=max_bytes, label=label)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            data = handle.read(max_bytes + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise ReleaseAgentError(f"{label} could not be read") from exc
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ReleaseAgentError(f"{label} changed while being read")
    return _json_document(data, label=label, max_bytes=max_bytes)


def _parse_timestamp(value: Any, *, label: str) -> datetime:
    if type(value) is not str or len(value) > 40:
        raise ReleaseAgentError(f"{label} timestamp is invalid")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseAgentError(f"{label} timestamp is invalid") from exc
    if timestamp.tzinfo is None:
        raise ReleaseAgentError(f"{label} timestamp must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _local_state_payload(state: LocalState) -> dict[str, Any]:
    return {"schema_version": LOCAL_STATE_SCHEMA_VERSION, **asdict(state)}


def _parse_local_state(config: AgentConfig, raw: Mapping[str, Any]) -> LocalState:
    _strict_fields(
        raw,
        label="local release state",
        required=frozenset(
            {
                "schema_version",
                "worker_id",
                "observed_generation",
                "applied_generation",
                "rollout_id",
                "current_code_version",
                "current_artifact_digest",
                "current_config_revision",
                "last_attempt_status",
                "updated_at",
            }
        ),
    )
    if raw["schema_version"] != LOCAL_STATE_SCHEMA_VERSION or raw["worker_id"] != config.worker_id:
        raise ReleaseAgentError("local release state identity is invalid")
    for field in ("observed_generation", "applied_generation"):
        if type(raw[field]) is not int or raw[field] < 0:
            raise ReleaseAgentError("local release state generation is invalid")
    if raw["applied_generation"] > raw["observed_generation"]:
        raise ReleaseAgentError("local release state generations are inconsistent")
    local_identity = (
        ("current_code_version", _VERSION, "local code version"),
        ("current_artifact_digest", _SHA256, "local artifact digest"),
        ("current_config_revision", _CONFIG_REVISION, "local config revision"),
    )
    for field, pattern, label in local_identity:
        value = raw[field]
        if type(value) is not str or (value and not pattern.fullmatch(value)):
            raise ReleaseAgentError(f"{label} is invalid")
    if (
        type(raw["rollout_id"]) is not str
        or (raw["rollout_id"] != "bootstrap" and not _ROLLOUT_ID.fullmatch(raw["rollout_id"]))
        or type(raw["last_attempt_status"]) is not str
        or raw["last_attempt_status"] not in _LOCAL_ATTEMPT_STATUSES
    ):
        raise ReleaseAgentError("local release attempt identity is invalid")
    _parse_timestamp(raw["updated_at"], label="local release state")
    return LocalState(
        worker_id=raw["worker_id"],
        observed_generation=raw["observed_generation"],
        applied_generation=raw["applied_generation"],
        rollout_id=str(raw["rollout_id"]),
        current_code_version=raw["current_code_version"],
        current_artifact_digest=raw["current_artifact_digest"],
        current_config_revision=raw["current_config_revision"],
        last_attempt_status=str(raw["last_attempt_status"]),
        updated_at=raw["updated_at"],
    )


def load_local_state(config: AgentConfig, *, required: bool) -> LocalState:
    if not config.local_state_path.exists() and not config.local_state_path.is_symlink():
        if not required:
            return LocalState.empty(config.worker_id)
        raise ReleaseAgentError("local release state is unavailable")
    raw = _read_private_json(
        config.local_state_path,
        max_bytes=MAX_STATUS_DOCUMENT_BYTES,
        label="local release state",
    )
    return _parse_local_state(config, raw)


def _atomic_json(path: Path, payload: Mapping[str, Any], *, mode: int = 0o600) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ReleaseAgentError("state destination directory is unavailable")
    temporary = parent / f".{path.name}.{uuid.uuid4().hex}.new"
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            os.fchmod(handle.fileno(), mode)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(parent)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReleaseAgentError("atomic state publication failed") from exc


def save_local_state(config: AgentConfig, state: LocalState) -> None:
    _atomic_json(config.local_state_path, _local_state_payload(state))


def _safe_detail(value: str) -> str:
    normalized = " ".join(str(value).split())
    return re.sub(r"[\x00-\x1f\x7f]", "?", normalized)[:MAX_REPORT_DETAIL]


def write_report(config: AgentConfig, result: ReconcileResult) -> Path:
    if result.status not in _REPORT_STATUSES:
        raise ReleaseAgentError("release report status is invalid")
    if config.reports_directory.is_symlink() or not config.reports_directory.is_dir():
        raise ReleaseAgentError("release report directory is unavailable")
    normalized_detail = _safe_detail(result.detail)
    # One file is one immutable health observation. A consumed report must not
    # be recreated with the same primary key, otherwise PostgreSQL correctly
    # treats it as an idempotent retry and its server-side freshness timestamp
    # never advances. Retries of this exact file remain idempotent because its
    # UUID is persisted in both the filename and payload.
    report_id = str(uuid.uuid4())
    observation_order = time.time_ns()
    reported_at = _utc_now()
    path = config.reports_directory / (
        f"{result.generation:020d}-{observation_order:020d}-{report_id}.json"
    )
    _atomic_json(
        path,
        {
            "schema_version": 1,
            "id": report_id,
            "environment": config.environment,
            "worker_key": config.worker_id,
            "rollout_id": result.rollout_id,
            "desired_generation": result.generation,
            "status": result.status,
            "code_version": result.code_version,
            "artifact_digest": result.artifact_digest,
            "config_revision": result.config_revision,
            "health": {"healthy": result.status in {"ready", "rolled_back"}},
            "error_code": None if result.status in {"ready", "rolled_back"} else result.status,
            "error_message": normalized_detail,
            "reported_at": reported_at,
        },
        mode=0o640,
    )
    return path


def _terminal_failure_payload(state: LocalState, result: ReconcileResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "local_state": _local_state_payload(state),
        "report": asdict(result),
    }


def _parse_terminal_failure(
    config: AgentConfig,
    raw: Mapping[str, Any],
) -> tuple[LocalState, ReconcileResult]:
    _strict_fields(
        raw,
        label="terminal failure journal",
        required=frozenset({"schema_version", "local_state", "report"}),
    )
    if raw["schema_version"] != 1 or type(raw["local_state"]) is not dict:
        raise ReleaseAgentError("terminal failure journal is invalid")
    next_state = _parse_local_state(config, raw["local_state"])
    report = raw["report"]
    if type(report) is not dict:
        raise ReleaseAgentError("terminal failure report is invalid")
    _strict_fields(
        report,
        label="terminal failure report",
        required=frozenset(
            {
                "status",
                "detail",
                "generation",
                "rollout_id",
                "code_version",
                "artifact_digest",
                "config_revision",
            }
        ),
    )
    if (
        report["status"] != "failed"
        or type(report["detail"]) is not str
        or report["detail"] != _safe_detail(report["detail"])
        or type(report["generation"]) is not int
        or report["generation"] < 1
        or type(report["rollout_id"]) is not str
        or not _ROLLOUT_ID.fullmatch(report["rollout_id"])
        or type(report["code_version"]) is not str
        or not _VERSION.fullmatch(report["code_version"])
        or type(report["artifact_digest"]) is not str
        or not _SHA256.fullmatch(report["artifact_digest"])
        or type(report["config_revision"]) is not str
        or not _CONFIG_REVISION.fullmatch(report["config_revision"])
        or next_state.last_attempt_status != "failed"
        or next_state.observed_generation != report["generation"]
        or next_state.rollout_id != report["rollout_id"]
    ):
        raise ReleaseAgentError("terminal failure journal identity is invalid")
    return next_state, ReconcileResult(**report)


def _remove_terminal_failure(config: AgentConfig) -> None:
    try:
        config.terminal_failure_path.unlink(missing_ok=True)
        _fsync_directory(config.state_directory)
    except OSError as exc:
        raise ReleaseAgentError("terminal failure journal could not be cleared") from exc


def recover_terminal_failure(config: AgentConfig, local: LocalState) -> LocalState:
    path = config.terminal_failure_path
    if not path.exists() and not path.is_symlink():
        return local
    if config.pending_switch_path.exists() or config.pending_switch_path.is_symlink():
        raise ReleaseAgentError("terminal failure conflicts with a pending release switch")
    raw = _read_private_json(
        path,
        max_bytes=MAX_STATUS_DOCUMENT_BYTES,
        label="terminal failure journal",
    )
    next_state, result = _parse_terminal_failure(config, raw)
    if local.observed_generation > next_state.observed_generation:
        raise ReleaseAgentError("terminal failure journal is older than local state")
    if local.observed_generation == next_state.observed_generation:
        if local != next_state:
            raise ReleaseAgentError("terminal failure journal conflicts with local state")
    elif (
        local.applied_generation != next_state.applied_generation
        or local.current_code_version != next_state.current_code_version
        or local.current_artifact_digest != next_state.current_artifact_digest
        or local.current_config_revision != next_state.current_config_revision
    ):
        raise ReleaseAgentError("terminal failure journal conflicts with the installed release")
    save_local_state(config, next_state)
    write_report(config, result)
    _remove_terminal_failure(config)
    return next_state


def _fsync_directory(path: Path) -> None:
    # Windows does not expose directory descriptors.  The production unit is
    # Linux-only; this branch keeps pure filesystem tests portable.
    if os.name != "posix":
        if not path.is_dir():
            raise ReleaseAgentError("directory durability target is unavailable")
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _exclusive_reconcile_lock(config: AgentConfig):
    if fcntl is None:
        raise ReleaseAgentError("POSIX deployment locking is unavailable")
    try:
        descriptor = os.open(
            config.lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise ReleaseAgentError("release reconcile lock is unavailable") from exc
    try:
        try:
            metadata = os.fstat(descriptor)
        except OSError as exc:
            raise ReleaseAgentError("release reconcile lock could not be inspected") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ReleaseAgentError("release reconcile lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ReleaseAgentError("another crawler release reconcile is active") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except (AttributeError, OSError):
            pass
        os.close(descriptor)


def _download_artifact(config: AgentConfig, artifact: ArtifactMetadata, destination: Path) -> None:
    url = artifact_url(config.artifact_base, artifact)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/gzip, application/octet-stream",
            "User-Agent": "MoonCen-Crawler-Release-Agent/1",
        },
        method="GET",
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with _https_opener(config).open(request, timeout=60) as response:
            if response.status != 200 or response.geturl() != url:
                raise ReleaseAgentError("artifact endpoint returned an unexpected response")
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) != artifact.size_bytes:
                raise ReleaseAgentError("artifact Content-Length does not match metadata")
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > artifact.size_bytes:
                        raise ReleaseAgentError("artifact exceeded its declared size")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
    except ReleaseAgentError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, ValueError, urllib.error.URLError) as exc:
        destination.unlink(missing_ok=True)
        raise ReleaseAgentError("artifact HTTPS download failed") from exc
    if total != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
        destination.unlink(missing_ok=True)
        raise ReleaseAgentError("artifact size or SHA-256 verification failed")


def _secure_existing_file(path: Path, *, label: str) -> None:
    metadata = _regular_private_file(path, max_bytes=4 * 1024 * 1024, label=label)
    if os.name == "posix" and metadata.st_uid != 0:
        raise ReleaseAgentError(f"{label} must be owned by root")


def _fixed_system_executable(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseAgentError(f"fixed {label} executable is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or (
        os.name == "posix"
        and (
            metadata.st_uid != 0
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        )
    ):
        raise ReleaseAgentError(f"fixed {label} executable is unavailable")


def verify_artifact_signature(config: AgentConfig, artifact: ArtifactMetadata, archive_path: Path) -> None:
    if not artifact.signed:
        if config.require_signature:
            raise ReleaseAgentError("unsigned crawler artifact is forbidden by local policy")
        return
    if artifact.key_id not in config.allowed_key_ids:
        raise ReleaseAgentError("artifact key_id is outside the local allowlist")
    if config.allowed_signers_path is None:
        raise ReleaseAgentError("signed artifact has no local allowed-signers policy")
    _secure_existing_file(config.allowed_signers_path, label="allowed signers file")
    _fixed_system_executable(SSHSIG_VERIFY_PATH, label="ssh-keygen verifier")
    signature_path = archive_path.with_suffix(archive_path.suffix + ".sig")
    assert artifact.signature is not None
    try:
        signature = base64.b64decode(artifact.signature, validate=True)
        descriptor = os.open(signature_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(signature)
            handle.flush()
            os.fsync(handle.fileno())
        with archive_path.open("rb") as archive:
            completed = subprocess.run(
                [
                    str(SSHSIG_VERIFY_PATH),
                    "-Y",
                    "verify",
                    "-f",
                    str(config.allowed_signers_path),
                    "-I",
                    str(artifact.key_id),
                    "-n",
                    SSHSIG_NAMESPACE,
                    "-s",
                    str(signature_path),
                ],
                stdin=archive,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
                shell=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C"},
            )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ReleaseAgentError("artifact signature verification could not run") from exc
    finally:
        signature_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise ReleaseAgentError("artifact OpenSSH signature verification failed")


def _safe_archive_name(value: str) -> PurePosixPath:
    if not value or len(value) > 512 or "\\" in value or "//" in value:
        raise ReleaseAgentError("release archive contains an unsafe path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseAgentError("release archive contains an unsafe path")
    return path


def _copy_exact(source: BinaryIO, destination: BinaryIO, expected: int) -> None:
    remaining = expected
    while remaining:
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ReleaseAgentError("release archive member ended early")
        destination.write(chunk)
        remaining -= len(chunk)
    if source.read(1):
        raise ReleaseAgentError("release archive member exceeded its declared size")


def extract_release_archive(
    archive_path: Path,
    candidate: Path,
    artifact: ArtifactMetadata,
    *,
    max_files: int,
    max_unpacked_bytes: int,
) -> None:
    """Extract regular files/directories only; links and special files are rejected."""

    try:
        candidate.mkdir(mode=0o700)
        with tarfile.open(archive_path, mode="r|gz", tarinfo=_BoundedTarInfo) as archive:
            setattr(archive, "_mooncen_extension_limit", max_files)
            setattr(archive, "_mooncen_extension_count", 0)
            setattr(archive, "_mooncen_extension_bytes", 0)
            setattr(archive, "_mooncen_global_pax_count", 0)
            paths: dict[PurePosixPath, str] = {}
            parents_with_children: set[PurePosixPath] = set()
            file_count = 0
            unpacked = 0
            for member in archive:
                file_count += 1
                if file_count > max_files:
                    raise ReleaseAgentError("release archive file count is invalid")
                path = _safe_archive_name(member.name)
                if member.mode & 0o7000:
                    raise ReleaseAgentError("release archive contains privileged mode bits")
                kind = "directory" if member.isdir() else "file" if member.isreg() else "unsupported"
                if kind == "unsupported" or path in paths:
                    raise ReleaseAgentError("release archive contains unsupported or duplicate entries")
                for parent in path.parents:
                    if str(parent) == ".":
                        break
                    if paths.get(parent) == "file":
                        raise ReleaseAgentError("release archive nests content below a regular file")
                    parents_with_children.add(parent)
                if kind == "file" and path in parents_with_children:
                    raise ReleaseAgentError("release archive replaces a populated directory with a file")
                paths[path] = kind
                if member.isreg():
                    if member.size < 0:
                        raise ReleaseAgentError("release archive member size is invalid")
                    unpacked += member.size
                    if unpacked > max_unpacked_bytes:
                        raise ReleaseAgentError("release archive exceeds the unpacked size policy")
                    destination = candidate.joinpath(*path.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ReleaseAgentError("release archive member could not be opened")
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                    descriptor = os.open(destination, flags, 0o600)
                    with source, os.fdopen(descriptor, "wb") as output:
                        _copy_exact(source, output, member.size)
                        output.flush()
                        os.fsync(output.fileno())
                    os.chmod(destination, 0o755 if member.mode & 0o111 else 0o644)
                else:
                    destination = candidate.joinpath(*path.parts)
                    destination.mkdir(parents=True, exist_ok=True, mode=0o755)
                # Stream mode still caches yielded TarInfo objects by default.
                # We never resolve archive links, so retaining them is unnecessary.
                archive.members.clear()
            if file_count == 0:
                raise ReleaseAgentError("release archive file count is invalid")
    except ReleaseAgentError:
        shutil.rmtree(candidate, ignore_errors=True)
        raise
    except (OSError, tarfile.TarError) as exc:
        shutil.rmtree(candidate, ignore_errors=True)
        raise ReleaseAgentError("release archive extraction failed") from exc

    manifest_path = candidate / RELEASE_MANIFEST_NAME
    manifest = _read_private_json(
        manifest_path,
        max_bytes=MAX_RELEASE_MANIFEST_BYTES,
        label="embedded crawler release manifest",
    )
    _strict_fields(
        manifest,
        label="embedded crawler release manifest",
        required=frozenset({"schema_version", "code_version", "config_revision"}),
    )
    if (
        manifest["schema_version"] != 1
        or manifest["code_version"] != artifact.code_version
        or manifest["config_revision"] != artifact.config_revision
    ):
        shutil.rmtree(candidate, ignore_errors=True)
        raise ReleaseAgentError("embedded crawler release manifest does not match metadata")


def _release_metadata(artifact: ArtifactMetadata) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "code_version": artifact.code_version,
        "artifact_digest": artifact.sha256,
        "config_revision": artifact.config_revision,
    }


def _write_release_files(candidate: Path, artifact: ArtifactMetadata) -> None:
    metadata_path = candidate / RELEASE_METADATA_NAME
    _atomic_json(metadata_path, _release_metadata(artifact), mode=0o444)
    if os.name == "posix":
        os.chown(metadata_path, 0, 0, follow_symlinks=False)
        metadata_descriptor = os.open(metadata_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(metadata_descriptor)
        finally:
            os.close(metadata_descriptor)
    env_path = candidate / RELEASE_ENV_NAME
    values = {
        WORKER_CODE_VERSION_ENV: artifact.code_version,
        WORKER_ARTIFACT_DIGEST_ENV: artifact.sha256,
        WORKER_CONFIG_REVISION_ENV: artifact.config_revision,
    }
    encoded = "".join(f"{key}={value}\n" for key, value in values.items()).encode("ascii")
    descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        if os.name == "posix":
            os.fchown(handle.fileno(), 0, 0)
        os.fchmod(handle.fileno(), 0o444)
        handle.flush()
        os.fsync(handle.fileno())
    for directory, _children, _files in os.walk(candidate, topdown=False):
        release_directory = Path(directory)
        os.chmod(release_directory, 0o755)
        _fsync_directory(release_directory)


def _release_tree_is_immutable(path: Path) -> bool:
    """Reject writable, non-owned, linked, or special installed release entries."""

    def raise_walk_error(error: OSError) -> None:
        raise error

    try:
        for directory, children, files in os.walk(
            path,
            topdown=True,
            onerror=raise_walk_error,
            followlinks=False,
        ):
            entries = [Path(directory)]
            entries.extend(Path(directory) / name for name in (*children, *files))
            for entry in entries:
                metadata = entry.lstat()
                if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                    return False
                if os.name == "posix" and (
                    metadata.st_uid != os.geteuid() or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                ):
                    return False
    except OSError:
        return False
    return True


def _existing_release_matches(path: Path, artifact: ArtifactMetadata) -> bool:
    if path.is_symlink() or not path.is_dir() or not _release_tree_is_immutable(path):
        return False
    try:
        metadata = _read_private_json(
            path / RELEASE_METADATA_NAME,
            max_bytes=MAX_RELEASE_MANIFEST_BYTES,
            label="installed release metadata",
        )
    except ReleaseAgentError:
        return False
    return metadata == _release_metadata(artifact)


def materialize_release(config: AgentConfig, artifact: ArtifactMetadata, archive_path: Path) -> Path:
    release_name = f"{artifact.code_version}-{artifact.sha256[:16]}"
    final = config.releases_directory / release_name
    if final.exists() or final.is_symlink():
        if not _existing_release_matches(final, artifact):
            raise ReleaseAgentError("immutable release directory conflicts with artifact metadata")
        return final
    candidate = config.staging_directory / f".incoming-{uuid.uuid4().hex}"
    extract_release_archive(
        archive_path,
        candidate,
        artifact,
        max_files=config.max_archive_files,
        max_unpacked_bytes=config.max_unpacked_bytes,
    )
    try:
        _write_release_files(candidate, artifact)
        os.replace(candidate, final)
        _fsync_directory(config.releases_directory)
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise
    return final


def _status_age_seconds(timestamp: datetime) -> float:
    return (datetime.now(timezone.utc) - timestamp).total_seconds()


def assert_drained(config: AgentConfig, state: DesiredState) -> None:
    raw = _read_private_json(
        config.drain_state_path,
        max_bytes=MAX_STATUS_DOCUMENT_BYTES,
        label="crawler drain state",
    )
    _strict_fields(
        raw,
        label="crawler drain state",
        required=frozenset(
            {
                "schema_version",
                "worker_id",
                "rollout_id",
                "generation",
                "drained",
                "active_jobs",
                "observed_at",
            }
        ),
    )
    timestamp = _parse_timestamp(raw["observed_at"], label="crawler drain state")
    age = _status_age_seconds(timestamp)
    if (
        raw["schema_version"] != STATUS_DOCUMENT_SCHEMA_VERSION
        or raw["worker_id"] != config.worker_id
        or raw["rollout_id"] != state.rollout.rollout_id
        or raw["generation"] != state.generation
        or raw["drained"] is not True
        or type(raw["active_jobs"]) is not int
        or raw["active_jobs"] != 0
        or not -5 <= age <= config.drain_max_age_seconds
    ):
        raise ReleaseAgentError("crawler drain state does not authorize this release switch")


def _current_release_target(config: AgentConfig, local: LocalState) -> str:
    target = _current_link_target(config)
    release = config.release_root.joinpath(*PurePosixPath(target).parts)
    expected = {
        "schema_version": 1,
        "code_version": local.current_code_version,
        "artifact_digest": local.current_artifact_digest,
        "config_revision": local.current_config_revision,
    }
    observed = _read_private_json(
        release / RELEASE_METADATA_NAME,
        max_bytes=MAX_RELEASE_MANIFEST_BYTES,
        label="current release metadata",
    )
    if observed != expected:
        raise ReleaseAgentError("local state does not match the current immutable release")
    return target


def _current_link_target(config: AgentConfig) -> str:
    link = config.current_link
    if not link.is_symlink():
        raise ReleaseAgentError("crawler current release is not an atomic symlink")
    target = os.readlink(link)
    relative = PurePosixPath(target)
    if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != "releases":
        raise ReleaseAgentError("crawler current symlink target is unsafe")
    release = config.release_root.joinpath(*relative.parts)
    if release.is_symlink() or not release.is_dir() or not _release_tree_is_immutable(release):
        raise ReleaseAgentError("crawler current release directory is unavailable")
    return target


def _switch_current(config: AgentConfig, target: str) -> None:
    relative = PurePosixPath(target)
    if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != "releases":
        raise ReleaseAgentError("release switch target is unsafe")
    destination = config.release_root.joinpath(*relative.parts)
    if destination.is_symlink() or not destination.is_dir() or not _release_tree_is_immutable(destination):
        raise ReleaseAgentError("release switch target is unavailable")
    temporary = config.release_root / f".current.{uuid.uuid4().hex}.new"
    try:
        os.symlink(target, temporary, target_is_directory=True)
        os.replace(temporary, config.current_link)
        _fsync_directory(config.release_root)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ReleaseAgentError("atomic crawler release switch failed") from exc


def _pending_switch_payload(
    config: AgentConfig,
    state: DesiredState,
    local: LocalState,
    decision: ReconcileDecision,
    *,
    previous_target: str,
    target: str,
    phase: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "worker_id": config.worker_id,
        "rollout_id": state.rollout.rollout_id,
        "generation": state.generation,
        "phase": phase,
        "previous_target": previous_target,
        "target": target,
        "previous_code_version": local.current_code_version,
        "previous_artifact_digest": local.current_artifact_digest,
        "previous_config_revision": local.current_config_revision,
        "target_code_version": decision.artifact.code_version,
        "target_artifact_digest": decision.artifact.sha256,
        "target_config_revision": decision.artifact.config_revision,
        "created_at": _utc_now(),
    }


def _write_pending_switch(config: AgentConfig, payload: Mapping[str, Any]) -> None:
    _atomic_json(config.pending_switch_path, payload)


def _remove_pending_switch(config: AgentConfig) -> None:
    try:
        config.pending_switch_path.unlink(missing_ok=True)
        _fsync_directory(config.state_directory)
    except OSError as exc:
        raise ReleaseAgentError("pending release journal could not be cleared") from exc


def _release_identity(code_version: str, digest: str, config_revision: str) -> ArtifactMetadata:
    if (
        type(code_version) is not str
        or not _VERSION.fullmatch(code_version)
        or type(digest) is not str
        or not _SHA256.fullmatch(digest)
        or type(config_revision) is not str
        or not _CONFIG_REVISION.fullmatch(config_revision)
    ):
        raise ReleaseAgentError("pending release journal identity is invalid")
    return ArtifactMetadata(
        code_version=code_version,
        relative_path="recovery/identity.tar.gz",
        sha256=digest,
        size_bytes=1,
        config_revision=config_revision,
    )


def _target_metadata_matches(config: AgentConfig, target: str, artifact: ArtifactMetadata) -> bool:
    relative = PurePosixPath(target)
    if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != "releases":
        return False
    release = config.release_root.joinpath(*relative.parts)
    return _existing_release_matches(release, artifact)


def recover_pending_switch(config: AgentConfig, local: LocalState) -> LocalState:
    """Conservatively restore an uncommitted switch after a killed agent."""

    path = config.pending_switch_path
    if not path.exists() and not path.is_symlink():
        return local
    raw = _read_private_json(
        path,
        max_bytes=MAX_STATUS_DOCUMENT_BYTES,
        label="pending release journal",
    )
    _strict_fields(
        raw,
        label="pending release journal",
        required=frozenset(
            {
                "schema_version",
                "worker_id",
                "rollout_id",
                "generation",
                "phase",
                "previous_target",
                "target",
                "previous_code_version",
                "previous_artifact_digest",
                "previous_config_revision",
                "target_code_version",
                "target_artifact_digest",
                "target_config_revision",
                "created_at",
            }
        ),
    )
    if (
        raw["schema_version"] != 1
        or raw["worker_id"] != config.worker_id
        or type(raw["generation"]) is not int
        or raw["generation"] < 1
        or type(raw["phase"]) is not str
        or raw["phase"] not in {"prepared", "switched", "committed"}
        or type(raw["rollout_id"]) is not str
        or not _ROLLOUT_ID.fullmatch(raw["rollout_id"])
    ):
        raise ReleaseAgentError("pending release journal identity is invalid")
    _parse_timestamp(raw["created_at"], label="pending release journal")
    previous = _release_identity(
        raw["previous_code_version"],
        raw["previous_artifact_digest"],
        raw["previous_config_revision"],
    )
    target_artifact = _release_identity(
        raw["target_code_version"],
        raw["target_artifact_digest"],
        raw["target_config_revision"],
    )
    previous_target = str(raw["previous_target"])
    target = str(raw["target"])
    if not _target_metadata_matches(config, previous_target, previous):
        raise ReleaseAgentError("pending journal rollback release is unavailable")
    if not _target_metadata_matches(config, target, target_artifact):
        raise ReleaseAgentError("pending journal target release is unavailable")
    current_target = _current_link_target(config)
    target_is_committed = bool(
        current_target == target
        and local.current_code_version == target_artifact.code_version
        and local.current_artifact_digest == target_artifact.sha256
        and local.current_config_revision == target_artifact.config_revision
        and local.applied_generation >= raw["generation"]
    )
    if target_is_committed:
        _remove_pending_switch(config)
        return local
    if current_target not in {previous_target, target}:
        raise ReleaseAgentError("current release does not match the pending release journal")
    try:
        rollback_at = datetime.now(timezone.utc)
        if current_target != previous_target:
            _switch_current(config, previous_target)
        restart_worker()
        wait_for_health(config, previous, switched_at=rollback_at)
    except Exception as exc:
        result = ReconcileResult(
            status="failed",
            detail=f"pending release could not restore previous worker health: {type(exc).__name__}",
            generation=int(raw["generation"]),
            rollout_id=str(raw["rollout_id"]),
            code_version=previous.code_version,
            artifact_digest=previous.sha256,
            config_revision=previous.config_revision,
        )
        write_report(config, result)
        failed = replace_local_attempt(
            local,
            observed_generation=max(local.observed_generation, int(raw["generation"])),
            rollout_id=str(raw["rollout_id"]),
            status="rollback_failed",
        )
        save_local_state(config, failed)
        raise RollbackFailed("pending release could not restore previous worker health") from exc
    recovered = replace_local_attempt(
        local,
        observed_generation=max(local.observed_generation, int(raw["generation"])),
        rollout_id=str(raw["rollout_id"]),
        status="recovered_previous",
    )
    save_local_state(config, recovered)
    write_report(
        config,
        ReconcileResult(
            status="rolled_back",
            detail="incomplete release switch was recovered to the previous healthy release",
            generation=int(raw["generation"]),
            rollout_id=str(raw["rollout_id"]),
            code_version=previous.code_version,
            artifact_digest=previous.sha256,
            config_revision=previous.config_revision,
        ),
    )
    _remove_pending_switch(config)
    return recovered


def restart_worker() -> None:
    _fixed_system_executable(SYSTEMCTL_PATH, label="systemctl")
    try:
        completed = subprocess.run(
            [str(SYSTEMCTL_PATH), "restart", WORKER_SYSTEMD_UNIT],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
            check=False,
            shell=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseAgentError("fixed crawler worker restart could not run") from exc
    if completed.returncode != 0:
        raise ReleaseAgentError("fixed crawler worker restart failed")


def _matching_health_timestamp(config: AgentConfig, artifact: ArtifactMetadata) -> datetime | None:
    try:
        raw = _read_private_json(
            config.health_state_path,
            max_bytes=MAX_STATUS_DOCUMENT_BYTES,
            label="crawler health state",
        )
        _strict_fields(
            raw,
            label="crawler health state",
            required=frozenset(
                {
                    "schema_version",
                    "worker_id",
                    "healthy",
                    "code_version",
                    "artifact_digest",
                    "config_revision",
                    "observed_at",
                }
            ),
        )
        observed_at = _parse_timestamp(raw["observed_at"], label="crawler health state")
    except ReleaseAgentError:
        return None
    if not (
        raw["schema_version"] == STATUS_DOCUMENT_SCHEMA_VERSION
        and raw["worker_id"] == config.worker_id
        and raw["healthy"] is True
        and raw["code_version"] == artifact.code_version
        and raw["artifact_digest"] == artifact.sha256
        and raw["config_revision"] == artifact.config_revision
    ):
        return None
    return observed_at


def _health_matches(config: AgentConfig, artifact: ArtifactMetadata, *, switched_at: datetime) -> bool:
    observed_at = _matching_health_timestamp(config, artifact)
    return bool(observed_at is not None and observed_at >= switched_at and _status_age_seconds(observed_at) >= -5)


def _recent_health_matches(config: AgentConfig, artifact: ArtifactMetadata) -> bool:
    observed_at = _matching_health_timestamp(config, artifact)
    if observed_at is None:
        return False
    age = _status_age_seconds(observed_at)
    return -5 <= age <= config.health_timeout_seconds


def wait_for_health(
    config: AgentConfig,
    artifact: ArtifactMetadata,
    *,
    switched_at: datetime,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = time.monotonic() + config.health_timeout_seconds
    while True:
        if _health_matches(config, artifact, switched_at=switched_at):
            return
        if time.monotonic() >= deadline:
            raise ReleaseAgentError("crawler worker did not publish matching healthy state")
        sleep(1.0)


def _prepare_directories(config: AgentConfig) -> None:
    for path in (
        config.release_root,
        config.releases_directory,
        config.staging_directory,
        config.state_directory,
        config.reports_directory,
    ):
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ReleaseAgentError(f"required release directory is unavailable: {path.name}") from exc
        invalid_posix_permissions = False
        if os.name == "posix":
            invalid_posix_permissions = metadata.st_uid != os.geteuid()
            if path == config.reports_directory and config.environment in {"production", "staging"}:
                # The separate reporter UID needs to enumerate and consume the
                # handoff spool.  The installer pins the group; runtime accepts
                # only the exact setgid/group-private mode and never world access.
                invalid_posix_permissions = invalid_posix_permissions or stat.S_IMODE(metadata.st_mode) != 0o2770
            else:
                invalid_posix_permissions = invalid_posix_permissions or bool(
                    metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                )
        if not stat.S_ISDIR(metadata.st_mode) or invalid_posix_permissions:
            raise ReleaseAgentError(f"required release directory is unavailable: {path.name}")


def _result(
    decision: ReconcileDecision,
    *,
    status: str,
    detail: str,
    artifact: ArtifactMetadata | None = None,
) -> ReconcileResult:
    selected = decision.artifact if artifact is None else artifact
    return ReconcileResult(
        status=status,
        detail=detail,
        generation=decision.generation,
        rollout_id=decision.rollout_id,
        code_version=selected.code_version,
        artifact_digest=selected.sha256,
        config_revision=selected.config_revision,
    )


def _record_attempt(
    local: LocalState,
    decision: ReconcileDecision,
    *,
    status: str,
    applied: bool,
) -> LocalState:
    artifact = decision.artifact
    return LocalState(
        worker_id=local.worker_id,
        observed_generation=decision.generation,
        applied_generation=decision.generation if applied else local.applied_generation,
        rollout_id=decision.rollout_id,
        current_code_version=artifact.code_version if applied else local.current_code_version,
        current_artifact_digest=artifact.sha256 if applied else local.current_artifact_digest,
        current_config_revision=(decision.desired.config_revision if applied else local.current_config_revision),
        last_attempt_status=status,
        updated_at=_utc_now(),
    )


def replace_local_attempt(
    local: LocalState,
    *,
    observed_generation: int,
    rollout_id: str,
    status: str,
) -> LocalState:
    return LocalState(
        worker_id=local.worker_id,
        observed_generation=observed_generation,
        applied_generation=local.applied_generation,
        rollout_id=rollout_id,
        current_code_version=local.current_code_version,
        current_artifact_digest=local.current_artifact_digest,
        current_config_revision=local.current_config_revision,
        last_attempt_status=status,
        updated_at=_utc_now(),
    )


def reconcile_apply(
    config: AgentConfig,
    state: DesiredState,
    local: LocalState,
    decision: ReconcileDecision,
) -> ReconcileResult:
    """Apply one generation, rolling back the symlink on any health failure."""

    _prepare_directories(config)
    if decision.action in {"blocked", "noop"}:
        noop_healthy = True
        if decision.action == "noop":
            try:
                _current_release_target(config, local)
            except ReleaseAgentError:
                noop_healthy = False
            if noop_healthy and not _recent_health_matches(config, decision.artifact):
                noop_healthy = False
        if decision.action == "blocked":
            local_status = "blocked"
            report_status = "drifted" if "disabled" in decision.reason else "pending"
        else:
            local_status = "no_change" if noop_healthy else "failed"
            report_status = "ready" if noop_healthy else "drifted"
        next_state = _record_attempt(
            local,
            decision,
            status=local_status,
            applied=decision.action == "noop" and noop_healthy,
        )
        save_local_state(config, next_state)
        if decision.action == "blocked":
            result = ReconcileResult(
                status=report_status,
                detail=decision.reason,
                generation=decision.generation,
                rollout_id=decision.rollout_id,
                code_version=local.current_code_version,
                artifact_digest=local.current_artifact_digest,
                config_revision=local.current_config_revision,
            )
        else:
            result = _result(
                decision,
                status=report_status,
                detail=(
                    decision.reason
                    if noop_healthy
                    else "current release integrity or recent worker health check failed"
                ),
            )
        write_report(config, result)
        return result
    if local.observed_generation == state.generation and local.last_attempt_status in {
        "failed",
        "rolled_back",
        "rollback_failed",
    }:
        raise ReleaseAgentError("failed rollout generation requires a new central generation")
    if not local.current_code_version or not local.current_artifact_digest:
        raise ReleaseAgentError("automatic first installation is forbidden; bootstrap a reviewed release")

    previous_target = _current_release_target(config, local)
    try:
        previous_artifact = state.artifacts[local.current_code_version]
    except KeyError as exc:
        raise ReleaseAgentError("current rollback artifact is absent from desired state") from exc
    if (
        previous_artifact.sha256 != local.current_artifact_digest
        or previous_artifact.config_revision != local.current_config_revision
    ):
        raise ReleaseAgentError("current rollback artifact metadata conflicts with local state")

    archive_path = config.staging_directory / f"artifact-{uuid.uuid4().hex}.tar.gz"
    switched = False
    pending: dict[str, Any] | None = None
    try:
        _download_artifact(config, decision.artifact, archive_path)
        verify_artifact_signature(config, decision.artifact, archive_path)
        release = materialize_release(config, decision.artifact, archive_path)
        assert_drained(config, state)
        target = f"releases/{release.name}"
        pending = _pending_switch_payload(
            config,
            state,
            local,
            decision,
            previous_target=previous_target,
            target=target,
            phase="prepared",
        )
        # The atomic rename inside journal publication can succeed before a
        # directory-fsync error is raised.  From this point onward, treat the
        # release as potentially switched and restore the baseline conservatively.
        switched = True
        _write_pending_switch(config, pending)
        switched_at = datetime.now(timezone.utc)
        _switch_current(config, target)
        pending = {**pending, "phase": "switched"}
        _write_pending_switch(config, pending)
        restart_worker()
        wait_for_health(config, decision.artifact, switched_at=switched_at)
    except Exception as exc:
        if switched:
            try:
                rollback_at = datetime.now(timezone.utc)
                _switch_current(config, previous_target)
                restart_worker()
                wait_for_health(config, previous_artifact, switched_at=rollback_at)
            except Exception as rollback_exc:
                failed = _record_attempt(local, decision, status="rollback_failed", applied=False)
                result = _result(
                    decision,
                    status="failed",
                    detail=f"activation failed; rollback health failed: {type(rollback_exc).__name__}",
                    artifact=previous_artifact,
                )
                write_report(config, result)
                save_local_state(config, failed)
                raise RollbackFailed(result.detail) from rollback_exc
            rolled_back = _record_attempt(local, decision, status="rolled_back", applied=False)
            save_local_state(config, rolled_back)
            result = _result(
                decision,
                status="rolled_back",
                detail=f"activation failed and previous release was restored: {type(exc).__name__}",
                artifact=previous_artifact,
            )
            write_report(config, result)
            _remove_pending_switch(config)
            return result
        failed = _record_attempt(local, decision, status="failed", applied=False)
        result = _result(
            decision,
            status="failed",
            detail=f"release preparation failed before switch: {type(exc).__name__}",
        )
        _atomic_json(config.terminal_failure_path, _terminal_failure_payload(failed, result))
        save_local_state(config, failed)
        write_report(config, result)
        _remove_terminal_failure(config)
        raise ReleaseAgentError(result.detail) from exc
    finally:
        archive_path.unlink(missing_ok=True)

    activated = _record_attempt(local, decision, status="activated", applied=True)
    save_local_state(config, activated)
    if pending is None:
        raise ReleaseAgentError("release switch journal was unexpectedly unavailable")
    _write_pending_switch(config, {**pending, "phase": "committed"})
    _remove_pending_switch(config)
    result = _result(decision, status="ready", detail="desired crawler release is healthy")
    write_report(config, result)
    return result


def run_apply(config: AgentConfig, state: DesiredState) -> ReconcileResult:
    """Lock, recover an interrupted switch, and reconcile the current generation."""

    _prepare_directories(config)
    with _exclusive_reconcile_lock(config):
        local = load_local_state(config, required=True)
        local = recover_terminal_failure(config, local)
        local = recover_pending_switch(config, local)
        decision = _decision(state, config, local)
        return reconcile_apply(config, state, local, decision)


def dry_run_plan(config: AgentConfig, decision: ReconcileDecision) -> dict[str, Any]:
    steps = []
    if decision.action == "deploy":
        steps = [
            "download_from_allowlisted_https_origin",
            "verify_exact_size_and_sha256",
            "verify_optional_openssh_signature",
            "extract_regular_files_only",
            "verify_generation_bound_drain_state",
            "atomically_switch_current_symlink",
            "restart_fixed_worker_unit",
            "verify_release_bound_health_state",
            "rollback_and_report_on_failure",
        ]
    return {
        "mode": "dry-run",
        "worker_id": config.worker_id,
        "environment": config.environment,
        "generation": decision.generation,
        "rollout_id": decision.rollout_id,
        "rollout_action": decision.action,
        "reason": decision.reason,
        "desired": {
            WORKER_CODE_VERSION_ENV: decision.artifact.code_version,
            WORKER_ARTIFACT_DIGEST_ENV: decision.artifact.sha256,
            WORKER_CONFIG_REVISION_ENV: decision.desired.config_revision,
        },
        "artifact_host": config.artifact_base.hostname,
        "steps": steps,
    }


def check_configuration(config: AgentConfig) -> dict[str, Any]:
    """Return public policy facts only; do not read the network or mutate disk."""

    _prepare_directories(config)
    _ssl_context(config)
    if config.require_signature:
        if config.allowed_signers_path is None:
            raise ReleaseAgentError("signature policy has no allowed-signers file")
        _secure_existing_file(config.allowed_signers_path, label="allowed signers file")
        _fixed_system_executable(SSHSIG_VERIFY_PATH, label="ssh-keygen verifier")
    _fixed_system_executable(SYSTEMCTL_PATH, label="systemctl")
    return {
        "ok": True,
        "mode": "check",
        "worker_id": config.worker_id,
        "environment": config.environment,
        "desired_state_host": config.desired_state.hostname,
        "artifact_host": config.artifact_base.hostname,
        "signature_required": config.require_signature,
        "allowed_key_ids": sorted(config.allowed_key_ids),
        "worker_unit": WORKER_SYSTEMD_UNIT,
        "arbitrary_commands_allowed": False,
        "manifest_urls_allowed": False,
        "local_directories_ready": True,
        "tls_context_ready": True,
    }


def _decision(state: DesiredState, config: AgentConfig, local: LocalState) -> ReconcileDecision:
    try:
        return reconcile_decision(
            state,
            config.worker_id,
            current_version=local.current_code_version,
            current_digest=local.current_artifact_digest,
            current_config_revision=local.current_config_revision,
            last_generation=local.observed_generation,
        )
    except ValueError as exc:
        raise ReleaseAgentError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MoonCen crawler desired-state release agent")
    parser.add_argument("--mode", choices=("check", "dry-run", "apply"), default=None)
    args = parser.parse_args(argv)
    try:
        if os.name == "posix":
            _secure_existing_file(LOCAL_POLICY_PATH, label="release agent policy file")
        config = load_agent_config()
        mode = args.mode or os.environ.get("OPS_CRAWLER_RELEASE_MODE", "check").strip()
        if mode not in {"check", "dry-run", "apply"}:
            raise ReleaseAgentError("OPS_CRAWLER_RELEASE_MODE is invalid")
        if mode == "check":
            payload = check_configuration(config)
        else:
            state = fetch_desired_state(config)
            if mode == "dry-run":
                local = load_local_state(config, required=False)
                payload = dry_run_plan(config, _decision(state, config, local))
            else:
                payload = asdict(run_apply(config, state))
    except ReleaseAgentError as exc:
        print(json.dumps({"ok": False, "error": _safe_detail(str(exc))}, sort_keys=True))
        return 70
    except Exception:
        print(
            json.dumps(
                {"ok": False, "error": "unexpected release-agent failure; no action authorized"},
                sort_keys=True,
            )
        )
        return 70
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AgentConfig",
    "HttpsEndpoint",
    "LocalState",
    "ReleaseAgentError",
    "RollbackFailed",
    "WORKER_ARTIFACT_DIGEST_ENV",
    "WORKER_CODE_VERSION_ENV",
    "WORKER_CONFIG_REVISION_ENV",
    "artifact_url",
    "assert_drained",
    "check_configuration",
    "dry_run_plan",
    "extract_release_archive",
    "fetch_desired_state",
    "load_agent_config",
    "load_local_state",
    "materialize_release",
    "reconcile_apply",
    "recover_pending_switch",
    "recover_terminal_failure",
    "restart_worker",
    "run_apply",
    "validate_https_endpoint",
    "verify_artifact_signature",
    "wait_for_health",
    "write_report",
]
