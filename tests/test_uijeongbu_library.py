from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as municipal_yaml
from Crawler import municipal_uijeongbu_library as uilib


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SourceRow:
    branch: str
    group: str
    category_id: str
    teach: str
    title: str
    category: str
    course_period: str
    apply_period: str
    status_class: str
    status_label: str
    capacity: str = "온라인 1 / 10 (대기자 0 / 2)"
    venue: str = "프로그램실"
    target: str = "의정부시민"
    detail_category: str = "문화강좌"
    description: str = "교육 강좌"


ROWS = (
    SourceRow(
        "information",
        "1",
        "1",
        "1001",
        "기록과 삶 작가 강연",
        "문화행사",
        "2026-08-05 ~ 2026-09-16",
        "2026-07-01 10:00 ~ 2026-09-15 18:00",
        "status_0",
        "수강신청",
        detail_category="문화행사 독서교실",
        description="인문학 작가 강연과 기록 수업",
    ),
    SourceRow(
        "science",
        "10",
        "1",
        "1002",
        "어린이 과학 읽기",
        "문화강좌",
        "2026-08-08",
        "2026-07-01 10:00 ~ 2026-08-07 18:00",
        "status_4",
        "접수마감",
    ),
    SourceRow(
        "art",
        "7",
        "0",
        "1003",
        "미술도서관 정기 투어",
        "도서관 투어(개인)",
        "2026-08-10",
        "2026-07-01 10:00 ~ 2026-08-09 18:00",
        "status_5",
        "정원마감",
        detail_category="도서관 투어(개인)",
        description="미술도서관 전시 공간 투어",
    ),
    SourceRow(
        "music",
        "1",
        "1",
        "1004",
        "여름 음악 공연",
        "문화행사",
        "2026-08-12",
        "2026-07-01 10:00 ~ 2026-08-11 18:00",
        "status_4",
        "접수마감",
        detail_category="문화행사 공연",
        description="연주자와 함께하는 순수 음악 공연",
    ),
    SourceRow(
        "music",
        "1",
        "2",
        "1005",
        "프로그램 변경 안내",
        "공지사항",
        "2026-08-13",
        "2026-07-01 10:00 ~ 2026-08-12 18:00",
        "status_4",
        "접수마감",
        detail_category="공지사항",
        description="운영 일정 변경 안내 공지",
    ),
    SourceRow(
        "gajaeul",
        "2",
        "3",
        "1006",
        "지난 독서 강좌",
        "문화강좌",
        "2026-07-01 ~ 2026-08-01",
        "2026-06-01 10:00 ~ 2026-06-30 18:00",
        "status_9",
        "수강종료",
    ),
    SourceRow(
        "english",
        "6",
        "0",
        "1007",
        "English Friends",
        "English Friends",
        "2026-08-12 ~ 2026-08-14",
        "2026-07-01 10:00 ~ 2026-08-11 18:00",
        "status_0",
        "수강신청",
        detail_category="English Friends",
        description="영어로 소통하며 여러 나라 문화를 배우는 수업",
    ),
    SourceRow(
        "english",
        "5",
        "0",
        "1008",
        "영리더 모집",
        "영어책 읽어주기 봉사단",
        "2026-08-22",
        "2026-07-01 10:00 ~ 2026-08-21 18:00",
        "status_4",
        "접수마감",
        capacity="온라인 2 / 2 (대기자 0 / 1)",
        detail_category="영어책 읽어주기 봉사단",
        description="영어그림책을 읽어주는 봉사 활동이며 봉사시간을 제공합니다.",
    ),
)


class FakeResponse:
    status_code = 200
    history: list[object] = []

    def __init__(self, url: str, html: str):
        self.url = url
        self.content = html.encode("utf-8")
        self.text = html


class FakeSession:
    def __init__(self, routes: dict[str, str | list[str]]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.calls.append(url)
        value = self.routes[url]
        if isinstance(value, list):
            html = value.pop(0) if len(value) > 1 else value[0]
        else:
            html = value
        return FakeResponse(url, html)

    def close(self) -> None:
        return None


@dataclass
class Target:
    provider: str = uilib.UIJEONGBU_LIBRARY_PROVIDER
    url: str = uilib.UIJEONGBU_LIBRARY_URL
    name: str = "의정부시립도서관 6개관 전체 교육 프로그램"
    branch: str = "경기도 의정부시"


def _directory_html(*, missing: str = "") -> str:
    links = "".join(
        f'<a href="/{branch.key}/index.do">{branch.short_name}</a>'
        for branch in uilib.UIJEONGBU_LIBRARY_BRANCHES
        if branch.key != missing
    )
    return (
        "<html><head><title>의정부시 도서관 대표홈페이지</title></head>"
        f"<body>{links}</body></html>"
    )


def _row_html(row: SourceRow, *, bad_application: bool = False) -> str:
    application = ""
    if row.status_class == "status_0":
        application_teach = "9999" if bad_application else row.teach
        application = (
            '<a class="btn add" href="" keyvalue1="h3" '
            f'keyvalue2="{row.group}" keyvalue3="{row.category_id}" '
            f'keyvalue4="{application_teach}" keyvalue5="16" '
            'apply_status="1">수강신청</a>'
        )
    else:
        application = f'<a href="javascript:void(0);">{row.status_label}</a>'
    return f"""
    <tr class="{row.status_class}">
      <td><dl><dd><span class="ca">{row.category}</span></dd>
        <dt><a class="name detail-btn" href="" keyvalue1="{row.group}"
          keyvalue2="{row.category_id}" keyvalue3="{row.teach}">{row.title}</a></dt>
        <dd class="con">장소 : {row.venue}</dd>
        <dd class="con">대상 : {row.target}</dd></dl></td>
      <td>{row.capacity}</td><td>{row.course_period}</td>
      <td>{row.apply_period}</td><td>{application}</td>
    </tr>
    """


def _list_html(
    branch: uilib.UijeongbuLibraryBranch,
    rows: list[SourceRow],
    *,
    pagination: bool = False,
    bad_application: bool = False,
) -> str:
    page_field = '<input name="viewPage" value="1">' if pagination else ""
    body = "".join(
        _row_html(row, bad_application=bad_application and row.status_class == "status_0")
        for row in rows
    )
    headers = "".join(f"<th>{value}</th>" for value in uilib._TABLE_HEADERS)
    return f"""
    <html><head><title>{branch.name} &gt; 책문화프로그램 &gt; 프로그램신청</title></head>
    <body><h3>프로그램신청</h3>
      <form id="teach" method="post" action="/{branch.key}/module/teach/student/save.do">
        <input type="hidden" name="menu_idx" value="24">{page_field}
      </form>
      <table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>
    </body></html>
    """


def _date_pair(value: str) -> tuple[str, str]:
    matches = uilib._DATE_RE.findall(value)
    values = [f"{year}-{int(month):02d}-{int(day):02d}" for year, month, day in matches]
    return values[0], values[-1]


def _detail_html(row: SourceRow, *, title: str | None = None, bad_period: bool = False) -> str:
    start, end = _date_pair(row.course_period)
    if bad_period:
        end = "2026-12-31"
    current, total = uilib._CAPACITY_RE.findall(row.capacity)[0]
    values = (
        ("강의 분류", row.detail_category),
        ("강의 설명", row.description),
        ("강의장소", row.venue),
        ("강의대상", row.target),
        ("접수기간", row.apply_period),
        ("강의기간(*)", f"{start} ~ {end}"),
        ("강의시간", "10:00 ~ 12:00"),
        ("강의요일", "토"),
        ("현재 참여 / 모집", f"{current} 명 / {total} 명"),
        ("강사명", "테스트 강사"),
    )
    table = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in values)
    branch = next(item for item in uilib.UIJEONGBU_LIBRARY_BRANCHES if item.key == row.branch)
    return f"""
    <html><head><title>{branch.name} &gt; 책문화프로그램 &gt; 프로그램신청</title></head>
    <body><h3>프로그램신청</h3><h3>{title or row.title}</h3>
      <table>{table}</table></body></html>
    """


def _routes(
    *,
    missing_branch: str = "",
    pagination_branch: str = "",
    bad_application_branch: str = "",
    bad_detail_teach: str = "",
    unstable_branch: str = "",
) -> dict[str, str | list[str]]:
    routes: dict[str, str | list[str]] = {
        uilib.UIJEONGBU_LIBRARY_URL: _directory_html(missing=missing_branch)
    }
    for branch in uilib.UIJEONGBU_LIBRARY_BRANCHES:
        branch_rows = [row for row in ROWS if row.branch == branch.key]
        stable = _list_html(
            branch,
            branch_rows,
            pagination=branch.key == pagination_branch,
            bad_application=branch.key == bad_application_branch,
        )
        if branch.key == unstable_branch:
            changed = stable.replace(branch_rows[0].title, f"{branch_rows[0].title} 변경")
            routes[branch.list_url] = [stable, changed]
        else:
            routes[branch.list_url] = stable
        for row in branch_rows:
            if _date_pair(row.course_period)[-1] < "2026-08-05":
                continue
            detail_url = uilib.uijeongbu_library_detail_url(
                row.branch, row.group, row.category_id, row.teach
            )
            routes[detail_url] = _detail_html(
                row, bad_period=row.teach == bad_detail_teach
            )
    return routes


def _target() -> Target:
    return Target()


def test_complete_six_library_snapshot_is_education_only_and_safe() -> None:
    session = FakeSession(_routes())
    rows, parser, meta = uilib.collect_uijeongbu_library_courses(
        _target(),
        today="2026-08-05",
        max_pages=14,
        detail_limit=7,
        session_factory=lambda: session,
    )

    assert parser == uilib.UIJEONGBU_LIBRARY_PARSER
    assert [row["title"] for row in rows] == [
        "기록과 삶 작가 강연",
        "어린이 과학 읽기",
        "English Friends",
    ]
    assert {row["service_group"] for row in rows} == {"공공강좌"}
    assert {row["service_group_policy"] for row in rows} == {"locked"}
    assert {row["municipality_code"] for row in rows} == {"4115000000"}
    assert all("테스트 강사" not in str(row) for row in rows)
    assert sum(bool(row["application_url"]) for row in rows) == 2
    assert all(
        not any(token in url.lower() for token in ("/student/", "login", "download"))
        for url in session.calls
    )
    assert meta["source_total"] == 8
    assert meta["current_candidates"] == 7
    assert meta["education_current"] == 3
    assert meta["experience_current"] == 1
    assert meta["excluded_non_course_count"] == 4
    assert meta["exclusion_counts"] == {
        "library_tour_or_path_exploration": 1,
        "performance_without_education_contract": 1,
        "notice": 1,
        "volunteer_recruitment": 1,
    }
    assert meta["directory_requests"] == 2
    assert meta["list_requests"] == 12
    assert meta["detail_pages"] == 7
    assert meta["physical_requests"] == 21
    assert meta["snapshot_complete"] is True
    assert meta["application_endpoints_called"] == 0
    assert meta["pii_payload_persisted"] is False


@pytest.mark.parametrize(
    ("routes", "error"),
    [
        (_routes(missing_branch="art"), "directory ownership changed"),
        (_routes(pagination_branch="science"), "pagination field"),
        (_routes(bad_application_branch="information"), "not bound"),
        (_routes(bad_detail_teach="1002"), "period does not match"),
        (_routes(unstable_branch="music"), "ledger changed"),
    ],
)
def test_contract_drift_returns_no_partial_rows(
    routes: dict[str, str | list[str]], error: str
) -> None:
    rows, _, meta = uilib.collect_uijeongbu_library_courses(
        _target(), today="2026-08-05", session_factory=lambda: FakeSession(routes)
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in meta["configured_collection_error"]


def test_caps_are_fail_closed_before_partial_snapshot() -> None:
    rows, _, page_meta = uilib.collect_uijeongbu_library_courses(
        _target(), max_pages=13, session_factory=lambda: FakeSession(_routes())
    )
    assert rows == []
    assert page_meta["source_cap_reached"] is True
    assert page_meta["list_requests"] == 0

    rows, _, detail_meta = uilib.collect_uijeongbu_library_courses(
        _target(),
        today="2026-08-05",
        detail_limit=6,
        session_factory=lambda: FakeSession(_routes()),
    )
    assert rows == []
    assert detail_meta["source_cap_reached"] is True
    assert detail_meta["detail_attempts"] == 0


def test_target_and_detail_url_contracts_reject_private_or_ambiguous_paths() -> None:
    assert uilib.is_uijeongbu_library_target(_target())
    for value in (
        "http://www.uilib.go.kr/main/index.do",
        "https://www.uilib.go.kr/main/index.do?x=1",
        "https://www.uilib.go.kr/english/module/teach/index.do?menu_idx=24",
    ):
        assert not uilib.is_uijeongbu_library_target(
            {"provider": uilib.UIJEONGBU_LIBRARY_PROVIDER, "url": value}
        )
    assert "category_idx=0" in uilib.uijeongbu_library_detail_url(
        "english", "6", "0", "1007"
    )
    with pytest.raises(uilib.UijeongbuLibraryContractError):
        uilib.uijeongbu_library_detail_url("english", "6", "0", "0")
    with pytest.raises(uilib.UijeongbuLibraryContractError):
        uilib._validate_public_url(
            "https://www.uilib.go.kr/english/module/teach/student/edit.do"
        )


def test_dedupe_may_not_change_ordered_complete_snapshot() -> None:
    rows, _, meta = uilib.collect_uijeongbu_library_courses(
        _target(),
        today="2026-08-05",
        session_factory=lambda: FakeSession(_routes()),
        dedupe_rows=lambda rows: rows[:-1],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_exact_dispatch_and_operational_target_linkage(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = ([{"title": "dispatched"}], uilib.UIJEONGBU_LIBRARY_PARSER, {"ok": True})
    monkeypatch.setattr(uilib, "collect_uijeongbu_library_courses", lambda *_a, **_k: marker)
    assert municipal_yaml.collect_from_url(
        _target(), max_depth=0, max_pages=14, detail_limit=7
    ) == marker

    target_doc = yaml.safe_load(
        (ROOT / "config/crawl_targets/municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    targets = target_doc if isinstance(target_doc, list) else target_doc["targets"]
    matches = [
        target for target in targets if target.get("provider") == uilib.UIJEONGBU_LIBRARY_PROVIDER
    ]
    assert len(matches) == 1
    target = matches[0]
    assert target["url"] == uilib.UIJEONGBU_LIBRARY_URL
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert target["municipality_code"] == "4115000000"
    assert target["full_snapshot_required"] is True

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    entries = operational["entries"]
    linked = [
        entry for entry in entries if entry.get("provider") == uilib.UIJEONGBU_LIBRARY_PROVIDER
    ]
    assert len(linked) == 1
    assert linked[0]["validation_outcome"] == "collected"
    assert linked[0]["row_count"] == 18
