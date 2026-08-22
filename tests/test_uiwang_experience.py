from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import Crawler_EducationExperience as experience_runner
from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import municipal_uiwang_experience as uiwang


@dataclass
class Target:
    provider: str = uiwang.UIWANG_EXPERIENCE_PROVIDER
    url: str = uiwang.UIWANG_EXPERIENCE_URL
    name: str = "의왕시 통합예약 전체 체험·캠프"
    branch: str = "경기도 의왕시"


@dataclass
class Response:
    text: str
    url: str
    status_code: int = 200
    history: tuple[object, ...] = ()

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")


def _card(
    identity: str,
    title: str,
    *,
    status: str = "접수중",
    period: str = "2099-08-01 ~ 2099-08-31",
    apply_period: str = "2099-07-01 09:00 ~ 2099-08-30 18:00",
    capacity: str = "2/10",
) -> str:
    return f"""
      <li><a href="#none" onclick="fnView('{identity}')">
        <span class="label">{status}</span>
        <div class="txtW"><p class="tit">{title}</p><ul class="etc chgMW">
          <li><span class="em">접수기간</span>{apply_period}</li>
          <li><span class="em">체험캠프기간</span>{period}</li>
          <li><span class="em">요일</span>토</li>
          <li><span class="em">대상</span>가족</li>
          <li><span class="em">사용료</span>무료</li>
          <li><span class="em">위치</span>청계동</li>
          <li><span class="em">신청/정원</span>{capacity}</li>
        </ul></div>
      </a></li>
    """


def _page(cards: str, *, total: int = 4) -> str:
    return f"""
    <html><head><title>체험·캠프 | 의왕시 통합예약시스템</title></head><body>
      <div id="conArea"><p class="subTit">체험·캠프</p>
        <div class="listTop"><p class="total">전체 <span class="em">{total}</span> 건</p></div>
        <ul class="album reserv">{cards}</ul>
      </div>
    </body></html>
    """


def _detail(
    identity: str,
    title: str,
    *,
    period: str = "2099-08-01 ~ 2099-08-31",
    capacity: int = 10,
    branch: str = "바라산자연휴양림",
    address: str = "경기 의왕시 바라산로 84 바라산자연휴양림",
    application_control: bool = True,
) -> str:
    control = (
        """
        <div id="resvRqstBtnArea"></div>
        <script>
          function fnResvRqst(agtReptYn) {
            var postData = {resrId: $("#resrId").val()};
          }
        </script>
        """
        if application_control
        else ""
    )
    return f"""
    <html><head><title>체험·캠프 | 의왕시 통합예약시스템</title></head><body>
      <div id="conArea"><p class="subTit">체험·캠프</p>
        <input id="resrId" name="resrId" value="{identity}" />
        <div class="listInfoTop"><p class="tit">{title}</p>
          <img src="/reserve/getResrImg.do;jsessionid=PRIVATE?atchFileId=FILE1&amp;fileSn=1" />
        </div>
        <div class="listInfoBtm"><div class="infoArea"><ul class="itemList">
          <li><span class="em">유형</span><span class="txt">체험</span></li>
          <li><span class="em">체험캠프기간</span><span class="txt">{period}</span></li>
          <li><span class="em">체험캠프시간</span><span class="txt">10:00 - 12:00</span></li>
          <li><span class="em">체험캠프요일</span><span class="txt">토</span></li>
          <li><span class="em">체험캠프장소</span><span class="txt">{branch}</span></li>
          <li><span class="em">읍면동</span><span class="txt">청계동</span></li>
          <li><span class="em">대상</span><span class="txt">가족</span></li>
          <li><span class="em">사용료</span><span class="txt">무료</span></li>
          <li><span class="em">예약방식</span><span class="txt">인터넷</span></li>
          <li><span class="em">기관/부서</span><span class="txt">문화관광과</span></li>
          <li><span class="em">모집정원</span><span class="txt">{capacity}</span></li>
          <li><span class="em">문의처</span><span class="txt">031-000-0000</span></li>
        </ul></div></div>
        <ul class="loca"><li><span class="em">위치</span>{address}</li></ul>
        {control}
      </div>
    </body></html>
    """


OPEN_ID = "RESR_000000000000001"
EXPIRED_ID = "RESR_000000000000002"
CLOSED_ID = "RESR_000000000000003"
TEST_ID = "RESR_000000000010989"


class FixtureSession:
    def __init__(
        self,
        *,
        polluted_sentinel: bool = False,
        unstable_first: bool = False,
        missing_application_control: bool = False,
        detail_identity_mismatch: bool = False,
    ) -> None:
        self.polluted_sentinel = polluted_sentinel
        self.unstable_first = unstable_first
        self.missing_application_control = missing_application_control
        self.detail_identity_mismatch = detail_identity_mismatch
        self.calls: list[str] = []
        self.page_one_calls = 0

    def get(self, url: str, **kwargs: object) -> Response:
        assert kwargs["allow_redirects"] is False
        assert int(kwargs["timeout"]) == 7
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("eduList.do"):
            page = int(query["pageIndex"][0])
            if page == 1:
                self.page_one_calls += 1
                first_title = (
                    "바뀐 체험"
                    if self.unstable_first and self.page_one_calls > 1
                    else "숲 체험"
                )
                body = _page(
                    _card(OPEN_ID, first_title)
                    + _card(
                        EXPIRED_ID,
                        "지난 체험",
                        status="접수마감",
                        period="2020-01-01 ~ 2020-01-02",
                        apply_period="2019-12-01 ~ 2019-12-31",
                    )
                )
            elif page == 2:
                body = _page(
                    _card(CLOSED_ID, "장기 문화유산 체험", status="접수마감")
                    + _card(
                        TEST_ID,
                        "테스트",
                        status="접수마감",
                        period="~",
                        apply_period="",
                        capacity="0/0",
                    )
                )
            elif page == 3:
                cards = _card("RESR_000000000000004", "경계 오염") if self.polluted_sentinel else ""
                body = _page(cards)
            else:
                raise AssertionError(f"unexpected list page {page}")
            return Response(body, url)
        if parsed.path.endswith("eduView.do"):
            identity = query["resrId"][0]
            if identity == OPEN_ID:
                detail_id = CLOSED_ID if self.detail_identity_mismatch else identity
                return Response(
                    _detail(
                        detail_id,
                        "숲 체험",
                        application_control=not self.missing_application_control,
                    ),
                    url,
                )
            if identity == CLOSED_ID:
                return Response(
                    _detail(
                        identity,
                        "장기 문화유산 체험",
                        branch="의왕향토사료관",
                        address="경기 의왕시 골우물길 49 2층",
                        application_control=False,
                    ),
                    url,
                )
        raise AssertionError(f"private or unexpected endpoint requested: {url}")

    def close(self) -> None:
        return None


def _collect(fixture: FixtureSession, **kwargs: object):
    return uiwang.collect_uiwang_experience_courses(
        Target(),
        timeout=7,
        max_pages=8,
        detail_limit=10,
        today="2099-08-05",
        session_factory=lambda: fixture,
        **kwargs,
    )


def test_complete_snapshot_is_locked_to_experience_and_public_details() -> None:
    fixture = FixtureSession()
    rows, parser, meta = _collect(fixture)

    assert parser == uiwang.UIWANG_EXPERIENCE_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"uiwang-experience:{OPEN_ID}",
        f"uiwang-experience:{CLOSED_ID}",
    ]
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["domain_category"] == "체험·견학" for row in rows)
    assert all(row["program_type"] == "체험" for row in rows)
    assert all(row["municipality_code"] == "4143000000" for row in rows)
    assert rows[0]["branch"] == "바라산자연휴양림"
    assert rows[0]["address"] == "경기도 의왕시 바라산로 84"
    assert rows[0]["application_url"] == rows[0]["raw_url"]
    assert rows[0]["reservation_available"] is True
    assert ";jsessionid" not in rows[0]["image_url"]
    assert rows[1]["branch"] == "의왕향토사료관"
    assert rows[1]["application_url"] == ""
    assert rows[1]["application_type"] == "INFO_ONLY"
    assert meta["source_total"] == meta["source_rows"] == 4
    assert meta["data_pages"] == 2
    assert meta["sentinel_page"] == 3
    assert meta["current_count"] == meta["returned_count"] == 2
    assert meta["expired_count"] == 1
    assert meta["explicit_non_program_count"] == meta["test_count"] == 1
    assert meta["detail_pages"] == 2
    assert meta["stable_first_page"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert not any(
        token in url.lower()
        for url in fixture.calls
        for token in ("login", "student", "apply", "resvrqst", "mypage")
    )
    persisted = repr(rows)
    assert "031-000-0000" not in persisted
    assert meta["application_endpoints_called"] == 0
    assert meta["pii_payload_persisted"] is False


@pytest.mark.parametrize(
    "fixture,error",
    [
        (FixtureSession(polluted_sentinel=True), "post-last page"),
        (FixtureSession(unstable_first=True), "first list page changed"),
        (FixtureSession(missing_application_control=True), "application control"),
        (FixtureSession(detail_identity_mismatch=True), "identity mismatch"),
    ],
)
def test_contract_drift_returns_no_partial_rows(
    fixture: FixtureSession, error: str
) -> None:
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in meta["configured_collection_error"]


def test_caps_fail_atomically_before_incomplete_snapshot() -> None:
    rows, _parser, meta = uiwang.collect_uiwang_experience_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=10,
        today="2099-08-05",
        session_factory=lambda: FixtureSession(),
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = uiwang.collect_uiwang_experience_courses(
        Target(),
        timeout=7,
        max_pages=8,
        detail_limit=1,
        today="2099-08-05",
        session_factory=lambda: FixtureSession(),
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_exact_target_and_request_allowlist() -> None:
    assert uiwang.is_uiwang_experience_target(Target())
    assert not uiwang.is_uiwang_experience_target(
        Target(url=uiwang.UIWANG_EXPERIENCE_URL + "&pageIndex=1")
    )
    assert not uiwang.is_uiwang_experience_target(
        Target(provider="MUNI_WRONG")
    )
    with pytest.raises(uiwang.UiwangExperienceContractError, match="blocked"):
        uiwang._validate_public_url(
            "https://www.uiwang.go.kr/reserve/login/loginForm.do"
        )


def test_dispatch_target_and_experience_schedule_contract(monkeypatch) -> None:
    expected = ([{"provider": uiwang.UIWANG_EXPERIENCE_PROVIDER}], "parser", {"ok": True})
    captured: dict[str, object] = {}

    def fake_collect(target: object, **kwargs: object):
        captured.update(kwargs)
        assert target == Target()
        return expected

    monkeypatch.setattr(
        uiwang, "collect_uiwang_experience_courses", fake_collect
    )
    result = municipal.collect_from_url(
        Target(), timeout=7, max_depth=0, max_pages=8, detail_limit=10
    )
    assert result == expected
    assert captured["timeout"] == 7
    assert captured["max_pages"] == 8
    assert captured["detail_limit"] == 10

    document = yaml.safe_load(
        (
            municipal.ROOT
            / "config"
            / "crawl_targets"
            / "public_reservation.yaml"
        ).read_text(encoding="utf-8")
    )
    target = next(
        row
        for row in document["targets"]
        if row.get("provider") == uiwang.UIWANG_EXPERIENCE_PROVIDER
    )
    assert target["crawler_status"] == "ready"
    assert target["full_snapshot_required"] is True
    assert target["service_group"] == "체험"
    assert experience_runner.is_experience_target(target)
    assert uiwang.UIWANG_EXPERIENCE_PROVIDER in experience_runner.experience_provider_names(
        [target], scheduled_providers=set()
    )


def test_dedupe_must_preserve_complete_ordered_snapshot() -> None:
    rows, _parser, meta = _collect(
        FixtureSession(), dedupe_rows=lambda incoming: list(reversed(incoming))
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]

