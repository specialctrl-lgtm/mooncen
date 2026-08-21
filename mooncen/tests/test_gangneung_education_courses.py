from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalIntegratedReservation as aggregate
from Crawler import Crawler_MunicipalYaml as municipal


LIFELONG_PROVIDER = municipal.GANGNEUNG_LIFELONG_PROVIDER
UNITY_PROVIDER = municipal.GANGNEUNG_UNITY_EDUCATION_PROVIDER
ROOT = Path(__file__).resolve().parents[1]


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _lifelong_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=LIFELONG_PROVIDER,
        name="강릉시 평생학습관 강좌",
        branch="강릉시 평생학습관",
        url="https://www.gn.go.kr/gnlll/selectLctreSearch.do?key=2254",
        source="test",
        priority=1,
        region="강원특별자치도 강릉시",
        extra={"crawl_year": "2099"},
    )


def _unity_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=UNITY_PROVIDER,
        name="강릉시 공공서비스 통합예약 교육",
        branch="강릉시 공공서비스 통합예약",
        url=(
            "https://www.gn.go.kr/yeyak/selectUnityProgrmWebList.do?"
            "key=5411&insttTy=URINTY01"
        ),
        source="test",
        priority=1,
        region="강원특별자치도 강릉시",
    )


def _lifelong_card(
    lecture_no: int,
    status: str,
    *,
    period: str,
    title: str | None = None,
) -> str:
    title = title or f"평생학습 강좌 {lecture_no}"
    application = ""
    if status == "접수진행":
        application = (
            f'<a href="/gnlll/insertReqstLctreWebView.do?key=2254&amp;lctreNo={lecture_no}">'
            "신청하기</a>"
        )
    return f"""
    <div class="lecture_item">
      <span class="category">시민교육</span>
      <strong class="lecture_title">{title}</strong>
      <div class="status">{status}{application}</div>
      <div class="lecture_info">
        <div class="info_item"><span class="title">기관</span><span class="text">강릉시 평생학습관</span></div>
        <div class="info_item"><span class="title">교육</span><span class="date">{period} 월 10:00~12:00</span></div>
        <div class="info_item"><span class="title">접수</span><span class="date">2099-07-01 ~ 2099-07-31</span></div>
        <div class="info_item"><span class="title">모집인원</span><span class="text">40명</span></div>
      </div>
      <a class="more" href="/gnlll/selectLctreWebView.do?key=2254&amp;lctreNo={lecture_no}">상세</a>
    </div>
    """


def _lifelong_list_page(cards: str, page: int, total_pages: int = 17) -> str:
    return f"""
    <html><body>
      <div class="lecture_container">{cards}</div>
      <div class="pagination">
        <a href="/gnlll/selectLctreSearch.do?key=2254&amp;searchYyyy=2099&amp;pageIndex={page}">{page}</a>
        <a href="/gnlll/selectLctreSearch.do?key=2254&amp;searchYyyy=2099&amp;pageIndex={total_pages}">마지막</a>
      </div>
    </body></html>
    """


def _lifelong_detail(lecture_no: str) -> str:
    return f"""
    <html><body><table><tbody>
      <tr><th>과목명</th><td>평생학습 상세 {lecture_no}</td></tr>
      <tr><th>접수기간</th><td>2099년 7월 1일 (09시 00분) ~ 2099년 7월 31일 (18시 00분)</td></tr>
      <tr><th>교육기간</th><td>2099년 8월 1일 ~ 2099년 8월 31일</td></tr>
      <tr><th>요일</th><td>월요일</td></tr>
      <tr><th>시간</th><td>10:00 ~ 12:00</td></tr>
      <tr><th>접수방법</th><td>온라인</td></tr>
      <tr><th>신청방법</th><td>선착순</td></tr>
      <tr><th>운영방법</th><td>대면</td></tr>
      <tr><th>모집정원</th><td>1 / 40</td></tr>
      <tr><th>대기인원</th><td>0 / 10</td></tr>
      <tr><th>수강료</th><td>무료</td></tr>
      <tr><th>분류</th><td>인문</td></tr>
      <tr><th>분야</th><td>시민교육</td></tr>
      <tr><th>대상</th><td>강릉시민</td></tr>
      <tr><th>장소</th><td>배움실 {lecture_no}</td></tr>
      <tr><th>강사명</th><td>강사 {lecture_no}</td></tr>
      <tr><th>강좌안내</th><td>공식 상세 설명</td></tr>
    </tbody></table></body></html>
    """


def _lifelong_fetcher(pages: dict[int, str], fetched: list[str]) -> Callable[[object, str, int], BeautifulSoup]:
    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.GANGNEUNG_LIFELONG_LIST_PATH:
            assert query["searchYyyy"] == ["2099"]
            assert query["pageUnit"] == ["12"]
            return _soup(pages[int(query["pageIndex"][0])])
        assert parsed.path in municipal.GANGNEUNG_LIFELONG_DETAIL_PATHS
        return _soup(_lifelong_detail(query["lctreNo"][0]))

    return fetch


def test_gangneung_lifelong_current_year_full_snapshot(monkeypatch) -> None:
    cards: list[str] = []
    for index in range(1, 198):
        if index == 1:
            status = "접수진행"
            period = "2099-08-01 ~ 2099-08-31"
        elif index <= 41:
            status = "접수마감"
            period = "2099-08-01 ~ 2099-08-31"
        elif index <= 54:
            status = "폐강"
            period = "2099-08-01 ~ 2099-08-31"
        elif index == 55:
            status = "폐강대기"
            period = "2099-08-01 ~ 2099-08-31"
        else:
            status = "접수마감"
            period = "2020-01-01 ~ 2020-01-31"
        duplicate_title = "중복 제목·기간 폐강 강좌" if index in {42, 43} else None
        cards.append(_lifelong_card(4700 + index, status, period=period, title=duplicate_title))
    pages = {
        page: _lifelong_list_page("".join(cards[(page - 1) * 12 : page * 12]), page)
        for page in range(1, 18)
    }
    fetched: list[str] = []
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", _lifelong_fetcher(pages, fetched))

    rows, parser, meta = municipal.collect_from_url(
        _lifelong_target(), timeout=7, max_depth=0, max_pages=20, detail_limit=200
    )

    assert parser == "gangneung_lifelong_current_year+detail"
    assert len(rows) == 41
    assert meta["pages"] == meta["declared_pages"] == 17
    assert meta["discovered_links"] == 197
    assert meta["excluded_cancelled_count"] == 14
    assert meta["expired_count"] == 142
    assert meta["detail_attempts"] == meta["detail_pages"] == 41
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta
    assert len({row["provider_course_id"] for row in rows}) == 41
    assert all(row["provider_course_id"].startswith(f"{LIFELONG_PROVIDER}:lecture:") for row in rows)
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all(parse_qs(urlparse(row["raw_url"]).query).get("listType") == ["list"] for row in rows)
    assert all(row["branch"] == "강릉시 평생학습관" for row in rows)
    assert all(row["venue_name"].startswith("배움실 ") for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["reservation_available"] is True
    assert urlparse(open_row["application_url"]).path == municipal.GANGNEUNG_LIFELONG_APPLY_PATH
    assert parse_qs(urlparse(open_row["application_url"]).query)["lctreNo"] == ["4701"]
    closed_rows = [row for row in rows if row["status"] == "CLOSED"]
    assert all("application_url" not in row for row in closed_rows)
    assert all(row["raw_fields"]["clear_application_url"] is True for row in closed_rows)
    assert len([url for url in fetched if urlparse(url).path == municipal.GANGNEUNG_LIFELONG_LIST_PATH]) == 17


UNITY_COUNTS = {
    "URORST01": 2,
    "URORST02": 5,
    "URORST03": 3,
    "URORST04": 0,
    "URORST05": 19,
    "URORST06": 30,
}


def _unity_card(program_no: int, status: str) -> str:
    return f"""
    <li class="edu_item">
      <a href="/yeyak/unityProgrmWebView.do?key=5411&amp;progrmNo={program_no}">
        <strong class="edu_title">통합예약 교육 {program_no}</strong>
      </a>
      <span class="edu_type">교육</span><span class="edu_price">무료</span>
      <ul class="edu_content">
        <li><span class="edu_dt">대상</span><span class="edu_dd">강릉시민</span></li>
        <li><span class="edu_dt">장소</span><span class="edu_dd">목록 강의실</span></li>
        <li><span class="edu_dt">접수</span><span class="edu_dd">2099.07.01 ~ 2099.07.31</span></li>
        <li><span class="edu_dt">운영</span><span class="edu_dd">2099.08.01 ~ 2099.08.31</span></li>
      </ul>
      <div class="edu_btn_wrap"><span class="edu_btn">{status}</span><span class="edu_btn">상세보기</span></div>
    </li>
    """


def _unity_list_page(cards: str, state_code: str, page: int, total_pages: int) -> str:
    return f"""
    <html><body>
      <ul class="edu_list">{cards}</ul>
      <div class="pagination">
        <a href="/yeyak/selectUnityProgrmWebList.do?key=5411&amp;insttTy=URINTY01&amp;searchOperSttus={state_code}&amp;pageIndex={page}">{page}</a>
        <a href="/yeyak/selectUnityProgrmWebList.do?key=5411&amp;insttTy=URINTY01&amp;searchOperSttus={state_code}&amp;pageIndex={total_pages}">마지막</a>
      </div>
    </body></html>
    """


def _unity_detail(program_no: str, has_apply: bool) -> str:
    apply = '<a href="#" onclick="fn_reqChk(); return false;">신청하기</a>' if has_apply else ""
    return f"""
    <html><body>
      <ul class="info_list">
        <li><span class="info_subject">운영기관</span><span class="info_text">운영기관 {program_no}</span></li>
        <li><span class="info_subject">대상</span><span class="info_text">강릉시민</span></li>
        <li><span class="info_subject">신청(연령)제한</span><span class="info_text">초등학생 이상</span></li>
        <li><span class="info_subject">장소</span><span class="info_text">강의실 {program_no}</span></li>
        <li><span class="info_subject">접수기간</span><span class="info_text">2099.07.01 09:00 ~ 2099.07.31 18:00</span></li>
        <li><span class="info_subject">운영기간</span><span class="info_text">2099.08.01 ~ 2099.08.31</span></li>
        <li><span class="info_subject">교육시간</span><span class="info_text">10:00 ~ 12:00</span></li>
        <li><span class="info_subject">요일</span><span class="info_text">토요일</span></li>
        <li><span class="info_subject">모집인원</span><span class="info_text">20명</span></li>
        <li><span class="info_subject">대기모집인원</span><span class="info_text">5명</span></li>
        <li><span class="info_subject">이용요금</span><span class="info_text">무료</span></li>
        <li><span class="info_subject">선별방법</span><span class="info_text">선착순</span></li>
        <li><span class="info_subject">신청방법</span><span class="info_text">온라인</span></li>
        <li><span class="info_subject">문의전화</span><span class="info_text">033-660-0000</span></li>
      </ul>
      <div class="tab_content active"><pre>공식 프로그램 상세 설명</pre></div>
      <div class="map_info"><ul>
        <li><span class="dt">주소</span><span class="dd">강원특별자치도 강릉시 임영로 1</span></li>
        <li><span class="dt">전화번호</span><span class="dd">033-660-1234</span></li>
      </ul></div>
      {apply}
    </body></html>
    """


def _unity_fixture_pages() -> tuple[dict[tuple[str, int], str], dict[str, str]]:
    pages: dict[tuple[str, int], str] = {}
    state_by_program: dict[str, str] = {}
    next_program = 1200
    status_labels = dict(municipal.GANGNEUNG_UNITY_EDUCATION_STATES)
    for state_code, count in UNITY_COUNTS.items():
        ids = list(range(next_program, next_program + count))
        next_program += count
        for value in ids:
            state_by_program[str(value)] = state_code
        total_pages = max(1, (count + 7) // 8)
        for page in range(1, total_pages + 1):
            cards = "".join(
                _unity_card(value, status_labels[state_code])
                for value in ids[(page - 1) * 8 : page * 8]
            )
            pages[(state_code, page)] = _unity_list_page(cards, state_code, page, total_pages)
    return pages, state_by_program


def _unity_fetcher(
    pages: dict[tuple[str, int], str],
    state_by_program: dict[str, str],
    fetched: list[str],
) -> Callable[[object, str, int], BeautifulSoup]:
    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.GANGNEUNG_UNITY_EDUCATION_LIST_PATH:
            assert query["insttTy"] == ["URINTY01"]
            assert query["pageUnit"] == ["8"]
            return _soup(pages[(query["searchOperSttus"][0], int(query["pageIndex"][0]))])
        assert parsed.path == municipal.GANGNEUNG_UNITY_EDUCATION_DETAIL_PATH
        program_no = query["progrmNo"][0]
        return _soup(
            _unity_detail(
                program_no,
                state_by_program[program_no] in {"URORST02", "URORST03", "URORST04"},
            )
        )

    return fetch


def test_gangneung_unity_six_nonended_states_full_snapshot(monkeypatch) -> None:
    pages, state_by_program = _unity_fixture_pages()
    fetched: list[str] = []
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", _unity_fetcher(pages, state_by_program, fetched))

    rows, parser, meta = municipal.collect_from_url(
        _unity_target(), timeout=7, max_depth=0, max_pages=20, detail_limit=200
    )

    assert parser == "gangneung_unity_education_state_filters+detail"
    assert len(rows) == 59
    assert meta["discovered_links"] == 59
    assert meta["pages"] == 11
    assert meta["status_pages"] == {
        "URORST01": 1,
        "URORST02": 1,
        "URORST03": 1,
        "URORST04": 1,
        "URORST05": 3,
        "URORST06": 4,
    }
    assert meta["detail_attempts"] == meta["detail_pages"] == 59
    assert meta["reservation_discovery_links"] == 8
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta
    assert Counter(row["status"] for row in rows) == {
        "SCHEDULED": 2,
        "OPEN": 5,
        "WAITING": 3,
        "CLOSED": 49,
    }
    assert len({row["provider_course_id"] for row in rows}) == 59
    for row in rows:
        program_no = row["raw_fields"]["program_no"]
        assert row["provider_course_id"] == f"{UNITY_PROVIDER}:program:{program_no}"
        assert row["prefer_incoming_provider_course_id"] is True
        assert row["branch"] == f"운영기관 {program_no}"
        assert row["venue_name"] == f"강의실 {program_no}"
        assert row["venue_address"] == "강원특별자치도 강릉시 임영로 1"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        if row["status"] in {"OPEN", "WAITING"}:
            assert row["reservation_available"] is True
            assert urlparse(row["application_url"]).path == municipal.GANGNEUNG_UNITY_EDUCATION_APPLY_PATH
        else:
            assert row["reservation_available"] is False
            assert "application_url" not in row
            assert row["raw_fields"]["clear_application_url"] is True


def test_gangneung_full_snapshot_caps_are_reported(monkeypatch) -> None:
    lifelong_cards = [
        _lifelong_card(4701, "접수진행", period="2099-08-01 ~ 2099-08-31")
    ]
    lifelong_pages = {
        1: _lifelong_list_page("".join(lifelong_cards), 1, total_pages=1),
    }
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", _lifelong_fetcher(lifelong_pages, []))
    rows, _parser, meta = municipal.collect_gangneung_lifelong_lectures(
        _lifelong_target(), timeout=5, max_pages=20, detail_limit=0
    )
    assert len(rows) == 1
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    unity_pages, state_by_program = _unity_fixture_pages()
    monkeypatch.setattr(municipal, "fetch_soup", _unity_fetcher(unity_pages, state_by_program, []))
    rows, _parser, meta = municipal.collect_gangneung_unity_education(
        _unity_target(), timeout=5, max_pages=2, detail_limit=200
    )
    assert rows
    assert meta["pagination_complete"] is False
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]


def test_gangneung_program_route_dispatch_does_not_replace_experience(monkeypatch) -> None:
    education = ([{"title": "education"}], "education", {"pages": 1})
    experience = ([{"title": "experience"}], "experience", {"pages": 1})
    monkeypatch.setattr(municipal, "collect_gangneung_unity_education", lambda *_args, **_kwargs: education)
    monkeypatch.setattr(municipal, "collect_gangneung_unity_reservation", lambda *_args, **_kwargs: experience)
    assert municipal.collect_from_url(
        _unity_target(), timeout=5, max_depth=0, max_pages=20, detail_limit=200
    ) == education
    experience_target = municipal.CrawlTarget(
        provider="MUNI_WWW_GN_GO_KR_5623F7DB",
        name="강릉 통합예약 체험",
        branch="강릉시",
        url=(
            "https://www.gn.go.kr/yeyak/selectUnityExprnWebList.do?"
            "key=5412&insttTy=URINTY01"
        ),
        source="test",
    )
    assert municipal.collect_from_url(
        experience_target, timeout=5, max_depth=0, max_pages=20, detail_limit=200
    ) == experience


def _yaml_target(path: Path, provider: str) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return next(row for row in document.get("targets", []) if row.get("provider") == provider)


def test_gangneung_configs_and_generated_registry_use_full_snapshot_contract() -> None:
    lifelong = _yaml_target(
        ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml", LIFELONG_PROVIDER
    )
    unity = _yaml_target(
        ROOT / "config" / "crawl_targets" / "public_reservation.yaml", UNITY_PROVIDER
    )
    assert lifelong["branch"] == "강릉시 평생학습관"
    assert lifelong["collection_type"] == "current_year+detail_html"
    assert unity["branch"] == "강릉시 공공서비스 통합예약"
    assert unity["collection_type"] == "state_filters+detail_html"
    for target in (lifelong, unity):
        assert target["domain_category"] == "교육·강좌"
        assert target["service_group"] == "공공강좌"
        assert target["service_group_policy"] == "locked"

    expected_arguments = (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "200",
    )
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[LIFELONG_PROVIDER] == expected_arguments
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[UNITY_PROVIDER] == expected_arguments

    registry = yaml.safe_load(
        (ROOT / "config" / "generated_yaml_crawler_registry.yaml").read_text(encoding="utf-8")
    )
    rows = {row["provider"]: row for row in registry["targets"]}
    aggregate_providers = set(aggregate.municipal_provider_names())
    for provider in (LIFELONG_PROVIDER, UNITY_PROVIDER):
        assert provider not in rows
        assert provider in aggregate_providers
        assert not (ROOT / "Crawler" / "generated_yaml" / f"{provider}.py").exists()
