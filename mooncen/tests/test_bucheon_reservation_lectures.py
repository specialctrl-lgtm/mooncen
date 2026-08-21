from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal


PROVIDER = municipal.BUCHEON_LECTURE_PROVIDER
TARGET_URL = municipal.BUCHEON_LECTURE_LIST_URL


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target(*, provider: str = PROVIDER, url: str = TARGET_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="부천시 공공서비스예약",
        branch="경기도 부천시",
        url=url,
        source="test",
        priority=1,
        region="경기도 부천시",
        extra={
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
        },
    )


def _card(
    lecture_seq: str,
    status: str,
    *,
    title: str | None = None,
    period: str = "2099-08-01 ~ 2099-08-31",
    branch: str = "상3동",
) -> str:
    title = title or f"공식 강좌 {lecture_seq}"
    return f"""
    <li>
      <a href="/site/main/lecture/lectureInfoForm?lec_seq={lecture_seq}&amp;cp=1&amp;pageSize=16&amp;listType=list">
        <span class="area orange">{status}</span>
        <em class="dong">{branch}</em>
        <span class="tit">{title}</span>
      </a>
      <div class="white-bg"><span class="lf">[목록 강의실]</span><p><strong>무료</strong></p></div>
      <div class="apl-time">교육기간 : {period}</div>
    </li>
    """


def _list_page(cards: str, *, current: int, total_count: int, explicit_empty: bool = False) -> str:
    total_pages = max(
        1,
        (total_count + municipal.BUCHEON_LECTURE_PAGE_SIZE - 1)
        // municipal.BUCHEON_LECTURE_PAGE_SIZE,
    )
    empty_text = '<p class="empty">등록된 강좌가 없습니다.</p>' if explicit_empty else ""
    return f"""
    <html><body>
      <form id="ctznLectureSch" action="/site/main/lecture/lectureList" method="post"></form>
      <p>총 {total_count:,}건</p>
      <ul class="img-list clearfix">{cards}</ul>
      {empty_text}
      <div class="page">
        <a href="?cp={current}&amp;pageSize=16&amp;listType=list" class="on">{current}</a>
        <a href="?cp={total_pages}&amp;pageSize=16&amp;listType=list" title="마지막 페이지">마지막</a>
      </div>
    </body></html>
    """


def _detail_page(
    lecture_seq: str,
    *,
    accept_online: bool,
    branch: str | None = None,
    venue: str = "시민학습실",
    address: str = "경기도 부천시 원미구 길주로 210 지도보기",
) -> str:
    branch = branch or f"교육기관 {lecture_seq}"
    accept = f"<button onclick=\"acceptOnline('{lecture_seq}')\">신청</button>" if accept_online else ""
    return f"""
    <html><body>
      <input id="lec_seq" name="lec_seq" type="hidden" value="{lecture_seq}" />
      <table><tbody>
        <tr><th>강좌분야</th><td>시민교육</td></tr>
        <tr><th>교육기간</th><td>2099.08.01 ~ 2099.08.31</td></tr>
        <tr><th>교육장소</th><td>{venue}</td></tr>
        <tr><th>위치안내</th><td>{address}</td></tr>
        <tr><th>교육대상</th><td>부천시민</td></tr>
        <tr><th>수강료</th><td>무료</td></tr>
        <tr><th>접수방법</th><td>온라인(1/10) 대기(0/2)</td></tr>
        <tr><th>접수기간</th><td>2099-07-01 09:00 ~ 2099-07-31 18:00</td></tr>
        <tr><th>접수현황</th><td>1(접수인원)/10(총인원)</td></tr>
        <tr><th>교육기관</th><td>{branch}</td></tr>
        <tr><th>연락처</th><td>032-000-0000</td></tr>
      </tbody></table>
      <script>function acceptOnline(no) {{ return no; }}</script>
      {accept}
    </body></html>
    """


def _fake_fetcher(
    pages: dict[int, str],
    details: Callable[[str], str],
    fetched: list[str] | None = None,
) -> Callable[[object, str, int], BeautifulSoup]:
    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        if fetched is not None:
            fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.BUCHEON_LECTURE_LIST_PATH:
            page = int(query["cp"][0])
            return _soup(pages[page])
        assert parsed.path == municipal.BUCHEON_LECTURE_DETAIL_PATH
        lecture_seq = query["lec_seq"][0]
        return _soup(details(lecture_seq))

    return fetch


def test_bucheon_full_13_page_contract_keeps_late_current_rows_and_gates_application(monkeypatch) -> None:
    statuses = ["접수중"] * 6 + ["접수예정"] * 46 + ["접수마감"] * 36
    cards: list[str] = []
    for index in range(1, 196):
        is_expired = index <= 107
        status = "접수마감" if is_expired else statuses[index - 108]
        period = "2020-01-01 ~ 2020-01-31" if is_expired else "2099-08-01 ~ 2099-08-31"
        cards.append(_card(str(15000 + index), status, period=period))
    pages = {
        page: _list_page(
            "".join(cards[(page - 1) * 16 : page * 16]),
            current=page,
            total_count=195,
        )
        for page in range(1, 14)
    }
    fetched: list[str] = []

    def detail(lecture_seq: str) -> str:
        return _detail_page(lecture_seq, accept_online=lecture_seq in {str(15108 + index) for index in range(6)})

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", _fake_fetcher(pages, detail, fetched))

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=20, detail_limit=200
    )

    assert parser == municipal.BUCHEON_LECTURE_PARSER
    assert len(rows) == 88
    assert meta["pages"] == 13
    assert meta["total_pages"] == 13
    assert meta["total_count"] == 195
    assert meta["discovered_links"] == 195
    assert meta["expired_count"] == 107
    assert meta["test_rows"] == 0
    assert meta["current_count"] == 88
    assert meta["status_counts"] == {"OPEN": 6, "SCHEDULED": 46, "CLOSED": 36}
    assert meta["detail_attempts"] == meta["detail_pages"] == 88
    assert meta["detail_errors"] == 0
    assert meta["pagination_complete"] is True
    assert meta["list_pagination_complete"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta
    assert len([url for url in fetched if urlparse(url).path == municipal.BUCHEON_LECTURE_LIST_PATH]) == 13
    assert len([url for url in fetched if urlparse(url).path == municipal.BUCHEON_LECTURE_DETAIL_PATH]) == 88

    by_status: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_status.setdefault(row["status"], []).append(row)
        lecture_seq = row["raw_fields"]["lecture_seq"]
        assert row["provider_course_id"] == f"{PROVIDER}:lecture:{lecture_seq}"
        assert row["raw_url"] == municipal.bucheon_lecture_detail_url(lecture_seq)
        assert row["branch"] == f"교육기관 {lecture_seq}"
        assert row["venue_name"] == "시민학습실"
        assert row["venue_address"] == "경기도 부천시 원미구 길주로 210"
        assert row["address"] == row["venue_address"]
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["municipality_code"] == "4119200000"
        assert row["municipality_full_name"] == "경기도 부천시 원미구"
    assert meta["current_municipality_counts"] == {"4119200000": 88}
    assert [item["code"] for item in meta["covered_municipalities"]] == [
        "4119000000",
        "4119200000",
        "4119400000",
        "4119600000",
    ]
    assert len(by_status["OPEN"]) == 6
    assert all(row["application_url"] == row["raw_url"] for row in by_status["OPEN"])
    assert all(row["reservation_available"] is True for row in by_status["OPEN"])
    for status in ("SCHEDULED", "CLOSED"):
        assert all("application_url" not in row for row in by_status[status])
        assert all(row["reservation_available"] is False for row in by_status[status])
        assert all(row["raw_fields"]["clear_application_url"] is True for row in by_status[status])


@pytest.mark.parametrize(
    "url",
    [
        "http://reserv.bucheon.go.kr/site/main/lecture/lectureList",
        "https://www.reserv.bucheon.go.kr/site/main/lecture/lectureList",
        "https://evil.reserv.bucheon.go.kr/site/main/lecture/lectureList",
        "https://reserv.bucheon.go.kr:443/site/main/lecture/lectureList",
        "https://reserv.bucheon.go.kr/site/main/lecture/lectureInfoForm",
        "https://reserv.bucheon.go.kr/site/main/lecture/lectureList?cp=0",
        "https://reserv.bucheon.go.kr/site/main/lecture/lectureList?pageSize=50",
        "https://reserv.bucheon.go.kr/site/main/lecture/lectureList#fragment",
    ],
)
def test_bucheon_route_and_owner_are_exact(url: str) -> None:
    assert municipal.is_bucheon_lecture_target(url) is False


def test_bucheon_waitlist_status_remains_open_for_public_waitlist_control() -> None:
    assert municipal.bucheon_lecture_status("대기접수") == "OPEN"


def test_bucheon_canonical_route_dispatch_and_stable_url_validation(monkeypatch) -> None:
    assert municipal.is_bucheon_lecture_target(TARGET_URL) is True
    assert municipal.is_bucheon_lecture_target(
        f"{TARGET_URL}?cp=2&pageSize=16&listType=list"
    ) is True
    assert municipal.bucheon_lecture_detail_url("15901") == (
        "https://reserv.bucheon.go.kr/site/main/lecture/lectureInfoForm?lec_seq=15901"
    )
    assert municipal.bucheon_lecture_detail_url("15901&next=https://evil.example") == ""
    with pytest.raises(ValueError, match="owned official HTTPS"):
        municipal.collect_bucheon_lecture_list(
            _target(provider="MUNI_UNOWNED"), timeout=5, max_pages=20, detail_limit=200
        )

    sentinel = ([{"title": "sentinel"}], municipal.BUCHEON_LECTURE_PARSER, {"pages": 1})
    monkeypatch.setattr(municipal, "collect_bucheon_lecture_list", lambda *_args, **_kwargs: sentinel)
    assert municipal.collect_from_url(
        _target(), timeout=5, max_depth=0, max_pages=20, detail_limit=200
    ) == sentinel


@pytest.mark.parametrize(
    ("dong", "code", "full_name"),
    [
        ("상3동", "4119200000", "경기도 부천시 원미구"),
        ("소사동", "4119200000", "경기도 부천시 원미구"),
        ("소사본동", "4119400000", "경기도 부천시 소사구"),
        ("고강본동", "4119600000", "경기도 부천시 오정구"),
    ],
)
def test_bucheon_restored_district_attribution_is_exact(
    dong: str,
    code: str,
    full_name: str,
) -> None:
    municipality = municipal.bucheon_municipality_for_dong(dong)
    assert municipality == {
        "code": code,
        "sido": "경기도",
        "sigungu": full_name.removeprefix("경기도 "),
        "full_name": full_name,
    }


def test_bucheon_unknown_dong_is_not_silently_attributed_to_the_city() -> None:
    assert municipal.bucheon_municipality_for_dong("알수없는동") is None
    assert municipal.bucheon_lecture_list_row(
        _target(),
        _soup(_card("15901", "접수중", branch="알수없는동")).select_one("li"),
        municipal.bucheon_lecture_list_url(1),
    ) is None


def test_bucheon_repeated_duplicate_and_malformed_pages_are_partial(monkeypatch) -> None:
    page_one = _list_page(_card("15001", "접수중"), current=1, total_count=17)
    page_two = _list_page(
        _card("15001", "접수중")
        + '<li><a href="/site/main/lecture/lectureInfoForm?lec_seq=bad"><span class="tit">깨진 강좌</span></a></li>',
        current=2,
        total_count=17,
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fake_fetcher(
            {1: page_one, 2: page_two},
            lambda lecture_seq: _detail_page(lecture_seq, accept_online=True),
        ),
    )

    rows, _parser, meta = municipal.collect_bucheon_lecture_list(
        _target(), timeout=5, max_pages=2, detail_limit=10
    )

    assert len(rows) == 1
    assert meta["invalid_count"] == 1
    assert meta["duplicate_count"] == 1
    assert meta["repeated_pages"] == 1
    assert meta["pagination_complete"] is False
    assert meta["no_current_data"] is False
    assert "malformed" in meta["configured_collection_error"]
    assert "duplicated" in meta["configured_collection_error"]
    assert "repeated" in meta["configured_collection_error"]
    assert "parsed 1 of 17" in meta["configured_collection_error"]


def test_bucheon_page_cap_and_changed_total_are_partial(monkeypatch) -> None:
    page_one = _list_page(_card("15001", "접수중"), current=1, total_count=33)
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fake_fetcher(
            {1: page_one},
            lambda lecture_seq: _detail_page(lecture_seq, accept_online=True),
        ),
    )

    rows, _parser, meta = municipal.collect_bucheon_lecture_list(
        _target(), timeout=5, max_pages=1, detail_limit=10
    )

    assert len(rows) == 1
    assert meta["source_cap_reached"] is True
    assert meta["pagination_complete"] is False
    assert "max_pages cap reached after 1 of 3" in meta["configured_collection_error"]
    assert "parsed 1 of 33" in meta["configured_collection_error"]


@pytest.mark.parametrize("failure", ["cap", "detail"])
def test_bucheon_detail_cap_or_failure_marks_the_collection_partial(monkeypatch, failure: str) -> None:
    cards = _card("15001", "접수중") + _card("15002", "접수예정")
    page = _list_page(cards, current=1, total_count=2)
    detail_calls: list[str] = []

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        if parsed.path == municipal.BUCHEON_LECTURE_LIST_PATH:
            return _soup(page)
        lecture_seq = parse_qs(parsed.query)["lec_seq"][0]
        detail_calls.append(lecture_seq)
        if failure == "detail" and lecture_seq == "15002":
            raise RuntimeError("fixture detail outage")
        return _soup(_detail_page(lecture_seq, accept_online=True))

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fetch)

    rows, _parser, meta = municipal.collect_bucheon_lecture_list(
        _target(), timeout=5, max_pages=2, detail_limit=1 if failure == "cap" else 2
    )

    assert len(rows) == 2
    assert meta["pagination_complete"] is False
    assert meta["no_current_data"] is False
    if failure == "cap":
        assert detail_calls == ["15001"]
        assert meta["detail_enrichment_capped"] is True
        assert "detail enrichment capped at 1 of 2" in meta["configured_collection_error"]
    else:
        assert detail_calls == ["15001", "15002"]
        assert meta["detail_errors"] == 1
        assert "detail fetch failed for 1" in meta["configured_collection_error"]


def test_bucheon_partial_default_blocks_save_and_stale(monkeypatch) -> None:
    save_calls: list[list[dict[str, Any]]] = []
    stale_calls: list[tuple[Any, ...]] = []
    partial_row = {
        "provider": PROVIDER,
        "provider_course_id": f"{PROVIDER}:lecture:15001",
        "title": "부분 수집 강좌",
        "branch": "부천시",
        "raw_url": municipal.bucheon_lecture_detail_url("15001"),
    }

    class FakeWriter:
        def __init__(self, provider: str) -> None:
            assert provider == PROVIDER

        def save_rows(self, rows: list[dict[str, Any]]) -> int:
            save_calls.append(rows)
            return len(rows)

    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: (
            [partial_row],
            municipal.BUCHEON_LECTURE_PARSER,
            {
                "pages": 1,
                "pagination_complete": False,
                "configured_collection_error": "max_pages cap reached after 1 of 13 declared pages",
            },
        ),
    )
    monkeypatch.setattr(
        municipal,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("partial collection must not open a DB transaction")),
    )
    monkeypatch.setattr(municipal, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(
        municipal,
        "mark_stale_courses",
        lambda *args: stale_calls.append(args) or 0,
    )

    reports = municipal.run(
        source="municipal",
        target_limit=None,
        per_target_limit=0,
        min_score=0,
        include_review=True,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=1,
        detail_limit=1,
        timeout=5,
    )

    assert reports[0].success is True
    assert reports[0].saved == 0
    assert reports[0].configured_collection_error.startswith("max_pages cap reached")
    assert save_calls == []
    assert stale_calls == []


def test_bucheon_target_lock_and_generated_full_run_contract() -> None:
    document = yaml.safe_load(
        (municipal.ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(
            encoding="utf-8"
        )
    )
    target = next(row for row in document["targets"] if row["provider"] == PROVIDER)
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert target["municipality_code"] == "4119000000"
    assert target["municipality_full_name"] == "경기도 부천시"
    assert [item["code"] for item in target["covered_municipalities"]] == [
        "4119000000",
        "4119200000",
        "4119400000",
        "4119600000",
    ]

    arguments = list(generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[PROVIDER])
    assert arguments == [
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "200",
    ]
    parsed = generated.parse_args(["--provider", PROVIDER, *arguments])
    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.per_target_limit == 0
    assert parsed.allow_partial_save is False
