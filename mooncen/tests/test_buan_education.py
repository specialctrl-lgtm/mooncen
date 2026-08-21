from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_buan as buan


@dataclass(frozen=True)
class _Record:
    category: str
    identity: str
    title: str
    period: str
    apply: str
    venue: str
    branch: str = ""
    schedule: str = "매주 화요일 10:00~12:00"
    target: str = "부안군민"
    capacity: str = "12명"
    fee: str = "무료"
    control: bool = False


RECORDS = (
    _Record(
        "lifelong",
        "BURE0000001",
        "모두배움터 지난 강좌",
        "2026-01-01 ~ 2026-01-31",
        "2025-12-01 ~ 2025-12-10",
        "나무공방",
        "모두배움터",
    ),
    _Record(
        "lifelong",
        "BURE0000002",
        "테스트입니다.",
        "2026-01-01 ~ 2026-01-02",
        "2025-12-01 ~ 2025-12-02",
        "임시 장소",
        "모두배움터",
    ),
    _Record(
        "lifelong",
        "BURE0000003",
        "청우 지난 강좌",
        "2026-02-01 ~ 2026-02-28",
        "2026-01-01 ~ 2026-01-10",
        "청우평생학습관 다목적실",
        "청우평생학습관",
    ),
    _Record(
        "culture",
        "BURE0000100",
        "옹기종기 지난 강좌",
        "2026-02-01 ~ 2026-03-01",
        "2026-01-01 ~ 2026-01-10",
        "옹기종기문화센터",
    ),
    _Record(
        "arts",
        "BURE0000200",
        "예술회관 지난 강좌",
        "2024-01-01 ~ 2024-02-01",
        "2023-12-01 ~ 2023-12-10",
        "부안예술회관",
    ),
    _Record(
        "media",
        "365900",
        "미디어 현재 접수 강좌",
        "2026-08-18 ~ 2026-12-08",
        "2026-07-20 ~ 2026-08-07",
        "부안미디어센터",
        control=True,
    ),
    _Record(
        "media",
        "365896",
        "2026년 제3기 미디어교육",
        "2026-08-04 ~ 2026-11-12",
        "2026-07-20 ~ 2026-07-31",
        "부안미디어센터",
        schedule="포스터 참조 (교육별 상이)",
        capacity="교육당 12명",
    ),
    _Record(
        "media",
        "358030",
        "2026년 상반기 부안군 소상공인 라이브커머스 참여자 모집",
        "2026-02-25 ~ 2027-06-22",
        "2026-01-26 ~ 2026-02-13",
        "부안미디어센터",
    ),
    _Record(
        "media",
        "358029",
        "진행 중인 군민 미디어 과정",
        "2026-03-01 ~ 2026-11-30",
        "2026-01-26 ~ 2026-02-13",
        "부안미디어센터",
    ),
    *(
        _Record(
            "media",
            str(350000 - index),
            f"종료 미디어 강좌 {index}",
            "2025-01-01 ~ 2025-02-01",
            "2024-12-01 ~ 2024-12-10",
            "부안미디어센터",
        )
        for index in range(6)
    ),
)


def _target(
    *,
    provider: str = buan.BUAN_PROVIDER,
    url: str = buan.BUAN_CANONICAL_URL,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "url": url,
        "name": "부안군 통합예약 교육강좌",
        "branch": buan.BUAN_MUNICIPALITY_NAME,
        "extra": {},
    }


def _menu(*, drift: bool = False) -> str:
    links = [
        ("평생학습", "/reserve/index.buan?menuCd=DOM_000002001001000000"),
        ("옹기종기문화센터", "/reserve/index.buan?menuCd=DOM_000002001002000000"),
        ("예술회관", "/reserve/index.buan?menuCd=DOM_000002001003000000"),
        ("미디어센터", "/reserve/index.buan?menuCd=DOM_000002001007000000"),
    ]
    if drift:
        links.append(
            ("새 교육시설", "/reserve/index.buan?menuCd=DOM_000002001009000000")
        )
    return (
        '<li class="menu1"><div class="depth_boxcon"><ul>'
        + "".join(f'<li><a href="{href}">{label}</a></li>' for label, href in links)
        + "</ul></div></li>"
    )


def _root_html(*, menu_drift: bool = False) -> str:
    return f"<html><head><title>교육강좌 &gt; 평생학습</title></head><body>{_menu(drift=menu_drift)}</body></html>"


def _detail_href(record: _Record) -> str:
    category = buan.BUAN_CATEGORY_BY_KEY[record.category]
    if record.category == "media":
        return (
            "/reserve/board/view.buan?boardId=BBS_0000237&"
            "menuCd=DOM_000002001007000000&paging=ok&startPage=1&"
            f"dataSid={record.identity}"
        )
    query = parse_qs(urlparse(category.url).query)
    sid = query["rsvCateSid"][0]
    menu = {
        "lifelong": "DOM_000002001001001000",
        "culture": "DOM_000002001002001000",
        "arts": "DOM_000002001003001000",
    }[record.category]
    return f"/index.buan?menuCd={menu}&rsvCateSid={sid}&reUniqId={record.identity}"


def _card(record: _Record) -> str:
    label = buan.BUAN_CATEGORY_BY_KEY[record.category].label
    return f"""
      <div><dl><dt><a href="{_detail_href(record)}" title="{record.title}">
        {record.title}</a></dt>
        <dd><span><i>{label}</i><i>{record.fee}</i></span></dd>
        <dd><strong>진행장소</strong>{record.venue}</dd>
        <dd><strong>이용대상</strong>{record.target}</dd>
        <dd><strong>접수기간</strong>{record.apply}</dd>
        <dd><strong>교육기간</strong>{record.period}</dd>
        <dd><strong>교육시간</strong>{record.schedule}</dd>
        <dd><strong>모집인원</strong>{record.capacity}</dd>
      </dl></div>
    """


def _pagination(category: str, current: int, last: int) -> str:
    if category == "media":
        links = "".join(
            (
                f'<a class="{"on" if page == current else ""}" '
                f'href="/reserve/board/list.buan?boardId=BBS_0000237&startPage={page}">{page}</a>'
            )
            for page in range(1, last + 1)
        )
    else:
        links = "".join(
            (
                f'<a class="{"on" if page == current else ""}" '
                f'onclick="link_go({page}); return false">{page}</a>'
            )
            for page in range(1, last + 1)
        )
    return f'<p class="bbs_page">{links}</p>'


def _branch_tabs(*, drift: bool = False) -> str:
    values = ["모두배움터", "청우평생학습관"]
    if drift:
        values.append("임의학습관")
    return (
        '<div class="basic_tab2"><a href="/reserve/index.buan?menuCd=DOM_000002001001000000&rsvCateSid=22">전체</a>'
        + "".join(
            f'<a href="/reserve/index.buan?menuCd=DOM_000002001001000000&rsvCateSid=22&eduPlaceCode={value}">{value}</a>'
            for value in values
        )
        + "</div>"
    )


def _list_html(
    category: str,
    rows: list[_Record],
    *,
    current: int,
    last: int,
    branch_drift: bool = False,
) -> str:
    label = buan.BUAN_CATEGORY_BY_KEY[category].label
    cards = "".join(_card(record) for record in rows)
    if not rows:
        cards = '<div><p>프로그램이 없습니다.</p></div>'
    tabs = _branch_tabs(drift=branch_drift) if category == "lifelong" else ""
    return f"""
      <html><head><title>교육강좌 &gt; {label}</title></head><body>
        {_menu()}{tabs}<div class="ed_list">{cards}</div>
        {_pagination(category, current, last)}
      </body></html>
    """


def _detail_html(record: _Record, *, title_override: str = "") -> str:
    control = ""
    if record.control:
        control = (
            '<div class="bbs_btn"><a class="bbs_bt2" '
            'href="/reserve/index.buan?menuCd=DOM_000002001001002000&'
            f'reUniqId={record.identity}">신청하기</a></div>'
        )
    return f"""
      <html><head><title>교육강좌 &gt; 상세페이지</title></head><body>
        <div class="bbs_view"><div class="bbs_vtop">
          <h4>{title_override or record.title}</h4><ul>
            <li><strong>교육기간</strong><span>{record.period}</span></li>
            <li><strong>교육시간</strong><span>{record.schedule}</span></li>
            <li><strong>교육대상</strong><span>{record.target}</span></li>
            <li><strong>교육장소</strong><span>{record.venue}</span></li>
            <li><strong>모집인원</strong><span>{record.capacity}</span></li>
            <li><strong>접수기간</strong><span>{record.apply}</span></li>
            <li><strong>강사명</strong><span>홍길동</span></li>
            <li><strong>문의</strong><span>063-580-0000</span></li>
          </ul></div>{control}
          <div class="bbs_con">담당자 person@example.com / 신청자 정보 없음</div>
        </div>
      </body></html>
    """


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FixtureFetcher:
    def __init__(
        self,
        *,
        menu_drift: bool = False,
        branch_drift: bool = False,
        detail_mismatch: str = "",
        unstable_media_first: bool = False,
        bad_media_sentinel: bool = False,
    ) -> None:
        self.menu_drift = menu_drift
        self.branch_drift = branch_drift
        self.detail_mismatch = detail_mismatch
        self.unstable_media_first = unstable_media_first
        self.bad_media_sentinel = bad_media_sentinel
        self.calls: list[str] = []
        self.counts: dict[str, int] = {}

    def __call__(self, _session: Any, url: str, _timeout: int) -> str:
        self.calls.append(url)
        self.counts[url] = self.counts.get(url, 0) + 1
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        menu = (query.get("menuCd") or [""])[0]
        if menu.endswith("002000"):
            raise AssertionError("application endpoint must never be fetched")
        if url == buan.BUAN_CANONICAL_URL:
            return _root_html(menu_drift=self.menu_drift)

        record: _Record | None = None
        if parsed.path.endswith("/board/view.buan"):
            identity = (query.get("dataSid") or [""])[0]
            record = next(item for item in RECORDS if item.identity == identity)
        elif (query.get("reUniqId") or [""])[0]:
            identity = query["reUniqId"][0]
            record = next(item for item in RECORDS if item.identity == identity)
        if record is not None:
            override = "다른 상세 제목" if record.identity == self.detail_mismatch else ""
            return _detail_html(record, title_override=override)

        if parsed.path.endswith("/board/list.buan"):
            category = "media"
            page = int((query.get("startPage") or ["1"])[0])
            branch = ""
        else:
            sid = (query.get("rsvCateSid") or [""])[0]
            category = {"22": "lifelong", "45": "culture", "46": "arts"}[sid]
            page = int((query.get("pageIndex") or ["1"])[0])
            branch = (query.get("eduPlaceCode") or [""])[0]

        rows = [item for item in RECORDS if item.category == category]
        if branch:
            rows = [item for item in rows if item.branch == branch]
        pages = [
            rows[index : index + buan.BUAN_PAGE_SIZE]
            for index in range(0, len(rows), buan.BUAN_PAGE_SIZE)
        ] or [[]]
        last = len(pages)
        if page > last:
            if category == "lifelong" and branch == "청우평생학습관":
                selected: list[_Record] = []
                displayed = last
            elif self.bad_media_sentinel and category == "media":
                selected = [replace(RECORDS[5], identity="999999", title="경계 침입")]
                displayed = last
            else:
                selected = pages[-1]
                displayed = last
        else:
            selected = pages[page - 1]
            displayed = page
        if (
            self.unstable_media_first
            and category == "media"
            and page == 1
            and self.counts[url] >= 2
        ):
            selected = [replace(selected[0], title="변경된 제목"), *selected[1:]]
        return _list_html(
            category,
            selected,
            current=displayed,
            last=last,
            branch_drift=self.branch_drift,
        )


def _collect(fetcher: _FixtureFetcher, **kwargs: Any):
    session = _Session()
    rows, parser, meta = buan.collect_buan_education(
        _target(),
        today="2026-07-23",
        timeout=5,
        max_pages=3,
        detail_limit=10,
        session_factory=lambda: session,
        fetcher=fetcher,
        **kwargs,
    )
    assert session.closed is True
    return rows, parser, meta


def test_target_and_candidate_boundaries_are_exact() -> None:
    assert buan.is_buan_education_target(_target()) is True
    assert buan.is_buan_education_target(
        _target(url=buan.BUAN_CANONICAL_URL + "#fragment")
    ) is False
    assert buan.is_buan_education_target(
        _target(provider=buan.BUAN_PORTAL_PROVIDER, url=buan.BUAN_PORTAL_URL)
    ) is False
    assert buan.is_buan_lifelong_notice_target(
        _target(provider=buan.BUAN_PORTAL_PROVIDER, url=buan.BUAN_PORTAL_URL)
    ) is True
    assert (
        buan.BUAN_CANDIDATE_DECISIONS[buan.BUAN_PORTAL_CANDIDATE_ID]
        == "exclude_from_integrated_coverage_keep_separate_lifelong_notice_owner"
    )
    assert (
        buan.BUAN_CANDIDATE_DECISIONS[
            buan.BUAN_DISCOVERY_PLAYGROUND_CANDIDATE_ID
        ]
        == "exclude_child_playground_facility_information"
    )


def test_complete_snapshot_reconciles_categories_branches_and_current_details() -> None:
    fetcher = _FixtureFetcher()
    rows, parser, meta = _collect(fetcher)

    assert parser == buan.BUAN_PARSER
    assert [row["title"] for row in rows] == [
        "미디어 현재 접수 강좌",
        "진행 중인 군민 미디어 과정",
    ]
    assert [row["status"] for row in rows] == ["OPEN", "CLOSED"]
    assert {row["branch"] for row in rows} == {"부안미디어센터"}
    assert {row["address"] for row in rows} == {
        "전북특별자치도 부안군 부안읍 예술회관길 11"
    }
    assert rows[0]["application_type"] == "ONLINE_APPLICATION_CONTROL"
    assert rows[0]["reservation_available"] is True
    assert rows[1]["application_type"] == "INFO_ONLY_NO_HTML_CONTROL"
    assert all("description" not in row and "phone" not in row for row in rows)
    assert "person@example.com" not in repr(rows)
    assert "063-580-0000" not in repr(rows)

    assert meta["full_snapshot_validated"] is True
    assert meta["source_total"] == 15
    assert meta["category_counts"] == {
        "평생학습": 3,
        "옹기종기문화센터": 1,
        "예술회관": 1,
        "미디어센터": 10,
    }
    assert meta["category_pages"]["미디어센터"] == 2
    assert meta["branch_source_counts"] == {
        "모두배움터": 2,
        "청우평생학습관": 1,
    }
    assert meta["raw_current_source_count"] == 4
    assert meta["current_source_count"] == 3
    assert meta["returned_count"] == 2
    assert meta["source_date_correction_count"] == 1
    assert meta["semantic_rejection_reasons"] == {
        "test_record": 1,
        "aggregate_overview_duplicates_individual_media_courses": 1,
    }
    assert meta["current_status_counts"] == {"OPEN": 1, "CLOSED": 1}
    assert meta["application_control_count"] == 1
    assert meta["application_endpoint_requests"] == 0
    assert meta["applicant_list_requests"] == 0
    assert meta["logical_requests"] == 31
    assert meta["physical_requests"] == 31
    assert meta["list_requests"] == 27
    assert meta["detail_pages"] == 4
    assert meta["boundary_modes"]["media"] == "clamped_last_page"
    assert (
        meta["boundary_modes"]["lifelong:청우평생학습관"]
        == "structural_empty"
    )
    assert not any("DOM_000002001001002000" in url for url in fetcher.calls)


@pytest.mark.parametrize(
    ("fetcher", "message"),
    [
        (_FixtureFetcher(menu_drift=True), "active education menu vocabulary changed"),
        (_FixtureFetcher(branch_drift=True), "lifelong branch vocabulary changed"),
        (_FixtureFetcher(detail_mismatch="365900"), "detail title mismatch"),
        (_FixtureFetcher(unstable_media_first=True), "stability recheck changed"),
        (_FixtureFetcher(bad_media_sentinel=True), "neither exact clamp nor empty"),
    ],
)
def test_contract_drift_fails_closed(
    fetcher: _FixtureFetcher, message: str
) -> None:
    rows, _, meta = _collect(fetcher)
    assert rows == []
    assert meta["full_snapshot_validated"] is False
    assert message in meta["configured_collection_error"]


def test_limits_fail_closed_instead_of_returning_partial_rows() -> None:
    fetcher = _FixtureFetcher()
    rows, _, meta = buan.collect_buan_education(
        _target(),
        today=date(2026, 7, 23),
        timeout=5,
        max_pages=1,
        detail_limit=3,
        session_factory=_Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "exceeds max_pages=1" in meta["configured_collection_error"]

    rows, _, meta = buan.collect_buan_education(
        _target(),
        today=date(2026, 7, 23),
        timeout=5,
        max_pages=3,
        detail_limit=3,
        session_factory=_Session,
        fetcher=_FixtureFetcher(),
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "partial current/future snapshot" in meta["configured_collection_error"]


def test_retry_is_counted_and_snapshot_still_completes() -> None:
    base = _FixtureFetcher()
    failed = False

    def flaky(session: Any, url: str, timeout: int) -> str:
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("temporary failure")
        return base(session, url, timeout)

    rows, _, meta = buan.collect_buan_education(
        _target(),
        today="2026-07-23",
        timeout=5,
        max_pages=3,
        detail_limit=10,
        session_factory=_Session,
        fetcher=flaky,
    )
    assert len(rows) == 2
    assert meta["full_snapshot_validated"] is True
    assert meta["logical_requests"] == 31
    assert meta["physical_requests"] == 32
    assert meta["request_retry_count"] == 1


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_BUAN_TESTS") != "1",
    reason="set RUN_LIVE_BUAN_TESTS=1 for the audited official snapshot",
)
def test_live_buan_snapshot_2026_07_23() -> None:
    rows, parser, meta = buan.collect_buan_education(
        _target(),
        today="2026-07-23",
        timeout=30,
        max_pages=10,
        detail_limit=20,
    )
    assert parser == buan.BUAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["full_snapshot_validated"] is True
    assert meta["source_total"] == 79
    assert meta["category_counts"] == {
        "평생학습": 18,
        "옹기종기문화센터": 0,
        "예술회관": 2,
        "미디어센터": 59,
    }
    assert meta["branch_source_counts"] == {
        "모두배움터": 13,
        "청우평생학습관": 5,
    }
    assert meta["raw_current_source_count"] == 12
    assert meta["current_source_count"] == 11
    assert meta["semantic_rejected_current_count"] == 2
    assert meta["returned_count"] == len(rows) == 9
    assert meta["current_status_counts"] == {"OPEN": 7, "CLOSED": 2}
    assert meta["branch_counts"] == {"부안미디어센터": 9}
    assert meta["application_control_count"] == 0
    assert meta["logical_requests"] == 48
    assert meta["physical_requests"] == 48
    assert meta["detail_pages"] == 12
    assert all(row["address"].endswith("예술회관길 11") for row in rows)
