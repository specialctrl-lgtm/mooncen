from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main, ops_static
from backend.ops_static import (
    CONTENT_SECURITY_POLICY,
    MANIFEST_NAME,
    OPS_STATIC_BUILD_CONTRACT,
    OpsStaticError,
    create_ops_static_manifest,
    load_ops_static_bundle,
)
from tools import seal_ops_static


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def _static_root(tmp_path: Path):
    root = tmp_path / "ops-console-dist"
    assets = root / "assets"
    assets.mkdir(parents=True)
    root.chmod(0o755)
    assets.chmod(0o755)
    files = {
        "index.html": b"<!doctype html><div id=\"root\"></div>\n",
        "assets/index-AbCdEf123.js": (
            b"const csrf='mooncen_ops_csrf';"
            b"fetch('/api/auth/ops/login');fetch('/api/ops/session');\n"
        ),
        "assets/index-XyZ987654.css": b"body { color: #123; }\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.write_bytes(content)
        path.chmod(0o644)
    manifest = {
        "build_contract": OPS_STATIC_BUILD_CONTRACT,
        "files": {
            relative: {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
            for relative, content in sorted(files.items())
        },
        "schema_version": 1,
    }
    receipt = root / MANIFEST_NAME
    receipt.write_bytes(_canonical(manifest))
    receipt.chmod(0o644)
    return root, load_ops_static_bundle(
        root,
        trusted_uid=os.geteuid(),
        trusted_gid=os.getegid(),
    )


def test_reviewed_ops_static_bundle_serves_spa_and_immutable_assets(
    tmp_path: Path,
) -> None:
    _root, bundle = _static_root(tmp_path)

    index = bundle.response("")
    assert index.status_code == 200
    assert index.body.startswith(b"<!doctype html>")
    assert index.headers["cache-control"] == "no-store"
    assert index.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert index.headers["x-frame-options"] == "DENY"

    route = bundle.response("deployments")
    assert route.status_code == 200
    assert route.body == index.body

    asset = bundle.response("assets/index-AbCdEf123.js")
    assert asset.status_code == 200
    assert asset.headers["content-type"] == "text/javascript; charset=utf-8"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"

    head = bundle.response("assets/index-AbCdEf123.js", head=True)
    assert head.status_code == 200
    assert head.body == b""


@pytest.mark.parametrize(
    "path",
    (
        "assets/missing.js",
        "../secrets.env",
        "assets/../../secrets.env",
        r"assets\index-AbCdEf123.js",
        "nested//route",
        "route\x00suffix",
    ),
)
def test_ops_static_bundle_rejects_unknown_assets_and_traversal(
    tmp_path: Path,
    path: str,
) -> None:
    _root, bundle = _static_root(tmp_path)

    response = bundle.response(path)

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_ops_static_bundle_never_turns_unknown_api_routes_into_spa(
    tmp_path: Path,
) -> None:
    _root, bundle = _static_root(tmp_path)

    response = bundle.response("api/not-a-route")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.body == b'{"detail":"Not Found"}'


def test_ops_static_bundle_fails_closed_after_file_mutation(tmp_path: Path) -> None:
    root, bundle = _static_root(tmp_path)
    asset = root / "assets/index-AbCdEf123.js"
    asset.write_bytes(b"console.log('tampered');\n")
    asset.chmod(0o644)

    with pytest.raises(OpsStaticError, match="reviewed receipt"):
        bundle.response("assets/index-AbCdEf123.js")


def test_ops_static_builder_seals_exact_compiled_contract(tmp_path: Path) -> None:
    root, _bundle = _static_root(tmp_path)
    (root / MANIFEST_NAME).unlink()

    result = create_ops_static_manifest(
        root,
        trusted_uid=os.geteuid(),
        trusted_gid=os.getegid(),
    )

    assert result["schema_version"] == 1
    assert result["file_count"] == 3
    manifest_bytes = (root / MANIFEST_NAME).read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["build_contract"] == OPS_STATIC_BUILD_CONTRACT
    assert manifest_bytes == _canonical(manifest)
    load_ops_static_bundle(
        root,
        trusted_uid=os.geteuid(),
        trusted_gid=os.getegid(),
    )


@pytest.mark.parametrize(
    "canary",
    (b"mooncen_csrf", b"127.0.0.1:8001", b"127.0.0.1:8002"),
)
def test_ops_static_builder_rejects_legacy_compiled_bindings(
    tmp_path: Path,
    canary: bytes,
) -> None:
    root, _bundle = _static_root(tmp_path)
    (root / MANIFEST_NAME).unlink()
    script = root / "assets/index-AbCdEf123.js"
    script.write_bytes(script.read_bytes() + canary)
    script.chmod(0o644)

    with pytest.raises(OpsStaticError, match="legacy"):
        create_ops_static_manifest(
            root,
            trusted_uid=os.geteuid(),
            trusted_gid=os.getegid(),
        )


def test_ops_static_bundle_requires_exact_root_owned_file_set(tmp_path: Path) -> None:
    root, _bundle = _static_root(tmp_path)
    extra = root / "operator-output.txt"
    extra.write_text("unreviewed\n", encoding="ascii")
    extra.chmod(0o644)

    with pytest.raises(OpsStaticError, match="file set"):
        load_ops_static_bundle(
            root,
            trusted_uid=os.geteuid(),
            trusted_gid=os.getegid(),
        )

    extra.unlink()
    asset = root / "assets/index-AbCdEf123.js"
    target = root / "assets/target.js"
    target.write_bytes(asset.read_bytes())
    target.chmod(0o644)
    asset.unlink()
    asset.symlink_to(target.name)
    with pytest.raises(OpsStaticError, match="symbolic link|metadata"):
        load_ops_static_bundle(
            root,
            trusted_uid=os.geteuid(),
            trusted_gid=os.getegid(),
        )


def test_ops_api_origin_routes_api_before_static_spa(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _root, bundle = _static_root(tmp_path)
    monkeypatch.setattr(main, "_api_profile", "ops")
    monkeypatch.setattr(main, "_ops_static_bundle", bundle)
    client = TestClient(main.app, raise_server_exceptions=False)

    root = client.get("/")
    route = client.get("/deployments")
    api = client.get("/api/not-a-real-endpoint")
    protected_api = client.get("/api/ops/runtime-metrics")
    post = client.post("/deployments")

    assert root.status_code == route.status_code == 200
    assert root.headers["cache-control"] == "no-store"
    assert route.text.startswith("<!doctype html>")
    assert api.status_code == 404
    assert api.headers["content-type"].startswith("application/json")
    assert "<!doctype html>" not in api.text
    assert protected_api.status_code == 401
    assert protected_api.headers["content-type"].startswith("application/json")
    assert "<!doctype html>" not in protected_api.text
    assert post.status_code == 405


def test_ops_static_startup_requires_reviewed_fixed_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_api_profile", "ops")
    monkeypatch.setattr(
        main,
        "load_fixed_ops_static_bundle",
        lambda: (_ for _ in ()).throw(OpsStaticError("bad receipt")),
    )

    with pytest.raises(RuntimeError, match="reviewed Ops static bundle"):
        main.load_ops_static_at_startup()


def test_fixed_ops_static_bundle_follows_only_the_root_runtime_pair_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    control_parent = tmp_path / "mooncen-an2p-control"
    runtime_parent = tmp_path / "mooncen-an2p-runtime"
    releases = runtime_parent / "releases"
    pair_name = f"runtime-pair.{'1' * 40}.{'2' * 40}.{'3' * 64}"
    control = releases / pair_name / "control"
    control.mkdir(parents=True)
    for directory in (control_parent, runtime_parent, releases, releases / pair_name, control):
        directory.mkdir(exist_ok=True)
        directory.chmod(0o755)
    _static_root(control)
    runtime_current = runtime_parent / "current"
    runtime_current.symlink_to(f"releases/{pair_name}")
    control_current = control_parent / "current"
    control_current.symlink_to("../mooncen-an2p-runtime/current/control")
    monkeypatch.setattr(ops_static, "CONTROL_CURRENT", control_current)
    monkeypatch.setattr(ops_static, "RUNTIME_CURRENT", runtime_current)
    monkeypatch.setattr(ops_static, "TRUSTED_UID", os.geteuid())
    monkeypatch.setattr(ops_static, "TRUSTED_GID", os.getegid())

    bundle = ops_static.load_fixed_ops_static_bundle()

    assert bundle.response("").status_code == 200
    control_current.unlink()
    control_current.symlink_to(control)
    with pytest.raises(OpsStaticError, match="pointer"):
        ops_static.load_fixed_ops_static_bundle()


def test_ops_static_build_uses_only_the_pinned_reviewed_node_container() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "deploy/an2p/ops_console_static.Dockerfile").read_text(
        encoding="utf-8"
    )
    ignore = (
        root / "deploy/an2p/ops_console_static.Dockerfile.dockerignore"
    ).read_text(encoding="utf-8")

    assert f"FROM {ops_static.OPS_STATIC_NODE_IMAGE} AS build" in dockerfile
    assert "RUN npm ci --ignore-scripts --no-audit --fund=false" in dockerfile
    assert 'VITE_API_BASE_URL=""' in dockerfile
    assert 'VITE_OPS_CSRF_COOKIE_NAME="mooncen_ops_csrf"' in dockerfile
    assert "FROM scratch" in dockerfile
    assert "COPY --from=build /build/ops-console/dist /" in dockerfile
    assert "/home/sgm" not in dockerfile
    assert ".local/bin" not in dockerfile
    assert ignore.splitlines()[0] == "**"
    assert "!ops-console/**" in ignore
    assert "ops-console/**/node_modules" in ignore
    assert "ops-console/**/dist" in ignore


def test_ops_static_trust_inputs_are_exact_build_and_clean_source_policy() -> None:
    from deploy.docker.production_runtime_integrity import BUILD_POLICY_PATHS
    from deploy.docker.verify_clean_source import REQUIRED_CONTROL_PATHS

    required = {
        "backend/main.py",
        "backend/ops_static.py",
        "deploy/an2p/container_evidence_handoff.py",
        "deploy/an2p/mooncen-api.service",
        "deploy/an2p/mooncen-development-runtime.target",
        "deploy/an2p/mooncen-frontend.service",
        "deploy/an2p/mooncen-ops-api.service",
        "deploy/an2p/mooncen-ops-api.socket",
        "deploy/an2p/mooncen-ops-api-ipv6.service",
        "deploy/an2p/mooncen-ops-api-ipv6.socket",
        "deploy/an2p/mooncen-ops-console.service",
        "deploy/an2p/mooncen-status-agent.service",
        "deploy/an2p/mooncen-an2p-runtime-recovery.service",
        "deploy/an2p/mooncen_register_container_evidence.py",
        "deploy/an2p/mooncen_loopback_redirect.py",
        "deploy/an2p/ops_console_static.Dockerfile",
        "deploy/an2p/ops_console_static.Dockerfile.dockerignore",
        "deploy/an2p/runtime_pair_manager.py",
        "ops-console/package-lock.json",
        "ops-console/src/api.ts",
        "ops-console/src/auth.ts",
        "ops-console/src/pages/DeploymentsPage.tsx",
        "deploy/an2p/README.md",
        "docs/an2p-control-plane-architecture.md",
        "tests/test_an2p_docker_release_selection.py",
        "tests/test_an2p_loopback_redirect.py",
        "tests/test_an2p_runtime_pair_manager.py",
        "tests/test_an2p_runtime_selector.py",
        "tests/test_ops_console_separation.py",
        "tests/test_rotate_an2p_ops_password.py",
        "tools/rotate_an2p_ops_password.py",
        "tools/seal_ops_static.py",
    }
    assert required.issubset(BUILD_POLICY_PATHS)
    assert required.issubset(REQUIRED_CONTROL_PATHS)


def test_static_sealer_accepts_only_one_fixed_runtime_pair_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    pair_name = f"runtime-pair.{'1' * 40}.{'2' * 40}.{'3' * 64}"
    control = releases / pair_name / "control"
    control.mkdir(parents=True)
    for directory in (releases, releases / pair_name, control):
        directory.chmod(0o755)
    static_root, _bundle = _static_root(control)
    (static_root / MANIFEST_NAME).unlink()
    monkeypatch.setattr(seal_ops_static, "CONTROL_ROOT", control)

    result = seal_ops_static.seal(
        pair_name,
        releases=releases,
        trusted_uid=os.geteuid(),
        trusted_gid=os.getegid(),
    )

    assert result["schema_version"] == 1
    with pytest.raises(OpsStaticError, match="already exists"):
        seal_ops_static.seal(
            pair_name,
            releases=releases,
            trusted_uid=os.geteuid(),
            trusted_gid=os.getegid(),
        )
    with pytest.raises(OpsStaticError, match="pair name"):
        seal_ops_static.seal(
            "/tmp/operator-path",
            releases=releases,
            trusted_uid=os.geteuid(),
            trusted_gid=os.getegid(),
        )


def test_static_sealer_accepts_only_the_exact_pending_pair_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    token = "4" * 32
    pair_name = f"runtime-pair.{'1' * 40}.{'2' * 40}.{'3' * 64}"
    stage = releases / f".stage.{token}"
    control = stage / "control"
    control.mkdir(parents=True)
    for directory in (releases, stage, control):
        directory.chmod(0o755)
    static_root, _bundle = _static_root(control)
    (static_root / MANIFEST_NAME).unlink()
    monkeypatch.setattr(seal_ops_static, "CONTROL_ROOT", control)

    result = seal_ops_static.seal(
        pair_name,
        staging_token=token,
        releases=releases,
        trusted_uid=os.geteuid(),
        trusted_gid=os.getegid(),
    )

    assert result["schema_version"] == 1
    assert (static_root / MANIFEST_NAME).is_file()
    with pytest.raises(OpsStaticError, match="staging token"):
        seal_ops_static.seal(
            pair_name,
            staging_token="../operator-path",
            releases=releases,
            trusted_uid=os.geteuid(),
            trusted_gid=os.getegid(),
        )
