from __future__ import annotations

import unittest

from target_category_fallback import infer_age_group_from_category
from tools.category_age_patterns import build_category_age_updates


class CategoryAgePatternTests(unittest.TestCase):
    def test_emart_culture_category_is_filled_when_missing(self) -> None:
        decision = build_category_age_updates(
            {
                "provider": "EMART",
                "title": "sample",
                "collection_category": None,
                "domain_category": "",
            }
        )

        self.assertEqual(decision.updates["collection_category"], "문화센터")
        self.assertEqual(decision.updates["domain_category"], "문화센터")

    def test_explicit_month_range_sets_month_values_and_group(self) -> None:
        decision = build_category_age_updates(
            {
                "provider": "EMART",
                "title": "월요 트니트니(13~19개월)",
                "target": "13~19개월",
                "target_age_group": None,
                "target_min_age": None,
                "target_max_age": None,
            }
        )

        self.assertEqual(decision.updates["target_min_age"], 13)
        self.assertEqual(decision.updates["target_max_age"], 19)
        self.assertEqual(decision.updates["target_age_group"], "INFANT")

    def test_non_explicit_adult_default_age_range_is_removed(self) -> None:
        decision = build_category_age_updates(
            {
                "provider": "LOTTE",
                "title": "플라워 클래스",
                "target": "1인강좌",
                "category_raw": "ADULT",
                "target_age_group": "ADULT",
                "target_min_age": 20,
                "target_max_age": 59,
            }
        )

        self.assertIsNone(decision.updates["target_min_age"])
        self.assertIsNone(decision.updates["target_max_age"])

    def test_non_explicit_adult_numeric_range_is_removed_even_when_not_default(self) -> None:
        decision = build_category_age_updates(
            {
                "provider": "LOTTE",
                "title": "음감발달 바이올린",
                "target": "1인강좌",
                "category_raw": "ADULT",
                "target_age_group": "ADULT",
                "target_min_age": 19,
                "target_max_age": 19,
            }
        )

        self.assertIsNone(decision.updates["target_min_age"])
        self.assertIsNone(decision.updates["target_max_age"])

    def test_description_month_phrase_is_not_used_as_target_age(self) -> None:
        decision = build_category_age_updates(
            {
                "provider": "EMART",
                "title": "댄스스포츠 (성인)",
                "target": None,
                "category_raw": "Dance & Exercise",
                "target_age_group": "ADULT",
                "target_min_age": None,
                "target_max_age": None,
                "description": "3개월 동안 배운 루틴을 복습합니다.",
            }
        )

        self.assertNotIn("target_min_age", decision.updates)
        self.assertNotIn("target_max_age", decision.updates)

    def test_birth_year_to_school_level_keeps_upper_bound(self) -> None:
        decision = build_category_age_updates(
            {
                "provider": "LOTTE",
                "title": "프리미엄 바이올린",
                "target": "2022년생~고등",
                "category_raw": "TEEN",
                "target_age_group": "TEEN",
                "target_min_age": 48,
                "target_max_age": 216,
            }
        )

        self.assertNotIn("target_age_group", decision.updates)
        self.assertNotEqual(decision.updates.get("target_max_age"), 48)

    def test_existing_age_group_is_not_overwritten_by_wide_birth_range(self) -> None:
        decision = build_category_age_updates(
            {
                "provider": "LOTTE",
                "title": "드럼교실",
                "target": "2011~2018년생",
                "category_raw": "CHILD",
                "target_age_group": "CHILD",
                "target_min_age": 8,
                "target_max_age": 15,
            }
        )

        self.assertNotIn("target_age_group", decision.updates)

    def test_category_fallback_understands_korean_tokens(self) -> None:
        self.assertEqual(infer_age_group_from_category("성인"), "ADULT")
        self.assertEqual(infer_age_group_from_category("어린이"), "CHILD")
        self.assertEqual(infer_age_group_from_category("With Mom"), "TODDLER")


if __name__ == "__main__":
    unittest.main()
