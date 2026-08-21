from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
import ssl
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import municipal_sejong_youth as sejong


ROOT = Path(__file__).resolve().parents[1]


def _target(
    *,
    provider: str = sejong.SEJONG_YOUTH_PROVIDER,
    url: str = sejong.SEJONG_YOUTH_CANONICAL_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "세종 청소년 프로그램 신청",
        "branch": "세종특별자치시",
    }


@dataclass(frozen=True)
class Program:
    program_no: str
    round_no: str
    title: str
    round_name: str
    category: str
    status: str = "접수마감"
    method: str = "선착순"
    branch: str = "남세종종합청소년센터"
    education_period: str = "2099. 2. 1. ~ 2099. 2. 3."
    education_datetime: str = "2099-02-01 10:00 ~ 2099-02-03 12:00"
    apply_period: str = "2099-01-01 09:00 ~ 2099-01-31 18:00"
    target: str = "세종시 청소년"

    @property
    def identity(self) -> str:
        return f"{self.program_no}:{self.round_no}"


PROGRAMS = (
    Program(
        "100",
        "1",
        "디지털 미디어 보호자 교육",
        "보호자교육 1회",
        "정서지원",
        status="접수중",
    ),
    Program(
        "101",
        "1",
        "나만의 슬랑이 만들기",
        "슬랑이 만들기",
        "문화/예술",
        status="접수중",
        method="오프라인",
        branch="북세종종합청소년센터",
    ),
    Program(
        "102",
        "1",
        "청소년운영위원회 12기 신규위원 모집",
        "위원 모집",
        "봉사/사회참여",
    ),
    Program(
        "103",
        "1",
        "[홍보] 여름방학 프로그램 안내",
        "[홍보] 프로그램 안내",
        "문화/예술",
    ),
    Program(
        "104",
        "1",
        "[이용예약] 재능개발공간",
        "공간 이용",
        "문화/예술",
    ),
    Program(
        "105",
        "1",
        "[여름방학특강] AI 글쓰기 연구소",
        "AI 글쓰기 특강",
        "기타",
    ),
    Program(
        "106",
        "1",
        "여름방학 맞이 동아리DAY!",
        "동아리DAY",
        "기타",
    ),
    Program(
        "107",
        "1",
        "센터 이용자 만족도 조사",
        "만족도 조사",
        "기타",
    ),
    Program(
        "108",
        "1",
        "발달장애청소년 진로 프로그램",
        "바리스타",
        "진로/직업",
        status="접수예정",
    ),
    Program(
        "109",
        "1",
        "글로벌교류 월드플레이트 참가 청소년 모집",
        "월드플레이트",
        "문화/예술",
        education_period="2099. 2. 8.(일)",
        education_datetime="2099-02-08 12:00 ~ 2099-01-25 17:00",
    ),
    Program(
        "110",
        "1",
        "청소년 성장 지원",
        "청소년 성장 지원",
        "기타",
    ),
)


def _pairs_html(values: dict[str, str], *, class_name: str) -> str:
    return (
        f'<ul class="{class_name}">'
        + "".join(f"<li><em>{label}</em>{value}</li>" for label, value in values.items())
        + "</ul>"
    )


def _list_card(program: Program) -> str:
    pairs = {
        "회차": program.round_name,
        "참여대상": program.target,
        "교육기간": program.education_period,
        "접수기간": program.apply_period,
        "교육일시": program.education_datetime,
    }
    return f"""
    <button class="link" onclick="fn_move_detail('{program.program_no}','{program.round_no}')">
      <div class="item">
        <div class="stats-list"><span>{program.status}</span><span>{program.method}</span><span>{program.category}</span></div>
        <div class="title"><em>{program.branch}</em></div>
        <strong class="tit">{program.title}</strong>
        {_pairs_html(pairs, class_name='ul--block__list')}
      </div>
    </button>
    """


def _list_html(
    rows: list[Program],
    *,
    page: int,
    total: int = 12,
    last_page: int = 2,
    sentinel_nonempty: bool = False,
) -> str:
    if not rows and sentinel_nonempty:
        rows = [PROGRAMS[0]]
    content = "".join(_list_card(row) for row in rows)
    if not rows:
        content = "등록된 프로그램이 없습니다."
    return f"""
    <div class="program--count">총 게시물 {total:,} 개, 페이지 {page} / {last_page}</div>
    <div class="board--card--list type2 board_reservation">{content}</div>
    """


def _control(program: Program) -> str:
    if program.status == "접수중" and program.method == "선착순":
        return (
            f'<a class="btn receiptStatus button-write" href="#" '
            f'data-progrm-no="{program.program_no}" data-tme-no="{program.round_no}">신청하기</a>'
        )
    if program.status == "접수중":
        return '<a class="receiptStatus" href="javascript:void(0);">오프라인접수</a>'
    if program.status == "접수예정":
        return '<a class="receiptStatus" href="javascript:void(0);">접수예정</a>'
    return '<a class="receiptStatus" href="javascript:void(0);">접수마감</a>'


def _detail_html(
    program: Program,
    *,
    mismatch: bool = False,
    form_drift: bool = False,
    extra_table: bool = False,
) -> str:
    pairs = {
        "회차": program.round_name,
        "참여대상": program.target,
        "참여생년": "2008년생 ~ 2017년생",
        "교육기간": program.education_period,
        "활동장소": "세종 청소년 활동실",
        "문의처": "044-000-0000",
        "준비물": "",
        "참가비": "무료",
        "접수기간": program.apply_period,
        "교육일시": program.education_datetime,
        "교육자료": "",
    }
    headers = (
        "회차명",
        "참여생년",
        "교육일시",
        "접수기간",
        "회차자료",
        "모집정원",
        "대기정원",
        "신청자",
        "접수상태",
    )
    row_title = program.round_name + (" 변경" if mismatch else "")
    action = (
        "/youth/prog/wrong/write.do"
        if form_drift
        else sejong.SEJONG_YOUTH_APPLICATION_PATH + ";jsessionid=FIXTURE"
    )
    descriptive_table = (
        "<table><tbody><tr><td>상세 일정표</td></tr></tbody></table>"
        if extra_table
        else ""
    )
    return f"""
    <div class="photo_wrap typeB edue"><div class="info_box">
      <div class="state_box"><span class="badge">{program.method}</span><span class="badge"></span><span class="badge cate-type">{program.category}</span></div>
      <strong class="tit"><em>{program.branch}</em>{program.title}</strong>
      {_pairs_html(pairs, class_name='list-1st')}
    </div></div>
    {descriptive_table}
    <table><thead><tr>{''.join(f'<th>{header}</th>' for header in headers)}</tr></thead>
      <tbody><tr>
        <td>{row_title}</td><td>2008년생 ~ 2017년생</td>
        <td>{program.education_datetime}</td><td>{program.apply_period}</td>
        <td><a href="/youth/cmm/fms/FileDown.do?file=1">자료</a></td>
        <td>10</td><td>5</td><td>2</td><td>{_control(program)}</td>
      </tr></tbody>
    </table>
    <form id="searchForm" method="get" action="{action}">
      <input type="hidden" name="progrmNo"><input type="hidden" name="tmeNo">
      <input type="hidden" name="pageIndex" value="1">
    </form>
    """


class FakeResponse:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.status_code = 200
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}
        self.content = html.encode("utf-8")


class FakeSession:
    def __init__(self, site: "FakeSite") -> None:
        self.site = site
        self.closed = False

    def post(self, url: str, *, data: dict[str, str], timeout: int, allow_redirects: bool) -> FakeResponse:
        assert timeout > 0 and allow_redirects is False
        assert url == sejong.SEJONG_YOUTH_CANONICAL_URL
        assert data["eduBgngDt"] == "2099-01-01"
        assert data["eduEndDt"] == ""
        assert not any(token in url for token in ("progrmAply", "login", "FileDown"))
        page = int(data["pageIndex"])
        self.site.calls.append(("POST", url, dict(data)))
        self.site.page_calls[page] = self.site.page_calls.get(page, 0) + 1
        if page == 1:
            rows = list(PROGRAMS[:10])
            if self.site.unstable_first and self.site.page_calls[page] > 1:
                rows[0] = replace(rows[0], title=rows[0].title + " 변경")
        elif page == 2:
            duplicate = PROGRAMS[9]
            if self.site.duplicate_conflict:
                duplicate = replace(duplicate, title=duplicate.title + " 충돌")
            rows = [duplicate, PROGRAMS[10]]
        else:
            rows = []
        return FakeResponse(
            url,
            _list_html(
                rows,
                page=page,
                sentinel_nonempty=self.site.sentinel_nonempty and page == 3,
            ),
        )

    def get(self, url: str, *, timeout: int, allow_redirects: bool) -> FakeResponse:
        assert timeout > 0 and allow_redirects is False
        parsed = urlparse(url)
        assert parsed.path == sejong.SEJONG_YOUTH_DETAIL_PATH
        assert not any(token in parsed.path for token in ("progrmAply", "login", "FileDown"))
        query = parse_qs(parsed.query)
        identity = f"{query['progrmNo'][0]}:{query['tmeNo'][0]}"
        program = next(row for row in PROGRAMS if row.identity == identity)
        self.site.calls.append(("GET", url, None))
        return FakeResponse(
            url,
            _detail_html(
                program,
                mismatch=identity == self.site.detail_mismatch,
                form_drift=identity == self.site.form_drift,
                extra_table=identity == "105:1",
            ),
        )

    def close(self) -> None:
        self.closed = True


class FakeSite:
    def __init__(
        self,
        *,
        sentinel_nonempty: bool = False,
        duplicate_conflict: bool = False,
        unstable_first: bool = False,
        detail_mismatch: str = "",
        form_drift: str = "",
    ) -> None:
        self.sentinel_nonempty = sentinel_nonempty
        self.duplicate_conflict = duplicate_conflict
        self.unstable_first = unstable_first
        self.detail_mismatch = detail_mismatch
        self.form_drift = form_drift
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []
        self.page_calls: dict[int, int] = {}
        self.sessions: list[FakeSession] = []

    def session_factory(self) -> FakeSession:
        value = FakeSession(self)
        self.sessions.append(value)
        return value


def _collect(site: FakeSite, **kwargs: Any):
    return sejong.collect_sejong_youth_programs(
        _target(),
        timeout=3,
        max_pages=20,
        detail_limit=20,
        session_factory=site.session_factory,
        today="2099-01-01",
        **kwargs,
    )


def test_exact_target_and_classifier_contract() -> None:
    assert sejong.is_sejong_youth_target(_target())
    assert not sejong.is_sejong_youth_target(
        _target(url=sejong.SEJONG_YOUTH_CANONICAL_URL + "?pageIndex=1")
    )
    assert not sejong.is_sejong_youth_target(
        _target(url="https://www2.sejong.go.kr.evil.test/youth/prog/progrm/kor/sub03_02/list.do")
    )
    assert not sejong.is_sejong_youth_target(_target(provider="OTHER"))
    assert sejong.classify_sejong_youth_program(
        "청소년운영위원회 신규위원 모집", "위원 모집", "봉사/사회참여"
    ) == ("exclude", "committee_membership")
    assert sejong.classify_sejong_youth_program(
        "청소년동아리 IOL 여름방학 멘토링", "착한 소비", "인문/과학"
    )[0] == "education"
    assert sejong.classify_sejong_youth_program(
        "여름방학 맞이 동아리DAY!", "동아리DAY", "기타"
    )[0] == "experience"


def test_collects_complete_mixed_snapshot_and_excludes_non_courses() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == sejong.SEJONG_YOUTH_PARSER
    assert len(rows) == 6
    assert Counter(row["program_type"] for row in rows) == {"교육": 3, "체험": 3}
    assert {tuple(row["provider_course_id"].rsplit(":", 2)[-2:]) for row in rows} == {
        ("100", "1"),
        ("101", "1"),
        ("105", "1"),
        ("106", "1"),
        ("108", "1"),
        ("109", "1"),
    }
    corrected = next(row for row in rows if row["raw_fields"]["source_program_no"] == "109")
    assert corrected["end_date"] == "2099-02-08"
    assert corrected["raw_fields"]["source_reversed_end_date_corrected"] is True
    assert all(row["municipality_code"] == "3611000000" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all("044-000-0000" not in repr(row) for row in rows)
    assert all("FileDown" not in repr(row) for row in rows)

    assert meta["source_total"] == 12
    assert meta["unique_source_count"] == 11
    assert meta["source_duplicate_count"] == 1
    assert meta["detail_attempts"] == 11
    assert meta["detail_pages"] == 11
    assert meta["returned_count"] == 6
    assert meta["education_count"] == 3
    assert meta["experience_count"] == 3
    assert meta["excluded_non_program_count"] == 5
    assert meta["excluded_reason_counts"] == {
        "committee_membership": 1,
        "facility_use": 1,
        "promotion_notice": 1,
        "survey": 1,
        "unclassified_other": 1,
    }
    assert meta["application_control_count"] == 1
    assert meta["sentinel_page"] == 3
    assert meta["stable_first_page"] is True
    assert meta["stable_final_page"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert [method for method, _url, _payload in site.calls].count("POST") == 5
    assert [method for method, _url, _payload in site.calls].count("GET") == 11
    assert all(session.closed for session in site.sessions)


@pytest.mark.parametrize(
    ("site", "error"),
    [
        (FakeSite(sentinel_nonempty=True), "empty sentinel"),
        (FakeSite(duplicate_conflict=True), "conflicting fields"),
        (FakeSite(unstable_first=True), "first-page boundary changed"),
        (FakeSite(detail_mismatch="105:1"), "detail 105:1"),
        (FakeSite(form_drift="105:1"), "application discovery form"),
    ],
)
def test_contract_drift_fails_atomically(site: FakeSite, error: str) -> None:
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in meta["configured_collection_error"]
    assert all("progrmAply" not in url for _method, url, _payload in site.calls)


def test_caps_fail_before_partial_detail_output() -> None:
    site = FakeSite()
    rows, _parser, meta = sejong.collect_sejong_youth_programs(
        _target(),
        max_pages=4,
        detail_limit=20,
        session_factory=site.session_factory,
        today="2099-01-01",
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_pages"] == 0

    site = FakeSite()
    rows, _parser, meta = sejong.collect_sejong_youth_programs(
        _target(),
        max_pages=20,
        detail_limit=10,
        session_factory=site.session_factory,
        today="2099-01-01",
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_pages"] == 0


def test_embedded_intermediate_keeps_tls_verification_enabled() -> None:
    der = __import__("base64").b64decode(sejong._GLOBALSIGN_ALPHA_SSL_2025_DER_B64)
    assert hashlib.sha256(der).hexdigest() == sejong.SEJONG_YOUTH_INTERMEDIATE_SHA256
    context = sejong._tls_context()
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_router_and_operational_configs_are_wired(monkeypatch) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    sentinel = ([{"id": 1}], sejong.SEJONG_YOUTH_PARSER, {"snapshot_complete": True})
    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(sejong, "collect_sejong_youth_programs", collect)
    target = municipal.CrawlTarget(
        provider=sejong.SEJONG_YOUTH_PROVIDER,
        name="세종 청소년 프로그램 신청",
        branch="세종특별자치시",
        url=sejong.SEJONG_YOUTH_CANONICAL_URL,
        source="test",
    )
    assert municipal.collect_from_url(
        target,
        timeout=3,
        max_pages=20,
        detail_limit=30,
    ) == sentinel
    assert captured["timeout"] == 3
    assert callable(captured["dedupe_rows"])

    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    matches = [
        item
        for item in targets
        if item.get("provider") == sejong.SEJONG_YOUTH_PROVIDER
        and item.get("url") == sejong.SEJONG_YOUTH_CANONICAL_URL
    ]
    assert len(matches) == 1
    assert matches[0]["crawler_status"] == "ready"
    assert set(matches[0]["ops_scopes"]) == {"education", "experience"}
    assert matches[0]["last_quality"]["snapshot_complete"] is True

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    entries = [
        item
        for item in operational
        if item.get("provider") == sejong.SEJONG_YOUTH_PROVIDER
        and item.get("target_url") == sejong.SEJONG_YOUTH_CANONICAL_URL
    ]
    assert len(entries) == 1
    assert entries[0]["validation_outcome"] == "collected"
    assert entries[0]["row_count"] == 68
