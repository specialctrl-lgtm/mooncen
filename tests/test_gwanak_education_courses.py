from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_gwanak as gwanak


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
    return Target(gwanak.GWANAK_EDUCATION_PROVIDER, gwanak.GWANAK_EDUCATION_URL)


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _count(total: int, current: int, pages: int) -> str:
    return f'<div class="count">총 {total:,} 건 페이지: {current} /{pages}</div>'


def _main_row(
    identity: str,
    title: str,
    *,
    organization: str = "29000100",
    institution: str = "평생학습관",
    start: str = "2026-07-19",
    end: str = "2026-08-19",
    venue: str = "평생학습관 5층",
    fee: str = "무료",
    capacity: str = "3/20",
    method: str = "온라인접수",
    status: str = "접수중",
    number: int = 1,
) -> str:
    return f"""
    <tr>
      <td class="num">{number}</td>
      <td class="title eduname"><a href="javascript:doLectureView('{identity}','','{organization}')">
        <span class="educate">[{institution}]</span><span>{title}</span>
      </a></td>
      <td class="period"><span>[교육] {start} ~ {end}</span></td>
      <td>{venue}</td><td>{fee}</td><td class="capacity"><span>{capacity}</span></td>
      <td class="reception"><span class="method">{method}</span><span class="state">{status}</span></td>
    </tr>
    """


def _main_page(*rows: str, total: int, current: int, pages: int) -> str:
    return f"""
    <html><body>{_count(total, current, pages)}
      <table><thead><tr><th>번호</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
    </body></html>
    """


def _knowledge_card(
    identity: str,
    title: str,
    *,
    start: str = "2026-07-20",
    end: str = "2026-08-20",
    status: str = "접수중",
    institution: str = "평생학습관",
) -> str:
    return f"""
    <li><a href="javascript:doLectureView('{identity}');">
      <div class="board-photo"><img src="https://www.gwanak.go.kr/common/files/Download.do?id={identity}" /></div>
      <div class="board-txt">
        <div class="txt-title"><div class="status">{status}</div><p class="title">{title}</p></div>
        <div class="txt-data">
          <p class="org"><span>교육기관</span>{institution}</p>
          <p class="period"><span>교육기간</span>{start} ~ {end}</p>
        </div>
      </div>
    </a></li>
    """


def _knowledge_page(*cards: str, total: int, current: int, pages: int) -> str:
    return f"""
    <html><body>{_count(total, current, pages)}
      <ul class="board-photo-list">{''.join(cards)}</ul>
    </body></html>
    """


def _detail(
    identity: str,
    title: str,
    *,
    source_kind: str = "main",
    institution: str = "평생학습관",
    status: str = "접수중",
    start: str = "2026-07-19",
    end: str = "2026-08-19",
    venue: str = "평생학습관 5층",
    fee: str = "무료",
    capacity: str = "20 / (5) 명",
    applicants: str = "3 / (1) 명",
    application_control: bool | None = None,
    omit: str = "",
) -> str:
    if application_control is None:
        application_control = status == "접수중"
    fields = [
        ("교육기관", institution),
        ("교육대상", "성인"),
        ("강좌분야", "인문/사회"),
        ("강사명", "관악강사"),
        ("수강료", fee),
        ("교육장소", venue),
        ("교육기간", f"{start} ~ {end}"),
        ("수강요일", "월 10:00 ~ 12:00"),
        ("접수기간", "2026-07-01/10:00 ~ 2026-07-31/18:00 (전체)"),
        ("정원(예비)", capacity),
        ("접수인원(예비)", applicants),
        ("접수방법", "온라인 선착순"),
        ("전화문의", "02-879-5000"),
    ]
    fields = [item for item in fields if item[0] != omit]
    rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in fields)
    function = "doLectureMemberForm" if source_kind == "knowledge" else "getToday"
    control = (
        f'<a class="btn blue" href="javascript:{function}(\'{identity}\');">강좌 신청 하기</a>'
        if application_control
        else ""
    )
    return f"""
    <html><body>
      <div class="title"><span class="name">{title}</span><span class="status">{status}</span></div>
      <table class="info-table"><tbody>{rows}</tbody></table>
      <div class="btns aright">{control}<a class="btn gray">목록</a></div>
    </body></html>
    """


def _fetcher(
    main_pages: dict[int, str],
    knowledge_pages: dict[int, str],
    details: dict[tuple[str, str], str],
) -> Callable[[Any, str, int], BeautifulSoup]:
    def fetch(_session: Any, url: str, _timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == gwanak.GWANAK_MAIN_LIST_PATH:
            assert query["pageUnit"] == [str(gwanak.GWANAK_MAIN_PAGE_SIZE)]
            return _soup(main_pages[int(query["pageIndex"][0])])
        if parsed.path == gwanak.GWANAK_KNOWLEDGE_LIST_PATH:
            return _soup(knowledge_pages[int(query["pageIndex"][0])])
        identity = query["clIdx"][0]
        if parsed.path == gwanak.GWANAK_MAIN_DETAIL_PATH:
            assert query["scLcOrganization1"][0] in gwanak.GWANAK_ORGANIZATIONS
            return _soup(details[("main", identity)])
        assert parsed.path == gwanak.GWANAK_KNOWLEDGE_DETAIL_PATH
        return _soup(details[("knowledge", identity)])

    return fetch


def _complete_fixture() -> tuple[dict[int, str], dict[int, str], dict[tuple[str, str], str]]:
    main_pages = {
        1: _main_page(
            _main_row("L00000001", "오늘 포함 정상 강좌"),
            _main_row(
                "L00000002",
                "종료 강좌",
                organization="29000400",
                institution="구민정보화교육",
                start="2026-06-01",
                end="2026-07-18",
                venue="난곡 정보화 교육장",
                status="강좌종료",
                number=2,
            ),
            total=3,
            current=1,
            pages=2,
        ),
        2: _main_page(
            _main_row(
                "L00000003",
                "test 페이지3(신청금지)",
                organization="29000400",
                institution="구민정보화교육",
                venue="관악구청",
                status="접수 마감",
                number=3,
            ),
            total=3,
            current=2,
            pages=2,
        ),
    }
    knowledge_pages = {
        1: _knowledge_page(
            _knowledge_card("L00001001", "재능나눔 현재 강좌"),
            _knowledge_card(
                "L00001002",
                "재능나눔 종료 강좌",
                start="2026-06-01",
                end="2026-07-18",
                status="강좌종료",
            ),
            total=2,
            current=1,
            pages=1,
        )
    }
    details = {
        ("main", "L00000001"): _detail("L00000001", "오늘 포함 정상 강좌"),
        ("knowledge", "L00001001"): _detail(
            "L00001001",
            "재능나눔 현재 강좌",
            source_kind="knowledge",
            start="2026-07-20",
            end="2026-08-20",
            venue="실시간 온라인 Zoom",
            applicants="0 / (0) 명",
        ),
    }
    return main_pages, knowledge_pages, details


def test_provider_is_preserved_while_canonical_url_is_complete_union() -> None:
    assert gwanak.GWANAK_EDUCATION_PROVIDER.endswith("51D9DCB4")
    assert "scLcOrganization1=29000400" in gwanak.GWANAK_EDUCATION_LEGACY_URL
    assert "?" not in gwanak.GWANAK_EDUCATION_URL
    assert gwanak.is_target(_target()) is True
    assert gwanak.is_target(
        Target(gwanak.GWANAK_EDUCATION_PROVIDER, gwanak.GWANAK_EDUCATION_LEGACY_URL)
    ) is False
    assert gwanak.is_target(Target("MUNI_UNOWNED", gwanak.GWANAK_EDUCATION_URL)) is False


def test_current_official_status_labels_are_mapped() -> None:
    assert gwanak.GWANAK_STATUS_MAP["2차 접수 대기"] == "WAITING"
    assert gwanak.GWANAK_STATUS_MAP["접수완료"] == "CLOSED"


def test_complete_main_and_knowledge_union_filters_expired_and_test_records() -> None:
    main_pages, knowledge_pages, details = _complete_fixture()
    dedupe_calls: list[list[dict[str, Any]]] = []

    def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        dedupe_calls.append(rows)
        return rows

    rows, parser, meta = gwanak.collect_gwanak_education_courses(
        _target(),
        max_pages=2,
        detail_limit=10,
        today="2026-07-19",
        fetcher=_fetcher(main_pages, knowledge_pages, details),
        session_factory=DummySession,
        dedupe_rows=dedupe,
    )

    assert parser == gwanak.GWANAK_EDUCATION_PARSER
    assert len(rows) == 2
    by_id = {row["raw_fields"]["lecture_id"]: row for row in rows}
    main = by_id["L00000001"]
    knowledge = by_id["L00001001"]
    assert main["provider_course_id"].endswith(":lecture:L00000001")
    assert knowledge["provider_course_id"].endswith(":knowledge:L00001001")
    assert main["branch"] == "평생학습관 5층"
    assert knowledge["branch"] == "평생학습관"
    assert main["capacity_current"] == 3
    assert main["capacity_total"] == 20
    assert main["waitlist_current"] == 1
    assert main["waitlist_total"] == 5
    assert main["reservation_available"] is True
    assert knowledge["reservation_available"] is True
    assert main["application_url"] == main["raw_url"]
    assert knowledge["application_url"] == knowledge["raw_url"]
    for row in rows:
        assert row["municipality_code"] == "1162000000"
        assert row["domain_category"] == "교육·강좌"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["preserve_branch"] is True

    assert len(dedupe_calls) == 1
    assert meta["pages"] == 3
    assert meta["declared_pages"] == 3
    assert meta["source_total_count"] == 5
    assert meta["main_total_count"] == 3
    assert meta["knowledge_total_count"] == 2
    assert meta["raw_current_count"] == 3
    assert meta["current_count"] == 2
    assert meta["expired_count"] == 2
    assert meta["invalid_test_count"] == 1
    assert meta["raw_current_section_counts"] == {"main": 2, "knowledge": 1}
    assert meta["current_section_counts"] == {"main": 1, "knowledge": 1}
    assert meta["current_organization_counts"] == {"29000100": 1}
    assert meta["cross_source_duplicate_count"] == 0
    assert meta["detail_required_count"] == 2
    assert meta["detail_attempts"] == 2
    assert meta["detail_pages"] == 2
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False


def test_transient_empty_detail_shell_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_pages, knowledge_pages, details = _complete_fixture()
    base_fetch = _fetcher(main_pages, knowledge_pages, details)
    detail_calls = 0

    def fetch(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
        nonlocal detail_calls
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if (
            parsed.path == gwanak.GWANAK_MAIN_DETAIL_PATH
            and query.get("clIdx") == ["L00000001"]
        ):
            detail_calls += 1
            if detail_calls == 1:
                return _soup("<html><body><div>temporary shell</div></body></html>")
        return base_fetch(current_session, url, timeout)

    monkeypatch.setattr(gwanak.time, "sleep", lambda _seconds: None)
    rows, _parser, meta = gwanak.collect_gwanak_education_courses(
        _target(),
        max_pages=2,
        detail_limit=10,
        today="2026-07-19",
        fetcher=fetch,
        session_factory=DummySession,
    )

    assert len(rows) == 2
    assert detail_calls == 2
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True


def test_transient_list_fetch_exception_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_pages, knowledge_pages, details = _complete_fixture()
    base_fetch = _fetcher(main_pages, knowledge_pages, details)
    page_two_calls = 0

    def fetch(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
        nonlocal page_two_calls
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if (
            parsed.path == gwanak.GWANAK_MAIN_LIST_PATH
            and query.get("pageIndex") == ["2"]
        ):
            page_two_calls += 1
            if page_two_calls == 1:
                raise ConnectionError("temporary response truncation")
        return base_fetch(current_session, url, timeout)

    monkeypatch.setattr(gwanak.time, "sleep", lambda _seconds: None)
    rows, _parser, meta = gwanak.collect_gwanak_education_courses(
        _target(),
        max_pages=2,
        detail_limit=10,
        today="2026-07-19",
        fetcher=fetch,
        session_factory=DummySession,
    )

    assert len(rows) == 2
    assert page_two_calls == 2
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True


def test_page_and_detail_caps_fail_closed() -> None:
    main_pages, knowledge_pages, details = _complete_fixture()
    fetch = _fetcher(main_pages, knowledge_pages, details)

    page_rows, _parser, page_meta = gwanak.collect_gwanak_education_courses(
        _target(),
        max_pages=1,
        detail_limit=10,
        today="2026-07-19",
        fetcher=fetch,
        session_factory=DummySession,
    )
    assert len(page_rows) == 2
    assert page_meta["source_cap_reached"] is True
    assert page_meta["snapshot_complete"] is False
    assert "main: max_pages cap reached after 1 of 2" in page_meta["configured_collection_error"]

    detail_rows, _parser, detail_meta = gwanak.collect_gwanak_education_courses(
        _target(),
        max_pages=2,
        detail_limit=1,
        today="2026-07-19",
        fetcher=fetch,
        session_factory=DummySession,
    )
    assert len(detail_rows) == 1
    assert detail_meta["detail_required_count"] == 2
    assert detail_meta["detail_attempts"] == 1
    assert detail_meta["source_cap_reached"] is True
    assert detail_meta["snapshot_complete"] is False
    assert "detail_limit cap allows 1 of 2" in detail_meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("detail_changes", "message"),
    [
        ({"title": "다른 제목"}, "detail/list title mismatch"),
        ({"status": "접수 마감", "application_control": False}, "detail/list status mismatch"),
        ({"omit": "교육기간"}, "missing detail fields 교육기간"),
    ],
)
def test_any_current_detail_drift_fails_closed(
    detail_changes: dict[str, Any], message: str
) -> None:
    main_pages = {
        1: _main_page(
            _main_row("L00002001", "원본 제목"), total=1, current=1, pages=1
        )
    }
    knowledge_pages = {1: _knowledge_page(total=0, current=1, pages=1)}
    options: dict[str, Any] = {"identity": "L00002001", "title": "원본 제목"}
    options.update(detail_changes)
    details = {("main", "L00002001"): _detail(**options)}

    rows, _parser, meta = gwanak.collect_gwanak_education_courses(
        _target(),
        max_pages=1,
        detail_limit=1,
        today="2026-07-19",
        fetcher=_fetcher(main_pages, knowledge_pages, details),
        session_factory=DummySession,
    )

    assert rows == []
    assert meta["detail_errors"] >= 1
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_duplicate_official_identity_and_total_mismatch_are_incomplete() -> None:
    duplicate = _main_row("L00003001", "중복 강좌")
    main_pages = {
        1: _main_page(duplicate, total=2, current=1, pages=2),
        2: _main_page(duplicate, total=2, current=2, pages=2),
    }
    knowledge_pages = {1: _knowledge_page(total=0, current=1, pages=1)}
    details = {("main", "L00003001"): _detail("L00003001", "중복 강좌")}

    rows, _parser, meta = gwanak.collect_gwanak_education_courses(
        _target(),
        max_pages=2,
        detail_limit=2,
        today="2026-07-19",
        fetcher=_fetcher(main_pages, knowledge_pages, details),
        session_factory=DummySession,
    )

    assert len(rows) == 1
    assert meta["duplicate_count"] == 1
    assert meta["pagination_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "duplicate official lecture ID L00003001" in meta["configured_collection_error"]
    assert "declared total 2 does not match 1 unique" in meta["configured_collection_error"]


def test_main_and_knowledge_identity_overlap_is_incomplete() -> None:
    shared = "L00004001"
    main_pages = {
        1: _main_page(_main_row(shared, "메인 강좌"), total=1, current=1, pages=1)
    }
    knowledge_pages = {
        1: _knowledge_page(
            _knowledge_card(shared, "재능 강좌"), total=1, current=1, pages=1
        )
    }
    details = {
        ("main", shared): _detail(shared, "메인 강좌"),
        ("knowledge", shared): _detail(
            shared,
            "재능 강좌",
            source_kind="knowledge",
            start="2026-07-20",
            end="2026-08-20",
            applicants="0 / (0) 명",
        ),
    }

    _rows, _parser, meta = gwanak.collect_gwanak_education_courses(
        _target(),
        max_pages=1,
        detail_limit=2,
        today="2026-07-19",
        fetcher=_fetcher(main_pages, knowledge_pages, details),
        session_factory=DummySession,
    )

    assert meta["cross_source_duplicate_count"] == 1
    assert meta["snapshot_complete"] is False
    assert "main/knowledge overlap contains 1" in meta["configured_collection_error"]


def test_open_detail_requires_exact_official_application_control() -> None:
    main_pages = {
        1: _main_page(
            _main_row("L00005001", "신청 제어 검증"), total=1, current=1, pages=1
        )
    }
    knowledge_pages = {1: _knowledge_page(total=0, current=1, pages=1)}
    details = {
        ("main", "L00005001"): _detail(
            "L00005001", "신청 제어 검증", application_control=False
        )
    }

    rows, _parser, meta = gwanak.collect_gwanak_education_courses(
        _target(),
        max_pages=1,
        detail_limit=1,
        today="2026-07-19",
        fetcher=_fetcher(main_pages, knowledge_pages, details),
        session_factory=DummySession,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "open detail has no exact application control" in meta["configured_collection_error"]


def test_non_open_knowledge_control_is_audited_but_not_exposed() -> None:
    main_pages = {1: _main_page(total=0, current=1, pages=1)}
    knowledge_pages = {
        1: _knowledge_page(
            _knowledge_card(
                "L00005002", "이미 시작한 재능 강좌", status="강좌시작"
            ),
            total=1,
            current=1,
            pages=1,
        )
    }
    details = {
        ("knowledge", "L00005002"): _detail(
            "L00005002",
            "이미 시작한 재능 강좌",
            source_kind="knowledge",
            status="강좌시작",
            start="2026-07-20",
            end="2026-08-20",
            applicants="0 / (0) 명",
            application_control=True,
        )
    }

    rows, _parser, meta = gwanak.collect_gwanak_education_courses(
        _target(),
        max_pages=1,
        detail_limit=1,
        today="2026-07-19",
        fetcher=_fetcher(main_pages, knowledge_pages, details),
        session_factory=DummySession,
    )

    assert len(rows) == 1
    assert rows[0]["reservation_available"] is False
    assert "application_url" not in rows[0]
    assert rows[0]["raw_fields"]["detail_application_control"] is True
    assert meta["snapshot_complete"] is True


def test_unsafe_target_never_performs_network_io() -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unsafe target must not start HTTP")

    rows, parser, meta = gwanak.collect_gwanak_education_courses(
        Target(
            gwanak.GWANAK_EDUCATION_PROVIDER,
            "https://evil.example/site/edu/lecture/Lecture_List.do",
        ),
        fetcher=forbidden,
        session_factory=forbidden,
    )

    assert rows == []
    assert parser == gwanak.GWANAK_EDUCATION_PARSER
    assert meta["snapshot_complete"] is False
    assert "provider-owned canonical" in meta["configured_collection_error"]


def test_detail_url_rejects_unknown_identity_or_organization() -> None:
    assert gwanak.gwanak_detail_url("main", "L00000001", "29000100").startswith(
        "https://www.gwanak.go.kr/site/edu/lecture/Lecture_View.do?"
    )
    assert gwanak.gwanak_detail_url("knowledge", "L00000001").endswith(
        "Knowledge_Lecture_View.do?clIdx=L00000001"
    )
    assert gwanak.gwanak_detail_url("main", "../../L00000001", "29000100") == ""
    assert gwanak.gwanak_detail_url("main", "L00000001", "99999999") == ""
