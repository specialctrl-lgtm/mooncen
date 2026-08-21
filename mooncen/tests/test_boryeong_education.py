from __future__ import annotations

import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_boryeong as boryeong


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


def _target() -> dict[str, str]:
    return {
        "provider": boryeong.BORYEONG_PROVIDER,
        "url": boryeong.BORYEONG_CANONICAL_URL,
    }


def _life_row(
    identity: str,
    title: str,
    period: str,
    status: str,
    *,
    capacity: str = "12(4)",
) -> str:
    href = f"/life/edu/comp/sub02_02_04/view.do?edu_idx={identity}"
    return f"""
    <tr>
      <td><a href="{href}">{title}</a></td><td>1기</td>
      <td>{period}</td><td>10:00 ~ 12:00</td><td>{capacity}</td>
      <td><a href="{href}">{status}</a></td>
    </tr>
    """


def _life_page(page: int, *, drift: bool = False, post_last_nonempty: bool = False) -> str:
    title = "미래 웹툰 교실" if not drift else "변경된 미래 웹툰 교실"
    rows = ""
    if page == 1:
        rows = _life_row("1001", title, "2026-08-01 ~ 2026-08-20", "접수중")
    elif page == 2:
        rows = _life_row("1000", "지난 교실", "2026-05-01 ~ 2026-05-20", "마감")
    elif page == 3 and post_last_nonempty:
        rows = _life_row("9999", "경계 이탈", "2026-09-01 ~ 2026-09-20", "접수중")
    if not rows:
        rows = '<tr><td colspan="6">데이터가 없습니다</td></tr>'
    return f"""
    <html><body><table><thead><tr>
      <th>과정명</th><th>기수</th><th>교육기간</th><th>교육시간</th>
      <th>접수인원(신청자)</th><th>신청</th>
    </tr></thead><tbody>{rows}</tbody></table>
    <div class="paging">
      <a href="?pageIndex=1">1</a><a href="?pageIndex=2">2</a>
    </div></body></html>
    """


def _life_detail(
    *,
    app_identity: str = "1001",
    title: str = "미래 웹툰 교실",
    venue: str = "보령시평생학습관 3층 강의실",
) -> str:
    return f"""
    <html><body><table><tbody>
      <tr><th>과정명</th><td>{title} - 1기</td></tr>
      <tr><th>강사명</th><td>개인 강사 010-1111-2222</td></tr>
      <tr><th>교육내용</th><td>개인 이메일 test@example.com</td></tr>
      <tr><th>교육기간</th><td>2026-08-01 ~ 2026-08-20</td>
          <th>교육시간</th><td>10:00 ~ 12:00</td></tr>
      <tr><th>교육장소</th><td>{venue}</td>
          <th>접수기간</th><td>2026-07-20 10:00 ~ 2026-07-31 23:59</td></tr>
      <tr><th>접수인원</th><td>12</td><th>신청자</th><td>4</td></tr>
      <tr><th>첨부파일</th><td>개인정보.pdf</td></tr>
    </tbody></table>
    <a class="btn_apply02" href="/life/edu/comp/sub02_02_04/form.do?edu_idx={app_identity}">교육신청</a>
    </body></html>
    """


def _library_row(
    number: int,
    group: str,
    identity: str,
    branch: str,
    title: str,
    status: str,
    *,
    education_period: str,
    actionable: bool,
) -> str:
    detail = (
        "./index.php?g_page=event&amp;m_page=event02&amp;act=lecture_view&amp;"
        f"lgCode={group}&amp;leCode={identity}&amp;siteCode=TOL"
    )
    action = ""
    if actionable:
        action = (
            "<a href=\"./index.php?g_page=event&amp;m_page=event02&amp;"
            "act=lecture_receive_form&amp;"
            f"lgCode={group}&amp;leCode={identity}&amp;siteCode=TOL\">{status}</a>"
        )
    else:
        action = f"<span>{status}</span>"
    result = (
        "<a href=\"./index.php?g_page=event&amp;m_page=event02&amp;"
        "act=lecture_result_view&amp;"
        f"lgCode={group}&amp;leCode={identity}&amp;siteCode=TOL\">접수확인</a>"
    )
    return f"""
    <tr><td>{number}</td><td>{branch}</td><td><a href="{detail}">{title}</a></td>
      <td>성인 10( 3 ) / 4</td>
      <td>2026.07.20 / 10:00 ~ 2026.08.01 / 18:00</td>
      <td>{education_period}</td><td>{action}{result if status != '접수예정' else ''}</td>
    </tr>
    """


def _library_page(page: int, *, post_last_nonempty: bool = False) -> str:
    rows = ""
    if page == 1:
        rows = _library_row(
            3,
            "27",
            "2001",
            "시립",
            "미래 독서 교실",
            "신청하기",
            education_period="2026.08.05 ~ 2026.08.12 수( 10:00 ~ 12:00 )",
            actionable=True,
        )
        rows += _library_row(
            2,
            "27",
            "2002",
            "시립",
            "전자책 구독권",
            "접수마감",
            education_period="2026.08.01 ~ 2026.09.01",
            actionable=False,
        )
    elif page == 2:
        rows = _library_row(
            1,
            "20",
            "2000",
            "죽정",
            "진행 중 역사 교실",
            "접수마감",
            education_period="2026.07.01 ~ 2026.08.02 토( 13:00 ~ 15:00 )",
            actionable=False,
        )
    elif page == 3 and post_last_nonempty:
        rows = _library_row(
            99,
            "27",
            "9999",
            "시립",
            "경계 이탈 교실",
            "신청하기",
            education_period="2026.09.01 ~ 2026.09.20 수( 10:00 ~ 12:00 )",
            actionable=True,
        )
    return f"""
    <html><body><table><thead><tr>
      <th>번호</th><th>분류</th><th>교육명</th><th>대상 정원(대기)/접수현황</th>
      <th>접수기간</th><th>수강기간</th><th>상태</th>
    </tr></thead><tbody>{rows}</tbody></table>
    <div class="paging"><a href="?page=1&amp;g_page=event&amp;m_page=event02">1</a>
      <a href="?page=2&amp;g_page=event&amp;m_page=event02">2</a></div>
    </body></html>
    """


def _library_detail(
    group: str,
    identity: str,
    *,
    app_identity: str | None = None,
    wrong_title: bool = False,
) -> str:
    if identity == "2001":
        title = "미래 독서 교실"
        period = "2026.08.05 ~ 2026.08.12"
        schedule = "수( 10:00 ~ 12:00 )"
        venue = "보령시립도서관 1층 강의실"
        control = "신청하기"
    else:
        title = "진행 중 역사 교실"
        period = "2026.07.01 ~ 2026.08.02"
        schedule = "토( 13:00 ~ 15:00 )"
        venue = "죽정도서관 2층 강의실"
        control = ""
    if wrong_title:
        title = "다른 상세 제목"
    action = ""
    if control:
        target_identity = app_identity or identity
        action = (
            "<a href=\"./index.php?g_page=event&amp;m_page=event02&amp;"
            "act=lecture_receive_form&amp;"
            f"lgCode={group}&amp;leCode={target_identity}&amp;siteCode=TOL\">{control}</a>"
        )
    return f"""
    <html><body><h3>{title}</h3><table><tbody>
      <tr><td>대상</td><td>성인</td><td>강사명</td><td>개인 강사 010-2222-3333</td></tr>
      <tr><td>정원</td><td>13 명</td></tr>
      <tr><td>대상인원</td><td>10 명</td><td>대기인원</td><td>3 명</td></tr>
      <tr><td>재료비</td><td>0 원</td></tr>
      <tr><td>계획서</td><td>개인정보.pdf</td></tr>
    </tbody></table>
    <dl><dd>접수 기간 : 2026.07.20 10:00 ~ 2026.08.01 18:00
      강좌 기간 : {period} 강좌 일시 : {schedule}
      강좌 장소 : {venue} 수업계획안 : 다운로드</dd></dl>{action}
    </body></html>
    """


class FixtureSite:
    def __init__(
        self,
        *,
        life_drift: bool = False,
        post_last_nonempty: bool = False,
        bad_application_identity: bool = False,
        bad_detail_title: bool = False,
    ) -> None:
        self.life_drift = life_drift
        self.post_last_nonempty = post_last_nonempty
        self.bad_application_identity = bad_application_identity
        self.bad_detail_title = bad_detail_title
        self.calls: list[str] = []
        self._counts: dict[str, int] = {}
        self._lock = Lock()

    def fetch(self, _session, url: str, _timeout: int) -> _Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        with self._lock:
            self.calls.append(url)
            count = self._counts.get(url, 0) + 1
            self._counts[url] = count
        if parsed.path == boryeong.BORYEONG_LIFE_LIST_PATH:
            page = int(query.get("pageIndex", ["1"])[0])
            drift = self.life_drift and page == 1 and count > 1
            return _Response(
                url,
                _life_page(
                    page,
                    drift=drift,
                    post_last_nonempty=self.post_last_nonempty,
                ),
            )
        if parsed.path == boryeong.BORYEONG_LIFE_DETAIL_PATH:
            assert query["edu_idx"] == ["1001"]
            return _Response(
                url,
                _life_detail(
                    app_identity="9999" if self.bad_application_identity else "1001"
                ),
            )
        if parsed.path != boryeong.BORYEONG_LIBRARY_PATH:
            raise AssertionError(url)
        action = query.get("act", [""])[0]
        if action == "lecture_view":
            identity = query["leCode"][0]
            return _Response(
                url,
                _library_detail(
                    query["lgCode"][0],
                    identity,
                    app_identity=(
                        "9999"
                        if self.bad_application_identity and identity == "2001"
                        else None
                    ),
                    wrong_title=self.bad_detail_title and identity == "2001",
                ),
            )
        page = int(query.get("page", ["1"])[0])
        return _Response(
            url,
            _library_page(page, post_last_nonempty=self.post_last_nonempty),
        )


def _collect(site: FixtureSite, **kwargs):
    return boryeong.collect_boryeong_education(
        _target(),
        today="2026-07-22",
        max_workers=1,
        session_factory=_Session,
        fetcher=site.fetch,
        **kwargs,
    )


def test_exact_target_and_canonical_identity_contract() -> None:
    assert boryeong.is_target(_target())
    assert boryeong.BORYEONG_CANDIDATE_ID == "MUNI_IR_77425B1A4952"
    assert not boryeong.is_target(
        {"provider": boryeong.BORYEONG_PROVIDER, "url": boryeong.BORYEONG_LIBRARY_URL}
    )
    assert not boryeong.is_target(
        {"provider": "MUNI_WWW_CHUNGNAM_GO_KR_6CB077C7", "url": boryeong.BORYEONG_CANONICAL_URL}
    )
    assert not boryeong.is_target(
        {"provider": boryeong.BORYEONG_PROVIDER, "url": boryeong.BORYEONG_CANONICAL_URL + "?pageIndex=1"}
    )
    assert not boryeong.is_target(
        {"provider": boryeong.BORYEONG_PROVIDER, "url": boryeong.BORYEONG_CANONICAL_URL + "#courses"}
    )


def test_collects_all_pages_details_branches_and_excludes_non_education() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == boryeong.BORYEONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["declared_pages"] == {"life": 2, "library": 2}
    assert meta["source_rows_by_ledger"] == {"life": 2, "library": 3}
    assert meta["education_excluded_count"] == 1
    assert meta["current_source_count_by_ledger"] == {"life": 1, "library": 2}
    assert meta["returned_count"] == 3
    assert meta["pagination_complete"] is True
    assert meta["stable_first_last"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_requests"] == 13
    assert {row["title"] for row in rows} == {
        "미래 웹툰 교실",
        "미래 독서 교실",
        "진행 중 역사 교실",
    }
    assert {row["branch"] for row in rows} == {
        "보령시평생학습관",
        "보령시립도서관",
        "죽정도서관",
    }
    assert {row["status"] for row in rows} == {"OPEN", "CLOSED"}
    open_rows = [row for row in rows if row["status"] == "OPEN"]
    assert len(open_rows) == 2
    assert all(row["reservation_available"] for row in open_rows)
    assert all("9999" not in row["application_url"] for row in rows)
    assert all(row["program_type"] == "교육" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["target"] for row in rows)
    assert all(row["fee"] for row in rows)
    lifelong = next(row for row in rows if row["title"] == "미래 웹툰 교실")
    assert lifelong["target"] == "대상 별도 안내"
    assert lifelong["fee"] == "요금 별도 안내"
    library = next(row for row in rows if row["title"] == "미래 독서 교실")
    assert library["target"] == "성인"
    assert library["fee"] == "0 원"


def test_detail_pii_and_free_form_content_are_not_persisted() -> None:
    rows, _, meta = _collect(FixtureSite())
    assert meta["configured_collection_error"] == ""
    payload = repr(rows)
    assert "010-1111-2222" not in payload
    assert "010-2222-3333" not in payload
    assert "test@example.com" not in payload
    assert "개인정보.pdf" not in payload
    assert all(row["description"] == row["title"] for row in rows)
    assert all("instructor" not in row for row in rows)
    assert all("attachments" not in row for row in rows)


def test_lifelong_detail_preserves_the_official_offsite_facility() -> None:
    listed = boryeong._parse_life_page(
        boryeong.BeautifulSoup(_life_page(1), "html.parser"),
        1,
    )["rows"][0]
    row = boryeong._validate_life_detail(
        listed,
        boryeong.BeautifulSoup(
            _life_detail(venue="남대천 공유주방(수산길 27)"),
            "html.parser",
        ),
    )
    assert row["branch"] == "남대천 공유주방"
    assert row["raw_fields"]["source_detail_venue"] == "남대천 공유주방(수산길 27)"


def test_library_waiting_status_is_scheduled_only_without_application_control() -> None:
    scheduled = boryeong.BeautifulSoup(
        "<tr><td><span>대기중</span></td></tr>", "html.parser"
    ).select_one("tr")
    assert boryeong._library_status(scheduled, "대기중", "22", "1634") == (
        "SCHEDULED",
        "",
    )

    actionable = boryeong.BeautifulSoup(
        """
        <tr><td>
          <a href="./index.php?g_page=event&amp;m_page=event02&amp;act=lecture_receive_form&amp;lgCode=22&amp;leCode=1634&amp;siteCode=TOL">
            신청하기
          </a>
        </td></tr>
        """,
        "html.parser",
    ).select_one("tr")
    with pytest.raises(
        boryeong.BoryeongContractError,
        match="actionable status/control drift",
    ):
        boryeong._library_status(actionable, "대기중", "22", "1634")


@pytest.mark.parametrize(
    ("site", "message"),
    [
        (FixtureSite(post_last_nonempty=True), "post-last page is not empty"),
        (FixtureSite(life_drift=True), "first-page stability failed"),
        (FixtureSite(bad_application_identity=True), "application identity drift"),
        (FixtureSite(bad_detail_title=True), "title identity drift"),
    ],
)
def test_contract_drift_fails_closed(site: FixtureSite, message: str) -> None:
    rows, _, meta = _collect(site)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_page_and_detail_caps_fail_closed() -> None:
    rows, _, meta = _collect(FixtureSite(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "below declared" in meta["configured_collection_error"]

    rows, _, meta = _collect(FixtureSite(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "below required 3" in meta["configured_collection_error"]


def test_rejected_provincial_candidate_is_reported_as_directory_only() -> None:
    rows, _, meta = _collect(FixtureSite())
    assert rows
    assert meta["rejected_candidate_id"] == "MUNI_IR_AE82ACA20618"
    assert meta["rejected_candidate_reason"] == (
        "provincial_municipality_directory_not_course_ledger"
    )
    aliases = repr(meta["excluded_official_sources"])
    assert "virtual_host_aliases_return_identical_city_ledgers" in aliases
    assert "separate_Chungcheongnamdo_Office_of_Education_owner" in aliases


def test_default_request_never_follows_redirects() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def get(self, _url: str, **kwargs: object) -> _Response:
            self.kwargs = kwargs
            return _Response(boryeong.BORYEONG_CANONICAL_URL, "redirect", 302)

    session = RecordingSession()
    response = boryeong._request(session, boryeong.BORYEONG_CANONICAL_URL, 7)

    assert response.status_code == 302
    assert session.kwargs == {"timeout": 7, "allow_redirects": False}
    with pytest.raises(boryeong.BoryeongContractError, match="not followed"):
        boryeong._soup(
            boryeong.BORYEONG_CANONICAL_URL,
            7,
            _Session,
            lambda _session, url, _timeout: _Response(url, "redirect", 302),
        )


@pytest.mark.skipif(
    os.getenv("MOONCEN_LIVE_CRAWL") != "1",
    reason="set MOONCEN_LIVE_CRAWL=1 for the official-site audit",
)
def test_live_boryeong_snapshot_is_complete() -> None:
    rows, _, meta = boryeong.collect_boryeong_education(_target(), timeout=30)
    assert meta["configured_collection_error"] == ""
    assert meta["pagination_complete"] is True
    assert meta["stable_first_last"] is True
    assert meta["snapshot_complete"] is True
    assert meta["detail_pages"] == meta["current_source_count"] == len(rows)
    assert all(row["municipality_code"] == "4418000000" for row in rows)
