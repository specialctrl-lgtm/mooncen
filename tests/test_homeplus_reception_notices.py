from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date

from Crawler import Crawler_Homeplus as homeplus


SAMPLE_NOTICE_HTML = """
<div class="board_view1">
  <div class="header">
    <p class="title">[인하점] 여름학기 7월 개강강좌 회원모집 안내</p>
    <ul><li class="place">인하점</li><li>2026-06-12</li></ul>
  </div>
  <div class="board_view_cont">
    <p>▶ 신청 기간┃6월 8일(월) ~ 선착순 마감</p>
    <p>▶ 온라인접수┃24시간 (서비스 점검 시간 : 23~24시)</p>
    <p>▶ 강좌 기간┃7월 1일(수) ~ 8월 30일(일) * 강좌별 종강일 상이</p>
  </div>
</div>
"""


def _notice_row(**overrides):
    row = {
        "notice_id": "16713",
        "title": "[인하점] 여름학기 7월 개강강좌 회원모집 안내",
        "branch_code": "1023",
        "branch_name": "인하점",
        "published_on": date(2026, 6, 12),
    }
    row.update(overrides)
    return row


def test_homeplus_notice_parser_extracts_open_ended_reception_and_course_scope() -> None:
    crawler = homeplus.HomeplusCrawler(use_selenium=False)
    try:
        notice = crawler.parse_reception_notice_detail(SAMPLE_NOTICE_HTML, _notice_row())
    finally:
        crawler.close()

    assert notice["status"] == "PARSED"
    assert notice["apply_start"] == date(2026, 6, 8)
    assert notice["apply_end"] is None
    assert notice["class_start"] == date(2026, 7, 1)
    assert notice["class_end"] == date(2026, 8, 30)
    assert "HOMEPLUS_NOTICE:16713" in notice["apply_period_raw"]
    assert "NoticeView?reqNoticeID=16713" in notice["source_url"]


def test_homeplus_notice_parser_reports_image_only_schedule_without_applying() -> None:
    crawler = homeplus.HomeplusCrawler(use_selenium=False)
    html = """
    <div class="board_view1">
      <div class="header"><p class="title">2026 여름학기 회원모집 안내</p></div>
      <div class="board_view_cont"><p>문의: 문화센터 데스크</p><img src="data:image/png;base64,AAAA"></div>
    </div>
    """
    try:
        notice = crawler.parse_reception_notice_detail(html, _notice_row())
    finally:
        crawler.close()

    assert notice["status"] == "UNPARSEABLE"
    assert notice["failure_reason"] == "RECEPTION_PERIOD_NOT_IN_TEXT"
    assert "apply_start" not in notice


def test_homeplus_notice_parser_rejects_list_and_detail_branch_mismatch() -> None:
    crawler = homeplus.HomeplusCrawler(use_selenium=False)
    try:
        notice = crawler.parse_reception_notice_detail(
            SAMPLE_NOTICE_HTML,
            _notice_row(branch_code="0013", branch_name="영등포점"),
        )
    finally:
        crawler.close()

    assert notice["status"] == "UNPARSEABLE"
    assert notice["failure_reason"] == "NOTICE_BRANCH_MISMATCH"


def test_homeplus_notice_feed_accepts_double_encoded_json(monkeypatch) -> None:
    crawler = homeplus.HomeplusCrawler(use_selenium=False)

    class Response:
        def json(self):
            return json.dumps({
                "Table": [
                    {
                        "NoticeID": 16713,
                        "Title": "회원모집 안내",
                        "StoreCode": "1023",
                        "StoreName": "인하점",
                        "DateCreate": "2026-06-12",
                    },
                    {"NoticeID": "unsafe/id", "Title": "discard"},
                ]
            }, ensure_ascii=False)

    captured = {}

    def request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return Response()

    monkeypatch.setattr(crawler, "_request_with_retry", request)
    try:
        rows = crawler.fetch_reception_notice_rows(max_items=25)
    finally:
        crawler.close()

    assert rows == [_notice_row(title="회원모집 안내")]
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/CommunityNoAuth/GetNoticeList")
    assert json.loads(captured["data"])["prm"] == ["", "", 1, 25]


def test_homeplus_notice_batch_update_is_branch_and_course_period_scoped(monkeypatch) -> None:
    crawler = homeplus.HomeplusCrawler(use_selenium=False)
    executed = {}

    class Cursor:
        def execute(self, statement, params):
            executed["statement"] = statement
            executed["params"] = params

        def fetchall(self):
            return [{"id": "course-1"}, {"id": "course-2"}]

    @contextmanager
    def fake_cursor():
        yield Cursor()

    monkeypatch.setattr(homeplus, "get_db_cursor", fake_cursor)
    notice = crawler.parse_reception_notice_detail(SAMPLE_NOTICE_HTML, _notice_row())
    try:
        affected = crawler.apply_reception_notice(notice)
    finally:
        crawler.close()

    assert affected == 2
    assert executed["params"]["branch_code"] == "1023"
    assert "branch.branch_code = %(branch_code)s" in executed["statement"]
    assert "COALESCE(course.end_date, course.start_date) >= %(class_start)s" in executed["statement"]
    assert "COALESCE(course.start_date, course.end_date) <= %(class_end)s" in executed["statement"]


def test_homeplus_notice_monitor_applies_newer_overlapping_notice_last(monkeypatch) -> None:
    crawler = homeplus.HomeplusCrawler(use_selenium=False)
    rows = [
        _notice_row(notice_id="20", published_on=date(2026, 6, 12)),
        _notice_row(notice_id="10", published_on=date(2026, 4, 17)),
    ]
    applied = []
    monkeypatch.setattr(crawler, "fetch_reception_notice_rows", lambda: rows)
    monkeypatch.setattr(
        crawler,
        "fetch_reception_notice_detail",
        lambda row: {
            **row,
            "status": "PARSED",
            "apply_start": row["published_on"],
            "apply_end": None,
            "apply_period_raw": row["notice_id"],
            "class_start": date(2026, 6, 1),
            "class_end": date(2026, 8, 31),
        },
    )
    monkeypatch.setattr(crawler, "apply_reception_notice", lambda notice: applied.append(notice["notice_id"]) or 1)
    try:
        summary = crawler.monitor_reception_notices()
    finally:
        crawler.close()

    assert applied == ["10", "20"]
    assert summary["parsed"] == 2
    assert summary["applied_courses"] == 2
