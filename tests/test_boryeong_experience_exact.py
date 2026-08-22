from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_boryeong_experience as collector


PROGRAMS = (
    {
        "identity": "138",
        "category": "유아체험",
        "title": "[유아체험] 간단한 소품을 선택하여 만들기",
        "operation": "동절기 09:00~17:00 / 하절기 09:00~18:00",
        "time": "화~금 10:00~12:00",
        "fee": "체험료 무료 / 재료비 별도",
        "capacity": "가족 4팀 이내 / 회",
        "intro": "간단한 소품 만들기를 통해 나무라는 소재를 체험합니다.",
        "venue": "보령목재문화체험장 2층 유아체험실 (보령무궁화수목원 내 위치)",
    },
    {
        "identity": "557",
        "category": "일반체험",
        "title": "[일반체험] 화요일 목공 체험",
        "operation": "화요일 오전 또는 오후",
        "time": "오전 10:00~12:00 / 오후 14:00~16:00",
        "fee": "체험료 2,000원 / 재료비 별도",
        "capacity": "4명 이내 / 회",
        "intro": "나무와 공구를 함께 사용하여 생활소품을 직접 제작하는 체험입니다.",
        "venue": "보령목재문화체험장 1층 일반체험실 (보령무궁화수목원 내 위치)",
    },
    {
        "identity": "164",
        "category": "심화체험",
        "title": "[심화체험] 원목트레이 만들기",
        "operation": "짝수 토요일",
        "time": "14:00~16:00",
        "fee": "체험료 3,000원 / 재료비 별도",
        "capacity": "4명 이내 / 회",
        "intro": "수공구 및 목공기계를 다루어 생활소품을 제작하는 체험입니다.",
        "venue": "보령목재문화체험장 1층 심화체험실 (보령무궁화수목원 내 위치)",
    },
    {
        "identity": "305",
        "category": "기타",
        "title": "CNC 코딩목공 체험",
        "operation": "수, 목",
        "time": "09:30~12:00",
        "duration": "150분",
        "fee": "체험료 3,000원 / 재료비 별도",
        "capacity": "8명 이내 / 회",
        "intro": "CNC기계와 코딩을 접목한 목공 체험 프로그램입니다.",
        "venue": "보령목재문화체험장 1층 일반체험실 (보령무궁화수목원 내 위치)",
    },
)


@dataclass
class _Response:
    url: str
    content: bytes
    status_code: int = 200
    location: str = ""

    @property
    def headers(self) -> dict[str, str]:
        result = {"content-type": "text/html; charset=UTF-8"}
        if self.location:
            result["location"] = self.location
        return result

    @property
    def history(self) -> tuple[Any, ...]:
        return ()


class _Session:
    def __init__(self) -> None:
        self.closed = False
        self.post_calls = 0

    def post(self, *_args: Any, **_kwargs: Any) -> None:
        self.post_calls += 1
        raise AssertionError("POST must never be called")

    def close(self) -> None:
        self.closed = True


def _target() -> dict[str, str]:
    return {
        "provider": collector.BORYEONG_EXPERIENCE_PROVIDER,
        "url": collector.BORYEONG_EXPERIENCE_URL,
    }


def _page_header() -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>K-GUIDE</title></head><body>"
        '<h1 onclick="javascript:home()">보령목재문화체험장 예약시스템</h1>'
    )


def _list_html(
    *,
    declared_delta: int = 0,
    reverse_registry: bool = False,
    title_suffix: str = "",
    pii_fee: bool = False,
) -> bytes:
    cards: list[str] = []
    for position, item in enumerate(PROGRAMS):
        schedule = item["time"]
        if item.get("duration"):
            schedule += f" ({item['duration']})"
        fee = "문의 041-930-4099" if pii_fee and position == 0 else item["fee"]
        title = item["title"] + (title_suffix if position == 0 else "")
        cards.append(
            f"""
            <li><a href="javascript:go('{item["identity"]}', '{position}')">
              <div class="thum_img"><span class="sort type4">{item["category"]}</span></div>
              <div class="pg_info">
                <div class="pg_tit">{title}</div><span class="status type1">예약중</span>
                <ul><li>{item["operation"]}</li><li>{schedule}</li><li>{fee}</li></ul>
              </div>
            </a></li>
            """
        )
    identities = [item["identity"] for item in PROGRAMS]
    if reverse_registry:
        identities.reverse()
    return (
        _page_header()
        + f'<div class="num">{len(PROGRAMS) + declared_delta} Listed</div>'
        + '<div class="notice">공지사항 및 이용안내 shell</div>'
        + f'<div class="pg_list"><ul>{"".join(cards)}</ul></div>'
        + f"<script>var g_magic = '{','.join(identities)}';</script>"
        + "</body></html>"
    ).encode("utf-8")


def _detail_html(
    *,
    missing_slide: bool = False,
    mismatched_fee: bool = False,
    conflicting_venue: bool = False,
) -> bytes:
    slides: list[str] = []
    selected = PROGRAMS[:-1] if missing_slide else PROGRAMS
    for position, item in enumerate(selected):
        duration = f"<tr><th>소요시간</th><td>{item['duration']}</td></tr>" if item.get("duration") else ""
        fee = "다른 요금" if mismatched_fee and position == 0 else item["fee"]
        venue = "충청남도 보령시 성주산로 318-59" if conflicting_venue and position == 0 else item["venue"]
        slides.append(
            f"""
            <div class="swiper-slide">
              <div class="program_detail"><table>
                <tr><th>운영기간</th><td>{item["operation"]}</td></tr>
                <tr><th>이용시간</th><td>{item["time"]}</td></tr>
                <tr><th>이용정원</th><td>{item["capacity"]}</td></tr>
                {duration}
                <tr><th>이용요금</th><td>{fee}</td></tr>
              </table></div>
              <div class="program_info">
                <p class="tit_h3">개요</p><div class="con_text">1. 시설 운영 정보 2. 프로그램 소개 {item["intro"]} 3. 체험 품목</div>
                <p class="tit_h3">이용 시 주의사항</p><div class="con_text">모든 프로그램은 100% 예약제로 진행됩니다.</div>
                <p class="tit_h3">예약 시 주의사항</p><div class="con_text"></div>
                <p class="tit_h3">모바일 티켓</p><div class="con_text">현장 검표 안내</div>
                <p class="tit_h3">환불정책</p><div class="con_text">공개 환불 기준</div>
                <p class="tit_h3">이용장소</p><div class="con_text">{venue}</div>
              </div>
            </div>
            """
        )
    return (_page_header() + "".join(slides) + "</body></html>").encode("utf-8")


class _Backend:
    def __init__(self, **flags: Any) -> None:
        self.flags = flags
        self.calls: list[str] = []
        self.list_calls = 0
        self.session = _Session()

    def fetch(self, _session: Any, url: str, _timeout: int) -> _Response:
        self.calls.append(url)
        parsed = urlparse(url)
        if parsed.path == collector.BORYEONG_EXPERIENCE_LIST_PATH:
            self.list_calls += 1
            suffix = " 변경" if self.flags.get("unstable") and self.list_calls == 2 else ""
            return _Response(
                url,
                _list_html(
                    declared_delta=1 if self.flags.get("bad_total") else 0,
                    reverse_registry=bool(self.flags.get("bad_registry")),
                    title_suffix=suffix,
                    pii_fee=bool(self.flags.get("pii_fee")),
                ),
            )
        assert parsed.path == collector.BORYEONG_EXPERIENCE_DETAIL_PATH
        query = parse_qs(parsed.query)
        expected_registry = ",".join(item["identity"] for item in PROGRAMS)
        assert query["ids"] == [expected_registry]
        return _Response(
            url,
            _detail_html(
                missing_slide=bool(self.flags.get("missing_slide")),
                mismatched_fee=bool(self.flags.get("mismatched_fee")),
                conflicting_venue=bool(self.flags.get("conflicting_venue")),
            ),
        )


def _collect(**flags: Any):
    backend = _Backend(**flags)
    rows, parser, meta = collector.collect_boryeong_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=2,
        detail_limit=int(flags.get("detail_limit", 10)),
        session_factory=lambda: backend.session,
        fetcher=backend.fetch,
    )
    return rows, parser, meta, backend


def test_provider_candidate_target_and_get_allowlist_are_exact() -> None:
    url = collector.BORYEONG_EXPERIENCE_PROVENANCE_URL
    assert collector.BORYEONG_EXPERIENCE_PROVIDER == (
        "MUNI_WWW_BRCN_GO_KR_" + hashlib.sha1(url.encode()).hexdigest()[:8].upper()
    )
    assert collector.BORYEONG_EXPERIENCE_CANDIDATE_ID == (
        "MUNI_IR_" + hashlib.sha256(url.encode()).hexdigest()[:12].upper()
    )
    assert collector.is_boryeong_experience_target(_target())
    assert not collector.is_boryeong_experience_target({**_target(), "url": url + "?x=1"})
    assert collector._request_kind(collector.boryeong_experience_list_url()) == "list"
    registry = tuple(item["identity"] for item in PROGRAMS)
    assert collector._request_kind(collector.boryeong_experience_detail_url(registry)) == "detail"
    for unsafe in (
        url,
        "https://www.kguide.kr/Web/Book/GetBookPlayDate.json",
        "https://www.kguide.kr/Web/Book/GetBookPlaySequence.json",
        "https://www.kguide.kr/svc/login",
        "https://www.kguide.kr/svc/apply",
        "https://www.kguide.kr/svc/detail?idx=138&ids=138&page=0&root=wrong",
        "https://www.kguide.kr/svc/detail?idx=138&ids=138&page=0&root=brwoodcec&download=1",
    ):
        with pytest.raises(collector.BoryeongExperienceContractError):
            collector._request_kind(unsafe)


def test_complete_registry_fixture_is_safe_and_excludes_shells() -> None:
    rows, parser, meta, backend = _collect()

    assert parser == collector.BORYEONG_EXPERIENCE_PARSER
    assert len(rows) == len(PROGRAMS) == 4
    assert meta["declared_total"] == meta["source_total"] == 4
    assert meta["unique_identity_count"] == meta["registry_count"] == 4
    assert meta["detail_slide_count"] == meta["detail_verified"] == 4
    assert meta["logical_requests"] == 3
    assert meta["list_requests"] == 2 and meta["detail_requests"] == 1
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert meta["category_counts"] == {
        "기타": 1,
        "심화체험": 1,
        "유아체험": 1,
        "일반체험": 1,
    }
    assert backend.session.closed is True and backend.session.post_calls == 0
    assert [urlparse(url).path for url in backend.calls] == [
        collector.BORYEONG_EXPERIENCE_LIST_PATH,
        collector.BORYEONG_EXPERIENCE_DETAIL_PATH,
        collector.BORYEONG_EXPERIENCE_LIST_PATH,
    ]
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
    assert all(row["address"] == "" and row["venue_name"] for row in rows)
    assert all("source_status" in row["raw_fields"] for row in rows)
    assert all("rounds" not in row and "remaining" not in row for row in rows)
    for key in (
        "official_provenance_requests",
        "calendar_endpoint_requests",
        "application_endpoint_requests",
        "login_endpoint_requests",
        "member_endpoint_requests",
        "applicant_endpoint_requests",
        "identity_endpoint_requests",
        "file_endpoint_requests",
        "attachment_endpoint_requests",
        "download_endpoint_requests",
        "pii_endpoint_requests",
    ):
        assert meta[key] == 0


@pytest.mark.parametrize(
    ("flag", "message"),
    (
        ("bad_total", "declared total"),
        ("bad_registry", "g_magic registry differ"),
        ("missing_slide", "detail slide count differ"),
        ("mismatched_fee", "identity drift"),
        ("conflicting_venue", "venue contract changed"),
        ("unstable", "stable recheck"),
        ("pii_fee", "PII entered safe"),
    ),
)
def test_registry_detail_stability_and_privacy_drift_fail_atomically(flag: str, message: str) -> None:
    rows, _, meta, backend = _collect(**{flag: True})
    assert rows == [] and meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]
    assert backend.session.closed is True


def test_detail_cap_is_atomic() -> None:
    rows, _, meta, backend = _collect(detail_limit=3)
    assert rows == [] and meta["source_cap_reached"] is True
    assert "detail_limit 3 truncates required registry 4" in meta["configured_collection_error"]
    assert len(backend.calls) == 1


def test_router_dispatches_exact_target_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_collect(target: Any, **kwargs: Any):
        calls.append((target, kwargs))
        return [{"provider": collector.BORYEONG_EXPERIENCE_PROVIDER}], "fixture", {"ok": True}

    monkeypatch.setattr(collector, "collect_boryeong_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.BORYEONG_EXPERIENCE_PROVIDER,
        name="보령목재문화체험장 현재·상시 목공 체험",
        branch="보령목재문화체험장",
        url=collector.BORYEONG_EXPERIENCE_URL,
        source="test",
        priority=1,
        region="충청남도 보령시",
        extra={},
    )
    rows, parser, meta = router.collect_from_url(target, timeout=3, max_depth=0, max_pages=2, detail_limit=30)
    assert rows and parser == "fixture" and meta == {"ok": True}
    assert len(calls) == 1


def test_single_public_target_and_operational_entry() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = yaml.safe_load((root / "config/crawl_targets/public_reservation.yaml").read_text(encoding="utf-8"))
    matches = [item for item in targets["targets"] if item.get("provider") == collector.BORYEONG_EXPERIENCE_PROVIDER]
    assert len(matches) == 1
    assert matches[0]["url"] == collector.BORYEONG_EXPERIENCE_PROVENANCE_URL
    assert matches[0]["public_data_url"] == collector.boryeong_experience_list_url()
    assert matches[0]["crawler_module"] == "Crawler.municipal_boryeong_experience"
    assert matches[0]["service_group"] == "체험"
    operational = yaml.safe_load(
        (root / "config/municipal_integrated_reservation_operational.yaml").read_text(encoding="utf-8")
    )
    entries = [
        item for item in operational["entries"] if item.get("provider") == collector.BORYEONG_EXPERIENCE_PROVIDER
    ]
    assert len(entries) == 1 and entries[0]["row_count"] == 23


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_BORYEONG_EXPERIENCE") != "1",
    reason="set RUN_LIVE_BORYEONG_EXPERIENCE=1 for the public GET-only contract",
)
def test_live_exact_23_identity_registry() -> None:
    rows, _, meta = collector.collect_boryeong_experience(_target(), today="2026-08-05", timeout=30, detail_limit=30)
    assert len(rows) == 23 and meta["snapshot_complete"] is True
    assert meta["declared_total"] == meta["registry_count"] == 23
    assert meta["detail_slide_count"] == 23
    assert meta["identity_sha256"] == ("1798bf2a9faf7131e7f37f0f58b2259f5368ff4435aba232790280e24b9c6e1d")
