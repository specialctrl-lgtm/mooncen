from __future__ import annotations

import unittest

from DB.course_upsert_guards import (
    coalesce_provider_course_id_by_raw_url,
    coalesce_provider_course_ids_by_raw_url,
    course_missing_required_display_fields,
    normalize_course_raw_url,
    recover_course_schedule_raw,
)


def publishable_course(**overrides):
    course = {
        "provider": "MUNI_TEST",
        "provider_course_id": "course-id",
        "raw_url": "https://example.test/course/course-id",
        "title": "시민 요리 강좌",
        "schedule_raw": "2026-08-01 ~ 2026-08-31 매주 월요일 10:00",
        "status": "OPEN",
    }
    course.update(overrides)
    return course


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row


class SequencedCursor(FakeCursor):
    def __init__(self, rows):
        super().__init__(None)
        self.rows = list(rows)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class BatchCursor:
    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self.candidates)


class CourseUpsertGuardTests(unittest.TestCase):
    def test_duplicate_raw_url_reuses_existing_provider_course_id(self) -> None:
        cursor = FakeCursor(
            {
                "provider_course_id": "existing-id",
                "title": "Old title",
                "branch_id": "old-branch",
            }
        )
        course = publishable_course(
            provider="HOMEPLUS",
            provider_course_id="incoming-id",
            raw_url="https://example.test/course/1",
            title="New title",
            branch_id="new-branch",
        )

        coalesce_provider_course_id_by_raw_url(cursor, course)

        self.assertEqual(course["provider_course_id"], "existing-id")
        self.assertEqual(course["raw_url"], "https://example.test/course/1")

    def test_missing_raw_url_does_not_query_or_mutate(self) -> None:
        cursor = FakeCursor(None)
        course = publishable_course(
            provider="HOMEPLUS",
            provider_course_id="incoming-id",
            raw_url="",
        )

        coalesce_provider_course_id_by_raw_url(cursor, course)

        self.assertEqual(cursor.executed, [])
        self.assertEqual(course["provider_course_id"], "incoming-id")

    def test_stable_incoming_id_migrates_legacy_raw_url_owner(self) -> None:
        cursor = SequencedCursor(
            [
                None,
                {
                    "provider_course_id": "legacy-hash-id",
                    "title": "Same course",
                    "branch_id": "old-branch",
                },
            ]
        )
        course = publishable_course(
            provider_course_id="8261",
            prefer_incoming_provider_course_id=True,
            raw_url="https://example.test/course/8261",
            title="Same course",
            branch_id="new-branch",
        )

        coalesce_provider_course_id_by_raw_url(cursor, course)

        self.assertEqual(course["provider_course_id"], "8261")
        self.assertEqual(len(cursor.executed), 3)
        self.assertIn("UPDATE courses", cursor.executed[-1][0])
        self.assertEqual(cursor.executed[-1][1], ("8261", "MUNI_TEST", "legacy-hash-id"))

    def test_existing_stable_incoming_id_wins_over_legacy_raw_url_owner(self) -> None:
        cursor = SequencedCursor([{"exists": 1}])
        course = publishable_course(
            provider_course_id="8261",
            prefer_incoming_provider_course_id=True,
            raw_url="https://example.test/course/8261",
            title="Same course",
        )

        coalesce_provider_course_id_by_raw_url(cursor, course)

        self.assertEqual(course["provider_course_id"], "8261")
        self.assertEqual(len(cursor.executed), 1)

    def test_batch_guard_coalesces_duplicate_urls_with_one_candidate_query(self) -> None:
        cursor = BatchCursor(
            [
                {
                    "ordinal": 0,
                    "provider": "HOMEPLUS",
                    "incoming_course_id": "incoming-id",
                    "existing_course_id": "existing-id",
                    "incoming_title": "New title",
                    "existing_title": "Old title",
                    "incoming_branch_id": "",
                    "existing_branch_id": "",
                    "prefer_incoming": False,
                    "incoming_id_exists": False,
                }
            ]
        )
        execute_calls = []
        course = publishable_course(
            provider="HOMEPLUS",
            provider_course_id="incoming-id",
            raw_url="https://example.test/course/1",
            title="New title",
        )

        coalesce_provider_course_ids_by_raw_url(
            cursor,
            [course],
            execute_values_fn=lambda *args, **kwargs: execute_calls.append(
                (args, kwargs)
            ),
        )

        self.assertEqual(course["provider_course_id"], "existing-id")
        self.assertEqual(len(execute_calls), 1)
        self.assertEqual(len(cursor.executed), 3)
        candidate_sql = cursor.executed[-1][0]
        self.assertIn("WITH incoming_rows AS MATERIALIZED", candidate_sql)
        self.assertIn("legacy_courses AS MATERIALIZED", candidate_sql)
        self.assertIn("0 AS match_priority", candidate_sql)
        self.assertIn("1 AS match_priority", candidate_sql)
        self.assertIn("ORDER BY\n                        match_priority", candidate_sql)
        self.assertIn("row_number() OVER", candidate_sql)

    def test_batch_guard_migrates_legacy_id_when_incoming_id_is_preferred(self) -> None:
        cursor = BatchCursor(
            [
                {
                    "ordinal": 0,
                    "provider": "MUNI_TEST",
                    "incoming_course_id": "8261",
                    "existing_course_id": "legacy-hash-id",
                    "incoming_title": "Same course",
                    "existing_title": "Same course",
                    "incoming_branch_id": "",
                    "existing_branch_id": "",
                    "prefer_incoming": True,
                    "incoming_id_exists": False,
                }
            ]
        )
        execute_calls = []
        course = publishable_course(
            provider_course_id="8261",
            prefer_incoming_provider_course_id=True,
            raw_url="https://example.test/course/8261",
            title="Same course",
        )

        coalesce_provider_course_ids_by_raw_url(
            cursor,
            [course],
            execute_values_fn=lambda *args, **kwargs: execute_calls.append(
                (args, kwargs)
            ),
        )

        self.assertEqual(course["provider_course_id"], "8261")
        self.assertEqual(len(execute_calls), 2)
        migration_values = execute_calls[-1][0][2]
        self.assertEqual(
            migration_values,
            [("MUNI_TEST", "8261", "legacy-hash-id")],
        )

    def test_batch_guard_uses_url_owner_when_preferred_id_already_exists(self) -> None:
        cursor = BatchCursor(
            [
                {
                    "ordinal": 0,
                    "provider": "MUNI_TEST",
                    "incoming_course_id": "stable-id",
                    "existing_course_id": "url-owner-id",
                    "incoming_title": "Current title",
                    "existing_title": "Old title",
                    "incoming_branch_id": "",
                    "existing_branch_id": "",
                    "prefer_incoming": True,
                    "incoming_id_exists": True,
                }
            ]
        )
        execute_calls = []
        course = publishable_course(
            provider_course_id="stable-id",
            prefer_incoming_provider_course_id=True,
            raw_url="https://example.test/course/current",
            title="Current title",
        )

        coalesce_provider_course_ids_by_raw_url(
            cursor,
            [course],
            execute_values_fn=lambda *args, **kwargs: execute_calls.append(
                (args, kwargs)
            ),
        )

        self.assertEqual(course["provider_course_id"], "url-owner-id")
        self.assertEqual(len(execute_calls), 1)

    def test_batch_guard_rejects_duplicate_incoming_raw_urls(self) -> None:
        cursor = BatchCursor([])
        courses = [
            publishable_course(
                provider="HOMEPLUS",
                provider_course_id="first",
                raw_url="https://example.test/course/1",
                title="First course",
            ),
            publishable_course(
                provider="HOMEPLUS",
                provider_course_id="second",
                raw_url="https://example.test/course/1",
                title="Second course",
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            "staging batch contains duplicate canonical raw_url",
        ):
            coalesce_provider_course_ids_by_raw_url(
                cursor,
                courses,
                execute_values_fn=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(cursor.executed, [])

    def test_synthetic_list_item_fragment_is_preserved_by_url_normalization(self) -> None:
        first = "https://example.test/reservation/list#mooncen-item-11111111111111111111"
        second = "https://example.test/reservation/list#mooncen-item-22222222222222222222"

        self.assertEqual(normalize_course_raw_url(first), first)
        self.assertEqual(normalize_course_raw_url(second), second)
        self.assertNotEqual(normalize_course_raw_url(first), normalize_course_raw_url(second))

    def test_schedule_raw_recovers_from_period(self) -> None:
        self.assertEqual(
            recover_course_schedule_raw({"schedule_raw": "", "period": "2026-03-01 ~ 2026-06-30"}),
            "2026-03-01 ~ 2026-06-30",
        )

    def test_schedule_raw_recovers_from_dates(self) -> None:
        self.assertEqual(
            recover_course_schedule_raw({"start_date": "2026-03-01", "end_date": "2026-06-30"}),
            "2026-03-01 ~ 2026-06-30",
        )

    def test_course_missing_required_display_fields_requires_title_and_schedule(self) -> None:
        self.assertTrue(course_missing_required_display_fields({"title": "강좌", "schedule_raw": ""}))
        self.assertTrue(course_missing_required_display_fields({"title": "", "schedule_raw": "월 10:00"}))
        self.assertFalse(
            course_missing_required_display_fields(
                {"title": "강좌", "schedule_raw": "", "start_date": "2026-03-01"}
            )
        )


if __name__ == "__main__":
    unittest.main()
