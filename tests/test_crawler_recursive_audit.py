import argparse
from pathlib import Path

import pytest

from tools import crawler_recursive_audit as audit


def test_declared_providers_are_read_without_importing_script(tmp_path: Path):
    script = tmp_path / "Crawler_Group.py"
    script.write_text(
        'PROVIDER = "ONE"\nPROVIDERS = ["TWO", "three"]\nraise RuntimeError("must not import")\n',
        encoding="utf-8",
    )

    assert audit.declared_providers_from_script(script) == {"ONE", "TWO", "THREE"}

    script.write_text('\ufeffPROVIDER = "BOM_PROVIDER"\n', encoding="utf-8")
    assert audit.declared_providers_from_script(script) == {"BOM_PROVIDER"}


def test_support_modules_are_not_crawler_entrypoints(tmp_path: Path):
    assert audit.is_crawler_entrypoint(tmp_path / "Crawler_Example.py")
    assert not audit.is_crawler_entrypoint(tmp_path / "site_adapters.py")
    assert not audit.is_crawler_entrypoint(tmp_path / "__init__.py")


def test_crawler_paths_cannot_escape_project():
    assert audit.normalize_crawler_path("Crawler/example.py") == (audit.ROOT / "Crawler/example.py").resolve()
    assert audit.normalize_crawler_path("../outside.py") is None
    assert audit.normalize_crawler_path("Crawler/../outside.py") is None
    assert audit.normalize_crawler_path("C:/outside.py") is None


def test_audit_numeric_arguments_have_hard_bounds():
    parse_iterations = audit.int_between(1, 100)

    assert parse_iterations("100") == 100
    with pytest.raises(argparse.ArgumentTypeError):
        parse_iterations("0")


def test_generated_wrapper_provider_mismatch_is_blocking_evidence(tmp_path: Path, monkeypatch):
    wrapper_dir = tmp_path / audit.GENERATED_WRAPPER_DIR
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "EXPECTED.py").write_text('PROVIDER = "WRONG"\n', encoding="utf-8")
    (wrapper_dir / "EXTRA.py").write_text('PROVIDER = "EXTRA"\n', encoding="utf-8")
    support_module = wrapper_dir / "manual_generic_crawler.py"
    support_module.write_text("def run_cli(provider): return 0\n", encoding="utf-8")
    monkeypatch.setattr(audit, "ROOT", tmp_path)

    _paths, issues = audit.audit_generated_wrappers(
        [{"provider": "EXPECTED", "_registry": audit.GENERATED_REGISTRY}],
        include_unexpected=True,
    )

    assert {issue["reason"] for issue in issues} == {
        "generated_wrapper_provider_mismatch",
        "unexpected_generated_wrapper",
    }
    assert not audit.is_crawler_entrypoint(support_module)


def test_generated_registry_wrapper_is_cross_checked():
    result = audit.build_audit(
        registry_paths=[audit.ROOT / audit.GENERATED_REGISTRY],
        compile_check=False,
        provider_filter={"SUWON_LIBRARY_MA"},
    )

    assert result["generated_wrapper_issues"] == []
    assert result["unregistered_scripts"] == []
    assert result["summary"]["generated_wrapper_issues"] == 0
    assert result["summary"]["invalid_crawler_paths"] == 0
    assert result["summary"]["invalid_runner_commands"] == 0
    assert result["summary"]["runner_without_registry"] == 0
