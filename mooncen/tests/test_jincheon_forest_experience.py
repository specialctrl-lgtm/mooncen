from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_jincheon_forest_experience as collector
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


ROOT = Path(__file__).resolve().parents[1]
TARGET = {
    "provider": collector.JINCHEON_FOREST_EXPERIENCE_PROVIDER,
    "url": collector.JINCHEON_FOREST_EXPERIENCE_URL,
}


def _item(identity: str, title: str, description: str, period: str) -> str:
    return f"""
      <div class="pw_item">
        <a href="#runParse" onclick="return runParse('/pot/rm/fa/selectPrgrmDtlView.do?insttId=ID02030033&amp;goodsId={identity}', '.layer_wrap', [openLayer, activePgSlideShow, photoWrapSlide], this);">
          <div class="pi_pt">
            <strong class="pp_ti">{title}</strong>
            <div class="pp_txt">{description}</div>
            <ul class="pp_list"><li>이용일 : {period}</li></ul>
          </div>
        </a>
      </div>
    """


EVIDENCE = (
    "어린이, 청소년, 성인 대상 교육 프로그램 "
    "10시(1차시) 20명 정원, 14시(2차시) 20명 정원 "
    "무료입니다 매주 화요일 휴무 모이는 장소 : 산림문화휴양관 주차장"
)


def _html(*, bad_page: bool = False, bad_venue: bool = False) -> bytes:
    evidence = EVIDENCE.replace(
        "산림문화휴양관 주차장",
        "변경 장소" if bad_venue else "산림문화휴양관 주차장",
    )
    items = "".join(
        (
            _item(
                "GID0203003340001000077",
                "숯 2kg+철망",
                "",
                "2026.01.20 ~ 2030.12.31",
            ),
            _item(
                "GID0203003340001000057",
                "숲체험",
                evidence,
                "2026.03.16 ~ 2026.12.15",
            ),
            _item(
                "GID0203003340001000058",
                "유아숲 체험",
                evidence.replace("어린이, 청소년, 성인", "어린이(5세 ~10세)"),
                "2025.04.01 ~ 2026.12.15",
            ),
        )
    )
    page = "1 (1/2)" if bad_page else "1 (1/1)"
    return f"""
      <!doctype html><html><head><meta charset="utf-8">
      <title>생거진천자연휴양림 - 프로그램 |</title></head><body>
        <h1>생거진천자연휴양림</h1>
        <p class="fa_addr">주소 : (우 27822) 충북 진천군 백곡면 명암길 435-135 생거진천자연휴양림 | 전화 : 043-539-3554</p>
        <form id="fripPotForm" method="post">
          <input type="hidden" name="hmpgId" value="ID02030033">
          <input type="hidden" name="nowPage" value="1">
          <div class="prog_webzinlist">{items}</div>
          <div class="page_list">{page}</div>
        </form>
      </body></html>
    """.encode("utf-8")


@dataclass
class _Response:
    url: str
    content: bytes
    status_code: int = 200

    @property
    def history(self) -> tuple[Any, ...]:
        return ()


class _Session:
    def __init__(self, *, bad_page: bool = False, bad_venue: bool = False) -> None:
        self.bad_page = bad_page
        self.bad_venue = bad_venue
        self.calls: list[str] = []
        self.closed = False

    def get(self, url: str, *, timeout: int, allow_redirects: bool) -> _Response:
        assert timeout == 3
        assert allow_redirects is False
        self.calls.append(url)
        return _Response(url, _html(bad_page=self.bad_page, bad_venue=self.bad_venue))

    def close(self) -> None:
        self.closed = True


def _collect(current: _Session | None = None):
    session = current or _Session()
    rows, parser, meta = collector.collect_jincheon_forest_experience(
        TARGET,
        timeout=3,
        max_pages=1,
        detail_limit=3,
        today="2026-08-05",
        session_factory=lambda: session,
    )
    return rows, parser, meta, session


def test_exact_target_and_stable_identifiers() -> None:
    assert collector.is_target(TARGET)
    assert not collector.is_target({**TARGET, "url": TARGET["url"] + "#x"})
    assert not collector.is_target({**TARGET, "url": TARGET["url"].replace("002003", "002004")})
    assert collector.JINCHEON_FOREST_EXPERIENCE_PROVIDER == stable_provider(TARGET["url"])
    assert collector.JINCHEON_FOREST_EXPERIENCE_CANDIDATE_ID == candidate_id(
        normalized_duplicate_url(TARGET["url"])
    )


def test_request_allowlist_blocks_every_other_route_and_method() -> None:
    collector._request_contract("GET", TARGET["url"])
    unsafe = (
        ("POST", TARGET["url"]),
        ("GET", "https://www.foresttrip.go.kr/pot/rm/fa/selectPrgrmDtlView.do?insttId=ID02030033&goodsId=GID0203003340001000057"),
        ("GET", "https://www.foresttrip.go.kr/rep/or/fcfsRsrvtMain.do"),
        ("GET", "https://www.foresttrip.go.kr/com/login.do"),
        ("GET", "https://www.foresttrip.go.kr/com/cm/editorDownload.do?name=x"),
    )
    for method, url in unsafe:
        with pytest.raises(collector.JincheonForestExperienceContractError):
            collector._request_contract(method, url)


def test_complete_snapshot_returns_only_two_fixed_venue_experiences() -> None:
    rows, parser, meta, session = _collect()
    assert parser == collector.JINCHEON_FOREST_EXPERIENCE_PARSER
    assert [row["source_course_id"] for row in rows] == [
        "GID0203003340001000057",
        "GID0203003340001000058",
    ]
    assert meta["source_total"] == meta["source_current_count"] == 3
    assert meta["returned_count"] == 2
    assert meta["excluded_reason_counts"] == {"retail_addon_not_programme": 1}
    assert meta["list_requests"] == meta["physical_requests"] == 1
    assert meta["detail_requests"] == meta["unsafe_endpoint_calls"] == 0
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert session.calls == [TARGET["url"]]
    assert session.closed is True
    assert all(row["municipality_code"] == "4375000000" for row in rows)
    assert all(row["venue"] == "산림문화휴양관 주차장" for row in rows)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(not row["application_url"] and row["reservation_available"] is False for row in rows)


@pytest.mark.parametrize("session", (_Session(bad_page=True), _Session(bad_venue=True)))
def test_contract_drift_is_atomic(session: _Session) -> None:
    rows, _parser, meta, current = _collect(session)
    assert rows == []
    assert meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False
    assert current.closed is True


def test_router_dispatches_only_exact_target(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(target: Any, **kwargs: Any):
        captured["target"] = target
        captured.update(kwargs)
        return ([{"provider": collector.JINCHEON_FOREST_EXPERIENCE_PROVIDER}], "fixture", {})

    monkeypatch.setattr(collector, "collect_jincheon_forest_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.JINCHEON_FOREST_EXPERIENCE_PROVIDER,
        name="생거진천자연휴양림 체험",
        branch="생거진천자연휴양림",
        url=collector.JINCHEON_FOREST_EXPERIENCE_URL,
        source="test",
        priority=1,
        region="충청북도 진천군",
        extra={},
    )
    rows, parser, meta = router.collect_from_url(
        target, timeout=3, max_depth=0, max_pages=1, detail_limit=3
    )
    assert rows and parser == "fixture" and meta == {}
    assert captured["target"] is target
    assert captured["max_pages"] == 1 and captured["detail_limit"] == 3
    assert callable(captured["session_factory"])


def test_target_is_ready_and_experience_locked() -> None:
    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    matches = [
        row
        for row in targets
        if row.get("provider") == collector.JINCHEON_FOREST_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    target = matches[0]
    assert target["url"] == collector.JINCHEON_FOREST_EXPERIENCE_URL
    assert target["crawler_status"] == "ready"
    assert target["service_group"] == "체험"
    assert target["service_group_policy"] == "locked"
    assert target["row_municipality_codes"] == ["4375000000"]
