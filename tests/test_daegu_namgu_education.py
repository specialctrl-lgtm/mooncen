from __future__ import annotations

from collections import Counter
from html import escape
import json
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_daegu_namgu as namgu


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200) -> None:
        self.url = url
        self.text = html
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.history: list[Any] = []


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(**updates: str) -> dict[str, str]:
    result = {
        "provider": namgu.DAEGU_NAMGU_PROVIDER,
        "candidate_id": namgu.DAEGU_NAMGU_CANDIDATE_ID,
        "url": namgu.DAEGU_NAMGU_URL,
    }
    result.update(updates)
    return result


def _pager(page: int | None) -> str:
    if page is None:
        return '<div class="board_paginate"><a href="?v_page=1">1</a></div>'
    return f'<div class="board_paginate"><strong>{page}</strong></div>'


def _lifelong_list_row(title: str = "[일반교육과정] 합성 평생 강좌") -> str:
    detail = namgu.daegu_namgu_detail_url("lifelong", "10")
    application = "https://nam.daegu.kr/lll/edusat/regist.do?edu_idx=10"
    return f"""
      <tr>
        <td><a href="{escape(detail, quote=True)}">{escape(title)}</a></td>
        <td><ul class="tlist">
          <li>신청: 99.07.01 ~ 99.07.31</li>
          <li>교육: 99.08.01 ~ 99.08.31</li>
        </ul></td>
        <td>무료</td>
        <td><span class="acc">인터넷접수</span></td>
        <td>0 / 10명</td><td>-</td>
        <td><span class="state">신청</span>
          <a href="{escape(application, quote=True)}">신청</a></td>
      </tr>
    """


def _lifelong_page(page: int, *, drift: bool = False) -> str:
    headers = "".join(
        f"<th>{value}</th>"
        for value in ("강좌명", "기간", "수강료", "접수방법", "신청/모집", "신청현황", "상태")
    )
    if page == 1:
        title = "[일반교육과정] 경계가 바뀐 강좌" if drift else "[일반교육과정] 합성 평생 강좌"
        body = _lifelong_list_row(title)
        pager = _pager(1)
    else:
        body = '<tr><td colspan="7">등록된 강좌가 없습니다.</td></tr>'
        pager = _pager(None)
    return f"""
      <html><head><title>강좌신청 | 대구남구 평생학습관</title></head><body>
        <form name="frm_edu" method="get" action="list.do"></form>
        <table class="edu_list_table"><thead><tr>{headers}</tr></thead>
          <tbody>{body}</tbody></table>{pager}
      </body></html>
    """


def _lifelong_detail() -> str:
    fields = (
        ("교육기간", "2099년 08월 01일(토) ~ 2099년 08월 31일(월)"),
        ("신청기간", "2099년 07월 01일(수) ~ 2099년 07월 31일(금)"),
        ("강 사 명", "SECRET_LIFELONG_INSTRUCTOR 010-1111-2222"),
        ("수 강 료", "무료"),
        ("교육방법", "대면"),
        ("교육대상", "남구 주민"),
        ("교육주기", "매주 토요일 10:00~12:00"),
        ("교육정원", "0 / 10명"),
        ("문의전화", "053-111-2222"),
        ("접수방법", "인터넷접수"),
        ("지 역", "대구 남구"),
        ("교육장소", "남구 평생학습관 1강의실"),
        ("URL", "private@example.test"),
        ("상세내용", "SECRET_LIFELONG_FREE_TEXT"),
    )
    rows = "".join(
        f"<tr><th>{label}</th><td>{escape(value)}</td></tr>" for label, value in fields
    )
    application = "https://nam.daegu.kr/lll/edusat/regist.do?edu_idx=10"
    return f"""
      <html><head><title>강좌신청 | 대구남구 평생학습관</title></head><body>
        <table class="edu_view_table">
          <thead><tr><th>강좌명</th><td>[일반교육과정] 합성 평생 강좌</td></tr></thead>
          <tbody>{rows}</tbody>
        </table>
        <a href="{escape(application, quote=True)}">신청접수하기</a>
        <div class="applicants">SECRET_LIFELONG_APPLICANT</div>
      </body></html>
    """


def _imom_card(
    identity: str,
    title: str,
    *,
    status: str,
    current: int = 0,
) -> str:
    detail = namgu.daegu_namgu_detail_url("imom", identity)
    fields = (
        ("신청기간", "2099-07-01 ~ 2099-07-31"),
        ("운영기간", "2099-08-01 ~ 2099-08-31"),
        ("수강대상", "남구 주민 가족"),
        ("모집인원", f"{current} / 10명"),
        ("신청유형", "홈페이지접수"),
    )
    definitions = "".join(
        f"<dl><dt>{label}</dt><dd>{escape(value)}</dd></dl>" for label, value in fields
    )
    return f"""
      <li><a href="{escape(detail, quote=True)}">
        <span class="subject">{escape(title)}</span>
        <span class="label_area"><span class="state">{status}</span></span>
        <div class="info_group">{definitions}</div>
      </a></li>
    """


def _imom_page(*, sentinel: bool = False, bad_clamp: bool = False) -> str:
    first_title = "클램프가 바뀐 강좌" if bad_clamp else "합성 아이맘 강좌"
    cards = _imom_card("20", first_title, status="접수중")
    cards += _imom_card("1622", "테스트 게시판", status="기간종료")
    return f"""
      <html><head><title>교육신청 | 온마을아이맘센터</title></head><body>
        <div class="list_filter"><span class="total">총 <strong class="eng">2</strong>건</span></div>
        <ul class="list_card">{cards}</ul>{_pager(None if sentinel else 1)}
      </body></html>
    """


def _imom_detail(
    identity: str,
    *,
    wrong_title: bool = False,
    wrong_application_identity: bool = False,
) -> str:
    production = identity == "20"
    title = "다른 아이맘 강좌" if wrong_title else (
        "합성 아이맘 강좌" if production else "테스트 게시판"
    )
    status = "접수중" if production else "기간종료"
    fields = (
        ("운영기간", "2099-08-01 ~ 2099-08-31"),
        ("운영시간", "매주 목요일 14:00~16:00"),
        ("신청기간", "2099-07-01 ~ 2099-07-31"),
        ("신청방법", "인터넷 신청"),
        ("수강대상", "남구 주민 가족"),
        ("모집인원", "0 / 10명"),
        ("장소", "2층 프로그램실"),
        ("참가비", "0원"),
        ("강사", "SECRET_IMOM_INSTRUCTOR 010-2222-3333"),
    )
    definitions = "".join(
        f"<dl><dt>{label}</dt><dd>{escape(value)}</dd></dl>" for label, value in fields
    )
    control = ""
    if production:
        action_identity = "21" if wrong_application_identity else identity
        action = (
            f"{namgu.DAEGU_NAMGU_IMOM_URL}?proc_type=regist&amp;"
            f"edu_idx={action_identity}"
        )
        control = f'<div class="btn_w"><a href="{action}">신청하기</a></div>'
    return f"""
      <html><head><title>교육신청 | 온마을아이맘센터</title></head><body>
        <div class="edu_view">
          <div class="tit">{escape(title)}
            <span class="label_area"><span class="state">{status}</span></span>
          </div>
          <div class="info"><div class="txtw">{definitions}</div></div>
          {control}
          <div class="contents">SECRET_IMOM_FREE_TEXT private@example.test</div>
        </div>
      </body></html>
    """


def _culture_card(
    title: str = "합성 문화예술 강좌",
    *,
    is_open: bool = False,
    application_identity: str = "30",
) -> str:
    detail = namgu.daegu_namgu_detail_url("culture", "30")
    if is_open:
        application = (
            f"{namgu.DAEGU_NAMGU_CULTURE_URL}"
            f"?proc_type=regist&edu_idx={application_identity}"
            "&prepage=%2Fculturalcenter%2Fmain%2Fsite%2FedusatRequest%2Fedusat.do"
            "%3Fv_page%3D1"
        )
        status_control = (
            f'<a class="btn ing" href="{escape(application, quote=True)}">'
            "수강신청</a>"
        )
    else:
        status_control = (
            '<a class="btn ready" href="#javascript:;">신청준비</a>'
        )
    fields = (
        ("신청기간", "2099-07-01 ~ 2099-07-31"),
        ("강의기간", "2099-08-01 ~ 2099-08-31"),
        ("강사명", "SECRET_CULTURE_LIST_INSTRUCTOR"),
        ("수강료", "6회 0원"),
        ("모집인원", "0 / 15명"),
    )
    definitions = "".join(
        f"<dl><dt>{label}</dt><dd>{escape(value)}</dd></dl>" for label, value in fields
    )
    return f"""
      <li><div class="cont"><div class="tit">
        <a href="{escape(detail, quote=True)}">{escape(title)}</a>
      </div><div class="info">{definitions}</div></div>
      <div class="btn_box"><a class="check" href="{escape(detail, quote=True)}">상세</a>
        {status_control}</div></li>
    """


def _culture_page(
    *,
    sentinel: bool = False,
    bad_clamp: bool = False,
    is_open: bool = False,
    application_identity: str = "30",
) -> str:
    title = "클램프가 바뀐 문화 강좌" if bad_clamp else "합성 문화예술 강좌"
    return f"""
      <html><head><title>문화강좌 | 대덕문화전당</title></head><body>
        <div class="count">총 <strong class="eng">1</strong>건</div>
        <div class="edu_list"><ul>{_culture_card(
            title,
            is_open=is_open,
            application_identity=application_identity,
        )}</ul></div>
        {_pager(None if sentinel else 1)}
      </body></html>
    """


def _culture_detail(
    *,
    is_open: bool = False,
    application_identity: str = "30",
) -> str:
    method = "방문,전화,인터넷접수" if is_open else "방문 신청"
    status = "신청중" if is_open else "신청준비"
    application_control = ""
    if is_open:
        application = (
            f"{namgu.DAEGU_NAMGU_CULTURE_URL}"
            f"?proc_type=regist&edu_idx={application_identity}"
            "&prepage=%2Fculturalcenter%2Fmain%2Fsite%2FedusatRequest%2Fedusat.do"
            "%3Fproc_type%3Dview%26edu_idx%3D30"
        )
        application_control = f"""
          <div class="board_button"><div class="btn_right">
            <a class="btn point" href="{escape(application, quote=True)}">신청</a>
          </div></div>
        """
    fields = (
        ("신청기간", "2099-07-01 ~ 2099-07-31"),
        ("신청방법", method),
        ("모집인원", "0 / 15명"),
        ("강의장소", "대덕문화전당 제1강의실"),
        ("수강료", "6회 0원"),
        ("문의전화", "053-333-4444"),
    )
    definitions = "".join(
        f"<dl><dt>{label}</dt><dd>{escape(value)}</dd></dl>" for label, value in fields
    )
    return f"""
      <html><head><title>문화강좌 | 대덕문화전당</title></head><body>
        <div class="edu_view_board"><div>
          <h4 class="tit">합성 문화예술 강좌<a class="btn2">{status}</a></h4>
        </div>
        <div class="instructor_time"><span class="time">2099-08-01 ~ 2099-08-31</span></div>
        <div class="title_view_box">{definitions}</div>
        <div class="contents">SECRET_CULTURE_FREE_TEXT private@example.test</div>
        </div>
        {application_control}
      </body></html>
    """


class _FixtureSite:
    def __init__(
        self,
        *,
        drift: bool = False,
        bad_clamp: str = "",
        wrong_detail: bool = False,
        wrong_application_identity: bool = False,
        culture_open: bool = False,
        wrong_culture_list_application_identity: bool = False,
        wrong_culture_detail_application_identity: bool = False,
        retry_once: bool = False,
    ) -> None:
        self.drift = drift
        self.bad_clamp = bad_clamp
        self.wrong_detail = wrong_detail
        self.wrong_application_identity = wrong_application_identity
        self.culture_open = culture_open
        self.wrong_culture_list_application_identity = (
            wrong_culture_list_application_identity
        )
        self.wrong_culture_detail_application_identity = (
            wrong_culture_detail_application_identity
        )
        self.retry_once = retry_once
        self.calls: Counter[tuple[str, str, str]] = Counter()
        self.urls: list[str] = []
        self.sessions: list[_Session] = []
        self.lock = Lock()

    def session_factory(self) -> _Session:
        session = _Session()
        with self.lock:
            self.sessions.append(session)
        return session

    def fetcher(self, _session: _Session, url: str, _timeout: int) -> _Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        page = query.get("v_page", ["1"])[0]
        proc = query.get("proc_type", [""])[0]
        identity = query.get("edu_idx", [""])[0]
        key = (parsed.path, page, proc or identity)
        with self.lock:
            self.calls[key] += 1
            call = self.calls[key]
            self.urls.append(url)

        if parsed.path == "/lll/edusat/list.do":
            return _Response(
                url,
                _lifelong_page(int(page), drift=self.drift and int(page) == 1 and call > 1),
            )
        if parsed.path == "/lll/edusat/view.do":
            return _Response(url, _lifelong_detail())
        if parsed.path == urlparse(namgu.DAEGU_NAMGU_IMOM_URL).path:
            if proc == "view":
                if self.retry_once and identity == "20" and call == 1:
                    return _Response(url, "temporary", status_code=503)
                return _Response(
                    url,
                    _imom_detail(
                        identity,
                        wrong_title=self.wrong_detail and identity == "20",
                        wrong_application_identity=(
                            self.wrong_application_identity and identity == "20"
                        ),
                    ),
                )
            sentinel = int(page) == 2
            return _Response(
                url,
                _imom_page(
                    sentinel=sentinel,
                    bad_clamp=self.bad_clamp == "imom" and sentinel,
                ),
            )
        if parsed.path == urlparse(namgu.DAEGU_NAMGU_CULTURE_URL).path:
            if proc == "view":
                return _Response(
                    url,
                    _culture_detail(
                        is_open=self.culture_open,
                        application_identity=(
                            "31"
                            if self.wrong_culture_detail_application_identity
                            else "30"
                        ),
                    ),
                )
            sentinel = int(page) == 2
            return _Response(
                url,
                _culture_page(
                    sentinel=sentinel,
                    bad_clamp=self.bad_clamp == "culture" and sentinel,
                    is_open=self.culture_open,
                    application_identity=(
                        "31"
                        if self.wrong_culture_list_application_identity
                        else "30"
                    ),
                ),
            )
        raise AssertionError(f"unexpected URL: {url}")


def _collect(site: _FixtureSite, **kwargs: Any):
    options = {
        "timeout": 5,
        "max_pages": 5,
        "detail_limit": 10,
        "today": "2099-01-01",
        "max_requests": 50,
        "max_workers": 4,
        "fetch_attempts": 2,
        "fetcher": site.fetcher,
        "session_factory": site.session_factory,
        "sleeper": lambda _seconds: None,
    }
    options.update(kwargs)
    return namgu.collect_daegu_namgu_education(
        _target(),
        **options,
    )


def test_target_urls_and_candidate_audit_are_exact() -> None:
    assert namgu.is_daegu_namgu_education_target(_target())
    assert not namgu.is_daegu_namgu_education_target(
        _target(provider="MUNI_WRONG")
    )
    assert not namgu.is_daegu_namgu_education_target(
        _target(url="https://nam.daegu.kr/")
    )
    assert namgu.daegu_namgu_list_url("lifelong", 2).startswith(
        "https://nam.daegu.kr/lll/edusat/list.do?v_page=2&"
    )
    assert namgu.daegu_namgu_detail_url("imom", 20) == (
        f"{namgu.DAEGU_NAMGU_IMOM_URL}?proc_type=view&edu_idx=20"
    )
    assert namgu.DAEGU_NAMGU_CANDIDATE_AUDIT[namgu.DAEGU_NAMGU_CANDIDATE_ID][
        "decision"
    ] == "canonical_complete_owner_with_fixed_official_fanout"
    assert {item.branch_code for item in namgu.DAEGU_NAMGU_LEDGERS} == {
        "DAEGU_NAMGU_LIFELONG",
        "DAEGU_NAMGU_IMOM",
        "DAEGU_NAMGU_DAEDEOK",
    }


def test_complete_atomic_snapshot_and_public_allowlist() -> None:
    site = _FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == namgu.DAEGU_NAMGU_PARSER
    assert len(rows) == 3
    assert meta["source_rows"] == 4
    assert meta["source_publishable_rows"] == 3
    assert meta["source_rows_by_ledger"] == {"lifelong": 1, "imom": 2, "culture": 1}
    assert meta["data_pages_by_ledger"] == {"lifelong": 1, "imom": 1, "culture": 1}
    assert meta["sentinel_kinds"] == {
        "lifelong": "empty",
        "imom": "exact_final_page_clamp",
        "culture": "exact_final_page_clamp",
    }
    assert meta["stability_rechecks"] == 6
    assert meta["list_requests"] == 12
    assert meta["detail_pages"] == 4
    assert meta["network_requests"] == 16
    assert meta["returned_count"] == 3
    assert meta["suppressed_nonproduction_rows"] == 1
    assert meta["status_counts"] == {"OPEN": 2, "SCHEDULED": 1}
    assert meta["reservation_available_count"] == 2
    assert meta["snapshot_complete"] is True
    assert {row["branch_code"] for row in rows} == {
        "DAEGU_NAMGU_LIFELONG",
        "DAEGU_NAMGU_IMOM",
        "DAEGU_NAMGU_DAEDEOK",
    }
    assert all(row["municipality_code"] == "2720000000" for row in rows)
    assert all(row["provider"] == namgu.DAEGU_NAMGU_PROVIDER for row in rows)
    assert all(row["raw_fields"]["pii_fields_read"] == [] for row in rows)
    culture = next(
        row for row in rows if row["branch_code"] == "DAEGU_NAMGU_DAEDEOK"
    )
    assert culture["target"] == "대상 별도 안내"
    serialized = json.dumps(rows, ensure_ascii=False)
    for forbidden in (
        "SECRET_",
        "private@example.test",
        "010-1111-2222",
        "010-2222-3333",
        "053-111-2222",
        "053-333-4444",
        "테스트 게시판",
    ):
        assert forbidden not in serialized
    assert not any("user.do" in value for value in site.urls)
    assert not any("regist.do" in value for value in site.urls)
    assert all(session.closed for session in site.sessions)


def test_open_culture_application_is_bound_across_list_and_detail() -> None:
    rows, _, meta = _collect(_FixtureSite(culture_open=True))

    culture = next(
        row for row in rows if row["branch_code"] == "DAEGU_NAMGU_DAEDEOK"
    )
    assert culture["status"] == "OPEN"
    assert culture["reservation_available"] is True
    assert culture["application_url"] == (
        f"{namgu.DAEGU_NAMGU_CULTURE_URL}?proc_type=regist&edu_idx=30"
    )
    assert meta["status_counts"] == {"OPEN": 3}
    assert meta["reservation_available_count"] == 3
    assert meta["snapshot_complete"] is True


@pytest.mark.parametrize("ledger", ["imom", "culture"])
def test_exact_clamp_sentinel_drift_fails_atomically(ledger: str) -> None:
    rows, _, meta = _collect(_FixtureSite(bad_clamp=ledger))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "clamp sentinel changed" in meta["configured_collection_error"]


def test_boundary_recheck_drift_fails_atomically() -> None:
    rows, _, meta = _collect(_FixtureSite(drift=True))
    assert rows == []
    assert "changed during stable recheck" in meta["configured_collection_error"]
    assert meta["stability_rechecks"] >= 1


@pytest.mark.parametrize(
    ("site", "message"),
    [
        (_FixtureSite(wrong_detail=True), "detail title/status mismatch"),
        (_FixtureSite(wrong_application_identity=True), "application identity mismatch"),
        (
            _FixtureSite(
                culture_open=True,
                wrong_culture_list_application_identity=True,
            ),
            "culture list application identity mismatch",
        ),
        (
            _FixtureSite(
                culture_open=True,
                wrong_culture_detail_application_identity=True,
            ),
            "culture application identity mismatch",
        ),
    ],
)
def test_detail_identity_contracts_fail_atomically(
    site: _FixtureSite, message: str
) -> None:
    rows, _, meta = _collect(site)
    assert rows == []
    assert message in meta["configured_collection_error"]


def test_caps_and_downstream_dedupe_fail_closed() -> None:
    rows, _, meta = _collect(_FixtureSite(), detail_limit=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit 3" in meta["configured_collection_error"]

    rows, _, meta = _collect(_FixtureSite(), max_requests=15)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_requests cap 15 exhausted" in meta["configured_collection_error"]

    rows, _, meta = _collect(
        _FixtureSite(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "downstream dedupe changed owned snapshot" in meta["configured_collection_error"]


def test_transient_detail_retry_preserves_complete_snapshot() -> None:
    rows, _, meta = _collect(_FixtureSite(retry_once=True))
    assert len(rows) == 3
    assert meta["retry_count"] == 1
    assert meta["network_requests"] == 17
    assert meta["snapshot_complete"] is True


def test_invalid_target_and_limits_do_not_issue_requests() -> None:
    site = _FixtureSite()
    rows, _, meta = namgu.collect_daegu_namgu_education(
        _target(provider="MUNI_WRONG"),
        fetcher=site.fetcher,
        session_factory=site.session_factory,
    )
    assert rows == []
    assert site.urls == []
    assert "target does not match" in meta["configured_collection_error"]

    rows, _, meta = namgu.collect_daegu_namgu_education(
        _target(),
        max_pages=0,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
    )
    assert rows == []
    assert site.urls == []
    assert "invalid collection limits" in meta["configured_collection_error"]

    rows, _, meta = namgu.collect_daegu_namgu_education(
        _target(),
        today="not-a-date",
        fetcher=site.fetcher,
        session_factory=site.session_factory,
    )
    assert rows == []
    assert site.urls == []
    assert site.sessions == []
    assert meta["snapshot_complete"] is False
    assert "Invalid isoformat string" in meta["configured_collection_error"]


def test_only_exact_historical_source_anomalies_are_tolerated() -> None:
    assert namgu._short_range(
        "24.02.01 ~ 24.02.31", identity="337", application=True
    ) == (None, None, True)
    corrected_start, corrected_end, corrected = namgu._short_range(
        "24.01.11 ~ 23.03.28", identity="322", application=False
    )
    assert (corrected_start.isoformat(), corrected_end.isoformat(), corrected) == (
        "2024-01-11",
        "2024-03-28",
        True,
    )
    with pytest.raises(namgu.DaeguNamguContractError):
        namgu._short_range(
            "99.02.01 ~ 99.02.31", identity="337", application=True
        )
    with pytest.raises(namgu.DaeguNamguContractError):
        namgu._short_range(
            "24.01.11 ~ 23.03.28", identity="999", application=False
        )


@pytest.mark.skipif(
    os.environ.get("RUN_DAEGU_NAMGU_LIVE") != "1",
    reason="set RUN_DAEGU_NAMGU_LIVE=1 for the exact official-ledger contract",
)
def test_live_exact_official_snapshot_contract() -> None:
    rows, parser, meta = namgu.collect_daegu_namgu_education(
        _target(),
        timeout=35,
        max_pages=50,
        detail_limit=150,
        today="2026-07-22",
        max_requests=240,
        max_workers=namgu.DAEGU_NAMGU_MAX_WORKERS,
        fetch_attempts=namgu.DAEGU_NAMGU_FETCH_ATTEMPTS,
    )

    assert parser == namgu.DAEGU_NAMGU_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == 543
    assert meta["source_rows_by_ledger"] == {
        "lifelong": 484,
        "imom": 30,
        "culture": 29,
    }
    assert meta["data_pages_by_ledger"] == {
        "lifelong": 25,
        "imom": 3,
        "culture": 2,
    }
    assert meta["list_requests"] == 39
    assert meta["detail_pages"] == 82
    assert meta["network_requests"] == 121 + meta["retry_count"]
    assert meta["historical_invalid_application_dates"] == 1
    assert meta["historical_invalid_education_dates"] == 1
    assert meta["suppressed_nonproduction_rows"] == 1
    assert meta["duplicate_source_rows"] == 0
    assert meta["semantic_duplicate_rows"] == 0
    assert meta["status_counts"] == {"CLOSED": 75, "OPEN": 6}
    assert meta["reservation_available_count"] == 6
    assert len(rows) == meta["returned_count"] == 81
    assert meta["snapshot_complete"] is True
