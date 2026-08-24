from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_siheung_sports as sports


@dataclass
class Target:
    provider: str = sports.SIHEUNG_SPORTS_PROVIDER
    url: str = sports.SIHEUNG_SPORTS_URL
    branch: str = sports.SIHEUNG_MUNICIPALITY_NAME


class FakeResponse:
    def __init__(self, *, url: str, text: str = "", payload: Any = None) -> None:
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = 200
        self.history: list[Any] = []
        self._payload = payload

    def json(self) -> Any:
        return deepcopy(self._payload)


def _item(
    company: str,
    identity: int,
    *,
    total: int,
    status: str = "R",
    title: str | None = None,
) -> dict[str, Any]:
    branch = sports.SIHEUNG_SPORTS_COMPANY_NAMES[company]
    return {
        "comcd": company,
        "comnm": branch,
        "class_cd": f"{identity:05d}",
        "class_nm": title or f"정규 강좌 {company}-{identity}",
        "train_stime": "10:00",
        "train_etime": "10:50",
        "course_fee": "40,000~60,000",
        "receive_etime": "21:00",
        "status": status,
        "receive_kind": "10",
        "target_age_name": "성인/청소년",
        "sports_cd": "01",
        "train_day_nm": "월수금",
        "capa": "20",
        "reg_person": "3",
        "teacher_name": "미지정",
        "total_count": total,
        "category1": f"{branch} 프로그램",
        "category2": "정규 강습",
    }


def _landing_html() -> str:
    return """
    <html><head>
      <title>수강신청(교육/강좌 목록) &lt; 수강신청 : 시흥도시공사</title>
    </head><body>
      <script>
        var IS_SHOW_ALL = true;
        var COMPANY_CODE = '';
        var READ_LINK_URL = '?action=read';
      </script>
      <div class="modules_fmcs_lecture"><div class="proc_list">
        <form id="search"><input name="lecture_type" value=""></form>
        <div class="list_tab"><a data-value="R">신규접수</a><a data-value="E">접수종료</a></div>
      </div></div>
    </body></html>
    """


def _detail_html(
    item: dict[str, Any],
    *,
    fault: str = "",
    status_override: str = "",
    capacity_override: tuple[int, int] | None = None,
    include_phone: bool = False,
) -> str:
    company = item["comcd"]
    identity = item["class_cd"]
    title = item["class_nm"]
    branch = item["comnm"]
    status = status_override or item["status"]
    capacity = int(item["capa"])
    registered = int(item["reg_person"])
    if capacity_override is not None:
        capacity, registered = capacity_override
    if fault == "identity":
        identity = "99999"
    elif fault == "title":
        title = "다른 상세 강좌"
    elif fault == "company":
        branch = "다른 운영센터"
    elif fault == "status":
        status = "W"
    schedule = "10:00 ~ 10:50 / 월수금"
    if fault == "schedule":
        schedule = "11:00 ~ 11:50 / 화목"
    if fault == "capacity":
        capacity, registered = 2, 9
    venue = f"{branch} 강의실"
    if fault == "pii_venue":
        venue = "문의 010-1111-2222"
    center_contact = " / 031-123-4567" if include_phone else " /"
    action = "/fmcs/21" if fault == "form" else "?action=write"
    if status == "R" and fault != "control":
        control = (
            "<button type='button' class='button action_write' "
            "onclick=\"location.href='/fmcs/21?referer=x'\">수강신청</button>"
        )
    elif status == "E" and fault == "closed_control":
        control = "<button type='button'>수강신청</button>"
    else:
        control = ""
    return f"""
    <html><body><div class="modules_fmcs_lecture"><div class="proc_read">
      <form action="{action}">
        <input name="comcd" value="{company}">
        <input name="classcd" value="{identity}">
        <input name="type" value="R">
        <input name="status" value="{status}">
        <input name="SecurityToken" value="fixture-secret-token">
        <table><tbody>
          <tr><th>강좌명</th><td>{title}</td></tr>
          <tr><th>운영센터</th><td>{branch}{center_contact}</td></tr>
          <tr><th>교육장소</th><td>{venue}</td></tr>
          <tr><th>시간/요일</th><td>{schedule}</td></tr>
          <tr><th>교육대상</th><td>{item['target_age_name']}</td></tr>
          <tr><th>강사명</th><td>{item['teacher_name']}</td></tr>
          <tr><th>접수방식</th><td>선착접수</td></tr>
          <tr><th>정원/신청인원</th><td>{capacity} / {registered}</td></tr>
        </tbody></table>
        {control}
      </form>
    </div></div></body></html>
    """


class Backend:
    def __init__(self) -> None:
        self.companies = [
            {"comcd": code, "comnm": name}
            for code, name in sports.SIHEUNG_SPORTS_COMPANIES
        ]
        self.rows: dict[str, list[dict[str, Any]]] = {
            code: [] for code, _ in sports.SIHEUNG_SPORTS_COMPANIES
        }
        self.rows["SIHEUNG01"] = [
            _item("SIHEUNG01", 1, total=3, status="R"),
            _item("SIHEUNG01", 2, total=3, status="E"),
            _item("SIHEUNG01", 3, total=3, status="R"),
        ]
        for index, company in enumerate(
            ("SIHEUNG07", "SIHEUNG08", "SIHEUNG09", "SIHEUNG11", "SIHEUNG12", "SIHEUNG14"),
            4,
        ):
            self.rows[company] = [
                _item(
                    company,
                    index,
                    total=1,
                    status="R" if index % 2 == 0 else "E",
                )
            ]
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.sessions: list[FakeSession] = []
        self.page_hits: Counter[tuple[str, int]] = Counter()
        self.nonempty_sentinel_company = ""
        self.partial_company = ""
        self.mutate_edge: tuple[str, int] | None = None
        self.detail_faults: dict[tuple[str, str], str] = {}
        self.detail_status_overrides: dict[tuple[str, str], str] = {}
        self.detail_capacity_overrides: dict[tuple[str, str], tuple[int, int]] = {}
        self.fail_once: Counter[tuple[str, str]] = Counter()

    def session_factory(self) -> "FakeSession":
        session = FakeSession(self)
        self.sessions.append(session)
        return session

    def maybe_fail(self, method: str, url: str) -> None:
        key = (method, url)
        if self.fail_once[key] > 0:
            self.fail_once[key] -= 1
            raise TimeoutError("synthetic transient failure")


class FakeSession:
    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self.headers: dict[str, str] = {}
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.backend.calls.append(("GET", url, deepcopy(kwargs)))
        self.backend.maybe_fail("GET", url)
        assert kwargs["allow_redirects"] is False
        if url == sports.SIHEUNG_SPORTS_URL:
            return FakeResponse(url=url, text=_landing_html())
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == sports.SIHEUNG_SPORTS_HOST
        assert parsed.path == sports.SIHEUNG_SPORTS_PATH
        query = parse_qs(parsed.query)
        assert query["action"] == ["read"]
        assert query["type"] == [sports.SIHEUNG_SPORTS_SEARCH_TYPE]
        key = (query["comcd"][0], query["classcd"][0])
        item = next(
            row for row in self.backend.rows[key[0]] if row["class_cd"] == key[1]
        )
        return FakeResponse(
            url=url,
            text=_detail_html(
                item,
                fault=self.backend.detail_faults.get(key, ""),
                status_override=self.backend.detail_status_overrides.get(key, ""),
                capacity_override=self.backend.detail_capacity_overrides.get(key),
                include_phone=key == ("SIHEUNG01", "00001"),
            ),
        )

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.backend.calls.append(("POST", url, deepcopy(kwargs)))
        self.backend.maybe_fail("POST", url)
        assert kwargs["allow_redirects"] is False
        if url == sports.SIHEUNG_SPORTS_COMPANY_API:
            assert kwargs["data"] == {"type": "L"}
            return FakeResponse(url=url, payload=self.backend.companies)
        assert url == sports.SIHEUNG_SPORTS_LECTURE_API
        data = kwargs["data"]
        assert set(data) == {
            "company_code",
            "mem_no",
            "search_type",
            "category_cd",
            "category_level",
            "class_nm",
            "train_day",
            "adult_gubn",
            "lecturer_nm",
            "page",
            "page_size",
        }
        assert data["search_type"] == "R"
        assert data["category_level"] == "9"
        assert data["page_size"] == str(sports.SIHEUNG_SPORTS_PAGE_SIZE)
        company = data["company_code"]
        page = int(data["page"])
        self.backend.page_hits[(company, page)] += 1
        start = (page - 1) * sports.SIHEUNG_SPORTS_PAGE_SIZE
        end = start + sports.SIHEUNG_SPORTS_PAGE_SIZE
        payload = deepcopy(self.backend.rows[company][start:end])
        if company == self.backend.partial_company and page == 2:
            payload = []
        if (
            company == self.backend.nonempty_sentinel_company
            and start >= len(self.backend.rows[company])
        ):
            payload = deepcopy(self.backend.rows[company][-1:])
        if (
            self.backend.mutate_edge == (company, page)
            and self.backend.page_hits[(company, page)] >= 2
            and payload
        ):
            payload[0]["class_nm"] += " 변경"
        return FakeResponse(url=url, payload=payload)


def _collect(
    monkeypatch: pytest.MonkeyPatch,
    backend: Backend,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    monkeypatch.setattr(sports, "SIHEUNG_SPORTS_PAGE_SIZE", 2)
    options: dict[str, Any] = {
        "timeout": 7,
        "max_pages": 16,
        "detail_limit": 9,
        "max_requests": 36,
        "session_factory": backend.session_factory,
        "sleeper": lambda _seconds: None,
    }
    options.update(kwargs)
    return sports.collect_siheung_sports_courses(Target(), **options)


def test_provider_route_payload_and_owner_boundary_constants_are_exact() -> None:
    expected_hash = hashlib.sha1(sports.SIHEUNG_SPORTS_URL.encode("utf-8")).hexdigest()[:8].upper()
    assert sports.SIHEUNG_SPORTS_PROVIDER == f"MUNI_SPORTSAPP_SHSI_OR_KR_{expected_hash}"
    assert sports.SIHEUNG_SPORTS_AUDITED_CURRENT_TOTAL == 241
    assert sum(sports.SIHEUNG_SPORTS_AUDITED_COMPANY_TOTALS.values()) == 241
    assert len(sports.SIHEUNG_SPORTS_COMPANIES) == 8
    assert sports.SIHEUNG_SSOC_SEPARATE_OWNER_URL == (
        "https://siheung.gseek.kr/user/course/offline/list"
    )
    assert sports.is_siheung_sports_target(Target())
    assert not sports.is_siheung_sports_target(Target(provider="WRONG"))
    for url in (
        "http://sportsapp.shsi.or.kr/fmcs/3",
        "https://sportsapp.shsi.or.kr:443/fmcs/3",
        sports.SIHEUNG_SPORTS_URL + "?page=1",
        sports.SIHEUNG_SPORTS_URL + "#lecture_R",
        "https://user@sportsapp.shsi.or.kr/fmcs/3",
        "https://sportsapp.shsi.or.kr/fmcs/3/../21",
    ):
        assert not sports.is_siheung_sports_target(Target(url=url))

    detail = sports.siheung_sports_detail_url("SIHEUNG09", "00021")
    parsed = urlparse(detail)
    assert parsed.netloc == sports.SIHEUNG_SPORTS_HOST
    assert parse_qs(parsed.query) == {
        "action": ["read"],
        "comcd": ["SIHEUNG09"],
        "classcd": ["00021"],
        "type": ["R"],
    }
    assert sports.siheung_sports_detail_url("UNKNOWN", "00021") == ""
    assert sports.siheung_sports_detail_url("SIHEUNG09", "../21") == ""
    payload = sports.siheung_sports_list_payload("SIHEUNG09", 2)
    assert payload["company_code"] == "SIHEUNG09"
    assert payload["search_type"] == "R"
    assert payload["page"] == "2"
    assert payload["page_size"] == "50"
    assert sports.siheung_sports_list_payload("UNKNOWN", 1) == {}


def test_complete_company_pages_sentinels_details_and_pii_minimization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = Backend()
    rows, parser, meta = _collect(monkeypatch, backend)

    assert parser == sports.SIHEUNG_SPORTS_PARSER
    assert len(rows) == 9
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_total"] == meta["source_rows"] == 9
    assert meta["discovered_links"] == 9
    assert meta["pagination_detected"] is True
    assert meta["no_current_data"] is False and meta["no_current_reason"] == ""
    assert meta["current_count"] == meta["returned_count"] == 9
    assert meta["official_company_count"] == meta["company_count"] == 8
    assert meta["active_company_count"] == 7
    assert meta["empty_company_count"] == 1
    assert meta["pages"] == meta["required_page_requests"] == 16
    assert meta["sentinel_requests"] == 8
    assert meta["sentinel_kind"] == "per_company_immediate_empty"
    assert meta["stability_rechecks"] == meta["required_edge_rechecks"] == 9
    assert meta["list_requests"] == 25
    assert meta["required_logical_requests"] == meta["physical_requests"] == 36
    assert meta["request_method_counts"] == {"GET": 10, "POST": 26}
    assert meta["detail_attempts"] == meta["detail_pages"] == 9
    assert meta["detail_errors"] == 0
    assert meta["company_data_pages"]["SIHEUNG01"] == 2
    assert meta["company_data_pages"]["SIHEUNG02"] == 0
    assert meta["company_sentinel_pages"]["SIHEUNG01"] == 3
    assert meta["company_sentinel_pages"]["SIHEUNG02"] == 1
    assert meta["page_counts"]["SIHEUNG01"] == {1: 2, 2: 1, 3: 0}
    assert meta["page_counts"]["SIHEUNG02"] == {1: 0}
    assert meta["first_identity"] == "SIHEUNG01:00001"
    assert meta["last_identity"] == "SIHEUNG14:00009"
    assert meta["source_identity_sha256"] == meta["output_identity_sha256"]
    assert meta["branch_count"] == 7
    assert meta["branch_counts"]["[하중]시흥국민체육센터"] == 3
    assert meta["pii_omission_count"] == 1
    assert meta["application_form_discovery_count"] == 9
    assert meta["application_control_count"] == meta["source_status_counts"]["R"]
    assert meta["reservation_discovery_links"] == meta["status_counts"]["OPEN"]
    assert meta["application_endpoints_called"] == 0
    assert meta["login_endpoints_called"] == 0
    assert meta["attachment_endpoints_called"] == 0
    assert meta["pii_endpoints_called"] == 0

    assert len({row["provider_course_id"] for row in rows}) == 9
    for row in rows:
        raw = row["raw_fields"]
        assert row["provider"] == sports.SIHEUNG_SPORTS_PROVIDER
        assert row["branch"] == sports.SIHEUNG_SPORTS_COMPANY_NAMES[
            raw["official_company_code"]
        ]
        assert row["municipality_code"] == "4139000000"
        assert row["municipality_full_name"] == "경기도 시흥시"
        assert row["domain_category"] == "교육·강좌"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["preserve_branch"] is True
        assert row["venue_name"]
        assert raw["detail_identity_verified"] is True
        assert raw["detail_company_verified"] is True
        assert raw["application_endpoint_called"] is False
        if row["status"] == "OPEN":
            assert row["application_url"] == row["raw_url"]
            assert row["reservation_available"] is True
        else:
            assert "application_url" not in row
            assert row["reservation_available"] is False

    persisted = json.dumps(rows, ensure_ascii=False)
    assert "031-123-4567" not in persisted
    assert "fixture-secret-token" not in persisted
    assert "detail_pairs" not in persisted
    assert "SecurityToken" not in persisted
    allowed_post = {
        sports.SIHEUNG_SPORTS_COMPANY_API,
        sports.SIHEUNG_SPORTS_LECTURE_API,
    }
    for method, url, _kwargs in backend.calls:
        if method == "POST":
            assert url in allowed_post
        else:
            parsed = urlparse(url)
            assert parsed.path == sports.SIHEUNG_SPORTS_PATH
            assert parse_qs(parsed.query).get("action", ["read"])[0] == "read"
        assert "action=write" not in url
        assert urlparse(url).path not in {
            "/fmcs/21",
            "/rest/member/process_residence_in_out",
            "/tools/gov_discount.jsp",
        }
    assert backend.sessions and all(session.closed for session in backend.sessions)


def test_company_name_order_or_count_drift_fails_before_lecture_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mutate in ("name", "order", "missing"):
        backend = Backend()
        if mutate == "name":
            backend.companies[0]["comnm"] += " 변경"
        elif mutate == "order":
            backend.companies[0], backend.companies[1] = (
                backend.companies[1],
                backend.companies[0],
            )
        else:
            backend.companies.pop()
        rows, _parser, meta = _collect(monkeypatch, backend)
        assert rows == []
        assert meta["snapshot_complete"] is False
        assert "eight-company" in meta["configured_collection_error"]
        assert all(call[1] != sports.SIHEUNG_SPORTS_LECTURE_API for call in backend.calls)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_pages": 15}, "max_pages cap"),
        ({"detail_limit": 8}, "detail_limit cap"),
        ({"max_requests": 35}, "max_requests cap"),
    ],
)
def test_caps_fail_closed_before_details(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, int],
    message: str,
) -> None:
    backend = Backend()
    rows, _parser, meta = _collect(monkeypatch, backend, **kwargs)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert message in meta["configured_collection_error"]


@pytest.mark.parametrize("mode", ["partial", "sentinel"])
def test_partial_page_or_nonempty_immediate_sentinel_fails_atomic_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    backend = Backend()
    if mode == "partial":
        backend.partial_company = "SIHEUNG01"
    else:
        backend.nonempty_sentinel_company = "SIHEUNG01"
    rows, _parser, meta = _collect(monkeypatch, backend)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert meta["detail_attempts"] == 0
    assert (
        "expected" in meta["configured_collection_error"]
        or "sentinel" in meta["configured_collection_error"]
    )


@pytest.mark.parametrize("edge", [("SIHEUNG01", 1), ("SIHEUNG01", 2), ("SIHEUNG02", 1)])
def test_first_last_or_empty_company_edge_change_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    edge: tuple[str, int],
) -> None:
    backend = Backend()
    backend.mutate_edge = edge
    if edge[0] == "SIHEUNG02":
        # The empty payload has no row to mutate; replace the page-one sentinel
        # only on its recheck.
        original_post = FakeSession.post

        def post_with_empty_mutation(self: FakeSession, url: str, **kwargs: Any) -> FakeResponse:
            response = original_post(self, url, **kwargs)
            if url == sports.SIHEUNG_SPORTS_LECTURE_API:
                data = kwargs["data"]
                key = (data["company_code"], int(data["page"]))
                if key == edge and self.backend.page_hits[key] >= 2:
                    payload = deepcopy(self.backend.rows["SIHEUNG01"][:1])
                    payload[0]["comcd"] = "SIHEUNG02"
                    payload[0]["comnm"] = sports.SIHEUNG_SPORTS_COMPANY_NAMES["SIHEUNG02"]
                    return FakeResponse(url=url, payload=payload)
            return response

        monkeypatch.setattr(FakeSession, "post", post_with_empty_mutation)
    rows, _parser, meta = _collect(monkeypatch, backend)
    assert rows == []
    assert meta["detail_attempts"] == 0
    assert "signature changed" in meta["configured_collection_error"]


def test_duplicate_identity_semantic_duplicate_and_test_title_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate = Backend()
    duplicate.rows["SIHEUNG01"][2]["class_cd"] = duplicate.rows["SIHEUNG01"][1][
        "class_cd"
    ]
    rows, _parser, meta = _collect(monkeypatch, duplicate)
    assert rows == []
    assert meta["duplicate_count"] == 1
    assert meta["detail_attempts"] == 0

    semantic = Backend()
    first = semantic.rows["SIHEUNG01"][0]
    second = semantic.rows["SIHEUNG01"][1]
    for field in (
        "class_nm",
        "train_stime",
        "train_etime",
        "train_day_nm",
        "target_age_name",
    ):
        second[field] = first[field]
    rows, _parser, meta = _collect(monkeypatch, semantic)
    assert rows == []
    assert meta["semantic_duplicate_count"] == 1
    assert meta["detail_attempts"] == 0

    test_row = Backend()
    test_row.rows["SIHEUNG14"][0]["class_nm"] = "수집 테스트"
    rows, _parser, meta = _collect(monkeypatch, test_row)
    assert rows == []
    assert meta["malformed_count"] >= 1
    assert "test/sample" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "W", "status left"),
        ("receive_kind", "20", "receive_kind"),
        ("train_stime", "25:99", "train_stime"),
        ("capa", "bad", "capacity"),
        ("total_count", 99, "total_count"),
        ("category2", "", "category2"),
        ("target_age_name", "문의 010-1111-2222", "contains PII"),
    ],
)
def test_malformed_list_contract_fails_before_details(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
    message: str,
) -> None:
    backend = Backend()
    if field == "total_count":
        backend.rows["SIHEUNG01"][2][field] = value
    else:
        backend.rows["SIHEUNG07"][0][field] = value
    rows, _parser, meta = _collect(monkeypatch, backend)
    assert rows == []
    assert meta["detail_attempts"] == 0
    assert message in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "fault",
    [
        "identity",
        "title",
        "company",
        "status",
        "schedule",
        "capacity",
        "form",
        "control",
        "pii_venue",
    ],
)
def test_any_detail_identity_application_or_pii_fault_discards_all_rows(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    backend = Backend()
    backend.detail_faults[("SIHEUNG01", "00001")] = fault
    rows, _parser, meta = _collect(monkeypatch, backend)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["details_complete"] is False
    assert meta["detail_attempts"] == 9
    assert meta["detail_errors"] >= 1
    assert "SIHEUNG01/00001" in meta["configured_collection_error"]


def test_closed_detail_cannot_expose_application_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = Backend()
    backend.detail_faults[("SIHEUNG01", "00002")] = "closed_control"
    rows, _parser, meta = _collect(monkeypatch, backend)
    assert rows == []
    assert "closed course unexpectedly exposes" in meta["configured_collection_error"]


def test_volatile_status_and_registration_counts_refresh_from_newer_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = Backend()
    key = ("SIHEUNG01", "00002")
    backend.detail_status_overrides[key] = "R"
    backend.detail_capacity_overrides[key] = (20, 7)
    rows, _parser, meta = _collect(monkeypatch, backend)

    assert meta["snapshot_complete"] is True
    assert len(rows) == 9
    refreshed = next(
        row
        for row in rows
        if row["raw_fields"]["official_company_code"] == key[0]
        and row["raw_fields"]["official_class_code"] == key[1]
    )
    assert refreshed["raw_fields"]["source_status"] == "E"
    assert refreshed["raw_fields"]["detail_status"] == "R"
    assert refreshed["raw_fields"]["detail_status_refreshed"] is True
    assert refreshed["raw_fields"]["detail_capacity_refreshed"] is True
    assert refreshed["status"] == "OPEN"
    assert refreshed["capacity_current"] == 7
    assert refreshed["application_url"] == refreshed["raw_url"]
    assert meta["detail_status_refresh_count"] == 1
    assert meta["detail_capacity_refresh_count"] == 1
    assert meta["application_control_count"] == meta["status_counts"]["OPEN"]


def test_transient_failure_retries_with_a_new_managed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = Backend()
    backend.fail_once[("GET", sports.SIHEUNG_SPORTS_URL)] = 1
    rows, _parser, meta = _collect(monkeypatch, backend, max_requests=37)
    assert len(rows) == 9
    assert meta["snapshot_complete"] is True
    assert meta["retry_count"] == 1
    assert meta["physical_requests"] == 37
    assert meta["sessions_created"] == 2
    assert all(session.closed for session in backend.sessions)


def test_managed_session_invalid_route_and_external_dedupe_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _parser, meta = sports.collect_siheung_sports_courses(Target())
    assert rows == []
    assert "managed session_factory" in meta["configured_collection_error"]

    backend = Backend()
    rows, _parser, meta = sports.collect_siheung_sports_courses(
        Target(provider="WRONG"),
        session_factory=backend.session_factory,
    )
    assert rows == []
    assert backend.calls == []
    assert "canonical Siheung sports route" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        monkeypatch,
        Backend(),
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SIHEUNG_SPORTS") != "1",
    reason="set RUN_LIVE_SIHEUNG_SPORTS=1 for the exact 2026-07-23 two-run audit",
)
def test_exact_live_siheung_sports_snapshot_is_stable_twice_20260723() -> None:
    snapshots: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for _attempt in range(2):
        rows, parser, meta = sports.collect_siheung_sports_courses(
            Target(),
            timeout=40,
            max_pages=32,
            detail_limit=300,
            max_requests=400,
            allow_raw_requests_for_tests=True,
        )
        assert parser == sports.SIHEUNG_SPORTS_PARSER
        assert meta["configured_collection_error"] == ""
        assert meta["snapshot_complete"] is True
        assert meta["pagination_complete"] is True
        assert meta["details_complete"] is True
        assert len(rows) == meta["source_total"] == 241
        assert meta["official_company_count"] == 8
        assert meta["active_company_count"] == 7
        assert meta["empty_company_count"] == 1
        assert meta["company_totals"] == dict(
            sports.SIHEUNG_SPORTS_AUDITED_COMPANY_TOTALS
        )
        assert meta["company_data_pages"] == {
            "SIHEUNG01": 2,
            "SIHEUNG02": 0,
            "SIHEUNG07": 1,
            "SIHEUNG08": 1,
            "SIHEUNG09": 1,
            "SIHEUNG11": 1,
            "SIHEUNG12": 1,
            "SIHEUNG14": 1,
        }
        assert meta["company_sentinel_pages"] == {
            "SIHEUNG01": 3,
            "SIHEUNG02": 1,
            "SIHEUNG07": 2,
            "SIHEUNG08": 2,
            "SIHEUNG09": 2,
            "SIHEUNG11": 2,
            "SIHEUNG12": 2,
            "SIHEUNG14": 2,
        }
        assert meta["pages"] == 16
        assert meta["sentinel_requests"] == 8
        assert meta["stability_rechecks"] == 9
        assert meta["list_requests"] == 25
        assert meta["detail_attempts"] == meta["detail_pages"] == 241
        assert meta["required_logical_requests"] == 268
        assert meta["physical_requests"] == 268
        assert meta["request_method_counts"] == {"GET": 242, "POST": 26}
        assert meta["sessions_created"] == 3
        assert meta["branch_counts"] == {
            "[하중]시흥국민체육센터": 75,
            "시흥능곡어울림센터": 42,
            "[정왕]시흥어울림국민체육센터": 44,
            "장곡동생활체육시설": 13,
            "다니생활체육관": 2,
            "장곡문화체육센터": 30,
            "목감2어울림센터": 35,
        }
        assert meta["branch_count"] == 7
        assert meta["venue_count"] == 9
        assert meta["pii_omission_count"] == 190
        assert meta["duplicate_count"] == 0
        assert meta["duplicate_url_count"] == 0
        assert meta["semantic_duplicate_count"] == 0
        assert meta["application_control_count"] == meta["status_counts"]["OPEN"]
        assert meta["reservation_discovery_links"] == meta["status_counts"]["OPEN"]
        assert sum(meta["status_counts"].values()) == 241
        assert meta["application_endpoints_called"] == 0
        assert meta["login_endpoints_called"] == 0
        assert meta["attachment_endpoints_called"] == 0
        assert meta["pii_endpoints_called"] == 0
        snapshots.append((rows, meta))

    first_rows, first_meta = snapshots[0]
    second_rows, second_meta = snapshots[1]
    assert [row["provider_course_id"] for row in first_rows] == [
        row["provider_course_id"] for row in second_rows
    ]
    assert first_meta["output_identity_sha256"] == second_meta["output_identity_sha256"]
    assert first_meta["edge_signatures"] == second_meta["edge_signatures"]
