from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import dotenv
import pytest
import yaml
from bs4 import BeautifulSoup

dotenv.load_dotenv = lambda *args, **kwargs: False

from Crawler import Crawler_MunicipalYaml as municipal  # noqa: E402


def _target(*, provider: str = municipal.YANGJU_EDUCATION_PROVIDER, url: str = municipal.YANGJU_EDUCATION_CANONICAL_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="양주시 통합예약 전체 교육·강좌",
        branch="양주시 통합예약",
        url=url,
        source="test",
        priority=1,
        region="경기도 양주시",
        extra={},
    )


def _row(
    sequence: int,
    lecture_no: int,
    status: str,
    period: str,
    *,
    application: bool = False,
    apply_period: str = "99.01.01~99.01.31",
    responsive_capacity: str | None = None,
) -> str:
    application_html = (
        f'<a href="./selectEduApplcntAgreView.do?key=2148&amp;eduLctreNo={lecture_no}&amp;pageIndex=99">수강신청</a>'
        if application
        else ""
    )
    return f"""
    <tr>
      <td>{sequence}</td>
      <td><a href="./eduLctreWebView.do?key=2148&amp;eduLctreNo={lecture_no}&amp;pageIndex=99">강좌 {lecture_no}</a></td>
      <td>선착순</td>
      <td>접수 : {apply_period} 교육 : {period}</td>
      <td>일 (13:00 ~ 14:30)</td>
      <td>정원 : 4/15 대기 : 0/5</td>
      {f'<td>{responsive_capacity}</td>' if responsive_capacity is not None else ''}
      <td>10,000원</td>
      <td>{status}</td>
      <td>{application_html}</td>
    </tr>
    """


def _list_page(page: int, total: int, last_page: int, rows: str) -> str:
    headers = "".join(f"<th>{header}</th>" for header in municipal.YANGJU_EDUCATION_HEADERS)
    return f"""
    <html><body>
      <div class="bbs_info clearfix"><div class="bbs_left bbs_count">
        <span>총 <strong>{total}</strong> 건</span>,
        <span class="division_line">[<strong>{page}</strong> / {last_page} 페이지]</span>
      </div></div>
      <table class="list_table"><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>
      <div class="pagination"><span class="page">1</span></div>
    </body></html>
    """


def _sentinel(page: int, total: int, last_page: int) -> str:
    return f"""
    <html><body>
      <div class="bbs_info clearfix"><div class="bbs_left bbs_count">
        <span>총 <strong>{total}</strong> 건</span>,
        <span class="division_line">[<strong>{page}</strong> / {last_page} 페이지]</span>
      </div></div>
      <table><tbody><tr><td class="empty">등록된 교육강좌 없습니다.</td></tr></tbody></table>
    </body></html>
    """


def _detail(
    lecture_no: int,
    status: str,
    period: str,
    branch: str,
    *,
    application: bool = False,
    apply_period: str = "99.01.01~99.01.31",
) -> str:
    application_html = (
        f'<a href="./selectEduApplcntAgreView.do?key=2148&amp;eduLctreNo={lecture_no}&amp;tracking=1">수강신청</a>'
        if application
        else ""
    )
    pairs = {
        "교육기관": branch,
        "교육장소": "제1교육실",
        "강의실": "교육실 A",
        "모집구분": "일반모집",
        "강사명": "양주 강사",
        "분류": "문화/체험",
        "수강대상": "성인",
        "접수방식": "온라인",
        "모집방법": "선착순",
        "접수기간": apply_period,
        "모집인원": "정원 : 15명 / 대기 : 5명",
        "교육기간": period,
        "교육요일": "일",
        "수강료": "10,000원",
        "전화번호": "031-8082-4173",
        "강의개요": "양주시 공식 교육입니다.",
        "유의사항": "신청 후 참여해 주세요.",
    }
    items = "".join(f"<li class='clearfix'><em>{key}</em><p>{value}</p></li>" for key, value in pairs.items())
    return f"""
    <html><body><div class="education_request">
      <div class="titlebox"><span class="state">{status}</span><span class="title">강좌 {lecture_no}</span></div>
      <ul>{items}</ul>{application_html}
    </div></body></html>
    """


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def test_yangju_completes_178_pages_and_immediate_sentinel_then_details_current(monkeypatch: pytest.MonkeyPatch) -> None:
    total = 178
    calls: list[str] = []
    monkeypatch.setattr(municipal, "YANGJU_EDUCATION_PAGE_UNIT", 1)
    monkeypatch.setattr(municipal, "session", lambda: object())

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.YANGJU_EDUCATION_DETAIL_PATH:
            lecture_no = int(query["eduLctreNo"][0])
            if lecture_no == 9001:
                return _soup(
                    _detail(
                        9001,
                        "접수중",
                        "추후협의",
                        "양주시립회암사지박물관",
                        application=True,
                        apply_period="수시모집",
                    )
                )
            assert lecture_no == 9002
            return _soup(
                _detail(
                    9002,
                    "대기접수중",
                    "20.01.01~20.01.31",
                    "양주시청소년수련원",
                    application=True,
                )
            )
        page = int(query["pageIndex"][0])
        if page == 179:
            return _soup(_sentinel(page, total, 178))
        sequence = total - page + 1
        lecture_no = 9000 + page
        if page == 1:
            row = _row(
                sequence,
                lecture_no,
                "접수중",
                "추후협의",
                application=False,
                apply_period="수시모집",
                responsive_capacity="정원 : 4/15 대기 : (0/5)",
            )
        elif page == 2:
            row = _row(
                sequence,
                lecture_no,
                "대기접수중",
                "20.01.01~20.01.31",
                application=True,
            )
        else:
            row = _row(sequence, lecture_no, "교육종료", "20.01.01~20.01.31")
        return _soup(_list_page(page, total, 178, row))

    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    rows, parser, meta = municipal.collect_yangju_edu_lectures(
        _target(), timeout=7, max_pages=179, detail_limit=2
    )

    assert parser == municipal.YANGJU_EDUCATION_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{municipal.YANGJU_EDUCATION_PROVIDER}:lecture:9001",
        f"{municipal.YANGJU_EDUCATION_PROVIDER}:lecture:9002",
    ]
    assert rows[0]["application_url"].endswith("key=2148&eduLctreNo=9001")
    assert rows[0]["reservation_available"] is True
    assert rows[0]["period"] == "추후협의"
    assert "start_date" not in rows[0]
    assert "end_date" not in rows[0]
    assert rows[0]["raw_fields"]["course_period_undated"] is True
    assert rows[0]["apply_period"] == "수시모집"
    assert "apply_start" not in rows[0]
    assert "apply_end" not in rows[0]
    assert rows[1]["application_url"].endswith("key=2148&eduLctreNo=9002")
    assert rows[1]["reservation_available"] is True
    assert rows[1]["status"] == "WAITING"
    assert rows[1]["raw_fields"]["detail_source_status"] == "대기접수중"
    assert rows[1]["raw_fields"]["clear_application_url"] is False
    assert {row["branch"] for row in rows} == {"양주시립회암사지박물관", "양주시청소년수련원"}
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "4163000000" for row in rows)
    assert meta["pages"] == 179
    assert meta["detail_pages"] == 2
    assert meta["source_total"] == 178
    assert meta["source_rows"] == 178
    assert meta["current_count"] == 2
    assert meta["expired_count"] == 176
    assert meta["sentinel_pages"] == 1
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    list_pages = [int(parse_qs(urlparse(url).query)["pageIndex"][0]) for url in calls if urlparse(url).path == municipal.YANGJU_EDUCATION_LIST_PATH]
    assert list_pages == list(range(1, 180))


def test_yangju_rejects_mismatched_desktop_mobile_capacity_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _list_page(
        1,
        1,
        1,
        _row(
            1,
            9001,
            "접수중",
            "99.02.01~99.02.28",
            application=True,
            responsive_capacity="정원 : 5/15 대기 : (0/5)",
        ),
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, _url, timeout: _soup(page),
    )

    rows, _parser, meta = municipal.collect_yangju_edu_lectures(
        _target(), timeout=7, max_pages=2, detail_limit=1
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert "responsive capacity columns disagree" in meta["configured_collection_error"]


def test_yangju_requires_capacity_for_the_immediate_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(municipal, "YANGJU_EDUCATION_PAGE_UNIT", 1)
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, _url, timeout: _soup(_list_page(1, 178, 178, _row(178, 9001, "접수중", "99.02.01~99.02.28", application=True))),
    )
    rows, _parser, meta = municipal.collect_yangju_edu_lectures(
        _target(), timeout=7, max_pages=178, detail_limit=178
    )
    assert rows == []
    assert meta["pages"] == 1
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "178 of 179 required" in meta["configured_collection_error"]


def test_yangju_rejects_arbitrary_unparseable_course_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _list_page(
        1,
        1,
        1,
        _row(1, 9001, "접수중", "일정미정", application=True),
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", lambda _session, _url, timeout: _soup(page))

    rows, _parser, meta = municipal.collect_yangju_edu_lectures(
        _target(), timeout=7, max_pages=2, detail_limit=1
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "required fields missing" in meta["configured_collection_error"]


def test_yangju_exact_target_and_safe_identity_urls() -> None:
    assert municipal.is_yangju_education_target(_target()) is True
    assert municipal.is_yangju_education_target(_target(url=municipal.YANGJU_EDUCATION_CANONICAL_URL + "&rcritTrget=adult")) is False
    assert municipal.is_yangju_education_target(_target(provider="MUNI_WRONG")) is False
    detail, lecture_no = municipal.canonical_yangju_detail_url(
        municipal.yangju_education_list_url(178),
        "./eduLctreWebView.do?key=2148&eduLctreNo=12945&pageIndex=178#tracking",
    )
    assert lecture_no == "12945"
    assert detail.endswith("key=2148&eduLctreNo=12945")
    assert municipal.canonical_yangju_detail_url(
        municipal.YANGJU_EDUCATION_CANONICAL_URL,
        "https://example.com/yeyak/eduLctreWebView.do?key=2148&eduLctreNo=12945",
    ) == ("", "")
    assert municipal.canonical_yangju_detail_url(
        municipal.YANGJU_EDUCATION_CANONICAL_URL,
        "./eduLctreWebView.do?key=2148&eduLctreNo=12945%26evil%3D1",
    ) == ("", "")
    assert municipal.canonical_yangju_application_url(
        municipal.YANGJU_EDUCATION_CANONICAL_URL,
        "./selectEduApplcntAgreView.do?key=2148&eduLctreNo=999",
        "12945",
    ) == ""
    assert municipal.yangju_source_row_is_current(
        {
            "raw_fields": {
                "source_status": "접수마감",
                "course_period_undated": True,
            },
        }
    ) is False
    assert municipal.yangju_source_row_is_current(
        {
            "raw_fields": {
                "source_status": "접수중",
                "course_period_undated": True,
            },
        }
    ) is True


def test_yangju_target_yaml_is_utf8_locked_full_snapshot_and_cli_limits_are_bounded() -> None:
    with open("config/crawl_targets/public_reservation.yaml", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    rows = [row for row in document["targets"] if row.get("provider") == municipal.YANGJU_EDUCATION_PROVIDER]
    assert len(rows) == 1
    target = rows[0]
    assert target["name"] == "양주시 통합예약 전체 교육·강좌"
    assert target["url"] == municipal.YANGJU_EDUCATION_CANONICAL_URL
    assert "rcritTrget=adult" in target["ownership_aliases"][0]
    assert target["domain_category"] == "교육·강좌"
    assert target["source_group"] == "municipal_reservation"
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert target["municipality_code"] == "4163000000"
    assert target["municipality_full_name"] == "경기도 양주시"
    assert target["full_snapshot_required"] is True
    quality = target["last_quality"]
    assert quality["collected"] == quality["current_count"] == quality["detail_pages"] == 147
    assert quality["source_total"] == 3551
    assert quality["source_pages"] == 178
    assert quality["pages"] == 179
    assert quality["reservation_count"] == 53
    assert quality["snapshot_complete"] is True
    with pytest.raises(SystemExit):
        municipal.main(["--max-pages", "501"])
    with pytest.raises(SystemExit):
        municipal.main(["--mark-stale"])
