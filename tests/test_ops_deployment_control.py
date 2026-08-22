from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from backend.main import app
from backend.ops.schemas import DeploymentRequest
from ops_agent import deployment_registry, deployment_worker
from ops_agent.deployment_registry import (
    DEPLOYMENT_WORKER_HEARTBEAT_PATH,
    DeployTarget,
    create_deployment_snapshot_commit,
    deploy_tree_snapshot,
    deployment_worker_heartbeat_ready,
    preserve_deployment_release_reference,
    release_deployment_snapshot_reference,
)


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40
TREE = "2" * 40
SOURCE_COMMIT = "3" * 40
JOB_ID = "11111111-1111-4111-8111-111111111111"
AGENT_ID = "22222222-2222-4222-8222-222222222222"
LEASE_TOKEN = "33333333-3333-4333-8333-333333333333"


def _leased_deployment_job(*, parameters: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": JOB_ID,
        "agent_id": AGENT_ID,
        "lease_token": LEASE_TOKEN,
        "lease_epoch": 9,
        "_lease_seconds": 300,
        "parameters": parameters or {},
    }


def _target() -> DeployTarget:
    return DeployTarget(
        name="n100",
        server="n100",
        user="sgm",
        domain="mooncen.kr",
        remote_dir="/opt/mooncen",
        identity_file="unused-in-test",
        role="standby",
        active=False,
    )


def test_ssh_agent_identity_rejects_ssh_without_ssh_add(monkeypatch) -> None:
    target = DeployTarget(
        name="cloud",
        server="cloud",
        user="ubuntu",
        domain="mooncen.kr",
        remote_dir="/opt/mooncen",
        identity_file="ssh-agent",
        role="primary",
        active=True,
    )
    monkeypatch.setattr(deployment_registry.shutil, "which", lambda name: "ssh.exe" if name == "ssh" else None)

    assert deployment_registry.identity_file_ready(target) is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX PowerShell trust contract")
def test_posix_powershell_runtime_is_explicit_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = Path(tempfile.mkdtemp(prefix=".pytest_tmp_pwsh-", dir=ROOT))
    runtime_root.chmod(0o700)
    executable = runtime_root / "pwsh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    try:
        monkeypatch.setenv("MOONCEN_POWERSHELL_EXECUTABLE", str(executable))
        monkeypatch.setattr(
            deployment_registry.shutil,
            "which",
            lambda _name: "/bin/false",
        )

        assert deployment_registry.powershell_executable() == str(executable.resolve())

        executable.chmod(0o722)
        assert deployment_registry.powershell_executable() == ""

        monkeypatch.setenv("MOONCEN_POWERSHELL_EXECUTABLE", "relative/pwsh")
        assert deployment_registry.powershell_executable() == ""
    finally:
        shutil.rmtree(runtime_root)


@pytest.mark.skipif(os.name != "nt", reason="Windows Git discovery contract")
def test_git_locator_uses_validated_localappdata_winget_mingit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = (
        tmp_path
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Git.MinGit_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "cmd"
        / "git.exe"
    )
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"MZ\x00\x00reviewed-test-executable")
    monkeypatch.delenv("MOONCEN_GIT_EXECUTABLE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(deployment_registry.shutil, "which", lambda _name: None)

    assert deployment_registry.git_executable() == str(candidate.resolve())


@pytest.mark.skipif(os.name != "nt", reason="Windows Git discovery contract")
def test_git_locator_fails_closed_for_invalid_explicit_or_winget_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "configured-git.exe"
    configured.write_bytes(b"not-a-windows-executable")
    monkeypatch.setenv("MOONCEN_GIT_EXECUTABLE", str(configured))
    monkeypatch.setattr(
        deployment_registry.shutil,
        "which",
        lambda _name: str(tmp_path / "ignored-path-git.exe"),
    )

    assert deployment_registry.git_executable() == ""

    monkeypatch.delenv("MOONCEN_GIT_EXECUTABLE")
    candidate = (
        tmp_path
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Git.MinGit_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "cmd"
        / "git.exe"
    )
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"invalid")
    monkeypatch.setattr(deployment_registry.shutil, "which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert deployment_registry.git_executable() == ""


def test_deployment_request_requires_exact_typed_confirmation() -> None:
    request = DeploymentRequest(
        target="n100",
        target_commit=COMMIT,
        source_tree=TREE,
        confirmation=f"DEPLOY n100 {TREE[:12]}",
    )

    assert request.target == "n100"
    assert request.target_commit == COMMIT

    with pytest.raises(ValidationError, match="confirmation"):
        DeploymentRequest(
            target="n100",
            target_commit=COMMIT,
            source_tree=TREE,
            confirmation="DEPLOY cloud",
        )


def test_deployment_worker_builds_only_reviewed_powershell_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target()
    monkeypatch.setattr(
        deployment_worker,
        "reviewed_target",
        lambda name, _root: target if name == target.name else (_ for _ in ()).throw(
            ValueError("deployment target is not in the reviewed registry")
        ),
    )
    monkeypatch.setattr(deployment_worker, "identity_file_ready", lambda _target: True)
    monkeypatch.setattr(deployment_worker, "powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(
        deployment_worker, "normalized_environment", lambda: "production"
    )
    readiness = {
        "available": True,
        "can_deploy": True,
        "snapshot": {"clean": False, "commit": COMMIT, "source_tree": TREE},
    }

    command = deployment_worker.build_deployment_command(
        {
            "action": "deploy",
            "target": "n100",
            "target_commit": COMMIT,
            "target_identity": target.identity,
            "service_type": "full",
            "skip_workers": True,
            "source_tree": TREE,
        },
        source_commit=SOURCE_COMMIT,
        root=ROOT,
        readiness=readiness,
    )

    assert command[:5] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
    ]
    assert command[command.index("-Target") + 1] == "n100"
    assert command[command.index("-ExpectedCommit") + 1] == COMMIT
    assert command[command.index("-SourceCommit") + 1] == SOURCE_COMMIT
    assert command[command.index("-ExpectedSourceTree") + 1] == TREE
    assert command[command.index("-ExpectedTargetIdentity") + 1] == target.identity
    assert "-SkipWorkers" in command

    with pytest.raises(ValueError, match="reviewed registry"):
        deployment_worker.build_deployment_command(
            {
                "action": "deploy",
                "target": "n100; Remove-Item C:\\",
                "target_commit": COMMIT,
                "target_identity": target.identity,
                "service_type": "full",
                "skip_workers": False,
                "source_tree": TREE,
            },
            source_commit=SOURCE_COMMIT,
            root=ROOT,
            readiness=readiness,
        )


def test_deployment_worker_accepts_dirty_tree_and_rejects_changed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target()
    monkeypatch.setattr(deployment_worker, "reviewed_target", lambda _name, _root: target)
    monkeypatch.setattr(deployment_worker, "identity_file_ready", lambda _target: True)
    monkeypatch.setattr(
        deployment_worker, "normalized_environment", lambda: "production"
    )
    parameters = {
        "action": "deploy",
        "target": "n100",
        "target_commit": COMMIT,
        "target_identity": target.identity,
        "service_type": "full",
        "skip_workers": False,
        "source_tree": TREE,
    }

    validated = deployment_worker.validated_parameters(
        parameters,
        root=ROOT,
        readiness={
            "available": True,
            "can_deploy": True,
            "snapshot": {
                "clean": False,
                "commit": COMMIT,
                "source_tree": TREE,
            },
        },
    )
    assert validated["source_tree"] == TREE

    with pytest.raises(ValueError, match="different executor host"):
        deployment_worker.validated_parameters(
            {**parameters, "required_agent_hostname": "gen1win"},
            root=ROOT,
            readiness={
                "available": True,
                "can_deploy": True,
                "snapshot": {"commit": COMMIT, "source_tree": TREE},
            },
        )

    with pytest.raises(ValueError, match="deployment runtime is unavailable"):
        deployment_worker.validated_parameters(
            parameters,
            root=ROOT,
            readiness={
                "available": True,
                "can_deploy": False,
                "snapshot": {
                    "clean": False,
                    "commit": COMMIT,
                    "source_tree": TREE,
                },
            },
        )

    with pytest.raises(ValueError, match="Git HEAD changed"):
        deployment_worker.validated_parameters(
            parameters,
            root=ROOT,
            readiness={
                "available": True,
                "can_deploy": True,
                "snapshot": {
                    "clean": False,
                    "commit": "4" * 40,
                    "source_tree": TREE,
                },
            },
        )
    with pytest.raises(ValueError, match="development files changed"):
        deployment_worker.validated_parameters(
            parameters,
            root=ROOT,
            readiness={
                "available": True,
                "can_deploy": True,
                "snapshot": {
                    "clean": False,
                    "commit": COMMIT,
                    "source_tree": "5" * 40,
                },
            },
        )


@pytest.mark.skipif(
    not deployment_registry.git_executable(), reason="validated git unavailable"
)
def test_development_snapshot_preserves_head_and_index_and_excludes_local_files(
    tmp_path: Path,
) -> None:
    git_executable = deployment_registry.git_executable()

    def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git_executable, *arguments],
            cwd=tmp_path,
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    git("init")
    git("config", "user.name", "Snapshot Test")
    git("config", "user.email", "snapshot@example.test")
    (tmp_path / ".gitignore").write_text("logs/\n", encoding="utf-8")
    (tmp_path / "app.txt").write_text("committed\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    base_commit = git("rev-parse", "HEAD").stdout.strip()
    index_before = (tmp_path / ".git" / "index").read_bytes()

    (tmp_path / "app.txt").write_text("development\n", encoding="utf-8")
    (tmp_path / "new_source.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=never-package\n", encoding="utf-8")
    (tmp_path / "ops-console").mkdir()
    (tmp_path / "ops-console" / "local.ts").write_text("local\n", encoding="utf-8")
    (tmp_path / "deploy" / "an2p").mkdir(parents=True)
    (tmp_path / "deploy" / "an2p" / "local.service").write_text(
        "local\n", encoding="utf-8"
    )

    snapshot = deploy_tree_snapshot(tmp_path)
    reference = "refs/mooncen/deploy-snapshots/11111111-1111-4111-8111-111111111111"
    source_commit = create_deployment_snapshot_commit(
        expected_base_commit=base_commit,
        expected_source_tree=snapshot["source_tree"],
        reference=reference,
        root=tmp_path,
    )

    assert git("rev-parse", "HEAD").stdout.strip() == base_commit
    assert (tmp_path / ".git" / "index").read_bytes() == index_before
    assert git("show", f"{source_commit}:app.txt").stdout == "development\n"
    assert git("show", f"{source_commit}:new_source.py").stdout == "VALUE = 1\n"
    assert git("cat-file", "-e", f"{source_commit}:.env", check=False).returncode != 0
    assert (
        git("cat-file", "-e", f"{source_commit}:ops-console/local.ts", check=False).returncode
        != 0
    )
    secret_blob = git("hash-object", ".env").stdout.strip()
    assert git("cat-file", "-e", secret_blob, check=False).returncode != 0
    archive = tmp_path / "snapshot.tar.gz"
    git("archive", "--format=tar.gz", f"--output={archive}", source_commit)
    with tarfile.open(archive, "r:gz") as bundle:
        names = set(bundle.getnames())
    assert {"app.txt", "new_source.py"} <= names
    assert ".env" not in names
    assert "ops-console/local.ts" not in names
    assert "deploy/an2p/local.service" not in names

    manifest = preserve_deployment_release_reference(
        commit=source_commit,
        source_tree=snapshot["source_tree"],
        base_commit=base_commit,
        job_id="11111111-1111-4111-8111-111111111111",
        status="deploying",
        root=tmp_path,
    )
    assert json.loads(manifest.read_text(encoding="utf-8"))["status"] == "deploying"
    activated_manifest = preserve_deployment_release_reference(
        commit=source_commit,
        source_tree=snapshot["source_tree"],
        base_commit=base_commit,
        job_id="11111111-1111-4111-8111-111111111111",
        status="activated",
        root=tmp_path,
    )
    assert activated_manifest == manifest
    release_deployment_snapshot_reference(reference, source_commit, tmp_path)
    assert git("show-ref", "--verify", reference, check=False).returncode != 0
    release_reference = f"refs/mooncen/releases/{source_commit}"
    assert git("show-ref", "--verify", release_reference).stdout.split()[0] == source_commit
    release_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    assert release_manifest["release_ref"] == release_reference
    assert release_manifest["commit"] == source_commit
    assert release_manifest["source_tree"] == snapshot["source_tree"]
    assert release_manifest["base_commit"] == base_commit
    assert release_manifest["status"] == "activated"


def test_deployment_worker_capability_requires_a_fresh_heartbeat(
    tmp_path: Path,
) -> None:
    heartbeat = tmp_path / DEPLOYMENT_WORKER_HEARTBEAT_PATH
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.write_text(json.dumps({"pid": 1234}), encoding="ascii")
    heartbeat.chmod(0o600)

    assert deployment_worker_heartbeat_ready(tmp_path) is True

    stale = time.time() - 60
    os.utime(heartbeat, (stale, stale))
    assert deployment_worker_heartbeat_ready(tmp_path) is False


def test_deployment_worker_registers_and_refreshes_its_queue_agent(monkeypatch) -> None:
    agent_id = UUID("11111111-1111-4111-8111-111111111111")
    monkeypatch.setenv("OPS_DEPLOY_AGENT_EXCLUSIVE", "true")
    monkeypatch.setattr(
        deployment_worker,
        "_deployment_agent_capabilities",
        lambda: ["deployment_queue", "container_deployment"],
    )

    class Cursor:
        rowcount = 1
        statements: list[tuple[str, object]] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement: str, parameters: object) -> None:
            self.statements.append((statement, parameters))

        def fetchone(self):
            return (agent_id,)

    class Connection:
        commits = 0
        cursor_instance = Cursor()

        def cursor(self):
            return self.cursor_instance

        def commit(self) -> None:
            self.commits += 1

    connection = Connection()

    registered = deployment_worker._register_deployment_agent(connection, "production")
    deployment_worker._touch_deployment_agent(connection, registered, "production")

    assert registered == agent_id
    assert connection.commits == 2
    statements = "\n".join(statement for statement, _ in connection.cursor_instance.statements)
    assert "INSERT INTO ops_agents" in statements
    assert "UPDATE ops_agents" in statements
    assert "capabilities = EXCLUDED.capabilities" in statements
    assert "capabilities = %(capability)s" in statements
    registration_parameters = connection.cursor_instance.statements[0][1]
    assert isinstance(registration_parameters, dict)
    capability = registration_parameters["capability"]
    assert getattr(capability, "adapted", None) == [
        "deployment_queue",
        "container_deployment",
    ]


def test_container_worker_capability_does_not_require_legacy_powershell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deployment_worker.socket, "gethostname", lambda: "an2p")
    monkeypatch.setattr(
        deployment_worker,
        "container_worker_service_boundary_ready",
        lambda: True,
    )
    monkeypatch.setenv("MOONCEN_POWERSHELL_EXECUTABLE", "/missing/legacy/pwsh")
    monkeypatch.setattr(
        deployment_worker,
        "powershell_executable",
        lambda: pytest.fail("container capability must not inspect PowerShell"),
    )

    assert deployment_worker._deployment_agent_capabilities() == [
        "deployment_queue",
        "container_deployment",
    ]


def test_deployment_subprocess_does_not_inherit_queue_secrets(monkeypatch) -> None:
    for name in deployment_worker.DEPLOYMENT_SUBPROCESS_SECRET_NAMES:
        monkeypatch.setenv(name, f"secret-for-{name.lower()}")
    monkeypatch.setenv("OPENAI_API_KEY", "generic-api-secret")
    monkeypatch.setenv("VENDOR_PRIVATE_KEY", "generic-private-secret")
    monkeypatch.setenv("PATH", "safe-path-value")

    environment = deployment_worker._deployment_subprocess_environment()

    assert environment["PATH"] == "safe-path-value"
    assert deployment_worker.DEPLOYMENT_SUBPROCESS_SECRET_NAMES.isdisjoint(environment)
    assert "OPENAI_API_KEY" not in environment
    assert "VENDOR_PRIVATE_KEY" not in environment


def test_deployment_runtime_is_private_bounded_and_exported(
    tmp_path: Path,
) -> None:
    job_id = "11111111-1111-4111-8111-111111111111"
    runtime = deployment_worker._create_deployment_runtime_directory(
        job_id,
        root=tmp_path,
    )
    try:
        assert runtime.parent == (
            tmp_path / deployment_worker.DEPLOYMENT_RUNTIME_DIRECTORY
        ).resolve()
        assert runtime.stat().st_mode & 0o077 == 0
        environment = deployment_worker._deployment_subprocess_environment(
            runtime_directory=runtime,
        )
        for name in ("MOONCEN_DEPLOY_TEMP_ROOT", "TEMP", "TMP", "TMPDIR"):
            assert environment[name] == str(runtime)

        with pytest.raises(OSError, match="already exists"):
            deployment_worker._create_deployment_runtime_directory(
                job_id,
                root=tmp_path,
            )
    finally:
        deployment_worker._remove_deployment_runtime_directory(
            runtime,
            root=tmp_path,
        )
    assert not runtime.exists()


@pytest.mark.parametrize(
    "missing_name",
    (
        "OPS_DEPLOY_QUEUE_DB_HOST",
        "OPS_DEPLOY_QUEUE_DB_PORT",
        "OPS_DEPLOY_QUEUE_DB_NAME",
        "OPS_DEPLOY_QUEUE_DB_USER",
        "OPS_DEPLOY_QUEUE_DB_PASSWORD",
    ),
)
def test_production_deployment_queue_never_falls_back_to_shared_database_settings(
    monkeypatch: pytest.MonkeyPatch,
    missing_name: str,
) -> None:
    dedicated = {
        "OPS_DEPLOY_QUEUE_DB_HOST": "127.0.0.1",
        "OPS_DEPLOY_QUEUE_DB_PORT": "15432",
        "OPS_DEPLOY_QUEUE_DB_NAME": "mooncen",
        "OPS_DEPLOY_QUEUE_DB_USER": "mooncen_deployment_worker",
        "OPS_DEPLOY_QUEUE_DB_PASSWORD": "dedicated-secret",
    }
    monkeypatch.setenv("ENVIRONMENT", "production")
    for name, value in dedicated.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing_name)
    for prefix in ("OPS_QUEUE_DB", "DB"):
        monkeypatch.setenv(f"{prefix}_HOST", "legacy-db.internal")
        monkeypatch.setenv(f"{prefix}_PORT", "5432")
        monkeypatch.setenv(f"{prefix}_NAME", "legacy_mooncen")
    monkeypatch.setenv("OPS_QUEUE_DB_USER", "legacy_queue_user")
    monkeypatch.setenv("OPS_QUEUE_DB_PASSWORD", "legacy-queue-secret")
    monkeypatch.setenv("DB_CRAWLER_USER", "legacy_crawler")
    monkeypatch.setenv("DB_CRAWLER_PASSWORD", "legacy-crawler-secret")

    with pytest.raises(
        RuntimeError,
        match=(
            "requires explicit queue database credentials via "
            "OPS_DEPLOY_QUEUE_DB_HOST/PORT/NAME/USER/PASSWORD"
        ),
    ):
        deployment_worker.queue_database_config()


def test_production_deployment_queue_uses_only_dedicated_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("OPS_DEPLOY_QUEUE_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("OPS_DEPLOY_QUEUE_DB_PORT", "15432")
    monkeypatch.setenv("OPS_DEPLOY_QUEUE_DB_NAME", "mooncen_control")
    monkeypatch.setenv(
        "OPS_DEPLOY_QUEUE_DB_USER",
        "mooncen_deployment_worker",
    )
    monkeypatch.setenv("OPS_DEPLOY_QUEUE_DB_PASSWORD", "dedicated-secret")
    monkeypatch.setenv("OPS_QUEUE_DB_HOST", "legacy-db.internal")
    monkeypatch.setenv("OPS_QUEUE_DB_USER", "legacy_queue_user")
    monkeypatch.setenv("OPS_QUEUE_DB_PASSWORD", "legacy-queue-secret")
    monkeypatch.setenv("DB_OWNER_USER", "mooncen_owner")

    config = deployment_worker.queue_database_config()

    assert config["host"] == "127.0.0.1"
    assert config["port"] == 15432
    assert config["database"] == "mooncen_control"
    assert config["user"] == "mooncen_deployment_worker"
    assert config["password"] == "dedicated-secret"


def test_development_deployment_queue_keeps_legacy_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    for name in (
        "OPS_DEPLOY_QUEUE_DB_HOST",
        "OPS_DEPLOY_QUEUE_DB_PORT",
        "OPS_DEPLOY_QUEUE_DB_NAME",
        "OPS_DEPLOY_QUEUE_DB_USER",
        "OPS_DEPLOY_QUEUE_DB_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPS_QUEUE_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("OPS_QUEUE_DB_PORT", "25432")
    monkeypatch.setenv("OPS_QUEUE_DB_NAME", "mooncen_dev")
    monkeypatch.setenv("OPS_QUEUE_DB_USER", "dev_queue_user")
    monkeypatch.setenv("OPS_QUEUE_DB_PASSWORD", "dev-queue-secret")

    config = deployment_worker.queue_database_config()

    assert config["host"] == "127.0.0.1"
    assert config["port"] == 25432
    assert config["database"] == "mooncen_dev"
    assert config["user"] == "dev_queue_user"
    assert config["password"] == "dev-queue-secret"


def test_deployment_runtime_cleanup_failure_is_logged_and_suppressed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_cleanup(*_args, **_kwargs) -> None:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(
        deployment_worker,
        "_remove_deployment_runtime_directory",
        fail_cleanup,
    )

    with caplog.at_level("WARNING", logger=deployment_worker.__name__):
        removed = deployment_worker._remove_deployment_runtime_directory_resilient(
            Path("ignored")
        )

    assert removed is False
    assert "Unable to remove the private deployment runtime directory" in caplog.text


class _ScriptedDatabaseCursor:
    def __init__(self, connection: "_ScriptedDatabaseConnection") -> None:
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement: str, parameters: object = None) -> None:
        self.connection.statements.append((statement, parameters))
        if self.connection.outcomes:
            outcome = self.connection.outcomes.pop(0)
            if outcome is not None:
                raise outcome

    def fetchone(self):
        return self.connection.fetchone_value


class _ScriptedDatabaseConnection:
    def __init__(
        self,
        outcomes: list[BaseException | None],
        *,
        fetchone_value: object = None,
    ) -> None:
        self.outcomes = outcomes
        self.fetchone_value = fetchone_value
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _ScriptedDatabaseCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _lock_timeout() -> BaseException:
    return deployment_worker.psycopg2.errors.LockNotAvailable("lock timeout")


def test_deployment_log_lock_timeout_retries_then_enters_drop_cooldown(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(deployment_worker.time, "sleep", sleeps.append)
    monkeypatch.setattr(deployment_worker.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(deployment_worker, "_LOG_WRITE_RETRY_AFTER", 0.0)
    connection = _ScriptedDatabaseConnection([_lock_timeout(), _lock_timeout()])

    assert deployment_worker._append_log(connection, "job-id", "info", "line") is False
    assert connection.rollbacks == 2
    assert connection.commits == 0
    assert len(connection.statements) == 2
    assert sleeps == [deployment_worker.DATABASE_RETRY_INITIAL_DELAY_SECONDS]
    assert deployment_worker._LOG_WRITE_RETRY_AFTER == 105.0
    assert "Dropping best-effort PostgreSQL operation" in caplog.text

    assert deployment_worker._append_log(connection, "job-id", "info", "next line") is False
    assert len(connection.statements) == 2


def test_deployment_heartbeat_and_cancel_check_tolerate_lock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deployment_worker.time, "sleep", lambda _delay: None)
    heartbeat_connection = _ScriptedDatabaseConnection([_lock_timeout(), _lock_timeout()])
    cancellation_connection = _ScriptedDatabaseConnection([_lock_timeout(), _lock_timeout()])

    assert (
        deployment_worker._heartbeat(heartbeat_connection, _leased_deployment_job(), 60)
        is deployment_worker.JobLeaseRefresh.UNAVAILABLE
    )
    assert (
        deployment_worker._cancellation_requested(
            cancellation_connection,
            _leased_deployment_job(),
        )
        is False
    )
    assert heartbeat_connection.rollbacks == 2
    assert cancellation_connection.rollbacks == 2


def test_deployment_reporting_permission_failure_is_spooled_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spooled: list[tuple[object, ...]] = []
    connection = _ScriptedDatabaseConnection(
        [deployment_worker.psycopg2.errors.InsufficientPrivilege("denied")]
    )
    monkeypatch.setattr(
        deployment_worker,
        "_spool_log",
        lambda *args, **_kwargs: spooled.append(args) or True,
    )
    monkeypatch.setattr(deployment_worker, "_LOG_WRITE_RETRY_AFTER", 0.0)

    written = deployment_worker._append_log(
        connection,
        "11111111-1111-4111-8111-111111111111",
        "error",
        "database permission failure",
    )

    assert written is False
    assert connection.rollbacks == 1
    assert len(spooled) == 1


def test_deployment_log_spool_is_bounded(tmp_path: Path, monkeypatch) -> None:
    job_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(deployment_worker, "DEPLOYMENT_SPOOL_MAX_BYTES", 350)

    results = [
        deployment_worker._spool_log(
            job_id,
            "info",
            "x" * 80,
            {"token": "must-be-redacted"},
            root=tmp_path,
        )
        for _ in range(10)
    ]

    spool = tmp_path / deployment_worker.DEPLOYMENT_SPOOL_DIRECTORY / f"{job_id}.jsonl"
    assert any(results)
    assert results[-1] is False
    assert spool.stat().st_size <= 350
    assert "must-be-redacted" not in spool.read_text(encoding="utf-8")


def test_final_status_db_outage_is_persisted_without_touching_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = object()
    replacement = object()
    pending: list[dict[str, object]] = []
    calls = 0

    def fail_finish(*_args, **_kwargs) -> None:
        nonlocal calls
        calls += 1
        raise deployment_worker.psycopg2.OperationalError("offline")

    monkeypatch.setattr(deployment_worker, "_finish_job", fail_finish)
    monkeypatch.setattr(
        deployment_worker,
        "_try_reconnect_queue",
        lambda connection: replacement if connection is initial else connection,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_spool_final_status",
        lambda _job, **kwargs: pending.append(kwargs) or True,
    )

    returned = deployment_worker._finish_job_resilient(
        initial,
        {"id": "11111111-1111-4111-8111-111111111111", "parameters": {}},
        final_status="success",
        return_code=0,
        duration_seconds=1.5,
        source_commit=SOURCE_COMMIT,
    )

    assert returned is replacement
    assert calls == 2
    assert pending == [
        {
            "final_status": "success",
            "return_code": 0,
            "duration_seconds": 1.5,
            "detail": "",
            "source_commit": SOURCE_COMMIT,
        }
    ]


def test_deployment_finish_retries_lock_timeout_without_losing_final_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _ScriptedDatabaseConnection(
        [_lock_timeout(), _lock_timeout(), None, None],
        fetchone_value=("success",),
    )
    sleeps: list[float] = []
    heartbeats: list[bool] = []
    final_logs: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(deployment_worker.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        deployment_worker,
        "_publish_worker_heartbeat",
        lambda: heartbeats.append(True),
    )
    monkeypatch.setattr(
        deployment_worker,
        "_append_log",
        lambda *args, **kwargs: final_logs.append((args, kwargs)) or True,
    )

    deployment_worker._finish_job(
        connection,
        _leased_deployment_job(
            parameters={
                "target": "cloud",
                "target_commit": COMMIT,
                "source_tree": TREE,
            }
        ),
        final_status="success",
        return_code=0,
        duration_seconds=12.5,
        source_commit=SOURCE_COMMIT,
    )

    assert connection.rollbacks == 2
    assert connection.commits == 1
    assert len(sleeps) == 2
    assert heartbeats == [True, True]
    job_update_parameters = [
        parameters for statement, parameters in connection.statements if "UPDATE ops_jobs" in statement
    ]
    assert len(job_update_parameters) == 3
    assert all(parameters[0] == "success" for parameters in job_update_parameters)
    assert len(final_logs) == 1


def test_active_deployment_continues_when_log_and_heartbeat_writes_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = "11111111-1111-4111-8111-111111111111"
    finished: list[dict[str, object]] = []
    released: list[tuple[str, str]] = []
    preserved: list[dict[str, object]] = []

    class CompletedProcess:
        stdout = iter(("Uploading immutable deployment artifact\n",))
        returncode = 0

        def poll(self):
            return 0

    monkeypatch.setattr(deployment_worker, "deployment_readiness", lambda: {"available": True})
    monkeypatch.setattr(
        deployment_worker,
        "validated_parameters",
        lambda *_args, **_kwargs: {
            "target": "cloud",
            "target_commit": COMMIT,
            "target_identity": "4" * 64,
            "skip_workers": False,
            "source_tree": TREE,
        },
    )
    monkeypatch.setattr(
        deployment_worker,
        "create_deployment_snapshot_commit",
        lambda **_kwargs: SOURCE_COMMIT,
    )
    monkeypatch.setattr(deployment_worker, "_build_deployment_command", lambda *_args, **_kwargs: ["deploy"])
    monkeypatch.setattr(deployment_worker, "_record_source_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deployment_worker, "_mark_running", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        deployment_worker,
        "_assert_native_deployment_not_mixed_with_container",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(deployment_worker, "_append_log", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(deployment_worker, "_try_reconnect_queue", lambda connection: connection)
    monkeypatch.setattr(deployment_worker, "_flush_spooled_logs", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        deployment_worker,
        "_heartbeat",
        lambda *_args, **_kwargs: deployment_worker.JobLeaseRefresh.UNAVAILABLE,
    )
    monkeypatch.setattr(deployment_worker, "_cancellation_requested", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(deployment_worker, "_publish_worker_heartbeat", lambda: None)
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setattr(
        deployment_worker,
        "_create_deployment_runtime_directory",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_remove_deployment_runtime_directory_resilient",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        deployment_worker,
        "preserve_deployment_release_reference",
        lambda **kwargs: preserved.append(kwargs),
    )
    monkeypatch.setattr(deployment_worker.subprocess, "Popen", lambda *_args, **_kwargs: CompletedProcess())
    monkeypatch.setattr(
        deployment_worker,
        "_finish_job",
        lambda *_args, **kwargs: finished.append(kwargs) or "written",
    )
    monkeypatch.setattr(
        deployment_worker,
        "release_deployment_snapshot_reference",
        lambda reference, commit: released.append((reference, commit)),
    )
    monkeypatch.setattr(deployment_worker, "ACTIVE_PROCESS", None)
    config = deployment_worker.WorkerConfig(
        environment="production",
        agent_id=None,
        poll_interval=2,
        command_timeout=300,
    )

    deployment_worker.execute_job(
        object(),
        {"id": job_id, "parameters": {"target": "cloud"}},
        config,
    )

    assert finished[0]["final_status"] == "success"
    assert finished[0]["return_code"] == 0
    assert released == [(f"refs/mooncen/deploy-snapshots/{job_id}", SOURCE_COMMIT)]
    assert [item["status"] for item in preserved] == ["deploying", "activated"]
    assert deployment_worker.ACTIVE_PROCESS is None


def test_deployment_preparation_failure_finishes_job_and_releases_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = "11111111-1111-4111-8111-111111111111"
    released: list[tuple[str, str]] = []
    finished: list[dict[str, object]] = []

    class Connection:
        rollbacks = 0

        def rollback(self) -> None:
            self.rollbacks += 1

    connection = Connection()
    config = deployment_worker.WorkerConfig(
        environment="production",
        agent_id=None,
        poll_interval=2,
        command_timeout=300,
    )
    monkeypatch.setattr(deployment_worker, "deployment_readiness", lambda: {"available": True})
    monkeypatch.setattr(
        deployment_worker,
        "validated_parameters",
        lambda *_args, **_kwargs: {
            "target_commit": COMMIT,
            "source_tree": TREE,
        },
    )
    monkeypatch.setattr(
        deployment_worker,
        "create_deployment_snapshot_commit",
        lambda **_kwargs: SOURCE_COMMIT,
    )
    monkeypatch.setattr(
        deployment_worker,
        "preserve_deployment_release_reference",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(deployment_worker, "_mark_running", lambda *_args: True)
    monkeypatch.setattr(
        deployment_worker,
        "_publish_worker_heartbeat_resilient",
        lambda: None,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_touch_deployment_agent_resilient",
        lambda active, _config: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_heartbeat",
        lambda *_args: deployment_worker.JobLeaseRefresh.REFRESHED,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_report_log_with_reconnect",
        lambda active, *_args, **_kwargs: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_cancellation_requested",
        lambda *_args: False,
    )
    monkeypatch.setattr(deployment_worker, "_build_deployment_command", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        deployment_worker,
        "_record_source_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database write failed")),
    )
    monkeypatch.setattr(
        deployment_worker,
        "release_deployment_snapshot_reference",
        lambda reference, commit: released.append((reference, commit)),
    )
    monkeypatch.setattr(
        deployment_worker,
        "_finish_job",
        lambda *_args, **kwargs: finished.append(kwargs) or "written",
    )

    deployment_worker.execute_job(
        connection,
        {"id": job_id, "parameters": {}},
        config,
    )

    assert connection.rollbacks == 1
    assert released == [(f"refs/mooncen/deploy-snapshots/{job_id}", SOURCE_COMMIT)]
    assert len(finished) == 1
    assert finished[0]["final_status"] == "failed"
    assert finished[0]["return_code"] is None
    assert float(finished[0]["duration_seconds"]) >= 0
    assert finished[0]["detail"] == "RuntimeError: deployment preparation failed"
    assert finished[0]["source_commit"] == SOURCE_COMMIT


def test_container_only_worker_never_recovers_legacy_native_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = UUID("11111111-1111-4111-8111-111111111111")
    recoveries: list[int] = []
    monotonic_values = iter((0.0, 61.0))

    class Connection:
        closed = False

        def close(self) -> None:
            self.closed = True

        def rollback(self) -> None:
            raise AssertionError("rollback should not be needed")

    connection = Connection()
    monkeypatch.setattr(
        deployment_worker,
        "parse_args",
        lambda _argv=None: SimpleNamespace(
            once=True,
            agent_id=agent_id,
            poll_interval=0.5,
        ),
    )
    monkeypatch.setattr(deployment_worker, "normalized_environment", lambda: "production")
    monkeypatch.setattr(
        deployment_worker,
        "container_worker_service_boundary_ready",
        lambda: True,
    )
    monkeypatch.setattr(deployment_worker, "connect_queue", lambda: connection)
    monkeypatch.setattr(deployment_worker.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(deployment_worker.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(deployment_worker, "_touch_deployment_agent", lambda *_args: None)
    monkeypatch.setattr(deployment_worker, "_publish_worker_heartbeat", lambda: None)
    monkeypatch.setattr(deployment_worker, "_clear_worker_heartbeat", lambda: None)
    monkeypatch.setattr(
        deployment_worker,
        "_recover_stale_jobs",
        lambda _connection, _config, *, stale_after_seconds: recoveries.append(
            stale_after_seconds
        ),
    )
    monkeypatch.setattr(
        deployment_worker,
        "_reconcile_stale_container_job",
        lambda active, _config, **_kwargs: (active, False),
    )
    monkeypatch.setattr(deployment_worker, "_claim_job", lambda *_args: None)
    monkeypatch.setattr(deployment_worker, "RUNNING", True)

    assert deployment_worker.main([]) == 0
    assert recoveries == []
    assert connection.closed is True


def test_deployment_routes_require_admin_for_mutation() -> None:
    route_dependencies: dict[tuple[str, str], set[str]] = {}
    for included in app.routes:
        if isinstance(included, APIRoute):
            candidates = [("", included)]
        elif hasattr(included, "original_router") and hasattr(included, "include_context"):
            candidates = [
                (included.include_context.prefix, route)
                for route in included.original_router.routes
                if isinstance(route, APIRoute)
            ]
        else:
            candidates = []
        for prefix, route in candidates:
            for method in route.methods or {"GET"}:
                route_dependencies[(prefix + route.path, method)] = {
                    getattr(dependency.call, "__name__", "")
                    for dependency in route.dependant.dependencies
                }

    assert (
        "require_ops_viewer"
        in route_dependencies[("/api/ops/deployments/readiness", "GET")]
    )
    assert (
        "require_ops_admin"
        in route_dependencies[("/api/ops/deployments", "POST")]
    )


def test_runtime_roles_allow_only_queue_reporting_for_deployments() -> None:
    roles = (ROOT / "DB/roles.sql").read_text(encoding="utf-8")

    assert "GRANT SELECT, INSERT, UPDATE ON ops_agents TO mooncen_crawler;" in roles
    assert "GRANT INSERT ON ops_deployments TO mooncen_api;" in roles
    assert (
        "GRANT UPDATE (deployment_status, finished_at)\n"
        "            ON ops_deployments TO mooncen_api;"
    ) in roles
    assert "GRANT UPDATE ON ops_deployments TO mooncen_api;" not in roles
    assert "DELETE ON ops_deployments TO mooncen_api;" not in roles
    assert "GRANT SELECT, UPDATE ON ops_deployments TO mooncen_crawler;" in roles
    assert "GRANT INSERT ON ops_deployments TO mooncen_crawler" not in roles

    migration = (
        ROOT / "DB" / "migrations" / "20260806_002_ops_deployment_worker_read_access.sql"
    ).read_text(encoding="utf-8")
    assert "GRANT SELECT, UPDATE ON TABLE ops_deployments TO mooncen_crawler;" in migration

    api_cancel_migration = (
        ROOT
        / "DB"
        / "migrations"
        / "20260807_002_ops_deployment_api_cancel_access.sql"
    ).read_text(encoding="utf-8")
    assert "REVOKE UPDATE ON TABLE ops_deployments FROM mooncen_api;" in api_cancel_migration
    assert (
        "GRANT UPDATE (deployment_status, finished_at)\n"
        "            ON TABLE ops_deployments TO mooncen_api;"
    ) in api_cancel_migration
    assert "has_table_privilege(" in api_cancel_migration
    assert api_cancel_migration.count("has_column_privilege(") == 3
    assert "'public.ops_deployments'" in api_cancel_migration
    assert "'UPDATE'" in api_cancel_migration
    assert "GRANT ALL" not in api_cancel_migration
    assert "GRANT UPDATE ON TABLE ops_deployments" not in api_cancel_migration
    assert "DELETE ON TABLE ops_deployments" not in api_cancel_migration
