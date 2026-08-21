from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

from Crawler import Crawler_GeneratedYamlTargets as generated_targets
from Crawler.site_adapters import build_adapter_registry


class SiteCrawlerAdapterTests(unittest.TestCase):
    def test_branch_filter_provider_command_keeps_limit_and_branch_flags(self) -> None:
        adapters = build_adapter_registry(
            {"HOMEPLUS": ["Crawler", "Crawler_Homeplus.py"]},
            set(),
            os.path.abspath("."),
        )

        command = adapters["HOMEPLUS"].build_command(3, branch_code="001", branch_name="Gangnam")

        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1:3], ["-X", "utf8"])
        self.assertTrue(command[3].endswith(os.path.join("Crawler", "Crawler_Homeplus.py")))
        self.assertIn("--limit", command)
        self.assertIn("3", command)
        self.assertIn("--branch-code", command)
        self.assertIn("001", command)
        self.assertIn("--branch-name", command)
        self.assertIn("Gangnam", command)

    def test_lotte_command_supports_branch_filtering(self) -> None:
        adapters = build_adapter_registry(
            {"LOTTE": ["Crawler", "Crawler_Lotte.py"]},
            set(),
            os.path.abspath("."),
        )

        command = adapters["LOTTE"].build_command(1, branch_code="0001")

        self.assertTrue(command[3].endswith(os.path.join("Crawler", "Crawler_Lotte.py")))
        self.assertIn("--limit", command)
        self.assertIn("1", command)
        self.assertIn("--branch-code", command)
        self.assertIn("0001", command)

    def test_generated_provider_uses_yaml_target_limits(self) -> None:
        adapters = build_adapter_registry(
            {"MUNI_TEST": ["Crawler", "Crawler_GeneratedYamlTargets.py", "--provider", "MUNI_TEST", "--save-db"]},
            {"MUNI_TEST"},
            os.path.abspath("."),
        )

        command = adapters["MUNI_TEST"].build_command(7)

        self.assertIn("--provider", command)
        self.assertIn("MUNI_TEST", command)
        self.assertIn("--save-db", command)
        self.assertIn("--per-target-limit", command)
        self.assertIn("7", command)
        self.assertIn("--allow-partial-save", command)
        self.assertIn("--max-depth", command)
        self.assertIn("--max-pages", command)
        self.assertIn("--detail-limit", command)

    def test_generated_provider_preserves_explicit_crawl_budgets(self) -> None:
        adapters = build_adapter_registry(
            {
                "MUNI_TEST": [
                    "Crawler",
                    "Crawler_GeneratedYamlTargets.py",
                    "--provider",
                    "MUNI_TEST",
                    "--save-db",
                    "--mark-stale",
                    "--per-target-limit",
                    "0",
                    "--max-pages",
                    "100",
                    "--detail-limit",
                    "1000",
                ]
            },
            {"MUNI_TEST"},
            os.path.abspath("."),
        )

        command = adapters["MUNI_TEST"].build_command(5000)

        self.assertEqual(command.count("--max-pages"), 1)
        self.assertEqual(command[command.index("--max-pages") + 1], "100")
        self.assertEqual(command.count("--detail-limit"), 1)
        self.assertEqual(command[command.index("--detail-limit") + 1], "1000")
        self.assertNotIn("--mark-stale", command)
        self.assertIn("--allow-partial-save", command)

    def test_generated_full_run_removes_registry_sample_opt_in(self) -> None:
        adapters = build_adapter_registry(
            {
                "MUNI_TEST": [
                    "Crawler",
                    "Crawler_GeneratedYamlTargets.py",
                    "--provider",
                    "MUNI_TEST",
                    "--save-db",
                    "--per-target-limit",
                    "50",
                    "--allow-partial-save",
                ]
            },
            {"MUNI_TEST"},
            os.path.abspath("."),
        )

        with patch.dict(
            os.environ,
            {"YAML_TARGETS_PER_TARGET_LIMIT": "20"},
        ):
            command = adapters["MUNI_TEST"].build_command(None)

        self.assertIn("--mark-stale", command)
        self.assertNotIn("--allow-partial-save", command)
        limit_index = command.index("--per-target-limit")
        self.assertEqual(command[limit_index + 1], "0")

    def test_limited_municipal_aggregate_is_upsert_only_and_parseable(self) -> None:
        adapters = build_adapter_registry(
            {
                "MUNICIPAL_RESERVATION_TARGETS": [
                    "Crawler",
                    "Crawler_MunicipalIntegratedReservation.py",
                    "--save-db",
                    "--mark-stale",
                    "--per-target-limit",
                    "0",
                    "--max-pages",
                    "1500",
                    "--detail-limit",
                    "3000",
                    "--parallel-workers",
                    "1",
                ]
            },
            set(),
            os.path.abspath("."),
        )

        command = adapters["MUNICIPAL_RESERVATION_TARGETS"].build_command(25)
        script_index = next(index for index, value in enumerate(command) if value.endswith(".py"))
        parsed = generated_targets.parse_args(command[script_index + 1 :])

        self.assertTrue(parsed.save_db)
        self.assertFalse(parsed.mark_stale)
        self.assertTrue(parsed.allow_partial_save)
        self.assertEqual(parsed.per_target_limit, 25)
        self.assertEqual(parsed.max_pages, 1500)
        self.assertEqual(parsed.detail_limit, 3000)
        self.assertEqual(parsed.parallel_workers, 1)

    def test_only_manual_generic_wrappers_receive_partial_save_opt_in(self) -> None:
        adapters = build_adapter_registry(
            {
                "BABSANG_WELFARE_PROGRAM": ["Crawler", "Crawler_BabsangWelfare.py"],
                "SAHASILVER_COURSE": ["Crawler", "Crawler_Sahasilver.py"],
            },
            set(),
            os.path.abspath("."),
        )

        generic_command = adapters["BABSANG_WELFARE_PROGRAM"].build_command(None)
        standalone_command = adapters["SAHASILVER_COURSE"].build_command(None)

        self.assertIn("--save-db", generic_command)
        self.assertIn("--allow-partial-save", generic_command)
        self.assertIn("--save-db", standalone_command)
        self.assertNotIn("--allow-partial-save", standalone_command)


if __name__ == "__main__":
    unittest.main()
