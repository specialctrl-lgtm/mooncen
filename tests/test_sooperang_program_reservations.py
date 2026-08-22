from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import yaml

from Crawler import Crawler_MunicipalYaml as municipal
from backend.ops import region_collection as ops_region
from tools.report_scope_region_coverage import compact_text, load_location_overrides


def _response(*, text: str = "", payload: Any = None, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    if payload is not None:
        response._content = json.dumps(payload).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
    else:
        response._content = text.encode("utf-8")
        response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.encoding = "utf-8"
    return response


def _card(
    *,
    title: str = "\uc232\uc624\uac10\uccb4\ud5d8",
    program_id: str = "PI00001",
    goods_id: str = "PG00001",
    duration: str = "60\ubd84",
) -> str:
    return f"""
    <div class="program">
      <div class="program_text">
        <span class="prg_label">\uc232\uccb4\ud5d8</span>
        <h3>{title}</h3>
        <ul><li>\uac00\uc871</li><li>\uc544\ub3d9\uccad\uc18c\ub144</li></ul>
        <div class="program_text_inner"><ul>
          <li><strong>\uc18c\uc694\uc2dc\uac04</strong><p>{duration}</p></li>
          <li><strong>\ud6a8\uacfc</strong><p>\uc0dd\ud0dc\uac10\uc218\uc131 \uc99d\uc9c4</p></li>
          <li><strong>\uc124\uba85</strong><p>\uc232\uc5d0\uc11c \uc9c4\ud589\ud558\ub294 \uccb4\ud5d8</p></li>
        </ul></div>
      </div>
      <img class="slide_img" src="/images/program.jpg">
      <button onclick="fn_prgmRsrvt('{program_id}', '{goods_id}')">\uc608\uc57d</button>
    </div>
    """


def _policy(
    start: str = "2026-07-28",
    end: str = "2026-07-29",
    *,
    disabled: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "minDate": start,
        "maxDate": end,
        "disabledDayList": disabled or [],
        "monthOpenBgdt": "15",
        "monthOpenTm": "09",
        "dateOpenBgnDate": "1",
    }


class FakeSession:
    def __init__(
        self,
        *,
        institutions: list[dict[str, str]],
        policies: dict[str, dict[str, Any]],
        pages: dict[tuple[str, str], str],
    ) -> None:
        self.headers: dict[str, str] = {}
        self.institutions = institutions
        self.policies = policies
        self.pages = pages
        self.gets: list[tuple[str, str]] = []

    def post(
        self,
        url: str,
        data: dict[str, str],
        headers: dict[str, str],
        timeout: int,
    ) -> requests.Response:
        assert timeout == 7
        assert headers["X-Requested-With"] == "XMLHttpRequest"
        if url == municipal.SOOPERANG_INSTITUTION_AJAX_URL:
            return _response(payload={"arcdInsttList": self.institutions})
        if url == municipal.SOOPERANG_POLICY_AJAX_URL:
            return _response(payload={"rsrvtPlcyVO": self.policies[data["insttId"]]})
        raise AssertionError(url)

    def get(self, url: str, timeout: int) -> requests.Response:
        assert timeout == 7
        query = parse_qs(urlparse(url).query)
        key = (query["searchInsttId"][0], query["searchUseDt"][0])
        self.gets.append(key)
        return _response(text=self.pages.get(key, "<html><body></body></html>"))


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="NATIONAL_FOREST_EDUCATION_CENTER",
        name="\uc232e\ub791 \uc0b0\ub9bc\ubcf5\uc9c0 \uc77c\uc77c\uccb4\ud5d8",
        branch=municipal.SOOPERANG_BRANCH,
        url=municipal.sooperang_list_url(),
        source="test",
    )


def test_collects_every_policy_date_and_complete_required_fields() -> None:
    fake = FakeSession(
        institutions=[
            {"insttId": "FA00001", "insttNm": "\uad6d\ub9bd\ud6a1\uc131\uc232\uccb4\uc6d0"},
            {"insttId": "FA00002", "insttNm": "\uad6d\ub9bd\uc7a5\uc131\uc232\uccb4\uc6d0"},
        ],
        policies={
            "FA00001": _policy(),
            "FA00002": _policy(disabled=["2026-07-29"]),
        },
        pages={
            ("FA00001", "2026-07-28"): _card(),
            ("FA00001", "2026-07-29"): "",
            ("FA00002", "2026-07-28"): _card(
                title="\uc232\uacf5\uc608",
                program_id="PI00002",
                goods_id="PG00002",
                duration="2\uc2dc\uac04",
            ),
        },
    )

    rows, parser, meta = municipal.collect_sooperang_program_reservations(
        _target(),
        timeout=7,
        max_pages=3,
        session_factory=lambda: fake,
        today=date(2026, 7, 28),
    )

    assert parser == "sooperang_complete_booking_dates"
    assert len(rows) == 2
    assert fake.gets == [
        ("FA00001", "2026-07-28"),
        ("FA00001", "2026-07-29"),
        ("FA00002", "2026-07-28"),
    ]
    first = rows[0]
    assert first["target"] == "\uac00\uc871, \uc544\ub3d9\uccad\uc18c\ub144"
    assert first["fee"] == "\uc694\uae08 \ubcc4\ub3c4 \uc548\ub0b4"
    assert first["period"] == "2026-07-28"
    assert first["venue_name"] == "\uad6d\ub9bd\ud6a1\uc131\uc232\uccb4\uc6d0"
    assert first["category"] == "\uc232\uccb4\ud5d8"
    assert first["schedule_raw"] == "\ud68c\ucc28 \uc2dc\uac04 \uae30\uad00 \ud611\uc758 (\uc18c\uc694\uc2dc\uac04 60\ubd84)"
    assert first["apply_period"] == "2026-06-15 09:00 ~ 2026-07-27 15:00"
    assert first["collection_category"] == "\uccb4\ud5d8"
    assert first["raw_fields"]["parser"] == "sooperang_complete_booking_dates"
    assert meta["institutions"] == 2
    assert meta["policy_pages"] == 2
    assert meta["booking_scopes"] == 3
    assert meta["pages"] == 3
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is False


def test_complete_scope_must_fit_max_pages_before_list_fetches() -> None:
    fake = FakeSession(
        institutions=[
            {"insttId": "FA00001", "insttNm": "\uad6d\ub9bd\ud6a1\uc131\uc232\uccb4\uc6d0"},
        ],
        policies={"FA00001": _policy()},
        pages={},
    )

    with pytest.raises(RuntimeError, match="requires 2 pages; max_pages=1"):
        municipal.collect_sooperang_program_reservations(
            _target(),
            timeout=7,
            max_pages=1,
            session_factory=lambda: fake,
            today=date(2026, 7, 28),
        )
    assert fake.gets == []


def test_institution_identity_change_fails_closed() -> None:
    fake = FakeSession(
        institutions=[{"insttId": "../../bad", "insttNm": "\uc798\ubabb\ub41c \uae30\uad00"}],
        policies={},
        pages={},
    )

    with pytest.raises(RuntimeError, match="institution discovery failed"):
        municipal.collect_sooperang_program_reservations(
            _target(),
            timeout=7,
            max_pages=10,
            session_factory=lambda: fake,
            today=date(2026, 7, 28),
        )


def test_booking_policy_window_is_bounded() -> None:
    fake = FakeSession(
        institutions=[
            {"insttId": "FA00001", "insttNm": "\uad6d\ub9bd\ud6a1\uc131\uc232\uccb4\uc6d0"},
        ],
        policies={"FA00001": _policy("2026-07-28", "2026-10-31")},
        pages={},
    )

    with pytest.raises(RuntimeError, match="exceeds reviewed 62-day window"):
        municipal.collect_sooperang_program_reservations(
            _target(),
            timeout=7,
            max_pages=200,
            session_factory=lambda: fake,
            today=date(2026, 7, 28),
        )


def test_complete_empty_scope_is_no_current_data() -> None:
    fake = FakeSession(
        institutions=[
            {"insttId": "FA00001", "insttNm": "\uad6d\ub9bd\ud6a1\uc131\uc232\uccb4\uc6d0"},
        ],
        policies={"FA00001": _policy("2026-07-28", "2026-07-28")},
        pages={},
    )

    rows, _parser, meta = municipal.collect_sooperang_program_reservations(
        _target(),
        timeout=7,
        max_pages=1,
        session_factory=lambda: fake,
        today=date(2026, 7, 28),
    )

    assert rows == []
    assert meta["pages"] == 1
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]


def test_all_official_sooperang_institutions_are_mapped_to_ops_regions() -> None:
    expected = {
        "국립횡성숲체원": ("5173000000", "강원특별자치도 횡성군"),
        "국립장성숲체원": ("1284000000", "전남광주통합특별시 장성군"),
        "국립산림치유원": ("4721000000", "경상북도 영주시"),
        "국립춘천숲체원": ("5111000000", "강원특별자치도 춘천시"),
        "국립제천치유의숲": ("4315000000", "충청북도 제천시"),
        "국립진안고원산림치유원": ("5272000000", "전북특별자치도 진안군"),
        "국립부산승학산치유의숲": ("2638000000", "부산광역시 사하구"),
        "국립청도숲체원": ("4782000000", "경상북도 청도군"),
        "국립양평치유의숲": ("4183000000", "경기도 양평군"),
        "국립익산치유의숲": ("5214000000", "전북특별자치도 익산시"),
        "국립칠곡숲체원": ("4785000000", "경상북도 칠곡군"),
        "국립나주숲체원": ("1217000000", "전남광주통합특별시 나주시"),
        "국립대관령치유의숲": ("5115000000", "강원특별자치도 강릉시"),
        "국립대전숲체원": ("3020000000", "대전광역시 유성구"),
        "국립대운산치유의숲": ("3171000000", "울산광역시 울주군"),
        "국립김천치유의숲": ("4715000000", "경상북도 김천시"),
        "국립예산치유의숲": ("4481000000", "충청남도 예산군"),
        "국립곡성치유의숲": ("1272000000", "전남광주통합특별시 곡성군"),
        "국립화순치유의숲": ("1276000000", "전남광주통합특별시 화순군"),
        "국립고창치유의숲": ("5279000000", "전북특별자치도 고창군"),
    }
    provider = "NATIONAL_FOREST_EDUCATION_CENTER"
    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load(
        (root / "config/crawl_targets/generated_review.yaml").read_text(
            encoding="utf-8"
        )
    )
    targets = [
        row for row in document["targets"] if row.get("provider") == provider
    ]
    assert len(targets) == 1
    assert set(targets[0]["row_municipality_codes"]) == {
        code for code, _full_name in expected.values()
    }

    reference = ops_region._region_reference()
    overrides = load_location_overrides(
        reference.index,
        root / "config/scope_region_location_overrides.yaml",
    )
    for branch, (_code, full_name) in expected.items():
        assert overrides[(provider, compact_text(branch))].full_name == full_name
        assert provider in reference.configured_by_scope["experience"][full_name]
        assert provider not in reference.configured_by_scope["education"].get(
            full_name, ()
        )

    assert provider not in reference.unmapped_configured_by_scope["experience"]
