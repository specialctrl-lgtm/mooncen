from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from html import escape
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_tongyeong as tongyeong


@dataclass
class DummySession:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class Response:
    def __init__(self, html: str, url: str) -> None:
        self.status_code = 200
        self.headers = {"Content-Type": "text/html;charset=UTF-8"}
        self.content = html.encode("utf-8")
        self.url = url


def _request_key(url: str) -> tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.hostname or "",
        parsed.path,
        tuple(sorted((key, tuple(values)) for key, values in query.items())),
    )


class FixtureRequester:
    def __init__(self) -> None:
        self.pages: dict[
            tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]], str
        ] = {}
        self.variants: dict[
            tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]], list[str]
        ] = {}
        self.counts: Counter[Any] = Counter()
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    def add(self, url: str, html: str) -> None:
        self.pages[_request_key(url)] = html

    def mutate(self, url: str, *html: str) -> None:
        self.variants[_request_key(url)] = list(html)

    def __call__(
        self,
        session: Any,
        url: str,
        timeout: int,
        headers: Mapping[str, str] | None,
    ) -> Response:
        del session, timeout
        self.calls.append((url, dict(headers or {})))
        key = _request_key(url)
        index = self.counts[key]
        self.counts[key] += 1
        if key in self.variants:
            values = self.variants[key]
            html = values[min(index, len(values) - 1)]
        else:
            if key not in self.pages:
                raise AssertionError(f"unexpected request (possibly private): {url}")
            html = self.pages[key]
        return Response(html, url)


def _target(provider: str, url: str) -> dict[str, str]:
    return {"provider": provider, "url": url}


def _gne_pager() -> str:
    return """
      <div class="paging">
        <a href="?mid=b20402000000&amp;cate_no=10&amp;page=1"
           onclick="goPage('1'); return false;">1</a>
      </div>
    """


def _gne_list_row(
    identity: str,
    title: str,
    status: str,
    apply_start: str,
    apply_end: str,
    start: str,
    end: str,
    schedule: str,
    *,
    category: str = "평생교육",
    target: str = "통영시민",
    current: int = 2,
    capacity: int = 20,
    wait_current: int = 0,
    wait_total: int = 5,
) -> str:
    href = (
        "/usr_gne/lec_v.es?mid=b20402000000&amp;"
        f"gno={identity}&amp;cate_no=10"
    )
    return f"""
      <tr>
        <td>{escape(category)}</td>
        <td><a href="{href}"><span>[무료] {escape(title)}</span><br>
          ㆍ모집기간 : {apply_start} 09:00 ~ {apply_end} 18:00<br>
          ㆍ학습기간 : {start} ~ {end}<br>
          ㆍ교육요일/시간 : {escape(schedule)}</a></td>
        <td>{escape(target)}</td>
        <td>모집 {current}/{capacity}<br>온라인 {current}/{capacity}<br>
          후보 {wait_current}/{wait_total}</td>
        <td><img alt="온라인접수"><img alt="전화접수"></td>
        <td><img alt="{status}"><a href="{href}">상세보기</a></td>
      </tr>
    """


def _gne_list_html(rows: str, *, empty: bool = False) -> str:
    body = '<tr><td colspan="6">데이터가 없습니다.</td></tr>' if empty else rows
    return f"""
      <html><body>
        <table>
          <thead><tr><th>과정</th><th>강좌명</th><th>학습대상</th>
            <th>정원</th><th>접수방법</th><th>상태</th></tr></thead>
          <tbody>{body}</tbody>
        </table>
        {_gne_pager()}
      </body></html>
    """


def _gne_detail_html(
    identity: str,
    title: str,
    status: str,
    apply_start: str,
    apply_end: str,
    start: str,
    end: str,
    schedule: str,
    *,
    target: str = "통영시민",
    venue: str = "3층 강의실1",
    current: int = 2,
    capacity: int = 20,
    wait_current: int = 0,
    wait_total: int = 5,
) -> str:
    active = status == "모집중"
    control = (
        f"<button onclick=\"requestStudent('{identity}','');return false;\">"
        "수강신청</button>"
        if active
        else ""
    )
    private_form = (
        '<form id="childForm" action="/usr_gne/lec_reqin.es">'
        '<input name="applicantName" value="홍길동">'
        '<input name="phone" value="010-1234-5678"></form>'
        if active
        else ""
    )
    register = (
        "/usr_gne/register.es?mid=b20402000000&amp;category=agree&amp;"
        f"gno={identity}&amp;cate_no=10&amp;bunru_no=251&amp;"
        f"mozip_end_date={apply_end.replace('-', '')}1800"
    )
    fields = [
        ("*강좌명", title, True),
        ("교육대상", target, True),
        ("진행상태", f'<img alt="{status}">', True),
        ("강사", "민감 강사명", True),
        ("초빙강사", "민감 초빙강사", True),
        ("담당자명", "민감 담당자", True),
        ("담당자 연락처", "010-1234-5678", True),
        ("*모집기간", f"{apply_start} 09:00 ~ {apply_end} 18:00", True),
        ("*교육기간", f"{start} ~ {end}", True),
        (
            "접수방법",
            '<img alt="온라인접수"><img alt="전화접수">',
            True,
        ),
        ("교육일시", schedule, True),
        ("강의장소", venue, True),
        ("재료비", "무료", True),
        ("수강료", "무료", True),
        ("강의소개", "전화 055-123-4567 자유본문", True),
        ("첨부파일", "private-plan.pdf", True),
    ]
    body = "".join(
        f'<td class="boardText1">{label}</td><td>{value}</td>'
        for label, value, _ in fields
    )
    body += (
        f"<td>모집인원</td><td>{current} / {capacity}</td>"
        f"<td>온라인접수</td><td>{current}/{capacity}</td>"
        f"<td>후보</td><td>{wait_current}/{wait_total}</td>"
    )
    return f"""
      <html><body><table><tbody><tr>{body}</tr></tbody></table>
        <script>var registerUrl='{register}';</script>
        {control}{private_form}
      </body></html>
    """


def _gne_fixture() -> FixtureRequester:
    requester = FixtureRequester()
    courses = [
        ("101", "열린 독서교실", "모집중", "2026-07-01", "2026-07-31", "2026-08-01", "2026-08-10", "토/10:00~12:00"),
        ("102", "가을 글쓰기", "준비중", "2026-08-01", "2026-08-10", "2026-09-01", "2026-09-10", "화/14:00~16:00"),
        ("103", "절기 따라 걷기", "교육중", "2026-06-01", "2026-06-10", "2026-07-01", "2026-08-01", "월/09:00~11:00"),
    ]
    requester.add(
        tongyeong.tongyeong_gne_page_url(1),
        _gne_list_html("".join(_gne_list_row(*course) for course in courses)),
    )
    requester.add(
        tongyeong.tongyeong_gne_page_url(2),
        _gne_list_html("", empty=True),
    )
    for course in courses:
        requester.add(
            tongyeong.tongyeong_gne_detail_url(course[0]),
            _gne_detail_html(*course),
        )
    return requester


def _city_row(
    identity: int,
    title: str,
    branch: str,
    status: str,
    apply_start: str,
    apply_end: str,
    start: str,
    end: str,
    application: bool = False,
) -> str:
    if application:
        status_html = (
            f'<a href="?amode=apply&amp;idx={identity}">{status}</a>'
        )
    elif status:
        status_html = f'<a href="#" onclick="alert(\'closed\')">{status}</a>'
    else:
        status_html = ""
    return f"""
      <tr><th>[{escape(branch)}]</th>
        <td><a href="?amode=view&amp;idx={identity}&amp;">{escape(title)}</a></td>
        <td>20 / 5 명</td>
        <td>{apply_start} 09시 ~ {apply_end} 18시</td>
        <td>{start} ~ {end}</td><td>온라인접수</td><td>무료</td>
        <td>{status_html}</td></tr>
    """


def _city_list_html(
    rows: str, *, total: int, reported: int, last: int
) -> str:
    return f"""
      <html><body><p>총 {total} 건이 있습니다. ({reported} /{last} 페이지)</p>
        <table><thead><tr><th>기관</th><th>강좌명</th>
          <th>모집/대기<br>인원</th><th>접수기간</th><th>강좌기간</th>
          <th>접수방법</th><th>수강료</th><th>상태</th></tr></thead>
          <tbody>{rows}</tbody></table>
      </body></html>
    """


def _city_detail_html(
    identity: int,
    title: str,
    branch: str,
    apply_start: str,
    apply_end: str,
    start: str,
    end: str,
    *,
    include_time: bool,
) -> str:
    time_row = (
        "<tr><th>교육시간</th><td>매주 목 18:30 ~ 21:30</td></tr>"
        if include_time
        else ""
    )
    return f"""
      <html><body><h1 class="h1">[{escape(branch)}]
        <span>{escape(title)}</span></h1>
        <table><tbody>
          <tr><th>교육기간</th><td>{start} ~ {end}</td></tr>{time_row}
          <tr><th>접수기간</th><td>{apply_start} 09시 ~ {apply_end} 18시</td></tr>
          <tr><th>수강료</th><td>무료</td></tr>
          <tr><th>모집대상</th><td>통영시민 우선</td></tr>
          <tr><th>접수방법</th><td>온라인접수</td></tr>
          <tr><th>첨부파일</th><td>010-9999-9999-private.hwpx</td></tr>
        </tbody></table>
      </body></html>
    """


def _city_fixture() -> FixtureRequester:
    requester = FixtureRequester()
    current = [
        (1, "현재 열린 강좌", "통영시청", "접수중", "2026-07-01", "2026-07-31", "2026-08-01", "2026-08-31", True),
        (2, "현재 마감 강좌", "읍면동 주민자치센터", "접수마감", "2026-06-01", "2026-06-20", "2026-07-01", "2026-09-01", False),
    ]
    expired = [
        (identity, f"과거 강좌 {identity}", "통영시청", "", "2025-01-01", "2025-01-10", "2025-02-01", "2025-02-10", False)
        for identity in range(3, 12)
    ]
    rows = current + expired
    requester.add(
        tongyeong.tongyeong_city_page_url(1),
        _city_list_html(
            "".join(_city_row(*row) for row in rows[:10]),
            total=11,
            reported=1,
            last=2,
        ),
    )
    last_html = _city_list_html(
        _city_row(*rows[10]), total=11, reported=2, last=2
    )
    requester.add(tongyeong.tongyeong_city_page_url(2), last_html)
    requester.add(tongyeong.tongyeong_city_page_url(3), last_html)
    for index, row in enumerate(current):
        identity, title, branch, _, apply_start, apply_end, start, end, _ = row
        requester.add(
            tongyeong.tongyeong_city_detail_url(str(identity)),
            _city_detail_html(
                identity,
                title,
                branch,
                apply_start,
                apply_end,
                start,
                end,
                include_time=index == 0,
            ),
        )
    return requester


def _library_href(action: str, lg_code: str, le_code: str) -> str:
    return (
        "./index.php?g_page=culture&amp;m_page=culture02&amp;libCho=TOL&amp;"
        f"act={action}&amp;lgCode={lg_code}&amp;leCode={le_code}"
    )


def _library_row(row: Mapping[str, Any]) -> str:
    detail = _library_href("lecture_view", row["lg"], row["le"])
    private = _library_href("lecture_cancel_form", row["lg"], row["le"])
    receive = _library_href("lecture_receive_form", row["lg"], row["le"])
    status = row["status"]
    status_html = (
        f'<a href="{receive}"><img alt="{status}"></a>'
        if status in {"신청하기", "대기자신청"}
        else f'<img alt="{status}">'
    )
    return f"""
      <tr><td><strong>{row['broad']}</strong></td>
        <td><a href="{detail}"><strong>{escape(row['title'])}</strong></a><br>
          <strong class="blue">{escape(row['target'])}</strong></td>
        <td><div>{row['capacity']}명 모집</div><div>{row['current']}명 신청</div>
          <a href="{private}">등록확인</a></td>
        <td><div>{row['apply_start']} 10:00 ~ {row['apply_end']} 18:00</div>
          <div>{row['start']} ~ {row['end']}<br>{escape(row['schedule'])}</div></td>
        <td>{status_html}</td></tr>
    """


def _library_page_html(
    rows: list[Mapping[str, Any]], *, reported: int | None, last: int
) -> str:
    links = "".join(
        f'<a href="?page={page}&amp;g_page=culture&amp;m_page=culture02">{page}</a>'
        for page in range(1, last + 1)
        if page != reported
    )
    current = f"<strong>{reported}</strong>" if reported is not None else ""
    return f"""
      <html><body><table><thead><tr><th>도서관</th>
        <th>행사 / 강좌명 / <span>대상</span></th><th>모집인원</th>
        <th>접수기간 / 수강일시</th><th>접수현황</th></tr></thead>
        <tbody>{''.join(_library_row(row) for row in rows)}</tbody></table>
        <div class="paging">{current}{links}</div>
      </body></html>
    """


def _library_detail_html(row: Mapping[str, Any]) -> str:
    receive = _library_href("lecture_receive_form", row["lg"], row["le"])
    control = (
        f'<a href="{receive}"><img alt="{row["status"]}"></a>'
        if row["status"] in {"신청하기", "대기자신청"}
        else ""
    )
    return f"""
      <html><body><div class="lecture_view"><h4>{escape(row['title'])}</h4>
        <dl><dt>대상</dt><dd>{escape(row['target'])}</dd>
          <dt>강사명</dt><dd>민감 강사명</dd></dl>
        <dl><dt>인터넷 모집인원</dt><dd>{row['capacity']}명</dd>
          <dt>현재신청자수</dt><dd>{row['current']}명</dd></dl>
        <dl><dt>대기자 모집인원</dt><dd>5명</dd>
          <dt>계획서</dt><dd>private-plan.pdf</dd></dl>
        <dl><dt>준비물</dt><dd>담당자 010-1111-2222에게 문의</dd>
          <dt>수강료</dt><dd>{escape(row['fee'])}</dd></dl>
        <dl><dt>접수 기간</dt><dd>{row['apply_start']} 10:00 ~ {row['apply_end']} 18:00</dd>
          <dt>강좌 기간</dt><dd>{row['start']} ~ {row['end']}</dd>
          <dt>강좌 일시</dt><dd>{escape(row['schedule'])}</dd>
          <dt>강좌 장소</dt><dd>{escape(row['venue'])}</dd></dl>
        <div class="lecture_contents">자유본문 이메일 secret@example.com</div>
        <div class="commend">{control}<a href="#">처음으로</a></div>
      </div></body></html>
    """


def _library_fixture() -> tuple[FixtureRequester, list[dict[str, Any]]]:
    requester = FixtureRequester()
    current_rows = [
        {"lg": "7", "le": "1001", "broad": "작은", "title": "[더팰리스] 비누 만들기", "target": "초등 7명", "capacity": 7, "current": 5, "apply_start": "2026.07.01", "apply_end": "2026.07.31", "start": "2026.08.01", "end": "2026.08.01", "schedule": "토( 10:00~11:30 )", "status": "신청하기", "venue": "더팰리스작은도서관", "fee": "무료"},
        {"lg": "7", "le": "1002", "broad": "작은", "title": "[안정] 여름 공예", "target": "성인 8명", "capacity": 8, "current": 8, "apply_start": "2026.07.01", "apply_end": "2026.07.31", "start": "2026.08.02", "end": "2026.08.02", "schedule": "일( 10:00~11:30 )", "status": "대기자신청", "venue": "안정작은도서관", "fee": "무료"},
        {"lg": "3", "le": "1003", "broad": "충무", "title": "충무 열린 강좌", "target": "성인 12명", "capacity": 12, "current": 5, "apply_start": "2026.07.01", "apply_end": "2026.07.31", "start": "2026.08.03", "end": "2026.09.03", "schedule": "수( 19:00~21:00 )", "status": "신청하기", "venue": "통제영강좌실", "fee": "0"},
        {"lg": "2", "le": "1004", "broad": "꿈이랑", "title": "꿈이랑 마감 강좌", "target": "초등 14명", "capacity": 14, "current": 14, "apply_start": "2026.06.01", "apply_end": "2026.06.30", "start": "2026.08.04", "end": "2026.08.04", "schedule": "토( 10:00~12:00 )", "status": "접수마감", "venue": "꿈이랑도서관 2층", "fee": "무료"},
        {"lg": "8", "le": "1005", "broad": "시립", "title": "시립 외부 강좌", "target": "성인 25명", "capacity": 25, "current": 25, "apply_start": "2026.06.01", "apply_end": "2026.06.30", "start": "2026.08.05", "end": "2026.08.05", "schedule": "토( 15:00~17:00 )", "status": "접수마감", "venue": "박경리기념관", "fee": "없음"},
        {"lg": "3", "le": "1006", "broad": "충무", "title": "공식 중복 외형 강좌", "target": "초등 12명", "capacity": 12, "current": 11, "apply_start": "2026.06.01", "apply_end": "2026.06.30", "start": "2026.08.06", "end": "2026.08.06", "schedule": "목( 10:00~12:00 )", "status": "접수마감", "venue": "통제영강좌실", "fee": "무료"},
        {"lg": "8", "le": "1031", "broad": "시립", "title": "공식 중복 외형 강좌", "target": "초등 12명", "capacity": 12, "current": 0, "apply_start": "2026.06.01", "apply_end": "2026.06.30", "start": "2026.08.06", "end": "2026.08.06", "schedule": "목( 10:00~12:00 )", "status": "접수마감", "venue": "통제영강좌실", "fee": "무료"},
    ]
    expired: list[dict[str, Any]] = []
    for index in range(7, 31):
        lg = "8" if index % 2 else "3"
        broad = "시립" if lg == "8" else "충무"
        expired.append(
            {"lg": lg, "le": str(1000 + index), "broad": broad, "title": f"과거 강좌 {index}", "target": "통영시민 10명", "capacity": 10, "current": 10, "apply_start": "2025.01.01", "apply_end": "2025.01.10", "start": "2025.02.01", "end": "2025.02.10", "schedule": "월( 10:00~12:00 )", "status": "접수마감", "venue": "문화강좌실", "fee": "무료"}
        )
    # Put one duplicate-looking current identity on the late fourth page.
    data_rows = current_rows[:6] + expired + [current_rows[6]]
    assert len(data_rows) == 31
    for page in range(1, 5):
        chunk = data_rows[(page - 1) * 10 : page * 10]
        requester.add(
            tongyeong.tongyeong_library_page_url(page),
            _library_page_html(chunk, reported=page, last=4),
        )
    requester.add(
        tongyeong.tongyeong_library_page_url(5),
        _library_page_html([], reported=None, last=4),
    )
    for row in current_rows:
        requester.add(
            tongyeong.tongyeong_library_detail_url(row["lg"], row["le"]),
            _library_detail_html(row),
        )
    return requester, current_rows


def _assert_no_private_or_pii_payload(rows: list[dict[str, Any]]) -> None:
    forbidden_keys = {
        "instructor",
        "teacher",
        "manager",
        "contact",
        "phone",
        "email",
        "attachments",
        "plan",
        "preparation",
        "source_html",
        "raw_html",
        "form_payload",
    }
    for row in rows:
        assert not (set(row) & forbidden_keys)
        payload = repr(
            {
                key: value
                for key, value in row.items()
                if key not in {"raw_url", "application_url"}
            }
        )
        assert "010-" not in payload
        assert "secret@example.com" not in payload
        assert "민감 강사" not in payload
        assert "private-plan" not in payload
        assert "자유본문" not in payload


@pytest.mark.parametrize(
    ("provider", "url", "expected"),
    [
        (
            tongyeong.TONGYEONG_GNE_PROVIDER,
            tongyeong.TONGYEONG_GNE_LIST_URL,
            True,
        ),
        (
            tongyeong.TONGYEONG_GNE_PROVIDER,
            tongyeong.TONGYEONG_GNE_MENU_URL,
            True,
        ),
        (
            tongyeong.TONGYEONG_CITY_PROVIDER,
            tongyeong.TONGYEONG_CITY_LIST_URL,
            True,
        ),
        (
            tongyeong.TONGYEONG_CITY_PROVIDER,
            tongyeong.TONGYEONG_CITY_HOME_URL,
            True,
        ),
        (
            tongyeong.TONGYEONG_LIBRARY_PROVIDER,
            tongyeong.TONGYEONG_LIBRARY_LIST_URL,
            True,
        ),
        (
            tongyeong.TONGYEONG_GNE_PROVIDER,
            tongyeong.TONGYEONG_CITY_LIST_URL,
            False,
        ),
        (
            tongyeong.TONGYEONG_CITY_PROVIDER,
            tongyeong.TONGYEONG_CITY_LIST_URL + "?cpage=2",
            False,
        ),
        (
            tongyeong.TONGYEONG_LIBRARY_PROVIDER,
            tongyeong.TONGYEONG_LIBRARY_LIST_URL + "&page=1",
            False,
        ),
        (
            tongyeong.TONGYEONG_LIBRARY_PROVIDER,
            tongyeong.TONGYEONG_LIBRARY_LIST_URL.replace("https://", "http://"),
            False,
        ),
    ],
)
def test_exact_target_and_owner_boundaries(
    provider: str, url: str, expected: bool
) -> None:
    assert tongyeong.is_tongyeong_education_target(_target(provider, url)) is expected


def test_owner_audit_keeps_three_disjoint_ledgers() -> None:
    audit = tongyeong.TONGYEONG_OWNER_BOUNDARY_AUDIT
    assert audit[tongyeong.TONGYEONG_GNE_PROVIDER]["owner"] == (
        "경상남도교육청 통영도서관"
    )
    assert audit[tongyeong.TONGYEONG_CITY_PROVIDER]["owner"] == (
        "통영시 평생학습도시"
    )
    assert audit[tongyeong.TONGYEONG_LIBRARY_PROVIDER]["owner"] == (
        "통영시립도서관"
    )
    assert tongyeong.TONGYEONG_GNE_PROVIDER != tongyeong.TONGYEONG_CITY_PROVIDER
    assert tongyeong.TONGYEONG_LIBRARY_PROVIDER not in {
        tongyeong.TONGYEONG_GNE_PROVIDER,
        tongyeong.TONGYEONG_CITY_PROVIDER,
    }


def test_gne_complete_fixture_preserves_single_branch_and_application_identity() -> None:
    requester = _gne_fixture()
    session = DummySession()
    rows, parser, meta = tongyeong.collect_tongyeong_gne_library(
        _target(
            tongyeong.TONGYEONG_GNE_PROVIDER,
            tongyeong.TONGYEONG_GNE_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=lambda: session,
        requester=requester,
    )

    assert parser == tongyeong.TONGYEONG_GNE_PARSER
    assert session.closed is True
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["source_total"] == 3
    assert meta["current_source_count"] == 3
    assert meta["source_status_counts"] == {
        "모집중": 1,
        "준비중": 1,
        "교육중": 1,
    }
    assert len(rows) == 3
    assert {row["branch"] for row in rows} == {
        "경상남도교육청 통영도서관"
    }
    assert Counter(row["status"] for row in rows) == {
        "OPEN": 1,
        "SCHEDULED": 1,
        "CLOSED": 1,
    }
    opened = next(row for row in rows if row["status"] == "OPEN")
    assert parse_qs(urlparse(opened["application_url"]).query)["gno"] == ["101"]
    assert opened["reservation_available"] is True
    assert all(
        "/usr_gne/register.es" not in url
        and "/usr_gne/lec_reqin.es" not in url
        for url, _ in requester.calls
    )
    detail_calls = [
        headers
        for url, headers in requester.calls
        if urlparse(url).path == tongyeong.TONGYEONG_GNE_DETAIL_PATH
    ]
    assert detail_calls
    assert all(headers.get("Referer") for headers in detail_calls)
    _assert_no_private_or_pii_payload(rows)


def test_gne_fails_closed_on_nonempty_sentinel() -> None:
    requester = _gne_fixture()
    first = requester.pages[_request_key(tongyeong.tongyeong_gne_page_url(1))]
    requester.add(tongyeong.tongyeong_gne_page_url(2), first)

    rows, _, meta = tongyeong.collect_tongyeong_gne_library(
        _target(
            tongyeong.TONGYEONG_GNE_PROVIDER,
            tongyeong.TONGYEONG_GNE_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "post-last" in meta["configured_collection_error"]


def test_gne_fails_closed_on_visible_application_identity_mismatch() -> None:
    requester = _gne_fixture()
    url = tongyeong.tongyeong_gne_detail_url("101")
    requester.add(
        url,
        requester.pages[_request_key(url)].replace(
            "requestStudent('101'", "requestStudent('999'"
        ),
    )

    rows, _, meta = tongyeong.collect_tongyeong_gne_library(
        _target(
            tongyeong.TONGYEONG_GNE_PROVIDER,
            tongyeong.TONGYEONG_GNE_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert rows == []
    assert meta["detail_errors"] == 1
    assert "identity mismatch" in meta["configured_collection_error"]


def test_gne_fails_closed_when_stable_first_page_changes() -> None:
    requester = _gne_fixture()
    url = tongyeong.tongyeong_gne_page_url(1)
    original = requester.pages[_request_key(url)]
    requester.mutate(url, original, original.replace("열린 독서교실", "변경된 강좌"))

    rows, _, meta = tongyeong.collect_tongyeong_gne_library(
        _target(
            tongyeong.TONGYEONG_GNE_PROVIDER,
            tongyeong.TONGYEONG_GNE_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert rows == []
    assert "stability recheck changed" in meta["configured_collection_error"]


def test_gne_scheduled_status_is_valid_on_opening_date_and_discards_participant_info() -> None:
    requester = _gne_fixture()
    list_url = tongyeong.tongyeong_gne_page_url(1)
    detail_url = tongyeong.tongyeong_gne_detail_url("102")
    list_html = requester.pages[_request_key(list_url)]
    original_period = "2026-08-01 09:00 ~ 2026-08-10 18:00"
    list_html = list_html.replace(
        original_period,
        "2026-07-22 09:00 ~ 2026-08-10 18:00",
        1,
    )
    requester.add(
        list_url,
        list_html,
    )
    requester.add(
        detail_url,
        requester.pages[_request_key(detail_url)]
        .replace(
            "2026-08-01 09:00 ~ 2026-08-10 18:00",
            "2026-07-22 09:00 ~ 2026-08-10 18:00",
        )
        .replace(
            "</tr>",
            '<td class="boardText1">참여자정보</td>'
            "<td>참가자 이름과 연령을 입력하세요</td></tr>",
            1,
        ),
    )

    rows, _, meta = tongyeong.collect_tongyeong_gne_library(
        _target(
            tongyeong.TONGYEONG_GNE_PROVIDER,
            tongyeong.TONGYEONG_GNE_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert meta["configured_collection_error"] == ""
    scheduled = next(row for row in rows if row["status"] == "SCHEDULED")
    assert scheduled["apply_start"] == "2026-07-22"
    assert "참가자 이름" not in repr(rows)


def test_city_complete_fixture_walks_total_and_exact_clamp() -> None:
    requester = _city_fixture()
    rows, parser, meta = tongyeong.collect_tongyeong_city_lifelong(
        _target(
            tongyeong.TONGYEONG_CITY_PROVIDER,
            tongyeong.TONGYEONG_CITY_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert parser == tongyeong.TONGYEONG_CITY_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["declared_total"] == 11
    assert meta["source_total"] == 11
    assert meta["declared_last_page"] == 2
    assert meta["current_source_count"] == 2
    assert len(rows) == 2
    assert {row["branch"] for row in rows} == {
        "통영시청",
        "읍면동 주민자치센터",
    }
    assert Counter(row["status"] for row in rows) == {"OPEN": 1, "CLOSED": 1}
    opened = next(row for row in rows if row["status"] == "OPEN")
    query = parse_qs(urlparse(opened["application_url"]).query)
    assert query["idx"] == ["1"]
    assert query["amode"] == ["apply"]
    assert all("Download.do" not in url for url, _ in requester.calls)
    _assert_no_private_or_pii_payload(rows)


def test_city_fails_closed_when_post_last_is_not_last_page_clamp() -> None:
    requester = _city_fixture()
    requester.add(
        tongyeong.tongyeong_city_page_url(3),
        requester.pages[_request_key(tongyeong.tongyeong_city_page_url(1))],
    )

    rows, _, meta = tongyeong.collect_tongyeong_city_lifelong(
        _target(
            tongyeong.TONGYEONG_CITY_PROVIDER,
            tongyeong.TONGYEONG_CITY_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert rows == []
    assert "exact last-page clamp" in meta["configured_collection_error"]


def test_city_fails_closed_on_duplicate_official_identity() -> None:
    requester = _city_fixture()
    page2_url = tongyeong.tongyeong_city_page_url(2)
    duplicated = requester.pages[_request_key(page2_url)].replace(
        "idx=11", "idx=1"
    )
    requester.add(page2_url, duplicated)
    requester.add(tongyeong.tongyeong_city_page_url(3), duplicated)

    rows, _, meta = tongyeong.collect_tongyeong_city_lifelong(
        _target(
            tongyeong.TONGYEONG_CITY_PROVIDER,
            tongyeong.TONGYEONG_CITY_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert rows == []
    assert meta["identity_duplicate_count"] == 1
    assert "duplicate" in meta["configured_collection_error"]


def test_city_fails_closed_on_unknown_detail_field() -> None:
    requester = _city_fixture()
    url = tongyeong.tongyeong_city_detail_url("1")
    requester.add(
        url,
        requester.pages[_request_key(url)].replace(
            "</tbody>",
            "<tr><th>담당자 연락처</th><td>010-1234-5678</td></tr></tbody>",
        ),
    )

    rows, _, meta = tongyeong.collect_tongyeong_city_lifelong(
        _target(
            tongyeong.TONGYEONG_CITY_PROVIDER,
            tongyeong.TONGYEONG_CITY_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert rows == []
    assert meta["detail_errors"] == 1
    assert "unknown detail label" in meta["configured_collection_error"]


def test_municipal_library_complete_fixture_preserves_late_and_exact_branches() -> None:
    requester, _ = _library_fixture()
    rows, parser, meta = tongyeong.collect_tongyeong_municipal_library(
        _target(
            tongyeong.TONGYEONG_LIBRARY_PROVIDER,
            tongyeong.TONGYEONG_LIBRARY_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert parser == tongyeong.TONGYEONG_LIBRARY_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 31
    assert meta["declared_last_page"] == 4
    assert meta["current_source_count"] == 7
    assert meta["current_page_counts"] == {1: 6, 4: 1}
    assert meta["late_current_pages"] == {4: 1}
    assert meta["current_source_status_counts"] == {
        "신청하기": 2,
        "대기자신청": 1,
        "접수마감": 4,
    }
    assert meta["application_control_count"] == 3
    assert meta["source_identity_anomaly_groups"] == [
        ["3:1006", "8:1031"]
    ]
    assert len(rows) == 7
    assert meta["branch_counts"] == {
        "더팰리스작은도서관": 1,
        "안정작은도서관": 1,
        "통영시립충무도서관": 2,
        "꿈이랑도서관": 1,
        "통영시립도서관": 2,
    }
    assert len({row["provider_course_id"] for row in rows}) == 7
    anomaly_rows = [
        row
        for row in rows
        if row["raw_fields"]["source_identity_anomaly"]
    ]
    assert {row["raw_fields"]["identity"] for row in anomaly_rows} == {
        "3:1006",
        "8:1031",
    }
    assert all(
        parse_qs(urlparse(row["application_url"]).query)["leCode"]
        == [row["raw_fields"]["le_code"]]
        for row in rows
        if row["reservation_available"]
    )
    assert all(
        parse_qs(urlparse(url).query).get("act")
        not in [
            [tongyeong.TONGYEONG_LIBRARY_RECEIVE_ACTION],
            [tongyeong.TONGYEONG_LIBRARY_PRIVATE_CHECK_ACTION],
        ]
        for url, _ in requester.calls
    )
    _assert_no_private_or_pii_payload(rows)


def test_municipal_library_accepts_waiting_before_application() -> None:
    row = {
        "lg": "9",
        "le": "11776",
        "broad": "꿈이랑",
        "title": "체험형 동화구연",
        "target": "4~8세",
        "capacity": 8,
        "current": 0,
        "apply_start": "2026.07.29",
        "apply_end": "2026.08.06",
        "start": "2026.08.08",
        "end": "2026.08.08",
        "schedule": "토( 11:00~11:30 )",
        "status": "대기중",
        "venue": "꿈이랑도서관",
        "fee": "무료",
    }
    soup = BeautifulSoup(
        _library_page_html([row], reported=1, last=1),
        "html.parser",
    )

    parsed = tongyeong._parse_library_page(soup, requested_page=1)

    assert parsed.rows[0]["source_status"] == "대기중"
    assert parsed.rows[0]["application_control"] is False
    assert parsed.rows[0]["application_url"] == ""
    assert tongyeong._LIBRARY_STATUS_MAP["대기중"] == "SCHEDULED"


def test_municipal_library_detail_cap_cannot_hide_late_current_row() -> None:
    requester, _ = _library_fixture()
    rows, _, meta = tongyeong.collect_tongyeong_municipal_library(
        _target(
            tongyeong.TONGYEONG_LIBRARY_PROVIDER,
            tongyeong.TONGYEONG_LIBRARY_LIST_URL,
        ),
        today="2026-07-22",
        detail_limit=6,
        session_factory=DummySession,
        requester=requester,
    )

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["current_candidate_count"] == 7
    assert meta["detail_attempts"] == 0
    assert "detail_limit" in meta["configured_collection_error"]


def test_municipal_library_max_pages_cannot_truncate_declared_last() -> None:
    requester, _ = _library_fixture()
    rows, _, meta = tongyeong.collect_tongyeong_municipal_library(
        _target(
            tongyeong.TONGYEONG_LIBRARY_PROVIDER,
            tongyeong.TONGYEONG_LIBRARY_LIST_URL,
        ),
        today="2026-07-22",
        max_pages=3,
        session_factory=DummySession,
        requester=requester,
    )

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["declared_last_page"] == 4
    assert "max_pages" in meta["configured_collection_error"]


def test_municipal_library_fails_closed_on_nonempty_sentinel() -> None:
    requester, _ = _library_fixture()
    requester.add(
        tongyeong.tongyeong_library_page_url(5),
        requester.pages[_request_key(tongyeong.tongyeong_library_page_url(4))],
    )

    rows, _, meta = tongyeong.collect_tongyeong_municipal_library(
        _target(
            tongyeong.TONGYEONG_LIBRARY_PROVIDER,
            tongyeong.TONGYEONG_LIBRARY_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert rows == []
    assert "post-last" in meta["configured_collection_error"]


def test_municipal_library_fails_closed_on_detail_receive_identity_mismatch() -> None:
    requester, _ = _library_fixture()
    url = tongyeong.tongyeong_library_detail_url("7", "1001")
    requester.add(
        url,
        requester.pages[_request_key(url)].replace(
            "leCode=1001", "leCode=9999"
        ),
    )

    rows, _, meta = tongyeong.collect_tongyeong_municipal_library(
        _target(
            tongyeong.TONGYEONG_LIBRARY_PROVIDER,
            tongyeong.TONGYEONG_LIBRARY_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
    )

    assert rows == []
    assert meta["detail_errors"] == 1
    assert "identity mismatch" in meta["configured_collection_error"]


def test_content_dedupe_may_not_drop_distinct_official_library_identity() -> None:
    requester, _ = _library_fixture()

    def title_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list({row["title"]: row for row in rows}.values())

    rows, _, meta = tongyeong.collect_tongyeong_municipal_library(
        _target(
            tongyeong.TONGYEONG_LIBRARY_PROVIDER,
            tongyeong.TONGYEONG_LIBRARY_LIST_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=requester,
        dedupe_rows=title_dedupe,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed official identity cardinality" in meta[
        "configured_collection_error"
    ]


def test_dispatcher_routes_each_exact_owner() -> None:
    gne_requester = _gne_fixture()
    rows, parser, meta = tongyeong.collect_tongyeong_education(
        _target(
            tongyeong.TONGYEONG_GNE_PROVIDER,
            tongyeong.TONGYEONG_GNE_MENU_URL,
        ),
        today="2026-07-22",
        session_factory=DummySession,
        requester=gne_requester,
    )
    assert len(rows) == 3
    assert parser == tongyeong.TONGYEONG_GNE_PARSER
    assert meta["source_kind"] == "gne_education_office_library"

    rows, parser, meta = tongyeong.collect_tongyeong_education(
        _target("MUNI_WRONG", tongyeong.TONGYEONG_GNE_LIST_URL),
        today="2026-07-22",
    )
    assert rows == []
    assert parser == tongyeong.TONGYEONG_PARSER
    assert meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("TONGYEONG_EDUCATION_LIVE") != "1",
    reason="set TONGYEONG_EDUCATION_LIVE=1 for the official live audit",
)
def test_live_all_three_official_ledgers() -> None:
    cases = [
        (
            _target(
                tongyeong.TONGYEONG_GNE_PROVIDER,
                tongyeong.TONGYEONG_GNE_LIST_URL,
            ),
            9,
            9,
        ),
        (
            _target(
                tongyeong.TONGYEONG_CITY_PROVIDER,
                tongyeong.TONGYEONG_CITY_LIST_URL,
            ),
            191,
            2,
        ),
        (
            _target(
                tongyeong.TONGYEONG_LIBRARY_PROVIDER,
                tongyeong.TONGYEONG_LIBRARY_LIST_URL,
            ),
            266,
            31,
        ),
    ]
    for target, expected_source, expected_current in cases:
        rows, _, meta = tongyeong.collect_tongyeong_education(
            target,
            today="2026-07-22",
            timeout=45,
        )
        assert meta["snapshot_complete"] is True, meta[
            "configured_collection_error"
        ]
        assert meta["source_total"] == expected_source
        assert meta["current_source_count"] == expected_current
        assert len(rows) == expected_current

    library_meta = tongyeong.collect_tongyeong_municipal_library(
        cases[2][0], today="2026-07-22", timeout=45
    )[2]
    assert library_meta["current_source_status_counts"] == {
        "신청하기": 6,
        "대기자신청": 3,
        "접수마감": 22,
    }
    assert library_meta["branch_counts"] == {
        "더팰리스작은도서관": 2,
        "안정작은도서관": 2,
        "통영시립충무도서관": 11,
        "통영시립도서관": 12,
        "꿈이랑도서관": 4,
    }
