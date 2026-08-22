from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
import yaml

from Crawler import Crawler_MunicipalIntegratedReservation as aggregate
from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import municipal_sejong_emd as sejong


ROOT = Path(__file__).resolve().parents[1]


def _target(*, provider: str = sejong.SEJONG_EMD_PROVIDER, url: str = sejong.SEJONG_EMD_CANONICAL_URL):
    return {
        "provider": provider,
        "url": url,
        "name": "세종특별자치시 읍면동 주민자치 교육",
        "branch": "세종특별자치시 읍면동 주민자치프로그램",
    }


def _form() -> str:
    controls = "".join(
        f'<input name="{name}" value="">'
        for name in (
            "pageUnit",
            "pageIndex",
            "pageSize",
            "suborgCode",
            "groupYn",
            "stDt",
            "edDt",
            "searchCondition",
            "searchKeyword",
        )
    )
    return f"""
      <form name="eduSearchForm" method="post"
            action="/prog/lecCourse/EMD/dong/sub03_03/intro.do">
        {controls}
        <select name="state">
          <option value="">-강좌상태-</option>
          <option value="1">모집중</option><option value="2">대기중</option>
          <option value="3">교육중</option><option value="4">교육종료</option>
        </select>
      </form>
    """


def _list_row(
    edu_no: str,
    *,
    title: str,
    suborg: str,
    slug: str,
    status: str,
    capacity: str = "3 / 20",
    include_info: bool = True,
    detail_path: str = "",
) -> str:
    info = (
        "운영일자 : 월 / 운영시간 : 10:00 ~ 12:00 / 수강인원 : 20 / 수강료 : 10,000원 "
        "/ 수강접수일정 : 08-01 ~ 08-20 / 교육장소 : 문화실"
        if include_info
        else "운영시간 : 10:00 ~ 12:00"
    )
    href = detail_path or (
        f"/prog/lecCourse/EMD/{slug}/sub03_03/view.do?pageIndex=1&eduNo={edu_no}&oneInwon="
    )
    return f"""
      <tr>
        <td data-cell-header="순번">1</td><td data-cell-header="읍면동">{suborg}</td>
        <td data-cell-header="교육과정">{title}</td>
        <td data-cell-header="신청인원/모집인원">{capacity}</td>
        <td data-cell-header="프로그램정보">{info}</td>
        <td data-cell-header="상태"><a href="{href}">{status}</a></td>
      </tr>
    """


def _list_html(total: int, rows: str, *, include_form: bool = True) -> str:
    body = rows or '<tr><td colspan="6">검색된 내용이 없습니다.</td></tr>'
    return f"""
      <html><body>{_form() if include_form else ''}
        <div class="program--count">총 게시물 <strong>{total}</strong> 개</div>
        <table class="table-default">
          <caption>강좌명/강사명, 대상, 접수기간, 교육기간, 신청인원/모집인원, 시간, 상태</caption>
          <thead><tr>
            <th>순번</th><th>읍면동</th><th>교육과정</th><th>신청인원/모집인원</th>
            <th>프로그램정보</th><th>상태</th>
          </tr></thead><tbody>{body}</tbody>
        </table>
        <nav><a href="?pageIndex=2">다음</a><a href="/bbs/notice/view.do">공지사항</a></nav>
      </body></html>
    """


def _detail_html(
    title: str,
    *,
    state: str,
    edu_no: str,
    slug: str,
    venue: str = "문화실",
    omit_period: bool = False,
) -> str:
    period = "" if omit_period else '<div class="li"><b><i>교육기간 아이콘</i>교육기간</b>2026-09-01 ~ 2026-12-20</div>'
    if state == "1":
        control = (
            f'<a href="/prog/lecReserve/EMD/{slug}/sub03_03/write.do?'
            f'pageIndex=1&eduNo={edu_no}&oneInwon=&resvChk=N">신청하기</a>'
        )
    elif state == "2":
        control = '<a href="#">대기중</a>'
    else:
        control = '<a href="#">접수마감</a>'
    return f"""
      <html><body><div id="txt"><div class="program--contents">
        <div class="caption-inner"><strong class="caption-title">{title}</strong>
          <div class="caption-info">
            <div class="li"><b><i>교육시간 아이콘</i>교육시간</b><ul><li>월 10:00~12:00</li></ul></div>
            {period}
            <div class="li"><b><i>접수기간 아이콘</i>접수기간</b>2026-08-01 09:00 ~ 2026-08-20 18:00</div>
            <div class="li"><b><i>담당자 아이콘</i>담당자</b></div>
            <div class="li"><b><i>접수 대상 아이콘</i>접수 대상</b>세종시민</div>
            <div class="li"><b><i>수업료 아이콘</i>수업료</b>월 10,000원</div>
            <div class="li"><b><i>재료비 아이콘</i>재료비</b>별도</div>
            <div class="li"><b><i>실습비 아이콘</i>실습비</b>0</div>
          </div>
        </div>
        <div class="figure"><div class="btn_wrap">{control}</div></div>
        <div class="apply-article">
          <div class="self-accrdt"><div class="item"><strong>교육정원</strong><em>20 명</em></div></div>
          <div class="self-accrdt"><div class="item"><strong>교육대상</strong><em>성인</em></div></div>
          <div class="self-accrdt"><div class="item"><strong>교육장소</strong><em>{venue}</em></div></div>
          <div class="self-accrdt"><div class="item"><strong>문의전화</strong><em>044-300-0000</em></div></div>
        </div>
        <a href="javascript:fn_egov_downFile('secret')">첨부파일</a>
      </div></div></body></html>
    """


@dataclass
class _Response:
    url: str
    body: str
    status_code: int = 200

    @property
    def content(self) -> bytes:
        return self.body.encode("utf-8")

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Type": "text/html; charset=UTF-8"}


class _FixtureSource:
    def __init__(
        self,
        *,
        bad_info: bool = False,
        omit_period: bool = False,
        open_suborg: str = "보람동",
        open_venue: str = "문화실",
    ) -> None:
        self.bad_info = bad_info
        self.omit_period = omit_period
        self.open_suborg = open_suborg
        self.open_venue = open_venue
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def response(self, url: str, params: dict[str, str] | None) -> _Response:
        self.calls.append((url, dict(params) if params is not None else None))
        if url == sejong.SEJONG_EMD_CANONICAL_URL:
            assert params is not None
            assert params["pageUnit"] == str(sejong.SEJONG_EMD_PAGE_SIZE)
            assert params["pageSize"] == str(sejong.SEJONG_EMD_PAGE_SIZE)
            assert params["suborgCode"] == "" and params["searchKeyword"] == ""
            state, page = params["state"], int(params["pageIndex"])
            rows = ""
            total = 0
            if state == "1":
                total = 1
                if page == 1:
                    rows = _list_row(
                        "20001",
                        title="도예교실",
                        suborg=self.open_suborg,
                        slug="boram",
                        status="모집중",
                        include_info=not self.bad_info,
                    )
            elif state == "3":
                total = 1
                if page == 1:
                    rows = _list_row(
                        "19001",
                        title="라인댄스",
                        suborg="전동면",
                        slug="jeondong",
                        status="모집마감",
                        capacity="4 / 15 대기(0/0/)",
                    )
            final_url = f"{url}?{urlencode(params)}"
            return _Response(final_url, _list_html(total, rows))
        if "eduNo=20001" in url:
            return _Response(
                url,
                _detail_html(
                    "도예교실",
                    state="1",
                    edu_no="20001",
                    slug="boram",
                    venue=self.open_venue,
                ),
            )
        if "eduNo=19001" in url:
            return _Response(
                url,
                _detail_html(
                    "라인댄스",
                    state="3",
                    edu_no="19001",
                    slug="jeondong",
                    venue="",
                    omit_period=self.omit_period,
                ),
            )
        raise AssertionError(f"unexpected request: {url} params={params}")


class _Session:
    def __init__(self, source: _FixtureSource) -> None:
        self.source = source

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        timeout: int,
        allow_redirects: bool,
    ) -> _Response:
        assert timeout > 0 and allow_redirects is False
        return self.source.response(url, params)

    def close(self) -> None:
        return None


def _collect(source: _FixtureSource, **kwargs: Any):
    return sejong.collect_sejong_emd_education(
        _target(),
        timeout=10,
        max_pages=20,
        detail_limit=10,
        session_factory=lambda: _Session(source),
        today="2026-08-06",
        max_workers=1,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("suborg", "venue", "branch", "address"),
    [
        (
            "연서면",
            "연서면사무소 2층 문화사랑방(대첩로 238)",
            "연서면행정복지센터",
            "세종특별자치시 연서면 대첩로 238",
        ),
        (
            "연서면",
            "봉암출장소 2층(당산로291)",
            "연서면행정복지센터 봉암출장소",
            "세종특별자치시 연서면 당산로 291",
        ),
        (
            "연서면",
            "갤러리 985(쌍류예술촌길 22)",
            "연서면 갤러리 985",
            "세종특별자치시 연서면 쌍류예술촌길 22",
        ),
        (
            "연서면",
            "한국DIY가구공방협회(도신고복로 443)",
            "연서면 한국DIY가구공방협회",
            "세종특별자치시 연서면 도신고복로 443",
        ),
        ("보람동", "GX룸1(3층)", "보람동 행복누림터", ""),
        ("대평동", "대평동 복컴 3층 G", "대평동 행복누림터", ""),
        ("부강면", "복지회관 3층", "부강면문화복지회관", ""),
        ("장군면", "장군면 복지회관 2층", "장군면복지회관", ""),
        ("전동면", "전동면 복컴 3층 다목적강당", "전동면 복합커뮤니티센터", ""),
        ("한솔동", "훈민관 203호(누리실)", "한솔동 행복누림터 훈민관", ""),
    ],
)
def test_venue_location_uses_only_reviewed_canonical_facilities_and_road_addresses(
    suborg: str,
    venue: str,
    branch: str,
    address: str,
) -> None:
    location = sejong.sejong_emd_venue_location(suborg, venue)

    assert location.branch == branch
    assert location.address == address


@pytest.mark.parametrize(
    ("suborg", "venue"),
    [
        ("연기면", "파크골프장/복지회관1층"),
        ("나성동", "나성동 복컴 2층 문화사랑방1,한솔파크골프장,오가낭뜰근린공원"),
        ("아름동", "아름1실/남세종청소년센터"),
    ],
)
def test_multi_place_venue_is_never_collapsed_or_given_one_address(suborg: str, venue: str) -> None:
    location = sejong.sejong_emd_venue_location(suborg, venue)

    assert location.branch == f"{suborg} {venue}"
    assert location.branch_identity == venue
    assert location.address == ""


def test_room_parentheses_are_not_misread_as_an_address_and_room_branches_share_identity() -> None:
    first = sejong.sejong_emd_venue_location("보람동", "GX룸1(3층)")
    second = sejong.sejong_emd_venue_location("보람동", "문화2실(4층)")

    assert first.address == second.address == ""
    assert first.branch_identity == second.branch_identity == "보람동 행복누림터"
    assert sejong._branch_code("보람동", first.branch_identity) == sejong._branch_code(
        "보람동", second.branch_identity
    )
    assert sejong.sejong_emd_venue_location("부강면", "복지회관 2층").branch != (
        sejong.sejong_emd_venue_location("장군면", "복지회관 2층").branch
    )


def test_exact_target_and_every_list_request_keeps_the_reviewed_filters() -> None:
    assert sejong.is_sejong_emd_target(_target()) is True
    assert sejong.is_sejong_emd_target(_target(provider="OTHER")) is False
    assert sejong.is_sejong_emd_target(_target(url=sejong.SEJONG_EMD_CANONICAL_URL + "?state=1")) is False

    source = _FixtureSource()
    rows, parser, meta = _collect(source)

    assert parser == sejong.SEJONG_EMD_PARSER
    assert len(rows) == 2
    assert meta["source_totals"] == {"1": 1, "2": 0, "3": 1}
    assert meta["source_counts"] == {"1": 1, "2": 0, "3": 1}
    assert meta["list_requests"] == meta["required_list_requests"] == 9
    assert meta["detail_pages"] == 2
    assert meta["snapshot_complete"] is True
    list_calls = [params for url, params in source.calls if url == sejong.SEJONG_EMD_CANONICAL_URL]
    assert len(list_calls) == 9
    assert all(params and set(params) == set(sejong.sejong_emd_list_payload("1", 1)) for params in list_calls)
    assert {params["state"] for params in list_calls if params} == {"1", "2", "3"}


def test_rows_are_detail_verified_and_application_routes_are_never_requested_or_stored() -> None:
    source = _FixtureSource()
    rows, _parser, meta = _collect(source)
    by_id = {row["provider_course_id"].rsplit(":", 1)[-1]: row for row in rows}

    opened = by_id["20001"]
    assert opened["status"] == "OPEN"
    assert opened["application_url"] == opened["raw_url"]
    assert "/lecReserve/" not in opened["application_url"]
    assert opened["reservation_available"] is True
    assert opened["branch"] == "보람동 행복누림터"
    assert opened["region_sido"] == opened["region_sigungu"] == "세종특별자치시"

    closed = by_id["19001"]
    assert closed["status"] == "CLOSED"
    assert closed["application_url"] == ""
    assert closed["branch"] == "전동면 주민자치프로그램"
    assert closed["venue_name"] == ""
    assert closed["raw_fields"]["source_venue_missing"] is True
    assert meta["application_control_count"] == 1
    assert not any("/lecReserve/" in url or "downFile" in url for url, _params in source.calls)


def test_inline_official_road_address_is_emitted_and_saved_as_branch_location() -> None:
    source = _FixtureSource(
        open_suborg="연서면",
        open_venue="연서면사무소 2층 문화사랑방(대첩로 238)",
    )
    rows, _parser, meta = _collect(source)
    opened = next(row for row in rows if row["provider_course_id"].endswith(":20001"))

    assert meta["snapshot_complete"] is True
    assert opened["branch"] == "연서면행정복지센터"
    assert opened["venue_name"] == "연서면사무소 2층 문화사랑방(대첩로 238)"
    assert opened["branch_address"] == opened["venue_address"] == opened["address"] == (
        "세종특별자치시 연서면 대첩로 238"
    )
    assert opened["branch_address_source"] == "OFFICIAL_SEJONG_EMD_DETAIL_VENUE"
    assert opened["raw_fields"]["source_venue_address_verified"] is True

    saved = municipal.MunicipalDbWriter(sejong.SEJONG_EMD_PROVIDER).branch_info_from_row(opened)
    assert saved["name"] == "연서면행정복지센터"
    assert saved["address"] == "세종특별자치시 연서면 대첩로 238"
    assert saved["address_source"] == "OFFICIAL_SEJONG_EMD_DETAIL_VENUE"


def test_non_course_or_incomplete_detail_fails_the_atomic_snapshot() -> None:
    rows, _parser, meta = _collect(_FixtureSource(bad_info=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "structured programme fields" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_FixtureSource(omit_period=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "caption field directory" in meta["configured_collection_error"]


def test_source_caps_fail_before_a_partial_snapshot_can_be_returned() -> None:
    source = _FixtureSource()
    rows, _parser, meta = sejong.collect_sejong_emd_education(
        _target(),
        timeout=10,
        max_pages=8,
        detail_limit=10,
        session_factory=lambda: _Session(source),
        today="2026-08-06",
        max_workers=1,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "required list requests=9" in meta["configured_collection_error"]


def test_dispatch_target_and_operational_allowlist_are_bound() -> None:
    target_document = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "public_reservation.yaml").read_text(encoding="utf-8")
    )
    target = next(row for row in target_document["targets"] if row.get("provider") == sejong.SEJONG_EMD_PROVIDER)
    assert target["url"] == sejong.SEJONG_EMD_CANONICAL_URL
    assert target["domain_category"] == "교육·강좌"
    assert target["service_group"] == "공공강좌"
    assert target["ops_scopes"] == ["education"]
    assert target["crawler_module"] == "Crawler.municipal_sejong_emd"
    assert target["ownership_scope"] == sejong.SEJONG_EMD_OWNERSHIP_SCOPE
    assert target["excluded_scope"].startswith("education_ended_archive_notice_navigation")

    operational = next(
        row for row in aggregate.load_operational_entries() if row["provider"] == sejong.SEJONG_EMD_PROVIDER
    )
    assert operational["target_url"] == sejong.SEJONG_EMD_CANONICAL_URL
    assert operational["row_count"] == 524
    assert operational["municipalities"] == [
        {
            "code": "3611000000",
            "sido": "세종특별자치시",
            "sigungu": "세종특별자치시",
            "full_name": "세종특별자치시",
        }
    ]

    sentinel = ([{"provider": sejong.SEJONG_EMD_PROVIDER}], sejong.SEJONG_EMD_PARSER, {"ok": True})

    def collect(*_args: Any, **_kwargs: Any):
        return sentinel

    original = sejong.collect_sejong_emd_education
    try:
        sejong.collect_sejong_emd_education = collect
        crawl_target = municipal.CrawlTarget(
            provider=sejong.SEJONG_EMD_PROVIDER,
            name=target["name"],
            branch=target["branch"],
            url=target["url"],
            source=target["source"],
        )
        assert municipal.collect_from_url(crawl_target, max_pages=20, detail_limit=800) == sentinel
    finally:
        sejong.collect_sejong_emd_education = original
