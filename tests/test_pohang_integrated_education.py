from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as municipal_runner
from Crawler import municipal_pohang_integrated as pohang


def _target(**overrides):
    value = {
        "provider": pohang.POHANG_INTEGRATED_PROVIDER,
        "url": pohang.POHANG_INTEGRATED_LIST_URL,
    }
    value.update(overrides)
    return value


def _card(
    identity: str,
    title: str,
    *,
    venue: str,
    end_date: str,
    status: str = "모집마감",
) -> str:
    return f"""
      <li>
        <a href="#" data-req-form-id="viewForm" data-req-p-idx="{identity}">
          <span class="tag end">{status}</span><span class="tag">교육중</span>
          <p class="subject">{title}</p>
        </a>
        <ul class="info">
          <li><strong>교육기간</strong>2026-08-01 ~ {end_date}</li>
          <li><strong>교육시간</strong>10:00 ~ 12:00</li>
          <li><strong>접수기간</strong>2026-07-01 ~ 2026-07-31</li>
          <li><strong>교육대상</strong>성인</li>
          <li><strong>교육장소</strong><em>{venue}</em> &gt; 1교육장</li>
          <li><strong>모집인원</strong>25</li>
        </ul>
      </li>
    """


def _list_page(page: int, cards: str, *, total: int = 4, pages: int = 1) -> str:
    return f"""
      <html><body>
        <form id="list" action="/apply/lecture/lectureInfoList.do?mid=0101000000"></form>
        <p class="page_total">총 <em>{total}</em>건이 있습니다.</p>
        <p class="page_num">현재 페이지 <em>{page}</em> / 전체 페이지 {pages}</p>
        <div class="multiPurpose-list edu"><ul>{cards}</ul></div>
      </body></html>
    """


def _detail(
    identity: str,
    title: str,
    *,
    venue: str,
    end_date: str,
    open_for_application: bool = False,
) -> str:
    control = (
        f'<a data-req-form-id="writeForm" data-req-p-idx="{identity}">신청</a>'
        if open_for_application
        else ""
    )
    return f"""
      <html><body>
        <form id="writeForm" action="/apply/lecture/lectureRequestWrite.do?mid=0101000000">
          <input name="idx" value="{identity}">
        </form>
        <div class="multiPurpose-view">
          <div class="subject">{title}</div>
          <ul class="info">
            <li><strong>교육기간</strong>2026-08-01 ~ {end_date}</li>
            <li><strong>교육시간</strong>10:00 ~ 12:00</li>
            <li><strong>접수기간</strong>2026-07-01 ~ 2026-07-31</li>
            <li><strong>교육대상</strong>성인</li>
            <li><strong>교육장소</strong>{venue} &gt; 1교육장</li>
            <li><strong>모집인원</strong>25</li>
          </ul>
          {control}
          <div class="int_box textarea">공개 교육 설명 010-1234-5678</div>
        </div>
      </body></html>
    """


@dataclass
class _Response:
    url: str
    text: str
    status_code: int = 200

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")

    @property
    def history(self) -> list[object]:
        return []


class _FixtureSession:
    def __init__(self, *, nonempty_sentinel: bool = False, mismatch_detail: bool = False):
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.closed = False
        self.nonempty_sentinel = nonempty_sentinel
        self.mismatch_detail = mismatch_detail
        self.cards = "".join(
            [
                _card(
                    "2026080100000001",
                    "남구 디지털 교육",
                    venue="오천읍 교육장",
                    end_date="2026-08-31",
                ),
                _card(
                    "2026080100000002",
                    "북구 디지털 교육",
                    venue="흥해읍 교육장",
                    end_date="2026-09-10",
                    status="접수중",
                ),
                _card(
                    "2026010100000003",
                    "지난 교육",
                    venue="시민정보화교육장",
                    end_date="2026-08-04",
                ),
                _card(
                    "2026080100000004",
                    "공지 교육장 휴관",
                    venue="시민정보화교육장",
                    end_date="2026-08-31",
                ),
            ]
        )

    def post(self, url, *, data, **_kwargs):
        body = {str(key): str(value) for key, value in data.items()}
        self.calls.append((url, body))
        path = urlparse(url).path
        if path.endswith("lectureInfoList.do"):
            page = int(body["page"])
            cards = self.cards if page == 1 else (
                _card(
                    "2026999900000099",
                    "센티널 오염",
                    venue="시민정보화교육장",
                    end_date="2026-12-31",
                )
                if self.nonempty_sentinel
                else ""
            )
            return _Response(url, _list_page(page, cards))
        if path.endswith("lectureInfoView.do"):
            identity = body["idx"]
            values = {
                "2026080100000001": ("남구 디지털 교육", "오천읍 교육장", "2026-08-31"),
                "2026080100000002": ("북구 디지털 교육", "흥해읍 교육장", "2026-09-10"),
            }
            title, venue, end_date = values[identity]
            if self.mismatch_detail and identity.endswith("1"):
                title = "다른 상세 제목"
            return _Response(
                url,
                _detail(
                    identity,
                    title,
                    venue=venue,
                    end_date=end_date,
                    open_for_application=identity.endswith("2"),
                ),
            )
        raise AssertionError(f"unsafe endpoint called: {url}")

    def close(self):
        self.closed = True


def test_exact_target_contract_rejects_aliases() -> None:
    assert pohang.is_pohang_integrated_education_target(_target())
    assert not pohang.is_pohang_integrated_education_target(
        _target(provider="MUNI_OTHER")
    )
    assert not pohang.is_pohang_integrated_education_target(
        _target(url="https://mbis.pohang.go.kr/apply/main.do")
    )


def test_complete_snapshot_reconciles_zero_sentinel_and_current_details() -> None:
    session = _FixtureSession()

    rows, parser, meta = pohang.collect(
        _target(),
        today="2026-08-05",
        session_factory=lambda: session,
    )

    assert parser == pohang.POHANG_INTEGRATED_PARSER
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["source_total"] == 4
    assert meta["source_rows"] == 4
    assert meta["data_pages"] == 1
    assert meta["sentinel_page"] == 2
    assert meta["stable_first_page"] is True
    assert meta["stable_last_page"] is True
    assert meta["current_count"] == 2
    assert meta["explicit_non_program_count"] == 1
    assert meta["returned_count"] == 2
    assert meta["detail_pages"] == 2
    assert meta["application_endpoint_requests"] == 0
    assert len(rows) == 2
    by_title = {row["title"]: row for row in rows}
    assert by_title["남구 디지털 교육"]["municipality_code"] == pohang.POHANG_NAMGU_CODE
    assert by_title["북구 디지털 교육"]["municipality_code"] == pohang.POHANG_BUKGU_CODE
    assert by_title["남구 디지털 교육"]["service_group"] == "공공강좌"
    assert by_title["남구 디지털 교육"]["program_type"] == "교육"
    assert by_title["북구 디지털 교육"]["reservation_available"] is True
    assert by_title["북구 디지털 교육"]["application_url"] == pohang.POHANG_INTEGRATED_LIST_URL
    assert all("010-1234-5678" not in str(row) for row in rows)
    assert all(
        pohang.POHANG_INTEGRATED_APPLICATION_PATH not in url
        for url, _body in session.calls
    )
    assert session.closed is True


def test_nonempty_post_last_page_fails_closed() -> None:
    rows, _, meta = pohang.collect(
        _target(),
        today="2026-08-05",
        session_factory=lambda: _FixtureSession(nonempty_sentinel=True),
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"]


def test_detail_identity_mismatch_fails_closed_without_partial_rows() -> None:
    rows, _, meta = pohang.collect(
        _target(),
        today="2026-08-05",
        session_factory=lambda: _FixtureSession(mismatch_detail=True),
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "title mismatch" in meta["configured_collection_error"]


def test_caps_fail_before_partial_snapshot_is_returned() -> None:
    rows, _, meta = pohang.collect(
        _target(),
        today="2026-08-05",
        max_pages=1,
        detail_limit=1,
        session_factory=lambda: _FixtureSession(),
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "cap" in meta["configured_collection_error"]


def test_raw_requests_require_explicit_test_opt_in() -> None:
    rows, _, meta = pohang.collect(_target())

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "session_factory" in meta["configured_collection_error"]


def test_dispatch_injects_managed_session_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any) -> tuple[list[Any], str, dict[str, Any]]:
        captured.update(kwargs)
        return [], pohang.POHANG_INTEGRATED_PARSER, {"snapshot_complete": True}

    monkeypatch.setattr(pohang, "collect_pohang_integrated_education", collect)
    target = municipal_runner.CrawlTarget(
        provider=pohang.POHANG_INTEGRATED_PROVIDER,
        name="포항시 통합예약 시민정보화교육",
        branch="포항시 시민정보화교육",
        url=pohang.POHANG_INTEGRATED_LIST_URL,
        source="test",
    )
    municipal_runner.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=100,
        detail_limit=300,
    )

    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])


def test_target_and_production_configs_register_exact_official_owner() -> None:
    target_document = yaml.safe_load(
        Path("config/crawl_targets/municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    targets = [
        row
        for row in target_document["targets"]
        if row.get("provider") == pohang.POHANG_INTEGRATED_PROVIDER
    ]
    assert len(targets) == 1
    target = targets[0]
    assert target["url"] == pohang.POHANG_INTEGRATED_LIST_URL
    assert target["crawler_module"] == "Crawler.municipal_pohang_integrated"
    assert target["crawler_callable"] == "collect_pohang_integrated_education"
    assert target["ops_scopes"] == ["education"]
    assert target["covered_municipalities"] == [
        dict(row) for row in pohang.POHANG_COVERED_MUNICIPALITIES
    ]
    assert target["last_quality"]["source_total"] == 271
    assert target["last_quality"]["current_count"] == 3
    assert target["last_quality"]["snapshot_complete"] is True

    production = yaml.safe_load(
        Path("config/production_crawler_providers.yaml").read_text(encoding="utf-8")
    )
    assert production["providers"].count(pohang.POHANG_INTEGRATED_PROVIDER) == 1
    for path in (
        Path("deploy/ubuntu/mooncen.env.example"),
        Path("deploy/ubuntu/setup_project.sh"),
    ):
        text = path.read_text(encoding="utf-8")
        assert pohang.POHANG_INTEGRATED_PROVIDER in text
