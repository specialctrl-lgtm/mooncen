from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_gangnam as gangnam


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
    return Target(gangnam.GANGNAM_EDUCATION_PROVIDER, gangnam.GANGNAM_EDUCATION_URL)


def _item(
    company: str,
    branch: str,
    identity: str,
    title: str,
    *,
    total: int,
    start: str = "",
    end: str = "",
    status: str = "W",
) -> dict[str, Any]:
    return {
        "comcd": company,
        "comnm": branch,
        "class_cd": identity,
        "class_nm": title,
        "train_sdate": start,
        "train_edate": end,
        "train_stime": "10:00",
        "train_etime": "12:00",
        "train_day_nm": "화",
        "course_fee": "24,000",
        "status": status,
        "target_age_name": "성인 15명",
        "teacher_name": "김강사",
        "sports_cd": "15",
        "receive_kind": "10",
        "capa": "17",
        "reg_person": "6",
        "total_count": total,
    }


def _detail(
    company: str,
    identity: str,
    branch: str,
    title: str,
    *,
    period_text: str,
    status: str = "접수중 2026-07-01~2026-07-31",
) -> str:
    return f"""
    <html><body><div class="proc_read">
      <input name="comcd" value="{company}" />
      <input name="classcd" value="{identity}" />
      <span class="status">{status}</span>
      <table><tbody>
        <tr><th>강좌명</th><td>{title}</td></tr>
        <tr><th>운영센터</th><td>{branch} / 02-0000-0000</td></tr>
        <tr><th>시간/요일</th><td>10:00 ~ 12:00 / 화</td></tr>
        <tr><th>교육대상</th><td>성인 15명</td></tr>
        <tr><th>강사명</th><td>김강사</td></tr>
        <tr><th>접수방식</th><td>선착접수</td></tr>
        <tr><th>신청인원/정원</th><td>6 / 17</td></tr>
      </tbody></table>
      <div class="pattern_box">수업 안내 {period_text}</div>
    </div></body></html>
    """


def _fixture_fetcher(
    monkeypatch: pytest.MonkeyPatch,
    *,
    bad_detail: str = "",
) -> tuple[Callable[[Any, str, int], Any], list[DummySession]]:
    monkeypatch.setattr(gangnam, "GANGNAM_PAGE_SIZE", 2)
    companies = [
        {"comcd": "GNCC02", "comnm": "강남스포츠문화센터"},
        {"comcd": "GNCC23", "comnm": "대치 평생학습관"},
    ]
    pages = {
        ("GNCC02", 1): [
            _item(
                "GNCC02",
                "강남스포츠문화센터",
                "00001",
                "여름 특강",
                total=3,
                start="2026-08-01",
                end="2026-08-31",
                status="R",
            ),
            _item(
                "GNCC02",
                "강남스포츠문화센터",
                "00002",
                "정규 템플릿",
                total=3,
            ),
        ],
        ("GNCC02", 2): [
            _item(
                "GNCC02",
                "강남스포츠문화센터",
                "00003",
                "지난 특강",
                total=3,
                start="2026-06-01",
                end="2026-06-30",
                status="E",
            )
        ],
        ("GNCC23", 1): [
            _item(
                "GNCC23",
                "대치 평생학습관",
                "00004",
                "AI 특강",
                total=1,
                start="2026-08-01",
                end="2026-08-31",
                status="CE",
            )
        ],
    }
    details = {
        ("GNCC02", "00001"): _detail(
            "GNCC02",
            "00001",
            "강남스포츠문화센터",
            "여름 특강",
            period_text="수업기간 2026년 8월 4일 ~ 8월 25일",
        ),
        ("GNCC23", "00004"): _detail(
            "GNCC23",
            "00004",
            "대치 평생학습관",
            "AI 특강",
            period_text="일시: 8/3~9/28 (매주 월)",
            status="접수마감",
        ),
    }
    if bad_detail:
        company, identity = bad_detail.split("/")
        details[(company, identity)] = details[(company, identity)].replace(
            "수업기간 2026년 8월 4일 ~ 8월 25일", "기간은 추후 공지"
        ).replace("일시: 8/3~9/28 (매주 월)", "기간은 추후 공지")

    def fetch(_session: Any, url: str, _timeout: int) -> Any:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if url == gangnam.GANGNAM_COMPANY_API:
            return companies
        if parsed.path == "/rest/lecture/list":
            return pages[(query["company_code"][0], int(query["page"][0]))]
        assert parsed.path == gangnam.GANGNAM_LIST_PATH
        assert query["action"] == ["read"]
        return BeautifulSoup(
            details[(query["comcd"][0], query["classcd"][0])], "lxml"
        )

    sessions: list[DummySession] = []

    def make_session() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    return fetch, sessions, make_session


def test_provider_and_canonical_route_are_exact() -> None:
    digest = hashlib.sha1(gangnam.GANGNAM_EDUCATION_URL.encode("utf-8")).hexdigest()[:8].upper()
    assert gangnam.GANGNAM_EDUCATION_PROVIDER == f"MUNI_LIFE_GANGNAM_GO_KR_{digest}"
    assert gangnam.is_target(_target()) is True
    assert gangnam.is_target(
        Target(gangnam.GANGNAM_EDUCATION_PROVIDER, gangnam.GANGNAM_EDUCATION_URL + "?")
    ) is False
    assert gangnam.is_target(
        Target("MUNI_WRONG", gangnam.GANGNAM_EDUCATION_URL)
    ) is False


def test_generated_urls_are_https_and_identity_bound() -> None:
    list_url = gangnam.gangnam_list_url("GNCC23", 2)
    parsed = urlparse(list_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    assert parsed.scheme == "https"
    assert parsed.netloc == gangnam.GANGNAM_HOST
    assert query["company_code"] == ["GNCC23"]
    assert query["page"] == ["2"]
    assert query["page_size"] == [str(gangnam.GANGNAM_PAGE_SIZE)]
    assert gangnam.gangnam_list_url("../../evil", 1) == ""

    detail = urlparse(gangnam.gangnam_detail_url("GNCC23", "00004"))
    detail_query = parse_qs(detail.query)
    assert detail.netloc == gangnam.GANGNAM_HOST
    assert detail_query == {
        "action": ["read"],
        "comcd": ["GNCC23"],
        "classcd": ["00004"],
        "type": ["R"],
    }


def test_complete_snapshot_enumerates_templates_but_publishes_only_dated_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch, sessions, make_session = _fixture_fetcher(monkeypatch)
    rows, parser, meta = gangnam.collect_gangnam_education_courses(
        _target(),
        max_pages=5,
        detail_limit=10,
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
    )

    assert parser == gangnam.GANGNAM_PARSER
    assert [row["title"] for row in rows] == ["여름 특강", "AI 특강"]
    assert [row["period"] for row in rows] == [
        "2026-08-04 ~ 2026-08-25",
        "2026-08-03 ~ 2026-09-28",
    ]
    assert rows[0]["reservation_available"] is True
    assert rows[1]["status"] == "접수마감"
    assert rows[0]["capacity_current"] == 6
    assert rows[0]["capacity_total"] == 17
    assert all(
        row["schedule_raw"] == "10:00 ~ 12:00 / \ud654"
        for row in rows
    )
    assert all(row["venue_name"] == row["branch"] for row in rows)
    assert all(
        row["raw_fields"]["required_field_provenance"]["schedule_raw"]
        == "detail"
        for row in rows
    )
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "1168000000" for row in rows)
    assert len({row["provider_course_id"] for row in rows}) == 2

    assert meta["total_count"] == 4
    assert meta["discovered_links"] == 4
    assert meta["pages"] == 3
    assert meta["declared_pages"] == 3
    assert meta["company_count"] == 2
    assert meta["undated_count"] == 1
    assert meta["expired_count"] == 1
    assert meta["detail_pages"] == 2
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["branch_counts"] == {"강남스포츠문화센터": 1, "대치 평생학습관": 1}
    assert sessions and all(item.closed for item in sessions)


def test_page_cap_marks_snapshot_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch, _sessions, make_session = _fixture_fetcher(monkeypatch)
    rows, _parser, meta = gangnam.collect_gangnam_education_courses(
        _target(),
        max_pages=1,
        detail_limit=10,
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
    )
    assert rows
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "max_pages cap" in meta["configured_collection_error"]


def test_detail_cap_marks_snapshot_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch, _sessions, make_session = _fixture_fetcher(monkeypatch)
    rows, _parser, meta = gangnam.collect_gangnam_education_courses(
        _target(),
        max_pages=5,
        detail_limit=1,
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
    )
    assert len(rows) == 1
    assert meta["detail_required_count"] == 2
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_detail_without_actual_course_period_is_audited_and_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch, _sessions, make_session = _fixture_fetcher(
        monkeypatch, bad_detail="GNCC02/00001"
    )
    rows, _parser, meta = gangnam.collect_gangnam_education_courses(
        _target(),
        max_pages=5,
        detail_limit=10,
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
    )
    assert [row["title"] for row in rows] == ["AI 특강"]
    assert meta["detail_undated_count"] == 1
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta


def test_invalid_target_never_fetches() -> None:
    called = False

    def fetch(_session: Any, _url: str, _timeout: int) -> Any:
        nonlocal called
        called = True
        raise AssertionError("must not fetch")

    rows, _parser, meta = gangnam.collect_gangnam_education_courses(
        Target("MUNI_WRONG", gangnam.GANGNAM_EDUCATION_URL),
        fetcher=fetch,
        session_factory=DummySession,
    )
    assert rows == []
    assert called is False
    assert meta["snapshot_complete"] is False
    assert "canonical Gangnam route" in meta["configured_collection_error"]


def test_detail_period_parser_handles_year_rollover() -> None:
    assert gangnam._detail_course_period(
        "일시: 12/20~1/10", "2026-12-01"
    ) == ("2026-12-20", "2027-01-10", "2026-12-20 ~ 2027-01-10")
