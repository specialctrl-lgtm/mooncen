from bs4 import BeautifulSoup

from Crawler.Crawler_MunicipalYaml import (
    discover_links,
    first_by_keys,
    filter_generic_miscollected_rows,
    parse_generic_table_courses,
)


def test_first_by_keys_respects_requested_key_priority() -> None:
    values = {
        "\uc0ac\uc5c5\uba85": "project heading",
        "\uac15\uc88c\uba85": "actual course",
    }

    assert first_by_keys(values, ("\uac15\uc88c\uba85", "\uc0ac\uc5c5\uba85")) == "actual course"


def test_generic_list_table_is_parsed_per_course_instead_of_as_one_header_row() -> None:
    soup = BeautifulSoup(
        """
        <table>
          <thead><tr>
            <th>번호</th><th>대상</th><th>강좌명</th><th>학습장소</th>
            <th>접수기간</th><th>강의기간</th><th>상태</th>
          </tr></thead>
          <tbody>
            <tr><td>2</td><td>청소년</td>
              <td><a href="./view.do?lctreNo=832">흥미와 적성으로 찾는 나의 진로</a></td>
              <td>공유평생학습관</td><td>2026-07-06~2026-07-19</td>
              <td>2026-07-27~2026-07-27</td><td>접수중</td></tr>
            <tr><td>1</td><td>성인</td>
              <td><a href="./view.do?lctreNo=833">AICE 자격증 취득 프로그램</a></td>
              <td>금빛평생학습관</td><td>2026-06-17~2026-06-24</td>
              <td>2026-06-29~2026-07-22</td><td>운영중</td></tr>
          </tbody>
        </table>
        """,
        "lxml",
    )

    rows = parse_generic_table_courses(
        "MUNI_TEST",
        "충청북도 음성군",
        "https://edu.example.go.kr/course/list.do?key=61",
        soup,
    )

    assert [row["title"] for row in rows] == [
        "흥미와 적성으로 찾는 나의 진로",
        "AICE 자격증 취득 프로그램",
    ]
    assert rows[0]["target"] == "청소년"
    assert rows[0]["venue_name"] == "공유평생학습관"
    assert rows[0]["apply_period"] == "2026-07-06~2026-07-19"
    assert rows[0]["period"] == "2026-07-27~2026-07-27"
    assert "lctreNo=832" in rows[0]["raw_url"]


def test_generic_list_table_supports_education_subject_and_combined_capacity() -> None:
    soup = BeautifulSoup(
        """
        <table>
          <thead><tr>
            <th>번호</th><th>교육과목</th><th>교육기간</th><th>교육시간</th>
            <th>접수기간</th><th>접수자/정원</th><th>상태</th>
          </tr></thead>
          <tbody><tr><td>47</td>
            <td><a href="./view.do?schedule_seq=20282">[오후교육] 포토샵 활용</a></td>
            <td>2026-05-11~2026-05-22</td><td>13:00 ~ 15:00</td>
            <td>2026.04.27 ~ 2026.05.07</td><td>3 / 5</td><td>접수완료</td>
          </tr></tbody>
        </table>
        """,
        "lxml",
    )

    rows = parse_generic_table_courses(
        "MUNI_TEST",
        "충청남도 당진시",
        "https://example.go.kr/edu/list.do",
        soup,
    )

    assert len(rows) == 1
    assert rows[0]["title"] == "[오후교육] 포토샵 활용"
    assert rows[0]["capacity_current"] == 3
    assert rows[0]["capacity_total"] == 5
    assert rows[0]["schedule_raw"] == "13:00 ~ 15:00"
    assert rows[0]["status"] == "CLOSED"


def test_generic_quality_filter_never_falls_back_to_navigation_rows() -> None:
    rows = [
        {"title": "접수중 강좌", "status": "OPEN", "description": "접수 신청"},
        {"title": "영천시 평생학습관 메인", "period": "2026-07-01~2026-07-31"},
        {"title": "Energetlc Dangjin 당찬 당진 더 큰 당진 거침없는 도약", "status": "OPEN"},
    ]

    assert filter_generic_miscollected_rows(rows) == []


def test_generic_quality_filter_rejects_title_only_course_detail_link() -> None:
    rows = [
        {
            "title": "여름방학 과학 탐구 교실",
            "status": "OPEN",
            "description": "신청 가능",
            "raw_url": "https://example.go.kr/program/programInfoDetail.do?prgm_seq=3",
            "raw_fields": {"source_url": "https://example.go.kr/program/programInfoList.do"},
        }
    ]

    assert filter_generic_miscollected_rows(rows) == []


def test_generic_article_with_course_words_and_structured_fields_is_not_published() -> None:
    soup = BeautifulSoup(
        """
        <html><head><title>공지사항</title></head><body>
          <table>
            <tr><th>강좌명</th><td>2026 여름방학 코딩교육 참여자 모집</td></tr>
            <tr><th>교육기간</th><td>2026.08.10 ~ 2026.08.14</td></tr>
            <tr><th>접수기간</th><td>2026.07.20 ~ 2026.07.31</td></tr>
            <tr><th>교육대상</th><td>초등학생</td></tr>
            <tr><th>모집인원</th><td>20명</td></tr>
          </table>
        </body></html>
        """,
        "lxml",
    )

    rows = parse_generic_table_courses(
        "MUNI_TEST",
        "검증시",
        "https://example.go.kr/news/articleView.do?articleSeq=123&category=education",
        soup,
    )

    assert rows == []


def test_notice_page_discovers_registration_menu_but_not_notice_articles() -> None:
    soup = BeautifulSoup(
        """
        <html><head><title>공지사항</title></head><body>
          <nav><a href="/education/courseList.do">수강신청</a></nav>
          <a href="/news/articleView.do?articleSeq=123">코딩교육 참여자 모집</a>
        </body></html>
        """,
        "lxml",
    )

    links, _pagination = discover_links("https://example.go.kr/board/list.do", soup)

    assert links == ["https://example.go.kr/education/courseList.do"]


def test_board_shaped_education_application_ledger_is_preserved() -> None:
    soup = BeautifulSoup(
        """
        <html><head><title>교육 프로그램 신청</title></head><body>
          <table><thead><tr>
            <th>강좌명</th><th>교육기간</th><th>교육시간</th><th>접수기간</th><th>대상</th>
          </tr></thead><tbody><tr>
            <td><a href="?bo_table=edu_app&amp;wr_id=7">가족 목공 교실</a></td>
            <td>2026.08.10 ~ 2026.08.31</td><td>토 10:00 ~ 12:00</td>
            <td>2026.07.20 ~ 2026.08.05</td><td>초등 가족</td>
          </tr></tbody></table>
        </body></html>
        """,
        "lxml",
    )

    rows = parse_generic_table_courses(
        "MUNI_TEST",
        "가족센터",
        "https://example.go.kr/bbs/board.php?bo_table=edu_app",
        soup,
    )

    assert [row["title"] for row in rows] == ["가족 목공 교실"]


def test_reception_period_does_not_leak_into_course_period() -> None:
    soup = BeautifulSoup(
        """
        <table>
          <tr><th>강좌명</th><td>상시 디지털 교실</td></tr>
          <tr><th>접수기간</th><td>2026.07.20 ~ 2026.07.31</td></tr>
          <tr><th>교육대상</th><td>성인</td></tr>
          <tr><th>수강료</th><td>무료</td></tr>
        </table>
        """,
        "lxml",
    )

    rows = parse_generic_table_courses(
        "MUNI_TEST",
        "평생학습관",
        "https://example.go.kr/education/course/view.do?id=1",
        soup,
    )

    assert len(rows) == 1
    assert rows[0]["apply_period"] == "2026.07.20 ~ 2026.07.31"
    assert "period" not in rows[0]
