from Crawler.Crawler_Homeplus import HomeplusCrawler
from ai_processor import AIProcessor
from data_parser import parse_crawler_target
from target_cleaner import extract_target_text, normalize_target_text
from title_cleaner import clean_course_title


def test_homeplus_bracket_adult_label_is_target_age_group():
    label = "[Adult] 6/29(\uc6d4) \uac1c\uac15"

    target = extract_target_text(label)
    assert target == "\uc131\uc778"
    assert normalize_target_text(label) == "\uc131\uc778"

    parsed = parse_crawler_target(target)
    assert parsed["age_group"] == "ADULT"
    assert parsed["min_age"] is None
    assert parsed["max_age"] is None
    assert parsed["age_is_explicit"] is False


def test_homeplus_bracket_label_is_removed_from_clean_title():
    title, removed = clean_course_title("[Adult] 6/29(\uc6d4) \uac1c\uac15 \ud504\ub9ac\ubbf8\uc5c4 \uc694\uac00")

    assert title == "\ud504\ub9ac\ubbf8\uc5c4 \uc694\uac00"
    assert "[Adult]" in removed


def test_homeplus_crawler_uses_title_one_age_label_as_target():
    crawler = HomeplusCrawler(use_selenium=False)

    assert crawler._extract_target_from_text("[Adult] 6/29(\uc6d4) \uac1c\uac15") == "\uc131\uc778"
    assert crawler._clean_target_value("[Adult] 6/29(\uc6d4) \uac1c\uac15") == "\uc131\uc778"


def test_homeplus_ai_title_rules_extract_adult_label_without_default_month_bounds():
    result = AIProcessor().analyze_title(
        {
            "provider": "HOMEPLUS",
            "title": "[Adult] 6/29(\uc6d4) \uac1c\uac15 \ud504\ub9ac\ubbf8\uc5c4 \uc694\uac00",
            "title_raw": "[Adult] 6/29(\uc6d4) \uac1c\uac15 \ud504\ub9ac\ubbf8\uc5c4 \uc694\uac00",
            "target": None,
            "category_raw": "Adult",
            "schedule_raw": "",
        }
    )

    assert result["title"] == "\ud504\ub9ac\ubbf8\uc5c4 \uc694\uac00"
    assert result["target"] == "\uc131\uc778"
    assert result["target_age_group"] == "ADULT"
    assert result["target_min_age"] is None
    assert result["target_max_age"] is None
