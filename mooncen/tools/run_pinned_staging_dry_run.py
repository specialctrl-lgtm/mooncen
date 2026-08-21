from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_staging_activation_result import (
    ActivationResultError,
    load_result,
    validate_dry_run_result,
)


BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def create_pinned_dry_run(
    *,
    batch_id: str,
    result_file: Path,
    require_latest_batch: bool = True,
    run_func: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    """Publish one validated JSON artifact owned by the applier service user."""
    if not BATCH_ID_PATTERN.fullmatch(batch_id):
        raise ActivationResultError("pinned dry-run batch id is invalid")
    if result_file.name != f"dry-run-{batch_id}.json":
        raise ActivationResultError("pinned dry-run result path does not match batch")
    result_dir = result_file.parent
    if result_dir.is_symlink() or not result_dir.is_dir():
        raise ActivationResultError("pinned dry-run result directory is unsafe")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".dry-run-{batch_id}-",
        suffix=".json.tmp",
        dir=result_dir,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            command = [
                sys.executable,
                "-I",
                "-X",
                "utf8",
                str(Path(__file__).resolve().with_name("apply_staging_batch.py")),
                "--dry-run",
                "--batch-id",
                batch_id,
            ]
            if require_latest_batch:
                command.append("--require-latest-batch")
            completed = run_func(command, stdout=output, check=False)
            output.flush()
            os.fsync(output.fileno())
        if completed.returncode != 0:
            raise ActivationResultError(
                f"pinned staging dry-run exited with status {completed.returncode}"
            )
        validate_dry_run_result(load_result(temporary_path), batch_id=batch_id)
        os.replace(temporary_path, result_file)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one activation-reviewed staging dry-run artifact",
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument(
        "--allow-held-control-batch",
        action="store_true",
        help="Review an exact held control-plane batch without making it promotion eligible.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        create_pinned_dry_run(
            batch_id=args.batch_id,
            result_file=args.result_file,
            require_latest_batch=not args.allow_held_control_batch,
        )
    except (ActivationResultError, OSError) as exc:
        print(f"Pinned staging dry-run rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
