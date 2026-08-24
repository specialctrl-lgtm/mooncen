from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import ssl
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from backend.ops import region_collection as ops
from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_seongnam_museum_experience as collector


PROGRAMS = (
    {
        "identity": "276",
        "title": "[가족] 성남시를 부탁해! 로보틱스",
        "status": "접수예정",
        "event": "2099-08-29 ~ 2099-11-21",
        "apply": "2099-08-20 ~ 2099-11-19",
        "target": "초등학생 가족",
        "application": "https://sugang.seongnam.go.kr/ilms/learning/learningList.do",
    },
    {
        "identity": "275",
        "title": "[단체][로봇코딩게임]우리 곁에 로봇",
        "status": "접수중",
        "event": "2099-08-06 ~ 2099-08-13",
        "apply": "2099-07-15 ~ 2099-08-07",
        "target": "청소년 단체",
        "application": "https://sugang.seongnam.go.kr/ilms/learning/learningList.do",
    },
    {
        "identity": "268",
        "title": "[청소년단체][3D펜]도전! 뮷즈 디자이너",
        "status": "접수중",
        "event": "2099-05-14 ~ 2099-12-03",
        "apply": "2099-04-01 ~ 2099-11-30",
        "target": "청소년 단체",
        "application": "https://museum.seongnam.go.kr/",
    },
)


@dataclass
class _Response:
    url: str
    content: bytes
    status_code: int = 200

    @property
    def history(self) -> tuple[Any, ...]:
        return ()


def _target() -> dict[str, str]:
    return {
        "provider": collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER,
        "url": collector.SEONGNAM_MUSEUM_EXPERIENCE_URL,
    }


def _card(program: dict[str, str]) -> str:
    return f"""
    <li class="prg_list">
      <a onclick="fn_goView('{program["identity"]}');">
        <span class="flag_kind">{program["status"]}</span>
        <div class="top_box">
          <p class="tit">{program["title"]}</p>
          <p class="s_tit">성남시박물관 교육실</p>
        </div>
        <span class="flag_kind">{program["status"]}</span>
      </a>
      <ul class="middle_list">
        <li><span class="lt_tit">대상</span><span class="lt_txt">{program["target"]}</span></li>
        <li><span class="lt_tit">교육기간</span><span class="lt_txt">{program["event"]}</span></li>
        <li><span class="lt_tit">신청기간</span><span class="lt_txt">{program["apply"]}</span></li>
      </ul>
      <div class="btn_wrap">
        <button onclick="goUrl('{program["application"]}');">신청</button>
      </div>
    </li>
    """


def _list_html(
    page: int,
    *,
    polluted_sentinel: bool = False,
    changed_first_title: bool = False,
) -> bytes:
    selected = list(PROGRAMS) if page == 1 else []
    if page == 2 and polluted_sentinel:
        selected = [PROGRAMS[0]]
    if changed_first_title and selected:
        selected[0] = {**selected[0], "title": selected[0]["title"] + " 변경"}
    body = "".join(_card(program) for program in selected)
    if not selected:
        body = "<li>준비중입니다.</li>"
    return f"""
    <!doctype html><html><head><meta charset="utf-8"><title>성남시 박물관</title></head>
    <body>
      <div class="total_box"><p class="total">전체목록 : <span>3</span> 건</p></div>
      <div class="program_board-wrap">
        <span class="total_num">1</span>
        <ul class="program_list clear">{body}</ul>
      </div>
    </body></html>
    """.encode()


def _detail_html(
    identity: str,
    *,
    title_mismatch: bool = False,
    missing_field: bool = False,
    bad_application: bool = False,
) -> bytes:
    program = next(item for item in PROGRAMS if item["identity"] == identity)
    title = program["title"] + (" 불일치" if title_mismatch else "")
    fields = {
        "신청기간": program["apply"],
        "교육기간": program["event"],
        "교육시간": "토요일 10:00 ~ 12:00",
        "교육인원": "20명",
        "교육대상": program["target"],
        "교육비": "무료",
        "교육장소": "성남시박물관 교육실",
        "문의": "성남시박물관",
    }
    if missing_field:
        fields.pop("교육시간")
    field_html = "".join(
        f"<li><p class='tit'>{label}</p><p class='txt'>{value}</p></li>" for label, value in fields.items()
    )
    application = "https://museum.seongnam.go.kr/shm/login.do" if bad_application else program["application"]
    return f"""
    <!doctype html><html><head><meta charset="utf-8"><title>성남시 박물관</title></head>
    <body><div class="academic_event-wrap"><div class="notice_img-view">
      <div class="img_txt-wrap">
        <p class="img_tit">{title}</p><span class="flag_kind">{program["status"]}</span>
      </div>
      <div class="txt_box"><div class="box_inner"><ul>{field_html}</ul></div></div>
      <div class="btn_wrap type02">
        <a href="{application}">교육프로그램 신청하기</a>
      </div>
    </div></div></body></html>
    """.encode()


class _FixtureSession:
    def __init__(
        self,
        *,
        polluted_sentinel: bool = False,
        unstable_first: bool = False,
        title_mismatch: bool = False,
        missing_field: bool = False,
        bad_application: bool = False,
    ) -> None:
        self.polluted_sentinel = polluted_sentinel
        self.unstable_first = unstable_first
        self.title_mismatch = title_mismatch
        self.missing_field = missing_field
        self.bad_application = bad_application
        self.calls: list[str] = []
        self.page_one_calls = 0
        self.closed = False

    def get(self, url: str, *, timeout: int, allow_redirects: bool) -> _Response:
        assert timeout == 3
        assert allow_redirects is False
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if set(query) == {"page"}:
            page = int(query["page"][0])
            if page == 1:
                self.page_one_calls += 1
            return _Response(
                url,
                _list_html(
                    page,
                    polluted_sentinel=self.polluted_sentinel,
                    changed_first_title=(self.unstable_first and page == 1 and self.page_one_calls > 1),
                ),
            )
        return _Response(
            url,
            _detail_html(
                query["id"][0],
                title_mismatch=self.title_mismatch,
                missing_field=self.missing_field,
                bad_application=self.bad_application,
            ),
        )

    def close(self) -> None:
        self.closed = True


def _collect(
    session: _FixtureSession | None = None,
    *,
    detail_limit: int = 30,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], _FixtureSession]:
    current = session or _FixtureSession()
    rows, parser, meta = collector.collect_seongnam_museum_experience(
        _target(),
        timeout=3,
        max_pages=10,
        detail_limit=detail_limit,
        today="2099-08-05",
        session_factory=lambda: current,
    )
    return rows, parser, meta, current


def test_exact_target_and_get_allowlist() -> None:
    assert collector.is_seongnam_museum_experience_target(_target())
    assert not collector.is_seongnam_museum_experience_target(
        {**_target(), "url": collector.SEONGNAM_MUSEUM_EXPERIENCE_URL + "?page=1"}
    )
    assert collector._request_kind(collector.seongnam_museum_experience_list_url(2)) == "list"
    assert collector._request_kind(collector.seongnam_museum_experience_detail_url("276")) == "detail"
    unsafe = (
        "https://museum.seongnam.go.kr/shm/contents/shm-reservationInfo.do",
        collector.SEONGNAM_MUSEUM_EXPERIENCE_URL + "?application=276",
        "https://museum.seongnam.go.kr/shm/login.do",
        collector.SEONGNAM_MUSEUM_EXPERIENCE_URL + "?schM=view&id=276&download=1",
        collector.SEONGNAM_MUSEUM_EXPERIENCE_URL + "?attachment=1",
    )
    for url in unsafe:
        with pytest.raises(collector.SeongnamMuseumExperienceContractError):
            collector._request_kind(url)


def test_complete_fixture_is_locked_private_safe_and_identity_scoped() -> None:
    rows, parser, meta, session = _collect()

    assert parser == collector.SEONGNAM_MUSEUM_EXPERIENCE_PARSER
    assert len(rows) == 3
    assert meta["source_total"] == meta["current_count"] == meta["returned_count"] == 3
    assert meta["list_requests"] == 4 and meta["detail_requests"] == 3
    assert meta["sentinel_page"] == 2 and meta["sentinel_count"] == 0
    assert meta["status_counts"] == {"SCHEDULED": 1, "OPEN": 2}
    assert meta["snapshot_complete"] is meta["details_complete"] is True
    assert session.closed is True
    identities = [row["provider_course_id"] for row in rows]
    expected_prefix = collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER + ":experience:"
    assert len(set(identities)) == 3
    assert all(identity.startswith(expected_prefix) for identity in identities)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "4113100000" for row in rows)
    assert all(bool(row["application_url"]) == row["reservation_available"] for row in rows)
    assert rows[0]["reservation_available"] is False
    assert rows[1]["reservation_available"] is True
    assert rows[2]["reservation_available"] is False
    assert all("문의" not in row and "phone" not in row for row in rows)
    assert all(collector._request_kind(url) in {"list", "detail"} for url in session.calls)
    for key in (
        "application_endpoint_requests",
        "reservation_endpoint_requests",
        "login_endpoint_requests",
        "auth_endpoint_requests",
        "identity_endpoint_requests",
        "applicant_endpoint_requests",
        "member_endpoint_requests",
        "attachment_endpoint_requests",
        "download_endpoint_requests",
        "pii_endpoint_requests",
    ):
        assert meta[key] == 0
    assert meta["pii_payload_persisted"] is False


@pytest.mark.parametrize(
    ("session", "error"),
    (
        (_FixtureSession(polluted_sentinel=True), "post-last page"),
        (_FixtureSession(unstable_first=True), "first list page changed"),
        (_FixtureSession(title_mismatch=True), "detail identity mismatch"),
        (_FixtureSession(missing_field=True), "detail field vocabulary changed"),
        (_FixtureSession(bad_application=True), "application control escaped"),
    ),
)
def test_contract_drift_returns_atomic_empty_snapshot(
    session: _FixtureSession,
    error: str,
) -> None:
    rows, _parser, meta, current = _collect(session)
    assert rows == []
    assert error in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False
    assert current.closed is True


def test_detail_limit_is_atomic() -> None:
    rows, _parser, meta, session = _collect(detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit truncates" in meta["configured_collection_error"]
    assert session.closed is True


def test_verified_legacy_tls_keeps_certificate_and_hostname_checks() -> None:
    context = collector._VerifiedLegacyTLSAdapter.context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_router_dispatches_exact_target(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(target: Any, **kwargs: Any):
        captured["target"] = target
        captured.update(kwargs)
        return ([{"provider": collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER}], "fixture", {})

    monkeypatch.setattr(collector, "collect_seongnam_museum_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER,
        name="성남시박물관 체험교육",
        branch="성남시박물관",
        url=collector.SEONGNAM_MUSEUM_EXPERIENCE_URL,
        source="test",
        priority=1,
        region="경기도 성남시 수정구",
        extra={},
    )
    rows, parser, meta = router.collect_from_url(
        target,
        timeout=3,
        max_depth=0,
        max_pages=10,
        detail_limit=30,
    )
    assert rows and parser == "fixture" and meta == {}
    assert captured["target"] is target
    assert captured["max_pages"] == 10 and captured["detail_limit"] == 30
    assert callable(captured["session_factory"])


def test_yaml_operational_and_ops_scope_cover_parent_and_sujeong_only() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = yaml.safe_load(
        (root / "config/crawl_targets/municipal_integrated_reservation.yaml").read_text(encoding="utf-8")
    )["targets"]
    matches = [row for row in targets if row.get("provider") == collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER]
    assert len(matches) == 1
    target = matches[0]
    assert target["url"] == collector.SEONGNAM_MUSEUM_EXPERIENCE_URL
    assert target["crawler_status"] == "ready"
    assert target["ops_scopes"] == ["experience"]
    assert target["service_group"] == "체험"
    assert {row["code"] for row in target["covered_municipalities"]} == {
        "4113000000",
        "4113100000",
    }

    operational = yaml.safe_load(
        (root / "config/municipal_integrated_reservation_operational.yaml").read_text(encoding="utf-8")
    )["entries"]
    assert any(
        row.get("provider") == collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER
        and row.get("validation_outcome") == "collected"
        for row in operational
    )

    reference = ops._region_reference()
    experience = reference.configured_by_scope["experience"]
    education = reference.configured_by_scope["education"]
    for name in ("경기도 성남시", "경기도 성남시 수정구"):
        assert collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER in experience[name]
        assert collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER not in education.get(name, ())
    for name in ("경기도 성남시 중원구", "경기도 성남시 분당구"):
        assert collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER not in experience.get(name, ())
    assert collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER not in (reference.unmapped_configured_by_scope["experience"])


@pytest.mark.skipif(
    os.getenv("RUN_SEONGNAM_MUSEUM_LIVE") != "1",
    reason="set RUN_SEONGNAM_MUSEUM_LIVE=1 for the safe official GET-only contract",
)
def test_live_complete_snapshot_uses_no_unsafe_endpoints() -> None:
    rows, parser, meta = collector.collect_seongnam_museum_experience(
        _target(),
        timeout=30,
        max_pages=10,
        detail_limit=30,
    )
    assert parser == collector.SEONGNAM_MUSEUM_EXPERIENCE_PARSER
    assert rows
    assert meta["source_total"] == meta["current_count"] == meta["returned_count"]
    assert meta["detail_verified"] == len(rows)
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert all(bool(row["application_url"]) == row["reservation_available"] for row in rows)
    assert all(
        row["provider_course_id"].startswith(collector.SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER + ":experience:")
        for row in rows
    )
    for key in (
        "application_endpoint_requests",
        "reservation_endpoint_requests",
        "login_endpoint_requests",
        "auth_endpoint_requests",
        "identity_endpoint_requests",
        "applicant_endpoint_requests",
        "member_endpoint_requests",
        "attachment_endpoint_requests",
        "download_endpoint_requests",
        "pii_endpoint_requests",
    ):
        assert meta[key] == 0
    assert meta["pii_payload_persisted"] is False
