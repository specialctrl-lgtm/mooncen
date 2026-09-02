from __future__ import annotations

import ast
import re
from pathlib import Path

from bs4 import BeautifulSoup

from Crawler.Crawler_MunicipalYaml import (
    CrawlTarget,
    INJE_LIFELONG_DEFAULT_ADDRESS,
    NATIONAL_LIGHTHOUSE_ADDRESS,
    NATIONAL_LIGHTHOUSE_BRANCH,
    NIHC_ADDRESS,
    SEJONG_LIFELONG_ADDRESS,
    ice_library_branch_name,
    inje_lifelong_kv_from_list_text,
    lighthouse_label,
    yjlib_normalize_branch,
)


SOURCE_PATH = Path(__file__).resolve().parents[1] / "Crawler" / "Crawler_MunicipalYaml.py"


def _string_constants() -> list[tuple[int, str]]:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"), filename=str(SOURCE_PATH))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_municipal_yaml_has_no_mojibake_codepoints() -> None:
    findings: list[str] = []
    for line, value in _string_constants():
        if any(
            0x3400 <= ord(char) <= 0x9FFF
            or 0xF900 <= ord(char) <= 0xFAFF
            or 0xE000 <= ord(char) <= 0xF8FF
            or ord(char) == 0xFFFD
            for char in value
        ):
            findings.append(f"{line}: {value!r}")

    assert findings == []


def test_municipal_yaml_has_no_question_mark_mojibake_prefixes() -> None:
    findings = [
        f"{line}: {value!r}"
        for line, value in _string_constants()
        if re.search(r"(?<![()\\])\?[가-힣]", value)
    ]

    assert findings == []


def test_repaired_branch_and_label_helpers_use_korean_source_labels() -> None:
    target = CrawlTarget(
        provider="TEST",
        name="테스트",
        branch="기본도서관",
        url="https://example.test",
        source="test",
    )
    soup = BeautifulSoup(
        "<title>인천광역시교육청 연수도서관 &gt; 평생교육</title>",
        "html.parser",
    )

    assert ice_library_branch_name(soup, target) == "연수도서관"
    assert yjlib_normalize_branch("여주") == "여주도서관"
    assert yjlib_normalize_branch("", venue_name="북내작은도서관 강의실") == "북내작은도서관"
    assert lighthouse_label("교육 일시：") == "교육일시"


def test_inje_list_fields_stop_at_the_next_korean_label() -> None:
    text = (
        "교육기간: 2026. 7. 1. ~ 2026. 7. 31. "
        "신청기간: 2026. 6. 1. ~ 2026. 6. 20. "
        "교육대상: 인제군민 모집인원: 20명"
    )

    assert inje_lifelong_kv_from_list_text(text, "교육기간") == "2026. 7. 1. ~ 2026. 7. 31."
    assert inje_lifelong_kv_from_list_text(text, "신청기간") == "2026. 6. 1. ~ 2026. 6. 20."
    assert inje_lifelong_kv_from_list_text(text, "교육대상") == "인제군민"


def test_repaired_public_facility_constants_are_readable_korean() -> None:
    assert INJE_LIFELONG_DEFAULT_ADDRESS.startswith("강원특별자치도 인제군")
    assert NIHC_ADDRESS == "전북특별자치도 전주시 완산구 서학로 95(동서학동 896-1)"
    assert NATIONAL_LIGHTHOUSE_BRANCH == "국립등대박물관"
    assert NATIONAL_LIGHTHOUSE_ADDRESS.startswith("경상북도 포항시 남구 호미곶면")
    assert SEJONG_LIFELONG_ADDRESS == "세종특별자치시 산울3로 124"
