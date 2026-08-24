from __future__ import annotations

from io import BytesIO
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from deploy.an2p.cloud import mooncen_container_ingress as ingress
from deploy.an2p.cloud import mooncen_container_ssh_dispatch as dispatcher


ROOT = Path(__file__).resolve().parents[1]
TREE = "a" * 40
DIGEST = "b" * 64
CONTROLLER_PREFIX = (
    "/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-release"
)
CLAIM_JOB = "c" * 32
CLAIM_EPOCH = "00000000000000000042"
CLAIM_TOKEN = "d" * 32
CLAIM = f"{CLAIM_JOB} {CLAIM_EPOCH} {CLAIM_TOKEN}"


def test_dispatcher_separates_status_and_mutation_accounts() -> None:
    for account in (dispatcher.DEPLOY_USER, dispatcher.STATUS_USER):
        selected = dispatcher.select_dispatch(
            f"{CONTROLLER_PREFIX} status",
            account=account,
        )
        assert selected.executable == "/usr/bin/sudo"
        assert selected.argv[-1] == "status"
        selected = dispatcher.select_dispatch(
            "/usr/bin/test -e /usr/local/libexec/mooncen-container-release",
            account=account,
        )
        assert selected.executable == "/usr/bin/test"

    promote = dispatcher.select_dispatch(
        f"{CONTROLLER_PREFIX} promote {TREE} 0000000000 {DIGEST} {DIGEST} {DIGEST} {CLAIM}",
        account=dispatcher.DEPLOY_USER,
    )
    assert promote.argv[-9:] == (
        "promote",
        TREE,
        "0000000000",
        DIGEST,
        DIGEST,
        DIGEST,
        CLAIM_JOB,
        CLAIM_EPOCH,
        CLAIM_TOKEN,
    )
    for action in ("lease-bind", "lease-release"):
        selected = dispatcher.select_dispatch(
            f"{CONTROLLER_PREFIX} {action} {CLAIM}",
            account=dispatcher.DEPLOY_USER,
        )
        assert selected.argv[-4:] == (action, CLAIM_JOB, CLAIM_EPOCH, CLAIM_TOKEN)
    for action in ("stage", "load-images", "preflight"):
        selected = dispatcher.select_dispatch(
            f"{CONTROLLER_PREFIX} {action} {TREE} {CLAIM}",
            account=dispatcher.DEPLOY_USER,
        )
        assert selected.argv[-5:] == (
            action,
            TREE,
            CLAIM_JOB,
            CLAIM_EPOCH,
            CLAIM_TOKEN,
        )
    for action in ("rollback", "rollback-native"):
        dispatcher.select_dispatch(
            f"{CONTROLLER_PREFIX} {action} 0000000000 {DIGEST} {DIGEST} {DIGEST} {CLAIM}",
            account=dispatcher.DEPLOY_USER,
        )
    with pytest.raises(dispatcher.DispatchError, match="mutation"):
        dispatcher.select_dispatch(
            f"{CONTROLLER_PREFIX} stage {TREE} {CLAIM}",
            account=dispatcher.STATUS_USER,
        )
    with pytest.raises(dispatcher.DispatchError, match="allowlist"):
        dispatcher.select_dispatch(
            f"{CONTROLLER_PREFIX} stage {TREE}",
            account=dispatcher.DEPLOY_USER,
        )


def test_interactive_sudoers_cannot_mint_claims_and_legacy_rule_is_scrubbed(
    tmp_path: Path,
) -> None:
    sudoers = (ROOT / "deploy/ubuntu/install_sudoers.sh").read_text(encoding="utf-8")
    provisioner = (
        ROOT / "deploy/an2p/cloud/provision_cloud_deploy_endpoint.sh"
    ).read_text(encoding="utf-8")
    for action in (
        "lease-bind",
        "lease-release",
        "stage",
        "load-images",
        "preflight",
        "promote",
        "rollback",
        "rollback-native",
    ):
        assert f"${{CONTAINER_CONTROLLER}} {action}" not in sudoers

    marker = (
        '/usr/bin/python3 -I - "$legacy_sudoers" "$legacy_sudoers_tmp" '
        '"$controller" <<\'PY\'\n'
    )
    scrubber = provisioner.split(marker, 1)[1].split("\nPY\n", 1)[0]
    controller = "/usr/local/libexec/mooncen-container-release"
    source = tmp_path / "mooncen-deploy"
    destination = tmp_path / "mooncen-deploy.new"
    source.write_text(
        "ubuntu ALL=(root) NOPASSWD: "
        f"{controller} lease-bind {'1' * 32} {'2' * 20} {'3' * 32}, "
        f"{controller} promote {'4' * 40} {'5' * 10} {'6' * 64} {'7' * 64} "
        f"{'8' * 64} {'1' * 32} {'2' * 20} {'3' * 32}, "
        f"{controller} native-begin {'9' * 32}, {controller} status\n",
        encoding="utf-8",
    )
    destination.write_bytes(b"")

    completed = subprocess.run(
        [sys.executable, "-I", "-", str(source), str(destination), controller],
        input=scrubber,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    converged = destination.read_text(encoding="utf-8")
    assert "lease-bind" not in converged
    assert " promote " not in converged
    assert f"{controller} native-begin {'9' * 32}" in converged
    assert f"{controller} status" in converged


def test_dispatcher_allows_only_the_fixed_stdin_ingress_protocol() -> None:
    base = "/usr/local/libexec/mooncen-container-ingress"
    for command in (
        f"{base} prepare {TREE}",
        f"{base} upload {TREE} images.tar 123 {DIGEST}",
        f"{base} abort {TREE}",
    ):
        selected = dispatcher.select_dispatch(
            command,
            account=dispatcher.DEPLOY_USER,
        )
        assert selected.executable == base

    rejected = (
        "scp -t /var/lib/mooncen-container-ingress/" + TREE + "/release.json",
        "internal-sftp",
        "/bin/sh -c id",
        f"{base} upload {TREE} ../release.json 1 {DIGEST}",
        f"{base} upload {TREE} release.json +1 {DIGEST}",
        f"{base} upload {TREE} release.json 1 {'B' * 64}",
        f"{CONTROLLER_PREFIX} native-begin {'c' * 32}",
        "/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-bootstrap",
        f"{CONTROLLER_PREFIX} status; /bin/sh",
    )
    for command in rejected:
        with pytest.raises(dispatcher.DispatchError):
            dispatcher.select_dispatch(command, account=dispatcher.DEPLOY_USER)


def _private_ingress_root(tmp_path: Path) -> Path:
    root = tmp_path / "ingress"
    root.mkdir(mode=0o700)
    return root


def test_ingress_is_new_only_hash_bound_and_durable_shape(tmp_path: Path) -> None:
    root = _private_ingress_root(tmp_path)
    uid = os.getuid()
    gid = os.getgid()
    content = b"canonical release\n"
    digest = hashlib.sha256(content).hexdigest()

    assert ingress.prepare(TREE, root=root, expected_uid=uid, expected_gid=gid) == {
        "prepared": True,
        "schema_version": 1,
        "source_tree": TREE,
    }
    result = ingress.upload(
        TREE,
        "release.json",
        str(len(content)),
        digest,
        BytesIO(content),
        root=root,
        expected_uid=uid,
        expected_gid=gid,
    )
    assert result == {
        "name": "release.json",
        "schema_version": 1,
        "sha256": digest,
        "size": len(content),
        "source_tree": TREE,
        "uploaded": True,
    }
    destination = root / TREE / "release.json"
    assert destination.read_bytes() == content
    assert destination.stat().st_mode & 0o777 == 0o600

    with pytest.raises(ingress.IngressError, match="new-only"):
        ingress.upload(
            TREE,
            "release.json",
            str(len(content)),
            digest,
            BytesIO(content),
            root=root,
            expected_uid=uid,
            expected_gid=gid,
        )
    assert destination.read_bytes() == content

    assert ingress.abort(TREE, root=root, expected_uid=uid, expected_gid=gid) == {
        "aborted": True,
        "schema_version": 1,
        "source_tree": TREE,
    }
    assert not (root / TREE).exists()


@pytest.mark.parametrize(
    ("stream", "size", "digest", "message"),
    (
        (b"short", 6, hashlib.sha256(b"short!").hexdigest(), "before"),
        (b"extra", 4, hashlib.sha256(b"extr").hexdigest(), "exceeds"),
        (b"wrong", 5, hashlib.sha256(b"right").hexdigest(), "digest"),
    ),
)
def test_ingress_unlinks_every_partial_or_mismatched_upload(
    tmp_path: Path,
    stream: bytes,
    size: int,
    digest: str,
    message: str,
) -> None:
    root = _private_ingress_root(tmp_path)
    uid = os.getuid()
    gid = os.getgid()
    ingress.prepare(TREE, root=root, expected_uid=uid, expected_gid=gid)

    with pytest.raises(ingress.IngressError, match=message):
        ingress.upload(
            TREE,
            "validation.json",
            str(size),
            digest,
            BytesIO(stream),
            root=root,
            expected_uid=uid,
            expected_gid=gid,
        )
    assert not (root / TREE / "validation.json").exists()


def test_ingress_rejects_symlink_and_unknown_cleanup_targets(tmp_path: Path) -> None:
    root = _private_ingress_root(tmp_path)
    uid = os.getuid()
    gid = os.getgid()
    ingress.prepare(TREE, root=root, expected_uid=uid, expected_gid=gid)
    (root / TREE / "release.json").symlink_to(tmp_path / "outside")
    with pytest.raises(ingress.IngressError, match="unsafe"):
        ingress.abort(TREE, root=root, expected_uid=uid, expected_gid=gid)
    assert (root / TREE / "release.json").is_symlink()

    (root / TREE / "release.json").unlink()
    (root / TREE / "unexpected").write_text("do not delete", encoding="ascii")
    (root / TREE / "unexpected").chmod(0o600)
    with pytest.raises(ingress.IngressError, match="unknown"):
        ingress.abort(TREE, root=root, expected_uid=uid, expected_gid=gid)
    assert (root / TREE / "unexpected").read_text(encoding="ascii") == "do not delete"


def test_cloud_sshd_and_provisioner_remove_the_shared_ubuntu_service_key() -> None:
    sshd = (ROOT / "deploy/an2p/cloud/mooncen-an2p-deploy-sshd_config").read_text(
        encoding="utf-8"
    )
    provisioner = (
        ROOT / "deploy/an2p/cloud/provision_cloud_deploy_endpoint.sh"
    ).read_text(encoding="utf-8")

    assert (
        "AllowUsers mooncen_container_deploy mooncen_container_status "
        "mooncen_ops_db_tunnel"
    ) in sshd
    assert "AllowUsers ubuntu" not in sshd
    assert "AuthorizedKeysFile /etc/mooncen/ssh/authorized_keys/%u" in sshd
    assert sshd.count("ForceCommand /usr/local/libexec/mooncen-container-ssh-dispatch") == 2
    assert "Match User mooncen_ops_db_tunnel" in sshd
    assert "AllowTcpForwarding local" in sshd
    assert "PermitOpen 127.0.0.1:5432" in sshd
    assert "MaxSessions 0" in sshd
    assert "PermitTTY no" in sshd
    assert "Subsystem sftp" not in sshd

    assert "mooncen-an2p-container-deploy-20260819" in provisioner
    assert "mooncen-an2p-container-status-20260819" in provisioner
    assert "mooncen-an2p-ops-db-20260819" in provisioner
    assert "restrict,command=" in provisioner
    assert "restrict,port-forwarding,permitopen=" in provisioner
    assert "LEGACY_SHARED_PUBLIC_KEY" in provisioner
    assert "mooncen-an2p-deploy-20260819" in provisioner
    assert 'legacy_blob=$(printf' in provisioner
    assert "if blob not in tokens" in provisioner
    assert "legacy shared SSH key blob remains authorized" in provisioner
    assert "grep -Ev" not in provisioner
    assert "native-begin" not in provisioner
    assert "mooncen-container-bootstrap}" not in provisioner


def test_production_runbook_provisions_and_negatively_tests_split_endpoint() -> None:
    document = (ROOT / "docs/docker-production.md").read_text(encoding="utf-8")

    required = (
        "provision_cloud_deploy_endpoint.sh",
        "mooncen-an2p-container-deploy-20260819",
        "mooncen-an2p-container-status-20260819",
        "mooncen-an2p-ops-db-20260819",
        "mooncen-an2p-deploy-20260819",
        "mooncen_container_deploy",
        "mooncen_container_status",
        "mooncen_ops_db_tunnel",
        "sshd -T",
        "addr=100.64.198.9",
        "100.75.187.63:2222",
        "127.0.0.1:5432",
        "127.0.0.1:22",
        "sftp",
        "ssh -tt",
        "ExitOnForwardFailure=yes",
        "human interactive Tailscale",
    )
    assert all(value in document for value in required)
    command = document.split(
        "provision_cloud_deploy_endpoint.sh", 1
    )[1].split("```", 1)[0]
    assert "sudo /bin/bash" in document
    assert "reviewed-control.sha256" in document
    assert "sha256sum --check --strict" in document
    assert command.count("/root/mooncen-container-endpoint/") >= 8
    assert command.count(".pub") == 4
