from __future__ import annotations

import unittest

from tools.standard_category_mapper import classify_standard_category, looks_corrupted_category
from bs4 import BeautifulSoup

from tools.sample_collect_from_yaml import lotte_mart_detail_category, lotte_mart_detail_category_from_soup


class StandardCategoryMapperTests(unittest.TestCase):
    CULTURE_CONFIG = "config/culture_center_standard_categories.yaml"

    def test_category_corruption_detection_preserves_legitimate_korean_labels(self) -> None:
        self.assertTrue(looks_corrupted_category("??깃문??덈뮸"))
        self.assertTrue(looks_corrupted_category("????"))
        self.assertTrue(looks_corrupted_category("�손상"))
        self.assertTrue(looks_corrupted_category("占쏙옙"))
        self.assertFalse(looks_corrupted_category("역사·전통문화"))
        self.assertFalse(looks_corrupted_category("모두愛학교"))
        self.assertFalse(looks_corrupted_category("책과 책읽기"))
        self.assertFalse(looks_corrupted_category("챔피언 책읽기"))

    def test_source_only_category_does_not_become_final_category(self) -> None:
        result = classify_standard_category(
            {
                "title": "정규 강좌",
                "category_raw": "정규",
                "collection_category": "문화센터",
                "domain_category": "문화센터",
            }
        )

        self.assertEqual(result.key, "uncategorized")

    def test_emart_with_mom_maps_to_infant_play(self) -> None:
        result = classify_standard_category(
            {
                "title": "화요 트니트니 20~30개월",
                "category_raw": "With Mom",
                "collection_category": "문화센터",
            }
        )

        self.assertEqual(result.key, "infant_play")

    def test_lotte_adult_title_uses_title_topic(self) -> None:
        result = classify_standard_category(
            {
                "title": "유러피언 플라워 스타일링",
                "category_raw": "ADULT",
                "collection_category": "문화센터",
            }
        )

        self.assertEqual(result.key, "art_craft")

    def test_public_it_category_maps_to_digital(self) -> None:
        result = classify_standard_category(
            {
                "title": "구민 정보화교육 스마트폰 사용",
                "category_raw": "구민정보화교육",
                "collection_category": "체육/스포츠",
            }
        )

        self.assertEqual(result.key, "digital_it")

    def test_child_music_title_prefers_subject_over_age_like_text(self) -> None:
        result = classify_standard_category(
            {
                "title": "어린이 바이올린 기초",
                "category_raw": "Kids & Children",
                "collection_category": "문화센터",
            }
        )

        self.assertEqual(result.key, "music_performance")

    def test_short_english_keyword_does_not_match_inside_word(self) -> None:
        result = classify_standard_category(
            {
                "title": "[뷰티] 메이크업 1:1",
                "category_raw": "뷰티/바디",
                "description": "makeup lesson",
            }
        )

        self.assertEqual(result.key, "health_life")

    def test_current_lifelong_learning_examples_map_to_topics(self) -> None:
        examples = {
            "바둑(초급)": "hobby_leisure",
            "재미있는 당구(초급)": "sports_fitness",
            "수제샌드위치&샐러드": "cooking_food",
            "오카리나(중급)": "music_performance",
            "연필화(1반)": "art_craft",
            "미용사(초급)": "career_license",
        }
        for title, expected_key in examples.items():
            with self.subTest(title=title):
                result = classify_standard_category(
                    {
                        "title": title,
                        "category_raw": "안양 평생학습",
                        "collection_category": "평생학습",
                    }
                )
                self.assertEqual(result.key, expected_key)

    def test_culture_center_source_only_values_stay_uncategorized(self) -> None:
        for raw in ("정규", "ADULT", "Kids & Children"):
            with self.subTest(raw=raw):
                result = classify_standard_category(
                    {
                        "title": "정규 강좌",
                        "category_raw": raw,
                        "collection_category": "문화센터",
                        "domain_category": "문화센터",
                    },
                    self.CULTURE_CONFIG,
                )
                self.assertEqual(result.key, "uncategorized")

    def test_culture_center_menu_can_support_subject_when_specific(self) -> None:
        result = classify_standard_category(
            {
                "title": "발레 스트레칭",
                "category_raw": "Dance & Exercise",
                "collection_category": "문화센터",
            },
            self.CULTURE_CONFIG,
        )

        self.assertEqual(result.key, "dance_fitness")

    def test_culture_center_child_music_prefers_subject(self) -> None:
        result = classify_standard_category(
            {
                "title": "어린이 바이올린 기초",
                "category_raw": "Kids & Children",
                "collection_category": "문화센터",
            },
            self.CULTURE_CONFIG,
        )

        self.assertEqual(result.key, "music_instrument")

    def test_culture_center_strong_subject_beats_weak_age_menu(self) -> None:
        result = classify_standard_category(
            {
                "title": "[10회] 토요 바둑",
                "category_raw": "Kids & Children",
                "description": "With Mom 메뉴 노출 텍스트",
                "collection_category": "문화센터",
            },
            self.CULTURE_CONFIG,
        )

        self.assertEqual(result.key, "hobby_leisure")

    def test_current_culture_uncategorized_examples_map_to_topics(self) -> None:
        examples = {
            "A*CLASS 제철 보양식과 와인 클래스": "cooking_food",
            "엄마랑 브레인팝 오감놀이": "infant_play",
            "1:1 퍼스널 네일 스타일링": "health_life",
            "초등 주산수리셈 사고력 수학": "science_creative",
            "PilaFit Balance Training": "sports_fitness",
        }
        for title, expected_key in examples.items():
            with self.subTest(title=title):
                result = classify_standard_category(
                    {
                        "title": title,
                        "category_raw": "성인",
                        "collection_category": "문화센터",
                    }
                )
                self.assertEqual(result.key, expected_key)

    def test_lotte_mart_detail_header_becomes_raw_category(self) -> None:
        category = lotte_mart_detail_category(
            [
                "강좌상세",
                "[신갈점] 유아강좌 미술퍼포먼스",
                "아트 클래스",
                "접수/수강료",
            ]
        )

        self.assertEqual(category, "유아강좌 미술퍼포먼스")

    def test_lotte_mart_standalone_source_category_becomes_raw_category(self) -> None:
        category = lotte_mart_detail_category(
            [
                "강좌상세",
                "성인강좌",
                "수강신청",
            ]
        )

        self.assertEqual(category, "성인강좌")

    def test_lotte_mart_depth_dom_becomes_raw_category(self) -> None:
        soup = BeautifulSoup(
            """
            <span class="lct-depth">
                유아강좌
                <span class="txt-bar"></span>미술퍼포먼스
            </span>
            """,
            "lxml",
        )

        self.assertEqual(lotte_mart_detail_category_from_soup(soup), "유아강좌 > 미술퍼포먼스")


if __name__ == "__main__":
    unittest.main()
