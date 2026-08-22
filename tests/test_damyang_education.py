from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
import hashlib
import os
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest

from Crawler import municipal_damyang as damyang


@dataclass(frozen=True)
class Target:
    provider: str
    url: str


class DummySession:
    def close(self) -> None:
        pass


def _library_target(**updates: str) -> Target:
    values = {
        "provider": damyang.DAMYANG_LIBRARY_PROVIDER,
        "url": damyang.DAMYANG_LIBRARY_URL,
    }
    values.update(updates)
    return Target(**values)


def _county_target(**updates: str) -> Target:
    values = {
        "provider": damyang.DAMYANG_LIFELONG_PROVIDER,
        "url": damyang.DAMYANG_LIFELONG_URL,
    }
    values.update(updates)
    return Target(**values)


@dataclass(frozen=True)
class LibraryCourse:
    sequence: int
    identity: str
    title: str
    start: str
    end: str
    apply_start: str
    apply_end: str
    status: str
    target: str = "담양군민"
    current: int = 2
    total: int = 10
    wait_current: int = 0
    wait_total: int = 3
    days: str = "수"
    hours: str = "10:00 ~ 12:00"
    venue: str = "시청각실(3층)"


def _library_courses(source: damyang.DamyangLibrarySource) -> list[LibraryCourse]:
    source_index = [row.code for row in damyang.DAMYANG_LIBRARY_SOURCES].index(
        source.code
    )
    status = ("신청하기", "대기자신청하기", "접수전")[source_index]
    apply_start = "2099-07-28" if status == "접수전" else "2099-07-01"
    apply_end = "2099-08-01" if status == "접수전" else "2099-07-30"
    return [
        LibraryCourse(
            2,
            str(1000 + source_index * 10),
            f"{source.menu} 현재 강좌",
            "2099-08-05",
            "2099-09-12",
            apply_start,
            apply_end,
            status,
            current=10 if status == "대기자신청하기" else 2,
            wait_current=1 if status == "대기자신청하기" else 0,
        ),
        LibraryCourse(
            1,
            str(1001 + source_index * 10),
            f"{source.menu} 지난 강좌",
            "2020-01-01",
            "2020-02-01",
            "2019-12-01",
            "2019-12-20",
            "마감",
        ),
    ]


def _library_action(status: str, *, detail: bool = False) -> str:
    if status in {"신청하기", "대기자신청하기"}:
        css = "w_app" if detail or status == "신청하기" else "w_tmp"
        return (
            '<a href="#" onclick="checkLogin(); return false;">'
            f'<span class="{css}">{status}</span></a>'
        )
    css = "w_wait" if status == "접수전" else "w_close"
    return f'<span class="{css}">{status}</span>'


def _library_list_html(
    source: damyang.DamyangLibrarySource,
    courses: list[LibraryCourse],
    page: int,
    *,
    title_drift: bool = False,
) -> str:
    owner = "변경 기관" if title_drift else damyang.DAMYANG_LIBRARY_BRANCH
    if page == 1:
        rows = "".join(
            f"""
            <tr>
              <td>{course.sequence}</td>
              <td><img src="/discarded-{course.identity}.jpg" alt="첨부"></td>
              <td aria-label="강좌명" class="txt_left t_title" scope="row">
                <a href="/lecture.es?mid={source.mid}&amp;act=view&amp;el_seq={course.identity}&amp;nPage=">{escape(course.title)}</a>
              </td>
              <td aria-label="대상">{escape(course.target)}</td>
              <td aria-label="운영기간">{course.start} ~<br>{course.end}<br>{course.days} {course.hours}</td>
              <td aria-label="인터넷접수">{course.apply_start} 10:00 ~<br>{course.apply_end} 18:00</td>
              <td aria-label="신청현황"><span>{course.current}</span> / <span>{course.total}</span><br>
                ( <span>{course.wait_current}</span> / <span>{course.wait_total}</span> )</td>
              <td aria-label="상태">{_library_action(course.status)}</td>
            </tr>
            """
            for course in courses
        )
    else:
        rows = (
            '<tr><td class="nodata" colspan="6">'
            "등록된 자료가 존재하지 않습니다.</td></tr>"
        )
    page_url = damyang.damyang_library_list_url(source.code, page)
    return f"""
      <html><head>
        <title>글쓰기 | 수강 신청 | {source.menu} : {owner}</title>
        <meta property="og:url" content="{escape(page_url)}">
      </head><body>
        <form name="srhForm" method="post" action="/lecture.es?mid={source.mid}">
          <input type="hidden" name="actionUrl" value="/lecture.es">
          <input type="hidden" name="nPage" value="{'' if page == 1 else page}">
          <input type="hidden" name="mid" value="{source.mid}">
          <input type="hidden" name="act" value="list">
          <input type="hidden" name="b_list" value="100">
          <input name="keyWord" value="">
        </form>
        <table class="tstyle_list"><thead><tr>
          <th>연번</th><th>이미지</th><th>강좌명</th><th>대상</th>
          <th>운영기간</th><th>인터넷접수</th>
          <th>신청 / 정원 (대기인원)</th><th>상태</th>
        </tr></thead><tbody>{rows}</tbody></table>
      </body></html>
    """


def _library_detail_html(
    source: damyang.DamyangLibrarySource,
    course: LibraryCourse,
    *,
    status_drift: bool = False,
) -> str:
    status = "마감" if status_drift else course.status
    return f"""
      <html><head><title>글쓰기 | 수강 신청 | {source.menu} : {damyang.DAMYANG_LIBRARY_BRANCH}</title></head>
      <body>
        <script>
          function checkLogin() {{
            alert('로그인 후 이용할 수 있습니다.');
            location.href='/login_search.es?sid=a6';
            return false;
          }}
        </script>
        <form name="insForm" method="post" action="/lecture.es&act=ins">
          <input type="hidden" name="actionUrl" value="/lecture.es">
          <input type="hidden" name="nPage" value="">
          <input type="hidden" name="act" value="list">
          <table class="tstyle_write"><tbody>
            <tr><th>썸네일</th><td><img src="/private-image.jpg"></td></tr>
            <tr><th>강좌명</th><td>{escape(course.title)}</td></tr>
            <tr><th>분기</th><td>여름</td><th>대상</th><td>{escape(course.target)}</td></tr>
            <tr><th>신청기간</th><td>{course.apply_start} 10시 00분 ~ {course.apply_end} 18시 00분</td></tr>
            <tr><th>운영기간</th><td>{course.start}~{course.end}</td></tr>
            <tr><th>강의 시간</th><td>{course.hours}</td></tr>
            <tr><th>회차</th><td>8</td><th>강의 요일</th><td>{course.days}</td></tr>
            <tr><th>교육장소</th><td>{escape(course.venue)}</td><th>계좌제 여부</th><td></td></tr>
            <tr><th>모집인원</th><td>{course.total}명 (대기 {course.wait_total}명)</td>
                <th>신청자</th><td>{course.current}명 (대기 {course.wait_current}명)</td></tr>
            <tr><th>신청방법</th><td>인터넷</td><th>접수상태</th><td>{_library_action(status, detail=True)}</td></tr>
            <tr><th>강의 계획서</th><td><a href="/private.pdf">private.pdf</a></td></tr>
            <tr><th>교육 일정표</th><td><a href="/private-plan">교육일정표 보기</a></td></tr>
            <tr><th>비고</th><td>담당자 061-123-4567 child@example.org 신청자 이름을 적으세요</td></tr>
          </tbody></table>
        </form>
      </body></html>
    """


class LibrarySite:
    def __init__(
        self, *, recheck_drift: bool = False, detail_status_drift: bool = False
    ) -> None:
        self.courses = {
            source.code: _library_courses(source)
            for source in damyang.DAMYANG_LIBRARY_SOURCES
        }
        self.recheck_drift = recheck_drift
        self.detail_status_drift = detail_status_drift
        self.page_calls: dict[tuple[str, int], int] = {}
        self.calls: list[str] = []

    def __call__(self, _session: object, url: str, _timeout: int) -> str:
        self.calls.append(url)
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == damyang.DAMYANG_LIBRARY_HOST
        query = parse_qs(parsed.query, keep_blank_values=True)
        source = next(
            row for row in damyang.DAMYANG_LIBRARY_SOURCES if row.mid == query["mid"][0]
        )
        if query.get("act") == ["view"]:
            identity = query["el_seq"][0]
            course = next(
                row for row in self.courses[source.code] if row.identity == identity
            )
            return _library_detail_html(
                source, course, status_drift=self.detail_status_drift
            )
        page = int(query.get("nPage", ["1"])[0])
        key = (source.code, page)
        self.page_calls[key] = self.page_calls.get(key, 0) + 1
        return _library_list_html(
            source,
            self.courses[source.code],
            page,
            title_drift=(
                self.recheck_drift
                and source.code == "lifelong"
                and page == 1
                and self.page_calls[key] > 1
            ),
        )


def _library_collect(site: LibrarySite, **kwargs: object):
    options: dict[str, object] = {"today": "2099-07-21"}
    options.update(kwargs)
    return damyang.collect_damyang_library_courses(
        _library_target(),
        fetcher=site,
        session_factory=DummySession,
        **options,
    )


def _county_landing() -> str:
    return """
      <html><head><title>담양 평생학습센터</title></head><body>
        <table class="tbl board"><thead class="tblHeader"><tr>
          <td>번호</td><td>강좌명/강사명/신청기간/교육기간</td><td>교육기관</td>
          <td>인원신청/정원</td><td>수강료</td><td>접수현황</td>
        </tr></thead><tbody id="dev-listArea"></tbody></table>
        <ul id="paging-tag" class="pagination" pageCnt="5" rowCnt="10"></ul>
        <script>
          var domainId = 'DOM_0000011';
          var boardId = 'BBS_0000098';
          var contentsSid = '264';
          var menuCd = 'DOM_000001101001000000';
          var strUrl = '/board/getContentsList';
          var detailPageUrl = '/board/detail?domainId=';
          var writePageUrl = '/board/write?domainId=' + '&boardType=register';
          if (nowDate < registerStartDate) {{}}
          if ((registerStartDate < nowDate) && (nowDate < registerEndDate)) {{}}
          if (listItem['tmpField2'] === 'P') {{}}
        </script>
      </body></html>
    """


def _county_rows() -> list[dict[str, object]]:
    return [
        {
            "identity": "901",
            "title": "온라인 현재 강좌",
            "branch": "담양군청",
            "status": "P",
            "method": "P",
            "apply_start": "2099-07-01",
            "apply_end": "2099-07-30",
            "start": "2099-08-01",
            "end": "2099-09-01",
            "current": 3,
            "total": "20",
            "fee": "0",
            "venue": "담빛누리센터 4층 동아리실",
            "target": "담양군민",
            "category": "언어/외국어",
            "wait": "2",
        },
        {
            "identity": "900",
            "title": "서면접수 예정 강좌",
            "branch": "주민자치센터",
            "status": "P",
            "method": "A",
            "apply_start": "2099-07-28",
            "apply_end": "2099-08-03",
            "start": "2099-08-10",
            "end": "2099-10-01",
            "current": 0,
            "total": "15",
            "fee": "10000",
            "venue": "도시재생어울림센터",
            "target": "군민",
            "category": "문화/예술",
            "wait": "",
        },
        {
            "identity": "899",
            "title": "역사 &lt;SNS마케팅&gt; 강좌",
            "branch": "여성회관",
            "status": "E",
            "method": "P",
            "apply_start": "2020-01-01",
            "apply_end": "2020-01-10",
            "start": "2020-02-01",
            "end": "",
            "current": 10,
            "total": "10",
            "fee": "0",
            "venue": "여성회관",
            "target": "군민",
            "category": "기타",
            "wait": "0",
        },
    ]


def _county_list_payload(
    rows: list[dict[str, object]], page: int, *, sentinel_drift: bool = False
) -> dict[str, object]:
    total = len(rows)
    page_rows = rows if page == 1 else ([rows[-1]] if sentinel_drift else [])
    items = []
    for offset, row in enumerate(page_rows):
        items.append(
            {
                "RNUM_REVERSE": total - offset,
                "dataSid": int(str(row["identity"])),
                "cate1Nm": row["branch"],
                "extFeeMoney": row["fee"],
                "tmpField1": row["status"],
                "tmpField2": row["method"],
                "applicant": row["current"],
                "tmpField9": "민감 강사명",
                "RNUM": offset + 1,
                "tmpField7": row["start"],
                "tmpField8": row["end"],
                "tmpField5": row["apply_start"],
                "tmpField6": row["apply_end"],
                "PG_ROW_NUM": total - offset,
                "PG_TOT_CNT": total,
                "dataTitle": row["title"],
                "extFixedNum": row["total"],
            }
        )
    begin = (page - 1) * damyang.DAMYANG_LIFELONG_PAGE_SIZE + 1
    return {
        "RSLT_CD": "0000",
        "PG_TOT_CNT": total,
        "RSLT_DATA": {"boardContentsList": items},
        "RSLT_MSG": "SUCCESS",
        "REQ_DATA": {
            "domainId": damyang.DAMYANG_LIFELONG_DOMAIN_ID,
            "boardId": damyang.DAMYANG_LIFELONG_BOARD_ID,
            "startDate": "",
            "endDate": "",
            "searchCondition1": "",
            "searchCondition2": "",
            "searchKeywordCon": "",
            "searchKeyword": "",
            "ROW_CNT": str(damyang.DAMYANG_LIFELONG_PAGE_SIZE),
            "BEGIN_ROW_IDX": str(begin),
            "CUR_PAGE_IDX": str(page),
            "PAGING_PROCESS_STATUS": "GET_PAGED_DATA",
            "PG_TOT_CNT": total,
        },
    }


def _county_detail_payload(row: dict[str, object]) -> dict[str, object]:
    detail = {
        "cate1Nm": row["branch"],
        "cate2Nm": row["category"],
        "categoryCode1": "OWNER",
        "categoryCode2": "C07",
        "dataContent": "담당자 061-999-9999 private@example.org 자유본문",
        "dataTitle": row["title"],
        "extBank": "비공개 계좌",
        "extBaseTime": "20",
        "extContact": "민감 담당자",
        "extEduTime": "10",
        "extFeeMoney": row["fee"],
        "extFixedNum": row["total"],
        "extInfo": "민감 정보",
        "extPlace": row["venue"],
        "extReadyNum": row["wait"],
        "extTeacher": "강사 소개 private@example.org",
        "extTel": "061-999-9999",
        "extTimeType": "회",
        "tmpField1": row["status"],
        "tmpField2": row["method"],
        "tmpField4": row["target"],
        "tmpField5": row["apply_start"],
        "tmpField6": row["apply_end"],
        "tmpField7": row["start"],
        "tmpField8": row["end"],
        "tmpField9": "민감 강사명",
    }
    return {
        "RSLT_CD": "0000",
        "RSLT_DATA": {
            "boardDetail": {
                "boardContentsDetail": detail,
                "boardContentsFileList": [
                    {"fileNm": "private.pdf", "fileMask": "private-uuid.pdf"}
                ],
            }
        },
        "RSLT_MSG": "SUCCESS",
        "REQ_DATA": {
            "boardId": damyang.DAMYANG_LIFELONG_BOARD_ID,
            "dataSid": str(row["identity"]),
        },
    }


class CountySite:
    def __init__(self, *, sentinel_drift: bool = False) -> None:
        self.rows = _county_rows()
        self.sentinel_drift = sentinel_drift
        self.calls: list[str] = []

    def __call__(self, _session: object, url: str, _timeout: int) -> object:
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == "/board/list":
            return _county_landing()
        if parsed.path == damyang.DAMYANG_LIFELONG_LIST_API_PATH:
            page = int(query["CUR_PAGE_IDX"][0])
            return _county_list_payload(
                self.rows,
                page,
                sentinel_drift=self.sentinel_drift and page == 2,
            )
        if parsed.path == damyang.DAMYANG_LIFELONG_DETAIL_API_PATH:
            identity = query["dataSid"][0]
            return _county_detail_payload(
                next(row for row in self.rows if row["identity"] == identity)
            )
        raise AssertionError(url)


def _county_collect(site: CountySite, **kwargs: object):
    return damyang.collect_damyang_lifelong_courses(
        _county_target(),
        fetcher=site,
        session_factory=DummySession,
        today="2099-07-21",
        **kwargs,
    )


def _all_strings(value: object):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _all_strings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _all_strings(nested)
    elif isinstance(value, str):
        yield value


def test_exact_targets_provider_hash_and_cross_host_owner_correction() -> None:
    digest = hashlib.sha1(damyang.DAMYANG_LIFELONG_URL.encode()).hexdigest()[:8].upper()
    assert damyang.DAMYANG_LIFELONG_PROVIDER == f"MUNI_WWW_DAMYANG_GO_KR_{digest}"
    assert damyang.is_damyang_library_target(_library_target())
    assert damyang.is_damyang_lifelong_target(_county_target())
    assert not damyang.is_target(_library_target(provider="MUNI_WRONG"))
    assert not damyang.is_target(_county_target(url=damyang.DAMYANG_LIFELONG_URL + "#"))
    correction = damyang.DAMYANG_CANDIDATE_AUDIT[
        "CROSS_HOST_A804_CONFIGURATION"
    ]
    assert correction["owner"] == damyang.DAMYANG_BOSEONG_PROVIDER
    assert correction["canonical_url"] == damyang.DAMYANG_BOSEONG_CANONICAL_URL
    assert "보성도서관" in correction["live_title_owner"]


def test_library_union_is_complete_current_and_privacy_allowlisted() -> None:
    site = LibrarySite()
    rows, parser, meta = _library_collect(site)
    assert parser == damyang.DAMYANG_LIBRARY_PARSER
    assert len(rows) == 3
    assert {row["raw_fields"]["source_catalogue"] for row in rows} == {
        "reading_culture",
        "lifelong",
        "humanities",
    }
    assert {row["status"] for row in rows} == {"OPEN", "SCHEDULED"}
    assert all(row["branch"] == damyang.DAMYANG_LIBRARY_BRANCH for row in rows)
    assert all(row["fee"] == "요금 별도 안내" for row in rows)
    assert all(row["venue_name"] for row in rows)
    assert meta["source_rows"] == 6
    assert meta["current_count"] == 3
    assert meta["expired_count"] == 3
    assert meta["required_list_requests"] == 9
    assert meta["list_requests"] == 9
    assert meta["sentinel_pages"] == 3
    assert meta["list_rechecks"] == 3
    assert meta["detail_pages"] == 3
    assert meta["full_snapshot_validated"] is True
    text = " ".join(_all_strings(rows))
    assert "061-123-4567" not in text
    assert "child@example.org" not in text
    assert "private.pdf" not in text
    assert "discarded-" not in text


def test_library_recheck_or_detail_status_drift_invalidates_snapshot() -> None:
    rows, _, meta = _library_collect(LibrarySite(recheck_drift=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "lifelong completeness" in meta["configured_collection_error"]
    rows, _, meta = _library_collect(LibrarySite(detail_status_drift=True))
    assert rows == []
    assert "status mismatch" in meta["configured_collection_error"]


def test_library_same_day_scheduled_status_uses_exact_seoul_time() -> None:
    before_start = datetime(
        2099, 7, 28, 9, 59, tzinfo=ZoneInfo("Asia/Seoul")
    )
    rows, _, meta = _library_collect(LibrarySite(), today=before_start)
    assert len(rows) == 3
    assert meta["snapshot_complete"] is True

    after_start = datetime(
        2099, 7, 28, 10, 1, tzinfo=ZoneInfo("Asia/Seoul")
    )
    rows, _, meta = _library_collect(LibrarySite(), today=after_start)
    assert rows == []
    assert "scheduled datetime mismatch" in meta["configured_collection_error"]


def test_county_json_owner_is_complete_and_application_is_identity_bound() -> None:
    site = CountySite()
    rows, parser, meta = _county_collect(site)
    assert parser == damyang.DAMYANG_LIFELONG_PARSER
    assert len(rows) == 2
    assert {row["branch"] for row in rows} == {"담양군청", "주민자치센터"}
    by_title = {row["title"]: row for row in rows}
    online = by_title["온라인 현재 강좌"]
    offline = by_title["서면접수 예정 강좌"]
    assert online["status"] == "OPEN"
    assert online["reservation_available"] is True
    assert "dataSid=901" in online["application_url"]
    assert offline["status"] == "SCHEDULED"
    assert offline["reservation_available"] is False
    assert offline["application_url"] == ""
    assert online["fee"] == "무료"
    assert offline["fee"] == "10000"
    assert all(row["venue_name"] for row in rows)
    assert meta["source_rows"] == 3
    assert meta["current_count"] == 2
    assert meta["expired_count"] == 1
    assert meta["historical_incomplete_date_count"] == 1
    assert meta["source_page_counts"] == [3]
    assert meta["required_list_requests"] == 3
    assert meta["required_total_page_requests"] == 4
    assert meta["list_rechecks"] == 1
    assert meta["sentinel_pages"] == 1
    assert meta["detail_pages"] == 2
    assert meta["full_snapshot_validated"] is True
    text = " ".join(_all_strings(rows))
    for forbidden in (
        "061-999-9999",
        "private@example.org",
        "private.pdf",
        "민감 강사명",
        "비공개 계좌",
    ):
        assert forbidden not in text


def test_county_sentinel_and_caps_fail_closed() -> None:
    rows, _, meta = _county_collect(CountySite(sentinel_drift=True))
    assert rows == []
    assert "completeness" in meta["configured_collection_error"]
    rows, _, meta = _county_collect(CountySite(), max_pages=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    rows, _, meta = _county_collect(CountySite(), detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_DAMYANG_EDUCATION") != "1",
    reason="set RUN_LIVE_DAMYANG_EDUCATION=1 for official live audit",
)
@pytest.mark.parametrize("target", [_library_target(), _county_target()])
def test_live_damyang_official_owners(target: Target) -> None:
    rows, _parser, meta = damyang.collect_damyang_education(
        target,
        timeout=30,
        max_pages=20,
        detail_limit=100,
        today="2026-07-21",
    )
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["returned_count"] == len(rows)
