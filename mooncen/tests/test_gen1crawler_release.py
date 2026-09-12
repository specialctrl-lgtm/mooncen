from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from tools.build_gen1crawler_release import BuildError, REQUIRED_PATHS, build


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repository"
    root = repo / "mooncen"
    root.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI")
    for name in REQUIRED_PATHS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{name}\n", encoding="utf-8")
    (root / "Crawler").mkdir()
    (root / "Crawler" / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "mooncen")
    _git(repo, "commit", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_gen1crawler_release_is_commit_only_and_deterministic(tmp_path: Path) -> None:
    repo, commit = _repository(tmp_path)
    first = build(repo, commit, tmp_path / "first")
    (repo / "mooncen" / "run_crawlers.py").write_text("DIRTY = True\n", encoding="utf-8")
    second = build(repo, commit, tmp_path / "second")

    assert first["archive_sha256"] == second["archive_sha256"]
    assert first["tree_sha256"] == second["tree_sha256"]
    assert first["source_tree"] == _git(repo, "rev-parse", f"{commit}:mooncen")
    assert hashlib.sha256(Path(first["metadata"]).read_bytes()).hexdigest()


def test_gen1crawler_release_rejects_symlink(tmp_path: Path) -> None:
    repo, _commit = _repository(tmp_path)
    link = repo / "mooncen" / "Crawler" / "unsafe-link"
    link.symlink_to("sample.py")
    _git(repo, "add", "mooncen/Crawler/unsafe-link")
    _git(repo, "commit", "-m", "unsafe")
    commit = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(BuildError, match="non-regular"):
        build(repo, commit, tmp_path / "output")


def test_root_activator_has_fixed_signature_and_rollback_boundaries() -> None:
    source = Path("deploy/ubuntu/activate_gen1crawler_release.sh").read_text(
        encoding="utf-8"
    )
    assert "mooncen-gen1crawler-release" in source
    assert "ssh-keygen -Y verify" in source
    assert "/etc/mooncen/gen1crawler-release-allowed-signers" in source
    assert "flock -n 9" in source
    assert "mv -Tf" in source
    assert "restore()" in source
    assert "--verify-active" in source
    assert "MOONCEN_GEN1CRAWLER_RELEASE_VERIFIED" in source
    assert "mooncen-crawler-once.service" in source
    assert "mooncen-staging-apply.service" in source
    assert "/var/lib/mooncen-crawler/logs" in source
    assert "active log tree contains links or special files" in source
    assert "runtime service user is not in the mooncen group" in source
    assert "sys.path.insert(0, root)" in source
    assert 'runpy.run_path(sys.argv[0], run_name="__main__")' in source
    assert 'python" -I "$candidate/run_crawlers.py"' not in source
    assert 'find "$candidate" -type f -exec chmod g+r,o-rwx {} +' in source
    assert "mooncen-gen1crawler-ops" in source
    assert "crawler-once-start" in source
    assert "crawler-status" in source
    assert "ops_service_helper.sh" in source
    assert "ops_service_action.py" in source
    assert "restore_managed_file helper" in source


def test_uploader_bootstrap_installs_only_fixed_helper_sudo_rule() -> None:
    source = Path("deploy/ubuntu/install_gen1crawler_release_uploader.sh").read_text(
        encoding="utf-8"
    )
    assert "visudo -cf" in source
    assert "NOPASSWD: /usr/local/libexec/mooncen-activate-gen1crawler-release" in source
    assert "NOPASSWD: ALL" not in source
    assert "gen1crawler-release-allowed-signers" in source


def test_windows_transport_accepts_one_exact_proof_among_native_noise() -> None:
    source = Path("deploy/ubuntu/deploy_gen1crawler_release_from_windows.ps1").read_text(
        encoding="utf-8"
    )
    assert "$proofLines | Where-Object { $_ -ceq $expected }" in source
    assert "$verifiedLines | Where-Object { $_ -ceq $expectedVerified }" in source
    assert "$bootstrapProof = & ssh" in source
    assert "gen1crawler-release-bootstrap-ok" in source


def test_outer_launcher_requires_both_exact_gen1crawler_provenance_proofs() -> None:
    source = Path("deploy_mooncen.ps1").read_text(encoding="utf-8")
    function = source.split("function Invoke-Gen1CrawlerUpdate", 1)[1].split(
        "function Invoke-CrawlerControlInstall", 1
    )[0]
    assert "$proofLines.Count -ne 2" in function
    assert "MOONCEN_GEN1CRAWLER_RELEASE_ACTIVATED=" in function
    assert "MOONCEN_GEN1CRAWLER_RELEASE_VERIFIED=" in function
    assert "-ceq $expectedActivated" in function
    assert "-ceq $expectedVerified" in function
