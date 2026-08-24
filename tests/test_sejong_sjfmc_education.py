from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from Crawler import municipal_sejong_sjfmc as sejong


def _target(**overrides: str) -> dict[str, str]:
    result = {
        "provider": sejong.SEJONG_SJFMC_PROVIDER,
        "url": sejong.SEJONG_SJFMC_URL,
        "name": "세종시설공단 전체 교육·강좌",
        "branch": "세종특별자치시",
    }
    result.update(overrides)
    return result


def _directory_html(*, omit_last: bool = False) -> str:
    rows = list(sejong.SEJONG_SJFMC_DIRECTORY)
    if omit_last:
        rows.pop()
    links = "".join(
        f'<a href="https://{sejong.SEJONG_SJFMC_HOST}/lecture/llist/index/'
        f'{center}/2001/L/{category}">{name}</a>'
        for center, category, name in rows
    )
    return f"""
    <html><head><title>세종시설공단 - 인터넷예약시스템 &gt; 교육/강좌</title></head>
    <body>{links}</body></html>
    """


def _detail_url(source: sejong.SejongSource, sequence: int) -> str:
    return (
        f"https://{sejong.SEJONG_SJFMC_HOST}/lecture/detail/index/"
        f"{source.center_code}/2001/{sequence:05d}/I000001"
    )


def _list_html(
    source: sejong.SejongSource,
    sequence: int,
    *,
    status: str = "접수준비",
    total: int = 1,
    active: int = 1,
    last: int = 1,
) -> str:
    detail = _detail_url(source, sequence)
    return f"""
    <html><head><title>세종시설공단 - 인터넷예약시스템 &gt; 교육/강좌</title></head>
    <body>
      <table class="table_class_list"><tbody><tr>
        <td class="table_content1" rowspan="1"><a href="{detail}">공식 수영 {sequence}</a></td>
        <td class="table_content8" rowspan="1">공식 강사</td>
        <td class="table_content2" rowspan="1">성인</td>
        <td class="table_content3" rowspan="1">월수금<br>10:00~10:50</td>
        <td class="table_content1"><a href="{detail}">주3회 (성인)</a></td>
        <td class="table_content5">10,000</td>
        <td class="table_content6" rowspan="1">8</td>
        <td class="table_content7" rowspan="1"><img alt="{status}" src="/status.gif"></td>
        <td class="table_content8"><a href="{detail}"><img alt="상세보기"></a></td>
      </tr></tbody></table>
      <li class="total_info">전체갯수 : {total}&nbsp;&nbsp;&nbsp;페이지 : {active} / {last}</li>
    </body></html>
    """


def _detail_html(
    source: sejong.SejongSource,
    sequence: int,
    *,
    title_suffix: str = "",
    application_control: bool = False,
) -> str:
    control = (
        '<div class="button_area1"><a href="/member/login" title="수강신청">'
        '<img alt="수강신청"></a></div>'
        if application_control
        else '<div class="button_area1"><a href="/lecture/llist/index" title="목록보기">목록보기</a></div>'
    )
    return f"""
    <html><head><title>세종시설공단 - 인터넷예약시스템 &gt; 상세보기</title></head>
    <body>
      <table class="lecture_preview_table"><tbody>
        <tr><td class="table_lecture_title" colspan="2">공식 수영 {sequence} - 주3회 (성인){title_suffix}</td></tr>
        <tr><td class="td_label">교육대상</td><td>성인</td></tr>
        <tr><td class="td_label">교육기간</td><td>2099-02-01~2099-02-28 (1개월)</td></tr>
        <tr><td class="td_label">교육시간</td><td>월수금 / 10:00~10:50</td></tr>
        <tr><td class="td_label">교육장소</td><td>{source.center_name} 수영장</td></tr>
        <tr><td class="td_label">수강료(원)</td><td>10,000</td></tr>
        <tr><td class="td_label">신규접수기간</td><td>2099-01-10 10:00 (토) ~ 2099-01-20 18:00 (화)</td></tr>
      </tbody></table>
      <div class="lecture_status_area"><img alt="온라인 수강신청 기간이 아닙니다."></div>
      {control}
      <table class="receipt_status_table1"><tbody>
        <tr><td>대기접수 추첨</td><td>10</td><td>2</td></tr>
      </tbody></table>
      <table class="lecture_detail_table"><tbody>
        <tr><td>강좌소개</td><td>공식 강좌 소개</td></tr>
        <tr><td>강좌내용</td><td>공식 강좌 내용</td></tr>
        <tr><td>강의계획서</td><td></td></tr>
      </tbody></table>
    </body></html>
    """


@dataclass
class FakeSession:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class FixtureSite:
    def __init__(
        self,
        *,
        omit_directory: bool = False,
        mutate_sentinel: bool = False,
        detail_title_suffix: str = "",
        status: str = "접수준비",
        application_control: bool = False,
    ) -> None:
        self.omit_directory = omit_directory
        self.mutate_sentinel = mutate_sentinel
        self.detail_title_suffix = detail_title_suffix
        self.status = status
        self.application_control = application_control
        self.calls: list[str] = []
        self.sessions: list[FakeSession] = []

    def session_factory(self) -> FakeSession:
        value = FakeSession()
        self.sessions.append(value)
        return value

    def fetcher(self, _session: Any, url: str, _timeout: int) -> str:
        self.calls.append(url)
        if url == sejong.SEJONG_SJFMC_URL:
            return _directory_html(omit_last=self.omit_directory)
        for index, source in enumerate(sejong.SEJONG_SJFMC_SOURCES, start=1):
            if url == sejong.sejong_sjfmc_list_url(source, 1):
                return _list_html(source, index, status=self.status)
            if url == sejong.sejong_sjfmc_list_url(source, 2):
                sequence = index + 10 if self.mutate_sentinel else index
                return _list_html(source, sequence, status=self.status)
            if url == _detail_url(source, index):
                return _detail_html(
                    source,
                    index,
                    title_suffix=self.detail_title_suffix,
                    application_control=self.application_control,
                )
        raise AssertionError(f"unexpected URL {url}")


def _collect(site: FixtureSite, **kwargs: Any):
    return sejong.collect(
        _target(),
        timeout=1,
        max_pages=4,
        detail_limit=2,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2099-01-01",
        max_workers=2,
        **kwargs,
    )


def test_exact_target_contract() -> None:
    assert sejong.is_target(_target())
    assert not sejong.is_target(_target(url=sejong.SEJONG_SJFMC_URL + "?page=1"))
    assert not sejong.is_target(_target(provider="MUNI_OTHER"))
    assert sejong.SEJONG_SJFMC_CANDIDATE_ID == "MUNI_IR_32F6E17F0ADD"


def test_collects_complete_two_source_snapshot() -> None:
    from Crawler.Crawler_MunicipalYaml import MunicipalDbWriter

    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == sejong.SEJONG_SJFMC_PARSER
    assert len(rows) == 2
    assert meta["declared_totals"] == {"SEJONG01:100": 1, "SEJONG03:100": 1}
    assert meta["list_requests"] == meta["required_list_requests"] == 4
    assert meta["detail_pages"] == meta["detail_attempts"] == 2
    assert meta["snapshot_complete"] is True
    assert meta["returned_count"] == 2
    assert {row["branch"] for row in rows} == {
        "세종특별자치시 · 보람수영장",
        "세종특별자치시 · 조치원복합커뮤니티센터",
    }
    assert {row["branch_address"] for row in rows} == {
        "세종특별자치시 호려울로 42",
        "세종특별자치시 조치원읍 대첩로 76",
    }
    assert all(row["venue_address"] == row["branch_address"] for row in rows)
    assert all(row["branch_address_source"] == "official_facility_page" for row in rows)
    assert all(row["municipality_full_name"] == "세종특별자치시" for row in rows)
    assert all(row["municipality_region_verified"] is True for row in rows)
    assert all(row["region_sido"] == "세종특별자치시" for row in rows)
    assert all(row["region_sigungu"] == "세종특별자치시" for row in rows)
    saved_branches = [
        MunicipalDbWriter(sejong.SEJONG_SJFMC_PROVIDER).branch_info_from_row(row)
        for row in rows
    ]
    assert {branch["address"] for branch in saved_branches} == {
        "세종특별자치시 호려울로 42",
        "세종특별자치시 조치원읍 대첩로 76",
    }
    assert all(branch["region_sido"] == "세종특별자치시" for branch in saved_branches)
    assert all(branch["region_sigungu"] == "세종특별자치시" for branch in saved_branches)
    assert all(row["domain_category"] == "교육" for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["fee"] == 10_000 for row in rows)
    assert all(row["capacity_remaining"] == 8 for row in rows)
    assert all(session.closed for session in site.sessions)


def test_detail_capacity_is_authoritative_when_list_summary_is_stale() -> None:
    source = sejong.SEJONG_SJFMC_SOURCES[0]
    list_soup = sejong.BeautifulSoup(
        _list_html(source, 1).replace(
            '<td class="table_content6" rowspan="1">8</td>',
            '<td class="table_content6" rowspan="1">1</td>',
        ),
        "lxml",
    )
    listed = sejong._parse_list_page(list_soup, source)[0]

    row = sejong._parse_detail(
        _target(),
        listed,
        sejong.BeautifulSoup(_detail_html(source, 1), "lxml"),
        sejong.date.fromisoformat("2099-01-01"),
    )

    assert row["capacity_total"] == 10
    assert row["capacity_current"] == 2
    assert row["capacity_remaining"] == 8
    assert row["raw_fields"]["list_capacity_value"] == 1
    assert row["raw_fields"]["list_detail_capacity_mismatch"] is True


def test_directory_drift_fails_closed() -> None:
    rows, _, meta = _collect(FixtureSite(omit_directory=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "directory changed" in meta["configured_collection_error"]


def test_wrapped_page_one_must_have_same_signature() -> None:
    rows, _, meta = _collect(FixtureSite(mutate_sentinel=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "wrapped sentinel differs" in meta["configured_collection_error"]


def test_any_detail_mismatch_fails_whole_snapshot() -> None:
    rows, _, meta = _collect(FixtureSite(detail_title_suffix=" 변경"))
    assert rows == []
    assert meta["detail_errors"] == 2
    assert meta["snapshot_complete"] is False
    assert "list/detail course title mismatch" in meta["configured_collection_error"]


def test_open_rows_require_a_public_application_control() -> None:
    rows, _, meta = _collect(FixtureSite(status="접수중"))
    assert rows == []
    assert meta["detail_errors"] == 2
    assert "open course has no public application control" in meta["configured_collection_error"]


def test_open_rows_publish_detail_application_url_when_control_exists() -> None:
    rows, _, meta = _collect(
        FixtureSite(status="접수중", application_control=True)
    )
    assert meta["snapshot_complete"] is True
    assert len(rows) == 2
    assert all(row["status"] == "OPEN" for row in rows)
    assert all(row["application_url"] == row["raw_url"] for row in rows)
    assert meta["reservation_discovery_links"] == 2


def test_detail_cap_fails_before_partial_details_are_published() -> None:
    site = FixtureSite()
    rows, _, meta = sejong.collect(
        _target(),
        timeout=1,
        max_pages=4,
        detail_limit=1,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2099-01-01",
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert "detail_limit cap" in meta["configured_collection_error"]
