from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import municipal_paju as paju

NATIVE_CODE = "EDC_0001"
NATIVE_SN = "2026074021"
LIB_ID = "53767"


class Response:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.status_code = 200
        self.history = []
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}
        self.content = html.encode()


class Session:
    def close(self) -> None:
        pass


def _options(items):
    return "".join(f'<option value="{escape(v)}">{escape(t)}</option>' for v, t in items)


def _form(page: int) -> str:
    return f'''<form id="dataForm" name="dataForm" method="post" action="BD_lectureList.do">
  <input name="eventClCode" value="1001"><input name="q_currPage" value="{page}"><input name="q_rowPerPage" value="10">
  <select name="q_edcMnnstCode">{_options(paju.PAJU_INSTITUTION_REGISTRY)}</select>
  <select name="q_rcritSttusCode">{_options(paju.PAJU_STATUS_REGISTRY)}</select>
  <select name="q_edcReqstMth">{_options(paju.PAJU_METHOD_REGISTRY)}</select></form>'''


def _native_row(sn: str = NATIVE_SN) -> str:
    return f"""<tr><td>평생학습관</td><td><a onclick="jsView('{NATIVE_CODE}','{sn}');">네이티브 강좌</a></td>
  <td><span class="text-red">무료</span><span class="text-label">온라인접수</span></td><td>0 / 10</td>
  <td>2026-07-20 ~ 2026-07-31</td><td>모집중<button onclick="jsRequest('{NATIVE_CODE}','{sn}','2026-07-20')">접수하기</button></td></tr>"""


def _library_row(idx: str = LIB_ID) -> str:
    return f"""<tr><td>도서관</td><td><a onclick="jsLibraryRequestForm('https://lib.paju.go.kr/srlib/lectureDetail.do?lectureIdx={idx}');">도서관 강좌</a></td>
  <td><span class="text-red">무료</span><span class="text-label">온라인접수</span></td><td>10</td>
  <td>2026-07-21 ~ 2026-08-01</td><td>모집예정</td></tr>"""


def _list(page: int, bad_sentinel: bool = False) -> str:
    rows = '<tr><td colspan="6">게시물이 없습니다.</td></tr>' if page > 1 else _native_row() + _library_row()
    if bad_sentinel and page > 1:
        rows = _native_row("2026074999")
    return f"""{_form(page)}<div class="list-info"><div class="total">총 2 건, 페이지 {page} / 1</div></div>
      <div class="table-list-edu"><table><tbody>{rows}</tbody></table></div>"""


def _table(fields):
    items = list(fields.items())
    rows = []
    for i in range(0, len(items), 2):
        row = "".join(f"<th>{escape(k)}</th><td>{escape(v)}</td>" for k, v in items[i : i + 2])
        rows.append(f"<tr>{row}</tr>")
    return "<table>" + "".join(rows) + "</table>"


def _native_detail(*, bad_title=False, bad_branch=False, bad_identity=False, bad_attachment=False) -> str:
    fields = {
        "교육분류": "기타",
        "모집상태": "모집중",
        "접수방법": "온라인",
        "모집기간": "2026-07-20 ~ 2026-07-31",
        "교육기간": "2026-08-01 ~ 2026-09-01",
        "교육시간": "10:00 ~ 12:00",
        "교육장소": "" if bad_branch else "평생학습관 2층 평화실",
        "모집인원": "10",
        "교육비 여부": "무료",
    }
    sn = "2026074999" if bad_identity else NATIVE_SN
    att = "https://evil.example/fileDownload?id=1" if bad_attachment else "/component/file/ND_fileDownload.do?id=abc"
    return f'''<form id="dataForm"><input name="edcMnnstCode" value="{NATIVE_CODE}"><input name="edcSn" value="{sn}"></form>
      <h1 class="article-subject">{"changed" if bad_title else "네이티브 강좌 - 평생학습관"}</h1>{_table(fields)}
      <button onclick="jsRequestForm();">교육프로그램 신청</button><a href="{att}">download</a>'''


def _library_detail(*, bad_title=False, bad_branch=False) -> str:
    fields = {
        "프로그램명": "접수예정 " + ("changed" if bad_title else "도서관 강좌"),
        "접수기간": "2026.07.21 ~ 2026.08.01",
        "수강기간": "2026.08.10",
        "시간": "당일 10:00~12:00",
        "장소": "" if bad_branch else "술이홀도서관 다목적실",
        "접수방법": "온라인접수",
        "정원": "10",
        "재료비": "없음",
    }
    return _table(fields)


@dataclass
class Fixture:
    bad_sentinel: bool = False
    bad_title: bool = False
    bad_branch: bool = False
    bad_identity: bool = False
    bad_attachment: bool = False

    def fetch(self, _session, method, url, *, timeout):
        del timeout
        assert method == "GET"
        p = urlparse(url)
        q = parse_qs(p.query)
        if p.path == paju.PAJU_LIST_PATH:
            return Response(url, _list(int(q["q_currPage"][0]), self.bad_sentinel))
        if p.path == paju.PAJU_DETAIL_PATH:
            return Response(
                url,
                _native_detail(
                    bad_title=self.bad_title,
                    bad_branch=self.bad_branch,
                    bad_identity=self.bad_identity,
                    bad_attachment=self.bad_attachment,
                ),
            )
        return Response(url, _library_detail(bad_title=self.bad_title, bad_branch=self.bad_branch))


def _target(provider=paju.PAJU_PROVIDER, url=paju.PAJU_URL):
    return {"provider": provider, "url": url}


def test_complete_two_owner_snapshot_and_private_routes_unrequested():
    rows, parser, meta = paju.collect_paju_education(
        _target(), today="2026-07-23", detail_workers=1, session_factory=Session, fetcher=Fixture().fetch
    )
    assert parser == paju.PAJU_PARSER and len(rows) == 2
    assert {r["identity_owner"] for r in rows} == {"native", "library"}
    assert len({r["provider_course_id"] for r in rows}) == 2 and all(r["application_url"] == "" for r in rows)
    assert meta["source_total"] == meta["current_source_count"] == 2 and meta["logical_requests"] == 6
    assert meta["owner_identity_disjoint"] and meta["snapshot_complete"]
    assert (
        meta["application_endpoint_requests"]
        == meta["attachment_endpoint_requests"]
        == meta["pii_endpoint_requests"]
        == 0
    )


def test_owner_boundaries_and_namespaces():
    assert paju.is_paju_target(_target())
    assert not paju.is_paju_target(_target(paju.PAJU_EXCLUDED_ROOT_PROVIDER, paju.PAJU_ROOT_URL))
    assert not paju.is_paju_target(_target(paju.PAJU_YOUTH_PROVIDER, "https://paju.pcy.or.kr/fmcs/2"))
    native = paju.paju_source_identity("native", NATIVE_CODE, NATIVE_SN)
    library = paju.paju_source_identity("library", LIB_ID)
    assert native != library and paju.PAJU_YOUTH_PROVIDER not in native + library


def test_library_title_matching_accepts_branch_and_status_prefix_drift():
    listed = "[문산 I 가족 평화프로그램] 우리가 꿈꾸는 평화세상"
    detail = "접수중 [가족 평화프로그램] 우리가 꿈꾸는 평화세상"
    assert paju._library_title_matches(listed, detail, "모집중")
    assert paju._library_title_matches(listed, detail, "모집예정")
    assert paju._library_detail_state(detail) == ("접수중", "OPEN")
    campaign_detail = "접수중 [문산] ★추가모집★ 우리가 꿈꾸는 평화세상"
    assert paju._library_title_matches(listed, campaign_detail, "모집마감")
    assert not paju._library_title_matches(listed, "접수중 [가족 평화프로그램] 다른 강좌", "모집중")


def test_library_application_extension_requires_open_detail_and_same_start():
    start = date(2026, 7, 13)
    listed_end = date(2026, 7, 27)
    extended_end = date(2026, 7, 29)
    assert paju._library_application_extension_allowed(start, listed_end, start, extended_end, "모집마감", "OPEN")
    assert not paju._library_application_extension_allowed(
        start, listed_end, date(2026, 7, 14), extended_end, "모집마감", "OPEN"
    )
    assert not paju._library_application_extension_allowed(start, listed_end, start, extended_end, "모집마감", "CLOSED")


@pytest.mark.parametrize(
    ("fixture", "fragment"),
    [
        (Fixture(bad_sentinel=True), "empty sentinel"),
        (Fixture(bad_title=True), "title drift"),
        (Fixture(bad_branch=True), "unaudited official branch"),
        (Fixture(bad_identity=True), "application identity drift"),
        (Fixture(bad_attachment=True), "unsafe attachment"),
    ],
)
def test_contract_drift_fails_closed(fixture, fragment):
    rows, _, meta = paju.collect_paju_education(
        _target(), today="2026-07-23", detail_workers=1, session_factory=Session, fetcher=fixture.fetch
    )
    assert rows == [] and fragment in meta["configured_collection_error"] and not meta["snapshot_complete"]


def test_caps_fail_before_details():
    rows, _, meta = paju.collect_paju_education(
        _target(), today="2026-07-23", max_pages=3, detail_workers=1, session_factory=Session, fetcher=Fixture().fetch
    )
    assert rows == [] and meta["source_cap_reached"] and meta["detail_pages"] == 0


def test_central_router_dispatches_youth_aggregate(monkeypatch):
    from Crawler import Crawler_MunicipalYaml as central

    sentinel = ([{"title": "youth"}], "paju-youth-test", {"snapshot_complete": True})
    monkeypatch.setattr(central, "collect_paju_pcy_fmcs_aggregate", lambda *_args, **_kwargs: sentinel)
    target = central.CrawlTarget(
        provider=paju.PAJU_YOUTH_PROVIDER,
        name="Paju youth",
        branch="Paju youth",
        url="https://paju.pcy.or.kr/fmcs/2",
        source="test",
    )
    assert central.collect_from_url(target, timeout=1, max_depth=0, max_pages=20, detail_limit=300) == sentinel


def test_youth_aggregate_uses_declared_totals_not_audited_count(monkeypatch):
    from Crawler import Crawler_MunicipalYaml as central

    companies = [
        {"comcd": "PJYF01", "comnm": "파주시청소년수련관"},
        {"comcd": "PJYF02", "comnm": "교하청소년문화의집"},
        {"comcd": "PJYF03", "comnm": "금촌청소년문화의집"},
        {"comcd": "PJYF04", "comnm": "운정청소년센터"},
    ]

    class YouthSession:
        def get(self, *_args, **_kwargs):
            return object()

    def request_json(_session, _root, endpoint, params, *_args):
        if endpoint == "rest/common/company":
            return companies
        comcd = params["company_code"]
        if comcd == "PJYF03":
            return []
        return [{"comcd": comcd, "total_count": 2, "id": f"{comcd}-{index}"} for index in range(2)]

    monkeypatch.setattr(central, "session", YouthSession)
    monkeypatch.setattr(central, "fmcs_http_method", lambda *_args: "GET")
    monkeypatch.setattr(central, "fmcs_request_json", request_json)
    monkeypatch.setattr(central, "fmcs_json_rows", lambda value: value)
    monkeypatch.setattr(
        central,
        "fmcs_row_from_api_item",
        lambda _target, _url, item, _search: {
            "provider_course_id": item["id"],
            "title": item["id"],
            "raw_url": f"https://example/{item['id']}",
            "raw_fields": {},
            "category": "",
        },
    )
    monkeypatch.setattr(central, "paju_pcy_detail_fields", lambda *_args: {})
    monkeypatch.setattr(central, "dedupe_rows", lambda rows: list(rows))
    target = central.CrawlTarget(
        provider=paju.PAJU_YOUTH_PROVIDER,
        name="Paju youth",
        branch="Paju youth",
        url="https://paju.pcy.or.kr/fmcs/2",
        source="test",
    )

    rows, parser, meta = central.collect_paju_pcy_fmcs_aggregate(target, timeout=1, max_pages=4, detail_limit=10)
    assert len(rows) == 6 and parser == "paju_pcy_fmcs_aggregate_api+all_details"
    assert meta["expected_total_count"] == 6 and meta["audited_total_count"] == 183
    assert meta["company_totals"] == {"PJYF01": 2, "PJYF02": 2, "PJYF03": 0, "PJYF04": 2}
    assert meta["snapshot_complete"]


def test_youth_detail_excludes_instructor_description_and_raw_pairs(monkeypatch):
    from Crawler import Crawler_MunicipalYaml as central

    class DetailResponse:
        text = "<html></html>"

        def raise_for_status(self):
            pass

    class DetailSession:
        def get(self, *_args, **_kwargs):
            return DetailResponse()

    monkeypatch.setattr(
        central,
        "fmcs_detail_fields",
        lambda *_args: {
            "title": "safe",
            "period": "2026-08-01 ~ 2026-08-31",
            "instructor": "private",
            "description": "private",
            "phone": "010-0000-0000",
            "raw_detail_pairs": {"강사명": "private"},
        },
    )
    detail = central.paju_pcy_detail_fields(DetailSession(), "https://example/detail", 1)
    assert detail == {"title": "safe", "period": "2026-08-01 ~ 2026-08-31"}


def test_target_ownership_and_full_snapshot_metadata():
    root = Path(__file__).resolve().parents[1]
    lifelong = yaml.safe_load((root / "config/crawl_targets/lifelong_learning.yaml").read_text(encoding="utf-8"))[
        "targets"
    ]
    sports = yaml.safe_load((root / "config/crawl_targets/sports_facility.yaml").read_text(encoding="utf-8"))["targets"]
    lifelong_by_provider = {row["provider"]: row for row in lifelong}
    root_alias = lifelong_by_provider[paju.PAJU_EXCLUDED_ROOT_PROVIDER]
    canonical = lifelong_by_provider[paju.PAJU_PROVIDER]
    youth = {row["provider"]: row for row in sports}[paju.PAJU_YOUTH_PROVIDER]

    assert root_alias["crawler_status"] == f"duplicate_url:{paju.PAJU_PROVIDER}"
    assert root_alias["duplicate_of"] == paju.PAJU_PROVIDER
    for row in (canonical, youth):
        assert row["crawler_status"] == "ready"
        assert row["full_snapshot_required"] is True
        assert row["municipality_code"] == paju.PAJU_MUNICIPALITY_CODE
        assert row["service_group_policy"] == "locked"


@pytest.mark.skipif(os.environ.get("RUN_PAJU_LIVE") != "1", reason="opt-in live audit")
def test_live_complete_snapshot():
    rows, _, meta = paju.collect_paju_education(_target(), today="2026-07-23")
    assert len(rows) == 210 and meta["source_total"] == 755 and meta["owner_counts"] == {"native": 125, "library": 85}
    assert meta["source_status_counts"] == {
        "모집중": 33,
        "대기모집": 19,
        "모집예정": 29,
        "교육중": 90,
        "모집마감": 39,
        "교육종료": 539,
        "교육폐강": 6,
    }
    assert meta["status_counts"] == {"OPEN": 52, "CLOSED": 129, "SCHEDULED": 29}
    assert meta["logical_requests"] == 290 and meta["snapshot_complete"]
