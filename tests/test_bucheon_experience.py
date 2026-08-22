from __future__ import annotations

import calendar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import municipal_bucheon_experience as collector
from Crawler import Crawler_MunicipalYaml as router


PROGRAMS = tuple(
    {
        "identity": str(9100 + index),
        "title": f"박물관 체험 프로그램 {index}",
        "dong": "오정동" if index == 19 else "중동",
        "district": "오정구" if index == 19 else "원미구",
    }
    for index in range(1, 20)
)


@dataclass
class _Response:
    url: str
    content: bytes
    status_code: int = 200

    @property
    def headers(self) -> dict[str, str]:
        return {"content-type": "text/html; charset=UTF-8"}

    @property
    def history(self) -> tuple[Any, ...]:
        return ()


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target() -> dict[str, str]:
    return {
        "provider": collector.BUCHEON_EXPERIENCE_PROVIDER,
        "url": collector.BUCHEON_EXPERIENCE_URL,
    }


def _list_html(page: int, *, sentinel_nonempty: bool = False) -> bytes:
    selected = PROGRAMS[(page - 1) * 16 : page * 16] if page <= 2 else ()
    if page == 3 and sentinel_nonempty:
        selected = PROGRAMS[:1]
    cards = []
    for program in selected:
        query = (
            f"program_seq={program['identity']}&amp;cp={page}&amp;pageSize=16"
            "&amp;listType=list&amp;search_prg_div=08&amp;viewMode=image"
        )
        cards.append(
            f"""
            <li>
              <a href="/site/main/see/detail?{query}">
                <span class="area">예약중</span>
                <em class="dong">{program['dong']}</em>
                <span class="tit">{program['title']}</span>
              </a>
              <div class="white-bg"><span class="lf">생태박물관</span><p>무료 / 온라인접수</p></div>
              <div class="apl-time">2026-08</div>
            </li>
            """
        )
    return f"""
    <!doctype html><html><head><meta charset="utf-8"><title>부천시 공공서비스예약</title></head>
    <body><form id="seeInfo" method="post" action="/site/main/see/list">
      <h2 class="s-tit">관람/체험</h2>
      <select name="search_prg_div"><option value="08" selected>관람/체험</option></select>
      <span class="num">총 19건</span>
      <ul class="img-list">{''.join(cards)}</ul>
      <div class="page_bk"><span><a href="?cp=1">1</a><a href="?cp=2">2</a></span></div>
    </form></body></html>
    """.encode()


def _detail_html(identity: str, *, bad_reserve_action: bool = False) -> bytes:
    current = next(program for program in PROGRAMS if program["identity"] == identity)
    options = "".join(
        f'<option value="{program["identity"]}"'
        f'{" selected" if program["identity"] == identity else ""}>'
        f'{program["title"]}</option>'
        for program in PROGRAMS
    )
    controls = "".join(
        f'<input name="{name}" value="{value}">'
        for name, value in {
            "program_seq": identity,
            "schy": "2026",
            "schm": "8",
            "schd": "",
            "recurrence": "",
            "reserve_div": "",
            "able_per": "",
            "total_pers": "",
            "see_div0305_cnt": "",
        }.items()
    )
    days = "".join(
        f'<p class="dateNum">{day}</p>'
        for day in range(1, calendar.monthrange(2026, 8)[1] + 1)
    )
    reserve_action = "/site/main/login" if bad_reserve_action else "/site/main/see/reserve"
    fields = {
        "체험장소": "부천 생태박물관",
        "운영시간": "10:00 ~ 17:00",
        "유의사항": "박물관 체험 프로그램입니다.",
        "접수방법": "온라인접수",
        "담당기관": "생태박물관",
        "연락처": "032-123-4567",
        "주소": f"14500 / 경기도 부천시 {current['district']} 길주로 1 / 지도보기",
        "홈페이지": "공식 홈페이지",
    }
    rows = "".join(f"<tr><th>{name}</th><td>{value}</td></tr>" for name, value in fields.items())
    return f"""
    <!doctype html><html><head><meta charset="utf-8"><title>부천시 공공서비스예약</title></head>
    <body><form id="seeSch" method="get" action="/site/main/see/detail">
      <select id="pSeq" name="program_seq">{options}</select>
    </form>
    <div id="sub-contents"><table>{rows}</table></div>
    <form method="post" action="{reserve_action}">{controls}{days}
      <button type="button" onclick="fncGoReserveForm()">신청</button>
    </form></body></html>
    """.encode()


def _collect(*, detail_limit: int = 30, sentinel_nonempty: bool = False, bad_reserve_action: bool = False):
    calls: list[str] = []
    session = _Session()

    def fetcher(_session: Any, url: str, _timeout: int) -> _Response:
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == collector.BUCHEON_EXPERIENCE_LIST_PATH:
            return _Response(url, _list_html(int(query["cp"][0]), sentinel_nonempty=sentinel_nonempty))
        identity = query["program_seq"][0]
        return _Response(url, _detail_html(identity, bad_reserve_action=bad_reserve_action))

    rows, parser, meta = collector.collect_bucheon_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=10,
        detail_limit=detail_limit,
        session_factory=lambda: session,
        fetcher=fetcher,
    )
    return rows, parser, meta, calls, session


def test_exact_target_and_public_get_allowlist() -> None:
    assert collector.is_bucheon_experience_target(_target())
    assert not collector.is_bucheon_experience_target({**_target(), "url": collector.BUCHEON_EXPERIENCE_URL + "&x=1"})
    assert collector._request_kind(collector.bucheon_experience_list_url(2)) == "list"
    assert collector._request_kind(collector.bucheon_experience_detail_url("9101")) == "detail"
    for unsafe in (
        "https://reserv.bucheon.go.kr/site/main/see/reserve",
        "https://reserv.bucheon.go.kr/site/main/login",
        "https://reserv.bucheon.go.kr/site/main/apply/seeGroup",
        "https://reserv.bucheon.go.kr/site/main/see/detail?program_seq=9101&download=1",
    ):
        with pytest.raises(collector.BucheonExperienceContractError):
            collector._request_kind(unsafe)


def test_complete_19_row_fixture_contract_and_no_unsafe_calls() -> None:
    rows, parser, meta, calls, session = _collect()

    assert parser == collector.BUCHEON_EXPERIENCE_PARSER
    assert len(rows) == 19
    assert meta["source_total"] == meta["current_source_count"] == 19
    assert meta["excluded_count"] == 0
    assert meta["page_counts"] == {1: 16, 2: 3}
    assert meta["sentinel_page"] == 3 and meta["sentinel_count"] == 0
    assert meta["detail_verified"] == 19
    assert meta["list_requests"] == 6 and meta["detail_requests"] == 19
    assert meta["logical_requests"] == 25
    assert meta["status_counts"] == {"OPEN": 19}
    assert meta["municipality_counts"] == {"4119200000": 18, "4119600000": 1}
    assert meta["snapshot_complete"] is meta["details_complete"] is True
    assert session.closed is True
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(bool(row["application_url"]) == row["reservation_available"] for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert not any(
        marker in url.lower()
        for url in calls
        for marker in ("reserve", "login", "group", "member", "applicant", "attachment", "download")
        if urlparse(url).path != collector.BUCHEON_EXPERIENCE_LIST_PATH
    )
    for key in (
        "application_endpoint_requests",
        "login_endpoint_requests",
        "group_endpoint_requests",
        "member_endpoint_requests",
        "applicant_endpoint_requests",
        "identity_endpoint_requests",
        "attachment_endpoint_requests",
        "download_endpoint_requests",
        "pii_endpoint_requests",
    ):
        assert meta[key] == 0


def test_contract_drift_and_detail_cap_are_atomic() -> None:
    rows, _, meta, _, session = _collect(sentinel_nonempty=True)
    assert rows == [] and meta["snapshot_complete"] is False
    assert "declared total differs" in meta["errors"][0]
    assert session.closed is True

    rows, _, meta, _, _ = _collect(bad_reserve_action=True)
    assert rows == [] and "reservation control count changed" in meta["errors"][0]

    rows, _, meta, _, _ = _collect(detail_limit=18)
    assert rows == [] and "detail_limit truncates" in meta["errors"][0]


def test_router_dispatches_exact_target_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_collect(target: Any, **kwargs: Any):
        calls.append((target, kwargs))
        return [{"provider": collector.BUCHEON_EXPERIENCE_PROVIDER}], "fixture", {"ok": True}

    monkeypatch.setattr(collector, "collect_bucheon_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.BUCHEON_EXPERIENCE_PROVIDER,
        name="부천시 관람·체험",
        branch="부천시",
        url=collector.BUCHEON_EXPERIENCE_URL,
        source="test",
        priority=1,
        region="경기도 부천시",
        extra={},
    )
    rows, parser, meta = router.collect_from_url(
        target, timeout=3, max_depth=0, max_pages=10, detail_limit=30
    )
    assert rows and parser == "fixture" and meta == {"ok": True}
    assert len(calls) == 1


def test_single_yaml_target_and_operational_coverage_linkage() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = yaml.safe_load((root / "config/crawl_targets/public_reservation.yaml").read_text(encoding="utf-8"))
    matches = [
        item
        for item in targets["targets"]
        if item.get("provider") == collector.BUCHEON_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    assert matches[0]["url"] == collector.BUCHEON_EXPERIENCE_URL
    assert matches[0]["crawler_module"] == "Crawler.municipal_bucheon_experience"

    operational = yaml.safe_load((root / "config/municipal_integrated_reservation_operational.yaml").read_text(encoding="utf-8"))
    assert any(
        item.get("provider") == collector.BUCHEON_EXPERIENCE_PROVIDER
        for item in operational["entries"]
    )
    coverage = yaml.safe_load((root / "config/municipal_integrated_reservation_coverage.yaml").read_text(encoding="utf-8"))
    by_code = {item["code"]: item for item in coverage["municipalities"]}
    for code in ("4119000000", "4119200000", "4119400000", "4119600000"):
        assert collector.BUCHEON_EXPERIENCE_PROVIDER in by_code[code]["owner_providers"]
