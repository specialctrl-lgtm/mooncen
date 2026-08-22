from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_gangdong as gangdong


@dataclass(frozen=True)
class Target:
    provider: str
    url: str


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(provider: str) -> Target:
    return Target(provider, gangdong.GANGDONG_CANONICAL_URLS[provider])


def _reserve_row(
    number: int,
    identity: str,
    title: str,
    *,
    source_kind: str,
    status: str = "접수중",
    apply_period: str = "2026-07-01 ~ 2026-07-31",
    period: str = "2026-08-01 ~ 2026-08-31",
) -> str:
    href = (
        f"/web/newreserve/reserve/view?basicId={identity}&amp;cp=1&amp;basicType=reserveType_01"
        if source_kind == "event"
        else f"/web/comedu/eduProgram/{identity}"
    )
    return f"""
      <li><a class="bis" href="{href}" title="{title}">
        <span class="no">{number}.</span><span class="state">{status}</span>
        <ul class="comp-list"><li>{apply_period}</li><li>{period}</li></ul>
      </a></li>
    """


def _reserve_list(*rows: str) -> str:
    return f"<div class='repla-lists'><ul>{''.join(rows)}</ul></div>"


def _event_detail(title: str, body: str) -> str:
    return f"""
    <div id="con"><div class="table01"><table><tbody>
      <tr><th>행사제목</th><td>{title}</td></tr>
      <tr><th>접수기간</th><td>2026-07-01 ~ 2026-07-31</td></tr>
      <tr><th>교육·행사 일시</th><td>2026-08-01 ~ 2026-08-31</td></tr>
    </tbody></table></div><div class="basicContent">{body}</div></div>
    """


def _comedu_detail(title: str) -> str:
    return f"""
    <table><tbody>
      <tr><th>교육장</th><td>강일동</td></tr>
      <tr><th>접수기간/상태</th><td>2026-07-01 ~ 2026-07-31 / 접수중</td></tr>
      <tr><th>교육기간</th><td>2026-08-01 ~ 2026-08-31</td></tr>
      <tr><th>요일 및 시간</th><td>매주 화 10:00~12:00</td></tr>
      <tr><th>수강료</th><td>무료</td></tr>
      <tr><th>수강인원</th><td>4 / 20</td></tr>
      <tr><th>연락처</th><td>02-0000-0000</td></tr>
      <tr><th>상세정보</th><td>강좌명 : {title} □ 교육장 찾아가는 길 - 강일동 교육장 □</td></tr>
      <tr><th>강좌안내</th><td>주민 정보화 교육</td></tr>
    </tbody></table>
    """


def _reserve_fixture(*, malformed_numbering: bool = False):
    pages = {
        gangdong.gangdong_reserve_list_url("reserveType_01", 1): _reserve_list(
            _reserve_row(3 if malformed_numbering else 2, "9002", "여름 건강 교육", source_kind="event"),
            _reserve_row(1, "9001", "응시료 지원사업 신청", source_kind="event"),
        ),
        gangdong.gangdong_reserve_list_url("RESIDENTCOMEDU", 1): _reserve_list(
            _reserve_row(1, "7001", "1. (일일)디지털 기초", source_kind="comedu")
        ),
        gangdong.gangdong_reserve_detail_url("9002"): _event_detail(
            "여름 건강 교육",
            "교육 장소 : 강동구청 5층 대강당 신청 대상 : 강동구민 교육 내용 : 건강관리",
        ),
        gangdong.gangdong_reserve_detail_url("9001"): _event_detail(
            "응시료 지원사업 신청", "청년 응시료 지원사업 신청 대상 : 강동구민"
        ),
        gangdong.gangdong_comedu_detail_url("7001"): _comedu_detail("(일일특강)디지털 기초"),
    }
    return _mapping_fixture(pages)


def _mapping_fixture(mapping: dict[str, str]):
    sessions: list[DummySession] = []

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        return mapping[url]

    def make_session() -> DummySession:
        session = DummySession()
        sessions.append(session)
        return session

    return fetch, make_session, sessions


def _health_list() -> str:
    return """
    <table><tbody>
      <tr><td>2</td><td>모자보건</td><td>부모 교육</td>
          <td>2026-08-12 ~ 2026-08-12</td><td>2026-07-21 ~ 2026-08-11</td>
          <td><a href="javascript:;">접수대기</a></td></tr>
      <tr><td>1</td><td>만성질환</td><td>당뇨병 교실</td>
          <td>2026-08-04 ~ 2026-08-04</td><td>2026-07-01 ~ 2026-08-03</td>
          <td><a href="/health/site/main/program/view?pgSeq=20260702110730258">접수중</a></td></tr>
    </tbody></table>
    """


def _health_detail() -> str:
    return """
    <table><tbody>
      <tr><th>프로그램명</th><td>당뇨병 교실</td></tr>
      <tr><th>교육일</th><td>2026-08-04 ~ 2026-08-04</td></tr>
      <tr><th>접수기간</th><td>2026-07-01 ~ 2026-08-03</td></tr>
      <tr><th>장소</th><td>강일보건지소</td></tr>
      <tr><th>대상</th><td>강동구민</td></tr>
      <tr><th>비용</th><td>무료</td></tr>
      <tr><th>문의처</th><td>02-0000-0000</td></tr>
      <tr><th>선발인원</th><td>20</td></tr>
      <tr><th>교육 내용</th><td>당뇨병 관리 교육</td></tr>
    </tbody></table>
    """


def _lll_list(identity: str, title: str, status: str) -> str:
    return f"""
    <table><tbody><tr>
      <td>1</td><td class="td_title"><a class="tit" onclick="fn_view('{identity}')">{title}</a></td>
      <td class="td_date">2026-07-01 ~ 2026-07-31 2026-08-01 ~ 2026-09-30</td>
      <td class="td_limit">3 / 15</td><td class="td_status">{status}</td>
    </tr></tbody></table><div class="paginate"></div>
    """


def _lll_empty_list() -> str:
    return """
    <html><head><title>서울특별시 강동구 평생학습관</title></head><body>
      <table><thead><tr>
        <th>번호</th><th>이미지</th><th>강의명</th><th>기간</th>
        <th>정원</th><th>조회수</th><th>상태</th>
      </tr></thead><tbody></tbody></table><div class="paginate"></div>
    </body></html>
    """


def _lll_detail(title: str, venue: str) -> str:
    return f"""
    <div class="tbl_wrap view"><table><tbody>
      <tr><th>강의명</th><td>{title}</td></tr>
      <tr><th>접수 기간</th><td>2026년 07월 01일 ~ 2026년 07월 31일</td></tr>
      <tr><th>강의 기간</th><td>2026년 08월 01일 ~ 2026년 09월 30일</td></tr>
      <tr><th>강의 시간</th><td>매주 수 10:00~12:00</td></tr>
      <tr><th>교육 장소</th><td>{venue}</td></tr>
      <tr><th>수강료</th><td>무료</td></tr>
      <tr><th>신청 현황</th><td>3 / 15</td></tr>
      <tr><th>담당자/문의</th><td>02-0000-0000</td></tr>
    </tbody></table></div>
    <div class="tab_wrap contab1"><div class="txt_area">평생학습 강의</div></div>
    """


def _library_card(
    section: str,
    identity: str,
    title: str,
    *,
    library: str,
    schedule: str,
    venue: str,
    status: str = "접수중",
    paginated_style: bool = True,
) -> str:
    _key, menu, slug, _paginated = gangdong._library_section(section)
    href = f"/ch/menu/{menu}/tmpr/lctr-evnt/{slug}/{identity}?searchHmpg=1"
    body = f"""
      <div class="img-area"><span class="status">{status}</span></div>
      <div class="info-area"><span class="library">{library}</span>
        {f'<a class="name" href="{href}">{title}</a>' if paginated_style else f'<p class="name">{title}</p>'}
        <ul>
          <li><span class="title">일정</span><span class="text">{schedule}</span></li>
          <li><span class="title">대상</span><span class="text">강동구민</span></li>
          <li><span class="title">장소</span><span class="text">{venue}</span></li>
          {('<li><span class="title">접수기간</span><span class="text">2026-07-01 ~ 2026-07-31</span></li>' if paginated_style else '')}
        </ul>
      </div>
    """
    return (
        f'<div class="result-box">{body}</div>'
        if paginated_style
        else f'<a class="result-box" href="{href}">{body}</a>'
    )


def _library_page(*cards: str) -> str:
    return f'<div class="program-list">{"".join(cards)}</div><button class="page" data-page-no="1">1</button>'


def _library_detail(title: str, library: str, schedule: str, venue: str) -> str:
    return f"""
    <div class="program-detail"><div class="info-area">
      <span class="library">{library}</span><h4 class="title">{title}</h4><ul>
        <li><span class="title">접수기간</span><span class="text">2026-07-01 ~ 2026-07-31</span></li>
        <li><span class="title">일정</span><span class="text">{schedule}</span></li>
        <li><span class="title">대상</span><span class="text">강동구민</span></li>
        <li><span class="title">장소</span><span class="text">{venue}</span></li>
        <li><span class="title">모집인원</span><span class="text">3/12명</span></li>
      </ul></div><div class="middle-area"><div class="content-text">도서관 교육</div></div>
    </div>
    """


def _50plus_page() -> str:
    values = (
        "강동센터",
        "2026년 여름학기",
        "직업역량강화",
        "AI 실무 교육",
        "2026.07.01 ~ 2026.07.31",
        "2026.08.01 ~ 2026.08.31",
        "김강사",
        "무료",
        "15",
    )
    cells = "".join(f"<td><label>항목 :</label>{value}</td>" for value in values)
    return f"""
    <div class="campus-course-list-table"><table><tbody><tr>
      {cells}<td><a href="education-detail.do?id=70001">수강신청</a></td>
    </tr></tbody></table></div>
    """


def _50plus_detail() -> str:
    return """
    <h2 class="show-title">AI 실무 교육</h2>
    <div class="course-content">
      <p>교육일정 2026.08.01 ~ 2026.08.31</p>
      <table><tr><td>교육장소</td><td>4층 401호 배움실</td></tr></table>
      <p>생성형 AI 활용 실무 과정</p>
    </div>
    """


def _jumin_card(
    identity: str,
    title: str,
    *,
    owner: str = "천호3동 주민센터",
    venue: str = "천호3동 주민센터 3층 2강의실",
    status: str = "모집중",
) -> str:
    return f"""
    <li class="group clearfix"><div class="l">
      <strong class="place">{venue}</strong>
      <div class="tit"><a onclick="fn_view('{identity}'); return false;">
        <span class="label color-pink">주민자치</span><strong>{title}</strong>
      </a></div>
      <ul class="sort clearfix">
        <li><span class="t">신청기간</span><span class="cont">2026-07-01 ~ 2026-07-31</span></li>
        <li><span class="t">교육기간</span><span class="cont">2026-08-01 ~ 2026-09-30</span></li>
        <li><span class="t">교육시간</span><span class="cont">월,금 10:00 ~ 11:00</span></li>
        <li><span class="t">신청인원 / 정원</span><span class="cont">3명 / 20명</span></li>
      </ul>
    </div><div class="r pc_only"><strong>{owner}</strong><strong>{status}</strong></div></li>
    """


def _jumin_page(*cards: str, last_page: int = 1) -> str:
    return f"""
    <div class="bbs-program_w"><ul>{''.join(cards)}</ul></div>
    <div class="wrap_paging"><a class="btn_last" onclick="linkPage({last_page}); return false;"></a></div>
    """


def _jumin_detail(identity: str, title: str) -> str:
    return f"""
    <form id="searchForm"><input name="gn_seq" value="{identity}">
      <div class="top"><h5 class="t"><strong>{title}</strong></h5></div>
      <div class="group"><h5 class="tit-st1">프로그램 소개</h5><div class="box grey"><ul>
        <li class="item"><span class="dt">강의지역</span><span class="dd">천호3동 / 천호3동 주민센터 / 3층 2강의실</span></li>
        <li class="item"><span class="dt">강사명</span><span class="dd">김강사</span></li>
        <li class="item"><span class="dt">교육기간</span><span class="dd">2026-08-01 ~ 2026-09-30</span></li>
        <li class="item"><span class="dt">교육시간</span><span class="dd">월,금 10:00 ~ 11:00</span></li>
        <li class="item"><span class="dt">강의료</span><span class="dd">30,000 원</span></li>
        <li class="item"><span class="dt">접수기간</span><span class="dd">2026-07-01 ~ 2026-07-31</span></li>
        <li class="item"><span class="dt">모집대상</span><span class="dd">강동구민</span></li>
      </ul></div></div>
      <div class="group"><h5 class="tit-st1">프로그램 상세 내용</h5>
        <div class="box grey">주민자치 교육 상세</div></div>
    </form>
    """


def _json_key(url: str, data: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return url, tuple(sorted(data.items()))


def _json_fixture(mapping: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]]):
    sessions: list[DummySession] = []

    def post(_session: Any, url: str, data: dict[str, str], _timeout: int) -> dict[str, Any]:
        return mapping[_json_key(url, dict(data))]

    def make_session() -> DummySession:
        session = DummySession()
        sessions.append(session)
        return session

    return post, make_session, sessions


def _slc_course(
    identity: int,
    term_id: int,
    title: str,
    *,
    location: str = "",
    details: str = "교육 과정",
) -> dict[str, Any]:
    return {
        "id": identity,
        "term_id": term_id,
        "term_name": f"term-{term_id}",
        "service_title": title,
        "start_date": "2026-08-01 09:00:00",
        "end_date": "2026-08-31 18:00:00",
        "registration_start_date": "2026-07-01 09:00:00",
        "registration_end_date": "2026-07-31 18:00:00",
        "status_code": 2,
        "max_student_count": 20,
        "student_count": 3,
        "auditing_count": 1,
        "price": 0,
        "course_code_institution_name": "강동구청",
        "attribute_list": [
            {
                "attribute_category_code": "USER_TYPE",
                "attribute_name": "청소년",
            }
        ],
        "properties": {
            "location": location,
            "course_details": details,
            "contact": "<p>강동구청 교육지원과</p>",
        },
    }


def _slc_response(body: dict[str, Any]) -> dict[str, Any]:
    return {"code": 10000, "current_date": "2026-07-19 12:00:00", "body": body}


def test_exact_targets_hashes_and_url_builders() -> None:
    for provider, url in gangdong.GANGDONG_CANONICAL_URLS.items():
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8].upper()
        assert provider.endswith(digest)
        assert gangdong.is_gangdong_target(Target(provider, url)) is True
        assert gangdong.is_gangdong_target(Target(provider, url + "#fragment")) is False
        assert gangdong.is_gangdong_target(Target("MUNI_WRONG", url)) is False

    event = urlparse(gangdong.gangdong_reserve_detail_url("4828"))
    assert event.scheme == "https" and event.netloc == gangdong.GANGDONG_RESERVE_HOST
    assert parse_qs(event.query) == {
        "basicId": ["4828"],
        "basicType": ["reserveType_01"],
    }
    assert gangdong.gangdong_reserve_detail_url("../4828") == ""
    assert gangdong.gangdong_comedu_detail_url("12/34") == ""
    assert gangdong.gangdong_health_detail_url("abc") == ""
    assert gangdong.gangdong_lll_detail_url("../../1") == ""
    assert gangdong.gangdong_library_detail_url("reading", "../1") == ""
    assert gangdong.gangdong_50plus_detail_url("1/2") == ""
    assert gangdong.gangdong_slc_detail_url("176", "2", "../1") == ""
    assert gangdong.gangdong_jumin_detail_url("../../1") == ""
    assert gangdong._library_branch("통합", "rkddlfehtjrhks") == "강일도서관"
    assert gangdong._library_branch("통합", "중앙도서관 배움곳3") == "강동중앙도서관 배움곳3"


def test_reserve_complete_snapshot_filters_noneducation_and_locks_public_course() -> None:
    fetch, make_session, sessions = _reserve_fixture()
    rows, parser, meta = gangdong.collect_gangdong_reserve(
        _target(gangdong.GANGDONG_RESERVE_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=10,
        max_workers=1,
    )

    assert parser == gangdong.GANGDONG_RESERVE_PARSER
    assert [row["title"] for row in rows] == ["여름 건강 교육", "1. (일일)디지털 기초"]
    assert [row["branch"] for row in rows] == ["강동구청 5층 대강당", "강일동 교육장"]
    assert [row["venue_name"] for row in rows] == ["강동구청 5층 대강당", "강일동 교육장"]
    assert [row["target"] for row in rows] == ["강동구민", "대상 별도 안내"]
    assert [row["fee"] for row in rows] == ["요금 별도 안내", "무료"]
    assert all(row["schedule_raw"] for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "1174000000" for row in rows)
    assert all(row["end_date"] >= "2026-07-19" for row in rows)
    assert len({row["provider_course_id"] for row in rows}) == 2
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 3
    assert meta["candidate_count"] == 3
    assert meta["excluded_non_education"] == 1
    assert meta["detail_pages"] == 3
    assert all(session.closed for session in sessions)


def test_reserve_numbering_or_detail_cap_is_fail_closed() -> None:
    fetch, make_session, _sessions = _reserve_fixture(malformed_numbering=True)
    rows, _parser, meta = gangdong.collect_gangdong_reserve(
        _target(gangdong.GANGDONG_RESERVE_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=10,
        max_workers=1,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "not continuous" in meta["configured_collection_error"]

    fetch, make_session, _sessions = _reserve_fixture()
    rows, _parser, meta = gangdong.collect_gangdong_reserve(
        _target(gangdong.GANGDONG_RESERVE_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=1,
        max_workers=1,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 1
    assert meta["detail_required_count"] == 3


def test_historical_event_with_open_ended_reception_still_counts_for_continuity() -> None:
    soup = gangdong._coerce_soup(
        _reserve_list(
            _reserve_row(
                1,
                "3947",
                "과거 문화 강연",
                source_kind="event",
                status="마감",
                apply_period="접수기간: 2025-07-07~",
                period="교육·행사 일시: 2025-07-26~2025-07-26",
            )
        )
    )
    rows, invalid, exposed = gangdong._reserve_list_rows(
        _target(gangdong.GANGDONG_RESERVE_PROVIDER),
        soup,
        basic_type="reserveType_01",
        source_kind="event",
        page=1,
    )
    assert invalid == 0 and exposed == 1
    assert rows[0]["end_date"] == "2025-07-26"
    assert rows[0]["apply_period"] == ""


def test_comedu_classroom_label_is_title_evidence_for_official_counselling_rows() -> None:
    row = gangdong._base_row(
        _target(gangdong.GANGDONG_RESERVE_PROVIDER),
        identity_kind="comedu",
        identity="11825",
        title="디지털 상담소",
        raw_url=gangdong.gangdong_comedu_detail_url("11825"),
        parser=gangdong.GANGDONG_RESERVE_PARSER,
    )
    row.update(
        {
            "period": "2026-08-07 ~ 2026-08-21",
            "apply_period": "2026-07-28 ~ 2026-07-30",
        }
    )
    soup = gangdong._coerce_soup(
        """
        <table><tbody>
          <tr><th>교육장</th><td>디지털 상담소</td></tr>
          <tr><th>접수기간/상태</th><td>2026-07-28 ~ 2026-07-30</td></tr>
          <tr><th>교육기간</th><td>2026-08-07 ~ 2026-08-21</td></tr>
          <tr><th>상세정보</th><td></td></tr><tr><th>강좌안내</th><td></td></tr>
        </tbody></table>
        """
    )
    assert gangdong._reserve_comedu_detail(row, soup) == []
    assert row["branch"] == "디지털 상담소"
    assert row["raw_fields"]["detail_title_evidence"] == "detail_classroom_label"


def test_event_branch_cleanup_removes_flattened_time_and_address_noise() -> None:
    assert gangdong._normalize_event_branch("00 온플릭클라이밍짐") == "온플릭클라이밍짐"
    assert (
        gangdong._normalize_event_branch("강동구청 5 층 대강당 ( 성내로 25) ·")
        == "강동구청 5층 대강당"
    )
    assert (
        gangdong._normalize_event_branch("하남 미사경정공원 조정 · 카누 경기장 -")
        == "하남 미사경정공원 조정·카누 경기장"
    )
    assert gangdong._normalize_event_branch("30 뚝섬 윈드서핑장 31 호") == "뚝섬 윈드서핑장 31호"
    assert gangdong._normalize_event_branch("암사 2 동 주민센터 3 층 다목적실") == "암사2동 주민센터 3층 다목적실"


def test_event_detail_uses_the_declared_schedule_table_venue() -> None:
    row = gangdong._base_row(
        _target(gangdong.GANGDONG_RESERVE_PROVIDER),
        identity_kind="event",
        identity="4907",
        title="2026년 자동차 정비교실 수강생 모집",
        raw_url=gangdong.gangdong_reserve_detail_url("4907"),
        parser=gangdong.GANGDONG_RESERVE_PARSER,
    )
    row.update(
        {
            "period": "2026-08-01 ~ 2026-08-31",
            "apply_period": "2026-07-01 ~ 2026-07-31",
        }
    )
    soup = gangdong._coerce_soup(
        _event_detail(
            "2026년 자동차 정비교실 수강생 모집",
            """
            <p>신청대상: 강동구민 ※ 참여비용 무료 ※ 자동차 실습 교육입니다.</p>
            <table><tbody>
              <tr>
                <td>구분</td><td>교육일시</td><td>교육장소</td><td>교육내용</td>
              </tr>
              <tr>
                <td>1회차</td><td>11:00 ~ 12:00</td>
                <td rowspan="2">암사동 유적지 주차장</td><td rowspan="2">차량 실습교육</td>
              </tr>
              <tr><td>2회차</td><td>14:00 ~ 15:00</td></tr>
            </tbody></table>
            """,
        )
    )

    assert gangdong._reserve_event_detail(row, soup) == []
    assert row["branch"] == "암사동 유적지 주차장"
    assert row["raw_fields"]["venue_evidence"] == "detail_schedule_table"
    assert row["target"] == "강동구민"
    assert row["fee"] == "무료"
    assert row["schedule_raw"] == "11:00 ~ 12:00 / 14:00 ~ 15:00"


def test_event_table_values_ignore_vertical_label_value_tables() -> None:
    body = gangdong._coerce_soup(
        """
        <div class="basicContent"><table><tbody>
          <tr><td>행사명</td><td>세무 설명회</td></tr>
          <tr><td>일시</td><td>2026. 9. 30. 14:00~17:00</td></tr>
          <tr><td>장소</td><td>강동구민회관</td></tr>
        </tbody></table></div>
        """
    ).select_one(".basicContent")

    assert gangdong._event_table_values(body, {"일시"}) == []


def test_health_snapshot_supports_official_list_only_scheduled_row() -> None:
    mapping = {
        gangdong.gangdong_health_list_url(1): _health_list(),
        gangdong.gangdong_health_detail_url("20260702110730258"): _health_detail(),
    }
    fetch, make_session, sessions = _mapping_fixture(mapping)
    rows, parser, meta = gangdong.collect_gangdong_health(
        _target(gangdong.GANGDONG_HEALTH_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=10,
        max_workers=1,
    )

    assert parser == gangdong.GANGDONG_HEALTH_PARSER
    assert [row["title"] for row in rows] == ["부모 교육", "당뇨병 교실"]
    assert rows[0]["status"] == "SCHEDULED"
    assert rows[0]["raw_fields"]["detail_required"] is False
    assert rows[0]["raw_url"] == gangdong.GANGDONG_HEALTH_URL
    assert rows[1]["branch"] == "강일보건지소"
    assert rows[1]["capacity"] == 20
    assert meta["snapshot_complete"] is True
    assert meta["detail_required_count"] == 1
    assert meta["detail_exempt_count"] == 1
    assert meta["discovered_numbers"] == 2
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(session.closed for session in sessions)


def test_lifelong_active_state_branches_are_complete_and_detail_validated() -> None:
    mapping = {
        gangdong.gangdong_lll_list_url("eYet", 1): _lll_list("6615", "숲속 생태 강좌", "접수중"),
        gangdong.gangdong_lll_list_url("eIng", 1): _lll_list("6575", "그림책 강좌", "교육진행"),
        gangdong.gangdong_lll_detail_url("6615"): _lll_detail(
            "숲속 생태 강좌", "강동숲속도서관 지하1층 강의실1"
        ),
        gangdong.gangdong_lll_detail_url("6575"): _lll_detail(
            "그림책 강좌", "성내도서관 문화강좌실"
        ),
    }
    fetch, make_session, sessions = _mapping_fixture(mapping)
    rows, parser, meta = gangdong.collect_gangdong_lll(
        _target(gangdong.GANGDONG_LLL_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=10,
        max_workers=1,
    )

    assert parser == gangdong.GANGDONG_LLL_PARSER
    assert [row["raw_fields"]["source_id"] for row in rows] == ["6615", "6575"]
    assert [row["branch"] for row in rows] == [
        "강동숲속도서관 지하1층 강의실1",
        "성내도서관 문화강좌실",
    ]
    assert all(row["end_date"] >= "2026-07-19" for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 2
    assert meta["detail_pages"] == 2
    assert set(meta["active_states"]) == {"eYet", "eIng"}
    assert all(session.closed for session in sessions)


def test_lifelong_structural_empty_active_state_is_complete() -> None:
    mapping = {
        gangdong.gangdong_lll_list_url("eYet", 1): _lll_list(
            "6615",
            "숲속 생태 강좌",
            "접수중",
        ),
        gangdong.gangdong_lll_list_url("eIng", 1): _lll_empty_list(),
        gangdong.gangdong_lll_detail_url("6615"): _lll_detail(
            "숲속 생태 강좌",
            "강동숲속도서관 지하1층 강의실1",
        ),
    }
    fetch, make_session, sessions = _mapping_fixture(mapping)
    rows, _parser, meta = gangdong.collect_gangdong_lll(
        _target(gangdong.GANGDONG_LLL_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=10,
        max_workers=1,
    )

    assert len(rows) == 1
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 1
    assert meta["active_states"]["eIng"]["structural_empty"] == 1
    assert rows[0]["target"] == "대상 별도 안내"
    assert rows[0]["fee"] == "무료"
    assert rows[0]["schedule_raw"] == "매주 수 10:00~12:00"
    assert rows[0]["venue_name"] == "강동숲속도서관 지하1층 강의실1"
    assert "02-0000-0000" not in repr(rows)
    assert all(session.closed for session in sessions)


def test_dispatcher_rejects_near_match_without_fetching() -> None:
    rows, parser, meta = gangdong.collect_gangdong_courses(
        Target(gangdong.GANGDONG_RESERVE_PROVIDER, gangdong.GANGDONG_RESERVE_URL + "#x"),
        fetcher=lambda *_args: pytest.fail("near match must not fetch"),
        session_factory=lambda: pytest.fail("near match must not make a session"),
        today="2026-07-19",
    )
    assert rows == []
    assert parser == "gangdong_target_mismatch"
    assert meta["snapshot_complete"] is False


def test_library_all_declared_sections_are_complete_and_details_lock_branches() -> None:
    reading_current = _library_card(
        "reading",
        "101",
        "여름 책놀이",
        library="천호",
        schedule="2026-08-01 ~ 2026-08-02",
        venue="-",
    )
    reading_bad_archive = _library_card(
        "reading",
        "99",
        "과거 공식 역전 날짜",
        library="해공",
        schedule="2025-04-09 ~ 2025-04-08",
        venue="다목적홀",
        status="종료",
    )
    special_current = _library_card(
        "special",
        "202",
        "문해력 특성화",
        library="강일",
        schedule="2026.07.19 ~ 2026.08.16",
        venue="4층 아름터",
        status="종료",
        paginated_style=False,
    )
    mapping = {
        gangdong.gangdong_library_list_url("reading", 1): _library_page(
            reading_current, reading_bad_archive
        ),
        gangdong.gangdong_library_list_url("reading", 2): _library_page(),
        gangdong.gangdong_library_list_url("special", 1): _library_page(special_current),
        gangdong.gangdong_library_list_url("reading_club", 1): _library_page(),
        gangdong.gangdong_library_list_url("reading_club", 2): _library_page(),
        gangdong.gangdong_library_list_url("book_festival", 1): _library_page(),
        gangdong.gangdong_library_list_url("book_festival", 2): _library_page(),
        gangdong.gangdong_library_list_url("itbookin", 1): _library_page(),
        gangdong.gangdong_library_list_url("itbookin", 2): _library_page(),
        gangdong.gangdong_library_detail_url("reading", "101"): _library_detail(
            "여름 책놀이", "천호", "2026-08-01 ~ 2026-08-02", "-"
        ),
        gangdong.gangdong_library_detail_url("special", "202"): _library_detail(
            "문해력 특성화", "강일", "2026.07.19 ~ 2026.08.16", "4층 아름터"
        ),
    }
    fetch, make_session, sessions = _mapping_fixture(mapping)
    rows, parser, meta = gangdong.collect_gangdong_library(
        _target(gangdong.GANGDONG_LIBRARY_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=10,
        max_workers=1,
    )

    assert parser == gangdong.GANGDONG_LIBRARY_PARSER
    assert [row["title"] for row in rows] == ["여름 책놀이", "문해력 특성화"]
    assert [row["branch"] for row in rows] == ["천호도서관", "강일도서관 4층 아름터"]
    assert [row["venue_address"] for row in rows] == [
        "서울특별시 강동구 성안로31마길 1",
        "서울특별시 강동구 아리수로93길 9-14 4,5층",
    ]
    assert all(row["branch_location_verified"] is True for row in rows)
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 3
    assert meta["expired_count"] == 1
    assert meta["sentinel_pages"] == 4
    assert meta["source_sections"]["special"]["exposed"] == 1
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(session.closed for session in sessions)


def test_50plus_complete_snapshot_uses_detail_dates_and_venue() -> None:
    mapping = {
        gangdong.gangdong_50plus_list_url(1): _50plus_page(),
        gangdong.gangdong_50plus_list_url(2): "<div class='campus-course-list-table'></div>",
        gangdong.gangdong_50plus_detail_url("70001"): _50plus_detail(),
    }
    fetch, make_session, sessions = _mapping_fixture(mapping)
    rows, parser, meta = gangdong.collect_gangdong_50plus(
        _target(gangdong.GANGDONG_50PLUS_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=10,
        max_workers=1,
    )

    assert parser == gangdong.GANGDONG_50PLUS_PARSER
    assert len(rows) == 1
    assert rows[0]["title"] == "AI 실무 교육"
    assert rows[0]["branch"] == "강동50플러스센터 4층 401호 배움실"
    assert rows[0]["capacity"] == 15
    assert rows[0]["reservation_available"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 1
    assert meta["detail_pages"] == 1
    assert meta["sentinel_pages"] == 1
    assert all(session.closed for session in sessions)


def test_library_detail_cap_is_fail_closed() -> None:
    mapping = {
        gangdong.gangdong_library_list_url("reading", 1): _library_page(
            _library_card(
                "reading",
                "101",
                "여름 책놀이",
                library="천호",
                schedule="2026-08-01 ~ 2026-08-02",
                venue="-",
            )
        ),
        gangdong.gangdong_library_list_url("reading", 2): _library_page(),
        gangdong.gangdong_library_list_url("special", 1): _library_page(),
        gangdong.gangdong_library_list_url("reading_club", 1): _library_page(),
        gangdong.gangdong_library_list_url("reading_club", 2): _library_page(),
        gangdong.gangdong_library_list_url("book_festival", 1): _library_page(),
        gangdong.gangdong_library_list_url("book_festival", 2): _library_page(),
        gangdong.gangdong_library_list_url("itbookin", 1): _library_page(),
        gangdong.gangdong_library_list_url("itbookin", 2): _library_page(),
    }
    fetch, make_session, _sessions = _mapping_fixture(mapping)
    rows, _parser, meta = gangdong.collect_gangdong_library(
        _target(gangdong.GANGDONG_LIBRARY_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=0,
        max_workers=1,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert meta["detail_required_count"] == 1


def test_resident_centres_complete_snapshot_validates_detail_branch_and_sentinel() -> None:
    mapping = {
        gangdong.gangdong_jumin_list_url(1): _jumin_page(
            _jumin_card("3062", "성인 영어회화", status="모집대기")
        ),
        gangdong.gangdong_jumin_list_url(2): _jumin_page(
            "<li class='group clearfix'></li>"
        ),
        gangdong.gangdong_jumin_detail_url("3062"): _jumin_detail(
            "3062", "성인 영어회화"
        ),
    }
    fetch, make_session, sessions = _mapping_fixture(mapping)
    rows, parser, meta = gangdong.collect_gangdong_jumin(
        _target(gangdong.GANGDONG_JUMIN_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=10,
        max_workers=1,
    )

    assert parser == gangdong.GANGDONG_JUMIN_PARSER
    assert [row["title"] for row in rows] == ["성인 영어회화"]
    assert rows[0]["branch"] == "천호3동 주민센터 3층 2강의실"
    assert rows[0]["capacity"] == 20 and rows[0]["enrolled"] == 3
    assert rows[0]["status"] == "SCHEDULED"
    assert rows[0]["target"] == "강동구민"
    assert rows[0]["fee"] == "30,000 원"
    assert rows[0]["category"] == "주민자치"
    assert rows[0]["schedule_raw"] == "월,금 10:00 ~ 11:00"
    assert rows[0]["service_group"] == "공공강좌"
    assert rows[0]["service_group_policy"] == "locked"
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 1
    assert meta["sentinel_pages"] == 1
    assert meta["sentinel_placeholders"] == 1
    assert meta["detail_pages"] == 1
    assert all(session.closed for session in sessions)


def test_future_on_all_declared_terms_and_venue_evidence_are_complete() -> None:
    located = _slc_course(
        2001,
        2,
        "미래인재 여름교실",
        location="<p>장소: 미래교육혁신센터 소강의실3 주소: 구천면로395, 3층</p>",
    )
    online = _slc_course(
        2069,
        2,
        "[2026 강동 스마트 캠퍼스] 4회차",
        details="본 프로그램은 실시간 온라인으로 진행되는 고등학생 대상 진로 수업입니다.",
    )
    arts = _slc_course(
        2058,
        5,
        "[강동아트센터] 2027학년도 대입전략설명회",
        details="현장강의로만 진행됩니다.",
    )
    rows_by_term = {2: [located, online], 5: [arts]}
    mapping: dict[
        tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]
    ] = {}
    for menu_id, term_id, menu_key, parent_id, menu_title in gangdong.GANGDONG_SLC_TERMS:
        mapping[
            _json_key(
                gangdong.gangdong_slc_menu_api_url(),
                {"id": str(menu_id), "isAvailable": "1"},
            )
        ] = {
            "code": 10000,
            "body": {
                "id": menu_id,
                "parent_id": parent_id,
                "title": menu_title,
                "key": menu_key,
                "is_available": 1,
                "is_deleted": 0,
            },
        }
        term_rows = rows_by_term.get(term_id, [])
        mapping[
            _json_key(
                gangdong.gangdong_slc_list_api_url(),
                gangdong._slc_list_payload(term_id, 1),
            )
        ] = _slc_response({"total_count": len(term_rows), "list": term_rows})
        mapping[
            _json_key(
                gangdong.gangdong_slc_list_api_url(),
                gangdong._slc_list_payload(term_id, 2),
            )
        ] = _slc_response({"total_count": len(term_rows), "list": []})
    for course in (located, online, arts):
        mapping[
            _json_key(
                gangdong.gangdong_slc_detail_api_url(),
                gangdong._slc_detail_payload(course["term_id"], str(course["id"])),
            )
        ] = _slc_response(course)
    poster, make_session, sessions = _json_fixture(mapping)
    rows, parser, meta = gangdong.collect_gangdong_slc(
        _target(gangdong.GANGDONG_SLC_PROVIDER),
        json_poster=poster,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=10,
        max_workers=1,
    )

    assert parser == gangdong.GANGDONG_SLC_PARSER
    assert [row["branch"] for row in rows] == [
        "미래교육혁신센터 소강의실3",
        "온라인 실시간",
        "강동아트센터",
    ]
    assert [row["raw_fields"]["venue_evidence"] for row in rows] == [
        "location",
        "detail_online_text",
        "detail_title_prefix",
    ]
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 3
    assert meta["pages"] == len(gangdong.GANGDONG_SLC_TERMS)
    assert meta["sentinel_pages"] == len(gangdong.GANGDONG_SLC_TERMS)
    assert meta["menu_declarations"] == len(gangdong.GANGDONG_SLC_TERMS)
    assert meta["detail_pages"] == 3
    assert all(session.closed for session in sessions)
