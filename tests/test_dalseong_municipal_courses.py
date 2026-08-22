from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Mapping

import pytest

from Crawler import municipal_dalseong as dalseong


@dataclass
class Target:
    provider: str = dalseong.DALSEONG_PROVIDER
    url: str = dalseong.DALSEONG_URL
    name: str = "달성군 시설관리공단 통합예약"
    branch: str = "대구광역시 달성군"
    extra: Mapping[str, str] | None = None


class DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _route_key(
    method: str,
    url: str,
    data: Mapping[str, str] | None,
) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    return method, url, tuple(sorted((data or {}).items()))


class FixtureSite:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str, tuple[tuple[str, str], ...]], str] = {}
        self.calls: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []
        self.sessions: list[DummySession] = []

    def add(
        self,
        method: str,
        url: str,
        html: str,
        data: Mapping[str, str] | None = None,
    ) -> None:
        self.routes[_route_key(method, url, data)] = html

    def session_factory(self) -> DummySession:
        current = DummySession()
        self.sessions.append(current)
        return current

    def requester(
        self,
        _session: DummySession,
        method: str,
        url: str,
        _timeout: int,
        data: Mapping[str, str] | None,
    ) -> str:
        key = _route_key(method, url, data)
        self.calls.append(key)
        try:
            return self.routes[key]
        except KeyError as exc:
            raise AssertionError(f"unexpected fixture request: {key}") from exc


def _shell(content: str, branch: str) -> str:
    return f"""
    <html><head><title>목록 | 달성군시설관리공단·통합예약시스템</title></head>
    <body><div id="content"><h1>{escape(branch)}</h1>{content}</div></body></html>
    """


def _course_form(source: dalseong.CourseSource, page: int) -> str:
    return f"""
    <script>
      function fn_search(page) {{
        frm.action = "{source.list_action}";
      }}
      function fnCrsInfo() {{
        frm.action = "{source.detail_action}";
      }}
    </script>
    <form id="dssCultureVO" method="post" action="/index.do?menu_id={source.menu_id}">
      <input name="searchCondition" value="">
      <input name="crs_id" value="">
      <input name="gds_id" value="">
      <input name="gds_clsf_dcd" value="DSS_0001_01">
      <input name="apply_ch" value="">
      <input name="bfr_searchCondition" value="">
      <input name="searchKeyword" value="">
      <input name="pageIndex" value="{page}">
    </form>
    """


def _course_list_html(
    source: dalseong.CourseSource,
    *,
    page: int = 1,
    gds_id: str,
    crs_id: str,
    title: str,
    status: str = "접수중",
    capacity: str = "20",
    online_control: str | None = None,
    start: str = "26.08.01",
    end: str = "26.08.31",
    nonempty_sentinel: bool = False,
) -> str:
    headers = "".join(f"<th>{escape(value)}</th>" for value in source.headers)
    if page == 2:
        if nonempty_sentinel:
            values = {
                "번호": "99",
                "강좌명": title,
                "요일": "월 수",
                "교육시간": "10:00 ~ 10:50",
                "수강기간": f"교 {start} ~ {end}",
                "모집인원(명)": "20",
                "신청/모집인원(명)": "1 / 20",
                "상태": status,
                "온라인등록": "수강신청",
            }
            cells = []
            for header in source.headers:
                value = values[header]
                if header == "강좌명":
                    cells.append(f"<td><a onclick=\"fnCrsInfo('{gds_id}', '{crs_id}', '')\">{escape(title)}</a></td>")
                else:
                    cells.append(f"<td>{escape(value)}</td>")
            body = f"<tr>{''.join(cells)}</tr>"
        else:
            body = f'<tr><td colspan="99">{dalseong._EMPTY_SENTINEL}</td></tr>'
        return _shell(
            _course_form(source, page) + f"<table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>",
            source.branch.replace("달성군 ", ""),
        )

    values = {
        "번호": "1",
        "강좌명": title,
        "요일": "월 수",
        "교육시간": "10:00 ~ 10:50",
        "수강기간": f"교 {start} ~ {end}",
        "모집인원(명)": capacity,
        "신청/모집인원(명)": f"1 / {capacity}",
        "상태": status,
        "온라인등록": online_control or ("현장접수" if status == "현장접수" else "수강신청"),
    }
    cells = []
    for header in source.headers:
        value = values[header]
        if header == "강좌명":
            cells.append(f"<td><a onclick=\"fnCrsInfo('{gds_id}', '{crs_id}', '')\">{escape(title)}</a></td>")
        else:
            cells.append(f"<td>{escape(value)}</td>")
    paging = '<div class="pagination"><strong>1</strong><a class="page_nextend" onclick="fn_search(1)">마지막</a></div>'
    return _shell(
        _course_form(source, page)
        + f"<table><thead><tr>{headers}</tr></thead><tbody><tr>{''.join(cells)}</tr></tbody></table>"
        + paging,
        source.branch.replace("달성군 ", ""),
    )


def _course_detail_html(
    source: dalseong.CourseSource,
    *,
    title: str,
    start: str = "2026.08.01",
    end: str = "2026.08.31",
    schedule: str = "10:00 ~ 10:50",
) -> str:
    fields = [
        ("강좌분류", f"{source.branch_contract} > 정규강좌 > 건강스포츠"),
        ("이용가능한 요일", "월 수"),
        ("수강료", "유료 / 1개월 : 30,000원"),
        ("모집인원", "20명"),
        ("교육대상", "달성군민"),
        ("교육장소", "문화강의실"),
        ("교육기간", f"{start} ~ {end}"),
        ("교육시간", schedule),
        ("강사", "김달성"),
        ("문의전화", "053-659-4000"),
        ("강좌소개", "안전한 공공 교육 프로그램"),
    ]
    rows = "".join(f"<tr><th>{escape(key)}</th><td>{escape(value)}</td></tr>" for key, value in fields)
    return _shell(
        f'<h2 class="reserve_tt">{escape(title)}</h2>'
        f"<table><caption>{escape(title)} 예약하기 상세</caption>{rows}</table>",
        source.branch,
    )


def _museum_forms(source: dalseong.MuseumSource, page: int) -> str:
    return f"""
    <script>
      const inst = "{dalseong.FOSSIL_INST_ID}";
      const path = "{dalseong.FOSSIL_DETAIL_PATH}";
      function fnPageSearch(page) {{ return page; }}
      function fnDetail(id) {{ return id; }}
    </script>
    <form id="detailVO" method="get">
      <input name="menu_id" value="{source.menu_id}">
    </form>
    <form id="dssMsmRsvtVO" method="get" action="/index.do?menu_id={source.menu_id}">
      <input name="menu_id" value="{source.menu_id}">
      <input name="pageIndex" value="{page}">
    </form>
    """


def _museum_list_html(
    source: dalseong.MuseumSource,
    *,
    page: int,
    identity: str,
    title: str,
    nonempty_sentinel: bool = False,
) -> str:
    if page == 2 and not nonempty_sentinel:
        body = f'<div class="no_data">{dalseong._EMPTY_SENTINEL}</div>'
    elif source.key == "fossil_special":
        body = f'<div class="no_data">{dalseong._EMPTY_SENTINEL}</div>'
    else:
        period = ""
        if source.dated:
            period = """
            <ul class="period"><li>
              <script>document.write(com.fnSetDate('20260701','-'))</script>
              <script>document.write(com.fnSetDate('20260731','-'))</script>
              <script>document.write(com.fnSetDate('20260810','-'))</script>
              <script>document.write(com.fnSetDate('20260810','-'))</script>
            </li></ul>
            """
        body = f"""
        <ul class="programme"><li>
          <div class="type_wrap"><span class="type01">접수중</span><span>유료</span></div>
          <h3 class="tit"><a onclick="fnDetail('{identity}')">{escape(title)}</a></h3>
          {period}
          <ul class="info">
            <li><span class="con_l">교육요일</span><span class="con_r">월 화</span></li>
            <li><span class="con_l">이용대상</span><span class="con_r">달성군민</span></li>
          </ul>
        </li></ul>
        """
    paging = (
        '<div class="pagination"><strong>1</strong><a onclick="fnPageSearch(1)">마지막</a></div>' if page == 1 else ""
    )
    return _shell(
        _museum_forms(source, page) + body + paging,
        dalseong.FOSSIL_BRANCH,
    )


def _museum_detail_html(
    source: dalseong.MuseumSource,
    *,
    identity: str,
    title: str,
    title_override: str | None = None,
) -> str:
    periods = ""
    description = "달성화석박물관의 안전한 교육내용입니다."
    if source.dated:
        periods = """
        <ul class="period">
          <li><span class="con_l">접수기간</span><span class="con_r">2026-07-01 ~ 2026-07-31</span></li>
          <li><span class="con_l">교육기간</span><span class="con_r">2026-08-10 ~ 2026-08-10</span></li>
          <li><span class="con_l">문의전화</span><span class="con_r">053 - 659 - 4900</span></li>
        </ul>
        """
    else:
        periods = """
        <ul class="period">
          <li><span class="con_l">문의전화</span><span class="con_r">053 - 659 - 4915</span></li>
        </ul>
        """
        description = "예약일 전까지 유선으로 신청하여 주시기 바랍니다."
    return _shell(
        f"""
        <form id="dssMsmRsvtVO">
          <input name="inst_id" value="{dalseong.FOSSIL_INST_ID}">
          <input name="msm_prgrm_id" value="{identity}">
          <input name="menu_id" value="{source.menu_id}">
          <input name="ntsl_amt" value="8000">
        </form>
        <div class="fossil_view">
          <div class="tit_wrap"><div class="type_wrap"><span class="type01">접수중</span></div>
            <h3 class="tit">{escape(title_override or title)}</h3></div>
          {periods}
          <ul class="info">
            <li><span class="con_l">교육요일</span><span class="con_r">월 화</span></li>
            <li><span class="con_l">이용대상</span><span class="con_r">달성군민</span></li>
            <li><span class="con_l">교육장소</span><span class="con_r">1층 제1교육실</span></li>
            <li><span class="con_l">교육비용</span><span class="con_r">원</span></li>
          </ul>
          <div class="details_cn"><span class="con_r">{escape(description)}</span></div>
        </div>
        """,
        dalseong.FOSSIL_BRANCH,
    )


def _sport_landing_html(
    source: dalseong.SportMatrixSource,
    identity: str,
    name: str,
) -> str:
    return _shell(
        f"""
        <script>function fnSearchCrs() {{ frm.action = "{source.result_action}"; }}</script>
        <form id="{source.form_id}" method="post" action="/index.do?menu_id={source.menu_id}">
          <input name="searchCondition" value="DSS_0035_02">
        </form>
        <table><thead><tr><th>번호</th><th>프로그램명</th><th>온라인등록</th></tr></thead>
          <tbody><tr><td>1</td><td>{escape(name)}</td>
          <td><a onclick="fnSearchCrs('{identity}', '{escape(name)}')">신청하기</a></td></tr></tbody>
        </table>
        """,
        source.branch,
    )


def _sport_empty_html(
    source: dalseong.SportMatrixSource,
    *,
    active: bool = False,
) -> str:
    active_html = ""
    if active:
        active_html = """
        <table><thead><tr><th>프로그램명</th><th>온라인등록</th></tr></thead>
          <tbody><tr><td>수영</td><td><a onclick="fnSearchCrsDeatail('GDS_1')">상세</a></td></tr></tbody>
        </table>
        """
    return _shell(active_html or "<p>현재 등록된 월별 강습이 없습니다.</p>", source.branch)


def _complete_site(
    *,
    duplicate_crs: bool = False,
    bad_course_sentinel: bool = False,
    bad_museum_detail: bool = False,
    active_sport: bool = False,
    expired_first: bool = False,
) -> FixtureSite:
    site = FixtureSite()
    course_records: list[tuple[dalseong.CourseSource, str, str, str, str, str]] = []
    for index, source in enumerate(dalseong.COURSE_SOURCES, start=1):
        gds_id = f"GDS_{index:08d}"
        crs_number = 1 if duplicate_crs and index == 2 else index
        crs_id = f"CRS_{crs_number:08d}"
        title = f"{source.branch} 테스트 강좌 [10:00 ~ 10:50]"
        start = "25.01.01" if expired_first and index == 1 else "26.08.01"
        end = "25.12.31" if expired_first and index == 1 else "26.08.31"
        first = _course_list_html(
            source,
            gds_id=gds_id,
            crs_id=crs_id,
            title=title,
            start=start,
            end=end,
        )
        site.add("GET", source.url, first)
        site.add("POST", dalseong.course_list_url(source), first, dalseong.course_post_data(source, 1))
        site.add(
            "POST",
            dalseong.course_list_url(source),
            _course_list_html(
                source,
                page=2,
                gds_id=gds_id,
                crs_id=crs_id,
                title=title,
                nonempty_sentinel=bad_course_sentinel and index == 1,
            ),
            dalseong.course_post_data(source, 2),
        )
        course_records.append((source, gds_id, crs_id, title, start, end))

    # The canonical target and the culture source URL are identical.
    culture = dalseong._COURSE_BY_KEY["dalseong_culture"]
    site.add("GET", dalseong.DALSEONG_URL, site.routes[_route_key("GET", culture.url, None)])

    for source, gds_id, crs_id, title, start, end in course_records:
        if expired_first and source == dalseong.COURSE_SOURCES[0]:
            continue
        site.add(
            "POST",
            dalseong.course_detail_request_url(source),
            _course_detail_html(
                source,
                title=title,
                start="20" + start if start.startswith("25") else "2026.08.01",
                end="20" + end if end.startswith("25") else "2026.08.31",
            ),
            dalseong.course_post_data(
                source,
                1,
                gds_id=gds_id,
                crs_id=crs_id,
            ),
        )

    museum_records = {
        "fossil_weekend": ("MSM_PRGRM_00000001", "주말 화석 탐구"),
        "fossil_group": ("MSM_PRGRM_00000002", "평일단체- 유치부"),
    }
    for source in dalseong.MUSEUM_SOURCES:
        identity, title = museum_records.get(source.key, ("MSM_PRGRM_00000009", "현재 없는 특강"))
        site.add(
            "GET",
            dalseong.museum_list_url(source, 1),
            _museum_list_html(
                source,
                page=1,
                identity=identity,
                title=title,
            ),
        )
        site.add(
            "GET",
            dalseong.museum_list_url(source, 2),
            _museum_list_html(
                source,
                page=2,
                identity=identity,
                title=title,
            ),
        )
        if source.key != "fossil_special":
            site.add(
                "GET",
                dalseong.museum_detail_url(source, identity),
                _museum_detail_html(
                    source,
                    identity=identity,
                    title=title,
                    title_override=("다른 교육" if bad_museum_detail and source.key == "fossil_weekend" else None),
                ),
            )

    for index, source in enumerate(dalseong.SPORT_MATRIX_SOURCES, start=1):
        identity = f"ONLN_RSVT_CLSF_{index:08d}"
        name = "수영"
        landing = _sport_landing_html(source, identity, name)
        site.add("GET", source.url, landing)
        classification = (identity, name)
        site.add(
            "POST",
            f"https://{dalseong.DALSEONG_HOST}{source.result_action}",
            _sport_empty_html(source, active=active_sport and index == 1),
            dalseong._sport_post_data(classification),
        )
    return site


def _collect(site: FixtureSite, **kwargs):
    return dalseong.collect_dalseong_education_courses(
        Target(extra={"collection_category": "공공예약"}),
        requester=site.requester,
        session_factory=site.session_factory,
        today="2026-07-20",
        max_pages=27,
        detail_limit=6,
        max_workers=4,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("provider", "url", "expected"),
    [
        (dalseong.DALSEONG_PROVIDER, dalseong.DALSEONG_URL, True),
        ("OTHER", dalseong.DALSEONG_URL, False),
        (dalseong.DALSEONG_PROVIDER, "http://yeyak.dssiseol.or.kr/index.do?menu_id=00005155", False),
        (dalseong.DALSEONG_PROVIDER, "https://evil.test/index.do?menu_id=00005155", False),
        (dalseong.DALSEONG_PROVIDER, dalseong.DALSEONG_URL + "&pageIndex=1", False),
        (dalseong.DALSEONG_PROVIDER, dalseong.DALSEONG_URL + "#x", False),
    ],
)
def test_exact_target_contract(provider: str, url: str, expected: bool) -> None:
    assert dalseong.is_target(Target(provider=provider, url=url)) is expected


def test_url_and_post_helpers_are_canonical() -> None:
    source = dalseong.COURSE_SOURCES[0]
    assert dalseong.course_list_url(source).startswith(
        "https://yeyak.dssiseol.or.kr/index.do?menu_id=00004951&menu_link="
    )
    payload = dalseong.course_post_data(
        source,
        3,
        gds_id="GDS_00000001",
        crs_id="CRS_00000001",
    )
    assert payload["pageIndex"] == "3"
    assert payload["gds_id"] == "GDS_00000001"
    assert payload["crs_id"] == "CRS_00000001"
    museum_url = dalseong.museum_detail_url(dalseong.MUSEUM_SOURCES[0], "MSM_PRGRM_00000001")
    assert "msm_prgrm_id=MSM_PRGRM_00000001" in museum_url
    assert "menu_id=00007350" in museum_url


def test_course_rows_have_distinct_source_urls_within_one_catalogue() -> None:
    source = dalseong.COURSE_SOURCES[0]
    rows = []
    for index in (1, 2):
        soup = dalseong.BeautifulSoup(
            _course_list_html(
                source,
                gds_id=f"GDS_{index:08d}",
                crs_id=f"CRS_{index:08d}",
                title=f"테스트 강좌 {index} [10:00 ~ 10:50]",
            ),
            "lxml",
        )
        _, table_rows = dalseong._course_page_contract(soup, source, 1)
        rows.append(dalseong._course_row(Target(), source, table_rows[0], 1))

    assert rows[0]["raw_url"] != rows[1]["raw_url"]
    assert all(row["branch_url"] == source.url for row in rows)
    assert all(
        row["raw_url"].endswith(
            f"#course-{row['raw_fields']['crs_id']}"
        )
        for row in rows
    )


def test_time_ranges_are_canonical_and_title_time_is_unique() -> None:
    assert dalseong._time_range("9:00~10:05") == "09:00 ~ 10:05"
    assert dalseong._title_time_range("강좌 [9:00 ~ 10:05]") == "09:00 ~ 10:05"
    with pytest.raises(ValueError, match="time range is invalid"):
        dalseong._time_range("10:00 ~ 09:00")
    with pytest.raises(ValueError, match="title time contract"):
        dalseong._title_time_range("시간 없는 강좌")


def test_semantic_duplicates_keep_newest_stable_identity_and_record_loser() -> None:
    shared = {
        "title": "중복 강좌",
        "start_date": date(2026, 8, 1),
        "end_date": date(2026, 8, 31),
        "branch": "달성문화센터",
        "schedule_raw": "월 10:00 ~ 10:50",
        "room": "문화강의실",
        "instructor": "김달성",
        "fee": "30,000원",
        "capacity_total": 20,
    }
    older = {
        **shared,
        "provider_course_id": f"{dalseong.DALSEONG_PROVIDER}:course:CRS_00000001",
        "raw_fields": {},
    }
    newer = {
        **shared,
        "provider_course_id": f"{dalseong.DALSEONG_PROVIDER}:course:CRS_00000002",
        "raw_fields": {},
    }

    rows, duplicate_count = dalseong._dedupe_semantic([older, newer])

    assert duplicate_count == 1
    assert rows == [newer]
    assert newer["raw_fields"]["semantic_duplicate_provider_course_ids"] == [older["provider_course_id"]]


def test_complete_snapshot_collects_all_owned_branches_and_details() -> None:
    site = _complete_site()
    rows, parser, meta = _collect(site)

    assert parser == dalseong.DALSEONG_PARSER
    assert len(rows) == 6
    assert meta["source_total"] == 6
    assert meta["course_source_total"] == 4
    assert meta["museum_source_total"] == 2
    assert meta["current_count"] == 6
    assert meta["returned_count"] == 6
    assert meta["undated_current_count"] == 1
    assert meta["duplicate_identity_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["title_detail_schedule_mismatch_count"] == 0
    assert meta["course_page_counts"] == {
        "citizen_gym": 1,
        "dalseong_culture": 1,
        "techno_health": 1,
        "women_culture": 1,
    }
    assert meta["museum_page_counts"] == {
        "fossil_group": 1,
        "fossil_special": 1,
        "fossil_weekend": 1,
    }
    assert meta["sport_matrix_classification_counts"] == {
        "national_sports": 1,
        "techno_swimming": 1,
    }
    assert meta["sport_matrix_active_count"] == 0
    assert meta["sentinel_requests"] == 7
    assert meta["recheck_requests"] == 9
    assert meta["detail_pages"] == 6
    assert meta["pages"] == 27
    assert meta["request_count"] == 33
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert {row["branch"] for row in rows} == {
        "달성군 여성문화복지센터",
        "달성문화센터",
        "달성군민체육관",
        "달성테크노스포츠센터",
        "달성화석박물관",
    }
    assert all(row["municipality_code"] == "2771000000" for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["raw_fields"]["detail_identity_verified"] for row in rows)
    group = next(row for row in rows if row["branch_code"] == "fossil_group")
    assert group["start_date"] is None
    assert group["status"] == "OPEN"
    assert group["application_type"] == "PHONE_RESERVATION"
    assert group["fee"] == "8,000원"
    assert site.sessions and all(session.closed for session in site.sessions)


def test_page_cap_is_checked_against_all_lists_sentinels_selections_and_rechecks() -> None:
    site = _complete_site()
    rows, _, meta = dalseong.collect_dalseong_education_courses(
        Target(),
        requester=site.requester,
        session_factory=site.session_factory,
        today="2026-07-20",
        max_pages=26,
        detail_limit=6,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "below 27 required" in meta["configured_collection_error"]


def test_detail_cap_discards_complete_snapshot_before_partial_details() -> None:
    site = _complete_site()
    rows, _, meta = dalseong.collect_dalseong_education_courses(
        Target(),
        requester=site.requester,
        session_factory=site.session_factory,
        today="2026-07-20",
        max_pages=27,
        detail_limit=5,
        max_workers=4,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_required_count"] == 6
    assert meta["detail_pages"] == 0
    assert "detail_limit cap 5" in meta["configured_collection_error"]


def test_nonempty_post_boundary_page_discards_snapshot() -> None:
    site = _complete_site(bad_course_sentinel=True)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"]


def test_duplicate_stable_course_identity_discards_snapshot() -> None:
    site = _complete_site(duplicate_crs=True)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["duplicate_identity_count"] == 1
    assert "duplicate stable identities" in meta["configured_collection_error"]


def test_museum_detail_title_mismatch_discards_snapshot() -> None:
    site = _complete_site(bad_museum_detail=True)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["detail_pages"] == 5
    assert "museum detail title/status mismatch" in meta["configured_collection_error"]


def test_new_active_sport_selection_fails_closed_instead_of_emitting_static_matrix() -> None:
    site = _complete_site(active_sport=True)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["sport_matrix_active_count"] == 0
    assert "unparsed active courses" in meta["configured_collection_error"]


def test_expired_rows_are_excluded_without_requesting_their_details() -> None:
    site = _complete_site(expired_first=True)
    rows, _, meta = dalseong.collect_dalseong_education_courses(
        Target(),
        requester=site.requester,
        session_factory=site.session_factory,
        today="2026-07-20",
        max_pages=27,
        detail_limit=5,
        max_workers=4,
    )
    assert len(rows) == 5
    assert meta["expired_count"] == 1
    assert meta["detail_required_count"] == 5
    assert meta["snapshot_complete"] is True
    assert all(row["branch"] != "달성군 여성문화복지센터" for row in rows)


def test_unknown_status_is_rejected() -> None:
    source = dalseong.COURSE_SOURCES[0]
    html = _course_list_html(
        source,
        gds_id="GDS_00000001",
        crs_id="CRS_00000001",
        title="테스트",
        status="새상태",
    )
    soup = dalseong.BeautifulSoup(html, "lxml")
    _, rows = dalseong._course_page_contract(soup, source, 1)
    with pytest.raises(ValueError, match="unknown course status"):
        dalseong._course_row(Target(), source, rows[0], 1)


def test_detail_time_is_authoritative_when_list_has_no_time_column() -> None:
    source = dalseong._COURSE_BY_KEY["techno_health"]
    title = "필라테스 (19:00 ~ 19:50)"
    list_soup = dalseong.BeautifulSoup(
        _course_list_html(
            source,
            gds_id="GDS_00000001",
            crs_id="CRS_00000001",
            title=title,
        ),
        "lxml",
    )
    _, trs = dalseong._course_page_contract(list_soup, source, 1)
    row = dalseong._course_row(Target(), source, trs[0], 1)
    detail_soup = dalseong.BeautifulSoup(
        _course_detail_html(source, title=title, schedule="18:50 ~ 20:30"),
        "lxml",
    )

    dalseong._enrich_course_detail(row, source, detail_soup)

    assert row["schedule"] == "18:50 ~ 20:30"
    assert row["raw_fields"]["title_schedule"] == "19:00 ~ 19:50"
    assert row["raw_fields"]["title_detail_schedule_mismatch"] is True


def test_explicit_list_and_detail_time_mismatch_fails_closed() -> None:
    source = dalseong._COURSE_BY_KEY["women_culture"]
    title = "테스트 강좌 [10:00 ~ 10:50]"
    list_soup = dalseong.BeautifulSoup(
        _course_list_html(
            source,
            gds_id="GDS_00000001",
            crs_id="CRS_00000001",
            title=title,
        ),
        "lxml",
    )
    _, trs = dalseong._course_page_contract(list_soup, source, 1)
    row = dalseong._course_row(Target(), source, trs[0], 1)
    detail_soup = dalseong.BeautifulSoup(
        _course_detail_html(source, title=title, schedule="11:00 ~ 11:50"),
        "lxml",
    )

    with pytest.raises(ValueError, match="detail/list time mismatch"):
        dalseong._enrich_course_detail(row, source, detail_soup)


def test_zero_capacity_closed_shower_addon_is_excluded_as_non_course() -> None:
    source = dalseong._COURSE_BY_KEY["techno_health"]
    html = _course_list_html(
        source,
        gds_id="GDS_00000001",
        crs_id="CRS_00000001",
        title="필라테스 (샤워 포함)",
        status="접수마감",
        capacity="0",
        online_control="현장접수",
    )
    soup = dalseong.BeautifulSoup(html, "lxml")
    _, rows = dalseong._course_page_contract(soup, source, 1)
    parsed = dalseong._course_row(Target(), source, rows[0], 1)
    assert parsed["capacity_total"] == 0
    assert parsed["raw_fields"]["excluded_non_course_addon"] is True


def test_other_zero_capacity_row_still_fails_closed() -> None:
    source = dalseong.COURSE_SOURCES[0]
    html = _course_list_html(
        source,
        gds_id="GDS_00000001",
        crs_id="CRS_00000001",
        title="일반 강좌",
        status="접수마감",
        capacity="0",
        online_control="현장접수",
    )
    soup = dalseong.BeautifulSoup(html, "lxml")
    _, rows = dalseong._course_page_contract(soup, source, 1)
    with pytest.raises(ValueError, match="capacity contract"):
        dalseong._course_row(Target(), source, rows[0], 1)


@pytest.mark.parametrize(
    "message",
    [dalseong._EMPTY_SENTINEL, "등록된 자료가 없습니다."],
)
def test_official_locale_specific_empty_sentinels_are_accepted(message: str) -> None:
    soup = dalseong.BeautifulSoup(
        f'<html><body><div id="content"><div>{message}</div></div></body></html>',
        "lxml",
    )
    assert dalseong._empty_sentinel(soup) is True
