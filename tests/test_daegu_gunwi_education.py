from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_daegu_gunwi as gunwi


@dataclass(frozen=True)
class Target:
    provider: str = gunwi.GUNWI_EDUCATION_PROVIDER
    url: str = gunwi.GUNWI_EDUCATION_CANONICAL_URL


class FakeResponse:
    def __init__(
        self,
        url: str,
        text: str,
        *,
        status_code: int = 200,
        history: list[object] | None = None,
    ) -> None:
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.history = history or []
        self.headers = {"content-type": "text/html; charset=utf-8"}


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _card(
    identity: str,
    title: str,
    *,
    partition: str,
    period: str,
    apply_period: str,
    hours: str = "12 시간",
    capacity: int = 20,
) -> str:
    config = gunwi._PARTITIONS[partition]
    detail_flag = "Y" if partition == "ing" else "N"
    button = config["tab"]
    current = "<dl><dt>현재 접수</dt><dd>3 명</dd></dl>" if partition == "ing" else ""
    return f"""
      <li><div class="cont"><div class="tit"><a
        href="javascript:DetailView('{identity}','{detail_flag}');">{escape(title)}</a></div>
        <div class="sm_box">
          <dl><dt>접수기간</dt><dd>{apply_period}</dd></dl>
          <dl><dt>운영기간</dt><dd>{period}</dd></dl>
          <dl><dt>총교육시간</dt><dd>{hours}</dd></dl>
          <dl><dt>모집인원</dt><dd>{capacity}명</dd></dl>{current}
        </div><div class="not-public">SECRET_LIST_INSTRUCTOR 010-1111-2222</div>
      </div><div class="btn_box"><a
        href="javascript:page_link('{identity}','{config['message']}');">
        {button}</a></div></li>
    """


def _list_page(
    partition: str,
    *,
    drift: bool = False,
    duplicate_identity: bool = False,
    bad_empty: bool = False,
    pagination: bool = False,
) -> str:
    tabs = []
    for key, config in gunwi._PARTITIONS.items():
        selected = " btn_sel" if key == partition else ""
        tabs.append(
            f'<span class="btn_mg{selected}"><a href="?view={key}">{config["tab"]}</a></span>'
        )
    if partition == "ing":
        body = _card(
            "2099-001",
            "미래 농업인 AI 교육",
            partition=partition,
            period="2099-08-01 ~ 2099-08-31",
            apply_period="2099-07-01 ~ 2099-07-31",
        )
    elif partition == "ready":
        identity = "2099-001" if duplicate_identity else "2099-002"
        body = _card(
            identity,
            "미래 귀농 교육",
            partition=partition,
            period="2099-09-01 ~ 2099-09-30",
            apply_period="2099-08-01 ~ 2099-08-20",
        )
    else:
        title = "변경된 진행 교육" if drift else "진행 중 마감 교육"
        body = _card(
            "2099-003",
            title,
            partition=partition,
            period="2099-06-01 ~ 2099-07-31",
            apply_period="2099-05-01 ~ 2099-05-20",
        )
        body += _card(
            "2020-001",
            "종료된 농업 교육",
            partition=partition,
            period="2020-01-01 ~ 2020-02-01",
            apply_period=gunwi._UNKNOWN_DATE_RANGE,
        )
    if bad_empty:
        body = '<p class="no_img"><img src="/img/no.gif" alt="잘못된 빈 목록"></p>'
    paging = '<div class="pagination"><a href="?view=end&page=2">2</a></div>' if pagination else ""
    return f"""
      <html><head><title>{gunwi._LIST_TITLE}</title></head><body>
        <header>{gunwi._LIST_TITLE}</header>{''.join(tabs)}
        <div class="lesson"><ul>{body}{paging}</ul></div>
        <footer>(우)43126 {gunwi._OWNERSHIP_ADDRESS}</footer>
      </body></html>
    """


def _empty_list_page(partition: str, *, wrong: bool = False) -> str:
    tabs = []
    for key, config in gunwi._PARTITIONS.items():
        selected = " btn_sel" if key == partition else ""
        tabs.append(
            f'<span class="btn_mg{selected}"><a href="?view={key}">{config["tab"]}</a></span>'
        )
    alt = "wrong" if wrong else gunwi._PARTITIONS[partition]["empty"]
    return f"""
      <html><head><title>{gunwi._LIST_TITLE}</title></head><body>
        <header>{gunwi._LIST_TITLE}</header>{''.join(tabs)}
        <div class="lesson"><ul><p class="no_img"><img alt="{alt}"></p></ul></div>
        <footer>{gunwi._OWNERSHIP_ADDRESS}</footer>
      </body></html>
    """


_DETAILS = {
    "2099-001": ("미래 농업인 AI 교육", "2099-08-01 ~ 2099-08-31"),
    "2099-002": ("미래 귀농 교육", "2099-09-01 ~ 2099-09-30"),
    "2099-003": ("진행 중 마감 교육", "2099-06-01 ~ 2099-07-31"),
    "2020-001": ("종료된 농업 교육", "2020-01-01 ~ 2020-02-01"),
}


def _detail_page(
    identity: str,
    *,
    wrong_title: bool = False,
    wrong_period: bool = False,
    private_surface: bool = False,
) -> str:
    title, period = _DETAILS[identity]
    if wrong_title:
        title = "다른 교육"
    if wrong_period:
        period = "2099-01-01 ~ 2099-01-02"
    private = '<form><input name="phone" value="SECRET_APPLICANT"></form>' if private_surface else ""
    return f"""
      <html><head><title>{gunwi._DETAIL_TITLE}</title></head><body>
        <div class="title"></div><div class="sub_tit">{escape(title)}</div>
        <div class="list2">
          <dl><dt>교 육 명</dt><dd>{escape(title)}</dd></dl>
          <dl><dt>교육기간</dt><dd>{period}</dd></dl>
          <dl><dt>교육시간</dt><dd>12 시간</dd></dl>
        </div>
        <div class="list1">
          <dl><dt>교육대상 및 인원</dt><dd>군위군민 20명</dd></dl>
          <dl><dt>교육장소</dt><dd>농업기술센터 교육장</dd></dl>
          <dl><dt>교육신청방법</dt><dd>온라인</dd></dl>
          <dl><dt>과정소개</dt><dd>공개 농업 교육 과정</dd></dl>
          <dl><dt>붙임파일</dt><dd>SECRET_ATTACHMENT.hwp</dd></dl>
        </div>
        <div class="free-form">SECRET_FREE_FORM private@example.test 010-2222-3333</div>
        {private}
      </body></html>
    """


class Backend:
    def __init__(
        self,
        *,
        probe_drift: bool = False,
        stable_drift: bool = False,
        duplicate_identity: bool = False,
        wrong_empty: bool = False,
        pagination: bool = False,
        wrong_detail_title: bool = False,
        wrong_detail_period: bool = False,
        private_detail: bool = False,
        missing_detail: bool = False,
        retry_once: bool = False,
    ) -> None:
        self.probe_drift = probe_drift
        self.stable_drift = stable_drift
        self.duplicate_identity = duplicate_identity
        self.wrong_empty = wrong_empty
        self.pagination = pagination
        self.wrong_detail_title = wrong_detail_title
        self.wrong_detail_period = wrong_detail_period
        self.private_detail = private_detail
        self.missing_detail = missing_detail
        self.retry_once = retry_once
        self.failed_once = False
        self.urls: list[str] = []
        self.base_partition_calls = {key: 0 for key in gunwi._PARTITIONS}
        self.session_value = FakeSession()

    def session(self) -> FakeSession:
        return self.session_value

    def fetch(self, _session: Any, url: str, _timeout: int) -> FakeResponse:
        self.urls.append(url)
        if self.retry_once and not self.failed_once:
            self.failed_once = True
            return FakeResponse(url, "temporary", status_code=503)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == gunwi.GUNWI_EDUCATION_LIST_PATH:
            partition = (query.get("view") or [""])[0]
            if partition not in gunwi._PARTITIONS:
                return FakeResponse(url, "missing", status_code=404)
            is_probe = (query.get("page") or [""])[0] == "2"
            if not is_probe:
                self.base_partition_calls[partition] += 1
            drift = bool(
                partition == "end"
                and (
                    (self.probe_drift and is_probe)
                    or (self.stable_drift and self.base_partition_calls[partition] >= 2)
                )
            )
            if partition in {"ing", "ready"} and self.wrong_empty:
                html = _empty_list_page(partition, wrong=True)
            else:
                html = _list_page(
                    partition,
                    drift=drift,
                    duplicate_identity=self.duplicate_identity,
                    pagination=self.pagination and partition == "end",
                )
            return FakeResponse(url, html)
        if parsed.path == gunwi.GUNWI_EDUCATION_DETAIL_PATH:
            identity = (query.get("mng_no") or [""])[0]
            if self.missing_detail and identity == "2099-003":
                return FakeResponse(url, "missing", status_code=404)
            if identity not in _DETAILS:
                return FakeResponse(url, "missing", status_code=404)
            return FakeResponse(
                url,
                _detail_page(
                    identity,
                    wrong_title=self.wrong_detail_title and identity == "2099-003",
                    wrong_period=self.wrong_detail_period and identity == "2099-003",
                    private_surface=self.private_detail and identity == "2099-003",
                ),
            )
        return FakeResponse(url, "missing", status_code=404)


def _collect(backend: Backend, **kwargs: Any):
    return gunwi.collect_gunwi_education(
        Target(),
        timeout=1,
        max_pages=kwargs.pop("max_pages", 1),
        detail_limit=kwargs.pop("detail_limit", 10),
        today=kwargs.pop("today", "2099-07-01"),
        fetch_attempts=kwargs.pop("fetch_attempts", 1),
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        **kwargs,
    )


def test_exact_target_ids_urls_and_owner_boundaries() -> None:
    assert gunwi.GUNWI_EDUCATION_PROVIDER == "MUNI_EDU_GWA_GO_KR_08B25674"
    assert gunwi.GUNWI_EDUCATION_CANDIDATE_ID == "MUNI_IR_3D1A86E912D5"
    assert gunwi.is_target(Target()) is True
    assert gunwi.is_target(Target(provider="WRONG")) is False
    assert gunwi.is_target(Target(url=gunwi.GUNWI_EDUCATION_CANONICAL_URL + "&x=1")) is False
    assert gunwi.gunwi_detail_url("2099-001").endswith("mng_no=2099-001&btn_dp=N")
    assert gunwi.gunwi_detail_url("bad") == ""
    assert (
        gunwi.GUNWI_EDUCATION_OWNER_BOUNDARY_AUDIT["DAEGU_CITY_EDUCATION"]["decision"]
        == "exclude_separate_city_aggregate"
    )
    assert (
        gunwi.GUNWI_EDUCATION_OWNER_BOUNDARY_AUDIT["GUNWI_THEME_PARK"]["decision"]
        == "exclude_tourism_experience_owner"
    )


def test_complete_partitions_probes_rechecks_details_current_and_privacy() -> None:
    backend = Backend()
    rows, parser, meta = _collect(backend)

    assert parser == gunwi.GUNWI_EDUCATION_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["partition_counts"] == {"ing": 1, "ready": 1, "end": 2}
    assert meta["source_total"] == meta["source_rows"] == 4
    assert meta["pages"] == meta["list_pages"] == 3
    assert meta["required_list_requests"] == 9
    assert meta["pagination_probe_requests"] == 3
    assert meta["stability_rechecks"] == 3
    assert meta["detail_attempts"] == meta["detail_pages"] == 4
    assert meta["network_requests"] == 13
    assert meta["current_count"] == meta["returned_count"] == 3
    assert meta["expired_count"] == 1
    assert meta["status_counts"] == {"OPEN": 1, "SCHEDULED": 1, "CLOSED": 1}
    assert meta["application_control_count"] == 1
    assert meta["pagination_detected"] is False
    assert meta["nonpagination_probes_complete"] is True
    assert meta["stable_recheck_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_pages_requested"] == 0
    assert meta["identity_verification_pages_requested"] == 0
    assert meta["pii_payload_persisted"] is False
    assert backend.session_value.closed is True

    assert [row["raw_fields"]["course_identity"] for row in rows] == [
        "2099-001", "2099-002", "2099-003"
    ]
    assert all(row["municipality_code"] == "2772000000" for row in rows)
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_url"].startswith(
        "https://edu.gwa.go.kr/application/edu_application.php?"
    )
    assert rows[1]["application_url"] == rows[2]["application_url"] == ""
    rendered = repr(rows)
    for secret in (
        "SECRET_LIST_INSTRUCTOR",
        "SECRET_ATTACHMENT",
        "SECRET_FREE_FORM",
        "private@example.test",
        "010-1111-2222",
        "010-2222-3333",
    ):
        assert secret not in rendered
    assert not any(
        urlparse(url).path == gunwi.GUNWI_EDUCATION_APPLICATION_PATH
        for url in backend.urls
    )


@pytest.mark.parametrize(
    ("flag", "needle"),
    (
        ("probe_drift", "non-pagination probe changed"),
        ("stable_drift", "stable recheck"),
        ("duplicate_identity", "duplicate official identity"),
        ("wrong_empty", "empty sentinel mismatch"),
        ("pagination", "pagination controls"),
        ("wrong_detail_title", "detail heading mismatch"),
        ("wrong_detail_period", "detail period mismatch"),
        ("private_detail", "private/application surface"),
        ("missing_detail", "unexpected http 404"),
    ),
)
def test_any_contract_failure_discards_the_whole_snapshot(flag: str, needle: str) -> None:
    rows, _parser, meta = _collect(Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert needle in meta["configured_collection_error"].casefold()


def test_caps_dedupe_retry_and_wrong_target_fail_closed() -> None:
    rows, _parser, meta = _collect(Backend(), detail_limit=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        Backend(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]

    backend = Backend(retry_once=True)
    rows, _parser, meta = _collect(backend, fetch_attempts=2)
    assert len(rows) == 3
    assert meta["network_retry_count"] == 1
    assert meta["network_requests"] == 14

    backend = Backend()
    rows, _parser, meta = gunwi.collect_gunwi_education(
        Target(provider="WRONG"),
        fetcher=backend.fetch,
        session_factory=backend.session,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact canonical" in meta["configured_collection_error"]


def test_explicit_no_current_snapshot_is_success_not_scrape_failure() -> None:
    backend = Backend()
    rows, _parser, meta = _collect(backend, today="2100-01-01")
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 4
    assert meta["detail_pages"] == 4
    assert meta["current_count"] == meta["returned_count"] == 0
    assert meta["expired_count"] == 4
    assert meta["no_current_data"] is True
    assert "every course in the complete official status partitions" in meta["no_current_reason"]


@pytest.mark.skipif(
    os.getenv("MOONCEN_RUN_DAEGU_GUNWI_LIVE") != "1",
    reason="set MOONCEN_RUN_DAEGU_GUNWI_LIVE=1 for the exact 21-request audit",
)
def test_live_exact_snapshot_matches_2026_07_22_audit() -> None:
    rows, parser, meta = gunwi.collect_gunwi_education(
        Target(),
        timeout=30,
        max_pages=1,
        detail_limit=20,
        today="2026-07-22",
    )
    assert parser == gunwi.GUNWI_EDUCATION_PARSER
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["partition_counts"] == {"ing": 0, "ready": 0, "end": 12}
    assert meta["source_total"] == 12
    assert meta["partition_empty_sentinels"] == 2
    assert meta["required_list_requests"] == 9
    assert meta["pagination_probe_requests"] == 3
    assert meta["stability_rechecks"] == 3
    assert meta["detail_pages"] == 12
    assert meta["network_requests"] == 21
    assert meta["current_count"] == meta["returned_count"] == 0
    assert meta["expired_count"] == 12
    assert meta["no_current_data"] is True
