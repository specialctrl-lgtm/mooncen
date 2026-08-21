from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
import requests

from Crawler import Crawler_MunicipalYaml as municipal


TOKEN_ONE = "fixture.jwt.one"
TOKEN_TWO = "fixture.jwt.two"


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: list[tuple[str, FakeResponse]]) -> None:
        self.responses = list(responses)
        self.headers = {"User-Agent": "fixture-agent"}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        assert self.responses, f"unexpected request: {url}"
        expected_url, response = self.responses.pop(0)
        assert url == expected_url
        return response


def _token(value: str, *, expires_at: int = 9_999_999_999_999) -> FakeResponse:
    return FakeResponse({"jwt": value, "expiresAt": expires_at})


def _course(
    uid: str,
    title: str,
    *,
    status: str,
    start: str,
    end: str,
    current: int = 7,
    capacity: int = 20,
    waitlist_current: int = 1,
    waitlist_total: int = 5,
    extra: str = "",
) -> dict[str, Any]:
    return {
        "crsUid": uid,
        "crsNm": title,
        "crsStatus": status,
        "crsStts": "2",
        "crsBgngYmd": start,
        "crsBgngTm": "10:00",
        "crsEndYmd": end,
        "crsEndTm": "12:00",
        "rcptBgngYmd": "2099-06-01",
        "rcptBgngTm": "10:00",
        "rcptEndYmd": "2099-06-30",
        "rcptEndTm": "23:59",
        "crsPlc": "회관 4층 문화강좌실",
        "crsTrgt": "대구지역 학부모 및 성인 20명",
        "instrNm": "김은아",
        "crsRcrtNope": capacity,
        "crsRcrtCnt": current,
        "crsRcrtRsrvNope": waitlist_total,
        "crsRsrvCnt": waitlist_current,
        "crsDowArr": ["5"],
        "lctrDowDrctInpt": "매주 목요일",
        "groupNm": "프로그램",
        "ctgryNm": None,
        "ctgryUid": "16",
        "hmpgUid": "h2",
        "hmpgNm": "대구2ㆍ28민주운동기념도서관",
        "hmpgAddr": "대구 중구 2.28길 9 (남산동)",
        "crsEtc": extra,
        "crsExpln": "질문과 토론으로 배우는 &lt;시민 교육&gt;",
        "crsNmtm": 8,
        "crsPicTelno": "053-257-2280",
        "delYn": "N",
        "useYn": "Y",
    }


def _api(items: list[dict[str, Any]], *, status_code: int = 200) -> FakeResponse:
    return FakeResponse(
        {
            "result": True,
            "code": "SUCCESS",
            "message": None,
            "data": {"courseList": items},
        },
        status_code=status_code,
    )


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=municipal.DAEGU_228_LIBRARY_PROVIDER,
        name="대구2·28민주운동기념도서관 수강신청",
        branch="대구광역시 중구",
        url="https://library.daegu.go.kr/228lib/module/course/index.do?menu_idx=30",
        source="test",
        priority=1,
        region="대구광역시 중구",
        extra={
            "service_group": "공공강좌",
            "service_group_policy": "locked",
        },
    )


def test_daegu_228_dispatch_maps_public_api_and_filters_expired_before_output(monkeypatch) -> None:
    active_closed = _course(
        "01TESTCURRENTCOURSE000000001",
        "작가와의 만남 &lt;행복은 어디서 오는 거니?&gt;",
        status="5",
        start="2099-07-25",
        end="2099-07-25",
        current=20,
        capacity=20,
        waitlist_current=5,
        waitlist_total=5,
        extra="수강료 무료, 재료비 20,000원",
    )
    active_closed["ctgryNm"] = "성인"
    active_open = _course(
        "28018",
        "2.28독서동아리",
        status="0",
        start="2099-01-07",
        end="2099-12-16",
    )
    ended_with_future_date = _course(
        "8048",
        "상태로 종료된 강좌",
        status="9",
        start="2099-08-06",
        end="2099-08-27",
    )
    expired = _course(
        "8049",
        "날짜로 종료된 강좌",
        status="4",
        start="2020-08-06",
        end="2020-08-27",
    )
    deleted = _course("8050", "삭제 강좌", status="0", start="2099-01-01", end="2099-12-31")
    deleted["delYn"] = "Y"
    disabled = _course("8051", "비활성 강좌", status="0", start="2099-01-01", end="2099-12-31")
    disabled["useYn"] = "N"
    fake_session = FakeSession(
        [
            (municipal.DAEGU_228_LIBRARY_TOKEN_URL, _token(TOKEN_ONE)),
            (
                municipal.DAEGU_228_LIBRARY_API_URL,
                _api([active_closed, active_open, ended_with_future_date, expired, deleted, disabled]),
            ),
        ]
    )
    monkeypatch.setattr(municipal, "session", lambda: fake_session)

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=3, detail_limit=0
    )

    assert parser == "daegu_228_library_course_api"
    assert len(rows) == 2
    by_uid = {row["raw_fields"]["course_uid"]: row for row in rows}
    closed = by_uid["01TESTCURRENTCOURSE000000001"]
    assert closed["provider_course_id"] == (
        f"{municipal.DAEGU_228_LIBRARY_PROVIDER}:crs:01TESTCURRENTCOURSE000000001"
    )
    assert closed["title"] == "작가와의 만남 <행복은 어디서 오는 거니?>"
    assert closed["raw_url"] == (
        "https://library.daegu.go.kr/228lib/module/course/detail.do?"
        "menu_idx=30&homepage_id=h2&crsUid=01TESTCURRENTCOURSE000000001"
    )
    assert "application_url" not in closed
    assert closed["branch"] == municipal.DAEGU_228_LIBRARY_BRANCH
    assert closed["branch_code"] == municipal.DAEGU_228_LIBRARY_BRANCH_CODE
    assert closed["preserve_branch"] is True
    assert closed["address"] == "대구 중구 2.28길 9 (남산동)"
    assert closed["venue_address"] == closed["address"]
    assert closed["venue_name"] == municipal.DAEGU_228_LIBRARY_BRANCH
    assert closed["room"] == "회관 4층 문화강좌실"
    assert closed["category"] == "프로그램 · 성인"
    assert closed["instructor"] == "김은아"
    assert closed["target"] == "대구지역 학부모 및 성인 20명"
    assert closed["status"] == "CLOSED"
    assert closed["reservation_available"] is False
    assert closed["fee"] == "무료"
    assert closed["material_fee"] == 20_000
    assert closed["material_note"] == "수강료 무료, 재료비 20,000원"
    assert closed["capacity_current"] == 20
    assert closed["capacity_total"] == 20
    assert closed["capacity_remaining"] == 0
    assert closed["waitlist_current"] == 5
    assert closed["waitlist_total"] == 5
    assert closed["period"] == "2099-07-25"
    assert closed["start_date"] == date(2099, 7, 25)
    assert closed["end_date"] == date(2099, 7, 25)
    assert closed["apply_period"] == "2099-06-01 10:00 ~ 2099-06-30 23:59"
    assert closed["sessions"] == 8
    assert "매주 목요일" in closed["schedule_raw"]
    assert "10:00 ~ 12:00" in closed["schedule_raw"]
    assert "<시민 교육>" in closed["description"]
    assert "재료비" not in closed["description"]
    assert by_uid["28018"]["reservation_available"] is True
    assert by_uid["28018"]["application_url"] == by_uid["28018"]["raw_url"]

    assert meta["pages"] == 1
    assert meta["detail_pages"] == 0
    assert meta["total_count"] == 6
    assert meta["discovered_links"] == 6
    assert meta["reservation_discovery_links"] == 1
    assert meta["expired_count"] == 1
    assert meta["ended_status_count"] == 1
    assert meta["hidden_count"] == 2
    assert meta["invalid_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["source_cap_reached"] is False
    assert meta["no_current_data"] is False

    assert fake_session.headers == {"User-Agent": "fixture-agent"}
    assert "headers" not in fake_session.calls[0][1]
    assert fake_session.calls[1][1]["headers"] == {"Authorization": f"Bearer {TOKEN_ONE}"}
    serialized_rows = json.dumps(rows, ensure_ascii=False, default=str)
    assert TOKEN_ONE not in serialized_rows
    assert TOKEN_TWO not in serialized_rows


def test_daegu_228_401_refreshes_public_token_exactly_once(monkeypatch) -> None:
    fake_session = FakeSession(
        [
            (municipal.DAEGU_228_LIBRARY_TOKEN_URL, _token(TOKEN_ONE)),
            (municipal.DAEGU_228_LIBRARY_API_URL, _api([], status_code=401)),
            (municipal.DAEGU_228_LIBRARY_TOKEN_URL, _token(TOKEN_TWO)),
            (municipal.DAEGU_228_LIBRARY_API_URL, _api([])),
        ]
    )
    monkeypatch.setattr(municipal, "session", lambda: fake_session)

    rows, parser, meta = municipal.collect_daegu_228_library_courses(
        _target(), timeout=6, max_pages=1
    )

    assert rows == []
    assert parser == "daegu_228_library_course_api"
    assert meta["no_current_data"] is True
    assert [url for url, _kwargs in fake_session.calls] == [
        municipal.DAEGU_228_LIBRARY_TOKEN_URL,
        municipal.DAEGU_228_LIBRARY_API_URL,
        municipal.DAEGU_228_LIBRARY_TOKEN_URL,
        municipal.DAEGU_228_LIBRARY_API_URL,
    ]
    assert fake_session.calls[1][1]["headers"]["Authorization"] == f"Bearer {TOKEN_ONE}"
    assert fake_session.calls[3][1]["headers"]["Authorization"] == f"Bearer {TOKEN_TWO}"
    assert "Authorization" not in fake_session.headers


def test_daegu_228_403_fails_without_refresh(monkeypatch) -> None:
    fake_session = FakeSession(
        [
            (municipal.DAEGU_228_LIBRARY_TOKEN_URL, _token(TOKEN_ONE)),
            (municipal.DAEGU_228_LIBRARY_API_URL, _api([], status_code=403)),
        ]
    )
    monkeypatch.setattr(municipal, "session", lambda: fake_session)

    with pytest.raises(requests.HTTPError, match="403"):
        municipal.collect_daegu_228_library_courses(_target(), timeout=6, max_pages=1)

    assert len(fake_session.calls) == 2
    assert not fake_session.responses
    assert "Authorization" not in fake_session.headers


def test_daegu_228_refreshes_expiring_token_before_api_request(monkeypatch) -> None:
    fake_session = FakeSession(
        [
            (municipal.DAEGU_228_LIBRARY_TOKEN_URL, _token(TOKEN_ONE, expires_at=1)),
            (municipal.DAEGU_228_LIBRARY_TOKEN_URL, _token(TOKEN_TWO)),
            (municipal.DAEGU_228_LIBRARY_API_URL, _api([])),
        ]
    )
    monkeypatch.setattr(municipal, "session", lambda: fake_session)

    items = municipal.daegu_228_library_course_items(fake_session, timeout=5)

    assert items == []
    assert fake_session.calls[2][1]["headers"]["Authorization"] == f"Bearer {TOKEN_TWO}"
    assert "Authorization" not in fake_session.headers


def test_daegu_228_all_expired_is_complete_no_current(monkeypatch) -> None:
    expired = _course(
        "8048",
        "종료된 강좌",
        status="4",
        start="2020-08-06",
        end="2020-08-27",
    )
    fake_session = FakeSession(
        [
            (municipal.DAEGU_228_LIBRARY_TOKEN_URL, _token(TOKEN_ONE)),
            (municipal.DAEGU_228_LIBRARY_API_URL, _api([expired])),
        ]
    )
    monkeypatch.setattr(municipal, "session", lambda: fake_session)

    rows, _parser, meta = municipal.collect_daegu_228_library_courses(
        _target(), timeout=5, max_pages=1
    )

    assert rows == []
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "all API courses are ended, hidden, or expired"


@pytest.mark.parametrize("status_code", ["0", "1", "11"])
def test_daegu_228_open_statuses_have_real_application_url(status_code: str) -> None:
    item = _course(
        f"OPEN{status_code}",
        "신청 가능 강좌",
        status=status_code,
        start="2099-01-01",
        end="2099-12-31",
    )

    row = municipal.daegu_228_library_course_row(_target(), item)

    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["application_url"] == row["raw_url"]


@pytest.mark.parametrize("status_code", ["2", "3", "4", "5", "10", "12"])
def test_daegu_228_closed_statuses_never_expose_application_url(status_code: str) -> None:
    item = _course(
        f"CLOSED{status_code}",
        "신청 불가 강좌",
        status=status_code,
        start="2099-01-01",
        end="2099-12-31",
    )

    row = municipal.daegu_228_library_course_row(_target(), item)

    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert "application_url" not in row


def test_daegu_228_scheduled_status_never_exposes_application_url() -> None:
    item = _course(
        "SCHEDULED6",
        "접수 예정 강좌",
        status="6",
        start="2099-01-01",
        end="2099-12-31",
    )

    row = municipal.daegu_228_library_course_row(_target(), item)

    assert row["status"] == "SCHEDULED"
    assert row["reservation_available"] is False
    assert "application_url" not in row


def test_daegu_228_detail_url_rejects_noncanonical_course_uid() -> None:
    assert municipal.daegu_228_library_detail_url("28018") == (
        "https://library.daegu.go.kr/228lib/module/course/detail.do?"
        "menu_idx=30&homepage_id=h2&crsUid=28018"
    )
    assert municipal.daegu_228_library_detail_url("01KTXG3YQWVDGJ2VJG3YM6EW4K")
    assert municipal.daegu_228_library_detail_url("../../bad") == ""
    assert municipal.daegu_228_library_detail_url("uid?token=secret") == ""
