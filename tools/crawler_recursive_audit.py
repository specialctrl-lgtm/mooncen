from __future__ import annotations

import argparse
import ast
import json
import os
import py_compile
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_REGISTRIES = (
    "config/crawler_targets.yaml",
    "config/generated_yaml_crawler_registry.yaml",
    "config/collected_yaml_crawl_targets.yaml",
)
LARGE_REGISTRIES = (
    "config/facility_registry_crawl_targets.yaml",
    "config/municipal_course_search_targets.yaml",
    "config/museum_course_search_targets.yaml",
)
REPORT_DIR = ROOT / "logs" / "crawler_audits"
STATE_PATH = REPORT_DIR / "crawler_recursive_state.json"
DEPRECATED_REGISTRY = "config/crawl_targets/deprecated.yaml"
GENERATED_REGISTRY = "config/generated_yaml_crawler_registry.yaml"
GENERATED_WRAPPER_DIR = "Crawler/generated_yaml"
CRAWL_TARGET_DIR = "config/crawl_targets"
AGGREGATE_REGISTRY_RUNNERS = {
    "config/collected_yaml_crawl_targets.yaml": "COLLECTED_YAML",
    "config/municipal_course_search_targets.yaml": "COLLECTED_YAML",
    "config/museum_course_search_targets.yaml": "COLLECTED_YAML",
    "config/facility_registry_crawl_targets.yaml": "FACILITY_REGISTRY",
}
AGGREGATE_RUNNER_ALIASES = set(AGGREGATE_REGISTRY_RUNNERS.values()) | {"YAML_TARGETS_ALL"}
EXCLUDED_REVIEW_URL_PATH_TOKENS = (
    "/news/",
    "/m_news/",
    "selectBbsNttView",
    "selectBbsDetail",
    "selectBoardView",
    "selectNttList",
    "mode=view",
    "articleSeq=",
    "nttId=",
    ".pdf",
    ".hwp",
    ".hwpx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".zip",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def load_yaml(path: Path) -> tuple[Any, str | None]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}, None
    except Exception as exc:  # noqa: BLE001 - report all registry parse failures.
        return None, str(exc)


def iter_target_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []

    for key in ("targets", "sources", "results", "municipalities"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def crawler_path_from_row(row: dict[str, Any]) -> str:
    for key in ("crawler", "crawler_script"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    command = str(row.get("command") or row.get("run_command") or "").strip()
    if command:
        parts = command.replace("\\", "/").split()
        for index, part in enumerate(parts):
            if part.endswith(".py"):
                return part
            if part == "Crawler" and index + 1 < len(parts) and parts[index + 1].endswith(".py"):
                return f"Crawler/{parts[index + 1]}"
    return ""


def normalize_crawler_path(value: str) -> Path | None:
    if not value:
        return None
    text = value.strip().strip('"').strip("'").replace("\\", "/")
    if text.startswith(("/", "//")) or re.match(r"^[A-Za-z]:/", text):
        return None
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or ".." in parts:
        return None
    candidate = (ROOT / Path(*parts)).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return None
    if candidate.suffix.lower() != ".py":
        return None
    return candidate


def discover_crawler_scripts() -> list[Path]:
    crawler_root = ROOT / "Crawler"
    return sorted(
        path
        for path in crawler_root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def is_crawler_entrypoint(path: Path) -> bool:
    """Return whether *path* is intended to be run as a crawler command."""
    if path.name == "__init__.py":
        return False
    if path.parent.resolve() == (ROOT / GENERATED_WRAPPER_DIR).resolve():
        return bool(declared_providers_from_script(path))
    return path.name.startswith("Crawler_")


def safe_provider_module_name(provider: str) -> str:
    name = re.sub(r"[^A-Z0-9_]+", "_", provider.upper()).strip("_")
    if not name:
        raise ValueError("Empty provider name")
    if name[0].isdigit():
        name = f"PROVIDER_{name}"
    return name


def declared_providers_from_script(path: Path) -> set[str]:
    """Read literal PROVIDER/PROVIDERS declarations without importing a crawler."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return set()

    values: set[str] = set()
    for node in tree.body:
        target_names: set[str] = set()
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign):
            target_names = {
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_names = {node.target.id}
            value_node = node.value
        if value_node is None or not target_names.intersection({"PROVIDER", "PROVIDERS"}):
            continue
        try:
            literal = ast.literal_eval(value_node)
        except (ValueError, TypeError, SyntaxError):
            continue
        candidates = literal if isinstance(literal, (list, tuple, set)) else [literal]
        for candidate in candidates:
            provider = str(candidate).strip().upper() if isinstance(candidate, str) else ""
            if provider:
                values.add(provider)
    return values


def audit_generated_wrappers(
    registry_rows: list[dict[str, Any]],
    *,
    include_unexpected: bool,
) -> tuple[set[Path], list[dict[str, Any]]]:
    """Cross-check every generated-registry provider and its runnable wrapper."""
    providers = {
        str(row.get("provider") or "").strip().upper()
        for row in registry_rows
        if row.get("_registry") == GENERATED_REGISTRY and str(row.get("provider") or "").strip()
    }
    if not providers:
        return set(), []

    wrapper_dir = ROOT / GENERATED_WRAPPER_DIR
    expected_by_name: dict[str, set[str]] = defaultdict(set)
    for provider in providers:
        expected_by_name[f"{safe_provider_module_name(provider)}.py"].add(provider)

    issues: list[dict[str, Any]] = []
    expected_paths: set[Path] = set()
    for filename, filename_providers in sorted(expected_by_name.items()):
        path = wrapper_dir / filename
        expected_paths.add(path.resolve())
        if len(filename_providers) > 1:
            issues.append({
                "reason": "generated_wrapper_name_collision",
                "wrapper": rel(path),
                "providers": sorted(filename_providers),
            })
            continue
        provider = next(iter(filename_providers))
        if not path.is_file():
            issues.append({
                "reason": "generated_wrapper_missing",
                "wrapper": rel(path),
                "provider": provider,
            })
            continue
        declared = declared_providers_from_script(path)
        if declared != {provider}:
            issues.append({
                "reason": "generated_wrapper_provider_mismatch",
                "wrapper": rel(path),
                "provider": provider,
                "declared_providers": sorted(declared),
            })

    if include_unexpected and wrapper_dir.is_dir():
        expected_names = set(expected_by_name)
        for path in sorted(wrapper_dir.glob("*.py")):
            declared_providers = declared_providers_from_script(path)
            if path.name != "__init__.py" and path.name not in expected_names and declared_providers:
                issues.append({
                    "reason": "unexpected_generated_wrapper",
                    "wrapper": rel(path),
                    "declared_providers": sorted(declared_providers),
                })
    return expected_paths, issues


def load_run_crawler_providers() -> tuple[dict[str, list[str]], str | None]:
    try:
        import run_crawlers  # type: ignore

        return dict(run_crawlers.PROVIDER_COMMANDS), None
    except Exception as exc:  # noqa: BLE001
        return {}, str(exc)


def load_provider_command_source_overlaps() -> list[dict[str, Any]]:
    try:
        import run_crawlers  # type: ignore
    except Exception:
        return []
    static_commands = getattr(run_crawlers, "STATIC_PROVIDER_COMMANDS", {})
    generated_commands = getattr(run_crawlers, "GENERATED_PROVIDER_COMMANDS", {})
    if not isinstance(static_commands, dict) or not isinstance(generated_commands, dict):
        return []
    return [
        {
            "provider": provider,
            "reason": "provider_command_source_overlap",
            "conflicting": static_commands[provider] != generated_commands[provider],
            "static_command": static_commands[provider],
            "generated_command": generated_commands[provider],
        }
        for provider in sorted(set(static_commands).intersection(generated_commands))
    ]


def command_script_path(command_parts: list[str]) -> Path | None:
    if not command_parts:
        return None
    if command_parts[0].endswith(".py"):
        return normalize_crawler_path(command_parts[0])
    if command_parts[0] == "Crawler" and len(command_parts) > 1:
        script_parts: list[str] = []
        for part in command_parts[1:]:
            script_parts.append(part)
            if part.endswith(".py"):
                return normalize_crawler_path(str(Path("Crawler", *script_parts)))
        return None
    for part in command_parts:
        if part.endswith(".py"):
            return normalize_crawler_path(part)
    return None


def compile_scripts(paths: list[Path]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for path in paths:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append({"script": rel(path), "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            errors.append({"script": rel(path), "error": str(exc)})
    return errors


def read_registries(registry_paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    registry_errors: list[dict[str, Any]] = []

    for path in registry_paths:
        data, error = load_yaml(path)
        if error:
            registry_errors.append({"registry": rel(path), "error": error})
            continue

        target_rows = iter_target_rows(data)
        for index, row in enumerate(target_rows):
            item = dict(row)
            item["_registry"] = rel(path)
            item["_index"] = index
            rows.append(item)

    return rows, registry_errors


def read_crawl_target_metadata() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target_dir = ROOT / CRAWL_TARGET_DIR
    paths = [path for path in sorted(target_dir.glob("*.yaml")) if path.name != "index.yaml"]
    return read_registries(paths)


def load_deprecated_providers() -> dict[str, dict[str, Any]]:
    path = ROOT / DEPRECATED_REGISTRY
    data, error = load_yaml(path)
    if error:
        return {}
    providers: dict[str, dict[str, Any]] = {}
    for row in iter_target_rows(data):
        provider = str(row.get("provider") or "").strip().upper()
        if provider:
            providers[provider] = {
                "provider": provider,
                "registry": rel(path),
                "crawler_status": row.get("crawler_status") or row.get("status"),
                "deprecated_reason": row.get("deprecated_reason") or row.get("manual_action") or "",
                "url": row.get("url") or row.get("list_url") or row.get("base_url") or "",
            }
    return providers


def provider_status(row: dict[str, Any]) -> str:
    if row.get("enabled") is False:
        return str(
            row.get("disabled_reason")
            or row.get("target_status")
            or row.get("crawler_status")
            or row.get("status")
            or row.get("state")
            or ""
        ).strip()
    return str(
        row.get("crawler_status")
        or row.get("target_status")
        or row.get("status")
        or row.get("state")
        or ""
    ).strip()


def is_enabled(row: dict[str, Any]) -> bool:
    if row.get("enabled") is False:
        return False
    if str(row.get("status") or "").lower() in {"rejected", "paused", "disabled"}:
        return False
    return True


def row_url(row: dict[str, Any]) -> str:
    return str(row.get("url") or row.get("list_url") or row.get("base_url") or "").strip()


def has_excluded_review_url_shape(row: dict[str, Any]) -> bool:
    url = row_url(row).lower()
    return any(token.lower() in url for token in EXCLUDED_REVIEW_URL_PATH_TOKENS)


def aggregate_runner_for_row(row: dict[str, Any]) -> str:
    return AGGREGATE_REGISTRY_RUNNERS.get(str(row.get("_registry") or ""), "")


def work_item_key(item: dict[str, Any]) -> str:
    provider = str(
        item.get("provider")
        or item.get("script")
        or item.get("crawler")
        or item.get("wrapper")
        or ""
    ).upper()
    return f"{item.get('reason') or 'unknown'}:{provider}"


def summarize_work_queue_state(work_items: list[dict[str, Any]], state_path: Path = STATE_PATH) -> dict[str, Any]:
    if not state_path.exists():
        return {"state_file": rel(state_path), "state_exists": False}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report corrupt state without failing audit.
        return {"state_file": rel(state_path), "state_exists": True, "error": str(exc)}
    records = state.get("items") if isinstance(state, dict) else {}
    if not isinstance(records, dict):
        records = {}
    by_status: Counter[str] = Counter()
    by_next_action: Counter[str] = Counter()
    missing_state = 0
    accepted_statuses = {"passed", "no_current_data"}
    actionable_items: list[dict[str, Any]] = []
    for item in work_items:
        record = records.get(work_item_key(item))
        if not isinstance(record, dict):
            missing_state += 1
            actionable_items.append({
                "provider": (
                    item.get("provider")
                    or item.get("script")
                    or item.get("crawler")
                    or item.get("wrapper")
                    or ""
                ),
                "reason": item.get("reason"),
                "last_status": "missing_state",
            })
            continue
        last_status = str(record.get("last_status") or "unknown")
        by_status[last_status] += 1
        by_next_action[str(record.get("last_next_action") or "inspect")] += 1
        if last_status not in accepted_statuses:
            actionable_items.append({
                "provider": (
                    item.get("provider")
                    or item.get("script")
                    or item.get("crawler")
                    or item.get("wrapper")
                    or ""
                ),
                "reason": item.get("reason"),
                "last_status": last_status,
                "last_next_action": record.get("last_next_action"),
            })
    return {
        "state_file": rel(state_path),
        "state_exists": True,
        "state_items": len(records),
        "work_items_with_state": len(work_items) - missing_state,
        "work_items_missing_state": missing_state,
        "accepted_statuses": sorted(accepted_statuses),
        "accepted_work_items": len(work_items) - len(actionable_items),
        "actionable_work_items": len(actionable_items),
        "actionable_items": actionable_items[:50],
        "by_status": dict(sorted(by_status.items())),
        "by_next_action": dict(sorted(by_next_action.items())),
    }


def build_audit(
    *,
    registry_paths: list[Path],
    compile_check: bool,
    provider_filter: set[str] | None,
) -> dict[str, Any]:
    crawler_scripts = discover_crawler_scripts()
    registry_rows, registry_errors = read_registries(registry_paths)
    metadata_rows, metadata_errors = read_crawl_target_metadata()
    registry_errors.extend(metadata_errors)
    metadata_providers = {
        str(row.get("provider") or "").strip().upper()
        for row in metadata_rows
        if str(row.get("provider") or "").strip()
    }
    all_registry_providers = {
        str(row.get("provider") or "").strip().upper()
        for row in registry_rows
        if str(row.get("provider") or "").strip()
    } | metadata_providers
    deprecated_providers = load_deprecated_providers()
    provider_commands, provider_command_error = load_run_crawler_providers()
    provider_command_source_overlaps = load_provider_command_source_overlaps()
    if provider_filter:
        provider_command_source_overlaps = [
            item for item in provider_command_source_overlaps if item.get("provider") in provider_filter
        ]

    aggregate_filter_registries = {
        registry
        for registry, runner in AGGREGATE_REGISTRY_RUNNERS.items()
        if provider_filter and runner in provider_filter
    }
    if provider_filter:
        registry_rows = [
            row for row in registry_rows
            if str(row.get("provider") or "").strip().upper() in provider_filter
            or str(row.get("_registry") or "") in aggregate_filter_registries
        ]

    provider_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    provider_crawlers: dict[str, list[tuple[str, Path | None, bool]]] = defaultdict(list)
    registry_crawler_paths: set[Path] = set()
    missing_crawler_files: list[dict[str, Any]] = []
    invalid_crawler_paths: list[dict[str, Any]] = []
    registry_without_crawler: list[dict[str, Any]] = []
    command_missing_files: list[dict[str, Any]] = []
    invalid_runner_commands: list[dict[str, Any]] = []
    provider_not_in_runner: list[dict[str, Any]] = []
    runner_without_registry: list[str] = []
    aggregate_covered_providers: set[str] = set()
    aggregate_runner_registries: dict[str, set[str]] = defaultdict(set)
    aggregate_runner_provider_counts: Counter[str] = Counter()
    deprecated_registry_stale: list[dict[str, Any]] = []
    status_counter: Counter[str] = Counter()
    registry_counter: Counter[str] = Counter()

    expected_generated_wrappers, generated_wrapper_issues = audit_generated_wrappers(
        registry_rows,
        include_unexpected=provider_filter is None,
    )
    registry_crawler_paths.update(expected_generated_wrappers)

    for row in registry_rows:
        provider = str(row.get("provider") or "").strip().upper()
        if not provider:
            continue

        provider_rows[provider].append(row)
        registry_counter[str(row.get("_registry") or "")] += 1
        status_counter[provider_status(row) or "unknown"] += 1

        crawler_text = crawler_path_from_row(row)
        crawler_path = normalize_crawler_path(crawler_text)
        if crawler_path:
            provider_crawlers[provider].append((crawler_text, crawler_path, crawler_path.exists()))
            registry_crawler_paths.add(crawler_path.resolve())
            if not crawler_path.exists():
                missing_crawler_files.append({
                    "provider": provider,
                    "registry": row.get("_registry"),
                    "crawler": crawler_text,
                    "status": provider_status(row),
                })
        elif crawler_text:
            invalid_crawler_paths.append({
                "provider": provider,
                "registry": row.get("_registry"),
                "crawler": crawler_text,
                "status": provider_status(row),
            })
        else:
            provider_crawlers[provider]

    for row in registry_rows:
        provider = str(row.get("provider") or "").strip().upper()
        if not provider:
            continue

        runner_script = command_script_path(provider_commands.get(provider, [])) if provider in provider_commands else None
        provider_has_runner_script = bool(runner_script and runner_script.exists())
        aggregate_runner = aggregate_runner_for_row(row)
        aggregate_script = (
            command_script_path(provider_commands.get(aggregate_runner, []))
            if aggregate_runner in provider_commands
            else None
        )
        provider_has_aggregate_runner = bool(aggregate_script and aggregate_script.exists())
        provider_has_crawler = (
            any(text for text, _path, _exists in provider_crawlers.get(provider, []))
            or provider_has_runner_script
            or provider_has_aggregate_runner
        )
        if is_enabled(row) and provider_has_aggregate_runner:
            aggregate_covered_providers.add(provider)
            aggregate_runner_registries[aggregate_runner].add(str(row.get("_registry") or ""))
            aggregate_runner_provider_counts[aggregate_runner] += 1
        if is_enabled(row) and not crawler_path_from_row(row) and not provider_has_crawler:
            registry_without_crawler.append({
                "provider": provider,
                "registry": row.get("_registry"),
                "status": provider_status(row),
                "name": row.get("name") or row.get("branch") or "",
            })

        if is_enabled(row) and provider not in provider_commands and not provider_has_aggregate_runner:
            crawler_text = crawler_path_from_row(row)
            if not crawler_text:
                crawler_text = next((text for text, _path, _exists in provider_crawlers.get(provider, []) if text), "")
            provider_not_in_runner.append({
                "provider": provider,
                "registry": row.get("_registry"),
                "status": provider_status(row),
                "crawler": crawler_text,
            })

    for provider, command in provider_commands.items():
        if provider_filter and provider not in provider_filter:
            continue
        script_path = command_script_path(command)
        if script_path is None and any(str(part).endswith(".py") for part in command):
            invalid_runner_commands.append({
                "provider": provider,
                "command": command,
                "reason": "runner_command_invalid_script_path",
            })
        if script_path:
            registry_crawler_paths.add(script_path.resolve())
        if script_path and not script_path.exists():
            command_missing_files.append({
                "provider": provider,
                "command": command,
                "script": rel(script_path),
            })
        if (
            provider not in provider_rows
            and provider not in metadata_providers
            and provider not in AGGREGATE_RUNNER_ALIASES
        ):
            runner_without_registry.append(provider)

    known_providers = all_registry_providers | set(provider_commands)
    indirectly_registered_scripts: list[dict[str, Any]] = []
    unregistered_scripts: list[str] = []
    for path in crawler_scripts:
        if not is_crawler_entrypoint(path) or path.resolve() in registry_crawler_paths:
            continue
        declared_providers = declared_providers_from_script(path)
        if provider_filter and not declared_providers.intersection(provider_filter):
            continue
        if declared_providers and declared_providers.issubset(known_providers):
            indirectly_registered_scripts.append({
                "script": rel(path),
                "declared_providers": sorted(declared_providers),
            })
            continue
        unregistered_scripts.append(rel(path))

    syntax_errors = compile_scripts(crawler_scripts) if compile_check else []
    syntax_error_by_script = {item["script"]: item for item in syntax_errors}

    work_items = []
    for item in missing_crawler_files:
        work_items.append({**item, "reason": "missing_crawler_file", "priority": 10})
    for item in invalid_crawler_paths:
        work_items.append({**item, "reason": "invalid_crawler_path", "priority": 10})
    for item in command_missing_files:
        work_items.append({**item, "reason": "runner_command_missing_file", "priority": 10})
    for item in invalid_runner_commands:
        work_items.append({**item, "priority": 10})
    for item in provider_command_source_overlaps:
        work_items.append({**item, "priority": 10})
    for item in syntax_errors:
        work_items.append({"reason": "syntax_error", "priority": 9, **item})
    for item in generated_wrapper_issues:
        work_items.append({**item, "priority": 9})
    for script in unregistered_scripts:
        work_items.append({
            "script": script,
            "reason": "unregistered_crawler_script",
            "priority": 8,
        })
    for item in registry_without_crawler:
        work_items.append({**item, "reason": "registry_without_crawler", "priority": 8})
    for item in provider_not_in_runner:
        work_items.append({**item, "reason": "provider_not_in_run_crawlers", "priority": 7})
    for provider in runner_without_registry:
        work_items.append({
            "provider": provider,
            "command": provider_commands.get(provider, []),
            "reason": "runner_without_registry",
            "priority": 7,
        })

    queued_providers = {str(item.get("provider") or "").upper() for item in work_items}
    for provider, rows in provider_rows.items():
        if provider_filter and provider not in provider_filter:
            continue
        deprecated = deprecated_providers.get(provider)
        if not deprecated:
            continue
        representative = rows[0]
        item = {
            "provider": provider,
            "registry": representative.get("_registry"),
            "status": provider_status(representative),
            "crawler": crawler_path_from_row(representative),
            "deprecated_registry": deprecated.get("registry"),
            "deprecated_reason": deprecated.get("deprecated_reason"),
            "reason": "deprecated_registry_stale",
            "priority": 6,
        }
        deprecated_registry_stale.append(item)
        if provider not in queued_providers:
            work_items.append(item)
            queued_providers.add(provider)

    for provider, rows in provider_rows.items():
        if provider_filter and provider not in provider_filter:
            continue
        parser_row = next(
            (
                row
                for row in rows
                if provider_status(row).lower() in {"needs_parser", "parser_needed"}
                or str(row.get("disabled_reason") or "").lower() == "needs_parser"
            ),
            None,
        )
        if parser_row and provider not in queued_providers:
            work_items.append({
                "provider": provider,
                "registry": parser_row.get("_registry"),
                "status": provider_status(parser_row),
                "disabled_reason": parser_row.get("disabled_reason"),
                "crawler": crawler_path_from_row(parser_row),
                "reason": "needs_parser",
                "priority": 5,
            })
            queued_providers.add(provider)

    for provider, rows in provider_rows.items():
        if provider_filter and provider not in provider_filter:
            continue
        review_rows = [
            row
            for row in rows
            if is_enabled(row) and not has_excluded_review_url_shape(row)
        ]
        statuses = {provider_status(row).lower() for row in review_rows}
        if statuses & {"candidate", "discovery", "ready", "implementing", "testing", "partial", "generated"}:
            representative = review_rows[0]
            if provider not in queued_providers:
                work_items.append({
                    "provider": provider,
                    "registry": representative.get("_registry"),
                    "status": provider_status(representative),
                    "crawler": crawler_path_from_row(representative),
                    "reason": "needs_recursive_review",
                    "priority": 4,
                })
                queued_providers.add(provider)

    for runner, registries in sorted(aggregate_runner_registries.items()):
        if provider_filter and runner not in provider_filter:
            continue
        if runner in queued_providers:
            continue
        runner_command = provider_commands.get(runner, [])
        runner_script = command_script_path(runner_command)
        work_items.append({
            "provider": runner,
            "registry": ",".join(sorted(registries)),
            "status": "aggregate",
            "crawler": rel(runner_script) if runner_script else "",
            "reason": "needs_recursive_review",
            "priority": 4,
            "aggregate_runner": True,
            "covered_providers": int(aggregate_runner_provider_counts.get(runner) or 0),
        })
        queued_providers.add(runner)

    work_items.sort(key=lambda item: (-int(item.get("priority") or 0), str(item.get("provider") or item.get("script") or "")))
    work_queue_state = summarize_work_queue_state(work_items)

    return {
        "generated_at": now_iso(),
        "root": str(ROOT),
        "registries": [rel(path) for path in registry_paths],
        "summary": {
            "registry_rows": len(registry_rows),
            "registry_providers": len(provider_rows),
            "crawl_target_metadata_rows": len(metadata_rows),
            "crawl_target_metadata_providers": len(metadata_providers),
            "runner_providers": len(provider_commands),
            "crawler_scripts": len(crawler_scripts),
            "registry_errors": len(registry_errors),
            "provider_command_errors": int(provider_command_error is not None),
            "missing_crawler_files": len(missing_crawler_files),
            "invalid_crawler_paths": len(invalid_crawler_paths),
            "registry_without_crawler": len(registry_without_crawler),
            "provider_not_in_run_crawlers": len(provider_not_in_runner),
            "runner_without_registry": len(runner_without_registry),
            "aggregate_covered_providers": len(aggregate_covered_providers),
            "deprecated_registry_stale": len(deprecated_registry_stale),
            "runner_command_missing_files": len(command_missing_files),
            "invalid_runner_commands": len(invalid_runner_commands),
            "provider_command_source_overlaps": len(provider_command_source_overlaps),
            "syntax_errors": len(syntax_errors),
            "generated_wrapper_issues": len(generated_wrapper_issues),
            "indirectly_registered_scripts": len(indirectly_registered_scripts),
            "unregistered_scripts": len(unregistered_scripts),
            "work_items": len(work_items),
            "actionable_work_items": work_queue_state.get("actionable_work_items"),
        },
        "status_counts": dict(status_counter),
        "work_queue_state": work_queue_state,
        "registry_counts": dict(registry_counter),
        "provider_command_error": provider_command_error,
        "registry_errors": registry_errors,
        "missing_crawler_files": missing_crawler_files,
        "invalid_crawler_paths": invalid_crawler_paths,
        "registry_without_crawler": registry_without_crawler[:200],
        "provider_not_in_run_crawlers": provider_not_in_runner[:200],
        "runner_without_registry": runner_without_registry,
        "aggregate_covered_providers": sorted(aggregate_covered_providers),
        "deprecated_registry_stale": deprecated_registry_stale[:200],
        "runner_command_missing_files": command_missing_files,
        "invalid_runner_commands": invalid_runner_commands,
        "provider_command_source_overlaps": provider_command_source_overlaps,
        "syntax_errors": syntax_errors,
        "generated_wrapper_issues": generated_wrapper_issues,
        "indirectly_registered_scripts": indirectly_registered_scripts,
        "unregistered_scripts": unregistered_scripts[:300],
        "work_queue": work_items[:500],
        "syntax_error_by_script": syntax_error_by_script,
    }


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_reports(audit: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{os.getpid()}"
    json_path = report_dir / f"crawler_recursive_audit_{stamp}.json"
    md_path = report_dir / f"crawler_recursive_audit_{stamp}.md"
    latest_json_path = report_dir / "crawler_recursive_audit_latest.json"
    latest_md_path = report_dir / "crawler_recursive_audit_latest.md"

    json_text = json.dumps(audit, ensure_ascii=False, indent=2)
    atomic_write_text(json_path, json_text)
    atomic_write_text(latest_json_path, json_text)

    lines = [
        "# Crawler Recursive Audit",
        "",
        f"- Generated: {audit['generated_at']}",
        f"- Root: `{audit['root']}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in audit["summary"].items():
        lines.append(f"- {key}: {value}")
    aggregate_covered = audit.get("aggregate_covered_providers", [])
    work_queue_state = audit.get("work_queue_state") or {}
    if work_queue_state.get("state_exists"):
        lines.extend(["", "## Work Queue State", ""])
        lines.append(f"- state_items: {work_queue_state.get('state_items')}")
        lines.append(f"- work_items_with_state: {work_queue_state.get('work_items_with_state')}")
        lines.append(f"- work_items_missing_state: {work_queue_state.get('work_items_missing_state')}")
        lines.append(f"- accepted_work_items: {work_queue_state.get('accepted_work_items')}")
        lines.append(f"- actionable_work_items: {work_queue_state.get('actionable_work_items')}")
        lines.append(f"- by_status: `{work_queue_state.get('by_status')}`")
        actionable = work_queue_state.get("actionable_items") or []
        if actionable:
            lines.extend(["", "### Actionable Items", ""])
            for item in actionable[:20]:
                lines.append(
                    f"- `{item.get('provider')}` - {item.get('reason')} "
                    f"(last_status `{item.get('last_status')}`)"
                )
    if aggregate_covered:
        lines.extend(["", "## Aggregate Coverage", ""])
        for provider in aggregate_covered[:50]:
            lines.append(f"- `{provider}`")
        if len(aggregate_covered) > 50:
            lines.append(f"- ... {len(aggregate_covered) - 50} more")
    lines.extend(["", "## Top Work Queue", ""])
    for index, item in enumerate(audit.get("work_queue", [])[:50], 1):
        label = (
            item.get("provider")
            or item.get("script")
            or item.get("crawler")
            or item.get("wrapper")
            or "unknown"
        )
        lines.append(
            f"{index}. `{label}` - {item.get('reason')} "
            f"(priority {item.get('priority')}, status `{item.get('status', '')}`)"
        )
        if item.get("registry"):
            lines.append(f"   - registry: `{item['registry']}`")
        if item.get("crawler"):
            lines.append(f"   - crawler: `{item['crawler']}`")
        if item.get("error"):
            lines.append(f"   - error: `{str(item['error'])[:240]}`")
    md_text = "\n".join(lines) + "\n"
    atomic_write_text(md_path, md_text)
    atomic_write_text(latest_md_path, md_text)
    return json_path, md_path


def int_between(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"expected {minimum}..{maximum}, got {parsed}")
        return parsed

    return parse


def float_between(minimum: float, maximum: float):
    def parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"expected {minimum}..{maximum}, got {parsed}")
        return parsed

    return parse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit crawler registries and scripts, then emit a recursive work queue."
    )
    parser.add_argument("--registry", action="append", help="Registry YAML path. Can be repeated.")
    parser.add_argument("--include-large-registries", action="store_true", help="Also scan large generated target files.")
    parser.add_argument("--no-compile", action="store_true", help="Skip Python compile checks.")
    parser.add_argument("--provider", action="append", help="Limit audit to one provider. Can be repeated.")
    parser.add_argument("--iterations", type=int_between(1, 100), default=1, help="Repeat scan N times.")
    parser.add_argument(
        "--sleep-seconds",
        type=float_between(0, 3600),
        default=0.0,
        help="Sleep between iterations.",
    )
    parser.add_argument("--fail-on-errors", action="store_true", help="Exit non-zero when blocking errors are found.")
    parser.add_argument("--fail-on-actionable", action="store_true", help="Exit non-zero when audited work queue has unaccepted state.")
    parser.add_argument("--report-dir", default=str(REPORT_DIR), help="Directory for JSON and Markdown reports.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    registry_names = args.registry or list(DEFAULT_REGISTRIES)
    if args.include_large_registries:
        registry_names.extend(LARGE_REGISTRIES)
    registry_paths = [(ROOT / name).resolve() for name in registry_names]
    provider_filter = {provider.strip().upper() for provider in args.provider or [] if provider.strip()} or None

    latest_audit: dict[str, Any] | None = None
    latest_paths: tuple[Path, Path] | None = None
    iterations = args.iterations

    for iteration in range(1, iterations + 1):
        latest_audit = build_audit(
            registry_paths=registry_paths,
            compile_check=not args.no_compile,
            provider_filter=provider_filter,
        )
        latest_audit["iteration"] = iteration
        latest_paths = write_reports(latest_audit, Path(args.report_dir))
        summary = latest_audit["summary"]
        print(
            "iteration={iteration} registry_providers={registry_providers} "
            "scripts={crawler_scripts} syntax_errors={syntax_errors} "
            "missing_files={missing_crawler_files} work_items={work_items} "
            "json={json_path} md={md_path}".format(
                iteration=iteration,
                json_path=latest_paths[0],
                md_path=latest_paths[1],
                **summary,
            )
        )
        if iteration < iterations and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    if not latest_audit:
        return 1

    blocking = (
        latest_audit["summary"]["registry_errors"]
        + latest_audit["summary"]["provider_command_errors"]
        + latest_audit["summary"]["missing_crawler_files"]
        + latest_audit["summary"]["invalid_crawler_paths"]
        + latest_audit["summary"]["registry_without_crawler"]
        + latest_audit["summary"]["provider_not_in_run_crawlers"]
        + latest_audit["summary"]["runner_command_missing_files"]
        + latest_audit["summary"]["invalid_runner_commands"]
        + latest_audit["summary"]["provider_command_source_overlaps"]
        + latest_audit["summary"]["runner_without_registry"]
        + latest_audit["summary"]["syntax_errors"]
        + latest_audit["summary"]["generated_wrapper_issues"]
        + latest_audit["summary"]["unregistered_scripts"]
    )
    if args.fail_on_errors and blocking:
        return 2
    actionable = int((latest_audit.get("work_queue_state") or {}).get("actionable_work_items") or 0)
    if args.fail_on_actionable and (blocking or actionable):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
