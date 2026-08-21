from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from deploy.docker.release_manifest import create_release_manifest


ROOT = Path(__file__).resolve().parents[1]


def _load_validator() -> ModuleType:
    path = ROOT / "deploy" / "an2p" / "validate_docker_release.py"
    spec = importlib.util.spec_from_file_location("mooncen_an2p_release_validator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _release() -> dict[str, object]:
    return create_release_manifest(
        base_commit="a" * 40,
        source_tree="b" * 40,
        snapshot_commit="c" * 40,
        platform="linux/amd64",
        bundle_sha256="d" * 64,
        compose_sha256="e" * 64,
        build_policy_sha256="f" * 64,
        migration_ledger_sha256="1" * 64,
        images={
            "api": {
                "tag": f"mooncen/api:release-{'b' * 40}",
                "image_id": f"sha256:{'2' * 64}",
            },
            "frontend": {
                "tag": f"mooncen/frontend:release-{'b' * 40}",
                "image_id": f"sha256:{'3' * 64}",
            },
        },
        created_at="2026-08-19T12:00:00Z",
    )


def _environment(path: Path, release_directory: Path, release: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True)
    path.write_text(
        "\n".join(
            (
                f"MOONCEN_DEV_RELEASE_DIR={release_directory}",
                f"MOONCEN_POSTGRES_IMAGE=mooncen/postgres:dev-release-{release['source_tree']}",
                f"MOONCEN_API_IMAGE={release['images']['api']['tag']}",
                f"MOONCEN_FRONTEND_IMAGE={release['images']['frontend']['tag']}",
                "MOONCEN_DB_PASSWORD=private-value",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_environment_parser_requires_private_owned_paths(
    tmp_path: Path,
) -> None:
    validator = _load_validator()
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    environment = config / "development.env"
    environment.write_text("MOONCEN_API_IMAGE=mooncen/api:test\n", encoding="utf-8")
    environment.chmod(0o600)

    assert validator._private_environment(environment) == {"MOONCEN_API_IMAGE": "mooncen/api:test"}

    environment.chmod(0o644)
    with pytest.raises(validator.ReleaseSelectionError, match="unsafe"):
        validator._private_environment(environment)


def test_release_directory_is_fixed_under_private_user_store(
    tmp_path: Path,
) -> None:
    validator = _load_validator()
    home = tmp_path / "home"
    release_root = home / ".local" / "share" / "mooncen-docker" / "releases"
    release_root.mkdir(mode=0o700, parents=True)
    release = release_root / ("b" * 40)
    release.mkdir(mode=0o700)

    assert validator._release_directory(str(release), home=home) == release

    outside = home / "outside" / ("b" * 40)
    outside.mkdir(mode=0o700, parents=True)
    with pytest.raises(validator.ReleaseSelectionError, match="unsafe"):
        validator._release_directory(str(outside), home=home)


def test_pending_system_release_binds_one_exact_stage_to_its_final_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o750)
    evidence_root.chmod(0o750)
    token = "4" * 32
    stage = evidence_root / f".stage.{token}"
    stage.mkdir(mode=0o750)
    stage.chmod(0o750)
    source_tree = "b" * 40
    final = evidence_root / source_tree
    monkeypatch.setattr(validator, "SYSTEM_EVIDENCE_ROOT", evidence_root)

    selected, configured_tree = validator._staged_release_directory(
        str(final),
        token,
        reader_gid=os.getegid(),
        trusted_uid=os.geteuid(),
    )

    assert selected == stage
    assert configured_tree == source_tree
    with pytest.raises(validator.ReleaseSelectionError, match="token"):
        validator._staged_release_directory(
            str(final),
            "../operator-path",
            reader_gid=os.getegid(),
            trusted_uid=os.geteuid(),
        )
    final.mkdir(mode=0o750)
    with pytest.raises(validator.ReleaseSelectionError, match="unsafe"):
        validator._staged_release_directory(
            str(final),
            token,
            reader_gid=os.getegid(),
            trusted_uid=os.geteuid(),
        )


def test_installed_runtime_pointer_is_relative_private_and_exact(
    tmp_path: Path,
) -> None:
    from deploy.an2p import check_docker_environment

    validator = _load_validator()
    runtime_parent = tmp_path / "mooncen-an2p"
    runtime_parent.mkdir(mode=0o700)
    target_name = f"docker-release-runtime.{'1' * 40}.{'2' * 64}.{'3' * 64}.Ab12Cd34"
    target = runtime_parent / target_name
    target.mkdir(mode=0o700)
    environment = target / "development.env"
    environment.write_text("MOONCEN_API_IMAGE=test\n", encoding="utf-8")
    environment.chmod(0o600)
    pointer = runtime_parent / "docker-release-runtime"
    pointer.symlink_to(target_name)

    assert validator._trusted_project_root(pointer) == (target, True)
    assert validator._runtime_pointer_child(target, pointer / "development.env", "development.env") == environment
    check_docker_environment.validate_environment_file(pointer / "development.env")

    pointer.unlink()
    pointer.symlink_to(target)
    with pytest.raises(validator.ReleaseSelectionError, match="unsafe"):
        validator._trusted_project_root(pointer)
    with pytest.raises(ValueError, match="unsafe"):
        check_docker_environment.validate_environment_file(pointer / "development.env")


def test_development_postgres_image_is_tree_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    tree = "b" * 40
    tag = f"mooncen/postgres:dev-release-{tree}"
    monkeypatch.setattr(
        validator,
        "_command",
        lambda *_args, **_kwargs: f"sha256:{'6' * 64}|linux/amd64|{tree}",
    )

    assert validator._development_postgres_image(
        root=tmp_path,
        tag=tag,
        source_tree=tree,
    ) == (f"sha256:{'6' * 64}", "linux/amd64")

    with pytest.raises(validator.ReleaseSelectionError, match="tag"):
        validator._development_postgres_image(
            root=tmp_path,
            tag="mooncen/postgres:16-postgis-dev",
            source_tree=tree,
        )

    monkeypatch.setattr(
        validator,
        "_command",
        lambda *_args, **_kwargs: f"sha256:{'6' * 64}|linux/amd64|{'a' * 40}",
    )
    with pytest.raises(validator.ReleaseSelectionError, match="source tree"):
        validator._development_postgres_image(
            root=tmp_path,
            tag=tag,
            source_tree=tree,
        )


def test_installed_compose_must_equal_reviewed_source(
    tmp_path: Path,
) -> None:
    validator = _load_validator()
    project = tmp_path / "project"
    project.mkdir(mode=0o700)
    (project / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    installed = tmp_path / "installed.yaml"
    installed.write_text("services: {}\n", encoding="utf-8")
    installed.chmod(0o600)

    assert validator._runtime_compose_file(project, installed) == installed

    installed.write_text("services: {unexpected: {}}\n", encoding="utf-8")
    with pytest.raises(validator.ReleaseSelectionError, match="differs"):
        validator._runtime_compose_file(project, installed)


def test_development_runtime_policy_set_covers_activation_and_postgres_provenance() -> None:
    import deploy.docker.smoke as smoke

    expected = (
        ".dockerignore",
        ".gitattributes",
        "compose.yaml",
        "deploy/an2p/check_docker_environment.py",
        "deploy/an2p/install_user_services.sh",
        "deploy/an2p/mooncen-api.service",
        "deploy/an2p/mooncen-development-runtime.target",
        "deploy/an2p/mooncen-docker-dev.service",
        "deploy/an2p/mooncen-frontend.service",
        "deploy/an2p/mooncen-status-agent.service",
        "deploy/an2p/validate_docker_release.py",
        "deploy/docker/postgres.Dockerfile",
        "deploy/docker/release_manifest.py",
        "deploy/docker/render_runtime_config.py",
        "deploy/docker/smoke.py",
        "deploy/docker/verify_release_bundle.py",
        "tools/wait_for_an2p_database.py",
        "tools/wait_for_an2p_http.py",
    )
    assert smoke.DEVELOPMENT_VALIDATION_POLICY_PATHS == expected

def test_activation_evidence_is_canonical_private_and_pins_environment(
    tmp_path: Path,
) -> None:
    validator = _load_validator()
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    activation_file = runtime / "activation.json"
    activation = {
        "schema_version": 1,
        "release_digest": "1" * 64,
        "receipt_digest": "2" * 64,
        "source_tree": "3" * 40,
        "target_identity": "4" * 64,
        "postgres_image_id": "sha256:" + "5" * 64,
        "environment_sha256": "6" * 64,
    }

    validator._write_activation(runtime, activation_file, activation)

    assert activation_file.stat().st_mode & 0o777 == 0o600
    assert validator._load_activation(runtime, activation_file) == activation
    with pytest.raises(validator.ReleaseSelectionError, match="already exists"):
        validator._write_activation(runtime, activation_file, activation)

    activation_file.write_text("{}\n", encoding="ascii")
    with pytest.raises(validator.ReleaseSelectionError, match="fields"):
        validator._load_activation(runtime, activation_file)


def test_selected_release_binds_receipt_tags_target_and_running_image_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    release = _release()
    project = tmp_path / "project"
    project.mkdir(mode=0o700)
    (project / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    home = tmp_path / "home"
    release_root = home / ".local" / "share" / "mooncen-docker" / "releases"
    release_root.mkdir(mode=0o700, parents=True)
    release_directory = release_root / str(release["source_tree"])
    release_directory.mkdir(mode=0o700)
    environment = home / ".config" / "mooncen-docker" / "development.env"
    _environment(environment, release_directory, release)
    receipt = {
        "receipt_digest": "4" * 64,
        "target": "an2p-dev",
        "target_identity": "5" * 64,
    }

    import deploy.docker.release_manifest as release_manifest
    import deploy.docker.smoke as smoke
    import deploy.docker.verify_release_bundle as verifier

    monkeypatch.setattr(
        validator.pwd,
        "getpwuid",
        lambda _uid: SimpleNamespace(pw_dir=str(home)),
    )
    monkeypatch.setattr(
        release_manifest,
        "load_json_evidence",
        lambda path, **_kwargs: receipt if path.name == "validation.json" else release,
    )
    monkeypatch.setattr(
        release_manifest,
        "bind_promotion_evidence",
        lambda *_args, **_kwargs: SimpleNamespace(release=release, receipt=receipt),
    )
    monkeypatch.setattr(
        release_manifest,
        "bind_validation_evidence",
        lambda *_args, **_kwargs: SimpleNamespace(release=release, receipt=receipt),
    )
    monkeypatch.setattr(
        verifier,
        "verify_release_directory",
        lambda *_args, **_kwargs: {
            "image_ids": {
                "api": release["images"]["api"]["image_id"],
                "frontend": release["images"]["frontend"]["image_id"],
            }
        },
    )
    monkeypatch.setattr(
        smoke,
        "_development_target_identity",
        lambda **_kwargs: "5" * 64,
    )

    postgres_marker = ["6"]

    def fake_command(arguments: tuple[str, ...], **_kwargs: object) -> str:
        if arguments[:2] == ("docker", "info"):
            return "linux/x86_64"
        if arguments[:3] == ("docker", "image", "inspect"):
            return f"sha256:{postgres_marker[0] * 64}|linux/amd64|{release['source_tree']}"
        if "ps" in arguments:
            service = arguments[-1]
            return {"postgres": "c", "api": "a", "frontend": "f"}[service] * 64
        if arguments[:2] == ("docker", "inspect"):
            if arguments[-1].startswith("c"):
                return f"sha256:{postgres_marker[0] * 64}"
            return (
                str(release["images"]["api"]["image_id"])
                if arguments[-1].startswith("a")
                else str(release["images"]["frontend"]["image_id"])
            )
        raise AssertionError(arguments)

    monkeypatch.setattr(validator, "_command", fake_command)

    evidence = validator.validate_selected_release(
        project_root=project,
        environment_file=environment,
        require_running=True,
    )

    assert evidence == {
        "release_digest": release["release_digest"],
        "receipt_digest": receipt["receipt_digest"],
        "source_tree": release["source_tree"],
        "target_identity": receipt["target_identity"],
        "postgres_image_id": f"sha256:{'6' * 64}",
        "environment_sha256": hashlib.sha256(environment.read_bytes()).hexdigest(),
        "running_verified": True,
    }

    activation_file = project / "activation.json"
    offline_evidence = validator.validate_selected_release(
        project_root=project,
        environment_file=environment,
        require_running=False,
        write_activation_file=activation_file,
    )
    assert offline_evidence["running_verified"] is False
    validator.validate_selected_release(
        project_root=project,
        environment_file=environment,
        require_running=False,
        activation_file=activation_file,
        require_current_receipt=False,
    )

    original_environment = environment.read_text(encoding="utf-8")
    environment.write_text(f"{original_environment}# drift\n", encoding="utf-8")
    with pytest.raises(validator.ReleaseSelectionError, match="activation evidence"):
        validator.validate_selected_release(
            project_root=project,
            environment_file=environment,
            require_running=False,
            activation_file=activation_file,
            require_current_receipt=False,
        )
    environment.write_text(original_environment, encoding="utf-8")

    postgres_marker[0] = "7"
    with pytest.raises(validator.ReleaseSelectionError, match="activation evidence"):
        validator.validate_selected_release(
            project_root=project,
            environment_file=environment,
            require_running=False,
            activation_file=activation_file,
            require_current_receipt=False,
        )
    postgres_marker[0] = "6"

    receipt["target"] = "other-dev"
    with pytest.raises(validator.ReleaseSelectionError, match="target"):
        validator.validate_selected_release(
            project_root=project,
            environment_file=environment,
            require_running=False,
        )


def test_an2p_unit_checks_release_before_and_after_start() -> None:
    unit = (ROOT / "deploy" / "an2p" / "mooncen-docker-dev.service").read_text(encoding="utf-8")
    validator = "deploy/an2p/validate_docker_release.py"
    assert unit.count(validator) == 5
    assert "ExecStartPre=" in unit
    assert "ExecStartPost=" in unit
    reload_lines = [line for line in unit.splitlines() if line.startswith("ExecReload=")]
    assert validator in reload_lines[0]
    assert validator in reload_lines[-1]
    assert "render_runtime_config.py" in reload_lines[1]
    assert "docker compose" in reload_lines[2]
    assert "--require-running" in unit
    assert unit.count("--allow-expired-receipt") == 5
    assert unit.count("--runtime-compose-file") == 5
    assert unit.count("--activation-file") == 5
    stop_lines = [line for line in unit.splitlines() if line.startswith("ExecStop=")]
    assert validator in stop_lines[0]
    assert stop_lines[0].startswith("ExecStop=-/usr/bin/python3 -I ")
    assert "docker compose" in stop_lines[1]
    stop_post_lines = [line for line in unit.splitlines() if line.startswith("ExecStopPost=")]
    assert len(stop_post_lines) == 1
    assert stop_post_lines[0].startswith("ExecStopPost=-/usr/bin/docker compose ")
    assert "--no-build --pull never" in unit
    installed_root = "/opt/mooncen-an2p-docker/current"
    assert f"WorkingDirectory={installed_root}" in unit
    assert f"ConditionPathExists={installed_root}/development.env" in unit
    assert "ConditionPathExists=%h/.config/mooncen-docker/development.env" not in unit
    assert "EnvironmentFile=" not in unit
    assert unit.count(f"--environment-file {installed_root}/development.env") == 7
    assert unit.count(f"{installed_root}/compose.yaml") == 10
    assert f"--project-root {installed_root}" in unit
    assert "%h/src/project/mooncen" not in unit
    assert ".venv/bin/python" not in unit
    assert unit.count("/usr/bin/python3 -I ") == 8
    assert "--file %h/src/project/mooncen/compose.yaml" not in unit
    assert "User=mooncen_docker_operator" in unit
    assert "SupplementaryGroups=docker" in unit
    assert (
        "ExecStartPre=+/usr/local/libexec/mooncen-an2p-runtime-manager "
        "gate-service-start mooncen-docker-dev.service"
    ) in unit
    assert "Requires=docker.service mooncen-an2p-runtime-recovery.service" in unit
    assert "down -v" not in unit


def test_validator_source_is_regular() -> None:
    path = ROOT / "deploy" / "an2p" / "validate_docker_release.py"
    metadata = path.stat()
    assert stat.S_ISREG(metadata.st_mode)
    assert not path.is_symlink()
    assert metadata.st_uid == os.getuid()
