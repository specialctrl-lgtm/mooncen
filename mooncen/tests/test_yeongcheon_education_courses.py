from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_yeongcheon as yeongcheon


@dataclass
class Target:
    provider: str = yeongcheon.YEONGCHEON_PROVIDER
    name: str = "영천시 교육문화센터 강좌"
    branch: str = "경상북도 영천시"
    url: str = yeongcheon.YEONGCHEON_URL


def _card(
    identity: str,
    *,
    title: str = "로봇 만들기",
    branch: str = "로봇캠퍼스",
    status: str = "접수 중",
    apply_period: str = "2099-06-01 09:00 ~ 2099-06-30 18:00",
    education_period: str = "2099-07-01 ~ 2099-08-01",
) -> str:
    return f"""
    <div class="cardWrap">
      <div class="cardTop">
        <p class="course">{branch}</p>
        <p class="title"><a href="#">{title}</a></p>
        <p class="process1">{status}</p>
      </div>
      <table><tbody>
        <tr><th>접수기간</th><td>{apply_period}</td><th>수강료/재료비</th><td>0원</td></tr>
        <tr><th>교육기간</th><td>{education_period}</td><th>모집인원</th><td>정원: 10 (0)명, 후보자: 2명</td></tr>
        <tr><th>교육일시</th><td>월 10:00-12:00</td><th>신청현황</th><td>신청: 3 (0)명, 후보자: 1명</td></tr>
        <tr><th>교육대상</th><td>영천시민</td><th>문의처</th><td>054-000-0000</td></tr>
      </tbody></table>
      <a class="btn_apply"
         data-action="{yeongcheon.YEONGCHEON_DETAIL_PATH}?mId={yeongcheon.YEONGCHEON_MID}"
         data-keyset="{{'idx': '{identity}'}}">{status}</a>
    </div>
    """


def _list_page(page: int, total: int, cards: str = "") -> str:
    last = max(1, (total + yeongcheon.YEONGCHEON_PAGE_SIZE - 1) // yeongcheon.YEONGCHEON_PAGE_SIZE)
    return f"""
    <html><body>
      <form id="list"><input name="page" value="{page}"></form>
      <p>강좌명 검색 결과 (전체 {total:,}건)</p>
      <div class="bod_card">{cards}</div>
      <div class="bod_page">
        <a href="#" onclick="goPage(1); return false;">처음</a>
        <a href="#" onclick="goPage({last}); return false;">끝</a>
      </div>
    </body></html>
    """


def _detail(
    identity: str,
    *,
    title: str = "로봇 만들기",
    branch: str = "로봇캠퍼스",
    status: str = "접수 중",
    button: bool = True,
    education_period: str = "2099-07-01 ~ 2099-08-01",
    apply_period: str = "2099-06-01 09:00 ~ 2099-06-30 18:00",
) -> str:
    apply_button = (
        '<input type="button" onclick="document.apply.submit();" value="수강 신청">'
        if button
        else ""
    )
    return f"""
    <html><body>
      <div class="tbl-apply"><table class="tbl"><tbody>
        <tr><th>강좌명</th><td colspan="3">
          [{branch}] {title} - <span class="process1">{status}</span>
        </td></tr>
        <tr><th>접수 기간</th><td>{apply_period}</td><th>분류</th><td>관학협력사업</td></tr>
        <tr><th>교육 기간</th><td>{education_period}</td><th>교육 시간</th><td>월 10:00-12:00</td></tr>
        <tr><th>교육 대상</th><td>영천시민</td><th>수강료</th><td>무료</td></tr>
        <tr><th>재료비</th><td>5,000원</td><th>모집 인원</th><td>정원 10명 / 후보자 2명</td></tr>
        <tr><th>신청 현황</th><td>신청 3명 / 후보자 1명</td><th>강사명</th><td>김강사</td></tr>
        <tr><th>강의 장소</th><td>로봇관 4층</td><th>문의 전화</th><td>054-000-0000</td></tr>
        <tr><th>강좌 정보</th><td colspan="3">즐거운 로봇 수업</td></tr>
        <tr><th>유의 사항</th><td colspan="3">필기구 지참</td></tr>
      </tbody></table></div>
      {apply_button}
      <form id="apply"
            action="{yeongcheon.YEONGCHEON_APPLICATION_PATH}?mId={yeongcheon.YEONGCHEON_MID}">
        <input name="programIdx" value="{identity}">
      </form>
    </body></html>
    """


def _fetcher(
    pages: dict[int, str],
    details: dict[str, str],
    calls: list[str] | None = None,
):
    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        if calls is not None:
            calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == yeongcheon.YEONGCHEON_LIST_PATH:
            page = int(query["page"][0])
            return BeautifulSoup(pages[page], "lxml")
        if parsed.path == yeongcheon.YEONGCHEON_DETAIL_PATH:
            return BeautifulSoup(details[query["idx"][0]], "lxml")
        raise AssertionError(url)

    return fetch


def _collect(
    pages: dict[int, str],
    details: dict[str, str],
    **kwargs,
):
    return yeongcheon.collect_yeongcheon_education_courses(
        Target(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", 10),
        detail_limit=kwargs.pop("detail_limit", 10),
        fetcher=_fetcher(pages, details, kwargs.pop("calls", None)),
        session_factory=lambda: object(),
        today=kwargs.pop("today", "2099-06-15"),
        max_workers=2,
        **kwargs,
    )


def _inflated_pages(*, detail_title: str = "로봇 만들기", button: bool = True):
    current = _card("1501")
    expired = _card(
        "42",
        title="옛 강좌",
        branch="옛 분류",
        status="교육 마감",
        apply_period="2020-01-01 ~ 2020-01-02",
        education_period="2020-02-01 ~ 2020-02-02",
    )
    pages = {
        1: _list_page(1, 25, current + expired),
        2: _list_page(2, 25),
        3: _list_page(3, 25),
        4: _list_page(4, 25),
    }
    details = {"1501": _detail("1501", title=detail_title, button=button)}
    return pages, details


def test_collects_complete_current_snapshot_despite_inflated_source_count() -> None:
    pages, details = _inflated_pages()
    calls: list[str] = []

    rows, parser, meta = _collect(pages, details, calls=calls)

    assert parser == yeongcheon.YEONGCHEON_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"] == f"{yeongcheon.YEONGCHEON_PROVIDER}:program:1501"
    assert row["title"] == "로봇 만들기"
    assert row["branch"] == "로봇캠퍼스"
    assert row["status"] == "OPEN"
    assert row["period"] == "2099-07-01 ~ 2099-08-01"
    assert row["apply_period"] == "2099-06-01 ~ 2099-06-30"
    assert row["capacity_total"] == 10
    assert row["capacity_current"] == 3
    assert row["waitlist_total"] == 2
    assert row["waitlist_current"] == 1
    assert row["instructor"] == "김강사"
    assert row["venue_name"] == "로봇관 4층"
    assert row["reservation_available"] is True
    assert parse_qs(urlparse(row["application_url"]).query) == {
        "mId": [yeongcheon.YEONGCHEON_MID],
        "programIdx": ["1501"],
    }
    assert meta["source_total"] == 25
    assert meta["source_rows"] == 2
    assert meta["source_total_mismatch"] == 23
    assert meta["source_total_consistent"] is False
    assert meta["advertised_last_page"] == 3
    assert meta["sentinel_page"] == 4
    assert meta["last_nonempty_page"] == 1
    assert meta["page_counts"] == {1: 2, 2: 0, 3: 0, 4: 0}
    assert meta["expired_count"] == 1
    assert meta["current_count"] == 1
    assert meta["detail_pages"] == 1
    assert meta["branch_count"] == 1
    assert meta["reservation_discovery_links"] == 1
    assert meta["snapshot_complete"] is True
    assert len([url for url in calls if urlparse(url).path == yeongcheon.YEONGCHEON_LIST_PATH]) == 4


def test_academy_categories_resolve_to_physical_facilities() -> None:
    assert yeongcheon.yeongcheon_physical_location(
        "야간교육과정",
        "3층 디지털교육장",
    ) == (
        "평생학습관",
        "경상북도 영천시 최무선로 243",
    )
    assert yeongcheon.yeongcheon_physical_location(
        "교양문화과정",
        "외부공방(중앙동3길 88)",
    ) == (
        "외부공방",
        "경상북도 영천시 중앙동3길 88",
    )
    assert (
        yeongcheon.yeongcheon_physical_location(
            "로봇캠퍼스",
            "로봇관 4층",
        )
        is None
    )


def test_complete_expired_only_catalogue_reports_no_current_data() -> None:
    expired = _card(
        "42",
        title="옛 강좌",
        branch="옛 분류",
        status="교육 마감",
        apply_period="2020-01-01 ~ 2020-01-02",
        education_period="2020-02-01 ~ 2020-02-02",
    )
    pages = {1: _list_page(1, 1, expired), 2: _list_page(2, 1)}

    rows, _parser, meta = _collect(pages, {}, max_pages=2)

    assert rows == []
    assert meta["source_total_consistent"] is True
    assert meta["detail_pages"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert "have ended" in meta["no_current_reason"]


def test_current_future_reception_closed_course_has_no_application_button() -> None:
    closed = _card(
        "1502",
        title="접수 마감 강좌",
        status="접수 마감",
        apply_period="2099-05-01 ~ 2099-05-31",
        education_period="2099-07-01 ~ 2099-07-31",
    )
    pages = {
        1: _list_page(1, 1, closed),
        2: _list_page(2, 1),
    }
    details = {
        "1502": _detail(
            "1502",
            title="접수 마감 강좌",
            status="접수 마감",
            button=False,
            apply_period="2099-05-01 ~ 2099-05-31",
            education_period="2099-07-01 ~ 2099-07-31",
        )
    }

    rows, _parser, meta = _collect(pages, details, max_pages=2)

    assert len(rows) == 1
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["reservation_available"] is False
    assert "application_url" not in rows[0]
    assert meta["detail_pages"] == 1
    assert meta["snapshot_complete"] is True


def test_max_pages_must_cover_every_advertised_page_and_sentinel() -> None:
    pages, details = _inflated_pages()

    rows, _parser, meta = _collect(pages, details, max_pages=3)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "3 of 4 required list requests" in meta["configured_collection_error"]


def test_rows_must_not_resume_after_an_empty_page() -> None:
    current = _card("1501")
    pages = {
        1: _list_page(1, 25, current),
        2: _list_page(2, 25),
        3: _list_page(3, 25, _card("1502", title="뒤늦은 강좌")),
        4: _list_page(4, 25),
    }

    rows, _parser, meta = _collect(
        pages,
        {"1501": _detail("1501"), "1502": _detail("1502", title="뒤늦은 강좌")},
    )

    assert rows == []
    assert meta["pagination_complete"] is False
    assert "resume after an empty" in meta["configured_collection_error"]


def test_duplicate_program_identity_fails_the_snapshot() -> None:
    cards = "".join(_card(str(1500 + index), title=f"강좌 {index}") for index in range(10))
    pages = {
        1: _list_page(1, 15, cards),
        2: _list_page(2, 15, _card("1500", title="중복 강좌")),
        3: _list_page(3, 15),
    }

    rows, _parser, meta = _collect(pages, {}, max_pages=3, detail_limit=20)

    assert rows == []
    assert meta["duplicate_count"] == 1
    assert "duplicate program identities" in meta["configured_collection_error"]


def test_detail_title_mismatch_fails_closed() -> None:
    pages, details = _inflated_pages(detail_title="다른 강좌")

    rows, _parser, meta = _collect(pages, details)

    assert rows == []
    assert meta["detail_errors"] == 1
    assert meta["snapshot_complete"] is False
    assert "detail/list title mismatch" in meta["configured_collection_error"]


def test_open_status_requires_the_official_application_button() -> None:
    pages, details = _inflated_pages(button=False)

    rows, _parser, meta = _collect(pages, details)

    assert rows == []
    assert "status/application control mismatch" in meta["configured_collection_error"]


def test_detail_limit_and_dedupe_are_fail_closed() -> None:
    pages, details = _inflated_pages()

    limited, _parser, limited_meta = _collect(pages, details, detail_limit=0)
    deduped, _parser, dedupe_meta = _collect(
        pages,
        details,
        dedupe_rows=lambda _rows: [],
    )

    assert limited == []
    assert limited_meta["source_cap_reached"] is True
    assert "0 of 1 required details" in limited_meta["configured_collection_error"]
    assert deduped == []
    assert "dedupe changed complete row count 1 to 0" in dedupe_meta["configured_collection_error"]


def test_target_and_identity_url_helpers_are_strict() -> None:
    assert yeongcheon.is_yeongcheon_target(Target()) is True
    assert yeongcheon.is_yeongcheon_target(
        Target(provider="WRONG")
    ) is False
    assert yeongcheon.is_yeongcheon_target(
        Target(url=yeongcheon.YEONGCHEON_URL + "&extra=1")
    ) is False
    assert yeongcheon.yeongcheon_list_url("2").endswith("mId=0303000000&page=2")
    assert yeongcheon.yeongcheon_list_url("../2") == ""
    assert yeongcheon.yeongcheon_detail_url("1501").endswith("mId=0303000000&idx=1501")
    assert yeongcheon.yeongcheon_detail_url("1501&evil=1") == ""
    assert yeongcheon.yeongcheon_application_url("1501").endswith(
        "mId=0303000000&programIdx=1501"
    )


def test_managed_network_injection_is_required() -> None:
    rows, parser, meta = yeongcheon.collect_yeongcheon_education_courses(Target())

    assert rows == []
    assert parser == yeongcheon.YEONGCHEON_PARSER
    assert meta["snapshot_complete"] is False
    assert "managed fetcher" in meta["configured_collection_error"]
