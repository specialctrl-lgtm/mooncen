from __future__ import annotations

import json

from Crawler import Crawler_GeneratedYamlTargets as generated_targets
from Crawler import gimhae_dongbu_senior as gimhae


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def json(self) -> object:
        return self._payload


class FakeSession:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.requests: list[str] = []
        self.closed = False

    def get(self, url: str, *, timeout: int) -> FakeResponse:
        assert timeout > 0
        self.requests.append(url)
        payload = self.routes.get(url)
        if payload is None:
            return FakeResponse({"success": False, "errorCode": "NOT_FOUND"}, 404)
        return FakeResponse({"success": True, "data": payload})

    def close(self) -> None:
        self.closed = True


def _target(url: str = gimhae.ROOT_URL) -> dict[str, str]:
    return {"provider": gimhae.PROVIDER, "url": url}


def _agency() -> dict[str, object]:
    return {
        "customerNo": 1956,
        "agencyName": "김해시동부노인종합복지관",
        "address": "[50811] 경남 김해시 신어산길 46",
    }


def _term6_lottery_rows() -> list[dict[str, object]]:
    rows = [
        {
            "totalRows": 67,
            "totalPages": 1,
            "pageNumber": 1,
            "idx": index,
            "termCd": 6,
            "termNm": "하반기",
            "prgCdid": 800 + index,
            "prgCd": spec.code,
            "prgNm": f"{spec.title}(하)",
            "maxPersons": spec.capacity,
            "recvCount": index,
        }
        for index, spec in enumerate(gimhae.TERM_6_SPECS, start=1)
    ]
    rows.extend(
        [
            {
                "totalRows": 67,
                "totalPages": 1,
                "pageNumber": 1,
                "idx": 65,
                "termCd": 6,
                "termNm": "하반기",
                "prgCdid": 9901,
                "prgCd": "pp001",
                "prgNm": "테스트",
                "maxPersons": 15,
                "recvCount": 1,
            },
            {
                "totalRows": 67,
                "totalPages": 1,
                "pageNumber": 1,
                "idx": 66,
                "termCd": 6,
                "termNm": "하반기",
                "prgCdid": 9902,
                "prgCd": "pp002",
                "prgNm": "테스트1",
                "maxPersons": 15,
                "recvCount": 1,
            },
            {
                "totalRows": 67,
                "totalPages": 1,
                "pageNumber": 1,
                "idx": 67,
                "termCd": 6,
                "termNm": "하반기",
                "prgCdid": 9903,
                "prgCd": "PPP01",
                "prgNm": "테스트3",
                "maxPersons": 12,
                "recvCount": 1,
            },
        ]
    )
    return rows


def _term6_routes(
    lottery_rows: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        gimhae.AGENCY_INFO_URL: _agency(),
        gimhae.TERMS_URL: [
            {
                "id": 2,
                "termCd": 6,
                "termYear": "2026",
                "termNm": "하반기",
                "termTitle": "2026 - 하반기",
                "activeYn": "Y",
                "finishYn": "Y",
            }
        ],
        gimhae.TERM_STATUS_URL: {
            "termNm": "하반기",
            "recvSdate": "2026-06-19",
            "recvEdate": "2026-07-31",
            "resultPublished": True,
        },
        gimhae.COURSES_URL: [],
        f"{gimhae.LOTTERY_RESULTS_URL}?termCd=6&size=200": (
            lottery_rows if lottery_rows is not None else _term6_lottery_rows()
        ),
    }


def test_term6_closed_catalogue_joins_complete_public_identity_ledger() -> None:
    fake = FakeSession(_term6_routes())

    rows, parser, meta = gimhae.collect(
        _target(),
        timeout=10,
        max_pages=10,
        detail_limit=200,
        today="2026-07-28",
        session_factory=lambda: fake,
    )

    assert parser == gimhae.PARSER
    assert len(rows) == 64
    assert meta["source_rows"] == 67
    assert meta["catalogue_rows"] == 0
    assert meta["excluded_test_rows"] == 3
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert fake.closed is True
    assert all(
        row[field]
        for row in rows
        for field in (
            "title",
            "target",
            "fee",
            "period",
            "venue_name",
            "category",
            "schedule_raw",
        )
    )
    assert {row["category"] for row in rows} == {
        "평생교육",
        "정보화교육",
        "취미여가",
        "건강증진",
    }
    assert {row["status"] for row in rows} == {"접수마감"}
    assert all("테스트" not in row["title"] for row in rows)
    assert {row["application_url"] for row in rows} == {gimhae.COURSE_URL}


def test_term6_identity_change_fails_closed() -> None:
    lottery = _term6_lottery_rows()
    lottery.pop(0)
    for item in lottery:
        item["totalRows"] = len(lottery)
    fake = FakeSession(_term6_routes(lottery))

    rows, _, meta = gimhae.collect(
        _target(),
        timeout=10,
        max_pages=10,
        detail_limit=200,
        today="2026-07-28",
        session_factory=lambda: fake,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "identity contract changed" in meta["configured_collection_error"]


def test_future_term_uses_complete_live_course_fields() -> None:
    course = {
        "prgCdid": 1001,
        "prgCd": "B1001",
        "prgNm": "생활영어",
        "eduTimes": "월, 수 10:00~10:50",
        "eduDuration": "2027-02-01 ~ 2027-06-18",
        "place": "1실(4층)",
        "amt": 15000,
        "maxPersons": 20,
        "recvCount": 3,
        "remainCount": 17,
        "closedYn": "N",
        "durationNm": "주 2회",
        "listGbn": "P001",
    }
    routes = {
        gimhae.AGENCY_INFO_URL: _agency(),
        gimhae.TERMS_URL: [
            {
                "termCd": 7,
                "termYear": "2027",
                "termNm": "상반기",
                "termTitle": "2027 - 상반기",
                "activeYn": "Y",
            }
        ],
        gimhae.TERM_STATUS_URL: {
            "recvSdate": "2027-01-04",
            "recvEdate": "2027-01-08",
            "resultPublished": False,
        },
        gimhae.COURSES_URL: [course],
    }
    fake = FakeSession(routes)

    rows, _, meta = gimhae.collect(
        _target(),
        timeout=10,
        max_pages=10,
        detail_limit=200,
        today="2027-01-05",
        session_factory=lambda: fake,
    )

    assert meta["source_mode"] == "live_course_catalogue"
    assert len(rows) == 1
    assert rows[0]["title"] == "생활영어"
    assert rows[0]["target"] == gimhae.DEFAULT_TARGET
    assert rows[0]["fee"] == "15,000원"
    assert rows[0]["period"] == "2027-02-01 ~ 2027-06-18"
    assert rows[0]["venue_name"].endswith("1실(4층)")
    assert rows[0]["category"] == "평생교육"
    assert rows[0]["schedule_raw"] == "월, 수 10:00~10:50"
    assert rows[0]["status"] == "접수중"


def test_target_and_request_caps_are_strict() -> None:
    assert gimhae.is_target(_target())
    assert gimhae.is_target(_target(f"{gimhae.ROOT_URL}/course"))
    assert not gimhae.is_target(_target("https://gimhaedongbu.or.kr/business/bus06.htm"))
    assert not gimhae.is_target(_target(f"{gimhae.ROOT_URL}/course?next=1"))

    rows, _, meta = gimhae.collect(
        _target(),
        timeout=10,
        max_pages=3,
        detail_limit=200,
        today="2026-07-28",
    )
    assert rows == []
    assert meta["configured_collection_error"] == ("timeout/max_pages/detail_limit are invalid")


def test_only_reviewed_provider_bypasses_generic_tenant_domain_exclusion() -> None:
    reviewed = {
        "provider": gimhae.PROVIDER,
        "crawler_status": "ready",
        "url": gimhae.ROOT_URL,
    }
    unreviewed = {
        "provider": "UNREVIEWED_E_NCOM_TENANT",
        "crawler_status": "ready",
        "url": "https://another.e-ncom.co.kr",
    }

    assert generated_targets._is_working_target(reviewed) is True
    assert generated_targets._is_registry_target(reviewed) is True
    assert generated_targets._is_working_target(unreviewed) is False
    assert generated_targets._is_registry_target(unreviewed) is False
