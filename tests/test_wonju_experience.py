from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from Crawler import municipal_wonju_experience as wonju
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


ROOT = Path(__file__).resolve().parents[1]


def _target(**overrides: Any) -> dict[str, Any]:
    return {
        "provider": wonju.WONJU_EXPERIENCE_PROVIDER,
        "url": wonju.WONJU_EXPERIENCE_URL,
        "name": "원주시 통합예약 전체 체험·견학",
        "branch": "강원특별자치도 원주시",
        **overrides,
    }


def _registry() -> str:
    return "".join(
        (
            '<a href="/www/selectTnExprnRceptListU.do?key=101&si1=8">원주시청</a>',
            '<a href="/www/selectTnExprnRceptListU.do?key=103&si1=1">도시정보센터</a>',
            '<a href="/www/selectTnExprnRceptListU.do?key=105&si1=2">산악자전거</a>',
            '<a href="/www/selectTnExprnRceptListU.do?key=201&si1=21">남원주건강생활지원센터</a>',
            '<a href="/www/selectTnExprnRceptListU.do?key=212&si1=25">영상미디어센터</a>',
        )
    )


def _card(
    identity: str,
    title: str,
    source_status: str,
    institution: str,
    period: str,
    *,
    venue: str | None = None,
    weekdays: str = "화,목",
    branch_code: str = "",
) -> str:
    detail_query = f"progrmNo={identity}"
    if branch_code:
        detail_query += f"&si1={branch_code}"
    detail_query += "&key=99"
    venue = venue or institution
    return f"""
      <li class="thumbnail_item service1">
        <a class="thumbnail_anchor" href="./viewTnExprnRceptU.do?{detail_query}">
          <span class="thumbnail_content">
            <span class="stat n2">{source_status}</span>
            <span class="place">{institution}</span>
            <span class="price free">무료</span>
            <span class="thumbnail_sub">{title}</span>
            <span class="info">
              <span class="info_item"><span class="info_sub">장소</span>{venue}</span>
              <span class="info_item"><span class="info_sub">요일</span>{weekdays}</span>
              <span class="info_item"><span class="info_sub">기간</span>{period}</span>
            </span>
          </span>
        </a>
      </li>
    """


def _page(total: int, page: int, last: int, cards: str, *, registry: bool = False) -> str:
    return f"""
      <html><head><meta charset="utf-8"><title>체험/견학 신청(전체) - 원주시 통합예약플랫폼</title></head>
      <body>
        {_registry() if registry else ''}
        <div class="program program_list experience_list">
          <span class="small">총게시물 : {total} 건 페이지 : {page}/{last}</span>
          <div class="thumbnail active"><ul class="thumbnail_list">{cards}</ul></div>
        </div>
      </body></html>
    """


def _detail(
    identity: str,
    title: str,
    institution: str,
    period: str,
    *,
    venue: str | None = None,
    weekdays: str = "화,목",
    application: bool = True,
    wrong_title: bool = False,
) -> str:
    venue = venue or institution
    control = (
        f'<a href="./selectTnExprnRceptCalU.do?progrmNo={identity}&key=99">신청하기</a>'
        if application
        else ""
    )
    heading = "다른 프로그램" if wrong_title else title
    return f"""
      <html><head><meta charset="utf-8"><title>체험/견학 신청(전체) - 원주시 통합예약플랫폼</title></head>
      <body><div class="program program_view experience_view">
        <div class="view_topbox"><p class="topbox_sub">{heading}</p>{control}</div>
        <div class="view_contents"><div class="view_box">
          <table><caption>체험견학 정보 - 프로그램 공개 정보</caption><tbody>
            <tr><th>접수기관</th><td>{institution}</td></tr>
            <tr><th>프로그램명</th><td>{title}</td></tr>
            <tr><th>장소</th><td>{venue}</td></tr>
            <tr><th>주소</th><td>강원특별자치도 원주시 시청로 1</td></tr>
            <tr><th>운영요일</th><td>{weekdays}</td></tr>
            <tr><th>운영기간</th><td>{period}</td></tr>
            <tr><th>담당자/문의전화</th><td>공개 담당자 033-737-0000</td></tr>
            <tr><th>첨부파일</th><td><a href="/www/downloadExprnFile.do?id={identity}">안내문</a></td></tr>
          </tbody></table>
        </div></div>
      </div></body></html>
    """


OPEN = {
    "identity": "38",
    "title": "산악자전거 코스 체험",
    "source_status": "접수중",
    "institution": "원주산악자전거파크",
    "period": "2026.04.25 ~ 2026.12.01",
    "branch_code": "2",
}
ROLLING = {
    "identity": "20",
    "title": "도시정보센터 상시 견학",
    "source_status": "접수중",
    "institution": "도시정보센터",
    "period": "신청일 기준 60일까지",
    "branch_code": "1",
}
EXPIRED = {
    "identity": "18",
    "title": "지난 원주시청 견학",
    "source_status": "접수마감",
    "institution": "원주시청",
    "period": "2024.01.01 ~ 2024.12.31",
    "branch_code": "8",
}
FUTURE_CLOSED = {
    "identity": "40",
    "title": "예약 마감 영상 체험",
    "source_status": "접수마감",
    "institution": "영상미디어센터",
    "period": "2026.09.01 ~ 2026.10.01",
    "branch_code": "25",
}
ITEMS = (OPEN, ROLLING, EXPIRED, FUTURE_CLOSED)


@dataclass
class _Response:
    text: str
    url: str
    status_code: int = 200
    history: tuple[Any, ...] = ()
    headers: Mapping[str, str] | None = None

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")


class _Session:
    def close(self) -> None:
        pass


def _fixture(
    *, wrong_detail: bool = False, partition_drift: bool = False
) -> tuple[dict[str, str], list[str]]:
    aggregate_cards = "".join(_card(**item) for item in ITEMS)
    pages: dict[str, str] = {
        wonju.wonju_experience_list_url(1): _page(
            4, 1, 1, aggregate_cards, registry=True
        ),
        wonju.wonju_experience_list_url(2): _page(4, 2, 1, ""),
    }
    for branch in wonju.WONJU_EXPERIENCE_BRANCHES:
        branch_items = [item for item in ITEMS if item["branch_code"] == branch.code]
        cards = "".join(_card(**item) for item in branch_items)
        if partition_drift and branch.code == "2":
            changed = dict(OPEN)
            changed["title"] = "바뀐 제목"
            cards = _card(**changed)
        pages[wonju.wonju_experience_list_url(1, branch)] = _page(
            len(branch_items), 1, 1, cards
        )
    for item in (OPEN, ROLLING, FUTURE_CLOSED):
        pages[wonju.wonju_experience_detail_url(item["identity"])] = _detail(
            item["identity"],
            item["title"],
            item["institution"],
            item["period"],
            application=item["source_status"] == "접수중",
            wrong_title=wrong_detail and item["identity"] == OPEN["identity"],
        )
    return pages, []


def _run(
    *,
    pages: Mapping[str, str] | None = None,
    calls: list[str] | None = None,
    detail_limit: int = 10,
):
    if pages is None or calls is None:
        fixture_pages, fixture_calls = _fixture()
        pages = fixture_pages if pages is None else pages
        calls = fixture_calls if calls is None else calls

    def fetcher(_session: Any, url: str, _timeout: int) -> _Response:
        calls.append(url)
        if url not in pages:
            raise AssertionError(f"unexpected request: {url}")
        return _Response(pages[url], url, headers={"content-type": "text/html"})

    result = wonju.collect_wonju_experience_courses(
        _target(),
        max_pages=5,
        detail_limit=detail_limit,
        today="2026-08-05",
        session_factory=_Session,
        fetcher=fetcher,
    )
    return result, calls


def test_provider_and_candidate_identity_are_stable() -> None:
    normalized = normalized_duplicate_url(wonju.WONJU_EXPERIENCE_URL)
    assert stable_provider(normalized) == wonju.WONJU_EXPERIENCE_PROVIDER
    assert candidate_id(normalized) == wonju.WONJU_EXPERIENCE_CANDIDATE_ID


def test_target_and_get_allowlist_are_exact() -> None:
    assert wonju.is_wonju_experience_target(_target())
    assert wonju.is_wonju_experience_target(
        _target(url="https://yeyak.wonju.go.kr/www/selectTnExprnRceptListU.do?key=99")
    )
    assert not wonju.is_wonju_experience_target(_target(provider="OTHER"))
    assert not wonju.is_wonju_experience_target(
        _target(url=wonju.WONJU_EXPERIENCE_URL + "&si1=2")
    )
    assert not wonju.is_wonju_experience_target(
        _target(url=wonju.WONJU_EXPERIENCE_URL + "#fragment")
    )

    assert wonju._request_kind("GET", wonju.wonju_experience_list_url(2)) == "list"
    assert wonju._request_kind("GET", wonju.wonju_experience_detail_url("38")) == "detail"
    for unsafe in (
        wonju.wonju_experience_calendar_url("38"),
        "https://yeyak.wonju.go.kr/loginView.do",
        "https://yeyak.wonju.go.kr/www/downloadExprnFile.do?id=38",
        wonju.WONJU_EXPERIENCE_URL + "&si1=999",
    ):
        with pytest.raises(wonju.WonjuExperienceContractError):
            wonju._request_kind("GET", unsafe)


def test_complete_snapshot_reconciles_partitions_and_never_fetches_controls() -> None:
    (rows, parser, meta), calls = _run()
    assert parser == wonju.WONJU_EXPERIENCE_PARSER
    assert [row["source_course_id"] for row in rows] == [
        "experience:38",
        "experience:20",
        "experience:40",
    ]
    assert {row["service_group"] for row in rows} == {"체험"}
    assert {row["domain_category"] for row in rows} == {"체험·견학"}
    assert {row["service_group_policy"] for row in rows} == {"locked"}
    assert {row["branch_code"] for row in rows} == {"1", "2", "25"}
    assert rows[1]["start_date"] is None and rows[1]["end_date"] is None
    assert rows[2]["status"] == "CLOSED"
    assert rows[2]["application_url"] == ""
    assert all("033-737-0000" not in repr(row) for row in rows)
    assert all("downloadExprnFile" not in repr(row) for row in rows)

    assert meta["source_total"] == 4
    assert meta["source_pages"] == 1
    assert meta["sentinel_page"] == 2
    assert meta["institution_totals"] == {
        "8": 1,
        "1": 1,
        "2": 1,
        "21": 0,
        "25": 1,
    }
    assert meta["current_count"] == 3
    assert meta["expired_count"] == 1
    assert meta["detail_pages"] == 3
    assert meta["logical_requests"] == 12
    assert meta["list_requests"] == 9
    assert meta["detail_requests"] == 3
    assert meta["application_control_count"] == 2
    assert meta["pagination_complete"] is True
    assert meta["partitions_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert all("selectTnExprnRceptCalU.do" not in url for url in calls)
    assert all("login" not in url.lower() for url in calls)
    assert all("download" not in url.lower() for url in calls)


def test_partition_drift_fails_before_detail_requests() -> None:
    pages, calls = _fixture(partition_drift=True)
    (rows, _parser, meta), calls = _run(pages=pages, calls=calls)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_requests"] == 0
    assert "escaped aggregate" in meta["errors"][0]
    assert all("viewTnExprnRceptU.do" not in url for url in calls)


def test_detail_drift_and_limit_fail_atomically() -> None:
    pages, calls = _fixture(wrong_detail=True)
    (rows, _parser, meta), _ = _run(pages=pages, calls=calls)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail title drift" in meta["errors"][0]

    pages, calls = _fixture()
    (rows, _parser, meta), calls = _run(
        pages=pages, calls=calls, detail_limit=2
    )
    assert rows == []
    assert meta["detail_requests"] == 0
    assert "detail limit truncates" in meta["errors"][0]
    assert all("viewTnExprnRceptU.do" not in url for url in calls)


def test_router_and_operational_configs_are_wired(monkeypatch) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    sentinel = ([{"id": 1}], wonju.WONJU_EXPERIENCE_PARSER, {"snapshot_complete": True})
    captured: dict[str, object] = {}

    def collect(*_args: Any, **kwargs: Any):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(wonju, "collect_wonju_experience_courses", collect)
    target = municipal.CrawlTarget(
        provider=wonju.WONJU_EXPERIENCE_PROVIDER,
        name="원주시 체험·견학",
        branch="강원특별자치도 원주시",
        url=wonju.WONJU_EXPERIENCE_URL,
        source="test",
    )
    assert municipal.collect_from_url(
        target, timeout=3, max_pages=10, detail_limit=20
    ) == sentinel
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])

    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    matches = [
        item
        for item in targets
        if item.get("provider") == wonju.WONJU_EXPERIENCE_PROVIDER
        and item.get("url") == wonju.WONJU_EXPERIENCE_URL
    ]
    assert len(matches) == 1
    assert matches[0]["crawler_status"] == "ready"
    assert matches[0]["service_group"] == "체험"
    assert matches[0]["last_quality"]["snapshot_complete"] is True

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    entries = [
        item
        for item in operational
        if item.get("provider") == wonju.WONJU_EXPERIENCE_PROVIDER
        and item.get("target_url") == wonju.WONJU_EXPERIENCE_URL
    ]
    assert len(entries) == 1
    assert entries[0]["validation_outcome"] == "collected"
    assert entries[0]["row_count"] == 7
