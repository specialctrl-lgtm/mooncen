from __future__ import annotations

import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_yeongdong as yeongdong


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def close(self) -> None:
        return None


def _county_target() -> dict[str, str]:
    return {
        "provider": yeongdong.YEONGDONG_COUNTY_PROVIDER,
        "url": yeongdong.YEONGDONG_COUNTY_URL,
    }


def _library_target() -> dict[str, str]:
    return {
        "provider": yeongdong.YEONGDONG_LIBRARY_PROVIDER,
        "url": yeongdong.YEONGDONG_LIBRARY_URL,
    }


def _county_card(
    identity: str,
    category_code: str,
    category: str,
    title: str,
    status: str,
    period: str,
    venue: str,
) -> str:
    return f"""
    <li>
      <div class="cate"><span>{status}</span></div>
      <div class="tit"><span>온라인예약</span><span>유선예약</span></div>
      <strong>{title}</strong>
      <ul class="eduli">
        <li><span>접수기간</span>2026-07-01 09:00~2026-07-31 18:00</li>
        <li><span>교육기간</span>{period}</li>
        <li><span>교육시간</span>월,수 10:00 ~ 12:00</li>
        <li><span>교육장소</span>{venue}</li>
      </ul>
      <div class="edu_btn"><a href="/kr/html/sub05/05090101.html?mode=V&amp;mng_no={identity}&amp;cgubun=&amp;edutype=">상세보기</a></div>
    </li>
    """


def _county_page(
    page: int,
    *,
    drift: bool = False,
    post_last_nonempty: bool = False,
) -> str:
    rows = ""
    if page == 1:
        title = "미래 한글 교실" if not drift else "변경된 미래 한글 교실"
        rows = _county_card(
            "101",
            "ECG01",
            "정보화교육",
            title,
            "접수중",
            "2026-08-03~2026-08-21",
            "영동읍행정복지센터 3층 주민정보화교육장",
        )
        rows += _county_card(
            "100",
            "ECG01",
            "정보화교육",
            "지난 컴퓨터 교실",
            "접수마감",
            "2026-05-01~2026-05-20",
            "영동읍행정복지센터 3층 주민정보화교육장",
        )
    elif page == 2:
        rows = _county_card(
            "99",
            "ECG12",
            "청소년 교육",
            "드론 농구 교실",
            "교육중",
            "2026-05-17~2026-08-30",
            "",
        )
    elif page == 3:
        rows = _county_card(
            "999" if post_last_nonempty else "99",
            "ECG01" if post_last_nonempty else "ECG12",
            "정보화교육" if post_last_nonempty else "청소년 교육",
            "경계 이탈 교실" if post_last_nonempty else "드론 농구 교실",
            "접수중" if post_last_nonempty else "교육중",
            "2026-09-01~2026-09-20" if post_last_nonempty else "2026-05-17~2026-08-30",
            "영동읍행정복지센터" if post_last_nonempty else "",
        )
    return f"""
    <html><head><title>목록 &gt; 교육신청 &gt; 영동군청</title></head><body>
      <div class="edu_wrap"><div class="edu_list"><ul>{rows}</ul></div></div>
      <div id="edu_pop"><div class="list_inner">팝업용 중복 목록</div></div>
      <ul class="pagination">
        <li><a href="?cgubun=&amp;edutype=&amp;GotoPage=1">1</a></li>
        <li><a href="?cgubun=&amp;edutype=&amp;GotoPage=2">2</a></li>
      </ul>
    </body></html>
    """


def _county_detail(
    identity: str,
    *,
    bad_application_identity: bool = False,
    wrong_title: bool = False,
) -> str:
    if identity == "101":
        category_code = "ECG01"
        category = "정보화교육"
        title = "미래 한글 교실"
        status = "접수중"
        period = "2026-08-03~2026-08-21"
        venue = "영동읍행정복지센터 3층 주민정보화교육장"
        body = "개인정보와 첨부파일.pdf"
        control = '<button type="submit">신청하기</button>'
    else:
        category_code = "ECG12"
        category = "청소년 교육"
        title = "드론 농구 교실"
        status = "교육중"
        period = "2026-05-17~2026-08-30"
        venue = ""
        body = "운영장소 영동군 청소년수련관 프로그램 4실 010-9999-8888"
        control = ""
    if wrong_title:
        title = "다른 상세 제목"
    form_identity = "999" if bad_application_identity and identity == "101" else identity
    return f"""
    <html><body><div class="edu_view">
      <form name="wrtForm" method="post" action="?">
        <input type="hidden" name="mode" value="AF">
        <input type="hidden" name="edu_mng_no" value="{form_identity}">
        <input type="hidden" name="mng_no" value="{identity}">
        <input type="hidden" name="cgubun" value="{category_code}">
        <input type="hidden" name="edutype" value="">
        <div class="view_top">
          <div class="cate">{status}</div>
          <div class="thumb"><strong>{title}</strong></div>
          <div class="info"><div class="tit">영동군청 / {category}</div>
            <ul class="eduli">
              <li><span>접수기간</span>2026-07-01 09:00~2026-07-31 18:00</li>
              <li><span>교육일자</span>{period}</li>
              <li><span>교육시간</span>월,수 10:00 ~ 12:00</li>
              <li><span>강사명</span>개인 강사 010-1111-2222</li>
              <li><span>교육장소</span>{venue}</li>
            </ul>
          </div>
        </div>
        <table><thead><tr><th>구분</th><th>예약여부</th><th>비고</th><th>예약현황(현원/총원)</th></tr></thead>
          <tbody>
            <tr><td>온라인예약</td><td>가능</td><td>대기자 접수가능</td><td>1/10 (대기자 0/5)</td></tr>
            <tr><td>유선예약</td><td>가능</td><td>043-740-3185</td><td>2/10</td></tr>
            <tr><td>이메일 접수</td><td>불가능</td><td>private@example.com</td><td>-</td></tr>
            <tr><td>방문 접수</td><td>불가능</td><td>-</td><td>-</td></tr>
          </tbody>
        </table>
        <div class="view_btm">{body}</div>
        <div class="edu_btn2"><a href="#">뒤로</a><a href="?">목록</a>{control}</div>
      </form>
    </div></body></html>
    """


def _library_href(action: str, group: str, identity: str) -> str:
    return (
        "./index.php?g_page=culture&amp;m_page=culture01&amp;"
        f"act={action}&amp;lgCode={group}&amp;leCode={identity}&amp;cate="
    )


def _library_row(
    number: int,
    group: str,
    identity: str,
    title: str,
    status: str,
    *,
    actionable: bool,
    target: str = "성인",
    total: int = 10,
    online: int = 10,
    wait: int = 5,
) -> str:
    control = (
        f'<a href="{_library_href("lecture_receive_form", group, identity)}">{status}</a>'
        if actionable
        else f"<span>{status}</span>"
    )
    result = (
        f'<a href="{_library_href("lecture_result_view", group, identity)}">접수확인</a>'
        if status != "접수예정"
        else ""
    )
    return f"""
    <tr><td>{number}</td><td>전체</td>
      <td><a href="{_library_href('lecture_view', group, identity)}">{title}</a></td>
      <td>{target} {total} / {online} / {wait}</td>
      <td>2026.07.01 / 09:00 ~ 2026.07.31 / 18:00</td>
      <td>{control}{result}</td>
    </tr>
    """


def _library_page(
    page: int,
    *,
    drift: bool = False,
    post_last_nonempty: bool = False,
) -> str:
    rows = ""
    if page == 1:
        title = "미래 독서 교실" if not drift else "변경된 미래 독서 교실"
        rows = _library_row(11, "20", "2001", title, "신청하기", actionable=True)
        rows += _library_row(
            10,
            "21",
            "1999",
            "지난 작은도서관 교실",
            "접수마감",
            actionable=False,
        )
        for number in range(9, 1, -1):
            rows += _library_row(
                number,
                "19",
                str(3000 + number),
                f"사물함 {number}",
                "접수마감",
                actionable=False,
            )
    elif page == 2:
        rows = _library_row(
            1,
            "21",
            "2000",
            "작은도서관 역사 교실",
            "접수마감",
            actionable=False,
            target="초등 3~6학년",
        )
    elif page == 3 and post_last_nonempty:
        rows = _library_row(
            99,
            "20",
            "9999",
            "경계 이탈 교실",
            "신청하기",
            actionable=True,
        )
    return f"""
    <html><body><h3>Total : <strong>11</strong>개 (page : <strong>{page}</strong>/2)</h3>
      <table><thead><tr>
        <th>번호</th><th>분류</th><th>교육명</th><th>대상 정원 / 온라인 / 대기</th>
        <th>접수기간</th><th>상태</th>
      </tr></thead><tbody>{rows}</tbody></table>
      <div class="paging">
        <a href="/front/index.php?page=1&amp;g_page=culture&amp;m_page=culture01">1</a>
        <a href="/front/index.php?page=2&amp;g_page=culture&amp;m_page=culture01">2</a>
      </div>
    </body></html>
    """


def _library_detail(
    group: str,
    identity: str,
    *,
    bad_application_identity: bool = False,
    wrong_title: bool = False,
) -> str:
    if identity == "2001":
        title = "미래 독서 교실"
        target = "성인"
        period = "2026.08.05 ~ 2026.08.12"
        venue = "레인보우영동도서관 2강의실"
        control = "신청하기"
    elif identity == "2000":
        title = "작은도서관 역사 교실"
        target = "초등 3~6학년"
        period = "2026.07.10 ~ 2026.08.20"
        venue = "가족센터 작은도서관 2층 집단상담실"
        control = ""
    else:
        title = "지난 작은도서관 교실"
        target = "성인"
        period = "2026.05.01 ~ 2026.05.20"
        venue = "가족센터 작은도서관"
        control = ""
    if wrong_title:
        title = "다른 상세 제목"
    action = ""
    if control:
        app_identity = "9999" if bad_application_identity else identity
        action = (
            f'<a href="{_library_href("lecture_receive_form", group, app_identity)}">'
            f"{control}</a>"
        )
    return f"""
    <html><body><div class="tit"><h2>{title}</h2></div>
      <table><tbody>
        <tr><th>대상</th><td>{target}</td><th>강사명</th><td>개인 강사 010-2222-3333</td></tr>
        <tr><th>정원</th><td>10 명</td></tr>
        <tr><th>현재 접수인원</th><td>3 명</td></tr>
        <tr><th>대상인원</th><td>10 명</td><th>대기인원</th><td>5 명</td></tr>
        <tr><th>계획서</th><td>개인정보.pdf</td></tr>
      </tbody></table>
      <div class="photos"><ul>
        <li>접수 기간 : 2026.07.01 09:00 ~ 2026.07.31 18:00</li>
        <li>강좌 기간 : {period}</li>
        <li>강좌 일시 : 수( 10:00~12:00 )</li>
        <li>강좌 장소 : {venue}</li>
        <li>수업계획안 : 다운로드</li>
      </ul></div>
      <div class="notice">private@example.com 010-3333-4444</div>{action}
    </body></html>
    """


class FixtureSite:
    def __init__(
        self,
        *,
        county_drift: bool = False,
        library_drift: bool = False,
        county_boundary: bool = False,
        library_boundary: bool = False,
        county_bad_application: bool = False,
        library_bad_application: bool = False,
        county_bad_title: bool = False,
        library_bad_title: bool = False,
    ) -> None:
        self.county_drift = county_drift
        self.library_drift = library_drift
        self.county_boundary = county_boundary
        self.library_boundary = library_boundary
        self.county_bad_application = county_bad_application
        self.library_bad_application = library_bad_application
        self.county_bad_title = county_bad_title
        self.library_bad_title = library_bad_title
        self.calls: list[str] = []
        self._counts: dict[str, int] = {}
        self._lock = Lock()

    def fetch(self, _session, url: str, _timeout: int) -> _Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self._lock:
            self.calls.append(url)
            count = self._counts.get(url, 0) + 1
            self._counts[url] = count
        if parsed.hostname == yeongdong.YEONGDONG_COUNTY_HOST:
            if query.get("mode") == ["V"]:
                identity = query["mng_no"][0]
                return _Response(
                    url,
                    _county_detail(
                        identity,
                        bad_application_identity=self.county_bad_application,
                        wrong_title=self.county_bad_title and identity == "101",
                    ),
                )
            page = int(query.get("GotoPage", ["1"])[0])
            drift = self.county_drift and page == 1 and count > 1
            return _Response(
                url,
                _county_page(
                    page,
                    drift=drift,
                    post_last_nonempty=self.county_boundary,
                ),
            )
        if parsed.hostname != yeongdong.YEONGDONG_LIBRARY_HOST:
            raise AssertionError(url)
        action = query.get("act", [""])[0]
        if action == "lecture_view":
            identity = query["leCode"][0]
            return _Response(
                url,
                _library_detail(
                    query["lgCode"][0],
                    identity,
                    bad_application_identity=(
                        self.library_bad_application and identity == "2001"
                    ),
                    wrong_title=self.library_bad_title and identity == "2001",
                ),
            )
        page = int(query.get("page", ["1"])[0])
        drift = self.library_drift and page == 1 and count > 1
        return _Response(
            url,
            _library_page(
                page,
                drift=drift,
                post_last_nonempty=self.library_boundary,
            ),
        )


def _collect(target: dict[str, str], site: FixtureSite, **kwargs):
    return yeongdong.collect_yeongdong_education(
        target,
        today="2026-07-22",
        max_workers=1,
        session_factory=_Session,
        fetcher=site.fetch,
        **kwargs,
    )


def test_exact_targets_and_stable_candidate_identities() -> None:
    assert yeongdong.is_target(_county_target())
    assert yeongdong.is_target(_library_target())
    assert yeongdong.YEONGDONG_COUNTY_CANDIDATE_ID == "MUNI_IR_362C7F0959ED"
    assert yeongdong.YEONGDONG_LIBRARY_CANDIDATE_ID == "MUNI_IR_D59C473C11D3"
    assert not yeongdong.is_target(
        {
            "provider": yeongdong.YEONGDONG_COUNTY_PROVIDER,
            "url": yeongdong.YEONGDONG_COUNTY_URL + "?mode=L",
        }
    )
    assert not yeongdong.is_target(
        {
            "provider": yeongdong.YEONGDONG_LIBRARY_PROVIDER,
            "url": yeongdong.YEONGDONG_LIBRARY_URL + "&page=1",
        }
    )
    assert not yeongdong.is_target(
        {
            "provider": yeongdong.YEONGDONG_COUNTY_PROVIDER,
            "url": yeongdong.YEONGDONG_LIBRARY_URL,
        }
    )


def test_county_collects_all_pages_current_details_and_exact_branches() -> None:
    rows, parser, meta = _collect(_county_target(), FixtureSite())
    assert parser == yeongdong.YEONGDONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["declared_pages"] == 2
    assert meta["source_rows"] == 3
    assert meta["current_source_count"] == meta["detail_pages"] == len(rows) == 2
    assert meta["source_requests"] == 7
    assert meta["pagination_complete"] is True
    assert meta["stable_first_last"] is True
    assert meta["snapshot_complete"] is True
    assert {row["title"] for row in rows} == {"미래 한글 교실", "드론 농구 교실"}
    assert {row["branch"] for row in rows} == {
        "영동읍행정복지센터",
        "영동군청소년수련관",
    }
    assert {row["status"] for row in rows} == {"OPEN", "CLOSED"}
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["reservation_available"] is True
    assert open_row["raw_fields"]["application_control_verified"] is True
    assert all(row["program_type"] == "교육" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)


def test_library_collects_all_pages_and_excludes_locker_facilities() -> None:
    rows, parser, meta = _collect(_library_target(), FixtureSite())
    assert parser == yeongdong.YEONGDONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["declared_pages"] == 2
    assert meta["declared_total"] == meta["source_rows"] == 11
    assert meta["education_excluded_count"] == 8
    assert meta["education_exclusion_counts"] == {"locker_facility_service": 8}
    assert meta["detail_candidate_count"] == meta["detail_pages"] == 3
    assert meta["current_source_count"] == meta["returned_count"] == len(rows) == 2
    assert meta["source_requests"] == 8
    assert {row["title"] for row in rows} == {
        "미래 독서 교실",
        "작은도서관 역사 교실",
    }
    assert {row["branch"] for row in rows} == {
        "레인보우영동도서관",
        "영동군 가족센터 작은도서관",
    }
    assert all("사물함" not in row["title"] for row in rows)
    assert all("지난" not in row["title"] for row in rows)


def test_detail_pii_and_free_form_content_are_not_persisted() -> None:
    county_rows, _, county_meta = _collect(_county_target(), FixtureSite())
    library_rows, _, library_meta = _collect(_library_target(), FixtureSite())
    assert county_meta["configured_collection_error"] == ""
    assert library_meta["configured_collection_error"] == ""
    payload = repr(county_rows + library_rows)
    assert "010-1111-2222" not in payload
    assert "010-2222-3333" not in payload
    assert "010-3333-4444" not in payload
    assert "private@example.com" not in payload
    assert "개인정보.pdf" not in payload
    assert all(row["description"] == row["title"] for row in county_rows + library_rows)


@pytest.mark.parametrize(
    ("target", "site", "message"),
    [
        (_county_target(), FixtureSite(county_boundary=True), "post-last page is not empty"),
        (_county_target(), FixtureSite(county_drift=True), "first-page stability failed"),
        (
            _county_target(),
            FixtureSite(county_bad_application=True),
            "application identity drift",
        ),
        (_county_target(), FixtureSite(county_bad_title=True), "title identity drift"),
        (_library_target(), FixtureSite(library_boundary=True), "post-last page is not empty"),
        (_library_target(), FixtureSite(library_drift=True), "first-page stability failed"),
        (
            _library_target(),
            FixtureSite(library_bad_application=True),
            "application identity drift",
        ),
        (_library_target(), FixtureSite(library_bad_title=True), "title identity drift"),
    ],
)
def test_contract_drift_fails_closed(
    target: dict[str, str], site: FixtureSite, message: str
) -> None:
    rows, _, meta = _collect(target, site)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


@pytest.mark.parametrize("target", [_county_target(), _library_target()])
def test_page_and_detail_caps_fail_closed(target: dict[str, str]) -> None:
    rows, _, meta = _collect(target, FixtureSite(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "below declared" in meta["configured_collection_error"]

    rows, _, meta = _collect(target, FixtureSite(), detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "below required" in meta["configured_collection_error"]


def test_review_notices_aliases_subsets_and_separate_owner_are_reported() -> None:
    rows, _, meta = _collect(_library_target(), FixtureSite())
    assert rows
    assert meta["review_candidate_decisions"] == {
        "MUNI_IR_3677657B79F2": "instructor_recruitment_notice_not_learner_course_ledger",
        "MUNI_IR_E3BF8DF1EECB": (
            "editorial_recruitment_notice_subset_without_course_identity"
        ),
    }
    decisions = repr(meta["excluded_official_sources"])
    assert yeongdong.YEONGDONG_BOOKING_PORTAL_PROVIDER == "MUNI_WWW_YD21_GO_KR_8C7953BE"
    assert yeongdong.YEONGDONG_BOOKING_PORTAL_CANDIDATE_ID == "MUNI_IR_9ACD39C815DA"
    assert "distinct_same_owner_experience_lodging_facility_catalog" in decisions
    assert "same_owner_virtual_host_aliases_not_independent_ledgers" in decisions
    assert "category_tabs_are_subsets_of_all_programme_ledger" in decisions
    assert "locker_application_is_facility_service_not_education" in decisions
    assert "separate_non_https_regional_centre_owner_not_county_ledger" in decisions


def test_default_session_uses_identified_nonblocked_product_token() -> None:
    session = yeongdong._session()
    try:
        user_agent = session.headers["User-Agent"]
        assert "MoonCenBot/1.0" in user_agent
        assert "https://mooncen.kr" in user_agent
        assert "MooncenMunicipalCrawler" not in user_agent
    finally:
        session.close()


def test_blank_official_venue_uses_category_branch_without_inventing_location() -> None:
    soup = yeongdong.BeautifulSoup(
        '<div class="edu_view"><div class="view_btm">교육내용만 있음</div></div>',
        "html.parser",
    )
    assert yeongdong._county_safe_detail_venue(soup.select_one(".edu_view"), "430") == ""
    assert yeongdong._county_branch("정보화교육", "") == "영동군 정보화교육"


def test_default_request_never_follows_redirects() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def get(self, _url: str, **kwargs: object) -> _Response:
            self.kwargs = kwargs
            return _Response(yeongdong.YEONGDONG_COUNTY_URL, "redirect", 302)

    session = RecordingSession()
    response = yeongdong._request(session, yeongdong.YEONGDONG_COUNTY_URL, 7)

    assert response.status_code == 302
    assert session.kwargs == {"timeout": 7, "allow_redirects": False}
    with pytest.raises(yeongdong.YeongdongContractError, match="not followed"):
        yeongdong._soup(
            yeongdong.YEONGDONG_COUNTY_URL,
            7,
            _Session,
            lambda _session, url, _timeout: _Response(url, "redirect", 302),
        )


@pytest.mark.skipif(
    os.getenv("MOONCEN_LIVE_CRAWL") != "1",
    reason="set MOONCEN_LIVE_CRAWL=1 for the official-site audit",
)
def test_live_yeongdong_sources_are_complete_or_fail_closed() -> None:
    library_rows, _, library_meta = yeongdong.collect_yeongdong_education(
        _library_target(), timeout=30
    )
    assert library_meta["configured_collection_error"] == ""
    assert library_meta["pagination_complete"] is True
    assert library_meta["stable_first_last"] is True
    assert library_meta["snapshot_complete"] is True
    assert library_meta["detail_pages"] == library_meta["detail_candidate_count"]
    assert library_meta["current_source_count"] == len(library_rows)
    assert all(row["municipality_code"] == "4374000000" for row in library_rows)

    county_rows, _, county_meta = yeongdong.collect_yeongdong_education(
        _county_target(), timeout=30
    )
    if county_meta["configured_collection_error"]:
        assert county_rows == []
        assert county_meta["snapshot_complete"] is False
        assert county_meta["returned_count"] == 0
    else:
        assert county_meta["pagination_complete"] is True
        assert county_meta["stable_first_last"] is True
        assert county_meta["snapshot_complete"] is True
        assert county_meta["current_source_count"] == len(county_rows)
