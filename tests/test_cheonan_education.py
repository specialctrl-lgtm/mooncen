from __future__ import annotations

import os
from typing import Any

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_cheonan as ch
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


TARGETS = (
    (ch.CHEONAN_INTEGRATED_PROVIDER, ch.CHEONAN_INTEGRATED_URL),
    (ch.CHEONAN_LIBRARY_PROVIDER, ch.CHEONAN_LIBRARY_URL),
    (ch.CHEONAN_SEONGJEONG_PROVIDER, ch.CHEONAN_SEONGJEONG_URL),
    (ch.CHEONAN_DUJEONG_PROVIDER, ch.CHEONAN_DUJEONG_URL),
    (ch.CHEONAN_DISABILITY_PROVIDER, ch.CHEONAN_DISABILITY_URL),
    (ch.CHEONAN_MEDIA_PROVIDER, ch.CHEONAN_MEDIA_URL),
    (ch.CHEONAN_EXPERIENCE_PROVIDER, ch.CHEONAN_EXPERIENCE_URL),
)


@pytest.mark.parametrize("provider,url", TARGETS)
def test_exact_canonical_owner_targets(provider: str, url: str) -> None:
    assert ch.is_cheonan_education_target({"provider": provider, "url": url})
    assert not ch.is_cheonan_education_target(
        {"provider": provider, "url": url + "#fragment"}
    )
    assert not ch.is_cheonan_education_target(
        {"provider": provider, "url": url.replace("https://", "http://")}
    )
    assert not ch.is_cheonan_education_target(
        {"provider": provider, "url": url.replace("https://", "https://user:pw@")}
    )


@pytest.mark.parametrize("provider,url", TARGETS)
def test_production_collection_requires_managed_session(provider: str, url: str) -> None:
    rows, parser, meta = ch.collect(
        {"provider": provider, "url": url}, today="2026-07-23"
    )
    assert rows == []
    assert parser == ch.CHEONAN_PARSER
    assert meta["snapshot_complete"] is False
    assert meta["application_endpoints_called"] == 0
    assert "session_factory" in meta["configured_collection_error"]


def test_provider_and_candidate_ids_follow_repository_url_hashes() -> None:
    pairs = (
        (ch.CHEONAN_INTEGRATED_PROVIDER, ch.CHEONAN_INTEGRATED_CANDIDATE_ID, ch.CHEONAN_INTEGRATED_URL),
        (ch.CHEONAN_LIBRARY_PROVIDER, ch.CHEONAN_LIBRARY_CANDIDATE_ID, ch.CHEONAN_LIBRARY_URL),
        (ch.CHEONAN_SEONGJEONG_PROVIDER, ch.CHEONAN_SEONGJEONG_CANDIDATE_ID, ch.CHEONAN_SEONGJEONG_URL),
        (ch.CHEONAN_DUJEONG_PROVIDER, ch.CHEONAN_DUJEONG_CANDIDATE_ID, ch.CHEONAN_DUJEONG_URL),
        (ch.CHEONAN_DISABILITY_PROVIDER, ch.CHEONAN_DISABILITY_CANDIDATE_ID, ch.CHEONAN_DISABILITY_URL),
        (ch.CHEONAN_MEDIA_PROVIDER, ch.CHEONAN_MEDIA_CANDIDATE_ID, ch.CHEONAN_MEDIA_URL),
        (ch.CHEONAN_EXPERIENCE_PROVIDER, ch.CHEONAN_EXPERIENCE_CANDIDATE_ID, ch.CHEONAN_EXPERIENCE_URL),
    )
    for provider, candidate, url in pairs:
        assert provider == stable_provider(url)
        assert candidate == candidate_id(normalized_duplicate_url(url))


def test_official_branch_names_and_district_assignments() -> None:
    assert ch.CHEONAN_LIBRARY_BRANCHES == (
        ("AD", "도서관정책과", "4413000000"),
        ("JY", "중앙도서관", "4413100000"),
        ("SG", "성거도서관", "4413300000"),
        ("SY", "쌍용도서관", "4413300000"),
        ("AW", "아우내도서관", "4413100000"),
        ("DS", "도솔도서관", "4413300000"),
        ("DJ", "두정도서관", "4413300000"),
        ("SB", "신방도서관", "4413100000"),
        ("CS", "청수도서관", "4413100000"),
        ("JS", "직산도서관", "4413300000"),
    )
    assert ch._municipality_for_region("목천읍")[0] == "4413100000"
    assert ch._municipality_for_region("불당2동")[0] == "4413300000"
    assert ch.CHEONAN_SEONGJEONG_BRANCH == "성정평생학습관"
    assert ch.CHEONAN_DUJEONG_BRANCH == "두정평생학습관"
    assert ch.CHEONAN_DISABILITY_BRANCH == "천안시장애인평생교육센터"
    assert ch.CHEONAN_MEDIA_BRANCH == "천안시영상미디어센터 비채"


def test_owner_boundaries_exclude_alias_applicant_sports_and_provincial_ledgers() -> None:
    audit = ch.CHEONAN_OWNER_BOUNDARY_AUDIT
    assert "landing" in audit["lifelong_portal_landing"]["decision"]
    assert audit["finding_lifelong_applications"]["decision"].startswith("hard_exclude")
    assert "separate sports" in audit["municipal_sports"]["decision"]
    assert "provincial" in audit["provincial_education_office"]["decision"]
    assert audit["media_centre"]["url"].endswith("/edu/list.php")
    assert ch.CHEONAN_MEDIA_INFO_ALIAS_URL.endswith("sub.php?menucode=0201")
    assert audit["experience"]["provider"] == ch.CHEONAN_EXPERIENCE_PROVIDER
    assert audit["experience"]["decision"].startswith("independent canonical")


class _NeverSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> None:
        self.calls.append(url)
        raise AssertionError("network must not be reached")


@pytest.mark.parametrize(
    "url",
    (
        ch.CHEONAN_INTEGRATED_APPLICATION_ENDPOINT,
        ch.CHEONAN_LIBRARY_APPLICATION_ENDPOINT,
        ch.CHEONAN_SEONGJEONG_APPLICATION_ENDPOINT,
        ch.CHEONAN_DUJEONG_APPLICATION_ENDPOINT,
        ch.CHEONAN_DISABILITY_APPLICATION_ENDPOINT,
        ch.CHEONAN_DISABILITY_STATE_ENDPOINT,
        ch.CHEONAN_MEDIA_APPLICATION_ENDPOINT,
        ch.CHEONAN_EXPERIENCE_APPLICATION_ENDPOINT,
    ),
)
def test_runner_refuses_application_endpoints_before_network(url: str) -> None:
    session = _NeverSession()
    runner = ch._Runner(lambda: session, 10, 10, lambda _: None)
    with pytest.raises(ch.CheonanContractError, match="application endpoint"):
        runner.get(url)
    assert session.calls == []


def _integrated_soup(institution: str = "천안박물관") -> BeautifulSoup:
    return BeautifulSoup(
        f"""
        <table><thead><tr>
          <th>기관구분</th><th>지역구분</th><th>강좌명</th><th>접수기간</th>
          <th>교육기간</th><th>교육시간</th><th>선정방식</th>
          <th>신청 /모집 인원 (명)</th><th>접수상태</th>
        </tr></thead><tbody><tr>
          <td>{institution}</td><td>청룡동</td>
          <td><a href="/prog/yeyakEdu/yeyak/sub01_01/view.do?eduNo=3786">박물관 교실</a></td>
          <td>2026-07-19 09:00 ~ 2026-08-28 18:00</td>
          <td>2026-08-29 ~ 2026-08-29</td><td>10:00-12:00</td>
          <td>선착순</td><td>5 / 15 (대기 0 / 2)</td><td>접수중</td>
        </tr></tbody></table>
        """,
        "lxml",
    )


def test_integrated_parser_assigns_dongnam_and_marks_delegated_aliases() -> None:
    row = ch._integrated_page(_integrated_soup())[0]
    assert row["provider_course_id"].endswith(":3786")
    assert row["branch"] == "천안박물관"
    assert row["municipality_code"] == "4413100000"
    assert row["capacity_total"] == 15 and row["capacity_current"] == 5
    assert row["raw_fields"]["delegated_owner_alias"] is False
    assert row["collection_category"] == "공공예약"
    assert row["domain_category"] == "교육·강좌"
    assert row["source_group"] == "municipal_reservation"
    assert row["service_group"] == "공공강좌"
    assert row["service_group_policy"] == "locked"
    assert row["classification_locked"] is True
    alias = ch._integrated_page(_integrated_soup("두정도서관"))[0]
    assert alias["raw_fields"]["delegated_owner_alias"] is True


def test_integrated_detail_is_safe_and_only_discovers_application_endpoint() -> None:
    row = ch._integrated_page(_integrated_soup())[0]
    detail = BeautifulSoup(
        """
        <div class="yeyakView">
          <strong class="info-title">박물관 교실</strong>
          <div class="pe-content"><ul>
            <li><strong class="subjact">교육기간</strong><span class="con">2026-08-29 ~ 2026-08-29</span></li>
            <li><strong class="subjact">교육장소</strong><span class="con">3층 강의실</span></li>
          </ul></div>
          <div class="progView-bottom-box"><div class="view-content">문의 041-111-2222 private@example.org</div></div>
          <button class="button_write">신청하기</button>
        </div>
        """,
        "lxml",
    )

    class SafeRunner:
        calls: list[str] = []

        def soup(self, url: str, *, parameterized: bool = True) -> BeautifulSoup:
            self.calls.append(url)
            return detail

    runner = SafeRunner()
    assert ch._integrated_detail(runner, row) == 1
    assert runner.calls == [row["raw_url"]]
    assert row["application_url"].startswith(ch.CHEONAN_INTEGRATED_APPLICATION_ENDPOINT)
    assert "041-111-2222" not in str(row)
    assert "private@example.org" not in str(row)


def _library_soup(*, total: int = 1, page: int = 1, last: int = 1) -> BeautifulSoup:
    return BeautifulSoup(
        f"""
        <div>총 게시물 {total} , 페이지 {page} / {last}</div>
        <table><thead><tr>
          <th>No.</th><th>분야</th><th>강좌명/ 강사명</th><th>대상</th>
          <th>참여가능년생</th><th>접수기간</th><th>교육기간/ 시간</th>
          <th>신청/ 모집인원</th><th>모집방법/ 모집상태</th>
        </tr></thead><tbody><tr>
          <td>1</td><td>강좌</td><td><span>[청수]</span>
          <a href="/prog/libLctr/lib/sub02_01/view.do?mngNo=599">상상공작소</a></td>
          <td>초등 1~2학년</td><td>2018 ~ 2019년생</td>
          <td>2026-07-09 10:00 ~ 마감시까지</td>
          <td>2026-07-28 ~ 2026-07-31 화요일(10:00 ~ 12:00)</td>
          <td>10/12 대기(0/5)</td><td>모집중</td>
        </tr></tbody></table>
        """,
        "lxml",
    )


def test_library_parser_expands_exact_official_branch_name() -> None:
    row = ch._library_page(_library_soup())[0]
    assert row["provider_course_id"].endswith(":599")
    assert row["branch"] == "청수도서관"
    assert row["municipality_code"] == "4413100000"
    assert row["end_date"] == "2026-07-31"
    assert row["status"] == "OPEN"


def test_library_duplicate_page_sentinel_must_add_no_new_identity() -> None:
    pages = [_library_soup(), _library_soup()]

    class Runner:
        def soup(self, *_: Any, **__: Any) -> BeautifulSoup:
            return pages.pop(0)

    rows, meta = ch._collect_library(
        Runner(), ch.date(2030, 1, 1), max_pages=3, detail_limit=2
    )
    assert rows == []
    assert meta["source_total"] == 1
    assert meta["sentinel_raw_rows"] == 1
    assert meta["sentinel_count"] == 0


def _lifelong_soup(
    owner: str,
    *,
    dujeong_status: str = "모집마감",
    dujeong_identity: str = "228",
) -> BeautifulSoup:
    if owner == "seongjeong":
        header = """
          <th>번호</th><th>강좌명</th><th>강사명</th><th>접수기간</th>
          <th>모집/신청 인원 (명)</th><th>상태</th>
        """
        body = """
          <td>1</td><td><button class="button_view" data-lctr-no="5115927">동화구연</button></td>
          <td>홍길동</td><td>2026-07-14 ~ 2026-08-07</td><td>15 / 15</td>
          <td>추가접수 모집중</td>
        """
    else:
        header = """
          <th>번호</th><th>강좌명/강사명</th><th>접수기간</th>
          <th>교육기간/ 교육시간</th><th>모집인원/ 신청인원/ 대기인원(명)</th><th>상태</th>
        """
        body = """
          <td>1</td><td><button class="button_view" data-lctr-no="{identity}">
          <span class="title">걸어갑니다. 세계 속으로 / 김가람 PD / KBS</span></button></td>
          <td>2026-07-06 ~ 2026-07-22</td><td>2026-07-23 화요일 19:00</td>
          <td>90 / 90 / 0</td><td>{status}</td>
        """.format(identity=dujeong_identity, status=dujeong_status)
    return BeautifulSoup(
        f"<table><thead><tr>{header}</tr></thead><tbody><tr>{body}</tr></tbody></table>",
        "lxml",
    )


def test_lifelong_parsers_omit_instructors_and_keep_distinct_branches() -> None:
    seongjeong = ch._lifelong_page(_lifelong_soup("seongjeong"), "seongjeong")[0]
    dujeong = ch._lifelong_page(_lifelong_soup("dujeong"), "dujeong")[0]
    assert seongjeong["title"] == "동화구연"
    assert seongjeong["branch"] == "성정평생학습관"
    assert seongjeong["preserve_branch"] is True
    assert "홍길동" not in str(seongjeong)
    assert dujeong["title"] == "걸어갑니다. 세계 속으로"
    assert dujeong["branch"] == "두정평생학습관"
    assert dujeong["preserve_branch"] is True
    assert "김가람" not in str(dujeong) and "KBS" not in str(dujeong)


@pytest.mark.parametrize(
    ("source_status", "apply_type"),
    (
        ("추가 모집중", "APPLY"),
        ("추가대기자 모집중", "WAIT"),
    ),
)
def test_dujeong_additional_recruitment_status_and_application_control(
    source_status: str,
    apply_type: str,
) -> None:
    row = ch._lifelong_page(
        _lifelong_soup(
            "dujeong",
            dujeong_status=source_status,
            dujeong_identity="233",
        ),
        "dujeong",
    )[0]
    detail = BeautifulSoup(
        f"""
        <div class="lifelongLearningView">
          <strong class="info-title">걸어갑니다. 세계 속으로</strong>
          <div class="pe-content"><ul>
            <li><strong class="subjact">교육기간</strong>
                <span class="con">2026-07-23 ~ 2026-08-31</span></li>
            <li><strong class="subjact">교육시간</strong>
                <span class="con">화요일 19:00~21:00</span></li>
            <li><strong class="subjact">교육장소</strong>
                <span class="con">두정평생학습관 강의실</span></li>
            <li><strong class="subjact">교육대상</strong>
                <span class="con">성인</span></li>
            <li><strong class="subjact">수업료</strong>
                <span class="con">무료</span></li>
          </ul></div>
          <button class="btn primary medium button_aply"
                  data-lctr-no="233" data-smstr-no="29"
                  data-apply-type="{apply_type}">{source_status}</button>
        </div>
        """,
        "lxml",
    )

    class Runner:
        def soup(self, *_: Any, **__: Any) -> BeautifulSoup:
            return detail

    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert ch._lifelong_detail(Runner(), row, "dujeong") == 1
    assert row["application_url"].endswith("write.do?lctrNo=233")


def test_lifelong_application_control_must_match_course_identity() -> None:
    row = ch._lifelong_page(
        _lifelong_soup(
            "dujeong",
            dujeong_status="추가 모집중",
            dujeong_identity="233",
        ),
        "dujeong",
    )[0]
    detail = BeautifulSoup(
        """
        <div class="lifelongLearningView">
          <strong class="info-title">걸어갑니다. 세계 속으로</strong>
          <div class="pe-content"><ul>
            <li><strong class="subjact">교육기간</strong>
                <span class="con">2026-07-23 ~ 2026-08-31</span></li>
          </ul></div>
          <button class="button_aply" data-lctr-no="999"
                  data-apply-type="APPLY">추가 모집중</button>
        </div>
        """,
        "lxml",
    )

    class Runner:
        def soup(self, *_: Any, **__: Any) -> BeautifulSoup:
            return detail

    with pytest.raises(ch.CheonanContractError, match="identity mismatch"):
        ch._lifelong_detail(Runner(), row, "dujeong")


def test_lifelong_disabled_closed_control_is_not_an_application() -> None:
    row = ch._lifelong_page(
        _lifelong_soup("dujeong", dujeong_identity="235"),
        "dujeong",
    )[0]
    detail = BeautifulSoup(
        """
        <div class="lifelongLearningView">
          <strong class="info-title">걸어갑니다. 세계 속으로</strong>
          <div class="pe-content"><ul>
            <li><strong class="subjact">교육기간</strong>
                <span class="con">2026-07-23 ~ 2026-08-31</span></li>
          </ul></div>
          <button class="button_aply" data-lctr-no="235"
                  data-apply-type="APPLY" disabled>모집마감</button>
        </div>
        """,
        "lxml",
    )

    class Runner:
        def soup(self, *_: Any, **__: Any) -> BeautifulSoup:
            return detail

    assert row["reservation_available"] is False
    assert ch._lifelong_detail(Runner(), row, "dujeong") == 0
    assert "application_url" not in row


def _disability_soup(identity: str = "129", status: str = "진행") -> BeautifulSoup:
    return BeautifulSoup(
        f"""
        <table class="pc_view"><thead><tr>
          <th>번호</th><th>상태</th><th>교육명</th><th>교육대상</th>
          <th>교육일시</th><th>정원</th><th>접수기간</th>
        </tr></thead><tbody><tr>
          <td>{identity}</td><td>{status}</td><td>
          <a class="bo_subject" href="/bbs/board.php?bo_table=edu_app&amp;wr_id={identity}">같이요가</a></td>
          <td>천안시민</td><td>2026-08-10 ~ 2026-11-30 10:00 ~ 11:00</td>
          <td>8명 / 8명 (대기: 4명)</td><td>2026-06-15 ~ 2026-07-31</td>
        </tr></tbody></table>
        """,
        "lxml",
    )


def test_disability_parser_and_offering_alias_collapse() -> None:
    latest = ch._disability_page(_disability_soup("129", "진행"))[0]
    old = ch._disability_page(_disability_soup("110", "종료"))[0]
    rows, aliases = ch._collapse_disability_aliases([old, latest])
    assert aliases == 1
    assert rows[0]["raw_fields"]["write_id"] == "129"
    assert rows[0]["branch"] == "천안시장애인평생교육센터"
    assert rows[0]["municipality_code"] == "4413300000"


def _media_soup() -> BeautifulSoup:
    return BeautifulSoup(
        """
        <div class="leehong_web_list"><div class="item">
          <div class="info">
            <p class="ind">뉴미디어클래스</p><p class="title">AI 포토샵</p>
            <p class="sub"><span>접수기간</span>2026-07-23 ~ 2026-08-07</p>
            <p class="sub"><span>교육기간</span>2026-08-12 ~ 2026-08-20</p>
            <p class="sub"><span>교육시간</span>수,목 13:00~16:00</p>
            <p class="sub"><span>교육장소</span>천안시영상미디어센터 3층</p>
            <p class="sub"><span>모집인원</span>9</p>
            <a href="./view.php?idx=160">자세히보기</a>
            <a href="./reg.php?idx=160">신청하기</a>
          </div>
        </div></div>
        """,
        "lxml",
    )


def test_media_parser_discovers_but_does_not_invoke_application_url() -> None:
    row = ch._media_page(_media_soup(), ch.date(2026, 7, 23))[0]
    assert row["provider_course_id"].endswith(":160")
    assert row["branch"] == "천안시영상미디어센터 비채"
    assert row["municipality_code"] == "4413100000"
    assert row["application_url"].startswith(ch.CHEONAN_MEDIA_APPLICATION_ENDPOINT)
    assert row["reservation_available"] is True


def _experience_list_soup(
    rows: list[tuple[str, str, str, str, str, str]],
    *,
    total: int,
    page: int,
    last: int,
) -> BeautifulSoup:
    body = []
    for identity, institution, title, period, selection, status in rows:
        detail = f"/prog/yeyakExprn/yeyak/sub03_01/view.do?exprnNo={identity}"
        status_control = (
            f'<a href="{detail}">{status}</a>'
            if status == "접수하기"
            else f"<span>{status}</span>"
        )
        body.append(
            f"""
            <tr><td>{institution}</td>
              <td><a class="botton_view" href="{detail}">{title}</a></td>
              <td>{period}</td><td>{selection}</td><td>{status_control}</td></tr>
            """
        )
    if not body:
        body.append('<tr><td colspan="5">데이터가 없습니다.</td></tr>')
    return BeautifulSoup(
        f"""
        <div>총 게시물 {total} , 페이지 {page} / {last}</div>
        <table><thead><tr><th>기관구분</th><th>체험명</th><th>체험기간</th>
        <th>선정방식</th><th>접수상태</th></tr></thead>
        <tbody>{''.join(body)}</tbody></table>
        """,
        "lxml",
    )


def _experience_detail_soup(
    identity: str,
    title: str,
    institution: str,
    period: str,
    *,
    location: str = "",
) -> BeautifulSoup:
    location_html = f"<p>장소 : {location}</p>" if location else ""
    return BeautifulSoup(
        f"""
        <form><input id="exprnNo" name="exprnNo" value="{identity}"></form>
        <div class="yeyakView"><strong class="info-title">{title}</strong>
          <div class="pe-content"><ul>
            <li><strong class="subjact">기관 구분</strong><span class="con">{institution}</span></li>
            <li><strong class="subjact">체험기간</strong><span class="con">{period}</span></li>
            <li><strong class="subjact">체험대상</strong><span class="con">어린이</span></li>
            <li><strong class="subjact">체험료</strong><span class="con">무료</span></li>
            <li><strong class="subjact">문의처</strong><span class="con">041-111-2222</span></li>
          </ul></div>
          <div class="progView-bottom-box"><div class="view-content">
            {location_html}<p>문의 041-111-2222 private@example.org</p>
          </div></div>
        </div>
        """,
        "lxml",
    )


def test_experience_parser_locks_scope_and_detail_evidence_controls_district() -> None:
    soup = _experience_list_soup(
        [("558", "어린이안전체험관", "하반기 안전체험", "2026-07-01 ~ 2026-12-31", "승인제", "접수하기")],
        total=1,
        page=1,
        last=1,
    )
    row = ch._experience_page(soup)[0]
    assert row["provider"] == ch.CHEONAN_EXPERIENCE_PROVIDER
    assert row["provider_course_id"].endswith(":558")
    assert row["municipality_code"] == ch.CHEONAN_MUNICIPALITY_CODE
    assert row["domain_category"] == "체험·견학"
    assert row["service_group"] == "체험"
    assert row["service_group_policy"] == "locked"
    assert row["program_type"] == "체험"

    detail = _experience_detail_soup(
        "558",
        "하반기 안전체험",
        "어린이안전체험관",
        "2026-07-01 ~ 2026-12-31",
        location="천안시 서북구 성환읍 성진로 15길",
    )

    class Runner:
        calls: list[str] = []

        def soup(self, url: str, *, parameterized: bool = True) -> BeautifulSoup:
            self.calls.append(url)
            return detail

    assert ch._experience_detail(Runner(), row) == 1
    assert row["municipality_code"] == ch.CHEONAN_SEOBUK_CODE
    assert "서북구" in row["venue"]
    assert "041-111-2222" not in str(row)
    assert "private@example.org" not in str(row)
    assert "application_url" not in row


def test_experience_complete_pagination_empty_sentinel_and_stable_edges() -> None:
    first_row = (
        "601", "천안박물관", "박물관 체험", "2026-08-10 ~ 2026-08-10",
        "선착순", "접수하기",
    )
    second_row = (
        "600", "어린이안전체험관", "안전체험", "2026-09-01 ~ 2026-09-30",
        "승인제", "접수하기",
    )
    pages = {
        1: _experience_list_soup([first_row], total=2, page=1, last=2),
        2: _experience_list_soup([second_row], total=2, page=2, last=2),
        3: _experience_list_soup([], total=2, page=3, last=2),
    }
    details = {
        "601": _experience_detail_soup(
            "601", "박물관 체험", "천안박물관", "2026-08-10 ~ 2026-08-10"
        ),
        "600": _experience_detail_soup(
            "600", "안전체험", "어린이안전체험관", "2026-09-01 ~ 2026-09-30",
            location="천안시 동남구 삼룡동 291-4",
        ),
    }

    class Runner:
        calls: list[tuple[str, int | None]] = []

        def soup(
            self,
            url: str,
            *,
            params: dict[str, int] | None = None,
            parameterized: bool = True,
        ) -> BeautifulSoup:
            if "view.do" in url:
                identity = url.split("exprnNo=", 1)[1]
                self.calls.append(("detail", int(identity)))
                return details[identity]
            page = int((params or {})["pageIndex"])
            self.calls.append(("list", page))
            return pages[page]

    runner = Runner()
    rows, meta = ch._collect_experience(
        runner, ch.date(2026, 8, 5), max_pages=3, detail_limit=2
    )
    assert [row["raw_fields"]["experience_id"] for row in rows] == ["601", "600"]
    assert rows[0]["municipality_code"] == ch.CHEONAN_MUNICIPALITY_CODE
    assert rows[1]["municipality_code"] == ch.CHEONAN_DONGNAM_CODE
    assert meta["source_total"] == meta["current_count"] == 2
    assert meta["sentinel_page"] == 3
    assert meta["stable_boundary_pages"] == [1, 2]
    assert meta["list_requests"] == 5
    assert meta["pagination_complete"] and meta["details_complete"]
    assert [call for call in runner.calls if call[0] == "list"] == [
        ("list", 1), ("list", 2), ("list", 3), ("list", 1), ("list", 2)
    ]


def test_experience_detail_fails_closed_on_identity_or_district_conflict() -> None:
    row = ch._experience_page(
        _experience_list_soup(
            [("558", "어린이안전체험관", "안전체험", "2026-07-01 ~ 2026-12-31", "승인제", "접수하기")],
            total=1,
            page=1,
            last=1,
        )
    )[0]
    wrong_identity = _experience_detail_soup(
        "559", "안전체험", "어린이안전체험관", "2026-07-01 ~ 2026-12-31"
    )

    class Runner:
        def soup(self, *_: Any, **__: Any) -> BeautifulSoup:
            return wrong_identity

    with pytest.raises(ch.CheonanContractError, match="detail identity mismatch"):
        ch._experience_detail(Runner(), row)
    with pytest.raises(ch.CheonanContractError, match="conflicting district"):
        ch._municipality_from_experience_evidence(
            ["천안시 동남구 체험장", "천안시 서북구 안내소"]
        )


def test_pii_sanitizer_removes_phone_email_and_resident_number() -> None:
    value = ch._sanitize("문의 041-123-4567 a@example.org 주민 900101-1234567")
    assert "041-123-4567" not in value
    assert "a@example.org" not in value
    assert "900101-1234567" not in value


@pytest.mark.skipif(
    os.environ.get("RUN_CHEONAN_LIVE_AUDIT") != "1",
    reason="set RUN_CHEONAN_LIVE_AUDIT=1 for two bounded official-source censuses",
)
@pytest.mark.parametrize(
    "owner,provider,url",
    (
        ("integrated", ch.CHEONAN_INTEGRATED_PROVIDER, ch.CHEONAN_INTEGRATED_URL),
        ("library", ch.CHEONAN_LIBRARY_PROVIDER, ch.CHEONAN_LIBRARY_URL),
        ("seongjeong", ch.CHEONAN_SEONGJEONG_PROVIDER, ch.CHEONAN_SEONGJEONG_URL),
        ("dujeong", ch.CHEONAN_DUJEONG_PROVIDER, ch.CHEONAN_DUJEONG_URL),
        ("disability", ch.CHEONAN_DISABILITY_PROVIDER, ch.CHEONAN_DISABILITY_URL),
        ("media", ch.CHEONAN_MEDIA_PROVIDER, ch.CHEONAN_MEDIA_URL),
        ("experience", ch.CHEONAN_EXPERIENCE_PROVIDER, ch.CHEONAN_EXPERIENCE_URL),
    ),
)
def test_live_two_stable_complete_owner_censuses(
    owner: str, provider: str, url: str
) -> None:
    snapshots = []
    for _ in range(2):
        rows, _, meta = ch.collect(
            {"provider": provider, "url": url},
            today="2026-07-23",
            allow_raw_requests_for_tests=True,
            timeout=30,
        )
        assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
        assert meta["pagination_complete"] and meta["details_complete"]
        assert meta["application_endpoints_called"] == 0
        assert len(rows) == meta["returned_count"] == meta["current_count"]
        assert all(
            row["municipality_code"]
            in {"4413000000", "4413100000", "4413300000"}
            for row in rows
        )
        assert all(
            "instructor" not in row and "phone" not in row and "email" not in row
            for row in rows
        )
        snapshots.append(
            (
                meta["source_total"],
                meta["source_current_count"],
                meta["current_count"],
                meta["source_identity_sha256"],
            )
        )
    assert snapshots[0] == snapshots[1]
    baseline = ch.CHEONAN_LIVE_AUDIT_BASELINE.get(owner)
    if baseline:
        assert snapshots[0][0] == baseline["source_total"]
        assert snapshots[0][1] == baseline["source_current_count"]
        assert snapshots[0][2] == baseline["current_count"]
        assert snapshots[0][3] == baseline["sorted_identity_sha256"]
