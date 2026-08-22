from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from deploy.docker import render_runtime_config as renderer


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "docker" / "render_runtime_config.py"
EXPECTED_FIELDS = {
    "siteUrl",
    "oauthRedirectUri",
    "kakaoMapsJavaScriptKey",
    "googleOAuthClientId",
    "naverOAuthClientId",
}


def _payload(script: str) -> dict[str, str]:
    match = re.search(r"Object\.freeze\((\{.*\})\);", script)
    assert match is not None
    payload = json.loads(match.group(1))
    assert isinstance(payload, dict)
    return payload


def test_renderer_outputs_only_allowlisted_json_safe_public_values(tmp_path: Path) -> None:
    output = tmp_path / "runtime-config.js"
    dangerous_public_value = 'client-id</script>&"quoted"'
    environment = {
        "MOONCEN_SITE_URL": " https://development.example.test/ ",
        "MOONCEN_OAUTH_REDIRECT_URI": "https://development.example.test/callback",
        "MOONCEN_KAKAO_MAPS_JAVASCRIPT_KEY": dangerous_public_value,
        "MOONCEN_GOOGLE_OAUTH_CLIENT_ID": "google-public-id",
        "MOONCEN_NAVER_OAUTH_CLIENT_ID": "naver-public-id",
        "MOONCEN_DB_PASSWORD": "must-never-appear",
        "MOONCEN_GOOGLE_OAUTH_CLIENT_SECRET": "must-never-appear-either",
    }

    renderer.render_to_file(output, environment)

    script = output.read_text(encoding="utf-8")
    payload = _payload(script)
    assert set(payload) == EXPECTED_FIELDS
    assert payload["siteUrl"] == "https://development.example.test/"
    assert payload["kakaoMapsJavaScriptKey"] == dangerous_public_value
    assert "must-never-appear" not in script
    assert "</script>" not in script
    assert r"\u003c/script\u003e\u0026" in script
    assert stat.S_IMODE(output.stat().st_mode) == 0o644


def test_cli_atomically_replaces_an_existing_file_with_mode_0644(tmp_path: Path) -> None:
    output = tmp_path / "runtime-config.js"
    output.write_text("stale\n", encoding="utf-8")
    output.chmod(0o600)
    environment = {
        "MOONCEN_SITE_URL": "https://mooncen.example.test",
        "UNRELATED_SECRET": "not-public",
    }

    result = subprocess.run(
        [sys.executable, os.fspath(SCRIPT), "--output", os.fspath(output)],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert _payload(output.read_text(encoding="utf-8"))["siteUrl"] == environment["MOONCEN_SITE_URL"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o644
    assert list(tmp_path.glob(".runtime-config.js.*.tmp")) == []


def test_cli_reads_public_values_from_private_file_without_inheriting_secrets(
    tmp_path: Path,
) -> None:
    output = tmp_path / "runtime-config.js"
    environment_file = tmp_path / "development.env"
    environment_file.write_text(
        "\n".join(
            (
                "MOONCEN_SITE_URL=https://installed.example.test",
                "MOONCEN_DB_PASSWORD=must-not-render",
                "PYTHONPATH=/must/not/control/the-started-interpreter",
                "",
            )
        ),
        encoding="utf-8",
    )
    environment_file.chmod(0o600)

    result = subprocess.run(
        [
            sys.executable,
            os.fspath(SCRIPT),
            "--environment-file",
            os.fspath(environment_file),
            "--output",
            os.fspath(output),
        ],
        check=False,
        capture_output=True,
        env={"MOONCEN_SITE_URL": "https://inherited.invalid"},
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    script = output.read_text(encoding="utf-8")
    assert _payload(script)["siteUrl"] == "https://installed.example.test"
    assert "must-not-render" not in script
    assert "must/not/control" not in script

    environment_file.chmod(0o640)
    with pytest.raises(renderer.RuntimeConfigError, match="unsafe"):
        renderer.environment_file(environment_file)


def test_renderer_accepts_the_protected_system_file_contract_for_its_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment_file = tmp_path / "development.env"
    environment_file.write_text("MOONCEN_SITE_URL=https://system.example.test\n", encoding="utf-8")
    environment_file.chmod(0o640)
    monkeypatch.setattr(renderer, "ROOT_UID", os.getuid())
    actual_gid = os.getgid()

    values = renderer.environment_file(environment_file)
    assert values["MOONCEN_SITE_URL"] == "https://system.example.test"

    monkeypatch.setattr(renderer.os, "getgid", lambda: actual_gid + 1)
    with pytest.raises(renderer.RuntimeConfigError, match="unsafe"):
        renderer.environment_file(environment_file)


@pytest.mark.parametrize(
    "value",
    ["line one\nline two", "tab\tvalue", "x" * (renderer.MAX_PUBLIC_VALUE_LENGTH + 1)],
)
def test_renderer_rejects_unsafe_public_values(tmp_path: Path, value: str) -> None:
    output = tmp_path / "runtime-config.js"

    with pytest.raises(renderer.RuntimeConfigError):
        renderer.render_to_file(output, {"MOONCEN_SITE_URL": value})

    assert not output.exists()


def test_render_javascript_rejects_missing_or_extra_fields() -> None:
    with pytest.raises(renderer.RuntimeConfigError):
        renderer.render_javascript({"siteUrl": "https://mooncen.example.test"})

    config = renderer.public_config({})
    config["serverSecret"] = "not-public"
    with pytest.raises(renderer.RuntimeConfigError):
        renderer.render_javascript(config)


def test_atomic_writer_refuses_a_symlink_destination(tmp_path: Path) -> None:
    real_file = tmp_path / "real-runtime-config.js"
    real_file.write_text("keep\n", encoding="utf-8")
    output = tmp_path / "runtime-config.js"
    output.symlink_to(real_file.name)

    with pytest.raises(renderer.RuntimeConfigError):
        renderer.render_to_file(output, {})

    assert real_file.read_text(encoding="utf-8") == "keep\n"
    assert output.is_symlink()
