from __future__ import annotations

from contextlib import contextmanager
from datetime import date

from bs4 import BeautifulSoup

from Crawler import Crawler_Lotte as lotte


BRANCHES = [
    {"branch_code": "0011", "name": "롯데문화센터 일산점"},
    {"branch_code": "0001", "name": "롯데문화센터 본점"},
    {"branch_code": "0028", "name": "롯데문화센터 건대스타시티점"},
]


def _row(seq: str, title: str, published: str = "2026.07.16") -> dict:
    return {"seq": seq, "title": title, "date": published, "total_count": 2}


def test_lotte_branch_notice_uses_new_member_date_and_title_term() -> None:
    crawler = lotte.LotteCrawler()
    body = """
    일산점 문화센터 26년 가을학기 회원모집 안내
    접수기간 기존회원 : 26. 7. 22 오전 10:30 ~ 선착순 마감
    신규회원 : 26. 7. 23 오전 10:30 ~ 선착순 마감
    기존회원 기준 : 일산점 26년 여름학기 정기강좌 수강 중인 회원
    강좌기간 2026. 9. 1 ~ 2026. 11. 30
    """
    try:
        notice = crawler.parse_lotte_reception_notice(
            BeautifulSoup("<div>notice</div>", "html.parser"),
            body,
            _row("1118", "[일산점] 26년 가을학기 회원모집 안내"),
            BRANCHES,
        )
    finally:
        crawler.http_session.close()

    assert notice["status"] == "PARSED"
    assert notice["term"] == "가을학기"
    assert notice["branch_code"] == "0011"
    assert notice["apply_start"] == date(2026, 7, 23)
    assert notice["apply_end"] is None
    assert notice["class_start"] == date(2026, 9, 1)
    assert notice["class_end"] == date(2026, 11, 30)


def test_lotte_main_notice_parses_region_schedules_and_aliases() -> None:
    crawler = lotte.LotteCrawler()
    body = """
    강좌기간 9. 1 - 11. 30
    신규회원 수강신청 수도권점 | 7.23 10:30 - 서울·지방점 | 7.24 10:30 -
    확인해주세요! 수도권점 | 인천/동탄/구리/수원/안산/일산/중동/평촌/광명
    서울점 | 잠실/본점/강남/건대/관악/김포/노원/미아/영등포/청량리
    지방점 | 부산본점/광복/광주/대구/대전/동래/상인/센텀/울산/전주/창원/포항 문의
    """
    try:
        notice = crawler.parse_lotte_reception_notice(
            BeautifulSoup("<div>notice</div>", "html.parser"),
            body,
            _row("1092", "[본사] 26년 가을학기 안내", "2026.06.30"),
            BRANCHES,
        )
        groups = notice["main_branch_groups"]
        assert crawler._main_schedule_group_for_branch("롯데문화센터 일산점", groups) == "METRO"
        assert crawler._main_schedule_group_for_branch("롯데문화센터 본점", groups) == "SEOUL_LOCAL"
        assert crawler._main_schedule_group_for_branch("롯데문화센터 건대스타시티점", groups) == "SEOUL_LOCAL"
    finally:
        crawler.http_session.close()

    assert notice["status"] == "PARSED"
    assert notice["main_starts"] == {
        "METRO": date(2026, 7, 23),
        "SEOUL_LOCAL": date(2026, 7, 24),
    }


def test_lotte_additional_recruitment_without_member_labels_is_parsed() -> None:
    crawler = lotte.LotteCrawler()
    body = """
    2026 여름학기 추가모집 안내
    강좌기간 26. 7. 1 ~ 26. 8. 31
    접수기간 6. 15 10:30 ~ 선착순 마감
    대상 강좌 추가모집 강좌
    노원점 문화센터
    """
    branches = [{"branch_code": "0022", "name": "롯데문화센터 노원점"}]
    try:
        notice = crawler.parse_lotte_reception_notice(
            BeautifulSoup("<div>notice</div>", "html.parser"),
            body,
            _row("1085", "[노원점] 26년 여름학기 추가모집 안내", "2026.06.14"),
            branches,
        )
    finally:
        crawler.http_session.close()

    assert notice["status"] == "PARSED"
    assert notice["apply_start"] == date(2026, 6, 15)
    assert notice["class_start"] == date(2026, 7, 1)
    assert notice["class_end"] == date(2026, 8, 31)


def test_lotte_notice_rejects_title_and_body_branch_mismatch() -> None:
    crawler = lotte.LotteCrawler()
    body = """
    본점 문화센터 26년 가을학기 회원모집 안내
    접수기간 신규회원 : 26. 7. 24 ~ 선착순 마감
    강좌기간 2026. 9. 1 ~ 2026. 11. 30
    """
    try:
        notice = crawler.parse_lotte_reception_notice(
            BeautifulSoup("<div>notice</div>", "html.parser"),
            body,
            _row("1118", "[일산점] 26년 가을학기 회원모집 안내"),
            BRANCHES,
        )
    finally:
        crawler.http_session.close()

    assert notice["status"] == "UNPARSEABLE"
    assert notice["failure_reason"] == "NOTICE_BRANCH_MISMATCH"


def test_lotte_monitor_prefers_branch_notice_and_uses_main_for_others(monkeypatch) -> None:
    crawler = lotte.LotteCrawler()
    rows = [
        _row("1092", "[본사] 26년 가을학기 안내", "2026.06.30"),
        _row("1118", "[일산점] 26년 가을학기 회원모집 안내"),
    ]
    main_notice = {
        **rows[0],
        "published_on": date(2026, 6, 30),
        "term": "가을학기",
        "scope": "MAIN",
        "branch_code": None,
        "branch_name": None,
        "source_url": "https://culture.lotteshopping.com/community/notice/view.do?notcSeqno=1092",
        "status": "PARSED",
        "failure_reason": None,
        "class_start": date(2026, 9, 1),
        "class_end": date(2026, 11, 30),
        "main_starts": {"METRO": date(2026, 7, 23), "SEOUL_LOCAL": date(2026, 7, 24)},
        "main_branch_groups": {"METRO": {"일산"}, "SEOUL": {"본", "건대"}, "LOCAL": set()},
    }
    branch_notice = {
        **rows[1],
        "published_on": date(2026, 7, 16),
        "term": "가을학기",
        "scope": "BRANCH",
        "branch_code": "0011",
        "branch_name": "롯데문화센터 일산점",
        "source_url": "https://culture.lotteshopping.com/community/notice/view.do?notcSeqno=1118",
        "status": "PARSED",
        "failure_reason": None,
        "class_start": date(2026, 9, 1),
        "class_end": date(2026, 11, 30),
        "apply_start": date(2026, 7, 25),
        "apply_end": None,
    }
    parsed = {"1092": main_notice, "1118": branch_notice}
    applied = []
    monkeypatch.setattr(crawler, "scrape_notice_rows", lambda: rows)
    monkeypatch.setattr(crawler, "scrape_notice_detail", lambda _seq: (BeautifulSoup("<div/>", "html.parser"), "body"))
    monkeypatch.setattr(crawler, "parse_lotte_reception_notice", lambda _soup, _body, row, _branches: parsed[row["seq"]])
    monkeypatch.setattr(crawler, "apply_lotte_reception_notice", lambda notice: applied.append(dict(notice)) or 1)
    try:
        summary = crawler.monitor_reception_notices(BRANCHES)
    finally:
        crawler.http_session.close()

    by_branch = {notice["branch_code"]: notice for notice in applied}
    assert by_branch["0011"]["seq"] == "1118"
    assert by_branch["0011"]["apply_start"] == date(2026, 7, 25)
    assert by_branch["0001"]["seq"] == "1092"
    assert by_branch["0001"]["apply_start"] == date(2026, 7, 24)
    assert by_branch["0028"]["seq"] == "1092"
    assert summary["branch_overrides"] == 1
    assert summary["main_fallbacks"] == 2
    assert summary["applied_courses"] == 3


def test_lotte_unparseable_branch_notice_blocks_main_fallback(monkeypatch) -> None:
    crawler = lotte.LotteCrawler()
    rows = [
        _row("1092", "[본사] 26년 가을학기 안내", "2026.06.30"),
        _row("1118", "[일산점] 26년 가을학기 회원모집 안내"),
    ]
    main = {
        **rows[0], "published_on": date(2026, 6, 30), "term": "가을학기", "scope": "MAIN",
        "status": "PARSED", "branch_code": None, "class_start": date(2026, 9, 1),
        "class_end": date(2026, 11, 30), "source_url": "main",
        "main_starts": {"METRO": date(2026, 7, 23)},
        "main_branch_groups": {"METRO": {"일산"}},
    }
    broken = {
        **rows[1], "published_on": date(2026, 7, 16), "term": "가을학기", "scope": "BRANCH",
        "status": "UNPARSEABLE", "failure_reason": "RECEPTION_PERIOD_NOT_IN_TEXT",
        "branch_code": "0011", "branch_name": "롯데문화센터 일산점", "source_url": "branch",
    }
    monkeypatch.setattr(crawler, "scrape_notice_rows", lambda: rows)
    monkeypatch.setattr(crawler, "scrape_notice_detail", lambda _seq: (BeautifulSoup("<div/>", "html.parser"), "body"))
    monkeypatch.setattr(
        crawler,
        "parse_lotte_reception_notice",
        lambda _soup, _body, row, _branches: main if row["seq"] == "1092" else broken,
    )
    applied = []
    monkeypatch.setattr(crawler, "apply_lotte_reception_notice", lambda notice: applied.append(notice) or 1)
    try:
        summary = crawler.monitor_reception_notices([BRANCHES[0]])
    finally:
        crawler.http_session.close()

    assert applied == []
    assert summary["unparseable"] == 1
    assert summary["main_fallbacks"] == 0


def test_lotte_notice_batch_update_is_branch_and_course_period_scoped(monkeypatch) -> None:
    crawler = lotte.LotteCrawler()
    executed = {}

    class Cursor:
        def execute(self, statement, params):
            executed["statement"] = statement
            executed["params"] = params

        def fetchall(self):
            return [{"id": "course-1"}]

    @contextmanager
    def fake_cursor():
        yield Cursor()

    monkeypatch.setattr(lotte, "get_db_cursor", fake_cursor)
    notice = {
        "status": "PARSED",
        "branch_code": "0011",
        "apply_start": date(2026, 7, 23),
        "apply_end": None,
        "apply_period_raw": "notice",
        "class_start": date(2026, 9, 1),
        "class_end": date(2026, 11, 30),
    }
    try:
        assert crawler.apply_lotte_reception_notice(notice) == 1
    finally:
        crawler.http_session.close()

    assert executed["params"] is notice
    assert "branch.branch_code = %(branch_code)s" in executed["statement"]
    assert "COALESCE(course.end_date, course.start_date) >= %(class_start)s" in executed["statement"]
    assert "COALESCE(course.start_date, course.end_date) <= %(class_end)s" in executed["statement"]
