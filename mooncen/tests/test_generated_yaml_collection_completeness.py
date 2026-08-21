from __future__ import annotations

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler.Crawler_MunicipalYaml import CrawlTarget


def test_explicitly_exhausted_last_page_is_not_treated_as_a_cap(monkeypatch) -> None:
    target = CrawlTarget(
        provider="TEST_COMPLETE_TARGET",
        name="완전 수집 테스트",
        branch="테스트 지점",
        url="https://example.com/courses",
        source="test",
    )

    def fake_collect(*_args, **_kwargs):
        return (
            [
                {
                    "title": "강좌",
                    "branch": "테스트 지점",
                    "raw_url": "https://example.com/courses/1",
                }
            ],
            "test_parser",
            {
                "pages": 2,
                "detail_pages": 0,
                "pagination_detected": True,
                "pagination_complete": True,
                "pagination_exhausted": True,
                "recursion_depth": 1,
            },
        )

    monkeypatch.setattr(generated, "collect_from_url", fake_collect)

    result = generated._collect_single_target(
        target,
        per_target_limit=0,
        max_depth=1,
        max_pages=2,
        detail_limit=10,
        timeout=1,
    )

    assert result.page_cap_reached is False
    assert result.recursion_cap_reached is False
    assert result.collection_complete is True


def test_generated_rows_preserve_safe_per_item_raw_url_fragments() -> None:
    target = CrawlTarget(
        provider="TEST_FRAGMENT_TARGET",
        name="목록형 강좌",
        branch="테스트 지점",
        url="https://example.com/programs",
        source="test",
    )
    rows = [
        {
            "title": "첫 번째 체험",
            "raw_url": "https://example.com/programs#mooncen-item-111",
            "schedule_raw": "상시 운영",
        },
        {
            "title": "두 번째 체험",
            "raw_url": "https://example.com/programs#mooncen-item-222",
            "schedule_raw": "상시 운영",
        },
    ]

    normalized = generated.normalize_collected_rows(rows, target)

    assert [row["raw_url"] for row in normalized] == [
        "https://example.com/programs#mooncen-item-111",
        "https://example.com/programs#mooncen-item-222",
    ]
    assert len({generated.provider_course_id_from_row(row) for row in normalized}) == 2


def test_generated_rows_fill_explicit_source_omission_markers() -> None:
    target = CrawlTarget(
        provider="TEST_REQUIRED_FIELDS",
        name="필수 필드",
        branch="테스트 지점",
        url="https://example.com/programs",
        source="test",
        extra={"domain_category": "교육·강좌"},
    )

    normalized = generated.normalize_collected_rows(
        [{"title": "원문 최소 강좌"}],
        target,
    )

    assert normalized[0]["target"] == "대상 별도 안내"
    assert normalized[0]["fee"] == "요금 별도 안내"
    assert normalized[0]["period"] == "날짜 별도 안내"
    assert normalized[0]["venue_name"] == "장소 별도 안내"
    assert normalized[0]["schedule_raw"] == "시간 별도 안내"
    assert normalized[0]["category"] == "교육·강좌"
    assert normalized[0]["target_source_omission"] is True
    assert normalized[0]["date_source_omission"] is True


def test_generated_rows_make_shared_list_urls_course_unique() -> None:
    target = CrawlTarget(
        provider="TEST_SHARED_LIST_URL",
        name="공유 목록 URL",
        branch="테스트 지점",
        url="https://example.com/programs",
        source="test",
    )

    normalized = generated.normalize_collected_rows(
        [
            {
                "provider_course_id": "course-1",
                "title": "첫 번째 강좌",
                "raw_url": target.url,
            },
            {
                "provider_course_id": "course-2",
                "title": "두 번째 강좌",
                "raw_url": target.url,
            },
        ],
        target,
    )

    assert len({row["raw_url"] for row in normalized}) == 2
    assert all("#mooncen-item-" in row["raw_url"] for row in normalized)
    assert all(row["shared_list_url_source"] == target.url for row in normalized)


def test_generated_rows_drop_secret_bearing_raw_url_fragments() -> None:
    target = CrawlTarget(
        provider="TEST_FRAGMENT_TARGET",
        name="목록형 강좌",
        branch="테스트 지점",
        url="https://example.com/programs",
        source="test",
    )

    normalized = generated.normalize_collected_rows(
        [
            {
                "title": "보안 테스트",
                "raw_url": "https://example.com/programs#token=secret",
                "schedule_raw": "상시 운영",
            }
        ],
        target,
    )

    assert normalized[0]["raw_url"] == "https://example.com/programs"


def test_gumigx_swim_rows_keep_a_safe_unique_url_per_program(monkeypatch) -> None:
    soup = municipal.BeautifulSoup(
        """
        <html><body>
          <table></table>
          <table></table>
          <table>
            <tr><th>time</th><th>beginner</th><th>intermediate</th><th>advanced</th></tr>
            <tr><td>06:10 ~ 07:00</td><td>20</td><td>20</td><td>20</td></tr>
          </table>
        </body></html>
        """,
        "html.parser",
    )
    monkeypatch.setattr(municipal, "fetch_soup", lambda *_args, **_kwargs: soup)
    target = CrawlTarget(
        provider="MUNI_WWW_GUMIGX_KR_2CBF84FC",
        name="Gumi worker culture center",
        branch="Gumi",
        url="https://www.gumigx.kr/course/info.asp",
        source="test",
    )

    collected = municipal.gumigx_swim_rows(target, object(), timeout=1)
    normalized = generated.normalize_collected_rows(collected, target)

    assert len(normalized) == 3
    assert len({row["raw_url"] for row in normalized}) == 3
    assert all("#mooncen-item-" in row["raw_url"] for row in normalized)
    assert len({generated.provider_course_id_from_row(row) for row in normalized}) == 3


class _JsonResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _GwacheonSession:
    def __init__(self) -> None:
        self.month_requests: list[dict[str, str]] = []

    def get(self, *_args, **_kwargs) -> _JsonResponse:
        return _JsonResponse({})

    def post(self, *_args, data: dict[str, str], **_kwargs) -> _JsonResponse:
        self.month_requests.append(data)
        return _JsonResponse({"lctreList": []})


def test_gwacheon_science_marks_only_the_full_lookahead_window_complete(
    monkeypatch,
) -> None:
    target = CrawlTarget(
        provider="GWACHEON_NATIONAL_SCIENCE_MUSEUM",
        name="국립과천과학관",
        branch="국립과천과학관",
        url="https://www.sciencecenter.go.kr/edu/user/edu/eduList.do",
        source="test",
        extra={"lookahead_months": 3},
    )
    complete_session = _GwacheonSession()
    monkeypatch.setattr(municipal, "session", lambda: complete_session)

    rows, parser, meta = municipal.collect_gwacheon_scipia(
        target,
        timeout=1,
        max_pages=3,
    )

    assert rows == []
    assert parser == "gwacheon_edu_list_json"
    assert len(complete_session.month_requests) == 3
    assert meta["pagination_complete"] is True
    assert meta["source_cap_reached"] is False
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "no_current_gwacheon_science_programs"

    capped_session = _GwacheonSession()
    monkeypatch.setattr(municipal, "session", lambda: capped_session)
    _, _, capped_meta = municipal.collect_gwacheon_scipia(
        target,
        timeout=1,
        max_pages=2,
    )

    assert len(capped_session.month_requests) == 2
    assert capped_meta["pagination_complete"] is False
    assert capped_meta["source_cap_reached"] is True
    assert capped_meta["no_current_data"] is False


def test_gwangju_science_program_cards_supply_required_fields_and_stop_on_history(
    monkeypatch,
) -> None:
    current_page = municipal.BeautifulSoup(
        """
        <html><body>
          <a onclick="linkPage(2)">2</a>
          <ul class="program_thumb">
            <li>
              <div class="desc_thumb"><span class="cate">접수중</span></div>
              <div class="desc_info">
                <div class="title"><span>실험탐구</span><span>별빛 실험실</span></div>
                <ul class="list">
                  <li><b>교육기간</b><span>2099년 08월 01일 ~ 2099년 08월 30일</span></li>
                  <li><b>접수기간</b><span>2099년 07월 01일 ~ 2099년 07월 31일</span></li>
                  <li><b>교육시간</b><span>토 10:00~12:00</span></li>
                  <li><b>모집대상</b><span>초등 3~6학년</span></li>
                  <li><b>교육비</b><span>15,000원</span></li>
                </ul>
              </div>
              <div class="program_button">
                <a class="view" href="/kor/program/view.do?id=100">보기</a>
              </div>
            </li>
          </ul>
        </body></html>
        """,
        "html.parser",
    )
    historical_page = municipal.BeautifulSoup(
        """
        <html><body>
          <ul class="program_thumb">
            <li>
              <div class="desc_info">
                <div class="title"><span>실험탐구</span><span>지난 강좌</span></div>
                <ul class="list">
                  <li><b>교육기간</b><span>2000년 01월 01일 ~ 2000년 01월 02일</span></li>
                </ul>
              </div>
            </li>
          </ul>
        </body></html>
        """,
        "html.parser",
    )
    fetched: list[str] = []

    def fake_fetch_soup(_session, url: str, **_kwargs):
        fetched.append(url)
        return historical_page if "page=2" in url else current_page

    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch_soup)
    target = CrawlTarget(
        provider="GWANGJU_NATIONAL_SCIENCE_MUSEUM",
        name="국립광주과학관",
        branch="국립광주과학관",
        url="https://www.sciencecenter.or.kr/kor/board/index.do",
        source="test",
    )

    rows, pages, complete = municipal.collect_gwangju_science_program_list(
        target,
        object(),
        "https://www.sciencecenter.or.kr/kor/program/list.do",
        category_hint="실험탐구",
        timeout=1,
        page_limit=10,
    )

    assert pages == 2
    assert len(fetched) == 2
    assert complete is True
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "별빛 실험실"
    assert row["target"] == "초등 3~6학년"
    assert row["fee"] == "15,000원"
    assert row["period"] == "2099-08-01 ~ 2099-08-30"
    assert row["venue_name"] == "국립광주과학관"
    assert row["category"] == "실험탐구"
    assert row["schedule_raw"] == "토 10:00~12:00"


def test_gwangju_science_discards_notice_fallbacks_when_application_lists_exist(
    monkeypatch,
) -> None:
    list_page = municipal.BeautifulSoup(
        """
        <html><body>
          <a onclick="linkView(100)">교육 공지</a>
        </body></html>
        """,
        "html.parser",
    )
    detail_page = municipal.BeautifulSoup(
        """
        <html><body><div class="board_view">
          <table>
            <tr><th>교육명</th><th>정원/교육비</th><th>예약</th></tr>
            <tr>
              <td>놀이수학</td><td>수학놀이터</td><td>16명/70,000원</td>
              <td><a href="/kor/edu/index.do?mode=list&amp;class=15">바로가기</a></td>
            </tr>
            <tr><td>도전수학</td><td>16명/70,000원</td></tr>
          </table>
        </div></body></html>
        """,
        "html.parser",
    )

    def fake_fetch_soup(_session, url: str, **_kwargs):
        return detail_page if "mode=view" in url else list_page

    application_row = {
        "provider": "GWANGJU_NATIONAL_SCIENCE_MUSEUM",
        "provider_course_id": "application-course",
        "title": "[4기] 놀이수학 도전수학",
        "branch": "국립광주과학관",
        "raw_url": "https://www.sciencecenter.or.kr/kor/edu/index.do?mode=view&eduSEQ=1",
        "period": "2099-08-01 ~ 2099-08-30",
        "schedule_raw": "11:00-12:00",
        "target": "초등 1~3학년",
        "fee": "70,000원",
    }
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch_soup)
    monkeypatch.setattr(
        municipal,
        "collect_gwangju_science_program_list",
        lambda *_args, **_kwargs: ([application_row], 1, True),
    )
    target = CrawlTarget(
        provider="GWANGJU_NATIONAL_SCIENCE_MUSEUM",
        name="국립광주과학관",
        branch="국립광주과학관",
        url=(
            "https://www.sciencecenter.or.kr/kor/board/index.do"
            "?bid=eduNotice&mode=list&menuId=285_335"
        ),
        source="test",
    )

    rows, parser, meta = municipal.collect_gwangju_national_science_museum(
        target,
        timeout=1,
        max_pages=1,
        detail_limit=10,
    )

    assert parser == "gwangju_sciencecenter_notice_detail"
    assert rows == [application_row]
    assert meta["pagination_complete"] is True
    assert meta["application_sources"] == 1
