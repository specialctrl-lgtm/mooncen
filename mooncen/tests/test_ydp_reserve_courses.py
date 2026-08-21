from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal


PROVIDER = municipal.YDP_RESERVE_PROVIDER
TARGET_URL = (
    "https://www.ydp.go.kr/reserve/selectTnEdcLctreListU.do?key=5062"
)


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=PROVIDER,
        name="영등포구 통합예약 교육강좌",
        branch="서울특별시 영등포구",
        url=TARGET_URL,
        source="test",
        priority=1,
        region="서울특별시 영등포구",
        extra={
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
        },
    )


def _card(
    lecture_no: int,
    status: str,
    title: str,
    venue: str,
    *,
    period: str = "99. 08. 01. ~ 99. 08. 31.",
    apply_period: str = "99. 07. 01. ~ 99. 07. 31.",
) -> str:
    return f"""
    <li class="typ1">
      <a class="sk" href="./viewTnEdcLctreU.do?lctreNo={lecture_no}&amp;user=USER&amp;rcpp=300&amp;cpn=99&amp;key=5062">
        <span>{status}</span></a>
      <div class="list-area">
        <a class="lists" href="./viewTnEdcLctreU.do?lctreNo={lecture_no}&amp;user=USER&amp;rcpp=300&amp;cpn=99&amp;key=5062">{title}</a>
        <ul class="adds"><li class="ad">{venue}</li><li class="as">#성인</li></ul>
        <p>접수기간 : {apply_period}</p>
        <p>교육기간 : {period}</p>
        <p>접수 / 정원 <span>3</span> / <span>20</span></p>
      </div>
    </li>
    """


def _list_page(
    cards: str,
    *,
    total: int,
    current: int = 1,
    pages: int = 1,
) -> str:
    links = "".join(
        f'<a href="?user=USER&amp;key=5062&amp;cpn={page}">{page}</a>'
        for page in range(1, pages + 1)
    )
    return f"""
    <html><body>
      <div class="clearfix"><div class="small">총
        <em class="em_black" data-mask="#,##0">{total}</em>건
        [ <em>{current}</em> / {pages} 페이지 ]
      </div></div>
      <ul class="board-lines">{cards}</ul>
      <div class="p-pagination">{links}</div>
    </body></html>
    """


def _detail_page(
    lecture_no: int,
    title: str,
    venue: str,
    *,
    application: bool,
    application_method: str = "온라인",
    notice: str = "수강생 유의사항입니다.",
) -> str:
    button = (
        f'<a class="write" href="./addTnEdcAtnlcViewU.do?insttNo=62&amp;lctreNo={lecture_no}'
        '&amp;user=USER&amp;rcpp=300&amp;cpn=99&amp;key=5062">수강신청</a>'
        if application
        else ""
    )
    return f"""
    <html><body>
      <table class="p-table block"><tbody>
        <tr><th>과정명</th><td>{title}</td></tr>
        <tr><th>교육장소</th><td>{venue}</td><th>강사명</th><td>홍길동</td></tr>
        <tr><th>접수방식</th><td>{application_method}</td><th>수강대상</th><td>#성인</td></tr>
        <tr><th>선별방식</th><td>선착순</td></tr>
        <tr><th>수강료</th><td>10,000원</td><th>재료비</th><td>2,000원</td></tr>
        <tr><th>접수기간</th><td>2099.07.01 09:00 ~ 2099.07.31 18:00</td></tr>
        <tr><th>교육기간</th><td>2099.08.01 ~ 2099.08.31</td></tr>
        <tr><th>강의요일</th><td>월 월 10:00 ~ 12:00</td></tr>
        <tr><th>정원</th><td>총인원 : 20명, 온라인인원 : 20명, 대기인원 : 5명</td></tr>
        <tr><th>강의개요</th><td>공식 상세 설명</td></tr>
        <tr><th>수강신청유의사항</th><td>{notice}</td></tr>
        <tr><th>교육과정문의</th><td>담당자 02-2670-1234</td></tr>
        <tr><th>관심분야</th><td>#자연/과학</td></tr>
      </tbody></table>
      {button}
    </body></html>
    """


def _live_shape_fixture() -> tuple[str, dict[str, str]]:
    statuses = (
        ["접수중"] * 68
        + ["대기접수"] * 9
        + ["접수예정"] * 17
        + ["접수마감"] * 29
        + ["교육중"] * 108
    )
    cards: list[str] = []
    details: dict[str, str] = {}
    for index, status in enumerate(statuses):
        lecture_no = 9000 + index
        title = f"공식 교육강좌 {lecture_no}"
        venue = "YDP미래평생학습관"
        notice = "수강생 유의사항입니다."
        if index == 0:
            title = "목록 제목 (신길5동 주민센터)"
            venue = "동주민센터"
        elif index == 1:
            venue = "YDP미래평생학습관4층(1강의실)"
        elif index == 2:
            venue = "기타"
            notice = (
                "✔ 장 소: 한국과학기술연구원(KIST) "
                "(서울시 성북구 화랑로14길 5) ✔ 참가비: 무료"
            )
        cards.append(_card(lecture_no, status, title, venue))
        has_application = index < 12 or 68 <= index < 77
        detail_title = (
            "상세 제목 (신길5동 주민센터)" if index == 0 else title
        )
        details[str(lecture_no)] = _detail_page(
            lecture_no,
            detail_title,
            venue,
            application=has_application,
            application_method="온라인" if has_application else "방문",
            notice=notice,
        )
    return _list_page("".join(cards), total=231), details


def test_ydp_full_231_fixture_is_exact_complete_and_uses_canonical_ids(monkeypatch) -> None:
    list_page, details = _live_shape_fixture()
    fetched: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.YDP_RESERVE_LIST_PATH:
            assert query == {
                "user": ["USER"],
                "key": [municipal.YDP_RESERVE_KEY],
                "rcpp": [str(municipal.YDP_RESERVE_PAGE_SIZE)],
            }
            return _soup(list_page)
        assert parsed.path == municipal.YDP_RESERVE_DETAIL_PATH
        assert set(query) == {"lctreNo", "user", "key"}
        return _soup(details[query["lctreNo"][0]])

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=5, detail_limit=300
    )

    assert parser == "ydp_education_lecture_list+detail"
    assert len(rows) == 231
    assert len({row["provider_course_id"] for row in rows}) == 231
    assert Counter(row["status"] for row in rows) == {
        "OPEN": 68,
        "WAITING": 9,
        "SCHEDULED": 17,
        "CLOSED": 137,
    }
    assert sum(bool(row.get("application_url")) for row in rows) == 21
    assert all(row["provider_course_id"].isdigit() for row in rows)
    assert all("cpn=" not in row["raw_url"] for row in rows)
    assert all("rcpp=" not in row["raw_url"] for row in rows)
    assert all("mooncen_course_id=" not in row["raw_url"] for row in rows)

    by_id = {row["provider_course_id"]: row for row in rows}
    resident = by_id["9000"]
    assert resident["title"] == "상세 제목 (신길5동 주민센터)"
    assert resident["branch"] == "신길5동 주민센터"
    assert resident["venue_name"] == "신길5동 주민센터"
    assert resident["branch_code"] == municipal.stable_provider(
        PROVIDER[:32], "신길5동 주민센터"
    )
    hall = by_id["9001"]
    assert hall["branch"] == "YDP미래평생학습관"
    assert hall["venue_name"] == "YDP미래평생학습관4층(1강의실)"
    external = by_id["9002"]
    assert external["branch"] == "한국과학기술연구원(KIST)"
    assert external["venue_name"] == "한국과학기술연구원(KIST)"
    assert external["venue_address"] == "서울시 성북구 화랑로14길 5"

    first = by_id["9000"]
    assert first["period"] == "2099-08-01 ~ 2099-08-31"
    assert first["apply_period"] == "2099-07-01 ~ 2099-07-31"
    assert str(first["start_date"]) == "2099-08-01"
    assert str(first["end_date"]) == "2099-08-31"
    assert first["schedule_raw"] == "월 10:00 ~ 12:00"
    assert first["capacity_current"] == 3
    assert first["capacity_total"] == 20
    assert first["waitlist_total"] == 5
    assert first["reservation_available"] is True
    assert first["application_url"] == (
        "https://www.ydp.go.kr/reserve/addTnEdcAtnlcViewU.do?"
        "insttNo=62&lctreNo=9000&user=USER&key=5062"
    )

    assert meta["pages"] == 1
    assert meta["detail_pages"] == 231
    assert meta["detail_attempts"] == 231
    assert meta["declared_total"] == 231
    assert meta["discovered_links"] == 231
    assert meta["reservation_discovery_links"] == 21
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta
    assert len(fetched) == 232


def test_ydp_cpn_fallback_exhausts_advertised_pages(monkeypatch) -> None:
    page_one = _list_page(
        _card(1001, "접수중", "첫 강좌", "당산1동주민센터")
        + _card(1002, "접수마감", "둘째 강좌", "당산1동 주민센터"),
        total=4,
        current=1,
        pages=2,
    )
    page_two = _list_page(
        _card(1003, "접수예정", "셋째 강좌", "신길7동주민센터")
        + _card(1004, "교육중", "넷째 강좌", "신길7동 주민센터"),
        total=4,
        current=2,
        pages=2,
    )
    fetched_list_pages: list[int] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 5
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.YDP_RESERVE_LIST_PATH:
            page = int((query.get("cpn") or ["1"])[0])
            fetched_list_pages.append(page)
            return _soup(page_two if page == 2 else page_one)
        lecture_no = int(query["lctreNo"][0])
        return _soup(
            _detail_page(
                lecture_no,
                f"상세 {lecture_no}",
                "당산1동 주민센터" if lecture_no < 1003 else "신길7동 주민센터",
                application=lecture_no == 1001,
            )
        )

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, _parser, meta = municipal.collect_ydp_reserve_list(
        _target(), timeout=5, max_pages=5, detail_limit=10
    )

    assert len(rows) == 4
    assert fetched_list_pages == [1, 2]
    assert meta["pages"] == 2
    assert meta["pagination_detected"] is True
    assert meta["snapshot_complete"] is True
    assert rows[0]["branch_code"] == rows[1]["branch_code"]
    assert rows[2]["branch_code"] == rows[3]["branch_code"]


def test_ydp_page_or_detail_caps_mark_snapshot_incomplete(monkeypatch) -> None:
    page = _list_page(
        _card(2001, "접수중", "첫 강좌", "YDP미래평생학습관")
        + _card(2002, "대기접수", "둘째 강좌", "YDP미래평생학습관"),
        total=4,
        current=1,
        pages=2,
    )
    complete_page = _list_page(
        _card(2001, "접수중", "첫 강좌", "YDP미래평생학습관")
        + _card(2002, "대기접수", "둘째 강좌", "YDP미래평생학습관"),
        total=2,
    )

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", lambda *_args, **_kwargs: _soup(page))
    _rows, _parser, page_meta = municipal.collect_ydp_reserve_list(
        _target(), timeout=5, max_pages=1, detail_limit=10
    )
    assert page_meta["snapshot_complete"] is False
    assert page_meta["source_cap_reached"] is True
    assert "max_pages cap reached" in page_meta["configured_collection_error"]

    def detail_capped_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 5
        if urlparse(url).path == municipal.YDP_RESERVE_LIST_PATH:
            return _soup(complete_page)
        lecture_no = int(parse_qs(urlparse(url).query)["lctreNo"][0])
        return _soup(
            _detail_page(
                lecture_no,
                f"상세 {lecture_no}",
                "YDP미래평생학습관",
                application=True,
            )
        )

    monkeypatch.setattr(municipal, "fetch_soup", detail_capped_fetch)
    rows, _parser, detail_meta = municipal.collect_ydp_reserve_list(
        _target(), timeout=5, max_pages=2, detail_limit=1
    )
    assert len(rows) == 2
    assert detail_meta["pagination_complete"] is True
    assert detail_meta["details_complete"] is False
    assert detail_meta["snapshot_complete"] is False
    assert detail_meta["source_cap_reached"] is True
    assert "detail_limit cap" in detail_meta["configured_collection_error"]


def test_ydp_incomplete_snapshot_blocks_save_and_mark_stale(monkeypatch) -> None:
    stale_calls: list[tuple[Any, ...]] = []
    partial_row = {
        "provider": PROVIDER,
        "provider_course_id": "2001",
        "title": "부분 수집 강좌",
        "branch": "YDP미래평생학습관",
        "raw_url": (
            "https://www.ydp.go.kr/reserve/viewTnEdcLctreU.do?"
            "lctreNo=2001&user=USER&key=5062"
        ),
    }
    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: (
            [partial_row],
            "ydp_education_lecture_list+detail",
            {
                "pages": 1,
                "detail_pages": 1,
                "snapshot_complete": False,
                "configured_collection_error": "detail_limit cap allows 1 of 2 required detail pages",
            },
        ),
    )
    monkeypatch.setattr(
        municipal,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("incomplete provider must not open DB")),
    )
    monkeypatch.setattr(
        municipal,
        "mark_stale_courses",
        lambda *args: stale_calls.append(args),
    )

    reports = municipal.run(
        source="test",
        target_limit=None,
        per_target_limit=0,
        min_score=0,
        include_review=False,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=120,
        detail_limit=1200,
        timeout=5,
    )

    assert len(reports) == 1
    assert reports[0].success is True
    assert reports[0].saved == 0
    assert reports[0].configured_collection_error
    assert stale_calls == []


def test_ydp_canonical_urls_reject_transient_or_foreign_identity() -> None:
    detail, lecture_no = municipal.canonical_ydp_reserve_detail_url(
        TARGET_URL,
        "./viewTnEdcLctreU.do?lctreNo=8343&user=USER&cpn=24&rcpp=300&key=5062&mooncen_course_id=bad#x",
    )
    assert lecture_no == "8343"
    assert detail == (
        "https://www.ydp.go.kr/reserve/viewTnEdcLctreU.do?"
        "lctreNo=8343&user=USER&key=5062"
    )
    assert municipal.canonical_ydp_reserve_detail_url(
        TARGET_URL, "https://evil.example/viewTnEdcLctreU.do?lctreNo=8343"
    ) == ("", "")
    assert municipal.canonical_ydp_reserve_application_url(
        detail,
        "https://evil.example/addTnEdcAtnlcViewU.do?insttNo=62&lctreNo=8343",
        "8343",
    ) == ""
    assert municipal.canonical_ydp_reserve_application_url(
        detail,
        "./addTnEdcAtnlcViewU.do?insttNo=62&lctreNo=9999&key=5062",
        "8343",
    ) == ""


def test_ydp_config_has_one_locked_education_owner_and_disables_search_duplicate() -> None:
    target_dir = Path("config/crawl_targets")
    all_rows: list[dict[str, Any]] = []
    for path in target_dir.glob("*.yaml"):
        if path.name == "index.yaml":
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        all_rows.extend(row for row in document.get("targets") or [] if isinstance(row, dict))

    owners = [row for row in all_rows if row.get("provider") == PROVIDER]
    assert len(owners) == 1
    owner = owners[0]
    assert owner["url"] == TARGET_URL
    assert owner["domain_category"] == "교육·강좌"
    assert owner["source_group"] == "municipal_reservation"
    assert owner["service_group"] == "공공강좌"
    assert owner["service_group_policy"] == "locked"
    assert owner["collection_type"] == "ydp_education_lecture_list+detail"
    assert owner["municipality_code"] == "1156000000"

    search = next(
        row for row in all_rows if row.get("provider") == "MUNI_WWW_YDP_GO_KR_0387C1A7"
    )
    assert search["collection_type"] == "duplicate"
    assert search["duplicate_of"] == PROVIDER
    assert search["superseded_by"] == PROVIDER
    assert search["crawler_status"] == f"duplicate_url:{PROVIDER}"

    arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[PROVIDER]
    assert arguments == (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "120",
        "--detail-limit",
        "1200",
    )
