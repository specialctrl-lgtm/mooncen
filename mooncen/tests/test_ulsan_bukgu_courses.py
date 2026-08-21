from __future__ import annotations

import base64
from datetime import date
import hashlib
import math
import ssl
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_ulsan_bukgu as municipal


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _public_target() -> dict[str, str]:
    return {
        "provider": municipal.ULSAN_BUKGU_PUBLIC_PROVIDER,
        "url": municipal.ULSAN_BUKGU_PUBLIC_URL,
    }


def _library_target() -> dict[str, str]:
    return {
        "provider": municipal.ULSAN_BUKGU_LIBRARY_PROVIDER,
        "url": municipal.ULSAN_BUKGU_LIBRARY_URL,
    }


def _public_identity(code: str) -> str:
    index = [item[0] for item in municipal.PUBLIC_FACILITIES].index(code) + 1
    return f"L{index:07d}"


def _public_nav(*, missing_last: bool = False) -> str:
    facilities = municipal.PUBLIC_FACILITIES[:-1] if missing_last else municipal.PUBLIC_FACILITIES
    return "<ul>" + "".join(
        f'<li><a href="/yeyak/new_lecture/lecture?mem_id={code}">{name}</a></li>'
        for code, name in facilities
    ) + "</ul>"


def _public_page(
    code: str,
    *,
    clamp_changed: bool = False,
    missing_last_nav: bool = False,
) -> str:
    facility = dict(municipal.PUBLIC_FACILITIES)[code]
    identity = _public_identity(code)
    title = f"{facility} 테스트 강좌" + (" 변형" if clamp_changed else "")
    is_open = code == municipal.PUBLIC_FACILITIES[0][0]
    is_closed = code == municipal.PUBLIC_FACILITIES[1][0]
    source_status = "접수중" if is_open else "접수마감" if is_closed else "준비중"
    person = "2 / 10" if is_open else source_status
    headers = "".join(f"<th>{value}</th>" for value in municipal.PUBLIC_TABLE_HEADERS)
    return f"""
    <html><head><title>울산북구공공시설예약서비스</title></head><body>
      {_public_nav(missing_last=missing_last_nav)}
      <form method="get" action="/yeyak/new_lecture/lecture">
        <select name="selItemKind"><option>전체</option></select>
        <input name="mem_id" value="{code}">
        <input name="selkind"><input name="selcheck"><input name="seek">
      </form>
      <table class="table_list">
        <thead><tr>{headers}</tr></thead>
        <tbody><tr>
          <td class="subject">
            <a href="/yeyak/new_lecture/lecture?prc=detail&amp;lec_id={identity}&amp;mem_id={code}">
              <p class="tit">{title}</p>
              <p class="edu_date"><span>강습기간 :</span>2099.08.01~2099.08.31</p>
              <p class="edu_date"><span>강습시간 :</span>월수 10:00~11:00</p>
            </a>
          </td>
          <td class="devide">문화교육</td><td class="person">{person}</td>
          <td class="pay">110,000<p class="youth">청소년(100,000)</p></td>
          <td class="state"><strong>{source_status}</strong></td>
        </tr></tbody>
      </table>
    </body></html>
    """


def _public_detail(code: str, *, application_control: bool = True) -> str:
    facility = dict(municipal.PUBLIC_FACILITIES)[code]
    identity = _public_identity(code)
    control = (
        '<a onclick="goto_lecture();"><button class="bt_visible">강좌신청</button></a>'
        if application_control and code == municipal.PUBLIC_FACILITIES[0][0]
        else ""
    )
    schedule = "월수 10:00~11:00"
    if code == municipal.PUBLIC_FACILITIES[1][0]:
        schedule += " 주말/공휴일 07:00 ~ 18:00"
    return f"""
    <html><body><div class="select_area">
      <div class="s_tit">{facility} 테스트 강좌</div>
      <table class="table_st2">
        <tr><th>강습대상</th><td>성인</td></tr>
        <tr><th>정원</th><td>10명(접수가능인원 : 2명)</td></tr>
        <tr><th>신규회원 모집기간</th><td>2099-07-01 09:00 ~ 2099-07-31 18:00</td></tr>
        <tr><th>강습기간</th><td>2099.08.01~2099.08.31</td></tr>
        <tr><th>강습시간</th><td>{schedule}</td></tr>
        <tr><th>수강료</th><td>110,000원</td></tr>
        <tr><th>강사</th><td>민감강사명</td></tr>
        <tr><th>문의전화</th><td>052-000-0000</td></tr>
        <tr><th>이메일</th><td>private@example.test</td></tr>
      </table>{control}
    </div>
    <script>
      function goto_lecture() {{
        location.href='/yeyak/new_lecture/lecture?mem_id={code}&prc=rsvinfo&lec_id={identity}';
      }}
    </script></body></html>
    """


def _collect_public(
    *,
    missing_last_nav: bool = False,
    clamp_mismatch: bool = False,
    application_control: bool = True,
    detail_limit: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str], FakeSession]:
    fetched: list[str] = []
    session = FakeSession()

    def fetcher(_session: Any, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if query.get("prc") == ["detail"]:
            return _soup(
                _public_detail(
                    query["mem_id"][0], application_control=application_control
                )
            )
        code = (query.get("mem_id") or [municipal.PUBLIC_FACILITIES[0][0]])[0]
        changed = clamp_mismatch and query.get("pg") == ["999999"]
        return _soup(
            _public_page(
                code,
                clamp_changed=changed,
                missing_last_nav=missing_last_nav,
            )
        )

    rows, _parser, meta = municipal.collect_ulsan_bukgu_public_courses(
        _public_target(),
        timeout=7,
        max_pages=20,
        detail_limit=detail_limit,
        fetcher=fetcher,
        session_factory=lambda: session,
        today=date(2026, 7, 21),
    )
    return rows, meta, fetched, session


def _library_card(
    source: Any,
    identity: int,
    title: str,
    *,
    branch: str,
    status: str,
    capacity: str = "0 / 10 명",
) -> str:
    state = {
        "OPEN": ("btn_ing", "수강신청"),
        "SCHEDULED": ("btn_prepare", "신청준비"),
        "CLOSED": ("btn_close", "기간종료"),
    }[status]
    state_href = (
        f"{source.application_path}?edu_idx={identity}"
        if status == "OPEN"
        else "#javascript:;"
    )
    check_path = source.list_path.replace("list.do", "user.do")
    return f"""
    <li>
      <p class="tit"><a href="{source.detail_path}?edu_idx={identity}&amp;prepage={source.list_path}">{title}</a></p>
      <p class="cate">{branch}</p>
      <div class="sm_box">
        <dl><dt>신청기간</dt><dd>2099-07-01 09:00 ~ 2099-07-31 18:00</dd></dl>
        <dl><dt>운영기간</dt><dd>2099-08-01 ~ 2099-08-31 월요일 10:00~11:00</dd></dl>
        <dl><dt>참가대상</dt><dd>성인</dd></dl>
        <dl><dt>모집인원</dt><dd>{capacity}</dd></dl>
      </div>
      <div class="btn_box">
        <a class="btn_sm {state[0]}" href="{state_href}">{state[1]}</a>
        <a class="btn_check" href="{check_path}?edu_idx={identity}">신청확인</a>
      </div>
    </li>
    """


def _library_page(
    source: Any,
    specs: list[dict[str, Any]],
    *,
    clamp: bool,
    declared_total: int | None = None,
    clamp_title_change: bool = False,
) -> str:
    total = len(specs) if declared_total is None else declared_total
    pages = max(1, math.ceil(total / municipal.LIBRARY_PAGE_SIZE))
    expected_filters = (
        municipal.LIBRARY_FILTER_BRANCHES
        if source == municipal.LIBRARY_COURSES
        else municipal.LIBRARY_EVENT_FILTER_BRANCHES
    )
    filters = "".join(
        f'<a href="{source.list_path}?sh_ct_idx={index}">{branch}</a>'
        for index, branch in enumerate(expected_filters, 1)
    )
    paginator = (
        "".join(
            f'<a data-page="{page}" href="?v_page={page}">{page}</a>'
            for page in range(1, pages + 1)
        )
        if clamp
        else '<strong>1</strong>'
    )
    cards: list[str] = []
    for index, spec in enumerate(specs):
        title = spec["title"]
        if clamp_title_change and index == 0:
            title += " 변형"
        cards.append(
            _library_card(
                source,
                spec["identity"],
                title,
                branch=spec["branch"],
                status=spec["status"],
                capacity=spec.get("capacity", "0 / 10 명"),
            )
        )
    return f"""
    <html><head><title>{source.label} | 울산 북구 구립도서관</title></head><body>
      {filters}
      <form method="get" action="{source.list_path}">
        <select name="sh_ct_idx"></select><input name="v_search"><input name="v_keyword">
      </form>
      <div class="board_total"><div class="board_total_left">총 <strong class="eng">{total}</strong>개의 프로그램이 등록되어 있습니다.</div></div>
      <div id="board"><div class="lesson"><ul>{''.join(cards)}</ul></div></div>
      <div class="board_paginate">{paginator}</div>
    </body></html>
    """


def _plain_library_title(title: str) -> str:
    return municipal._library_display_title(title)[0]


def _library_detail(source: Any, spec: dict[str, Any]) -> str:
    status = {
        "OPEN": ("btn_receipt", "신청중"),
        "SCHEDULED": ("btn_prepare", "신청준비"),
        "CLOSED": ("btn_close", "기간종료"),
    }[spec["status"]]
    identity = spec["identity"]
    title = _plain_library_title(spec["title"])
    venue_key = "교육장소" if source == municipal.LIBRARY_COURSES else "행사장소"
    application = (
        f'<a class="con_btn btn_receipt" href="{source.application_path}?edu_idx={identity}">신청</a>'
        if spec["status"] == "OPEN"
        else ""
    )
    return f"""
    <html><body><div id="contents"><div class="table_bview"><table>
      <thead><tr><th>{title}<a class="btn_sm {status[0]}">{status[1]}</a></th></tr></thead>
      <tbody><tr><td>
        <dl class="info"><dt>운영기간</dt><dd>2099-08-01 ~ 2099-08-31</dd></dl>
        <dl class="info"><dt>운영시간</dt><dd>월요일 10:00~11:00</dd></dl>
        <dl class="info"><dt>신청기간</dt><dd>2099-07-01 ~ 2099-07-31</dd></dl>
        <dl class="info"><dt>신청방법</dt><dd>인터넷접수</dd></dl>
        <dl class="info"><dt>참가대상</dt><dd>성인</dd></dl>
        <dl class="info"><dt>모집인원</dt><dd>{spec.get('capacity', '0 / 10 명')}</dd></dl>
        <dl class="info"><dt>{venue_key}</dt><dd>{spec['branch']} 강의실</dd></dl>
        <dl class="info"><dt>참가비</dt><dd>무료</dd></dl>
        <dl class="info"><dt>강사</dt><dd>민감강사명</dd></dl>
        <dl class="info"><dt>문의전화</dt><dd>052-000-0000</dd></dl>
        <dl class="info"><dt>이메일</dt><dd>private@example.test</dd></dl>
      </td></tr></tbody>
    </table>{application}</div></div></body></html>
    """


def _default_library_specs() -> dict[str, list[dict[str, Any]]]:
    return {
        "edusat": [
            {
                "identity": 101,
                "title": "[독서] 시민 글쓰기",
                "branch": municipal.LIBRARY_BRANCHES[0],
                "status": "CLOSED",
            }
        ],
        "edusat2": [
            {
                "identity": 201,
                "title": "[문화] 작가와의 만남",
                "branch": municipal.LIBRARY_BRANCHES[1],
                "status": "OPEN",
            }
        ],
    }


def _collect_library(
    specs_by_source: dict[str, list[dict[str, Any]]] | None = None,
    *,
    clamp_mismatch_source: str = "",
    declared_total_delta: int = 0,
    detail_limit: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str], FakeSession]:
    specs_by_source = specs_by_source or _default_library_specs()
    fetched: list[str] = []
    session = FakeSession()
    sources = {source.key: source for source in municipal.LIBRARY_CATALOGUES}

    def fetcher(_session: Any, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        fetched.append(url)
        parsed = urlparse(url)
        source = next(item for item in sources.values() if parsed.path.startswith(item.list_path.rsplit("/", 1)[0] + "/"))
        query = parse_qs(parsed.query)
        if parsed.path == source.detail_path:
            identity = int(query["edu_idx"][0])
            spec = next(item for item in specs_by_source[source.key] if item["identity"] == identity)
            return _soup(_library_detail(source, spec))
        assert parsed.path == source.list_path
        clamp = int((query.get("v_page") or ["1"])[0]) > 1
        specs = specs_by_source[source.key]
        declared = len(specs) + declared_total_delta
        return _soup(
            _library_page(
                source,
                specs,
                clamp=clamp,
                declared_total=declared,
                clamp_title_change=clamp and source.key == clamp_mismatch_source,
            )
        )

    rows, _parser, meta = municipal.collect_ulsan_bukgu_library_courses(
        _library_target(),
        timeout=7,
        max_pages=20,
        detail_limit=detail_limit,
        fetcher=fetcher,
        session_factory=lambda: session,
        today=date(2026, 7, 21),
    )
    return rows, meta, fetched, session


def test_exact_targets_and_rejected_non_owner_aliases() -> None:
    assert municipal.is_ulsan_bukgu_public_target(_public_target())
    assert municipal.is_ulsan_bukgu_library_target(_library_target())
    assert not municipal.is_ulsan_bukgu_public_target(
        {**_public_target(), "url": municipal.ULSAN_BUKGU_PUBLIC_URL + "?mem_id=B0001007"}
    )
    assert not municipal.is_ulsan_bukgu_library_target(
        {**_library_target(), "url": municipal.ULSAN_BUKGU_LIBRARY_EVENT_URL}
    )
    assert municipal.is_ulsan_bukgu_rejected_alias_target(
        {
            "provider": municipal.ULSAN_BUKGU_LIBRARY_PROVIDER,
            "url": municipal.ULSAN_BUKGU_LIBRARY_BAD_CANDIDATE_URL,
        }
    )
    assert municipal.is_ulsan_bukgu_rejected_alias_target(
        {
            "provider": municipal.ULSAN_BUKGU_LIFELONG_ALIAS_PROVIDER,
            "url": municipal.ULSAN_BUKGU_LIFELONG_ALIAS_URL,
        }
    )
    assert municipal.is_ulsan_bukgu_rejected_alias_target(
        {"provider": "foreign", "url": municipal.ULJU_FOREIGN_LECTURE_URL}
    )


def test_public_complete_fixed_fanout_clamps_and_open_detail() -> None:
    rows, meta, fetched, session = _collect_public()

    assert meta["snapshot_complete"] is True
    assert len(rows) == len(municipal.PUBLIC_FACILITIES) == 10
    assert meta["list_requests"] == 20
    assert meta["clamp_verified_count"] == 10
    assert meta["detail_pages"] == len(municipal.PUBLIC_FACILITIES)
    assert meta["branch_count"] == 10
    assert sum("pg=999999" in url for url in fetched) == 10
    open_rows = [row for row in rows if row["status"] == "OPEN"]
    assert len(open_rows) == 1
    assert sum(row["status"] == "CLOSED" for row in rows) == 1
    assert open_rows[0]["application_url"].endswith(
        f"mem_id={municipal.PUBLIC_FACILITIES[0][0]}&prc=rsvinfo&lec_id=L0000001"
    )
    assert open_rows[0]["fee"] == "110,000원"
    assert all(row["target"] == "성인" for row in rows)
    assert all(row["application_type"] for row in rows)
    assert any(
        row["raw_fields"]["detail_schedule_supplement"]
        == "주말/공휴일 07:00 ~ 18:00"
        for row in rows
    )
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    payload = repr(rows)
    for forbidden in ("민감강사명", "052-000-0000", "private@example.test"):
        assert forbidden not in payload
    assert session.closed is True


@pytest.mark.parametrize("failure", ["missing_facility", "clamp", "application"])
def test_public_contract_drift_fails_closed(failure: str) -> None:
    rows, meta, _fetched, _session = _collect_public(
        missing_last_nav=failure == "missing_facility",
        clamp_mismatch=failure == "clamp",
        application_control=failure != "application",
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_library_union_pages_clamps_details_and_no_bad_candidate_provenance() -> None:
    rows, meta, fetched, session = _collect_library()

    assert meta["snapshot_complete"] is True
    assert meta["source_totals"] == {"edusat": 1, "edusat2": 1}
    assert meta["source_rows"] == 2
    assert meta["current_count"] == meta["detail_pages"] == len(rows) == 2
    assert meta["clamp_verified_count"] == 2
    assert meta["list_requests"] == 4
    assert sum("v_page=2" in url for url in fetched) == 2
    assert sum(bool(row["application_url"]) for row in rows) == 1
    payload = repr((rows, meta))
    assert municipal.ULSAN_BUKGU_LIBRARY_BAD_CANDIDATE_ID not in payload
    for forbidden in ("민감강사명", "052-000-0000", "private@example.test"):
        assert forbidden not in payload
    assert session.closed is True


def test_library_semantic_waitlist_duplicate_collapses_to_open_row() -> None:
    branch = municipal.LIBRARY_BRANCHES[0]
    specs = {
        "edusat": [
            {
                "identity": 301,
                "title": "[독서] 동일 강좌 (추가모집)",
                "branch": branch,
                "status": "CLOSED",
            },
            {
                "identity": 302,
                "title": "[독서] 동일 강좌",
                "branch": branch,
                "status": "OPEN",
            },
        ],
        "edusat2": [],
    }
    rows, meta, _fetched, _session = _collect_library(specs)

    assert meta["snapshot_complete"] is True
    assert meta["current_count"] == 2
    assert meta["semantic_duplicate_groups"] == 1
    assert meta["semantic_collapsed_count"] == 1
    assert len(rows) == 1
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["raw_fields"]["edu_idx"] == "302"


@pytest.mark.parametrize("failure", ["declared_total", "clamp"])
def test_library_total_or_clamp_drift_fails_closed(failure: str) -> None:
    rows, meta, _fetched, _session = _collect_library(
        declared_total_delta=1 if failure == "declared_total" else 0,
        clamp_mismatch_source="edusat" if failure == "clamp" else "",
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_transport_injection_and_caps_are_fail_closed() -> None:
    rows, _parser, meta = municipal.collect_ulsan_bukgu_courses(_public_target())
    assert rows == []
    assert "managed fetcher" in meta["configured_collection_error"]

    rows, meta, _fetched, _session = _collect_public(detail_limit=0)
    assert rows == []
    assert meta["source_cap_reached"] is True


def test_ubimc_intermediate_adapter_keeps_certificate_verification_enabled() -> None:
    mounted: dict[str, Any] = {}

    class MountableSession:
        def mount(self, prefix: str, adapter: Any) -> None:
            mounted[prefix] = adapter

    current = MountableSession()
    assert municipal.configure_ulsan_bukgu_verified_session(current) is current
    adapter = mounted[f"https://{municipal.ULSAN_BUKGU_PUBLIC_HOST}/"]
    context = adapter.context()

    assert municipal.ULSAN_BUKGU_INTERMEDIATE_CERT.is_file()
    assert municipal.ULSAN_BUKGU_INTERMEDIATE_CERT.suffix == ".crt"
    certificate_bytes = municipal.ULSAN_BUKGU_INTERMEDIATE_CERT.read_bytes()
    encoded = b"".join(
        line
        for line in certificate_bytes.splitlines()
        if line and not line.startswith(b"-----")
    )
    assert (
        hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest()
        == "8c54c334b66ba4e426772af4a3f9136c19a1aec729fdb28c535c07a5a4ef22e0"
    )
    assert b"-----BEGIN CERTIFICATE-----" in certificate_bytes
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED

    rows, _parser, meta = municipal.collect_ulsan_bukgu_library_courses(
        _library_target(),
        max_pages=3,
        fetcher=lambda *_args: _soup("<html></html>"),
        session_factory=FakeSession,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
