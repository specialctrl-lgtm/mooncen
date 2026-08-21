from __future__ import annotations

from pathlib import Path

import pytest

from DB.course_upsert_guards import (
    coalesce_provider_course_id_by_raw_url,
    coalesce_provider_course_ids_by_raw_url,
)
from utils.course_semantic_eligibility import (
    CourseSemanticEligibilityError,
    course_semantic_eligibility_decision,
    guard_course_before_upsert,
)
from utils.generic_course_eligibility import generic_course_row_decision


ROOT = Path(__file__).resolve().parents[1]


def course_row(**overrides):
    row = {
        "provider": "TEST_PROVIDER",
        "provider_course_id": "COURSE_1",
        "title": "어린이 도자기 만들기 교실",
        "schedule_raw": "2026-08-01 ~ 2026-08-31 매주 토요일 10:00",
        "status": "접수중",
        "raw_url": "https://example.go.kr/education/program/detail?id=1",
        "application_url": "https://example.go.kr/education/program/apply?id=1",
        "raw_fields": {
            "source_url": "https://example.go.kr/education/program/list",
        },
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("기간제 직원 채용 공고", "non_learner_recruitment"),
        ("청사 청소용역 입찰 공고", "procurement_or_contract_notice"),
        ("수강생 모집 결과 발표", "result_announcement"),
        ("교육 프로그램 자주 묻는 질문 FAQ", "faq_or_general_information"),
        ("어린이 교육 관련 보도자료", "press_or_news_article"),
        ("[공지] 홈페이지 시스템 점검", "operational_notice"),
    ],
)
def test_editorial_rows_are_rejected_even_with_course_fields(
    title: str, reason: str
) -> None:
    decision = course_semantic_eligibility_decision(course_row(title=title))

    assert decision.eligible is False
    assert decision.reason == reason


def test_notice_shaped_course_requires_schedule_and_application_evidence() -> None:
    row = course_row(
        title="[공지] 어린이 미술 교실 모집 안내",
        schedule_raw="",
        application_url="",
        status="접수중",
        raw_url="https://example.go.kr/notice/detail?id=7",
    )

    eligible, reason = generic_course_row_decision(row)

    assert eligible is False
    assert reason == "editorial_article_url"


def test_notice_shaped_real_course_is_allowed_with_schedule_and_application() -> None:
    row = course_row(
        title="[공지] 어린이 미술 교실 모집 안내",
        apply_period_raw="2026-07-01 ~ 2026-07-20",
        raw_url="https://example.go.kr/notice/detail?id=7",
    )

    eligible, reason = generic_course_row_decision(row)

    assert eligible is True
    assert reason == "notice_course_with_schedule_and_application_evidence"


def test_title_and_detail_status_without_schedule_or_application_is_rejected() -> None:
    row = course_row(
        schedule_raw="",
        application_url="",
        target="",
        venue_name="",
        capacity_total=None,
        instructor="",
        fee=None,
    )

    decision = course_semantic_eligibility_decision(row)

    assert decision.eligible is False
    assert decision.reason == "insufficient_course_registration_evidence"


def test_upsert_guard_records_reason_metadata_for_accepted_row() -> None:
    row = course_row()

    decision = guard_course_before_upsert(row)

    assert decision.eligible is True
    assert row["semantic_eligibility_reason"] == decision.reason
    assert row["raw_fields"]["semantic_eligibility"] == {
        "policy": "course_registration_v1",
        "eligible": True,
        "reason": decision.reason,
        "evidence": list(decision.evidence),
    }


def test_single_upsert_guard_rejects_before_opening_identity_sql() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.executed = []

        def execute(self, sql, params=None) -> None:
            self.executed.append((sql, params))

    cursor = Cursor()
    row = course_row(
        title="기관 채용 공고",
        schedule_raw="",
        application_url="",
        status="",
    )

    with pytest.raises(CourseSemanticEligibilityError) as error:
        coalesce_provider_course_id_by_raw_url(cursor, row)

    assert error.value.reason == "non_learner_recruitment"
    assert cursor.executed == []
    assert row["raw_fields"]["semantic_eligibility"]["eligible"] is False


def test_batch_upsert_guard_rejects_entire_batch_before_sql() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.executed = []

        def execute(self, sql, params=None) -> None:
            self.executed.append((sql, params))

    cursor = Cursor()
    execute_values_calls = []
    rows = [course_row(), course_row(provider_course_id="FAQ", title="강좌 FAQ")]

    with pytest.raises(CourseSemanticEligibilityError) as error:
        coalesce_provider_course_ids_by_raw_url(
            cursor,
            rows,
            execute_values_fn=lambda *args, **kwargs: execute_values_calls.append(
                (args, kwargs)
            ),
        )

    assert error.value.reason == "faq_or_general_information"
    assert cursor.executed == []
    assert execute_values_calls == []


@pytest.mark.parametrize(
    ("relative_path", "guard_marker"),
    [
        ("Crawler/Crawler_Emart.py", "guard_course_before_upsert("),
        ("Crawler/Crawler_Homeplus.py", "guard_course_before_upsert("),
        ("Crawler/Crawler_Lotte.py", "guard_course_before_upsert("),
        ("Crawler/Crawler_MunicipalYaml.py", "coalesce_provider_course_id_by_raw_url("),
        ("Crawler/Crawler_SeongnamBaeumsoop.py", "guard_course_before_upsert("),
        ("Crawler/Crawler_YamlSources.py", "coalesce_provider_course_id_by_raw_url("),
        (
            "Crawler/generated_yaml/manual_generic_crawler.py",
            "coalesce_provider_course_id_by_raw_url(",
        ),
        ("tools/apply_staging_batch.py", "coalesce_provider_course_ids_by_raw_url("),
    ],
)
def test_every_course_insert_adapter_uses_common_upsert_guard(
    relative_path: str, guard_marker: str
) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert "INSERT INTO courses" in source
    assert guard_marker in source


@pytest.mark.parametrize(
    "relative_path",
    [
        "Crawler/Crawler_Emart.py",
        "Crawler/Crawler_Homeplus.py",
        "Crawler/Crawler_Lotte.py",
        "Crawler/Crawler_MunicipalYaml.py",
        "Crawler/Crawler_SeongnamBaeumsoop.py",
        "Crawler/Crawler_YamlSources.py",
    ],
)
def test_row_rejection_is_not_promoted_to_provider_hard_failure(
    relative_path: str,
) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert "CourseSemanticEligibilityError" in source
    assert "Rejected non-course" in source
