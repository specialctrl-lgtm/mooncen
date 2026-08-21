from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_yaml(path: Path) -> Any:
    text = path.read_text(encoding="utf-8", errors="ignore")
    text = CONTROL_CHARS.sub("", text)
    return yaml.safe_load(text)


def target_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        rows = data.get("targets")
        return rows if isinstance(rows, list) else []
    return data if isinstance(data, list) else []


def write_yaml(path: Path, data: Any) -> None:
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=160),
        encoding="utf-8",
    )


def target_key(row: dict[str, Any]) -> tuple[str, str] | None:
    provider = clean_text(row.get("provider")).upper()
    url = clean_text(row.get("url") or row.get("list_url") or row.get("base_url"))
    if not provider or not url:
        return None
    return provider, url


def is_deleted(row: dict[str, Any]) -> bool:
    return (
        row.get("enabled") is False
        or clean_text(row.get("manual_action")).lower() == "delete"
        or clean_text(row.get("target_status")).lower() in {"deleted", "deprecated"}
        or clean_text(row.get("status")).lower() == "deprecated"
    )


def rank_path(path: Path) -> int:
    path_text = str(path).replace("\\", "/")
    if "config/crawl_targets/" in path_text:
        return 0
    if path.name == "collected_yaml_crawl_targets.yaml":
        return 1
    return 3


def dedupe_files(paths: list[Path], dry_run: bool) -> list[dict[str, Any]]:
    loaded: list[tuple[Path, Any, list[dict[str, Any]]]] = []
    for path in sorted(paths, key=rank_path):
        data = load_yaml(path)
        rows = target_rows(data)
        if rows:
            loaded.append((path, data, rows))

    seen: dict[tuple[str, str], tuple[Path, int]] = {}
    removed: list[dict[str, Any]] = []
    for path, _data, rows in loaded:
        new_rows: list[dict[str, Any]] = []
        changed = False
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                new_rows.append(row)
                continue
            key = target_key(row)
            if not key or is_deleted(row):
                new_rows.append(row)
                continue
            if key in seen:
                keep_path, keep_index = seen[key]
                removed.append(
                    {
                        "provider": key[0],
                        "url": key[1],
                        "removed_file": str(path),
                        "removed_index": index,
                        "kept_file": str(keep_path),
                        "kept_index": keep_index,
                    }
                )
                changed = True
                continue
            seen[key] = (path, len(new_rows))
            new_rows.append(row)

        if changed and not dry_run:
            if isinstance(_data, dict) and isinstance(_data.get("targets"), list):
                _data["targets"] = new_rows
            else:
                _data = new_rows
            write_yaml(path, _data)
    return removed


def default_paths(include_registry: bool = False) -> list[Path]:
    paths = list((ROOT / "config" / "crawl_targets").glob("*.yaml"))
    paths.append(ROOT / "config" / "collected_yaml_crawl_targets.yaml")
    if include_registry:
        paths.append(ROOT / "config" / "generated_yaml_crawler_registry.yaml")
    return [path for path in paths if path.exists()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove duplicate crawler target rows by provider+url.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--include-registry",
        action="store_true",
        help="Also dedupe generated_yaml_crawler_registry.yaml. Default keeps execution registry untouched.",
    )
    args = parser.parse_args()

    removed = dedupe_files(default_paths(args.include_registry), args.dry_run)
    print(f"duplicates_removed={len(removed)} dry_run={args.dry_run}")
    for row in removed[:100]:
        print(
            f"- {row['provider']} {row['url']} "
            f"removed={row['removed_file']} kept={row['kept_file']}"
        )
    if len(removed) > 100:
        print(f"... {len(removed) - 100} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
