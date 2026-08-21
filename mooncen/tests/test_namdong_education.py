from __future__ import annotations

from datetime import date
import hashlib
import inspect
import os
import ssl
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_namdong as namdong


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
        "provider": namdong.NAMDONG_PROVIDER,
        "url": namdong.NAMDONG_CANONICAL_URL,
    }


def _life_card(
    identity: str,
    title: str,
    *,
    category: str,
    status: str,
    apply_period: str,
    education_period: str,
) -> str:
    return f"""
    <li><div>
      <p class="tit"><a href="/lecture/lectureDetail.do?lecseq={identity}&amp;leccate={category}&amp;sitediv=life&amp;cd=life">{title}</a></p>
      <p class="tag_state">{status}</p>
      <ul class="lec_info">
        <li><span class="wfont">접수 : </span>{apply_period}</li>
        <li><span class="wfont">교육기관 : </span>남동평생학습관</li>
        <li><span class="wfont">교육 : </span>{education_period}</li>
        <li><span class="wfont">수강료 : </span>무료</li>
        <li><span class="wfont">교재비 : </span>무료</li>
        <li><span class="wfont">교육 요일 및 시간 : </span>매주 수 10:00~12:00</li>
      </ul>
    </div></li>
    """


_LIFE_CURRENT = _life_card(
    "1001",
    "남동 미래교실",
    category="101",
    status="접수예정",
    apply_period="2026-08-01 ~ 2026-08-10",
    education_period="2026-08-15 ~ 2026-09-01",
)
_LIFE_EXPIRED = _life_card(
    "1000",
    "종료된 남동교실",
    category="101",
    status="접수마감",
    apply_period="2026-05-01 ~ 2026-05-10",
    education_period="2026-06-01 ~ 2026-06-20",
)
_LIFE_CANCELLED = _life_card(
    "1061",
    "[폐강] 남동 시민교실",
    category="106",
    status="접수마감",
    apply_period="2026-06-01 ~ 2026-06-10",
    education_period="2026-07-01 ~ 2026-08-20",
)


def _life_list_html(category: str, page: int, *, clamp_drift: bool = False) -> str:
    data = {
        "101": _LIFE_CURRENT + _LIFE_EXPIRED,
        "106": _LIFE_CANCELLED,
    }.get(category, "")
    if not data:
        return '<html><body><div class="board_list"><p class="nodata">등록된 교육이 없음</p></div></body></html>'
    if page > 1 and clamp_drift:
        return '<html><body><div class="board_list"><p class="nodata">등록된 교육이 없음</p></div></body></html>'
    return f"""
    <html><body>
      <ul class="lecList">{data}</ul>
      <div class="paging">
        <span class="num select">1</span>
        <a href="?nowPage=1">1</a>
      </div>
    </body></html>
    """


def _life_detail_html() -> str:
    return """
    <html><body><div id="detail_con"><div class="board_view">
      <div class="title">남동 미래교실</div>
      <div class="data_list">
        <dl><dt>교육기관</dt><dd>남동평생학습관</dd></dl>
        <dl><dt>교육대상</dt><dd>성인</dd></dl>
        <dl><dt>접수방법</dt><dd>온라인</dd></dl>
        <dl><dt>접수기간</dt><dd>2026-08-01 09:00 ~ 2026-08-10 18:00</dd></dl>
        <dl><dt>교육기간</dt><dd>2026-08-15 ~ 2026-09-01</dd></dl>
        <dl><dt>신청정원</dt><dd>온라인 : 20 명 대기 : 5 명</dd></dl>
        <dl><dt>교육장소</dt><dd>남동구평생학습관 3층 배움실302</dd></dl>
        <dl><dt>문의전화</dt><dd>032-123-4567</dd></dl>
        <dl><dt>강사</dt><dd>테스트 강사</dd></dl>
      </div>
      <div class="btnBox"><a href="/lecture/lectureList.do">목록</a></div>
    </div></div></body></html>
    """


def _library_card(
    identity: str,
    title: str,
    *,
    reception: str,
    event: str,
    apply_period: str,
    education_period: str,
    capacity: str = "3 / 10",
    waitlist: str = "0 / 5",
) -> str:
    return f"""
    <li><a href="#this" name="title">
      <div class="eventListCon"><h3>{title}</h3><div>
        <dl><dt>접수기간</dt><dd>{apply_period}</dd></dl>
        <dl><dt>교육기간</dt><dd>{education_period}</dd></dl>
        <dl><dt>교육대상</dt><dd>초등학생 10명</dd></dl>
        <dl><dt>접수/정원</dt><dd>{capacity}</dd></dl>
        <dl><dt>예비접수/예비정원</dt><dd>{waitlist}</dd></dl>
      </div></div>
      <div class="eventListBtn"><p>{reception}</p><p>{event}</p></div>
      <input id="IDX" type="hidden" value="{identity}">
    </a></li>
    """


_LIBRARY_OPEN = _library_card(
    "2001",
    "책으로 만나는 과학",
    reception="접수진행",
    event="행사대기",
    apply_period="2026-07-01 10:00 ~ 2026-08-01 18:00",
    education_period="2026-08-05 10:00 ~ 2026-08-12 12:00",
)
_LIBRARY_EXPIRED = _library_card(
    "2000",
    "종료된 독서교실",
    reception="접수종료",
    event="행사종료",
    apply_period="2026-05-01 10:00 ~ 2026-05-10 18:00",
    education_period="2026-06-01 10:00 ~ 2026-06-10 12:00",
)
_LIBRARY_SCHEDULED = _library_card(
    "3001",
    "여름 인문학",
    reception="접수대기",
    event="행사대기",
    apply_period="2026-08-01 10:00 ~ 2026-08-05 18:00",
    education_period="2026-08-10 10:00 ~ 2026-08-20 12:00",
    capacity="0 / 10",
)


def _library_list_html(
    catalogue: namdong.LibraryCatalogue,
    branch_code: str,
    page: int,
    *,
    nonempty_sentinel: bool = False,
) -> str:
    rows = ""
    if catalogue.key == "program" and branch_code == "172" and page == 1:
        rows = _LIBRARY_OPEN + _LIBRARY_EXPIRED
    elif catalogue.key == "event" and branch_code == "272" and page == 1:
        rows = _LIBRARY_SCHEDULED
    elif nonempty_sentinel and catalogue.key == "program" and branch_code == "172" and page == 2:
        rows = _LIBRARY_OPEN
    tabs = "".join(
        f'<li class="{"active" if code == branch_code else ""}"><a href="#this" name="libClick">{name}</a><input id="AGENCY_CD" value="{code}"></li>'
        for code, name in catalogue.branches
    )
    paging = (
        '<div class="paging"><a class="focus" href="#none" onclick="fn_movePage(\'1\')">1</a></div>'
        if rows
        else '<div class="paging"></div>'
    )
    return f"""
    <html><body>
      <ul class="library-tabs">{tabs}</ul>
      <form name="frm"><input name="AGENCY_CD" value="{branch_code}"></form>
      <div class="eventListBox"><ul>{rows}</ul></div>
      {paging}
    </body></html>
    """


def _library_detail_html(
    catalogue: namdong.LibraryCatalogue,
    branch_code: str,
    identity: str,
    *,
    broken_handler: bool = False,
) -> str:
    if identity == "2001":
        title = "책으로 만나는 과학"
        apply_period = "2026-07-01 10:00 ~ 2026-08-01 18:00"
        education_period = "2026-08-05 10:00 ~ 2026-08-12 12:00"
        state = "접수중 행사대기"
        button = '<a class="btn2" href="/ndglib/usr/member/memberLogin.do?mnid=mn07&amp;mnidx=154">로그인</a>'
    else:
        title = "여름 인문학"
        apply_period = "2026-08-01 10:00 ~ 2026-08-05 18:00"
        education_period = "2026-08-10 10:00 ~ 2026-08-20 12:00"
        state = "접수대기 행사대기"
        button = '<a class="btn2" href="#">접수대기</a>'
    application_path = "/wrong/write.do" if broken_handler else catalogue.application_path
    return f"""
    <html><body><div class="subContentsArea">
      <table class="tbView"><tbody>
        <tr><th>행사명</th><td>{title}</td></tr>
        <tr><th>접수기간</th><td>{apply_period}</td><th>강좌상태</th><td>{state}</td></tr>
        <tr><th>대상</th><td>초등학생 10명</td><th>장소</th><td>도서관 프로그램실</td></tr>
        <tr><th>교육기간</th><td>{education_period}</td></tr>
        <tr><th>교육시간</th><td>수 10:00~12:00</td></tr>
        <tr><th>정원</th><td>10</td><th>예비정원</th><td>5</td></tr>
        <tr><th>강사명</th><td>개인 강사명</td><th>문의</th><td>032-123-4567</td></tr>
        <tr><th>수강료</th><td>0</td></tr>
      </tbody></table>
      {button}<a class="btn1" href="{catalogue.list_path}">목록</a>
    </div>
    <script>
      var ac = '{branch_code}';
      function fn_openBoardApply(obj) {{
        var url = '{application_path}';
        comSubmit.addParam("IDX", obj.parent().find("#IDX").val());
        comSubmit.addParam("PARENTS_IDX", obj.parent().find("#IDX").val());
        comSubmit.addParam("PARENTS_AGENCY_CD", obj.parent().find("#AGENCY_CD").val());
      }}
    </script></body></html>
    """


class FixtureSite:
    def __init__(
        self,
        *,
        clamp_drift: bool = False,
        nonempty_library_sentinel: bool = False,
        broken_handler: bool = False,
    ) -> None:
        self.clamp_drift = clamp_drift
        self.nonempty_library_sentinel = nonempty_library_sentinel
        self.broken_handler = broken_handler

    def fetch(self, _session, url: str, _timeout: int) -> _Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.hostname == namdong.NAMDONG_BIZ_HOST:
            if parsed.path == namdong.NAMDONG_LIFE_DETAIL_PATH:
                assert query.get("lecseq") == ["1001"]
                return _Response(url, _life_detail_html())
            category = query["leccate"][0]
            page = int(query.get("nowPage", ["1"])[0])
            return _Response(
                url,
                _life_list_html(category, page, clamp_drift=self.clamp_drift),
            )

        catalogue = next(
            item
            for item in namdong.NAMDONG_LIBRARY_CATALOGUES
            if parsed.path in {item.list_path, item.detail_path}
        )
        branch = query["AGENCY_CD"][0]
        if parsed.path == catalogue.detail_path:
            identity = query["IDX"][0]
            return _Response(
                url,
                _library_detail_html(
                    catalogue,
                    branch,
                    identity,
                    broken_handler=self.broken_handler,
                ),
            )
        page = int(query.get("pageNo", ["1"])[0])
        return _Response(
            url,
            _library_list_html(
                catalogue,
                branch,
                page,
                nonempty_sentinel=self.nonempty_library_sentinel,
            ),
        )


def _collect(site: FixtureSite, **kwargs):
    return namdong.collect_namdong_education(
        _target(),
        today="2026-07-22",
        max_pages=100,
        detail_limit=20,
        session_factory=_Session,
        fetcher=site.fetch,
        sleeper=lambda _seconds: None,
        **kwargs,
    )


def test_exact_canonical_target_only() -> None:
    assert namdong.is_namdong_education_target(_target())
    assert not namdong.is_namdong_education_target(
        {**_target(), "provider": "MUNI_WRONG"}
    )
    assert not namdong.is_namdong_education_target(
        {**_target(), "url": namdong.NAMDONG_CANONICAL_URL + "&nowPage=1"}
    )
    assert not namdong.is_namdong_education_target(
        {**_target(), "url": namdong.NAMDONG_CANONICAL_URL + "#courses"}
    )
    assert not namdong.is_namdong_education_target(
        {
            **_target(),
            "url": "https://biz.namdong.go.kr/lecturelll/lectureList.do?cd=life&leccate=101&sitediv=life",
        }
    )


def test_verified_intermediate_is_embedded_without_unsafe_tls_bypass() -> None:
    der = ssl.PEM_cert_to_DER_cert(namdong.NAMDONG_AIA_INTERMEDIATE_PEM)
    assert hashlib.sha256(der).hexdigest() == namdong.NAMDONG_AIA_INTERMEDIATE_SHA256
    source = inspect.getsource(namdong)
    assert "verify=False" not in source
    assert "CERT_NONE" not in source
    assert "check_hostname = False" not in source


def test_waf_403_refreshes_the_verified_session_before_retry() -> None:
    sessions: list[_Session] = []
    attempts = 0

    def factory() -> _Session:
        session = _Session()
        sessions.append(session)
        return session

    def fetch(_session: _Session, url: str, _timeout: int) -> _Response:
        nonlocal attempts
        attempts += 1
        return _Response(url, "<html><body>ok</body></html>", 403 if attempts == 1 else 200)

    requester = namdong._Requester(
        factory(),
        fetch,
        30,
        lambda _seconds: None,
        session_factory=factory,
    )
    try:
        soup = requester.soup(
            namdong._life_detail_url("106", "34847"),
            host=namdong.NAMDONG_BIZ_HOST,
        )
        assert soup.get_text(strip=True) == "ok"
        assert requester.waf_retry_count == 1
        assert requester.session_refresh_count == 1
        assert len(sessions) == 2
    finally:
        requester.close()


def _listed_open_lifelong_row() -> dict[str, object]:
    return {
        "identity": "1001",
        "title": "남동 미래교실",
        "category": "101",
        "category_label": "인생사계학교",
        "status": "OPEN",
        "status_raw": "접수중",
        "institution": "남동평생학습관",
        "apply_start": date(2026, 8, 1),
        "apply_end": date(2026, 8, 10),
        "start": date(2026, 8, 15),
        "end": date(2026, 9, 1),
        "apply_period": "2026-08-01 ~ 2026-08-10",
        "education_period": "2026-08-15 ~ 2026-09-01",
        "list_page": 1,
        "raw_url": namdong._life_detail_url("101", "1001"),
    }


def test_structured_open_lifelong_course_without_control_is_kept_closed() -> None:
    soup = BeautifulSoup(_life_detail_html(), "html.parser")

    row = namdong._life_detail(
        _listed_open_lifelong_row(),
        soup,
        date(2026, 8, 5),
    )

    assert row["title"] == "남동 미래교실"
    assert row["program_type"] == "교육"
    assert row["status"] == "CLOSED"
    assert row["application_url"] == ""
    assert row["application_type"] == "INFO_ONLY"
    assert row["reservation_available"] is False
    assert row["raw_fields"]["source_status"] == "접수중"
    assert row["raw_fields"]["application_control_contract"] == (
        "official_open_without_application_control_conservative_closed"
    )


def test_lifelong_application_url_requires_visible_identity_bound_anchor() -> None:
    html = _life_detail_html().replace(
        '<div class="btnBox"><a href="/lecture/lectureList.do">목록</a></div>',
        '<div class="btnBox">'
        '<a href="/lecture/lectureApply.do?lecseq=1001">신청</a>'
        '<a href="/lecture/lectureList.do">목록</a>'
        "</div>",
    )

    row = namdong._life_detail(
        _listed_open_lifelong_row(),
        BeautifulSoup(html, "html.parser"),
        date(2026, 8, 5),
    )

    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["application_url"] == (
        "https://biz.namdong.go.kr/lecture/lectureApply.do?lecseq=1001"
    )


def test_notice_like_detail_still_fails_before_no_control_fallback() -> None:
    notice = BeautifulSoup(
        """
        <html><body><div id="detail_con"><div class="board_view">
          <div class="title">평생학습관 휴관 안내</div>
          <div class="content">시설 이용 공지입니다.</div>
          <div class="btnBox"><a href="/lecture/lectureList.do">목록</a></div>
        </div></div></body></html>
        """,
        "html.parser",
    )
    listed = {**_listed_open_lifelong_row(), "title": "평생학습관 휴관 안내"}

    with pytest.raises(namdong.NamdongContractError, match="detail fields missing"):
        namdong._life_detail(listed, notice, date(2026, 8, 5))


def test_complete_snapshot_covers_both_owners_and_omits_pii() -> None:
    rows, parser, meta = _collect(FixtureSite())

    assert parser == namdong.NAMDONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["source_rows"] == 6
    assert meta["source_counts"] == {"lifelong": 3, "library": 3}
    assert meta["current_source_count"] == 3
    assert meta["expired_count"] == 2
    assert meta["cancelled_count"] == 1
    assert meta["detail_pages"] == meta["returned_count"] == 3
    assert meta["status_counts"] == {"SCHEDULED": 2, "OPEN": 1}
    assert meta["branch_counts"] == {
        "남동구평생학습관": 1,
        "남동논현도서관": 1,
        "소래도서관": 1,
    }
    assert meta["lifelong_category_counts"] == {
        "101": 2,
        "102": 0,
        "103": 0,
        "104": 0,
        "106": 1,
        "107": 0,
    }
    assert len(meta["library_catalogue_counts"]) == 11
    assert len(meta["excluded_official_sources"]) >= 7

    assert [row["raw_fields"]["identity"] for row in rows] == [
        "2001",
        "3001",
        "1001",
    ]
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["reservation_available"] is True
    assert open_row["application_url"] == open_row["raw_url"]
    assert open_row["branch"] == "남동논현도서관"
    assert open_row["venue"] == "도서관 프로그램실"
    assert open_row["raw_fields"]["application_control_contract"] == (
        "identity_detail+authenticated_identity_bound_write_handler"
    )
    assert all(row["description"] == row["title"] for row in rows)
    assert "032-123-4567" not in repr(rows)
    assert "개인 강사명" not in repr(rows)
    assert "테스트 강사" not in repr(rows)


def test_fail_closed_when_page_budget_cannot_prove_all_ledgers() -> None:
    rows, _, meta = namdong.collect_namdong_education(
        _target(),
        today="2026-07-22",
        max_pages=1,
        detail_limit=20,
        session_factory=_Session,
        fetcher=FixtureSite().fetch,
        sleeper=lambda _seconds: None,
    )

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["pagination_complete"] is False
    assert "max_pages 1" in meta["configured_collection_error"]


def test_lifelong_post_last_clamp_drift_suppresses_snapshot() -> None:
    rows, _, meta = _collect(FixtureSite(clamp_drift=True))

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "post-last clamp changed" in meta["configured_collection_error"]


def test_library_nonempty_post_last_page_suppresses_snapshot() -> None:
    rows, _, meta = _collect(FixtureSite(nonempty_library_sentinel=True))

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "post-last page is not empty" in meta["configured_collection_error"]


def test_open_library_course_requires_audited_identity_handler() -> None:
    rows, _, meta = _collect(FixtureSite(broken_handler=True))

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "identity-bound application handler changed" in meta[
        "configured_collection_error"
    ]


@pytest.mark.skipif(
    os.getenv("MOONCEN_LIVE_CRAWL") != "1",
    reason="opt-in live crawl",
)
def test_live_namdong_complete_snapshot() -> None:
    rows, parser, meta = namdong.collect_namdong_education(
        _target(),
        today="2026-07-22",
        timeout=40,
        max_pages=400,
        detail_limit=200,
    )

    assert parser == namdong.NAMDONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] >= 2187
    assert meta["lifelong_category_counts"]["101"] >= 553
    assert meta["lifelong_category_counts"]["106"] >= 190
    assert sum(meta["library_catalogue_counts"].values()) >= 1443
    assert meta["cancelled_count"] >= 1
    assert meta["returned_count"] == len(rows)
    assert all(row["end_date"] >= "2026-07-22" for row in rows)
    assert all(row["municipality_code"] == "2820000000" for row in rows)
    assert not any("남동구도시관리공단" in row["branch"] for row in rows)
