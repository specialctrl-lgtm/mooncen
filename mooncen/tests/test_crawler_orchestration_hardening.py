from __future__ import annotations

import errno
import json
import re
from datetime import time as datetime_time
from pathlib import Path

import pytest
import yaml

import run_crawlers as runner
from Crawler import Crawler_EducationExperience as education_experience
from Crawler.Crawler_GeneratedYamlTargets import parse_args as parse_generated_args
from tools.crawler_report import replace_cycle_report, write_cycle_report


def _script_index(command: list[str]) -> int:
    return next(index for index, argument in enumerate(command) if argument.endswith(".py"))


def test_provider_registry_is_unique_reachable_and_generated_argv_parses() -> None:
    registry = yaml.safe_load(Path(runner.GENERATED_REGISTRY_FILE).read_text(encoding="utf-8"))
    enabled_registry_providers = {row["provider"] for row in registry["targets"] if row.get("enabled") is not False}
    assert set(runner.GENERATED_PROVIDER_COMMANDS) == enabled_registry_providers
    assert len(runner.PROVIDER_COMMANDS) == len(runner.STATIC_PROVIDER_COMMANDS) + len(enabled_registry_providers)
    assert set(runner.STATIC_PROVIDER_COMMANDS).isdisjoint(runner.GENERATED_PROVIDER_COMMANDS)
    assert all("generated_yaml" not in command for command in runner.STATIC_PROVIDER_COMMANDS.values())
    assert "MUNI_SLL_SEOUL_GO_KR_A0D6D8A2" not in runner.PROVIDER_COMMANDS
    assert runner.STATIC_PROVIDER_COMMANDS["SEOUL_PUBLIC_SERVICE"] == [
        "Crawler",
        "Crawler_SeoulPublicService.py",
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1000",
        "--detail-limit",
        "3000",
    ]

    seoul_command = runner.build_provider_command("SEOUL_PUBLIC_SERVICE", None)
    seoul_args = parse_generated_args(seoul_command[_script_index(seoul_command) + 1 :])
    # The dedicated wrapper injects the fixed provider after parsing this tail.
    assert seoul_args.provider is None
    assert seoul_args.save_db is True
    assert seoul_args.mark_stale is True
    assert seoul_args.per_target_limit == 0
    assert seoul_args.allow_partial_save is False
    assert seoul_command.count("--save-db") == 1

    seoul_sample_command = runner.build_provider_command(
        "SEOUL_PUBLIC_SERVICE",
        5,
    )
    seoul_sample_args = parse_generated_args(seoul_sample_command[_script_index(seoul_sample_command) + 1 :])
    assert seoul_sample_args.provider is None
    assert seoul_sample_args.save_db is True
    assert seoul_sample_args.mark_stale is False
    assert seoul_sample_args.per_target_limit == 5
    assert seoul_sample_args.allow_partial_save is True

    project_root = Path(runner.PROJECT_ROOT).resolve()
    for provider in sorted(runner.PROVIDER_COMMANDS):
        command = runner.build_provider_command(provider, None)
        script_index = _script_index(command)
        script_path = Path(command[script_index]).resolve()
        assert script_path.is_relative_to(project_root)
        assert script_path.is_file()

    for provider in sorted(runner.GENERATED_PROVIDER_COMMANDS):
        command = runner.build_provider_command(provider, None)
        script_index = _script_index(command)
        tail = command[script_index + 1 :]
        if Path(command[script_index]).name != "Crawler_GeneratedYamlTargets.py":
            tail = ["--provider", provider, *tail]
        parsed = parse_generated_args(tail)
        assert parsed.save_db is True
        assert not parsed.mark_stale or parsed.per_target_limit == 0

    aggregate_command = runner.build_provider_command("YAML_TARGETS_ALL", None)
    aggregate_tail = aggregate_command[_script_index(aggregate_command) + 1 :]
    aggregate_args = parse_generated_args(aggregate_tail)
    assert aggregate_args.all is True
    assert aggregate_args.save_db is True
    assert aggregate_args.allow_partial_save is True

    experience_command = runner.build_provider_command("EXPERIENCE_TARGETS", None)
    experience_tail = experience_command[_script_index(experience_command) + 1 :]
    experience_args = parse_generated_args(experience_tail)
    assert experience_args.save_db is True
    assert experience_args.mark_stale is True
    assert experience_args.per_target_limit == 0
    assert experience_args.allow_partial_save is False
    assert experience_args.max_pages == 1000
    assert experience_args.detail_limit == 3000
    assert experience_args.parallel_workers == 4

    municipal_command = runner.build_provider_command("MUNICIPAL_RESERVATION_TARGETS", None)
    assert "--save-db" in municipal_command
    assert "--mark-stale" in municipal_command
    assert "--allow-partial-save" not in municipal_command
    assert municipal_command[municipal_command.index("--per-target-limit") + 1] == "0"
    assert municipal_command[municipal_command.index("--max-pages") + 1] == "1500"
    assert municipal_command[municipal_command.index("--detail-limit") + 1] == "3000"
    assert municipal_command[municipal_command.index("--parallel-workers") + 1] == "3"

    seosan_command = runner.build_provider_command("SEOSAN_WELFARE_TOTAL_RESERVATION", None)
    assert Path(seosan_command[_script_index(seosan_command)]).name == "Crawler_GeneratedYamlTargets.py"
    seosan_args = parse_generated_args(seosan_command[_script_index(seosan_command) + 1 :])
    assert seosan_args.provider == ["SEOSAN_WELFARE_TOTAL_RESERVATION"]
    assert seosan_args.save_db is True
    assert seosan_args.mark_stale is True
    assert seosan_args.per_target_limit == 0
    assert seosan_args.allow_partial_save is False
    assert seosan_args.max_pages == 100
    assert seosan_args.detail_limit == 100
    assert "--save-db" in seosan_command
    assert "--mark-stale" in seosan_command

    seosan_sample_command = runner.build_provider_command(
        "SEOSAN_WELFARE_TOTAL_RESERVATION",
        5,
    )
    assert Path(seosan_sample_command[_script_index(seosan_sample_command)]).name == "Crawler_GeneratedYamlTargets.py"
    seosan_sample_args = parse_generated_args(seosan_sample_command[_script_index(seosan_sample_command) + 1 :])
    assert seosan_sample_args.provider == ["SEOSAN_WELFARE_TOTAL_RESERVATION"]
    assert seosan_sample_args.save_db is True
    assert seosan_sample_args.mark_stale is False
    assert seosan_sample_args.per_target_limit == 5
    assert seosan_sample_args.allow_partial_save is True

    babsang_command = runner.build_provider_command(
        "BABSANG_WELFARE_PROGRAM",
        None,
    )
    assert "--save-db" in babsang_command
    assert "--mark-stale" in babsang_command
    assert "--allow-partial-save" not in babsang_command
    assert babsang_command[babsang_command.index("--per-target-limit") + 1] == "0"
    assert babsang_command[babsang_command.index("--max-pages") + 1] == "100"
    assert babsang_command[babsang_command.index("--max-depth") + 1] == "1"
    assert babsang_command[babsang_command.index("--detail-limit") + 1] == "1000"
    assert babsang_command.count("--save-db") == 1

    babsang_sample_command = runner.build_provider_command(
        "BABSANG_WELFARE_PROGRAM",
        5,
    )
    assert "--mark-stale" not in babsang_sample_command
    assert "--allow-partial-save" in babsang_sample_command
    assert babsang_sample_command[babsang_sample_command.index("--per-target-limit") + 1] == "5"

    galleria_command = runner.build_provider_command("GALLERIA", None)
    assert "--mark-stale" in galleria_command
    assert "--limit" not in galleria_command

    for provider in ("COLLECTED_YAML", "FACILITY_REGISTRY"):
        command = runner.build_provider_command(provider, None)
        assert "--save-db" in command
        assert "--allow-partial-save" in command
        assert command[command.index("--per-target-limit") + 1] == "20"


def test_enabled_registry_numeric_overrides_survive_adapter_defaults() -> None:
    checked = 0
    for provider, registry_command in runner.GENERATED_PROVIDER_COMMANDS.items():
        script_index = _script_index(registry_command)
        registry_arguments = registry_command[script_index + 1 :]
        command = runner.build_provider_command(provider, None)
        index = 0
        while index < len(registry_arguments):
            option = registry_arguments[index]
            if option in runner.GENERATED_REGISTRY_NUMERIC_ARGUMENTS:
                value = registry_arguments[index + 1]
                assert command.count(option) == 1
                if option == "--per-target-limit":
                    assert command[command.index(option) + 1] == "0"
                    assert "--allow-partial-save" not in command
                else:
                    assert command[command.index(option) + 1] == value
                checked += 1
                index += 2
            else:
                index += 1
    assert checked >= 2


def test_registry_argument_and_provider_boundaries_fail_closed() -> None:
    assert runner.PROVIDER_NAME_PATTERN.fullmatch("A" * 50)
    assert runner.PROVIDER_NAME_PATTERN.fullmatch("A" * 51) is None
    assert runner._validated_registry_arguments(
        ["--save-db", "--per-target-limit", "50", "--allow-partial-save"],
        provider="SAFE",
    ) == ["--save-db", "--per-target-limit", "50", "--allow-partial-save"]
    for arguments in (
        ["--save-db"],
        ["--save-db", "--per-target-limit", "50"],
        ["--save-db", "--per-target-limit", "0", "--allow-partial-save"],
        ["--save-db", "--shell", "calc.exe"],
        ["--save-db", "--max-pages", "2001"],
        ["--save-db", "--max-pages", "3", "--max-pages", "4"],
        ["--max-pages", "3"],
    ):
        with pytest.raises(RuntimeError):
            runner._validated_registry_arguments(arguments, provider="SAFE")


def test_missing_generated_registry_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "GENERATED_REGISTRY_FILE", str(tmp_path / "missing.yaml"))
    with pytest.raises(RuntimeError, match="required but missing"):
        runner.load_generated_provider_commands()


def test_deployed_provider_lists_are_registered_and_do_not_duplicate_experience_macro() -> None:
    root = Path(runner.PROJECT_ROOT)
    env_text = (root / "deploy" / "ubuntu" / "mooncen.env.example").read_text(encoding="utf-8")
    setup_text = (root / "deploy" / "ubuntu" / "setup_project.sh").read_text(encoding="utf-8")
    configured_lists = re.findall(r'^CRAWLER_PROVIDERS="([^"]+)"', env_text, flags=re.MULTILINE)
    configured_lists.extend(re.findall(r'^CRAWLER_PROVIDERS="([^"]+)"', setup_text, flags=re.MULTILINE))
    assert len(configured_lists) == 3
    production_snapshot = yaml.safe_load(
        (root / "config" / "production_crawler_providers.yaml").read_text(encoding="utf-8")
    )
    assert all(configured.split() == production_snapshot["providers"] for configured in configured_lists)

    experience_providers = set(education_experience.experience_provider_names())
    assert not (experience_providers & education_experience.aggregate_owned_provider_names())
    for configured in configured_lists:
        providers = configured.split()
        assert len(providers) == len(set(providers))
        assert set(providers) <= set(runner.PROVIDER_ADAPTERS)
        assert "EXPERIENCE_TARGETS" in providers
        directly_scheduled = set(providers) - {"EXPERIENCE_TARGETS"}
        assert not directly_scheduled & set(
            education_experience.experience_provider_names(scheduled_providers=directly_scheduled)
        )
        assert "SEOUL_PUBLIC_SERVICE" in providers
        assert "MUNI_SLL_SEOUL_GO_KR_A0D6D8A2" not in providers

    for service_name in ("mooncen-crawler.service", "mooncen-crawler-once.service"):
        service_text = (root / "deploy" / "ubuntu" / "systemd" / service_name).read_text(encoding="utf-8")
        assert (
            "${CRAWLER_PROVIDERS:-HOMEPLUS EMART LOTTE EXPERIENCE_TARGETS MUNICIPAL_RESERVATION_TARGETS}"
        ) in service_text

    powershell_text = (root / "start_crawler_worker.ps1").read_text(encoding="utf-8")
    assert ('@("HOMEPLUS", "EMART", "LOTTE", "EXPERIENCE_TARGETS", "MUNICIPAL_RESERVATION_TARGETS")') in powershell_text


def test_close_missing_requires_complete_non_aggregate_scope() -> None:
    assert runner.close_missing_is_safe(["HOMEPLUS"], None, None, None) is True
    assert runner.close_missing_is_safe(["HOMEPLUS"], 10, None, None) is False
    assert runner.close_missing_is_safe(["HOMEPLUS"], None, "001", None) is False
    for provider in runner.PARTIAL_AGGREGATE_PROVIDER_NAMES:
        assert runner.close_missing_is_safe([provider], None, None, None) is False


def test_provider_record_cleanup_requires_unbounded_full_scope() -> None:
    assert runner.provider_record_cleanup_is_safe(None, None, None) is True
    assert runner.provider_record_cleanup_is_safe(10, None, None) is False
    assert runner.provider_record_cleanup_is_safe(None, "001", None) is False
    assert runner.provider_record_cleanup_is_safe(None, None, "Branch") is False


def test_branch_scoped_provider_run_does_not_delete_other_empty_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FinishedProcess:
        def poll(self) -> int:
            return 0

    cleanup_calls: list[str] = []
    monkeypatch.setattr(runner, "build_provider_command", lambda *_args, **_kwargs: ["python", "crawler.py"])
    monkeypatch.setattr(runner, "start_crawler_run", lambda **_kwargs: 77)
    monkeypatch.setattr(runner, "_spawn_process", lambda *_args, **_kwargs: FinishedProcess())
    monkeypatch.setattr(
        runner,
        "build_provider_report_safe",
        lambda **kwargs: {
            "provider": kwargs["provider"],
            "success": True,
            "exit_code": 0,
            "finished_at": runner.now_iso(),
            "elapsed_seconds": 0,
            "created_since": 1,
            "updated_since": 1,
        },
    )
    monkeypatch.setattr(runner, "finish_provider_run_log", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "cleanup_empty_branches", cleanup_calls.append)

    report = runner.run_provider("EMART", None, 60, branch_code="800")

    assert report["success"] is True
    assert cleanup_calls == []


@pytest.mark.parametrize("provider", ["EMART", "EXPERIENCE_TARGETS"])
def test_provider_subprocess_never_inherits_kakao_rest_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
) -> None:
    class Process:
        pass

    captured: dict[str, str] = {}

    def fake_spawn(_command, *, env=None):
        captured.update(env or {})
        return Process()

    monkeypatch.setenv("KAKAO_MAPS_REST_API_KEY", "server-secret")
    monkeypatch.setenv("MoonCenKakaoMapsRestApiKey", "server-secret-2")
    monkeypatch.setenv("CRAWL_BATCH_ID", "batch-1")
    monkeypatch.setattr(runner, "CONCRETE_RESULT_MANIFEST_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "_spawn_process", fake_spawn)

    process = runner._spawn_provider_process(provider, ["python", "crawler.py"])

    assert "KAKAO_MAPS_REST_API_KEY" not in captured
    assert "MoonCenKakaoMapsRestApiKey" not in captured
    if provider in runner.AGGREGATE_PROVIDER_OWNERS:
        assert captured[runner.SCHEDULED_PROVIDER_ENV] == provider
        assert getattr(process, "_mooncen_concrete_result_manifest_path")
    else:
        assert runner.SCHEDULED_PROVIDER_ENV not in captured


def test_concrete_result_manifest_is_bound_to_aggregate_ownership(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Process:
        pass

    monkeypatch.setenv("CRAWL_BATCH_ID", "batch-1")
    monkeypatch.setattr(runner, "CONCRETE_RESULT_MANIFEST_DIR", str(tmp_path))
    manifest_path = tmp_path / "result.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "crawl_batch_id": "batch-1",
                "scheduled_provider": "EXPERIENCE_TARGETS",
                "save_db": True,
                "providers": [
                    {
                        "provider": "EXPERIENCE_ONE",
                        "success": True,
                        "targets_total": 1,
                        "targets_succeeded": 1,
                        "collected_courses": 2,
                        "saved_courses": 2,
                    },
                    {
                        "provider": "EXPERIENCE_TWO",
                        "success": False,
                        "targets_total": 1,
                        "targets_succeeded": 0,
                        "collected_courses": 0,
                        "saved_courses": 0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    process = Process()
    process._mooncen_concrete_result_manifest_path = str(manifest_path)
    report = {"provider": "EXPERIENCE_TARGETS", "success": False}

    runner.attach_concrete_provider_results(
        "EXPERIENCE_TARGETS",
        process,
        report,
    )
    assert runner.provider_failure_message(report, "exit_code=1") == (
        "failed concrete providers (1/2): EXPERIENCE_TWO"
    )
    assert runner.provider_failure_type(report, "CalledProcessError") == "AggregatePartialFailure"
    concrete_results = runner.build_concrete_provider_results(
        [report],
        {
            "EXPERIENCE_ONE": "EXPERIENCE_TARGETS",
            "EXPERIENCE_TWO": "EXPERIENCE_TARGETS",
        },
    )

    assert [item["success"] for item in concrete_results] == [True, False]
    assert {item["scheduled_owner"] for item in concrete_results} == {"EXPERIENCE_TARGETS"}
    assert manifest_path.exists() is False


def test_aggregate_failure_type_preserves_missing_evidence_and_distinguishes_all_failed() -> None:
    assert runner.provider_failure_type({}, "CalledProcessError") == "CalledProcessError"
    assert (
        runner.provider_failure_type(
            {
                "concrete_provider_results": [
                    {"provider": "EXPERIENCE_ONE", "success": False},
                    {"provider": "EXPERIENCE_TWO", "success": False},
                ]
            },
            "CalledProcessError",
        )
        == "AggregateFailure"
    )


def test_aggregate_report_counts_only_commit_proven_concrete_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_snapshot(provider, since_iso=None, *, course_providers=None):
        captured.update(
            {
                "provider": provider,
                "since_iso": since_iso,
                "course_providers": course_providers,
            }
        )
        return {"provider": provider, "total": 3, "updated_since": 3}

    monkeypatch.setattr(runner, "fetch_provider_snapshot", fake_snapshot)
    report = {
        "provider": "EXPERIENCE_TARGETS",
        "success": False,
        "total": 0,
        "concrete_provider_results": [
            {"provider": "EXPERIENCE_COMMITTED", "success": True},
            {"provider": "EXPERIENCE_FAILED", "success": False},
        ],
    }

    runner.refresh_concrete_provider_snapshot(report, "2026-08-02T00:00:00+09:00")

    assert captured["provider"] == "EXPERIENCE_TARGETS"
    assert captured["course_providers"] == ["EXPERIENCE_COMMITTED"]
    assert report["total"] == 3
    assert report["updated_since"] == 3


def test_cycle_outcome_uses_validated_aggregate_partial_evidence() -> None:
    mixed = [
        {"provider": "EXPERIENCE_ONE", "success": True},
        {"provider": "EXPERIENCE_TWO", "success": False},
    ]

    assert (
        runner.classify_cycle_outcome(
            providers_completed=0,
            providers_failed=1,
            concrete_provider_results=mixed,
            batch_finished=True,
            maintenance_failed=False,
        )
        == "partial_success"
    )
    assert (
        runner.classify_cycle_outcome(
            providers_completed=0,
            providers_failed=1,
            concrete_provider_results=[{"provider": "EXPERIENCE_TWO", "success": False}],
            batch_finished=True,
            maintenance_failed=False,
        )
        == "zero_provider"
    )
    assert runner.exit_code_for_cycle_outcome("zero_provider") == runner.CRAWLER_ZERO_PROVIDER_EXIT_CODE
    assert runner.progress_status_for_cycle_outcome("zero_provider") == "failed"
    for batch_finished, maintenance_failed in ((False, False), (True, True)):
        assert (
            runner.classify_cycle_outcome(
                providers_completed=0,
                providers_failed=1,
                concrete_provider_results=mixed,
                batch_finished=batch_finished,
                maintenance_failed=maintenance_failed,
            )
            == "failed"
        )


def test_cycle_outcome_marks_mixed_direct_provider_results_partial() -> None:
    assert (
        runner.classify_cycle_outcome(
            providers_completed=37,
            providers_failed=1,
            concrete_provider_results=[],
            batch_finished=True,
            maintenance_failed=False,
        )
        == "partial_success"
    )


def test_registry_loader_consumes_only_canonical_argv_and_honors_disabled_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        yaml.safe_dump(
            {
                "targets": [
                    {
                        "provider": "TEST_CANONICAL",
                        "crawler": "Crawler/generated_yaml/DAEGU_RESERVATION.py",
                        "command": "python -c malicious_payload",
                        "arguments": [
                            "--save-db",
                            "--per-target-limit",
                            "50",
                            "--allow-partial-save",
                            "--max-pages",
                            "3",
                        ],
                        "enabled": True,
                    },
                    {
                        "provider": "TEST_DISABLED",
                        "crawler": "../../outside.py",
                        "command": "python outside.py",
                        "enabled": False,
                    },
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "GENERATED_REGISTRY_FILE", str(registry_path))
    commands = runner.load_generated_provider_commands()
    assert commands == {
        "TEST_CANONICAL": [
            "Crawler",
            "generated_yaml",
            "DAEGU_RESERVATION.py",
            "--save-db",
            "--per-target-limit",
            "50",
            "--allow-partial-save",
            "--max-pages",
            "3",
        ]
    }

    with pytest.raises(RuntimeError, match="collides with a static provider"):
        runner.load_generated_provider_commands(reserved_providers={"TEST_DISABLED"})


def test_worker_os_lock_keeps_pid_readable_and_cleans_it_on_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(runner, "PID_FILE", str(tmp_path / "worker.pid"))
    monkeypatch.setattr(runner, "WORKER_LOCK_FILE", str(tmp_path / "worker.lock"))
    monkeypatch.setattr(runner, "WORKER_LOCK_HANDLE", None)
    try:
        assert runner.acquire_worker_lock() == runner.WORKER_LOCK_ACQUIRED
        assert runner.read_pid_file() == runner.os.getpid()
    finally:
        runner.release_worker_lock()
    assert runner.read_pid_file() is None
    assert not Path(runner.PID_FILE).exists()


def test_worker_lock_open_permission_error_is_never_contention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "WORKER_LOCK_HANDLE", None)
    monkeypatch.setattr(runner, "ensure_log_dir", lambda: None)

    def denied(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(runner, "open", denied, raising=False)
    assert runner.acquire_worker_lock() == runner.WORKER_LOCK_ERROR


@pytest.mark.parametrize("lock_errno", [errno.EAGAIN, errno.EACCES])
def test_worker_lock_reports_only_confirmed_active_contention(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lock_errno: int,
) -> None:
    monkeypatch.setattr(runner, "WORKER_LOCK_FILE", str(tmp_path / "worker.lock"))
    monkeypatch.setattr(runner, "WORKER_LOCK_HANDLE", None)
    monkeypatch.setattr(
        runner,
        "_try_lock_file",
        lambda _file: (_ for _ in ()).throw(OSError(lock_errno, "busy")),
    )
    monkeypatch.setattr(runner, "_confirmed_lock_holder_pid", lambda: 4242)
    assert runner.acquire_worker_lock() == runner.WORKER_LOCK_CONTENDED


def test_worker_lock_unconfirmed_eacces_is_a_hard_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(runner, "WORKER_LOCK_FILE", str(tmp_path / "worker.lock"))
    monkeypatch.setattr(runner, "WORKER_LOCK_HANDLE", None)
    monkeypatch.setattr(
        runner,
        "_try_lock_file",
        lambda _file: (_ for _ in ()).throw(PermissionError(errno.EACCES, "denied")),
    )
    monkeypatch.setattr(runner, "_confirmed_lock_holder_pid", lambda: None)
    assert runner.acquire_worker_lock() == runner.WORKER_LOCK_ERROR


def test_worker_exit_distinguishes_contention_from_lock_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def invoke(lock_status: str) -> int:
        monkeypatch.setattr(runner, "acquire_worker_lock", lambda: lock_status)
        return runner.run_worker(
            providers=["HOMEPLUS"],
            limit=None,
            run_interval=1,
            active_start=datetime_time(0, 0),
            active_end=datetime_time(23, 59),
            active_check_interval=1,
            enforce_active_window=False,
            parallel=False,
            max_workers=1,
            provider_timeout=30,
            once=True,
            max_cycles=None,
            coordinate_backfill=False,
            coordinate_backfill_limit=None,
            coordinate_backfill_delay=0.1,
            category_backfill=False,
        )

    assert invoke(runner.WORKER_LOCK_CONTENDED) == runner.CRAWLER_LOCK_CONTENTION_EXIT_CODE
    assert invoke(runner.WORKER_LOCK_ERROR) == runner.CRAWLER_FAILED_EXIT_CODE


def test_cycle_state_is_atomic_and_preserves_last_full_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "crawler_cycle_state.json"
    monkeypatch.setattr(runner, "CYCLE_STATE_FILE", str(state_path))
    monkeypatch.setattr(runner, "ensure_log_dir", lambda: None)
    monkeypatch.delenv("CRAWL_WRITE_MODE", raising=False)

    running = runner.build_cycle_state(
        crawl_batch_id="batch-1",
        cycle=1,
        started_at="2026-08-06T10:00:00+00:00",
        finished_at="",
        final_outcome="running",
        exit_code=None,
        providers_requested=2,
    )
    assert runner.write_cycle_state(running)["last_success_at"] == ""

    succeeded = runner.build_cycle_state(
        crawl_batch_id="batch-1",
        cycle=1,
        started_at="2026-08-06T10:00:00+00:00",
        finished_at="2026-08-06T11:00:00+00:00",
        final_outcome="success",
        exit_code=0,
        providers_requested=2,
        providers_completed=2,
        batch_finished=True,
    )
    runner.write_cycle_state(succeeded)

    zero_provider = runner.build_cycle_state(
        crawl_batch_id="batch-2",
        cycle=2,
        started_at="2026-08-07T10:00:00+00:00",
        finished_at="2026-08-07T10:05:00+00:00",
        final_outcome="zero_provider",
        exit_code=runner.CRAWLER_ZERO_PROVIDER_EXIT_CODE,
        providers_requested=2,
        providers_failed=2,
        batch_finished=True,
    )
    written = runner.write_cycle_state(zero_provider)
    assert written["last_success_at"] == "2026-08-06T11:00:00+00:00"
    assert written["last_completed_at"] == "2026-08-07T10:05:00+00:00"
    assert written["zero_provider"] is True
    assert json.loads(state_path.read_text(encoding="utf-8"))["final_outcome"] == "zero_provider"
    assert list(tmp_path.glob("*.tmp")) == []


def test_worker_cli_rejects_ambiguous_or_unbounded_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRAWL_WRITE_MODE", raising=False)
    with pytest.raises(SystemExit):
        runner.parse_args(["--providers", "HOMEPLUS", "HOMEPLUS"])
    with pytest.raises(SystemExit):
        runner.parse_args(["--providers", "HOMEPLUS", "--max-workers", "17"])
    with pytest.raises(SystemExit):
        runner.parse_args(["--providers", "COLLECTED_YAML", "--branch-code", "001"])
    with pytest.raises(SystemExit):
        runner.parse_args(["--providers", "HOMEPLUS", "--limit", "-1"])

    assert runner.parse_args([]).providers == [
        "HOMEPLUS",
        "EMART",
        "LOTTE",
        "EXPERIENCE_TARGETS",
        "MUNICIPAL_RESERVATION_TARGETS",
    ]
    assert runner.parse_args([]).provider_timeout is None
    assert runner.parse_args(
        ["--provider-timeout", str(runner.DEFAULT_PROVIDER_TIMEOUT_SECONDS)]
    ).provider_timeout == runner.DEFAULT_PROVIDER_TIMEOUT_SECONDS


def test_parallel_cycle_reports_every_pending_provider_when_window_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "RUNNING", True)
    monkeypatch.setattr(runner, "is_within_active_window", lambda *_args: False)
    reports = runner.run_cycle_parallel(
        ["HOMEPLUS", "EMART", "LOTTE"],
        None,
        datetime_time(22, 0),
        datetime_time(7, 0),
        True,
        2,
    )
    assert [report["provider"] for report in reports] == ["HOMEPLUS", "EMART", "LOTTE"]
    assert all(report["success"] is False for report in reports)
    assert {report["error_type"] for report in reports} == {"ActiveWindowExpired"}


def test_parallel_cycle_enforces_individual_provider_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        pid = 4321

        def __init__(self) -> None:
            self.return_code = None

        def poll(self):
            return self.return_code

    process = FakeProcess()
    monkeypatch.setattr(runner, "RUNNING", True)
    monkeypatch.setattr(
        runner,
        "run_provider_process",
        lambda provider, limit, **_kwargs: (provider, process, runner.now_iso(), runner.time.monotonic(), 77),
    )
    monkeypatch.setattr(runner, "terminate_process_tree", lambda target: setattr(target, "return_code", -9))
    monkeypatch.setattr(
        runner,
        "build_provider_report_safe",
        lambda **kwargs: {
            "provider": kwargs["provider"],
            "success": False,
            "exit_code": -9,
            "finished_at": runner.now_iso(),
            "elapsed_seconds": 0,
            "created_since": 0,
            "updated_since": 0,
        },
    )
    monkeypatch.setattr(runner, "finish_provider_run_log", lambda *_args, **_kwargs: True)

    reports = runner.run_cycle_parallel(
        ["HOMEPLUS"],
        None,
        datetime_time(0, 0),
        datetime_time(23, 59),
        False,
        1,
        provider_timeout=0,
    )
    assert len(reports) == 1
    assert reports[0]["error_type"] == "ProviderTimeout"
    assert reports[0]["success"] is False


def test_lotte_gets_a_long_default_timeout_without_overriding_operator_choice() -> None:
    assert runner.effective_provider_timeout_seconds("LOTTE", None) == 28_800
    assert (
        runner.effective_provider_timeout_seconds("HOMEPLUS", None)
        == runner.DEFAULT_PROVIDER_TIMEOUT_SECONDS
    )
    assert runner.effective_provider_timeout_seconds("LOTTE", 3_600) == 3_600
    assert (
        runner.effective_provider_timeout_seconds("LOTTE", runner.DEFAULT_PROVIDER_TIMEOUT_SECONDS)
        == runner.DEFAULT_PROVIDER_TIMEOUT_SECONDS
    )


def test_windows_tree_termination_waits_for_reaped_exit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        pid = 4321

        def __init__(self) -> None:
            self.returncode = None
            self.wait_calls = 0
            self.killed = False

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise runner.subprocess.TimeoutExpired("crawler", timeout)
            self.returncode = -9
            return self.returncode

        def kill(self):
            self.killed = True

    process = FakeProcess()
    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: None)

    runner.terminate_process_tree(process)

    assert process.killed is True
    assert process.wait_calls == 2
    assert process.poll() == -9


def test_posix_tree_termination_kills_group_after_parent_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        pid = 4321
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.returncode = -15
            return self.returncode

    signals = []
    monkeypatch.setattr(runner.os, "name", "posix")
    monkeypatch.setattr(runner.os, "killpg", lambda pid, sig: signals.append((pid, sig)), raising=False)
    monkeypatch.setattr(runner.signal, "SIGKILL", 9, raising=False)

    runner.terminate_process_tree(FakeProcess())

    assert signals == [(4321, runner.signal.SIGTERM), (4321, 9)]


@pytest.mark.skipif(runner.os.name == "nt", reason="POSIX process-group behavior")
def test_posix_tree_termination_kills_sigterm_ignoring_descendant(tmp_path: Path) -> None:
    ready = tmp_path / "child-ready"
    survived = tmp_path / "child-survived"
    child_code = (
        "import signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(ready)!r}).write_text('ready'); "
        "time.sleep(1); "
        f"Path({str(survived)!r}).write_text('alive')"
    )
    parent_code = (
        f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(30)"
    )
    process = runner.subprocess.Popen(
        [runner.sys.executable, "-c", parent_code],
        start_new_session=True,
    )
    deadline = runner.time.monotonic() + 5
    while not ready.exists() and runner.time.monotonic() < deadline:
        runner.time.sleep(0.02)
    assert ready.exists()

    runner.terminate_process_tree(process)
    runner.time.sleep(1.2)

    assert not survived.exists()


def test_run_log_counts_do_not_double_count_inserted_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def finish(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(runner, "finish_crawler_run", finish)
    report = {"success": True, "created_since": 3, "updated_since": 5}
    assert runner.finish_provider_run_log(77, report) is True
    assert captured["collected_count"] == 5
    assert captured["inserted_count"] == 3
    assert captured["updated_count"] == 2


def test_partial_collection_and_geocoder_failure_do_not_skip_ended_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_sets = [
        [
            {"provider": "HOMEPLUS", "success": True, "exit_code": 0, "total": 3},
            {"provider": "EMART", "success": False, "exit_code": 1, "total": 0},
        ],
        [{"provider": "HOMEPLUS", "success": True, "exit_code": 0, "total": 3}],
    ]
    maintenance_calls: list[str] = []
    coordinate_results = [True, False]
    batch_results: list[tuple[str, dict]] = []
    cycle_reports: list[dict] = []
    cycle_states: list[dict] = []

    monkeypatch.setattr(runner, "RUNNING", True)
    monkeypatch.setattr(runner, "make_crawl_batch_id", lambda cycle: f"batch-{cycle}")
    monkeypatch.setattr(
        runner,
        "init_progress_state",
        lambda **_kwargs: {"status": "running", "started_at": runner.now_iso(), "providers": []},
    )
    monkeypatch.setattr(runner, "begin_staging_batch", lambda *_args: True)
    monkeypatch.setattr(runner, "run_cycle", lambda *_args, **_kwargs: report_sets.pop(0))
    monkeypatch.setattr(
        runner,
        "run_coordinate_backfill",
        lambda *_args: maintenance_calls.append("coordinate") or coordinate_results.pop(0),
    )
    monkeypatch.setattr(
        runner,
        "run_category_backfill",
        lambda: maintenance_calls.append("category") or True,
    )
    monkeypatch.setattr(
        runner,
        "run_ended_course_cleanup",
        lambda *_args: maintenance_calls.append("ended") or True,
    )
    monkeypatch.setattr(
        runner,
        "write_cycle_report",
        lambda report: cycle_reports.append(report) or "report.json",
    )
    monkeypatch.setattr(runner, "replace_cycle_report", lambda _path, _report: _path)
    monkeypatch.setattr(
        runner,
        "finish_staging_batch",
        lambda _batch, status, result: batch_results.append((status, result)) or True,
    )
    monkeypatch.setattr(
        runner,
        "finish_progress_cycle",
        lambda progress, status, **_kwargs: progress.update(status=status),
    )
    monkeypatch.setattr(runner, "write_progress", lambda *_args: None)
    monkeypatch.setattr(
        runner,
        "write_cycle_state",
        lambda state: cycle_states.append(dict(state)) or state,
    )
    monkeypatch.setenv("CRAWL_BATCH_ID", "test-batch")

    def invoke(providers: list[str]) -> int:
        return runner.run_worker(
            providers=providers,
            limit=None,
            run_interval=1,
            active_start=datetime_time(0, 0),
            active_end=datetime_time(23, 59),
            active_check_interval=1,
            enforce_active_window=False,
            parallel=False,
            max_workers=1,
            provider_timeout=30,
            once=True,
            max_cycles=None,
            coordinate_backfill=True,
            coordinate_backfill_limit=10,
            coordinate_backfill_delay=0.1,
            category_backfill=True,
            ignore_worker_lock=True,
        )

    assert invoke(["HOMEPLUS", "EMART"]) == runner.CRAWLER_PARTIAL_SUCCESS_EXIT_CODE
    assert invoke(["HOMEPLUS"]) == 1
    assert maintenance_calls == [
        "coordinate",
        "category",
        "ended",
        "coordinate",
        "category",
        "ended",
    ]
    assert [status for status, _result in batch_results] == ["FAILED", "COLLECTED"]
    assert batch_results[0][1]["close_missing_enabled"] is False
    assert batch_results[1][1]["close_missing_enabled"] is True
    assert [report["collection_outcome"] for report in cycle_reports] == [
        "partial_success",
        "failed",
    ]
    assert [report["final_outcome"] for report in cycle_reports] == [
        "partial_success",
        "failed",
    ]
    assert [state["final_outcome"] for state in cycle_states] == [
        "running",
        "partial_success",
        "running",
        "failed",
    ]
    assert all(report["batch_finished"] is True for report in cycle_reports)
    assert [report["maintenance"]["coordinate_backfill"]["success"] for report in cycle_reports] == [True, False]
    assert all(report["maintenance"]["ended_course_cleanup"]["success"] is True for report in cycle_reports)


@pytest.mark.parametrize(
    ("provider_success", "state_write_fails", "expected_exit", "expected_outcome"),
    [
        (False, False, runner.CRAWLER_ZERO_PROVIDER_EXIT_CODE, "zero_provider"),
        (True, True, runner.CRAWLER_FAILED_EXIT_CODE, "failed"),
    ],
)
def test_cycle_never_exits_success_without_provider_or_durable_terminal_evidence(
    monkeypatch: pytest.MonkeyPatch,
    provider_success: bool,
    state_write_fails: bool,
    expected_exit: int,
    expected_outcome: str,
) -> None:
    cycle_reports: list[dict] = []
    terminal_states: list[dict] = []
    monkeypatch.setattr(runner, "RUNNING", True)
    monkeypatch.setattr(runner, "make_crawl_batch_id", lambda _cycle: "batch-terminal-evidence")
    monkeypatch.setattr(
        runner,
        "init_progress_state",
        lambda **_kwargs: {
            "status": "running",
            "started_at": runner.now_iso(),
            "providers": [],
        },
    )
    monkeypatch.setattr(runner, "write_progress", lambda *_args: None)
    monkeypatch.setattr(runner, "finish_progress_cycle", lambda progress, status, **_kwargs: progress.update(status=status))
    monkeypatch.setattr(runner, "build_course_provider_owners", lambda _providers: {})
    monkeypatch.setattr(runner, "begin_staging_batch", lambda *_args: True)
    monkeypatch.setattr(
        runner,
        "run_cycle",
        lambda *_args, **_kwargs: [
            {
                "provider": "HOMEPLUS",
                "success": provider_success,
                "exit_code": 0 if provider_success else 1,
                "total": 1 if provider_success else 0,
            }
        ],
    )
    monkeypatch.setattr(
        runner,
        "run_cycle_maintenance",
        lambda **_kwargs: {
            "coordinate_backfill": {"requested": False, "success": None},
            "category_backfill": {"requested": False, "success": None},
            "ended_course_cleanup": {"requested": True, "success": True},
        },
    )
    monkeypatch.setattr(
        runner,
        "write_cycle_report",
        lambda report: cycle_reports.append(report) or "report.json",
    )
    monkeypatch.setattr(runner, "replace_cycle_report", lambda _path, _report: _path)
    monkeypatch.setattr(runner, "finish_staging_batch", lambda *_args, **_kwargs: True)

    def persist_state(state: dict) -> dict:
        if state["final_outcome"] != "running":
            terminal_states.append(dict(state))
            if state_write_fails:
                raise PermissionError(errno.EACCES, "denied")
        return state

    monkeypatch.setattr(runner, "write_cycle_state", persist_state)

    result = runner.run_worker(
        providers=["HOMEPLUS"],
        limit=None,
        run_interval=1,
        active_start=datetime_time(0, 0),
        active_end=datetime_time(23, 59),
        active_check_interval=1,
        enforce_active_window=False,
        parallel=False,
        max_workers=1,
        provider_timeout=30,
        once=True,
        max_cycles=None,
        coordinate_backfill=False,
        coordinate_backfill_limit=None,
        coordinate_backfill_delay=0.1,
        category_backfill=False,
        ignore_worker_lock=True,
    )

    assert result == expected_exit
    assert cycle_reports[0]["final_outcome"] == expected_outcome
    assert terminal_states[0]["final_outcome"] == ("success" if provider_success else "zero_provider")
    if state_write_fails:
        assert cycle_reports[0]["cycle_evidence_error"] == "PermissionError"


def test_category_backfill_runs_source_metadata_and_standard_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str]] = []

    def fake_run(command: list[str], label: str) -> bool:
        calls.append((command, label))
        return label != "course category metadata backfill"

    monkeypatch.setattr(runner, "run_maintenance_process", fake_run)

    assert runner.run_category_backfill() is False
    assert [label for _command, label in calls] == [
        "course category metadata backfill",
        "standard category backfill",
    ]
    assert calls[0][0][-1].endswith("backfill_course_categories.py")
    assert calls[1][0][-1].endswith("backfill_standard_categories.py")


def test_coordinate_backfill_skips_cleanly_without_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "KAKAO_MAPS_REST_API_KEY",
        "MoonCenKakaoMapsRestApiKey",
        "GOOGLE_MAPS_API_KEY",
        "VITE_GOOGLE_MAPS_API_KEY",
        "MoonCenGoogleMapsApiKey",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VITE_KAKAO_MAPS_JAVASCRIPT_KEY", "browser-key-is-not-a-rest-key")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "retired-google-key")
    dotenv_paths: list[Path] = []

    def read_dotenv(path: Path) -> dict[str, str]:
        dotenv_paths.append(path)
        return {
            "VITE_KAKAO_MAPS_JAVASCRIPT_KEY": "browser-key-is-not-a-rest-key",
            "GOOGLE_MAPS_API_KEY": "retired-google-key",
        }

    monkeypatch.setattr(runner, "dotenv_values", read_dotenv)
    calls: list[tuple[list[str], str]] = []
    monkeypatch.setattr(
        runner,
        "run_maintenance_process",
        lambda command, label, **_kwargs: calls.append((command, label)) or True,
    )

    assert runner.run_coordinate_backfill(None, 0.5) is True
    assert dotenv_paths == [Path(runner.PROJECT_ROOT) / ".env"]
    assert len(calls) == 1
    assert Path(calls[0][0][_script_index(calls[0][0])]).name == (
        "propagate_branch_locations.py"
    )
    assert calls[0][1] == "verified same-name branch coordinate propagation"


@pytest.mark.parametrize(
    "key_name",
    ["KAKAO_MAPS_REST_API_KEY", "MoonCenKakaoMapsRestApiKey"],
)
def test_coordinate_backfill_uses_kakao_geocoder_without_exposing_key_in_argv(
    monkeypatch: pytest.MonkeyPatch,
    key_name: str,
) -> None:
    for name in ("KAKAO_MAPS_REST_API_KEY", "MoonCenKakaoMapsRestApiKey"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(key_name, "server-only-rest-secret")
    monkeypatch.setenv("KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN", "1000")
    calls: list[tuple[list[str], str]] = []
    options: list[dict] = []

    def fake_run(command, label, **kwargs):
        calls.append((command, label))
        options.append(kwargs)
        return True

    monkeypatch.setattr(
        runner,
        "run_maintenance_process",
        fake_run,
    )

    assert runner.run_coordinate_backfill(12, 0.25) is True
    assert len(calls) == 6
    copy_command, copy_label = calls[0]
    assert Path(copy_command[_script_index(copy_command)]).name == (
        "propagate_branch_locations.py"
    )
    assert "--with-active-courses" in copy_command
    assert copy_command[copy_command.index("--limit") + 1] == "12"
    assert copy_label == "verified same-name branch coordinate propagation"
    assert options[0] == {}
    assert all(option == {"accepted_exit_codes": (0, 3)} for option in options[1:])
    address_command, address_label = calls[1]
    course_address_command, course_address_label = calls[2]
    region_command, region_label = calls[3]
    configured_command, configured_label = calls[4]
    reverify_command, reverify_label = calls[5]
    for command in (
        address_command,
        course_address_command,
        region_command,
        configured_command,
        reverify_command,
    ):
        assert Path(command[_script_index(command)]).name == "kakao_geocode_branches.py"
        assert command[command.index("--delay") + 1] == "0.25"
        assert command[command.index("--limit") + 1] == "12"
        assert "--with-active-courses" in command
        assert "server-only-rest-secret" not in command
        assert "--max-requests" in command
    assert sum(
        int(command[command.index("--max-requests") + 1])
        for command, _label in calls[1:]
    ) == 1000
    assert "--address-only" in address_command
    assert address_command[address_command.index("--retry-after-days") + 1] == "30"
    assert address_label == "branch address coordinate backfill"
    assert "--course-address-only" in course_address_command
    assert course_address_command[course_address_command.index("--retry-after-days") + 1] == "30"
    assert course_address_label == "branch course-address coordinate backfill"
    assert "--region-keyword-only" in region_command
    assert region_command[region_command.index("--retry-after-days") + 1] == "14"
    assert region_label == "branch region coordinate backfill"
    assert "--configured-locality-only" in configured_command
    assert configured_command[configured_command.index("--retry-after-days") + 1] == "30"
    assert configured_label == "branch configured-locality coordinate backfill"
    assert "--verify-existing" in reverify_command
    assert reverify_command[reverify_command.index("--coordinate-source-prefix") + 1] == "GOOGLE"
    assert reverify_command[reverify_command.index("--retry-after-days") + 1] == "30"
    assert reverify_label == "legacy Google coordinate Kakao reverification"


def test_coordinate_geocode_request_budgets_are_positive_and_exactly_bounded() -> None:
    budgets = runner.coordinate_geocode_request_budgets(1003)

    assert set(budgets) == {
        "address",
        "course_address",
        "stored_region",
        "configured_locality",
        "legacy_reverify",
    }
    assert all(value > 0 for value in budgets.values())
    assert sum(budgets.values()) == 1003
    with pytest.raises(ValueError):
        runner.coordinate_geocode_request_budgets(99)


def test_coordinate_backfill_stops_before_region_mode_when_address_mode_hard_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAKAO_MAPS_REST_API_KEY", "server-only-rest-secret")
    calls: list[str] = []

    def fail_address(_command, label, **_kwargs):
        calls.append(label)
        return label != "branch address coordinate backfill"

    monkeypatch.setattr(runner, "run_maintenance_process", fail_address)

    assert runner.run_coordinate_backfill(10, 0.1) is False
    assert calls == [
        "verified same-name branch coordinate propagation",
        "branch address coordinate backfill",
    ]


def test_maintenance_process_accepts_bounded_partial_exit_without_hiding_hard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FinishedProcess:
        def __init__(self, returncode: int):
            self.returncode = returncode

        def poll(self):
            return self.returncode

    process = FinishedProcess(3)
    monkeypatch.setattr(runner, "_spawn_process", lambda *_args, **_kwargs: process)

    assert runner.run_maintenance_process(
        ["python", "bounded.py"],
        "bounded task",
        accepted_exit_codes=(0, 3),
    ) is True
    assert runner.run_maintenance_process(
        ["python", "bounded.py"],
        "hard-failure contract",
    ) is False


def test_cycle_reports_are_unique_atomic_and_parseable(tmp_path: Path) -> None:
    first = Path(write_cycle_report({"providers": []}, str(tmp_path)))
    second = Path(write_cycle_report({"providers": []}, str(tmp_path)))

    assert first != second
    assert json.loads(first.read_text(encoding="utf-8"))["summary"]["provider_success"] == "0/0"
    assert json.loads(second.read_text(encoding="utf-8"))["summary"]["provider_success"] == "0/0"
    assert list(tmp_path.glob("*.tmp")) == []

    replace_cycle_report(
        str(first),
        {"providers": [], "batch_finished": False, "final_outcome": "failed"},
    )
    replaced = json.loads(first.read_text(encoding="utf-8"))
    assert replaced["batch_finished"] is False
    assert replaced["final_outcome"] == "failed"
    assert list(tmp_path.glob("*.tmp")) == []
