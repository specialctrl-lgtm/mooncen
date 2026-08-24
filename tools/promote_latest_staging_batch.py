from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.apply_staging_batch import (
    connect,
    db_config,
    latest_batch_id,
    successful_apply_result,
)
from tools.run_pinned_staging_dry_run import BATCH_ID_PATTERN, create_pinned_dry_run
from tools.validate_staging_activation_result import (
    ActivationResultError,
    load_result,
    validate_apply_result,
    validate_dry_run_result,
)


def _safe_runtime_directory(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise ActivationResultError("staging promotion runtime directory is unavailable") from exc
    if not stat.S_ISDIR(details.st_mode) or path.is_symlink():
        raise ActivationResultError("staging promotion runtime path is not a regular directory")
    if os.name == "posix" and (
        details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) & 0o077
    ):
        raise ActivationResultError("staging promotion runtime directory ownership or mode is unsafe")


def _write_apply_result(
    *,
    batch_id: str,
    fingerprint: str,
    result_file: Path,
    run_func: Callable[..., subprocess.CompletedProcess[bytes]],
) -> dict[str, Any]:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".apply-{batch_id}-",
        suffix=".json.tmp",
        dir=result_file.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            completed = run_func(
                [
                    sys.executable,
                    "-I",
                    "-X",
                    "utf8",
                    str(Path(__file__).resolve().with_name("apply_staging_batch.py")),
                    "--batch-id",
                    batch_id,
                    "--require-latest-batch",
                    "--expected-staging-fingerprint",
                    fingerprint,
                ],
                stdout=output,
                check=False,
            )
            output.flush()
            os.fsync(output.fileno())
        if completed.returncode != 0:
            raise ActivationResultError(
                f"pinned staging apply exited with status {completed.returncode}"
            )
        result = load_result(temporary_path)
        if result.get("status") == "SKIPPED_ALREADY_APPLIED":
            if result.get("batch_id") != batch_id:
                raise ActivationResultError("already-applied result batch id does not match")
            if result.get("staging_fingerprint") != fingerprint:
                raise ActivationResultError("already-applied staging fingerprint does not match")
            if result.get("successful_apply_fingerprint") != fingerprint:
                raise ActivationResultError("recorded successful apply fingerprint does not match")
        else:
            validate_apply_result(
                result,
                batch_id=batch_id,
                expected_fingerprint=fingerprint,
            )
        os.replace(temporary_path, result_file)
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def promote_latest_batch(
    *,
    runtime_directory: Path,
    connect_func: Callable[[dict[str, Any]], Any] = connect,
    dry_run_func: Callable[..., None] = create_pinned_dry_run,
    run_func: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    """Dry-run and atomically apply the exact latest complete staging snapshot."""
    _safe_runtime_directory(runtime_directory)
    staging_config = db_config(
        "CRAWL_STAGING",
        os.getenv("CRAWL_STAGING_DB_NAME", "mooncen_staging"),
    )
    primary_config = db_config("PRIMARY", os.getenv("PRIMARY_DB_NAME", "mooncen"))
    staging_conn = connect_func(staging_config)
    primary_conn = connect_func(primary_config)
    try:
        staging_conn.set_session(readonly=True, autocommit=False)
        batch_id = str(latest_batch_id(staging_conn) or "").strip()
        if not BATCH_ID_PATTERN.fullmatch(batch_id):
            raise ActivationResultError("latest staging batch id is invalid")
        previous = successful_apply_result(primary_conn, batch_id)
        if previous is not None:
            return {
                "status": "NO_NEW_BATCH",
                "batch_id": batch_id,
                "staging_fingerprint": str(previous.get("staging_fingerprint") or ""),
            }
    finally:
        staging_conn.close()
        primary_conn.close()

    dry_run_file = runtime_directory / f"dry-run-{batch_id}.json"
    apply_file = runtime_directory / f"apply-{batch_id}.json"
    dry_run_file.unlink(missing_ok=True)
    apply_file.unlink(missing_ok=True)
    dry_run_func(batch_id=batch_id, result_file=dry_run_file)
    fingerprint = validate_dry_run_result(load_result(dry_run_file), batch_id=batch_id)
    result = _write_apply_result(
        batch_id=batch_id,
        fingerprint=fingerprint,
        result_file=apply_file,
        run_func=run_func,
    )
    return {
        "status": str(result.get("status") or "SUCCESS"),
        "batch_id": batch_id,
        "staging_fingerprint": fingerprint,
        "dry_run_result": str(dry_run_file),
        "apply_result": str(apply_file),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Promote the latest complete crawler batch through a pinned dry-run",
    )
    parser.add_argument(
        "--runtime-directory",
        type=Path,
        default=Path("/run/mooncen-staging-apply"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = promote_latest_batch(runtime_directory=args.runtime_directory)
    except RuntimeError as exc:
        if str(exc).startswith("No ready staging batch"):
            print(json.dumps({"status": "NO_READY_BATCH"}, ensure_ascii=False))
            return 0
        print(f"Pinned staging promotion failed: {exc}", file=sys.stderr)
        return 1
    except (ActivationResultError, OSError) as exc:
        print(f"Pinned staging promotion failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
