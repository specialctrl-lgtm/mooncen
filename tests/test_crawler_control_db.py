from __future__ import annotations

import pytest

from ops_agent import crawler_control_db


def test_control_database_uses_explicit_control_role_not_worker_queue_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DB_SSLMODE", "verify-full")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_HOST", "staging-db")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_PORT", "5432")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_NAME", "mooncen")
    monkeypatch.setenv("OPS_CRAWLER_CONTROL_DB_USER", "mooncen_control_login")
    monkeypatch.setenv("OPS_CRAWLER_CONTROL_DB_PASSWORD", "secret")
    monkeypatch.setenv("OPS_QUEUE_DB_USER", "mooncen_crawler_login")

    config = crawler_control_db.control_database_config()

    assert config["host"] == "staging-db"
    assert config["user"] == "mooncen_control_login"
    assert config["password"] == "secret"
    assert config["application_name"] == "mooncen-crawler-control"


def test_control_database_fails_closed_without_control_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_HOST", "staging-db")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_PORT", "5432")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_NAME", "mooncen")
    monkeypatch.setenv("DB_API_USER", "mooncen_api_login")
    monkeypatch.setenv("DB_API_PASSWORD", "legacy-secret")
    monkeypatch.delenv("OPS_CRAWLER_CONTROL_DB_USER", raising=False)
    monkeypatch.delenv("OPS_CRAWLER_CONTROL_DB_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="Explicit OPS_CRAWLER_CONTROL_DB"):
        crawler_control_db.control_database_config()


def test_finalizer_database_uses_applier_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DB_SSLMODE", "verify-full")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_HOST", "staging-db")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_PORT", "5432")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_NAME", "mooncen")
    monkeypatch.setenv("OPS_CRAWLER_FINALIZER_DB_USER", "mooncen_applier_login")
    monkeypatch.setenv("OPS_CRAWLER_FINALIZER_DB_PASSWORD", "secret")

    config = crawler_control_db.finalizer_database_config()

    assert config["user"] == "mooncen_applier_login"
    assert config["application_name"] == "mooncen-crawler-finalizer"


def test_shared_database_endpoint_is_explicit_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("OPS_CRAWLER_SHARED_DB_HOST", raising=False)
    monkeypatch.delenv("OPS_CRAWLER_SHARED_DB_PORT", raising=False)
    monkeypatch.delenv("OPS_CRAWLER_SHARED_DB_NAME", raising=False)

    with pytest.raises(RuntimeError, match="explicit"):
        crawler_control_db.shared_database_endpoint()
