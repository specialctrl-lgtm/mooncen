from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


LIST_URL = "https://www.science.go.kr/mps/1092/bbs/edu/list.do"
DETAIL_WITH_CONTROL = (
    "https://www.science.go.kr/mps/1092/bbs/edu/"
    "moveBbsNttDetail.do?nttSn=101"
)
DETAIL_WITHOUT_CONTROL = (
    "https://www.science.go.kr/mps/1092/bbs/edu/"
    "moveBbsNttDetail.do?nttSn=102"
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "2026. 8. 1.(토) ~ 8. 22.(토)",
            ("2026-08-01", "2026-08-22"),
        ),
        (
            "2026. 9. 5.(토), 9. 12.(토), 9. 26.(토)",
            ("2026-09-05", "2026-09-26"),
        ),
    ],
)
def test_national_science_date_range_handles_omitted_year_and_sessions(
    source: str,
    expected: tuple[str, str],
) -> None:
    assert municipal.national_science_date_range(source) == expected


@pytest.fixture
def national_science_pages(monkeypatch: pytest.MonkeyPatch):
    list_soup = BeautifulSoup(
        """
        <table>
          <thead>
            <tr>
              <th>번호</th><th>상태</th><th>교육명</th><th>대상</th>
              <th>교육기간</th><th>접수기간</th>
            </tr>
          </thead>
          <tbody>
            <tr onclick="location.href='/reservation'">
              <td>0</td><td>접수중</td><td><a href="/reservation">예약하기</a></td>
              <td>초등학생</td><td>2026. 8. 1. ~ 8. 2.</td><td>2026. 7. 1. ~ 7. 20.</td>
            </tr>
            <tr onclick="fn_moveEduBbsNttDetail('edu', '101')">
              <td>1</td><td>접수중</td><td>주말 가족 천문 관측 교실</td>
              <td>초등학생 가족</td><td>2026. 8. 1.(토) ~ 8. 22.(토)</td>
              <td>2026. 7. 1.(수) ~ 7. 31.(금)</td>
            </tr>
            <tr onclick="fn_moveEduBbsNttDetail('edu', '102')">
              <td>2</td><td>교육예정</td><td>어린이 로봇 코딩 실험실</td>
              <td>초등학교 4~6학년</td><td>2026. 9. 5.(토), 9. 12.(토), 9. 26.(토)</td>
              <td>2026. 8. 1.(토) ~ 8. 20.(목)</td>
            </tr>
          </tbody>
        </table>
        """,
        "html.parser",
    )
    detail_with_control = BeautifulSoup(
        """
        <main>
          <h1>주말 가족 천문 관측 교실</h1>
          <dl>
            <dt>교육시간</dt><dd>10:00 ~ 12:00</dd>
            <dt>교육비</dt><dd>무료</dd>
            <dt>모집인원</dt><dd>20명</dd>
            <dt>교육장소</dt><dd>천체관측실</dd>
          </dl>
          <a href="/mps/reservation/apply.do?program=101">사전예약 하러가기</a>
        </main>
        """,
        "html.parser",
    )
    detail_without_control = BeautifulSoup(
        """
        <main>
          <h1>어린이 로봇 코딩 실험실</h1>
          <dl>
            <dt>교육시간</dt><dd>14:00 ~ 16:00</dd>
            <dt>참가비</dt><dd>10,000원</dd>
            <dt>모집인원</dt><dd>16명</dd>
            <dt>교육장소</dt><dd>창의과학실</dd>
          </dl>
          <p>교육 신청 방법은 추후 별도 안내합니다.</p>
        </main>
        """,
        "html.parser",
    )
    pages = {
        LIST_URL: list_soup,
        DETAIL_WITH_CONTROL: detail_with_control,
        DETAIL_WITHOUT_CONTROL: detail_without_control,
    }
    requested: list[str] = []

    monkeypatch.setattr(municipal, "session", lambda: object())

    def fake_fetch_soup(_session, url: str, timeout: int):
        assert timeout == 7
        requested.append(url)
        return pages[url]

    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch_soup)
    target = municipal.CrawlTarget(
        "NATIONAL_SCIENCE_TEST",
        "국립중앙과학관",
        "국립중앙과학관",
        LIST_URL,
        "test",
    )
    return target, requested


def test_national_science_collection_requires_structured_rows_and_explicit_control(
    national_science_pages,
) -> None:
    target, requested = national_science_pages

    rows, parser, meta = municipal.collect_national_science_museum(
        target,
        timeout=7,
        max_pages=1,
        detail_limit=10,
    )

    assert parser == "national_science_table"
    assert requested == [LIST_URL, DETAIL_WITH_CONTROL, DETAIL_WITHOUT_CONTROL]
    assert [row["title"] for row in rows] == [
        "주말 가족 천문 관측 교실",
        "어린이 로봇 코딩 실험실",
    ]
    assert all(row["title"] != "예약하기" for row in rows)

    by_title = {row["title"]: row for row in rows}
    reservable = by_title["주말 가족 천문 관측 교실"]
    assert reservable["raw_url"] == DETAIL_WITH_CONTROL
    assert reservable["application_url"] == (
        "https://www.science.go.kr/mps/reservation/apply.do?program=101"
    )
    assert reservable["application_type"] == "ONLINE_RESERVATION"
    assert reservable["reservation_available"] is True
    assert reservable["raw_fields"]["application_control"] == "explicit_detail_control"
    assert "clear_application_url" not in reservable["raw_fields"]
    assert reservable["start_date"] == "2026-08-01"
    assert reservable["end_date"] == "2026-08-22"
    assert reservable["apply_start"] == "2026-07-01"
    assert reservable["apply_end"] == "2026-07-31"
    assert reservable["fee"] == "무료"
    assert reservable["venue_name"] == "천체관측실"

    informational = by_title["어린이 로봇 코딩 실험실"]
    assert informational["raw_url"] == DETAIL_WITHOUT_CONTROL
    assert informational["application_url"] == ""
    assert informational["application_type"] == "INFO_ONLY"
    assert informational["reservation_available"] is False
    assert informational["raw_fields"]["application_control"] == "none"
    assert informational["raw_fields"]["clear_application_url"] is True
    assert informational["start_date"] == "2026-09-05"
    assert informational["end_date"] == "2026-09-26"
    assert informational["apply_start"] == "2026-08-01"
    assert informational["apply_end"] == "2026-08-20"
    assert informational["target"] == "초등학교 4~6학년"
    assert informational["fee"] == "10,000원"
    assert informational["venue_name"] == "창의과학실"

    assert meta["detail_pages"] == 2
    assert meta["reservation_discovery_links"] == 1
