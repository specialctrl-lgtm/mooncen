from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path, PurePosixPath
from typing import Optional

import yaml
from dotenv import dotenv_values

from DB.course_lifecycle import apply_ended_course_lifecycle
from Crawler.site_adapters import BRANCH_FILTER_PROVIDERS, build_adapter_registry
from DB.crawler_run_log import finish_crawler_run, start_crawler_run
from DB.crawl_progress import init_crawl_progress, update_crawl_progress
from DB.course_upsert_guards import (
    cleanup_invalid_display_courses_for_provider,
    deduplicate_course_raw_urls_for_provider,
    delete_empty_branches_for_provider,
)
from DB.db_utils import get_db_config, get_db_cursor
from ops_agent.crawler_outcome import (
    CRAWLER_FAILED_EXIT_CODE,
    CRAWLER_PARTIAL_SUCCESS_EXIT_CODE,
    CRAWLER_SUCCESS_EXIT_CODE,
)
from tools.crawler_report import (
    build_provider_report,
    fetch_provider_snapshot,
    now_iso,
    replace_cycle_report,
    write_cycle_report,
)


RUNNING = True
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.abspath(os.getenv("CRAWLER_LOG_DIR", os.path.join(PROJECT_ROOT, "logs")))
PID_FILE = os.path.join(LOG_DIR, "crawler_worker.pid")
WORKER_LOCK_FILE = os.path.join(LOG_DIR, "crawler_worker.lock")
PROGRESS_FILE = os.path.join(LOG_DIR, "crawler_progress.json")
CYCLE_STATE_FILE = os.path.join(LOG_DIR, "crawler_cycle_state.json")
WORKER_LOCK_HANDLE = None

CYCLE_STATE_SCHEMA_VERSION = 1
MAX_CYCLE_STATE_BYTES = 65_536
WORKER_LOCK_ACQUIRED = "acquired"
WORKER_LOCK_CONTENDED = "contended"
WORKER_LOCK_ERROR = "error"
CRAWLER_LOCK_CONTENTION_EXIT_CODE = 75
CRAWLER_ZERO_PROVIDER_EXIT_CODE = 4
MAX_DISTRIBUTED_TASK_RESULT_BYTES = 1_048_576

GENERATED_REGISTRY_FILE = os.path.join(PROJECT_ROOT, "config", "generated_yaml_crawler_registry.yaml")
PROVIDER_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")
MAX_PARALLEL_WORKERS = 16
MAX_PROVIDERS_PER_RUN = 512
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 7_200
MAX_PROVIDER_TIMEOUT_SECONDS = 32_400
# Full LOTTE, LOTTE_MART, experience, and municipal aggregate crawls can need
# more than two hours.  The experience aggregate owns dozens of concrete
# providers and must reach its atomic persistence step; killing it at the
# generic two-hour deadline discards the whole completed prefix.
# Apply these only when the operator did not explicitly choose another timeout.
DEFAULT_PROVIDER_TIMEOUT_OVERRIDES_SECONDS = {
    "LOTTE": 28_800,
    "LOTTE_MART": 28_800,
    "EXPERIENCE_TARGETS": 28_800,
    "MUNICIPAL_RESERVATION_TARGETS": 28_800,
}
PROCESS_POLL_INTERVAL_SECONDS = 0.5
MAX_COMMAND_ARGUMENTS = 128
MAX_COMMAND_ARGUMENT_LENGTH = 4_096
MAX_CONCRETE_RESULT_MANIFEST_BYTES = 1_048_576
CONCRETE_RESULT_MANIFEST_DIR = os.path.join(LOG_DIR, "crawler_provider_results")
CONCRETE_RESULT_MANIFEST_PATH_ENV = "CRAWLER_CONCRETE_RESULT_PATH"
SCHEDULED_PROVIDER_ENV = "CRAWLER_SCHEDULED_PROVIDER"

AGGREGATE_PROVIDER_OWNERS = {
    "EXPERIENCE_TARGETS",
    "MUNICIPAL_RESERVATION_TARGETS",
}

PARTIAL_AGGREGATE_PROVIDER_NAMES = {
    "COLLECTED_YAML",
    "FACILITY_REGISTRY",
    "YAML_TARGETS_ALL",
    "EXPERIENCE_TARGETS",
    "MUNICIPAL_RESERVATION_TARGETS",
}

REQUIRED_DB_COLUMNS = {
    "branches": {
        "id",
        "provider",
        "branch_code",
        "name",
        "address",
        "lat",
        "lon",
        "address_source",
        "coordinate_source",
        "location_confidence",
        "location_verified",
        "location_checked_at",
        "location_query",
        "updated_at",
    },
    "courses": {
        "id",
        "branch_id",
        "provider",
        "provider_course_id",
        "title",
        "raw_url",
        "is_active",
        "last_seen_at",
        "service_group",
        "standard_category_key",
        "standard_category_label",
        "target_age_is_explicit",
    },
    "crawler_run_log": {
        "id",
        "target_key",
        "source_type",
        "crawler_name",
        "status",
        "started_at",
        "ended_at",
        "error_message",
    },
}

GENERATED_REGISTRY_NUMERIC_ARGUMENTS = {
    "--per-target-limit": (0, 5_000),
    "--max-depth": (0, 3),
    "--max-pages": (1, 2_000),
    "--detail-limit": (0, 3_000),
    "--timeout": (1, 60),
}

STAGING_REQUIRED_DB_COLUMNS = {
    "branches": {"crawl_batch_id"},
    "courses": {"crawl_batch_id"},
    "crawl_batches": {"crawl_batch_id", "status", "started_at", "finished_at"},
}


def _validated_script_parts(value: object, *, provider: str) -> list[str]:
    text = str(value or "Crawler/Crawler_GeneratedYamlTargets.py").strip().replace("\\", "/")
    if not text or len(text) > 1_024 or any(ord(character) < 32 for character in text):
        raise RuntimeError(f"Generated crawler path is invalid for provider={provider}")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Generated crawler path escapes the project for provider={provider}")
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts or not parts[-1].endswith(".py"):
        raise RuntimeError(f"Generated crawler path must name a Python file for provider={provider}")
    project_root = Path(PROJECT_ROOT).resolve()
    script_path = project_root.joinpath(*parts).resolve()
    if not script_path.is_relative_to(project_root) or not script_path.is_file():
        raise RuntimeError(f"Generated crawler script is missing for provider={provider}")
    return parts


def _validated_registry_arguments(value: object, *, provider: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise RuntimeError(f"Generated crawler arguments must be a bounded argv list for provider={provider}")
    arguments: list[str] = []
    seen: set[str] = set()
    index = 0
    while index < len(value):
        option = value[index]
        if not isinstance(option, str) or option in seen:
            raise RuntimeError(
                f"Generated crawler arguments contain an invalid or duplicate option for provider={provider}"
            )
        seen.add(option)
        if option in {"--save-db", "--allow-partial-save", "--mark-stale"}:
            arguments.append(option)
            index += 1
            continue
        bounds = GENERATED_REGISTRY_NUMERIC_ARGUMENTS.get(option)
        if bounds is None or index + 1 >= len(value):
            raise RuntimeError(f"Generated crawler arguments contain a forbidden option for provider={provider}")
        raw_value = value[index + 1]
        if (
            not isinstance(raw_value, str)
            or not raw_value
            or len(raw_value) > 100
            or raw_value.startswith("--")
            or any(ord(character) < 32 for character in raw_value)
        ):
            raise RuntimeError(f"Generated crawler option has an invalid value for provider={provider}")
        try:
            numeric_value = int(raw_value)
        except ValueError as exc:
            raise RuntimeError(f"Generated crawler option must be an integer for provider={provider}") from exc
        if not bounds[0] <= numeric_value <= bounds[1]:
            raise RuntimeError(f"Generated crawler option is outside safe bounds for provider={provider}")
        arguments.extend((option, raw_value))
        index += 2
    if "--save-db" not in seen:
        raise RuntimeError(f"Generated crawler arguments must enable persistence for provider={provider}")
    if "--per-target-limit" not in seen:
        raise RuntimeError(f"Generated crawler arguments must declare its persistence limit for provider={provider}")
    per_target_limit = int(arguments[arguments.index("--per-target-limit") + 1])
    if per_target_limit > 0 and "--allow-partial-save" not in seen:
        raise RuntimeError(f"Generated crawler bounded persistence requires explicit opt-in for provider={provider}")
    if per_target_limit == 0 and "--allow-partial-save" in seen:
        raise RuntimeError(f"Generated crawler full persistence cannot opt into partial saves for provider={provider}")
    if "--mark-stale" in seen and per_target_limit != 0:
        raise RuntimeError(f"Generated crawler stale cleanup requires a full crawl for provider={provider}")
    return arguments


def load_generated_provider_commands(*, reserved_providers: set[str] | None = None) -> dict[str, list[str]]:
    if not os.path.exists(GENERATED_REGISTRY_FILE):
        raise RuntimeError("Generated crawler registry is required but missing")

    try:
        with open(GENERATED_REGISTRY_FILE, "r", encoding="utf-8") as registry_file:
            data = yaml.safe_load(registry_file) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError("Generated crawler registry could not be loaded") from exc
    if not isinstance(data, dict) or not isinstance(data.get("targets", []), list):
        raise RuntimeError("Generated crawler registry must contain a targets list")

    commands: dict[str, list[str]] = {}
    reserved_providers = reserved_providers or set()
    for row_index, row in enumerate(data.get("targets") or [], start=1):
        if not isinstance(row, dict):
            raise RuntimeError(f"Generated crawler registry row {row_index} must be a mapping")
        provider = str(row.get("provider") or "").strip().upper()
        if not PROVIDER_NAME_PATTERN.fullmatch(provider):
            raise RuntimeError(f"Generated crawler registry row {row_index} has an invalid provider")
        if provider in reserved_providers:
            raise RuntimeError(f"Generated crawler registry collides with a static provider={provider}")
        if row.get("enabled") is False:
            continue
        script_parts = _validated_script_parts(row.get("crawler"), provider=provider)
        registry_arguments = _validated_registry_arguments(row.get("arguments"), provider=provider)
        if script_parts[-1] == "Crawler_GeneratedYamlTargets.py":
            command = [*script_parts, "--provider", provider, *registry_arguments]
        else:
            command = [*script_parts, *registry_arguments]
        previous = commands.get(provider)
        if previous is not None and previous != command:
            raise RuntimeError(f"Generated crawler registry has conflicting commands for provider={provider}")
        commands[provider] = command
    return commands


STATIC_PROVIDER_COMMANDS = {
    "HOMEPLUS": ["Crawler", "Crawler_Homeplus.py"],
    "EMART": ["Crawler", "Crawler_Emart.py"],
    "LOTTE": ["Crawler", "Crawler_Lotte.py"],
    "HYUNDAI_DEPT": [
        "Crawler",
        "Crawler_YamlSources.py",
        "--provider",
        "HYUNDAI_DEPT",
        "--mark-stale",
    ],
    "GALLERIA": [
        "Crawler",
        "Crawler_YamlSources.py",
        "--provider",
        "GALLERIA",
        "--mark-stale",
    ],
    "AK_PLAZA": [
        "Crawler",
        "Crawler_YamlSources.py",
        "--provider",
        "AK_PLAZA",
        "--mark-stale",
    ],
    "ELAND_RETAIL": ["Crawler", "Crawler_YamlSources.py", "--provider", "ELAND_RETAIL"],
    "SHINSEGAE_ACADEMY": [
        "Crawler",
        "Crawler_YamlSources.py",
        "--provider",
        "SHINSEGAE_ACADEMY",
        "--mark-stale",
    ],
    "LOTTE_MART": ["Crawler", "Crawler_YamlSources.py", "--provider", "LOTTE_MART"],
    "BABSANG_WELFARE_PROGRAM": [
        "Crawler",
        "Crawler_BabsangWelfare.py",
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--max-depth",
        "1",
        "--detail-limit",
        "1000",
    ],
    "SAHASILVER_COURSE": ["Crawler", "Crawler_Sahasilver.py"],
    "SEOSAN_WELFARE_TOTAL_RESERVATION": [
        "Crawler",
        "Crawler_GeneratedYamlTargets.py",
        "--provider",
        "SEOSAN_WELFARE_TOTAL_RESERVATION",
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "100",
    ],
    "SEOUL_PUBLIC_SERVICE": [
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
    ],
    "SEONGNAM_BAEUMSOOP": ["Crawler", "Crawler_SeongnamBaeumsoop.py"],
    "YONGIN_LIFELONG_LEARNING": ["Crawler", "Crawler_YonginLifelong.py"],
    "ESONGPA_SPORTS_CULTURE": ["Crawler", "Crawler_EsongpaSportsCulture.py"],
    "COLLECTED_YAML": ["Crawler", "Crawler_MunicipalYaml.py"],
    "FACILITY_REGISTRY": ["Crawler", "Crawler_MunicipalYaml.py"],
    "YAML_TARGETS_ALL": ["Crawler", "Crawler_GeneratedYamlTargets.py"],
    "EXPERIENCE_TARGETS": [
        "Crawler",
        "Crawler_EducationExperience.py",
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1000",
        "--detail-limit",
        "3000",
        "--parallel-workers",
        "4",
    ],
    "MUNICIPAL_RESERVATION_TARGETS": [
        "Crawler",
        "Crawler_MunicipalIntegratedReservation.py",
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1500",
        "--detail-limit",
        "3000",
        "--parallel-workers",
        "3",
    ],
}

GENERATED_PROVIDER_COMMANDS = load_generated_provider_commands(reserved_providers=set(STATIC_PROVIDER_COMMANDS))


def validate_provider_commands(provider_commands: dict[str, list[str]]) -> None:
    project_root = Path(PROJECT_ROOT).resolve()
    if not provider_commands or len(provider_commands) > MAX_PROVIDERS_PER_RUN:
        raise RuntimeError("Crawler provider registry is empty or exceeds the supported size")
    for provider, command in provider_commands.items():
        if not PROVIDER_NAME_PATTERN.fullmatch(provider):
            raise RuntimeError(f"Crawler provider name is invalid: {provider!r}")
        if not isinstance(command, list) or not 1 <= len(command) <= MAX_COMMAND_ARGUMENTS:
            raise RuntimeError(f"Crawler command shape is invalid for provider={provider}")
        normalized_parts = [str(part) for part in command]
        if any(
            not part or len(part) > MAX_COMMAND_ARGUMENT_LENGTH or any(ord(character) < 32 for character in part)
            for part in normalized_parts
        ):
            raise RuntimeError(f"Crawler command contains an unsafe argument for provider={provider}")
        script_index = next(
            (index for index, part in enumerate(normalized_parts) if part.endswith(".py")),
            None,
        )
        if script_index is None:
            raise RuntimeError(f"Crawler command has no Python entry point for provider={provider}")
        script_path = project_root.joinpath(*normalized_parts[: script_index + 1]).resolve()
        if not script_path.is_relative_to(project_root) or not script_path.is_file():
            raise RuntimeError(f"Crawler entry point is missing or escapes the project for provider={provider}")


PROVIDER_COMMANDS = {**GENERATED_PROVIDER_COMMANDS, **STATIC_PROVIDER_COMMANDS}
validate_provider_commands(PROVIDER_COMMANDS)
GENERATED_PROVIDER_NAMES = set(GENERATED_PROVIDER_COMMANDS) - set(STATIC_PROVIDER_COMMANDS)
PROVIDER_ADAPTERS = build_adapter_registry(PROVIDER_COMMANDS, GENERATED_PROVIDER_NAMES, PROJECT_ROOT)

CULTURE_CENTER_PROVIDER_NAMES = {
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
}


def effective_provider_timeout_seconds(provider: str, configured_timeout: float | None) -> float:
    if configured_timeout is not None:
        return float(configured_timeout)
    return float(
        DEFAULT_PROVIDER_TIMEOUT_OVERRIDES_SECONDS.get(
            provider,
            DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        )
    )


logger = logging.getLogger("CrawlerWorker")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


def cleanup_empty_branches(provider: str) -> int:
    try:
        with get_db_cursor() as cursor:
            deduplicate_course_raw_urls_for_provider(cursor, provider, logger)
            cleanup_invalid_display_courses_for_provider(cursor, provider, logger)
            return delete_empty_branches_for_provider(cursor, provider, logger)
    except Exception as exc:
        logger.error("Failed to clean provider records for %s: %s", provider, exc)
        return 0


def handle_shutdown(signum, _frame):
    global RUNNING
    logger.info("Received signal %s. Stopping crawler worker...", signum)
    RUNNING = False


def parse_clock(value: str) -> datetime_time:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("time must be HH:MM") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise argparse.ArgumentTypeError("time must be HH:MM in 00:00-23:59")
    return datetime_time(hour=hour, minute=minute)


def is_within_active_window(now: datetime_time, start: datetime_time, end: datetime_time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def seconds_until_active_window(now: datetime, start: datetime_time, end: datetime_time) -> int:
    if is_within_active_window(now.time(), start, end):
        return 0

    start_today = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    target = start_today if now < start_today else start_today + timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


def seconds_until_window_end(now: datetime, start: datetime_time, end: datetime_time) -> Optional[int]:
    if not is_within_active_window(now.time(), start, end):
        return None

    end_today = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if start < end:
        target = end_today
    else:
        target = end_today + timedelta(days=1) if now.time() >= start else end_today
    return max(1, int((target - now).total_seconds()))


def ensure_log_dir() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def iso_after(seconds: float) -> str:
    return (datetime.now().astimezone() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def update_progress_counts(progress: dict) -> None:
    provider_rows = progress.get("providers") or []
    completed_states = {"success", "failed", "stopped", "skipped"}
    completed_rows = [row for row in provider_rows if row.get("state") in completed_states]
    running_rows = [row for row in provider_rows if row.get("state") == "running"]
    pending_rows = [row for row in provider_rows if row.get("state") == "pending"]
    total = len(provider_rows)

    progress["total"] = total
    progress["completed"] = len(completed_rows)
    progress["success"] = sum(1 for row in provider_rows if row.get("state") == "success")
    progress["failed"] = sum(1 for row in provider_rows if row.get("state") in {"failed", "stopped"})
    progress["running"] = [row.get("provider") for row in running_rows if row.get("provider")]
    progress["pending"] = [row.get("provider") for row in pending_rows if row.get("provider")]
    progress["progress_percent"] = round((len(completed_rows) / total * 100), 1) if total else 0.0


def write_progress(progress: dict) -> None:
    ensure_log_dir()
    progress["updated_at"] = now_iso()
    update_progress_counts(progress)
    temp_path = f"{PROGRESS_FILE}.tmp"
    with open(temp_path, "w", encoding="utf-8") as progress_file:
        json.dump(progress, progress_file, ensure_ascii=False, indent=2, default=str)
    try:
        os.replace(temp_path, PROGRESS_FILE)
    except PermissionError:
        # Some Windows workspaces expose logs as a reparse point and reject
        # atomic replace. Fall back to a direct write so manual local runs can
        # still report progress.
        with open(PROGRESS_FILE, "w", encoding="utf-8") as progress_file:
            json.dump(progress, progress_file, ensure_ascii=False, indent=2, default=str)
        try:
            os.remove(temp_path)
        except OSError:
            pass


def _previous_cycle_timestamps() -> tuple[str, str]:
    """Return only bounded success/completion timestamps carried by our state."""

    try:
        if os.path.islink(CYCLE_STATE_FILE):
            raise OSError("cycle state must not be a symlink")
        if os.path.getsize(CYCLE_STATE_FILE) > MAX_CYCLE_STATE_BYTES:
            raise OSError("cycle state is too large")
        with open(CYCLE_STATE_FILE, "r", encoding="utf-8") as state_file:
            previous = json.load(state_file)
    except FileNotFoundError:
        return "", ""
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring invalid crawler cycle state. error_type=%s", type(exc).__name__)
        return "", ""

    if not isinstance(previous, dict) or previous.get("schema_version") != CYCLE_STATE_SCHEMA_VERSION:
        return "", ""

    def bounded_timestamp(key: str) -> str:
        value = previous.get(key)
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 64
            or any(ord(character) < 32 for character in value)
        ):
            return ""
        return value

    return bounded_timestamp("last_success_at"), bounded_timestamp("last_completed_at")


def write_cycle_state(state: dict) -> dict:
    """Atomically persist bounded cycle-level outcome and freshness evidence.

    This state is deliberately filesystem-backed instead of production-DB-only
    so permission/configuration failures cannot be hidden by a successful row
    from one unrelated provider. Staging writes the same local evidence without
    changing its database batch contract.
    """

    if not isinstance(state, dict):
        raise TypeError("cycle state must be an object")
    ensure_log_dir()
    payload = dict(state)
    payload["schema_version"] = CYCLE_STATE_SCHEMA_VERSION
    payload["updated_at"] = now_iso()
    previous_success, previous_completion = _previous_cycle_timestamps()
    if payload.get("final_outcome") == "success":
        finished_at = payload.get("finished_at")
        if not isinstance(finished_at, str) or not finished_at:
            raise ValueError("successful cycle state requires finished_at")
        payload["last_success_at"] = finished_at
    else:
        payload["last_success_at"] = previous_success
    if payload.get("final_outcome") == "running":
        payload["last_completed_at"] = previous_completion
    else:
        finished_at = payload.get("finished_at")
        if not isinstance(finished_at, str) or not finished_at:
            raise ValueError("terminal cycle state requires finished_at")
        payload["last_completed_at"] = finished_at

    temporary_path = f"{CYCLE_STATE_FILE}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary_path, "x", encoding="utf-8") as state_file:
            json.dump(payload, state_file, ensure_ascii=False, indent=2, default=str)
            state_file.flush()
            os.fsync(state_file.fileno())
        if os.path.getsize(temporary_path) > MAX_CYCLE_STATE_BYTES:
            raise OSError("cycle state exceeds its bounded size")
        os.replace(temporary_path, CYCLE_STATE_FILE)
    finally:
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass
    return payload


def build_cycle_state(
    *,
    crawl_batch_id: str,
    cycle: int,
    started_at: str,
    finished_at: str,
    final_outcome: str,
    exit_code: int | None,
    providers_requested: int,
    providers_completed: int = 0,
    providers_failed: int = 0,
    concrete_providers_completed: int = 0,
    concrete_providers_failed: int = 0,
    batch_finished: bool | None = None,
    maintenance_failed: bool = False,
    report_path: str = "",
    failure_stage: str = "",
) -> dict:
    persistence_succeeded = providers_completed > 0 or concrete_providers_completed > 0
    return {
        "crawl_batch_id": crawl_batch_id,
        "cycle": cycle,
        "started_at": started_at,
        "finished_at": finished_at,
        "final_outcome": final_outcome,
        "exit_code": exit_code,
        "providers_requested": providers_requested,
        "providers_completed": providers_completed,
        "providers_failed": providers_failed,
        "concrete_providers_completed": concrete_providers_completed,
        "concrete_providers_failed": concrete_providers_failed,
        "zero_provider": final_outcome != "running" and not persistence_succeeded,
        "batch_finished": batch_finished,
        "maintenance_failed": maintenance_failed,
        "report_path": report_path,
        "failure_stage": failure_stage,
        "staging": staging_enabled(),
    }


def init_progress_state(
    *,
    cycle: int,
    providers: list[str],
    limit: Optional[int],
    run_interval: float,
    parallel: bool,
    max_workers: int,
    status: str = "running",
    run_id: str = "",
) -> dict:
    progress = {
        "schema_version": 1,
        "status": status,
        "cycle": cycle,
        "run_id": run_id or str(cycle),
        "started_at": now_iso(),
        "finished_at": "",
        "next_run_at": "",
        "run_interval_seconds": run_interval,
        "parallel": parallel,
        "max_workers": max_workers,
        "limit": limit,
        "providers_requested": providers,
        "providers": [{"provider": provider, "state": "pending"} for provider in providers],
    }
    write_progress(progress)
    init_crawl_progress(str(progress["run_id"]), providers)
    return progress


def set_provider_progress(progress: Optional[dict], provider: str, state: str, **fields) -> None:
    if progress is None:
        return
    for row in progress.get("providers") or []:
        if row.get("provider") == provider:
            row["state"] = state
            row.update({key: value for key, value in fields.items() if value is not None})
            break
    write_progress(progress)
    update_crawl_progress(str(progress.get("run_id") or progress.get("cycle") or ""), provider, state, **fields)


def finish_progress_cycle(
    progress: Optional[dict], status: str, *, next_run_at: str = "", latest_report: str = ""
) -> None:
    if progress is None:
        return
    progress["status"] = status
    progress["finished_at"] = now_iso()
    progress["next_run_at"] = next_run_at
    if latest_report:
        progress["latest_report"] = latest_report
    write_progress(progress)
    for row in progress.get("providers") or []:
        update_crawl_progress(
            str(progress.get("run_id") or progress.get("cycle") or ""),
            str(row.get("provider") or ""),
            str(row.get("state") or "pending"),
            latest_report=latest_report,
        )


def is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    if os.name != "nt":
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as cmdline_file:
                cmdline = cmdline_file.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
            return "run_crawlers.py" in cmdline
        except OSError:
            return False

    return True


def read_pid_file() -> Optional[int]:
    if not os.path.exists(PID_FILE):
        return None

    try:
        with open(PID_FILE, "r", encoding="utf-8") as pid_file:
            return int(pid_file.read().strip())
    except (OSError, ValueError):
        return None


def _try_lock_file(lock_file) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _lock_operation_may_be_contention(exc: OSError) -> bool:
    """Accept EACCES only from the lock syscall, never from opening the path."""

    contention_errnos = {errno.EACCES, errno.EAGAIN}
    if hasattr(errno, "EWOULDBLOCK"):
        contention_errnos.add(errno.EWOULDBLOCK)
    return exc.errno in contention_errnos


def _confirmed_lock_holder_pid() -> Optional[int]:
    # The lock owner publishes its PID immediately after flock/locking. Give
    # that bounded critical section time to finish before declaring corruption.
    for attempt in range(6):
        existing_pid = read_pid_file()
        if existing_pid is not None and is_process_running(existing_pid):
            return existing_pid
        if attempt < 5:
            time.sleep(0.05)
    return None


def acquire_worker_lock() -> str:
    global WORKER_LOCK_HANDLE
    if WORKER_LOCK_HANDLE is not None:
        return WORKER_LOCK_ACQUIRED
    try:
        ensure_log_dir()
    except OSError as exc:
        logger.error(
            "Crawler worker lock setup failed. operation=mkdir error_type=%s errno=%s",
            type(exc).__name__,
            exc.errno,
        )
        return WORKER_LOCK_ERROR

    try:
        lock_file = open(WORKER_LOCK_FILE, "a+", encoding="utf-8")
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(" ")
            lock_file.flush()
        lock_file.seek(0)
    except OSError as exc:
        logger.error(
            "Crawler worker lock setup failed. operation=open error_type=%s errno=%s",
            type(exc).__name__,
            exc.errno,
        )
        try:
            lock_file.close()
        except (OSError, UnboundLocalError):
            pass
        return WORKER_LOCK_ERROR

    try:
        _try_lock_file(lock_file)
    except OSError as exc:
        try:
            lock_file.close()
        except OSError:
            pass
        existing_pid = _confirmed_lock_holder_pid() if _lock_operation_may_be_contention(exc) else None
        if existing_pid is not None:
            logger.warning(
                "Crawler worker lock contention confirmed. pid=%s error_type=%s errno=%s",
                existing_pid,
                type(exc).__name__,
                exc.errno,
            )
            return WORKER_LOCK_CONTENDED
        logger.error(
            "Crawler worker lock acquisition failed. operation=lock error_type=%s errno=%s active_pid=none",
            type(exc).__name__,
            exc.errno,
        )
        return WORKER_LOCK_ERROR

    WORKER_LOCK_HANDLE = lock_file
    try:
        with open(PID_FILE, "w", encoding="utf-8") as pid_file:
            pid_file.write(str(os.getpid()))
            pid_file.flush()
            os.fsync(pid_file.fileno())
    except OSError as exc:
        logger.error(
            "Failed to publish crawler worker PID. error_type=%s errno=%s",
            type(exc).__name__,
            exc.errno,
        )
        release_worker_lock()
        return WORKER_LOCK_ERROR
    logger.info("Worker lock acquired. PID=%s", os.getpid())
    return WORKER_LOCK_ACQUIRED


def release_worker_lock() -> None:
    global WORKER_LOCK_HANDLE
    lock_file = WORKER_LOCK_HANDLE
    if lock_file is None:
        return
    try:
        try:
            if read_pid_file() == os.getpid():
                os.remove(PID_FILE)
        except FileNotFoundError:
            pass
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        logger.info("Worker lock released.")
    except OSError as exc:
        logger.warning("Failed to release worker lock: %s", type(exc).__name__)
    finally:
        try:
            lock_file.close()
        finally:
            WORKER_LOCK_HANDLE = None


def _bounded_cli_text(value: str | None, label: str, maximum: int = 100) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    if not normalized or len(normalized) > maximum or any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{label} must be 1-{maximum} printable characters")
    return normalized


def _validate_built_provider_command(provider: str, command: list[str]) -> None:
    if not 1 <= len(command) <= MAX_COMMAND_ARGUMENTS:
        raise RuntimeError(f"Built crawler command has an invalid argument count for provider={provider}")
    if any(
        not isinstance(argument, str)
        or not argument
        or len(argument) > MAX_COMMAND_ARGUMENT_LENGTH
        or any(ord(character) < 32 for character in argument)
        for argument in command
    ):
        raise RuntimeError(f"Built crawler command has an unsafe argument for provider={provider}")
    script_path = next((Path(argument).resolve() for argument in command if argument.endswith(".py")), None)
    project_root = Path(PROJECT_ROOT).resolve()
    if script_path is None or not script_path.is_relative_to(project_root) or not script_path.is_file():
        raise RuntimeError(f"Built crawler command entry point is invalid for provider={provider}")

    numeric_bounds = {
        "--target-limit": (1, 10_000),
        "--per-target-limit": (0, 100_000),
        "--parallel-workers": (1, MAX_PARALLEL_WORKERS),
        "--max-depth": (0, 5),
        "--max-pages": (1, 2_000),
        "--detail-limit": (0, 3_000),
        "--limit": (1, 1_000_000),
    }
    for index, argument in enumerate(command[:-1]):
        if argument not in numeric_bounds:
            continue
        minimum, maximum = numeric_bounds[argument]
        try:
            value = int(command[index + 1])
        except ValueError as exc:
            raise RuntimeError(f"{argument} must be an integer for provider={provider}") from exc
        if not minimum <= value <= maximum:
            raise RuntimeError(f"{argument} is outside its safe bounds for provider={provider}")


def build_provider_command(
    provider: str,
    limit: Optional[int],
    branch_code: str | None = None,
    branch_name: str | None = None,
) -> list[str]:
    if provider not in PROVIDER_ADAPTERS:
        raise KeyError(f"Unknown crawler provider: {provider}")
    branch_code = _bounded_cli_text(branch_code, "branch_code")
    branch_name = _bounded_cli_text(branch_name, "branch_name")
    command = PROVIDER_ADAPTERS[provider].build_command(
        limit,
        branch_code=branch_code,
        branch_name=branch_name,
    )
    command = apply_provider_arg_overrides(provider, command)
    _validate_built_provider_command(provider, command)
    return command


def apply_provider_arg_overrides(provider: str, command: list[str]) -> list[str]:
    registry_command = GENERATED_PROVIDER_COMMANDS.get(provider)
    if not registry_command:
        return command
    script_index = next(index for index, argument in enumerate(registry_command) if argument.endswith(".py"))
    registry_arguments = registry_command[script_index + 1 :]
    if registry_arguments[:1] == ["--provider"]:
        registry_arguments = registry_arguments[2:]
    replacements: dict[str, str] = {}
    index = 0
    while index < len(registry_arguments):
        option = registry_arguments[index]
        if option in GENERATED_REGISTRY_NUMERIC_ARGUMENTS:
            if option != "--per-target-limit":
                replacements[option] = registry_arguments[index + 1]
            index += 2
        else:
            index += 1
    if not replacements:
        return command
    normalized: list[str] = []
    index = 0
    while index < len(command):
        argument = command[index]
        if argument in replacements:
            if index + 1 >= len(command) or command[index + 1].startswith("--"):
                raise RuntimeError(f"Crawler option {argument} has no value for provider={provider}")
            index += 2
            continue
        normalized.append(argument)
        index += 1
    for option, value in replacements.items():
        normalized.extend((option, value))
    return normalized


def provider_source_type(provider: str) -> str:
    provider = str(provider or "").strip().upper()
    if provider == "HOMEPLUS":
        return "homeplus"
    if provider == "EMART":
        return "emart"
    if provider == "LOTTE":
        return "lotte"
    if provider in CULTURE_CENTER_PROVIDER_NAMES:
        return "culture_center"
    if provider.startswith("MUNI_") or provider in PARTIAL_AGGREGATE_PROVIDER_NAMES:
        return "education_experience"
    if provider in GENERATED_PROVIDER_NAMES:
        return "generated_yaml"
    return provider.lower() or "unknown"


def close_missing_is_safe(
    providers: list[str],
    limit: Optional[int],
    branch_code: str | None,
    branch_name: str | None,
) -> bool:
    """Only close unseen rows after a complete, unscoped provider crawl."""
    return bool(
        limit is None
        and not branch_code
        and not branch_name
        and not any(provider in PARTIAL_AGGREGATE_PROVIDER_NAMES for provider in providers)
    )


def provider_record_cleanup_is_safe(
    limit: Optional[int],
    branch_code: str | None,
    branch_name: str | None,
) -> bool:
    """Provider-wide cleanup is valid only after an unbounded full crawl."""
    return limit is None and not branch_code and not branch_name


def build_course_provider_owners(providers: list[str]) -> dict[str, str]:
    """Map every concrete course provider to its scheduled crawler owner.

    Most scheduled providers write rows with the same provider name. Aggregate
    workers are different: they run one scheduled owner while persisting rows
    for many concrete providers. Persisting this deterministic ownership
    snapshot with the batch lets the primary applier exclude every row owned by
    a failed aggregate without guessing from partially written staging data.
    """
    scheduled = {str(provider or "").strip().upper() for provider in providers if str(provider or "").strip()}
    if len(scheduled) != len(providers):
        raise ValueError("scheduled providers must be non-empty and unique")
    if any(not PROVIDER_NAME_PATTERN.fullmatch(provider) for provider in scheduled):
        raise ValueError("scheduled provider name is invalid")

    owners: dict[str, str] = {}

    def assign(actual_provider: str, scheduled_owner: str) -> None:
        actual = str(actual_provider or "").strip().upper()
        owner = str(scheduled_owner or "").strip().upper()
        if not PROVIDER_NAME_PATTERN.fullmatch(actual):
            raise ValueError(f"aggregate emitted invalid provider name: {actual!r}")
        previous = owners.get(actual)
        if previous is not None and previous != owner:
            raise ValueError(
                f"course provider has conflicting scheduled owners: provider={actual} owners={previous},{owner}"
            )
        owners[actual] = owner

    for owner in sorted(scheduled - AGGREGATE_PROVIDER_OWNERS):
        assign(owner, owner)

    if "MUNICIPAL_RESERVATION_TARGETS" in scheduled:
        from Crawler.Crawler_MunicipalIntegratedReservation import (
            configured_provider_names,
            load_municipal_targets,
            municipal_provider_names,
        )

        # The ops helper can invoke the aggregate owner explicitly while using
        # CRAWLER_PROVIDERS as an exclusion scope for every other operational
        # provider. Keep the ownership snapshot identical to the aggregate's
        # actual target selection instead of claiming providers it did not run.
        municipal_scheduled = set(scheduled) | configured_provider_names()
        municipal_targets = load_municipal_targets(scheduled_providers=municipal_scheduled)
        for actual in municipal_provider_names(municipal_targets):
            assign(actual, "MUNICIPAL_RESERVATION_TARGETS")

    if "EXPERIENCE_TARGETS" in scheduled:
        from Crawler.Crawler_EducationExperience import experience_provider_names

        for actual in experience_provider_names(scheduled_providers=set(scheduled)):
            assign(actual, "EXPERIENCE_TARGETS")

    missing_owners = scheduled - set(owners.values())
    if missing_owners:
        raise ValueError("scheduled aggregate has no concrete provider ownership: " + ",".join(sorted(missing_owners)))
    return dict(sorted(owners.items()))


def distributed_course_provider_owners(providers: list[str]) -> dict[str, str]:
    """Load the scheduler-frozen output scope for one distributed task."""

    if len(providers) != 1:
        raise ValueError("a distributed crawler task must have one scheduled provider")
    scheduled_owner = str(os.getenv("CRAWL_SCHEDULED_TASK_PROVIDER") or "").strip()
    if scheduled_owner != providers[0] or not PROVIDER_NAME_PATTERN.fullmatch(scheduled_owner):
        raise ValueError("distributed crawler task owner does not match its command")
    encoded = str(os.getenv("CRAWL_ALLOWED_OUTPUT_PROVIDERS_JSON") or "")
    if not encoded or len(encoded.encode("utf-8")) > 128 * 1024:
        raise ValueError("distributed crawler output provider scope is missing or too large")
    try:
        raw_providers = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError("distributed crawler output provider scope is invalid JSON") from exc
    if not isinstance(raw_providers, list) or not 1 <= len(raw_providers) <= 4096:
        raise ValueError("distributed crawler output provider scope is not a bounded list")
    concrete_providers: set[str] = set()
    for raw_provider in raw_providers:
        concrete_provider = str(raw_provider or "")
        if (
            not PROVIDER_NAME_PATTERN.fullmatch(concrete_provider)
            or concrete_provider in concrete_providers
        ):
            raise ValueError("distributed crawler output provider scope is invalid or duplicated")
        concrete_providers.add(concrete_provider)
    return {
        concrete_provider: scheduled_owner
        for concrete_provider in sorted(concrete_providers)
    }


def provider_target_key(provider: str, branch_code: str | None = None, branch_name: str | None = None) -> str:
    parts = [str(provider or "").strip().upper()]
    if branch_code:
        parts.append(f"branch_code={branch_code}")
    if branch_name:
        parts.append(f"branch_name={branch_name}")
    return "|".join(part for part in parts if part)


def _safe_error_text(value: object, maximum: int = 1_000) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(
        r"(?i)(\b(?:access[_-]?token|refresh[_-]?token|token|secret|password|api[_-]?key|client[_-]?secret)\b\s*[=:]\s*)([^\s&,;]+)",
        r"\1<redacted>",
        text,
    )
    return text[:maximum]


def minimal_provider_report(
    provider: str,
    *,
    started_at: str,
    started_time: float,
    limit: Optional[int],
    exit_code: int | None,
    error_type: str,
    error_message: str,
) -> dict:
    return {
        "provider": provider,
        "started_at": started_at,
        "finished_at": now_iso(),
        "success": False,
        "exit_code": exit_code,
        "elapsed_seconds": round(max(time.monotonic() - started_time, 0.0), 2),
        "limit": limit,
        "total": 0,
        "active_total": 0,
        "inactive_total": 0,
        "created_since": 0,
        "updated_since": 0,
        "quality": {},
        "missing": {},
        "weak_samples": [],
        "error_type": _safe_error_text(error_type, 100),
        "error_message": _safe_error_text(error_message),
    }


def build_provider_report_safe(
    *,
    provider: str,
    started_at: str,
    started_time: float,
    process_success: bool,
    exit_code: int | None,
    limit: Optional[int],
) -> dict:
    try:
        return build_provider_report(
            provider=provider,
            started_at=started_at,
            finished_at=now_iso(),
            success=process_success,
            exit_code=exit_code,
            elapsed_seconds=time.monotonic() - started_time,
            limit=limit,
        )
    except Exception as exc:
        logger.error("Failed to build crawler report for provider=%s error_type=%s", provider, type(exc).__name__)
        return minimal_provider_report(
            provider,
            started_at=started_at,
            started_time=started_time,
            limit=limit,
            exit_code=exit_code if exit_code not in {None, 0} else 70,
            error_type="ReportBuildError",
            error_message=type(exc).__name__,
        )


def finish_provider_run_log(
    run_log_id: int | None,
    report: dict,
    *,
    status: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> bool:
    if not run_log_id:
        report["success"] = False
        report["error_type"] = "RunLogUnavailable"
        report["error_message"] = "crawler_run_log start record was unavailable"
        return False
    inserted_count = int(report.get("created_since") or 0)
    touched_count = int(report.get("updated_since") or 0)
    updated_count = max(touched_count - inserted_count, 0)
    collected_count = max(touched_count, inserted_count)
    finished = finish_crawler_run(
        run_id=run_log_id,
        status=status or ("success" if report.get("success") else "failed"),
        collected_count=collected_count,
        inserted_count=inserted_count,
        updated_count=updated_count,
        skipped_count=0,
        error_type=error_type or report.get("error_type"),
        error_message=error_message or report.get("error_message"),
    )
    if not finished:
        report["success"] = False
        report["error_type"] = "RunLogFinalizeError"
        report["error_message"] = "crawler_run_log final update failed"
    return finished


def interruptible_sleep(seconds: float) -> None:
    deadline = time.monotonic() + max(float(seconds), 0.0)
    while RUNNING:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, PROCESS_POLL_INTERVAL_SECONDS))


def _spawn_process(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.Popen:
    kwargs: dict[str, object] = {
        "cwd": PROJECT_ROOT,
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _new_concrete_result_manifest_path(provider: str) -> Path | None:
    normalized_provider = str(provider or "").strip().upper()
    if normalized_provider not in AGGREGATE_PROVIDER_OWNERS:
        return None
    batch_id = str(os.getenv("CRAWL_BATCH_ID") or "no-batch").strip()
    safe_batch_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", batch_id)[:100] or "no-batch"
    return Path(CONCRETE_RESULT_MANIFEST_DIR) / safe_batch_id / f"{normalized_provider}-{uuid.uuid4().hex}.json"


_RETIRED_GOOGLE_MAP_CREDENTIALS = {
    "GOOGLE_MAPS_API_KEY",
    "VITE_GOOGLE_MAPS_API_KEY",
    "MOONCENGOOGLEMAPSAPIKEY",
}


def _strip_retired_google_map_credentials(env: dict[str, str]) -> None:
    """Remove retired map credentials regardless of platform key casing."""
    for name in list(env):
        if name.upper() in _RETIRED_GOOGLE_MAP_CREDENTIALS:
            env.pop(name, None)


def _spawn_provider_process(provider: str, command: list[str]) -> subprocess.Popen:
    manifest_path = _new_concrete_result_manifest_path(provider)
    child_env = os.environ.copy()
    # Provider crawlers do not geocode. Keep map credentials out of their
    # inherited environment. Google Maps credentials are retired and are
    # stripped defensively even on hosts with a stale crawler.env.
    child_env.pop("KAKAO_MAPS_REST_API_KEY", None)
    child_env.pop("MoonCenKakaoMapsRestApiKey", None)
    _strip_retired_google_map_credentials(child_env)
    child_env.pop(CONCRETE_RESULT_MANIFEST_PATH_ENV, None)
    child_env.pop(SCHEDULED_PROVIDER_ENV, None)
    if manifest_path is not None:
        child_env[CONCRETE_RESULT_MANIFEST_PATH_ENV] = str(manifest_path)
        child_env[SCHEDULED_PROVIDER_ENV] = provider
    process = _spawn_process(command, env=child_env)
    if manifest_path is not None:
        setattr(process, "_mooncen_concrete_result_manifest_path", str(manifest_path))
    return process


def _manifest_failure(report: dict, provider: str, message: str) -> None:
    logger.error("Concrete provider result evidence failed. provider=%s reason=%s", provider, message)
    if report.get("success"):
        report["success"] = False
        report["error_type"] = "ConcreteResultManifestError"
        report["error_message"] = "aggregate crawler persistence evidence is unavailable"


def attach_concrete_provider_results(
    provider: str,
    process: subprocess.Popen,
    report: dict,
) -> None:
    """Consume a bounded child manifest; an aggregate exit 0 requires this evidence."""
    if provider not in AGGREGATE_PROVIDER_OWNERS:
        return

    raw_path = str(getattr(process, "_mooncen_concrete_result_manifest_path", "") or "").strip()
    if not raw_path:
        _manifest_failure(report, provider, "manifest path was not assigned")
        return

    path = Path(raw_path)
    safe_to_remove = False
    try:
        manifest_root = Path(CONCRETE_RESULT_MANIFEST_DIR).resolve()
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(manifest_root):
            raise ValueError("manifest path escaped its bounded directory")
        safe_to_remove = True
        size = resolved_path.stat().st_size
        if size <= 0 or size > MAX_CONCRETE_RESULT_MANIFEST_BYTES:
            raise ValueError("manifest size is invalid")
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("manifest version is invalid")
        if payload.get("save_db") is not True:
            raise ValueError("manifest lacks database persistence evidence")
        if str(payload.get("scheduled_provider") or "").strip().upper() != provider:
            raise ValueError("manifest scheduled provider does not match")
        expected_batch_id = str(os.getenv("CRAWL_BATCH_ID") or "").strip()
        if str(payload.get("crawl_batch_id") or "").strip() != expected_batch_id:
            raise ValueError("manifest crawl batch does not match")

        raw_results = payload.get("providers")
        if not isinstance(raw_results, list) or not 1 <= len(raw_results) <= MAX_PROVIDERS_PER_RUN:
            raise ValueError("manifest concrete provider results are missing or unbounded")
        normalized_results: list[dict] = []
        seen: set[str] = set()
        for item in raw_results:
            if not isinstance(item, dict):
                raise ValueError("manifest concrete provider result is not an object")
            concrete_provider = str(item.get("provider") or "").strip().upper()
            if not PROVIDER_NAME_PATTERN.fullmatch(concrete_provider) or concrete_provider in seen:
                raise ValueError("manifest concrete provider is invalid or duplicated")
            success = item.get("success")
            if not isinstance(success, bool):
                raise ValueError("manifest concrete provider success is not boolean")
            integer_fields: dict[str, int] = {}
            for field in (
                "targets_total",
                "targets_succeeded",
                "collected_courses",
                "saved_courses",
            ):
                value = item.get(field)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"manifest {field} is invalid")
                integer_fields[field] = value
            if (
                integer_fields["targets_total"] <= 0
                or integer_fields["targets_succeeded"] > integer_fields["targets_total"]
                or (success and integer_fields["targets_succeeded"] != integer_fields["targets_total"])
            ):
                raise ValueError("manifest concrete target counters are inconsistent")
            seen.add(concrete_provider)
            normalized_results.append(
                {
                    "provider": concrete_provider,
                    "success": success,
                    **integer_fields,
                }
            )
        report["concrete_provider_results"] = sorted(
            normalized_results,
            key=lambda item: item["provider"],
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        _manifest_failure(report, provider, type(exc).__name__)
    finally:
        if safe_to_remove:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Could not remove consumed concrete result manifest. provider=%s",
                    provider,
                )


def concrete_provider_failure_summary(report: dict) -> str:
    """Return a bounded, non-sensitive aggregate failure summary for the DB log."""

    raw_results = report.get("concrete_provider_results")
    if not isinstance(raw_results, list):
        return ""
    failed = sorted(
        {
            str(item.get("provider") or "").strip().upper()
            for item in raw_results
            if isinstance(item, dict) and item.get("success") is False
        }
    )
    failed = [provider for provider in failed if PROVIDER_NAME_PATTERN.fullmatch(provider)]
    if not failed:
        return ""
    visible = failed[:25]
    suffix = f" (+{len(failed) - len(visible)} more)" if len(failed) > len(visible) else ""
    return _safe_error_text(
        f"failed concrete providers ({len(failed)}/{len(raw_results)}): "
        f"{','.join(visible)}{suffix}"
    )


def provider_failure_message(report: dict, fallback: str) -> str:
    base = _safe_error_text(report.get("error_message") or fallback)
    concrete = concrete_provider_failure_summary(report)
    if not concrete:
        return base
    if not base or base == fallback:
        return concrete
    return _safe_error_text(f"{base}; {concrete}")


def provider_failure_type(report: dict, fallback: str) -> str:
    """Classify validated aggregate evidence without weakening its failure gate."""

    raw_results = report.get("concrete_provider_results")
    if not isinstance(raw_results, list) or not raw_results:
        return fallback
    successes = [item.get("success") for item in raw_results if isinstance(item, dict)]
    if len(successes) != len(raw_results) or any(not isinstance(value, bool) for value in successes):
        return fallback
    if any(successes) and not all(successes):
        return "AggregatePartialFailure"
    if not any(successes):
        return "AggregateFailure"
    return fallback


def refresh_concrete_provider_snapshot(report: dict, started_at: str) -> None:
    """Report aggregate DB activity over only commit-proven concrete providers."""
    raw_results = report.get("concrete_provider_results")
    if not isinstance(raw_results, list):
        return
    successful_providers = [
        str(item.get("provider") or "").strip().upper()
        for item in raw_results
        if isinstance(item, dict) and item.get("success") is True
    ]
    if not successful_providers:
        return
    try:
        report.update(
            fetch_provider_snapshot(
                str(report.get("provider") or "").strip().upper(),
                since_iso=started_at,
                course_providers=successful_providers,
            )
        )
    except Exception as exc:
        logger.error(
            "Failed to refresh aggregate crawler report. provider=%s error_type=%s",
            report.get("provider"),
            type(exc).__name__,
        )


def run_provider(
    provider: str,
    limit: Optional[int],
    timeout: Optional[float],
    branch_code: str | None = None,
    branch_name: str | None = None,
    *,
    timeout_is_active_window: bool = False,
) -> dict:
    command = build_provider_command(provider, limit, branch_code=branch_code, branch_name=branch_name)
    run_log_id = start_crawler_run(
        target_key=provider_target_key(provider, branch_code, branch_name),
        source_type=provider_source_type(provider),
        crawler_name=" ".join(command),
    )
    started_at = now_iso()
    started = time.monotonic()
    if run_log_id is None:
        return minimal_provider_report(
            provider,
            started_at=started_at,
            started_time=started,
            limit=limit,
            exit_code=70,
            error_type="RunLogUnavailable",
            error_message="crawler_run_log start record was unavailable",
        )
    logger.info("Starting %s crawler. limit=%s timeout=%s", provider, limit, timeout)
    try:
        process = _spawn_provider_process(provider, command)
    except Exception as exc:
        report = minimal_provider_report(
            provider,
            started_at=started_at,
            started_time=started,
            limit=limit,
            exit_code=70,
            error_type=type(exc).__name__,
            error_message="crawler process could not be started",
        )
        finish_provider_run_log(run_log_id, report, error_type=type(exc).__name__, error_message=type(exc).__name__)
        return report

    deadline = time.monotonic() + timeout if timeout is not None else None
    stopped = False
    timed_out = False
    while process.poll() is None:
        if not RUNNING:
            stopped = True
            terminate_process_tree(process)
            break
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            terminate_process_tree(process)
            break
        interruptible_sleep(PROCESS_POLL_INTERVAL_SECONDS)

    exit_code = process.poll()
    process_success = exit_code == 0 and not stopped and not timed_out
    if process_success:
        logger.info("%s crawler completed.", provider)
    elif timed_out:
        logger.warning("%s crawler exceeded its execution deadline.", provider)
    elif stopped:
        logger.warning("%s crawler stopped because the worker is shutting down.", provider)
    else:
        logger.error("%s crawler failed with exit code %s.", provider, exit_code)

    report = build_provider_report_safe(
        provider=provider,
        started_at=started_at,
        started_time=started,
        process_success=process_success,
        exit_code=exit_code,
        limit=limit,
    )
    attach_concrete_provider_results(provider, process, report)
    refresh_concrete_provider_snapshot(report, started_at)
    if report.get("success") and provider_record_cleanup_is_safe(limit, branch_code, branch_name):
        cleanup_empty_branches(provider)
    if timed_out:
        error_type = "ActiveWindowExpired" if timeout_is_active_window else "ProviderTimeout"
        error_message = (
            "Active window ended before crawler completed."
            if timeout_is_active_window
            else "Provider execution deadline exceeded."
        )
        status = "stopped" if timeout_is_active_window else "failed"
    elif stopped:
        error_type = "WorkerStopped"
        error_message = "Worker stopped before crawler completed."
        status = "stopped"
    elif not report.get("success"):
        error_type = provider_failure_type(
            report,
            str(report.get("error_type") or "CalledProcessError"),
        )
        error_message = provider_failure_message(report, f"exit_code={exit_code}")
        status = "failed"
    else:
        error_type = None
        error_message = None
        status = "success"
    if error_type:
        report["success"] = False
        report["error_type"] = error_type
        report["error_message"] = _safe_error_text(error_message)
    finish_provider_run_log(
        run_log_id,
        report,
        status=status,
        error_type=error_type,
        error_message=error_message,
    )
    return report


def run_provider_process(
    provider: str, limit: Optional[int], branch_code: str | None = None, branch_name: str | None = None
) -> tuple[str, subprocess.Popen, str, float, int | None]:
    command = build_provider_command(provider, limit, branch_code=branch_code, branch_name=branch_name)
    logger.info("Starting %s crawler in parallel. limit=%s", provider, limit)
    run_log_id = start_crawler_run(
        target_key=provider_target_key(provider, branch_code, branch_name),
        source_type=provider_source_type(provider),
        crawler_name=" ".join(command),
    )
    started_at = now_iso()
    started_time = time.monotonic()
    if run_log_id is None:
        raise RuntimeError("crawler_run_log start record was unavailable")
    try:
        process = _spawn_provider_process(provider, command)
    except Exception as exc:
        if run_log_id:
            finish_crawler_run(
                run_id=run_log_id,
                status="failed",
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
        raise
    return provider, process, started_at, started_time, run_log_id


def terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The direct crawler may exit on SIGTERM while a descendant ignores it.
        # A process group remains addressable as long as any such descendant is
        # alive, so always close the grace period with SIGKILL for the old pgid.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.wait(timeout=10)


def _finalize_parallel_process(
    provider: str,
    process: subprocess.Popen,
    meta: tuple[str, float, int | None, float],
    *,
    limit: Optional[int],
    branch_code: str | None,
    branch_name: str | None,
    progress_state: Optional[dict],
    forced_status: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> dict:
    started_at, started_time, run_log_id, _deadline = meta
    return_code = process.poll()
    process_success = return_code == 0 and forced_status is None
    report = build_provider_report_safe(
        provider=provider,
        started_at=started_at,
        started_time=started_time,
        process_success=process_success,
        exit_code=return_code,
        limit=limit,
    )
    attach_concrete_provider_results(provider, process, report)
    refresh_concrete_provider_snapshot(report, started_at)
    if report.get("success") and provider_record_cleanup_is_safe(limit, branch_code, branch_name):
        cleanup_empty_branches(provider)
    final_status = forced_status or ("success" if report.get("success") else "failed")
    final_error_type = error_type or (
        None
        if report.get("success")
        else provider_failure_type(
            report,
            str(report.get("error_type") or "CalledProcessError"),
        )
    )
    final_error_message = error_message or (
        None
        if report.get("success")
        else provider_failure_message(report, f"exit_code={return_code}")
    )
    if final_error_type:
        report["success"] = False
        report["error_type"] = final_error_type
        report["error_message"] = _safe_error_text(final_error_message)
    finish_provider_run_log(
        run_log_id,
        report,
        status=final_status,
        error_type=final_error_type,
        error_message=final_error_message,
    )
    if not report.get("success"):
        final_error_message = str(report.get("error_message") or final_error_message or f"exit_code={return_code}")
    progress_status = "success" if report.get("success") else ("stopped" if final_status == "stopped" else "failed")
    set_provider_progress(
        progress_state,
        provider,
        progress_status,
        success=bool(report.get("success")),
        finished_at=report.get("finished_at"),
        elapsed_seconds=report.get("elapsed_seconds"),
        exit_code=report.get("exit_code"),
        error=final_error_message,
    )
    return report


def run_cycle_parallel(
    providers: list[str],
    limit: Optional[int],
    active_start: datetime_time,
    active_end: datetime_time,
    enforce_active_window: bool,
    max_workers: int,
    provider_timeout: float | None = None,
    branch_code: str | None = None,
    branch_name: str | None = None,
    progress_state: Optional[dict] = None,
) -> list[dict]:
    active_window_initially_open = not enforce_active_window or is_within_active_window(
        datetime.now().time(), active_start, active_end
    )
    active_timeout = (
        seconds_until_window_end(datetime.now(), active_start, active_end) if enforce_active_window else None
    )
    active_deadline = time.monotonic() + active_timeout if active_timeout is not None else None
    if enforce_active_window and not active_window_initially_open:
        active_deadline = time.monotonic()
    pending = list(providers)
    remaining: dict[str, subprocess.Popen] = {}
    process_report_meta: dict[str, tuple[str, float, int | None, float]] = {}
    reports_by_provider: dict[str, dict] = {}

    try:
        while (pending or remaining) and RUNNING:
            active_window_open = not enforce_active_window or is_within_active_window(
                datetime.now().time(), active_start, active_end
            )
            while pending and len(remaining) < max_workers and active_window_open and RUNNING:
                provider = pending.pop(0)
                try:
                    provider, process, started_at, started_time, run_log_id = run_provider_process(
                        provider,
                        limit,
                        branch_code=branch_code,
                        branch_name=branch_name,
                    )
                except Exception as exc:
                    logger.error("Failed to start provider=%s error_type=%s", provider, type(exc).__name__)
                    report = minimal_provider_report(
                        provider,
                        started_at=now_iso(),
                        started_time=time.monotonic(),
                        limit=limit,
                        exit_code=70,
                        error_type=type(exc).__name__,
                        error_message="crawler process could not be started",
                    )
                    reports_by_provider[provider] = report
                    set_provider_progress(
                        progress_state,
                        provider,
                        "failed",
                        success=False,
                        finished_at=report["finished_at"],
                        exit_code=70,
                        error=report["error_message"],
                    )
                    continue
                remaining[provider] = process
                process_report_meta[provider] = (
                    started_at,
                    started_time,
                    run_log_id,
                    started_time + effective_provider_timeout_seconds(provider, provider_timeout),
                )
                set_provider_progress(
                    progress_state,
                    provider,
                    "running",
                    started_at=started_at,
                    pid=process.pid,
                )

            for provider, process in list(remaining.items()):
                meta = process_report_meta[provider]
                if process.poll() is not None:
                    remaining.pop(provider)
                    process_report_meta.pop(provider)
                    reports_by_provider[provider] = _finalize_parallel_process(
                        provider,
                        process,
                        meta,
                        limit=limit,
                        branch_code=branch_code,
                        branch_name=branch_name,
                        progress_state=progress_state,
                    )
                    continue
                if time.monotonic() >= meta[3]:
                    logger.warning("Provider execution deadline exceeded. provider=%s pid=%s", provider, process.pid)
                    terminate_process_tree(process)
                    remaining.pop(provider)
                    process_report_meta.pop(provider)
                    reports_by_provider[provider] = _finalize_parallel_process(
                        provider,
                        process,
                        meta,
                        limit=limit,
                        branch_code=branch_code,
                        branch_name=branch_name,
                        progress_state=progress_state,
                        forced_status="failed",
                        error_type="ProviderTimeout",
                        error_message="Provider execution deadline exceeded.",
                    )

            if not pending and not remaining:
                break
            if active_deadline is not None and time.monotonic() >= active_deadline:
                logger.warning("Active window ended. Stopping %s running crawlers.", len(remaining))
                break
            interruptible_sleep(PROCESS_POLL_INTERVAL_SECONDS)

        stop_type = "WorkerStopped" if not RUNNING else "ActiveWindowExpired"
        stop_message = (
            "Worker stopped before crawler completed."
            if not RUNNING
            else "Active window ended before crawler completed."
        )
        for provider, process in list(remaining.items()):
            logger.warning("Stopping %s crawler. PID=%s", provider, process.pid)
            terminate_process_tree(process)
            meta = process_report_meta.pop(provider)
            reports_by_provider[provider] = _finalize_parallel_process(
                provider,
                process,
                meta,
                limit=limit,
                branch_code=branch_code,
                branch_name=branch_name,
                progress_state=progress_state,
                forced_status="stopped",
                error_type=stop_type,
                error_message=stop_message,
            )
            remaining.pop(provider, None)

        for provider in pending:
            report = minimal_provider_report(
                provider,
                started_at=now_iso(),
                started_time=time.monotonic(),
                limit=limit,
                exit_code=None,
                error_type=stop_type,
                error_message=stop_message,
            )
            reports_by_provider[provider] = report
            set_provider_progress(
                progress_state,
                provider,
                "stopped",
                success=False,
                finished_at=report["finished_at"],
                error=stop_message,
            )
    finally:
        for provider, process in list(remaining.items()):
            if process.poll() is None:
                logger.warning("Cleaning up %s crawler. PID=%s", provider, process.pid)
                terminate_process_tree(process)
            meta = process_report_meta.get(provider)
            if meta and provider not in reports_by_provider:
                reports_by_provider[provider] = _finalize_parallel_process(
                    provider,
                    process,
                    meta,
                    limit=limit,
                    branch_code=branch_code,
                    branch_name=branch_name,
                    progress_state=progress_state,
                    forced_status="stopped",
                    error_type="WorkerCleanup",
                    error_message="Crawler was stopped during worker cleanup.",
                )

    return [reports_by_provider[provider] for provider in providers]


def run_cycle(
    providers: list[str],
    limit: Optional[int],
    active_start: datetime_time,
    active_end: datetime_time,
    enforce_active_window: bool,
    parallel: bool,
    max_workers: int,
    provider_timeout: float | None = None,
    branch_code: str | None = None,
    branch_name: str | None = None,
    progress_state: Optional[dict] = None,
) -> list[dict]:
    if parallel:
        return run_cycle_parallel(
            providers,
            limit,
            active_start,
            active_end,
            enforce_active_window,
            max_workers,
            provider_timeout,
            branch_code=branch_code,
            branch_name=branch_name,
            progress_state=progress_state,
        )

    reports = []
    for provider_index, provider in enumerate(providers):
        if not RUNNING:
            for pending_provider in providers[provider_index:]:
                report = minimal_provider_report(
                    pending_provider,
                    started_at=now_iso(),
                    started_time=time.monotonic(),
                    limit=limit,
                    exit_code=None,
                    error_type="WorkerStopped",
                    error_message="Worker stopped before crawler started.",
                )
                reports.append(report)
                set_provider_progress(
                    progress_state,
                    pending_provider,
                    "stopped",
                    success=False,
                    finished_at=report["finished_at"],
                    error=report["error_message"],
                )
            break

        timeout = effective_provider_timeout_seconds(provider, provider_timeout)
        timeout_is_active_window = False
        if enforce_active_window:
            now = datetime.now()
            if not is_within_active_window(now.time(), active_start, active_end):
                logger.info("Active window ended before %s. Pausing cycle.", provider)
                for pending_provider in providers[provider_index:]:
                    report = minimal_provider_report(
                        pending_provider,
                        started_at=now_iso(),
                        started_time=time.monotonic(),
                        limit=limit,
                        exit_code=None,
                        error_type="ActiveWindowExpired",
                        error_message="Active window ended before crawler started.",
                    )
                    reports.append(report)
                    set_provider_progress(
                        progress_state,
                        pending_provider,
                        "stopped",
                        success=False,
                        finished_at=report["finished_at"],
                        error=report["error_message"],
                    )
                break
            window_timeout = seconds_until_window_end(now, active_start, active_end)
            if window_timeout is not None and window_timeout <= timeout:
                timeout = window_timeout
                timeout_is_active_window = True

        started_at = now_iso()
        set_provider_progress(progress_state, provider, "running", started_at=started_at)
        report = run_provider(
            provider,
            limit,
            timeout,
            branch_code=branch_code,
            branch_name=branch_name,
            timeout_is_active_window=timeout_is_active_window,
        )
        reports.append(report)
        set_provider_progress(
            progress_state,
            provider,
            "success" if report.get("success") else "failed",
            success=bool(report.get("success")),
            finished_at=report.get("finished_at"),
            elapsed_seconds=report.get("elapsed_seconds"),
            exit_code=report.get("exit_code"),
            error=report.get("error_message"),
        )
    return reports


def maintenance_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CRAWL_BATCH_ID", None)
    # Coordinate maintenance is Kakao-only. Never let stale Google Maps
    # credentials make a legacy helper capable of issuing billable calls.
    _strip_retired_google_map_credentials(env)
    return env


def run_maintenance_process(
    command: list[str],
    label: str,
    timeout_seconds: float = 1_800.0,
    *,
    accepted_exit_codes: tuple[int, ...] = (0,),
) -> bool:
    logger.info("Starting %s.", label)
    started = time.monotonic()
    try:
        process = _spawn_process(command, env=maintenance_env())
    except Exception as exc:
        logger.error("Failed to start %s. error_type=%s", label, type(exc).__name__)
        return False
    deadline = started + timeout_seconds
    while process.poll() is None:
        if not RUNNING or time.monotonic() >= deadline:
            terminate_process_tree(process)
            reason = "worker shutdown" if not RUNNING else "execution deadline"
            logger.error("%s stopped because of %s.", label, reason)
            return False
        interruptible_sleep(PROCESS_POLL_INTERVAL_SECONDS)
    elapsed = time.monotonic() - started
    if process.returncode not in accepted_exit_codes:
        logger.error("%s failed. exit_code=%s elapsed=%.1fs", label, process.returncode, elapsed)
        return False
    if process.returncode != 0:
        logger.warning(
            "%s made bounded partial progress. exit_code=%s elapsed=%.1fs",
            label,
            process.returncode,
            elapsed,
        )
        return True
    logger.info("%s completed. elapsed=%.1fs", label, elapsed)
    return True


def coordinate_geocode_request_budgets(total: int) -> dict[str, int]:
    """Split one hard request ceiling across every non-overlapping safe pass."""

    if isinstance(total, bool) or not 100 <= total <= 100_000:
        raise ValueError("Kakao coordinate request budget must be between 100 and 100000")
    budgets = {
        "address": total * 12 // 100,
        "course_address": total * 8 // 100,
        "stored_region": total * 30 // 100,
        "configured_locality": total * 38 // 100,
    }
    budgets["legacy_reverify"] = total - sum(budgets.values())
    if any(value < 1 for value in budgets.values()) or sum(budgets.values()) != total:
        raise ValueError("Kakao coordinate request budget allocation is invalid")
    return budgets


def run_coordinate_backfill(limit: Optional[int], delay: float) -> bool:
    verified_copy_command = [
        sys.executable,
        "-X",
        "utf8",
        os.path.join("tools", "maintenance", "propagate_branch_locations.py"),
        "--with-active-courses",
    ]
    if limit:
        verified_copy_command.extend(["--limit", str(limit)])
    if not run_maintenance_process(
        verified_copy_command,
        "verified same-name branch coordinate propagation",
    ):
        return False

    dotenv_config: dict[str, object] = {}
    try:
        dotenv_config = dict(dotenv_values(Path(PROJECT_ROOT) / ".env"))
    except OSError:
        dotenv_config = {}
    key_names = (
        "KAKAO_MAPS_REST_API_KEY",
        "MoonCenKakaoMapsRestApiKey",
    )
    configured = any(str(os.getenv(name) or "").strip() for name in key_names)
    if not configured:
        configured = any(str(dotenv_config.get(name) or "").strip() for name in key_names)
    if not configured:
        logger.warning("Skipping branch coordinate backfill because no Kakao Maps REST API key is configured.")
        return True

    budget_value = (
        os.getenv("KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN")
        or dotenv_config.get("KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN")
        or "1000"
    )
    try:
        request_budgets = coordinate_geocode_request_budgets(int(str(budget_value)))
    except (TypeError, ValueError):
        logger.warning(
            "Invalid KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN; using bounded default."
        )
        request_budgets = coordinate_geocode_request_budgets(1000)

    common_command = [
        sys.executable,
        "-X",
        "utf8",
        os.path.join("tools", "maintenance", "kakao_geocode_branches.py"),
        "--with-active-courses",
        "--delay",
        str(delay),
    ]
    if limit:
        common_command.extend(["--limit", str(limit)])

    commands = (
        (
            [
                *common_command,
                "--address-only",
                "--retry-after-days",
                "30",
                "--max-requests",
                str(request_budgets["address"]),
            ],
            "branch address coordinate backfill",
        ),
        (
            [
                *common_command,
                "--course-address-only",
                "--retry-after-days",
                "30",
                "--max-requests",
                str(request_budgets["course_address"]),
            ],
            "branch course-address coordinate backfill",
        ),
        (
            [
                *common_command,
                "--region-keyword-only",
                "--retry-after-days",
                "14",
                "--max-requests",
                str(request_budgets["stored_region"]),
            ],
            "branch region coordinate backfill",
        ),
        (
            [
                *common_command,
                "--configured-locality-only",
                "--retry-after-days",
                "30",
                "--max-requests",
                str(request_budgets["configured_locality"]),
            ],
            "branch configured-locality coordinate backfill",
        ),
        (
            [
                *common_command,
                "--verify-existing",
                "--coordinate-source-prefix",
                "GOOGLE",
                "--retry-after-days",
                "30",
                "--max-requests",
                str(request_budgets["legacy_reverify"]),
            ],
            "legacy Google coordinate Kakao reverification",
        ),
    )
    logger.info(
        "Coordinate backfill parameters. limit=%s delay=%s modes=address,course-address,"
        "stored-region,configured-locality,legacy-reverify total_request_budget=%s",
        limit,
        delay,
        sum(request_budgets.values()),
    )
    for command, label in commands:
        # Exit 3 means this pass exhausted its own bounded request budget. It
        # is expected partial progress, not a hard failure, and must not starve
        # the remaining non-overlapping evidence paths.
        if not run_maintenance_process(command, label, accepted_exit_codes=(0, 3)):
            return False
    return True


def run_category_backfill() -> bool:
    commands = [
        (
            [
                sys.executable,
                "-X",
                "utf8",
                os.path.join("tools", "maintenance", "backfill_course_categories.py"),
            ],
            "course category metadata backfill",
        ),
        (
            [
                sys.executable,
                "-X",
                "utf8",
                os.path.join("tools", "maintenance", "backfill_standard_categories.py"),
            ],
            "standard category backfill",
        ),
    ]
    results = [run_maintenance_process(command, label) for command, label in commands]
    return all(results)


def run_ended_course_cleanup(grace_days: int = 7) -> bool:
    if staging_enabled():
        logger.info("Skipping ended-course cleanup in staging write mode.")
        return True
    crawl_batch_id = os.environ.pop("CRAWL_BATCH_ID", None)
    try:
        result = apply_ended_course_lifecycle(grace_days=grace_days)
        logger.info(
            "Ended course cleanup completed. grace_days=%s closed=%s deactivated=%s",
            grace_days,
            result["closed"],
            result["deactivated"],
        )
        return True
    except Exception as exc:
        logger.error("Ended course cleanup failed: %s", exc)
        return False
    finally:
        if crawl_batch_id:
            os.environ["CRAWL_BATCH_ID"] = crawl_batch_id


def _run_maintenance_step(name: str, requested: bool, operation) -> dict[str, object]:
    if not requested:
        return {"requested": False, "success": None}
    try:
        success = bool(operation())
    except Exception as exc:
        logger.error(
            "%s raised unexpectedly. error_type=%s error=%s",
            name,
            type(exc).__name__,
            _safe_error_text(exc),
        )
        success = False
    return {"requested": True, "success": success}


def run_cycle_maintenance(
    *,
    coordinate_backfill: bool,
    coordinate_backfill_limit: Optional[int],
    coordinate_backfill_delay: float,
    category_backfill: bool,
) -> dict[str, dict[str, object]]:
    """Run independent maintenance steps without one failure skipping the rest."""
    results = {
        "coordinate_backfill": _run_maintenance_step(
            "branch coordinate backfill",
            coordinate_backfill,
            lambda: run_coordinate_backfill(
                coordinate_backfill_limit,
                coordinate_backfill_delay,
            ),
        ),
        "category_backfill": _run_maintenance_step(
            "course category backfill",
            category_backfill,
            run_category_backfill,
        ),
    }
    # Ended-course cleanup is deliberately last and unconditional. In particular,
    # a transient geocoder failure must never leave expired courses active forever.
    results["ended_course_cleanup"] = _run_maintenance_step(
        "ended course cleanup",
        True,
        lambda: run_ended_course_cleanup(7),
    )
    return results


def staging_enabled() -> bool:
    return os.getenv("CRAWL_WRITE_MODE", "").lower() == "staging"


def distributed_task_batch_id() -> str:
    enabled = os.getenv("CRAWL_DISTRIBUTED_TASK", "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return ""
    if not staging_enabled():
        raise ValueError("distributed crawler tasks may write only to staging")
    if os.getenv("CRAWL_REQUIRE_LEASE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise ValueError("distributed crawler tasks require a fenced lease")
    raw = os.getenv("CRAWL_BATCH_ID", "").strip()
    try:
        batch_id = str(uuid.UUID(raw))
    except (ValueError, AttributeError) as exc:
        raise ValueError("distributed crawler task batch id must be a canonical UUID") from exc
    if raw.lower() != batch_id:
        raise ValueError("distributed crawler task batch id must be a canonical UUID")
    return batch_id


def distributed_progress_run_id(batch_id: str) -> str:
    """Return an attempt-unique progress key without changing the shared batch."""
    raw_job_id = os.getenv("CRAWL_JOB_ID", "").strip()
    try:
        job_id = str(uuid.UUID(raw_job_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("distributed crawler task job id must be a canonical UUID") from exc
    if raw_job_id != job_id:
        raise ValueError("distributed crawler task job id must be a canonical UUID")
    raw_attempt = os.getenv("CRAWL_ATTEMPT_NO", "").strip()
    try:
        attempt_no = int(raw_attempt)
    except ValueError as exc:
        raise ValueError("distributed crawler task attempt number must be positive") from exc
    if attempt_no <= 0 or raw_attempt != str(attempt_no):
        raise ValueError("distributed crawler task attempt number must be positive")
    return f"{batch_id}:{job_id}:{attempt_no}"


def publish_distributed_task_result(batch_id: str, batch_result: dict) -> str:
    """Publish the bounded result consumed by the fenced queue worker."""
    raw_path = os.getenv("CRAWL_TASK_RESULT_PATH", "").strip()
    raw_directory = os.getenv("CRAWL_TASK_RESULT_DIR", "").strip()
    if not raw_path or not raw_directory:
        raise ValueError("distributed crawler task result destination is missing")
    destination = Path(raw_path)
    directory = Path(raw_directory)
    if not destination.is_absolute() or not directory.is_absolute():
        raise ValueError("distributed crawler task result destination must be absolute")
    resolved_directory = directory.resolve(strict=True)
    if directory.is_symlink() or not resolved_directory.is_dir():
        raise ValueError("distributed crawler task result directory is unsafe")
    if destination.parent.resolve(strict=True) != resolved_directory or destination.exists():
        raise ValueError("distributed crawler task result path is unsafe or already exists")

    identity: dict[str, object] = {}
    for field, environment_name in (
        ("job_id", "CRAWL_JOB_ID"),
        ("lease_token", "CRAWL_LEASE_TOKEN"),
    ):
        raw = os.getenv(environment_name, "").strip()
        try:
            canonical = str(uuid.UUID(raw))
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"{environment_name} must be a canonical UUID") from exc
        if raw != canonical:
            raise ValueError(f"{environment_name} must be a canonical UUID")
        identity[field] = canonical
    for field, environment_name in (
        ("lease_epoch", "CRAWL_LEASE_EPOCH"),
        ("attempt_no", "CRAWL_ATTEMPT_NO"),
    ):
        raw = os.getenv(environment_name, "").strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{environment_name} must be a positive integer") from exc
        if value <= 0 or raw != str(value):
            raise ValueError(f"{environment_name} must be a positive integer")
        identity[field] = value

    payload = {
        "schema_version": 1,
        **identity,
        "batch_id": batch_id,
        "result": batch_result,
    }
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    if len(encoded) > MAX_DISTRIBUTED_TASK_RESULT_BYTES:
        raise ValueError("distributed crawler task result exceeds the size limit")
    temporary = resolved_directory / f".{destination.name}.{uuid.uuid4().hex}.new"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        if os.name == "posix":
            directory_fd = os.open(resolved_directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return str(destination)


def validate_database_schema() -> None:
    required: dict[str, set[str]] = {table: set(columns) for table, columns in REQUIRED_DB_COLUMNS.items()}
    if staging_enabled():
        for table, columns in STAGING_REQUIRED_DB_COLUMNS.items():
            required.setdefault(table, set()).update(columns)

    tables = sorted(required)
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN %s
                """,
                (tuple(tables),),
            )
            existing_tables = {row["table_name"] for row in cursor.fetchall()}

            cursor.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN %s
                """,
                (tuple(tables),),
            )
            columns_by_table: dict[str, set[str]] = {}
            for row in cursor.fetchall():
                columns_by_table.setdefault(row["table_name"], set()).add(row["column_name"])
    except Exception as exc:
        config = {key: value for key, value in get_db_config().items() if key != "password"}
        logger.critical("Crawler DB schema preflight failed to connect. config=%s error=%s", config, exc)
        raise SystemExit(78) from exc

    missing_tables = [table for table in tables if table not in existing_tables]
    missing_columns = {
        table: sorted(columns - columns_by_table.get(table, set()))
        for table, columns in required.items()
        if table in existing_tables and columns - columns_by_table.get(table, set())
    }
    if missing_tables or missing_columns:
        config = {key: value for key, value in get_db_config().items() if key != "password"}
        logger.critical(
            "Crawler DB schema preflight failed. config=%s missing_tables=%s missing_columns=%s",
            config,
            missing_tables,
            missing_columns,
        )
        raise SystemExit(78)

    config = {key: value for key, value in get_db_config().items() if key != "password"}
    logger.info("Crawler DB schema preflight passed. config=%s staging=%s", config, staging_enabled())


def make_crawl_batch_id(cycle: int) -> str:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    host = socket.gethostname().split(".")[0]
    return f"{host}-{timestamp}-c{cycle}-{uuid.uuid4().hex[:8]}"


def build_concrete_provider_results(
    provider_reports: list[dict],
    course_provider_owners: dict[str, str],
) -> list[dict]:
    """Bind child persistence evidence to the deterministic aggregate ownership snapshot."""
    concrete_results: list[dict] = []
    for report in provider_reports:
        scheduled_owner = str(report.get("provider") or "").strip().upper()
        if scheduled_owner not in AGGREGATE_PROVIDER_OWNERS:
            continue
        expected_providers = {
            provider for provider, owner in course_provider_owners.items() if owner == scheduled_owner
        }
        raw_results = report.get("concrete_provider_results")
        if not isinstance(raw_results, list):
            _manifest_failure(report, scheduled_owner, "manifest result list is missing")
            continue

        normalized_manifest_results: list[dict] = []
        for item in raw_results:
            if not isinstance(item, dict):
                normalized_manifest_results = []
                break
            concrete_provider = str(item.get("provider") or "").strip().upper()
            success = item.get("success")
            integer_fields = {
                field: item.get(field)
                for field in (
                    "targets_total",
                    "targets_succeeded",
                    "collected_courses",
                    "saved_courses",
                )
            }
            if (
                not PROVIDER_NAME_PATTERN.fullmatch(concrete_provider)
                or not isinstance(success, bool)
                or any(
                    isinstance(value, bool) or not isinstance(value, int) or value < 0
                    for value in integer_fields.values()
                )
                or integer_fields["targets_total"] <= 0
                or integer_fields["targets_succeeded"] > integer_fields["targets_total"]
                or (success and integer_fields["targets_succeeded"] != integer_fields["targets_total"])
            ):
                normalized_manifest_results = []
                break
            normalized_manifest_results.append(
                {
                    "provider": concrete_provider,
                    "scheduled_owner": scheduled_owner,
                    "success": success,
                    **integer_fields,
                }
            )
        actual_providers = {item["provider"] for item in normalized_manifest_results}
        if (
            not expected_providers
            or len(normalized_manifest_results) != len(raw_results)
            or len(actual_providers) != len(normalized_manifest_results)
            or actual_providers != expected_providers
        ):
            report.pop("concrete_provider_results", None)
            _manifest_failure(
                report,
                scheduled_owner,
                "manifest providers do not match the ownership snapshot",
            )
            continue

        concrete_results.extend(normalized_manifest_results)
    return sorted(
        concrete_results,
        key=lambda item: (item["scheduled_owner"], item["provider"]),
    )


def classify_cycle_outcome(
    *,
    providers_completed: int,
    providers_failed: int,
    concrete_provider_results: list[dict],
    batch_finished: bool,
    maintenance_failed: bool,
) -> str:
    """Classify a cycle without weakening any collection or apply gate.

    A successful top-level provider report or a validated aggregate manifest is
    persistence evidence. A cycle with both proven work and a provider failure
    is partial. Missing evidence, an all-failed cycle, maintenance failure, or
    failure to finalize the batch remains a hard failure.
    """

    if not batch_finished or maintenance_failed:
        return "failed"

    persistence_succeeded = providers_completed > 0 or any(
        result.get("success") is True for result in concrete_provider_results
    )
    collection_failed = providers_failed > 0 or any(
        result.get("success") is False for result in concrete_provider_results
    )
    if collection_failed:
        return "partial_success" if persistence_succeeded else "zero_provider"
    if not persistence_succeeded:
        return "zero_provider"
    return "success"


def exit_code_for_cycle_outcome(outcome: str) -> int:
    if outcome == "success":
        return CRAWLER_SUCCESS_EXIT_CODE
    if outcome == "partial_success":
        return CRAWLER_PARTIAL_SUCCESS_EXIT_CODE
    if outcome == "zero_provider":
        return CRAWLER_ZERO_PROVIDER_EXIT_CODE
    return CRAWLER_FAILED_EXIT_CODE


def progress_status_for_cycle_outcome(outcome: str) -> str:
    if outcome == "success":
        return "completed"
    if outcome == "zero_provider":
        return "failed"
    return outcome


def begin_staging_batch(batch_id: str, providers: list[str]) -> bool:
    if not staging_enabled():
        return True
    try:
        from DB.db_utils import get_db_cursor

        with get_db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_batches
                    (crawl_batch_id, source_host, mode, providers, status, started_at)
                VALUES (%s, %s, 'staging', %s, 'RUNNING', now())
                ON CONFLICT (crawl_batch_id)
                DO UPDATE SET status = 'RUNNING', updated_at = now()
                """,
                (batch_id, socket.gethostname(), providers),
            )
        logger.info("Staging crawl batch started: %s", batch_id)
        return True
    except Exception as exc:
        logger.error("Failed to start staging crawl batch %s: %s", batch_id, exc)
        return False


def build_staging_batch_result(status: str, result: dict, provider_course_counts: dict[str, int]) -> dict:
    """Attach closure evidence that the primary apply worker can verify."""
    enriched = dict(result)
    provider_owners = enriched.get("course_provider_owners")
    if not isinstance(provider_owners, dict) or not provider_owners:
        raise ValueError("course_provider_owners evidence is missing")
    normalized_owners = {
        str(provider or "").strip().upper(): str(owner or "").strip().upper()
        for provider, owner in provider_owners.items()
        if str(provider or "").strip() and str(owner or "").strip()
    }
    if len(normalized_owners) != len(provider_owners):
        raise ValueError("course_provider_owners evidence is malformed")
    unmapped = sorted(set(provider_course_counts) - set(normalized_owners))
    if unmapped:
        raise ValueError("staging rows contain providers without scheduled owner evidence: " + ",".join(unmapped))
    enriched["course_provider_owners"] = dict(sorted(normalized_owners.items()))
    enriched["provider_course_counts"] = {
        str(provider): int(count) for provider, count in provider_course_counts.items()
    }
    completed = int(enriched.get("providers_completed") or 0)
    total = int(enriched.get("providers_total") or 0)
    failed = int(enriched.get("providers_failed") or 0)
    enriched["collection_complete"] = bool(
        status == "COLLECTED"
        and total > 0
        and completed == total
        and failed == 0
        and enriched.get("limit") is None
        and not enriched.get("branch_code")
        and not enriched.get("branch_name")
    )
    return enriched


def finish_staging_batch(batch_id: str, status: str, result: dict) -> bool:
    if not staging_enabled():
        return True
    try:
        from psycopg2.extras import Json
        from DB.db_utils import get_db_cursor

        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT provider, COUNT(*) AS course_count
                FROM courses
                WHERE crawl_batch_id = %s
                GROUP BY provider
                """,
                (batch_id,),
            )
            provider_course_counts = {
                str(row["provider"]): int(row["course_count"] or 0) for row in cursor.fetchall() if row.get("provider")
            }
            enriched_result = build_staging_batch_result(status, result, provider_course_counts)
            cursor.execute(
                """
                UPDATE crawl_batches
                SET status = %s,
                    finished_at = now(),
                    total_branches = (
                        SELECT COUNT(*) FROM branches WHERE crawl_batch_id = %s
                    ),
                    total_courses = (
                        SELECT COUNT(*) FROM courses WHERE crawl_batch_id = %s
                    ),
                    valid_courses = (
                        SELECT COUNT(*)
                        FROM courses
                        WHERE crawl_batch_id = %s
                          AND provider IS NOT NULL
                          AND provider_course_id IS NOT NULL
                          AND title IS NOT NULL
                          AND btrim(title) <> ''
                    ),
                    invalid_courses = (
                        SELECT COUNT(*)
                        FROM courses
                        WHERE crawl_batch_id = %s
                          AND (
                              provider IS NULL
                              OR provider_course_id IS NULL
                              OR title IS NULL
                              OR btrim(title) = ''
                          )
                    ),
                    result = %s,
                    updated_at = now()
                WHERE crawl_batch_id = %s
                """,
                (status, batch_id, batch_id, batch_id, batch_id, Json(enriched_result), batch_id),
            )
        logger.info("Staging crawl batch finished: %s status=%s", batch_id, status)
        return True
    except Exception as exc:
        logger.error("Failed to finish staging crawl batch %s: %s", batch_id, exc)
        return False


def run_worker(
    providers: list[str],
    limit: Optional[int],
    run_interval: float,
    active_start: datetime_time,
    active_end: datetime_time,
    active_check_interval: float,
    enforce_active_window: bool,
    parallel: bool,
    max_workers: int,
    provider_timeout: float | None,
    once: bool,
    max_cycles: Optional[int],
    coordinate_backfill: bool,
    coordinate_backfill_limit: Optional[int],
    coordinate_backfill_delay: float,
    category_backfill: bool,
    branch_code: str | None = None,
    branch_name: str | None = None,
    ignore_worker_lock: bool = False,
) -> int:
    distributed_batch_id = distributed_task_batch_id()
    if not providers or len(providers) != len(set(providers)):
        raise ValueError("providers must be non-empty and unique")
    if any(provider not in PROVIDER_ADAPTERS for provider in providers):
        raise ValueError("providers contains an unregistered crawler")
    if not 1 <= max_workers <= min(MAX_PARALLEL_WORKERS, len(providers)):
        raise ValueError("max_workers is outside the safe provider bound")
    if provider_timeout is not None and not 30 <= provider_timeout <= MAX_PROVIDER_TIMEOUT_SECONDS:
        raise ValueError("provider_timeout is outside the safe bound")
    if staging_enabled() and ignore_worker_lock:
        raise ValueError("ignore_worker_lock is forbidden in staging write mode")
    if distributed_batch_id and (not once or len(providers) != 1):
        raise ValueError("a distributed crawler task must be a one-shot single-provider run")
    lock_status = WORKER_LOCK_ACQUIRED if ignore_worker_lock else ""
    if ignore_worker_lock:
        logger.warning("Crawler worker lock is ignored for this manual run.")
    else:
        lock_status = acquire_worker_lock()

    if lock_status == WORKER_LOCK_CONTENDED:
        return CRAWLER_LOCK_CONTENTION_EXIT_CODE
    if lock_status != WORKER_LOCK_ACQUIRED:
        return CRAWLER_FAILED_EXIT_CODE
    lock_acquired = not ignore_worker_lock

    cycle = 0
    final_exit_code = CRAWLER_SUCCESS_EXIT_CODE
    logger.info(
        "Crawler worker started. providers=%s limit=%s branch_code=%s run_interval=%ss active_window=%s-%s enforce=%s parallel=%s max_workers=%s",
        ",".join(providers),
        limit,
        branch_code or "",
        run_interval,
        active_start.strftime("%H:%M"),
        active_end.strftime("%H:%M"),
        enforce_active_window,
        parallel,
        max_workers,
    )

    current_progress: Optional[dict] = None
    try:
        while RUNNING:
            if enforce_active_window and not is_within_active_window(datetime.now().time(), active_start, active_end):
                next_window_seconds = seconds_until_active_window(datetime.now(), active_start, active_end)
                wait_seconds = min(
                    active_check_interval,
                    next_window_seconds,
                )
                current_progress = init_progress_state(
                    cycle=cycle + 1,
                    providers=providers,
                    limit=limit,
                    run_interval=run_interval,
                    parallel=parallel,
                    max_workers=max_workers,
                    status="sleeping",
                )
                current_progress["next_run_at"] = iso_after(next_window_seconds)
                write_progress(current_progress)
                logger.info(
                    "Outside crawler active window (%s-%s). Sleeping for %ss.",
                    active_start.strftime("%H:%M"),
                    active_end.strftime("%H:%M"),
                    wait_seconds,
                )
                if once:
                    logger.info("One-shot crawler run skipped because it is outside the active window.")
                    final_exit_code = CRAWLER_ZERO_PROVIDER_EXIT_CODE
                    try:
                        write_cycle_state(
                            build_cycle_state(
                                crawl_batch_id=str(current_progress.get("run_id") or ""),
                                cycle=cycle + 1,
                                started_at=str(current_progress.get("started_at") or now_iso()),
                                finished_at=now_iso(),
                                final_outcome="zero_provider",
                                exit_code=final_exit_code,
                                providers_requested=len(providers),
                                providers_failed=len(providers),
                                batch_finished=False,
                                failure_stage="active_window",
                            )
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to persist skipped crawler cycle evidence. error_type=%s",
                            type(exc).__name__,
                        )
                        final_exit_code = CRAWLER_FAILED_EXIT_CODE
                    finish_progress_cycle(current_progress, "failed")
                    break
                interruptible_sleep(wait_seconds)
                continue

            cycle += 1
            crawl_batch_id = distributed_batch_id or make_crawl_batch_id(cycle)
            os.environ["CRAWL_BATCH_ID"] = crawl_batch_id
            progress_run_id = (
                distributed_progress_run_id(crawl_batch_id)
                if distributed_batch_id
                else crawl_batch_id
            )
            current_progress = init_progress_state(
                cycle=cycle,
                providers=providers,
                limit=limit,
                run_interval=run_interval,
                parallel=parallel,
                max_workers=max_workers,
                run_id=progress_run_id,
            )
            cycle_started_at = current_progress["started_at"]
            try:
                write_cycle_state(
                    build_cycle_state(
                        crawl_batch_id=crawl_batch_id,
                        cycle=cycle,
                        started_at=cycle_started_at,
                        finished_at="",
                        final_outcome="running",
                        exit_code=None,
                        providers_requested=len(providers),
                    )
                )
            except Exception as exc:
                logger.error(
                    "Failed to persist crawler cycle start evidence. error_type=%s",
                    type(exc).__name__,
                )
                final_exit_code = CRAWLER_FAILED_EXIT_CODE
                finish_progress_cycle(current_progress, "failed")
                break
            try:
                course_provider_owners = (
                    distributed_course_provider_owners(providers)
                    if distributed_batch_id
                    else build_course_provider_owners(providers)
                )
            except Exception as exc:
                logger.error(
                    "Failed to build crawler provider ownership snapshot. error_type=%s error=%s",
                    type(exc).__name__,
                    _safe_error_text(exc),
                )
                final_exit_code = CRAWLER_FAILED_EXIT_CODE
                try:
                    write_cycle_state(
                        build_cycle_state(
                            crawl_batch_id=crawl_batch_id,
                            cycle=cycle,
                            started_at=cycle_started_at,
                            finished_at=now_iso(),
                            final_outcome="failed",
                            exit_code=final_exit_code,
                            providers_requested=len(providers),
                            providers_failed=len(providers),
                            batch_finished=False,
                            failure_stage="provider_ownership",
                        )
                    )
                except Exception as state_exc:
                    logger.error(
                        "Failed to persist crawler ownership failure evidence. error_type=%s",
                        type(state_exc).__name__,
                    )
                finish_progress_cycle(current_progress, "failed")
                break
            if not distributed_batch_id and not begin_staging_batch(crawl_batch_id, providers):
                final_exit_code = CRAWLER_FAILED_EXIT_CODE
                try:
                    write_cycle_state(
                        build_cycle_state(
                            crawl_batch_id=crawl_batch_id,
                            cycle=cycle,
                            started_at=cycle_started_at,
                            finished_at=now_iso(),
                            final_outcome="failed",
                            exit_code=final_exit_code,
                            providers_requested=len(providers),
                            providers_failed=len(providers),
                            batch_finished=False,
                            failure_stage="staging_batch_start",
                        )
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to persist crawler batch-start failure evidence. error_type=%s",
                        type(exc).__name__,
                    )
                finish_progress_cycle(current_progress, "failed")
                break
            provider_reports = run_cycle(
                providers,
                limit,
                active_start,
                active_end,
                enforce_active_window,
                parallel,
                max_workers,
                provider_timeout,
                branch_code=branch_code,
                branch_name=branch_name,
                progress_state=current_progress,
            )
            concrete_provider_results = build_concrete_provider_results(
                provider_reports,
                course_provider_owners,
            )
            concrete_providers_completed = sum(1 for result in concrete_provider_results if result["success"])
            concrete_providers_failed = len(concrete_provider_results) - concrete_providers_completed
            completed = sum(1 for report in provider_reports if report.get("success"))
            failed = len(providers) - completed
            maintenance_results = (
                {
                    "coordinate_backfill": {"requested": False, "success": None},
                    "category_backfill": {"requested": False, "success": None},
                    "ended_course_cleanup": {"requested": False, "success": None},
                }
                if distributed_batch_id
                else
                run_cycle_maintenance(
                    coordinate_backfill=coordinate_backfill,
                    coordinate_backfill_limit=coordinate_backfill_limit,
                    coordinate_backfill_delay=coordinate_backfill_delay,
                    category_backfill=category_backfill,
                )
                if RUNNING
                else {
                    "coordinate_backfill": {"requested": coordinate_backfill, "success": None},
                    "category_backfill": {"requested": category_backfill, "success": None},
                    "ended_course_cleanup": {"requested": True, "success": None},
                }
            )
            maintenance_failed = any(
                result.get("requested") and result.get("success") is False for result in maintenance_results.values()
            )
            collection_outcome = classify_cycle_outcome(
                providers_completed=completed,
                providers_failed=failed,
                concrete_provider_results=concrete_provider_results,
                batch_finished=True,
                maintenance_failed=maintenance_failed,
            )
            cycle_report = {
                "cycle": cycle,
                "crawl_batch_id": crawl_batch_id,
                "started_at": cycle_started_at,
                "finished_at": now_iso(),
                "providers_requested": providers,
                "parallel": parallel,
                "max_workers": max_workers,
                "limit": limit,
                "providers": provider_reports,
                "maintenance": maintenance_results,
                "collection_outcome": collection_outcome,
                "batch_finished": None,
                "final_outcome": "pending_batch_finalize",
            }
            report_path = write_cycle_report(cycle_report)
            logger.info(
                "Crawler cycle %s completed. batch=%s providers_completed=%s/%s report=%s",
                cycle,
                crawl_batch_id,
                completed,
                len(providers),
                report_path,
            )
            batch_result = {
                    "report_path": report_path,
                    "providers_requested": providers,
                    "provider_results": [
                        {
                            "provider": report.get("provider"),
                            "success": bool(report.get("success")),
                            "exit_code": report.get("exit_code"),
                            "collected_courses": int(report.get("total") or 0),
                            "limit": report.get("limit"),
                        }
                        for report in provider_reports
                    ],
                    "concrete_provider_results": concrete_provider_results,
                    "concrete_providers_completed": concrete_providers_completed,
                    "concrete_providers_failed": concrete_providers_failed,
                    "concrete_providers_total": len(concrete_provider_results),
                    "collection_outcome": collection_outcome,
                    "providers_completed": completed,
                    "providers_failed": failed,
                    "providers_total": len(providers),
                    "failed_providers": [
                        str(report.get("provider")) for report in provider_reports if not report.get("success")
                    ],
                    "course_provider_owners": course_provider_owners,
                    "limit": limit,
                    "branch_code": branch_code,
                    "branch_name": branch_name,
                    "close_missing_enabled": failed == 0
                    and close_missing_is_safe(providers, limit, branch_code, branch_name),
                }
            if distributed_batch_id:
                publish_distributed_task_result(distributed_batch_id, batch_result)
            batch_finished = (
                True
                if distributed_batch_id
                else finish_staging_batch(
                    crawl_batch_id,
                    "COLLECTED" if failed == 0 else "FAILED",
                    batch_result,
                )
            )
            cycle_outcome = classify_cycle_outcome(
                providers_completed=completed,
                providers_failed=failed,
                concrete_provider_results=concrete_provider_results,
                batch_finished=batch_finished,
                maintenance_failed=maintenance_failed,
            )
            cycle_report["batch_finished"] = batch_finished
            cycle_report["final_outcome"] = cycle_outcome
            cycle_report["finished_at"] = now_iso()
            replace_cycle_report(report_path, cycle_report)
            final_exit_code = exit_code_for_cycle_outcome(cycle_outcome)
            try:
                write_cycle_state(
                    build_cycle_state(
                        crawl_batch_id=crawl_batch_id,
                        cycle=cycle,
                        started_at=cycle_started_at,
                        finished_at=str(cycle_report["finished_at"]),
                        final_outcome=cycle_outcome,
                        exit_code=final_exit_code,
                        providers_requested=len(providers),
                        providers_completed=completed,
                        providers_failed=failed,
                        concrete_providers_completed=concrete_providers_completed,
                        concrete_providers_failed=concrete_providers_failed,
                        batch_finished=batch_finished,
                        maintenance_failed=maintenance_failed,
                        report_path=report_path,
                    )
                )
            except Exception as exc:
                logger.error(
                    "Failed to persist terminal crawler cycle evidence. error_type=%s",
                    type(exc).__name__,
                )
                cycle_outcome = "failed"
                final_exit_code = CRAWLER_FAILED_EXIT_CODE
                cycle_report["final_outcome"] = cycle_outcome
                cycle_report["cycle_evidence_error"] = type(exc).__name__
                replace_cycle_report(report_path, cycle_report)

            if not RUNNING:
                finish_progress_cycle(current_progress, "stopped", latest_report=report_path)
                break
            if once:
                finish_progress_cycle(
                    current_progress,
                    progress_status_for_cycle_outcome(cycle_outcome),
                    latest_report=report_path,
                )
                break
            if max_cycles and cycle >= max_cycles:
                logger.info("Reached max cycles. Stopping worker.")
                finish_progress_cycle(
                    current_progress,
                    progress_status_for_cycle_outcome(cycle_outcome),
                    latest_report=report_path,
                )
                break

            finish_progress_cycle(
                current_progress, "sleeping", next_run_at=iso_after(run_interval), latest_report=report_path
            )
            current_progress["last_cycle_status"] = progress_status_for_cycle_outcome(cycle_outcome)
            write_progress(current_progress)
            interruptible_sleep(run_interval)
    finally:
        if current_progress and current_progress.get("status") == "running":
            finish_progress_cycle(current_progress, "stopped")
        if lock_acquired:
            release_worker_lock()
        logger.info("Crawler worker stopped.")
    return final_exit_code


def bounded_int_argument(name: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"{name} must be between {minimum} and {maximum}")
        return parsed

    return parse


def bounded_float_argument(name: str, minimum: float, maximum: float):
    def parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be numeric") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"{name} must be between {minimum:g} and {maximum:g}")
        return parsed

    return parse


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawler worker with an active time window")
    parser.add_argument(
        "--providers",
        nargs="+",
        choices=sorted(PROVIDER_ADAPTERS),
        default=[
            "HOMEPLUS",
            "EMART",
            "LOTTE",
            "EXPERIENCE_TARGETS",
            "MUNICIPAL_RESERVATION_TARGETS",
        ],
        help="Crawler providers to run in order",
    )
    parser.add_argument(
        "--limit",
        type=bounded_int_argument("limit", 1, 100_000),
        default=None,
        help="Optional per-provider course limit",
    )
    parser.add_argument(
        "--run-interval",
        type=bounded_float_argument("run-interval", 1.0, 604_800.0),
        default=86400.0,
        help="Delay between full crawler cycles",
    )
    parser.add_argument(
        "--active-start", type=parse_clock, default=parse_clock("22:00"), help="Active window start, HH:MM"
    )
    parser.add_argument("--active-end", type=parse_clock, default=parse_clock("07:00"), help="Active window end, HH:MM")
    parser.add_argument(
        "--active-check-interval",
        type=bounded_float_argument("active-check-interval", 1.0, 3_600.0),
        default=1800.0,
        help="Sleep interval while outside the active window, in seconds",
    )
    parser.add_argument(
        "--ignore-active-window", action="store_true", help="Run immediately regardless of active window"
    )
    parser.add_argument(
        "--ignore-worker-lock", action="store_true", help="Run even when the long-running crawler worker is active"
    )
    parser.add_argument(
        "--branch-code", help="Optional provider branch code for crawlers that support branch filtering"
    )
    parser.add_argument(
        "--branch-name", help="Optional provider branch name for crawlers that support branch filtering"
    )
    parser.add_argument("--parallel", action="store_true", help="Run provider crawlers in parallel")
    parser.add_argument(
        "--max-workers",
        type=bounded_int_argument("max-workers", 1, MAX_PARALLEL_WORKERS),
        default=2,
        help="Maximum parallel crawler processes",
    )
    parser.add_argument(
        "--provider-timeout",
        type=bounded_float_argument("provider-timeout", 30.0, MAX_PROVIDER_TIMEOUT_SECONDS),
        default=None,
        help=(
            "Maximum runtime for each provider process in seconds; when omitted, "
            "reviewed provider-specific defaults are used"
        ),
    )
    parser.add_argument("--once", action="store_true", help="Run one crawler cycle and exit")
    parser.add_argument(
        "--max-cycles",
        type=bounded_int_argument("max-cycles", 1, 100_000),
        default=None,
        help="Optional cycle limit",
    )
    parser.add_argument(
        "--skip-coordinate-backfill",
        action="store_true",
        help="Do not run branch coordinate backfill after crawler cycles",
    )
    parser.add_argument(
        "--coordinate-backfill-limit",
        type=bounded_int_argument("coordinate-backfill-limit", 1, 1_000_000),
        default=None,
        help="Optional maximum number of missing branch coordinates to backfill after each cycle",
    )
    parser.add_argument(
        "--coordinate-backfill-delay",
        type=bounded_float_argument("coordinate-backfill-delay", 0.0, 60.0),
        default=0.5,
        help="Delay between geocoding requests during branch coordinate backfill",
    )
    parser.add_argument(
        "--skip-category-backfill",
        action="store_true",
        help="Do not run course collection category backfill after crawler cycles",
    )
    parser.add_argument(
        "--skip-db-schema-check",
        action="store_true",
        help="Skip startup DB schema preflight. Intended only for local diagnostics.",
    )
    args = parser.parse_args(argv)
    if len(args.providers) != len(set(args.providers)):
        parser.error("--providers must not contain duplicates")
    if len(args.providers) > MAX_PROVIDERS_PER_RUN:
        parser.error(f"--providers accepts at most {MAX_PROVIDERS_PER_RUN} values")
    if (args.branch_code or args.branch_name) and any(
        provider not in BRANCH_FILTER_PROVIDERS for provider in args.providers
    ):
        parser.error("branch filters require every selected provider to support branch filtering")
    try:
        args.branch_code = _bounded_cli_text(args.branch_code, "branch_code")
        args.branch_name = _bounded_cli_text(args.branch_name, "branch_name")
    except ValueError as exc:
        parser.error(str(exc))
    if staging_enabled() and args.ignore_worker_lock:
        parser.error("--ignore-worker-lock is forbidden in staging write mode")
    if args.skip_db_schema_check and os.getenv("ENVIRONMENT", "").strip().lower() in {"prod", "production"}:
        parser.error("--skip-db-schema-check is forbidden in production")
    args.max_workers = min(args.max_workers, len(args.providers))
    return args


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)

    args = parse_args()
    if not args.skip_db_schema_check:
        validate_database_schema()
    raise SystemExit(
        run_worker(
            providers=args.providers,
            limit=args.limit,
            run_interval=args.run_interval,
            active_start=args.active_start,
            active_end=args.active_end,
            active_check_interval=args.active_check_interval,
            enforce_active_window=not args.ignore_active_window,
            parallel=args.parallel,
            max_workers=args.max_workers,
            provider_timeout=args.provider_timeout,
            once=args.once,
            max_cycles=args.max_cycles,
            coordinate_backfill=not args.skip_coordinate_backfill,
            coordinate_backfill_limit=args.coordinate_backfill_limit,
            coordinate_backfill_delay=args.coordinate_backfill_delay,
            category_backfill=not args.skip_category_backfill,
            branch_code=args.branch_code,
            branch_name=args.branch_name,
            ignore_worker_lock=args.ignore_worker_lock,
        )
    )
