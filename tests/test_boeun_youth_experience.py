from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_boeun_youth_experience as collector


PROGRAMS = (
    {
        "programNo": 101,
        "programTitle": "[꿈.찾.주] 로봇 엔지니어 A",
        "categoryNo": 54,
        "categoryName": "특기적성 프로그램",
        "typeNo": 24,
        "typeName": "청소년교육사업",
        "location": "보은군청소년센터 2층 프로그램실2",
        "applyStatus": "applyStatusAccepting",
        "applyPeriod": "7.14.(화) 18:00 ~",
        "programPeriod": "당일형 1회",
        "applyWay": "Play.Pass를 이용한 프로그램 사전신청",
        "openYn": "Y",
        "programFee": "무료",
        "quota": "10명",
    },
    {
        "programNo": 102,
        "programTitle": "팝업부스 D (자개함 만들기)",
        "categoryNo": 9,
        "categoryName": "자율 이용 활성화",
        "typeNo": 26,
        "typeName": "청소년특화사업",
        "location": "보은군청소년센터 1층 프로그램실1",
        "applyStatus": "applyStatusWait",
        "applyPeriod": "사전 접수 없음 (현장 신청)",
        "programPeriod": "8.11.(화)~8.14.(금), 당일형 1회",
        "applyWay": "현장 신청",
        "openYn": "Y",
        "programFee": "무료",
        "quota": "70명",
    },
    {
        "programNo": 103,
        "programTitle": "[하반기] 드론",
        "categoryNo": 17,
        "categoryName": "정규강좌형 특기적성 프로그램",
        "typeNo": 24,
        "typeName": "청소년교육사업",
        "location": "보은군청소년센터 2층 다목적강당",
        "applyStatus": "applyStatusClosed",
        "applyPeriod": "7.16.(목) 18:00 ~ 7.24.(금) 17:00",
        "programPeriod": "8.25.(화) ~ 12.4.(금) / 12회기 운영",
        "applyWay": "Play.Pass를 이용한 프로그램 사전신청",
        "openYn": "Y",
        "programFee": "무료",
        "quota": "12명",
    },
    {
        "programNo": 104,
        "programTitle": "[하반기] 창의미술",
        "categoryNo": 14,
        "categoryName": "하반기 정규프로그램",
        "typeNo": 24,
        "typeName": "청소년교육사업",
        "location": "보은군청소년센터 2층 프로그램실3",
        "applyStatus": "applyStatusClosed",
        "applyPeriod": "7.16.(목) 18:00 ~ 7.24.(금) 17:00",
        "programPeriod": "8.25.(화) ~ 12.4.(금) / 12회기 운영",
        "applyWay": "Play.Pass를 이용한 프로그램 사전신청",
        "openYn": "Y",
        "programFee": "무료",
        "quota": "12명",
    },
    {
        "programNo": 105,
        "programTitle": "어푸어푸",
        "categoryNo": 5,
        "categoryName": "청소년 축제",
        "typeNo": 25,
        "typeName": "청소년문화사업",
        "location": "보은군 뱃들공원 물놀이장",
        "applyStatus": "applyStatusWait",
        "applyPeriod": "현장 행사",
        "programPeriod": "당일형 1회 (축제)",
        "applyWay": "현장 자율참가",
        "openYn": "Y",
        "programFee": "무료",
        "quota": "400명",
    },
    {
        "programNo": 106,
        "programTitle": "도슨트 투어 프로그램 (in 제주)",
        "categoryNo": 39,
        "categoryName": "도슨트 투어 프로그램",
        "typeNo": 26,
        "typeName": "청소년특화사업",
        "location": "제주도 (본태박물관, 곶자왈도립공원)",
        "applyStatus": "applyStatusWait",
        "applyPeriod": "9.1.(화) 18:00 ~ 9.14.(월)",
        "programPeriod": "숙박형 1박 2일",
        "applyWay": "온라인 접수 (외부 폼)",
        "openYn": "Y",
        "programFee": "무료",
        "quota": "20명",
    },
)


@dataclass
class _Response:
    url: str
    value: Mapping[str, Any]
    status_code: int = 200

    @property
    def content(self) -> bytes:
        return json.dumps(self.value, ensure_ascii=False).encode("utf-8")

    @property
    def headers(self) -> dict[str, str]:
        return {"content-type": "application/json; charset=utf-8"}

    @property
    def history(self) -> tuple[Any, ...]:
        return ()


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target() -> dict[str, str]:
    return {"provider": collector.BOEUN_YOUTH_PROVIDER, "url": collector.BOEUN_YOUTH_URL}


def _round(program: Mapping[str, Any]) -> dict[str, Any]:
    identity = int(program["programNo"])
    use_date = "2026-11-17" if identity == 106 else "2026-08-13"
    status = str(program["applyStatus"])
    return {
        "roundNo": 9000 + identity,
        "roundId": 1,
        "programNo": identity,
        "roundTitle": str(program["programTitle"]),
        "useDate": f"{use_date}T00:00:00.000Z",
        "useStartTime": "13:30:00",
        "useEndTime": "17:30:00",
        "applyStartDate": "2026-07-14T00:00:00.000Z" if identity != 102 else None,
        "applyEndDate": "2026-08-12T00:00:00.000Z" if identity != 102 else None,
        "applyStatus": status,
        "openYn": "Y",
        "nextRoundList": [],
        "teacherList": [{"teacherName": "discarded public staff field"}],
    }


def _collect(
    *,
    sentinel_nonempty: bool = False,
    detail_mismatch: bool = False,
    unknown_classification: bool = False,
):
    calls: list[tuple[str, str, Optional[Mapping[str, Any]]]] = []
    session = _Session()

    def fetcher(
        _session: Any,
        method: str,
        url: str,
        payload: Optional[Mapping[str, Any]],
        _timeout: int,
    ) -> _Response:
        calls.append((method, url, payload))
        path = urlparse(url).path
        if path == collector.BOEUN_YOUTH_LIST_PATH:
            page = int(payload["pagination"]["currentPage"])
            selected = list(PROGRAMS) if page == 1 else []
            if page == 2 and sentinel_nonempty:
                selected = [dict(PROGRAMS[0])]
            value = {
                "resultCd": "S",
                "resultMsg": "ok",
                "data": selected,
                "pagination": {
                    "perPage": collector.BOEUN_YOUTH_PAGE_SIZE,
                    "currentPage": page,
                    "total": len(PROGRAMS),
                    "lastPage": 1,
                },
            }
            return _Response(url, value)
        if path == collector.BOEUN_YOUTH_ROUND_PATH:
            identity = int(payload["programNo"])
            program = next(row for row in PROGRAMS if row["programNo"] == identity)
            return _Response(url, {"resultCd": "S", "resultMsg": "ok", "data": [_round(program)]})
        identity = int(path.rsplit("/", 1)[-1])
        program = dict(next(row for row in PROGRAMS if row["programNo"] == identity))
        if detail_mismatch and identity == 101:
            program["programTitle"] = "changed"
        if unknown_classification and identity == 101:
            program["categoryNo"] = 999
        return _Response(url, {"resultCd": "S", "resultMsg": "ok", "data": program})

    rows, parser, meta = collector.collect_boeun_youth_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=5,
        detail_limit=10,
        session_factory=lambda: session,
        fetcher=fetcher,
    )
    return rows, parser, meta, calls, session


def test_exact_target_and_request_allowlist() -> None:
    assert collector.is_boeun_youth_experience_target(_target())
    assert not collector.is_boeun_youth_experience_target(
        {**_target(), "url": collector.BOEUN_YOUTH_URL + "?x=1"}
    )
    assert collector._request_kind(
        "POST",
        f"https://{collector.BOEUN_YOUTH_HOST}{collector.BOEUN_YOUTH_LIST_PATH}",
        collector._list_payload(1),
    ) == "list"
    assert collector._request_kind(
        "POST",
        f"https://{collector.BOEUN_YOUTH_HOST}{collector.BOEUN_YOUTH_ROUND_PATH}",
        {"programNo": 101},
    ) == "round"
    assert collector._request_kind("GET", collector.boeun_youth_detail_url(101), None) == "detail"
    for path in (
        "/nodeapi/web/program/apply",
        "/nodeapi/web/program/application",
        "/nodeapi/web/auth/login",
        "/nodeapi/web/member/1",
        "/nodeapi/web/program/101/question/schema",
        "/nodeapi/web/file/download",
    ):
        with pytest.raises(collector.BoeunYouthContractError):
            collector._request_kind("GET", f"https://{collector.BOEUN_YOUTH_HOST}{path}", None)


def test_complete_mixed_fixture_and_no_unsafe_calls() -> None:
    rows, parser, meta, calls, session = _collect()

    assert parser == collector.BOEUN_YOUTH_PARSER
    assert len(rows) == 4
    assert meta["source_total"] == meta["source_rows"] == 6
    assert meta["pages"] == 5
    assert meta["data_pages"] == 1
    assert meta["sentinel_pages"] == 1
    assert meta["stable_rechecks"] == 3
    assert meta["detail_pages"] == meta["round_pages"] == 6
    assert meta["experience_rows"] == meta["education_rows"] == 2
    assert meta["excluded_count"] == 2
    assert meta["excluded_reason_counts"] == {
        "offsite_jeju_venue_not_boeun_municipality": 1,
        "youth_festival_without_programme_application": 1,
    }
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert session.closed is True
    assert {row["service_family"] for row in rows} == {"education", "experience"}
    assert all(row["provider_course_id"].startswith(collector.BOEUN_YOUTH_PROVIDER + ":") for row in rows)
    assert all(bool(row["application_url"]) == row["reservation_available"] for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["municipality_code"] == "4372000000" for row in rows)
    assert "discarded public staff field" not in repr(rows)
    assert not any(
        marker in urlparse(url).path.lower()
        for _, url, _ in calls
        for marker in ("/apply", "/application", "/auth", "/member", "/question", "/file")
    )
    for key in (
        "application_endpoint_requests",
        "login_endpoint_requests",
        "auth_endpoint_requests",
        "member_endpoint_requests",
        "applicant_endpoint_requests",
        "question_endpoint_requests",
        "file_endpoint_requests",
        "attachment_endpoint_requests",
        "download_endpoint_requests",
        "pii_endpoint_requests",
    ):
        assert meta[key] == 0


def test_contract_drift_is_atomic() -> None:
    for kwargs, message in (
        ({"sentinel_nonempty": True}, "page row count differs"),
        ({"detail_mismatch": True}, "detail/list mismatch"),
        ({"unknown_classification": True}, "detail/list mismatch"),
    ):
        rows, _, meta, _, session = _collect(**kwargs)
        assert rows == []
        assert message in meta["configured_collection_error"]
        assert meta["snapshot_complete"] is False
        assert session.closed is True


def test_router_dispatches_exact_target_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_collect(target: Any, **kwargs: Any):
        calls.append((target, kwargs))
        return [{"provider": collector.BOEUN_YOUTH_PROVIDER}], "fixture", {"ok": True}

    monkeypatch.setattr(collector, "collect_boeun_youth_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.BOEUN_YOUTH_PROVIDER,
        name="fixture",
        branch="fixture",
        url=collector.BOEUN_YOUTH_URL,
        source="fixture",
        priority=1,
        region="충청북도 보은군",
        extra={},
    )
    rows, parser, meta = router.collect_from_url(target, timeout=3, max_pages=5, detail_limit=10)
    assert rows == [{"provider": collector.BOEUN_YOUTH_PROVIDER}]
    assert parser == "fixture" and meta == {"ok": True}
    assert len(calls) == 1


def test_target_operational_and_coverage_configuration() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = yaml.safe_load(
        (root / "config/crawl_targets/public_reservation.yaml").read_text(encoding="utf-8")
    )["targets"]
    target = next(row for row in targets if row.get("provider") == collector.BOEUN_YOUTH_PROVIDER)
    assert target["url"] == collector.BOEUN_YOUTH_URL
    assert target["candidate_id"] == collector.BOEUN_YOUTH_CANDIDATE_ID
    assert target["ops_scopes"] == ["education", "experience"]
    assert target["covered_municipalities"] == [
        {
            "code": "4372000000",
            "sido": "충청북도",
            "sigungu": "보은군",
            "full_name": "충청북도 보은군",
        }
    ]
    operational = yaml.safe_load(
        (root / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    source = next(row for row in operational if row.get("provider") == collector.BOEUN_YOUTH_PROVIDER)
    assert source["ops_scopes"] == ["education", "experience"]
    coverage = yaml.safe_load(
        (root / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )["municipalities"]
    region = next(row for row in coverage if row.get("code") == "4372000000")
    assert collector.BOEUN_YOUTH_PROVIDER in region["owner_providers"]
    assert collector.BOEUN_YOUTH_PROVIDER in region["promoted_providers"]
    assert collector.BOEUN_YOUTH_PROVIDER in region["yaml_owner_providers"]
    assert any(
        row.get("kind") == "exact_active_url"
        and row.get("provider") == collector.BOEUN_YOUTH_PROVIDER
        and row.get("target_file") == "public_reservation.yaml"
        for row in region["evidence"]
    )


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_BOEUN_YOUTH_EXPERIENCE") != "1",
    reason="set RUN_LIVE_BOEUN_YOUTH_EXPERIENCE=1 for the official live contract test",
)
def test_live_boeun_youth_snapshot() -> None:
    rows, _, meta = collector.collect_boeun_youth_experience(
        _target(), today="2026-08-05", timeout=30, max_pages=10, detail_limit=60
    )
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 39
    assert meta["experience_rows"] >= 18
    assert any(row["service_family"] == "experience" for row in rows)
    assert all(bool(row["application_url"]) == row["reservation_available"] for row in rows)
