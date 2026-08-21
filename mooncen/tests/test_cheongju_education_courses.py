from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import threading
from typing import Any, Mapping

import yaml

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import municipal_cheongju as cheongju


ROOT = Path(__file__).resolve().parents[1]
TARGETS_PATH = ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml"
REGISTRY_PATH = ROOT / "config" / "generated_yaml_crawler_registry.yaml"


def _target(
    provider: str = cheongju.CHEONGJU_PROVIDER,
    url: str = cheongju.CHEONGJU_URL,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "url": url,
        "name": "청주시 평생학습관 전체 현재·향후 교육강좌",
        "branch": "충청북도 청주시",
    }


def _list_item(
    sequence: int,
    scope: cheongju.CheongjuStatusScope,
    *,
    category_code: str,
    category_name: str,
    title: str | None = None,
    batch_type: str = "",
    additional_order: int = 0,
) -> dict[str, Any]:
    return {
        "lctreSeq": sequence,
        "batchTy": batch_type,
        "aditOrdr": additional_order,
        "lctreNm": title or f"공식 청주시 강좌 {sequence}",
        "beginDe": "2099-08-01",
        "endDe": "2099-08-31",
        "beginTm": "10:00",
        "endTm": "12:00",
        "rceptBeginDe": "2099-07-01",
        "rceptEndDe": "2099-07-31",
        "rceptBeginTm": "09:00",
        "rceptEndTm": "18:00",
        "amount": 0,
        "psncpa": 20,
        "applyCnt": 3,
        "edcSe": {"code": category_code, "name": category_name},
        "area": {"code": "43114", "name": "청원구"},
        "result": {
            "lctreTy": "inner",
            "lctreSttus": {"code": scope.code, "name": scope.name},
            "insttNm": "청주시평생학습관",
        },
    }


def _detail(item: Mapping[str, Any]) -> dict[str, Any]:
    result = item["result"]
    return {
        "lctreSeq": item["lctreSeq"],
        "batchTy": item["batchTy"],
        "aditOrdr": item["aditOrdr"],
        "lctreNm": item["lctreNm"],
        "beginDe": item["beginDe"],
        "endDe": item["endDe"],
        "beginTm": item["beginTm"],
        "endTm": item["endTm"],
        "rceptBeginDe": item["rceptBeginDe"],
        "rceptEndDe": item["rceptEndDe"],
        "rceptBeginTm": item["rceptBeginTm"],
        "rceptEndTm": item["rceptEndTm"],
        "amount": item["amount"],
        "matrlCt": 0,
        "psncpa": item["psncpa"],
        "applyCnt": item["applyCnt"],
        "lctreStep": deepcopy(result["lctreSttus"]),
        "referMatter": f"<p>상세 API 설명 {item['lctreSeq']}</p>",
        "edcTrget": "청주시민",
        "edcDow": {"code": "MON", "name": "월요일"},
        "slctnMthd": {"code": "ONLINE", "name": "온라인 선착순"},
        "cnterNm": "내수평생학습센터",
        "edcInstt": {
            "seq": 44,
            "name": "청주시평생학습관",
            # This is public venue/address data, not a person's contact field.
            "etc": "충청북도 청주시 청원구 내수읍",
        },
        "lctrum": {
            "lctrumSeq": 51,
            "lctrumNm": "내수 강의실",
            "lctrumSe": {"code": "CENTER", "name": "분관"},
        },
        "lctrumCenter": "내수평생학습센터 내수 강의실",
        "instrctrs": [
            {
                "instrctrNm": "공식 강사",
                "moblphon": "010-1234-5678",
                "account": {"email": "teacher@example.test"},
            }
        ],
    }


def _official_35() -> dict[str, list[dict[str, Any]]]:
    counts = {
        "C0120002": 2,
        "C0120003": 1,
        "C0120004": 4,
        "C0120005": 28,
    }
    categories = (
        [("C0020005", "테마특강")] * 16
        + [("C0020014", "배움더하기")] * 6
        + [("C0020004", "평생학습센터")] * 5
        + [("C0020013", "성인문해")] * 5
        + [("C0020009", "딩동! 찾아가는평생학습")] * 2
        + [("C0020012", "시민대학")]
    )
    scopes = {scope.code: scope for scope in cheongju.CHEONGJU_STATUS_SCOPES}
    result: dict[str, list[dict[str, Any]]] = {}
    cursor = 0
    sequence = 7001
    for status_code, count in counts.items():
        values: list[dict[str, Any]] = []
        for _ in range(count):
            category_code, category_name = categories[cursor]
            values.append(
                _list_item(
                    sequence,
                    scopes[status_code],
                    category_code=category_code,
                    category_name=category_name,
                    batch_type="adit" if sequence == 7001 else "",
                    additional_order=1 if sequence == 7001 else 0,
                )
            )
            cursor += 1
            sequence += 1
        result[status_code] = values
    return result


class FakeCheongjuApi:
    def __init__(
        self,
        status_rows: Mapping[str, list[dict[str, Any]]],
        *,
        declared_total_delta: int = 0,
        mutate_recheck: bool = False,
        schedule_date: str = "2099-08-04",
    ) -> None:
        self.status_rows = deepcopy(dict(status_rows))
        self.declared_total_delta = declared_total_delta
        self.mutate_recheck = mutate_recheck
        self.schedule_date = schedule_date
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.paging_calls: Counter[str] = Counter()
        self._lock = threading.Lock()
        self.items_by_key = {
            (
                item["lctreSeq"],
                item["batchTy"],
                item["aditOrdr"],
            ): item
            for values in self.status_rows.values()
            for item in values
        }

    def __call__(
        self,
        _session: object,
        url: str,
        params: Mapping[str, Any],
        timeout: int,
    ) -> dict[str, Any]:
        assert timeout > 0
        current_params = dict(params)
        with self._lock:
            self.calls.append((url, current_params))
        if url == cheongju.CHEONGJU_PAGING_URL:
            assert current_params["lctreTy"] == "inner"
            assert current_params["size"] == cheongju.CHEONGJU_PAGE_SIZE
            assert current_params["page"] == 1
            status = current_params["lctreSttus"]
            with self._lock:
                self.paging_calls[status] += 1
                visit = self.paging_calls[status]
            values = deepcopy(self.status_rows[status])
            if self.mutate_recheck and visit == 2 and values:
                values[0]["lctreNm"] += " 변경"
            total = len(values) + self.declared_total_delta
            return {
                "code": 200,
                "errors": None,
                "paging": {
                    "totalPages": 1,
                    "totalElements": total,
                    "page": 1,
                    "size": cheongju.CHEONGJU_PAGE_SIZE,
                    "first": True,
                    "last": True,
                },
                "dataList": values,
            }
        key = (
            current_params.get("lctreSeq") or current_params.get("trgetLctre"),
            current_params.get("batchTy", ""),
            current_params.get("aditOrdr", 0),
        )
        if url == cheongju.CHEONGJU_DETAIL_URL:
            return {"code": 200, "errors": None, "data": _detail(self.items_by_key[key])}
        assert url == cheongju.CHEONGJU_SCHEDULE_URL
        sequence = current_params["trgetLctre"]
        assert any(item[0] == sequence for item in self.items_by_key)
        return {
            "code": 200,
            "errors": None,
            "dataList": [
                {
                    "schdulSeq": sequence * 10,
                    "trgetLctre": sequence,
                    "edcDe": self.schedule_date,
                }
            ],
        }


def _session_factory() -> object:
    return object()


def _collect(api: FakeCheongjuApi, **kwargs: Any):
    return cheongju.collect_cheongju_education_courses(
        _target(),
        json_getter=api,
        session_factory=_session_factory,
        today="2099-07-20",
        max_pages=1200,
        detail_limit=1200,
        max_workers=6,
        **kwargs,
    )


def test_cheongju_collects_all_four_current_scopes_with_details_and_schedules() -> None:
    api = FakeCheongjuApi(_official_35())

    rows, parser, meta = _collect(api)

    assert parser == cheongju.CHEONGJU_PARSER
    assert len(rows) == 35
    assert meta["source_total"] == 35
    assert meta["unique_id_count"] == meta["composite_id_count"] == 35
    assert meta["current_count"] == meta["returned_count"] == 35
    assert meta["detail_attempts"] == meta["detail_pages"] == 35
    assert meta["schedule_attempts"] == meta["schedule_pages"] == 35
    assert meta["list_requests"] == meta["list_recheck_requests"] == 4
    assert meta["request_count"] == 78
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["schedule_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["status_source_counts"] == {
        "접수대기": 2,
        "접수진행": 1,
        "접수마감": 4,
        "교육중": 28,
    }
    assert meta["education_type_counts"] == {
        "테마특강": 16,
        "배움더하기": 6,
        "평생학습센터": 5,
        "성인문해": 5,
        "딩동! 찾아가는평생학습": 2,
        "시민대학": 1,
    }
    assert all(count == 2 for count in api.paging_calls.values())
    assert all(row["description"].startswith("상세 API 설명") for row in rows)
    assert all(row["raw_fields"]["description_source"] == "detail_refer_matter" for row in rows)
    assert all(row["fee"] == "무료" for row in rows)
    assert all(row["raw_fields"]["fee_amount"] == 0 for row in rows)
    assert all(row["schedule_dates"] == ["2099-08-04"] for row in rows)
    assert meta["schedule_period_anomaly_count"] == 0
    assert all(row["municipality_code"] == "4311400000" for row in rows)
    assert all(row["address"] == "충청북도 청주시 청원구 내수읍" for row in rows)
    assert all(row["target"] == "청주시민" for row in rows)
    assert {row["status"] for row in rows} == {"SCHEDULED", "OPEN", "CLOSED"}
    open_rows = [row for row in rows if row["status"] == "OPEN"]
    assert len(open_rows) == 1
    assert open_rows[0]["application_url"] == open_rows[0]["raw_url"]
    assert open_rows[0]["reservation_available"] is True
    assert all(
        not row["application_url"] and row["reservation_available"] is False
        for row in rows
        if row["status"] != "OPEN"
    )
    serialized = json.dumps(rows, ensure_ascii=False).casefold()
    assert "010-1234-5678" not in serialized
    assert "teacher@example.test" not in serialized
    assert "moblphon" not in serialized
    assert '"email"' not in serialized


def test_cheongju_category_routes_use_composite_fragment_and_static_adult_page() -> None:
    assert cheongju.cheongju_course_url("C0020005", 7003) == (
        "https://lll.cheongju.go.kr/papp/P020303#7003"
    )
    assert cheongju.cheongju_course_url("C0020014", 7001, "adit", 2) == (
        "https://lll.cheongju.go.kr/papp/P020104#7001!adit2"
    )
    assert cheongju.cheongju_course_url("C0020013", 7008) == (
        "https://lll.cheongju.go.kr/papp/P020305"
    )
    assert cheongju.cheongju_course_url("C0020012", 7010) == (
        "https://lll.cheongju.go.kr/ccu/capp/C1202#7010"
    )
    assert cheongju.cheongju_course_url("UNKNOWN", 1) == ""
    assert cheongju.cheongju_course_url("C0020005", 7003, "bad type", 0) == ""


def test_cheongju_exact_canonical_target_and_aliases_are_non_executing() -> None:
    assert cheongju.is_cheongju_target(_target()) is True
    for bad_url in (
        "http://lll.cheongju.go.kr/papp/P0401",
        "https://www.lll.cheongju.go.kr/papp/P0401",
        "https://lll.cheongju.go.kr:443/papp/P0401",
        "https://lll.cheongju.go.kr/papp/P0401?status=open",
        "https://lll.cheongju.go.kr/papp/P0401#7001",
        "https://lll.cheongju.go.kr/papp/P020101",
    ):
        assert cheongju.is_cheongju_target(_target(url=bad_url)) is False
    assert cheongju.is_cheongju_target(
        _target(provider="MUNI_UNREVIEWED")
    ) is False

    for alias in cheongju.CHEONGJU_NON_EXECUTING_ALIASES:
        called = False

        def fail_if_called(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            nonlocal called
            called = True
            raise AssertionError("alias must not make a network request")

        rows, parser, meta = cheongju.collect_cheongju_education_courses(
            _target(alias.provider, alias.url),
            json_getter=fail_if_called,
            session_factory=_session_factory,
        )
        assert rows == []
        assert parser == cheongju.CHEONGJU_PARSER
        assert called is False
        assert meta["non_executing_alias"] is True
        assert meta["execution_enabled"] is False
        assert meta["duplicate_of"] == cheongju.CHEONGJU_PROVIDER
        assert meta["snapshot_complete"] is False


def test_cheongju_recursive_payload_sanitizer_removes_keys_and_scalar_pii() -> None:
    cleaned = cheongju.sanitize_cheongju_payload(
        {
            "safe": "교육 문의 043-000-0000",
            "moblphon": "010-1111-2222",
            "nested": [
                {
                    "emailAddress": "secret@example.test",
                    "memo": "강사 010 3333 4444 / hidden@example.test",
                }
            ],
        }
    )
    assert cleaned == {
        "safe": "교육 문의 043-000-0000",
        "nested": [{"memo": "강사 [redacted-phone] / [redacted-email]"}],
    }


def test_cheongju_declared_total_mismatch_fails_closed() -> None:
    api = FakeCheongjuApi(_official_35(), declared_total_delta=1)

    rows, _parser, meta = _collect(api)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "exposes" in meta["configured_collection_error"]
    assert not any(url == cheongju.CHEONGJU_DETAIL_URL for url, _ in api.calls)


def test_cheongju_cross_status_duplicate_composite_identity_fails_closed() -> None:
    status_rows = _official_35()
    duplicated = deepcopy(status_rows["C0120002"][0])
    duplicated["result"]["lctreSttus"] = {
        "code": "C0120003",
        "name": "접수진행",
    }
    status_rows["C0120003"] = [duplicated]
    api = FakeCheongjuApi(status_rows)

    rows, _parser, meta = _collect(api)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "status scopes overlap" in meta["configured_collection_error"]


def test_cheongju_recheck_mutation_fails_closed_after_enrichment() -> None:
    api = FakeCheongjuApi(_official_35(), mutate_recheck=True)

    rows, _parser, meta = _collect(api)

    assert rows == []
    assert meta["detail_pages"] == 35
    assert meta["schedule_pages"] == 35
    assert meta["snapshot_complete"] is False
    assert "status rows changed" in meta["configured_collection_error"]


def test_cheongju_preserves_identified_official_schedule_rows_outside_declared_period() -> None:
    api = FakeCheongjuApi(_official_35(), schedule_date="2099-07-29")

    rows, _parser, meta = _collect(api)

    assert len(rows) == 35
    assert meta["snapshot_complete"] is True
    assert meta["schedule_period_anomaly_count"] == 35
    assert all(row["schedule_dates"] == ["2099-07-29"] for row in rows)
    assert all(
        row["raw_fields"]["schedule_period_anomaly_count"] == 1 for row in rows
    )


def test_cheongju_detail_limit_is_a_hard_complete_snapshot_cap() -> None:
    api = FakeCheongjuApi(_official_35())

    rows, _parser, meta = cheongju.collect_cheongju_education_courses(
        _target(),
        json_getter=api,
        session_factory=_session_factory,
        today="2099-07-20",
        max_pages=1200,
        detail_limit=34,
    )

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is False
    assert "detail_limit=34" in meta["configured_collection_error"]


def test_cheongju_target_blocks_and_generated_execution_policy() -> None:
    targets = yaml.safe_load(TARGETS_PATH.read_text(encoding="utf-8"))["targets"]
    by_provider = {target["provider"]: target for target in targets}
    canonical = by_provider[cheongju.CHEONGJU_PROVIDER]
    assert canonical["url"] == cheongju.CHEONGJU_URL
    assert canonical["crawler_status"] == "ready"
    assert canonical["priority"] == 1
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["collection_type"] == cheongju.CHEONGJU_PARSER
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["full_snapshot_required"] is True
    assert canonical["ownership_aliases"] == list(cheongju.CHEONGJU_OWNERSHIP_ALIAS_URLS)
    assert [item["code"] for item in canonical["covered_municipalities"]] == [
        "4311000000",
        "4311100000",
        "4311200000",
        "4311300000",
        "4311400000",
    ]
    for alias in cheongju.CHEONGJU_NON_EXECUTING_ALIASES:
        target = by_provider[alias.provider]
        assert target["crawler_status"] == f"duplicate_url:{cheongju.CHEONGJU_PROVIDER}"
        assert target["duplicate_of"] == cheongju.CHEONGJU_PROVIDER
        assert target["collection_type"] == "duplicate"

    arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        cheongju.CHEONGJU_PROVIDER
    ]
    assert arguments == (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1200",
        "--detail-limit",
        "1200",
    )
    assert "--allow-partial-save" not in arguments


def test_cheongju_uses_municipal_aggregate_and_has_no_second_generated_route(
    monkeypatch,
) -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    entries = registry["targets"] if isinstance(registry, dict) else registry
    registry_providers = {entry["provider"] for entry in entries}
    assert cheongju.CHEONGJU_PROVIDER not in registry_providers
    for alias in cheongju.CHEONGJU_NON_EXECUTING_ALIASES:
        assert alias.provider not in registry_providers

    target = municipal.CrawlTarget(
        provider=cheongju.CHEONGJU_PROVIDER,
        name="청주시 평생학습관 전체 현재·향후 교육강좌",
        branch="충청북도 청주시",
        url=cheongju.CHEONGJU_URL,
        source="test",
        priority=1,
        region="충청북도 청주시",
        extra={
            "collection_category": "평생학습",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
        },
    )
    sentinel = ([{"title": "aggregate sentinel"}], cheongju.CHEONGJU_PARSER, {"pages": 4})
    monkeypatch.setattr(
        cheongju,
        "collect_cheongju_education_courses",
        lambda *_args, **_kwargs: sentinel,
    )
    assert municipal.collect_from_url(
        target,
        timeout=7,
        max_depth=0,
        max_pages=1200,
        detail_limit=1200,
    ) == sentinel
