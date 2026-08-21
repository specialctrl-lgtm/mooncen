from __future__ import annotations

from collections import Counter
import os
from typing import Any

import pytest

from Crawler import municipal_cheongsong as cheongsong


class FakeResponse:
    def __init__(self, html: str, url: str, status_code: int = 200) -> None:
        self.content = html.encode("utf-8")
        self.url = url
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: dict[str, str | list[str]]) -> None:
        self.responses = responses
        self.offsets: Counter[str] = Counter()
        self.calls: list[str] = []
        self.closed = False

    def get(self, url: str, **_kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError(f"unexpected URL: {url}")
        raw = self.responses[url]
        if isinstance(raw, list):
            index = self.offsets[url]
            self.offsets[url] += 1
            html = raw[min(index, len(raw) - 1)]
        else:
            html = raw
        return FakeResponse(html, url)

    def close(self) -> None:
        self.closed = True


def _shell(body: str, *, title: str = "온라인수강신청") -> str:
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
        <title>{title} | {cheongsong.CHEONGSONG_BRANCH}</title></head><body>
        <header>{cheongsong.CHEONGSONG_BRANCH}</header>
        {body}
        <footer>{cheongsong.CHEONGSONG_BRANCH_ADDRESS} /
          TEL : 054-870-6692 / FAX : 054-870-6695</footer>
      </body></html>
    """


def _card(
    identity: str,
    *,
    sequence: int = 1,
    title: str = "여름 창의 로봇",
    target: str = "초등1~6학년",
    apply_period: str = "07.01~07.10",
    education_period: str = "2099.07.20 ~ 08.20",
    capacity: int = 15,
    waitlist: int = 5,
    applicants: int = 9,
    status: str = "마 감",
) -> str:
    return f"""
      <tr>
        <td class="td_id">{sequence}</td>
        <td class="subject"><a href="./educationView.php?yp_id={identity}">{title}</a></td>
        <td>{target}</td>
        <td>접수기간 {apply_period}<br>교육기간 {education_period}</td>
        <td>{capacity}</td><td>{waitlist}</td><td>{applicants}</td><td>{status}</td>
      </tr>
    """


def _list_page(*cards: str, empty: bool = False) -> str:
    body = '<tr><td class="empty_table" colspan="10">등록된 프로그램이 없습니다.</td></tr>' if empty else "".join(cards)
    return _shell(
        f"""
        <table class="ed_list">
          <caption>온라인수강신청 (비회원도 신청가능합니다) 목록</caption>
          <tr><th>번호</th><th>강좌명</th><th>교육대상</th><th>강좌일시</th>
            <th>정원</th><th>후보</th><th>접수</th><th>접수현황</th></tr>
          {body}
        </table>
        """
    )


def _detail(
    identity: str,
    *,
    title: str = "여름 창의 로봇",
    target: str = "초등1~6학년",
    education_period: str = "2099.07.20 ~ 08.20",
    schedule: str = "월/13:00~15:00",
    venue: str = "1층 프로그램실",
    apply_period: str = "99-07-01~99-07-10",
    capacity: int = 15,
    waitlist: int = 5,
    applicants: int = 9,
    open_control: bool = False,
    application_href: str | None = None,
) -> str:
    if open_control:
        href = application_href or f"./applicationForm.php?yp_id={identity}"
        control = f'<a class="ap_btn" href="{href}">신청하기</a>'
    else:
        control = '<a class="ap_end"><font color="red">신청마감</font></a>'
    return _shell(
        f"""
        <table class="ed_view">
          <tr><td rowspan="8"><img src="https://www.futurecsy.or.kr:443/board/data/education/{identity}"></td>
            <td>{title}</td></tr>
          <tr><td>강좌명 : {title}</td></tr>
          <tr><td>교육대상 : {target}</td></tr>
          <tr><td>교육기간 : {education_period}</td></tr>
          <tr><td>교육시간 : {schedule}</td></tr>
          <tr><td>교육장소 : {venue}</td></tr>
          <tr><td>접수기간 : {apply_period}</td></tr>
          <tr><td>정원 : {capacity} / 후보 : {waitlist} / 접수 : {applicants}</td></tr>
          <tr><th colspan="2">교 육 내 용</th></tr>
          <tr><td colspan="2">강사 연락처 010-9999-9999는 저장하면 안 되는 자유 서술이다.</td></tr>
        </table>
        <div class="ed_view_foot">{control}&nbsp;&nbsp;
          <a href="./educationList.php" class="ap_cancel">목록</a></div>
        """
    )


def _target(**changes: str) -> dict[str, str]:
    target = {
        "provider": cheongsong.CHEONGSONG_PROVIDER,
        "url": cheongsong.CHEONGSONG_CANONICAL_URL,
    }
    target.update(changes)
    return target


def _closed_responses(*, detail_html: str | None = None) -> dict[str, str]:
    identity = "411"
    return {
        cheongsong.CHEONGSONG_CANONICAL_URL: _list_page(_card(identity)),
        f"{cheongsong.CHEONGSONG_CANONICAL_URL}?page=2": _list_page(empty=True),
        f"https://{cheongsong.CHEONGSONG_HOST}{cheongsong.CHEONGSONG_DETAIL_PATH}?yp_id={identity}": (
            detail_html or _detail(identity)
        ),
    }


def test_exact_provider_candidate_owner_and_target_boundaries() -> None:
    assert cheongsong.CHEONGSONG_PROVIDER == "MUNI_WWW_FUTURECSY_OR_KR_D9EE9C9C"
    assert cheongsong.CHEONGSONG_CANONICAL_CANDIDATE_ID == "MUNI_IR_353943CBEEF1"
    assert cheongsong.CHEONGSONG_REJECTED_CANDIDATE_ID == "MUNI_IR_929A4889D565"
    assert cheongsong.CHEONGSONG_MUNICIPALITY_CODE == "4775000000"
    assert cheongsong.CHEONGSONG_BRANCH == "청송군청소년수련관"
    assert cheongsong.is_target(_target())
    assert not cheongsong.is_target(_target(provider="MUNI_GUSLE_KR_1D7285A1"))
    assert not cheongsong.is_target(_target(url=cheongsong.CHEONGSONG_CANONICAL_URL + "?page=1"))
    assert not cheongsong.is_target(_target(url=cheongsong.CHEONGSONG_CANONICAL_URL + "#top"))
    assert not cheongsong.is_target(_target(url="https://www.futurecsy.or.kr.evil.test/board/bbs/educationList.php"))

    rejected = cheongsong.CHEONGSONG_CANDIDATE_AUDIT[cheongsong.CHEONGSONG_REJECTED_CANDIDATE_ID]
    assert rejected["decision"] == "excluded_unofficial_third_party_homepage_guide"
    assert rejected["url"].startswith("https://gusle.kr/")
    assert {item["name"] for item in cheongsong.CHEONGSONG_SEPARATE_OWNER_BOUNDARIES} == {
        "경상북도교육청 청송도서관",
        "진보공공도서관",
    }
    assert not cheongsong._allowed_fetch_url(cheongsong.CHEONGSONG_APPLICATION_CHECK_URL)
    assert not cheongsong._allowed_fetch_url("https://www.futurecsy.or.kr/board/bbs/applicationForm.php?yp_id=411")


def test_date_contract_supports_partial_end_single_day_rollover_and_two_digit_year() -> None:
    assert cheongsong._date_period("2099.07.20 ~ 08.20") == (
        cheongsong.date(2099, 7, 20),
        cheongsong.date(2099, 8, 20),
    )
    assert cheongsong._date_period("2099.08.14") == (
        cheongsong.date(2099, 8, 14),
        cheongsong.date(2099, 8, 14),
    )
    assert cheongsong._date_period("2099.12.30(수) ~ 01.02(토), 3박 4일") == (
        cheongsong.date(2099, 12, 30),
        cheongsong.date(2100, 1, 2),
    )
    assert cheongsong._date_period("99-07-01~99-07-10") == (
        cheongsong.date(2099, 7, 1),
        cheongsong.date(2099, 7, 10),
    )


def test_complete_closed_snapshot_sentinel_details_branch_and_pii_allowlist() -> None:
    responses = _closed_responses()
    session = FakeSession(responses)
    rows, parser, meta = cheongsong.collect(
        _target(),
        today="2099-07-15",
        session_factory=lambda: session,
    )

    assert parser == cheongsong.CHEONGSONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_requests"] == 5
    assert meta["list_requests"] == 4
    assert meta["detail_pages"] == 1
    assert meta["sentinel_page"] == 2
    assert meta["page1_rechecked"] and meta["sentinel_rechecked"]
    assert meta["pagination_complete"] and meta["details_complete"]
    assert meta["snapshot_complete"] and meta["full_snapshot_validated"]
    assert meta["status_counts"] == {"CLOSED": 1}
    assert meta["branch_counts"] == {cheongsong.CHEONGSONG_BRANCH: 1}
    assert meta["application_endpoints_called"] == 0
    assert meta["pii_lookup_endpoints_called"] == 0
    assert session.closed

    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"].endswith(":411")
    assert row["branch"] == cheongsong.CHEONGSONG_BRANCH
    assert row["venue"] == "1층 프로그램실"
    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert row["application_url"] == row["raw_url"]
    assert row["description"] == row["title"]
    assert set(row["raw_fields"]) <= cheongsong._SAFE_RAW_FIELDS
    assert "010-9999-9999" not in repr(row)
    assert not any("applicationForm.php" in url for url in session.calls)
    assert cheongsong.CHEONGSONG_APPLICATION_CHECK_URL not in session.calls


def test_open_row_requires_exact_identity_bound_control_without_fetching_form() -> None:
    identity = "512"
    detail_url = f"https://{cheongsong.CHEONGSONG_HOST}{cheongsong.CHEONGSONG_DETAIL_PATH}?yp_id={identity}"
    responses = {
        cheongsong.CHEONGSONG_CANONICAL_URL: _list_page(
            _card(
                identity,
                status="접수중",
                apply_period="07.01~07.31",
                education_period="2099.08.01 ~ 08.31",
            )
        ),
        f"{cheongsong.CHEONGSONG_CANONICAL_URL}?page=2": _list_page(empty=True),
        detail_url: _detail(
            identity,
            education_period="2099.08.01 ~ 08.31",
            apply_period="99-07-01~99-07-31",
            open_control=True,
        ),
    }
    session = FakeSession(responses)
    rows, _parser, meta = cheongsong.collect(_target(), today="2099-07-15", session_factory=lambda: session)

    assert meta["configured_collection_error"] == ""
    assert meta["application_control_count"] == 1
    assert len(rows) == 1
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_type"] == "ONLINE_RESERVATION"
    assert rows[0]["application_url"].endswith(f"applicationForm.php?yp_id={identity}")
    assert rows[0]["application_url"] not in session.calls


def test_empty_first_page_is_a_stable_complete_no_current_snapshot() -> None:
    session = FakeSession({cheongsong.CHEONGSONG_CANONICAL_URL: _list_page(empty=True)})
    rows, _parser, meta = cheongsong.collect(_target(), today="2099-07-15", session_factory=lambda: session)
    assert rows == []
    assert meta["configured_collection_error"] == ""
    assert meta["source_requests"] == 2
    assert meta["sentinel_page"] == 1
    assert meta["page1_rechecked"] and meta["sentinel_rechecked"]
    assert meta["no_current_data"] is True
    assert meta["snapshot_complete"] is True


def test_expired_list_row_is_counted_but_never_detail_fetched() -> None:
    identity = "300"
    detail_url = f"https://{cheongsong.CHEONGSONG_HOST}{cheongsong.CHEONGSONG_DETAIL_PATH}?yp_id={identity}"
    session = FakeSession(
        {
            cheongsong.CHEONGSONG_CANONICAL_URL: _list_page(_card(identity, education_period="2098.07.01 ~ 07.05")),
            f"{cheongsong.CHEONGSONG_CANONICAL_URL}?page=2": _list_page(empty=True),
        }
    )
    rows, _parser, meta = cheongsong.collect(_target(), today="2099-07-15", session_factory=lambda: session)
    assert rows == []
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == 1 and meta["expired_source_count"] == 1
    assert meta["current_source_count"] == 0 and meta["detail_pages"] == 0
    assert detail_url not in session.calls


@pytest.mark.parametrize(
    ("detail_html", "error_text"),
    [
        (_detail("411", title="다른 강좌"), "detail identity/title drift"),
        (
            _detail(
                "411",
                education_period="2099.07.20 ~ 08.20",
                apply_period="99-07-01~99-07-10",
                venue="문의 054-870-6692",
            ),
            "venue contains contact data",
        ),
    ],
)
def test_detail_drift_or_contact_data_fails_atomically(detail_html: str, error_text: str) -> None:
    session = FakeSession(_closed_responses(detail_html=detail_html))
    rows, _parser, meta = cheongsong.collect(_target(), today="2099-07-15", session_factory=lambda: session)
    assert rows == []
    assert error_text in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_spoofed_or_wrong_identity_application_control_fails_atomically() -> None:
    identity = "512"
    detail_url = f"https://{cheongsong.CHEONGSONG_HOST}{cheongsong.CHEONGSONG_DETAIL_PATH}?yp_id={identity}"
    session = FakeSession(
        {
            cheongsong.CHEONGSONG_CANONICAL_URL: _list_page(
                _card(
                    identity,
                    status="접수중",
                    apply_period="07.01~07.31",
                    education_period="2099.08.01 ~ 08.31",
                )
            ),
            f"{cheongsong.CHEONGSONG_CANONICAL_URL}?page=2": _list_page(empty=True),
            detail_url: _detail(
                identity,
                education_period="2099.08.01 ~ 08.31",
                apply_period="99-07-01~99-07-31",
                open_control=True,
                application_href="https://evil.test/applicationForm.php?yp_id=512",
            ),
        }
    )
    rows, _parser, meta = cheongsong.collect(_target(), today="2099-07-15", session_factory=lambda: session)
    assert rows == []
    assert "application control is not identity-bound" in meta["configured_collection_error"]


def test_missing_sentinel_or_insufficient_detail_limit_never_returns_partial_rows() -> None:
    page_two = _list_page(_card("412", sequence=16, title="두 번째 강좌"))
    session = FakeSession(
        {
            cheongsong.CHEONGSONG_CANONICAL_URL: _list_page(_card("411")),
            f"{cheongsong.CHEONGSONG_CANONICAL_URL}?page=2": page_two,
        }
    )
    rows, _parser, meta = cheongsong.collect(
        _target(), max_pages=2, today="2099-07-15", session_factory=lambda: session
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "before exact empty sentinel" in meta["configured_collection_error"]

    limited = FakeSession(_closed_responses())
    rows, _parser, meta = cheongsong.collect(
        _target(), detail_limit=0, today="2099-07-15", session_factory=lambda: limited
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit 0 below required 1" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


def test_page_one_stability_drift_fails_before_detail_fetch() -> None:
    first = _list_page(_card("411"))
    changed = _list_page(_card("411", applicants=10))
    session = FakeSession(
        {
            cheongsong.CHEONGSONG_CANONICAL_URL: [first, changed],
            f"{cheongsong.CHEONGSONG_CANONICAL_URL}?page=2": _list_page(empty=True),
        }
    )
    rows, _parser, meta = cheongsong.collect(_target(), today="2099-07-15", session_factory=lambda: session)
    assert rows == []
    assert "page-one stability recheck failed" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_CHEONGSONG") != "1",
    reason="set RUN_LIVE_CHEONGSONG=1 for the official read-only census",
)
def test_live_official_snapshot_opt_in() -> None:
    rows, parser, meta = cheongsong.collect(_target())
    assert parser == cheongsong.CHEONGSONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["pagination_complete"] and meta["details_complete"]
    assert meta["snapshot_complete"] and meta["full_snapshot_validated"]
    assert meta["sentinel_page"] >= 1
    assert meta["page1_rechecked"] and meta["sentinel_rechecked"]
    assert meta["detail_pages"] == meta["current_source_count"] == len(rows)
    assert meta["application_endpoints_called"] == 0
    assert meta["pii_lookup_endpoints_called"] == 0
    assert all(row["branch"] == cheongsong.CHEONGSONG_BRANCH for row in rows)
    assert all(row["municipality_code"] == "4775000000" for row in rows)
    assert all(row["description"] == row["title"] for row in rows)
    assert all(not cheongsong._privacy_errors(row) for row in rows)
