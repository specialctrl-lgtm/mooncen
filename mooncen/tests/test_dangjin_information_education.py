from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import municipal_dangjin as dangjin


def _list_html(rows: list[tuple[str, ...]], total: int = 2) -> str:
    body = "".join(
        (
            "<tr>"
            f"<td>{sequence}</td>"
            f"<td><a href='{href}'>{title}</a></td>"
            f"<td>{period}</td>"
            f"<td>{schedule}</td>"
            f"<td>{apply_period}</td>"
            f"<td>{capacity}</td>"
            f"<td>{status}</td>"
            "</tr>"
        )
        for (
            sequence,
            href,
            title,
            period,
            schedule,
            apply_period,
            capacity,
            status,
        ) in rows
    )
    return f"""
    <html>
      <head><title>당진시청 &gt; 분야별정보 &gt; 시민정보화교육</title></head>
      <body>
        <div id="content">
          <p class="board_total">총게시물 : {total}건</p>
          <table class="tbl_basic center">
            <caption>시민정보화교육 목록입니다.</caption>
            <thead><tr>
              <th>번호</th><th>교육과목</th><th>교육기간</th>
              <th>교육시간</th><th>접수기간</th>
              <th>접수자/정원</th><th>상태</th>
            </tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
      </body>
    </html>
    """


def _detail_html(
    *,
    title: str = "[오후교육] 포토샵 활용(이미지 편집)",
    schedule: str = "13:00 ~ 15:00",
    fee: str = "",
) -> str:
    return f"""
    <html>
      <head><title>당진시청 &gt; 분야별정보 &gt; 시민정보화교육</title></head>
      <body>
        <div id="content">
          <h1>시민정보화교육</h1>
          <h2>{title}</h2>
          <table class="basic_table">
            <tr><th>접수기간</th><td>2026년 04월 27일 ~ 2026년 05월 07일</td></tr>
            <tr><th>교육대상</th><td>당진시민</td></tr>
            <tr>
              <th>교육기간</th><td>2026-05-11~2026-05-22</td>
              <th>교육시간</th><td>{schedule}</td>
            </tr>
            <tr>
              <th>교육장소</th><td>당진시청 5층 전산교육장</td>
              <th>교육비</th><td>{fee}</td>
            </tr>
            <tr>
              <th>전화문의</th><td>041-350-3153</td>
              <th>교육인원</th><td>5</td>
            </tr>
            <tr><th>강의계획서</th><td>강의계획서.hwp</td></tr>
            <tr><th>기타</th><td>월~금 진행하는 시민 정보화 과정</td></tr>
          </table>
          <h2>정보 변경 내역</h2>
        </div>
      </body>
    </html>
    """


CURRENT_DETAIL = (
    "https://www.dangjin.go.kr/prog/reprsntInfrmEdu/kor/"
    "sub05_07_01/view.do?schedule_seq=20282"
)
EXPIRED_DETAIL = (
    "https://www.dangjin.go.kr/prog/reprsntInfrmEdu/kor/"
    "sub05_07_01/view.do?schedule_seq=20273"
)
LIST_ROWS = [
    (
        "2",
        "/prog/reprsntInfrmEdu/kor/sub05_07_01/view.do?schedule_seq=20282",
        "[오후교육] 포토샵 활용(이미지 편집)",
        "2026-05-11~2026-05-22",
        "13:00 ~ 15:00",
        "2026.04.27 ~ 2026.05.07",
        "0 / 5",
        "접수예정",
    ),
    (
        "1",
        "/prog/reprsntInfrmEdu/kor/sub05_07_01/view.do?schedule_seq=20273",
        "[오후교육] 스마트폰 기본 활용",
        "2026-03-09~2026-03-20",
        "13:00 ~ 15:00",
        "2026.02.23 ~ 2026.03.05",
        "2 / 5",
        "접수완료",
    ),
]


class _Session:
    def close(self) -> None:
        return None


def _collector(
    *,
    today: str,
    detail_html: str | None = None,
    max_pages: int = 20,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], list[str]]:
    calls: list[str] = []
    first = _list_html(LIST_ROWS)
    sentinel = _list_html([], total=2)
    pages = {
        dangjin.DANGJIN_CANONICAL_URL: first,
        f"{dangjin.DANGJIN_CANONICAL_URL}?pageIndex=2": sentinel,
    }
    if detail_html is not None:
        pages[CURRENT_DETAIL] = detail_html

    def fetcher(_session: Any, url: str, _timeout: int) -> BeautifulSoup:
        calls.append(url)
        return BeautifulSoup(pages[url], "lxml")

    rows, parser, meta = dangjin.collect_dangjin_information_courses(
        {
            "provider": dangjin.DANGJIN_PROVIDER,
            "url": dangjin.DANGJIN_CANONICAL_URL,
        },
        timeout=10,
        max_pages=max_pages,
        detail_limit=10,
        fetcher=fetcher,
        session_factory=_Session,
        today=today,
        max_workers=2,
    )
    return rows, parser, meta, calls


def test_dangjin_collector_returns_complete_current_course_fields() -> None:
    rows, parser, meta, calls = _collector(
        today="2026-04-01",
        detail_html=_detail_html(),
    )

    assert parser == dangjin.DANGJIN_PARSER
    assert len(rows) == 1
    assert meta["source_total"] == 2
    assert meta["current_count"] == 1
    assert meta["expired_count"] == 1
    assert meta["pages"] == 2
    assert meta["detail_pages"] == 1
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert calls.count(dangjin.DANGJIN_CANONICAL_URL) == 2
    assert CURRENT_DETAIL in calls

    row = rows[0]
    assert row["title"] == "[오후교육] 포토샵 활용(이미지 편집)"
    assert row["target"] == "당진시민"
    assert row["fee"] == "무료"
    assert row["period"] == "2026-05-11 ~ 2026-05-22"
    assert row["venue_name"] == "당진시청 5층 전산교육장"
    assert row["category"] == "디지털·사진"
    assert row["schedule_raw"] == "13:00 ~ 15:00"
    assert row["capacity_current"] == 0
    assert row["capacity_total"] == 5
    assert row["raw_url"] == CURRENT_DETAIL


def test_dangjin_complete_expired_catalogue_is_no_current_data() -> None:
    rows, _, meta, calls = _collector(today="2026-07-28")

    assert rows == []
    assert meta["source_total"] == 2
    assert meta["current_count"] == 0
    assert meta["expired_count"] == 2
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert "have ended" in meta["no_current_reason"]
    assert CURRENT_DETAIL not in calls
    assert EXPIRED_DETAIL not in calls


def test_dangjin_page_cap_fails_closed_before_sentinel() -> None:
    rows, _, meta, _ = _collector(today="2026-07-28", max_pages=1)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["page_cap_reached"] is True
    assert meta["source_cap_reached"] is True
    assert "below required 2" in meta["configured_collection_error"]


def test_dangjin_detail_schedule_mismatch_fails_closed() -> None:
    rows, _, meta, _ = _collector(
        today="2026-04-01",
        detail_html=_detail_html(schedule="14:00 ~ 16:00"),
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "schedule mismatch" in meta["configured_collection_error"]


def test_dangjin_target_contract_rejects_query_and_credentials() -> None:
    assert dangjin.is_dangjin_target(
        {
            "provider": dangjin.DANGJIN_PROVIDER,
            "url": dangjin.DANGJIN_CANONICAL_URL,
        }
    )
    assert not dangjin.is_dangjin_target(
        {
            "provider": dangjin.DANGJIN_PROVIDER,
            "url": f"{dangjin.DANGJIN_CANONICAL_URL}?pageIndex=1",
        }
    )
    assert not dangjin.is_dangjin_target(
        {
            "provider": dangjin.DANGJIN_PROVIDER,
            "url": dangjin.DANGJIN_CANONICAL_URL.replace(
                "https://", "https://user:pass@"
            ),
        }
    )


def test_municipal_router_uses_dangjin_dedicated_collector(
    monkeypatch: Any,
) -> None:
    expected = ([{"title": "전용"}], dangjin.DANGJIN_PARSER, {"pages": 1})

    def fake_collect(*_args: Any, **_kwargs: Any) -> Any:
        return expected

    monkeypatch.setattr(
        dangjin,
        "collect_dangjin_information_courses",
        fake_collect,
    )
    target = municipal.CrawlTarget(
        provider=dangjin.DANGJIN_PROVIDER,
        name="당진 시민정보화교육",
        branch="충청남도 당진시",
        url=dangjin.DANGJIN_CANONICAL_URL,
        source="test",
    )

    assert municipal.collect_from_url(target) == expected


def test_dangjin_generated_command_uses_complete_snapshot() -> None:
    arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        dangjin.DANGJIN_PROVIDER
    ]

    assert arguments[:4] == (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
    )
    assert "--allow-partial-save" not in arguments
    assert arguments[arguments.index("--max-pages") + 1] == "20"
    assert arguments[arguments.index("--detail-limit") + 1] == "100"
