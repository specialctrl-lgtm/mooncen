from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_seoul_junggu as junggu


@dataclass(frozen=True)
class Target:
    provider: str
    url: str


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(url: str = junggu.SEOUL_JUNGGU_EDUCATION_URL) -> Target:
    return Target(junggu.SEOUL_JUNGGU_EDUCATION_PROVIDER, url)


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _native_card(identity: str, title: str) -> str:
    return f"""
    <li>
      <span class="small_tit">구청 교육장</span>
      <strong class="tit">{title}</strong>
      <div class="dl_wrap">
        <dl><dt>접수기간</dt><dd>2026-07-01 ~ 2026-07-31</dd></dl>
        <dl><dt>지원대상</dt><dd>중구민</dd></dl>
      </div>
      <a class="now_type" href="https://www.junggu.seoul.kr/content.do?cmsid=14235&amp;command=view&amp;lec_idx={identity}">모집중</a>
    </li>
    """


def _native_list(*cards: str, total: int) -> str:
    return f"""
    <html><body>
      <div class="page_num"><span>총 {total} 개</span></div>
      <div class="edu_list_wrap"><ul>{''.join(cards)}</ul></div>
    </body></html>
    """


def _native_detail(identity: str, title: str, period: str) -> str:
    pairs = [
        ("담당부서", "디지털정책과"),
        ("강좌명", title),
        ("구분", "정보화교육"),
        ("교육대상", "중구민"),
        ("강사명", "김강사"),
        ("교육기간", period),
        ("교육시간", "월 10:00 ~ 12:00"),
        ("접수기간", "2026-07-01 ~ 2026-07-31"),
        ("접수방법", "인터넷"),
        ("문의전화", "02-3396-0000"),
        ("교육장소", "구청 교육장"),
        ("정원", "20명"),
        ("수강료", "무료"),
        ("강좌소개", "정보화 교육 과정"),
    ]
    rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in pairs)
    return f"""
    <html><body><table>{rows}</table>
      <a class="btn_write" href="/content.do?cmsid=14235&amp;command=write&amp;lec_idx={identity}">신청</a>
    </body></html>
    """


def _myhand_list_payload(*, total: int = 3) -> dict[str, Any]:
    rows = [
        {
            "businessId": 1001,
            "name": "여름 미술 교실",
            "typeName": "PROGRAM",
            "host": "가온도서관",
            "oname": "중구도서관",
            "startDate": "2026-07-10",
            "endDate": "2026-08-20",
        },
        {
            "businessId": 1002,
            "name": "반려식물 클리닉",
            "typeName": "PROGRAM",
            "host": "공원녹지과",
            "oname": "공원녹지과",
            "startDate": "2026-07-10",
            "endDate": "2026-12-31",
        },
        {
            "businessId": 1003,
            "name": "세금 지원 정책",
            "typeName": "WLF",
            "host": "세무과",
            "oname": "세무과",
            "startDate": "2026-01-01",
            "endDate": "2026-12-31",
        },
    ]
    return {
        "code": "success",
        "data": {
            "total": total,
            "list": rows,
            "pageNum": 1,
            "pageSize": junggu.MYHAND_PAGE_SIZE,
            "size": len(rows),
            "startRow": 1,
            "endRow": len(rows),
            "pages": 1,
        },
    }


def _myhand_detail(
    identity: int,
    title: str,
    *,
    branch: str,
    tags: str,
    business_code_id: int | None = None,
) -> str:
    data = {
        "businessId": identity,
        "businessName": title,
        "businessType": "PROGRAM",
        "businessCodeId": business_code_id,
        "businessCodeName": "자치회관프로그램" if business_code_id == 54 else None,
        "categoryNames": tags,
        "startDate": "2026-07-10",
        "endDate": "2026-08-20" if identity == 1001 else "2026-12-31",
        "applyStartDate": "2026-07-01",
        "applyEndDate": "2026-07-31",
        "studyStartDate": "2026-08-01" if identity == 1001 else None,
        "studyEndDate": "2026-08-20" if identity == 1001 else None,
        "studyStartTime": "10:00:00",
        "studyEndTime": "12:00:00",
        "studyWeekend": "SAT",
        "host": branch,
        "organizationName": branch,
        "organizationCodeId": None,
        "place": f"{branch} 강의실",
        "contact": "02-3396-1111",
        "clientCount": 15,
        "cost": 0,
        "applyFormId": 501 if identity == 1001 else 502,
        "useBusinessSubYn": "N",
        "clientRule": "중구민",
        "content": "교육 내용",
    }
    return (
        "<html><body><script>const app = { businessData: "
        + json.dumps(data, ensure_ascii=False)
        + ", other: true };</script></body></html>"
    )


def _fixture_fetcher(
    *,
    payload: dict[str, Any] | None = None,
) -> Callable[[Any, str, int], Any]:
    native_page = _native_list(
        _native_card("101", "현재 정보화 과정"),
        _native_card("102", "종료 정보화 과정"),
        total=2,
    )
    native_details = {
        "101": _native_detail("101", "현재 정보화 과정", "2026-07-20 ~ 2026-08-20"),
        "102": _native_detail("102", "종료 정보화 과정", "2026-05-01 ~ 2026-06-01"),
    }
    myhand_details = {
        "1001": _myhand_detail(
            1001,
            "여름 미술 교실",
            branch="가온도서관",
            tags="자녀가족,취미교육",
        ),
        "1002": _myhand_detail(
            1002,
            "반려식물 클리닉",
            branch="공원녹지과",
            tags="기타,생활편의",
        ),
    }

    def fetch(_session: Any, url: str, _timeout: int) -> Any:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.netloc == junggu.JUNGGU_HOST and parsed.path == junggu.JUNGGU_LIST_PATH:
            assert query["type1"] == [junggu.JUNGGU_NATIVE_CATEGORY]
            assert query["page"] == ["1"]
            return _soup(native_page)
        if parsed.netloc == junggu.JUNGGU_HOST:
            return _soup(native_details[query["lec_idx"][0]])
        if parsed.path == junggu.MYHAND_LIST_PATH:
            assert query["pageSize"] == [str(junggu.MYHAND_PAGE_SIZE)]
            assert query["endBusinessIncude"] == ["1"]
            return payload or _myhand_list_payload()
        identity = parsed.path.rstrip("/").split("/")[-1]
        return _soup(myhand_details[identity])

    return fetch


def test_route_and_official_upstream_urls_are_exact() -> None:
    assert junggu.is_target(_target()) is True
    assert junggu.is_target(
        _target(junggu.SEOUL_JUNGGU_EDUCATION_URL + "&page=1")
    ) is False

    native = urlparse(junggu.native_list_url(2))
    assert native.netloc == junggu.JUNGGU_HOST
    assert parse_qs(native.query) == {
        "cmsid": ["16554"],
        "type1": ["정보화교육"],
        "page": ["2"],
    }
    myhand = urlparse(junggu.myhand_list_url())
    query = parse_qs(myhand.query, keep_blank_values=True)
    assert myhand.netloc == junggu.MYHAND_HOST
    assert query["pageSize"] == ["500"]
    assert query["businessTypeArr"] == ["[]"]
    assert query["endBusinessIncude"] == ["1"]


def test_complete_dual_upstream_snapshot_filters_expired_and_non_course() -> None:
    sessions: list[DummySession] = []

    def session_factory() -> DummySession:
        value = DummySession()
        sessions.append(value)
        return value

    rows, parser, meta = junggu.collect_seoul_junggu_education(
        _target(),
        timeout=5,
        max_pages=5,
        detail_limit=10,
        fetcher=_fixture_fetcher(),
        session_factory=session_factory,
        today="2026-07-19",
        max_workers=2,
    )

    assert parser == junggu.SEOUL_JUNGGU_EDUCATION_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_total"] == 5
    assert meta["native_total"] == 2
    assert meta["myhand_total"] == 3
    assert meta["myhand_detail_candidates"] == 2
    assert meta["detail_pages"] == 4
    assert meta["expired_count"] == 1
    assert meta["excluded_non_course_count"] == 1
    assert meta["current_count"] == 2
    assert all(value.closed for value in sessions)

    by_id = {row["provider_course_id"]: row for row in rows}
    native_id = f"{junggu.SEOUL_JUNGGU_EDUCATION_PROVIDER}:junggu-lecture:101"
    myhand_id = f"{junggu.SEOUL_JUNGGU_EDUCATION_PROVIDER}:myhand-business:1001"
    assert set(by_id) == {native_id, myhand_id}
    assert by_id[native_id]["branch"] == "디지털정책과"
    assert by_id[native_id]["end_date"].isoformat() == "2026-08-20"
    assert by_id[native_id]["status"] == "OPEN"
    assert by_id[myhand_id]["branch"] == "가온도서관"
    assert by_id[myhand_id]["category"] == "취미교육"
    assert by_id[myhand_id]["application_url"].endswith("/1001/501")
    for row in rows:
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["preserve_branch"] is True


def test_detail_cap_blocks_full_snapshot_without_silent_partial_success() -> None:
    rows, _parser, meta = junggu.collect_seoul_junggu_education(
        _target(),
        timeout=5,
        max_pages=5,
        detail_limit=3,
        fetcher=_fixture_fetcher(),
        session_factory=DummySession,
        today="2026-07-19",
        max_workers=2,
    )

    assert len(rows) == 2
    assert meta["snapshot_complete"] is False
    assert meta["details_complete"] is False
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 3
    assert "detail_limit cap allows 3 of 4" in meta["configured_collection_error"]


def test_official_community_centre_code_admits_untagged_course_not_rental() -> None:
    listed = {
        "official_id": "1002",
        "title": "회현쿵짝 노래교실",
        "business_type": "PROGRAM",
        "end_date": "2026-12-31",
        "raw_url": junggu.myhand_detail_url("1002"),
    }
    detail = _myhand_detail(
        1002,
        "회현쿵짝 노래교실",
        branch="회현동",
        tags="기타,문화축제",
        business_code_id=54,
    )

    row, state, errors = junggu._myhand_detail_row(
        _target(), listed, _soup(detail), junggu._today("2026-07-19")
    )

    assert errors == []
    assert state == "current"
    assert row is not None
    assert row["category"] == "자치회관"
    assert row["raw_fields"]["business_code_id"] == "54"

    rental_title = "[청구동] 자치회관 공유물품 대여"
    rental = dict(listed, title=rental_title)
    rental_detail = _myhand_detail(
        1002,
        rental_title,
        branch="청구동",
        tags="기타,생활편의",
        business_code_id=54,
    )
    rental_row, rental_state, rental_errors = junggu._myhand_detail_row(
        _target(), rental, _soup(rental_detail), junggu._today("2026-07-19")
    )
    assert rental_errors == []
    assert rental_state == "excluded"
    assert rental_row is None


def test_myhand_declared_count_drift_fails_closed() -> None:
    payload = _myhand_list_payload(total=4)
    _rows, _parser, meta = junggu.collect_seoul_junggu_education(
        _target(),
        timeout=5,
        max_pages=5,
        detail_limit=10,
        fetcher=_fixture_fetcher(payload=payload),
        session_factory=DummySession,
        today="2026-07-19",
        max_workers=2,
    )

    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert "myhand PageInfo does not reconcile" in meta["configured_collection_error"]
    assert "myhand total 4 does not match 3 unique IDs" in meta["configured_collection_error"]
