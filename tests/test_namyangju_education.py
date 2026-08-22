from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from html import escape
import json
import os
from threading import Lock
from urllib.parse import urlencode, urlparse

import pytest

from Crawler import municipal_namyangju as namyangju


OPEN_ID = "101"
CLOSED_ID = "102"


class Response:
    def __init__(
        self,
        url: str,
        content: str | bytes,
        *,
        content_type: str,
        status: int = 200,
    ) -> None:
        self.url = url
        self.status_code = status
        self.history: list[object] = []
        self.headers = {"Content-Type": content_type}
        self.content = content.encode("utf-8") if isinstance(content, str) else content


class Session:
    def close(self) -> None:
        pass


def _landing_html(*, bad_registry: bool = False) -> str:
    defaults = namyangju.namyangju_list_params(1)
    hidden = "".join(
        f'<input type="hidden" name="{escape(key)}" value="{escape(value)}">'
        for key, value in defaults.items()
    )
    branches = list(namyangju.NAMYANGJU_BRANCH_REGISTRY)
    if bad_registry:
        branches[1] = (branches[1][0], "변경된 센터")
    checks = "".join(
        f'<input type="checkbox" id="org-{index}" name="sOrgNo" value="{escape(value)}">'
        f'<label for="org-{index}">{escape(label)}</label>'
        for index, (value, label) in enumerate(branches)
    )
    return (
        f'<form id="searchVO" name="searchVO" method="get">{hidden}</form>'
        f'<form id="searchSmart" name="searchSmart">{checks}</form>'
    )


def _item(
    identity: int,
    rnum: int,
    *,
    current: bool,
    source_status: str = "종료",
    status_class: str = "09",
    org_no: str = "18",
    branch: str = "와부읍 주민자치센터",
    access_code: str = "3001",
    plan: bool = False,
) -> dict[str, object]:
    return {
        "edc_prgm_no": identity,
        "edc_prgm_nm": f"강좌 {identity}",
        "org_no": int(org_no),
        "org_name": branch,
        "edc_status": source_status,
        "edc_status_class": status_class,
        "edc_sdate": "20260801" if current else "20260101",
        "edc_edate": "20260930" if current else "20260201",
        "edc_rsvn_sdate": "20260701" if current else "20251201",
        "edc_rsvn_edate": "20260731" if current else "20251231",
        "rnum": rnum,
        "tot_count": 12,
        "edc_rsvn_accssrd": access_code,
        "rsvn_type_nm": "선착접수",
        "ctg_nm": "문화교양",
        "ctg_cd": "0060090000",
        "edc_rsvnset_seq": "202601",
        "edc_day_gbn_nm": "화",
        "edc_time": "10:00 ~ 11:00",
        "edc_place_nm": "교육실",
        "target_name": "성인",
        "area_cd": "12",
        "area_nm": "와부읍",
        "sale_amt": 10000,
        "edc_pncpa": 20,
        "edc_plan_fileid": "0000012345" if plan else "",
    }


def _all_items() -> list[dict[str, object]]:
    rows = [
        _item(
            101,
            1,
            current=True,
            source_status="접수중",
            status_class="01",
            plan=True,
        ),
        _item(
            102,
            2,
            current=True,
            source_status="종료",
            status_class="09",
            org_no="23",
            branch="퇴계원읍주민자치센터",
            access_code="2001",
        ),
    ]
    rows.extend(_item(identity, rnum, current=False) for rnum, identity in enumerate(range(103, 113), 3))
    return rows


def _payload(page: int, rows: list[dict[str, object]]) -> str:
    if page == 1:
        contents = rows[:10]
        total = 12
    elif page == 2:
        contents = rows[10:]
        total = 12
    else:
        contents = []
        total = 0
    value = {"result": True, "data": {"pagination": {"TotalRecordCount": total}, "contents": contents}}
    return json.dumps(json.dumps(value, ensure_ascii=False), ensure_ascii=False)


def _detail_html(
    item: dict[str, object],
    *,
    bad_title: bool = False,
    bad_branch: bool = False,
    bad_control: bool = False,
    bad_attachment: bool = False,
    bad_identity: bool = False,
) -> str:
    identity = str(item["edc_prgm_no"])
    title = "변경된 강좌" if bad_title else str(item["edc_prgm_nm"])
    branch = "미등록 센터" if bad_branch else str(item["org_name"])
    source_status = str(item["edc_status"])
    plan = bool(item["edc_plan_fileid"])
    attachment_href = (
        "/unsafe/download?originName=plan.pdf"
        if bad_attachment
        else "/web/common/file/view/202607/EDC_202607010101010101?originName=plan.pdf"
    )
    attachment = f'<a href="{escape(attachment_href)}">plan.pdf</a>' if plan else ""
    pairs = [
        ("교육기관", branch),
        ("접수방법", "온라인+방문접수/선착접수"),
        ("모집인원", "20명"),
        ("신청/모집인원", "3 명/ 20 명"),
        ("신청기간", "26.07.01 ~ 26.07.31"),
        ("교육기간", "26.08.01 ~ 26.09.30"),
        ("강사명", "폐기 대상 강사"),
        ("교육장소", "교육실"),
        ("교육시간", "화 / 10:00 ~ 11:00 (2026년도 1차)"),
        ("교육비", "유료 10,000원"),
        ("강의계획서", attachment),
        ("문의전화", "031-000-0000"),
        ("강좌소개", "폐기 대상 설명"),
        ("특이사항 (준비물, 기타사항 등)", "폐기 대상 준비물"),
        ("교육대상", "성인"),
        ("주소", "폐기 대상 주소"),
    ]
    cells: list[str] = []
    for key, value in pairs:
        rendered = value if key == "강의계획서" else escape(value)
        cells.append(f"<th>{escape(key)}</th><td>{rendered}</td>")
    table_rows = "".join(f"<tr>{''.join(cells[index:index + 4])}</tr>" for index in range(0, len(cells), 4))
    access = str(item["edc_rsvn_accssrd"])
    if source_status == "접수중" and access in {"1001", "3001"}:
        action_href, action_class, action_text = "javascript:fnDetailApply();", "green", "강좌 신청하기"
    elif source_status == "접수중":
        action_href, action_class, action_text = "#none", "red", "현장에서 접수하세요!"
    else:
        action_href, action_class, action_text = "#none", "gray", "접수종료"
    if bad_control:
        action_href = "/web/edc/rsvn/termsAgree/101/202601"
    script_identity = "999" if bad_identity else identity
    return f'''
      <div class="myTable">
        <h3 class="myTable-title"><p>{escape(title)}</p><span class="bedge red">{escape(source_status)}</span></h3>
        <div class="myTable-inner"><div class="myTable-wrap"><table>{table_rows}</table></div></div>
      </div>
      <div class="badge-btn"><a class="black" href="javascript:history.back();">목록</a>
        <a class="{action_class}" href="{escape(action_href)}">{escape(action_text)}</a></div>
      <table class="table-check"><tr><th>이름</th><td>김별내</td></tr></table>
      <script>
        function fnDetailApply() {{
          var data = new Object();
          data.edcPrgmNo = {script_identity};
          data.edcRsvnsetSeq = 202601;
          location.href = "../rsvn/termsAgree/{script_identity}/202601";
          $.ajax({{url:"../rsvn/termsAgreeAjax/{script_identity}/202601"}});
        }}
      </script>
    '''


@dataclass
class Fixture:
    bad_registry: bool = False
    bad_sentinel: bool = False
    boundary_drift: bool = False
    duplicate_identity: bool = False
    bad_title: bool = False
    bad_branch: bool = False
    bad_control: bool = False
    bad_attachment: bool = False
    bad_identity: bool = False
    transient_first_page: bool = False
    calls: list[tuple[str, str, dict[str, str] | None]] = field(default_factory=list)
    page_calls: dict[int, int] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def fetch(
        self,
        _session: object,
        method: str,
        url: str,
        *,
        timeout: int,
        params: dict[str, str] | None = None,
    ) -> Response:
        del timeout
        assert method == "GET"
        with self.lock:
            self.calls.append((method, url, dict(params) if params else None))
        if url == namyangju.NAMYANGJU_CANONICAL_URL:
            assert params is None
            return Response(url, _landing_html(bad_registry=self.bad_registry), content_type="text/html")
        if url == namyangju.NAMYANGJU_API_URL:
            assert params is not None
            page = int(params["pageIndex"])
            assert params == namyangju.namyangju_list_params(page)
            with self.lock:
                self.page_calls[page] = self.page_calls.get(page, 0) + 1
                call_number = self.page_calls[page]
            observed_url = f"{url}?{urlencode(params)}"
            if self.transient_first_page and page == 1 and call_number == 1:
                return Response(observed_url, "temporary", content_type="application/json", status=503)
            rows = _all_items()
            if self.duplicate_identity:
                rows[10]["edc_prgm_no"] = 101
            if self.boundary_drift and page == 1 and call_number > 1:
                rows[0]["edc_prgm_nm"] = "경계 변경"
            if self.bad_sentinel and page == 3:
                value = {"result": True, "data": {"pagination": {"TotalRecordCount": 12}, "contents": rows[:1]}}
                body = json.dumps(json.dumps(value, ensure_ascii=False), ensure_ascii=False)
            else:
                body = _payload(page, rows)
            return Response(observed_url, body, content_type="application/json")
        identity = urlparse(url).path.rsplit("/", 1)[-1]
        assert identity in {OPEN_ID, CLOSED_ID} and params is None
        item = _all_items()[0 if identity == OPEN_ID else 1]
        return Response(
            url,
            _detail_html(
                item,
                bad_title=self.bad_title and identity == OPEN_ID,
                bad_branch=self.bad_branch and identity == OPEN_ID,
                bad_control=self.bad_control and identity == OPEN_ID,
                bad_attachment=self.bad_attachment and identity == OPEN_ID,
                bad_identity=self.bad_identity and identity == OPEN_ID,
            ),
            content_type="text/html",
        )


def _target(
    provider: str = namyangju.NAMYANGJU_PROVIDER,
    url: str = namyangju.NAMYANGJU_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "branch": "남양주시 주민자치센터"}


def _collect(fixture: Fixture, **kwargs: object):
    return namyangju.collect_namyangju_education(
        _target(),
        today="2026-07-23",
        session_factory=Session,
        fetcher=fixture.fetch,
        **kwargs,
    )


def test_complete_snapshot_and_private_routes_are_never_requested() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)
    assert parser == namyangju.NAMYANGJU_PARSER
    assert [row["source_course_id"] for row in rows] == [OPEN_ID, CLOSED_ID]
    assert rows[0]["provider_course_id"] == f"{namyangju.NAMYANGJU_PROVIDER}:edc_prgm_no:{OPEN_ID}"
    assert rows[0]["branch"] == "와부읍 주민자치센터"
    assert rows[1]["branch"] == "퇴계원읍주민자치센터"
    assert rows[0]["application_url"] == rows[1]["application_url"] == ""
    assert not any(key in row for row in rows for key in ("phone", "instructor", "description", "attachments"))
    assert meta["source_total"] == 12 and meta["current_source_count"] == 2
    assert meta["discovered_links"] == 12 and meta["pagination_detected"] is True
    assert meta["pages"] == 2 and meta["sentinel_page"] == 3 and meta["sentinel_rows"] == 0
    assert meta["required_list_requests"] == 6 and meta["list_requests"] == 6
    assert meta["detail_verified"] == 2 and meta["logical_requests"] == 9
    assert meta["application_control_count"] == 1
    assert meta["attachment_fields_discarded"] == 1
    assert meta["reservation_endpoint_requests"] == meta["attachment_endpoint_requests"] == 0
    assert meta["pii_endpoint_requests"] == meta["pii_values_persisted"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is False and meta["no_current_reason"] == ""
    requested = [url for _, url, _ in fixture.calls]
    assert all("termsAgree" not in url and "/file/view/" not in url and "login" not in url for url in requested)


def test_owner_aliases_and_namespaced_identity_are_strict() -> None:
    assert namyangju.is_namyangju_education_target(_target())
    assert not namyangju.is_namyangju_education_target(_target(url=namyangju.NAMYANGJU_LEGACY_DETAIL_URL))
    assert not namyangju.is_namyangju_education_target(_target(url=namyangju.NAMYANGJU_MUNICIPAL_MIRROR_URL))
    assert not namyangju.is_namyangju_education_target(_target(provider=namyangju.NAMYANGJU_STATIC_NOTICE_PROVIDER))
    assert not namyangju.is_namyangju_education_target(_target(url="http://jumin.nyj.go.kr/web/edc/program/list"))
    assert namyangju.namyangju_source_identity(4056) == (
        f"{namyangju.NAMYANGJU_PROVIDER}:edc_prgm_no:4056"
    )


@pytest.mark.parametrize(
    ("fixture", "fragment"),
    [
        (Fixture(bad_registry=True), "official branch registry"),
        (Fixture(bad_sentinel=True), "empty sentinel"),
        (Fixture(boundary_drift=True), "boundary stability"),
        (Fixture(duplicate_identity=True), "complete page union"),
        (Fixture(bad_title=True), "title/status drift"),
        (Fixture(bad_branch=True), "official branch drift"),
        (Fixture(bad_control=True), "application control"),
        (Fixture(bad_attachment=True), "attachment route"),
        (Fixture(bad_identity=True), "application identity"),
    ],
)
def test_contract_drift_fails_closed(fixture: Fixture, fragment: str) -> None:
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert fragment in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"max_pages": 5}, "max_pages cap"),
        ({"detail_limit": 1}, "detail_limit"),
    ],
)
def test_caps_fail_before_partial_output(kwargs: dict[str, int], fragment: str) -> None:
    rows, _, meta = _collect(Fixture(), **kwargs)
    assert rows == [] and meta["source_cap_reached"] is True
    assert fragment in meta["configured_collection_error"]
    assert meta["returned_count"] == 0


def test_transient_api_failure_is_retried_once() -> None:
    rows, _, meta = _collect(Fixture(transient_first_page=True))
    assert len(rows) == 2 and meta["snapshot_complete"] is True
    assert meta["request_retry_count"] == 1
    assert meta["physical_requests"] == meta["logical_requests"] + 1


def test_external_dedupe_cannot_hide_an_incomplete_snapshot() -> None:
    def destructive(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        return rows[:1]

    fixture = Fixture()
    rows, _, meta = namyangju.collect_namyangju_education(
        _target(),
        today=date(2026, 7, 23),
        session_factory=Session,
        fetcher=fixture.fetch,
        dedupe_rows=destructive,
    )
    assert rows == [] and "dedupe changed" in meta["configured_collection_error"]


@pytest.mark.skipif(os.environ.get("RUN_NAMYANGJU_LIVE") != "1", reason="opt-in live audit")
def test_live_complete_snapshot() -> None:
    rows, parser, meta = namyangju.collect_namyangju_education(
        _target(),
        today="2026-07-23",
    )
    baseline = namyangju.NAMYANGJU_LIVE_AUDIT_BASELINE
    assert parser == namyangju.NAMYANGJU_PARSER
    assert meta["source_total"] == baseline["source_total"]
    assert meta["pages"] == baseline["pages"]
    assert meta["current_source_count"] == baseline["current_source_count"]
    assert meta["source_identity_sha256"] == baseline["source_identity_sha256"]
    assert meta["current_identity_sha256"] == baseline["current_identity_sha256"]
    assert meta["branch_counts"] == baseline["current_branch_counts"]
    assert len(rows) == 1037 and meta["snapshot_complete"] is True


@pytest.mark.skipif(
    os.environ.get("RUN_NAMYANGJU_LIVE_TWICE") != "1",
    reason="opt-in two-run live identity audit",
)
def test_two_live_runs_have_identical_stable_identity_snapshot() -> None:
    first_rows, _, first_meta = namyangju.collect_namyangju_education(
        _target(), today="2026-07-23"
    )
    second_rows, _, second_meta = namyangju.collect_namyangju_education(
        _target(), today="2026-07-23"
    )
    assert first_meta["snapshot_complete"] and second_meta["snapshot_complete"]
    assert first_meta["source_identity_sha256"] == second_meta["source_identity_sha256"]
    assert first_meta["current_identity_sha256"] == second_meta["current_identity_sha256"]
    assert [row["provider_course_id"] for row in first_rows] == [
        row["provider_course_id"] for row in second_rows
    ]
