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
    "SAHASILVER_COURSE",
    "SEOSAN_WELFARE_TOTAL_RESERVATION",
    "SEONGNAM_BAEUMSOOP",
    "ANYANG_LIFELONG_LEARNING",
    "YONGIN_LIFELONG_LEARNING",
    "ESONGPA_SPORTS_CULTURE",
}

PARTIAL_SAVE_FLAG_PROVIDERS = {
    "ANYANG_LIFELONG_LEARNING",
    "BABSANG_WELFARE_PROGRAM",
    "YONGIN_LIFELONG_LEARNING",
}

LIMITED_AGGREGATE_PROVIDERS = {
    "EXPERIENCE_TARGETS",
    "MUNICIPAL_RESERVATION_TARGETS",
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
        script_index = next(
            (
                index
                for index, part in enumerate(self.script_parts)
                if str(part).endswith(".py")
            ),
            1,
        )
        script_path = os.path.join(
            self.project_root, *self.script_parts[: script_index + 1]
        )
        command = [
            sys.executable,
            "-X",
            "utf8",
            script_path,
            *self.script_parts[script_index + 1 :],
        ]

        if self.provider in {"COLLECTED_YAML", "FACILITY_REGISTRY"}:
            return self._build_collected_yaml_command(command, limit)
        if self.provider == "YAML_TARGETS_ALL":
            return self._build_yaml_targets_all_command(command, limit)
        if self.generated_provider:
            return self._build_generated_provider_command(command, limit)
        if self.provider in LIMITED_AGGREGATE_PROVIDERS and limit is not None:
            return self._build_limited_aggregate_command(command, limit)
        if (
            self.provider
            in {
                "BABSANG_WELFARE_PROGRAM",
                "SEOSAN_WELFARE_TOTAL_RESERVATION",
                "SEOUL_PUBLIC_SERVICE",
            }
            and "--per-target-limit" in command
        ):
            return self._build_full_manual_command(command, limit)

        if self.provider in SAVE_DB_FLAG_PROVIDERS:
            command.append("--save-db")
        if self.provider in PARTIAL_SAVE_FLAG_PROVIDERS:
            command.append("--allow-partial-save")
        if limit is not None:
            command.extend(["--limit", str(limit)])
        if branch_code and self.provider in BRANCH_FILTER_PROVIDERS:
            command.extend(["--branch-code", branch_code])
        if branch_name and self.provider in BRANCH_FILTER_PROVIDERS:
            command.extend(["--branch-name", branch_name])
        return command

    def _build_full_manual_command(
        self,
        command: list[str],
        limit: Optional[int],
    ) -> list[str]:
        if limit is None:
            return command

        normalized: list[str] = []
        index = 0
        while index < len(command):
            argument = command[index]
            if argument in {"--per-target-limit", "--limit"}:
                index += 2
                continue
            if argument in {"--allow-partial-save", "--mark-stale"}:
                index += 1
                continue
            normalized.append(argument)
            index += 1
        normalized.extend(
            [
                "--allow-partial-save",
                "--per-target-limit",
                str(limit),
            ]
        )
        return normalized

    def _build_limited_aggregate_command(
        self, command: list[str], limit: int
    ) -> list[str]:
        """Make an explicitly capped aggregate run upsert-only.

        Aggregate production commands normally request a complete snapshot and
        mark unseen rows stale. A manual ``run_crawlers --limit`` request is not
        a complete snapshot, so forwarding the limit must also remove stale
        cleanup and opt in to the generated crawler's bounded-save contract.
        """
        normalized: list[str] = []
        index = 0
        while index < len(command):
            argument = command[index]
            if argument == "--per-target-limit":
                index += 2
                continue
            if argument in {"--allow-partial-save", "--mark-stale"}:
                index += 1
                continue
            normalized.append(argument)
            index += 1
        normalized.extend(
            [
                "--allow-partial-save",
                "--per-target-limit",
                str(limit),
            ]
        )
        return normalized

    def _build_collected_yaml_command(
        self, command: list[str], limit: Optional[int]
    ) -> list[str]:
        env_prefix = (
            "FACILITY_REGISTRY"
            if self.provider == "FACILITY_REGISTRY"
            else "COLLECTED_YAML"
        )
        default_source = (
            "facility" if self.provider == "FACILITY_REGISTRY" else "collected"
        )
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
        if per_target_limit != "0":
            command.append("--allow-partial-save")
        if os.getenv(f"{env_prefix}_INCLUDE_REVIEW", "").lower() in {
            "1",
            "true",
            "yes",
            "y",
        }:
            command.append("--include-review")
        if limit is not None:
            command.extend(["--target-limit", str(limit)])
        elif target_limit.strip():
            command.extend(["--target-limit", target_limit.strip()])
        return command

    def _build_yaml_targets_all_command(
        self, command: list[str], limit: Optional[int]
    ) -> list[str]:
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
        if per_target_limit != "0":
            command.append("--allow-partial-save")
        if source.strip():
            command.extend(["--source", source.strip()])
        if max_priority.strip():
            command.extend(["--max-priority", max_priority.strip()])
        if limit is not None:
            command.extend(["--target-limit", str(limit)])
        elif target_limit.strip():
            command.extend(["--target-limit", target_limit.strip()])
        return command

    def _build_generated_provider_command(
        self, command: list[str], limit: Optional[int]
    ) -> list[str]:
        # Generated production crawlers mark unseen rows stale, so their default
        # must be a complete (uncapped) row pass. Explicit sampled runs keep the
        # requested cap and disable destructive stale cleanup.
        per_target_limit = str(limit) if limit is not None else "0"
        max_depth = os.getenv("YAML_TARGETS_MAX_DEPTH", "1")
        max_pages = os.getenv("YAML_TARGETS_MAX_PAGES", "20")
        detail_limit = os.getenv("YAML_TARGETS_DETAIL_LIMIT", "30")
        normalized: list[str] = []
        index = 0
        while index < len(command):
            argument = command[index]
            if argument == "--per-target-limit":
                index += 2
                continue
            if argument == "--allow-partial-save":
                index += 1
                continue
            normalized.append(argument)
            index += 1
        command = normalized
        if per_target_limit == "0" and "--mark-stale" not in command:
            command.append("--mark-stale")
        elif per_target_limit != "0":
            command = [argument for argument in command if argument != "--mark-stale"]
            command.append("--allow-partial-save")
        command.extend(["--per-target-limit", per_target_limit])
        # Registry-generated provider commands may carry deliberately larger
        # crawl budgets than the generic YAML defaults.  Appending another
        # argparse value here made the generic 20-page/30-detail defaults win
        # silently, which truncated those provider runs.  Use generic values
        # only when the provider did not declare an explicit budget.
        for option, default_value in (
            ("--max-depth", max_depth),
            ("--max-pages", max_pages),
            ("--detail-limit", detail_limit),
        ):
            if option not in command:
                command.extend([option, default_value])
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
