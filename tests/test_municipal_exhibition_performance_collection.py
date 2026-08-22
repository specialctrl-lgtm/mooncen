from bs4 import BeautifulSoup

from Crawler.Crawler_MunicipalYaml import (
    build_generic_course_row,
    discover_links,
    generic_application_url,
    has_course_evidence,
    infer_program_type,
    is_relevant_link,
    normalize_program_type,
    parse_all_courses,
    parse_generic_table_courses,
    ranked_main_site_links,
    stable_list_item_url,
    status_has_course_state,
)


def test_exhibition_and_performance_text_counts_as_course_evidence():
    assert has_course_evidence("전시기간 2026.07.01~2026.08.31 관람 대상 누구나")
    assert has_course_evidence("공연기간 2026.07.05 14:00 예매 가능")
    assert has_course_evidence("문화행사 접수 2026.07.01부터")


def test_exhibition_and_performance_status_counts_as_active_state():
    assert status_has_course_state("예매중")
    assert status_has_course_state("관람예약")


def test_exhibition_performance_event_links_are_relevant():
    assert is_relevant_link("전시", "https://example.org/exhibit/list")
    assert is_relevant_link("공연 예매", "https://example.org/show/ticket")
    assert is_relevant_link("문화행사", "https://example.org/event")


def test_menu_discovery_uses_accessible_labels_and_onclick_urls():
    soup = BeautifulSoup(
        """
        <nav>
          <a href="/intro" title="교육프로그램 신청">바로가기</a>
          <a href="#" onclick="location.href='/reservation/program/list.do'">예약신청</a>
          <button data-url="/exhibit/ticket" aria-label="전시 예매">open</button>
        </nav>
        """,
        "html.parser",
    )

    links, _ = discover_links("https://museum.example.org/", soup)

    assert "https://museum.example.org/intro" in links
    assert "https://museum.example.org/reservation/program/list.do" in links
    assert "https://museum.example.org/exhibit/ticket" in links


def test_menu_discovery_follows_safe_iframe_and_get_form_only():
    soup = BeautifulSoup(
        """
        <main>
          <iframe src="/education/program/list.do" title="교육 프로그램"></iframe>
          <form method="get" action="/lecture/search.do" aria-label="강좌 검색">
            <input type="hidden" name="category" value="adult">
            <select name="status"><option value="open" selected>접수중</option></select>
          </form>
          <iframe src="https://evil.example/program/list" title="교육 프로그램"></iframe>
          <form method="post" action="/lecture/apply.do" aria-label="강좌 신청">
            <input type="email" name="email">
          </form>
        </main>
        """,
        "html.parser",
    )

    links, _ = discover_links("https://museum.example.org/", soup)

    assert "https://museum.example.org/education/program/list.do" in links
    assert "https://museum.example.org/lecture/search.do?category=adult&status=open" in links
    assert all("evil.example" not in link and "apply.do" not in link for link in links)


def test_main_site_link_ranking_prioritizes_program_and_performance_menus():
    soup = BeautifulSoup(
        """
        <nav>
          <a href="/intro">기관 소개</a>
          <a href="/program/list">교육 프로그램 신청</a>
          <button onclick="location.href='/performance/ticket'">공연 예매</button>
        </nav>
        """,
        "html.parser",
    )

    links = ranked_main_site_links("https://museum.example.org/", soup, 10)

    assert "https://museum.example.org/program/list" in links
    assert "https://museum.example.org/performance/ticket" in links
    assert "https://museum.example.org/intro" not in links


def test_infer_program_type_for_experience_site_menus():
    assert infer_program_type("기획전시 해설 예약") == "전시"
    assert infer_program_type("토요 음악 공연 예매") == "공연"
    assert infer_program_type("가족 문화행사") == "행사"
    assert infer_program_type("어린이 체험 프로그램") == "체험"


def test_normalize_program_type_refines_default_collector_values():
    assert normalize_program_type("강좌", "기획전시 해설 예약") == "전시"
    assert normalize_program_type("강좌", "토요 음악 공연 예매") == "공연"
    assert normalize_program_type("프로그램", "가족 문화행사") == "행사"
    assert normalize_program_type("견학", "어린이 전시 견학") == "견학"


def test_generic_row_keeps_exhibition_program_type():
    row = build_generic_course_row(
        "TEST_PROVIDER",
        "수원시립미술관",
        "https://example.org/exhibit/list",
        {
            "전시명": "어린이 기획전시",
            "전시기간": "2026.07.01~2026.08.31",
            "관람대상": "누구나",
            "관람료": "무료",
        },
        "전시기간 2026.07.01~2026.08.31 관람대상 누구나 관람료 무료",
        "어린이 기획전시",
        "https://example.org/exhibit/1",
        "generic_table",
    )

    assert row is not None
    assert row["title"] == "어린이 기획전시"
    assert row["program_type"] == "전시"


def test_generic_table_inside_search_form_is_not_dropped_when_it_has_course_pairs():
    soup = BeautifulSoup(
        """
        <form id="searchForm">
          <table>
            <tr><th>강의명</th><td>시니어 인문학 강좌</td></tr>
            <tr><th>수강료</th><td>무료</td></tr>
            <tr><th>강의 기간</th><td>2026.07.01 ~ 2026.08.31</td></tr>
            <tr><th>강의 시간</th><td>수 14:00 ~ 16:00</td></tr>
            <tr><th>교육 장소</th><td>강동구 평생학습관</td></tr>
          </table>
        </form>
        """,
        "html.parser",
    )

    rows = parse_generic_table_courses("TEST_PROVIDER", "강동구 평생학습관", "https://example.org/program/view", soup)

    assert len(rows) == 1
    assert rows[0]["title"] == "시니어 인문학 강좌"
    assert rows[0]["fee"] == "무료"
    assert rows[0]["venue_name"] == "강동구 평생학습관"


def test_generic_application_url_accepts_exhibition_performance_links():
    assert (
        generic_application_url(
            "https://example.org/program/123",
            "전시 해설 관람 예약",
            "OPEN",
        )
        == "https://example.org/program/123"
    )
    assert (
        generic_application_url(
            "https://example.org/show/456",
            "공연 예매 가능",
            "CLOSED",
        )
        == "https://example.org/show/456"
    )


def test_stable_list_item_url_is_deterministic_and_schedule_sensitive():
    base_url = "https://example.org/reservation/program/list?page=1#old"
    first = stable_list_item_url(
        base_url,
        "TEST_PROVIDER",
        "가족 과학 교실",
        "과학관",
        "2026-08-01 ~ 2026-08-31",
        "토 10:00",
    )
    repeated = stable_list_item_url(
        base_url,
        "TEST_PROVIDER",
        "가족 과학 교실",
        "과학관",
        "2026-08-01 ~ 2026-08-31",
        "토 10:00",
    )
    other_schedule = stable_list_item_url(
        base_url,
        "TEST_PROVIDER",
        "가족 과학 교실",
        "과학관",
        "2026-08-01 ~ 2026-08-31",
        "토 14:00",
    )

    assert first == repeated
    assert first != other_schedule
    assert first.startswith("https://example.org/reservation/program/list?page=1#mooncen-item-")


def test_generic_linkless_list_keeps_same_title_sessions_as_distinct_courses():
    list_url = "https://example.org/reservation/program/list"
    soup = BeautifulSoup(
        """
        <table>
          <tr><th>강좌명</th><th>교육기간</th><th>교육시간</th><th>접수상태</th></tr>
          <tr><td>가족 과학 교실</td><td>2026.08.01 ~ 2026.08.31</td><td>토 10:00</td><td>접수중</td></tr>
          <tr><td>가족 과학 교실</td><td>2026.08.01 ~ 2026.08.31</td><td>토 14:00</td><td>접수중</td></tr>
        </table>
        """,
        "html.parser",
    )

    rows, parser = parse_all_courses("TEST_PROVIDER", "과학관", list_url, soup)

    assert parser == "generic_table"
    assert len(rows) == 2
    assert len({row["provider_course_id"] for row in rows}) == 2
    assert len({row["raw_url"] for row in rows}) == 2
    assert all(row["raw_url"].startswith(f"{list_url}#mooncen-item-") for row in rows)
    assert all(row["application_url"] == list_url for row in rows)


def test_generic_real_detail_link_remains_the_canonical_raw_url():
    detail_url = "https://example.org/reservation/program/42"
    row = build_generic_course_row(
        "TEST_PROVIDER",
        "과학관",
        "https://example.org/reservation/program/list",
        {
            "강좌명": "가족 과학 교실",
            "교육기간": "2026.08.01 ~ 2026.08.31",
            "접수상태": "접수중",
        },
        "가족 과학 교실 교육기간 2026.08.01 ~ 2026.08.31 접수중",
        "가족 과학 교실",
        detail_url,
        "generic_table",
    )

    assert row is not None
    assert row["raw_url"] == detail_url
    assert row["application_url"] == detail_url
