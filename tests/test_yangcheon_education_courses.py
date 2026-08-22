from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import municipal_yangcheon as yangcheon


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Target:
    provider: str
    url: str
    branch: str = "서울특별시 양천구"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _integrated_target() -> Target:
    return Target(
        provider=yangcheon.YANGCHEON_INTEGRATED_PROVIDER,
        url=yangcheon.YANGCHEON_INTEGRATED_URL,
    )


def _lifestudy_target() -> Target:
    return Target(
        provider=yangcheon.YANGCHEON_LIFESTUDY_PROVIDER,
        url=yangcheon.YANGCHEON_LIFESTUDY_URL,
    )


def _integrated_list_row(
    lecture_id: str,
    number: int,
    *,
    end: str = "99.12.31",
    branch: str = "정보화교육",
    status: str = "접수중",
) -> str:
    start = "20.01.01" if end.startswith("20.") else "99.02.01"
    return f"""
      <tr onclick="javascript:doLectureUserView('{lecture_id}');">
        <td class="num">{number}</td>
        <td class="dong"><span>{branch}</span></td>
        <td class="edu-subj"><span>공식 강좌 {lecture_id}</span></td>
        <td class="edu-date">
          <span>접수 : 98.01.01~98.01.31</span>
          <span>교육 : {start}~{end}</span>
        </td>
        <td class="conf-date"><span>월/10:00~12:00</span></td>
        <td class="method"><span>선착순</span></td>
        <td class="people"><span>20명/5명/3명</span></td>
        <td class="state"><span>{status}</span></td>
      </tr>
    """


def _integrated_list_page(rows: str, *, current: int, total_pages: int) -> str:
    links = "".join(
        f'<a class="page_no {"page_on" if page == current else ""}" '
        f'onclick="doLectureUserPag({page});return false;">{page}</a>'
        for page in range(1, total_pages + 1)
    )
    return f"""
      <table class="table_list"><tbody>{rows}</tbody></table>
      <div class="pagination_wrap"><div class="pagintion">{links}</div></div>
    """


def _integrated_detail(
    lecture_id: str,
    *,
    period: str,
    branch: str,
    reservable: bool,
    application_method: str = "온라인접수",
) -> str:
    button = (
        f'<a class="submit-btn" onclick="doMemberForm(\'{lecture_id}\',\'{branch}\')">예약하기</a>'
        if reservable
        else ""
    )
    return f"""
      <meta id="mtTitle" property="og:title" content="공식 강좌 {lecture_id}" />
      <table class="common-table"><tbody>
        <tr><th>교육기관</th><td>{branch}</td><th>관리부서</th><td>교육지원과</td></tr>
        <tr><th>교육기간</th><td>{period}</td><th>총수강일</th><td>2일</td></tr>
        <tr><th>교육요일</th><td>월 10:00~12:00</td><th>수강료</th><td>무료</td></tr>
        <tr><th>강사명</th><td>양천강사</td><th>교육장소</th><td>{branch} 강의실</td></tr>
        <tr><th>접수기간</th><td>2098-01-01 ~ 2098-01-31</td><th>접수방법</th><td>{application_method}</td></tr>
        <tr><th>신청방법</th><td>선착순</td><th>모집인원</th><td>정원 20명 / 예비 5명 / 접수 3명</td></tr>
        <tr><th>전화문의</th><td>02-0000-0000</td><th>장애인 편의시설</th><td>-</td></tr>
      </tbody></table>
      <div class="box-btns">{button}</div>
      <div class="view-detail">상세 설명 {lecture_id}</div>
    """


def test_integrated_numbered_pagination_filters_expired_and_enriches_every_current_detail() -> None:
    rows = []
    details: dict[str, str] = {}
    statuses: dict[str, str] = {}
    for index in range(13):
        lecture_id = f"L{index + 1:08d}"
        number = 13 - index
        expired = index == 0
        status = "접수대기" if index % 3 == 0 else "접수중"
        branch = "정보화교육" if index % 2 == 0 else "보건소"
        end = "20.01.31" if expired else "99.12.31"
        rows.append(
            _integrated_list_row(
                lecture_id, number, end=end, branch=branch, status=status
            )
        )
        period = "2099-02-01 ~ 2099-12-31" if not expired else "2099-02-01 ~ 2020-01-31"
        details[lecture_id] = _integrated_detail(
            lecture_id,
            period=period,
            branch=branch,
            reservable=status == "접수중",
        )
        statuses[lecture_id] = status
    pages = {
        1: _integrated_list_page("".join(rows[:12]), current=1, total_pages=2),
        2: _integrated_list_page("".join(rows[12:]), current=2, total_pages=2),
    }
    fetched_details: list[str] = []
    lock = Lock()

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == yangcheon.YANGCHEON_INTEGRATED_LIST_PATH:
            return _soup(pages[int(query["pageIndex"][0])])
        assert parsed.path == yangcheon.YANGCHEON_INTEGRATED_DETAIL_PATH
        lecture_id = query["clIdx"][0]
        with lock:
            fetched_details.append(lecture_id)
        return _soup(details[lecture_id])

    result, parser, meta = yangcheon.collect_yangcheon_integrated(
        _integrated_target(),
        timeout=7,
        max_pages=2,
        detail_limit=20,
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
        max_workers=3,
    )

    assert parser == yangcheon.YANGCHEON_INTEGRATED_PARSER
    assert len(result) == 12
    assert len(fetched_details) == 12
    assert meta["total_count"] == 13
    assert meta["total_pages"] == 2
    assert meta["expired_count"] == 1
    assert meta["detail_pages"] == 12
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta
    assert {row["branch"] for row in result} == {"정보화교육", "보건소"}
    assert all(row["preserve_branch"] is True for row in result)
    assert all(row["branch_code"].startswith("YANGCHEON_BRANCH_") for row in result)
    assert all(row["municipality_code"] == "1147000000" for row in result)
    assert all(row["municipality_full_name"] == "서울특별시 양천구" for row in result)
    assert all(row["service_group"] == "공공강좌" for row in result)
    assert all(row["service_group_policy"] == "locked" for row in result)
    assert all(row["program_type"] == "강좌" for row in result)
    for row in result:
        lecture_id = row["raw_fields"]["lecture_id"]
        assert row["provider_course_id"] == (
            f"{yangcheon.YANGCHEON_INTEGRATED_PROVIDER}:lecture:{lecture_id}"
        )
        if statuses[lecture_id] == "접수중":
            assert row["application_url"] == row["raw_url"]
            assert row["reservation_available"] is True
        else:
            assert "application_url" not in row
            assert row["reservation_available"] is False


def _life_card(
    lecture_id: str,
    *,
    status: str,
    branch: str,
    end: str = "99.12.31",
) -> str:
    start = "20.01.01" if end.startswith("20.") else "99.02.01"
    return f"""
      <div class="course_card">
        <div class="boxs">
          <div class="course_header"><span class="course_institute">{branch}</span></div>
          <div class="teg_box"><span class="course_tag">인문교양</span></div>
          <h3 class="course_title"><a onclick="fnView('{lecture_id}');">평생강좌 {lecture_id}</a></h3>
          <ul class="course_info"><li><strong>교육기간</strong> {start} ~ {end}<br />
            <span>접수인원 : 1/20명 대기0명</span></li></ul>
          <div class="teg_order_list"><span class="point">무료</span><span>온라인</span><span>월: 10:00 ~ 12:00</span></div>
        </div>
        <a onclick="fnView('{lecture_id}');"><div class="course_status">
          <span class="status_label">{status}</span>
          <span class="status_period"><div>98.01.01 ~ 98.01.31</div></span>
        </div></a>
      </div>
    """


def _life_page(cards: str, *, state: str, total: int, current: int, pages: int) -> str:
    return f"""
      <input type="hidden" name="searchSttusCdArr" value="{state}" />
      <div class="board_total"><mark></mark> 총 <em>{total}</em> 건 ({current}/{pages} 페이지)</div>
      <div class="course_card_wrap">{cards}</div>
    """


def _life_detail(
    lecture_id: str,
    *,
    branch: str,
    application: str = "none",
) -> str:
    if application == "external":
        button = '<a href="https://booking.example/apply" class="b-save">예약 사이트로 이동</a>'
    elif application == "internal":
        button = '<a href="" class="b-save" onclick="tabMove(\'B\');return false;">예약하기</a>'
    elif application == "unsafe":
        button = '<a href="javascript:alert(1)" class="b-save">예약하기</a>'
    else:
        button = '<span class="b-default">접수 종료</span>'
    return f"""
      <div class="view-hgroup">
        <em class="view-hgroup__cate">접수중</em>
        <h3 class="view-hgroup__title">평생강좌 {lecture_id}</h3>
        <div class="title-btn-set">{button}</div>
      </div>
      <div class="bd-view">
        <dl><dt>기관</dt><dd>{branch}</dd><dt>문의전화</dt><dd>02-1111-2222</dd></dl>
        <dl><dt>분류</dt><dd>평생교육</dd><dt>분야</dt><dd>인문교양</dd></dl>
        <dl><dt>장소</dt><dd>{branch} 강의실</dd><dt>주소</dt><dd>서울특별시 양천구</dd></dl>
        <dl><dt>대상</dt><dd>양천구민</dd></dl>
        <dl><dt>교육기간</dt><dd>2099.02.01 ~ 2099.12.31</dd>
            <dt>교육요일 및 교육시간 안내</dt><dd>월 10:00 ~ 12:00</dd></dl>
        <dl><dt>수강료</dt><dd>무료</dd><dt>재료비</dt><dd>-</dd></dl>
        <dl><dt>총접수인원</dt><dd>정원 20명 / 신청 1명</dd></dl>
        <dl><dt>모집방법/접수방법</dt><dd>온라인</dd></dl>
        <dl><dt>강좌소개</dt><dd>공식 상세 설명</dd></dl>
      </div>
    """


def _life_fixture(monkeypatch: Any) -> tuple[dict[tuple[str, int], str], dict[str, str]]:
    monkeypatch.setattr(yangcheon, "YANGCHEON_LIFESTUDY_PAGE_SIZE", 2)
    cards = {
        "READY": [
            _life_card("100", status="접수예정", branch="방아다리문학도서관")
        ],
        "RECPT_PROGRESS": [
            _life_card("201", status="접수중", branch="양천창업지원센터"),
            _life_card("202", status="접수중", branch="목동미래교육센터"),
            _life_card("203", status="접수중", branch="목동미래교육센터"),
        ],
        "RECPT_END": [
            _life_card("301", status="접수마감", branch="평생학습관"),
            _life_card("302", status="접수마감", branch="평생학습관", end="20.01.01"),
        ],
    }
    pages = {
        ("READY", 1): _life_page(
            cards["READY"][0], state="READY", total=1, current=1, pages=1
        ),
        ("RECPT_PROGRESS", 1): _life_page(
            "".join(cards["RECPT_PROGRESS"][:2]),
            state="RECPT_PROGRESS",
            total=3,
            current=1,
            pages=2,
        ),
        ("RECPT_PROGRESS", 2): _life_page(
            cards["RECPT_PROGRESS"][2],
            state="RECPT_PROGRESS",
            total=3,
            current=2,
            pages=2,
        ),
        ("RECPT_END", 1): _life_page(
            "".join(cards["RECPT_END"]),
            state="RECPT_END",
            total=2,
            current=1,
            pages=1,
        ),
    }
    branches = {
        "100": "방아다리문학도서관",
        "201": "양천창업지원센터",
        "202": "목동미래교육센터",
        "203": "목동미래교육센터",
        "301": "평생학습관",
        "302": "평생학습관",
    }
    return pages, branches


def test_lifestudy_status_pagination_details_and_expired_closed_filter(monkeypatch: Any) -> None:
    pages, branches = _life_fixture(monkeypatch)
    details = {
        lecture_id: _life_detail(
            lecture_id,
            branch=branch,
            application=(
                "external" if lecture_id == "201" else "internal" if lecture_id in {"202", "203"} else "none"
            ),
        )
        for lecture_id, branch in branches.items()
    }
    session_count = 0
    session_lock = Lock()

    def sessions() -> DummySession:
        nonlocal session_count
        with session_lock:
            session_count += 1
        return DummySession()

    def fetch(_session: object, url: str, _timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == yangcheon.YANGCHEON_LIFESTUDY_LIST_PATH:
            assert query["pageUnit"] == ["2"]
            return _soup(pages[(query["searchSttusCdArr"][0], int(query["pageIndex"][0]))])
        assert parsed.path == yangcheon.YANGCHEON_LIFESTUDY_DETAIL_PATH
        return _soup(details[query["lctreNo"][0]])

    result, parser, meta = yangcheon.collect_yangcheon_integrated(
        _lifestudy_target(),
        timeout=5,
        max_pages=3,
        detail_limit=10,
        fetcher=fetch,
        session_factory=sessions,
        today="2026-07-19",
        max_workers=2,
    )

    assert parser == yangcheon.YANGCHEON_LIFESTUDY_PARSER
    assert len(result) == 5
    assert meta["declared_totals_by_status"] == {
        "READY": 1,
        "RECPT_PROGRESS": 3,
        "RECPT_END": 2,
    }
    assert meta["status_pages"] == {"READY": 1, "RECPT_PROGRESS": 2, "RECPT_END": 1}
    assert meta["expired_count"] == 1
    assert meta["detail_attempts"] == meta["detail_pages"] == 5
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta
    assert 2 <= session_count <= 3  # one list session plus at most two detail workers
    by_id = {row["raw_fields"]["lecture_id"]: row for row in result}
    assert by_id["201"]["application_url"] == "https://booking.example/apply"
    assert by_id["202"]["application_url"] == by_id["202"]["raw_url"]
    assert "application_url" not in by_id["100"]
    assert "application_url" not in by_id["301"]
    assert by_id["203"]["branch"] == "목동미래교육센터"
    assert by_id["202"]["branch_code"] == by_id["203"]["branch_code"]
    assert all(row["source_group"] == "municipal_reservation" for row in result)
    assert all(row["domain_category"] == "교육·강좌" for row in result)


def test_integrated_open_offline_course_does_not_require_online_control() -> None:
    lecture_id = "L00000999"
    page = _integrated_list_page(
        _integrated_list_row(lecture_id, 1, branch="목1동", status="접수중"),
        current=1,
        total_pages=1,
    )
    detail = _integrated_detail(
        lecture_id,
        period="2099-02-01 ~ 2099-12-31",
        branch="목1동",
        reservable=False,
        application_method="전화 및 현장접수",
    )

    def fetch(_session: object, url: str, _timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        return _soup(
            page if parsed.path == yangcheon.YANGCHEON_INTEGRATED_LIST_PATH else detail
        )

    rows, _parser, meta = yangcheon.collect_yangcheon_portal(
        _integrated_target(),
        max_pages=1,
        detail_limit=1,
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
    )

    assert len(rows) == 1
    assert rows[0]["application_type"] == "OFFLINE_APPLY"
    assert rows[0]["reservation_available"] is False
    assert "application_url" not in rows[0]
    assert meta["snapshot_complete"] is True


def test_lifestudy_external_list_card_is_complete_without_empty_internal_detail(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(yangcheon, "YANGCHEON_LIFESTUDY_PAGE_SIZE", 2)
    lecture_id = "830766"
    external_url = (
        "https://www.work24.go.kr/hr/a/a/3100/selectTracseDetl.do?"
        "tracseId=AIG20250000506274&tracseTme=2"
    )
    card = f"""
      <div class="course_card">
        <div class="boxs">
          <div class="course_header"><span class="course_institute">서부여성발전센터</span></div>
          <div class="teg_box"><span class="course_tag"></span></div>
          <h3 class="course_title"><a href="{external_url}" target="_blank">직장인 실무엑셀 토요반</a></h3>
          <ul class="course_info"><li><strong>교육기간</strong> 99.07.25 ~ 99.09.05<br />
            <span>접수인원 : 0/17명 대기0명</span></li></ul>
          <div class="teg_order_list"><span>유료</span><span>온라인</span></div>
        </div>
        <a onclick="fnView('{lecture_id}');"><div class="course_status ongoing">
          <span class="status_label">접수중</span>
          <span class="status_period"><div>98.06.10 ~ 98.07.22</div></span>
        </div></a>
      </div>
    """
    pages = {
        ("READY", 1): _empty_life_page("READY"),
        ("RECPT_PROGRESS", 1): _life_page(
            card, state="RECPT_PROGRESS", total=1, current=1, pages=1
        ),
        ("RECPT_END", 1): _empty_life_page("RECPT_END"),
    }

    def fetch(_session: object, url: str, _timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        assert parsed.path == yangcheon.YANGCHEON_LIFESTUDY_LIST_PATH
        query = parse_qs(parsed.query)
        return _soup(pages[(query["searchSttusCdArr"][0], 1)])

    rows, _parser, meta = yangcheon.collect_yangcheon_lifestudy(
        _lifestudy_target(),
        max_pages=1,
        detail_limit=1,
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
    )

    assert len(rows) == 1
    assert rows[0]["provider_course_id"].endswith(f":lecture:{lecture_id}")
    assert rows[0]["raw_url"] == yangcheon.yangcheon_lifestudy_detail_url(lecture_id)
    assert rows[0]["application_url"] == external_url
    assert rows[0]["application_type"] == "EXTERNAL_RESERVATION"
    assert rows[0]["raw_fields"]["detail_required"] is False
    assert meta["required_detail_count"] == 0
    assert meta["detail_exempt_count"] == 1
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is True


def _empty_life_page(state: str) -> str:
    return _life_page("", state=state, total=0, current=1, pages=1)


def test_lifestudy_rejects_unsafe_application_url_fail_closed(monkeypatch: Any) -> None:
    monkeypatch.setattr(yangcheon, "YANGCHEON_LIFESTUDY_PAGE_SIZE", 2)
    pages = {
        ("READY", 1): _empty_life_page("READY"),
        ("RECPT_PROGRESS", 1): _life_page(
            _life_card("901", status="접수중", branch="양천학습관"),
            state="RECPT_PROGRESS",
            total=1,
            current=1,
            pages=1,
        ),
        ("RECPT_END", 1): _empty_life_page("RECPT_END"),
    }

    def fetch(_session: object, url: str, _timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == yangcheon.YANGCHEON_LIFESTUDY_LIST_PATH:
            return _soup(pages[(query["searchSttusCdArr"][0], 1)])
        return _soup(_life_detail("901", branch="양천학습관", application="unsafe"))

    rows, _parser, meta = yangcheon.collect_yangcheon_lifestudy(
        _lifestudy_target(),
        max_pages=2,
        detail_limit=2,
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
    )

    assert len(rows) == 1
    assert "application_url" not in rows[0]
    assert rows[0]["reservation_available"] is False
    assert meta["snapshot_complete"] is False
    assert "no safe http(s) destination" in meta["configured_collection_error"]


def test_caps_are_reported_and_snapshot_is_never_complete(monkeypatch: Any) -> None:
    pages, branches = _life_fixture(monkeypatch)
    details = {
        lecture_id: _life_detail(lecture_id, branch=branch)
        for lecture_id, branch in branches.items()
    }

    def fetch(_session: object, url: str, _timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == yangcheon.YANGCHEON_LIFESTUDY_LIST_PATH:
            return _soup(pages[(query["searchSttusCdArr"][0], int(query["pageIndex"][0]))])
        return _soup(details[query["lctreNo"][0]])

    page_rows, _parser, page_meta = yangcheon.collect_yangcheon_lifestudy(
        _lifestudy_target(),
        max_pages=1,
        detail_limit=20,
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
    )
    assert page_rows
    assert page_meta["source_cap_reached"] is True
    assert page_meta["snapshot_complete"] is False
    assert "max_pages cap reached after 1 of 2" in page_meta["configured_collection_error"]

    detail_rows, _parser, detail_meta = yangcheon.collect_yangcheon_lifestudy(
        _lifestudy_target(),
        max_pages=3,
        detail_limit=1,
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
    )
    assert len(detail_rows) == 5
    assert detail_meta["detail_attempts"] == 1
    assert detail_meta["source_cap_reached"] is True
    assert detail_meta["snapshot_complete"] is False
    assert "detail_limit cap allows 1 of 5" in detail_meta["configured_collection_error"]


def test_provider_owned_routes_are_exact_and_invalid_dispatch_does_not_fetch() -> None:
    assert yangcheon.is_target(_integrated_target()) is True
    assert yangcheon.is_target(_lifestudy_target()) is True
    assert yangcheon.is_target(
        Target(
            provider=yangcheon.YANGCHEON_INTEGRATED_PROVIDER,
            url=f"{yangcheon.YANGCHEON_INTEGRATED_URL}?pageIndex=1",
        )
    ) is False
    assert yangcheon.is_target(
        Target(
            provider=yangcheon.YANGCHEON_LIFESTUDY_PROVIDER,
            url=f"{yangcheon.YANGCHEON_LIFESTUDY_URL}&pageIndex=1",
        )
    ) is False
    assert yangcheon.is_target(
        Target(provider="MUNI_UNOWNED", url=yangcheon.YANGCHEON_INTEGRATED_URL)
    ) is False

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("invalid target must not perform network I/O")

    rows, _parser, meta = yangcheon.collect_yangcheon_integrated(
        Target(provider="MUNI_UNOWNED", url="https://evil.example/course"),
        fetcher=forbidden,
        session_factory=forbidden,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "provider-owned canonical" in meta["configured_collection_error"]


def test_yangcheon_targets_require_complete_snapshots_and_safe_scheduler_caps() -> None:
    document = yaml.safe_load(
        (
            ROOT
            / "config"
            / "crawl_targets"
            / "municipal_integrated_reservation.yaml"
        ).read_text(encoding="utf-8")
    )
    targets = {
        row["provider"]: row
        for row in document["targets"]
        if row.get("provider") in yangcheon.YANGCHEON_PROVIDERS
    }
    assert set(targets) == yangcheon.YANGCHEON_PROVIDERS
    for provider, target in targets.items():
        assert target["url"] == yangcheon.YANGCHEON_CANONICAL_URLS[provider]
        assert target["crawler_status"] == "ready"
        assert target["full_snapshot_required"] is True
        assert target["municipality_code"] == "1147000000"
        assert target["service_group"] == "공공강좌"
        assert target["service_group_policy"] == "locked"

        arguments = list(generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider])
        assert arguments[:4] == ["--save-db", "--mark-stale", "--per-target-limit", "0"]
        assert "--allow-partial-save" not in arguments

    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        yangcheon.YANGCHEON_INTEGRATED_PROVIDER
    ][-4:] == ("--max-pages", "10", "--detail-limit", "100")
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        yangcheon.YANGCHEON_LIFESTUDY_PROVIDER
    ][-4:] == ("--max-pages", "30", "--detail-limit", "1000")
