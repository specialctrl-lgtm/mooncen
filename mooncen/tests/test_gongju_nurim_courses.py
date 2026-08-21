from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal


PROVIDER = "MUNI_WWW_GONGJU_GO_KR_7CBA2D38"
LIST_URL = "https://www.gongju.go.kr/prog/nurimLeaEducate/E01/nurim/sub03_01/list.do"


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=PROVIDER,
        name="공주시청",
        branch="공주시청",
        url=LIST_URL,
        source="test",
    )


def _card(
    edu_no: str,
    title: str,
    period: str,
    *,
    status: str = "접수중",
    branch: str = "공주시 평생학습과",
    venue: str = "공주시 학습관",
    apply_period: str = "2026-06-01 ~ 2026-06-30",
    schedule: str = "수 / 14:00-16:00",
    capacity: str = "2명/10명",
) -> str:
    return f"""
    <div class="list">
      <a href="/prog/leaEducate/E01/learning/sub02_02/view.do?eduNo={edu_no}">
        <div class="state_btn"><p><b>{status}</b><span>자체접수</span></p></div>
        <div class="item_content">
          <div class="tit"><strong>{title}</strong></div>
          <ul class="info">
            <li><b>교육기관</b>{branch}</li>
            <li><b>교육장소</b>{venue}</li>
            <li><b>접수기간</b>{apply_period}</li>
            <li><b>신청/정원</b>{capacity}</li>
            <li><b>교육기간</b>{period}</li>
            <li><b>교육시간</b>{schedule}</li>
          </ul>
        </div>
      </a>
    </div>
    """


def _list_page(total: int, cards: list[str], *, advertised_pages: int = 2) -> BeautifulSoup:
    pager = "".join(
        f'<a href="?pageIndex={page}">{page}</a>'
        for page in range(1, advertised_pages + 1)
    )
    return BeautifulSoup(
        f"<html><body><p>총 게시물 {total:,} 개</p>{''.join(cards)}{pager}</body></html>",
        "lxml",
    )


def _detail(
    title: str,
    period: str,
    *,
    branch: str = "공주시 평생학습과",
    venue: str = "공주시 학습관",
    apply_period: str = "2026-06-01 09:00 ~ 2026-06-30 18:00",
    schedule: str = "수 / 14:00-16:00",
) -> BeautifulSoup:
    pairs = {
        "강좌명": title,
        "교육기간": period,
        "강좌구분": "일반강좌",
        "분야": "문화/예술",
        "접수기간": apply_period,
        "교육시간": schedule,
        "교육대상": "성인",
        "교육장소": venue,
        "정원": "10",
        "강사명": "홍길동",
        "문의전화": "041-000-0000",
        "교육기관": branch,
        "수강료": "무료",
        "교육내용": f"{title} 교육 내용",
    }
    rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in pairs.items())
    return BeautifulSoup(f"<html><body><table>{rows}</table></body></html>", "lxml")


class GongjuFixture:
    def __init__(
        self,
        pages: dict[int, BeautifulSoup],
        details: dict[str, BeautifulSoup | Exception],
    ) -> None:
        self.pages = pages
        self.details = details
        self.calls: list[str] = []

    def __call__(self, _session: Any, url: str, timeout: int = 20) -> BeautifulSoup:
        del timeout
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/list.do"):
            assert query["pageUnit"] == ["2"]
            return self.pages[int(query["pageIndex"][0])]
        edu_no = query["eduNo"][0]
        result = self.details[edu_no]
        if isinstance(result, Exception):
            raise result
        return result


def _fixture(
    *,
    malformed_last_card: bool = False,
    changed_total: bool = False,
    failed_detail: bool = False,
) -> GongjuFixture:
    expired = _card("1", "종료 강좌", "2026-07-01 ~ 2026-07-29").replace(
        "<li><b>교육장소</b>공주시 학습관</li>",
        "<li><b>교육장소</b></li>",
    )
    current = _card(
        "2",
        "현재 강좌",
        "2026-07-30 ~ 2026-08-30",
        capacity="2명/10명 / 대기(3명)",
    )
    future = _card("3", "향후 강좌", "2026-08-01 ~ 2026-09-01")
    if malformed_last_card:
        future = future.replace(
            "<li><b>교육기간</b>2026-08-01 ~ 2026-09-01</li>",
            "<li><b>교육기간</b>날짜 미정</li>",
        )
    pages = {
        1: _list_page(3, [expired, current]),
        2: _list_page(4 if changed_total else 3, [future]),
    }
    details: dict[str, BeautifulSoup | Exception] = {
        "2": _detail("현재 강좌", "2026-07-30 ~ 2026-08-30"),
        "3": (
            RuntimeError("detail unavailable")
            if failed_detail
            else _detail("향후 강좌", "2026-08-01 ~ 2026-09-01")
        ),
    }
    return GongjuFixture(pages, details)


def _collect(
    monkeypatch: pytest.MonkeyPatch,
    fixture: GongjuFixture,
    *,
    max_pages: int = 2,
    detail_limit: int = 2,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    monkeypatch.setattr(municipal, "GONGJU_NURIM_PAGE_UNIT", 2)
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fixture)
    return municipal.collect_gongju_nurim_courses(
        _target(),
        timeout=5,
        max_pages=max_pages,
        detail_limit=detail_limit,
        reference_date=municipal.date(2026, 7, 30),
    )


def test_gongju_scans_declared_snapshot_and_fetches_only_current_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()
    rows, parser, meta = _collect(monkeypatch, fixture)

    assert parser == "gongju_nurim_full_list+current_detail"
    assert [row["title"] for row in rows] == ["현재 강좌", "향후 강좌"]
    assert all(row["category"] == "문화/예술" for row in rows)
    assert all(row["target"] == "성인" for row in rows)
    assert all(row["fee"] == "무료" for row in rows)
    assert all(row["venue_name"] == "공주시 학습관" for row in rows)
    assert meta["declared_total"] == 3
    assert meta["pages"] == 2
    assert meta["detail_pages"] == 2
    assert meta["expired_count"] == 1
    assert meta["historical_partial_count"] == 1
    assert meta["snapshot_complete"] is True
    assert len(fixture.calls) == 4
    assert not any("eduNo=1" in url for url in fixture.calls)


def test_gongju_page_cap_fails_closed_before_partial_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()
    rows, _parser, meta = _collect(monkeypatch, fixture, max_pages=1)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "max_pages cap" in meta["configured_collection_error"]
    assert len(fixture.calls) == 1


def test_gongju_detail_cap_fails_closed_without_partial_enrichment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()
    rows, _parser, meta = _collect(monkeypatch, fixture, detail_limit=1)

    assert rows == []
    assert meta["detail_cap_reached"] is True
    assert meta["detail_pages"] == 0
    assert "detail_limit cap" in meta["configured_collection_error"]
    assert len(fixture.calls) == 2


@pytest.mark.parametrize(
    ("fixture", "error_text"),
    [
        (_fixture(changed_total=True), "declared total changed"),
        (_fixture(malformed_last_card=True), "invalid education period"),
        (_fixture(failed_detail=True), "detail validation failed"),
    ],
)
def test_gongju_contract_drift_or_detail_failure_blocks_output(
    monkeypatch: pytest.MonkeyPatch,
    fixture: GongjuFixture,
    error_text: str,
) -> None:
    rows, _parser, meta = _collect(monkeypatch, fixture)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_text in meta["configured_collection_error"]


def test_gongju_dispatch_forwards_detail_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    def collector(
        target: municipal.CrawlTarget,
        timeout: int,
        max_pages: int,
        detail_limit: int,
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        del target
        captured.update(timeout=timeout, max_pages=max_pages, detail_limit=detail_limit)
        return [], "stub", {}

    monkeypatch.setattr(municipal, "collect_gongju_nurim_courses", collector)
    result = municipal.collect_from_url(
        _target(),
        timeout=7,
        max_depth=1,
        max_pages=50,
        detail_limit=500,
    )

    assert result == ([], "stub", {})
    assert captured == {"timeout": 7, "max_pages": 50, "detail_limit": 500}


def test_gongju_registry_uses_atomic_full_snapshot_limits() -> None:
    expected = (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "500",
    )
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[PROVIDER] == expected

    registry_path = Path(__file__).resolve().parents[1] / "config" / "generated_yaml_crawler_registry.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    entry = next(item for item in registry["targets"] if item["provider"] == PROVIDER)
    assert tuple(entry["arguments"]) == expected
    assert "--allow-partial-save" not in entry["command"]
