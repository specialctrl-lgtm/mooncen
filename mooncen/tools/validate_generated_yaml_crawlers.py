from __future__ import annotations

import argparse
import hashlib
import importlib.util
import shlex
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_GeneratedYamlTargets import (  # noqa: E402
    REGISTRY_FILE,
    TARGET_DIR,
    _is_working_target,
    build_registry,
    explicit_duplicate_reason,
    load_registry_targets,
    load_unique_yaml,
    main as crawler_main,
    parse_args as parse_crawler_args,
    safe_module_name,
    target_preference_key,
    target_scope_keys,
    validate_target_row,
)
from tools.generate_registry_crawler_files import (  # noqa: E402
    MANAGED_HEADER,
    MANIFEST_FILE,
    OUTPUT_DIR,
    WRAPPER_TEMPLATE,
    is_managed_wrapper,
    load_manifest,
)


INDEX_FILE = TARGET_DIR / "index.yaml"


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def _counter(values: list[Any]) -> dict[Any, int]:
    return dict(Counter(values))


def _without_generated_at(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != "generated_at"}


def validate_target_documents(report: ValidationReport) -> list[dict[str, Any]]:
    target_files = sorted(path for path in TARGET_DIR.glob("*.yaml") if path.name != INDEX_FILE.name)
    rows: list[dict[str, Any]] = []
    target_ids: dict[str, str] = {}
    providers: set[str] = set()
    expected_file_entries: list[dict[str, Any]] = []
    for path in target_files:
        try:
            document = load_unique_yaml(path) or {}
        except Exception as exc:
            report.error(f"{path.relative_to(ROOT)}: YAML load failed: {exc}")
            continue
        if not isinstance(document, dict) or document.get("version") != 1:
            report.error(f"{path.relative_to(ROOT)}: version must be 1")
            continue
        target_rows = document.get("targets")
        if not isinstance(target_rows, list):
            report.error(f"{path.relative_to(ROOT)}: targets must be a list")
            continue
        defaults = {
            key: document.get(key)
            for key in ("collection_category", "domain_category", "source_group", "operator_type", "service_group")
            if document.get(key)
        }
        for index, raw_row in enumerate(target_rows, start=1):
            label = f"{path.relative_to(ROOT)}:{index}"
            if not isinstance(raw_row, dict):
                report.error(f"{label}: target must be a mapping")
                continue
            row = {
                **defaults,
                **raw_row,
                "_target_file": path.name,
                "_target_index": index,
                "_explicit_service_group": raw_row.get("service_group"),
            }
            try:
                validate_target_row(row, label=label)
            except Exception as exc:
                report.error(str(exc))
                continue
            providers.add(row["provider"])
            target_id = str(row.get("target_id") or "").strip()
            if target_id:
                if target_id in target_ids:
                    report.error(f"{label}: duplicate target_id also used by {target_ids[target_id]}")
                target_ids[target_id] = label
            rows.append(row)
        summary = document.get("summary") or {}
        expected_summary = {
            "targets": len(target_rows),
            "by_status": _counter([str(row.get("crawler_status") or row.get("status") or "") for row in target_rows if isinstance(row, dict)]),
            "by_service_group": _counter([str(row.get("service_group")) for row in target_rows if isinstance(row, dict) and row.get("service_group")]),
            "by_collection_type": _counter([str(row.get("collection_type") or "") for row in target_rows if isinstance(row, dict)]),
        }
        if summary != expected_summary:
            report.error(f"{path.relative_to(ROOT)}: summary does not match target rows")
        expected_file_entries.append(
            {
                "domain_category": document.get("domain_category"),
                "source_group": document.get("source_group"),
                "file": path.name,
                "targets": len(target_rows),
            }
        )

    try:
        index = load_unique_yaml(INDEX_FILE) or {}
    except Exception as exc:
        report.error(f"{INDEX_FILE.relative_to(ROOT)}: YAML load failed: {exc}")
        index = {}
    if not isinstance(index, dict) or index.get("version") != 1:
        report.error(f"{INDEX_FILE.relative_to(ROOT)}: version must be 1")
    else:
        raw_rows = rows
        by_file = Counter(row["_target_file"] for row in rows)
        category_by_file = {
            entry["file"]: entry["domain_category"] for entry in expected_file_entries
        }
        expected_index_summary = {
            "targets": len(rows),
            "by_category": dict(
                Counter(category_by_file[file_name] for file_name, count in by_file.items() for _ in range(count))
            ),
            "by_status": _counter([str(row.get("crawler_status") or row.get("status") or "") for row in raw_rows]),
            "by_collection_type": _counter([str(row.get("collection_type") or "") for row in raw_rows]),
            "by_service_group": _counter(
                [str(row.get("_explicit_service_group")) for row in raw_rows if row.get("_explicit_service_group")]
            ),
            "by_origin": _counter([str(row.get("origin") or "") for row in raw_rows]),
        }
        if index.get("summary") != expected_index_summary:
            report.error(f"{INDEX_FILE.relative_to(ROOT)}: aggregate summary does not match target files")
        if index.get("files") != expected_file_entries:
            report.error(f"{INDEX_FILE.relative_to(ROOT)}: files inventory does not match target files")

    provider_set = {str(row.get("provider")) for row in rows}
    for row in rows:
        duplicate_of = str(row.get("duplicate_of") or "").strip()
        if duplicate_of and duplicate_of not in provider_set:
            report.error(
                f"config/crawl_targets/{row['_target_file']}:{row['_target_index']}: duplicate_of provider does not exist: {duplicate_of}"
            )
    registry_candidates = load_registry_targets()
    seen_scopes: dict[str, str] = {}
    for row in sorted(registry_candidates, key=target_preference_key):
        if not _is_working_target(row):
            continue
        provider = str(row["provider"])
        duplicate_owner = next((seen_scopes[key] for key in target_scope_keys(row) if key in seen_scopes), "")
        if duplicate_owner and not explicit_duplicate_reason(row):
            report.error(f"unannotated executable URL/scope duplicate: {provider} overlaps {duplicate_owner}")
        if not explicit_duplicate_reason(row):
            for key in target_scope_keys(row):
                seen_scopes.setdefault(key, provider)

    module_owners: dict[str, str] = {}
    for provider in sorted(providers):
        module = safe_module_name(provider)
        if module in module_owners and module_owners[module] != provider:
            report.error(f"provider/module collision: {provider} and {module_owners[module]} -> {module}")
        module_owners[module] = provider
    report.stats.update(
        target_files=len(target_files),
        source_targets=len(rows),
        source_providers=len(providers),
        registry_source_targets=len(registry_candidates),
    )
    return rows


def validate_registry(report: ValidationReport) -> list[dict[str, Any]]:
    try:
        registry = load_unique_yaml(REGISTRY_FILE) or {}
    except Exception as exc:
        report.error(f"{REGISTRY_FILE.relative_to(ROOT)}: YAML load failed: {exc}")
        return []
    if not isinstance(registry, dict) or registry.get("version") != 1:
        report.error(f"{REGISTRY_FILE.relative_to(ROOT)}: version must be 1")
        return []
    targets = registry.get("targets")
    if not isinstance(targets, list):
        report.error(f"{REGISTRY_FILE.relative_to(ROOT)}: targets must be a list")
        return []
    expected = build_registry(load_registry_targets())
    if _without_generated_at(registry) != _without_generated_at(expected):
        report.error(f"{REGISTRY_FILE.relative_to(ROOT)}: registry is stale or non-canonical")
    provider_counts = Counter(str(row.get("provider")) for row in targets if isinstance(row, dict))
    duplicate_providers = sorted(provider for provider, count in provider_counts.items() if count != 1)
    if duplicate_providers:
        report.error(f"registry duplicate providers: {duplicate_providers[:20]}")
    crawler_counts = Counter(str(row.get("crawler")) for row in targets if isinstance(row, dict))
    duplicate_crawlers = sorted(crawler for crawler, count in crawler_counts.items() if count != 1)
    if duplicate_crawlers:
        report.error(f"registry duplicate crawler paths: {duplicate_crawlers[:20]}")

    for index, row in enumerate(targets, start=1):
        label = f"{REGISTRY_FILE.relative_to(ROOT)}:{index}"
        if not isinstance(row, dict):
            report.error(f"{label}: row must be a mapping")
            continue
        provider = str(row.get("provider") or "")
        crawler = str(row.get("crawler") or "")
        expected_crawler = f"Crawler/generated_yaml/{safe_module_name(provider)}.py"
        if crawler != expected_crawler:
            report.error(f"{label}: crawler mismatch for {provider}")
            continue
        crawler_path = (ROOT / crawler).resolve()
        if crawler_path.parent != OUTPUT_DIR.resolve() or not crawler_path.is_file():
            report.error(f"{label}: crawler path is missing or escapes generated directory")
        command = str(row.get("command") or "")
        if any(character in command for character in "\r\n\0"):
            report.error(f"{label}: command contains control characters")
            continue
        try:
            command_arguments = shlex.split(command, posix=True)
        except ValueError as exc:
            report.error(f"{label}: command cannot be parsed: {exc}")
            continue
        if command_arguments[:4] != ["python", "-X", "utf8", crawler]:
            report.error(f"{label}: command prefix is not canonical")
            continue
        if any(token in {";", "|", "||", "&&", ">", ">>", "<", "`"} for token in command_arguments):
            report.error(f"{label}: shell control token is not permitted")
            continue
        structured_arguments = row.get("arguments")
        if (
            not isinstance(structured_arguments, list)
            or not all(isinstance(argument, str) and argument for argument in structured_arguments)
            or command_arguments[4:] != structured_arguments
        ):
            report.error(f"{label}: structured arguments are missing or differ from command")
            continue
        if structured_arguments.count("--save-db") != 1 or any(
            argument == "--all" or argument == "--write-registry" or argument.startswith("--provider")
            for argument in structured_arguments
        ):
            report.error(f"{label}: structured arguments violate the generated-runner policy")
            continue
        if structured_arguments.count("--per-target-limit") != 1:
            report.error(f"{label}: structured arguments must declare one persistence limit")
            continue
        try:
            parse_crawler_args(["--provider", provider, *structured_arguments])
        except SystemExit:
            report.error(f"{label}: command arguments are invalid for the common engine")
    report.stats.update(
        registry_providers=len(targets),
        registry_enabled_providers=sum(1 for row in targets if isinstance(row, dict) and row.get("enabled")),
        registry_enabled_targets=sum(int(row.get("enabled_target_count") or 0) for row in targets if isinstance(row, dict)),
    )
    return [row for row in targets if isinstance(row, dict)]


def validate_wrappers(report: ValidationReport, registry_rows: list[dict[str, Any]], *, import_wrappers: bool) -> None:
    providers = {str(row["provider"]) for row in registry_rows}
    expected_names = {f"{safe_module_name(provider)}.py" for provider in providers}
    all_python_paths = sorted(path for path in OUTPUT_DIR.glob("*.py") if path.name != "__init__.py")
    actual_paths = sorted(path for path in all_python_paths if is_managed_wrapper(path))
    infrastructure_paths: list[Path] = []
    for path in all_python_paths:
        content = path.read_text(encoding="utf-8")
        if path in actual_paths:
            continue
        if content.startswith(MANAGED_HEADER) or "from Crawler.Crawler_GeneratedYamlTargets import main" in content:
            report.error(f"{path.relative_to(ROOT)}: malformed or legacy unmanifested generated wrapper")
        else:
            infrastructure_paths.append(path)
    actual_names = {path.name for path in actual_paths}
    missing = sorted(expected_names - actual_names)
    extra = sorted(actual_names - expected_names)
    if missing:
        report.error(f"missing generated wrappers: {missing[:20]}")
    if extra:
        report.error(f"unregistered generated wrappers: {extra[:20]}")
    try:
        manifest = load_manifest(MANIFEST_FILE)
    except Exception as exc:
        report.error(f"{MANIFEST_FILE.relative_to(ROOT)}: manifest load failed: {exc}")
        manifest = {}
    if set(manifest) != providers:
        report.error("generated wrapper manifest providers do not match registry")
    for path in actual_paths:
        provider = path.stem
        expected_content = WRAPPER_TEMPLATE.format(provider=provider)
        content = path.read_text(encoding="utf-8")
        if content != expected_content:
            report.error(f"{path.relative_to(ROOT)}: wrapper differs from the common template")
            continue
        manifest_entry = manifest.get(provider) or {}
        if manifest_entry.get("file") != path.name or manifest_entry.get("sha256") != hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest():
            report.error(f"{path.relative_to(ROOT)}: wrapper hash does not match manifest")
            continue
        try:
            compile(content, str(path), "exec")
        except SyntaxError as exc:
            report.error(f"{path.relative_to(ROOT)}: compile failed: {exc}")
            continue
        if not import_wrappers:
            continue
        module_name = f"_mooncen_generated_validation_{provider}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            report.error(f"{path.relative_to(ROOT)}: import spec could not be created")
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            report.error(f"{path.relative_to(ROOT)}: import failed: {type(exc).__name__}: {exc}")
            continue
        if module.PROVIDER != provider or module.main is not crawler_main:
            report.error(f"{path.relative_to(ROOT)}: provider/common-engine binding is invalid")
            continue
        captured: list[list[str]] = []

        def fake_main(arguments: list[str]) -> int:
            captured.append(arguments)
            return 73

        module.main = fake_main
        try:
            result = module.run(["--dry-run", "--max-pages", "1", "--detail-limit", "1"])
            if result != 73 or captured != [["--provider", provider, "--dry-run", "--max-pages", "1", "--detail-limit", "1"]]:
                report.error(f"{path.relative_to(ROOT)}: fixed-provider CLI delegation is invalid")
            for forbidden in (["--all"], ["--provider", provider], [f"--provider={provider}"], ["--write-registry"]):
                try:
                    module.run(forbidden)
                except SystemExit:
                    pass
                else:
                    report.error(f"{path.relative_to(ROOT)}: forbidden CLI override was accepted: {forbidden[0]}")
        finally:
            module.main = crawler_main
    report.stats["wrappers"] = len(actual_paths)
    report.stats["wrappers_imported"] = len(actual_paths) if import_wrappers else 0
    report.stats["generated_infrastructure_modules"] = len(infrastructure_paths)


def validate(import_wrappers: bool = True) -> ValidationReport:
    report = ValidationReport()
    try:
        validate_target_documents(report)
        registry_rows = validate_registry(report)
        validate_wrappers(report, registry_rows, import_wrappers=import_wrappers)
    except Exception as exc:
        report.error(f"validator internal failure: {type(exc).__name__}: {exc}")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate every generated YAML crawler target, registry entry, and wrapper")
    parser.add_argument("--skip-import", action="store_true", help="Skip in-process import/CLI delegation checks")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate(import_wrappers=not args.skip_import)
    print("== generated YAML crawler validation ==")
    for key, value in sorted(report.stats.items()):
        print(f"{key}={value}")
    if report.warnings:
        print("warnings:")
        for warning in report.warnings:
            print(f"- {warning}")
    if report.errors:
        print("errors:")
        for error in report.errors:
            print(f"- {error}")
        return 1
    print("errors=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
