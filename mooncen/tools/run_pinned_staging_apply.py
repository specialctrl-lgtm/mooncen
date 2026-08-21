from __future__ import annotations

import argparse
import os
import re
import stat
import sys
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


def execute_pinned_apply(
    *,
    batch_id: str,
    dry_run_result_file: Path,
    exec_func: Callable[[str, list[str]], object] = os.execv,
) -> None:
    """Exec the exact full apply without contaminating its JSON stdout."""
    if not BATCH_ID_PATTERN.fullmatch(batch_id):
        raise ActivationResultError("pinned apply batch id is invalid")
    try:
        dry_run_stat = dry_run_result_file.lstat()
    except OSError as exc:
        raise ActivationResultError("pinned dry-run artifact is unavailable") from exc
    linux_permissions_are_unsafe = os.name == "posix" and (
        dry_run_stat.st_uid != os.geteuid()
        or stat.S_IMODE(dry_run_stat.st_mode) != 0o600
    )
    if not stat.S_ISREG(dry_run_stat.st_mode) or linux_permissions_are_unsafe:
        raise ActivationResultError(
            "pinned dry-run artifact must be service-owned mode 0600"
        )
    dry_run_result = load_result(dry_run_result_file)
    fingerprint = validate_dry_run_result(
        dry_run_result,
        batch_id=batch_id,
    )

    apply_script = Path(__file__).resolve().with_name("apply_staging_batch.py")
    command = [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        str(apply_script),
        "--batch-id",
        batch_id,
        "--require-latest-batch",
        "--expected-staging-fingerprint",
        fingerprint,
        # Activation must produce a fresh, semantically complete SUCCESS. A
        # prior PARTIAL_SUCCESS log must never turn this into a skipped apply.
        "--force",
    ]
    exec_func(sys.executable, command)
    raise OSError("pinned apply exec unexpectedly returned")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply one activation-reviewed staging snapshot",
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--dry-run-result-file", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        execute_pinned_apply(
            batch_id=args.batch_id,
            dry_run_result_file=args.dry_run_result_file,
        )
    except (ActivationResultError, OSError) as exc:
        # StandardOutput belongs exclusively to apply_staging_batch.py's one
        # JSON document. Wrapper diagnostics go to the journal via stderr.
        print(f"Pinned staging apply rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
