from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import municipal_incheon_reservation as incheon


def _target(catalogue: incheon.IncheonCatalogue) -> dict[str, str]:
    return {
        "provider": incheon.INCHEON_RESERVATION_PROVIDER,
        "url": catalogue.canonical_url,
    }


@pytest.mark.parametrize("catalogue", incheon.INCHEON_CATALOGUES)
def test_exact_sibling_target_matcher(catalogue: incheon.IncheonCatalogue) -> None:
    assert incheon.is_incheon_reservation_target(_target(catalogue))
    assert not incheon.is_incheon_reservation_target(
        {**_target(catalogue), "url": catalogue.canonical_url + "#fragment"}
    )
    assert not incheon.is_incheon_reservation_target(
        {**_target(catalogue), "url": catalogue.canonical_url.replace("https://", "http://")}
    )
    assert not incheon.is_incheon_reservation_target(
        {**_target(catalogue), "url": catalogue.canonical_url + "&curPage=1"}
    )
    assert not incheon.is_incheon_reservation_target(
        {
            "provider": incheon.INCHEON_RESERVATION_PROVIDER,
            "url": "https://www.incheon.go.kr/res/",
        }
    )


@pytest.mark.parametrize("catalogue", incheon.INCHEON_CATALOGUES)
def test_production_collection_requires_managed_session(
    catalogue: incheon.IncheonCatalogue,
) -> None:
    rows, parser, meta = incheon.collect(
        _target(catalogue), today="2026-08-05"
    )
    assert rows == []
    assert parser == catalogue.parser
    assert meta["snapshot_complete"] is False
    assert meta["application_endpoints_called"] == 0
    assert "session_factory" in meta["configured_collection_error"]


def _sort_controls(catalogue: incheon.IncheonCatalogue, selected: str = "") -> str:
    options = (
        incheon._EDUCATION_SORT_OPTIONS
        if catalogue is incheon.INCHEON_EDUCATION
        else incheon._EXPERIENCE_SORT_OPTIONS
    )
    return "".join(
        f'<input type="radio" name="sortType" id="sortType{value or "All"}" '
        f'value="{value}" {"checked" if value == selected else ""}>'
        f'<label for="sortType{value or "All"}"><span>{label}</span></label>'
        for value, label in options
    )


def _education_card(*, notice: bool = False) -> str:
    identity = "999" if notice else "101"
    title = "공지사항 시스템 점검" if notice else "안전 교육"
    badges = "" if notice else '<i class="accept ing">접수중</i><i class="accept free">무료</i>'
    return f"""
      <li>
        <a href="/res/RE010101/lctreEdcView?resveGroupSn=21&amp;resveProgrmSeCode=L&amp;progrmSn={identity}&amp;curPage=1&amp;resveInsttCode=">
          <div class="search-reservation-wrap">
            <span class="institution">교육청</span>
            <strong class="reservation-name">{title}{badges}</strong>
            <div class="item-data-wrap">
              <dl><dt>기관</dt><dd>교육청</dd></dl>
              <dl><dt>대상</dt><dd>어린이</dd></dl>
              <dl><dt>장소</dt><dd><div class="item-data-group">미추홀구 교육관</div><div class="item-data-group">수 / 10:00 ~ 11:00</div></dd></dl>
              <dl><dt>일자</dt><dd>
                <div class="item-data-group"><span class="item">신청</span>2026-08-01 ~ 2026-08-20</div>
                <div class="item-data-group"><span class="item">수강</span>2026-08-26 ~ 2026-08-26</div>
              </dd></dl>
              <dl><dt>문의</dt><dd>032-000-0000</dd></dl>
            </div>
          </div>
        </a>
      </li>
    """


def _experience_card() -> str:
    return """
      <li>
        <a href="/res/RE030101/lnbnsExprnView?resveGroupSn=201&amp;resveProgrmSeCode=E&amp;progrmSn=301&amp;curPage=1">
          <div class="search-reservation-wrap">
            <span class="institution">검단소방서</span>
            <strong class="reservation-name">소방안전체험<i class="accept ing">접수중</i><i class="accept free">무료</i></strong>
            <div class="item-data-wrap">
              <dl><dt>기관</dt><dd>검단소방서</dd></dl>
              <dl><dt>장소</dt><dd>검단소방서 체험관</dd></dl>
              <dl><dt>일자</dt><dd>
                <div class="item-data-group"><span class="item">신청</span>2026-08-01 ~ 2026-09-30</div>
                <div class="item-data-group"><span class="item">운영</span>2026-08-01 ~ 2026-09-30</div>
              </dd></dl>
              <dl><dt>문의</dt><dd>032-000-0000</dd></dl>
            </div>
          </div>
        </a>
      </li>
    """


def _list_page(
    catalogue: incheon.IncheonCatalogue,
    rows: str,
    *,
    selected: str = "",
    active: bool = True,
) -> str:
    contents = rows or '<li><div class="board-nodata"><p>등록된 게시물이 없습니다.</p></div></li>'
    active_page = '<a class="active">1</a>' if active and rows else ""
    return f"""
      <html><head><title>{catalogue.name} 온라인통합예약</title></head><body>
        <div id="onlineSection">
          <form id="searchFrm" method="get" action="{catalogue.list_path}">
            <input type="hidden" name="useAt" value="">
            <input type="hidden" name="resveProgrmSeCode" value="">
            <input type="hidden" name="resveGroupSn" value="">
            {_sort_controls(catalogue, selected)}
          </form>
          <div class="search-list-wrap"><ul class="img-wrap-1117">{contents}</ul></div>
          <div class="pagination">{active_page}<span class="num-page-total"><em>1</em></span></div>
        </div>
      </body></html>
    """


def _education_detail(address: str = "인천 미추홀구 교육로 1") -> str:
    return f"""
      <html><body><div class="content-body"><form id="frm">
        <section class="cont-view-board">
          <div class="cont-view-board-title"><h4>안전 교육<i class="accept ing">접수중</i></h4></div>
          <div class="cont-view-board-detail">
            <div class="detail-img-area"><span class="institution">교육청</span></div>
            <div class="detail-txt-area"><ul>
              <li><dl><dt>신청기간</dt><dd>2026-08-01 ~ 2026-08-20</dd></dl></li>
              <li><dl><dt>신청인원</dt><dd>신청 정원 20명 / 예약 3명</dd></dl></li>
              <li><dl><dt>교육기간</dt><dd>2026-08-26 ~ 2026-08-26</dd></dl></li>
              <li><dl><dt>요일/시간</dt><dd>수 / 10:00 ~ 11:00</dd></dl></li>
              <li><dl><dt>수강료</dt><dd>무료</dd></dl></li>
              <li><dl><dt>수강신청방법</dt><dd>온라인</dd></dl></li>
              <li><dl><dt>수강대상</dt><dd>어린이</dd></dl></li>
              <li><dl><dt>교육장소/수강정원</dt><dd>미추홀구 교육관 / 20 명</dd></dl></li>
              <li><dl><dt>문의전화</dt><dd>032-000-0000</dd></dl></li>
            </ul></div>
          </div>
          <div class="board-btn-wrap"><button id="btn_appl">예약하기</button></div>
        </section>
        <h5 class="h6">주소(위치정보)</h5><ul><li>{address}</li></ul>
        <h5 class="h6">찾아오시는길</h5><ul><li>미추홀구 교육관</li></ul>
      </form></div></body></html>
    """


def _experience_detail(address: str = "인천 검단구 원당대로 736") -> str:
    return f"""
      <html><body><div class="content-body"><form id="frm">
        <section class="cont-view-board">
          <div class="cont-view-board-title"><h4>소방안전체험<i class="accept ing">접수중</i></h4></div>
          <div class="cont-view-board-detail">
            <div class="detail-img-area"><span class="institution">검단소방서</span></div>
            <div class="detail-txt-area"><ul>
              <li><dl><dt>운영기간</dt><dd>2026-08-01 ~ 2026-09-30</dd></dl></li>
              <li><dl><dt>신청기간</dt><dd>2026-08-01 ~ 2026-09-30</dd></dl></li>
              <li><dl><dt>신청방법</dt><dd>온라인</dd></dl></li>
              <li><dl><dt>신청가능인원</dt><dd>최소 10명, 최대 20명</dd></dl></li>
              <li><dl><dt>신청가능요일</dt><dd>화, 목</dd></dl></li>
              <li><dl><dt>문의전화</dt><dd>032-000-0000</dd></dl></li>
              <li><dl><dt>대상</dt><dd>제한없음</dd></dl></li>
            </ul></div>
          </div>
          <div class="board-btn-wrap"><a id="btn_appl" href="#;">예약하기</a></div>
        </section>
        <h5 class="h6">주소(위치정보)</h5><ul><li>{address}</li></ul>
        <h5 class="h6">찾아오시는길</h5><ul><li>검단소방서 체험관</li></ul>
      </form></div></body></html>
    """


class _Response:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.status_code = 200
        self.history: list[Any] = []


class _FixtureSession:
    def __init__(self, *, mismatched_experience_address: bool = False) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        self.mismatched_experience_address = mismatched_experience_address

    def get(self, url: str, **_: Any) -> _Response:
        self.calls.append(("GET", url))
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        page = int((query.get("curPage") or ["1"])[0])
        selected = (query.get("sortType") or [""])[0]
        if parsed.path == incheon.INCHEON_EDUCATION.list_path:
            rows = _education_card() + _education_card(notice=True) if page == 1 else ""
            return _Response(
                url,
                _list_page(
                    incheon.INCHEON_EDUCATION,
                    rows,
                    active=page == 1,
                ),
            )
        if parsed.path == incheon.INCHEON_EXPERIENCE.list_path:
            rows = _experience_card() if page == 1 and selected in {"", "14"} else ""
            return _Response(
                url,
                _list_page(
                    incheon.INCHEON_EXPERIENCE,
                    rows,
                    selected=selected,
                    active=page == 1,
                ),
            )
        if parsed.path == incheon.INCHEON_EDUCATION.detail_path:
            return _Response(url, _education_detail())
        if parsed.path == incheon.INCHEON_EXPERIENCE.detail_path:
            address = (
                "인천 남동구 정각로 29"
                if self.mismatched_experience_address
                else "인천 검단구 원당대로 736"
            )
            return _Response(url, _experience_detail(address))
        raise AssertionError(f"unexpected GET endpoint: {url}")

    def post(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("POST must never be used")

    def close(self) -> None:
        self.closed = True


def test_complete_education_snapshot_excludes_notice_and_maps_detail_address() -> None:
    session = _FixtureSession()
    rows, parser, meta = incheon.collect(
        _target(incheon.INCHEON_EDUCATION),
        today="2026-08-05",
        session_factory=lambda: session,
    )
    assert parser == incheon.INCHEON_EDUCATION_PARSER
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["source_total"] == 2
    assert meta["current_count"] == 1
    assert meta["notice_count"] == 1
    assert meta["detail_pages"] == 1
    assert len(rows) == 1
    row = rows[0]
    assert row["service_group"] == "공공강좌"
    assert row["service_group_policy"] == "locked"
    assert row["program_type"] == "교육"
    assert row["municipality_code"] == "2817700000"
    assert row["provider_course_id"].startswith("INCHEON_RESERVATION:education:")
    assert row["application_url"] == row["raw_url"]
    assert session.closed is True
    assert {method for method, _ in session.calls} == {"GET"}
    assert all(
        urlparse(url).path
        in {incheon.INCHEON_EDUCATION.list_path, incheon.INCHEON_EDUCATION.detail_path}
        for _, url in session.calls
    )


def test_complete_experience_snapshot_reconciles_all_eleven_district_filters() -> None:
    session = _FixtureSession()
    rows, parser, meta = incheon.collect(
        _target(incheon.INCHEON_EXPERIENCE),
        today="2026-08-05",
        session_factory=lambda: session,
    )
    assert parser == incheon.INCHEON_EXPERIENCE_PARSER
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["district_reconciled"] is True
    assert meta["source_total"] == 1
    assert meta["district_source_totals"]["14"] == 1
    assert sum(meta["district_source_totals"].values()) == 1
    assert set(meta["row_municipality_codes"]) == set(
        incheon.INCHEON_ROW_MUNICIPALITY_CODES
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["service_group"] == "체험"
    assert row["service_group_policy"] == "locked"
    assert row["program_type"] == "체험"
    assert row["municipality_code"] == "2829000000"
    assert row["raw_fields"]["district_filter_code"] == "14"
    assert row["provider_course_id"].startswith("INCHEON_RESERVATION:experience:")
    called_paths = {urlparse(url).path for _, url in session.calls}
    assert called_paths == {
        incheon.INCHEON_EXPERIENCE.list_path,
        incheon.INCHEON_EXPERIENCE.detail_path,
    }
    called_filters = {
        (parse_qs(urlparse(url).query, keep_blank_values=True).get("sortType") or [""])[0]
        for _, url in session.calls
        if urlparse(url).path == incheon.INCHEON_EXPERIENCE.list_path
    }
    assert called_filters == {"", *incheon.INCHEON_DISTRICT_BY_SOURCE}


def test_experience_detail_address_is_physical_location_when_filter_differs() -> None:
    session = _FixtureSession(mismatched_experience_address=True)
    rows, _, meta = incheon.collect(
        _target(incheon.INCHEON_EXPERIENCE),
        today="2026-08-05",
        session_factory=lambda: session,
    )
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert rows[0]["municipality_code"] == "2820000000"
    assert rows[0]["raw_fields"]["district_filter_code"] == "14"
    assert rows[0]["raw_fields"]["district_filter_conflict"] is True


def test_public_url_allowlist_refuses_login_application_and_pii_before_get() -> None:
    session = _FixtureSession()
    requester = incheon._Requester(lambda: session, 10, 200)
    try:
        for url in (
            "https://www.incheon.go.kr/unityMberLoginCnter",
            "https://www.incheon.go.kr/res/RE080101",
            "https://www.incheon.go.kr/res/RE070101",
            "https://www.incheon.go.kr/res/apply?progrmSn=301",
        ):
            with pytest.raises(incheon.IncheonReservationContractError, match="endpoint"):
                requester.soup(url, kind="detail")
    finally:
        requester.close()
    assert session.calls == []


@pytest.mark.parametrize("catalogue", incheon.INCHEON_CATALOGUES)
def test_dispatch_injects_managed_session_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
    catalogue: incheon.IncheonCatalogue,
) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any) -> tuple[list[Any], str, dict[str, Any]]:
        captured.update(kwargs)
        return [], "incheon", {"snapshot_complete": True}

    monkeypatch.setattr(incheon, "collect_incheon_reservations", collect)
    target = municipal.CrawlTarget(
        provider=incheon.INCHEON_RESERVATION_PROVIDER,
        name=catalogue.name,
        branch="인천광역시 온라인통합예약",
        url=catalogue.canonical_url,
        source="test",
    )
    municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=200,
        detail_limit=200,
    )
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "allow_raw_requests_for_tests" not in captured


def test_public_target_config_deprecates_root_and_registers_two_locked_siblings() -> None:
    path = Path("config/crawl_targets/public_reservation.yaml")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    targets = [
        row
        for row in document["targets"]
        if row.get("provider") == incheon.INCHEON_RESERVATION_PROVIDER
    ]
    assert len(targets) == 3
    by_url = {row["url"]: row for row in targets}
    root = by_url["https://www.incheon.go.kr/res/"]
    assert root["crawler_status"] == "deprecated"
    assert root["collection_type"] == "excluded_generic_root_shell"
    expected = {
        incheon.INCHEON_EDUCATION_URL: ("education", "공공강좌", "교육"),
        incheon.INCHEON_EXPERIENCE_URL: ("experience", "체험", "체험"),
    }
    for url, (scope, group, program_type) in expected.items():
        row = by_url[url]
        assert row["crawler_status"] == "ready"
        assert row["ops_scopes"] == [scope]
        assert row["service_group"] == group
        assert row["service_group_policy"] == "locked"
        assert row["program_type"] == program_type
        assert row["full_snapshot_required"] is True
        assert set(row["row_municipality_codes"]) == set(
            incheon.INCHEON_ROW_MUNICIPALITY_CODES
        )


def test_generated_provider_uses_full_snapshot_arguments() -> None:
    from Crawler import Crawler_GeneratedYamlTargets as generated

    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        incheon.INCHEON_RESERVATION_PROVIDER
    ] == (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "200",
        "--detail-limit",
        "200",
    )
