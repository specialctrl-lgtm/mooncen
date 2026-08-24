from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import run_crawlers as runner
from Crawler import Crawler_MunicipalIntegratedReservation as municipal
from tools import ops_service_action as action


ROOT = Path(__file__).resolve().parents[1]


def test_trusted_runtime_environment_requires_owned_mode_0700_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_home = "/tmp/mooncen-ops-runtime.Ab12Cd"
    expected = {
        "HOME": runtime_home,
        "TMPDIR": runtime_home,
        "TMP": runtime_home,
        "TEMP": runtime_home,
        "XDG_CACHE_HOME": f"{runtime_home}/.cache",
        "XDG_CONFIG_HOME": f"{runtime_home}/.config",
        "XDG_RUNTIME_DIR": runtime_home,
    }
    for key, value in expected.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(action.os, "name", "posix")
    monkeypatch.setattr(action.os, "geteuid", lambda: 996, raising=False)
    monkeypatch.setattr(
        action.os,
        "lstat",
        lambda _path: SimpleNamespace(st_uid=996, st_mode=action.stat.S_IFDIR | 0o700),
    )

    assert action._trusted_runtime_environment() == expected

    monkeypatch.setenv("TMPDIR", "/tmp")
    assert action._trusted_runtime_environment() == {}


def test_trusted_runtime_environment_rejects_unsafe_directory_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_home = "/tmp/mooncen-ops-runtime.Ab12Cd"
    for key, value in {
        "HOME": runtime_home,
        "TMPDIR": runtime_home,
        "TMP": runtime_home,
        "TEMP": runtime_home,
        "XDG_CACHE_HOME": f"{runtime_home}/.cache",
        "XDG_CONFIG_HOME": f"{runtime_home}/.config",
        "XDG_RUNTIME_DIR": runtime_home,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(action.os, "name", "posix")
    monkeypatch.setattr(action.os, "geteuid", lambda: 996, raising=False)
    monkeypatch.setattr(
        action.os,
        "lstat",
        lambda _path: SimpleNamespace(st_uid=996, st_mode=action.stat.S_IFDIR | 0o750),
    )

    assert action._trusted_runtime_environment() == {}


def _capture_process(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def run(
        arguments: list[str],
        secrets: set[str],
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> int:
        captured["arguments"] = arguments
        captured["secrets"] = secrets
        captured["env_overrides"] = env_overrides
        return 0

    monkeypatch.setattr(action, "_run_process", run)
    return captured


def test_provider_limit_is_forwarded_without_a_branch_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_process(monkeypatch)
    monkeypatch.setattr(action, "_crawler_provider_execution", lambda provider: (provider, {}))

    result = action._dispatch_process(
        "crawler-provider",
        {"provider": "BUSAN_NATIONAL_SCIENCE_MUSEUM", "limit": 25},
        {"not-printed"},
    )

    assert result == 0
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[arguments.index("--providers") + 1] == "BUSAN_NATIONAL_SCIENCE_MUSEUM"
    assert arguments[arguments.index("--limit") + 1] == "25"
    assert "--branch-code" not in arguments
    assert "--branch-name" not in arguments
    assert captured["env_overrides"] == {}


def test_provider_limit_defaults_to_safe_partial_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_process(monkeypatch)
    monkeypatch.setattr(action, "_crawler_provider_execution", lambda provider: (provider, {}))

    result = action._dispatch_process(
        "crawler-provider",
        {"provider": "HOMEPLUS"},
        set(),
    )

    assert result == 0
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[arguments.index("--limit") + 1] == str(
        action.DEFAULT_CRAWLER_PROVIDER_LIMIT
    )


@pytest.mark.parametrize("invalid_limit", [0, -1, 100_001])
def test_provider_limit_is_bounded_before_process_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    invalid_limit: int,
) -> None:
    invoked = False

    def should_not_resolve(_provider: str) -> tuple[str, dict[str, str]]:
        nonlocal invoked
        invoked = True
        return "HOMEPLUS", {}

    monkeypatch.setattr(action, "_crawler_provider_execution", should_not_resolve)

    with pytest.raises(action.ActionError, match="invalid limit"):
        action._dispatch_process(
            "crawler-provider",
            {"provider": "HOMEPLUS", "limit": invalid_limit},
            set(),
        )

    assert invoked is False


def test_provider_limit_accepts_explicit_full_catalogue_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_process(monkeypatch)
    monkeypatch.setattr(action, "_crawler_provider_execution", lambda provider: (provider, {}))

    result = action._dispatch_process(
        "crawler-provider",
        {"provider": "LOTTE", "limit": action.MAX_CRAWLER_PROVIDER_LIMIT},
        set(),
    )

    assert result == 0
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[arguments.index("--limit") + 1] == "100000"


def test_explicit_full_provider_run_omits_the_partial_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_process(monkeypatch)
    monkeypatch.setattr(action, "_crawler_provider_execution", lambda provider: (provider, {}))

    result = action._dispatch_process(
        "crawler-provider",
        {"provider": "LOTTE", "full": True},
        set(),
    )

    assert result == 0
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert "--limit" not in arguments


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "LOTTE", "full": "true"},
        {"provider": "LOTTE", "full": True, "limit": 100_000},
        {"provider": "LOTTE", "full": True, "branch_code": "001"},
    ],
)
def test_invalid_full_provider_combinations_fail_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    invoked = False

    def should_not_resolve(_provider: str) -> tuple[str, dict[str, str]]:
        nonlocal invoked
        invoked = True
        return "LOTTE", {}

    monkeypatch.setattr(action, "_crawler_provider_execution", should_not_resolve)

    with pytest.raises(action.ActionError):
        action._dispatch_process("crawler-provider", payload, set())

    assert invoked is False


def test_concrete_municipal_provider_resolves_to_one_aggregate_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(action, "APP_DIR", ROOT)
    provider = "SUWON_RESERV_EDUCATION"

    scheduled_owner, overrides = action._crawler_provider_execution(provider)

    assert scheduled_owner == action.MUNICIPAL_AGGREGATE_OWNER
    excluded = set(overrides["CRAWLER_PROVIDERS"].split())
    operational = {
        str(row["provider"]).strip().upper()
        for row in municipal.load_operational_entries()
    }
    assert excluded == operational - {provider}
    assert provider not in excluded

    monkeypatch.setenv("CRAWLER_PROVIDERS", overrides["CRAWLER_PROVIDERS"])
    selected = municipal.load_municipal_targets()
    assert municipal.municipal_provider_names(selected) == [provider]
    assert runner.build_course_provider_owners([scheduled_owner]) == {
        provider: scheduled_owner
    }
    assert runner.close_missing_is_safe([scheduled_owner], None, None, None) is False


def test_registered_static_provider_keeps_its_direct_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(action, "APP_DIR", ROOT)

    scheduled_owner, overrides = action._crawler_provider_execution("HOMEPLUS")

    assert scheduled_owner == "HOMEPLUS"
    assert overrides == {}


def test_aggregate_dispatch_passes_only_nonsecret_exclusion_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_process(monkeypatch)
    monkeypatch.setattr(
        action,
        "_crawler_provider_execution",
        lambda _provider: (
            action.MUNICIPAL_AGGREGATE_OWNER,
            {"CRAWLER_PROVIDERS": "MUNI_OTHER_A MUNI_OTHER_B"},
        ),
    )

    result = action._dispatch_process(
        "crawler-provider",
        {"provider": "SUWON_RESERV_EDUCATION", "limit": 50},
        {"database-password"},
    )

    assert result == 0
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[arguments.index("--providers") + 1] == action.MUNICIPAL_AGGREGATE_OWNER
    assert arguments[arguments.index("--limit") + 1] == "50"
    assert captured["env_overrides"] == {
        "CRAWLER_PROVIDERS": "MUNI_OTHER_A MUNI_OTHER_B"
    }
    assert "database-password" not in str(captured["env_overrides"])


def test_staging_provider_action_requires_complete_local_routing() -> None:
    values = {
        "CRAWL_WRITE_MODE": "staging",
        "CRAWL_STAGING_DB_HOST": "localhost",
        "CRAWL_STAGING_DB_PORT": "55432",
        "CRAWL_STAGING_DB_NAME": "mooncen_staging",
        "CRAWL_STAGING_DB_USER": "mooncen_crawler_login",
        "CRAWL_STAGING_DB_PASSWORD": "secret",
    }

    action._validate_crawler_service_environment("crawler-provider", values)

    with pytest.raises(action.ActionError, match="environment is incomplete"):
        action._validate_crawler_service_environment(
            "crawler-provider",
            {**values, "CRAWL_STAGING_DB_PASSWORD": ""},
        )
    with pytest.raises(action.ActionError, match="must be local"):
        action._validate_crawler_service_environment(
            "crawler-provider",
            {**values, "CRAWL_STAGING_DB_HOST": "cloud-primary"},
        )
    with pytest.raises(action.ActionError, match="port is invalid"):
        action._validate_crawler_service_environment(
            "crawler-provider",
            {**values, "CRAWL_STAGING_DB_PORT": "not-a-port"},
        )

    action._validate_crawler_service_environment(
        "crawler-provider",
        {"CRAWL_WRITE_MODE": "direct"},
    )


def test_process_environment_override_is_not_printed_and_output_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def run(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout="password=top-secret\n",
            stderr="",
        )

    monkeypatch.setattr(action.subprocess, "run", run)

    result = action._run_process(
        ["/bin/true"],
        {"top-secret"},
        env_overrides={"CRAWLER_PROVIDERS": "MUNI_A MUNI_B"},
    )

    assert result == 0
    assert capsys.readouterr().out == "password=[REDACTED]\n"
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["CRAWLER_PROVIDERS"] == "MUNI_A MUNI_B"


def _promotion_result(*, dry_run: bool, courses: int = 8) -> dict[str, object]:
    return {
        "batch_id": "n100-20260724221250-c1-59a3e551",
        "dry_run": dry_run,
        "providers": ["BUSAN_NATIONAL_SCIENCE_MUSEUM"],
        "apply_scope_provider": "BUSAN_NATIONAL_SCIENCE_MUSEUM",
        "promotion_provider": "BUSAN_NATIONAL_SCIENCE_MUSEUM",
        "staging_fingerprint": "a" * 64,
        "batch_status": "COLLECTED",
        "scheduled_owners": ["BUSAN_NATIONAL_SCIENCE_MUSEUM"],
        "successful_owners": ["BUSAN_NATIONAL_SCIENCE_MUSEUM"],
        "failed_owners": [],
        "providers_completed": 1,
        "providers_failed": 0,
        "partial_batch": False,
        "excluded_failed_branches": 0,
        "excluded_failed_courses": 0,
        "collection_complete": False,
        "close_missing_enabled": False,
        "close_requested_providers": [],
        "closed_providers": [],
        "incoming_provider_counts": {
            "BUSAN_NATIONAL_SCIENCE_MUSEUM": courses,
        },
        "branches": 1,
        "courses": courses,
        "valid_courses": courses,
        "invalid_courses": 0,
        "inserted": courses,
        "updated": 0,
        "closed": 0,
        "status": "DRY_RUN" if dry_run else "SUCCESS",
    }


def test_exact_provider_promotion_gates_dry_run_before_same_batch_apply(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(action, "APP_DIR", ROOT)
    calls: list[list[str]] = []
    results = [
        (0, _promotion_result(dry_run=True)),
        (0, _promotion_result(dry_run=False)),
    ]

    def run(arguments: list[str], _secrets: set[str]):
        calls.append(arguments)
        return results.pop(0)

    monkeypatch.setattr(action, "_run_json_process", run)

    result = action._promote_staging_provider(
        {
            "batch_id": "n100-20260724221250-c1-59a3e551",
            "provider": "BUSAN_NATIONAL_SCIENCE_MUSEUM",
        },
        {"not-printed"},
    )

    assert result == 0
    assert len(calls) == 2
    assert calls[0][-1] == "--dry-run"
    assert calls[1][-6:] == [
        "--batch-id",
        "n100-20260724221250-c1-59a3e551",
        "--promote-provider",
        "BUSAN_NATIONAL_SCIENCE_MUSEUM",
        "--expected-staging-fingerprint",
        "a" * 64,
    ]
    output = capsys.readouterr().out
    assert '"status": "PROMOTED"' in output
    assert '"courses": 8' in output


def test_exact_provider_promotion_rejects_zero_rows_before_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def run(_arguments: list[str], _secrets: set[str]):
        nonlocal calls
        calls += 1
        return 0, _promotion_result(dry_run=True, courses=0)

    monkeypatch.setattr(action, "_run_json_process", run)

    with pytest.raises(action.ActionError, match="course counts"):
        action._promote_staging_provider(
            {
                "batch_id": "n100-20260724221250-c1-59a3e551",
                "provider": "BUSAN_NATIONAL_SCIENCE_MUSEUM",
            },
            set(),
        )
    assert calls == 1


def test_exact_aggregate_promotion_accepts_concrete_provider_counts() -> None:
    result = _promotion_result(dry_run=True, courses=8)
    result.update(
        {
            "providers": ["EXPERIENCE_ONE", "EXPERIENCE_TWO"],
            "apply_scope_provider": "EXPERIENCE_TARGETS",
            "promotion_provider": "EXPERIENCE_TARGETS",
            "scheduled_owners": ["EXPERIENCE_TARGETS"],
            "successful_owners": ["EXPERIENCE_TARGETS"],
            "incoming_provider_counts": {
                "EXPERIENCE_ONE": 3,
                "EXPERIENCE_TWO": 5,
            },
        }
    )

    action._validate_provider_promotion_result(
        result,
        batch_id="n100-20260724221250-c1-59a3e551",
        provider="EXPERIENCE_TARGETS",
        dry_run=True,
    )


def test_exact_provider_promotion_rejects_lifecycle_closure() -> None:
    result = _promotion_result(dry_run=True)
    result["close_missing_enabled"] = True

    with pytest.raises(action.ActionError, match="close_missing_enabled"):
        action._validate_provider_promotion_result(
            result,
            batch_id="n100-20260724221250-c1-59a3e551",
            provider="BUSAN_NATIONAL_SCIENCE_MUSEUM",
            dry_run=True,
        )


def test_exact_provider_promotion_retry_rejects_changed_staging_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skipped = {
        "batch_id": "n100-20260724221250-c1-59a3e551",
        "dry_run": False,
        "providers": ["BUSAN_NATIONAL_SCIENCE_MUSEUM"],
        "apply_scope_provider": "BUSAN_NATIONAL_SCIENCE_MUSEUM",
        "promotion_provider": "BUSAN_NATIONAL_SCIENCE_MUSEUM",
        "staging_fingerprint": "a" * 64,
        "successful_apply_fingerprint": "b" * 64,
        "status": "SKIPPED_ALREADY_APPLIED",
    }
    results = [
        (0, _promotion_result(dry_run=True)),
        (0, skipped),
    ]
    monkeypatch.setattr(
        action,
        "_run_json_process",
        lambda _arguments, _secrets: results.pop(0),
    )

    with pytest.raises(action.ActionError, match="retry evidence"):
        action._promote_staging_provider(
            {
                "batch_id": "n100-20260724221250-c1-59a3e551",
                "provider": "BUSAN_NATIONAL_SCIENCE_MUSEUM",
            },
            set(),
        )
