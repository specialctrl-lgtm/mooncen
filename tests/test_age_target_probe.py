from data_parser import explicit_age_month_range, parse_crawler_target
from tools.parser_probe import _add_age_fields


def test_mixed_month_to_korean_age_range_is_month_bounds():
    text = "\uc720\uc544 \u00b7 24\uac1c\uc6d4~\ub9cc 4\uc138"

    assert explicit_age_month_range(text) == (24, 59)

    parsed = parse_crawler_target(text)
    assert parsed["age_is_explicit"] is True
    assert parsed["age_group"] == "TODDLER"
    assert parsed["min_age"] == 24
    assert parsed["max_age"] == 59


def test_inverted_source_month_range_is_normalized_before_persistence():
    text = "16~12개월, 보호자 1인"

    assert explicit_age_month_range(text) == (12, 16)

    parsed = parse_crawler_target(text)
    assert parsed["min_age"] == 12
    assert parsed["max_age"] == 16
    assert parsed["age_is_explicit"] is True


def test_parser_probe_adds_normalized_age_fields():
    fields = {"target": "\uc720\uc544 \u00b7 24\uac1c\uc6d4~\ub9cc 4\uc138"}
    sources = {"target": "unit-test"}

    _add_age_fields(fields, sources)

    assert fields["target_age_source"] == "\uc720\uc544 \u00b7 24\uac1c\uc6d4~\ub9cc 4\uc138"
    assert fields["target_age_display"] == "24~59\uac1c\uc6d4"
    assert fields["target_age_group"] == "TODDLER"
    assert fields["target_min_age"] == 24
    assert fields["target_max_age"] == 59
    assert fields["target_age_is_explicit"] is True
    assert sources["target_age_display"].startswith("data_parser.parse_crawler_target")


def test_parser_probe_does_not_duplicate_age_raw_in_age_source():
    fields = {"target": "\uc720\uc544\uac15\uc88c / 4~6\uc138", "age_raw": "4~6\uc138"}
    sources = {"target": "target-source", "age_raw": "age-source"}

    _add_age_fields(fields, sources)

    assert fields["target_age_source"] == "\uc720\uc544\uac15\uc88c / 4~6\uc138"
    assert fields["target_age_display"] == "48~83\uac1c\uc6d4"
