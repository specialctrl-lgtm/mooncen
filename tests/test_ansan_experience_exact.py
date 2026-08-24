from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import municipal_ansan_experience as experience


ROOT = Path(__file__).resolve().parents[1]
TARGET = {
    "provider": experience.ANSAN_EXPERIENCE_PROVIDER,
    "url": experience.ANSAN_EXPERIENCE_URL,
}


@dataclass(frozen=True)
class Programme:
    category: str
    identity: str
    title: str
    source_status: str
    apply_period: str
    event_period: str
    department: str
    weekdays: str
    target: str
    fee: str
    location: str
    address: str
    method: str = "인터넷"
    age_limit: str = ""


PROGRAMMES = (
    Programme(
        "X01",
        "RESR_000000000090001",
        "합성 실내 목공 체험",
        "접수중",
        "2026-08-01 09:00 ~ 2026-08-20 18:00",
        "2026-08-10 ~ 2026-08-30",
        "녹지과",
        "월, 화",
        "어린이",
        "무료",
        "사동",
        "경기 안산시 상록구 합성로 1 합성체험관",
        "인터넷, 전화, 방문",
        "출생년도 2020년생 ~ 2026년생 신청 가능",
    ),
    Programme(
        "X01",
        "RESR_000000000090002",
        "종료됐지만 접수중 배지가 남은 실내 체험",
        "접수중",
        "2026-06-01 09:00 ~ 2026-06-10 18:00",
        "2026-07-01 ~ 2026-07-02",
        "관광과",
        "수",
        "제한없음",
        "무료",
        "초지동",
        "경기 안산시 단원구 합성로 2",
    ),
    Programme(
        "X02",
        "RESR_000000000090003",
        "접수는 끝난 야외 생태 체험",
        "접수마감",
        "2026-07-01 09:00 ~ 2026-07-10 18:00",
        "2026-08-15 ~ 2026-09-15",
        "공원과",
        "토",
        "가족",
        "무료",
        "초지동",
        "경기 안산시 단원구 합성로 3 생태공원",
        "방문",
    ),
    Programme(
        "X02",
        "RESR_000000000090004",
        "종료된 야외 체험",
        "접수마감",
        "2026-06-01 09:00 ~ 2026-06-10 18:00",
        "2026-07-01 ~ 2026-07-02",
        "공원과",
        "일",
        "가족",
        "무료",
        "초지동",
        "경기 안산시 단원구 합성로 4",
    ),
    Programme(
        "X03",
        "RESR_000000000090005",
        "향후 박물관 견학",
        "접수대기",
        "2026-08-20 09:00 ~ 2026-09-20 18:00",
        "2026-09-21 ~ 2026-10-20",
        "관광과",
        "금",
        "제한없음",
        "무료",
        "고잔동",
        "경기 안산시 단원구 합성로 5 박물관",
    ),
    Programme(
        "X03",
        "RESR_000000000090006",
        "종료된 견학",
        "접수마감",
        "2026-06-01 09:00 ~ 2026-06-10 18:00",
        "2026-07-01 ~ 2026-07-02",
        "관광과",
        "목",
        "제한없음",
        "무료",
        "고잔동",
        "경기 안산시 단원구 합성로 6",
    ),
)


def _category(code: str) -> experience.AnsanExperienceCategory:
    return next(item for item in experience.ANSAN_EXPERIENCE_CATEGORIES if item.code == code)


def _list_html(code: str, requested_page: int, programmes: tuple[Programme, ...]) -> str:
    category = _category(code)
    rows = [item for item in programmes if item.category == code] if requested_page == 1 else []
    cards = "".join(
        f"""
        <li><a href="#none" onclick="fnView('{item.identity}');">
          <span class="label">{item.source_status}</span>
          <div class="txtW"><p class="tit">{item.title}</p><ul class="etc">
            <li><span class="em emExp">기관/부서</span>{item.department}</li>
            <li><span class="em emExp">접수기간</span>{item.apply_period}</li>
            <li><span class="em emExp">체험/견학기간</span>{item.event_period}</li>
            <li><span class="em emExp">요일</span>{item.weekdays}</li>
            <li><span class="em emExp">대상</span>{item.target}</li>
            <li><span class="em emExp">사용료</span>{item.fee}</li>
            <li><span class="em emExp">위치</span>{item.location}</li>
          </ul></div></a></li>
        """
        for item in rows
    )
    pager = "<script>fnSearch(1);</script>" if requested_page == 1 else ""
    return f"""
    <html><head><title>안산시 통합예약시스템</title></head><body>
      <form name="searchVO"><input type="hidden" name="currentMenuNo"
        value="{category.menu_no}"></form>
      <p>전체 : 2 건</p>{pager}<ul class="blog reserv">{cards}</ul>
    </body></html>
    """


def _detail_html(item: Programme, *, mismatch: bool = False) -> str:
    age = (
        f'<li><span class="em">나이제한</span><span class="txt">{item.age_limit}</span></li>'
        if item.age_limit
        else ""
    )
    control = (
        '<a id="resvRqstBtn" href="#none" onclick="checkInTracer();">예약신청</a>'
        if item.source_status == "접수중"
        else ""
    )
    time_capacity = (
        '<li><span class="em">체험시간</span><span class="txt">14:00 ~ 16:00</span></li>'
        '<li><span class="em">모집정원</span><span class="txt">1명/20명</span></li>'
        if item.category == "X03"
        else ""
    )
    title = item.title + (" 불일치" if mismatch else "")
    return f"""
    <html><head><title>안산시 통합예약시스템</title></head><body>
      <div class="listInfo"><div class="infoArea">
        <p class="label">{item.source_status}</p><p class="tit">{title}</p>
        <ul class="itemList">
          <li><span class="em">기관/부서</span><span class="txt">{item.department}</span></li>
          <li><span class="em">예약방식</span><span class="txt">{item.method}</span></li>
          <li><span class="em">접수기간</span><span class="txt">{item.apply_period}</span></li>
          <li><span class="em">선정방식</span><span class="txt">선착순</span></li>
          {age}
          <li><span class="em">대상</span><span class="txt">{item.target}</span></li>
          <li><span class="em">사용료</span><span class="txt">{item.fee}</span></li>
          <li><span class="em">체험/견학기간</span><span class="txt">{item.event_period}</span></li>
          <li><span class="em">요일</span><span class="txt">{item.weekdays}</span></li>
          {time_capacity}
        </ul>
        <a href="#none" onclick="fnFavorite('{item.identity}');">관심자원</a>
        {control}
      </div></div>
      <div class="rsvInfo">저장 금지 문의 031-123-4567 person@example.com
        <a onclick="commonDownFile('FILE_PRIVATE','1');">private.hwp</a></div>
      <div class="rsvPlace"><ul class="loca">
        <li><span class="em"><i>location_on</i>위치</span>{item.address}</li>
        <li><span class="em">문의처</span>031-123-4567</li>
      </ul></div>
      <script>loadCalendar('{item.identity}','');</script>
    </body></html>
    """


class FakeResponse:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.status_code = 200
        self.content = html.encode("utf-8")
        self.text = html
        self.history: list[Any] = []
        self.headers = {"Content-Type": "text/html; charset=utf-8"}


class FixtureFactory:
    def __init__(
        self,
        *,
        programmes: tuple[Programme, ...] = PROGRAMMES,
        mismatch_identity: str = "",
    ) -> None:
        self.programmes = programmes
        self.mismatch_identity = mismatch_identity
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.lock = Lock()
        self.closed = 0

    def __call__(self) -> "FixtureSession":
        return FixtureSession(self)


class FixtureSession:
    def __init__(self, owner: FixtureFactory) -> None:
        self.owner = owner

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        with self.owner.lock:
            self.owner.calls.append((url, kwargs))
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == experience.ANSAN_EXPERIENCE_HOST
        code = parsed.path.split("/")[2]
        category = _category(code)
        assert query["currentMenuNo"] == [category.menu_no]
        if parsed.path == category.list_path:
            page = int(query.get("pageIndex", ["1"])[0])
            return FakeResponse(url, _list_html(code, page, self.owner.programmes))
        if parsed.path == category.detail_path:
            identity = query["resrId"][0]
            item = next(row for row in self.owner.programmes if row.identity == identity)
            return FakeResponse(
                url,
                _detail_html(
                    item,
                    mismatch=identity == self.owner.mismatch_identity,
                ),
            )
        raise AssertionError(f"unsafe request: {url}")

    def close(self) -> None:
        with self.owner.lock:
            self.owner.closed += 1


def _collect(factory: FixtureFactory, **kwargs: Any):
    return experience.collect(
        TARGET,
        today="2026-08-05",
        timeout=10,
        max_pages=3,
        detail_limit=10,
        session_factory=factory,
        **kwargs,
    )


def test_exact_target_url_and_request_guards() -> None:
    assert experience.is_target(TARGET)
    assert not experience.is_target({**TARGET, "url": TARGET["url"] + "&pageIndex=1"})
    assert not experience.is_target({**TARGET, "url": TARGET["url"] + "#fragment"})
    assert not experience.is_target({**TARGET, "provider": "WRONG"})
    assert experience._request_kind("GET", TARGET["url"]) == "list"
    with pytest.raises(experience.AnsanExperienceContractError, match="POST"):
        experience._request_kind("POST", TARGET["url"])
    for unsafe in (
        "https://reserve.ansan.go.kr/calendar.do?cgyCd=EXP",
        "https://reserve.ansan.go.kr/reservation.do?resrId=RESR_000000000090001",
        "https://reserve.ansan.go.kr/login.do",
        "https://reserve.ansan.go.kr/member/applicant.do",
        "https://reserve.ansan.go.kr/commonDownFile.do?file=private",
    ):
        with pytest.raises(experience.AnsanExperienceContractError):
            experience._request_kind("GET", unsafe)


def test_fixture_full_snapshot_detail_identity_districts_and_privacy() -> None:
    factory = FixtureFactory()
    rows, parser, meta = _collect(factory)

    assert parser == experience.ANSAN_EXPERIENCE_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == 6
    assert meta["category_totals"] == {"X01": 2, "X02": 2, "X03": 2}
    assert meta["data_pages"] == 3
    assert meta["current_count"] == 3
    assert meta["expired_count"] == 3
    assert meta["expired_active_status_count"] == 1
    assert meta["status_counts"] == {"OPEN": 1, "CLOSED": 1, "SCHEDULED": 1}
    assert meta["municipality_counts"] == {
        experience.ANSAN_SANGNOK_CODE: 1,
        experience.ANSAN_DANWON_CODE: 2,
    }
    assert meta["list_requests"] == 18
    assert meta["detail_warmup_list_requests"] == 3
    assert meta["detail_pages"] == 3
    assert meta["physical_requests"] == 21
    assert meta["boundary_rechecks"] == 9
    assert meta["application_controls_current"] == 1
    assert meta["unsafe_endpoint_calls"] == 0
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["full_snapshot_validated"] is True
    assert factory.closed == 18

    assert len(rows) == 3
    assert all(row["source_course_id"] != "experience:RESR_000000000090002" for row in rows)
    assert Counter(row["program_type"] for row in rows) == {"체험": 2, "견학": 1}
    assert all(row["provider_course_id"].startswith(experience.ANSAN_EXPERIENCE_PROVIDER + ":") for row in rows)
    assert all(bool(row["application_url"]) == row["reservation_available"] for row in rows)
    assert all(row["description"] == row["title"] for row in rows)
    assert all("031-123-4567" not in repr(row) for row in rows)
    assert all("person@example.com" not in repr(row) for row in rows)
    assert all(row["raw_fields"]["detail_verified"] is True for row in rows)
    assert all(row["raw_fields"]["calendar_endpoint_fetched"] is False for row in rows)

    called_urls = [url for url, _ in factory.calls]
    assert all("calendar" not in url.casefold() for url in called_urls)
    assert all("download" not in url.casefold() for url in called_urls)
    assert all("login" not in url.casefold() for url in called_urls)
    assert all(kwargs["allow_redirects"] is False for _, kwargs in factory.calls)
    assert all(experience._request_kind("GET", url) in {"list", "detail"} for url in called_urls)


@pytest.mark.parametrize(
    ("factory", "message"),
    (
        (
            FixtureFactory(mismatch_identity="RESR_000000000090001"),
            "list/detail identity drift",
        ),
        (
            FixtureFactory(
                programmes=tuple(
                    replace(row, address="경기 안산시 합성로 1")
                    if row.identity == "RESR_000000000090001"
                    else row
                    for row in PROGRAMMES
                )
            ),
            "explicit Sangnok/Danwon",
        ),
    ),
)
def test_detail_contract_drift_fails_atomically(
    factory: FixtureFactory, message: str
) -> None:
    rows, _, meta = _collect(factory)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_dedupe_cannot_remove_a_current_identity() -> None:
    rows, _, meta = _collect(
        FixtureFactory(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed complete" in meta["configured_collection_error"]


def test_exact_dispatch_uses_ansan_verified_tls_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as router
    from Crawler import municipal_ansan

    captured: dict[str, Any] = {}

    def fake_collect(*_args: Any, **kwargs: Any):
        captured.update(kwargs)
        return [], "fixture", {"snapshot_complete": True}

    monkeypatch.setattr(experience, "collect_ansan_experience", fake_collect)
    target = router.CrawlTarget(
        provider=experience.ANSAN_EXPERIENCE_PROVIDER,
        name="안산 체험",
        branch="안산시",
        url=experience.ANSAN_EXPERIENCE_URL,
        source="fixture",
    )
    router.collect_from_url(target, timeout=1, max_depth=0, max_pages=30, detail_limit=100)

    assert captured["session_factory"] is municipal_ansan.ansan_session_factory
    assert callable(captured["dedupe_rows"])


def test_target_operational_and_coverage_configuration() -> None:
    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/public_reservation.yaml").read_text(encoding="utf-8")
    )["targets"]
    target = next(
        row for row in targets if row.get("provider") == experience.ANSAN_EXPERIENCE_PROVIDER
    )
    assert target["url"] == experience.ANSAN_EXPERIENCE_URL
    assert target["candidate_id"] == experience.ANSAN_EXPERIENCE_CANDIDATE_ID
    assert target["crawler_module"] == "Crawler.municipal_ansan_experience"
    assert target["crawler_callable"] == "collect_ansan_experience"
    assert target["ops_scopes"] == ["experience"]
    assert target["covered_municipalities"] == [
        dict(item) for item in experience.ANSAN_EXPERIENCE_COVERED_MUNICIPALITIES
    ]

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    source = next(
        row for row in operational if row.get("provider") == experience.ANSAN_EXPERIENCE_PROVIDER
    )
    assert source["ops_scopes"] == ["experience"]
    assert source["row_count"] == 41

    coverage = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )["municipalities"]
    for code in (
        experience.ANSAN_CITY_CODE,
        experience.ANSAN_SANGNOK_CODE,
        experience.ANSAN_DANWON_CODE,
    ):
        region = next(row for row in coverage if row.get("code") == code)
        assert experience.ANSAN_EXPERIENCE_PROVIDER in region["owner_providers"]
        assert experience.ANSAN_EXPERIENCE_PROVIDER in region["promoted_providers"]
        assert any(
            evidence.get("kind") == "operational_allowlist"
            and evidence.get("provider") == experience.ANSAN_EXPERIENCE_PROVIDER
            for evidence in region["evidence"]
        )


@pytest.mark.skipif(
    __import__("os").getenv("RUN_LIVE_ANSAN_EXPERIENCE") != "1",
    reason="set RUN_LIVE_ANSAN_EXPERIENCE=1 for official live verification",
)
def test_live_ansan_experience_snapshot() -> None:
    from Crawler import municipal_ansan

    rows, _, meta = experience.collect(
        TARGET,
        today="2026-08-05",
        timeout=45,
        max_pages=30,
        detail_limit=100,
        session_factory=municipal_ansan.ansan_session_factory,
    )
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["source_total"] >= 334
    assert len(rows) >= 41
    assert all(bool(row["application_url"]) == row["reservation_available"] for row in rows)
