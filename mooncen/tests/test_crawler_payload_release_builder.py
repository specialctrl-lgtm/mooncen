from __future__ import annotations

import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from ops_agent.crawler_builder_evidence import TICKET_FORMAT, BuilderTicket
from tools import build_crawler_payload_release as builder


def _git(root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    process = subprocess.run(
        ["git", *arguments],
        cwd=root,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise AssertionError(process.stderr.decode("utf-8", "replace"))
    return process.stdout


def _repository(tmp_path: Path) -> tuple[Path, bytes, bytes]:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Payload Test")
    _git(root, "config", "user.email", "payload@example.invalid")
    crawler = b"print('committed crawler')\n"
    runner = b"print('committed runner')\n"
    (root / "Crawler").mkdir()
    (root / "Crawler" / "main.py").write_bytes(crawler)
    (root / "run_crawlers.py").write_bytes(runner)
    _git(root, "add", "--", "Crawler/main.py", "run_crawlers.py")
    _git(root, "commit", "--quiet", "-m", "payload fixture")
    return root.resolve(), crawler, runner


def _reviewed_fixture_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = ["Crawler/main.py"]
    digest = hashlib.sha256(("\n".join(paths) + "\n").encode("ascii")).hexdigest()
    monkeypatch.setattr(builder, "RUNTIME_EXACT_PATHS", ("run_crawlers.py",))
    monkeypatch.setattr(builder, "EXPECTED_CRAWLER_PATH_COUNT", 1)
    monkeypatch.setattr(builder, "EXPECTED_CRAWLER_PATHS_SHA256", digest)
    monkeypatch.setattr(builder, "RUNTIME_IMPORT_SMOKE", ("run_crawlers",))
    monkeypatch.setattr(builder, "RUNTIME_REQUIRED_DATA_BINDINGS", ())


def _ticket(root: Path, crawler: bytes) -> BuilderTicket:
    commit = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    tree = _git(root, "rev-parse", "HEAD^{tree}").decode("ascii").strip()
    return BuilderTicket.parse(
        {
            "format": TICKET_FORMAT,
            "ticket_id": "00000000-0000-4000-8000-000000000001",
            "build_request_id": "00000000-0000-4000-8000-000000000002",
            "environment": "staging",
            "request_digest": "1" * 64,
            "source_commit": commit,
            "source_tree": tree,
            "code_version": "fixture-1",
            "config_revision": "fixture-config-1",
            "test_profile": "crawler",
            "source_approval_receipt_id": "00000000-0000-4000-8000-000000000003",
            "source_approval_digest": "2" * 64,
            "source_approver_login": "crawler_source_approver",
            "source_approved_at": "2026-08-12T00:00:00.000000Z",
            "sources": [
                {
                    "draft_id": "00000000-0000-4000-8000-000000000004",
                    "revision": 1,
                    "source_path": "Crawler/main.py",
                    "source_sha256": hashlib.sha256(crawler).hexdigest(),
                }
            ],
        }
    )


def _build(root: Path, ticket: BuilderTicket, output: Path) -> dict[str, object]:
    output.mkdir()
    return builder.build_release(root, ticket, output.resolve())


def test_payload_is_byte_identical_and_ignores_dirty_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reviewed_fixture_contract(monkeypatch)
    root, crawler, _runner = _repository(tmp_path)
    ticket = _ticket(root, crawler)
    first = tmp_path / "first"
    first_result = _build(root, ticket, first)

    # Neither a modified tracked file nor an untracked secret can enter a
    # commit-only build.
    (root / "Crawler" / "main.py").write_text("dirty\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=not-packaged\n", encoding="utf-8")
    second = tmp_path / "second"
    second_result = _build(root, ticket, second)

    for name in (
        builder.ARCHIVE_NAME,
        builder.EVIDENCE_NAME,
        f"{builder.CONTENT_MANIFEST_NAME}.detached",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert first_result == second_result
    assert first_result["registration_ready"] is False

    archive_bytes = (first / builder.ARCHIVE_NAME).read_bytes()
    assert hashlib.sha256(archive_bytes).hexdigest() == first_result["archive_sha256"]
    with gzip.GzipFile(fileobj=io.BytesIO(archive_bytes), mode="rb") as compressed:
        tar_bytes = compressed.read()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        assert names == sorted(names, key=str.encode)
        assert names == [
            builder.CONTENT_MANIFEST_NAME,
            "Crawler/main.py",
            builder.RELEASE_MANIFEST_NAME,
            "run_crawlers.py",
        ]
        assert all(member.uid == 0 and member.gid == 0 and member.mtime == 0 for member in members)
        assert all(member.isfile() and member.name not in {".env"} for member in members)
        manifest_handle = archive.extractfile(builder.CONTENT_MANIFEST_NAME)
        assert manifest_handle is not None
        manifest = json.loads(manifest_handle.read())
    assert manifest["source_commit"] == ticket.source_commit
    assert manifest["source_tree"] == ticket.source_tree
    assert {row["path"] for row in manifest["files"]} == {
        "Crawler/main.py",
        "run_crawlers.py",
    }
    assert all(len(row["blob_oid"]) == 40 and len(row["sha256"]) == 64 for row in manifest["files"])


def test_ticket_source_digest_must_match_committed_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reviewed_fixture_contract(monkeypatch)
    root, crawler, _runner = _repository(tmp_path)
    raw = _ticket(root, crawler).document()
    raw["sources"][0]["source_sha256"] = "f" * 64
    ticket = BuilderTicket.parse(raw)
    with pytest.raises(builder.PayloadBuildError, match="Studio source digest differs"):
        _build(root, ticket, tmp_path / "output")


def test_symlink_mode_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reviewed_fixture_contract(monkeypatch)
    root, crawler, _runner = _repository(tmp_path)
    blob = _git(root, "hash-object", "-w", "--stdin", input_bytes=b"run_crawlers.py").decode().strip()
    _git(root, "update-index", "--add", "--cacheinfo", f"120000,{blob},Crawler/main.py")
    _git(root, "commit", "--quiet", "-m", "symlink mode")
    ticket = _ticket(root, b"run_crawlers.py")
    with pytest.raises(builder.PayloadBuildError, match="symlink|special mode"):
        _build(root, ticket, tmp_path / "output")


def test_gitlink_anywhere_in_crawler_runtime_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reviewed_fixture_contract(monkeypatch)
    root, crawler, _runner = _repository(tmp_path)
    parent = _git(root, "rev-parse", "HEAD").decode().strip()
    _git(root, "update-index", "--add", "--cacheinfo", f"160000,{parent},Crawler/vendor")
    _git(root, "commit", "--quiet", "-m", "gitlink mode")
    ticket = _ticket(root, crawler)
    with pytest.raises(builder.PayloadBuildError, match="gitlink"):
        _build(root, ticket, tmp_path / "output")


def test_oversized_blob_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reviewed_fixture_contract(monkeypatch)
    root, crawler, _runner = _repository(tmp_path)
    ticket = _ticket(root, crawler)
    monkeypatch.setattr(builder, "MAX_FILE_BYTES", 4)
    with pytest.raises(builder.PayloadBuildError, match="exceeded its bound|size"):
        _build(root, ticket, tmp_path / "output")


def test_path_traversal_unicode_and_casefold_collisions_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(builder.PayloadBuildError, match="non-canonical|unsafe"):
        builder._validate_path("Crawler/../secret.py", selected=True)
    with pytest.raises(builder.PayloadBuildError, match="non-canonical"):
        builder._validate_path("Crawler/e\u0301.py", selected=False)

    paths = ["Crawler/Main.py", "Crawler/main.py"]
    digest = hashlib.sha256(("\n".join(paths) + "\n").encode("ascii")).hexdigest()
    monkeypatch.setattr(builder, "RUNTIME_EXACT_PATHS", ())
    monkeypatch.setattr(builder, "EXPECTED_CRAWLER_PATH_COUNT", 2)
    monkeypatch.setattr(builder, "EXPECTED_CRAWLER_PATHS_SHA256", digest)
    entries = {
        path: builder.GitEntry("100644", "blob", "1" * 40, path) for path in paths
    }
    with pytest.raises(builder.PayloadBuildError, match="case-insensitive"):
        builder._selected_entries(entries)


def test_secret_path_filter_alternate_and_git_environment_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reviewed_fixture_contract(monkeypatch)
    root, crawler, _runner = _repository(tmp_path)
    ticket = _ticket(root, crawler)

    (root / "Crawler" / "private.key").write_text("secret\n", encoding="utf-8")
    _git(root, "add", "--", "Crawler/private.key")
    _git(root, "commit", "--quiet", "-m", "secret path")
    secret_ticket = _ticket(root, crawler)
    with pytest.raises(builder.PayloadBuildError, match="secret-bearing"):
        _build(root, secret_ticket, tmp_path / "secret-output")

    _git(root, "reset", "--hard", "HEAD~1")
    _git(root, "config", "filter.unreviewed.clean", "external-transform")
    with pytest.raises(builder.PayloadBuildError, match="forbidden object source"):
        _build(root, ticket, tmp_path / "filter-output")
    _git(root, "config", "--unset-all", "filter.unreviewed.clean")

    alternates = root / ".git" / "objects" / "info" / "alternates"
    alternates.write_text("C:/unreviewed/objects\n", encoding="ascii")
    with pytest.raises(builder.PayloadBuildError, match="alternate"):
        _build(root, ticket, tmp_path / "alternate-output")

    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(root / ".git" / "objects"))
    with pytest.raises(builder.PayloadBuildError, match="environment override"):
        builder._git_environment()


def test_repository_config_includes_cannot_supply_object_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reviewed_fixture_contract(monkeypatch)
    root, crawler, _runner = _repository(tmp_path)
    ticket = _ticket(root, crawler)
    external = tmp_path / "external.gitconfig"
    external.write_text(
        "[filter \"external\"]\n\tclean = unreviewed-transform\n",
        encoding="ascii",
    )
    with (root / ".git" / "config").open("a", encoding="ascii") as handle:
        handle.write(f"\n[include]\n\tpath = {external.as_posix()}\n")
    # Includes are never followed; their key is inert, and the committed build
    # remains sourced from the repository-local object database only.
    result = _build(root, ticket, tmp_path / "include-output")
    assert result["registration_ready"] is False


def test_git_environment_and_cli_expose_no_caller_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_OBJECT_DIRECTORY", raising=False)
    environment = builder._git_environment()
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert "PYTHONPATH" not in environment
    assert "DATABASE_URL" not in environment
    with pytest.raises(SystemExit):
        builder._parser().parse_args(["--repository-root", "/tmp/repo"])


def test_first_party_import_closure_rejects_omitted_tree_module() -> None:
    files = {
        "run_crawlers.py": builder.ReleaseFile(
            "run_crawlers.py",
            0o644,
            "1" * 40,
            b"from tools.sample_collect_from_yaml import collect_provider\n",
        )
    }
    entries = {
        "run_crawlers.py": builder.GitEntry("100644", "blob", "1" * 40, "run_crawlers.py"),
        "tools/sample_collect_from_yaml.py": builder.GitEntry(
            "100644", "blob", "2" * 40, "tools/sample_collect_from_yaml.py"
        ),
    }
    with pytest.raises(builder.PayloadBuildError, match="omits a first-party Python import"):
        builder._validate_first_party_import_closure(files, entries)


def test_production_runtime_smoke_contract_covers_agent_command_and_scheduled_data() -> None:
    assert {
        "run_crawlers",
        "Crawler.Crawler_YamlSources",
        "Crawler.Crawler_MunicipalYaml",
        "ops_agent.crawler_worker",
        "backend.ops.service",
        "tools.parser_probe",
    } <= set(builder.RUNTIME_IMPORT_SMOKE)
    required_data = {path for _attribute, path in builder.RUNTIME_REQUIRED_DATA_BINDINGS}
    assert required_data == {
        "config/municipal_course_candidate_results.yaml",
        "config/municipal_course_candidate_results_sugang_sports.yaml",
        "config/municipal_course_search_targets.yaml",
        "config/national_keyword_course_search_targets.yaml",
        "config/national_institution_course_search_targets.yaml",
        "config/museum_course_search_targets.yaml",
    }
    assert required_data <= set(builder.RUNTIME_EXACT_PATHS)
    assert {
        "tools/sample_collect_from_yaml.py",
        "tools/parser_probe.py",
        "backend/ops/__init__.py",
        "backend/ops/service.py",
    } <= set(builder.RUNTIME_EXACT_PATHS)


def _runtime_smoke_fixture_archive(
    *, include_all_data: bool = True
) -> tuple[bytes, list[builder.ReleaseFile]]:
    binding_lines = [
        f'{attribute} = ROOT / "{relative}"'
        for attribute, relative in builder.RUNTIME_REQUIRED_DATA_BINDINGS
    ]
    municipal_module = (
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        + "\n".join(binding_lines)
        + "\n"
        + "def load_targets(source):\n"
        + "    assert source == 'collected'\n"
        + "    for path in ("
        + ",".join(attribute for attribute, _relative in builder.RUNTIME_REQUIRED_DATA_BINDINGS)
        + ",):\n"
        + "        assert path.read_bytes()\n"
        + "    return [SimpleNamespace(provider='fixture', url='https://example.invalid', source='fixture')]\n"
    ).encode("ascii")
    modules = {
        "run_crawlers.py": b"READY = True\n",
        "Crawler/Crawler_YamlSources.py": b"READY = True\n",
        "Crawler/Crawler_MunicipalYaml.py": municipal_module,
        "ops_agent/crawler_worker.py": b"READY = True\n",
        "backend/ops/__init__.py": b'"""Fixture package."""\n',
        "backend/ops/service.py": b"READY = True\n",
        "tools/parser_probe.py": b"READY = True\n",
    }
    files = [
        builder.ReleaseFile(path, 0o644, "1" * 40, content)
        for path, content in modules.items()
    ]
    data_paths = [relative for _attribute, relative in builder.RUNTIME_REQUIRED_DATA_BINDINGS]
    if not include_all_data:
        data_paths = data_paths[:-1]
    files.extend(
        builder.ReleaseFile(path, 0o644, "2" * 40, b"targets: []\n") for path in data_paths
    )
    files.sort(key=lambda item: item.path.encode("ascii"))
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=buffer, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
            for item in files:
                builder._add_tar_file(archive, item)
    return buffer.getvalue(), files


def test_archive_runtime_smoke_imports_full_contract_and_opens_data(tmp_path: Path) -> None:
    archive, files = _runtime_smoke_fixture_archive()
    output = tmp_path / "runtime-smoke"
    output.mkdir()
    builder._validate_archive_runtime(archive, output.resolve(), files)

    incomplete_archive, incomplete_files = _runtime_smoke_fixture_archive(include_all_data=False)
    with pytest.raises(builder.PayloadBuildError, match="runtime import smoke failed"):
        builder._validate_archive_runtime(
            incomplete_archive,
            output.resolve(),
            incomplete_files,
        )
