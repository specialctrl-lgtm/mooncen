from __future__ import annotations

import unittest

from DB.crawl_progress import normalize_progress_status


class CrawlProgressTests(unittest.TestCase):
    def test_progress_status_mapping(self) -> None:
        self.assertEqual(normalize_progress_status("pending"), "pending")
        self.assertEqual(normalize_progress_status("running"), "in_progress")
        self.assertEqual(normalize_progress_status("success"), "completed")
        self.assertEqual(normalize_progress_status("skipped"), "completed")
        self.assertEqual(normalize_progress_status("failed"), "failed")
        self.assertEqual(normalize_progress_status("stopped"), "failed")


if __name__ == "__main__":
    unittest.main()
