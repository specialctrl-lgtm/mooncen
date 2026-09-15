from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any


APP_LINK = Path("/opt/mooncen")
RELEASES_ROOT = Path("/opt/mooncen-releases")
MAX_FILE_BYTES = 1_048_576
MAX_PROVIDERS = 512
EXPECTED_RELEASE_UID = 0


def _safe_text(value: object, limit: int = 500) -> str:
    text = str(value or "").strip()
    return "".join(character for character in text if ord(character) >= 32)[:limit]


def _read_object(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise RuntimeError(f"unsafe crawler status file: {path.name}")
    if metadata.st_size < 2 or metadata.st_size > MAX_FILE_BYTES:
        raise RuntimeError(f"invalid crawler status file size: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid crawler status payload: {path.name}")
    return value


def _provider_rows(progress: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rows = progress.get("providers")
    if not isinstance(raw_rows, list) or len(raw_rows) > MAX_PROVIDERS:
        return []
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        provider = _safe_text(raw.get("provider"), 64).upper()
        state = _safe_text(raw.get("state"), 32).lower()
        if not provider or not state:
            continue
        rows.append(
            {
                "provider": provider,
                "state": state,
                "exit_code": raw.get("exit_code") if isinstance(raw.get("exit_code"), int) else None,
                "error_type": _safe_text(raw.get("error_type"), 100) or None,
                "error_message": _safe_text(raw.get("error_message") or raw.get("error")) or None,
                "started_at": _safe_text(raw.get("started_at"), 80) or None,
                "finished_at": _safe_text(raw.get("finished_at"), 80) or None,
                "total": raw.get("total") if isinstance(raw.get("total"), int) else None,
            }
        )
    return rows


def build_summary() -> dict[str, Any]:
    release = APP_LINK.resolve(strict=True)
    releases_root = RELEASES_ROOT.resolve(strict=True)
    if release.parent != releases_root or not release.is_dir():
        raise RuntimeError("active crawler release is unsafe")
    release_metadata = os.lstat(release)
    if release_metadata.st_uid != EXPECTED_RELEASE_UID or stat.S_IMODE(release_metadata.st_mode) & 0o022:
        raise RuntimeError("active crawler release metadata is unsafe")

    logs = release / "logs"
    progress = _read_object(logs / "crawler_progress.json")
    try:
        cycle = _read_object(logs / "crawler_cycle_state.json")
    except FileNotFoundError:
        cycle = {}
    providers = _provider_rows(progress)
    return {
        "schema_version": 1,
        "status": _safe_text(progress.get("status"), 32) or "unknown",
        "crawl_batch_id": _safe_text(cycle.get("crawl_batch_id") or progress.get("run_id"), 128) or None,
        "started_at": _safe_text(progress.get("started_at"), 80) or None,
        "finished_at": _safe_text(progress.get("finished_at"), 80) or None,
        "updated_at": _safe_text(progress.get("updated_at"), 80) or None,
        "total": len(providers),
        "completed": sum(row["state"] in {"success", "failed", "stopped", "skipped"} for row in providers),
        "success": sum(row["state"] == "success" for row in providers),
        "failed": sum(row["state"] in {"failed", "stopped"} for row in providers),
        "providers": providers,
    }


def main() -> int:
    try:
        print(json.dumps(build_summary(), ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "available": False,
                    "error": f"{type(exc).__name__}: crawler runtime summary is unavailable",
                },
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
