from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os

import pytest

from Crawler import municipal_gyeryong as gyeryong


OPEN_ID = "11111111111111111111111111111111"


class Response:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.status_code = 200
        self.history: list[object] = []
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}
        self.content = html.encode("utf-8")


class Session:
    def close(self) -> None:
        pass


def _options(items: tuple[tuple[str, str], ...]) -> str:
    return "".join(f'<option value="{escape(v)}">{escape(label)}</option>' for v, label in items)


def _form() -> str:
    return f'''<form name="searchFrm" method="post" action="?">
      <select name="sch_edu_se">{_options(gyeryong.GYERYONG_EDUCATION_REGISTRY)}</select>
      <select name="sch_edu_place">{_options(gyeryong.GYERYONG_PLACE_REGISTRY)}</select>
      <select name="sch_oper_mby">{_options(gyeryong.GYERYONG_OPERATOR_REGISTRY)}</select>
      <input name="sch_edu_bgng_ymd" value=""><input name="sch_edu_end_ymd" value="">
      <select name="sch_rcrit_mth">{_options(gyeryong.GYERYONG_METHOD_REGISTRY)}</select>
      <select name="skey">{_options(gyeryong.GYERYONG_SEARCH_REGISTRY)}</select><input name="sval" value="">
    </form>'''


def _card(identity: str, status: str, title: str) -> str:
    href = gyeryong.gyeryong_detail_url(identity).replace(gyeryong.GYERYONG_URL, "")
    return f'''<div class="col"><div class="inner"><a href="{escape(href)}">
      <div class="accept"><span>{status}</span><em>인터넷</em></div>
      <div class="list__divps"><div class="tit">{escape(title)}</div>
      <ul class="list_con"><li><span>학 기 명</span><em>1학기</em></li>
      <li><span>교육기간</span><em>2026-08-01 ~ 2026-09-01</em></li>
      <li><span>교육시간</span><em>10:00~12:00</em></li>
      <li><span>접수기간</span><em>2026-07-01 ~ 2026-07-31</em></li>
      <li><span>신청/정원</span><em>신청:3명 대기: 0명 / 10명</em></li></ul></div>
    </a></div></div>'''


def _list_html(page: int, *, bad_sentinel: bool = False) -> str:
    first = "22222222222222222222222222222222" if bad_sentinel and page == 2 else OPEN_ID
    cards = _card(first, "접수중", "공개 강좌") + _card(
        gyeryong.GYERYONG_TEST_IDENTITY, "접수마감", gyeryong.GYERYONG_TEST_TITLE
    )
    return f'''{_form()}<div class="program_con edu_list">{cards}</div>
      <ul class="pagination"><li class="page-item active"><a onclick="postPrintPage(1);">1</a></li>
      <li><a aria-label="last" onclick="postPrintPage(1);"></a></li></ul>'''


def _detail_html(identity: str, *, bad_title: bool = False, bad_branch: bool = False,
                 bad_application: bool = False, bad_attachment: bool = False) -> str:
    is_open = identity == OPEN_ID
    title = "changed" if bad_title and is_open else ("공개 강좌" if is_open else gyeryong.GYERYONG_TEST_TITLE)
    status = "접수중" if is_open else "접수마감"
    branch = "미등록 장소" if bad_branch and is_open else ("계룡시평생학습관" if is_open else "온라인")
    fields = [("학기명", "1학기"), ("운영주체", "계룡시청"), ("교육기간", "2026-08-01 ~ 2026-09-01"),
              ("접수기간", "2026-07-01 ~ 2026-07-31"), ("취소기간", "2026-07-01~2026-07-31"),
              ("교육장소", branch), ("교육주기", "매주 화요일"), ("교육대상", "계룡시민"),
              ("교육시간", "10:00~12:00"), ("신청/정원", "3명 / 10명"), ("대기자/정원", "0명 / 99명"),
              ("문의", "discarded"), ("신청방법", "인터넷")]
    lis = "".join(f'<li><span>{escape(k)}</span><em>{escape(v)}</em></li>' for k, v in fields)
    app_id = "33333333333333333333333333333333" if bad_application else identity
    application = f'<div><a href="?edu_no={app_id}&amp;mode=W">강좌신청</a></div>' if is_open else ""
    attachment = "/unsafe?file_id=" if bad_attachment and is_open else "/_prog/dn00/?file_id="
    files = "" if not is_open else f'<ul class="list_con btn-lst"><li><a href="{attachment}44444444444444444444444444444444">file</a></li></ul>'
    return f'''<div class="program_con program_view"><div class="accept"><span>{status}</span><em>인터넷</em></div>
      <div class="in_top"><div class="tit">{escape(title)}<span class="cond">교육대기</span></div></div>
      <ul class="list_con">{lis}</ul>{files}</div>{application}
      <table><tr><th>담당강사</th><td>discard</td><th>수강료</th><td>무료</td></tr>
      <tr><th>강좌 상세설명</th><td>discard</td></tr></table>'''


@dataclass
class Fixture:
    bad_sentinel: bool = False
    bad_title: bool = False
    bad_branch: bool = False
    bad_application: bool = False
    bad_attachment: bool = False

    def fetch(self, _session: object, method: str, url: str, *, timeout: int,
              data: dict[str, str] | None = None) -> Response:
        del timeout
        if method == "POST":
            assert url == gyeryong.GYERYONG_URL and data == gyeryong.gyeryong_list_data(int(data["GotoPage"]))
            return Response(url, _list_html(int(data["GotoPage"]), bad_sentinel=self.bad_sentinel))
        assert method == "GET" and data is None
        identity = dict(__import__("urllib.parse").parse.parse_qsl(__import__("urllib.parse").parse.urlparse(url).query))["mng_no"]
        return Response(url, _detail_html(identity, bad_title=self.bad_title, bad_branch=self.bad_branch,
            bad_application=self.bad_application, bad_attachment=self.bad_attachment))


def _target(provider: str = gyeryong.GYERYONG_PROVIDER, url: str = gyeryong.GYERYONG_URL) -> dict[str, str]:
    return {"provider": provider, "url": url}


def test_complete_ledger_excludes_audited_test_and_private_routes() -> None:
    rows, parser, meta = gyeryong.collect_gyeryong_education(
        _target(), today="2026-07-23", session_factory=Session, fetcher=Fixture().fetch
    )
    assert parser == gyeryong.GYERYONG_PARSER and len(rows) == 1
    assert rows[0]["source_course_id"] == OPEN_ID and rows[0]["application_url"] == ""
    assert rows[0]["branch"] == "계룡시평생학습관"
    assert rows[0]["raw_url"] == gyeryong.gyeryong_detail_url(OPEN_ID)
    assert rows[0]["period"] == "2026-08-01 ~ 2026-09-01"
    assert rows[0]["apply_period"] == "2026-07-01 ~ 2026-07-31"
    assert rows[0]["schedule_raw"] == "10:00~12:00"
    assert rows[0]["target"] == "계룡시민"
    assert rows[0]["venue_name"] == "계룡시평생학습관"
    assert rows[0]["description"] == "공개 강좌"
    assert meta["source_total"] == meta["current_source_count"] == 2
    assert meta["excluded_test_count"] == 1 and meta["logical_requests"] == 6
    assert meta["reservation_control_count"] == meta["attachment_fields_discarded"] == 1
    assert meta["reservation_endpoint_requests"] == meta["attachment_endpoint_requests"] == 0
    assert meta["pii_values_persisted"] == 0 and meta["snapshot_complete"] is True


def test_owner_boundary_and_namespaced_identity() -> None:
    assert gyeryong.GYERYONG_OPERATOR_REGISTRY[:2] == (
        ("__intro__", "운영주체전체"),
        ("0606", "계룡시 평생학습관"),
    )
    assert gyeryong._STATUS["접수예정"] == "SCHEDULED"
    assert gyeryong.is_gyeryong_target(_target())
    assert not gyeryong.is_gyeryong_target(_target(gyeryong.GYERYONG_EXCLUDED_FARM_PROVIDER, gyeryong.GYERYONG_FARM_URL))
    assert not gyeryong.is_gyeryong_target(_target(url=gyeryong.GYERYONG_FARM_URL))
    assert not gyeryong.is_gyeryong_target(_target(url="http://www.gyeryong.go.kr/lll/html/sub03/030102.html"))
    assert gyeryong.gyeryong_source_identity(OPEN_ID) == f"{gyeryong.GYERYONG_PROVIDER}:course:{OPEN_ID}"


@pytest.mark.parametrize(("fixture", "fragment"), [
    (Fixture(bad_sentinel=True), "clamp sentinel"), (Fixture(bad_title=True), "title/status drift"),
    (Fixture(bad_branch=True), "unaudited official branch"), (Fixture(bad_application=True), "reservation identity drift"),
    (Fixture(bad_attachment=True), "attachment control changed")])
def test_contract_drift_fails_closed(fixture: Fixture, fragment: str) -> None:
    rows, _, meta = gyeryong.collect_gyeryong_education(
        _target(), today="2026-07-23", session_factory=Session, fetcher=fixture.fetch
    )
    assert rows == [] and fragment in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_caps_fail_before_detail_output() -> None:
    rows, _, meta = gyeryong.collect_gyeryong_education(
        _target(), today="2026-07-23", max_pages=3, session_factory=Session, fetcher=Fixture().fetch
    )
    assert rows == [] and meta["source_cap_reached"] is True and meta["detail_pages"] == 0


@pytest.mark.skipif(os.environ.get("RUN_GYERYONG_LIVE") != "1", reason="opt-in live audit")
def test_live_complete_snapshot() -> None:
    rows, _, meta = gyeryong.collect_gyeryong_education(_target(), today="2026-07-23")
    assert len(rows) == 18 and meta["source_total"] == 207 and meta["current_source_count"] == 19
    assert meta["source_status_counts"] == {"접수중": 4, "대기접수": 5, "접수마감": 10}
    assert meta["status_counts"] == {"OPEN": 9, "CLOSED": 9}
    assert meta["logical_requests"] == 44 and meta["snapshot_complete"] is True
