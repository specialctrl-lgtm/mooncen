from __future__ import annotations

from dataclasses import dataclass
from html import escape
import math
import os
import threading
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_incheon_gyeyang as gyeyang


@dataclass
class _Session:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


def _target(
    provider: str = gyeyang.GYEYANG_PROVIDER,
    candidate_id: str = gyeyang.GYEYANG_CANDIDATE_ID,
    url: str = gyeyang.GYEYANG_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "candidate_id": candidate_id,
        "url": url,
        "name": "계양구 평생학습포털 교육신청",
        "branch": gyeyang.GYEYANG_MUNICIPALITY_NAME,
    }


def _course(
    identity: str,
    division: str,
    *,
    branch: str,
    title: str | None = None,
    status: str = "접수마감",
    start: str = "2026-08-01",
    end: str = "2026-09-01",
) -> dict[str, str]:
    if title is None:
        title = f"계양 안전교육 {identity}"
    if status == "추가접수중":
        application_kind = "추가"
    elif status == "방문접수중":
        application_kind = "방문"
    else:
        application_kind = "정시"
    return {
        "identity": identity,
        "division": division,
        "branch": branch,
        "title": title,
        "status": status,
        "start": start,
        "end": end,
        "application_kind": application_kind,
    }


def _list_row(course: Mapping[str, str], *, link_division: str | None = None) -> str:
    division = link_division or course["division"]
    href = (
        "/program/programInfoDetail.do?"
        f"prgm_seq={course['identity']}&prgmdiv={division}&acptrun=y&pgno=1"
    )
    period = f"{course['start'].replace('-', '.')} ~ {course['end'].replace('-', '.')}"
    application_period = (
        f'<li><span class="q">{course["application_kind"]}접수 :</span>'
        "07.01 09:00~07.31 23:59</li>"
        if course["application_kind"]
        else ""
    )
    return f"""
      <li class="{'close' if course['status'] == '접수마감' else ''}">
        <div><p class="tit"><a href="{escape(href, quote=True)}">{escape(course['title'])}</a></p>
        <p class="tag_state">{course['status']}</p>
        <ul class="lec_info">
          {application_period}
          <li><span class="q">주최기관 :</span>{course['branch']}</li>
          <li><span class="q">교육일정 :</span>{period}</li>
          <li><span class="q">문의처 :</span>032-450-0000</li>
          <li><span class="q">교육시간 :</span>[오전] 월 (10:00~12:00)</li>
          <li><span class="q">수강 료 :</span>0원</li>
          <li><span class="q">수강정원 :</span>선착순 10 명</li>
        </ul></div>
      </li>
    """


def _registry_inputs() -> str:
    return "".join(
        f'<input type="checkbox" name="cate2" value="{code}">'
        for code in gyeyang.GYEYANG_RESIDENT_CENTRES
    )


def _list_page(
    division: str,
    rows: list[Mapping[str, str]],
    *,
    total: int,
    reported_page: int,
) -> str:
    last = math.ceil(total / gyeyang.GYEYANG_PAGE_SIZE) if total else 0
    registry = _registry_inputs() if division == "citizen" else ""
    return f"""
      <html><body>
        <form action="{gyeyang.GYEYANG_LIST_PATH}" method="get">
          <input type="hidden" name="prgmdiv" value="{division}">
          {registry}
        </form>
        <p class="off edu_state"><a href="#">접수중</a></p>
        <div class="edu_array">전체 {total:,}건, 현재페이지 {reported_page}/{last}</div>
        <div class="board_list"><ul class="lecList">
          {''.join(_list_row(row, link_division=division) for row in rows)}
        </ul></div>
      </body></html>
    """


def _detail_page(
    course: Mapping[str, str],
    *,
    title: str | None = None,
    application_identity: str | None = None,
    status: str | None = None,
) -> str:
    division = course["division"]
    if division == "life":
        field = "평생학습관 > 없음"
        institution = "평생학습관"
    elif division == "citizen":
        field = f"주민자치센터 > {course['branch']}"
        institution = "주민자치센터"
    else:
        field = "골목틈새학교 > 없음"
        institution = "골목틈새학교"
    application = ""
    if course["status"] in {"정시접수중", "추가접수중", "대기접수"}:
        identity = application_identity or course["identity"]
        application = (
            '<a class="btn btn_ok" '
            f'href="/program/programAcptRegForm.do?prgm_seq={identity}&prgmdiv={division}">'
            "수강신청</a>"
        )
    safe_fields = [
        ("분야", field),
        ("교육기관", institution),
        ("추첨여부", "선착순"),
        ("정원", "방문: 1/2명 온라인: 3/8명"),
        ("대기", "1 / 5 명"),
        ("교육 레벨", "기본"),
        ("교육 대상", "성인"),
        ("나이제한", "제한없음"),
        ("교육기간", f"{course['start']} ~ {course['end']}"),
        ("교육 요일", "월"),
        ("교육 시간", "10:00~12:00"),
        ("강사명", "비공개 검증값"),
        ("수강료", "무료"),
        ("재료비", "없음"),
        ("강의실", "교육실"),
        ("문의처", "032-450-9999"),
    ]
    if course["application_kind"]:
        safe_fields.insert(
            0,
            (
                f"{course['application_kind']} 접수",
                "2026.07.01 09시 00분 ~ 2026.07.31 23시 59분",
            ),
        )
    fields = "".join(
        f"<li><dl><dt>{key}</dt><dd>{escape(value)}</dd></dl></li>"
        for key, value in safe_fields
    )
    source_status = status or course["status"]
    detail_status = "접수예정" if source_status == "신청예정" else source_status
    return f"""
      <html><body><div class="board_view">
        <div class="title"><div><span>오전</span><span>{detail_status}</span><span>교육전</span></div>
          <p class="margin_t10">{escape(title or course['title'])}</p></div>
        <ul class="data_list list_col2">{fields}</ul>
        <div class="add_file">민감한 첨부·연락처는 파서가 읽지 않아야 합니다.</div>
        <div class="con"><div class="detail">자유본문 user@example.com 032-123-4567</div></div>
      </div>{application}</body></html>
    """


class _Backend:
    def __init__(self) -> None:
        resident_names = list(gyeyang.GYEYANG_RESIDENT_CENTRES.values())
        self.courses: dict[str, dict[str, str]] = {
            "1001": _course("1001", "life", branch="평생학습관"),
            "1431": _course(
                "1431",
                "life",
                branch="평생학습관",
                title="수강신청을 연습하는 화면입니다. (실제 강좌 없음)",
                status="대기접수",
                start="2027-01-01",
                end="2027-01-01",
            ),
        }
        statuses = ("정시접수중", "방문접수중", "추가접수중", "대기접수")
        for offset in range(11):
            identity = str(2001 + offset)
            self.courses[identity] = _course(
                identity,
                "citizen",
                branch=resident_names[offset % len(resident_names)],
                status=(
                    statuses[offset]
                    if offset < len(statuses)
                    else "신청예정"
                    if offset == 4
                    else "접수마감"
                ),
            )
        self.courses["2006"]["application_kind"] = ""
        self.order = {
            "life": ["1001", "1431"],
            "citizen": [str(2001 + offset) for offset in range(11)],
            "school": [],
        }
        self.aggregate_override: list[str] | None = None
        self.bad_detail_title: dict[str, str] = {}
        self.bad_application_identity: dict[str, str] = {}
        self.detail_status: dict[str, str] = {}
        self.unstable_key: tuple[str, int] | None = None
        self.call_counts: dict[tuple[str, int], int] = {}
        self.calls: list[str] = []
        self.sessions: list[_Session] = []
        self._lock = threading.Lock()

    def session(self) -> _Session:
        value = _Session()
        with self._lock:
            self.sessions.append(value)
        return value

    def fetch(self, _session: Any, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 9
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        with self._lock:
            self.calls.append(url)
        if parsed.path == gyeyang.GYEYANG_DETAIL_PATH:
            identity = query["prgm_seq"][0]
            course = self.courses[identity]
            html = _detail_page(
                course,
                title=self.bad_detail_title.get(identity),
                application_identity=self.bad_application_identity.get(identity),
                status=self.detail_status.get(identity),
            )
            return BeautifulSoup(html, "lxml")
        assert parsed.path == gyeyang.GYEYANG_LIST_PATH
        division = query["prgmdiv"][0]
        requested_page = int(query["pgno"][0])
        if division == "all":
            identities = self.aggregate_override
            if identities is None:
                identities = self.order["life"] + self.order["citizen"] + self.order["school"]
        else:
            identities = self.order[division]
        total = len(identities)
        last = math.ceil(total / gyeyang.GYEYANG_PAGE_SIZE) if total else 0
        reported_page = min(requested_page, last) if total else 0
        effective_page = max(1, reported_page)
        start = (effective_page - 1) * gyeyang.GYEYANG_PAGE_SIZE
        page_ids = identities[start : start + gyeyang.GYEYANG_PAGE_SIZE]
        rows = [dict(self.courses[identity]) for identity in page_ids]
        key = (division, requested_page)
        with self._lock:
            self.call_counts[key] = self.call_counts.get(key, 0) + 1
            call_count = self.call_counts[key]
        if self.unstable_key == key and call_count > 1 and rows:
            rows[0]["title"] += " 변경"
        html = _list_page(
            division,
            rows,
            total=total,
            reported_page=reported_page,
        )
        return BeautifulSoup(html, "lxml")


def _collect(backend: _Backend, **kwargs: Any):
    return gyeyang.collect_incheon_gyeyang_education(
        _target(),
        timeout=9,
        max_pages=20,
        detail_limit=20,
        today="2026-07-22",
        max_requests=80,
        max_workers=3,
        fetch_attempts=1,
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        dedupe_rows=lambda rows: rows,
        **kwargs,
    )


def test_owner_constants_urls_and_candidate_boundaries_are_exact() -> None:
    assert gyeyang.GYEYANG_PROVIDER == "MUNI_GYLLE_GYEYANG_GO_KR_1630ABDE"
    assert gyeyang.GYEYANG_CANDIDATE_ID == "MUNI_IR_0CA80BB9B401"
    assert gyeyang.GYEYANG_LEGACY_HOME_CANDIDATE_ID == "MUNI_IR_9FBB86F259B7"
    assert gyeyang.GYEYANG_MUNICIPALITY_CODE == "2824500000"
    assert gyeyang.GYEYANG_URL.endswith("/program/programInfoList.do?prgmdiv=all")
    assert [item.key for item in gyeyang.GYEYANG_CATALOGUES] == [
        "life",
        "citizen",
        "school",
    ]
    assert set(gyeyang.GYEYANG_RESIDENT_CENTRES.values()) == {
        "효성1동",
        "효성2동",
        "계산1동",
        "계산2동",
        "계산3동",
        "계산4동",
        "작전1동",
        "작전2동",
        "작전서운동",
        "계양1동",
        "계양2동",
        "계양3동",
    }
    assert gyeyang.GYEYANG_CANDIDATE_AUDIT["MUNI_IR_E7645B1861FF"]["owner"] == gyeyang.GYEYANG_PROVIDER
    assert gyeyang.GYEYANG_CANDIDATE_AUDIT["GYEYANG_LIFELONG"]["owner"] == gyeyang.GYEYANG_PROVIDER
    assert gyeyang.GYEYANG_CANDIDATE_AUDIT["MUNI_IR_EAFA3C86B2E4"]["decision"].startswith("separate_")
    assert parse_qs(urlparse(gyeyang.gyeyang_list_url("citizen", 7)).query) == {
        "prgmdiv": ["citizen"],
        "acptrun": ["n"],
        "orderby": ["edt"],
        "pgno": ["7"],
    }
    assert gyeyang.gyeyang_detail_url("life", "1431").endswith(
        "prgm_seq=1431&prgmdiv=life"
    )


@pytest.mark.parametrize(
    ("provider", "candidate_id", "url"),
    [
        ("WRONG", gyeyang.GYEYANG_CANDIDATE_ID, gyeyang.GYEYANG_URL),
        (gyeyang.GYEYANG_PROVIDER, "WRONG", gyeyang.GYEYANG_URL),
        (gyeyang.GYEYANG_PROVIDER, gyeyang.GYEYANG_CANDIDATE_ID, "http://gylle.gyeyang.go.kr/program/programInfoList.do?prgmdiv=all"),
        (gyeyang.GYEYANG_PROVIDER, gyeyang.GYEYANG_CANDIDATE_ID, "https://gylle.gyeyang.go.kr/program/programInfoList.do?prgmdiv=life"),
        (gyeyang.GYEYANG_PROVIDER, gyeyang.GYEYANG_CANDIDATE_ID, "https://evil.example/program/programInfoList.do?prgmdiv=all"),
    ],
)
def test_target_identity_rejects_aliases(
    provider: str, candidate_id: str, url: str
) -> None:
    assert not gyeyang.is_incheon_gyeyang_education_target(
        _target(provider=provider, candidate_id=candidate_id, url=url)
    )


def test_complete_owner_partitions_equal_aggregate_and_details_are_safe() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == gyeyang.GYEYANG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == 13
    assert meta["source_rows_by_catalogue"] == {
        "life": 2,
        "citizen": 11,
        "school": 0,
    }
    assert meta["aggregate_total"] == 13
    assert meta["data_pages_by_catalogue"] == {
        "life": 1,
        "citizen": 2,
        "school": 0,
    }
    assert meta["aggregate_pages"] == 2
    assert meta["pages"] == 3
    assert meta["list_requests"] == 13
    assert meta["sentinel_requests"] == 3
    assert meta["sentinel_kinds"] == {
        "life": "exact_final_page_clamp",
        "citizen": "exact_final_page_clamp",
        "school": "stable_empty",
    }
    assert meta["stability_rechecks"] == 5
    assert meta["detail_attempts"] == 13
    assert meta["detail_pages"] == 13
    assert meta["suppressed_nonproduction_rows"] == 1
    assert meta["returned_count"] == 12
    assert meta["network_requests"] == 26
    assert meta["retry_count"] == 0
    assert meta["worker_sessions"] == 3
    assert meta["closed_without_application_period_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["aggregate_union_complete"] is True
    assert meta["details_complete"] is True
    assert meta["stable_recheck_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert len(rows) == 12
    assert not any(row["provider_course_id"].endswith(":1431") for row in rows)

    by_id = {row["raw_fields"]["prgm_seq"]: row for row in rows}
    online = by_id["2001"]
    assert online["status"] == "OPEN"
    assert online["reservation_available"] is True
    assert online["application_url"].endswith("prgm_seq=2001&prgmdiv=citizen")
    assert online["branch"] == "효성1동"
    assert online["branch_code"] == "GYEYANG_HYOSUNG1"
    assert online["capacity_current"] == 4
    assert online["capacity_total"] == 10
    assert online["waitlist_current"] == 1
    assert online["waitlist_total"] == 5
    assert online["target"] == "성인"
    assert online["schedule_raw"] == "월 10:00~12:00"
    assert "phone" not in online
    assert "instructor" not in online
    assert "description" not in online
    assert "032-" not in repr(rows)
    assert "@example" not in repr(rows)

    visit = by_id["2002"]
    assert visit["status"] == "OPEN"
    assert visit["reservation_available"] is True
    assert "application_url" not in visit
    assert visit["application_method_raw"] == "방문접수중"
    assert all("programAcptRegForm" not in url for url in backend.calls)
    assert all("programInfoFileDownload" not in url for url in backend.calls)
    assert all(session.closed for session in backend.sessions)


def test_aggregate_identity_drift_fails_atomically() -> None:
    backend = _Backend()
    backend.aggregate_override = backend.order["life"] + backend.order["citizen"][:-1]
    rows, _parser, meta = _collect(backend)
    assert rows == []
    assert "advertised catalogue total changed" in meta["configured_collection_error"]
    assert meta["aggregate_union_complete"] is False
    assert meta["snapshot_complete"] is False


def test_boundary_change_during_recheck_fails_atomically() -> None:
    backend = _Backend()
    backend.unstable_key = ("citizen", 1)
    rows, _parser, meta = _collect(backend)
    assert rows == []
    assert "changed during stable recheck" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


def test_detail_title_mismatch_fails_atomically() -> None:
    backend = _Backend()
    backend.bad_detail_title["2001"] = "다른 강좌"
    rows, _parser, meta = _collect(backend)
    assert rows == []
    assert "detail title does not match" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 13


def test_cross_identity_application_control_fails_atomically() -> None:
    backend = _Backend()
    backend.bad_application_identity["2001"] = "9999"
    rows, _parser, meta = _collect(backend)
    assert rows == []
    assert "application control changed programme identity" in meta["configured_collection_error"]


def test_open_to_waiting_capacity_transition_uses_detail_status() -> None:
    backend = _Backend()
    backend.detail_status["2001"] = "대기접수"
    rows, _parser, meta = _collect(backend)
    assert meta["configured_collection_error"] == ""
    row = next(row for row in rows if row["raw_fields"]["prgm_seq"] == "2001")
    assert row["status"] == "WAITING"
    assert row["raw_fields"]["list_status"] == "정시접수중"
    assert row["raw_fields"]["detail_status"] == "대기접수"
    assert row["raw_fields"]["status_reconciled"] is True
    assert meta["reconciled_status_rows"] == 1


def test_unapproved_test_title_is_never_published() -> None:
    backend = _Backend()
    backend.courses["2001"]["title"] = "신규 테스트 강좌"
    rows, _parser, meta = _collect(backend)
    assert rows == []
    assert "unapproved nonproduction course" in meta["configured_collection_error"]


def test_practice_allowlist_is_bound_to_exact_dates() -> None:
    backend = _Backend()
    backend.courses["1431"]["end"] = "2027-02-01"
    rows, _parser, meta = _collect(backend)
    assert rows == []
    assert "unapproved nonproduction course" in meta["configured_collection_error"]
    assert meta["suppressed_nonproduction_rows"] == 0


def test_unknown_resident_branch_fails_before_details() -> None:
    backend = _Backend()
    backend.courses["2001"]["branch"] = "계양99동"
    rows, _parser, meta = _collect(backend)
    assert rows == []
    assert "unknown resident-centre branch" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


def test_caps_fail_closed_without_partial_rows() -> None:
    backend = _Backend()
    rows, _parser, meta = gyeyang.collect_incheon_gyeyang_education(
        _target(),
        timeout=9,
        max_pages=2,
        detail_limit=20,
        today="2026-07-22",
        max_requests=80,
        max_workers=3,
        fetch_attempts=1,
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert "sentinel page 3 exceeds max_pages" in meta["configured_collection_error"]
    assert meta["source_cap_reached"] is True

    backend = _Backend()
    rows, _parser, meta = gyeyang.collect_incheon_gyeyang_education(
        _target(),
        timeout=9,
        max_pages=20,
        detail_limit=12,
        today="2026-07-22",
        max_requests=80,
        max_workers=3,
        fetch_attempts=1,
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert "detail count 13 exceeds detail_limit 12" in meta["configured_collection_error"]
    assert meta["source_cap_reached"] is True


def test_only_two_exact_historical_period_anomalies_are_tolerated() -> None:
    citizen = next(
        item for item in gyeyang.GYEYANG_CATALOGUES if item.key == "citizen"
    )
    assert gyeyang._list_date_range(
        citizen,
        "1051",
        "컴퓨터 A반 (기초)",
        "작전2동",
        "~ 2024.03.31",
    ) == ("2024-03-31", "2024-03-31", True)
    assert gyeyang._list_date_range(
        citizen,
        "831",
        "명화로 만나는 나의 재능",
        "작전2동",
        "~",
    ) == ("1900-01-01", "1900-01-01", True)
    with pytest.raises(gyeyang.GyeyangContractError, match="unaudited"):
        gyeyang._list_date_range(
            citizen,
            "1051",
            "재사용된 강좌",
            "작전2동",
            "~ 2024.03.31",
        )


def test_invalid_limits_and_wrong_target_return_explicit_failed_meta() -> None:
    rows, parser, meta = gyeyang.collect_incheon_gyeyang_education(
        _target(provider="WRONG")
    )
    assert rows == []
    assert parser == gyeyang.GYEYANG_PARSER
    assert "canonical Gyeyang" in meta["configured_collection_error"]

    rows, _parser, meta = gyeyang.collect_incheon_gyeyang_education(
        _target(), max_requests=0
    )
    assert rows == []
    assert meta["configured_collection_error"] == "invalid collection limits"


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_TESTS") != "1",
    reason="set RUN_LIVE_MUNICIPAL_TESTS=1 for the official Gyeyang live audit",
)
def test_live_complete_current_snapshot() -> None:
    rows, parser, meta = gyeyang.collect_incheon_gyeyang_education(
        _target(),
        timeout=30,
        max_pages=200,
        detail_limit=180,
        max_requests=520,
    )
    assert parser == gyeyang.GYEYANG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == meta["aggregate_total"]
    assert meta["source_total"] == sum(meta["source_rows_by_catalogue"].values())
    assert meta["detail_pages"] == meta["source_total"]
    assert meta["returned_count"] == len(rows)
    assert meta["suppressed_nonproduction_rows"] in {0, 1}
    assert meta["full_snapshot_validated"] is True
    assert all(row["end_date"] >= "2026-07-22" for row in rows)
