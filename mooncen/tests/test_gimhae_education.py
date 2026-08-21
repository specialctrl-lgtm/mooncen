from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_gimhae as gimhae


class Response:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.status_code = 200
        self.history: list[object] = []
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}
        self.content = html.encode()


class Session:
    def close(self) -> None:
        pass


def _options(items: tuple[tuple[str, str], ...]) -> str:
    return "".join(
        f'<option value="{escape(value)}">{escape(label)}</option>'
        for value, label in items
    )


def _form(page: int) -> str:
    return f'''<form id="listForm" name="listForm" method="get"
      action="{gimhae.GIMHAE_LEDGER_PATH}?cpage={page}">
      <input name="cpage" value="1"><input name="oby" value="">
      <input name="sstring" value=""><input name="regStartDate" value="">
      <input name="regEndDate" value=""><input name="startDt" value="">
      <input name="endDt" value="">
      <select name="stype">{_options(gimhae.GIMHAE_SEARCH_TYPE_REGISTRY)}</select>
      <select name="lecState">{_options(gimhae.GIMHAE_STATE_REGISTRY)}</select>
      <select name="targetCd">{_options(gimhae.GIMHAE_TARGET_REGISTRY)}</select>
      <select name="appMethod">{_options(gimhae.GIMHAE_METHOD_REGISTRY)}</select>
    </form>'''


def _card(
    identity: str,
    page: int,
    status: str,
    title: str,
    *,
    method: str = "인터넷",
) -> str:
    return f'''<li class="column"><div class="w1"><a class="a1"
      href="?amode=view&amp;cssno={identity}&amp;cpage={page}"><div class="tg1">
      <b class="g1">{status}</b><strong class="t1">{title}</strong>
      <ul class="lst1"><li class="li1">접수기간 : 2026.07.01 ~ 2026.07.31</li>
      <li class="li1">교육기간 : 2026.08.01 ~ 2026.09.01</li>
      <li class="li1">요일시간 : 화, 10:00~12:00</li>
      <li class="li1">접수인원/총인원 : 3명 / 10명</li>
      <li class="li1">대상 : 성인</li><li class="li1">접수방법 : {method}</li></ul>
      </div></a></div></li>'''


def _list_html(
    page: int,
    *,
    bad_sentinel: bool = False,
    offline_application: bool = False,
) -> str:
    identities = ("999", "202") if bad_sentinel and page == 2 else ("101", "202")
    cards = _card(
        identities[0],
        page,
        "접수중",
        "열린 강좌",
        method="전화,방문" if offline_application else "인터넷",
    ) + _card(
        identities[1], page, "접수마감", "마감 강좌"
    )
    return f'''{_form(page)}<div class="infomenu1"><div class="info1">
      총 <b class="em">2</b>건의 게시물이 있습니다.
      <span>(<b class="em">1</b>/1 페이지)</span></div></div>
      <ul class="even-grid evenmix-123">{cards}</ul>'''


def _detail_html(
    identity: str,
    *,
    bad_title: bool = False,
    bad_branch: bool = False,
    bad_application: bool = False,
    offline_application: bool = False,
    applicant_count: int = 3,
) -> str:
    opened = identity == "101"
    status = "접수중" if opened else "접수마감"
    title = "changed" if bad_title else ("열린 강좌" if opened else "마감 강좌")
    branch = "unknown" if bad_branch else "기적의도서관"
    apply_identity = "999" if bad_application else identity
    if opened and offline_application:
        notice = (
            "alert('변경된 안내');return false;"
            if bad_application
            else "alert('선택하신 강좌는 인터넷 접수를 받지 않습니다.');return false;"
        )
        application = (
            f'<div class="btns"><a href="#" onclick="{notice}">예약하기</a></div>'
        )
    elif opened:
        application = (
            f'<div class="btns"><a href="?amode=ins&amp;cssno={apply_identity}&amp;cpage=1">예약하기</a></div>'
        )
    else:
        application = ""
    fields = [
        ("접수기간", "2026-07-01 10:00 ~ 2026-07-31 00:00"),
        ("교육기간", "2026-08-01 ~ 2026-09-01"),
        ("요일시간", "화, 10:00~12:00"),
        ("대상", "성인"),
        ("장소", "강의실"),
        ("강사명", "discard"),
        ("수강료", "무료"),
        ("재료비 / 교재비", "없음"),
        ("접수인원 / 총인원", f"{applicant_count} / 10"),
        ("이용문의", branch),
    ]
    items = "".join(
        (
            f'<li class="di"><b class="dt">{escape(label)}</b><span class="dd">'
            + (
                f'{escape(value)}, <a href="tel:055-000-0000">055-000-0000</a>'
                if label == "이용문의"
                else escape(value)
            )
            + "</span></li>"
        )
        for label, value in fields
    )
    return f'''<div class="cp20view1"><div class="hg1"><b class="g1">{status}</b>
      <h2 class="h1">{title}</h2></div><div class="even-grid"><div><div class="cp20dlist1">
      <ul class="dl1">{items}</ul></div>{application}</div></div></div>'''


@dataclass
class Fixture:
    bad_sentinel: bool = False
    bad_title: bool = False
    bad_branch: bool = False
    bad_application: bool = False
    offline_application: bool = False
    changed_applicant_count: bool = False

    def fetch(self, _session: object, method: str, url: str, *, timeout: int) -> Response:
        del timeout
        assert method == "GET"
        query = parse_qs(urlparse(url).query)
        if query.get("amode") == ["view"]:
            identity = query["cssno"][0]
            return Response(
                url,
                _detail_html(
                    identity,
                    bad_title=self.bad_title and identity == "101",
                    bad_branch=self.bad_branch and identity == "101",
                    bad_application=self.bad_application and identity == "101",
                    offline_application=self.offline_application and identity == "101",
                    applicant_count=(
                        4
                        if self.changed_applicant_count and identity == "101"
                        else 3
                    ),
                ),
            )
        return Response(
            url,
            _list_html(
                int(query["cpage"][0]),
                bad_sentinel=self.bad_sentinel,
                offline_application=self.offline_application,
            ),
        )


def _target(
    provider: str = gimhae.GIMHAE_PROVIDER,
    url: str = gimhae.GIMHAE_DISCOVERY_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url}


def test_complete_identity_ledger_and_safe_controls() -> None:
    rows, parser, meta = gimhae.collect_gimhae_education(
        _target(),
        today="2026-07-23",
        detail_workers=1,
        session_factory=Session,
        fetcher=Fixture().fetch,
    )
    assert parser == gimhae.GIMHAE_PARSER and len(rows) == 2
    assert {row["status"] for row in rows} == {"OPEN", "CLOSED"}
    assert {row["branch"] for row in rows} == {"기적의도서관"}
    assert len({row["provider_course_id"] for row in rows}) == 2
    assert all(row["application_url"] == "" for row in rows)
    assert meta["source_total"] == meta["current_source_count"] == 2
    assert meta["clamp_sentinel_page"] == 2 and meta["clamp_sentinel_rows"] == 2
    assert meta["reservation_control_count"] == 1
    assert meta["logical_requests"] == 6 and meta["snapshot_complete"] is True
    assert meta["reservation_endpoint_requests"] == meta["pii_endpoint_requests"] == 0
    assert meta["pii_values_persisted"] == 0


def test_owner_boundary_and_global_identity_namespace() -> None:
    assert "서부청소년센터" in gimhae.GIMHAE_BRANCH_REGISTRY
    assert "농업기술센터 농업기술지원과" in gimhae.GIMHAE_BRANCH_REGISTRY
    assert "김해시청소년센터" in gimhae.GIMHAE_BRANCH_REGISTRY
    assert "김해시" in gimhae.GIMHAE_BRANCH_REGISTRY
    assert gimhae.is_gimhae_target(_target())
    assert gimhae.is_gimhae_target(_target(url=gimhae.GIMHAE_LEDGER_URL))
    assert not gimhae.is_gimhae_target(
        _target(
            provider=gimhae.GIMHAE_EXCLUDED_DISCOVERY_PROVIDER,
            url=gimhae.GIMHAE_EXCLUDED_DISCOVERY_URL,
        )
    )
    assert not gimhae.is_gimhae_target(
        _target(url="http://www.gimhae.go.kr/yes/05560.web")
    )
    identity = gimhae.gimhae_source_identity("101")
    assert identity == f"{gimhae.GIMHAE_PROVIDER}:course:101"
    assert gimhae.GIMHAE_EXCLUDED_DISCOVERY_PROVIDER not in identity
    assert gimhae.GIMHAE_BRANCH_ALIASES["여성센터1"] == "김해시여성센터"


def test_single_deleted_year_is_repaired_only_from_the_matching_period_year() -> None:
    repaired = gimhae._two_dates(
        "206.09.04 ~ 2026.12.04",
        "39827",
        "education period",
    )
    assert tuple(value.isoformat() for value in repaired) == (
        "2026-09-04",
        "2026-12-04",
    )

    with pytest.raises(gimhae.GimhaeContractError, match="must contain two dates"):
        gimhae._two_dates(
            "205.09.04 ~ 2026.12.04",
            "39827",
            "education period",
        )


def test_offline_application_notice_is_preserved_as_info_only() -> None:
    rows, _, meta = gimhae.collect_gimhae_education(
        _target(),
        today="2026-07-23",
        detail_workers=1,
        session_factory=Session,
        fetcher=Fixture(offline_application=True).fetch,
    )
    opened = next(row for row in rows if row["title"] == "열린 강좌")
    assert opened["status"] == "OPEN"
    assert opened["application_type"] == "INFO_ONLY"
    assert opened["reservation_available"] is False
    assert opened["raw_fields"]["offline_application_notice"] is True
    assert meta["offline_application_notice_count"] == 1


def test_mutable_applicant_count_uses_latest_detail_value() -> None:
    rows, _, meta = gimhae.collect_gimhae_education(
        _target(),
        today="2026-07-23",
        detail_workers=1,
        session_factory=Session,
        fetcher=Fixture(changed_applicant_count=True).fetch,
    )
    opened = next(row for row in rows if row["title"] == "열린 강좌")
    assert opened["capacity_current"] == 4
    assert opened["capacity_total"] == 10
    assert opened["raw_fields"]["list_capacity_current"] == 3
    assert opened["raw_fields"]["capacity_changed_during_collection"] is True
    assert meta["capacity_change_count"] == 1


@pytest.mark.parametrize(
    ("fixture", "fragment"),
    [
        (Fixture(bad_sentinel=True), "clamp sentinel"),
        (Fixture(bad_title=True), "title/status drift"),
        (Fixture(bad_branch=True), "unaudited official branch"),
        (Fixture(bad_application=True), "reservation identity drift"),
        (
            Fixture(offline_application=True, bad_application=True),
            "offline reservation notice changed",
        ),
    ],
)
def test_contract_drift_fails_closed(fixture: Fixture, fragment: str) -> None:
    rows, _, meta = gimhae.collect_gimhae_education(
        _target(),
        today="2026-07-23",
        detail_workers=1,
        session_factory=Session,
        fetcher=fixture.fetch,
    )
    assert rows == [] and fragment in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_caps_fail_before_partial_detail_output() -> None:
    rows, _, meta = gimhae.collect_gimhae_education(
        _target(),
        today="2026-07-23",
        max_pages=3,
        detail_workers=1,
        session_factory=Session,
        fetcher=Fixture().fetch,
    )
    assert rows == [] and meta["source_cap_reached"] is True
    assert meta["detail_pages"] == 0


@pytest.mark.skipif(os.environ.get("RUN_GIMHAE_LIVE") != "1", reason="opt-in live audit")
def test_live_gimhae_complete_snapshot() -> None:
    rows, _, meta = gimhae.collect_gimhae_education(_target(), today="2026-07-23")
    assert len(rows) == 488
    assert meta["source_status_counts"] == {
        "접수중": 25,
        "대기자접수중": 29,
        "정원마감": 61,
        "홍보중": 54,
        "접수마감": 319,
    }
    assert meta["status_counts"] == {"OPEN": 54, "CLOSED": 380, "SCHEDULED": 54}
    assert meta["logical_requests"] == 547
