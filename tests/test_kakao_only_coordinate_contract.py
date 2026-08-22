from __future__ import annotations

from pathlib import Path

import run_crawlers as runner


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_coordinate_tools_contain_no_google_maps_api_endpoint() -> None:
    paths = [
        "run_crawlers.py",
        "tools/maintenance/kakao_geocode_branches.py",
        "tools/maintenance/google_geocode_branches.py",
        "tools/maintenance/audit_emart_branch_locations.py",
        "tools/maintenance/backfill_missing_branch_addresses.py",
        "tools/maintenance/backfill_branch_operator_addresses.py",
    ]
    for path in paths:
        source = _read(path).lower()
        assert "maps.googleapis.com/maps/api" not in source, path


def test_deployment_never_accepts_or_installs_google_maps_key() -> None:
    paths = [
        "deploy_ubuntu.ps1",
        "deploy_mooncen.ps1",
        "deploy.local.example.ps1",
        "deploy/ubuntu/deploy_from_windows.ps1",
        "deploy/ubuntu/setup_project.sh",
        "deploy/ubuntu/setup_split_crawler.sh",
    ]
    for path in paths:
        source = _read(path)
        if path.endswith("setup_project.sh"):
            # The build sanitizer deliberately removes stale inherited keys.
            before, sanitizer_and_after = source.split(
                "without_runtime_secrets() {",
                1,
            )
            _sanitizer, after = sanitizer_and_after.split("\n}", 1)
            source = before + after
        assert "GoogleMapsApiKey" not in source, path
        assert "GOOGLE_MAPS_API_KEY=" not in source, path


def test_active_frontend_is_kakao_only() -> None:
    package = _read("frontend2/package.json").lower()
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "frontend2" / "src").rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".jsx"}
    ).lower()
    assert "google-maps" not in package
    assert "@react-google-maps" not in sources
    assert "maps.googleapis.com/maps/api" not in sources
    assert "dapi.kakao.com" in sources


def test_maintenance_environment_strips_stale_google_map_credentials(
    monkeypatch,
) -> None:
    for name in (
        "GOOGLE_MAPS_API_KEY",
        "VITE_GOOGLE_MAPS_API_KEY",
        "MoonCenGoogleMapsApiKey",
    ):
        monkeypatch.setenv(name, "retired-secret")

    environment = runner.maintenance_env()

    assert not any("retired-secret" == value for value in environment.values())
    assert not any("GOOGLE_MAPS" in name for name in environment)
    assert "MoonCenGoogleMapsApiKey" not in environment
