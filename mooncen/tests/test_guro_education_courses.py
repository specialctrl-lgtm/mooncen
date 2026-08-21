from __future__ import annotations

from html import escape
from threading import Lock

from Crawler import municipal_guro as guro


def _target(**overrides):
    value = {
        "provider": guro.GURO_PROVIDER,
        "url": guro.GURO_URL,
        "name": "구로구 통합예약 교육강좌",
        "branch": "서울특별시 구로구",
    }
    value.update(overrides)
    return value


def _item(
    identity: str,
    title: str,
    *,
    source_kind: str,
    status: str = "모집 중 교육 중",
    apply: tuple[str, str] = ("2026-07-20", "2026-08-05"),
    period: tuple[str, str] = ("2026-08-10", "2026-08-31"),
    venue: str = "오류1동주민센터 4층 강당",
):
    return {
        "id": identity,
        "title": title,
        "source_kind": source_kind,
        "status": status,
        "apply": apply,
        "period": period,
        "venue": venue,
    }


def _source(item):
    return next(value for value in guro.GURO_SOURCES if value[0] == item["source_kind"])


def _list_page(items, *, total=None, page=1, pages=1):
    rows = []
    for item in items:
        _kind, key, jachi, _group, _label = _source(item)
        apply_start, apply_end = item["apply"]
        start, end = item["period"]
        href = (
            f"/yeyak/edcLctreView.do?key={key}&searchLctreKey={item['id']}"
            f"&searchInsttCode=&jachi={jachi}"
        )
        rows.append(
            f"""
            <tr>
              <td><span class="p-badge">{escape(item['status'])}</span></td>
              <td class="p-subject"><a href="{href}">{escape(item['title'])}</a></td>
              <td>{escape(item['venue'])}</td>
              <td>{apply_start} 09:00 ~ {apply_end} 18:00</td>
              <td><p>{start}~{end}</p>10:00~11:00 (월)</td>
              <td>20/3/1</td>
            </tr>
            """
        )
    declared = len(items) if total is None else total
    return f"""
      <div>총 {declared} 건 [ {page} /{pages} 페이지 ]</div>
      <table><tbody>{''.join(rows)}</tbody></table>
    """


def _detail(item, *, include_application=True, omit=""):
    _kind, key, jachi, _group, _label = _source(item)
    apply_start, apply_end = item["apply"]
    start, end = item["period"]
    fields = {
        "강좌영역": "취미",
        "강좌상태": item["status"],
        "신청기간": f"{apply_start}(월요일) 09:00 ~ {apply_end}(수요일) 18:00",
        "교육기간": f"{start}(월요일) ~ {end}(월요일)",
        "강의시간": "(월) 10:00~11:00",
        "수강신청방법": "온 온라인 ※ 선별방법 : 선착순",
        "수강료": "무료",
        "강의장소": f"{item['venue']} 08300 서울특별시 구로구 테스트로 1",
        "수강대상": "구로구민",
        "정원": "20명 + 대기자 5명",
        "주최": "구로구",
        "문의": "02-860-0000",
    }
    if omit:
        fields.pop(omit)
    cells = []
    for name, value in fields.items():
        cells.append(f"<tr><th>{escape(name)}</th><td>{escape(value)}</td></tr>")
    application = ""
    if include_application:
        agreement = (
            "webEdcLctreAgree2.do"
            if item["source_kind"] == "information"
            else "webEdcLctreAgree.do"
        )
        application = (
            f'<a href="./{agreement}?key={key}&lctreKey={item["id"]}'
            f'&searchInsttCode=&jachi={jachi}">신청</a>'
        )
    return f"<table><tbody>{''.join(cells)}</tbody></table>{application}"


class _Session:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Network:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.sessions = []
        self.lock = Lock()

    def session_factory(self):
        value = _Session()
        with self.lock:
            self.sessions.append(value)
        return value

    def fetcher(self, _session, url, _timeout):
        with self.lock:
            self.calls.append(url)
        value = self.pages.get(url)
        if value is None:
            raise AssertionError(f"unexpected URL: {url}")
        if isinstance(value, Exception):
            raise value
        return value


def _successful_network():
    information = _item(
        "101",
        "스마트폰 활용",
        source_kind="information",
        status="모집마감 교육 중",
        venue="구로구청 정보화교육장(4층)",
    )
    resident = _item("202", "여름 미술", source_kind="resident_center")
    expired = _item(
        "203",
        "지난 강좌",
        source_kind="resident_center",
        status="모집마감 교육완료",
        apply=("2026-01-01", "2026-01-10"),
        period=("2026-01-15", "2026-02-01"),
    )
    info_source = _source(information)
    resident_source = _source(resident)
    pages = {
        guro._source_url(info_source, 1): _list_page([information]),
        guro._source_url(resident_source, 1): _list_page([resident, expired]),
        guro.guro_detail_url("101", source_kind="information"): _detail(
            information, include_application=False
        ),
        guro.guro_detail_url("202", source_kind="resident_center"): _detail(resident),
    }
    return _Network(pages), information, resident, expired


def test_canonical_provider_and_url_contract():
    assert guro.is_guro_education_target(_target())
    assert not guro.is_guro_education_target(_target(provider="WRONG"))
    assert not guro.is_guro_education_target(_target(url=guro.GURO_RESIDENT_URL))
    assert guro.guro_detail_url("123", source_kind="resident_center").endswith(
        "key=3600&searchLctreKey=123&searchInsttCode=&jachi=1"
    )
    assert guro.guro_detail_url("bad", source_kind="information") == ""


def test_complete_two_catalog_snapshot_filters_history_and_enriches_details():
    network, *_items = _successful_network()
    rows, parser, meta = guro.collect_guro_education_courses(
        _target(),
        max_pages=2,
        detail_limit=2,
        today="2026-07-19",
        max_workers=2,
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )

    assert parser == guro.GURO_PARSER
    assert len(rows) == 2
    assert meta["source_total"] == 3
    assert meta["source_totals"] == {"information": 1, "resident_center": 2}
    assert meta["current_source_counts"] == {"information": 1, "resident_center": 1}
    assert meta["expired_count"] == 1
    assert meta["pages"] == 2
    assert meta["detail_pages"] == 2
    assert meta["snapshot_complete"] is True
    assert {row["provider_course_id"] for row in rows} == {
        f"{guro.GURO_PROVIDER}:edc:101",
        f"{guro.GURO_PROVIDER}:edc:202",
    }
    resident = next(row for row in rows if row["title"] == "여름 미술")
    assert resident["branch"] == "오류1동 자치회관"
    assert resident["status"] == "OPEN"
    assert resident["application_url"].endswith(
        "key=3600&lctreKey=202&searchInsttCode=&jachi=1"
    )
    assert resident["capacity_total"] == 20
    assert resident["waitlist_total"] == 5
    information = next(row for row in rows if row["title"] == "스마트폰 활용")
    assert information["status"] == "CLOSED"
    assert "application_url" not in information
    assert all(row["municipality_code"] == "1153000000" for row in rows)
    assert network.sessions and all(value.closed for value in network.sessions)


def test_information_courses_use_the_official_agree2_route_for_open_and_waitlist():
    for source_status, application_type in (
        ("모집 중 교육대기", "ONLINE_RESERVATION"),
        ("대기접수 교육대기", "WAITLIST_APPLY"),
    ):
        network, information, _resident, _expired = _successful_network()
        information["status"] = source_status
        source = _source(information)
        network.pages[guro._source_url(source, 1)] = _list_page([information])
        network.pages[guro.guro_detail_url("101", source_kind="information")] = _detail(
            information
        )

        rows, _parser, meta = guro.collect_guro_education_courses(
            _target(),
            max_pages=2,
            detail_limit=2,
            today="2026-07-19",
            max_workers=2,
            fetcher=network.fetcher,
            session_factory=network.session_factory,
        )

        assert meta["snapshot_complete"] is True
        course = next(row for row in rows if row["title"] == information["title"])
        assert course["status"] == "OPEN"
        assert course["application_type"] == application_type
        assert "/webEdcLctreAgree2.do?" in course["application_url"]


def test_detail_cap_fails_closed_instead_of_publishing_a_sample():
    network, *_items = _successful_network()
    rows, _parser, meta = guro.collect_guro_education_courses(
        _target(),
        max_pages=2,
        detail_limit=1,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )

    assert rows == []
    assert meta["current_count"] == 2
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "detail_limit cap allows 1 of 2" in meta["configured_collection_error"]


def test_open_course_without_canonical_application_link_fails_closed():
    network, _information, resident, _expired = _successful_network()
    network.pages[guro.guro_detail_url("202", source_kind="resident_center")] = _detail(
        resident, include_application=False
    )
    rows, _parser, meta = guro.collect_guro_education_courses(
        _target(),
        max_pages=2,
        detail_limit=2,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "open course has no canonical application link" in meta["configured_collection_error"]


def test_declared_extra_page_respects_max_pages_and_fails_closed():
    network, _information, resident, _expired = _successful_network()
    source = _source(resident)
    network.pages[guro._source_url(source, 1)] = _list_page(
        [resident], total=1001, page=1, pages=2
    )
    rows, _parser, meta = guro.collect_guro_education_courses(
        _target(),
        max_pages=2,
        detail_limit=2,
        today="2026-07-19",
        fetcher=network.fetcher,
        session_factory=network.session_factory,
    )

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "max_pages cap" in meta["configured_collection_error"]
