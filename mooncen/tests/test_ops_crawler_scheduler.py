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


def test_local_launcher_requires_explicit_opt_in_for_data_workers() -> None:
    launcher = (ROOT / "start_ops_console.ps1").read_text(encoding="utf-8")

    assert "[switch]$EnableLocalCrawlerRuntime" in launcher
    assert "if ($EnableLocalCrawlerRuntime)" in launcher
    assert "OPS_LOCAL_CRAWLER_RUNTIME_ENABLED" in launcher
    assert '@("-m", "ops_agent.crawler_scheduler", "--check")' in launcher
    assert '@{ name = "crawler-scheduler"; module = "ops_agent.crawler_scheduler"' in launcher
    assert '@{ name = "crawler-worker"; module = "ops_agent.crawler_worker"' in launcher
    assert '@{ name = "quality-worker"; module = "ops_agent.quality_worker"' in launcher
    assert "Local crawler/quality runtime: disabled" in launcher
