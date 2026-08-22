from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import yaml

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import municipal_gangseo as gangseo
from Crawler import municipal_gangseo_sports as gangseo_sports


ROOT = Path(__file__).resolve().parents[1]


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _information_target() -> dict[str, str]:
    return {
        "provider": gangseo.GANGSEO_INFORMATION_PROVIDER,
        "url": gangseo.GANGSEO_INFORMATION_URL,
    }


def _library_target() -> dict[str, str]:
    return {
        "provider": gangseo.GANGSEO_LIBRARY_PROVIDER,
        "url": gangseo.GANGSEO_LIBRARY_URL,
    }


def _information_row(
    generation: str,
    title: str,
    venue: str,
    period: str,
    schedule: str,
    target: str,
    status: str,
    token: str,
) -> str:
    return f"""
      <tr>
        <td>{generation}</td>
        <td><a href="/reserve/re010202/view?lecDetSn={token}">{title}</a></td>
        <td>{venue}</td>
        <td>{period}</td>
        <td>{schedule}</td>
        <td>{target}</td>
        <td>20 / 7</td>
        <td>{status}</td>
      </tr>
    """


def _information_page(page: int, total_pages: int, rows: list[str]) -> str:
    return f"""
    <html><body>
      <div class="count">총 3 건 ({page} / {total_pages} 페이지)</div>
      <table>
        <thead><tr>
          <th>기수</th><th>강좌명</th><th>교육장소</th><th>교육기간</th>
          <th>교육시간</th><th>대상</th><th>정원/신청인원</th><th>상태</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </body></html>
    """


def _information_detail(
    generation: str,
    title: str,
    venue: str,
    period: str,
    schedule: str,
    target: str,
) -> str:
    return f"""
    <html><body>
      <h3 class="view-title">{title}</h3>
      <table class="detail">
        <tr><th>교육장소</th><td>{venue}</td><th>기 수</th><td>{generation}</td></tr>
        <tr><th>모집기간</th><td>2026.06.01 ~ 2026.06.30</td><th>대상</th><td>{target}</td></tr>
        <tr><th>교육기간</th><td>{period}</td><th>교육시간</th><td>{schedule}</td></tr>
        <tr><th>교육인원</th><td>20명</td><th>강의횟수</th><td>8회</td></tr>
        <tr><th>강사명</th><td>홍길동</td><th>선정방법</th><td>선착순</td></tr>
        <tr><th>비고</th><td></td></tr>
      </table>
      <div class="view-content">상세 교육 안내</div>
    </body></html>
    """


def _information_source(token_suffix: str = "a") -> tuple[dict[int, str], dict[str, str]]:
    expired = (
        "1기",
        "지난 강좌",
        "염창정보화교육장",
        "2026.01.01 ~ 2026.01.31",
        "월 10:00~12:00",
        "성인",
    )
    current = (
        "2기",
        "컴퓨터 기초",
        "등촌정보화교육장",
        "2026.07.01 ~ 2026.07.31",
        "화 10:00~12:00",
        "성인",
    )
    future = (
        "3기",
        "스마트폰 활용",
        "화곡정보화교육장",
        "2026.08.01 ~ 2026.08.31",
        "수 14:00~16:00",
        "어르신",
    )
    expired_token = f"expired-{token_suffix}"
    current_token = f"current-{token_suffix}"
    future_token = f"future-{token_suffix}"
    pages = {
        1: _information_page(
            1,
            2,
            [
                _information_row(*expired, "마감", expired_token),
                _information_row(*current, "마감", current_token),
            ],
        ),
        2: _information_page(
            2,
            2,
            [_information_row(*future, "접수예정", future_token)],
        ),
    }
    details = {
        current_token: _information_detail(*current),
        future_token: _information_detail(*future),
    }
    return pages, details


def _information_fetcher(
    pages: dict[int, str], details: dict[str, str], calls: list[str]
):
    def fetch(_session: DummySession, url: str, _timeout: int):
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == gangseo.GANGSEO_INFORMATION_LIST_PATH:
            return pages[int(query["curPage"][0])]
        if parsed.path == gangseo.GANGSEO_INFORMATION_DETAIL_PATH:
            token = query["lecDetSn"][0]
            if token not in details:
                raise RuntimeError("missing detail")
            return details[token]
        raise AssertionError(f"unexpected URL: {url}")

    return fetch


def _library_item(
    le_code: str,
    le_lg_code: str,
    library_code: str,
    title: str,
    venue: str,
    schedule: str = "매주 토 10:00~12:00",
    source_status: str = "신청가능",
) -> dict[str, object]:
    return {
        "leCode": le_code,
        "leLGCode": le_lg_code,
        "lgLib": library_code,
        "leLName": title,
        "leOpenSDateFmt": "2026.07.01",
        "leOpenEDateFmt": "2026.12.31",
        "leTakeSDateFmt": "2026.06.01",
        "leTakeEDateFmt": "2026.07.31",
        "lectureStatusName": source_status,
        "leArea": venue,
        "leBeginTime": schedule,
        "leTarget": "초등학생",
        "leMoney": "0",
        "leNum": 20,
        "leTakeNum": 5,
    }


def _library_page(page: int, items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "OK",
        "message": "",
        "data": {
            "pageIndex": page,
            "pageSize": 2,
            "totalCount": 3,
            "totalPage": 2,
            "data": items,
        },
    }


def _library_detail(item: dict[str, object], *, title: str | None = None) -> dict[str, object]:
    data = dict(item)
    if title is not None:
        data["leLName"] = title
    data["leContent"] = "<p>도서관 상세 강의 안내</p>"
    return {"status": "OK", "message": "", "data": data}


def _library_source():
    tol = _library_item(
        "8155", "1", "TOL", "작은도서관 독서교실", "큰마음작은도서관"
    )
    za = _library_item(
        "8154", "2", "ZA", "작은도서관 독서교실", "큰마음작은도서관"
    )
    main = _library_item(
        "9000",
        "1",
        "AA",
        "영어 그림책",
        "",
        schedule="",
        source_status="접수대기",
    )
    pages = {1: _library_page(1, [tol, za]), 2: _library_page(2, [main])}
    details = {
        ("8155", "1"): _library_detail(tol),
        ("8154", "2"): _library_detail(za),
        ("9000", "1"): _library_detail(main),
    }
    return pages, details


def _library_fetcher(pages, details, calls: list[str]):
    def fetch(_session: DummySession, url: str, _timeout: int):
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == "/service/culturalLecture/list":
            assert query["libCode"] == ["TOL"]
            assert query["pageSize"] == ["2"]
            assert query["leLName"] == [""]
            return pages[int(query["pageIndex"][0])]
        if parsed.path == "/service/culturalLecture/detail":
            identity = (query["leCode"][0], query["leLGCode"][0])
            value = details[identity]
            if isinstance(value, Exception):
                raise value
            return value
        raise AssertionError(f"unexpected URL: {url}")

    return fetch


def test_exact_canonical_validators_and_invalid_dispatch_do_not_fetch():
    assert gangseo.is_gangseo_information_target(_information_target())
    assert gangseo.is_gangseo_library_target(_library_target())
    assert not gangseo.is_gangseo_information_target(
        {
            "provider": gangseo.GANGSEO_INFORMATION_PROVIDER,
            "url": gangseo.GANGSEO_INFORMATION_URL + "?curPage=1",
        }
    )
    assert not gangseo.is_gangseo_library_target(
        {
            "provider": gangseo.GANGSEO_LIBRARY_PROVIDER,
            "url": "https://evil.example/LibProgramApply?libCode=TOL",
        }
    )

    def forbidden_session():
        raise AssertionError("invalid dispatch must not create a session")

    rows, parser, meta = gangseo.collect_gangseo_current_education(
        {"provider": "WRONG", "url": gangseo.GANGSEO_INFORMATION_URL},
        session_factory=forbidden_session,
    )
    assert rows == []
    assert parser == ""
    assert meta["snapshot_complete"] is False
    assert "exact provider-owned" in meta["configured_collection_error"]


def test_information_complete_history_current_filter_and_rotating_token_stable_id(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(gangseo, "GANGSEO_INFORMATION_PAGE_SIZE", 2)
    first_pages, first_details = _information_source("first")
    second_pages, second_details = _information_source("second")
    first_calls: list[str] = []
    second_calls: list[str] = []

    first_rows, parser, first_meta = gangseo.collect_gangseo_information(
        _information_target(),
        fetcher=_information_fetcher(first_pages, first_details, first_calls),
        session_factory=DummySession,
        today="2026-07-19",
        max_workers=2,
    )
    second_rows, _, second_meta = gangseo.collect_gangseo_information(
        _information_target(),
        fetcher=_information_fetcher(second_pages, second_details, second_calls),
        session_factory=DummySession,
        today="2026-07-19",
        max_workers=2,
    )

    assert parser == gangseo.GANGSEO_INFORMATION_PARSER
    assert first_meta["snapshot_complete"] is True
    assert second_meta["snapshot_complete"] is True
    assert first_meta["declared_total"] == 3
    assert first_meta["declared_pages"] == 2
    assert first_meta["unique_count"] == 3
    assert first_meta["expired_count"] == 1
    assert first_meta["detail_required_count"] == 2
    assert first_meta["detail_pages"] == 2
    assert [row["provider_course_id"] for row in first_rows] == [
        row["provider_course_id"] for row in second_rows
    ]
    assert {row["title"] for row in first_rows} == {
        "컴퓨터 기초",
        "스마트폰 활용",
    }
    assert all(row["fee"] == "요금 별도 안내" for row in first_rows)
    assert first_meta["required_field_counts"] == {
        "target": 2,
        "fee": 2,
        "start_date": 2,
        "end_date": 2,
        "venue_name": 2,
        "category": 2,
        "schedule_raw": 2,
    }
    assert all(
        row["raw_fields"]["rotating_lecDetSn_ignored_for_identity"]
        for row in first_rows
    )
    assert all("expired-" not in url for url in first_calls if "/view?" in url)


def test_information_live_fetch_recovers_one_transient_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gangseo, "GANGSEO_INFORMATION_PAGE_SIZE", 2)
    pages, details = _information_source("retry")
    successful_calls: list[str] = []
    base_fetch = _information_fetcher(pages, details, successful_calls)
    attempts: list[str] = []
    sleeps: list[float] = []

    def transient_then_success(current_session, url: str, timeout: int):
        attempts.append(url)
        if len(attempts) == 1:
            raise requests.ConnectTimeout("temporary connect timeout")
        return base_fetch(current_session, url, timeout)

    monkeypatch.setattr(gangseo, "_default_fetcher", transient_then_success)
    rows, _parser, meta = gangseo.collect_gangseo_information(
        _information_target(),
        session_factory=DummySession,
        today="2026-07-19",
        max_workers=1,
        crawl_delay_seconds=0,
        sleep_fn=sleeps.append,
    )

    assert len(rows) == 2
    assert meta["snapshot_complete"] is True
    assert meta["transient_fetch_attempts"] == 2
    assert attempts[:2] == [
        gangseo.gangseo_information_list_url(1),
        gangseo.gangseo_information_list_url(1),
    ]
    assert sleeps == [gangseo.GANGSEO_TRANSIENT_RETRY_BACKOFF_SECONDS]


def test_information_host_pacer_enforces_official_ten_second_crawl_delay() -> None:
    clock = [100.0]
    sleeps: list[float] = []
    calls: list[str] = []

    def monotonic() -> float:
        return clock[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    paced = gangseo._paced_fetcher(
        lambda _session, url, _timeout: calls.append(url) or "ok",
        delay_seconds=gangseo.GANGSEO_INFORMATION_CRAWL_DELAY_SECONDS,
        pacer=gangseo.GangseoHostPacer(),
        monotonic_fn=monotonic,
        sleep_fn=sleep,
    )

    assert paced(DummySession(), "https://www.gangseo.seoul.kr/reserve/one", 5) == "ok"
    assert paced(DummySession(), "https://www.gangseo.seoul.kr/reserve/two", 5) == "ok"
    assert calls == [
        "https://www.gangseo.seoul.kr/reserve/one",
        "https://www.gangseo.seoul.kr/reserve/two",
    ]
    assert sleeps == [10.0]


def test_information_transient_retry_does_not_retry_contract_errors() -> None:
    attempts: list[str] = []
    sleeps: list[float] = []

    def malformed(_session, url: str, _timeout: int):
        attempts.append(url)
        raise ValueError("malformed response contract")

    fetch = gangseo._retrying_fetcher(malformed, sleep_fn=sleeps.append)
    with pytest.raises(ValueError, match="malformed response contract"):
        fetch(DummySession(), gangseo.GANGSEO_INFORMATION_URL, 5)

    assert attempts == [gangseo.GANGSEO_INFORMATION_URL]
    assert sleeps == []


def test_information_detail_failure_and_page_cap_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(gangseo, "GANGSEO_INFORMATION_PAGE_SIZE", 2)
    pages, details = _information_source("failure")
    details.pop("future-failure")
    rows, _, detail_meta = gangseo.collect_gangseo_information(
        _information_target(),
        fetcher=_information_fetcher(pages, details, []),
        session_factory=DummySession,
        today="2026-07-19",
        max_workers=1,
    )
    assert len(rows) == 2
    assert detail_meta["snapshot_complete"] is False
    assert detail_meta["detail_required_count"] == 2
    assert detail_meta["detail_pages"] == 1
    assert "detail fetch RuntimeError" in detail_meta["configured_collection_error"]

    full_pages, full_details = _information_source("cap")
    _, _, cap_meta = gangseo.collect_gangseo_information(
        _information_target(),
        fetcher=_information_fetcher(full_pages, full_details, []),
        session_factory=DummySession,
        today="2026-07-19",
        max_pages=1,
    )
    assert cap_meta["snapshot_complete"] is False
    assert cap_meta["source_cap_reached"] is True
    assert "max_pages cap reached" in cap_meta["configured_collection_error"]


def test_library_full_api_real_branch_stable_identity_detail_and_tol_za_dedupe(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(gangseo, "GANGSEO_LIBRARY_PAGE_SIZE", 2)
    pages, details = _library_source()
    calls: list[str] = []
    hook_calls: list[int] = []

    def dedupe_hook(rows):
        hook_calls.append(len(rows))
        return rows

    rows, parser, meta = gangseo.collect_gangseo_library(
        _library_target(),
        fetcher=_library_fetcher(pages, details, calls),
        session_factory=DummySession,
        today="2026-07-19",
        max_workers=3,
        dedupe_rows=dedupe_hook,
    )

    assert parser == gangseo.GANGSEO_LIBRARY_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["declared_total"] == 3
    assert meta["declared_pages"] == 2
    assert meta["detail_required_count"] == 3
    assert meta["detail_pages"] == 3
    assert meta["logical_duplicate_count"] == 1
    assert meta["logical_current_count"] == 2
    assert hook_calls == [2]
    assert len(rows) == 2

    small = next(row for row in rows if row["title"] == "작은도서관 독서교실")
    main = next(row for row in rows if row["title"] == "영어 그림책")
    assert small["provider_course_id"].endswith(":8154:2")
    assert small["branch"] == "큰마음작은도서관"
    assert small["raw_fields"]["logical_duplicate_aliases"] == [
        {"leCode": "8155", "leLGCode": "1", "lgLib": "TOL"}
    ]
    assert main["branch"] == "강서영어도서관"
    assert main["venue_name"] == "강서영어도서관"
    assert not main.get("schedule_raw")
    assert main["status"] == "SCHEDULED"
    assert main["fee"] == "0"
    assert main["raw_fields"]["stable_identity_pair"] == ["9000", "1"]
    detail_calls = [url for url in calls if "/detail?" in url]
    assert len(detail_calls) == 3
    assert meta["missing_schedule_count"] == 1
    assert meta["missing_venue_count"] == 0


def test_library_detail_identity_mismatch_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(gangseo, "GANGSEO_LIBRARY_PAGE_SIZE", 2)
    pages, details = _library_source()
    mismatched = dict(details[("9000", "1")])
    mismatched_data = dict(mismatched["data"])
    mismatched_data["leLName"] = "다른 강좌"
    mismatched["data"] = mismatched_data
    details[("9000", "1")] = mismatched

    rows, _, meta = gangseo.collect_gangseo_library(
        _library_target(),
        fetcher=_library_fetcher(pages, details, []),
        session_factory=DummySession,
        today="2026-07-19",
        max_workers=1,
    )
    assert len(rows) == 2
    assert meta["snapshot_complete"] is False
    assert meta["detail_pages"] == 3
    assert meta["detail_errors"] == 1
    assert "detail/list title mismatch" in meta["configured_collection_error"]


def test_library_detail_limit_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(gangseo, "GANGSEO_LIBRARY_PAGE_SIZE", 2)
    pages, details = _library_source()
    _, _, meta = gangseo.collect_gangseo_library(
        _library_target(),
        fetcher=_library_fetcher(pages, details, []),
        session_factory=DummySession,
        today="2026-07-19",
        detail_limit=2,
    )
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert meta["detail_required_count"] == 3
    assert meta["detail_attempts"] == 2
    assert "detail_limit cap allows 2 of 3" in meta["configured_collection_error"]


def test_gangseo_targets_are_distinct_complete_education_snapshots() -> None:
    public = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    information = next(
        row
        for row in public["targets"]
        if row.get("provider") == gangseo.GANGSEO_INFORMATION_PROVIDER
    )
    assert information["url"] == gangseo.GANGSEO_INFORMATION_URL
    assert information["collection_type"] == gangseo.GANGSEO_INFORMATION_PARSER
    assert information["full_snapshot_required"] is True
    assert information["municipality_code"] == "1150000000"
    assert information["service_group"] == "공공강좌"
    assert information["service_group_policy"] == "locked"
    assert information["last_quality"]["source_total"] == 809
    assert information["last_quality"]["snapshot_complete"] is True

    promoted = yaml.safe_load(
        (
            ROOT
            / "config"
            / "crawl_targets"
            / "municipal_integrated_reservation.yaml"
        ).read_text(encoding="utf-8")
    )
    promoted_by_provider = {
        row["provider"]: row
        for row in promoted["targets"]
        if row.get("provider")
        in {gangseo.GANGSEO_LIBRARY_PROVIDER, gangseo_sports.GANGSEO_SPORTS_PROVIDER}
    }
    assert set(promoted_by_provider) == {
        gangseo.GANGSEO_LIBRARY_PROVIDER,
        gangseo_sports.GANGSEO_SPORTS_PROVIDER,
    }
    assert (
        promoted_by_provider[gangseo.GANGSEO_LIBRARY_PROVIDER]["url"]
        == gangseo.GANGSEO_LIBRARY_URL
    )
    assert (
        promoted_by_provider[gangseo_sports.GANGSEO_SPORTS_PROVIDER]["url"]
        == gangseo_sports.GANGSEO_SPORTS_URL
    )
    assert all(
        row["full_snapshot_required"] is True
        and row["municipality_code"] == "1150000000"
        and row["service_group"] == "공공강좌"
        and row["service_group_policy"] == "locked"
        for row in promoted_by_provider.values()
    )

    expected_caps = {
        gangseo.GANGSEO_INFORMATION_PROVIDER: ("100", "100"),
        gangseo.GANGSEO_LIBRARY_PROVIDER: ("10", "300"),
        gangseo_sports.GANGSEO_SPORTS_PROVIDER: ("5", "100"),
    }
    for provider, (max_pages, detail_limit) in expected_caps.items():
        arguments = list(generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider])
        assert arguments[:4] == ["--save-db", "--mark-stale", "--per-target-limit", "0"]
        assert arguments[-4:] == ["--max-pages", max_pages, "--detail-limit", detail_limit]
        assert "--allow-partial-save" not in arguments
