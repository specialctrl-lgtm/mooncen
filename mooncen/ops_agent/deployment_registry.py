from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from .production_topology import load_production_topology


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_REGISTRY_PATH = Path("config/deploy_servers.json")
DEPLOY_SCRIPT_PATH = Path("deploy_mooncen.ps1")
DEPLOY_LOCAL_PATH = Path("deploy.local.ps1")
# The isolated system worker cannot write into the immutable application
# release mounted at PROJECT_ROOT.  Its private state is deliberately pinned
# outside the release tree and is not shared with the Ops API account.
DEPLOYMENT_WORKER_STATE_ROOT = Path("/var/lib/mooncen-deployment-worker/state")
DEPLOYMENT_WORKER_HEARTBEAT_PATH = Path("heartbeat.json")
DEPLOYMENT_HOLD_PATH = Path("logs/ops-console-local/deployment.hold.json")
DEPLOYMENT_RELEASE_MANIFEST_DIR = Path(
    "logs/ops-console-local/deployment-releases"
)
COMMIT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
TARGET_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
HOLD_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SSH_PUBLIC_KEY_TYPE_PATTERN = re.compile(
    r"^(?:(?:ssh-(?:rsa|dss|ed25519)|ecdsa-sha2-nistp(?:256|384|521))"
    r"(?:-cert-v01@openssh\.com)?|"
    r"sk-(?:ssh-ed25519|ecdsa-sha2-nistp256)(?:-cert-v01)?@openssh\.com)$"
)
HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,252}$")
USER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,63}$")
DOMAIN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
REMOTE_DIR_PATTERN = re.compile(r"^/opt/[A-Za-z0-9._/-]{1,220}$")
SNAPSHOT_REF_PATTERN = re.compile(
    r"^refs/mooncen/deploy-snapshots/[0-9a-f-]{36}$"
)
RELEASE_REF_PATTERN = re.compile(
    r"^refs/mooncen/releases/(?:[0-9a-f]{40}|[0-9a-f]{64})$"
)
LOCAL_ONLY_DIRECTORIES = {
    ".agents",
    ".android-tools",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "chromedriver",
    "logs",
    "node_modules",
    "ops-console",
    "tmp",
    "venv",
    "venv_clean",
}
FORBIDDEN_SNAPSHOT_SUFFIXES = {
    ".db-shm",
    ".db-wal",
    ".jks",
    ".key",
    ".mobileprovision",
    ".p12",
    ".p8",
    ".pem",
    ".pfx",
    ".pyc",
    ".sqlite",
}


@dataclass(frozen=True)
class DeployTarget:
    name: str
    server: str
    user: str
    domain: str
    remote_dir: str
    identity_file: str
    role: str
    active: bool
    deploy_profile: str = "full-stack"
    environment: str = "production"

    @property
    def identity(self) -> str:
        values = (
            ("name_b64", self.name),
            ("server_b64", self.server),
            ("user_b64", self.user),
            ("domain_b64", self.domain),
            ("remote_dir_b64", self.remote_dir),
            ("role_b64", self.role.lower()),
            ("deploy_profile_b64", self.deploy_profile.lower()),
            ("environment_b64", self.environment.lower()),
        )
        canonical = "\n".join(
            f"{key}={base64.b64encode(value.strip().encode('utf-8')).decode('ascii')}"
            for key, value in values
        )
        canonical += f"\nactive={'1' if self.active else '0'}"
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def public_dict(self, *, key_ready: bool) -> dict[str, Any]:
        return {
            "name": self.name,
            "server": self.server,
            "domain": self.domain,
            "remote_dir": self.remote_dir,
            "role": self.role,
            "deploy_profile": self.deploy_profile,
            "environment": self.environment,
            "active": self.active,
            "target_identity": self.identity,
            "key_ready": key_ready,
        }


def _bounded_text(value: Any, *, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or "\x00" in text or any(character in text for character in "\r\n"):
        raise ValueError(f"deploy target {field} is invalid")
    return text


def _validate_target(name: str, raw: Any) -> DeployTarget:
    if not TARGET_PATTERN.fullmatch(name):
        raise ValueError("deploy target name is invalid")
    if not isinstance(raw, dict):
        raise ValueError(f"deploy target {name} must be an object")
    server = _bounded_text(raw.get("server"), field="server", maximum=253)
    user = _bounded_text(raw.get("user") or "ubuntu", field="user", maximum=64)
    domain = _bounded_text(raw.get("domain") or server, field="domain", maximum=253)
    remote_dir = _bounded_text(raw.get("remoteDir") or "/opt/mooncen", field="remoteDir", maximum=225)
    identity_file = _bounded_text(raw.get("identityFile"), field="identityFile", maximum=1_024)
    role = _bounded_text(raw.get("role") or "standby", field="role", maximum=20).lower()
    deploy_profile = _bounded_text(
        raw.get("deployProfile") or "full-stack",
        field="deployProfile",
        maximum=32,
    ).lower()
    environment = _bounded_text(
        raw.get("environment") or "production",
        field="environment",
        maximum=16,
    ).lower()
    active = raw.get("active", False)

    if not HOST_PATTERN.fullmatch(server):
        raise ValueError(f"deploy target {name} server is invalid")
    if not USER_PATTERN.fullmatch(user):
        raise ValueError(f"deploy target {name} user is invalid")
    if not DOMAIN_PATTERN.fullmatch(domain):
        raise ValueError(f"deploy target {name} domain is invalid")
    if not REMOTE_DIR_PATTERN.fullmatch(remote_dir) or ".." in Path(remote_dir).parts:
        raise ValueError(f"deploy target {name} remoteDir is invalid")
    if role not in {"primary", "standby", "crawler", "crawler-control"}:
        raise ValueError(f"deploy target {name} role is invalid")
    if deploy_profile not in {"full-stack", "crawler-only", "control-only"}:
        raise ValueError(f"deploy target {name} deploy profile is invalid")
    if environment not in {"development", "staging", "production"}:
        raise ValueError(f"deploy target {name} environment is invalid")
    expected_profile = {
        "primary": "full-stack",
        "standby": "full-stack",
        "crawler": "crawler-only",
        "crawler-control": "control-only",
    }[role]
    if deploy_profile != expected_profile:
        raise ValueError(f"deploy target {name} role and deploy profile are incompatible")
    if not isinstance(active, bool):
        raise ValueError(f"deploy target {name} active must be boolean")
    if deploy_profile in {"crawler-only", "control-only"} and active:
        raise ValueError(f"deploy target {name} limited profile cannot be active")
    if deploy_profile in {"crawler-only", "control-only"} and remote_dir != "/opt/mooncen":
        raise ValueError(f"deploy target {name} limited-profile remoteDir is invalid")
    return DeployTarget(
        name=name,
        server=server,
        user=user,
        domain=domain,
        remote_dir=remote_dir,
        identity_file=identity_file,
        role=role,
        active=active,
        deploy_profile=deploy_profile,
        environment=environment,
    )


def load_deploy_targets(root: Path = PROJECT_ROOT) -> tuple[str, dict[str, DeployTarget]]:
    path = (root / DEPLOY_REGISTRY_PATH).resolve()
    expected_parent = (root / DEPLOY_REGISTRY_PATH.parent).resolve()
    if path.parent != expected_parent or not path.is_file() or path.is_symlink():
        raise ValueError("reviewed deployment target registry is unavailable")
    if path.stat().st_size > 128 * 1_024:
        raise ValueError("deployment target registry is too large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("deployment target registry is invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), dict):
        raise ValueError("deployment target registry must contain servers")
    raw_servers = payload["servers"]
    if not 1 <= len(raw_servers) <= 10:
        raise ValueError("deployment target registry size is invalid")
    targets = {
        str(name): _validate_target(str(name), raw)
        for name, raw in raw_servers.items()
    }
    default_target = str(payload.get("defaultTarget") or "").strip()
    if default_target not in targets:
        raise ValueError("deployment default target is not reviewed")
    return default_target, targets


def _expanded_identity_path(value: str) -> Path:
    expanded = os.path.expandvars(value)
    if os.name == "nt":
        expanded = re.sub(
            r"%([^%]+)%",
            lambda match: os.getenv(match.group(1), match.group(0)),
            expanded,
        )
    return Path(expanded).expanduser()


def _valid_ssh_public_key_line(raw_line: bytes) -> bool:
    parts = raw_line.strip().split(None, 2)
    if len(parts) < 2:
        return False
    try:
        key_type = parts[0].decode("ascii")
        encoded_key = parts[1].decode("ascii")
    except UnicodeDecodeError:
        return False
    if not SSH_PUBLIC_KEY_TYPE_PATTERN.fullmatch(key_type):
        return False
    try:
        key_blob = base64.b64decode(encoded_key, validate=True)
    except (ValueError, binascii.Error):
        return False
    if not 8 <= len(key_blob) <= 64 * 1_024:
        return False
    embedded_type_size = int.from_bytes(key_blob[:4], byteorder="big")
    if not 1 <= embedded_type_size <= 256:
        return False
    embedded_type_end = 4 + embedded_type_size
    if embedded_type_end >= len(key_blob):
        return False
    try:
        embedded_type = key_blob[4:embedded_type_end].decode("ascii")
    except UnicodeDecodeError:
        return False
    return embedded_type == key_type


def _ssh_agent_identity_ready() -> bool:
    if shutil.which("ssh") is None:
        return False
    ssh_add = shutil.which("ssh-add")
    if ssh_add is None:
        return False
    try:
        result = subprocess.run(
            [ssh_add, "-L"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        return False
    return any(_valid_ssh_public_key_line(line) for line in result.stdout.splitlines())


def identity_file_ready(target: DeployTarget) -> bool:
    if target.identity_file.strip().lower() == "ssh-agent":
        return _ssh_agent_identity_ready()
    path = _expanded_identity_path(target.identity_file)
    try:
        if not path.is_file() or path.is_symlink():
            return False
        with path.open("rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


def _validated_git_executable(
    value: str | os.PathLike[str],
    *,
    trusted_root: Path | None = None,
) -> str:
    raw_value = os.fspath(value).strip()
    if (
        not raw_value
        or "\x00" in raw_value
        or any(character in raw_value for character in "\r\n")
    ):
        return ""
    candidate = Path(raw_value).expanduser()
    if not candidate.is_absolute():
        return ""
    try:
        if candidate.is_symlink():
            return ""
        resolved = candidate.resolve(strict=True)
        if not resolved.is_file():
            return ""
        if trusted_root is not None:
            resolved_root = trusted_root.resolve(strict=True)
            if not resolved.is_relative_to(resolved_root):
                return ""
        if os.name == "nt":
            if resolved.suffix.lower() != ".exe":
                return ""
            with resolved.open("rb") as handle:
                if handle.read(2) != b"MZ":
                    return ""
    except (OSError, RuntimeError):
        return ""
    return str(resolved)


def git_executable() -> str:
    configured = os.getenv("MOONCEN_GIT_EXECUTABLE", "").strip()
    if configured:
        # An explicit service override is authoritative. Do not silently use a
        # different executable when the reviewed path is missing or unsafe.
        return _validated_git_executable(configured)

    path_candidate = shutil.which("git")
    if path_candidate:
        validated = _validated_git_executable(path_candidate)
        if validated:
            return validated
    if os.name != "nt":
        return ""

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if not local_app_data or "\x00" in local_app_data:
        return ""
    package_root = Path(local_app_data).expanduser() / "Microsoft" / "WinGet" / "Packages"
    candidate = (
        package_root
        / "Git.MinGit_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "cmd"
        / "git.exe"
    )
    return _validated_git_executable(candidate, trusted_root=package_root)


def _run_git(root: Path, *arguments: str) -> str:
    git = git_executable()
    if not git:
        raise ValueError("git executable is unavailable")
    try:
        completed = subprocess.run(
            [git, *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("git snapshot inspection failed") from exc
    return completed.stdout


def _run_git_bytes(
    root: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
    input_data: bytes | None = None,
) -> bytes:
    git = git_executable()
    if not git:
        raise ValueError("git executable is unavailable")
    command_environment = os.environ.copy()
    if environment:
        command_environment.update(environment)
    try:
        completed = subprocess.run(
            [git, *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            input=input_data,
            timeout=120,
            shell=False,
            env=command_environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Git deployment snapshot operation failed") from exc
    return completed.stdout


def _snapshot_path_is_forbidden(value: str) -> bool:
    normalized = value.replace("\\", "/").strip("/")
    if not normalized or "\x00" in normalized:
        return True
    parts = tuple(part for part in normalized.split("/") if part)
    lowered_parts = tuple(part.lower() for part in parts)
    lowered = normalized.lower()
    name = lowered_parts[-1]
    if any(part in LOCAL_ONLY_DIRECTORIES for part in lowered_parts):
        return True
    if lowered in {
        ".env",
        "config/deploy_servers.json",
        "deploy.local.ps1",
    }:
        return True
    if lowered.startswith("deploy/an2p/"):
        return True
    if name.startswith(".env.") and name != ".env.example":
        return True
    if name.startswith("deploy.local.") and name != "deploy.local.example.ps1":
        return True
    if name.endswith(tuple(FORBIDDEN_SNAPSHOT_SUFFIXES)):
        return True
    if name.endswith(".dump") or ".dump." in name:
        return True
    if lowered_parts[0].startswith(".pytest_tmp"):
        return True
    return False


def deploy_tree_snapshot(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    base_commit = _run_git(root, "rev-parse", "--verify", "HEAD^{commit}").strip().lower()
    if not COMMIT_PATTERN.fullmatch(base_commit):
        raise ValueError("git returned an invalid deployment base commit")

    with tempfile.TemporaryDirectory(prefix="mooncen-deploy-index-") as temporary:
        index_path = Path(temporary) / "index"
        index_environment = {"GIT_INDEX_FILE": str(index_path)}
        _run_git_bytes(root, "read-tree", base_commit, environment=index_environment)
        candidate_records = (
            _run_git_bytes(root, "ls-tree", "-r", "--name-only", "-z", base_commit)
            + _run_git_bytes(root, "ls-files", "-z")
            + _run_git_bytes(
                root,
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            )
        )
        candidate_paths = sorted(
            {
                item.decode("utf-8", errors="surrogateescape")
                for item in candidate_records.split(b"\0")
                if item
            }
        )
        excluded_paths = [
            path for path in candidate_paths if _snapshot_path_is_forbidden(path)
        ]
        safe_paths = [
            path for path in candidate_paths if not _snapshot_path_is_forbidden(path)
        ]
        for offset in range(0, len(safe_paths), 100):
            _run_git_bytes(
                root,
                "add",
                "-A",
                "--",
                *safe_paths[offset : offset + 100],
                environment=index_environment,
            )

        if excluded_paths:
            encoded_paths = b"".join(
                path.encode("utf-8", errors="surrogateescape") + b"\0"
                for path in excluded_paths
            )
            _run_git_bytes(
                root,
                "update-index",
                "--force-remove",
                "-z",
                "--stdin",
                environment=index_environment,
                input_data=encoded_paths,
            )

        staged_records = [
            item
            for item in _run_git_bytes(
                root,
                "ls-files",
                "--stage",
                "-z",
                environment=index_environment,
            ).split(b"\0")
            if item
        ]
        for record in staged_records:
            mode = record.split(b" ", 1)[0]
            if mode in {b"120000", b"160000"}:
                raise ValueError(
                    "deployment snapshot contains a symbolic link or submodule"
                )
        tree = _run_git_bytes(
            root,
            "write-tree",
            environment=index_environment,
        ).decode("ascii").strip().lower()
    if not COMMIT_PATTERN.fullmatch(tree):
        raise ValueError("git returned an invalid deployment tree")
    return {
        "source_tree": tree,
        "short_source_tree": tree[:12],
        "deploy_path_count": len(staged_records),
        "excluded_count": len(excluded_paths),
        "excluded_paths": excluded_paths[:30],
        "excluded_paths_truncated": len(excluded_paths) > 30,
    }


def create_deployment_snapshot_commit(
    *,
    expected_base_commit: str,
    expected_source_tree: str,
    reference: str,
    root: Path = PROJECT_ROOT,
) -> str:
    if not COMMIT_PATTERN.fullmatch(expected_base_commit):
        raise ValueError("deployment base commit is invalid")
    if not COMMIT_PATTERN.fullmatch(expected_source_tree):
        raise ValueError("deployment source tree is invalid")
    if not SNAPSHOT_REF_PATTERN.fullmatch(reference):
        raise ValueError("deployment snapshot reference is invalid")

    current = git_snapshot(root)
    if current["commit"] != expected_base_commit:
        raise ValueError("Git HEAD changed after the deployment plan was reviewed")
    if current["source_tree"] != expected_source_tree:
        raise ValueError(
            "development files changed after the deployment plan was reviewed"
        )

    commit_environment = {
        "GIT_AUTHOR_NAME": "MoonCen Ops",
        "GIT_AUTHOR_EMAIL": "ops@localhost",
        "GIT_COMMITTER_NAME": "MoonCen Ops",
        "GIT_COMMITTER_EMAIL": "ops@localhost",
    }
    commit = _run_git_bytes(
        root,
        "commit-tree",
        expected_source_tree,
        "-p",
        expected_base_commit,
        environment=commit_environment,
        input_data=b"MoonCen reviewed development snapshot\n",
    ).decode("ascii").strip().lower()
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("git returned an invalid deployment snapshot commit")
    _run_git_bytes(root, "update-ref", reference, commit)
    return commit


def release_deployment_snapshot_reference(
    reference: str,
    commit: str,
    root: Path = PROJECT_ROOT,
) -> None:
    if not SNAPSHOT_REF_PATTERN.fullmatch(reference):
        return
    if not COMMIT_PATTERN.fullmatch(commit):
        return
    try:
        _run_git_bytes(root, "update-ref", "-d", reference, commit)
    except ValueError:
        pass


def preserve_deployment_release_reference(
    *,
    commit: str,
    source_tree: str,
    base_commit: str,
    job_id: str,
    status: str = "activated",
    root: Path = PROJECT_ROOT,
) -> Path:
    """Permanently retain a successfully activated synthetic release commit.

    The queue-specific snapshot ref remains disposable.  This release ref and
    its atomic local manifest are intentionally stable so an operator can
    inspect or re-archive the exact deployed tree after the queue job ends.
    """

    normalized_commit = str(commit).strip().lower()
    normalized_tree = str(source_tree).strip().lower()
    normalized_base = str(base_commit).strip().lower()
    normalized_job_id = str(job_id).strip().lower()
    normalized_status = str(status).strip().lower()
    if not COMMIT_PATTERN.fullmatch(normalized_commit):
        raise ValueError("deployment release commit is invalid")
    if not COMMIT_PATTERN.fullmatch(normalized_tree):
        raise ValueError("deployment release tree is invalid")
    if not COMMIT_PATTERN.fullmatch(normalized_base):
        raise ValueError("deployment release base commit is invalid")
    try:
        if str(UUID(normalized_job_id)) != normalized_job_id:
            raise ValueError
    except ValueError as exc:
        raise ValueError("deployment release job identifier is invalid") from exc
    if normalized_status not in {"deploying", "activated"}:
        raise ValueError("deployment release manifest status is invalid")

    resolved_commit = _run_git_bytes(
        root,
        "rev-parse",
        "--verify",
        f"{normalized_commit}^{{commit}}",
    ).decode("ascii").strip().lower()
    resolved_tree = _run_git_bytes(
        root,
        "rev-parse",
        "--verify",
        f"{normalized_commit}^{{tree}}",
    ).decode("ascii").strip().lower()
    resolved_parent = _run_git_bytes(
        root,
        "rev-parse",
        "--verify",
        f"{normalized_commit}^1",
    ).decode("ascii").strip().lower()
    if resolved_commit != normalized_commit:
        raise ValueError("deployment release commit no longer resolves exactly")
    if resolved_tree != normalized_tree:
        raise ValueError("deployment release tree does not match the reviewed tree")
    if resolved_parent != normalized_base:
        raise ValueError("deployment release parent does not match the reviewed base")

    release_reference = f"refs/mooncen/releases/{normalized_commit}"
    if not RELEASE_REF_PATTERN.fullmatch(release_reference):
        raise ValueError("deployment release reference is invalid")
    _run_git_bytes(root, "update-ref", release_reference, normalized_commit)

    manifest_directory = (root / DEPLOYMENT_RELEASE_MANIFEST_DIR).resolve()
    expected_parent = (root / DEPLOYMENT_RELEASE_MANIFEST_DIR.parent).resolve()
    if manifest_directory.parent != expected_parent or manifest_directory.is_symlink():
        raise ValueError("deployment release manifest directory is unsafe")
    manifest_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_directory / f"{normalized_commit}-{normalized_job_id}.json"
    payload = {
        "schema_version": 1,
        "release_ref": release_reference,
        "commit": normalized_commit,
        "source_tree": normalized_tree,
        "base_commit": normalized_base,
        "job_id": normalized_job_id,
        "status": normalized_status,
        "preserved_at_epoch": int(time.time()),
    }
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{normalized_commit}.",
            suffix=".tmp",
            dir=manifest_directory,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary_path, manifest_path)
        temporary_path = None
    except OSError as exc:
        raise ValueError(
            "deployment release ref was retained, but its local manifest could not be written"
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
    return manifest_path


def _porcelain_paths(raw: str) -> list[str]:
    records = raw.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            raise ValueError("git status output is invalid")
        status_code = record[:2]
        paths.append(record[3:])
        if "R" in status_code or "C" in status_code:
            if index >= len(records) or not records[index]:
                raise ValueError("git rename status output is invalid")
            index += 1
    return paths


def git_snapshot(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    commit = _run_git(root, "rev-parse", "--verify", "HEAD^{commit}").strip().lower()
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("git returned an invalid deployment commit")
    branch = _run_git(root, "branch", "--show-current").strip() or "detached"
    if len(branch) > 200 or "\x00" in branch:
        raise ValueError("git branch name is invalid")
    changed_paths = _porcelain_paths(
        _run_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    )
    snapshot = {
        "branch": branch,
        "commit": commit,
        "short_commit": commit[:12],
        "clean": not changed_paths,
        "changed_count": len(changed_paths),
        "changed_paths": changed_paths[:30],
        "changed_paths_truncated": len(changed_paths) > 30,
    }
    snapshot.update(deploy_tree_snapshot(root))
    return snapshot


def _validated_powershell_executable(value: str | os.PathLike[str]) -> str:
    """Return a reviewed PowerShell host path or fail closed.

    The deployment worker executes a security-sensitive release state machine.
    On POSIX, accepting a PATH entry below a group/world-writable directory
    would let another local account replace that state machine's interpreter.
    The operator account may own its pinned portable runtime because it already
    owns the reviewed worktree and deployment key, but no other account may
    write the executable or any parent directory.
    """

    raw_value = os.fspath(value).strip()
    if (
        not raw_value
        or "\x00" in raw_value
        or any(character in raw_value for character in "\r\n")
    ):
        return ""
    candidate = Path(raw_value).expanduser()
    if not candidate.is_absolute():
        return ""
    try:
        if candidate.is_symlink():
            return ""
        resolved = candidate.resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return ""
        if os.name == "nt":
            if resolved.suffix.lower() not in {".exe", ""}:
                return ""
            with resolved.open("rb") as handle:
                if handle.read(2) != b"MZ":
                    return ""
        else:
            # systemd --user services with PrivateTmp use a mount/user
            # namespace where root-owned parent directories can appear as the
            # namespace overflow UID. Trust the observed owner of `/` rather
            # than hard-coding 0, while still requiring every component to be
            # non-writable by group/other.
            allowed_owners = {Path("/").stat().st_uid, os.geteuid()}
            current = resolved
            while True:
                metadata = current.stat()
                if metadata.st_uid not in allowed_owners or metadata.st_mode & 0o022:
                    return ""
                if current.parent == current:
                    break
                current = current.parent
    except (OSError, RuntimeError):
        return ""
    return str(resolved)


def powershell_executable() -> str:
    configured = os.getenv("MOONCEN_POWERSHELL_EXECUTABLE", "").strip()
    if configured:
        # An explicit service path is authoritative. Never fall back to a
        # different interpreter when the reviewed runtime disappears or drifts.
        return _validated_powershell_executable(configured)

    names = ("powershell.exe", "powershell") if os.name == "nt" else ("pwsh",)
    for name in names:
        candidate = shutil.which(name)
        if candidate:
            validated = _validated_powershell_executable(candidate)
            if validated:
                return validated
    return ""


def deployment_worker_heartbeat_ready(
    root: Path = DEPLOYMENT_WORKER_STATE_ROOT,
    *,
    maximum_age_seconds: int = 15,
) -> bool:
    path = root / DEPLOYMENT_WORKER_HEARTBEAT_PATH
    try:
        root_metadata = root.stat()
        if (
            root.is_symlink()
            or not root.is_dir()
            or (os.name != "nt" and root_metadata.st_uid != os.geteuid())
            or root_metadata.st_mode & 0o077
            or not path.is_file()
            or path.is_symlink()
        ):
            return False
        metadata = path.stat()
        if (
            (os.name != "nt" and metadata.st_uid != os.geteuid())
            or metadata.st_mode & 0o077
            or metadata.st_size > 4_096
        ):
            return False
        age = time.time() - metadata.st_mtime
        if age < -5 or age > maximum_age_seconds:
            return False
        payload = json.loads(path.read_text(encoding="ascii"))
        pid = payload.get("pid") if isinstance(payload, dict) else None
        return isinstance(pid, int) and pid > 0
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def load_deployment_hold(root: Path = PROJECT_ROOT) -> dict[str, str] | None:
    """Return a reviewed local release hold without exposing secret material."""

    candidate = root / DEPLOYMENT_HOLD_PATH
    expected_parent = (root / DEPLOYMENT_HOLD_PATH.parent).resolve()
    try:
        if candidate.parent.resolve() != expected_parent:
            raise ValueError("deployment hold path is outside the local state directory")
        if candidate.is_symlink():
            raise ValueError("deployment hold file is unsafe")
        if not candidate.exists():
            return None
        path = candidate.resolve(strict=True)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 8 * 1_024:
            raise ValueError("deployment hold file is unsafe")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("deployment hold file is invalid") from exc
    if not isinstance(payload, dict) or payload.get("active") is not True:
        raise ValueError("deployment hold must be an active object")
    code = str(payload.get("code") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not HOLD_CODE_PATTERN.fullmatch(code):
        raise ValueError("deployment hold code is invalid")
    if not message or len(message) > 300 or "\x00" in message or any(
        character in message for character in "\r\n"
    ):
        raise ValueError("deployment hold message is invalid")
    return {"code": code, "message": message}


def deployment_readiness(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    reasons: list[dict[str, str]] = []
    try:
        default_target, targets = load_deploy_targets(root)
    except ValueError as exc:
        return {
            "available": False,
            "can_deploy": False,
            "default_target": None,
            "targets": [],
            "snapshot": None,
            "reasons": [{"code": "target_registry_invalid", "message": str(exc)}],
            "topology": None,
        }

    topology = None
    topology_ready = True
    try:
        topology = load_production_topology(root)
    except ValueError as exc:
        topology_ready = False
        reasons.append(
            {
                "code": "production_topology_invalid",
                "message": str(exc),
            }
        )

    hold_ready = True
    try:
        deployment_hold = load_deployment_hold(root)
    except ValueError as exc:
        deployment_hold = None
        hold_ready = False
        reasons.append(
            {
                "code": "deployment_hold_invalid",
                "message": str(exc),
            }
        )
    if deployment_hold is not None:
        reasons.append(deployment_hold)

    try:
        snapshot = git_snapshot(root)
    except ValueError as exc:
        snapshot = None
        reasons.append({"code": "git_snapshot_invalid", "message": str(exc)})

    script_ready = (root / DEPLOY_SCRIPT_PATH).is_file()
    local_config_ready = (root / DEPLOY_LOCAL_PATH).is_file()
    powershell_ready = bool(powershell_executable())
    if not script_ready:
        reasons.append({"code": "deploy_script_missing", "message": "배포 스크립트를 찾을 수 없습니다."})
    if not local_config_ready:
        reasons.append({"code": "deploy_config_missing", "message": "로컬 배포 설정이 없습니다."})
    if not powershell_ready:
        reasons.append({"code": "powershell_missing", "message": "검증된 PowerShell 배포 실행 환경이 없습니다."})
    public_targets = []
    for target in targets.values():
        key_ready = identity_file_ready(target)
        public_target = target.public_dict(key_ready=key_ready)
        placements: list[dict[str, str | None]] = []
        if topology is not None:
            topology_node = topology.nodes.get(target.name)
            if topology_node is None or topology_node.dns_host != target.server:
                topology_ready = False
                reasons.append(
                    {
                        "code": f"topology_target_mismatch:{target.name}",
                        "message": f"{target.name} 배포 호스트와 운영 topology가 일치하지 않습니다.",
                    }
                )
            else:
                for service, service_placements in topology.services.items():
                    placements.extend(
                        {
                            "service": service,
                            "role": placement.role,
                            "replicates_from": placement.replicates_from,
                        }
                        for placement in service_placements
                        if placement.node == target.name
                    )
                expected_contracts: set[tuple[str, str]] = set()
                database_placement = next(
                    (
                        placement
                        for placement in topology.services.get("database", ())
                        if placement.node == target.name
                    ),
                    None,
                )
                if database_placement is not None:
                    expected_contracts.add(
                        (database_placement.role, "full-stack")
                    )
                for service, contract in (
                    ("crawler", ("crawler", "crawler-only")),
                    ("crawler_control", ("crawler-control", "control-only")),
                ):
                    primary = next(
                        (
                            placement
                            for placement in topology.services.get(service, ())
                            if placement.role == "primary"
                        ),
                        None,
                    )
                    if primary is not None and primary.node == target.name:
                        expected_contracts.add(contract)
                expected_active = target.name == topology.active_node
                target_contract = (target.role, target.deploy_profile)
                if (
                    target.active != expected_active
                    or len(expected_contracts) != 1
                    or target_contract not in expected_contracts
                ):
                    topology_ready = False
                    reasons.append(
                        {
                            "code": f"topology_role_mismatch:{target.name}",
                            "message": f"{target.name} 배포 역할과 운영 topology가 일치하지 않습니다.",
                        }
                    )
        public_target["services"] = placements
        public_targets.append(public_target)
        if not key_ready:
            reasons.append(
                {
                    "code": f"identity_unavailable:{target.name}",
                    "message": f"{target.name} 배포 키를 읽을 수 없습니다.",
                }
            )

    runtime_ready = script_ready and local_config_ready and powershell_ready and topology_ready
    return {
        "available": runtime_ready,
        "can_deploy": bool(runtime_ready and snapshot and hold_ready and deployment_hold is None),
        "default_target": default_target,
        "targets": public_targets,
        "snapshot": snapshot,
        "reasons": reasons,
        "topology": topology.public_payload() if topology is not None else None,
        "deployment_hold": deployment_hold,
    }


def reviewed_target(name: str, root: Path = PROJECT_ROOT) -> DeployTarget:
    _default_target, targets = load_deploy_targets(root)
    target = targets.get(name)
    if target is None:
        raise ValueError("deployment target is not in the reviewed registry")
    return target
