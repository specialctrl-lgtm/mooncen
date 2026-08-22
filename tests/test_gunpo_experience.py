from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import municipal_gunpo
from Crawler import municipal_gunpo_experience as gunpo
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


ROOT = Path(__file__).resolve().parents[1]


def _target(**updates: str) -> dict[str, str]:
    target = {
        "provider": gunpo.GUNPO_EXPERIENCE_PROVIDER,
        "url": gunpo.GUNPO_EXPERIENCE_URL,
    }
    target.update(updates)
    return target


def test_exact_target_is_separate_from_nine_education_owners() -> None:
    assert gunpo.is_gunpo_experience_target(_target())
    assert not gunpo.is_gunpo_experience_target(
        _target(url=gunpo.GUNPO_EXPERIENCE_URL + "#fragment")
    )
    assert not gunpo.is_gunpo_experience_target(
        _target(url=gunpo.GUNPO_EXPERIENCE_URL.replace("https://", "http://"))
    )
    assert not gunpo.is_gunpo_experience_target(
        _target(url=gunpo.GUNPO_EXPERIENCE_URL.replace("https://", "https://u:p@"))
    )
    assert not municipal_gunpo.is_gunpo_education_target(_target())
    assert gunpo.GUNPO_EXPERIENCE_PROVIDER not in {
        config["provider"] for config in municipal_gunpo.GUNPO_OWNERS.values()
    }
    assert len(municipal_gunpo.GUNPO_OWNERS) == 9


def test_provider_and_candidate_ids_follow_repository_url_hashes() -> None:
    assert gunpo.GUNPO_EXPERIENCE_PROVIDER == stable_provider(
        gunpo.GUNPO_EXPERIENCE_URL
    )
    assert gunpo.GUNPO_EXPERIENCE_CANDIDATE_ID == candidate_id(
        normalized_duplicate_url(gunpo.GUNPO_EXPERIENCE_URL)
    )


def test_production_collection_requires_managed_session() -> None:
    rows, parser, meta = gunpo.collect(_target(), today="2026-08-05")

    assert rows == []
    assert parser == gunpo.GUNPO_EXPERIENCE_PARSER
    assert meta["snapshot_complete"] is False
    assert meta["application_endpoints_called"] == 0
    assert "session_factory" in meta["configured_collection_error"]


class _NeverSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> Any:
        self.calls.append(url)
        raise AssertionError("network must not be reached")

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    "url",
    (
        gunpo.GUNPO_EXPERIENCE_APPLICATION_ENDPOINT
        + "?key=1008275&searchEtcResveNo=1721",
        "https://www.gunpo.go.kr/www/login.do?key=4693",
        "https://www.gunpo.go.kr/portal/downloadResveAtchmnflFileStr.do?file=x",
        "https://www.gunpo.go.kr/portal/myReqsEtcResveList.do?key=1008286",
    ),
)
def test_runner_refuses_application_login_attachment_and_personal_routes(
    url: str,
) -> None:
    session = _NeverSession()
    with gunpo._Runner(lambda: session, 10) as runner:
        with pytest.raises(gunpo.GunpoExperienceContractError, match="endpoint"):
            runner.soup(url)
    assert session.calls == []


@pytest.mark.parametrize(
    ("title", "expected"),
    (
        ("2026 방짜유기 체험프로그램 수강생 모집", (True, "explicit_experience_title")),
        ("[공지] 체험 신청 안내", (False, "notice")),
        ("재난지원금 신청", (False, "administrative_benefit")),
        ("체험관 대관 신청", (False, "facility_rental")),
        ("시민참여단 모집", (False, "committee_or_club")),
        ("VR＆AI 모의면접 체험 서비스 신청", (False, "service_application")),
        ("청년 단기행정인턴 모집", (False, "employment_recruitment")),
        ("대학입시 설명회", (False, "education_only")),
        ("나눔장터 참가자 모집", (False, "other_recruitment")),
        ("군포 특별 프로그램 신청", (False, "ambiguous")),
    ),
)
def test_mixed_ledger_classification_is_explicit(
    title: str, expected: tuple[bool, str]
) -> None:
    assert gunpo._classification(title, "") == expected


def _list_row(
    identity: str,
    title: str,
    *,
    status: str = "접수중",
    apply_period: str = "2026-08-01 ~ 2026-09-30",
    event_period: str = "2026-08-10 ~ 2026-10-31",
) -> str:
    return f"""
      <tr>
        <td>{status}</td>
        <td>군포시청</td>
        <td>
          <a href="./webEtcResveView.do?key=1008275&amp;searchEtcResveNo={identity}">{title}</a>
          <span>031-000-0000</span>
        </td>
        <td>신청 : {apply_period} 행사 : {event_period}</td>
        <td>군포시민</td>
        <td>온 온라인</td>
      </tr>
    """


def _list_page(total: int, page: int, rows: str) -> str:
    return f"""
      <html><head><title>행사/모집 - 교육문화행사포털</title></head><body>
        <main id="contents">
          <form id="frm" method="get" action="./webEtcResveList.do">
            <input type="hidden" name="key" value="1008275">
            <input type="hidden" name="rep" value="1">
            <input type="hidden" name="searchGubun" value="S">
          </form>
          <span class="small">총 {total} 건 [ {page} /1 페이지 ]</span>
          <table class="p-table"><tbody>{rows}</tbody></table>
        </main>
      </body></html>
    """


def _detail(identity: str, title: str, detail_text: str = "행사 신청 안내") -> str:
    return f"""
      <html><head><title>행사/모집 - 교육문화행사포털</title></head><body>
        <main id="contents"><div class="etc view"><div class="p-wrap bbs bbs__view">
          <table class="p-table"><tbody class="p-table--th-left">
            <tr class="p-table__subject"><td colspan="4">
              <strong class="detail_info_title"><span class="p-table__subject_text">{title}</span></strong>
            </td></tr>
            <tr><th>신청기간</th><td>2026-08-01 ~ 2026-09-30</td>
                <th>행사기간</th><td>2026-08-10 ~ 2026-10-31</td></tr>
            <tr><th>장소</th><td colspan="3">군포시 방짜유기 전수교육관(군포시 송부로12) 방짜유기전수교육관</td></tr>
            <tr><th>신청방법</th><td>온라인 ※ 선착순</td>
                <th>신청대상</th><td>군포시민</td></tr>
            <tr><th>주최</th><td>문화예술과</td><th>주관</th><td>군포시</td></tr>
            <tr><th>문의</th><td>031-000-0000</td>
                <th>신청현황</th><td>정원 : 3 / 30 대기 : 0 / 10</td></tr>
            <tr><th>첨부파일</th><td colspan="3">
              <a href="./downloadResveAtchmnflFileStr.do?file=secret">신청서</a>
            </td></tr>
          </tbody></table>
          <h4>상세정보</h4><p>{detail_text}</p>
          <div class="btn_group">
            <a href="./webEtcResveList.do?key=1008275&amp;rep=1">목록</a>
            <a href="./webEtcResveApplcntAgree.do?key=1008275&amp;searchEtcResveNo={identity}">신청하기</a>
          </div>
        </div></div></main>
      </body></html>
    """


_CURRENT_ROWS = (
    ("1721", "2026년 방짜유기전수교육관 체험프로그램 수강생 모집"),
    ("1701", "2026 재난지원금 신청"),
    ("1681", "2026 청년 단기행정인턴 모집"),
    ("1661", "군포시청 주차장 이용자 모집"),
    ("1641", "스마트도시 시민참여단 모집"),
    ("1621", "VR＆AI 모의면접 체험 서비스 신청"),
    ("1601", "[공지] 행사 신청 안내"),
)


class _Response:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.content = text.encode()
        self.status_code = 200
        self.history: list[Any] = []


class _FixtureSession:
    def __init__(
        self,
        *,
        current_rows: tuple[tuple[str, str], ...] = _CURRENT_ROWS,
        nonempty_sentinel: bool = False,
        unstable_first: bool = False,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.current_rows = current_rows
        self.nonempty_sentinel = nonempty_sentinel
        self.unstable_first = unstable_first
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.page_one_calls = 0
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path.endswith("webEtcResveList.do"):
            page = int(query["pageIndex"][0])
            total = len(self.current_rows) + 1
            if page == 2:
                rows = (
                    _list_row("9999", "sentinel drift")
                    if self.nonempty_sentinel
                    else ""
                )
                return _Response(url, _list_page(total, 2, rows))
            self.page_one_calls += 1
            rows = "".join(
                _list_row(identity, title)
                for identity, title in self.current_rows
            )
            rows += _list_row(
                "1581",
                "지난 체험프로그램",
                status="완료",
                apply_period="2025-01-01 ~ 2025-01-10",
                event_period="2025-01-11 ~ 2025-01-12",
            )
            if self.unstable_first and self.page_one_calls > 1:
                rows = rows.replace("searchEtcResveNo=1721", "searchEtcResveNo=1722")
            return _Response(url, _list_page(total, 1, rows))
        if parsed.path.endswith("webEtcResveView.do"):
            identity = query["searchEtcResveNo"][0]
            title = dict(self.current_rows)[identity]
            return _Response(url, _detail(identity, title))
        raise AssertionError(f"unexpected public GET {url}")

    def close(self) -> None:
        self.closed = True


def test_complete_snapshot_returns_only_one_explicit_experience() -> None:
    session = _FixtureSession()
    rows, parser, meta = gunpo.collect(
        _target(),
        today="2026-08-05",
        session_factory=lambda: session,
    )

    assert parser == gunpo.GUNPO_EXPERIENCE_PARSER
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["source_total"] == 8
    assert meta["source_rows"] == 8
    assert meta["source_current_count"] == 7
    assert meta["current_count"] == 1
    assert meta["returned_count"] == 1
    assert meta["expired_count"] == 1
    assert meta["classification_excluded_current_count"] == 6
    assert meta["classification_exclusion_counts"] == {
        "administrative_benefit": 1,
        "employment_recruitment": 1,
        "facility_rental": 1,
        "committee_or_club": 1,
        "service_application": 1,
        "notice": 1,
    }
    assert meta["sentinel_page"] == 2
    assert meta["stable_first_page"] is True
    assert meta["stable_last_page"] is True
    assert meta["stable_sentinel"] is True
    assert meta["details_complete"] is True
    assert meta["classification_complete"] is True
    assert meta["application_endpoints_called"] == 0
    assert meta["attachment_endpoints_called"] == 0
    assert meta["pii_endpoints_called"] == 0
    assert len(rows) == 1
    row = rows[0]
    assert row["source_course_id"] == "1721"
    assert row["branch"] == "방짜유기전수교육관"
    assert row["venue_address"] == "경기도 군포시 송부로 12"
    assert row["service_group"] == "체험"
    assert row["classification_locked"] is True
    assert row["reservation_available"] is True
    assert row["application_url"] == row["raw_url"]
    assert "Applcnt" not in row["application_url"]
    assert "031-000-0000" not in str(row)
    assert session.closed is True
    assert all(kwargs["verify"] is True for _, kwargs in session.calls)
    assert all(kwargs["allow_redirects"] is False for _, kwargs in session.calls)
    assert all(
        urlparse(url).path
        in {"/portal/webEtcResveList.do", "/portal/webEtcResveView.do"}
        for url, _ in session.calls
    )


def test_unknown_current_row_fails_closed_instead_of_being_dropped() -> None:
    session = _FixtureSession(current_rows=(("1721", "군포 특별 프로그램 신청"),))
    rows, _, meta = gunpo.collect(
        _target(), today="2026-08-05", session_factory=lambda: session
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "unclassified current/future" in meta["configured_collection_error"]
    assert meta["application_endpoints_called"] == 0


@pytest.mark.parametrize(
    ("session", "message"),
    (
        (_FixtureSession(nonempty_sentinel=True), "post-last page"),
        (_FixtureSession(unstable_first=True), "first-page identities"),
    ),
)
def test_boundary_drift_fails_the_atomic_snapshot(
    session: _FixtureSession, message: str
) -> None:
    rows, _, meta = gunpo.collect(
        _target(), today="2026-08-05", session_factory=lambda: session
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_dispatch_injects_managed_session_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any) -> tuple[list[Any], str, dict[str, Any]]:
        captured.update(kwargs)
        return [], "gunpo-experience", {"snapshot_complete": True}

    monkeypatch.setattr(gunpo, "collect_gunpo_experience_courses", collect)
    target = municipal.CrawlTarget(
        provider=gunpo.GUNPO_EXPERIENCE_PROVIDER,
        name="군포시 체험",
        branch=gunpo.GUNPO_EXPERIENCE_BRANCH,
        url=gunpo.GUNPO_EXPERIENCE_URL,
        source="test",
    )
    municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=20,
        detail_limit=100,
    )

    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "allow_raw_requests_for_tests" not in captured


def test_target_operational_and_coverage_linkage() -> None:
    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    matches = [
        item
        for item in targets
        if item.get("provider") == gunpo.GUNPO_EXPERIENCE_PROVIDER
        and item.get("url") == gunpo.GUNPO_EXPERIENCE_URL
    ]
    assert len(matches) == 1
    target = matches[0]
    assert target["crawler_module"] == "Crawler.municipal_gunpo_experience"
    assert target["crawler_callable"] == "collect_gunpo_experience_courses"
    assert target["service_group"] == "체험"
    assert target["full_snapshot_required"] is True
    assert target["last_quality"]["collected"] == 1

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    entries = [
        item
        for item in operational
        if item.get("provider") == gunpo.GUNPO_EXPERIENCE_PROVIDER
        and item.get("target_url") == gunpo.GUNPO_EXPERIENCE_URL
    ]
    assert len(entries) == 1
    assert entries[0]["validation_outcome"] == "collected"
    assert entries[0]["row_count"] == 1

    coverage = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )
    municipality = next(
        item
        for item in coverage["municipalities"]
        if item.get("code") == gunpo.GUNPO_EXPERIENCE_MUNICIPALITY_CODE
    )
    assert gunpo.GUNPO_EXPERIENCE_PROVIDER in municipality["owner_providers"]
    assert gunpo.GUNPO_EXPERIENCE_PROVIDER in municipality["promoted_providers"]
    assert gunpo.GUNPO_EXPERIENCE_PROVIDER in municipality["yaml_owner_providers"]
    assert any(
        evidence.get("provider") == gunpo.GUNPO_EXPERIENCE_PROVIDER
        and evidence.get("target_url") == gunpo.GUNPO_EXPERIENCE_URL
        and evidence.get("row_count") == 1
        for evidence in municipality["evidence"]
    )


def test_live_baseline_documents_the_single_current_experience() -> None:
    assert gunpo.GUNPO_EXPERIENCE_LIVE_BASELINE == {
        "checked_at": "2026-08-05",
        "source_total": 89,
        "data_pages": 1,
        "sentinel_page": 2,
        "source_current_count": 1,
        "experience_current_count": 1,
        "current_identity": "1721",
    }
