from sqlalchemy.dialects import postgresql

from backend.models import Course
from backend.routers.courses import (
    _course_experience_scope_filter,
    _is_culture_center_course,
    _course_locked_public_education_scope_filter,
    _course_local_government_education_scope_filter,
    _course_not_explicitly_unavailable_filter,
    _service_group_for_response,
    course_scope_filter,
)
from service_group import (
    SERVICE_GROUP_EXPERIENCE,
    SERVICE_GROUP_PUBLIC_COURSE,
    is_local_government_education_facility,
)


def test_application_availability_filter_excludes_only_explicit_false():
    sql = str(
        _course_not_explicitly_unavailable_filter().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "courses.reservation_available IS DISTINCT FROM false" in sql


def test_culture_scope_uses_only_the_fixed_provider_registry():
    sql = str(course_scope_filter("provider").compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))

    assert "courses.provider" in sql
    assert "HOMEPLUS" in sql
    assert "LOTTE_MART" in sql
    assert "courses.service_group" not in sql
    assert "courses.collection_category" not in sql
    assert "courses.domain_category" not in sql
    assert "branches" not in sql


def test_culture_center_metadata_does_not_override_a_non_fixed_provider():
    fixed = Course(provider="EMART", service_group="공공강좌")
    metadata_only = Course(
        provider="MUNI_CULTURE_CENTER",
        service_group="문화센터",
        collection_category="문화센터",
        domain_category="문화센터",
    )

    assert _is_culture_center_course(fixed)
    assert not _is_culture_center_course(metadata_only)


def test_experience_scope_filter_avoids_large_text_columns():
    sql = str(_course_experience_scope_filter().compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))

    assert "courses.description" not in sql
    assert "courses.raw_url" not in sql
    assert "courses.title" not in sql
    assert "courses.program_type" in sql
    assert "courses.category_raw" in sql
    assert "branches.facility_source" in sql
    assert "CULTURE_FACILITY" in sql
    assert "courses.service_group" in sql
    assert "courses.provider" in sql
    assert "service_group_policy" in sql
    assert "courses.raw_fields" in sql
    assert "LIKE" in sql and "CULTURE" in sql
    assert "체험" in sql
    assert "숙박" in sql
    assert "CULTURE\\\\_" not in sql
    assert "CULTURE_FACILITY" in sql
    assert "library" in sql
    assert "sports_facility" in sql


def test_education_scope_is_exclusive_with_the_production_experience_predicate():
    predicate = course_scope_filter("education")
    compiled = predicate.compile(dialect=postgresql.dialect())
    sql = str(predicate.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))

    assert "courses.program_type" in sql
    assert "courses.category_raw" in sql
    assert "courses.source_group" in sql
    assert "left(courses.provider, 5)" in sql
    assert "MUNI_" in sql
    assert SERVICE_GROUP_PUBLIC_COURSE in sql
    assert SERVICE_GROUP_EXPERIENCE in sql
    assert "branches.name" in sql
    assert "operator_address_backfill" in sql
    assert "education_institution" in sql
    assert "target_name" in sql
    assert "시청" in sql
    assert "군청" in sql
    assert "구청" in sql
    assert "주민센터" in sql
    assert "행정복지센터" in sql
    assert "도서관" in sql
    assert "청소년수련관" in sql
    assert "ILIKE ANY" in sql
    assert len(compiled.params) < 150
    assert len(sql) < 30_000


def test_experience_scope_uses_bounded_array_predicates():
    predicate = course_scope_filter("experience")
    compiled = predicate.compile(dialect=postgresql.dialect())
    sql = str(predicate.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))

    assert "ILIKE ANY" in sql
    assert len(compiled.params) < 100
    assert len(sql) < 20_000


def test_local_government_education_scope_accepts_locked_metadata_or_administrative_branch():
    sql = str(_course_local_government_education_scope_filter().compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))

    assert "EXISTS" in sql
    assert "branches.id = courses.branch_id" in sql
    assert SERVICE_GROUP_PUBLIC_COURSE in sql
    assert "문화센터" in sql
    assert "education_institution" in sql
    assert "service_group_policy" in sql
    assert "domain_category" in sql
    assert "collection_category" in sql
    assert "regexp" not in sql.lower()
    assert " ~ " in sql


def test_locked_public_education_requires_both_lock_and_explicit_category():
    sql = str(_course_locked_public_education_scope_filter().compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))

    assert "service_group_policy" in sql
    assert "locked" in sql
    assert SERVICE_GROUP_PUBLIC_COURSE in sql
    assert "courses.domain_category" in sql
    assert "courses.collection_category" in sql
    assert "교육·강좌" in sql
    assert "branches" not in sql


def test_local_government_education_facility_boundaries():
    included = (
        "광주광역시청",
        "평창군청",
        "동작구청",
        "수택3동 주민자치센터",
        "초평동 행정복지센터",
        "반포2동 자치회관",
        "동구청 8층 시청각실",
    )
    excluded = (
        "중앙도서관",
        "김세중미술관",
        "서대문문화체육회관",
        "군포시청소년수련관",
        "포천시청년센터",
        "시청자미디어센터",
        "도서관 시청각실",
        "화성시청 도서관정책과",
    )

    assert all(is_local_government_education_facility(name) for name in included)
    assert not any(is_local_government_education_facility(name) for name in excluded)
    assert is_local_government_education_facility("수원시", "수원시청")
    assert not is_local_government_education_facility("슬기샘도서관", "수원시청")


def test_locked_public_course_response_ignores_row_level_experience_inference():
    course = Course(
        provider="MUNI_LOCKED_TEST",
        service_group=SERVICE_GROUP_EXPERIENCE,
        program_type=SERVICE_GROUP_EXPERIENCE,
        raw_fields={
            "service_group_policy": "locked",
            "service_group": SERVICE_GROUP_PUBLIC_COURSE,
        },
    )

    assert _service_group_for_response(course) == SERVICE_GROUP_PUBLIC_COURSE
