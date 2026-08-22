from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import tools.deactivate_duplicate_provider_courses as module


def test_duplicate_config_evidence_requires_exact_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target_dir = tmp_path / "targets"
    target_dir.mkdir()
    (target_dir / "sample.yaml").write_text(
        yaml.safe_dump(
            {
                "targets": [
                    {
                        "provider": "MUNI_DUPLICATE",
                        "duplicate_of": "MUNI_CANONICAL",
                        "superseded_by": "MUNI_CANONICAL",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "TARGET_DIR", target_dir)

    assert module.duplicate_config_evidence("MUNI_DUPLICATE", "MUNI_CANONICAL") == ["sample.yaml:0"]


def test_duplicate_config_evidence_rejects_wrong_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "targets"
    target_dir.mkdir()
    (target_dir / "sample.yaml").write_text(
        yaml.safe_dump(
            {"targets": [{"provider": "MUNI_DUPLICATE", "duplicate_of": "MUNI_OTHER"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "TARGET_DIR", target_dir)

    with pytest.raises(RuntimeError, match="duplicate_of"):
        module.duplicate_config_evidence("MUNI_DUPLICATE", "MUNI_CANONICAL")
