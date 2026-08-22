from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import municipal_gunsan
from Crawler import municipal_gunsan_future_experience as gunsan
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


ROOT = Path(__file__).resolve().parents[1]


def _target(**updates: str) -> dict[str, str]:
    target = {
        "provider": gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER,
        "url": gunsan.GUNSAN_FUTURE_EXPERIENCE_URL,
    }
    target.update(updates)
    return target


def _dates() -> list[date]:
    past = [date(2026, 4, 1) + timedelta(days=3 * index) for index in range(29)]
    future = [
        date(2026, 8, 26) + timedelta(days=3 * index) for index in range(28)
    ] + [date(2026, 12, 11)]
    return past + future


def _slot_rows(*, sequence_drift: bool = False, status_drift: bool = False) -> str:
    rows: list[str] = []
    open_sequences = set(range(1, 11)) | set(range(117, 130))
    sequence = 0
    for service_date in _dates():
        for class_number in range(1, 5):
            sequence += 1
            if sequence_drift and sequence == 116:
                continue
            is_open = sequence in open_sequences
            if status_drift and sequence == 117:
                is_open = not is_open
            capacity = "0 / 1" if is_open else "1 / 1"
            control = ""
            if is_open:
                control = (
                    '<a href="contents.htm?code=3_1_1&amp;oidx=1&amp;'
                    f'pidx={sequence}&amp;sdate={service_date.isoformat()}&amp;'
                    f'stime={class_number}반">접수중</a>'
                )
            else:
                control = "<span>접수마감</span>"
            rows.append(
                "<tr>"
                f"<td>{sequence}</td>"
                f"<td>{service_date.isoformat()} [{class_number}반]</td>"
                f"<td>{capacity}</td>"
                f"<td>{control}</td>"
                "</tr>"
            )
    return "".join(rows)


def _page(*, sequence_drift: bool = False, status_drift: bool = False) -> str:
    siblings = "".join(
        f'<a href="contents.htm?code={code}">{label}</a>'
        for code, label in (
            ("3_2", "초·중등 미래교실"),
            ("3_3", "상시프로그램"),
            ("3_4", "지역사회연계"),
            ("3_5", "환경동아리"),
            ("3_6", "기후탐험대"),
            ("3_7", "생태배움터"),
            ("3_8", "2026 교원연수"),
        )
    )
    return f"""
      <html><head><title>금강미래체험관 &gt; 체험프로그램 &gt; 유·초등 프로그램</title></head>
      <body><nav>{siblings}</nav><main>
        <h1>유·초등 프로그램</h1><h2>자연친구, 건강학교</h2>
        <dl>
          <dt>대상</dt><dd>유치(만 3세이상), 초등</dd>
          <dt>장소</dt><dd>금강미래체험관</dd>
          <dt>일정</dt><dd>4 ~ 11월 매주 수, 금 10:30 ~ 12:00</dd>
          <dt>모집</dt><dd>홈페이지 접수</dd>
          <dt>교육내용</dt><dd>기후·건강 연계 체험 중심 환경교육 프로그램</dd>
        </dl>
        <h3>프로그램신청 <a href="/contents.htm?code=3_1_3">접수확인</a></h3>
        <table><thead><tr><th>회차</th><th>날짜</th><th>교육신청</th><th></th></tr></thead>
          <tbody>{_slot_rows(sequence_drift=sequence_drift, status_drift=status_drift)}</tbody>
        </table>
      </main></body></html>
    """


class _Response:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = 200
        self.history: list[Any] = []
        self.headers = {"Content-Type": "text/html; charset=utf-8"}


class _FixtureSession:
    def __init__(
        self,
        *,
        sequence_drift: bool = False,
        unstable_second: bool = False,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.sequence_drift = sequence_drift
        self.unstable_second = unstable_second
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        assert url == gunsan.GUNSAN_FUTURE_EXPERIENCE_URL
        return _Response(
            url,
            _page(
                sequence_drift=self.sequence_drift,
                status_drift=self.unstable_second and len(self.calls) > 1,
            ),
        )

    def close(self) -> None:
        self.closed = True


def _collect(session: _FixtureSession, **kwargs: Any):
    return gunsan.collect(
        _target(),
        today="2026-08-05",
        max_pages=4,
        detail_limit=300,
        timeout=10,
        session_factory=lambda: session,
        **kwargs,
    )


def test_exact_target_and_repository_ids_are_disjoint_from_education() -> None:
    assert gunsan.is_target(_target())
    assert not gunsan.is_target(
        _target(url=gunsan.GUNSAN_FUTURE_EXPERIENCE_URL + "#fragment")
    )
    assert not gunsan.is_target(
        _target(url=gunsan.GUNSAN_FUTURE_EXPERIENCE_URL.replace("https://", "http://"))
    )
    assert gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER == stable_provider(
        gunsan.GUNSAN_FUTURE_EXPERIENCE_URL
    )
    assert gunsan.GUNSAN_FUTURE_EXPERIENCE_CANDIDATE_ID == candidate_id(
        normalized_duplicate_url(gunsan.GUNSAN_FUTURE_EXPERIENCE_URL)
    )
    assert gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER != municipal_gunsan.GUNSAN_PROVIDER
    assert not municipal_gunsan.is_gunsan_education_target(_target())


def test_production_collection_requires_managed_session() -> None:
    rows, parser, meta = gunsan.collect(_target(), today="2026-08-05")
    assert rows == []
    assert parser == gunsan.GUNSAN_FUTURE_EXPERIENCE_PARSER
    assert meta["snapshot_complete"] is False
    assert "session_factory" in meta["configured_collection_error"]


class _NeverSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> Any:
        self.calls.append(url)
        raise AssertionError("network must not be reached")

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    "url",
    (
        "https://green.gunsan.go.kr/contents.htm?code=3_1_1&oidx=1&pidx=135&sdate=2026-09-09&stime=3반",
        "https://green.gunsan.go.kr/contents.htm?code=3_1_3",
        "https://green.gunsan.go.kr/login.htm",
        "https://green.gunsan.go.kr/member/applicant.do",
        "https://green.gunsan.go.kr/download.do?file=secret",
        gunsan.GUNSAN_FUTURE_EXPERIENCE_URL + "#fragment",
    ),
)
def test_runner_refuses_every_noncanonical_endpoint_before_network(url: str) -> None:
    session = _NeverSession()
    with gunsan._Runner(lambda: session, 10) as runner:
        with pytest.raises(
            gunsan.GunsanFutureExperienceContractError,
            match="endpoint refused",
        ):
            runner.soup(url)
    assert session.calls == []


def test_fixture_proves_complete_current_snapshot_and_safe_boundary() -> None:
    session = _FixtureSession()
    rows, parser, meta = _collect(session)

    assert parser == gunsan.GUNSAN_FUTURE_EXPERIENCE_PARSER
    assert len(rows) == 116
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 232
    assert meta["source_rows"] == 232
    assert meta["unique_identity_count"] == 232
    assert meta["source_identity_continuous"] is True
    assert meta["source_first_date"] == "2026-04-01"
    assert meta["source_last_date"] == "2026-12-11"
    assert meta["current_count"] == 116
    assert meta["expired_count"] == 116
    assert meta["status_counts"] == {"OPEN": 13, "CLOSED": 103}
    assert meta["source_status_counts"] == {"OPEN": 23, "CLOSED": 209}
    assert meta["application_controls_total"] == 23
    assert meta["application_controls_current"] == 13
    assert meta["stable_double_fetch"] is True
    assert meta["list_requests"] == 2
    assert meta["physical_requests"] == 2
    assert meta["pagination_detected"] is False
    assert meta["sentinel_not_applicable"] is True
    assert meta["sentinel_verified"] is True
    assert meta["sibling_excluded_count"] == 367
    assert meta["sibling_pages_requested"] == 0
    assert meta["application_endpoints_called"] == 0
    assert meta["receipt_endpoints_called"] == 0
    assert meta["login_member_pii_endpoints_called"] == 0
    assert meta["attachment_download_endpoints_called"] == 0
    assert meta["post_requests"] == 0
    assert session.closed is True
    assert [url for url, _ in session.calls] == [
        gunsan.GUNSAN_FUTURE_EXPERIENCE_URL,
        gunsan.GUNSAN_FUTURE_EXPERIENCE_URL,
    ]

    assert rows[0]["title"] == "자연친구, 건강학교 2026-08-26 1반"
    assert rows[-1]["title"] == "자연친구, 건강학교 2026-12-11 4반"
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["classification_locked"] is True for row in rows)
    assert all(row["raw_url"] == gunsan.GUNSAN_FUTURE_EXPERIENCE_URL for row in rows)
    assert all("3_1_1" not in row["application_url"] for row in rows)
    assert all("3_1_3" not in row["application_url"] for row in rows)


@pytest.mark.parametrize(
    ("session", "message"),
    (
        (_FixtureSession(sequence_drift=True), "not exactly continuous"),
        (_FixtureSession(unstable_second=True), "changed on recheck"),
    ),
)
def test_contract_drift_fails_the_atomic_snapshot(
    session: _FixtureSession, message: str
) -> None:
    rows, _, meta = _collect(session)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_dispatch_injects_managed_session_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as router

    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any) -> tuple[list[Any], str, dict[str, Any]]:
        captured.update(kwargs)
        return [], "gunsan-future-experience", {"snapshot_complete": True}

    monkeypatch.setattr(gunsan, "collect_gunsan_future_experience", collect)
    target = router.CrawlTarget(
        provider=gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER,
        name="군산시 금강미래체험관",
        branch=gunsan.GUNSAN_FUTURE_EXPERIENCE_BRANCH,
        url=gunsan.GUNSAN_FUTURE_EXPERIENCE_URL,
        source="test",
    )
    router.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=4,
        detail_limit=300,
    )

    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "allow_raw_requests_for_tests" not in captured


def test_single_target_operational_coverage_and_education_owner_linkage() -> None:
    target_path = ROOT / "config/crawl_targets/municipal_integrated_reservation.yaml"
    targets = yaml.safe_load(target_path.read_text(encoding="utf-8"))["targets"]
    matches = [
        item
        for item in targets
        if item.get("provider") == gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    target = matches[0]
    assert target["url"] == gunsan.GUNSAN_FUTURE_EXPERIENCE_URL
    assert target["crawler_module"] == "Crawler.municipal_gunsan_future_experience"
    assert target["crawler_callable"] == "collect_gunsan_future_experience"
    assert target["service_group"] == "체험"
    assert target["ops_scopes"] == ["experience"]
    assert target["full_snapshot_required"] is True
    assert target["last_quality"]["collected"] == 116

    all_targets = []
    for path in (ROOT / "config/crawl_targets").glob("*.yaml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        all_targets.extend(document.get("targets", []) if isinstance(document, dict) else [])
    assert sum(
        item.get("provider") == gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER
        and item.get("crawler_status") in {"ready", "no_current_data", "partial"}
        for item in all_targets
    ) == 1
    education = next(
        item
        for item in all_targets
        if item.get("provider") == municipal_gunsan.GUNSAN_PROVIDER
    )
    assert education["url"] == municipal_gunsan.GUNSAN_CANONICAL_URL
    assert education["service_group"] == "공공강좌"

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    entries = [
        item
        for item in operational
        if item.get("provider") == gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER
    ]
    assert len(entries) == 1
    assert entries[0]["target_url"] == gunsan.GUNSAN_FUTURE_EXPERIENCE_URL
    assert entries[0]["validation_outcome"] == "collected"
    assert entries[0]["row_count"] == 116

    coverage = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )
    municipality = next(
        item
        for item in coverage["municipalities"]
        if item.get("code") == gunsan.GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_CODE
    )
    assert gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER in municipality["owner_providers"]
    assert gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER in municipality["promoted_providers"]
    assert gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER in municipality["yaml_owner_providers"]
    assert municipal_gunsan.GUNSAN_PROVIDER in municipality["owner_providers"]
    assert any(
        evidence.get("provider") == gunsan.GUNSAN_FUTURE_EXPERIENCE_PROVIDER
        and evidence.get("target_url") == gunsan.GUNSAN_FUTURE_EXPERIENCE_URL
        and evidence.get("row_count") == 116
        for evidence in municipality["evidence"]
    )


def test_live_baseline_and_sibling_exclusion_contract_are_explicit() -> None:
    assert gunsan.GUNSAN_FUTURE_EXPERIENCE_LIVE_BASELINE == {
        "checked_at": "2026-08-05",
        "source_total": 232,
        "source_first_date": "2026-04-01",
        "source_last_date": "2026-12-11",
        "current_count": 116,
        "expired_count": 116,
        "open_current_count": 13,
        "closed_current_count": 103,
        "application_controls_total": 23,
        "sibling_excluded_count": 367,
    }
    assert sum(
        int(value["count"])
        for value in gunsan.GUNSAN_FUTURE_EXPERIENCE_SIBLING_EXCLUSIONS.values()
    ) == 367
    assert set(gunsan.GUNSAN_FUTURE_EXPERIENCE_SIBLING_EXCLUSIONS) == {
        "3_2",
        "3_4",
        "3_5",
        "3_6",
        "3_7",
        "3_8",
    }


def test_application_controls_are_inert_identity_bound_links() -> None:
    session = _FixtureSession()
    rows, _, meta = _collect(session)
    assert meta["snapshot_complete"] is True
    assert len(rows) == 116
    assert len(session.calls) == 2
    for url, _ in session.calls:
        parsed = urlparse(url)
        assert parsed.path == "/contents.htm"
        assert parse_qs(parsed.query) == {"code": ["3_1"]}
