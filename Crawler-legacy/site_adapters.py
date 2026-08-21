from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional


BRANCH_FILTER_PROVIDERS = {
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

SAVE_DB_FLAG_PROVIDERS = {
    "BABSANG_WELFARE_PROGRAM",
    "BUSAN_RESERVATION",
    "SAHASILVER_COURSE",
    "SEOSAN_WELFARE_TOTAL_RESERVATION",
    "SEONGNAM_BAEUMSOOP",
    "ANYANG_LIFELONG_LEARNING",
    "YONGIN_LIFELONG_LEARNING",
    "ESONGPA_SPORTS_CULTURE",
    "SEOUL_PUBLIC_SERVICE",
}


@dataclass(frozen=True)
class SiteCrawlerAdapter:
    provider: str
    script_parts: list[str]
    project_root: str
    generated_provider: bool = False

    def build_command(
        self,
        limit: Optional[int],
        branch_code: str | None = None,
        branch_name: str | None = None,
    ) -> list[str]:
        script_index = next((index for index, part in enumerate(self.script_parts) if str(part).endswith(".py")), 1)
        script_path = os.path.join(self.project_root, *self.script_parts[: script_index + 1])
        command = [sys.executable, "-X", "utf8", script_path, *self.script_parts[script_index + 1 :]]

        if self.provider in {"COLLECTED_YAML", "FACILITY_REGISTRY"}:
            return self._build_collected_yaml_command(command, limit)
        if self.provider == "YAML_TARGETS_ALL":
            return self._build_yaml_targets_all_command(command, limit)
        if self.generated_provider:
            return self._build_generated_provider_command(command, limit)

        if self.provider in SAVE_DB_FLAG_PROVIDERS:
            command.append("--save-db")
        if limit is not None:
            command.extend(["--limit", str(limit)])
        if branch_code and self.provider in BRANCH_FILTER_PROVIDERS:
            command.extend(["--branch-code", branch_code])
        if branch_name and self.provider in BRANCH_FILTER_PROVIDERS:
            command.extend(["--branch-name", branch_name])
        return command

    def _build_collected_yaml_command(self, command: list[str], limit: Optional[int]) -> list[str]:
        env_prefix = "FACILITY_REGISTRY" if self.provider == "FACILITY_REGISTRY" else "COLLECTED_YAML"
        default_source = "facility" if self.provider == "FACILITY_REGISTRY" else "collected"
        source = os.getenv(f"{env_prefix}_SOURCE", default_source)
        per_target_limit = os.getenv(f"{env_prefix}_PER_TARGET_LIMIT", "20")
        max_depth = os.getenv(f"{env_prefix}_MAX_DEPTH", "1")
        max_pages = os.getenv(f"{env_prefix}_MAX_PAGES", "20")
        detail_limit = os.getenv(f"{env_prefix}_DETAIL_LIMIT", "30")
        target_limit = os.getenv(f"{env_prefix}_TARGET_LIMIT", "")
        command.extend(
            [
                "--source",
                source,
                "--save-db",
                "--per-target-limit",
                per_target_limit,
                "--max-depth",
                max_depth,
                "--max-pages",
                max_pages,
                "--detail-limit",
                detail_limit,
            ]
        )
        if os.getenv(f"{env_prefix}_INCLUDE_REVIEW", "").lower() in {"1", "true", "yes", "y"}:
            command.append("--include-review")
        if limit is not None:
            command.extend(["--target-limit", str(limit)])
        elif target_limit.strip():
            command.extend(["--target-limit", target_limit.strip()])
        return command

    def _build_yaml_targets_all_command(self, command: list[str], limit: Optional[int]) -> list[str]:
        source = os.getenv("YAML_TARGETS_SOURCE", "")
        max_priority = os.getenv("YAML_TARGETS_MAX_PRIORITY", "")
        target_limit = os.getenv("YAML_TARGETS_TARGET_LIMIT", "")
        per_target_limit = os.getenv("YAML_TARGETS_PER_TARGET_LIMIT", "20")
        max_depth = os.getenv("YAML_TARGETS_MAX_DEPTH", "1")
        max_pages = os.getenv("YAML_TARGETS_MAX_PAGES", "20")
        detail_limit = os.getenv("YAML_TARGETS_DETAIL_LIMIT", "30")
        command.extend(
            [
                "--all",
                "--save-db",
                "--per-target-limit",
                per_target_limit,
                "--max-depth",
                max_depth,
                "--max-pages",
                max_pages,
                "--detail-limit",
                detail_limit,
            ]
        )
        if source.strip():
            command.extend(["--source", source.strip()])
        if max_priority.strip():
            command.extend(["--max-priority", max_priority.strip()])
        if limit is not None:
            command.extend(["--target-limit", str(limit)])
        elif target_limit.strip():
            command.extend(["--target-limit", target_limit.strip()])
        return command

    def _build_generated_provider_command(self, command: list[str], limit: Optional[int]) -> list[str]:
        per_target_limit = os.getenv("YAML_TARGETS_PER_TARGET_LIMIT", "20")
        max_depth = os.getenv("YAML_TARGETS_MAX_DEPTH", "1")
        max_pages = os.getenv("YAML_TARGETS_MAX_PAGES", "20")
        detail_limit = os.getenv("YAML_TARGETS_DETAIL_LIMIT", "30")
        if "--mark-stale" not in command:
            command.append("--mark-stale")
        command.extend(
            [
                "--per-target-limit",
                str(limit) if limit is not None else per_target_limit,
                "--max-depth",
                max_depth,
                "--max-pages",
                max_pages,
                "--detail-limit",
                detail_limit,
            ]
        )
        return command


def build_adapter_registry(
    provider_commands: dict[str, list[str]],
    generated_provider_names: set[str],
    project_root: str,
) -> dict[str, SiteCrawlerAdapter]:
    return {
        provider: SiteCrawlerAdapter(
            provider=provider,
            script_parts=script_parts,
            project_root=project_root,
            generated_provider=provider in generated_provider_names,
        )
        for provider, script_parts in provider_commands.items()
    }
