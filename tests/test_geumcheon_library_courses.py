from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest
import yaml

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import municipal_geumcheon_library as geumcheon_library


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Target:
    provider: str
    url: str


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target() -> Target:
    return Target(
        provider=geumcheon_library.GEUMCHEON_LIBRARY_PROVIDER,
        url=geumcheon_library.GEUMCHEON_LIBRARY_URL,
    )


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _list_row(
    program_id: str,
    title: str,
    *,
    status: str,
    branch_class: str = "ds",
    target: str = "성인",
    apply_period: str = "2026-07-01 09:00 ~ 2026-07-31 18:00",
    capacity: str = "2/10",
    fee: str = "무료",
    material_fee: str = "무료",
) -> str:
    class_attribute = f' class="{branch_class}"' if branch_class else ""
    return f"""
    <tr>
      <td class="notice_title">
        <a href="#" name="go_detail"{class_attribute}>{title}</a>
        <input type="hidden" id="PGM_IDX" value="{program_id}" />
        <input type="hidden" id="PGM_BBS_ID" value="PGM_000000000001" />
      </td>
      <td>{target}</td><td>{apply_period}</td><td>{capacity}</td>
      <td>{fee}</td><td>{material_fee}</td>
      <td class="regis"><span>{status}</span></td>
    </tr>
    """


def _list_page(*rows: str, total: int, current: int, pages: int) -> str:
    return f"""
    <html><body>
      <p class="board_page">전체 {total:,}개 (페이지 <span>{current}</span>/{pages})</p>
      <div class="notice_wrap pro_table"><table class="board"><tbody>
        {''.join(rows)}
      </tbody></table></div>
    </body></html>
    """


def _detail(
    title: str,
    *,
    status: str,
    venue: str = "독산도서관 2층 강의실",
    target: str = "성인",
    capacity: str = "2/10",
    course_period: str = "2026-07-19 10:00 ~ 2026-08-19 12:00 (2회)",
    schedule: str = "수요일 10:00 ~ 12:00",
    apply_period: str = "2026-07-01 09:00 ~ 2026-07-31 18:00",
    fee: str = "무료",
    material_fee: str = "무료",
    application_control: bool | None = None,
    extra_links: str = "",
    omit_field: str = "",
) -> str:
    if application_control is None:
        application_control = status in {"접수중", "대기신청"}
    values = [
        ("강좌명", title),
        ("상태", status),
        ("강좌장소", venue),
        ("강사", "김강사"),
        ("대상", target),
        ("모집정원", capacity),
        ("강좌기간", course_period),
        ("강좌시간", schedule),
        ("모집기간", apply_period),
        ("수강료", fee),
        ("교재 및 재료비", material_fee),
    ]
    values = [pair for pair in values if pair[0] != omit_field]
    table_rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in values)
    apply = (
        '<a class="part2" href="javascript:void(0);" '
        'onclick="fn_select_Usr(\'N\');return false;">수강신청</a>'
        if application_control
        else ""
    )
    return f"""
    <html><body>
      <div class="sub_cont">
        <div class="table_wrap"><table>{table_rows}</table></div>
        <div class="borderBox">상세 교육 안내<br />강사소개</div>
        <ul class="btnBox_list"><li>{extra_links}</li><li>{apply}</li></ul>
      </div>
    </body></html>
    """


def _fetcher(
    pages: dict[int, str], details: dict[str, str]
) -> Callable[[Any, str, int], BeautifulSoup]:
    def fetch(_session: Any, url: str, _timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == geumcheon_library.GEUMCHEON_LIBRARY_LIST_PATH:
            assert query["selfId"] == ["1090"]
            assert query["pageSize"] == [
                str(geumcheon_library.GEUMCHEON_LIBRARY_PAGE_SIZE)
            ]
            return _soup(pages[int(query["pageNo"][0])])
        assert parsed.path == geumcheon_library.GEUMCHEON_LIBRARY_DETAIL_PATH
        assert query["bbsId"] == [geumcheon_library.GEUMCHEON_LIBRARY_BBS_ID]
        return _soup(details[query["idxNo"][0]])

    return fetch


def _three_row_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[int, str], dict[str, str]]:
    monkeypatch.setattr(geumcheon_library, "GEUMCHEON_LIBRARY_PAGE_SIZE", 2)
    pages = {
        1: _list_page(
            _list_row("101", "독산 여름 강좌", status="접수중", branch_class="ds"),
            _list_row(
                "099",
                "종료된 기록",
                status="강좌종료",
                branch_class="",
                target="",
                apply_period="",
                capacity="",
                fee="",
                material_fee="",
            ),
            total=3,
            current=1,
            pages=2,
        ),
        2: _list_page(
            _list_row(
                "102",
                "참새 대기 강좌",
                status="대기신청",
                branch_class="s_2",
                target="초등",
                capacity="10/10",
                material_fee="5,000원",
            ),
            total=3,
            current=2,
            pages=2,
        ),
    }
    details = {
        "101": _detail("독산 여름 강좌", status="접수중"),
        "102": _detail(
            "참새 대기 강좌",
            status="대기신청",
            venue="참새작은도서관",
            target="초등",
            capacity="10/10",
            material_fee="5,000원",
            extra_links='<a href="https://evil.example/apply">외부 신청</a>',
        ),
    }
    return pages, details


def test_provider_uses_canonical_url_convention_and_route_is_exact() -> None:
    digest = hashlib.sha1(
        geumcheon_library.GEUMCHEON_LIBRARY_URL.encode("utf-8")
    ).hexdigest()[:8].upper()
    assert geumcheon_library.GEUMCHEON_LIBRARY_PROVIDER == (
        f"MUNI_GEUMCHEONLIB_SEOUL_KR_{digest}"
    )
    assert geumcheon_library.is_target(_target()) is True
    assert geumcheon_library.is_target(
        Target(
            geumcheon_library.GEUMCHEON_LIBRARY_PROVIDER,
            f"{geumcheon_library.GEUMCHEON_LIBRARY_URL}&pageNo=1",
        )
    ) is False
    assert geumcheon_library.is_target(
        Target("MUNI_UNOWNED", geumcheon_library.GEUMCHEON_LIBRARY_URL)
    ) is False


def test_haeoreum_branch_has_official_location() -> None:
    row = geumcheon_library._base_row(
        _target(),
        program_id="200",
        title="해오름 강좌",
        branch="해오름작은도서관",
        branch_class="s_6",
        detail_url=geumcheon_library.geumcheon_library_detail_url("200"),
    )

    assert row["venue_address"] == "서울특별시 금천구 시흥대로123길 11, 4층"
    assert row["branch_lat"] == 37.47019
    assert row["branch_location_verified"] is True


def test_complete_pages_details_and_real_library_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages, details = _three_row_fixture(monkeypatch)
    dedupe_calls: list[list[dict[str, Any]]] = []

    def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        dedupe_calls.append(rows)
        return rows

    rows, parser, meta = geumcheon_library.collect_geumcheon_library_courses(
        _target(),
        max_pages=2,
        detail_limit=5,
        fetcher=_fetcher(pages, details),
        session_factory=DummySession,
        dedupe_rows=dedupe,
        today="2026-07-19",
    )

    assert parser == geumcheon_library.GEUMCHEON_LIBRARY_PARSER
    assert len(rows) == 2
    by_id = {row["raw_fields"]["program_id"]: row for row in rows}
    open_row = by_id["101"]
    wait_row = by_id["102"]
    assert open_row["provider_course_id"] == (
        f"{geumcheon_library.GEUMCHEON_LIBRARY_PROVIDER}:program:101"
    )
    assert open_row["branch"] == "독산도서관"
    assert open_row["room"] == "독산도서관 2층 강의실"
    assert wait_row["branch"] == "참새작은도서관"
    assert wait_row["room"] == "참새작은도서관"
    assert open_row["status"] == "OPEN"
    assert wait_row["status"] == "WAITING"
    assert open_row["start_date"] == "2026-07-19"
    assert open_row["end_date"] == "2026-08-19"
    assert open_row["application_url"] == open_row["raw_url"]
    assert wait_row["application_url"] == wait_row["raw_url"]
    assert "evil.example" not in json.dumps(rows, ensure_ascii=False)
    for row in rows:
        assert row["reservation_available"] is True
        assert row["category"] == "교육·강좌"
        assert row["domain_category"] == "교육·강좌"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["municipality_code"] == "1154500000"
        assert row["preserve_branch"] is True

    assert len(dedupe_calls) == 1
    assert meta["pages"] == 2
    assert meta["total_count"] == 3
    assert meta["discovered_links"] == 3
    assert meta["candidate_count"] == 2
    assert meta["ended_count"] == 1
    assert meta["detail_required_count"] == 2
    assert meta["detail_attempts"] == 2
    assert meta["detail_pages"] == 2
    assert meta["source_status_counts"] == {
        "접수중": 1,
        "강좌종료": 1,
        "대기신청": 1,
    }
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False


def test_page_and_detail_caps_are_explicitly_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages, details = _three_row_fixture(monkeypatch)
    fetch = _fetcher(pages, details)

    page_rows, _parser, page_meta = geumcheon_library.collect_geumcheon_library_courses(
        _target(),
        max_pages=1,
        detail_limit=5,
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
    )
    assert len(page_rows) == 1
    assert page_meta["source_cap_reached"] is True
    assert page_meta["snapshot_complete"] is False
    assert "max_pages cap reached after 1 of 2" in page_meta["configured_collection_error"]

    detail_rows, _parser, detail_meta = geumcheon_library.collect_geumcheon_library_courses(
        _target(),
        max_pages=2,
        detail_limit=1,
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
    )
    assert len(detail_rows) == 1
    assert detail_meta["detail_attempts"] == 1
    assert detail_meta["detail_required_count"] == 2
    assert detail_meta["source_cap_reached"] is True
    assert detail_meta["snapshot_complete"] is False
    assert "detail_limit cap allows 1 of 2" in detail_meta["configured_collection_error"]


def test_kst_today_boundary_is_inclusive_and_yesterday_is_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(geumcheon_library, "GEUMCHEON_LIBRARY_PAGE_SIZE", 2)
    pages = {
        1: _list_page(
            _list_row("201", "오늘 종료", status="강좌진행", branch_class="gs"),
            _list_row("202", "어제 종료", status="강좌진행", branch_class="gnr"),
            total=2,
            current=1,
            pages=1,
        )
    }
    details = {
        "201": _detail(
            "오늘 종료",
            status="강좌진행",
            venue="가산도서관 강의실",
            course_period="2026-07-01 10:00 ~ 2026-07-19 12:00 (2회)",
        ),
        "202": _detail(
            "어제 종료",
            status="강좌진행",
            venue="금나래도서관 강의실",
            course_period="2026-07-01 10:00 ~ 2026-07-18 12:00 (2회)",
        ),
    }

    rows, _parser, meta = geumcheon_library.collect_geumcheon_library_courses(
        _target(),
        max_pages=1,
        detail_limit=2,
        fetcher=_fetcher(pages, details),
        session_factory=DummySession,
        today="2026-07-19",
    )

    assert [row["title"] for row in rows] == ["오늘 종료"]
    assert rows[0]["end_date"] == "2026-07-19"
    assert rows[0]["reservation_available"] is False
    assert "application_url" not in rows[0]
    assert meta["expired_count"] == 1
    assert meta["snapshot_complete"] is True


@pytest.mark.parametrize(
    ("detail_kwargs", "message"),
    [
        ({"title": "다른 제목"}, "detail/list title mismatch"),
        ({"status": "접수마감"}, "detail/list status mismatch"),
        ({"omit_field": "강좌기간"}, "missing detail fields 강좌기간"),
    ],
)
def test_any_current_detail_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    detail_kwargs: dict[str, str],
    message: str,
) -> None:
    monkeypatch.setattr(geumcheon_library, "GEUMCHEON_LIBRARY_PAGE_SIZE", 1)
    pages = {
        1: _list_page(
            _list_row("301", "원본 제목", status="접수중"),
            total=1,
            current=1,
            pages=1,
        )
    }
    options: dict[str, Any] = {"title": "원본 제목", "status": "접수중"}
    options.update(detail_kwargs)
    details = {"301": _detail(**options)}

    rows, _parser, meta = geumcheon_library.collect_geumcheon_library_courses(
        _target(),
        max_pages=1,
        detail_limit=1,
        fetcher=_fetcher(pages, details),
        session_factory=DummySession,
        today="2026-07-19",
    )

    assert rows == []
    assert meta["detail_errors"] >= 1
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_duplicate_official_id_or_declaration_mismatch_is_not_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(geumcheon_library, "GEUMCHEON_LIBRARY_PAGE_SIZE", 1)
    duplicate = _list_row("401", "중복 강좌", status="강좌진행")
    pages = {
        1: _list_page(duplicate, total=2, current=1, pages=2),
        2: _list_page(duplicate, total=2, current=2, pages=2),
    }
    details = {"401": _detail("중복 강좌", status="강좌진행")}

    rows, _parser, meta = geumcheon_library.collect_geumcheon_library_courses(
        _target(),
        max_pages=2,
        detail_limit=2,
        fetcher=_fetcher(pages, details),
        session_factory=DummySession,
        today="2026-07-19",
    )

    assert len(rows) == 1
    assert meta["duplicate_count"] == 1
    assert meta["discovered_links"] == 1
    assert meta["pagination_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "duplicate official program IDs" in meta["configured_collection_error"]
    assert "declared total 2 does not match 1 unique" in meta["configured_collection_error"]


def test_unsafe_target_never_starts_session_or_fetches() -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unsafe target must not perform network I/O")

    rows, parser, meta = geumcheon_library.collect_geumcheon_library_courses(
        Target(
            provider=geumcheon_library.GEUMCHEON_LIBRARY_PROVIDER,
            url="https://evil.example/programList.do?selfId=1090",
        ),
        fetcher=forbidden,
        session_factory=forbidden,
    )

    assert rows == []
    assert parser == geumcheon_library.GEUMCHEON_LIBRARY_PARSER
    assert meta["snapshot_complete"] is False
    assert "provider-owned canonical" in meta["configured_collection_error"]


def test_open_detail_with_only_external_apply_link_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(geumcheon_library, "GEUMCHEON_LIBRARY_PAGE_SIZE", 1)
    pages = {
        1: _list_page(
            _list_row("501", "외부 링크만 있는 강좌", status="접수중"),
            total=1,
            current=1,
            pages=1,
        )
    }
    details = {
        "501": _detail(
            "외부 링크만 있는 강좌",
            status="접수중",
            application_control=False,
            extra_links='<a class="part2" href="https://evil.example/apply">수강신청</a>',
        )
    }

    rows, _parser, meta = geumcheon_library.collect_geumcheon_library_courses(
        _target(),
        max_pages=1,
        detail_limit=1,
        fetcher=_fetcher(pages, details),
        session_factory=DummySession,
        today="2026-07-19",
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "no exact application control" in meta["configured_collection_error"]


def test_detail_url_rejects_non_numeric_official_id() -> None:
    assert geumcheon_library.geumcheon_library_detail_url("3710").startswith(
        "https://geumcheonlib.seoul.kr/geumcheonlib/uce/programDetail.do?"
    )
    assert "idxNo=3710" in geumcheon_library.geumcheon_library_detail_url("3710")
    assert geumcheon_library.geumcheon_library_detail_url("../../3710") == ""
    assert geumcheon_library.geumcheon_library_detail_url("3710&token=secret") == ""


def test_operational_target_owns_a_complete_bounded_library_snapshot() -> None:
    assert geumcheon_library.GEUMCHEON_LIBRARY_PAGE_SIZE == 500

    document = yaml.safe_load(
        (
            ROOT
            / "config"
            / "crawl_targets"
            / "municipal_integrated_reservation.yaml"
        ).read_text(encoding="utf-8")
    )
    target = next(
        row
        for row in document["targets"]
        if row.get("provider") == geumcheon_library.GEUMCHEON_LIBRARY_PROVIDER
    )
    assert target["url"] == geumcheon_library.GEUMCHEON_LIBRARY_URL
    assert target["collection_type"] == geumcheon_library.GEUMCHEON_LIBRARY_PARSER
    assert target["crawler_status"] == "ready"
    assert target["full_snapshot_required"] is True
    assert target["ownership_scope"] == "all_geumcheon_library_programs_current_future"
    assert target["municipality_code"] == "1154500000"
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert target["last_quality"]["source_total"] == 3127
    assert target["last_quality"]["source_pages"] == 7
    assert target["last_quality"]["detail_pages"] == 111
    assert target["last_quality"]["snapshot_complete"] is True

    arguments = list(
        generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
            geumcheon_library.GEUMCHEON_LIBRARY_PROVIDER
        ]
    )
    assert arguments == [
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "200",
    ]

    operational = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = next(
        row
        for row in operational["entries"]
        if row.get("provider") == geumcheon_library.GEUMCHEON_LIBRARY_PROVIDER
    )
    assert entry["validation_outcome"] == "collected"
    assert entry["row_count"] == 111
