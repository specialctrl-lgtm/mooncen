from __future__ import annotations

import unittest

import requests

from ai_processor import AIProcessor, infer_category
from run_ai_pipeline import course_needs_ai_work, title_ai_changed


class AiProcessingGuardTests(unittest.TestCase):
    def test_title_unchanged_result_is_not_requeued_for_title_only(self) -> None:
        course = {
            "is_ai_processed": True,
            "ai_title_processed": False,
            "ai_title_result": {"source": "title_unchanged"},
        }

        self.assertFalse(course_needs_ai_work(course, process_title=True, process_summary=False))

    def test_summary_unprocessed_still_queues_even_when_title_unchanged(self) -> None:
        course = {
            "is_ai_processed": False,
            "ai_title_processed": False,
            "ai_title_result": {"source": "title_unchanged"},
        }

        self.assertTrue(course_needs_ai_work(course, process_title=True, process_summary=True))

    def test_completed_course_is_not_requeued(self) -> None:
        course = {
            "is_ai_processed": True,
            "ai_title_processed": True,
            "ai_title_result": {"source": "ai"},
        }

        self.assertFalse(course_needs_ai_work(course, process_title=True, process_summary=True))

    def test_title_ai_changed_when_age_target_improves_without_title_change(self) -> None:
        course = {
            "title": "미술놀이",
            "target": "",
            "target_age_group": None,
            "target_min_age": None,
            "target_max_age": None,
            "target_tags": [],
        }
        title_result = {
            "title": "미술놀이",
            "target": "24개월 이상",
            "target_age_group": "TODDLER",
            "target_min_age": 24,
            "target_max_age": None,
            "target_tags": ["유아"],
        }

        self.assertTrue(title_ai_changed(course, title_result))

    def test_title_result_keeps_source_metadata_for_upsert_audits(self) -> None:
        processor = AIProcessor()
        result = processor._normalize_title_result(
            {},
            {
                "provider": "HOMEPLUS",
                "title": "미술놀이",
                "title_raw": "미술놀이",
                "category_raw": "유아",
                "target": "",
                "schedule_raw": "",
            },
            "미술놀이",
            "",
            "",
        )

        metadata = result["ai_title_result"]
        self.assertEqual(metadata["source_title"], "미술놀이")
        self.assertEqual(metadata["source_title_raw"], "미술놀이")
        self.assertEqual(metadata["source_category_raw"], "유아")

    def test_clear_age_title_uses_rule_fast_path_without_ai_call(self) -> None:
        processor = AIProcessor()

        def fail_call(*_args, **_kwargs):
            raise AssertionError("AI model should not be called for explicit age title fast path")

        processor._call_ollama = fail_call
        result = processor.analyze_title(
            {
                "provider": "EMART",
                "title": "K-POP 방송댄스 [2017~21년생/토/10:00]",
                "title_raw": "K-POP 방송댄스 [2017~21년생/토/10:00]",
                "target": "2017~21년생",
                "category_raw": "댄스",
                "schedule_raw": "토 10:00",
            }
        )

        self.assertEqual(result["title"], "K-POP 방송댄스")
        self.assertEqual(result["target"], "2017~2021년생")
        self.assertEqual(result["target_age_group"], "CHILD")
        self.assertEqual(result["ai_title_result"]["source"], "rule_fast_path")

    def test_title_rules_do_not_require_ai_provider(self) -> None:
        processor = AIProcessor()
        processor.model = None
        processor.provider = None

        def fail_call(*_args, **_kwargs):
            raise AssertionError("AI model should not be called for title metadata rules")

        processor._call_ollama = fail_call
        result = processor.analyze_title(
            {
                "provider": "LOTTE_MART",
                "title": "오감놀이 [24개월~만 4세/토/10:00]",
                "title_raw": "오감놀이 [24개월~만 4세/토/10:00]",
                "target": "유아 · 24개월~만 4세",
                "category_raw": "유아",
                "schedule_raw": "토 10:00",
            }
        )

        self.assertEqual(result["title"], "오감놀이")
        self.assertEqual(result["target"], "유아 · 24개월~만 4세")
        self.assertEqual(result["target_age_group"], "TODDLER")
        self.assertEqual(result["target_min_age"], 24)
        self.assertEqual(result["target_max_age"], 59)
        self.assertEqual(result["target_tags"], ["유아"])
        self.assertEqual(result["ai_title_result"]["source"], "rule_fast_path")
        self.assertEqual(result["ai_title_result"]["age_value_source"], "crawler_target")

    def test_non_age_target_text_is_marked_for_clear(self) -> None:
        processor = AIProcessor()
        result = processor.analyze_title(
            {
                "provider": "LOTTE",
                "title": "[\ud2b9\uac15]\uc5ec\ub984 \ub370\uc77c\ub9ac \ud50c\ub77c\uc6cc \ud074\ub798\uc2a4",
                "title_raw": "[\ud2b9\uac15]\uc5ec\ub984 \ub370\uc77c\ub9ac \ud50c\ub77c\uc6cc \ud074\ub798\uc2a4",
                "target": "1\uc778\uac15\uc88c",
                "category_raw": "ADULT",
                "schedule_raw": "",
            }
        )

        self.assertEqual(result["title"], "\uc5ec\ub984 \ub370\uc77c\ub9ac \ud50c\ub77c\uc6cc \ud074\ub798\uc2a4")
        self.assertIsNone(result["target"])
        self.assertTrue(result["clear_target_text"])
        self.assertTrue(
            title_ai_changed(
                {
                    "title": result["title"],
                    "target": "1\uc778\uac15\uc88c",
                    "target_age_group": result.get("target_age_group"),
                    "target_min_age": result.get("target_min_age"),
                    "target_max_age": result.get("target_max_age"),
                    "target_tags": result.get("target_tags") or [],
                },
                result,
            )
        )

        stale_age_result = processor.analyze_title(
            {
                "provider": "LOTTE",
                "title": "\ud50c\ub77c\uc6cc 1\uc77c \ud2b9\uac15",
                "title_raw": "[6/25] \ud50c\ub77c\uc6cc 1\uc77c \ud2b9\uac15",
                "target": "1\uc778\uac15\uc88c",
                "target_age_group": "SENIOR",
                "target_min_age": None,
                "target_max_age": None,
                "category_raw": "",
                "schedule_raw": "",
            }
        )
        self.assertIsNone(stale_age_result["target"])
        self.assertIsNone(stale_age_result["target_age_group"])
        self.assertTrue(stale_age_result["clear_target_text"])

    def test_menu_like_target_text_is_rejected_but_target_label_list_is_kept(self) -> None:
        processor = AIProcessor()

        menu_result = processor.analyze_title(
            {
                "provider": "LOCAL",
                "title": "\uc815\uae30\uad50\uc721",
                "title_raw": "\uc815\uae30\uad50\uc721",
                "target": "\uc131\uc778\ubb38\ud574\uad50\uc721 \uc18c\uac1c \uc6a9\uc778\uc2dc \uc9c1\uc601\uad50\uc2e4 \ubbfc\uac04\uc774\uc804 \uad50\uc2e4 \ud3c9\uc0dd\ud559\uc2b5\uad00",
                "category_raw": "",
                "schedule_raw": "",
            }
        )
        self.assertIsNone(menu_result["target"])
        self.assertTrue(menu_result["clear_target_text"])

        label_result = processor.analyze_title(
            {
                "provider": "LOCAL",
                "title": "\uc2dc\uccad\uc815\uc6d0 \ud574\uc124\ud22c\uc5b4",
                "title_raw": "\uc2dc\uccad\uc815\uc6d0 \ud574\uc124\ud22c\uc5b4",
                "target": "\uc720\uc544, \ucd08\ub4f1, \uccad\uc18c\ub144, \uccad\ub144/\uc9c1\uc7a5\uc778, \uc131\uc778, \uc5b4\ub974\uc2e0, \uc7a5\uc560\uc778, \uc678\uad6d\uc778",
                "category_raw": "",
                "schedule_raw": "",
            }
        )
        self.assertEqual(
            label_result["target"],
            "\uc720\uc544, \ucd08\ub4f1, \uccad\uc18c\ub144, \uccad\ub144/\uc9c1\uc7a5\uc778, \uc131\uc778, \uc5b4\ub974\uc2e0, \uc7a5\uc560\uc778, \uc678\uad6d\uc778",
        )
        self.assertFalse(label_result["clear_target_text"])

    def test_ai_course_result_uses_ai_tags_only(self) -> None:
        processor = AIProcessor()
        result = processor._normalize_ai_result(
            {
                "category": "Technology",
                "summary": "AI가 만든 요약",
                "tags": ["꽃꽂이", "플라워", "취미"],
            },
            "플라워 클래스",
            "꽃다발을 만들며 기본 플라워 디자인을 배웁니다",
            "성인",
        )

        self.assertEqual(result["tags"], ["꽃꽂이", "플라워", "취미"])
        self.assertEqual(result["category"], "Art")
        self.assertNotEqual(result["summary"], "AI가 만든 요약")
        self.assertIn("꽃다발", result["summary"])

    def test_summary_fallback_prefers_description_over_repeated_ascii_title(self) -> None:
        processor = AIProcessor()
        result = processor._fallback_result(
            "K-POP \ubc29\uc1a1\ub304\uc2a4 [2017~21\ub144\uc0dd]",
            "\ucd5c\uc2e0 K-POP \uc74c\uc545\uc5d0 \ub9de\ucdb0 \uae30\ubcf8 \ub9ac\ub4ec\uac10\uacfc \uc548\ubb34\ub97c \uc775\ud788\ub294 \uc5b4\ub9b0\uc774 \ubc29\uc1a1\ub304\uc2a4 \uac15\uc88c",
            "\ub304\uc2a4",
        )

        self.assertNotEqual(result["summary"], "K-POP \ubc29\uc1a1\ub304\uc2a4")
        self.assertNotIn("K-POP", result["summary"])
        self.assertIn("\ub9ac\ub4ec\uac10", result["summary"])

    def test_long_english_summary_uses_description_fallback(self) -> None:
        processor = AIProcessor()
        summary = processor._normalize_summary(
            "K-pop music dance for kids learning rhythm and choreography",
            "K-POP \ubc29\uc1a1\ub304\uc2a4 [2017~21\ub144\uc0dd]",
            "\ucd5c\uc2e0 K-POP \uc74c\uc545\uc5d0 \ub9de\ucdb0 \uae30\ubcf8 \ub9ac\ub4ec\uac10\uacfc \uc548\ubb34\ub97c \uc775\ud788\ub294 \uc5b4\ub9b0\uc774 \ubc29\uc1a1\ub304\uc2a4 \uac15\uc88c",
        )

        self.assertNotIn("K-POP", summary)
        self.assertIn("\ub9ac\ub4ec\uac10", summary)

    def test_operational_summary_noise_is_rejected(self) -> None:
        processor = AIProcessor()
        summary = processor._normalize_summary(
            "\uac00\ub4e05\uc810 (02-411-1250~2) \uc5c5\ubb34 : \ud3c9\uc77c/\uc8fc\ub9d0 ~ \u25c7",
            "\ub9b4\ub808\uc774 \ub3d9\ud654, \ub124 \ub4f1\uc5d0 \uc9d1 \uc9c0\uc5b4\ub3c4\ub418\ub2c8? \uc2a4\ud1a0\ub9ac\ud154\ub9c1 A *3\uac00\uc9c0 \ubaa8\ub450 \uc811\uc218",
            "\uac00\ub4e05\uc810 CULTURE CLUB (02-411-1250~2) \uc5c5\ubb34 \uc2dc\uac04 : \ud3c9\uc77c/\uc8fc\ub9d0 10:00~17:00 \u25c7 \uc218\uac15\ub8cc \ud658\ubd88 \uae30\uc900",
        )

        self.assertNotIn("02-411", summary)
        self.assertNotIn("\uc5c5\ubb34", summary)
        self.assertNotIn("\uc811\uc218", summary)
        self.assertIn("\ub9b4\ub808\uc774", summary)

    def test_broad_kids_category_without_target_does_not_preserve_child_age(self) -> None:
        processor = AIProcessor()
        result = processor._normalize_title_result(
            {
                "clean_title": "[\ucd94\uac00\uc811\uc218\uc778\uc6d0\uc6a9] \uc0ac\uc9c4\uc791\uac00\uac00 \ucc0d\uc5b4\uc8fc\ub294 \uc6b0\ub9ac \uac00\uc871 \uac10\uc131 \ud751\ubc31\uc0ac\uc9c4(B)(1\uc778\uae30\uc900,\uc778\ub2f9\uacb0\uc81c)",
                "confidence": 0.9,
            },
            {
                "provider": "EMART",
                "title": "[\ucd94\uac00\uc811\uc218\uc778\uc6d0\uc6a9] \uc0ac\uc9c4\uc791\uac00\uac00 \ucc0d\uc5b4\uc8fc\ub294 \uc6b0\ub9ac \uac00\uc871 \uac10\uc131 \ud751\ubc31\uc0ac\uc9c4(B)(1\uc778\uae30\uc900,\uc778\ub2f9\uacb0\uc81c)",
                "title_raw": "[\ucd94\uac00\uc811\uc218\uc778\uc6d0\uc6a9]6/21(\uc77c) 11:30\uc0ac\uc9c4\uc791\uac00\uac00 \ucc0d\uc5b4\uc8fc\ub294 \uc6b0\ub9ac \uac00\uc871 \uac10\uc131 \ud751\ubc31\uc0ac\uc9c4(B)(1\uc778\uae30\uc900,\uc778\ub2f9\uacb0\uc81c)",
                "target": None,
                "category_raw": "Kids & Children(event)",
                "target_age_group": "CHILD",
            },
            "\uc0ac\uc9c4\uc791\uac00\uac00 \ucc0d\uc5b4\uc8fc\ub294 \uc6b0\ub9ac \uac00\uc871 \uac10\uc131 \ud751\ubc31\uc0ac\uc9c4(B)(1\uc778\uae30\uc900,\uc778\ub2f9\uacb0\uc81c)",
            "",
            "",
        )

        self.assertEqual(result["title"], "\uc0ac\uc9c4\uc791\uac00\uac00 \ucc0d\uc5b4\uc8fc\ub294 \uc6b0\ub9ac \uac00\uc871 \uac10\uc131 \ud751\ubc31\uc0ac\uc9c4(B)")
        self.assertIsNone(result["target"])
        self.assertIsNone(result["target_age_group"])
        self.assertIsNone(result["target_min_age"])
        self.assertIsNone(result["target_max_age"])

    def test_adult_category_without_explicit_target_keeps_group_without_month_bounds(self) -> None:
        processor = AIProcessor()
        result = processor._normalize_title_result(
            {"clean_title": "\uc6d0\uc608\uc9c0\ub3c4\uc0ac 3\uae09 \uc790\uaca9\uc99d \uacfc\uc815", "confidence": 0.9},
            {
                "provider": "LOTTE",
                "title": "\uc6d0\uc608\uc9c0\ub3c4\uc0ac 3\uae09 \uc790\uaca9\uc99d \uacfc\uc815",
                "title_raw": "\uc77c\uc694 \uc6d0\uc608\uc9c0\ub3c4\uc0ac 3\uae09 \uc790\uaca9\uc99d \uacfc\uc815",
                "target": None,
                "category_raw": "ADULT",
                "target_age_group": "ADULT",
            },
            "\uc6d0\uc608\uc9c0\ub3c4\uc0ac 3\uae09 \uc790\uaca9\uc99d \uacfc\uc815",
            "",
            "",
        )

        self.assertEqual(result["target_age_group"], "ADULT")
        self.assertIsNone(result["target_min_age"])
        self.assertIsNone(result["target_max_age"])
        self.assertTrue(result["clear_target_age_bounds"])

    def test_explicit_crawler_target_wins_over_title_age(self) -> None:
        processor = AIProcessor()
        result = processor._normalize_title_result(
            {
                "clean_title": "K-POP \ubc29\uc1a1\ub304\uc2a4",
                "target_text": "2020~22\ub144\uc0dd",
                "confidence": 0.9,
            },
            {
                "provider": "TEST",
                "title": "K-POP \ubc29\uc1a1\ub304\uc2a4 [2020~22\ub144\uc0dd]",
                "title_raw": "K-POP \ubc29\uc1a1\ub304\uc2a4 [2020~22\ub144\uc0dd]",
                "target": "\uc131\uc778 \ub9cc 18\uc138 \uc774\uc0c1 \uc131\uc778",
                "category_raw": "",
                "schedule_raw": "",
            },
            "K-POP \ubc29\uc1a1\ub304\uc2a4",
            "\uc131\uc778 \ub9cc 18\uc138 \uc774\uc0c1 \uc131\uc778",
            "",
        )

        self.assertEqual(result["target"], "\uc131\uc778 \ub9cc 18\uc138 \uc774\uc0c1 \uc131\uc778")
        self.assertEqual(result["target_age_group"], "ADULT")
        self.assertEqual(result["target_min_age"], 216)
        self.assertIsNone(result["target_max_age"])
        self.assertEqual(result["ai_title_result"]["age_value_source"], "crawler_target")

    def test_explicit_crawler_age_flag_ignores_ai_age_values(self) -> None:
        processor = AIProcessor()
        result = processor._normalize_title_result(
            {
                "clean_title": "K-POP \ubc29\uc1a1\ub304\uc2a4",
                "target_text": "2020~22\ub144\uc0dd",
                "age_group": "CHILD",
                "min_age": 48,
                "max_age": 72,
                "confidence": 0.9,
            },
            {
                "provider": "HOMEPLUS",
                "title": "K-POP \ubc29\uc1a1\ub304\uc2a4",
                "title_raw": "K-POP \ubc29\uc1a1\ub304\uc2a4",
                "target": "\ub300\uc0c1 \ud655\uc778",
                "target_age_group": "TODDLER",
                "target_min_age": 24,
                "target_max_age": 48,
                "target_age_is_explicit": True,
                "category_raw": "",
                "schedule_raw": "",
            },
            "K-POP \ubc29\uc1a1\ub304\uc2a4",
            "",
            "",
        )

        self.assertIsNone(result["target"])
        self.assertEqual(result["target_age_group"], "TODDLER")
        self.assertEqual(result["target_min_age"], 24)
        self.assertEqual(result["target_max_age"], 48)
        self.assertFalse(result["ai_title_result"]["ai_age_used"])
        self.assertEqual(result["ai_title_result"]["age_value_source"], "crawler_explicit")

    def test_invalid_explicit_crawler_age_range_is_reparsed_from_target_text(self) -> None:
        processor = AIProcessor()
        result = processor.analyze_title(
            {
                "provider": "LOCAL",
                "title": "\uc5b4\ub974\uc2e0 \ub514\uc9c0\ud138 \ud2b8\ub808\uc774\ub2dd 7~9\uc6d4 \uacfc\uc815",
                "title_raw": "\u26059\uc2dc \uc811\uc218_\uc5b4\ub974\uc2e0 \ub514\uc9c0\ud138 \ud2b8\ub808\uc774\ub2dd(7~9\uc6d4,3\uac1c\uc6d4)",
                "target": "65\uc138 \uc774\uc0c1",
                "target_age_group": "SENIOR",
                "target_min_age": 780,
                "target_max_age": 3,
                "target_age_is_explicit": True,
                "target_tags": ["\uc720\uc544"],
                "category_raw": "",
                "schedule_raw": "",
            }
        )

        self.assertEqual(result["target"], "65\uc138 \uc774\uc0c1")
        self.assertEqual(result["target_age_group"], "SENIOR")
        self.assertEqual(result["target_min_age"], 780)
        self.assertIsNone(result["target_max_age"])
        self.assertEqual(result["target_tags"], ["\uc2dc\ub2c8\uc5b4"])
        self.assertEqual(result["ai_title_result"]["age_value_source"], "crawler_target")

    def test_year_age_suffix_is_removed_from_title_and_extracted(self) -> None:
        processor = AIProcessor()
        result = processor.analyze_title(
            {
                "provider": "HOMEPLUS",
                "title": "\uc2dc\ub2c8\uc5b4 \ud328\uc158\ubaa8\ub378 \uc6cc\ud0b9\uae30\ucd08 \ucde8\ubbf8\ubc18 40\uc138\uc774\uc0c1",
                "title_raw": "\uc2dc\ub2c8\uc5b4 \ud328\uc158\ubaa8\ub378 \uc6cc\ud0b9\uae30\ucd08 \ucde8\ubbf8\ubc18 40\uc138\uc774\uc0c1",
                "target": None,
                "category_raw": "ADULT",
                "schedule_raw": "",
            }
        )

        self.assertEqual(result["title"], "\uc2dc\ub2c8\uc5b4 \ud328\uc158\ubaa8\ub378 \uc6cc\ud0b9\uae30\ucd08 \ucde8\ubbf8\ubc18")
        self.assertEqual(result["target"], "40\uc138\uc774\uc0c1")
        self.assertEqual(result["target_age_group"], "ADULT")
        self.assertEqual(result["target_min_age"], 480)
        self.assertIsNone(result["target_max_age"])

    def test_lotte_month_round_prefix_is_removed_from_title(self) -> None:
        processor = AIProcessor()
        result = processor.analyze_title(
            {
                "provider": "LOTTE",
                "title": "[6\u6708-4\uac15] \uc790\uc5f0\uc774 \uc8fc\ub294 \uae30\uc068, \uc720\ub7ec\ud53c\uc548 \ud50c\ub77c\uc6cc \ud074\ub798\uc2a4",
                "title_raw": "[6\u6708-4\uac15] \uc790\uc5f0\uc774 \uc8fc\ub294 \uae30\uc068, \uc720\ub7ec\ud53c\uc548 \ud50c\ub77c\uc6cc \ud074\ub798\uc2a4",
                "target": None,
                "category_raw": "ADULT",
                "schedule_raw": "",
            }
        )

        self.assertEqual(result["title"], "\uc790\uc5f0\uc774 \uc8fc\ub294 \uae30\uc068, \uc720\ub7ec\ud53c\uc548 \ud50c\ub77c\uc6cc \ud074\ub798\uc2a4")

    def test_week_count_and_only_prefixes_are_removed_from_title(self) -> None:
        processor = AIProcessor()
        result = processor.analyze_title(
            {
                "provider": "LOTTE",
                "title": "[10\uc8fc][Only] \ud37c\ud3ec\uba3c\uc2a4 \ubbf8\uc220\ud30c\ud2f0 \uc0dd\ud06c\ub9bc ~\ub9cc",
                "title_raw": "[10\uc8fc][Only] \ud37c\ud3ec\uba3c\uc2a4 \ubbf8\uc220\ud30c\ud2f0 \uc0dd\ud06c\ub9bc ~\ub9cc",
                "target": "\uc720\uc544\uac15\uc88c",
                "category_raw": "\uc720\uc544",
                "schedule_raw": "",
            }
        )

        self.assertEqual(result["title"], "\ud37c\ud3ec\uba3c\uc2a4 \ubbf8\uc220\ud30c\ud2f0 \uc0dd\ud06c\ub9bc")
        self.assertNotIn("[10\uc8fc]", result["title"])
        self.assertNotIn("Only", result["title"])

    def test_plain_week_count_and_pipe_schedule_suffix_are_removed_from_title(self) -> None:
        processor = AIProcessor()

        week_result = processor.analyze_title(
            {
                "provider": "LOTTE",
                "title": "8\uc8fc \ud587\uc0b4\uc544\uc774 \uc624\uac10\ub098\ubb34",
                "title_raw": "8\uc8fc \ud587\uc0b4\uc544\uc774 \uc624\uac10\ub098\ubb34",
                "target": "\uc601\uc544\uac15\uc88c",
                "category_raw": "\uc601\uc544",
                "schedule_raw": "",
            }
        )
        self.assertEqual(week_result["title"], "\ud587\uc0b4\uc544\uc774 \uc624\uac10\ub098\ubb34")

        schedule_result = processor.analyze_title(
            {
                "provider": "LOTTE",
                "title": "[7\uc6d4\uac1c\uac15] 1:1 \uac00\uc57c\uae08|\uae08 15:30|\ub9cc",
                "title_raw": "[7\uc6d4\uac1c\uac15] 1:1 \uac00\uc57c\uae08|\uae08 15:30|\ub9cc",
                "target": "\uc131\uc778\uac15\uc88c",
                "category_raw": "\uc131\uc778",
                "schedule_raw": "\uae08 15:30",
            }
        )
        self.assertEqual(schedule_result["title"], "1:1 \uac00\uc57c\uae08")

    def test_decorated_week_oneday_and_notice_suffixes_are_removed_from_title(self) -> None:
        processor = AIProcessor()

        decorated_result = processor.analyze_title(
            {
                "provider": "LOTTE",
                "title": "\u26658\uc8fc \uc18c\ub3c4\uad6c\ub97c \ud65c\uc6a9\ud55c \ud544\ub77c\ucf54\uc5b4 \uc694\uac00",
                "title_raw": "\u26658\uc8fc \uc18c\ub3c4\uad6c\ub97c \ud65c\uc6a9\ud55c \ud544\ub77c\ucf54\uc5b4 \uc694\uac00",
                "target": "\uc131\uc778\uac15\uc88c",
                "category_raw": "\uc131\uc778",
                "schedule_raw": "",
            }
        )
        self.assertEqual(decorated_result["title"], "\uc18c\ub3c4\uad6c\ub97c \ud65c\uc6a9\ud55c \ud544\ub77c\ucf54\uc5b4 \uc694\uac00")

        oneday_result = processor.analyze_title(
            {
                "provider": "LOTTE",
                "title": "[1DAY] \uc774\ud654 YSM \uc5c4\ub9c8(\uc544\ube60)\ub791 \ubc1c\ub808 \uccb4\ud5d8",
                "title_raw": "[1DAY] \uc774\ud654 YSM \uc5c4\ub9c8(\uc544\ube60)\ub791 \ubc1c\ub808 \uccb4\ud5d8",
                "target": "\uc601\uc544\uac15\uc88c",
                "category_raw": "\uc601\uc544",
                "schedule_raw": "",
            }
        )
        self.assertEqual(oneday_result["title"], "\uc774\ud654 YSM \uc5c4\ub9c8(\uc544\ube60)\ub791 \ubc1c\ub808 \uccb4\ud5d8")

        notice_result = processor.analyze_title(
            {
                "provider": "LOTTE",
                "title": "\ub450\ub1cc \uc2a4\ud3ec\uce20 \uc5b4\ub9b0\uc774 \uccb4\uc2a4 \u203b \ud734\uac15",
                "title_raw": "\ub450\ub1cc \uc2a4\ud3ec\uce20 \uc5b4\ub9b0\uc774 \uccb4\uc2a4 \u203b \ud734\uac15",
                "target": "\uc720\uc544\uac15\uc88c",
                "category_raw": "\uc720\uc544",
                "schedule_raw": "",
            }
        )
        self.assertEqual(notice_result["title"], "\ub450\ub1cc \uc2a4\ud3ec\uce20 \uc5b4\ub9b0\uc774 \uccb4\uc2a4")

    def test_operational_prefixes_and_notice_suffixes_are_removed_from_title(self) -> None:
        samples = {
            "[11\uc8fc][ONLY] \uc2e0\uae30\ud55c \uc624\uac10 \uc810\ud504\ubca0\ubca0": "\uc2e0\uae30\ud55c \uc624\uac10 \uc810\ud504\ubca0\ubca0",
            "\uac1c\uac15\ud655\uc815 9\uc8fc 1:5 \uc6b0\ub9ac\uc758 \uc18c\ub9ac \uac00\uc57c\uae08": "1:5 \uc6b0\ub9ac\uc758 \uc18c\ub9ac \uac00\uc57c\uae08",
            "[20\ud68c] \ud558\ub8e8\uc758 \uc27c\ud45c, \uc800\ub141 \uc694\uac00& \ud544\ub77c\ud14c\uc2a4": "\ud558\ub8e8\uc758 \uc27c\ud45c, \uc800\ub141 \uc694\uac00& \ud544\ub77c\ud14c\uc2a4",
            "[\uc815\uaddc\ud2b9\uac15] \uad7f\ub098\uc787 \uccb4\uc9c0\ubc29 \ubd84\ud574 \ub2e4\uc774\uc5b4\ud2b8 \uc90c\ubc14\ub304\uc2a4": "\uad7f\ub098\uc787 \uccb4\uc9c0\ubc29 \ubd84\ud574 \ub2e4\uc774\uc5b4\ud2b8 \uc90c\ubc14\ub304\uc2a4",
            "[6\uc6d4\ub2e8\uae30\uac15\uc88c]\ucc3d\uc758\ub825 \ud321\ud321 \ub808\uace0 \uad50\uc2e4(\ud14c\ud06c\uba38\uc2e0)": "\ucc3d\uc758\ub825 \ud321\ud321 \ub808\uace0 \uad50\uc2e4(\ud14c\ud06c\uba38\uc2e0)",
            "[8\uc8fc/\uc218]5060\uc744 \uc704\ud55c \uadfc\ub825\uac15\ud654, \ubc38\ub7f0\uc2a4 \uc2a4\ud2b8\ub808\uce6d & \ubc1c\ub808(\uc2a4\ud2b8\ub808\uce6d \uc911\uc2ec)": "5060\uc744 \uc704\ud55c \uadfc\ub825\uac15\ud654, \ubc38\ub7f0\uc2a4 \uc2a4\ud2b8\ub808\uce6d & \ubc1c\ub808(\uc2a4\ud2b8\ub808\uce6d \uc911\uc2ec)",
            "60\ubd84 \uc9d1\uc911! SNPE \ubc14\ub978\uc790\uc138 \ucc99\ucd94\uc6b4\ub3d9 \u203b\ub3c4\uad6c\ub300\uc5ec\ube44 \ubb34\ub8cc": "60\ubd84 \uc9d1\uc911! SNPE \ubc14\ub978\uc790\uc138 \ucc99\ucd94\uc6b4\ub3d9",
            "\uc9d1\uc911\ub825\uc744 \ud0a4\uc6cc\uc8fc\ub294 \uae00\ub80c\ub3c4\ub9cc (\ud734\uac15)": "\uc9d1\uc911\ub825\uc744 \ud0a4\uc6cc\uc8fc\ub294 \uae00\ub80c\ub3c4\ub9cc",
            "\u25ce (\uc2dc\uc791) \uc800\ub141 \uce7c\ub85c\ub9ac \uc18c\ubaa8, \uadfc\ub825 \uc694\uac00": "\uc800\ub141 \uce7c\ub85c\ub9ac \uc18c\ubaa8, \uadfc\ub825 \uc694\uac00",
            "\ubbfc\uacbd\uc0d8\uc758 \ubbf8\uc988 \ub86f\ub370\ub178\ub798\uad50\uc2e4 \u203b ~": "\ubbfc\uacbd\uc0d8\uc758 \ubbf8\uc988 \ub86f\ub370\ub178\ub798\uad50\uc2e4",
            "[\uc2e0\uc124/8\uc8fc] \ubc14\ub514 \ubc38\ub7f0\uc2a4 \uc800\ub141 \uc694\uac00 (\uc8fc2\ud68c/\ud654,\ubaa9) \u203b ~": "\ubc14\ub514 \ubc38\ub7f0\uc2a4 \uc800\ub141 \uc694\uac00 (\uc8fc2\ud68c/\ud654,\ubaa9)",
            "[8\uc8fc/\uc2e0\uc124] \uc800\ub141 8\uc2dc ZUMBA \uc90c\ubc14 \ub304\uc2a4 \u203b ~": "\uc800\ub141 8\uc2dc ZUMBA \uc90c\ubc14 \ub304\uc2a4",
            "[23\ud68c \uc218\uac15] \uc5d0\ub108\uc9c0 \uc5c5 \uc154\ud50c\ub304\uc2a4 (\ud654/\uae08)": "\uc5d0\ub108\uc9c0 \uc5c5 \uc154\ud50c\ub304\uc2a4 (\ud654/\uae08)",
            "8\uc8fc] \uc0dd\ud65c \uaf43\uaf42\uc774_ \ud734\uac15": "\uc0dd\ud65c \uaf43\uaf42\uc774",
            "8\uc8fc] \uce98\ub9ac\uadf8\ub77c\ud53c / \uba39\uc77c\ub7ec\uc2a4\ud2b8": "\uce98\ub9ac\uadf8\ub77c\ud53c / \uba39\uc77c\ub7ec\uc2a4\ud2b8",
            "\ud2b8\ub2c8\ud2b8\ub2c8 (~)": "\ud2b8\ub2c8\ud2b8\ub2c8",
            "\uc9c0\uc824 \ubc1c\ub808\uc2a4\ucfe8 4~ (~)": "\uc9c0\uc824 \ubc1c\ub808\uc2a4\ucfe8",
            "11\uc8fc\u2605 \uc2a4\ud398\uc15c \uc790\uc5f0 \uc624\uac10\ub180\uc774 \ubf40\uc791\ubf40\uc791": "\uc2a4\ud398\uc15c \uc790\uc5f0 \uc624\uac10\ub180\uc774 \ubf40\uc791\ubf40\uc791",
            "8\uc8fc\u2605 \ud1a0\ub9ac\ud1a0\ub9ac \uc624\uac10\ub180\uc774": "\ud1a0\ub9ac\ud1a0\ub9ac \uc624\uac10\ub180\uc774",
            "(\uc911\ub3c4)\uac10\uc131\ub180\uc774\ud130 \uc624\uac10\ub300\uc7a5": "\uac10\uc131\ub180\uc774\ud130 \uc624\uac10\ub300\uc7a5",
            "[8\uc8fc\uc911\ub3c4] \uc774\uc218\uc724\uc758 \ub9c8\uc77c\ub4dc&\ube48\uc57c\uc0ac \uc694\uac00": "\uc774\uc218\uc724\uc758 \ub9c8\uc77c\ub4dc&\ube48\uc57c\uc0ac \uc694\uac00",
        }

        processor = AIProcessor()
        for raw_title, expected in samples.items():
            with self.subTest(raw_title=raw_title):
                result = processor.analyze_title(
                    {
                        "provider": "LOTTE",
                        "title": raw_title,
                        "title_raw": raw_title,
                        "target": "\uc131\uc778\uac15\uc88c",
                        "category_raw": "\uc131\uc778",
                        "schedule_raw": "",
                    }
                )
                self.assertEqual(result["title"], expected)

    def test_summary_http_error_is_not_saved_as_fallback_success(self) -> None:
        processor = AIProcessor()

        def fail_call(*_args, **_kwargs):
            raise requests.HTTPError("404 model not found")

        processor._call_ollama = fail_call
        result = processor.analyze_course(
            "\ud50c\ub77c\uc6cc \ud074\ub798\uc2a4",
            "\uaf43\ub2e4\ubc1c\uc744 \ub9cc\ub4e4\uba70 \uae30\ubcf8 \ud50c\ub77c\uc6cc \ub514\uc790\uc778\uc744 \ubc30\uc6c1\ub2c8\ub2e4",
            "\uc131\uc778",
        )

        self.assertIsNone(result)

    def test_korean_instrument_keywords_are_music(self) -> None:
        self.assertEqual(
            infer_category("\uace0\uace0\uc7a5\uad6c(\uc911\uae09)", "", ["\uace0\uace0\uc7a5\uad6c"]),
            "Music",
        )
        self.assertEqual(
            infer_category("\ud1b5\uae30\ud0c0\uad50\uc2e4(\uc911\uae09)", "", ["\ud1b5\uae30\ud0c0"]),
            "Music",
        )


if __name__ == "__main__":
    unittest.main()
