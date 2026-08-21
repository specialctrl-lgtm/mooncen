from __future__ import annotations

import unittest

from tools.calculate_course_quality import calculate_quality_score, grade_for_score


class CourseQualityScoreTests(unittest.TestCase):
    def test_complete_course_is_good(self) -> None:
        result = calculate_quality_score(
            {
                "title": "Kids Art Class",
                "branch_name": "Suwon Branch",
                "raw_url": "https://example.test/course/1",
                "start_date": "2026-08-30",
                "schedule_raw": "Sun 11:00-12:00",
                "fee": 0,
                "domain_category": "art",
                "target_min_age": 96,
                "venue_address": "Suwon",
            }
        )

        self.assertEqual(result["total_score"], 100)
        self.assertEqual(result["grade"], "good")
        self.assertEqual(result["missing_fields"], [])

    def test_missing_core_fields_are_reported(self) -> None:
        result = calculate_quality_score(
            {
                "title": "Untimed Class",
                "provider": "HOMEPLUS",
                "fee": 10000,
            }
        )

        self.assertEqual(result["grade"], "bad")
        self.assertIn("branch_name", result["missing_fields"])
        self.assertIn("url", result["missing_fields"])
        self.assertIn("period", result["missing_fields"])
        self.assertIn("target_age", result["missing_fields"])

    def test_grade_boundaries(self) -> None:
        self.assertEqual(grade_for_score(80), "good")
        self.assertEqual(grade_for_score(50), "warning")
        self.assertEqual(grade_for_score(49), "bad")


if __name__ == "__main__":
    unittest.main()
