from __future__ import annotations

from html import escape
from threading import Lock

import pytest

from Crawler import municipal_seocho as seocho


def _target(**overrides):
    value = {
        "provider": seocho.SEOCHO_EDUCATION_PROVIDER,
        "url": seocho.SEOCHO_EDUCATION_URL,
        "name": "서초구청 통합예약",
        "branch": "서울특별시 서초구",
    }
    value.update(overrides)
    return value


def _main_item(
    identity: str,
    title: str,
    *,
    branch: str = "서초1동 자치회관",
    period: tuple[str, str] = ("2026-08-01", "2026-08-31"),
    apply: tuple[str, str] = ("2026-07-20", "2026-07-31"),
    status: str = "접수중",
    schedule: str = "월 10:00 ~ 11:00",
    number: str = "1",
) -> dict[str, object]:
    return {
        "id": identity,
        "title": title,
        "branch": branch,
        "period": period,
        "apply": apply,
        "status": status,
        "schedule": schedule,
        "number": number,
    }


def _info_item(
    identity: str,
    title: str,
    *,
    period: tuple[str, str] = ("2026-08-01", "2026-08-31"),
    apply: tuple[str, str] = ("2026-07-20", "2026-07-31"),
    status: str = "강좌시작",
    schedule: str = "월 10:00 ~ 11:00",
) -> dict[str, object]:
    return {
        "id": identity,
        "title": title,
        "period": period,
        "apply": apply,
        "status": status,
        "schedule": schedule,
    }


def _main_page(items, *, total: int, pages: int = 1) -> str:
    body = []
    for item in items:
        start, end = item["period"]
        apply_start, apply_end = item["apply"]
        body.append(
            f"""
            <tr>
              <td>{escape(str(item['number']))}</td>
              <td class="left"><a href="javascript:doLectureUserView('{item['id']}');">
                {escape(str(item['title']))}<br><span>{escape(str(item['schedule']))}</span>
              </a></td>
              <td>{escape(str(item['branch']))}</td>
              <td>{start}<br>~{end}</td>
              <td>{apply_start}<br>~{apply_end}</td>
              <td>{escape(str(item['status']))}</td>
            </tr>
            """
        )
    paging = "".join(
        f'<a href="?pageIndex={page}" onclick="doLectureUserPag({page});return false;">{page}</a>'
        for page in range(1, pages + 1)
    )
    return f"""
      <div class="board-top"><span class="count">총 <em>{total}</em>건</span></div>
      <table class="list"><caption>강좌예약 목록</caption><tbody>{''.join(body)}</tbody></table>
      <div class="paging">{paging}</div>
    """


def _info_page(items, *, total: int, pages: int = 1) -> str:
    body = []
    for item in items:
        start, end = item["period"]
        apply_start, apply_end = item["apply"]
        body.append(
            f"""
            <tr>
              <td><a href="/site/seocho/ex/lecture/info/InfoView.do?clIdx={item['id']}">{escape(str(item['title']))}</a></td>
              <td>{escape(str(item['schedule']))}</td>
              <td>{start}<br>~{end}</td>
              <td>{apply_start}<br>~{apply_end}</td>
              <td>{escape(str(item['status']))}</td>
            </tr>
            """
        )
    paging = "".join(
        f'<a href="?pageIndex={page}" onclick="doLectureUserPag({page});return false;">{page}</a>'
        for page in range(1, pages + 1)
    )
    return f"""
      <div class="board-top"><span class="count">총 <em>{total}</em>건</span></div>
      <table class="list"><caption>시니어 정보화교육 목록</caption><tbody>{''.join(body)}</tbody></table>
      <div class="paging">{paging}</div>
    """


def _pairs(fields: dict[str, str]) -> str:
    rows = []
    items = list(fields.items())
    for index in range(0, len(items), 2):
        cells = []
        for key, value in items[index : index + 2]:
            cells.append(f"<th>{escape(key)}</th><td>{escape(value)}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return "".join(rows)


def _main_detail(
    item,
    *,
    related_url: str = "",
    internal_control: bool = False,
    omit: str = "",
    title: str | None = None,
) -> str:
    start, end = item["period"]
    apply_start, apply_end = item["apply"]
    fields = {
        "교육 기관": str(item["branch"]),
        "관리부서": "교육체육과",
        "강사명": "홍길동",
        "수강료": "무료",
        "교육기간": f"{start} 10:00 ~ {end} 11:00",
        "수강요일": str(item["schedule"]),
        "교육대상": "전체",
        "교육장소": "교육실",
        "접수기간": f"{apply_start} 09:00 ~ {apply_end} 18:00",
        "교육인원": "20명",
        "접수방법": "인터넷접수",
        "강의방법": "현장강의",
        "전화문의": "02-2155-0000",
    }
    if omit:
        fields.pop(omit)
    if related_url:
        fields["관련사이트"] = related_url
    table = _pairs(fields)
    if related_url:
        table = table.replace(
            escape(related_url),
            f'<a href="{escape(related_url)}">{escape(related_url)}</a>',
        )
    control = (
        f'<a onclick="doMemberForm(\'{item["id"]}\')">교육접수하기</a>'
        if internal_control
        else ""
    )
    return f"""
      <meta id="mtTitle" property="og:title" content="{escape(title if title is not None else str(item['title']))}">
      <table class="view"><caption>강좌 상세정보</caption><tbody>{table}</tbody></table>
      <div class="btns">{control}</div><div class="lectureContent">상세 교육 내용</div>
    """


def _info_detail(
    item,
    *,
    branch: str = "서초스마트시니어교육센터 (반포1동주민센터 4층)",
    omit: str = "",
    title: str | None = None,
    application: bool = False,
) -> str:
    start, end = item["period"]
    apply_start, apply_end = item["apply"]
    fields = {
        "교육 기관": branch,
        "관리부서": "어르신행복과",
        "강사명": "김강사",
        "수강료": "무료",
        "교육기간": f"{start} 10:00 ~ {end} 11:00",
        "수강요일": str(item["schedule"]),
        "교육인원": "30명",
        "전화문의": "02-2155-8660",
        "교육장소": "IT Class A",
        "접수기간": f"{apply_start} 10:00 ~ {apply_end} 18:00",
        "선별방법": "우선순위(1.만55세 이상 / 2. 해당강좌 미수강생)",
    }
    if omit:
        fields.pop(omit)
    application_link = (
        '<a href="/site/seocho/foffice/ex/lecture/info/InfoForm.do?'
        f'clIdx={item["id"]}">교육접수하기</a>'
        if application
        else ""
    )
    return f"""
      <h4 class="con-title1">{escape(title if title is not None else str(item['title']))}</h4>
      <table class="view"><caption>강좌 상세정보</caption><tbody>{_pairs(fields)}</tbody></table>
      <div class="btns">{application_link}</div>
      <table><caption>신청예비자 순위보기</caption><tbody><tr><td>masked-user</td></tr></tbody></table>
    """


class _Session:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FixtureNetwork:
    def __init__(self, pages: dict[str, str | Exception]):
        self.pages = pages
        self.calls: list[str] = []
        self.sessions: list[_Session] = []
        self.lock = Lock()

    def session_factory(self):
        value = _Session()
        with self.lock:
            self.sessions.append(value)
        return value

    def fetcher(self, _session, url: str, _timeout: int):
        with self.lock:
            self.calls.append(url)
        value = self.pages.get(url)
        if value is None:
            raise AssertionError(f"unexpected URL: {url}")
        if isinstance(value, Exception):
            raise value
        return value


def _successful_network():
    main_one = _main_item("L00000001", "AI 첫걸음", number="2")
    main_two = _main_item(
        "L00000002",
        "미술 교실",
        branch="서초2동 자치회관",
        status="접수마감",
        number="1",
    )
    info = _info_item("L00000003", "스마트폰 활용")
    pages = {
        seocho.SEOCHO_EDUCATION_URL: _main_page([main_one], total=2, pages=2),
        f"{seocho.SEOCHO_EDUCATION_URL}?pageIndex=2": _main_page(
            [main_two], total=2, pages=2
        ),
        seocho.SEOCHO_INFO_URL: _info_page([info], total=1),
        seocho.seocho_detail_url("L00000001"): _main_detail(
            main_one, internal_control=True
        ),
        seocho.seocho_detail_url("L00000002"): _main_detail(main_two),
        seocho.seocho_detail_url("L00000003", source_kind="info"): _info_detail(info),
    }
    return _FixtureNetwork(pages), main_one, main_two, info


def test_canonical_provider_and_url_contract():
    assert seocho.SEOCHO_EDUCATION_PROVIDER == "MUNI_WWW_SEOCHO_GO_KR_0866A56C"
    assert seocho.is_seocho_education_target(_target())
    assert not seocho.is_seocho_education_target(
        _target(url=seocho.SEOCHO_EDUCATION_URL + "?pageIndex=2")
    )
    assert not seocho.is_seocho_education_target(_target(provider="WRONG"))
    assert seocho.seocho_detail_url("L00000001").endswith(
        "/lecture/View.do?clIdx=L00000001"
    )
    assert seocho.seocho_detail_url("not-an-id") == ""


def test_complete_two_source_snapshot_is_enriched_and_stable():
    network, *_items = _successful_network()
    rows, parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=3,
        detail_limit=3,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
        max_workers=3,
    )

    assert parser == seocho.SEOCHO_PARSER
    assert len(rows) == 3
    assert {row["provider_course_id"] for row in rows} == {
        f"{seocho.SEOCHO_EDUCATION_PROVIDER}:lecture:L00000001",
        f"{seocho.SEOCHO_EDUCATION_PROVIDER}:lecture:L00000002",
        f"{seocho.SEOCHO_EDUCATION_PROVIDER}:lecture:L00000003",
    }
    assert meta["source_total"] == 3
    assert meta["main_total"] == 2
    assert meta["info_total"] == 1
    assert meta["pages"] == 3
    assert meta["detail_pages"] == 3
    assert meta["snapshot_complete"] is True
    assert meta["source_counts"] == {"main": 2, "info": 1}
    assert meta["duplicate_count"] == 0
    open_row = next(row for row in rows if row["title"] == "AI 첫걸음")
    assert open_row["status"] == "OPEN"
    assert open_row["category"] == "교육강좌"
    assert open_row["raw_fields"]["category_basis"] == "official_ledger:강좌예약"
    assert open_row["application_url"] == open_row["raw_url"]
    assert open_row["application_type"] == "ONLINE_RESERVATION"
    info_row = next(row for row in rows if row["title"] == "스마트폰 활용")
    assert info_row["branch"] == "서초스마트시니어교육센터 (반포1동주민센터 4층)"
    assert info_row["category"] == "정보화교육"
    assert (
        info_row["raw_fields"]["category_basis"]
        == "official_ledger:시니어 정보화교육"
    )
    assert "masked-user" not in info_row["description"]
    assert all(row["municipality_code"] == "1165000000" for row in rows)
    assert network.sessions and all(value.closed for value in network.sessions)


def test_external_related_site_is_the_application_url():
    item = _main_item("L00000011", "외부 접수 특강")
    external = "https://example.org/apply?id=11"
    network = _FixtureNetwork(
        {
            seocho.SEOCHO_EDUCATION_URL: _main_page([item], total=1),
            seocho.SEOCHO_INFO_URL: _info_page([], total=0),
            seocho.seocho_detail_url("L00000011"): _main_detail(
                item, related_url=external
            ),
        }
    )
    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=2,
        detail_limit=1,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )
    assert meta["snapshot_complete"] is True
    assert rows[0]["application_url"] == external
    assert rows[0]["application_type"] == "EXTERNAL_ONLINE"


def test_open_information_course_uses_exact_course_bound_form_without_fetching_it():
    item = _info_item(
        "L00000012",
        "시니어 컴퓨터 기초",
        status="신청하기",
    )
    application_url = (
        "https://www.seocho.go.kr"
        "/site/seocho/foffice/ex/lecture/info/InfoForm.do?clIdx=L00000012"
    )
    network = _FixtureNetwork(
        {
            seocho.SEOCHO_EDUCATION_URL: _main_page([], total=0),
            seocho.SEOCHO_INFO_URL: _info_page([item], total=1),
            seocho.seocho_detail_url(
                "L00000012", source_kind="info"
            ): _info_detail(item, application=True),
        }
    )

    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=2,
        detail_limit=1,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )

    assert meta["snapshot_complete"] is True
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["application_url"] == application_url
    assert rows[0]["application_type"] == "ONLINE_RESERVATION"
    assert rows[0]["reservation_available"] is True
    assert rows[0]["target"] == "만 55세 이상"
    assert application_url not in network.calls


def test_information_course_waiting_phase_is_closed_not_an_incomplete_row():
    item = _info_item(
        "L00000013",
        "개강 대기 정보화 강좌",
        status="강좌대기",
    )
    network = _FixtureNetwork(
        {
            seocho.SEOCHO_EDUCATION_URL: _main_page([], total=0),
            seocho.SEOCHO_INFO_URL: _info_page([item], total=1),
            seocho.seocho_detail_url(
                "L00000013", source_kind="info"
            ): _info_detail(item),
        }
    )

    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=2,
        detail_limit=1,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )

    assert meta["snapshot_complete"] is True
    assert len(rows) == 1
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["reservation_available"] is False
    assert "configured_collection_error" not in meta


def test_declared_page_cap_fails_closed_before_details():
    network, *_items = _successful_network()
    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=2,
        detail_limit=3,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "exceed max_pages" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_detail_cap_fails_closed():
    network, *_items = _successful_network()
    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=3,
        detail_limit=2,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )
    assert rows == []
    assert meta["detail_required_count"] == 3
    assert meta["detail_attempts"] == 2
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True


def test_detail_parse_mismatch_fails_closed():
    network, main_one, *_items = _successful_network()
    network.pages[seocho.seocho_detail_url("L00000001")] = _main_detail(
        main_one, title="다른 제목"
    )
    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=3,
        detail_limit=3,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail/list title mismatch" in meta["configured_collection_error"]


def test_detail_fetch_error_fails_closed_and_closes_sessions():
    network, *_items = _successful_network()
    network.pages[seocho.seocho_detail_url("L00000002")] = TimeoutError("boom")
    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=3,
        detail_limit=3,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail fetch TimeoutError" in meta["configured_collection_error"]
    assert all(value.closed for value in network.sessions)


def test_same_identity_cross_source_duplicate_is_removed_once():
    main = _main_item(
        "L00000021",
        "공통 강좌",
        branch="서초스마트시니어교육센터",
        schedule="월 10:00 ~ 11:00",
    )
    info = _info_item("L00000021", "공통 강좌", schedule="월 10:00 ~ 11:00")
    network = _FixtureNetwork(
        {
            seocho.SEOCHO_EDUCATION_URL: _main_page([main], total=1),
            seocho.SEOCHO_INFO_URL: _info_page([info], total=1),
            seocho.seocho_detail_url("L00000021"): _main_detail(
                main, internal_control=True
            ),
        }
    )
    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=2,
        detail_limit=1,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )
    assert len(rows) == 1
    assert meta["source_total"] == 2
    assert meta["listed_unique_count"] == 1
    assert meta["duplicate_count"] == 1
    assert meta["snapshot_complete"] is True


def test_conflicting_duplicate_identity_fails_closed():
    main = _main_item(
        "L00000022",
        "원본 강좌",
        branch="서초스마트시니어교육센터",
    )
    info = _info_item("L00000022", "충돌 강좌")
    network = _FixtureNetwork(
        {
            seocho.SEOCHO_EDUCATION_URL: _main_page([main], total=1),
            seocho.SEOCHO_INFO_URL: _info_page([info], total=1),
        }
    )
    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=2,
        detail_limit=2,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "conflicting duplicate list identity" in meta["configured_collection_error"]


def test_expired_rows_are_counted_but_not_detailed():
    expired = _main_item(
        "L00000031",
        "종료 강좌",
        period=("2026-06-01", "2026-06-30"),
        apply=("2026-05-01", "2026-05-20"),
        status="접수마감",
    )
    network = _FixtureNetwork(
        {
            seocho.SEOCHO_EDUCATION_URL: _main_page([expired], total=1),
            seocho.SEOCHO_INFO_URL: _info_page([], total=0),
        }
    )
    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=2,
        detail_limit=0,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )
    assert rows == []
    assert meta["expired_count"] == 1
    assert meta["detail_required_count"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True


def test_empty_official_lists_are_a_complete_empty_snapshot():
    network = _FixtureNetwork(
        {
            seocho.SEOCHO_EDUCATION_URL: _main_page([], total=0),
            seocho.SEOCHO_INFO_URL: _info_page([], total=0),
        }
    )
    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=2,
        detail_limit=0,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["source_total"] == 0


@pytest.mark.parametrize("omit", ["교육 기관", "접수기간", "전화문의"])
def test_required_detail_fields_fail_closed(omit):
    item = _info_item("L00000041", "정보화 강좌")
    network = _FixtureNetwork(
        {
            seocho.SEOCHO_EDUCATION_URL: _main_page([], total=0),
            seocho.SEOCHO_INFO_URL: _info_page([item], total=1),
            seocho.seocho_detail_url("L00000041", source_kind="info"): _info_detail(
                item, omit=omit
            ),
        }
    )
    rows, _parser, meta = seocho.collect_seocho_education_courses(
        _target(),
        max_pages=2,
        detail_limit=1,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "missing detail fields" in meta["configured_collection_error"]
