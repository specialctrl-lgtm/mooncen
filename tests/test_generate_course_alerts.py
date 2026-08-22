from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from tools.generate_course_alerts import alert_types_for_course, normalize_course_url, scheduled_at_for


class GenerateCourseAlertsTests(unittest.TestCase):
    def test_registration_open_and_closing_within_one_day(self) -> None:
        row = {
            "apply_start": date(2026, 6, 23),
            "apply_end": date(2026, 6, 22),
        }

        alerts = alert_types_for_course(row, date(2026, 6, 22), days=1)

        self.assertEqual(alerts, [
            ("registration_open", date(2026, 6, 23)),
            ("registration_closing", date(2026, 6, 22)),
        ])

    def test_alert_window_excludes_past_and_far_future(self) -> None:
        row = {
            "apply_start": date(2026, 6, 20),
            "apply_end": date(2026, 6, 25),
        }

        self.assertEqual(alert_types_for_course(row, date(2026, 6, 22), days=1), [])

    def test_course_url_falls_back_to_course_id(self) -> None:
        self.assertEqual(normalize_course_url({"course_id": "abc"}), "course:abc")
        self.assertEqual(normalize_course_url({"course_url": "https://example.test/a"}), "https://example.test/a")

    def test_scheduled_at_uses_now_for_today(self) -> None:
        now = datetime(2026, 6, 22, 10, 30, tzinfo=ZoneInfo("Asia/Seoul"))

        self.assertEqual(scheduled_at_for(date(2026, 6, 22), now), now)
        self.assertEqual(scheduled_at_for(date(2026, 6, 23), now).hour, 9)


if __name__ == "__main__":
    unittest.main()
