from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "decommission_docker_runtime.sh"


def test_decommission_requires_explicit_host_specific_confirmation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "an2p:REMOVE-AN2P-DOCKER|cloud:REMOVE-CLOUD-DOCKER" in source
    assert '"$(hostname -s)" = an2p' in source
    assert '"$(hostname -s)" = mooncen' in source


def test_an2p_decommission_proves_native_health_before_archiving() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    health = source.index("native API health failed")
    archive = source.index("/opt/mooncen-an2p-runtime", health)

    assert "registration_sha256\") != \"0\" * 64" in source
    assert "pending_sha256\") != hashlib.sha256(pending_payload).hexdigest()" in source
    assert "partial control transaction residue exists" in source
    assert 'if [ "$transaction_present" = true ]; then' in source
    assert "native selector is unavailable while a transaction still exists" in source
    assert "systemctl --user --machine=sgm@ is-active --quiet mooncen-api.service" in source
    assert "systemctl --user --machine=sgm@ is-active --quiet mooncen-frontend.service" in source
    assert health < archive


def test_cloud_decommission_refuses_active_transition_state() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"$state/active.json"' in source
    assert '"$state/transaction.json"' in source
    assert '"$transition/native-bootstrap-intent.json"' in source
    assert "active runtime state blocks decommission" in source
    assert "/etc/systemd/system/multi-user.target.wants/mooncen-container-stack.service" in source


def test_cleanup_is_scoped_to_mooncen_docker_objects() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    python_block = source.split("<<'PY'", 1)[1].split("\nPY\n", 1)[0]

    assert "docker system prune" not in source
    assert "docker volume prune" not in source
    assert "docker network prune" not in source
    assert "mooncen-dev mooncen-production" in source
    assert "^mooncen-smoke-" in source
    assert "mooncen\\/(api|frontend|postgres|ops-console-static):" in source
    assert "mooncen-monitoring" not in source
    assert "remove_mooncen_docker_objects()" not in python_block
    assert source.index("\nPY\n") < source.index("remove_mooncen_docker_objects()")
    compile(python_block, str(SCRIPT), "exec")
