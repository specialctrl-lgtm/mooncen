from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_goyang_library as goyang


TARGET = {
    "provider": goyang.GOYANG_LIBRARY_PROVIDER,
    "url": goyang.GOYANG_LIBRARY_URL,
}


@dataclass(frozen=True)
class Lecture:
    identity: str
    code: str
    label: str
    title: str
    venue: str
    status: str = "접수중"
    start: str = "2026.08.01"
    end: str = "2026.08.31"
    target: str = "고양시민"
    schedule: str = "토 10:00 ~ 11:30"
    apply_period: str = "2026.07.01 10:00 ~ 2026.07.31 18:00"
    fee_badge: str = "무료"
    fee: str = "무료"
    material_fee: str = "없음"


class FakeSession:
    def close(self) -> None:
        return None


def _list_item(row: Lecture) -> str:
    capacity = (
        ""
        if row.status == "현장참여"
        else (
            '<span class="count">신청 : 2/10 '
            '<span class="wait">(대기 : 1/3)</span></span>'
        )
    )
    return f"""
      <div class="program-item">
        <div class="infoBox">
          <div class="location">
            <span class="lib">{row.label}</span>
            <span class="paymentN">{row.fee_badge}</span>
            <span class="target">성인</span>
          </div>
          <div class="title">
            <a onclick="javascript:fnDetail('{row.identity}'); return false;">
              {row.title}
            </a>
          </div>
          <div class="info">
            <span><span class="tit">강의기간</span>: {row.start} ~ {row.end}</span>
            <span>{row.schedule}</span>
          </div>
          <div class="info">
            <span><span class="tit">참여대상</span>: {row.target}</span>
            <span><span class="tit">장소</span>: {row.venue}</span>
          </div>
          <div class="info">
            <span><span class="tit">접수기간</span>: {row.apply_period}</span>
          </div>
        </div>
        <div class="statusBox">
          {capacity}
          <span class="status">{row.status}</span>
        </div>
      </div>
    """


def _list_page(
    status: str,
    page_no: int,
    pages: dict[int, list[Lecture]],
) -> str:
    last_page = max(pages)
    options = "".join(
        (
            f'<option value="{value}"'
            f'{" selected" if value == status else ""}>{value}</option>'
        )
        for value in ("", *goyang.GOYANG_LIBRARY_STATUS_PARTITIONS)
    )
    codes = "".join(
        f'<input name="manageCd" value="{code}" checked>'
        for code in goyang.GOYANG_LIBRARY_MANAGE_CODES
    )
    paging = "".join(
        f'<a href="javascript:fnList({number});">{number}</a>'
        for number in range(1, last_page + 1)
    )
    items = "".join(_list_item(row) for row in pages.get(page_no, []))
    return f"""
      <html><body>
        <form>
          <input name="currentPageNo" value="{page_no}">
          <input name="targetAll" value="Y" checked>
          {codes}
          <select name="lectureStatusCd">{options}</select>
        </form>
        <div class="programList">{items}</div>
        <div class="pagingWrap">{paging}</div>
      </body></html>
    """


def _detail_page(row: Lecture, *, status: str | None = None) -> str:
    detail_status = status or row.status
    capacity = (
        ""
        if detail_status == "현장참여"
        else (
            "<li><strong>접수현황</strong>"
            "신청자수 : 2/10명 (대기자수 : 1/3명)</li>"
        )
    )
    return f"""
      <html><body>
        <div class="article-viewTit">
          <span class="lib">{row.label}</span>
          {row.title}
          <div class="rt"><a>관심강좌</a></div>
        </div>
        <div class="article-view">
          <ul>
            <li><strong>장소</strong>{row.venue}</li>
            <li><strong>수강료</strong>{row.fee}</li>
            <li><strong>재료비</strong>{row.material_fee}</li>
            <li><strong>강의기간</strong>{row.start} ~ {row.end}</li>
            <li><strong>요일/시간</strong>{row.schedule}</li>
            <li>
              <strong>접수기간</strong>{row.apply_period}
              <span class="tblBtn">{detail_status}</span>
            </li>
            {capacity}
            <li><strong>참여대상</strong>{row.target}</li>
            <li><strong>강사명</strong>저장하면 안 되는 이름</li>
            <li><strong>첨부파일</strong><a href="/download/private">파일</a></li>
          </ul>
          <div class="content">저장하면 안 되는 자유 서술 본문</div>
        </div>
      </body></html>
    """


def _lectures() -> list[Lecture]:
    rows = []
    for number, (code, branch) in enumerate(
        goyang.GOYANG_LIBRARY_MANAGE_CODES.items(),
        start=1,
    ):
        label = next(
            label
            for label, mapped_code in goyang.GOYANG_LIBRARY_LABEL_CODES.items()
            if mapped_code == code
        )
        venue = (
            "아람누리도서관 3층 아람마루"
            if code == "AL"
            else f"{branch} 강의실"
        )
        rows.append(
            Lecture(
                identity=str(500000 + number),
                code=code,
                label=label,
                title=f"{branch} 프로그램 {number}",
                venue=venue,
            )
        )
    return rows


def _site(
    *,
    conflicting_duplicate: bool = False,
    nonempty_sentinel: bool = False,
    bad_detail_status: bool = False,
) -> tuple[Any, list[str], list[Lecture]]:
    rows = _lectures()
    pages_by_status: dict[str, dict[int, list[Lecture]]] = {
        "apply": {1: rows[:10], 2: rows[10:]},
        "ready": {1: []},
        "wait": {1: []},
        "finish": {1: []},
        "offline": {
            1: [
                (
                    Lecture(
                        **{
                            **rows[0].__dict__,
                            "title": "충돌하는 제목",
                        }
                    )
                    if conflicting_duplicate
                    else rows[0]
                )
            ]
        },
    }
    fetched: list[str] = []

    def fetcher(_session: Any, url: str, _timeout: int) -> str:
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == goyang.GOYANG_LIBRARY_LIST_PATH:
            status = query["lectureStatusCd"][0]
            page_no = int(query["currentPageNo"][0])
            pages = pages_by_status[status]
            sentinel = max(pages) + 1
            if nonempty_sentinel and status == "apply" and page_no == sentinel:
                return _list_page(status, page_no, {page_no: [rows[0]]})
            return _list_page(status, page_no, pages)
        if parsed.path == goyang.GOYANG_LIBRARY_DETAIL_PATH:
            identity = query["lectureIdx"][0]
            row = next(item for item in rows if item.identity == identity)
            return _detail_page(
                row,
                status="알 수 없음" if bad_detail_status else None,
            )
        raise AssertionError(f"unexpected route: {url}")

    return fetcher, fetched, rows


def test_target_and_list_url_are_exact_and_select_all_library_codes() -> None:
    assert goyang.is_goyang_library_target(TARGET)
    assert not goyang.is_goyang_library_target(
        {**TARGET, "provider": "ANOTHER_PROVIDER"}
    )
    assert not goyang.is_goyang_library_target(
        {**TARGET, "url": f"{goyang.GOYANG_LIBRARY_URL}?other=1"}
    )

    query = parse_qs(
        urlparse(goyang.goyang_library_list_url("apply", 3)).query
    )
    assert query["currentPageNo"] == ["3"]
    assert query["lectureStatusCd"] == ["apply"]
    assert query["targetAll"] == ["Y"]
    assert query["manageCd"] == list(goyang.GOYANG_LIBRARY_MANAGE_CODES)
    with pytest.raises(ValueError):
        goyang.goyang_library_list_url("end", 1)
    assert goyang._date_range("2026.08.12 (1일)") == (
        "2026-08-12",
        "2026-08-12",
    )
    assert goyang.GOYANG_LIBRARY_STATUS_MAP["대기자접수"] == "WAITLIST"


def test_complete_snapshot_separates_eighteen_libraries_and_has_required_fields() -> None:
    fetcher, fetched, source_rows = _site()

    rows, parser, meta = goyang.collect_goyang_library_courses(
        TARGET,
        timeout=5,
        max_pages=20,
        detail_limit=30,
        fetcher=fetcher,
        session_factory=FakeSession,
        today="2026-07-26",
        dedupe_rows=lambda values: values,
        request_delay=0,
    )

    assert parser == goyang.GOYANG_LIBRARY_PARSER
    assert len(rows) == len(source_rows) == 19
    assert meta["snapshot_complete"] is True
    assert meta["source_pages"] == 6
    assert meta["source_exposed"] == 20
    assert meta["source_total"] == 19
    assert meta["duplicate_partition_rows"] == 1
    assert meta["sentinel_pages"] == 5
    assert meta["stable_rechecks"] == 6
    assert meta["branch_count"] == 18
    assert meta["pii_payload_persisted"] is False
    assert {row["branch_code"] for row in rows} == {
        f"GOYANG_LIBRARY_{code}"
        for code in goyang.GOYANG_LIBRARY_MANAGE_CODES
        if code != "AL"
    }
    assert all(
        row["target"]
        and row["fee"]
        and row["start_date"]
        and row["venue_name"]
        and row["category"]
        and row["schedule_raw"]
        for row in rows
    )
    assert all(
        "instructor" not in row
        and "description" not in row
        and "attachment" not in row
        for row in rows
    )
    assert not any(
        "/download" in url or "/apply" in url or "/login" in url
        for url in fetched
    )


@pytest.mark.parametrize(
    ("site_kwargs", "error_fragment"),
    (
        ({"conflicting_duplicate": True}, "conflicting partition rows"),
        ({"nonempty_sentinel": True}, "is not an empty sentinel"),
        ({"bad_detail_status": True}, "status mismatch"),
    ),
)
def test_contract_mismatches_fail_closed(
    site_kwargs: dict[str, bool],
    error_fragment: str,
) -> None:
    fetcher, _fetched, _source_rows = _site(**site_kwargs)

    rows, _parser, meta = goyang.collect_goyang_library_courses(
        TARGET,
        max_pages=20,
        detail_limit=30,
        fetcher=fetcher,
        session_factory=FakeSession,
        today="2026-07-26",
        request_delay=0,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["error_kind"] == "incomplete_snapshot"
    assert error_fragment in meta["configured_collection_error"]


def test_detail_limit_fails_before_any_detail_request() -> None:
    fetcher, fetched, _source_rows = _site()

    rows, _parser, meta = goyang.collect_goyang_library_courses(
        TARGET,
        max_pages=20,
        detail_limit=18,
        fetcher=fetcher,
        session_factory=FakeSession,
        today="2026-07-26",
        request_delay=0,
    )

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert not any(
        urlparse(url).path == goyang.GOYANG_LIBRARY_DETAIL_PATH
        for url in fetched
    )


def test_on_site_program_does_not_require_capacity() -> None:
    source = Lecture(
        identity="599999",
        code="MV",
        label="높빛",
        title="현장 영화 상영",
        venue="높빛도서관 4층",
        status="현장참여",
    )
    item = BeautifulSoup(_list_item(source), "lxml").select_one(
        ".program-item"
    )
    assert item is not None
    record = goyang._list_record(item)
    detail = BeautifulSoup(_detail_page(source), "lxml")

    row = goyang._enrich_detail(TARGET, record, detail)

    assert row["status"] == "OPEN"
    assert row["capacity_total"] is None
    assert row["capacity_remaining"] is None


def test_age_range_is_accepted_as_the_structured_target_field() -> None:
    source = Lecture(
        identity="599998",
        code="MF",
        label="아람누리",
        title="그림책 읽어주기",
        venue="아람누리도서관 어린이자료실",
        status="현장참여",
        target="0세~9세",
    )
    list_html = _list_item(source).replace("참여대상", "대상연령")
    detail_html = _detail_page(source).replace("참여대상", "대상연령")
    item = BeautifulSoup(list_html, "lxml").select_one(".program-item")
    assert item is not None

    record = goyang._list_record(item)
    row = goyang._enrich_detail(
        TARGET,
        record,
        BeautifulSoup(detail_html, "lxml"),
    )

    assert row["target"] == "0세~9세"


def test_target_badge_is_used_when_on_site_target_detail_is_empty() -> None:
    source = Lecture(
        identity="599997",
        code="MQ",
        label="별꿈",
        title="자료실 전시",
        venue="별꿈도서관 자료실",
        status="현장참여",
        target="성인",
    )
    list_html = _list_item(source).replace(
        '<span><span class="tit">참여대상</span>: 성인</span>',
        '<span><span class="tit">참여대상</span>:</span>',
    )
    detail_html = _detail_page(source).replace(
        "<li><strong>참여대상</strong>성인</li>",
        "",
    )
    item = BeautifulSoup(list_html, "lxml").select_one(".program-item")
    assert item is not None

    record = goyang._list_record(item)
    row = goyang._enrich_detail(
        TARGET,
        record,
        BeautifulSoup(detail_html, "lxml"),
    )

    assert row["target"] == "성인"


def test_detail_target_can_refine_an_open_ended_list_age_range() -> None:
    source = Lecture(
        identity="599996",
        code="MP",
        label="삼송",
        title="한 줄 책장",
        venue="삼송도서관",
        status="현장참여",
        target="0세~",
    )
    item = BeautifulSoup(_list_item(source), "lxml").select_one(
        ".program-item"
    )
    assert item is not None
    detail_html = _detail_page(source).replace(
        "<li><strong>참여대상</strong>0세~</li>",
        "<li><strong>대상연령</strong>0세~99세</li>",
    )

    row = goyang._enrich_detail(
        TARGET,
        goyang._list_record(item),
        BeautifulSoup(detail_html, "lxml"),
    )

    assert row["target"] == "0세~99세"
