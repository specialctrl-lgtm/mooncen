from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
import threading
from typing import Any

from Crawler import municipal_daegu_reservation as daegu


@dataclass(frozen=True)
class Target:
    provider: str = daegu.DAEGU_EDUCATION_PROVIDER
    url: str = daegu.DAEGU_EDUCATION_URL


class FakeCookies:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, name: str, value: str, **_kwargs: Any) -> None:
        self.values[name] = value


class FakeResponse:
    def __init__(
        self,
        *,
        payload: Any = None,
        text: str = "",
        status_code: int = 200,
        history: list[object] | None = None,
    ) -> None:
        self._payload = payload
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.history = history or []

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeBackend:
    def __init__(self, responses: dict[tuple[Any, ...], Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Any]] = []
        self.lock = threading.Lock()
        self.netfunnel_body = (
            "300:key=" + ("A" * 96)
            + "&nwait=0&nnext=0&tps=0.000000&ttl=0"
            + "&ip=yeyakwait.daegu.go.kr&port=443&sticky=nf1"
        )

    def factory(self) -> "FakeSession":
        return FakeSession(self)


class FakeSession:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend
        self.cookies = FakeCookies()

    def get(self, url: str, **_kwargs: Any) -> FakeResponse:
        with self.backend.lock:
            self.backend.calls.append(("GET", url))
        if url != daegu.DAEGU_NETFUNNEL_URL:
            return FakeResponse(status_code=404, text="missing")
        return FakeResponse(text=self.backend.netfunnel_body)

    def post(self, url: str, *, json: dict[str, Any], **_kwargs: Any) -> FakeResponse:
        with self.backend.lock:
            self.backend.calls.append(("POST", (url, dict(json))))
        if not any(name.startswith("_nfbasic:") for name in self.cookies.values):
            return FakeResponse(
                status_code=400,
                payload={"result": False, "code": "BIZ_ERROR", "data": None},
            )
        if url == daegu.DAEGU_LIST_API:
            key = ("list", str(json.get("searchGbn2")), int(json.get("pageIndex", 0)))
        elif url == daegu.DAEGU_DETAIL_API:
            key = (
                "detail",
                str(json.get("instId")),
                str(json.get("gdsId")),
                str(json.get("lsnId")),
            )
        else:
            return FakeResponse(status_code=404, text="missing")
        value = self.backend.responses.get(key)
        if value is None:
            return FakeResponse(status_code=404, text=f"missing {key}")
        if isinstance(value, FakeResponse):
            return value
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], int):
            return FakeResponse(status_code=value[0], payload=value[1])
        return FakeResponse(payload=value)

    def close(self) -> None:
        return None


def api_success(data: dict[str, Any]) -> dict[str, Any]:
    return {"result": True, "code": "SUCCESS", "message": None, "data": data}


def list_item(
    suffix: str,
    *,
    inst_id: str,
    institution: str,
    status: str,
    education: tuple[str, str],
    application: tuple[str, str],
    charged: str = "N",
    capacity: int = 20,
    applied: int = 3,
    deadline: str = "N",
) -> dict[str, Any]:
    return {
        "gdsId": f"GDS_{suffix}",
        "instId": inst_id,
        "lsnId": f"CRS_{suffix}",
        "gdsClsfCd": "DSS_0001_01_01_0001",
        "gdsClsfDcd": "DSS_0001_01",
        "gdsNm": f"교육 강좌 {suffix}",
        "instNm": institution,
        "utztnPsbltyDow": "3",
        "eduBgngTm": "10:00",
        "eduEndTm": "12:00",
        "dowEduTmMngYn": "N",
        "dayEduTm": None,
        "eduBgngYmd": education[0],
        "eduEndYmd": education[1],
        "eduYmdChk": "2",
        "rcrtCnt": capacity,
        "chrgYn": charged,
        "grndsRcptYn": "N",
        "rtrcnLsnYn": "N",
        "ddlnYn": deadline,
        "utztnTrgt": "대구시민",
        "usgDcd": "",
        "rsvtAplyCnt": applied,
        "rsvtWaitAplyCnt": 0,
        "rsvtLotteryAplyCnt": 0,
        "rcptBgngYmd": application[0],
        "rcptEndYmd": application[1],
        "prrtyRsvtBgngYmd": None,
        "prrtyRsvtEndYmd": None,
        "addRsvtBgngYmd": None,
        "addRsvtEndYmd": None,
        "telRcptYn": "N",
        "onlnYn": "Y",
        "rcptStatus": status,
    }


def list_page(items: list[dict[str, Any]], total: int, pages: int, page: int) -> dict[str, Any]:
    return api_success(
        {
            "items": items,
            "page": page,
            "pageSize": daegu.DAEGU_PAGE_SIZE,
            "totalElements": total,
            "toalPages": pages,
        }
    )


def detail_for(
    listed: dict[str, Any],
    *,
    address: str | None,
    place: str = "교육실",
    intro: str = "<p>공식 교육 안내</p>",
    amount: str | None = None,
    wait_enabled: str = "Y",
    wait_capacity: str = "5",
    wait_applied: int = 0,
) -> dict[str, Any]:
    start = "20" + listed["eduBgngYmd"]
    end = "20" + listed["eduEndYmd"]
    apply_start = "20" + listed["rcptBgngYmd"]
    apply_end = "20" + listed["rcptEndYmd"]
    return api_success(
        {
            "gdsId": listed["gdsId"],
            "lsnId": listed["lsnId"],
            "instId": listed["instId"],
            "gdsClsfCd": listed["gdsClsfCd"],
            "gdsClsfDcd": listed["gdsClsfDcd"],
            "gdsNm": listed["gdsNm"],
            "gdsClsfNm": f"{listed['instNm']} > 교육 > 강좌",
            "utztnPsbltyDow": listed["utztnPsbltyDow"],
            "chrgYnNm": "유료" if listed["chrgYn"] == "Y" else "무료",
            "ntslAmt": amount,
            "rcrtCnt": listed["rcrtCnt"],
            "eduTrgt": None,
            "eduPlc": place,
            "eduBgngYmd": start,
            "eduEndYmd": end,
            "eduBgngTm": listed["eduBgngTm"],
            "eduEndTm": listed["eduEndTm"],
            "dowEduTmMngYn": listed["dowEduTmMngYn"],
            "dayEduTm": listed["dayEduTm"],
            "instrNm": "강사",
            "inqryTelNo": "053-120",
            "rcptBgngYmd": apply_start,
            "rcptEndYmd": apply_end,
            "rcptBgngTm": "09:00",
            "rcptEndTm": "18:00",
            "urlAddr": "",
            "lsnIntro": intro,
            "cutnMttr": "<p>유의사항</p>",
            "grndsRcptYn": listed["grndsRcptYn"],
            "telRcptYn": listed["telRcptYn"],
            "onlnYn": listed["onlnYn"],
            "rcptStatus": listed["rcptStatus"],
            "rsvtAplyCnt": listed["rsvtAplyCnt"],
            "waitprsUseYn": wait_enabled,
            "rsvtWaitAplyCnt": wait_applied,
            "waitprsNope": wait_capacity,
            "rtrcnLsnYn": listed["rtrcnLsnYn"],
            "instAddr": address,
            "instDaddr": "",
            "instNm": listed["instNm"],
        }
    )


def valid_backend() -> tuple[FakeBackend, dict[str, dict[str, Any]]]:
    open_row = list_item(
        "00000001",
        inst_id="DSS_INST_00000114",
        institution="대구약령시 한방의료체험타운",
        status="ING",
        education=("26.07.25", "26.08.25"),
        application=("26.07.10", "26.07.22"),
    )
    scheduled_row = list_item(
        "00000002",
        inst_id="DSS_INST_00000107",
        institution="대구광역시여성회관",
        status="READY",
        education=("26.09.01", "26.10.01"),
        application=("26.08.01", "26.08.10"),
        charged="Y",
    )
    ongoing_closed = list_item(
        "00000003",
        inst_id="DSS_INST_00000121",
        institution="동부여성문화회관",
        status="END",
        education=("26.05.01", "26.07.29"),
        application=("26.04.01", "26.04.10"),
        deadline="Y",
    )
    expired = list_item(
        "00000004",
        inst_id="DSS_INST_00000135",
        institution="종합복지회관",
        status="END",
        education=("26.05.01", "26.07.19"),
        application=("26.04.01", "26.04.10"),
        deadline="Y",
    )
    responses: dict[tuple[Any, ...], Any] = {
        ("list", "1", 1): list_page([open_row], 1, 1, 1),
        ("list", "1", 2): list_page([], 1, 1, 2),
        ("list", "2", 1): list_page([scheduled_row], 1, 1, 1),
        ("list", "2", 2): list_page([], 1, 1, 2),
        ("list", "3", 1): list_page([ongoing_closed, expired], 2, 1, 1),
        ("list", "3", 2): list_page([], 2, 1, 2),
        (
            "detail",
            open_row["instId"],
            open_row["gdsId"],
            open_row["lsnId"],
        ): detail_for(open_row, address="대구 중구 중앙대로77길 45"),
        (
            "detail",
            scheduled_row["instId"],
            scheduled_row["gdsId"],
            scheduled_row["lsnId"],
        ): detail_for(
            scheduled_row,
            address="대구광역시 북구 팔달로1길 26",
            amount="20,000",
        ),
        (
            "detail",
            ongoing_closed["instId"],
            ongoing_closed["gdsId"],
            ongoing_closed["lsnId"],
        ): detail_for(ongoing_closed, address="대구시 동구 신암북로11길 54-2"),
    }
    return FakeBackend(responses), {
        "open": open_row,
        "scheduled": scheduled_row,
        "ongoing": ongoing_closed,
        "expired": expired,
    }


def collect(backend: FakeBackend, **kwargs: Any):
    return daegu.collect_daegu_integrated_education(
        Target(),
        timeout=1,
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 10),
        today=kwargs.pop("today", date(2026, 7, 20)),
        max_workers=kwargs.pop("max_workers", 3),
        fetch_attempts=kwargs.pop("fetch_attempts", 1),
        session_factory=backend.factory,
        **kwargs,
    )


def test_target_contract_keeps_one_provider_but_separates_exact_catalog_paths() -> None:
    assert daegu.DAEGU_EDUCATION_PROVIDER == daegu.DAEGU_EXPERIENCE_PROVIDER
    assert daegu.is_target(Target()) is True
    assert daegu.is_target(Target(url=daegu.DAEGU_EXPERIENCE_URL)) is False
    assert daegu.is_target(Target(url="https://yeyak.daegu.go.kr/")) is False
    assert daegu.is_target(
        Target(provider=daegu.DAEGU_DISCOVERY_ALIAS_PROVIDER)
    ) is False
    assert daegu.is_target(Target(url=daegu.DAEGU_EDUCATION_URL + "?instId=x")) is False
    assert daegu.daegu_education_detail_url(
        "DSS_INST_00000114", "GDS_00000001", "CRS_00000001"
    ).endswith("/lect/detail/DSS_INST_00000114/GDS_00000001/CRS_00000001")
    assert daegu.daegu_education_detail_url("bad", "GDS_00000001", "CRS_00000001") == ""


def test_complete_partitions_sentinels_recheck_and_current_details() -> None:
    backend, _items = valid_backend()
    rows, parser, meta = collect(backend)

    assert parser == daegu.DAEGU_EDUCATION_PARSER
    assert [row["title"] for row in rows] == [
        "교육 강좌 00000001",
        "교육 강좌 00000002",
        "교육 강좌 00000003",
    ]
    assert meta["source_total"] == meta["source_rows"] == 4
    assert meta["partition_totals"] == {"1": 1, "2": 1, "3": 2}
    assert meta["partition_pages"] == {"1": 1, "2": 1, "3": 1}
    assert meta["pages"] == meta["required_list_pages"] == 3
    assert meta["sentinel_requests"] == 3
    assert meta["stability_rechecks"] == 3
    assert meta["list_api_requests"] == 9
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["current_count"] == meta["returned_count"] == 3
    assert meta["expired_count"] == 1
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["stable_recheck_complete"] is True
    assert meta["netfunnel_api_admissions"] == 12
    assert meta["pii_payload_persisted"] is False
    assert meta["actual_municipality_codes"] == [
        "2711000000",
        "2714000000",
        "2723000000",
    ]

    open_row, scheduled, ongoing = rows
    assert open_row["status"] == "OPEN"
    assert open_row["reservation_available"] is True
    assert open_row["application_url"] == open_row["raw_url"]
    assert open_row["municipality_full_name"] == "대구광역시 중구"
    assert scheduled["status"] == "SCHEDULED"
    assert scheduled["reservation_available"] is False
    assert scheduled["application_url"] == ""
    assert scheduled["fee"] == "20,000원"
    assert scheduled["municipality_full_name"] == "대구광역시 북구"
    assert ongoing["status"] == "CLOSED"
    assert ongoing["application_type"] == "INFO_ONLY"
    assert ongoing["municipality_full_name"] == "대구광역시 동구"
    assert len({row["provider_course_id"] for row in rows}) == 3
    assert len({row["raw_url"] for row in rows}) == 3
    assert all("key" not in row["raw_fields"] for row in rows)
    assert all("cookie" not in row["raw_fields"] for row in rows)
    assert all("inqryTelNo" not in row["raw_fields"] for row in rows)

    call_counts = Counter(method for method, _value in backend.calls)
    assert call_counts == {"GET": 12, "POST": 12}


def test_null_institution_address_uses_official_course_venue_evidence() -> None:
    backend, items = valid_backend()
    row = items["open"]
    detail_key = ("detail", row["instId"], row["gdsId"], row["lsnId"])
    backend.responses[detail_key] = detail_for(
        row,
        address=None,
        place="서대구산단 복합문화센터 교육장",
        intro="<p>이론: 서구 와룡로 90길 41</p>",
    )

    rows, _parser, meta = collect(backend)

    assert meta["snapshot_complete"] is True
    assert rows[0]["municipality_code"] == "2717000000"
    assert rows[0]["raw_fields"]["municipality_evidence_source"] == "course_venue"


def test_nonempty_immediate_sentinel_fails_closed() -> None:
    backend, items = valid_backend()
    backend.responses[("list", "1", 2)] = list_page([items["open"]], 1, 1, 2)

    rows, _parser, meta = collect(backend)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel contract failed" in meta["configured_collection_error"]


def test_partition_status_or_declared_total_drift_fails_closed() -> None:
    backend, items = valid_backend()
    changed = dict(items["scheduled"])
    changed["rcptStatus"] = "ING"
    backend.responses[("list", "2", 1)] = list_page([changed], 1, 1, 1)

    rows, _parser, meta = collect(backend)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "does not belong to partition 2" in meta["configured_collection_error"]


def test_page_and_detail_caps_never_return_partial_rows() -> None:
    backend, _items = valid_backend()
    rows, _parser, page_meta = collect(backend, max_pages=1)
    assert rows == []
    assert page_meta["snapshot_complete"] is False
    assert "sentinel beyond max_pages" in page_meta["configured_collection_error"]

    backend, _items = valid_backend()
    rows, _parser, detail_meta = collect(backend, detail_limit=2)
    assert rows == []
    assert detail_meta["snapshot_complete"] is False
    assert detail_meta["source_cap_reached"] is True
    assert detail_meta["current_count"] == 3
    assert detail_meta["detail_attempts"] == 0


def test_detail_identity_or_municipality_mismatch_fails_closed() -> None:
    backend, items = valid_backend()
    row = items["open"]
    key = ("detail", row["instId"], row["gdsId"], row["lsnId"])
    invalid = detail_for(row, address="대구 중구 중앙대로77길 45")
    invalid["data"]["gdsId"] = "GDS_99999999"
    backend.responses[key] = invalid

    rows, _parser, meta = collect(backend)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail identity mismatch" in meta["configured_collection_error"]

    backend, items = valid_backend()
    row = items["open"]
    key = ("detail", row["instId"], row["gdsId"], row["lsnId"])
    backend.responses[key] = detail_for(row, address="경상북도 경산시 대학로 1")
    rows, _parser, meta = collect(backend)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "municipality evidence" in meta["configured_collection_error"]


def test_full_or_closed_courses_have_no_application_url() -> None:
    backend, items = valid_backend()
    row = items["open"]
    row["rsvtAplyCnt"] = row["rcrtCnt"]
    key = ("detail", row["instId"], row["gdsId"], row["lsnId"])
    backend.responses[key] = detail_for(
        row,
        address="대구 중구 중앙대로77길 45",
        wait_enabled="Y",
        wait_capacity="5",
        wait_applied=5,
    )

    rows, _parser, meta = collect(backend)

    assert meta["snapshot_complete"] is True
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is False
    assert rows[0]["application_url"] == ""


def test_netfunnel_failure_and_redirect_are_fail_closed_without_key_leak() -> None:
    backend, _items = valid_backend()
    backend.netfunnel_body = "201:key=SHOULD_NOT_BE_REPORTED&nwait=20"

    rows, _parser, meta = collect(backend)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "NetFUNNEL admission was not immediately passed" in meta["configured_collection_error"]
    assert "SHOULD_NOT_BE_REPORTED" not in str(meta)


def test_stable_recheck_detects_catalogue_drift() -> None:
    backend, items = valid_backend()
    original = backend.responses[("list", "1", 1)]
    changed_row = dict(items["open"])
    changed_row["gdsId"] = "GDS_00000009"
    changed_row["lsnId"] = "CRS_00000009"
    changed = list_page([changed_row], 1, 1, 1)
    queue = [original, changed]

    class DriftingSession(FakeSession):
        def post(self, url: str, *, json: dict[str, Any], **kwargs: Any) -> FakeResponse:
            if (
                url == daegu.DAEGU_LIST_API
                and str(json.get("searchGbn2")) == "1"
                and int(json.get("pageIndex", 0)) == 1
            ):
                with self.backend.lock:
                    self.backend.calls.append(("POST", (url, dict(json))))
                    value = queue.pop(0)
                return FakeResponse(payload=value)
            return super().post(url, json=json, **kwargs)

    backend.factory = lambda: DriftingSession(backend)  # type: ignore[method-assign]
    rows, _parser, meta = collect(backend)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "changed during stable recheck" in meta["configured_collection_error"]
