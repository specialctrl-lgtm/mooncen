from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_hapcheon as hapcheon


def _record(
    identity: str,
    *,
    title: str,
    branch: str,
    status: str,
    period: str,
    apply: str,
    method: str = "온라인접수, 전화접수",
    category: str = "건강취미",
    venue: str = "합천군 평생학습관 2층 강의실",
    control: bool = False,
) -> dict[str, Any]:
    return {
        "id": identity,
        "title": title,
        "branch": branch,
        "status": status,
        "period": period,
        "apply": apply,
        "method": method,
        "category": category,
        "venue": venue,
        "control": control,
        "fee": "무료",
        "schedule": "매주 월요일 10:00~12:00",
        "target": "합천군민",
        "capacity": 20,
    }


MAIN_RECORDS = (
    _record(
        "210",
        title="온라인 미래 강좌",
        branch="평생교육포털",
        status="접수중",
        period="2099.08.01 ~ 2099.08.31",
        apply="2099.07.01 09:00 ~ 2099.07.31 18:00",
        control=True,
    ),
    _record(
        "209",
        title="대기자 미래 강좌",
        branch="평생교육포털",
        status="대기접수",
        period="2099.08.02 ~ 2099.09.01",
        apply="2099.07.01 09:00 ~ 2099.07.31 18:00",
        control=True,
    ),
    _record(
        "208",
        title="현장 미래 강좌",
        branch="주민복지과",
        status="접수중",
        period="2099.08.03 ~ 2099.09.03",
        apply="2099.07.01 ~ 2099.07.31",
        method="현장접수",
    ),
    _record(
        "207",
        title="접수 예정 강좌",
        branch="체육시설과",
        status="접수대기",
        period="2099.09.01 ~ 2099.09.30",
        apply="2099.08.01 ~ 2099.08.20",
    ),
    _record(
        "206",
        title="오늘 종료 마감 강좌",
        branch="노인아동여성과",
        status="접수마감",
        period="2099.07.10 ~ 2099.07.20",
        apply="2099.06.01 ~ 2099.06.30",
    ),
    *(
        _record(
            str(identity),
            title=f"지난 강좌 {identity}",
            branch="평생교육포털",
            status="접수완료" if identity == 205 else "접수마감",
            period="2098.01.01 ~ 2098.02.01",
            apply="2097.12.01 ~ 2097.12.20",
        )
        for identity in range(205, 200, -1)
    ),
)

LITERACY_RECORDS = tuple(
    _record(
        str(identity),
        title=f"학력인정반 {identity}",
        branch=hapcheon.HAPCHEON_LITERACY_BRANCH,
        status="접수마감",
        period="2098.03.03 ~ 2098.12.31",
        apply="2098.01.01 ~ 2098.04.30",
        category="초등학력보완프로그램",
        venue="합천군 문해교실",
    )
    for identity in (425, 424, 423)
)


def _target(
    *,
    provider: str = hapcheon.HAPCHEON_PROVIDER,
    url: str = hapcheon.HAPCHEON_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "합천군 통합예약 교육강좌",
        "branch": hapcheon.HAPCHEON_MUNICIPALITY_NAME,
    }


def _form(
    ledger: hapcheon.HapcheonLedger,
    *,
    requested_page: int,
    displayed_page: int,
    total: int,
    last_page: int,
) -> str:
    query = urlparse(hapcheon.hapcheon_list_url(ledger, requested_page)).query
    action = ledger.path + (f"?{query}" if query else "")
    return f"""
      <form id="frmLecture" name="frmLecture" method="get" action="{action}">
        <div class="infomenu1 lecture1search1">
          <select name="stype"><option value="title">과정명</option></select>
          <input name="sstring" value="">
          <div class="left">총 <em>{total}</em>건의 자료가 있습니다.
            (<b>{displayed_page}</b>/{last_page} 페이지)</div>
        </div>
      </form>
    """


def _pagination(
    ledger: hapcheon.HapcheonLedger,
    *,
    displayed_page: int,
    last_page: int,
) -> str:
    pages = []
    for page in range(1, last_page + 1):
        if page == displayed_page:
            pages.append(
                f'<span class="m on"><a title="현재 {page} 페이지">{page}</a></span>'
            )
        else:
            query = urlencode(
                (
                    ((ledger.facility_key, ledger.facility_value), ("cpage", str(page)))
                    if ledger.key == "main"
                    else (("cpage", str(page)),)
                )
            )
            pages.append(
                f'<span class="m"><a href="?{query}" title="{page} 페이지">{page}</a></span>'
            )
    return f'<div class="pagination"><span class="pages">{"".join(pages)}</span></div>'


def _card(
    ledger: hapcheon.HapcheonLedger,
    record: Mapping[str, Any],
    *,
    requested_page: int,
    title_override: str = "",
) -> str:
    params: list[tuple[str, str]] = [
        ("amode", "view"),
        ("idx", str(record["id"])),
        (ledger.facility_key, ledger.facility_value),
    ]
    if requested_page > 1:
        params.append(("cpage", str(requested_page)))
    return f"""
      <li class="column"><div class="w1">
        <a class="a1" href="?{urlencode(params)}">
          <div class="tg1">
            <i class="c" data-progress="{record['status']}">{record['status']}</i>
            <strong class="t1">{title_override or record['title']}</strong>
            <span class="t2">{record['category']}</span>
          </div>
          <div class="tg2">
            <span class="place1">[{record['branch']}]</span>
            <span class="li1"><span class="t1">교육기간</span>
              <span class="t2">{record['period']}</span></span>
            <span class="li1"><span class="t1">신청기간</span>
              <span class="t2">{record['apply']}</span></span>
            <span class="li1"><span class="t1">수강료</span>
              <span class="t2">{record['fee']}</span></span>
          </div>
        </a>
      </div></li>
    """


def _list_html(
    ledger: hapcheon.HapcheonLedger,
    *,
    requested_page: int,
    displayed_page: int,
    records: tuple[Mapping[str, Any], ...],
    total: int,
    last_page: int,
    title_override: str = "",
) -> str:
    cards = "".join(
        _card(
            ledger,
            record,
            requested_page=requested_page,
            title_override=title_override if index == 0 else "",
        )
        for index, record in enumerate(records)
    )
    return f"""
      <html><body><div id="body_content">
        {_form(ledger, requested_page=requested_page, displayed_page=displayed_page, total=total, last_page=last_page)}
        <div class="cp8card1"><ul>{cards}</ul></div>
        {_pagination(ledger, displayed_page=displayed_page, last_page=last_page)}
      </div></body></html>
    """


def _detail_html(
    ledger: hapcheon.HapcheonLedger,
    record: Mapping[str, Any],
    *,
    title_override: str = "",
    period_override: str = "",
    control_id_override: str = "",
) -> str:
    control = ""
    if record["control"]:
        identity = control_id_override or str(record["id"])
        label = "대기접수" if record["status"] == "대기접수" else "신청하기"
        control_query = urlencode(
            (
                ("amode", "ins_realname"),
                ("lecIdx", identity),
                (ledger.facility_key, ledger.facility_value),
            )
        )
        control = (
            '<div class="infomenu1"><a class="button large primary" '
            f'href="?{control_query}">{label}</a></div>'
        )
    return f"""
      <html><body><div id="body_content">
        <h1 class="h1 cv0">{title_override or record['title']}</h1>
        <table class="w100 t3 thtac ttvam"><tbody>
          <tr><th scope="row">교육기간</th><td>{period_override or record['period']}</td></tr>
          <tr><th scope="row">접수기간</th><td>{record['apply']}</td></tr>
          <tr><th scope="row">교육시간</th><td>{record['schedule']}</td></tr>
          <tr><th scope="row">교육장소</th><td>{record['venue']}</td></tr>
          <tr><th scope="row">준비물</th><td>개인 준비물 010-9999-9999</td></tr>
          <tr><th scope="row">수강료</th><td>{record['fee']}</td></tr>
          <tr><th scope="row">모집대상</th><td>{record['target']}</td></tr>
          <tr><th scope="row">모집지역</th><td>관내</td></tr>
          <tr><th scope="row">접수방법</th><td>{record['method']}</td></tr>
          <tr><th scope="row">이용문의</th><td>055-930-3169</td></tr>
          <tr><th scope="row">인원</th><td>정원 : {record['capacity']}<br>현재접수인원 : 7</td></tr>
          <tr><th scope="row">강사명</th><td>홍길동</td></tr>
          <tr><th scope="row">교육소개</th><td>개인 소개 teacher@example.org</td></tr>
          <tr><th scope="row">첨부파일</th><td><a href="/private.hwp">계획서</a></td></tr>
        </tbody></table>
        {control}
      </div></body></html>
    """


@dataclass
class DummySession:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class FakeSite:
    def __init__(
        self,
        *,
        bad_clamp: bool = False,
        mutate_recheck: bool = False,
        unknown_status: bool = False,
        bad_branch: bool = False,
        duplicate_identity: bool = False,
        detail_title_mismatch: bool = False,
        detail_period_mismatch: bool = False,
        wrong_control_id: bool = False,
        fail_once_detail: bool = False,
    ) -> None:
        self.bad_clamp = bad_clamp
        self.mutate_recheck = mutate_recheck
        self.unknown_status = unknown_status
        self.bad_branch = bad_branch
        self.duplicate_identity = duplicate_identity
        self.detail_title_mismatch = detail_title_mismatch
        self.detail_period_mismatch = detail_period_mismatch
        self.wrong_control_id = wrong_control_id
        self.fail_once_detail = fail_once_detail
        self.calls: Counter[tuple[str, str]] = Counter()
        self.sessions: list[DummySession] = []
        self.application_fetches = 0

    def session_factory(self) -> DummySession:
        current = DummySession()
        self.sessions.append(current)
        return current

    def _records(self, ledger: hapcheon.HapcheonLedger) -> tuple[dict[str, Any], ...]:
        source = MAIN_RECORDS if ledger.key == "main" else LITERACY_RECORDS
        rows = [dict(record) for record in source]
        if ledger.key == "main" and self.unknown_status:
            rows[0]["status"] = "임의상태"
        if ledger.key == "main" and self.bad_branch:
            rows[0]["branch"] = "임의교육원"
        if ledger.key == "main" and self.duplicate_identity:
            rows[1]["id"] = rows[0]["id"]
        return tuple(rows)

    def fetcher(
        self,
        _session: DummySession,
        method: str,
        url: str,
        *,
        timeout: int,
        data: Mapping[str, str],
    ) -> tuple[BeautifulSoup, str]:
        assert method == "GET"
        assert timeout > 0
        assert data == {}
        parsed = urlparse(url)
        assert parsed.hostname == hapcheon.HAPCHEON_HOST
        ledger = next(item for item in hapcheon.HAPCHEON_LEDGERS if item.path == parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query.get("amode") in (["ins"], ["ins_realname"]):
            self.application_fetches += 1
            raise AssertionError("application endpoint must never be fetched")

        records = self._records(ledger)
        if query.get("amode") == ["view"]:
            identity = query["idx"][0]
            self.calls[("detail", f"{ledger.key}:{identity}")] += 1
            if (
                self.fail_once_detail
                and identity == "210"
                and self.calls[("detail", "main:210")] == 1
            ):
                raise ConnectionError("synthetic transient reset")
            record = next(item for item in records if str(item["id"]) == identity)
            html = _detail_html(
                ledger,
                record,
                title_override=(
                    "다른 상세 제목"
                    if self.detail_title_mismatch and identity == "210"
                    else ""
                ),
                period_override=(
                    "2099.01.01 ~ 2099.01.02"
                    if self.detail_period_mismatch and identity == "210"
                    else ""
                ),
                control_id_override=(
                    "999" if self.wrong_control_id and identity == "210" else ""
                ),
            )
            return BeautifulSoup(html, "lxml"), url

        requested = int((query.get("cpage") or ["1"])[0])
        self.calls[("list", f"{ledger.key}:{requested}")] += 1
        page_size = hapcheon.HAPCHEON_PAGE_SIZE
        last_page = max(1, (len(records) + page_size - 1) // page_size)
        displayed = min(requested, last_page)
        start = (displayed - 1) * page_size
        selected = records[start : start + page_size]
        title_override = ""
        if requested > last_page and self.bad_clamp:
            title_override = "잘못된 sentinel 강좌"
        if (
            requested == 1
            and self.mutate_recheck
            and self.calls[("list", f"{ledger.key}:1")] > 1
        ):
            title_override = "재확인 중 바뀐 강좌"
        html = _list_html(
            ledger,
            requested_page=requested,
            displayed_page=displayed,
            records=tuple(selected),
            total=len(records),
            last_page=last_page,
            title_override=title_override,
        )
        return BeautifulSoup(html, "lxml"), url


def _collect(site: FakeSite, **kwargs: Any):
    return hapcheon.collect_hapcheon_education(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 5),
        detail_limit=kwargs.pop("detail_limit", 10),
        max_workers=1,
        today=kwargs.pop("today", "2099-07-20"),
        session_factory=site.session_factory,
        fetcher=site.fetcher,
        **kwargs,
    )


def test_target_candidate_override_and_owner_boundaries() -> None:
    assert hapcheon.is_target(_target())
    assert not hapcheon.is_target(_target(provider=hapcheon.HAPCHEON_REVIEW_PROVIDER))
    assert not hapcheon.is_target(_target(url=hapcheon.HAPCHEON_PORTAL_MIRROR_URL))
    assert not hapcheon.is_target(_target(url=hapcheon.HAPCHEON_URL + "?cpage=1"))
    assert hapcheon.HAPCHEON_CANONICAL_CANDIDATE_ID == "MUNI_IR_E06BB8D5CD0D"
    assert hapcheon.HAPCHEON_REVIEW_CANDIDATE_ID == "MUNI_IR_9823192E9747"
    assert hapcheon.HAPCHEON_CANDIDATE_DECISIONS == {
        "MUNI_IR_E06BB8D5CD0D": (
            "manual_override_promote_new_complete_county_education_owner"
        ),
        "MUNI_IR_9823192E9747": (
            "exclude_education_support_office_home_not_course_identity_ledger"
        ),
    }
    assert len(hapcheon.HAPCHEON_OFFICIAL_BRANCHES) == 5
    assert "https://hcedu.gne.go.kr/" in hapcheon.HAPCHEON_OWNER_BOUNDARIES
    assert "https://www.hc.go.kr/09363/09364/09364.web" in hapcheon.HAPCHEON_OWNER_BOUNDARIES


def test_complete_two_ledger_snapshot_details_controls_branches_and_privacy() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == hapcheon.HAPCHEON_PARSER
    assert len(rows) == 5
    assert meta["ledger_totals"] == {"main": 10, "literacy": 3}
    assert meta["ledger_pages"] == {"main": 2, "literacy": 1}
    assert meta["source_total"] == 13
    assert meta["list_requests"] == 8
    assert meta["sentinel_pages"] == {"main": 3, "literacy": 2}
    assert meta["sentinel_counts"] == {"main": 1, "literacy": 3}
    assert meta["stable_rechecks"] == {
        "main": {"1": True, "2": True},
        "literacy": {"1": True},
    }
    assert meta["current_count"] == 5
    assert meta["expired_count"] == 8
    assert meta["ledger_current_counts"] == {"main": 5}
    assert meta["detail_attempts"] == meta["detail_pages"] == 5
    assert meta["application_control_count"] == 2
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["pii_payload_persisted"] is False
    assert site.application_fetches == 0
    assert all(session.closed for session in site.sessions)

    by_id = {row["raw_fields"]["source_education_id"]: row for row in rows}
    assert by_id["210"]["status"] == "OPEN"
    assert by_id["210"]["application_type"] == "ONLINE_RESERVATION"
    assert by_id["210"]["reservation_available"] is True
    assert parse_qs(urlparse(by_id["210"]["application_url"]).query)["lecIdx"] == ["210"]
    assert by_id["209"]["status"] == "WAITING"
    assert by_id["209"]["application_type"] == "WAITLIST_APPLY"
    assert by_id["208"]["status"] == "OPEN"
    assert by_id["208"]["reservation_available"] is False
    assert by_id["207"]["status"] == "SCHEDULED"
    assert by_id["206"]["status"] == "CLOSED"
    assert {row["branch"] for row in rows} == set(hapcheon.HAPCHEON_MAIN_BRANCHES)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["municipality_code"] == "4889000000" for row in rows)
    assert all(row["raw_fields"]["application_form_fetched"] is False for row in rows)

    serialized = repr(rows)
    assert "055-" not in serialized
    assert "010-" not in serialized
    assert "teacher@example.org" not in serialized
    assert "홍길동" not in serialized
    assert "현재접수인원" not in serialized
    assert "교육소개" not in serialized
    assert "첨부파일" not in serialized


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"max_pages": 1}, "max_pages cap"),
        ({"detail_limit": 4}, "detail_limit cap"),
    ],
)
def test_required_caps_fail_closed(kwargs: dict[str, int], error: str) -> None:
    rows, _, meta = _collect(FakeSite(), **kwargs)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert error in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


@pytest.mark.parametrize(
    ("site", "error"),
    [
        (FakeSite(bad_clamp=True), "immediate post-last clamp"),
        (FakeSite(mutate_recheck=True), "changed on recheck"),
        (FakeSite(unknown_status=True), "unknown source status"),
        (FakeSite(bad_branch=True), "outside official vocabulary"),
        (FakeSite(duplicate_identity=True), "duplicate source identities"),
        (FakeSite(detail_title_mismatch=True), "list/detail title mismatch"),
        (FakeSite(detail_period_mismatch=True), "list/detail education period mismatch"),
        (FakeSite(wrong_control_id=True), "malformed public application control"),
    ],
)
def test_structural_identity_and_detail_drift_fail_closed(
    site: FakeSite, error: str
) -> None:
    rows, _, meta = _collect(site)
    assert rows == []
    assert error in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False
    assert site.application_fetches == 0


def test_transient_detail_request_is_retried_without_fetching_application() -> None:
    site = FakeSite(fail_once_detail=True)
    rows, _, meta = _collect(site)
    assert len(rows) == 5
    assert meta["request_retry_count"] == 1
    assert meta["logical_requests"] + 1 == meta["physical_requests"]
    assert site.calls[("detail", "main:210")] == 2
    assert site.application_fetches == 0


def test_invalid_parameters_and_wrong_target_do_not_touch_network() -> None:
    site = FakeSite()
    rows, _, meta = hapcheon.collect(
        _target(provider="MUNI_WRONG"),
        session_factory=site.session_factory,
        fetcher=site.fetcher,
    )
    assert rows == []
    assert "outside canonical Hapcheon scope" in meta["configured_collection_error"]
    assert site.sessions == []

    rows, _, meta = hapcheon.collect(
        _target(),
        max_pages=0,
        session_factory=site.session_factory,
        fetcher=site.fetcher,
    )
    assert rows == []
    assert "are invalid" in meta["configured_collection_error"]
    assert site.sessions == []


@pytest.mark.skipif(
    os.getenv("RUN_HAPCHEON_LIVE_TESTS") != "1",
    reason="set RUN_HAPCHEON_LIVE_TESTS=1 for official live validation",
)
def test_live_complete_hapcheon_snapshot() -> None:
    rows, parser, meta = hapcheon.collect(
        _target(),
        timeout=30,
        max_pages=30,
        detail_limit=50,
        max_workers=4,
        today="2026-07-23",
    )
    assert parser == hapcheon.HAPCHEON_PARSER
    assert meta["ledger_totals"]["main"] >= 217
    assert meta["ledger_totals"]["literacy"] >= 3
    assert meta["source_total"] == sum(meta["ledger_totals"].values())
    assert meta["ledger_pages"]["main"] >= 25
    assert meta["sentinel_mode"] == "exact_clamped_final_page"
    assert all(all(values.values()) for values in meta["stable_rechecks"].values())
    assert meta["current_count"] == len(rows)
    assert meta["detail_pages"] == len(rows)
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["pii_payload_persisted"] is False
    assert all(row["branch"] in hapcheon.HAPCHEON_OFFICIAL_BRANCHES for row in rows)
    serialized = repr(rows)
    assert "055-" not in serialized
    assert "010-" not in serialized
    assert "@" not in serialized
