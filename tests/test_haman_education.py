from __future__ import annotations

from collections import Counter
import hashlib
import html
import os
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from Crawler import municipal_haman as haman


class _Response:
    def __init__(
        self,
        url: str,
        body: str,
        status_code: int = 200,
        *,
        redirected: bool = False,
    ) -> None:
        self.url = url
        self.content = body.encode("utf-8")
        self.status_code = status_code
        self.history = [object()] if redirected else []


class _Session:
    def __init__(self, scenario: "_Scenario") -> None:
        self.scenario = scenario
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target() -> dict[str, str]:
    return {"provider": haman.HAMAN_PROVIDER, "url": haman.HAMAN_CANONICAL_URL}


def _record(
    identity: str,
    route: haman.HamanRoute,
    branch: str,
    *,
    current: bool,
    title: str,
    event_period: str | None = None,
    status: str = "접수완료",
    fee: str = "무료",
    application_control: bool = False,
    attachment: bool = False,
    teacher: bool = False,
) -> dict[str, object]:
    if event_period is None:
        event_period = (
            "2026.08.01~2026.08.31"
            if current
            else "2025.01.01~2025.02.01"
        )
    return {
        "identity": identity,
        "route": route.key,
        "branch": branch,
        "title": title,
        "status": status,
        "fee": fee,
        "event_period": event_period,
        "apply_period": "2026.06.01~2026.06.30" if current else "2024.12.01~2024.12.31",
        "weekdays": "월,수",
        "application_method": "인터넷,방문",
        "time": "10:00~12:00",
        "selection_method": "선착순",
        "room": "교육실 1",
        "capacity_current": 2,
        "capacity_total": 10,
        "category": "문화예술교육",
        "material_fee": "없음",
        "application_control": application_control,
        "attachment": attachment,
        "teacher": teacher,
    }


def _records() -> dict[str, list[dict[str, object]]]:
    lifelong = haman.HAMAN_ROUTE_BY_KEY["lifelong"]
    digital = haman.HAMAN_ROUTE_BY_KEY["digital"]
    welfare = haman.HAMAN_ROUTE_BY_KEY["social_welfare"]
    women = haman.HAMAN_ROUTE_BY_KEY["women"]
    rows = [
        _record(
            "9001",
            lifelong,
            "평생학습센터",
            current=True,
            title="현재 평생교육 1",
            attachment=True,
            teacher=True,
        ),
        _record(
            "9000",
            lifelong,
            "평생학습센터",
            current=True,
            title="현재 평생교육 2",
        ),
    ]
    rows.extend(
        _record(
            str(8999 - number),
            lifelong,
            "함안군평생교육원",
            current=False,
            title=f"과거 평생교육 {number + 1:02d}",
        )
        for number in range(18)
    )
    rows.append(
        _record(
            "1648",
            lifelong,
            "함안군평생교육원",
            current=False,
            title="기간 미상 과거 강좌",
            event_period="~",
        )
    )
    return {
        "lifelong": rows,
        "digital": [
            _record(
                "7001",
                digital,
                "군민정보화교육",
                current=False,
                title="과거 정보화교육",
            )
        ],
        "social_welfare": [
            _record(
                "6001",
                welfare,
                "종합사회복지관",
                current=True,
                title="현재 복지관 교육",
                status="강좌중",
                fee="40,000",
                application_control=True,
            )
        ],
        "women": [
            _record(
                "5001",
                women,
                "여성센터",
                current=True,
                title="종료일 당일 여성교육",
                event_period="2026.07.01~2026.07.23",
            )
        ],
        "literature": [],
    }


def _options(
    registry: tuple[tuple[str, str], ...],
    *,
    selected: str = "",
    drift: bool = False,
) -> str:
    values = registry[:-1] if drift else registry
    return "".join(
        f'<option value="{html.escape(value, quote=True)}"'
        + (" selected" if value == selected else "")
        + f">{html.escape(label)}</option>"
        for value, label in values
    )


def _form(
    requested_url: str,
    *,
    target_value: str = "",
    duplicate_hidden: bool = False,
    option_drift: bool = False,
) -> str:
    parsed = urlparse(requested_url)
    action = html.escape(parsed.path + "?" + parsed.query, quote=True)
    duplicate = '<input type="hidden" name="cpage" value="1">' if duplicate_hidden else ""
    return f"""
    <form id="listForm" method="get" action="{action}">
      <input type="hidden" name="cpage" value="1">
      <input type="hidden" name="stype" value="">{duplicate}
      <input type="text" name="sstring" value="">
      <select name="lecState">{_options(haman._STATE_OPTIONS, drift=option_drift)}</select>
      <select name="lecDivLvl1">{_options(haman._DIVISION_OPTIONS)}</select>
      <select name="lecTarget">{_options(haman._TARGET_OPTIONS, selected=target_value)}</select>
    </form>
    """


def _relative_list_url(
    route: haman.HamanRoute,
    page: int,
    target_value: str = "",
) -> str:
    url = (
        route.target_list_url(page, target_value)
        if target_value
        else route.list_url(page)
    )
    return "?" + urlparse(url).query


def _pager(
    route: haman.HamanRoute,
    current: int,
    last: int,
    *,
    target_value: str = "",
    bad_last_route: bool = False,
    middle_drift: bool = False,
) -> str:
    visible_last = 2 if middle_drift and current == 2 else last
    pages = []
    for number in range(1, visible_last + 1):
        if number == current:
            pages.append(
                f'<span class="m on"><a title="현재 {number} 페이지">{number}</a></span>'
            )
        else:
            href = html.escape(
                _relative_list_url(route, number, target_value),
                quote=True,
            )
            pages.append(
                f'<span class="m"><a href="{href}" title="{number} 페이지">{number}</a></span>'
            )
    last_href = ""
    if current < last and not middle_drift:
        href = _relative_list_url(route, last, target_value)
        if bad_last_route:
            href = "https://example.invalid/escape.web?cpage=" + str(last)
        last_href = f' href="{html.escape(href, quote=True)}"'
    return (
        '<div class="pagination bdt0" title="페이지 수 매기기">'
        f'<span class="pages">{"".join(pages)}</span>'
        f'<span class="m last"><a{last_href} title="맨끝 페이지">»</a></span>'
        "</div>"
    )


def _pairs(labels: tuple[str, ...], values: dict[str, object]) -> str:
    return "".join(
        "<li>"
        f'<span class="t1">{html.escape(label)}</span>'
        f'<span class="t2">{html.escape(str(values[label]))}</span>'
        "</li>"
        for label in labels
    )


def _detail_href(
    route: haman.HamanRoute,
    identity: str,
    requested_page: int,
    target_value: str = "",
) -> str:
    pairs = [("amode", "view"), ("idx", identity), ("cpage", str(requested_page))]
    if route.selector_name:
        pairs.append((route.selector_name, route.selector_value))
    if target_value:
        pairs.append(("lecTarget", target_value))
    return "?" + urlencode(pairs)


def _card(
    record: dict[str, object],
    route: haman.HamanRoute,
    requested_page: int,
    target_value: str = "",
) -> str:
    list_values = {
        "교육기관": record["branch"],
        "수강료": record["fee"],
        "교육기간": record["event_period"],
        "접수기간": record["apply_period"],
        "강좌요일": record["weekdays"],
        "접수방법": record["application_method"],
        "강좌시간": record["time"],
        "선별방법": record["selection_method"],
        "교육장소": record["room"],
        "신청/정원": f"{record['capacity_current']} / {record['capacity_total']}명",
    }
    href = html.escape(
        _detail_href(
            route,
            str(record["identity"]),
            requested_page,
            target_value,
        ),
        quote=True,
    )
    status = html.escape(str(record["status"]))
    return f"""
    <li><a href="{href}">
      <span class="cate" data-category="{status}">{status}</span>
      <strong class="h1">{html.escape(str(record['title']))}</strong>
      <ul class="tg1">{_pairs(haman._LIST_LABELS, list_values)}</ul>
    </a></li>
    """


def _shell(route: haman.HamanRoute, body: str, *, bad_footer: bool = False) -> str:
    address = (
        "(52043) 경상남도 함안군 가야읍 다른로 9 (함안군청)"
        if bad_footer
        else "(52043) 경상남도 함안군 가야읍 말산로 1 (함안군청)"
    )
    return (
        f"<html><head><title>{route.title} | 통합예약시스템</title></head>"
        f'<body>{body}<div id="author1"><address>{address}</address></div></body></html>'
    )


def _list_html(
    route: haman.HamanRoute,
    requested_url: str,
    requested_page: int,
    current_page: int,
    last: int,
    records: list[dict[str, object]],
    *,
    target_value: str = "",
    duplicate_hidden: bool = False,
    option_drift: bool = False,
    bad_last_route: bool = False,
    middle_drift: bool = False,
    bad_footer: bool = False,
) -> str:
    if records:
        ledger = '<div class="edu1list"><ul>' + "".join(
            _card(record, route, requested_page, target_value)
            for record in records
        ) + "</ul></div>"
    else:
        ledger = '<div class="edu1list"><p>등록된 강좌가 없습니다.</p></div>'
    body = (
        _form(
            requested_url,
            target_value=target_value,
            duplicate_hidden=duplicate_hidden,
            option_drift=option_drift,
        )
        + ledger
        + _pager(
            route,
            current_page,
            last,
            target_value=target_value,
            bad_last_route=bad_last_route,
            middle_drift=middle_drift,
        )
    )
    return _shell(route, body, bad_footer=bad_footer)


def _detail_html(
    route: haman.HamanRoute,
    record: dict[str, object],
    page: int,
    *,
    title_drift: bool = False,
    back_drift: bool = False,
    external_back: bool = False,
    capacity_drift: bool = False,
) -> str:
    detail_values = {
        "분류": record["category"],
        "수강료": record["fee"],
        "교육기관": record["branch"],
        "접수기간": record["apply_period"],
        "교육기간": record["event_period"],
        "접수방법": record["application_method"],
        "강좌요일": record["weekdays"],
        "선별방법": record["selection_method"],
        "강좌시간": record["time"],
        "교육장소": record["room"],
        "정원": f"{int(record['capacity_total']) + (1 if capacity_drift else 0)}명",
        "신청인원": f"{record['capacity_current']} 명",
        "부대비용": record["material_fee"],
        "문의처": "055-580-0000",
    }
    back_page = page + 1 if back_drift else page
    back_value = _relative_list_url(route, back_page)
    if external_back:
        back_value = "https://example.invalid" + route.path + back_value
    back = html.escape(back_value, quote=True)
    controls = f'<a href="{back}">목록으로</a>'
    if record["application_control"]:
        controls += '<a href="/unsafe/apply.do">신청하기</a>'
    attachment = (
        '<a href="/Download.do?file=private">첨부파일</a>'
        if record["attachment"]
        else ""
    )
    teacher = '<div class="teacher1">강사 개인정보</div>' if record["teacher"] else ""
    title = str(record["title"]) + (" 변경" if title_drift else "")
    status = html.escape(str(record["status"]))
    body = f"""
    <div class="edu1view">
      <div class="hg1"><span class="cate" data-category="{status}">{status}</span>
      <strong class="h1">{html.escape(title)}</strong></div>
      <ul class="tg1">{_pairs(haman._DETAIL_LABELS, detail_values)}</ul>
      <div class="btns">{controls}</div>
      <div class="tabs1cont"><div class="tabs1pane">자유서술 {attachment}</div>
      <div class="tabs1pane">강사안내 {teacher}</div></div>
    </div>
    """
    return _shell(route, body)


class _Scenario:
    def __init__(self, mutation: str = "") -> None:
        self.mutation = mutation
        self.rows = _records()
        if mutation == "cross_route_duplicate":
            self.rows["digital"][0]["identity"] = self.rows["lifelong"][0]["identity"]
        if mutation == "pii_in_raw_field":
            self.rows["lifelong"][0]["selection_method"] = "선착순 010-1234-5678"
        if mutation == "historical_exception_drift":
            self.rows["lifelong"][-1]["event_period"] = "2018.01.01~2018.02.01"
        if mutation == "unknown_status":
            self.rows["lifelong"][0]["status"] = "새로운상태"
        self.calls: list[str] = []
        self.occurrences: Counter[str] = Counter()
        self.sessions: list[_Session] = []

    def session_factory(self) -> _Session:
        session = _Session(self)
        self.sessions.append(session)
        return session

    def fetch(self, _session: _Session, url: str, _timeout: int) -> _Response:
        self.calls.append(url)
        self.occurrences[url] += 1
        if self.mutation == "transient" and len(self.calls) == 1:
            return _Response(url, "temporary", 503)
        parsed = urlparse(url)
        route = haman.HAMAN_ROUTE_BY_PATH[parsed.path]
        values = parse_qs(parsed.query, keep_blank_values=True)
        response_url = url
        if self.mutation == "response_url" and len(self.calls) == 1:
            response_url = url + "#changed"
        redirected = self.mutation == "redirect" and len(self.calls) == 1
        if values.get("amode") == ["view"]:
            identity = values["idx"][0]
            record = next(
                row
                for rows in self.rows.values()
                for row in rows
                if row["identity"] == identity and row["route"] == route.key
            )
            page = int(values["cpage"][0])
            body = _detail_html(
                route,
                record,
                page,
                title_drift=self.mutation == "detail_title" and identity == "9001",
                back_drift=self.mutation == "detail_back" and identity == "9001",
                external_back=(
                    self.mutation == "detail_back_external" and identity == "9001"
                ),
                capacity_drift=self.mutation == "detail_capacity" and identity == "9001",
            )
            return _Response(response_url, body, redirected=redirected)

        requested_page = int(values["cpage"][0])
        target_value = values.get("lecTarget", [""])[0]
        route_rows = self.rows[route.key]
        if target_value and target_value != haman._CURRENT_TARGET_VALUE:
            route_rows = []
        if self.mutation == "target_missing" and target_value:
            route_rows = [
                row for row in route_rows if row["identity"] != "6001"
            ]
        last = max(1, (len(route_rows) + haman.HAMAN_PAGE_SIZE - 1) // haman.HAMAN_PAGE_SIZE)
        current_page = min(requested_page, last)
        start = (current_page - 1) * haman.HAMAN_PAGE_SIZE
        records = [dict(row) for row in route_rows[start : start + haman.HAMAN_PAGE_SIZE]]
        if self.mutation == "short_page" and route.key == "lifelong" and requested_page == 1:
            records = records[:-1]
        if self.mutation == "clamp_drift" and requested_page > last and records:
            records[0]["title"] = str(records[0]["title"]) + " 변경"
        if (
            self.mutation == "unstable_first"
            and route.key == "lifelong"
            and requested_page == 1
            and self.occurrences[url] > 1
        ):
            records[0]["title"] = str(records[0]["title"]) + " 변경"
        body = _list_html(
            route,
            url,
            requested_page,
            current_page,
            last,
            records,
            target_value=target_value,
            duplicate_hidden=self.mutation == "duplicate_hidden" and len(self.calls) == 1,
            option_drift=self.mutation == "option_drift" and len(self.calls) == 1,
            bad_last_route=self.mutation == "bad_pager_route" and len(self.calls) == 1,
            middle_drift=(
                self.mutation == "middle_pager_drift"
                and route.key == "lifelong"
                and requested_page == 2
            ),
            bad_footer=(
                self.mutation == "bad_footer"
                or (
                    self.mutation == "transient_shell"
                    and len(self.calls) == 1
                )
            ),
        )
        return _Response(response_url, body, redirected=redirected)


def _run(
    scenario: _Scenario,
    **kwargs: object,
) -> tuple[list[dict[str, object]], str, dict[str, object]]:
    return haman.collect_haman_education(
        _target(),
        today="2026-07-23",
        session_factory=scenario.session_factory,
        fetcher=scenario.fetch,
        **kwargs,
    )


def test_candidate_ownership_and_hash_audit_is_explicit() -> None:
    assert hashlib.sha256(haman.HAMAN_CANONICAL_URL.encode()).hexdigest() == (
        haman.HAMAN_CANONICAL_URL_SHA256
    )
    for candidate_id, audit in haman.HAMAN_CANDIDATE_AUDIT.items():
        assert candidate_id.endswith(
            hashlib.sha256(audit["url"].encode()).hexdigest()[:12].upper()
        )
    assert hashlib.sha1(haman.HAMAN_DOWNLOAD_CANDIDATE_URL.encode()).hexdigest()[:8].upper() == (
        "8FBD0B4C"
    )
    assert haman.HAMAN_PROVIDER_ALIAS_AUDIT[haman.HAMAN_STATIC_DIRECTIONS_PROVIDER]["state"] == (
        "disabled"
    )
    assert haman.HAMAN_PROVIDER_ALIAS_AUDIT[haman.HAMAN_STATIC_INTRO_PROVIDER]["state"] == (
        "disabled"
    )
    assert haman.HAMAN_PROVIDER_ALIAS_AUDIT[haman.HAMAN_AGGREGATE_PROVIDER]["state"] == (
        "superseded"
    )


def test_target_requires_exact_retained_provider_and_url() -> None:
    assert haman.is_haman_education_target(_target())
    for target in (
        {"provider": haman.HAMAN_PROVIDER, "url": haman.HAMAN_CANONICAL_URL + "?x=1"},
        {"provider": haman.HAMAN_AGGREGATE_PROVIDER, "url": haman.HAMAN_AGGREGATE_URL},
        {"provider": haman.HAMAN_PROVIDER, "url": haman.HAMAN_CANONICAL_URL.replace("https", "http")},
    ):
        assert not haman.is_haman_education_target(target)
    scenario = _Scenario()
    rows, _, meta = haman.collect_haman_education(
        {"provider": "wrong", "url": haman.HAMAN_CANONICAL_URL},
        session_factory=scenario.session_factory,
        fetcher=scenario.fetch,
    )
    assert rows == []
    assert "does not match" in str(meta["configured_collection_error"])
    assert scenario.calls == []


def test_raw_network_requires_explicit_test_opt_in() -> None:
    rows, _, meta = haman.collect_haman_education(_target(), today="2026-07-23")
    assert rows == []
    assert meta["configured_collection_error"] == "managed session_factory injection is required"


def test_session_factory_failure_is_returned_fail_closed() -> None:
    def broken_factory() -> _Session:
        raise RuntimeError("session unavailable")

    rows, _, meta = haman.collect_haman_education(
        _target(), today="2026-07-23", session_factory=broken_factory
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"] == (
        "RuntimeError: session_factory failed: session unavailable"
    )


def test_complete_synthetic_snapshot_and_privacy_boundary() -> None:
    scenario = _Scenario()
    rows, parser, meta = _run(scenario)
    assert parser == haman.HAMAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert len(rows) == 4
    assert meta["source_total_count"] == 24
    assert meta["current_source_count"] == 4
    assert meta["expired_source_count"] == 19
    assert meta["historical_unknown_period_count"] == 1
    assert meta["route_source_counts"] == {
        "lifelong": 21,
        "digital": 1,
        "social_welfare": 1,
        "women": 1,
        "literature": 0,
    }
    assert meta["route_pages"] == {
        "lifelong": 3,
        "digital": 1,
        "social_welfare": 1,
        "women": 1,
        "literature": 1,
    }
    assert meta["route_final_sizes"] == {
        "lifelong": 1,
        "digital": 1,
        "social_welfare": 1,
        "women": 1,
        "literature": 0,
    }
    assert meta["route_identity_union_count"] == 24
    assert meta["route_identity_overlap_count"] == 0
    assert meta["list_requests"] == 54
    assert meta["target_filter_requests"] == 27
    assert meta["detail_requests"] == 4
    assert meta["source_requests"] == 58
    assert meta["request_attempts"] == 58
    assert meta["target_filter_identity_count"] == 24
    assert meta["target_filter_current_match_count"] == 4
    assert meta["target_filter_missing_current_count"] == 0
    assert meta["attachment_links_discarded"] == 1
    assert meta["teacher_blocks_discarded"] == 1
    assert meta["free_text_tabs_discarded"] == 8
    assert meta["application_control_count"] == 1
    assert meta["status_counts"] == {"CLOSED": 4}
    assert [row["provider_course_id"] for row in rows] == [
        f"{haman.HAMAN_PROVIDER}:idx:9001",
        f"{haman.HAMAN_PROVIDER}:idx:9000",
        f"{haman.HAMAN_PROVIDER}:idx:6001",
        f"{haman.HAMAN_PROVIDER}:idx:5001",
    ]
    assert rows[-1]["end_date"] == "2026-07-23"
    assert rows[2]["fee_amount"] == 40000
    assert rows[0]["application_methods"] == ["인터넷", "방문"]
    assert rows[2]["raw_fields"]["application_control_present"] is True
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["address"] == row["venue_address"] == "" for row in rows)
    assert all(row["collection_category"] == "공공예약" for row in rows)
    assert all(row["program_type"] == "교육" for row in rows)
    assert all(row["target"] == "함안군민" for row in rows)
    assert all(
        row["raw_fields"]["target_filter_verified"] is True
        for row in rows
    )
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(
        urlparse(url).path in haman.HAMAN_ROUTE_BY_PATH for url in scenario.calls
    )
    assert not any("apply" in url.lower() or "download" in url.lower() for url in scenario.calls)
    assert scenario.sessions and all(session.closed for session in scenario.sessions)


def test_retry_is_bounded_and_accounted() -> None:
    scenario = _Scenario("transient")
    rows, _, meta = _run(scenario)
    assert len(rows) == 4
    assert meta["source_requests"] == 58
    assert meta["request_attempts"] == 59
    assert len(scenario.calls) == 59


def test_transient_official_shell_drift_is_retried_and_accounted() -> None:
    scenario = _Scenario("transient_shell")
    rows, _, meta = _run(scenario)

    assert len(rows) == 4
    assert meta["source_requests"] == 58
    assert meta["request_attempts"] == 59
    assert len(scenario.calls) == 59


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("cross_route_duplicate", "route identity overlap"),
        ("short_page", "pre-final page size drift"),
        ("clamp_drift", "did not clamp exactly"),
        ("unstable_first", "source boundaries changed"),
        ("middle_pager_drift", "declared last page drift"),
        ("bad_pager_route", "request escaped exact Haman HTTPS host"),
        ("duplicate_hidden", "hidden fields drift"),
        ("option_drift", "registry drift"),
        ("bad_footer", "official reservation footer drift"),
        ("detail_title", "list/detail structured data drift"),
        ("detail_back", "detail back/list binding drift"),
        ("detail_back_external", "detail back/list binding drift"),
        ("detail_capacity", "list/detail structured data drift"),
        ("pii_in_raw_field", "PII-like value"),
        ("historical_exception_drift", "historical period exception changed"),
        ("unknown_status", "source status drift"),
        ("target_missing", "not proven by the county-resident target partition"),
        ("response_url", "response URL drift"),
        ("redirect", "redirect history is not allowed"),
    ],
)
def test_contract_drift_fails_closed(mutation: str, message: str) -> None:
    rows, _, meta = _run(_Scenario(mutation))
    assert rows == []
    assert message in str(meta["configured_collection_error"])
    assert meta["snapshot_complete"] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://www.haman.go.kr/02697/02708.web;captive?agency=AGENCY005&cpage=1",
        "https://www.haman.go.kr/02697/02708.web?agency=AGENCY005&cpage=1#fragment",
        "https://www.haman.go.kr/02697/02708.web?agency=AGENCY005&cpage=1&cpage=2",
        "https://www.haman.go.kr/02697/02708.web?agency=AGENCY005&cpage=1&broken",
        "https://www.haman.go.kr/02697/02708.web?amode=view&idx=1&cpage=1&agency=AGENCY005&extra=1",
        "https://www.haman.go.kr/board/Download.do?idx=1",
        "https://example.invalid/02697/02708.web?agency=AGENCY005&cpage=1",
    ],
)
def test_request_allowlist_rejects_malformed_or_unsafe_url(url: str) -> None:
    with pytest.raises(haman.HamanContractError):
        haman._validate_fetch_url(url)


def test_caps_are_hard_and_fail_before_unbounded_work() -> None:
    scenario = _Scenario()
    rows, _, meta = _run(scenario, max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "last page 3 exceeds" in str(meta["configured_collection_error"])

    scenario = _Scenario()
    rows, _, meta = _run(scenario, detail_limit=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "current details exceed" in str(meta["configured_collection_error"])

    for kwargs in ({"max_pages": 101}, {"detail_limit": 101}, {"timeout": 0}):
        scenario = _Scenario()
        rows, _, meta = _run(scenario, **kwargs)
        assert rows == []
        assert "ValueError" in str(meta["configured_collection_error"])
        assert scenario.calls == []


def test_future_reception_status_is_preserved_as_scheduled() -> None:
    scenario = _Scenario()
    scenario.rows["social_welfare"][0]["status"] = "접수예정"

    rows, _, meta = _run(scenario)

    future = next(row for row in rows if row["provider_course_id"].endswith(":6001"))
    assert future["status"] == "SCHEDULED"
    assert meta["current_raw_status_counts"]["접수예정"] == 1


def test_dedupe_hook_cannot_drop_or_duplicate_owned_identities() -> None:
    scenario = _Scenario()
    rows, _, meta = _run(scenario, dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe_rows changed complete identity cardinality" in str(
        meta["configured_collection_error"]
    )


@pytest.mark.skipif(os.getenv("RUN_HAMAN_LIVE") != "1", reason="live Haman audit is opt-in")
def test_haman_live_two_exact_snapshots() -> None:
    snapshots: list[list[dict[str, object]]] = []
    for _ in range(2):
        rows, parser, meta = haman.collect_haman_education(
            _target(),
            today=haman.HAMAN_LIVE_AUDIT_BASELINE["cutoff"],
            allow_raw_requests_for_tests=True,
        )
        baseline = haman.HAMAN_LIVE_AUDIT_BASELINE
        assert parser == haman.HAMAN_PARSER
        assert meta["configured_collection_error"] == ""
        assert meta["snapshot_complete"] is True
        assert len(rows) == baseline["current_total"]
        assert meta["source_total_count"] == baseline["source_total"]
        assert meta["current_source_count"] == baseline["current_total"]
        assert meta["expired_source_count"] == baseline["expired_dated_source"]
        assert meta["historical_unknown_period_count"] == baseline["historical_unknown_periods"]
        assert meta["historical_unknown_reception_period_count"] == (
            baseline["historical_unknown_reception_periods"]
        )
        for key in (
            "route_source_counts",
            "route_current_counts",
            "route_pages",
            "route_final_sizes",
            "source_branch_counts",
            "current_branch_counts",
            "source_raw_status_counts",
            "current_raw_status_counts",
            "status_counts",
            "current_ids",
            "attachment_links_discarded",
            "teacher_blocks_discarded",
            "free_text_tabs_discarded",
            "application_control_count",
            "list_requests",
            "detail_requests",
            "source_requests",
        ):
            assert meta[key] == baseline[key]
        assert meta["route_identity_union_count"] == baseline["source_total"]
        assert meta["route_identity_overlap_count"] == 0
        assert meta["application_endpoint_requests"] == 0
        assert meta["login_endpoint_requests"] == 0
        assert meta["applicant_endpoint_requests"] == 0
        assert meta["attachment_endpoint_requests"] == 0
        assert meta["download_endpoint_requests"] == 0
        assert meta["application_form_submissions"] == 0
        snapshots.append(rows)
    assert snapshots[0] == snapshots[1]
