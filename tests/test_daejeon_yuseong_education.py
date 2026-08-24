from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_daejeon_yuseong as ys


def test_preparsed_safe_session_html_keeps_document_structure() -> None:
    soup = BeautifulSoup(
        "<html><head><title>official</title></head><body><form name='searchForm'></form></body></html>",
        "lxml",
    )

    assert ys._response_soup(soup) is soup
    assert ys._response_soup(soup).select_one("form[name=searchForm]") is not None


@dataclass
class Target:
    provider: str = ys.DAEJEON_YUSEONG_PROVIDER
    url: str = ys.DAEJEON_YUSEONG_CANONICAL_URL
    branch: str = ys.DAEJEON_YUSEONG_MUNICIPALITY_NAME


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(
        self,
        url: str,
        text: str,
        *,
        final_url: str | None = None,
        status_code: int = 200,
    ) -> None:
        self.url = final_url or url
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.history: list[Any] = []


def _record(
    identity: str,
    catalogue: str,
    *,
    title: str | None = None,
    status: str = "접수마감",
    start: str = "2099-07-21",
    end: str = "2099-08-31",
) -> dict[str, str]:
    item = ys.DAEJEON_YUSEONG_CATALOGUE_BY_KEY[catalogue]
    return {
        "identity": identity,
        "catalogue": catalogue,
        "title": title or f"공식 교육 {identity}",
        "badge": item.heading,
        "status": status,
        "start": start,
        "end": end,
        "apply_start": "2099-07-01 09:00",
        "apply_end": "2099-07-31 18:00",
        "fee": "무료",
        "schedule": "화요일(10:00~12:00)",
        "target": "유성구민",
        "capacity": "20명",
    }


def _card(record: dict[str, str]) -> str:
    return f"""
      <a class="inner-box button_view" data-key-no="{record['identity']}" href="#">
        <div class="status-wrap">
          <span class="status status1 place">{record['badge']}</span>
          <span class="status status2">{record['status']}</span>
        </div>
        <strong class="title">{record['title']}</strong>
        <ul>
          <li><span class="tit">접수기간</span><em class="txt">
            {record['apply_start']} ~ {record['apply_end']} (1 차 접수기간)
          </em></li>
          <li><span class="tit">수강료</span><em class="txt">{record['fee']}</em></li>
          <li><span class="tit">교육기간</span><em class="txt">
            {record['start']} ~ {record['end']}
          </em></li>
          <li><span class="tit">모집인원</span><em class="txt">{record['capacity']}</em></li>
          <li><span class="tit">교육일시</span><em class="txt">{record['schedule']}</em></li>
          <li><span class="tit">접수인원</span><em class="txt">11명</em></li>
          <li><span class="tit">교육대상</span><em class="txt">{record['target']}</em></li>
          <li><span class="tit">접수방법</span><em class="txt">홈페이지 접수</em></li>
          <li><span class="tit">문의처</span><em class="txt">042-611-0000</em></li>
        </ul>
      </a>
    """


def _list_page(
    catalogue: ys.YuseongCatalogue,
    records: list[dict[str, str]],
    *,
    page: int,
    total: int,
    page_size: int,
) -> str:
    last = max(1, math.ceil(total / page_size))
    cards = "".join(_card(record) for record in records)
    return f"""
      <html><head><title>{catalogue.heading} | 유성구 평생학습센터</title></head>
      <body>
        <h2 class="page__title">{catalogue.heading}</h2>
        <form id="searchForm" method="post" action="{catalogue.path}">
          <input type="hidden" name="pageIndex" value="{page}">
          <input type="hidden" name="lctrNo" value="">
          <input type="hidden" name="searchLctrNo" value="">
          <input type="hidden" name="lctrGroupType" value="{catalogue.group_type}">
          <input type="hidden" name="searchLctrGroupCd" value="{catalogue.group_code}">
        </form>
        <div class="program--count">Total <strong>{total}</strong> 개,
          페이지 (<strong>{page}</strong>/{last})</div>
        <div class="cards">{cards}</div>
      </body></html>
    """


def _landing_page(*, omit_path: str = "") -> str:
    links = []
    for catalogue in ys.DAEJEON_YUSEONG_CATALOGUES:
        if catalogue.path == omit_path:
            continue
        text = "온라인신청" if catalogue.key == "all" else catalogue.heading
        links.append(f'<a href="{catalogue.path}">{text}</a>')
    return (
        "<html><head><title>유성구 평생학습센터</title></head><body>"
        + "".join(links)
        + "</body></html>"
    )


def _detail_page(
    record: dict[str, str],
    *,
    bad_title: bool = False,
    application_control: bool | None = None,
) -> str:
    title = "다른 강좌" if bad_title else record["title"]
    if application_control is None:
        application_control = record["status"] in {"접수중", "대기자 접수중"}
    button = (
        '<button class="button_write" type="button">수강 신청하기</button>'
        if application_control
        else ""
    )
    return f"""
      <html><head><title>내게맞는 강좌 찾기 | 유성구 평생학습센터</title></head>
      <body>
        <form id="searchForm"><input id="lctrNo" name="lctrNo"
          value="{record['identity']}"></form>
        <div class="view-wrap">
          <strong class="title">{title}</strong>
          <ul class="info-list">
            <li><strong class="subjact">교육기간</strong><span class="con">
              {record['start']} ~ {record['end']}</span></li>
            <li><strong class="subjact">교육시간</strong><span class="con">
              {record['schedule']}</span></li>
            <li><strong class="subjact">문의처</strong><span class="con">
              전민센터 (042-611-6580)</span></li>
            <li><strong class="subjact">강사명</strong><span class="con">
              홍길동 선생님</span></li>
            <li><strong class="subjact">인원</strong><span class="con">
              접수인원 11명 / 모집정원 {record['capacity']}</span></li>
          </ul>
          {button}
        </div>
        <script>
          $(".button_write").click(function() {{
            fn_submit("{ys.DAEJEON_YUSEONG_APPLICATION_PATH}");
          }});
        </script>
      </body></html>
    """


@pytest.fixture
def complete_source(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(ys, "DAEJEON_YUSEONG_PAGE_SIZE", 2)
    leaf_records = {
        "guam": [_record("101", "guam")],
        "jeonmin": [_record("102", "jeonmin")],
        "youth_5060": [
            _record("103", "youth_5060", title="재능기부자 모집")
        ],
        "linku": [_record("104", "linku", status="접수중")],
        "special": [_record("105", "special")],
        "oneday": [_record("106", "oneday")],
        "humanities": [
            _record(
                "107",
                "humanities",
                start="2090-01-01",
                end="2090-01-02",
            )
        ],
        "disabled": [_record("108", "disabled")],
        "slow_learner": [_record("109", "slow_learner")],
    }
    all_records = [
        record
        for key, records in leaf_records.items()
        if key != "humanities"
        for record in records
    ]
    records_by_catalogue = {
        "all": all_records,
        "regular": leaf_records["guam"] + leaf_records["jeonmin"],
        **leaf_records,
    }
    mapping: dict[str, tuple[str, str | None]] = {
        ys.DAEJEON_YUSEONG_LANDING_URL: (
            _landing_page(),
            f"https://{ys.DAEJEON_YUSEONG_HOST}{ys.DAEJEON_YUSEONG_LANDING_PATH}",
        )
    }
    for catalogue in ys.DAEJEON_YUSEONG_CATALOGUES:
        records = records_by_catalogue[catalogue.key]
        total = len(records)
        last = max(1, math.ceil(total / ys.DAEJEON_YUSEONG_PAGE_SIZE))
        for page in range(1, last + 1):
            chunk = records[
                (page - 1) * ys.DAEJEON_YUSEONG_PAGE_SIZE :
                page * ys.DAEJEON_YUSEONG_PAGE_SIZE
            ]
            html = _list_page(
                catalogue,
                chunk,
                page=page,
                total=total,
                page_size=ys.DAEJEON_YUSEONG_PAGE_SIZE,
            )
            if page == 1:
                mapping[catalogue.list_url] = (html, None)
            mapping[ys.daejeon_yuseong_list_url(catalogue.key, page)] = (
                html,
                None,
            )
        mapping[
            ys.daejeon_yuseong_list_url(catalogue.key, last + 1)
        ] = (
            _list_page(
                catalogue,
                [],
                page=last + 1,
                total=total,
                page_size=ys.DAEJEON_YUSEONG_PAGE_SIZE,
            ),
            None,
        )

    current_education = [
        record
        for key, records in leaf_records.items()
        for record in records
        if record["end"] >= "2099-07-21" and record["title"] != "재능기부자 모집"
    ]
    for record in current_education:
        url = ys.daejeon_yuseong_detail_url(record["identity"])
        mapping[url] = (_detail_page(record), None)

    calls: list[str] = []

    def fetcher(_session: Any, url: str, _timeout: int) -> FakeResponse:
        calls.append(url)
        if url not in mapping:
            raise AssertionError(f"unexpected URL {url}")
        text, final_url = mapping[url]
        return FakeResponse(url, text, final_url=final_url)

    return {
        "leaf_records": leaf_records,
        "records_by_catalogue": records_by_catalogue,
        "current_education": current_education,
        "mapping": mapping,
        "calls": calls,
        "fetcher": fetcher,
    }


def _collect(source: dict[str, Any], **overrides: Any):
    values = {
        "today": "2099-07-21",
        "max_pages": 50,
        "detail_limit": 20,
        "fetcher": source["fetcher"],
        "session_factory": DummySession,
        "max_workers": 3,
    }
    values.update(overrides)
    return ys.collect_daejeon_yuseong_education(Target(), **values)


def test_target_candidate_and_alias_contracts_are_strict() -> None:
    assert ys.is_daejeon_yuseong_education_target(Target())
    assert not ys.is_daejeon_yuseong_education_target(
        Target(url=ys.DAEJEON_YUSEONG_LANDING_URL)
    )
    assert not ys.is_daejeon_yuseong_education_target(
        Target(provider=ys.DAEJEON_YUSEONG_LEGACY_PROVIDERS[0])
    )
    assert ys.is_daejeon_yuseong_owned_alias_target(
        Target(
            provider=ys.DAEJEON_YUSEONG_LEGACY_PROVIDERS[0],
            url=ys.DAEJEON_YUSEONG_JEONMIN_URL,
        )
    )
    assert ys.is_daejeon_yuseong_owned_alias_target(
        Target(url=ys.daejeon_yuseong_detail_url("104"))
    )
    assert not ys.is_daejeon_yuseong_owned_alias_target(
        Target(url=ys.daejeon_yuseong_detail_url("104") + "&other=1")
    )
    assert ys.DAEJEON_YUSEONG_CANDIDATE_IDS == (
        "MUNI_IR_6F8200A35D1B",
        "MUNI_IR_70BBAFE5E162",
        "MUNI_IR_BDE4E2B1625F",
    )
    assert ys.DAEJEON_YUSEONG_OFFICIAL_BRANCH_NAMES == (
        "구암평생학습센터",
        "전민평생학습센터",
        "5060 청춘대학",
        "링크유마을캠퍼스",
        "특별강좌",
        "원데이클래스",
        "별별인문학",
        "장애인 평생교육",
        "느린학습자",
    )


def test_complete_snapshot_uses_leaf_union_and_excludes_pii(
    complete_source: dict[str, Any],
) -> None:
    rows, parser, meta = _collect(complete_source)
    assert parser == ys.DAEJEON_YUSEONG_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == [
        "101",
        "102",
        "104",
        "105",
        "106",
        "108",
        "109",
    ]
    assert meta["source_rows"] == 9
    assert meta["source_totals"]["all"] == 8
    assert meta["source_totals"]["regular"] == 2
    assert meta["source_partition_counts"] == {
        "education": 8,
        "recruitment": 1,
    }
    assert meta["current_source_count"] == 8
    assert meta["current_education_count"] == 7
    assert meta["current_partition_counts"] == {
        "education": 7,
        "recruitment": 1,
    }
    assert meta["canonical_omission_count"] == 1
    assert meta["canonical_omission_humanities_count"] == 1
    assert meta["canonical_subset_verified"] is True
    assert meta["regular_alias_verified"] is True
    assert meta["identity_duplicate_count"] == 0
    assert meta["aggregate_alias_duplicate_rows"] == 10
    assert meta["detail_attempts"] == meta["detail_pages"] == 7
    assert meta["application_control_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert next(row for row in rows if row["raw_fields"]["identity"] == "104")[
        "application_url"
    ] == ys.daejeon_yuseong_detail_url("104")
    for row in rows:
        assert "instructor" not in row
        assert "contact" not in row
        assert "applicants" not in row
        assert "description" not in row
        assert "042-611" not in str(row)
        assert set(row) == ys._SAFE_ROW_KEYS
        assert set(row["raw_fields"]) == ys._SAFE_RAW_FIELDS


def test_all_pages_sentinels_and_page_one_rechecks_are_requested(
    complete_source: dict[str, Any],
) -> None:
    _rows, _parser, meta = _collect(complete_source)
    calls = complete_source["calls"]
    assert calls.count(ys.DAEJEON_YUSEONG_LANDING_URL) == 1
    assert calls.count(ys.DAEJEON_YUSEONG_CANONICAL_URL) == 1
    assert calls.count(ys.daejeon_yuseong_list_url("all", 1)) == 1
    assert calls.count(ys.daejeon_yuseong_list_url("all", 5)) == 1
    for catalogue in ys.DAEJEON_YUSEONG_CATALOGUES:
        last = meta["declared_pages"][catalogue.key]
        assert calls.count(ys.daejeon_yuseong_list_url(catalogue.key, last + 1)) == 1
        assert calls.count(ys.daejeon_yuseong_list_url(catalogue.key, 1)) == 1
    assert meta["required_page_requests"] == 37
    assert meta["landing_requests"] == 1
    assert meta["list_requests"] == 36
    assert meta["sentinel_requests"] == 11
    assert meta["stability_rechecks"] == 11


def test_page_and_detail_caps_fail_closed(complete_source: dict[str, Any]) -> None:
    rows, _parser, meta = _collect(complete_source, max_pages=36)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(complete_source, detail_limit=6)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_landing_menu_drift_fails_closed(complete_source: dict[str, Any]) -> None:
    humanities = ys.DAEJEON_YUSEONG_CATALOGUE_BY_KEY["humanities"]
    final_url = f"https://{ys.DAEJEON_YUSEONG_HOST}{ys.DAEJEON_YUSEONG_LANDING_PATH}"
    complete_source["mapping"][ys.DAEJEON_YUSEONG_LANDING_URL] = (
        _landing_page(omit_path=humanities.path),
        final_url,
    )
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert "landing official education menu fan-out changed" in meta[
        "configured_collection_error"
    ]


def test_nonempty_post_last_page_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    catalogue = ys.DAEJEON_YUSEONG_CATALOGUE_BY_KEY["special"]
    record = complete_source["leaf_records"]["special"][0]
    complete_source["mapping"][ys.daejeon_yuseong_list_url("special", 2)] = (
        _list_page(catalogue, [record], page=2, total=1, page_size=2),
        None,
    )
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert "immediate post-last page is not empty" in meta[
        "configured_collection_error"
    ]


def test_page_one_recheck_change_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    catalogue = ys.DAEJEON_YUSEONG_CATALOGUE_BY_KEY["all"]
    records = complete_source["records_by_catalogue"]["all"][:2]
    changed = [dict(records[0], title="변경된 강좌"), records[1]]
    complete_source["mapping"][ys.daejeon_yuseong_list_url("all", 1)] = (
        _list_page(catalogue, changed, page=1, total=8, page_size=2),
        None,
    )
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert "page-one recheck changed" in meta["configured_collection_error"]


def test_aggregate_alias_relation_change_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    catalogue = ys.DAEJEON_YUSEONG_CATALOGUE_BY_KEY["regular"]
    wrong = [
        complete_source["leaf_records"]["guam"][0],
        complete_source["leaf_records"]["special"][0],
    ]
    page = _list_page(catalogue, wrong, page=1, total=2, page_size=2)
    complete_source["mapping"][catalogue.list_url] = (page, None)
    complete_source["mapping"][ys.daejeon_yuseong_list_url("regular", 1)] = (
        page,
        None,
    )
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert "REGULAR alias is not exactly Gu-am plus Jeonmin" in meta[
        "configured_collection_error"
    ]


def test_current_detail_mismatch_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    record = complete_source["leaf_records"]["guam"][0]
    url = ys.daejeon_yuseong_detail_url(record["identity"])
    complete_source["mapping"][url] = (_detail_page(record, bad_title=True), None)
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is False
    assert "detail title mismatch" in meta["configured_collection_error"]


def test_open_status_without_course_bound_application_control_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    record = complete_source["leaf_records"]["linku"][0]
    url = ys.daejeon_yuseong_detail_url(record["identity"])
    complete_source["mapping"][url] = (
        _detail_page(record, application_control=False),
        None,
    )
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert "source status/application control mismatch" in meta[
        "configured_collection_error"
    ]


def test_duplicate_identity_across_leaf_menus_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    catalogue = ys.DAEJEON_YUSEONG_CATALOGUE_BY_KEY["special"]
    duplicate = dict(
        complete_source["leaf_records"]["special"][0],
        identity="106",
    )
    page = _list_page(catalogue, [duplicate], page=1, total=1, page_size=2)
    complete_source["mapping"][catalogue.list_url] = (page, None)
    complete_source["mapping"][ys.daejeon_yuseong_list_url("special", 1)] = (
        page,
        None,
    )
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert "official leaf menus contain duplicate identities" in meta[
        "configured_collection_error"
    ]


def test_noncanonical_target_never_fetches(complete_source: dict[str, Any]) -> None:
    rows, parser, meta = ys.collect_daejeon_yuseong_education(
        Target(url=ys.DAEJEON_YUSEONG_LANDING_URL),
        fetcher=complete_source["fetcher"],
        session_factory=DummySession,
    )
    assert rows == []
    assert parser == ys.DAEJEON_YUSEONG_PARSER
    assert complete_source["calls"] == []
    assert meta["configured_collection_error"] == "non-canonical Yuseong owner target"
