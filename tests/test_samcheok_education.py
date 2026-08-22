from __future__ import annotations

from collections import Counter
import os
from threading import Lock
from typing import Any, Mapping

import pytest

from Crawler import municipal_samcheok as samcheok
from utils.outbound_http import SafeSession


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(self, text: str, url: str, status_code: int = 200) -> None:
        self.content = text.encode("utf-8")
        self.text = text
        self.url = url
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.history: list[Any] = []


class FixtureTransport:
    def __init__(self, pages: Mapping[str, Any]) -> None:
        self.pages = dict(pages)
        self.calls: list[tuple[str, str]] = []
        self.offsets: Counter[str] = Counter()
        self.lock = Lock()

    def __call__(
        self,
        _session: Any,
        method: str,
        url: str,
        *,
        timeout: int,
    ) -> FakeResponse:
        assert method == "GET"
        assert timeout >= 1
        assert "form.naver.com" not in url
        assert not any(token in url.lower() for token in ("login", "insert", "write", ".hwp"))
        with self.lock:
            self.calls.append((method, url))
            offset = self.offsets[url]
            self.offsets[url] += 1
        if url not in self.pages:
            raise AssertionError(f"unexpected request: {url}")
        value = self.pages[url]
        if isinstance(value, list):
            value = value[min(offset, len(value) - 1)]
        if isinstance(value, FakeResponse):
            return value
        return FakeResponse(str(value), url)


def _target(owner: str, **changes: Any) -> dict[str, Any]:
    target = dict(samcheok.SAMCHEOK_OWNERS[owner])
    target.update(changes)
    return target


_LIFE_HEADER = (
    "<thead><tr><th>강좌명</th><th>교육기간</th><th>수강신청기간</th>"
    "<th>교육기관</th><th>수강료</th><th>접수방법</th><th>상태</th></tr></thead>"
)


def _life_list(
    *,
    current_title: str = "[원덕평생학습센터] 현재 강좌",
    current_institution: str = "원",
) -> str:
    rows = f"""
      <tr><td><a href="?amode=view&amp;idx=199">{current_title}</a></td>
      <td>2026. 08. 01 ~ 2026. 08. 31</td><td>2026. 07. 01 ~ 2026. 07. 31</td>
      <td>{current_institution}</td><td>무료</td><td>인터넷</td><td>접수중</td></tr>
      <tr><td><a href="?amode=view&amp;idx=198">[삼척평생학습관] 종료 강좌</a></td>
      <td>2026. 06. 01 ~ 2026. 07. 01</td><td>2026. 05. 01 ~ 2026. 05. 31</td>
      <td>삼척평생학습관</td><td>무료</td><td>인터넷</td><td>마감</td></tr>
    """
    return f"""
      <html><body><div class="pagination"><span class="on"><a>1</a></span></div>
      <table class="t1">{_LIFE_HEADER}<tbody>{rows}</tbody></table></body></html>
    """


def _life_empty() -> str:
    return f"""
      <html><body><div class="pagination"><span class="on"><a>1</a></span></div>
      <table class="t1">{_LIFE_HEADER}<tbody><tr>
      <td colspan="7">등록된 내용이 없습니다.</td></tr></tbody></table></body></html>
    """


def _pair(label: str, value: str) -> str:
    return f"<tr><th>{label}</th><td>{value}</td></tr>"


def _life_detail(
    *,
    title: str = "[원덕평생학습센터] 현재 강좌",
    target: str = "성인",
    institution: str = "원",
) -> str:
    pairs = "".join(
        (
            _pair("교육 구분", "평생교육"),
            _pair("교육 대상", target),
            _pair("교육 기관", institution),
            _pair("교육 장소", "원덕평생학습센터"),
            _pair("강의 시간", "매주 월요일 10:00"),
            _pair("접수인원/정원", "3/10"),
            _pair("수강료", "무료"),
            _pair("접수 방법", "인터넷"),
            _pair("접수 상태", "접수중"),
            _pair("문의 전화", "033-570-0000"),
            _pair("강사명", "홍길동"),
        )
    )
    return f"""
      <html><body><div id="contents"><h2 class="h1">{title}</h2>
      <table class="t3">{pairs}</table><a href="/apply/write.do">신청</a>
      </div></body></html>
    """


def _life_pages() -> dict[str, Any]:
    normal = _life_list()
    return {
        samcheok.lifelong_list_url(1): normal,
        samcheok.lifelong_list_url(2): normal,
        samcheok.lifelong_empty_sentinel_url(): _life_empty(),
        samcheok.lifelong_detail_url("199"): _life_detail(),
    }


def _run_life(
    *,
    pages: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], FixtureTransport]:
    transport = FixtureTransport(pages or _life_pages())
    rows, parser, meta = samcheok.collect(
        _target("lifelong"),
        today="2026-07-23",
        fetcher=transport,
        session_factory=FakeSession,
        max_workers=1,
        **kwargs,
    )
    assert parser == samcheok.SAMCHEOK_PARSER
    return rows, meta, transport


def _nav(owner: str) -> str:
    return "".join(
        f'<a href="/sub/{name}">{name}</a>'
        for name in sorted(samcheok._STATIC_NAV_EXPECTED[owner])
    )


def _static_source_page(spec: samcheok._StaticSpec) -> str:
    heading = spec.heading_token
    if "20" not in heading:
        heading = f"2025 {heading}"
    definitions: list[tuple[str, str]] = []
    if spec.operation_key:
        definitions.append((spec.operation_key, "2026. 08. 01 ~ 2026. 09. 01"))
    if spec.application_keys:
        definitions.append((spec.application_keys[0], "2026. 07. 01 ~ 2026. 07. 31"))
    if spec.fee_keys:
        definitions.append((spec.fee_keys[0], "무료"))
    definitions.append(("모집대상", "청소년"))
    definition_html = "".join(f"<dt>{key}</dt><dd>{value}</dd>" for key, value in definitions)
    indexes = tuple(
        index
        for index in (
            spec.title_index,
            spec.period_index,
            spec.schedule_index,
            spec.capacity_index,
            spec.target_index,
            spec.venue_index,
        )
        if index is not None
    )
    values = [""] * (max(indexes) + 1)
    values[spec.title_index] = f"{spec.key} 공개강좌"
    if spec.period_index is not None:
        values[spec.period_index] = "2026. 08. 01 ~ 2026. 09. 01"
    if spec.schedule_index is not None:
        values[spec.schedule_index] = "매주 토요일"
    if spec.capacity_index is not None:
        values[spec.capacity_index] = "10명"
    if spec.target_index is not None:
        values[spec.target_index] = "청소년"
    if spec.venue_index is not None:
        values[spec.venue_index] = spec.branch
    cells = "".join(f"<td>{value}</td>" for value in values)
    headers = "".join(f"<th>{token}</th>" for token in spec.header_tokens)
    return f"""
      <html><body><div id="SubContWrap">{_nav(spec.owner)}
      <div class="noticeBox"><h4>{heading}</h4><dl>{definition_html}</dl></div>
      <div class="scrollTB"><table class="t3"><thead><tr>{headers}</tr></thead>
      <tbody><tr>{cells}</tr></tbody></table></div>
      <a href="https://form.naver.com/example">신청</a></div></body></html>
    """


def _audit_page(owner: str, *, institutional: bool = False) -> str:
    table = ""
    if institutional:
        table = (
            '<table class="t3"><tbody>'
            '<tr><td>1</td><td>학교연계 A</td></tr>'
            '<tr><td>2</td><td>학교연계 B</td></tr>'
            "</tbody></table>"
        )
    return f"<html><body><div id='SubContWrap'>{_nav(owner)}{table}</div></body></html>"


def _static_pages(owner: str) -> dict[str, Any]:
    pages = {spec.url: _static_source_page(spec) for spec in samcheok._STATIC_SOURCE_SPECS[owner]}
    sentinel = samcheok._STATIC_SENTINELS[owner]
    for url in samcheok._STATIC_AUDIT_URLS[owner]:
        institutional = url.endswith("Program3.php") if owner in {"youth", "dgyouth"} else url.endswith("Program4.php")
        pages[url] = _audit_page(owner, institutional=institutional and url != sentinel)
    return pages


def _run_static(
    owner: str,
    *,
    pages: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], FixtureTransport]:
    transport = FixtureTransport(pages or _static_pages(owner))
    rows, parser, meta = samcheok.collect(
        _target(owner),
        today="2026-07-23",
        fetcher=transport,
        session_factory=FakeSession,
        max_workers=1,
        **kwargs,
    )
    assert parser == samcheok.SAMCHEOK_PARSER
    return rows, meta, transport


def _assert_failed(meta: Mapping[str, Any], phrase: str) -> None:
    assert phrase in str(meta["configured_collection_error"])
    assert meta["returned_count"] == 0
    assert meta["pagination_complete"] is False
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert meta["full_snapshot_validated"] is False


def test_owner_registry_candidate_ids_branches_and_exclusions_are_exact() -> None:
    assert samcheok.SAMCHEOK_MUNICIPALITY_CODE == "5123000000"
    assert samcheok.SAMCHEOK_MUNICIPALITY_NAME == "강원특별자치도 삼척시"
    assert samcheok.SAMCHEOK_OWNERS == {
        "lifelong": {
            "provider": "MUNI_WWW_SAMCHEOK_GO_KR_AEA01740",
            "url": "https://www.samcheok.go.kr/specialty/00465/01127.web",
            "candidate_id": "MUNI_IR_90DD3B4771BF",
        },
        "youth": {
            "provider": "MUNI_YOUTH_SAMCHEOK_GO_KR_96E8E691",
            "url": "https://youth.samcheok.go.kr/sub/Program1.php",
            "candidate_id": "MUNI_IR_565500DF239C",
        },
        "dgyouth": {
            "provider": "MUNI_DGYOUTH_SAMCHEOK_GO_KR_C683FA1B",
            "url": "https://dgyouth.samcheok.go.kr/sub/Program1.php",
            "candidate_id": "MUNI_IR_72CCF0EE42A4",
        },
        "wdyouth": {
            "provider": "MUNI_WDYOUTH_SAMCHEOK_GO_KR_AE04F451",
            "url": "https://wdyouth.samcheok.go.kr/sub/Program1.php",
            "candidate_id": "MUNI_IR_405013E57576",
        },
    }
    assert samcheok.SAMCHEOK_LIFELONG_BRANCHES == (
        "삼척평생학습관",
        "도계평생학습센터",
        "원덕평생학습센터",
    )
    assert samcheok.SAMCHEOK_YOUTH_BRANCHES["wdyouth"] == (
        "원덕청소년문화의집",
        "근덕청소년문화의집",
    )
    assert "MUNI_LIB_GWE_GO_KR_303FFE72" in samcheok.SAMCHEOK_EXCLUDED_BOUNDARIES[
        "https://lib.gwe.go.kr/samecc/menu/3560/lecture-event/list/all"
    ]
    assert samcheok.SAMCHEOK_LEGACY_LIFELONG_CANDIDATE_ID == "MUNI_IR_0B7A7B0B1CB2"
    assert samcheok.SAMCHEOK_AUDIT_BASELINE["wdyouth"] == {
        "source": 30,
        "current": 13,
        "returned": 13,
    }


def test_target_matching_is_exact_and_legacy_shell_is_not_a_second_owner() -> None:
    for owner in samcheok.SAMCHEOK_OWNERS:
        assert samcheok.owner_for_target(_target(owner)) == owner
        assert samcheok.is_target(_target(owner)) is True
        assert samcheok.is_target(_target(owner, url=_target(owner)["url"] + "?extra=1")) is False
    rows, _, meta = samcheok.collect(
        {
            "provider": samcheok.SAMCHEOK_LIFELONG_PROVIDER,
            "url": samcheok.SAMCHEOK_LEGACY_LIFELONG_URL,
        },
        fetcher=lambda *_args, **_kwargs: None,
        session_factory=FakeSession,
    )
    assert rows == []
    _assert_failed(meta, "non-canonical")


@pytest.mark.parametrize(
    ("owner", "method", "url"),
    [
        ("lifelong", "POST", samcheok.lifelong_list_url(1)),
        ("lifelong", "GET", samcheok.SAMCHEOK_LIFELONG_URL + "?amode=write"),
        ("lifelong", "GET", samcheok.SAMCHEOK_LIFELONG_URL + "?idx=199&amode=view&next=1"),
        ("youth", "GET", "https://form.naver.com/example"),
        ("dgyouth", "GET", "https://dgyouth.samcheok.go.kr/data/application.hwp"),
        ("wdyouth", "GET", "https://wdyouth.samcheok.go.kr/member/login.php"),
    ],
)
def test_request_allowlist_never_admits_application_attachment_or_login(
    owner: str, method: str, url: str
) -> None:
    with pytest.raises(samcheok.SamcheokContractError):
        samcheok._classify_url(owner, method, url)


def test_lifelong_complete_snapshot_repairs_one_source_typo_and_discards_pii() -> None:
    rows, meta, transport = _run_life()
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == 2
    assert meta["current_source_count"] == 1
    assert meta["detail_verified"] == 1
    assert meta["branch_repair_count"] == 1
    assert meta["empty_sentinel_rows"] == 0
    assert all(meta["boundary_rechecks"].values())
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert len(rows) == 1
    row = rows[0]
    assert row["branch"] == "원덕평생학습센터"
    assert row["source_course_id"] == "199"
    assert row["application_url"] == ""
    assert row["period"] == "2026-08-01 ~ 2026-08-31"
    assert row["apply_period"] == "2026-07-01 ~ 2026-07-31"
    assert row["apply_start"] == "2026-07-01"
    assert row["apply_end"] == "2026-07-31"
    assert row["schedule_raw"] == "매주 월요일 10:00"
    assert row["venue_name"] == "원덕평생학습센터"
    assert row["raw_url"] == samcheok.lifelong_detail_url("199")
    assert row["preserve_branch"] is True
    assert "phone" not in str(row).lower()
    assert "033-570-0000" not in str(row)
    assert not any("apply" in url or "write" in url for _, url in transport.calls)
    assert meta["application_endpoint_requests"] == 0
    assert meta["pii_values_persisted"] == 0


def test_lifelong_normalizes_current_official_institution_alias() -> None:
    normal = _life_list(
        current_title="[삼척평생학습관] 현재 강좌",
        current_institution="삼척시평생학습관",
    )
    pages = _life_pages()
    pages[samcheok.lifelong_list_url(1)] = normal
    pages[samcheok.lifelong_list_url(2)] = normal
    pages[samcheok.lifelong_detail_url("199")] = _life_detail(
        title="[삼척평생학습관] 현재 강좌",
        institution="삼척시평생학습관",
    )

    rows, meta, _ = _run_life(pages=pages)

    assert len(rows) == 1
    assert rows[0]["branch"] == "삼척평생학습관"
    assert meta["branch_repair_count"] == 1
    assert meta["snapshot_complete"] is True


def test_dogye_regular_heading_allows_semester_rollover() -> None:
    spec = samcheok._STATIC_SOURCE_SPECS["dgyouth"][0]
    pages = _static_pages("dgyouth")
    pages[spec.url] = _static_source_page(spec).replace(
        "<h4>2025 교육문화 프로그램</h4>",
        "<h4>2026 하반기 교육문화 프로그램 참가자 모집 및 안내</h4>",
    )

    rows, meta, _ = _run_static("dgyouth", pages=pages)

    regular = next(row for row in rows if row["raw_fields"]["page_key"] == "regular")
    assert regular["raw_fields"]["cohort"].startswith("2026 하반기")
    assert meta["snapshot_complete"] is True


def test_wondeok_split_heading_and_short_program_header_are_accepted() -> None:
    spec = next(
        item
        for item in samcheok._STATIC_SOURCE_SPECS["wdyouth"]
        if item.key == "wondeok_summer"
    )
    pages = _static_pages("wdyouth")
    regular = next(
        item
        for item in samcheok._STATIC_SOURCE_SPECS["wdyouth"]
        if item.key == "wondeok_regular"
    )
    pages[regular.url] = _static_source_page(regular).replace(
        "<th>운영기간</th>",
        "<th>운영 기간</th>",
    )
    pages[spec.url] = _static_source_page(spec).replace(
        "<h4>2026 여름방학프로그램</h4>",
        (
            "<h4>원덕청소년문화의집</h4>"
            '<p class="sub-title-M"><span>2026 여름방학프로그램</span>'
            "<span>참가자 모집 안내</span></p>"
        ),
    )

    rows, meta, _ = _run_static("wdyouth", pages=pages)

    summer = next(
        row for row in rows if row["raw_fields"]["page_key"] == "wondeok_summer"
    )
    assert "2026 여름방학프로그램" in summer["raw_fields"]["cohort"]
    assert meta["snapshot_complete"] is True


@pytest.mark.parametrize(
    ("owner", "expected_count", "expected_branches"),
    [
        ("youth", 2, {"삼척시청소년수련관"}),
        ("dgyouth", 2, {"도계청소년장학센터"}),
        ("wdyouth", 5, {"원덕청소년문화의집", "근덕청소년문화의집"}),
    ],
)
def test_each_youth_owner_uses_complete_fixed_registry_and_empty_sentinel(
    owner: str, expected_count: int, expected_branches: set[str]
) -> None:
    rows, meta, transport = _run_static(owner)
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == expected_count
    assert meta["current_source_count"] == expected_count
    assert meta["detail_verified"] == expected_count
    assert meta["empty_sentinel_rows"] == 0
    assert meta["excluded_surface_counts"] == {"closed_school_partner_programmes": 2}
    assert all(meta["boundary_rechecks"].values())
    assert len(rows) == expected_count
    assert {row["branch"] for row in rows} == expected_branches
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["target"] for row in rows)
    assert all(row["fee"] for row in rows)
    assert all(row["period"] for row in rows)
    assert all(row["venue_name"] for row in rows)
    assert all(row["category"] for row in rows)
    assert all(row["schedule_raw"] for row in rows)
    assert all(row["apply_period"] for row in rows)
    assert all(row["raw_url"] for row in rows)
    assert len({row["raw_url"] for row in rows}) == len(rows)
    assert all(
        row["raw_url"].startswith(f"{row['source_url']}#course-")
        for row in rows
    )
    assert all(row["preserve_branch"] is True for row in rows)
    allowed = {
        *(spec.url for spec in samcheok._STATIC_SOURCE_SPECS[owner]),
        *samcheok._STATIC_AUDIT_URLS[owner],
    }
    assert {url for _, url in transport.calls} == allowed
    assert meta["application_endpoint_requests"] == 0
    assert meta["attachment_endpoint_requests"] == 0
    assert meta["pii_endpoint_requests"] == 0


def test_raw_network_is_disabled_without_an_explicit_managed_transport() -> None:
    rows, parser, meta = samcheok.collect(_target("lifelong"), today="2026-07-23")
    assert rows == []
    assert parser == samcheok.SAMCHEOK_PARSER
    _assert_failed(meta, "raw requests disabled")
    assert meta["logical_requests"] == 0


def test_access_restriction_redirect_and_header_drift_fail_closed() -> None:
    pages = _life_pages()
    pages[samcheok.lifelong_list_url(1)] = FakeResponse(
        "access denied", samcheok.lifelong_list_url(1), 403
    )
    rows, meta, _ = _run_life(pages=pages)
    assert rows == []
    _assert_failed(meta, "HTTP 403")

    pages = _life_pages()
    pages[samcheok.lifelong_list_url(1)] = FakeResponse(
        _life_list(), "https://www.samcheok.go.kr/member/login.web"
    )
    rows, meta, _ = _run_life(pages=pages)
    assert rows == []
    _assert_failed(meta, "response URL changed")

    pages = _life_pages()
    pages[samcheok.lifelong_list_url(1)] = _life_list().replace("강좌명", "과정명", 1)
    rows, meta, _ = _run_life(pages=pages)
    assert rows == []
    _assert_failed(meta, "header changed")


def test_boundary_and_empty_sentinel_drift_fail_closed() -> None:
    pages = _life_pages()
    pages[samcheok.lifelong_list_url(1)] = [
        _life_list(),
        _life_list(current_title="[원덕평생학습센터] 변경된 강좌"),
    ]
    rows, meta, _ = _run_life(pages=pages)
    assert rows == []
    _assert_failed(meta, "boundary drift")

    pages = _life_pages()
    pages[samcheok.lifelong_empty_sentinel_url()] = _life_empty().replace(
        "등록된 내용이 없습니다.", "검색 결과 확인 중"
    )
    rows, meta, _ = _run_life(pages=pages)
    assert rows == []
    _assert_failed(meta, "empty-search sentinel changed")


def test_transient_retry_refreshes_session_and_remains_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(samcheok.time, "sleep", lambda _seconds: None)
    pages = _life_pages()
    pages[samcheok.lifelong_list_url(1)] = [
        FakeResponse("busy", samcheok.lifelong_list_url(1), 503),
        _life_list(),
        _life_list(),
    ]
    rows, meta, _ = _run_life(pages=pages)
    assert len(rows) == 1
    assert meta["request_retry_count"] == 1
    assert meta["physical_requests"] == meta["logical_requests"] + 1
    assert meta["full_snapshot_validated"] is True


def test_caps_dedupe_cardinality_and_output_pii_fail_closed() -> None:
    rows, meta, _ = _run_life(max_pages=6)
    assert rows == []
    assert meta["source_cap_reached"] is True
    _assert_failed(meta, "max_pages cap")

    rows, meta, _ = _run_life(dedupe_rows=lambda values: values[:-1])
    assert rows == []
    _assert_failed(meta, "dedupe changed")

    pages = _life_pages()
    pages[samcheok.lifelong_detail_url("199")] = _life_detail(target="문의 010-1234-5678")
    rows, meta, _ = _run_life(pages=pages)
    assert rows == []
    _assert_failed(meta, "PII value")


def _safe_session() -> SafeSession:
    session = SafeSession(max_redirects=1, max_response_bytes=samcheok.SAMCHEOK_MAX_BYTES)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


@pytest.mark.skipif(
    os.getenv("RUN_SAMCHEOK_LIVE") != "1",
    reason="set RUN_SAMCHEOK_LIVE=1 for two complete official-source snapshots",
)
def test_live_all_four_owners_twice_match_the_audited_snapshot() -> None:
    snapshots: list[dict[str, tuple[list[dict[str, Any]], dict[str, Any]]]] = []
    for pass_number in range(2):
        snapshot: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        for owner, target in samcheok.SAMCHEOK_OWNERS.items():
            options: dict[str, Any]
            if pass_number == 0:
                options = {"allow_raw_requests_for_tests": True}
            else:
                options = {"session_factory": _safe_session}
            rows, parser, meta = samcheok.collect(
                target,
                today="2026-07-23",
                max_pages=samcheok.SAMCHEOK_MAX_PAGES,
                detail_limit=samcheok.SAMCHEOK_MAX_DETAILS,
                max_workers=samcheok.SAMCHEOK_MAX_WORKERS,
                **options,
            )
            assert parser == samcheok.SAMCHEOK_PARSER
            assert meta["configured_collection_error"] == ""
            baseline = samcheok.SAMCHEOK_AUDIT_BASELINE[owner]
            assert meta["source_total"] == baseline["source"]
            assert meta["current_source_count"] == baseline["current"]
            assert meta["returned_count"] == baseline["returned"]
            assert meta["detail_verified"] == baseline["current"]
            assert meta["empty_sentinel_rows"] == 0
            assert all(meta["boundary_rechecks"].values())
            assert meta["full_snapshot_validated"] is True
            assert meta["application_endpoint_requests"] == 0
            assert meta["login_endpoint_requests"] == 0
            assert meta["attachment_endpoint_requests"] == 0
            assert meta["payment_endpoint_requests"] == 0
            assert meta["pii_endpoint_requests"] == 0
            assert meta["pii_values_persisted"] == 0
            snapshot[owner] = (rows, meta)
        snapshots.append(snapshot)
    for owner in samcheok.SAMCHEOK_OWNERS:
        first_rows, first_meta = snapshots[0][owner]
        second_rows, second_meta = snapshots[1][owner]
        assert first_rows == second_rows
        for key in (
            "source_total",
            "current_source_count",
            "returned_count",
            "branch_counts",
            "source_identity_sha256",
            "output_identity_sha256",
        ):
            assert first_meta[key] == second_meta[key]
