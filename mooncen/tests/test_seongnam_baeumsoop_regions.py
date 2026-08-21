from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup

from Crawler import Crawler_SeongnamBaeumsoop as seongnam
from backend.ops import region_collection
from utils.generic_course_eligibility import generic_course_row_decision


EXPECTED_OFFICE_MUNICIPALITIES = {
    "OFFICE_00000670": ("4113500000", "경기도 성남시 분당구"),
    "OFFICE_00000680": ("4113100000", "경기도 성남시 수정구"),
    "OFFICE_00000681": ("4113300000", "경기도 성남시 중원구"),
    "OFFICE_00001080": ("4113300000", "경기도 성남시 중원구"),
    "OFFICE_00002180": ("4113100000", "경기도 성남시 수정구"),
}


def _course_row(office_code: str, *, title: str = "테스트 강좌") -> dict:
    soup = BeautifulSoup(
        f"""
        <table><tbody><tr>
          <td class="subject">
            <a class="tit" onclick="fn_learning_detail('COURSE_1')">{title}</a>
          </td>
          <td><span class="org">성남시 교육기관</span></td>
          <td><span class="s_btn">접수중</span></td>
        </tr></tbody></table>
        """,
        "html.parser",
    )
    row = seongnam.parse_course_row(
        soup.select_one("tr"),
        {"office_code": office_code, "branch": "성남시 교육기관"},
        seongnam.list_url(office_code),
    )
    assert row is not None
    return row


@pytest.mark.parametrize(
    ("office_code", "expected"),
    EXPECTED_OFFICE_MUNICIPALITIES.items(),
)
def test_official_office_address_assigns_exact_seongnam_district(
    office_code: str,
    expected: tuple[str, str],
) -> None:
    row = _course_row(office_code)

    assert row["address"] == seongnam.OFFICE_ADDRESS_MAP[office_code]
    assert (row["municipality_code"], row["municipality_full_name"]) == expected
    assert row["region_sido"] == "경기도"
    assert row["region_sigungu"] == expected[1].removeprefix("경기도 ")
    assert row["municipality_region_verified"] is True


@pytest.mark.parametrize(
    ("venue_address", "expected_code"),
    [
        ("경기도 성남시 수정구 산성대로 123", "4113100000"),
        ("경기도 성남시 중원구 둔촌대로 123", "4113300000"),
        ("경기도 성남시 분당구 판교로 123", "4113500000"),
    ],
)
def test_detail_venue_address_assigns_exact_seongnam_district(
    venue_address: str,
    expected_code: str,
) -> None:
    row = {
        "address": "",
        "venue_address": venue_address,
        "venue_name": "성남시 교육장",
        "branch": "성남시 교육기관",
    }

    code, _name = seongnam.assign_seongnam_municipality(row)

    assert code == expected_code
    assert row["municipality_resolution_source"] == "official_address"


@pytest.mark.parametrize(
    "row",
    [
        {"address": "", "venue_address": "", "branch": "성남시 교육기관"},
        {
            "address": "경기도 성남시 수정구 수정로 1",
            "venue_address": "경기도 성남시 분당구 분당로 1",
        },
    ],
)
def test_missing_or_conflicting_district_evidence_stays_at_seongnam_parent(
    row: dict,
) -> None:
    code, name = seongnam.assign_seongnam_municipality(row)

    assert (code, name) == ("4113000000", "경기도 성남시")
    assert row["region_sigungu"] == "성남시"


@pytest.mark.parametrize(
    "title",
    [
        "접수연습용(실제강의 아님)",
        "접수연습용",
        "접수 연습용 (실제 강의 아님)",
        "수강신청연습용",
    ],
)
def test_practice_course_title_is_rejected_regardless_of_spacing(title: str) -> None:
    assert seongnam.should_skip_course({"title": title}) is True


def test_generic_eligibility_rejects_structured_practice_course() -> None:
    eligible, reason = generic_course_row_decision(
        {
            "title": "접수연습용(실제강의 아님)",
            "period": "2026-08-01 ~ 2026-08-31",
            "schedule_raw": "매주 토요일 10:00",
            "venue_name": "중원구 교육장",
            "capacity_total": 20,
            "raw_url": "https://sugang.seongnam.go.kr/ilms/learning/learningDetail.do?id=practice",
        }
    )

    assert (eligible, reason) == (False, "practice_or_test_course")


def test_collect_excludes_practice_course_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    office = {"office_code": "OFFICE_00000681", "branch": "중원구 교육기관"}
    practice = _course_row(
        office["office_code"],
        title="접수연습용(실제강의 아님)",
    )
    practice["provider_course_id"] = "PRACTICE"
    valid = _course_row(office["office_code"], title="시민 교육")
    valid["provider_course_id"] = "VALID"
    soup = BeautifulSoup("<table><tbody></tbody></table>", "html.parser")

    monkeypatch.setattr(seongnam, "discover_offices_from_files", lambda: [office])
    monkeypatch.setattr(seongnam, "session", lambda: nullcontext(object()))
    monkeypatch.setattr(
        seongnam,
        "validate_office",
        lambda *_args: {
            "soup": soup,
            "rows": [practice, valid],
            "max_page": 1,
            "parse_errors": 0,
        },
    )

    rows, _meta = seongnam.collect(
        limit=None,
        office_limit=None,
        max_pages=1,
        timeout=20,
        detail=False,
    )

    assert [row["provider_course_id"] for row in rows] == ["VALID"]


def test_branch_upsert_persists_verified_region_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def execute(self, sql: str, params: object) -> None:
            self.calls.append((sql, params))

        def fetchone(self) -> dict[str, str]:
            return {"id": "branch-id"}

    cursor = FakeCursor()

    @contextmanager
    def fake_db_cursor():
        yield cursor

    monkeypatch.setattr(seongnam, "get_db_cursor", fake_db_cursor)
    monkeypatch.setattr(seongnam, "sanitize_course_payload", lambda _row: None)

    assert seongnam.save_db([_course_row("OFFICE_00000680")]) == 1

    branch_sql, branch_params = cursor.calls[0]
    assert "address_source, region_sido, region_sigungu" in branch_sql
    assert "NULLIF(EXCLUDED.region_sido, '')" in branch_sql
    assert "NULLIF(EXCLUDED.region_sigungu, '')" in branch_sql
    assert isinstance(branch_params, tuple)
    assert branch_params[-2:] == ("경기도", "성남시 수정구")


def test_ops_reference_configures_and_resolves_all_seongnam_districts() -> None:
    reference = region_collection._region_reference()
    provider = seongnam.PROVIDER
    expected_names = {
        "경기도 성남시",
        "경기도 성남시 수정구",
        "경기도 성남시 중원구",
        "경기도 성남시 분당구",
    }

    assert len(reference.index.municipalities) == 269
    assert len(reference.configured_by_municipality) == 269
    assert reference.provider_municipalities[provider] == expected_names
    assert all(
        provider in reference.configured_by_scope["education"][name]
        for name in expected_names
    )

    for office_code, (expected_code, _name) in EXPECTED_OFFICE_MUNICIPALITIES.items():
        aggregate = region_collection.ScopeAggregateRow(
            provider=provider,
            branch_id=office_code,
            branch_name="성남시 교육기관",
            branch_address=seongnam.OFFICE_ADDRESS_MAP[office_code],
            facility_type="",
            facility_category="",
            venue_name="",
            venue_address="",
            active_data_count=1,
            total_data_count=1,
            latest_collected_at=datetime.now(timezone.utc),
            latest_historical_at=datetime.now(timezone.utc),
        )
        resolved = region_collection._resolve_aggregate_municipality(
            aggregate,
            reference,
        )
        assert resolved is not None
        assert resolved.code == expected_code
