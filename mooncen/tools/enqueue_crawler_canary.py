"""Validate or enqueue one release-pinned distributed crawler canary task.

The command deliberately reuses the central scheduler's reviewed manifest,
single-transaction enqueue function, advisory lock, active-provider check, and
least-privileged scheduler database identity.  Validation is the default;
database mutation requires the explicit ``--enqueue`` confirmation gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg2

from ops_agent.crawler_control_scheduler import (
    ControlSchedulerConfig,
    ScheduleResult,
    enqueue_schedule_slot,
    load_config,
    provider_output_allowlists,
)
from tools.preflight_distributed_crawler_control import (
    PreflightError,
    _assert_component_environment_permissions,
    _check_required_paths,
    _connection_config,
    _database_contract,
    _protected_environment,
)


DEFAULT_ENV_FILE = Path("/etc/mooncen/crawler-control-scheduler.env")
CANARY_SLOT_MAX_SKEW = timedelta(minutes=15)
_PROVIDER = re.compile(r"[A-Z][A-Z0-9_]{0,99}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_UTC_SLOT = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z"
)


class CanaryEnqueueError(RuntimeError):
    """Raised when a bounded canary request cannot be proven safe."""


@contextmanager
def exact_runtime_environment(values: Mapping[str, str]) -> Iterator[None]:
    """Temporarily expose only the protected scheduler environment."""

    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(values)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def canonical_provider(value: str) -> str:
    provider = str(value)
    if provider != provider.strip() or not _PROVIDER.fullmatch(provider):
        raise CanaryEnqueueError("provider must be a canonical uppercase provider key")
    return provider


def canonical_artifact_digest(value: str) -> str:
    digest = str(value)
    if digest != digest.strip() or not _SHA256.fullmatch(digest):
        raise CanaryEnqueueError("artifact confirmation must be a lowercase SHA-256 digest")
    return digest


def parse_canary_slot(value: str) -> datetime:
    """Parse a canonical fractional UTC slot reserved for one-off canaries."""

    raw = str(value)
    if not _UTC_SLOT.fullmatch(raw):
        raise CanaryEnqueueError(
            "slot must use canonical UTC form YYYY-MM-DDTHH:MM:SS.ffffffZ"
        )
    try:
        slot = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise CanaryEnqueueError("slot is not a valid UTC timestamp") from exc
    if slot.microsecond == 0:
        raise CanaryEnqueueError(
            "canary slot requires a non-zero fractional second to avoid daily slots"
        )
    return slot


def assert_recent_slot(
    slot: datetime,
    *,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise CanaryEnqueueError("current time must be timezone-aware")
    if abs(current.astimezone(timezone.utc) - slot) > CANARY_SLOT_MAX_SKEW:
        raise CanaryEnqueueError("canary slot must be within 15 minutes of current UTC time")


def selected_canary_config(
    config: ControlSchedulerConfig,
    *,
    provider: str,
    max_retries: int | None = None,
) -> ControlSchedulerConfig:
    selected = canonical_provider(provider)
    if selected not in config.providers:
        raise CanaryEnqueueError(
            "provider is not in the scheduler's expanded reviewed task set"
        )
    retries = config.max_retries if max_retries is None else max_retries
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 20:
        raise CanaryEnqueueError("max retries must be between 0 and 20")
    selected_execution_owners = tuple(
        pair for pair in config.provider_execution_owners if pair[0] == selected
    )
    return replace(
        config,
        providers=(selected,),
        max_retries=retries,
        provider_execution_owners=selected_execution_owners,
    )


def enqueue_canary(
    connection: Any,
    config: ControlSchedulerConfig,
    *,
    slot: datetime,
) -> ScheduleResult:
    """Enqueue exactly one task through the scheduler's atomic DB contract."""

    if len(config.providers) != 1:
        raise CanaryEnqueueError("canary configuration must contain exactly one provider")
    result = enqueue_schedule_slot(connection, config, slot)
    if (
        result.reason != "enqueued"
        or result.batch_id is None
        or result.job_count != 1
    ):
        reason = result.reason or "scheduler_contract_rejected"
        raise CanaryEnqueueError(f"canary was not enqueued: {reason}")
    return result


def review_document(
    config: ControlSchedulerConfig,
    *,
    slot: datetime,
    status: str,
    result: ScheduleResult | None = None,
) -> dict[str, Any]:
    provider = config.providers[0]
    execution_provider = dict(config.provider_execution_owners).get(
        provider,
        provider,
    )
    # selected_canary_config reduces an aggregate owner to one reviewed
    # concrete task; that task's output fence is exactly itself. Direct owners
    # retain the scheduler's reviewed expansion contract.
    allowlist = (
        (provider,)
        if execution_provider != provider
        else provider_output_allowlists(config.providers)[provider]
    )
    document: dict[str, Any] = {
        "status": status,
        "environment": config.environment,
        "provider": provider,
        "execution_provider": execution_provider,
        "allowed_output_providers": list(allowlist),
        "scheduled_slot": slot.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "max_retries": config.max_retries,
        "code_version": config.code_version,
        "artifact_digest": config.artifact_digest,
        "config_revision": config.config_revision,
    }
    if result is not None:
        document.update(batch_id=str(result.batch_id), job_count=result.job_count)
    return document


def _max_retries(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer between 0 and 20") from exc
    if not 0 <= parsed <= 20:
        raise argparse.ArgumentTypeError("must be between 0 and 20")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or enqueue one reviewed distributed crawler canary task",
    )
    parser.add_argument("--provider", required=True)
    parser.add_argument(
        "--slot",
        required=True,
        help="Unique UTC slot in YYYY-MM-DDTHH:MM:SS.ffffffZ form",
    )
    parser.add_argument("--max-retries", type=_max_retries)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--enqueue",
        action="store_true",
        help="Perform the write after all confirmation and control-plane checks",
    )
    parser.add_argument("--confirm-provider")
    parser.add_argument("--confirm-artifact-sha256")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    connection = None
    try:
        slot = parse_canary_slot(args.slot)
        assert_recent_slot(slot)
        provider = canonical_provider(args.provider)
        environment = _protected_environment(args.env_file)
        _assert_component_environment_permissions(args.env_file, "scheduler")
        _check_required_paths("scheduler", environment)
        with exact_runtime_environment(environment):
            scheduler_config = load_config()
        canary_config = selected_canary_config(
            scheduler_config,
            provider=provider,
            max_retries=args.max_retries,
        )

        if not args.enqueue:
            print(
                json.dumps(
                    review_document(canary_config, slot=slot, status="VALIDATED"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        confirmed_provider = canonical_provider(args.confirm_provider or "")
        if confirmed_provider != provider:
            raise CanaryEnqueueError("provider confirmation does not match the request")
        confirmed_digest = canonical_artifact_digest(
            args.confirm_artifact_sha256 or ""
        )
        if confirmed_digest != canary_config.artifact_digest:
            raise CanaryEnqueueError(
                "artifact confirmation does not match the pinned scheduler release"
            )

        database_config = _connection_config("scheduler", environment)
        database_config["application_name"] = "mooncen-crawler-canary-enqueue"
        connection = psycopg2.connect(**database_config)
        _database_contract(
            "scheduler",
            connection,
            database_config["database"],
            environment,
        )
        result = enqueue_canary(connection, canary_config, slot=slot)
        document = review_document(
            canary_config,
            slot=slot,
            status="ENQUEUED",
            result=result,
        )
    except (
        CanaryEnqueueError,
        OSError,
        PreflightError,
        RuntimeError,
        ValueError,
        psycopg2.Error,
    ) as exc:
        print(f"Crawler canary enqueue rejected: {exc}")
        return 1
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
