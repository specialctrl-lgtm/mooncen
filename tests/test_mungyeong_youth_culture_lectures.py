from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_mungyeong as municipal


PROVIDER = municipal.MUNGYEONG_YOUTH_PROVIDER
TARGET_URL = municipal.MUNGYEONG_YOUTH_LIST_URL


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target(
    url: str = TARGET_URL,
    provider: str = PROVIDER,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "name": "문경시 청소년문화의집 교육강좌",
        "branch": "경상북도 문경시",
        "url": url,
        "extra": {
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
        },
    }


def _list_row(
    idx: str,
    title: str,
    status: str,
    *,
    category: str = "2099 문경시청소년 여름문화활동",
    apply_period: str = "2099-07-13 09:00 ~ 2099-07-22 18:00",
    period: str = "2099-07-28 ~ 2099-08-07 (10:00 ~ 12:00)",
    capacity: str = "5/14 (0/2)",
) -> str:
    return f"""
    <tr class="taC">
      <td>{category}</td>
      <td><a href="javascript:void(0);" data-button="view" data-idx="{idx}">{title}</a></td>
      <td>{apply_period}</td>
      <td>{period}</td>
      <td>{capacity}</td>
      <td>{status}</td>
    </tr>
    """


def _list_page(
    rows: str,
    *,
    current: int = 1,
    total: int = 1,
    keyword_type: str = "0",
    keyword: str = "",
) -> str:
    zero_selected = " selected" if keyword_type == "0" else ""
    one_selected = " selected" if keyword_type == "1" else ""
    return f"""
    <html><body>
      <form id="listForm" name="listForm" method="post"
            action="/reservation/youthCulture/lecture/list.do?mId=0109020000">
        <input type="hidden" id="currentPageNo" name="currentPageNo" value="{current}" />
        <select id="keywordType" name="keywordType">
          <option value="0"{zero_selected}>검색구분(전체)</option>
          <option value="1"{one_selected}>교육명</option>
        </select>
        <input id="keyword" name="keyword" value="{keyword}" />
        <p class="page_num">현재 페이지 {current} / 전체 페이지 {total}</p>
      </form>
      <table>
        <thead><tr>
          <th>구분</th><th>교육명</th><th>신청기간</th><th>교육기간</th>
          <th>접수자/정원 (예비자/정원)</th><th>상태</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </body></html>
    """


def _empty_sentinel() -> str:
    return _list_page(
        "",
        keyword_type="1",
        keyword=municipal.MUNGYEONG_YOUTH_EMPTY_SENTINEL_TERM,
    )


def _detail_page(
    idx: str,
    title: str,
    status: str,
    room: str,
    description: str,
    *,
    apply_period: str = "2099-07-13 09:00 ~ 2099-07-22 18:00",
    period: str = "2099-07-28 ~ 2099-08-07",
    schedule: str = "10:00 ~ 12:00",
    capacity: str = "5/14 (0/2)",
    include_application_contract: bool = True,
) -> str:
    application = ""
    if include_application_contract:
        application = f"""
          <a href="javascript:void(0);" data-button="write" data-idx="{idx}">신청</a>
          <script>
            yhEnroll.init("/reservation/youthCulture/enroll/", "0109020000");
            yhEnroll.move("write.do?mId=" + yhEnroll.menuId,
                          {{ "lectureIdx" : "{idx}" }});
          </script>
        """
    return f"""
    <html><body>
      <h4>{title}</h4>
      <table><tbody>
        <tr><th>신청기간</th><td>{apply_period}</td></tr>
        <tr><th>교육기간</th><td>{period}</td></tr>
        <tr><th>교육시간</th><td>{schedule}</td></tr>
        <tr><th>강의장소</th><td>{room}</td></tr>
        <tr><th>신청현황</th><td>{capacity}</td></tr>
        <tr><th>진행상태</th><td>{status}</td></tr>
        <tr><th>교육내용 소개</th><td>{description}</td></tr>
      </tbody></table>
      {application}
    </body></html>
    """


class FakeResponse:
    def __init__(self, body: str, status_code: int = 200) -> None:
        self.text = body
        self.content = body.encode("utf-8")
        self.status_code = status_code
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self.history: list[Any] = []

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(
        self,
        post_pages: dict[int, str] | None = None,
        sentinel: str | None = None,
    ) -> None:
        self.post_pages = post_pages or {}
        self.sentinel = sentinel if sentinel is not None else _empty_sentinel()
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts.append((url, kwargs))
        data = kwargs["data"]
        if data["keywordType"] == "1":
            return FakeResponse(self.sentinel)
        page = int(data["currentPageNo"])
        assert page in self.post_pages, f"unexpected POST page {page}"
        return FakeResponse(self.post_pages[page])

    def close(self) -> None:
        self.closed = True


def _collector(
    list_html: str,
    details: dict[str, str] | None = None,
    *,
    fake_session: FakeSession | None = None,
    target: dict[str, Any] | None = None,
    max_pages: int = 2,
    detail_limit: int = 50,
    today: date = date(2026, 7, 20),
) -> tuple[list[dict[str, Any]], str, dict[str, Any], list[str], FakeSession]:
    details = details or {}
    fetched: list[str] = []
    current_session = fake_session or FakeSession()

    def fetcher(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        fetched.append(url)
        parsed = urlparse(url)
        if parsed.path == municipal.MUNGYEONG_YOUTH_LIST_PATH:
            assert url == TARGET_URL
            return _soup(list_html)
        assert parsed.path == municipal.MUNGYEONG_YOUTH_DETAIL_PATH
        idx = parse_qs(parsed.query)["idx"][0]
        value = details[idx]
        if value == "__FAIL__":
            raise RuntimeError("fixture detail outage")
        return _soup(value)

    rows, parser, meta = municipal.collect_mungyeong_youth_culture_lectures(
        target or _target(),
        timeout=7,
        max_pages=max_pages,
        detail_limit=detail_limit,
        fetcher=fetcher,
        session_factory=lambda: current_session,
        today=today,
    )
    return rows, parser, meta, fetched, current_session


def test_mungyeong_complete_snapshot_maps_status_gated_enrollment_and_branch() -> None:
    list_html = _list_page(
        _list_row("240", "고전한문", "접수중")
        + _list_row(
            "243",
            "드럼",
            "접수중",
            period="2099-07-28 ~ 2099-08-07 (13:00 ~ 15:00)",
            capacity="12/12 (0/0)",
        )
        + _list_row(
            "244",
            "요리체험(과일타르트)",
            "대기자접수중",
            period="2099-07-31 ~ 2099-07-31 (13:00 ~ 15:00)",
            capacity="8/8 (0/2)",
        )
    )
    details = {
        "240": _detail_page(
            "240", "고전한문", "접수중", "창작공방", "초등 3학년 이상 한자급수와 천자문"
        ),
        # The official page can keep a visible 신청 button after the status closes.
        "243": _detail_page(
            "243",
            "드럼",
            "접수마감",
            "음악공방",
            "드럼 기본자세와 리듬 익히기",
            schedule="13:00 ~ 15:00",
            capacity="12/12 (0/0)",
        ),
        "244": _detail_page(
            "244",
            "요리체험(과일타르트)",
            "대기자접수중",
            "요리공방",
            "초등 3학년 이상, 재료비 25,000원 중 참가자 자부담 10,000원",
            period="2099-07-31 ~ 2099-07-31",
            schedule="13:00 ~ 15:00",
            capacity="8/8 (0/2)",
        ),
    }

    rows, parser, meta, fetched, fake_session = _collector(
        list_html, details, detail_limit=3
    )

    assert parser == municipal.MUNGYEONG_YOUTH_PARSER
    assert len(rows) == 3
    assert len({row["provider_course_id"] for row in rows}) == 3
    assert len({row["raw_url"] for row in rows}) == 3
    by_idx = {row["raw_fields"]["lecture_idx"]: row for row in rows}

    classic = by_idx["240"]
    assert classic["provider_course_id"] == f"{PROVIDER}:lecture:240"
    assert classic["raw_url"] == (
        "https://www.gbmg.go.kr/reservation/youthCulture/lecture/view.do?mId=0109020000&idx=240"
    )
    assert classic["application_url"] == (
        "https://www.gbmg.go.kr/reservation/youthCulture/enroll/write.do?mId=0109020000&lectureIdx=240"
    )
    assert classic["reservation_available"] is True
    assert classic["status"] == "OPEN"
    assert classic["period"] == "2099-07-28 ~ 2099-08-07"
    assert classic["apply_period"] == "2099-07-13 09:00 ~ 2099-07-22 18:00"
    assert classic["schedule_raw"] == "10:00 ~ 12:00"
    assert classic["start_date"] == date(2099, 7, 28)
    assert classic["end_date"] == date(2099, 8, 7)
    assert classic["capacity_current"] == 5
    assert classic["capacity_total"] == 14
    assert classic["waitlist_current"] == 0
    assert classic["waitlist_total"] == 2
    assert classic["room"] == "창작공방"
    assert "한자급수" in classic["description"]
    assert classic["fee"] == "공식 페이지 미기재"
    assert classic["raw_fields"]["fee_source"] == "official_source_unspecified"
    assert classic["raw_fields"]["fee_source_omission"] is True

    closed = by_idx["243"]
    assert closed["raw_fields"]["list_source_status"] == "접수중"
    assert closed["raw_fields"]["detail_source_status"] == "접수마감"
    assert closed["status"] == "CLOSED"
    assert closed["reservation_available"] is False
    assert not closed["application_url"]
    assert not closed["application_type"]

    waitlist = by_idx["244"]
    assert waitlist["status"] == "WAITING"
    assert waitlist["reservation_available"] is True
    assert waitlist["application_url"].endswith("lectureIdx=244")
    assert waitlist["room"] == "요리공방"
    assert waitlist["fee"] == 10_000
    assert waitlist["material_fee"] == 25_000
    assert waitlist["raw_fields"]["fee_source"] == "description:자부담"
    assert "fee_source_omission" not in waitlist["raw_fields"]
    for row in rows:
        assert row["provider"] == PROVIDER
        assert row["branch"] == municipal.MUNGYEONG_YOUTH_BRANCH
        assert row["branch_code"] == PROVIDER
        assert row["preserve_branch"] is True
        assert row["venue_name"] == municipal.MUNGYEONG_YOUTH_BRANCH
        assert row["venue_address"] == municipal.MUNGYEONG_YOUTH_ADDRESS
        assert row["address"] == municipal.MUNGYEONG_YOUTH_ADDRESS
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["domain_category"] == "교육·강좌"
        assert row["municipality_code"] == "4728000000"

    assert len(
        [url for url in fetched if urlparse(url).path == municipal.MUNGYEONG_YOUTH_DETAIL_PATH]
    ) == 3
    assert fake_session.closed is True
    assert meta["pages"] == 2
    assert meta["data_pages"] == 1
    assert meta["total_pages"] == 1
    assert meta["list_requests"] == 2
    assert meta["detail_attempts"] == 3
    assert meta["detail_pages"] == 3
    assert meta["detail_errors"] == 0
    assert meta["source_rows"] == 3
    assert meta["reservation_discovery_links"] == 2
    assert meta["invalid_count"] == 0
    assert meta["duplicate_count"] == 0
    assert meta["empty_sentinel_verified"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert meta["no_current_data"] is False
    assert meta["configured_collection_error"] == ""


def test_course_fee_distinguishes_explicit_free_from_unspecified() -> None:
    assert municipal._course_fee("체험비가 전액 무료입니다.") == (
        "무료",
        "description:explicit_free",
        False,
    )
    assert municipal._course_fee("재료비 20,000원") == (
        "공식 페이지 미기재",
        "official_source_unspecified",
        True,
    )


def test_official_education_complete_status_is_closed() -> None:
    assert municipal._status("교육완료") == "CLOSED"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.gbmg.go.kr/reservation/youthCulture/lecture/list.do?mId=0109020000",
        "https://gbmg.go.kr/reservation/youthCulture/lecture/list.do?mId=0109020000",
        "https://evil.www.gbmg.go.kr/reservation/youthCulture/lecture/list.do?mId=0109020000",
        "https://www.gbmg.go.kr:443/reservation/youthCulture/lecture/list.do?mId=0109020000",
        "https://www.gbmg.go.kr/reservation/youthCulture/lecture/view.do?mId=0109020000",
        "https://www.gbmg.go.kr/reservation/youthCulture/lecture/list.do?mId=wrong",
        "https://www.gbmg.go.kr/reservation/youthCulture/lecture/list.do?mId=0109020000&mId=bad",
        "https://www.gbmg.go.kr/reservation/youthCulture/lecture/list.do?mId=0109020000&currentPageNo=2",
        "https://www.gbmg.go.kr/reservation/youthCulture/lecture/list.do?mId=0109020000#fragment",
    ],
)
def test_mungyeong_route_is_exact_host_path_menu_and_query(url: str) -> None:
    assert municipal.is_mungyeong_youth_lecture_url(url) is False


def test_mungyeong_target_binds_provider_and_canonical_urls_are_numeric() -> None:
    assert municipal.MUNGYEONG_CANDIDATE_ID == "MUNI_IR_3D76A819B980"
    assert municipal.MUNGYEONG_MUNICIPALITY_CODE == "4728000000"
    assert municipal.is_mungyeong_youth_lecture_url(TARGET_URL) is True
    assert municipal.is_mungyeong_youth_lecture_target(_target()) is True
    assert (
        municipal.is_mungyeong_youth_lecture_target(
            _target(provider="MUNI_WRONG_PROVIDER")
        )
        is False
    )
    detail, enroll = municipal.canonical_mungyeong_youth_urls("250")
    assert parse_qs(urlparse(detail).query) == {
        "mId": ["0109020000"],
        "idx": ["250"],
    }
    assert parse_qs(urlparse(enroll).query) == {
        "mId": ["0109020000"],
        "lectureIdx": ["250"],
    }
    for value in ("", "24x", "../../admin", "240&next=https://evil.example", "1" * 13):
        assert municipal.canonical_mungyeong_youth_urls(value) == ("", "")


def test_mungyeong_pagination_uses_waf_safe_official_post_and_empty_sentinel() -> None:
    page_one = _list_page(_list_row("240", "고전한문", "접수중"), current=1, total=2)
    page_two = _list_page(_list_row("241", "탁구", "접수중"), current=2, total=2)
    fake_session = FakeSession({2: page_two})
    details = {
        "240": _detail_page("240", "고전한문", "접수중", "창작공방", "한자 교육"),
        "241": _detail_page("241", "탁구", "접수중", "핑퐁방", "탁구 교육"),
    }

    rows, _parser, meta, _fetched, fake_session = _collector(
        page_one,
        details,
        fake_session=fake_session,
        max_pages=3,
        detail_limit=2,
    )

    assert {row["raw_fields"]["lecture_idx"] for row in rows} == {"240", "241"}
    assert len(fake_session.posts) == 2
    page_post = fake_session.posts[0]
    assert page_post[0] == TARGET_URL
    assert page_post[1]["data"] == {
        "currentPageNo": "2",
        "keywordType": "0",
        "keyword": "",
    }
    assert page_post[1]["headers"] == {
        "Referer": TARGET_URL,
        "Origin": "https://www.gbmg.go.kr",
    }
    assert page_post[1]["allow_redirects"] is False
    sentinel_post = fake_session.posts[1]
    assert sentinel_post[1]["data"] == {
        "currentPageNo": "1",
        "keywordType": "1",
        "keyword": municipal.MUNGYEONG_YOUTH_EMPTY_SENTINEL_TERM,
    }
    assert meta["pages"] == 3
    assert meta["total_pages"] == 2
    assert meta["required_list_requests"] == 3
    assert meta["pagination_detected"] is True
    assert meta["pagination_complete"] is True
    assert meta["empty_sentinel_verified"] is True


def test_mungyeong_page_cap_is_fail_closed() -> None:
    page_one = _list_page(_list_row("240", "고전한문", "접수중"), current=1, total=2)
    rows, _parser, meta, fetched, fake_session = _collector(
        page_one,
        max_pages=2,
        detail_limit=10,
    )

    assert rows == []
    assert fetched == [TARGET_URL]
    assert fake_session.posts == []
    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert meta["source_cap_reached"] is True
    assert meta["no_current_data"] is False
    assert "max_pages cap allows 2 of 3" in meta["configured_collection_error"]


def test_mungyeong_repeated_page_identity_is_fail_closed() -> None:
    page_one = _list_page(_list_row("240", "고전한문", "접수중"), current=1, total=2)
    repeated_page = _list_page(
        _list_row("240", "고전한문", "접수중"), current=2, total=2
    )
    fake_session = FakeSession({2: repeated_page})
    rows, _parser, meta, _fetched, fake_session = _collector(
        page_one,
        fake_session=fake_session,
        max_pages=3,
        detail_limit=10,
    )

    assert rows == []
    assert len(fake_session.posts) == 1
    assert meta["duplicate_count"] == 1
    assert meta["snapshot_complete"] is False
    assert meta["no_current_data"] is False
    assert "duplicate provider course identities" in meta["configured_collection_error"]


def test_mungyeong_malformed_or_mixed_rows_fail_closed_but_empty_tbody_is_clean() -> None:
    malformed = _list_page(
        """
        <tr><td>구분</td><td><a data-button="view" data-idx="bad">깨진 강좌</a></td>
        <td></td><td></td><td></td><td>접수중</td></tr>
        """
    )
    mixed = _list_page(
        _list_row("240", "고전한문", "접수중")
        + """
        <tr><td>구분</td><td><a data-button="view">깨진 강좌</a></td>
        <td></td><td></td><td></td><td>접수중</td></tr>
        """
    )
    empty = _list_page("")

    malformed_rows, _parser, malformed_meta, *_ = _collector(malformed)
    mixed_rows, _parser, mixed_meta, *_ = _collector(mixed)
    empty_rows, _parser, empty_meta, *_ = _collector(
        empty, detail_limit=0
    )

    assert malformed_rows == []
    assert malformed_meta["invalid_count"] == 1
    assert malformed_meta["snapshot_complete"] is False
    assert malformed_meta["no_current_data"] is False
    assert "malformed lecture row" in malformed_meta["configured_collection_error"]

    assert mixed_rows == []
    assert mixed_meta["valid_count"] == 1
    assert mixed_meta["invalid_count"] == 1
    assert mixed_meta["snapshot_complete"] is False
    assert mixed_meta["no_current_data"] is False

    assert empty_rows == []
    assert empty_meta["no_data_pages"] == 1
    assert empty_meta["source_rows"] == 0
    assert empty_meta["snapshot_complete"] is True
    assert empty_meta["empty_sentinel_verified"] is True
    assert empty_meta["no_current_data"] is True
    assert "official catalogue" in empty_meta["no_current_reason"]
    assert empty_meta["configured_collection_error"] == ""


def test_mungyeong_expired_complete_catalogue_skips_details_and_can_cleanly_empty() -> None:
    expired_page = _list_page(
        _list_row(
            "100",
            "지난 강좌",
            "접수마감",
            apply_period="2020-01-01 09:00 ~ 2020-01-02 18:00",
            period="2020-01-03 ~ 2020-01-31 (10:00 ~ 12:00)",
        )
    )

    rows, _parser, meta, fetched, _session = _collector(
        expired_page, detail_limit=0
    )

    assert rows == []
    assert fetched == [TARGET_URL]
    assert meta["source_rows"] == 1
    assert meta["expired_count"] == 1
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == (
        "all complete official catalogue lectures are expired"
    )


def test_mungyeong_detail_cap_and_detail_failure_are_fail_closed() -> None:
    list_page = _list_page(
        _list_row("240", "고전한문", "접수중")
        + _list_row("241", "탁구", "접수중")
    )
    details = {"240": "__FAIL__", "241": "__FAIL__"}

    cap_rows, _parser, cap_meta, cap_fetched, _session = _collector(
        list_page,
        details,
        detail_limit=1,
    )
    outage_rows, _parser, outage_meta, outage_fetched, _session = _collector(
        list_page,
        details,
        detail_limit=2,
    )

    assert cap_rows == []
    assert cap_fetched == [TARGET_URL]
    assert cap_meta["detail_attempts"] == 0
    assert cap_meta["source_cap_reached"] is True
    assert cap_meta["snapshot_complete"] is False
    assert "detail_limit cap allows 1 of 2" in cap_meta["configured_collection_error"]

    assert outage_rows == []
    assert len(outage_fetched) == 3
    assert outage_meta["detail_attempts"] == 2
    assert outage_meta["detail_pages"] == 0
    assert outage_meta["detail_errors"] == 2
    assert outage_meta["details_complete"] is False
    assert outage_meta["snapshot_complete"] is False
    assert outage_meta["no_current_data"] is False


def test_mungyeong_empty_sentinel_or_open_application_contract_failure_is_closed() -> None:
    list_page = _list_page(_list_row("240", "고전한문", "접수중"))
    repeated_sentinel = _list_page(
        _list_row("240", "고전한문", "접수중"),
        keyword_type="1",
        keyword=municipal.MUNGYEONG_YOUTH_EMPTY_SENTINEL_TERM,
    )
    sentinel_session = FakeSession(sentinel=repeated_sentinel)
    sentinel_rows, _parser, sentinel_meta, *_ = _collector(
        list_page,
        fake_session=sentinel_session,
        detail_limit=1,
    )

    missing_application = {
        "240": _detail_page(
            "240",
            "고전한문",
            "접수중",
            "창작공방",
            "한자 교육",
            include_application_contract=False,
        )
    }
    application_rows, _parser, application_meta, *_ = _collector(
        list_page,
        missing_application,
        detail_limit=1,
    )

    assert sentinel_rows == []
    assert sentinel_meta["empty_sentinel_verified"] is False
    assert sentinel_meta["detail_attempts"] == 0
    assert sentinel_meta["snapshot_complete"] is False
    assert "empty search sentinel returned lecture rows" in sentinel_meta[
        "configured_collection_error"
    ]

    assert application_rows == []
    assert application_meta["detail_errors"] == 1
    assert application_meta["snapshot_complete"] is False
    assert "application control" in application_meta["configured_collection_error"]


def test_mungyeong_managed_fetcher_and_session_are_required() -> None:
    rows, parser, meta = municipal.collect_mungyeong_youth_culture_lectures(
        _target(), max_pages=2, detail_limit=20
    )
    assert rows == []
    assert parser == municipal.MUNGYEONG_YOUTH_PARSER
    assert meta["snapshot_complete"] is False
    assert "managed fetcher" in meta["configured_collection_error"]
