from pathlib import Path

import pytest

from ops_agent import crawler_scheduler


ROOT = Path(__file__).resolve().parents[1]


def test_scheduler_defaults_to_reviewed_development_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("OPS_LOCAL_CRAWLER_PROVIDERS", raising=False)
    monkeypatch.delenv("CRAWLER_PROVIDERS", raising=False)
    monkeypatch.delenv("OPS_LOCAL_CRAWLER_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("CRAWLER_RUN_INTERVAL", raising=False)
    monkeypatch.delenv("OPS_LOCAL_CRAWLER_SCHEDULER_POLL_SECONDS", raising=False)

    config = crawler_scheduler.load_config()

    assert config.providers == crawler_scheduler.DEFAULT_PROVIDERS
    assert config.interval_seconds == 86_400
    assert config.poll_seconds == 30


def test_scheduler_accepts_registered_provider_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("OPS_LOCAL_CRAWLER_PROVIDERS", "homeplus, emart")

    assert crawler_scheduler.load_config().providers == ("HOMEPLUS", "EMART")


def test_scheduler_rejects_unknown_or_non_development_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("OPS_LOCAL_CRAWLER_PROVIDERS", "NOT_REVIEWED")
    with pytest.raises(RuntimeError, match="not registered"):
        crawler_scheduler.load_config()

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("OPS_LOCAL_CRAWLER_PROVIDERS", "HOMEPLUS")
    with pytest.raises(RuntimeError, match="development-only"):
        crawler_scheduler.load_config()


def test_local_launcher_cannot_start_data_workers() -> None:
    launcher = (ROOT / "start_ops_console.ps1").read_text(encoding="utf-8")

    assert "EnableLocalCrawlerRuntime" not in launcher
    assert "OPS_LOCAL_CRAWLER_RUNTIME_ENABLED" in launcher
    start_function = launcher.split("function Start-OpsConsole {", 1)[1].split(
        "function Refresh-OpsControl {", 1
    )[0]
    assert "ops_agent.crawler_scheduler" not in start_function
    assert "ops_agent.crawler_worker" not in start_function
    assert "ops_agent.quality_worker" not in start_function
    assert "gen1crawler direct owner path only" in start_function
