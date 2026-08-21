from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_gimpo_experience as gimpo
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


TARGETS = (
    (gimpo.GIMPO_EXPERIENCE_PROVIDER, gimpo.GIMPO_EXPERIENCE_URL),
    (gimpo.GIMPO_LEISURE_PROVIDER, gimpo.GIMPO_LEISURE_URL),
)


@pytest.mark.parametrize("provider,url", TARGETS)
def test_exact_official_experience_targets(provider: str, url: str) -> None:
    target = {"provider": provider, "url": url}
    assert gimpo.is_gimpo_experience_target(target)
    assert not gimpo.is_gimpo_experience_target(
        {"provider": provider, "url": url + "#fragment"}
    )
    assert not gimpo.is_gimpo_experience_target(
        {"provider": provider, "url": url.replace("https://", "http://")}
    )
    assert not gimpo.is_gimpo_experience_target(
        {"provider": provider, "url": url.replace("https://", "https://u:p@")}
    )


def test_provider_and_candidate_ids_follow_repository_url_hashes() -> None:
    for provider, candidate, url in (
        (
            gimpo.GIMPO_EXPERIENCE_PROVIDER,
            gimpo.GIMPO_EXPERIENCE_CANDIDATE_ID,
            gimpo.GIMPO_EXPERIENCE_URL,
        ),
        (
            gimpo.GIMPO_LEISURE_PROVIDER,
            gimpo.GIMPO_LEISURE_CANDIDATE_ID,
            gimpo.GIMPO_LEISURE_URL,
        ),
    ):
        assert provider == stable_provider(url)
        assert candidate == candidate_id(normalized_duplicate_url(url))


@pytest.mark.parametrize("provider,url", TARGETS)
def test_production_collection_requires_managed_session(provider: str, url: str) -> None:
    rows, parser, meta = gimpo.collect(
        {"provider": provider, "url": url}, today="2026-08-05"
    )
    assert rows == []
    assert parser == gimpo.GIMPO_PARSER
    assert meta["snapshot_complete"] is False
    assert meta["application_endpoints_called"] == 0
    assert "session_factory" in meta["configured_collection_error"]


@pytest.mark.parametrize("provider,url", TARGETS)
def test_dispatch_route_injects_managed_session_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    url: str,
) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any) -> tuple[list[Any], str, dict[str, Any]]:
        captured.update(kwargs)
        return [], "gimpo-experience", {"snapshot_complete": True}

    monkeypatch.setattr(gimpo, "collect_gimpo_experience_courses", collect)
    target = municipal.CrawlTarget(
        provider=provider,
        name="김포시 견학·체험",
        branch="경기도 김포시",
        url=url,
        source="test",
    )
    municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=30,
        detail_limit=100,
    )
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "allow_raw_requests_for_tests" not in captured


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
        gimpo.GIMPO_EXPERIENCE_APPLICATION_ENDPOINT
        + "?key=113&etcProgramSection=EXPERIENCE&searchEtcResveNo=101",
        gimpo.GIMPO_LEISURE_APPLICATION_ENDPOINT + "?srvcNo=350&key=113",
        "https://www.gimpo.go.kr/reserve/mberLogin.do?key=4978",
    ),
)
def test_runner_refuses_application_and_login_endpoints_before_network(url: str) -> None:
    session = _NeverSession()
    with gimpo._Runner(lambda: session, 10) as runner:
        with pytest.raises(gimpo.GimpoExperienceContractError, match="endpoint"):
            runner.soup(url)
    assert session.calls == []


def _generic_item(
    identity: str,
    title: str,
    *,
    status: str = "접수중",
    apply_range: str = "2026-08-01 ~ 2026-08-10",
    event_range: str = "2026-08-11 ~ 2026-08-11",
) -> str:
    return f"""
      <li class="participation_item">
        <div class="participation_title">
          <span class="participation_label type2">{status}</span>
          <a href="./webEtcResveView.do?key=113&amp;etcProgramSection=EXPERIENCE&amp;searchEtcGroup=0&amp;searchEtcResveNo={identity}">
            <strong>{title}</strong>
          </a>
        </div>
        <div class="participation_information"><ul>
          <li><span class="participation_information_subject">대상</span>제한없음</li>
          <li><span class="participation_information_subject">장소</span>김포 체험실</li>
          <li><span class="participation_information_subject">신청</span>{apply_range}</li>
          <li><span class="participation_information_subject">행사</span>{event_range}</li>
          <li><span class="participation_information_subject">문의</span>031-000-0000</li>
        </ul></div>
      </li>
    """


def _generic_page(total: int, rows: str) -> str:
    return f"""
      <html><head><title>견학/체험 - 김포시통합예약</title></head><body>
        <h1>견학/체험</h1>
        <form id="frm" method="get" action="./webEtcResveList.do">
          <input type="hidden" name="key" value="113">
          <input type="hidden" name="etcProgramSection" value="EXPERIENCE">
          <input type="hidden" name="rep" value="1">
        </form>
        <span class="small">총 <em class="em_black">{total}</em> 건</span>
        <ul class="participation_list">{rows}</ul>
      </body></html>
    """


def _generic_detail(identity: str, title: str) -> str:
    return f"""
      <html><head><title>견학/체험 - 김포시통합예약</title></head><body>
        <div class="participation view"><div class="p-wrap bbs bbs__view">
          <h3 class="h0">{title}</h3>
          <ul class="participation_info_list">
            <li><span class="participation_info_subject">운영기간</span>2026-08-11(화) ~ 2026-08-11(화)</li>
            <li><span class="participation_info_subject">신청기간</span>2026-08-01(토) ~ 2026-08-10(월)</li>
            <li><span class="participation_info_subject">운영요일</span>화요일</li>
            <li><span class="participation_info_subject">문의전화</span>031-000-0000</li>
            <li><span class="participation_info_subject">운영기관</span>김포시농업기술센터</li>
            <li><span class="participation_info_subject">대상</span>제한없음</li>
          </ul>
          <a class="btn write" href="./webEtcResveApplcntAgree.do?key=113&amp;etcProgramSection=EXPERIENCE&amp;searchEtcResveNo={identity}">신청하기</a>
          <script>fn_setAddressToMapPosition('경기도 김포시 월곶면 체험로 1', '{title}');</script>
        </div></div>
      </body></html>
    """


def test_general_page_locks_experience_and_marks_notice_test_only() -> None:
    soup = BeautifulSoup(
        _generic_page(
            3,
            _generic_item("101", "2026년 가족 체험 안내(네이버 예약 이용)")
            + _generic_item("102", "(공지사항) 자주 묻는 질문", status="완료")
            + _generic_item("103", "테스트(A) - 예약하지 마세요", status="완료"),
        ),
        "lxml",
    )
    rows = gimpo._experience_page(soup)
    assert len(rows) == 3
    assert rows[0]["raw_fields"]["explicit_non_program"] is False
    assert rows[0]["program_type"] == "체험"
    assert rows[0]["domain_category"] == "체험·견학"
    assert rows[0]["service_group"] == "체험"
    assert rows[0]["service_group_policy"] == "locked"
    assert rows[0]["classification_locked"] is True
    assert rows[1]["raw_fields"]["non_program_reason"] == "notice"
    assert rows[2]["raw_fields"]["non_program_reason"] == "test"
    assert "031-000-0000" not in str(rows)


def _leisure_item(identity: str, title: str, status: str = "접수중") -> str:
    return f"""
      <li class="participation_item">
        <div class="participation_title">
          <a href="./step0TnLesureApplcntViewU.do?srvcNo={identity}&amp;key=113">
            <span class="participation_label type1">{status}</span>
          </a>
          <a href="./viewTnLesureResveU.do?srvcNo={identity}&amp;key=113"><strong>{title}</strong></a>
        </div>
        <div class="participation_information"><ul>
          <li><span class="participation_information_subject">신청기간</span>2026-08-03 ~ 2026-08-13</li>
          <li><span class="participation_information_subject">방문기간</span>2026-08-10 ~ 2026-08-13</li>
          <li><span class="participation_information_subject">문의</span>070-0000-0000</li>
        </ul></div>
      </li>
    """


def _leisure_page(total: int, rows: str) -> str:
    return f"""
      <html><head><title>견학/체험 - 김포시통합예약</title></head><body>
        <main class="participation list"><h1>견학/체험</h1><div>총 {total}건</div>
          <ul class="participation_list">{rows}</ul>
        </main>
      </body></html>
    """


def _leisure_detail(identity: str, title: str) -> str:
    return f"""
      <html><head><title>견학/체험 - 김포시통합예약</title></head><body>
        <div class="participation view"><div class="p-wrap bbs bbs__view">
          <h3 class="h0"><span class="p-badge type1">접수중</span><span class="p-badge type5">온라인</span>{title}</h3>
          <ul class="participation_info_list">
            <li><span class="participation_info_subject">신청기간</span>2026-08-03 ~ 2026-08-13</li>
            <li><span class="participation_info_subject">방문기간</span>2026-08-10 ~ 2026-08-13</li>
            <li><span class="participation_info_subject">방문요일</span>월, 화, 수, 목</li>
            <li><span class="participation_info_subject">방문시간</span>18:15~18:45</li>
            <li><span class="participation_info_subject">이용요금</span>대당 25,000원</li>
          </ul>
          <a class="btn write" href="./step0TnLesureApplcntViewU.do?srvcNo={identity}&amp;key=113">예약신청</a>
          <script>fn_setAddressToMapPosition('경기도 김포시 김포한강2로 1', '{title}');</script>
        </div></div>
      </body></html>
    """


def test_leisure_page_has_distinct_identity_and_locked_experience_scope() -> None:
    soup = BeautifulSoup(_leisure_page(1, _leisure_item("350", "패밀리보트")), "lxml")
    row = gimpo._leisure_page(soup)[0]
    assert row["provider"] == gimpo.GIMPO_LEISURE_PROVIDER
    assert row["provider_course_id"].endswith(":leisure:350")
    assert row["provider_course_id"] != (
        f"{gimpo.GIMPO_EXPERIENCE_PROVIDER}:experience:350"
    )
    assert row["branch"] == gimpo.GIMPO_LEISURE_BRANCH
    assert row["municipality_code"] == "4157000000"
    assert row["program_type"] == "체험"
    assert "070-0000-0000" not in str(row)


def test_leisure_source_total_accepts_live_small_markup() -> None:
    soup = BeautifulSoup('<span class="small">총 8 건</span>', "lxml")

    assert gimpo._source_total(soup, leisure=True) == 8


class _Response:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.content = text.encode()
        self.status_code = 200
        self.history: list[Any] = []


class _FixtureSession:
    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []
        self.closed = False

    def get(self, url: str, **_: Any) -> _Response:
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if self.owner == "experience":
            if parsed.path.endswith("webEtcResveList.do"):
                page = int((query.get("pageIndex") or ["1"])[0])
                if page == 2:
                    return _Response(url, _generic_page(3, ""))
                rows = (
                    _generic_item("101", "가족 체험")
                    + _generic_item(
                        "102",
                        "지난 체험",
                        status="완료",
                        apply_range="2026-06-01 ~ 2026-06-10",
                        event_range="2026-06-11 ~ 2026-06-11",
                    )
                    + _generic_item("103", "(공지사항) 신청 안내")
                )
                return _Response(url, _generic_page(3, rows))
            if parsed.path.endswith("webEtcResveView.do"):
                identity = query["searchEtcResveNo"][0]
                assert identity == "101"
                return _Response(url, _generic_detail(identity, "가족 체험"))
        if self.owner == "leisure":
            if parsed.path.endswith("selectTnLesureResveListU.do"):
                rows = _leisure_item("350", "패밀리보트")
                return _Response(url, _leisure_page(1, rows))
            if parsed.path.endswith("viewTnLesureResveU.do"):
                identity = query["srvcNo"][0]
                return _Response(url, _leisure_detail(identity, "패밀리보트"))
        raise AssertionError(f"unexpected public GET {url}")

    def close(self) -> None:
        self.closed = True


def test_complete_general_snapshot_reconciles_and_never_calls_application() -> None:
    session = _FixtureSession("experience")
    rows, parser, meta = gimpo.collect(
        {
            "provider": gimpo.GIMPO_EXPERIENCE_PROVIDER,
            "url": gimpo.GIMPO_EXPERIENCE_URL,
        },
        today="2026-08-05",
        session_factory=lambda: session,
    )
    assert parser == gimpo.GIMPO_PARSER
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["source_total"] == 3
    assert meta["current_count"] == 1
    assert meta["returned_count"] == 1
    assert meta["explicit_non_program_count"] == 1
    assert meta["notice_count"] == 1
    assert meta["sentinel_new_rows"] == 0
    assert meta["stable_first_page"] is True
    assert meta["application_endpoints_called"] == 0
    assert len(rows) == 1
    row = rows[0]
    assert row["branch"] == "김포시농업기술센터"
    assert row["venue_address"] == "경기도 김포시 월곶면 체험로 1"
    assert "webEtcResveApplcntAgree.do" in row["application_url"]
    assert all("ApplcntAgree" not in url for url in session.calls)
    assert session.closed is True


def test_complete_leisure_snapshot_accepts_clamped_duplicate_sentinel() -> None:
    session = _FixtureSession("leisure")
    rows, _, meta = gimpo.collect(
        {
            "provider": gimpo.GIMPO_LEISURE_PROVIDER,
            "url": gimpo.GIMPO_LEISURE_URL,
        },
        today="2026-08-05",
        session_factory=lambda: session,
    )
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["source_total"] == 1
    assert meta["sentinel_raw_rows"] == 1
    assert meta["sentinel_new_rows"] == 0
    assert meta["current_count"] == 1
    assert rows[0]["fee"] == "대당 25,000원"
    assert rows[0]["venue_address"] == "경기도 김포시 김포한강2로 1"
    assert "step0TnLesureApplcntViewU.do" in rows[0]["application_url"]
    assert all("step0TnLesure" not in url for url in session.calls)


def test_each_owner_rejects_the_other_sentinel_contract() -> None:
    class BadGeneralSentinel(_FixtureSession):
        def get(self, url: str, **kwargs: Any) -> _Response:
            parsed = urlparse(url)
            query = parse_qs(parsed.query, keep_blank_values=True)
            if parsed.path.endswith("webEtcResveList.do") and query.get(
                "pageIndex"
            ) == ["2"]:
                self.calls.append(url)
                return _Response(url, _generic_page(3, _generic_item("101", "가족 체험")))
            return super().get(url, **kwargs)

    general = BadGeneralSentinel("experience")
    rows, _, meta = gimpo.collect(
        {
            "provider": gimpo.GIMPO_EXPERIENCE_PROVIDER,
            "url": gimpo.GIMPO_EXPERIENCE_URL,
        },
        today="2026-08-05",
        session_factory=lambda: general,
    )
    assert rows == []
    assert "sentinel is no longer empty" in meta["configured_collection_error"]

    class EmptyLeisureSentinel(_FixtureSession):
        list_requests = 0

        def get(self, url: str, **kwargs: Any) -> _Response:
            if urlparse(url).path.endswith("selectTnLesureResveListU.do"):
                self.list_requests += 1
                if self.list_requests == 2:
                    self.calls.append(url)
                    return _Response(url, _leisure_page(1, ""))
            return super().get(url, **kwargs)

    leisure = EmptyLeisureSentinel("leisure")
    rows, _, meta = gimpo.collect(
        {
            "provider": gimpo.GIMPO_LEISURE_PROVIDER,
            "url": gimpo.GIMPO_LEISURE_URL,
        },
        today="2026-08-05",
        session_factory=lambda: leisure,
    )
    assert rows == []
    assert "no longer clamps exactly" in meta["configured_collection_error"]


def test_official_live_baseline_documents_both_independent_ledgers() -> None:
    assert gimpo.GIMPO_LIVE_AUDIT_BASELINE["experience"] == {
        "checked_at": "2026-08-05",
        "source_total": 86,
        "data_pages": 9,
        "current_count": 17,
        "sentinel_page": 10,
        "notice_rows": 2,
        "test_rows": 1,
    }
    assert gimpo.GIMPO_LIVE_AUDIT_BASELINE["leisure"]["source_total"] == 8
    assert gimpo.GIMPO_LIVE_AUDIT_BASELINE["leisure"]["current_count"] == 8
