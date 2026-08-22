from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests


ROOT = Path(__file__).resolve().parents[1]


def test_crawlers_do_not_disable_tls_verification() -> None:
    forbidden = (
        "verify" + "=False",
        "verify" + " = False",
        "disable_warnings(InsecureRequestWarning)",
        '"-k"',
        "'--insecure'",
    )
    offenders: list[str] = []
    for base in (ROOT / "Crawler", ROOT / "tools"):
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in forbidden):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_ops_bot_authorization_fails_closed_without_chat_allowlist(monkeypatch) -> None:
    from tools import mooncen_ops_bot

    monkeypatch.setattr(mooncen_ops_bot, "CHAT_IDS", set())
    assert mooncen_ops_bot.is_authorized("12345") is False


def test_ops_bot_never_exposes_telegram_token_in_exception(monkeypatch) -> None:
    from tools import mooncen_ops_bot

    token = "123456789:abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN"
    response = requests.Response()
    response.status_code = 401
    response.url = f"https://api.telegram.org/bot{token}/sendMessage"
    error = requests.HTTPError(
        f"401 Client Error for url: {response.url}",
        response=response,
    )

    monkeypatch.setattr(mooncen_ops_bot, "TOKEN", token)
    monkeypatch.setattr(
        mooncen_ops_bot.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(RuntimeError) as exc_info:
        mooncen_ops_bot.telegram("sendMessage", {"chat_id": "1", "text": "test"})

    message = str(exc_info.value)
    assert token not in message
    assert "HTTPError" in message
    assert "status=401" in message


def test_functional_report_redacts_urls_and_environment_secrets(monkeypatch) -> None:
    from tools.ops.functional_test import redact_sensitive_text

    token = "987654321:abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN"
    auth_secret = "a-production-auth-secret-that-must-not-leak"
    monkeypatch.setenv("MOONCEN_BOT_TOKEN", token)
    monkeypatch.setenv("AUTH_SECRET", auth_secret)

    redacted = redact_sensitive_text(
        f"https://api.telegram.org/bot{token}/sendMessage?code=oauth-code&state=csrf-state "
        f"secret={auth_secret}"
    )

    assert token not in redacted
    assert auth_secret not in redacted
    assert "oauth-code" not in redacted
    assert "csrf-state" not in redacted
    assert redacted.count("<redacted>") >= 3


def test_functional_health_checks_match_api_readiness_contract() -> None:
    from tools.ops import functional_test

    class Context:
        args = SimpleNamespace(
            internal_api_url="http://127.0.0.1:8001",
            base_url="https://mooncen.test",
        )

        @staticmethod
        def get_json(base_url, path):
            assert path == "/health"
            return {"status": "ready"}, 200, "application/json"

        @staticmethod
        def make_url(base_url, path):
            return f"{base_url}{path}"

    context = Context()
    assert functional_test.check_internal_api_health(context)["http_status"] == 200
    assert functional_test.check_public_api_health(context)["http_status"] == 200


def test_functional_course_scope_checks_are_required_and_latency_bounded(monkeypatch) -> None:
    from tools.ops import functional_test

    requested_paths: list[str] = []

    class Context:
        args = SimpleNamespace(internal_api_url="http://127.0.0.1:8001")

        @staticmethod
        def get_json(_base_url, path):
            requested_paths.append(path)
            return {"total": 1, "items": [{"id": "course-1"}]}, 200, "application/json"

    ticks = iter((10.0, 15.5))
    monkeypatch.setattr(functional_test.time, "perf_counter", lambda: next(ticks))
    result = functional_test.check_course_scope_api(Context(), "education")

    assert requested_paths == [
        "/api/courses/?page=1&size=30&scope=education"
        "&statuses=OPEN%2CDEADLINE&exclude_unavailable=true&sort=latest"
    ]
    assert result["duration_ms"] == 5_500
    required_checks = {name: required for name, _fn, required in functional_test.CHECKS}
    assert required_checks["course_scope_provider"] is True
    assert required_checks["course_scope_experience"] is True
    assert required_checks["course_scope_education"] is True

    ticks = iter((20.0, 28.001))
    monkeypatch.setattr(functional_test.time, "perf_counter", lambda: next(ticks))
    with pytest.raises(AssertionError, match="exceeded latency budget"):
        functional_test.check_course_scope_api(Context(), "education")


def test_nginx_trusts_tunnel_client_ip_only_from_loopback() -> None:
    config = (ROOT / "deploy" / "ubuntu" / "nginx" / "mooncen.conf").read_text(encoding="utf-8")

    assert "set_real_ip_from 127.0.0.1;" in config
    assert "set_real_ip_from ::1;" in config
    assert "real_ip_header CF-Connecting-IP;" in config
    assert "limit_req_zone $binary_remote_addr" in config
    assert "set_real_ip_from 0.0.0.0/0" not in config
    assert "set_real_ip_from ::/0" not in config


def test_nginx_origin_and_logs_do_not_expose_public_ports_or_query_secrets() -> None:
    active = (ROOT / "deploy" / "ubuntu" / "nginx" / "mooncen.conf").read_text(encoding="utf-8")
    standby = (ROOT / "deploy" / "ubuntu" / "nginx" / "mooncen_standby.conf").read_text(
        encoding="utf-8"
    )

    for config in (active, standby):
        assert "listen 127.0.0.1:80;" in config
        assert "listen [::1]:80;" in config
        assert "\n    listen 80;" not in config
        log_format = (
            config.split("limit_req_zone", 1)[0]
            if "limit_req_zone" in config
            else config.split("server {", 1)[0]
        )
        assert "$request_uri" not in log_format
        assert "$http_referer" not in log_format
        assert '\"$request_method $uri $server_protocol\"' in log_format

    assert "proxy_set_header X-Forwarded-Proto $mooncen_forwarded_proto;" in active


def test_cloudflared_token_is_never_passed_on_the_command_line() -> None:
    controller = (ROOT / "deploy" / "ubuntu" / "mooncenctl.sh").read_text(encoding="utf-8")
    unit = (ROOT / "deploy" / "ubuntu" / "systemd" / "cloudflared.service").read_text(encoding="utf-8")
    windows = (ROOT / "deploy_mooncen.ps1").read_text(encoding="utf-8")
    full_deploy = (ROOT / "deploy" / "ubuntu" / "deploy_from_windows.ps1").read_text(encoding="utf-8")

    assert "sudo -n \"$helper\" install" in controller
    assert "LoadCredential=cloudflared-token:/etc/cloudflared/token" in unit
    assert "--token-file %d/cloudflared-token" in unit
    assert "User=cloudflared" in unit
    assert "NoNewPrivileges=true" in unit
    assert "EnvironmentFile=/etc/cloudflared/token" not in unit
    assert "--token ${TUNNEL_TOKEN}" not in controller + unit
    assert 'install_cloudflared_token_service "${2:-}"' not in controller
    assert 'install_cloudflared_token_service "$@"' in controller
    assert 'Invoke-RemoteMoonCenWithInput "mooncenctl cloudflared-token" $token' in windows
    assert 'Invoke-RemoteWithInput "mooncenctl cloudflared-token" $CloudflaredToken' in full_deploy
    assert "mooncenctl cloudflared-token '$token'" not in windows


def test_ops_bot_diagnostic_runner_does_not_enable_shell_mode() -> None:
    source = (ROOT / "tools" / "mooncen_ops_bot.py").read_text(encoding="utf-8")
    assert "shell=True" not in source
