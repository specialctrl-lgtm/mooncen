from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_wanju as wanju


class _Response:
    def __init__(self, url: str, html: str):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = 200
        self.headers: dict[str, str] = {}


class _Session:
    def close(self) -> None:
        return None


def _identity(number: int) -> str:
    return f"{number:032x}"


def _row(
    number: int | None,
    *,
    title: str,
    branch: str,
    period: str,
    registered: str,
) -> str:
    identity = _identity(number if number is not None else 1000)
    marker = "공지" if number is None else str(number)
    notice_class = " colNotice" if number is None else ""
    return f"""
    <ul class="programme{notice_class}">
      <li class="num">{marker}</li>
      <li class="title"><a href="./view.9is?dataUid={identity}&amp;boardUid={wanju.WANJU_BOARD_UID}&amp;contentUid={wanju.WANJU_CONTENT_UID}">
        <strong>[{branch}]</strong>{title}<span class="icon-new">새글</span>
      </a></li>
      <li class="col_w20">{period}</li>
      <li class="writer">관리자</li>
      <li class="date">{registered}</li>
    </ul>
    """


def _page(page: int) -> str:
    if page == 1:
        rows = _row(
            None,
            title="음악 콘서트 참여 안내",
            branch="콩쥐팥쥐",
            period="2026-08-02 ~ 2026-08-02",
            registered="2026-07-20",
        ) + _row(
            11,
            title="[모집] 여름 독서교실 교육",
            branch="삼례",
            period="2026-08-01 ~ 2026-08-20",
            registered="2026-07-19",
        )
        rows += "".join(
            _row(
                number,
                title=f"지난 독서교실 {number}",
                branch="중앙",
                period="2025-01-01 ~ 2025-01-31",
                registered="2024-12-01",
            )
            for number in range(10, 1, -1)
        )
    elif page == 2:
        rows = _row(
            1,
            title="지난 교육 1",
            branch="둔산",
            period="2025-01-01 ~ 2025-01-31",
            registered="2024-12-01",
        )
    else:
        rows = ""
    return f"""
    <html><body>
      <div class="headList">전체 11건 페이지 {page} / 2</div>
      <div class="list_group"><div class="group_con">{rows}</div></div>
    </body></html>
    """


def _detail(identity: str) -> str:
    assert identity == _identity(11)
    return """
    <html><body><div class="view-group">
      <div class="view-title"><h4><strong>[삼례]</strong>[모집] 여름 독서교실 교육</h4></div>
      <ul class="view-info">
        <li><strong>등록일</strong><span>2026-07-19</span></li>
        <li><strong>행사기간</strong><span>2026-08-01 ~ 2026-08-20</span></li>
        <li><strong>작성자</strong><span>홍길동 010-1234-5678</span></li>
      </ul>
      <div class="view-con">
        문의 010-9876-5432 test@example.com
        <a href="https://naver.me/AbCd1234">https://naver.me/AbCd1234</a>
      </div>
    </div></body></html>
    """


def _target() -> dict[str, str]:
    return {"provider": wanju.WANJU_PROVIDER, "url": wanju.WANJU_CANONICAL_URL}


def _fetcher(_session, url: str, _timeout: int):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.path == wanju.WANJU_LIST_PATH:
        page = int((query.get("page") or ["1"])[0])
        return _Response(url, _page(page))
    identity = query["dataUid"][0]
    return _Response(url, _detail(identity))


def test_exact_canonical_target_only() -> None:
    assert wanju.is_wanju_education_target(_target())
    assert not wanju.is_wanju_education_target(
        {**_target(), "provider": "MUNI_OTHER"}
    )
    assert not wanju.is_wanju_education_target(
        {**_target(), "url": wanju.WANJU_CANONICAL_URL.replace("lib.", "www.")}
    )
    assert not wanju.is_wanju_education_target(
        {**_target(), "url": wanju.WANJU_CANONICAL_URL + "#fragment"}
    )


def test_complete_snapshot_semantic_partition_branch_and_application() -> None:
    rows, parser, meta = wanju.collect_wanju_education(
        _target(),
        today="2026-07-22",
        max_pages=5,
        detail_limit=1,
        max_workers=2,
        session_factory=_Session,
        fetcher=_fetcher,
    )

    assert parser == wanju.WANJU_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["list_requests"] == 5
    assert meta["declared_numbered_total"] == 11
    assert meta["pinned_notice_count"] == 1
    assert meta["source_rows"] == 12
    assert meta["data_pages"] == 2
    assert meta["empty_sentinel_page"] == 3
    assert meta["boundary_rechecks"] == 2
    assert meta["current_source_count"] == 2
    assert meta["current_education_count"] == 1
    assert meta["excluded_current_non_education_count"] == 1
    assert meta["expired_count"] == 10
    assert meta["detail_pages"] == meta["returned_count"] == 1
    assert meta["status_counts"] == {"OPEN": 1}
    assert meta["branch_counts"] == {"삼례도서관": 1}
    assert meta["application_control_count"] == 1
    assert meta["actionable_row_count"] == 1

    row = rows[0]
    assert row["title"] == "[모집] 여름 독서교실 교육"
    assert row["branch"] == "삼례도서관"
    assert row["branch_code"] == "WANJU_LIBRARY_SAMRYE"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["application_type"] == "EXTERNAL_OFFICIAL_LINK"
    assert row["application_url"] == "https://naver.me/AbCd1234"
    assert row["raw_fields"]["official_branch_label"] == "삼례"
    assert "010-" not in repr(row)
    assert "test@example.com" not in repr(row)
    assert "홍길동" not in repr(row)


def test_archive_cap_is_fail_closed() -> None:
    rows, _, meta = wanju.collect_wanju_education(
        _target(),
        today="2026-07-22",
        max_pages=4,
        session_factory=_Session,
        fetcher=_fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "max_pages 4 below required 5" in meta["configured_collection_error"]


def test_only_audited_legacy_unlabelled_identities_are_accepted() -> None:
    known = next(iter(wanju.WANJU_LEGACY_UNLABELED))
    anchor = BeautifulSoup("<a>오래된 공식 게시물</a>", "lxml").a
    assert wanju._title(anchor, known) == ("오래된 공식 게시물", "미표기")
    with pytest.raises(wanju.WanjuContractError, match="branch label"):
        wanju._title(anchor, _identity(9999))
    assert len(wanju.WANJU_LEGACY_UNLABELED) == 20


def test_external_application_controls_remain_fail_closed() -> None:
    detail_url = wanju.wanju_detail_url(_identity(11))
    root = BeautifulSoup(
        """
        <div class="view-con">
          <a href="https://example.com/information">관련 정보</a>
          <a href="https://naver.me/AbCd1234">https://naver.me/AbCd1234</a>
        </div>
        """,
        "lxml",
    )
    assert wanju._application_controls(root, detail_url) == (
        "https://naver.me/AbCd1234",
    )

    private = BeautifulSoup(
        '<div class="view-con"><a href="https://127.0.0.1/apply">신청</a></div>',
        "lxml",
    )
    with pytest.raises(wanju.WanjuContractError, match="unsafe"):
        wanju._application_controls(private, detail_url)


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_CRAWLER_TESTS") != "1",
    reason="set RUN_LIVE_CRAWLER_TESTS=1 for official live validation",
)
def test_live_wanju_official_snapshot() -> None:
    rows, _, meta = wanju.collect_wanju_education(
        _target(), timeout=40, max_pages=100, detail_limit=100
    )
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] >= 393
    assert meta["returned_count"] == len(rows)
