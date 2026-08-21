from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from backend import schemas
from backend.routers import locations


class _AggregateResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _AggregateDb:
    def __init__(self, rows_by_scope):
        self.rows_by_scope = rows_by_scope
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return _AggregateResult(self.rows_by_scope[len(self.statements) - 1])


class _NearbyRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _NearbyDb:
    def __init__(self, nearby_rows, aggregate_rows_by_scope):
        self.nearby_rows = nearby_rows
        self.aggregate_rows_by_scope = aggregate_rows_by_scope
        self.calls = []
        self.aggregate_call_count = 0

    def execute(self, statement, params=None):
        self.calls.append((statement, params))
        if params is not None:
            return _NearbyRowsResult(self.nearby_rows)
        rows = self.aggregate_rows_by_scope[self.aggregate_call_count]
        self.aggregate_call_count += 1
        return _AggregateResult(rows)


def _nearby_row(branch_id, *, provider, course_count):
    return SimpleNamespace(
        id=branch_id,
        provider=provider,
        branch_code=f"{provider}-branch",
        name="Shared physical facility",
        address="Seoul test address",
        phone=None,
        lat=37.5,
        lon=127.0,
        website_url=None,
        operating_hours=None,
        regular_holiday=None,
        admission_fee=None,
        facility_type=None,
        facility_category=None,
        facility_source=None,
        facility_source_sheet=None,
        facility_service_group=None,
        facility_collection_category=None,
        region_sido="Seoul",
        region_sigungu="Test-gu",
        basic_info={},
        course_count=course_count,
        active_course_count=course_count,
        open_course_count=course_count,
        category_counts={},
        collection_categories=[],
        service_group_counts={},
        service_groups=[],
        distance_m=100.0,
    )


def test_uncomputed_embedded_branch_scope_counts_remain_null():
    branch = schemas.Branch(
        id=str(uuid4()),
        provider="TEST_PROVIDER",
        name="Test branch",
    )

    assert branch.model_dump()["scope_course_counts"] is None


def test_computed_nearby_scope_counts_require_all_three_zero_filled_keys():
    branch = schemas.Branch(
        id=str(uuid4()),
        provider="TEST_PROVIDER",
        name="Test branch",
        scope_course_counts={
            "provider": 0,
            "education": 0,
            "experience": 1,
        },
    )

    assert branch.model_dump()["scope_course_counts"] == {
        "provider": 0,
        "education": 0,
        "experience": 1,
    }


def test_active_scope_counts_use_three_bounded_course_api_aggregate_queries(monkeypatch):
    first_id = uuid4()
    second_id = uuid4()
    db = _AggregateDb(
        [
            [SimpleNamespace(branch_id=second_id, course_count=4)],
            [SimpleNamespace(branch_id=first_id, course_count=2)],
            [SimpleNamespace(branch_id=first_id, course_count=3)],
        ]
    )
    real_scope_filter = locations.course_scope_filter
    calls = []

    def tracked_scope_filter(scope):
        calls.append(scope)
        return real_scope_filter(scope)

    monkeypatch.setattr(locations, "course_scope_filter", tracked_scope_filter)

    result = locations._active_scope_course_counts(
        db,
        [first_id, first_id, second_id],
    )

    assert calls == ["provider", "education", "experience"]
    assert len(db.statements) == 3
    assert result == {
        str(first_id): {"provider": 0, "education": 2, "experience": 3},
        str(second_id): {"provider": 4, "education": 0, "experience": 0},
    }
    for statement in db.statements:
        compiled = str(statement)
        assert "courses.is_active IS true" in compiled
        assert "courses.status IN" in compiled
        assert "courses.end_date" in compiled
        assert "courses.apply_end" in compiled
        assert "timezone" in compiled
        assert "GROUP BY courses.branch_id" in compiled
        assert "FILTER (WHERE" not in compiled
        assert ["OPEN", "SCHEDULED", "DEADLINE", "WAITING"] in tuple(
            statement.compile().params.values()
        )
        assert "CLOSED" not in repr(statement.compile().params)


def test_active_scope_counts_skip_the_database_for_an_empty_nearby_result():
    db = _AggregateDb([])

    assert locations._active_scope_course_counts(db, []) == {}
    assert db.statements == []


def test_nearby_endpoint_sums_explicit_scope_counts_when_physical_branches_merge():
    first_id = uuid4()
    second_id = uuid4()
    db = _NearbyDb(
        [
            _nearby_row(first_id, provider="FIRST", course_count=2),
            _nearby_row(second_id, provider="SECOND", course_count=3),
        ],
        [
            [],
            [SimpleNamespace(branch_id=first_id, course_count=2)],
            [SimpleNamespace(branch_id=second_id, course_count=3)],
        ],
    )

    result = locations.get_nearby_branches(
        lat=37.5,
        lon=127.0,
        radius_km=10,
        limit=20,
        include_empty=True,
        db=db,
    )

    assert len(db.calls) == 4
    assert len(result) == 1
    assert result[0].branch_ids == [str(first_id), str(second_id)]
    assert result[0].scope_course_counts is not None
    assert result[0].scope_course_counts.model_dump() == {
        "provider": 0,
        "education": 2,
        "experience": 3,
    }
