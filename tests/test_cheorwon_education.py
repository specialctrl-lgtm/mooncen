from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import ssl
from threading import Lock
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from Crawler import municipal_cheorwon as cheorwon


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    code: str
    title: str
    year: str = "2026"
    ins_no: str = "3"
    status: str = "접수마감"
    start: str = "2026-01-05"
    end: str = "2026-03-30"
    apply_start: str = "2025-12-01"
    apply_end: str = "2025-12-20"
    method: str = "온라인"
    fee: str = "무료"
    schedule: str = "10:00~12:00 ( 화 )"
    target: str = "성인"
    venue: str = "2층 배움실"
    category: str = "취미문화"
    selection: str = "선착순"
    current: int = 3
    total: int = 12
    waiting: int = 0
    waiting_total: int = 5

    @property
    def institution(self) -> str:
        return cheorwon.CHEORWON_INSTITUTION_BY_INS_NO[self.ins_no]


class DummySession:
    def close(self) -> None:
        return None


def _target(*, canonical: bool = False) -> Target:
    return Target(
        cheorwon.CHEORWON_PROVIDER,
        (
            cheorwon.CHEORWON_CANONICAL_URL
            if canonical
            else cheorwon.CHEORWON_REGISTERED_URL
        ),
        cheorwon.CHEORWON_CANONICAL_CANDIDATE_ID,
    )


def _current_courses() -> list[Course]:
    return [
        Course(
            "9001",
            "2026-6-26",
            "여름 문화쉼터",
            ins_no="4",
            status="접수진행",
            start="2026-08-08",
            end="2026-08-08",
            apply_start="2026-07-21",
            apply_end="2026-08-05",
            method="온라인,방문,전화",
            venue="1층 로비",
            category="기타",
        ),
        Course(
            "9002",
            "2026-204-하반기정규강좌",
            "야간 방송댄스",
            status="접수대기",
            start="2026-08-25",
            end="2026-12-08",
            apply_start="2026-07-31",
            apply_end="2026-08-07",
            venue="별관2층 건강실",
            category="운동",
        ),
        Course(
            "9003",
            "2026-30-상반기정규강좌",
            "종료된 통기타",
            status="접수마감",
            start="2026-03-02",
            end="2026-06-30",
            apply_start="2026-02-01",
            apply_end="2026-02-15",
            category="음악",
        ),
        Course(
            "9004",
            "2026-69-상반기정규강좌야간",
            "폐강된 토익",
            status="폐강",
            start="2026-08-01",
            end="2026-10-01",
            apply_start="2026-07-01",
            apply_end="2026-07-10",
        ),
    ]


def _historical_courses(count: int = 197) -> list[Course]:
    return [
        Course(
            str(8000 - number),
            f"2025-{number + 1}",
            f"과거 강좌 {number + 1}",
            year="2025",
            start="2025-02-01",
            end="2025-05-31",
            apply_start="2025-01-01",
            apply_end="2025-01-20",
        )
        for number in range(count)
    ]


def _form(year: str) -> str:
    options = []
    for value in ("2026", "2025", "2024", "2023", "2022"):
        selected = " selected" if year == value else ""
        options.append(
            f'<option value="{value}"{selected}>{value}</option>'
        )
    return f"""
      <form id="lctreVO" name="lctreVOForm" method="get"
            action="{cheorwon.CHEORWON_LIST_PATH}?key=692">
        <input type="hidden" name="key" value="692">
        <input type="hidden" name="insNo" value="">
        <input type="hidden" name="pageUnit" value="10">
        <select id="year" name="year">{''.join(options)}</select>
        <select name="semesterType"><option value="" selected>기수 선택</option></select>
        <select name="rceptSttus"><option value="" selected>접수상태 선택</option></select>
        <select name="lctreType"><option value="" selected>강좌분야 선택</option></select>
      </form>
    """


def _list_row(item: Course) -> str:
    href = cheorwon.CHEORWON_DETAIL_PATH + "?" + urlencode(
        {
            "key": "692",
            "insNo": item.ins_no,
            "lctreSe": "",
            "lctreNo": item.identity,
            "pageUnit": "100",
        }
    )
    capacity = (
        f"{item.current} / {item.total} "
        f"({item.waiting} / {item.waiting_total})"
    )
    return f"""
      <tr>
        <td>{escape(item.code)}</td>
        <td><a href="{escape(href)}">{escape(item.title)}</a></td>
        <td>김강사</td>
        <td>{escape(item.fee)}</td>
        <td>{escape(item.method)}</td>
        <td>-</td>
        <td>{capacity}</td>
        <td>{escape(item.schedule)}</td>
        <td>{escape(item.selection)}</td>
        <td>{escape(item.status)}</td>
      </tr>
    """


_HEADERS = (
    "코드번호",
    "강좌명",
    "강사명",
    "수강료",
    "접수 방법",
    "접수 인원",
    "승인/모집 (대기인원)",
    "교육시간 (교육요일)",
    "선정 방법",
    "상태",
)


def _list_html(
    rows: list[Course],
    *,
    total: int,
    page: int,
    year: str,
    active_page: int | None = None,
    wrong_sentinel: bool = False,
    omit_last: bool = False,
) -> str:
    last = max(1, (total + 99) // 100)
    if rows:
        body = "".join(_list_row(item) for item in rows)
    elif wrong_sentinel:
        body = "<tr><td>강좌 없음</td></tr>"
    else:
        body = '<tr><td colspan="10">등록된 강좌가 없습니다.</td></tr>'
    active = ""
    if rows:
        shown_page = active_page if active_page is not None else page
        active = f'<strong class="p-page__link active">{shown_page}</strong>'
    last_link = ""
    if total > 100 and not omit_last:
        href = cheorwon.CHEORWON_LIST_PATH + "?" + urlencode(
            {"key": "692", "year": year, "pageUnit": "100", "pageIndex": last}
        )
        last_link = (
            f'<a class="p-page__link next-end" href="{escape(href)}">'
            "끝 페이지</a>"
        )
    headers = "".join(f"<th>{escape(value)}</th>" for value in _HEADERS)
    return f"""
      <html lang="ko"><head><title>교육신청 - 철원군평생학습관</title></head>
      <body>{_form(year)}<div class="total">총 {total} 건</div>
        <table class="table responsive">
          <thead><tr>{headers}</tr></thead><tbody>{body}</tbody>
        </table>{active}{last_link}
      </body></html>
    """


_HEADING_STATUS = {
    "접수진행": "접수중",
    "대기접수": "대기접수",
    "접수대기": "접수대기",
    "접수마감": "접수마감",
    "폐강": "폐강",
}


def _detail_html(
    item: Course,
    *,
    bad_period: bool = False,
    institution_mismatch: bool = False,
    title_mismatch: bool = False,
    method_mismatch: bool = False,
    missing_control: bool = False,
    control_identity_mismatch: bool = False,
    inactive_control: bool = False,
    field_drift: bool = False,
) -> str:
    title = "다른 강좌" if title_mismatch else item.title
    heading = (
        f"{_HEADING_STATUS[item.status]} 교육대기 {title} "
        f"코드번호 ( {item.code} )"
    )
    institution = "다른 기관" if institution_mismatch else item.institution
    method = "방문" if method_mismatch else item.method
    period = (
        "날짜 미정"
        if bad_period
        else (
            f"{item.start[:4]}년 {int(item.start[5:7]):02d}월 "
            f"{int(item.start[8:10]):02d}일 ~ "
            f"{item.end[:4]}년 {int(item.end[5:7]):02d}월 "
            f"{int(item.end[8:10]):02d}일 (10시 00분 ~ 12시 00분)"
        )
    )
    apply_period = (
        f"{item.apply_start[:4]}년 {int(item.apply_start[5:7]):02d}월 "
        f"{int(item.apply_start[8:10]):02d}일 (10시 00분) ~ "
        f"{item.apply_end[:4]}년 {int(item.apply_end[5:7]):02d}월 "
        f"{int(item.apply_end[8:10]):02d}일 (18시 00분)"
    )
    pairs = [
        ("기수", item.year),
        ("강좌분야", item.category),
        ("강좌대상", item.target),
        ("강좌장소", item.venue),
        ("모집정원", f"{item.total} 명"),
        ("대기인원", f"{item.waiting_total} 명"),
        ("문의전화", "033-450-5867"),
        ("접수방법", method),
        ("선정방법", item.selection),
        ("접수기간", apply_period),
        ("강좌일자", period),
        ("수강료", item.fee),
        ("재료비", "강의계획서참고"),
        ("강사명", "김강사"),
        ("기관명", institution),
        ("강의계획서", "private-plan.pdf"),
        ("강좌안내", "자유서술은 저장하면 안 됩니다."),
        ("참고사항", "신청자 개인정보를 입력하지 않습니다."),
    ]
    if field_drift:
        pairs.pop()
    pair_rows = "".join(
        f"<tr><th>{escape(key)}</th><td>{escape(value)}</td></tr>"
        for key, value in pairs
    )
    actionable = item.status in {"접수진행", "대기접수"}
    show_control = actionable and "온라인" in item.method and not missing_control
    if inactive_control:
        show_control = True
    control = ""
    if show_control:
        control_id = "999999" if control_identity_mismatch else item.identity
        href = cheorwon.CHEORWON_APPLICATION_PATH + "?" + urlencode(
            {"key": "692", "insNo": item.ins_no, "lctreNo": control_id}
        )
        control = f'<a class="p-button write" href="{escape(href)}">강좌신청</a>'
    return f"""
      <html lang="ko"><head><title>교육신청 - 철원군평생학습관</title></head>
      <body><table><caption>강좌상세</caption><tbody>
        <tr><td>{escape(heading)}</td></tr>{pair_rows}
      </tbody></table>{control}</body></html>
    """


class FixtureSite:
    def __init__(
        self,
        *,
        total_drift: bool = False,
        duplicate_identity: bool = False,
        unstable_page_one: bool = False,
        bad_sentinel: bool = False,
        clamped_sentinel: bool = False,
        active_drift: bool = False,
        missing_last: bool = False,
        year_outside_all: bool = False,
        year_signature_drift: bool = False,
        no_current: bool = False,
        detail_failure: bool = False,
        **detail_flags: bool,
    ) -> None:
        self.current = [] if no_current else _current_courses()
        self.historical = _historical_courses(201 - len(self.current))
        self.all_rows = self.current + self.historical
        self.by_id = {item.identity: item for item in self.all_rows}
        self.total_drift = total_drift
        self.duplicate_identity = duplicate_identity
        self.unstable_page_one = unstable_page_one
        self.bad_sentinel = bad_sentinel
        self.clamped_sentinel = clamped_sentinel
        self.active_drift = active_drift
        self.missing_last = missing_last
        self.year_outside_all = year_outside_all
        self.year_signature_drift = year_signature_drift
        self.no_current = no_current
        self.detail_failure = detail_failure
        self.detail_flags = detail_flags
        self.calls: dict[tuple[str, int], int] = {}
        self.detail_calls: list[str] = []
        self._lock = Lock()

    def _list(self, year: str, page: int) -> str:
        with self._lock:
            key = (year, page)
            self.calls[key] = self.calls.get(key, 0) + 1
            call_number = self.calls[key]
        source = list(self.current if year == "2026" else self.all_rows)
        if year and year != "2026":
            source = [item for item in self.all_rows if item.year == year]
        total = len(source)
        if self.year_outside_all and year == "2026" and source:
            source[0] = replace(source[0], identity="999999")
        if self.year_signature_drift and year == "2026" and source:
            source[0] = replace(source[0], title="분할에서 바뀐 제목")
        if self.duplicate_identity and not year and len(source) > 1:
            source[1] = replace(source[1], identity=source[0].identity)
        last = max(1, (total + 99) // 100)
        if self.clamped_sentinel and not year and page == last + 1:
            page = last
        start = (page - 1) * 100
        page_rows = source[start : start + 100]
        if self.unstable_page_one and not year and page == 1 and call_number > 1:
            page_rows[0] = replace(page_rows[0], title="불안정한 재조회 제목")
        shown_total = total
        if self.total_drift and not year and page == 2:
            shown_total += 1
        active = 1 if self.active_drift and not year and page == 2 else None
        return _list_html(
            page_rows,
            total=shown_total,
            page=page,
            year=year,
            active_page=active,
            wrong_sentinel=(self.bad_sentinel and not year and page == last + 1),
            omit_last=(self.missing_last and not year),
        )

    def fetch(self, _session, url: str, _timeout: int) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == cheorwon.CHEORWON_LIST_PATH:
            year = (query.get("year") or [""])[0]
            page = int((query.get("pageIndex") or ["1"])[0])
            return self._list(year, page)
        if parsed.path == cheorwon.CHEORWON_DETAIL_PATH:
            identity = (query.get("lctreNo") or [""])[0]
            with self._lock:
                self.detail_calls.append(identity)
            if self.detail_failure and identity == "9001":
                raise RuntimeError("simulated detail outage")
            item = self.by_id[identity]
            flags = {
                key: bool(value)
                and identity == ("9002" if key == "inactive_control" else "9001")
                for key, value in self.detail_flags.items()
            }
            return _detail_html(item, **flags)
        raise AssertionError(f"unexpected fixture URL {url}")


def _collect(site: FixtureSite, **kwargs):
    options = {
        "today": "2026-07-21",
        "max_pages": 20,
        "detail_limit": 10,
        "max_workers": 4,
        "session_factory": DummySession,
        "fetcher": site.fetch,
    }
    options.update(kwargs)
    return cheorwon.collect_cheorwon_education(_target(), **options)


def test_candidate_provider_and_source_boundaries_are_explicit() -> None:
    assert set(cheorwon.CHEORWON_CANDIDATE_AUDIT) == {
        "MUNI_IR_73F665EE43A9",
        "MUNI_IR_A649E6E29020",
        "MUNI_IR_B155FBDFE852",
        "MUNI_IR_D28C2FEC49A1",
    }
    assert (
        cheorwon.CHEORWON_CANDIDATE_AUDIT[
            cheorwon.CHEORWON_CANONICAL_CANDIDATE_ID
        ]["decision"]
        == "include_existing_owner_retarget_to_unfiltered_catalogue"
    )
    assert all(
        value["decision"].startswith("excluded")
        for key, value in cheorwon.CHEORWON_CANDIDATE_AUDIT.items()
        if key != cheorwon.CHEORWON_CANONICAL_CANDIDATE_ID
    )
    assert set(cheorwon.CHEORWON_PROVIDER_AUDIT) == {
        "MUNI_WWW_CWG_GO_KR_982AC30C",
        "MUNI_WWW_CWG_GO_KR_B360CE70",
        cheorwon.CHEORWON_PROVIDER,
        "MUNI_LIB_GWE_GO_KR_E49C8D9C",
        "MUNI_LAW_GO_KR_DBC95778",
    }
    assert cheorwon.CHEORWON_DISCOVERY_AUDIT["unfiltered_total"] == 1000
    assert cheorwon.CHEORWON_DISCOVERY_AUDIT["duplicate_source_identities"] == 0
    assert cheorwon.CHEORWON_DISCOVERY_AUDIT["current_or_future_rows"] == 157
    assert sum(
        cheorwon.CHEORWON_DISCOVERY_AUDIT["official_year_totals"].values()
    ) == 1000
    assert cheorwon.CHEORWON_REGISTERED_URL != cheorwon.CHEORWON_CANONICAL_URL
    assert cheorwon.is_cheorwon_education_target(_target())
    assert cheorwon.is_cheorwon_education_target(_target(canonical=True))
    assert cheorwon.is_cheorwon_separate_library_target(
        Target("library", cheorwon.CHEORWON_LIBRARY_PROGRAM_URL)
    )
    for candidate_id, url in (
        ("MUNI_IR_73F665EE43A9", cheorwon.CHEORWON_GENERAL_HOMEPAGE_URL),
        ("MUNI_IR_A649E6E29020", cheorwon.CHEORWON_ORDINANCE_URL),
        ("MUNI_IR_D28C2FEC49A1", cheorwon.CHEORWON_LIBRARY_MAIN_URL),
        ("", cheorwon.CHEORWON_ATTACHMENT_NOTICE_URL),
    ):
        assert cheorwon.is_cheorwon_excluded_candidate(
            Target("candidate", url, candidate_id)
        )


def test_url_builders_are_bounded_and_unfiltered() -> None:
    url = cheorwon.cheorwon_list_url(3, "2026")
    query = parse_qs(urlparse(url).query, keep_blank_values=True)
    assert query == {
        "key": ["692"],
        "insNo": [""],
        "semesterType": [""],
        "year": ["2026"],
        "lctreSe": [""],
        "lctreNm": [""],
        "lctreType": [""],
        "rceptTrgter": [""],
        "rceptSttus": [""],
        "lctreSttus": [""],
        "lctrePdBgnde": [""],
        "lctrePdEndde": [""],
        "pageUnit": ["100"],
        "pageIndex": ["3"],
    }
    assert cheorwon.cheorwon_list_url(0) == ""
    assert cheorwon.cheorwon_list_url(True) == ""
    assert cheorwon.cheorwon_list_url(1, "not-a-year") == ""
    assert cheorwon.cheorwon_detail_url("9001", "4").endswith(
        "key=692&insNo=4&lctreSe=&lctreNo=9001"
    )
    assert cheorwon.cheorwon_detail_url("9001", "99") == ""
    assert cheorwon.cheorwon_application_url("x", "3") == ""


def test_complete_snapshot_uses_all_pages_details_and_source_institutions() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)
    assert parser == cheorwon.CHEORWON_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["source_rows"] == 201
    assert meta["page_counts"] == {1: 100, 2: 100, 3: 1}
    assert meta["year_partition_totals"] == {"2026": 4}
    assert meta["list_requests"] == meta["required_list_requests"] == 8
    assert meta["sentinel_requests"] == 2
    assert meta["stability_rechecks"] == 2
    assert meta["identity_duplicate_count"] == 0
    assert meta["partition_identity_duplicate_count"] == 0
    assert meta["cancelled_partition_count"] == 1
    assert meta["detail_candidate_count"] == 3
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["expired_after_detail_count"] == 1
    assert meta["current_source_count"] == 2
    assert meta["returned_count"] == 2
    assert meta["public_application_control_count"] == 1
    assert meta["branch_counts"] == {
        "철원종합문화복지센터": 1,
        "철원평생학습관": 1,
    }
    assert set(site.detail_calls) == {"9001", "9002", "9003"}

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    assert set(by_id) == {"9001", "9002"}
    assert by_id["9001"]["branch"] == "철원종합문화복지센터"
    assert by_id["9001"]["branch_code"] == "cheorwon:4"
    assert by_id["9001"]["status"] == "OPEN"
    assert by_id["9001"]["reservation_available"] is True
    assert by_id["9001"]["application_type"] == "ONLINE_RESERVATION"
    assert parse_qs(urlparse(by_id["9001"]["application_url"]).query) == {
        "key": ["692"],
        "insNo": ["4"],
        "lctreNo": ["9001"],
    }
    assert by_id["9002"]["branch"] == "철원평생학습관"
    assert by_id["9002"]["status"] == "SCHEDULED"
    assert by_id["9002"]["application_url"] == ""
    assert by_id["9002"]["reservation_available"] is False
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["raw_fields"]["service_family"] == "education" for row in rows)
    payload = repr(rows)
    assert "033-450-5867" not in payload
    assert "김강사" not in payload
    assert "private-plan.pdf" not in payload
    assert "자유서술은 저장하면" not in payload
    assert meta["pii_payload_persisted"] is False
    assert meta["configured_collection_error"] == ""


def test_zero_current_partition_is_a_complete_no_data_snapshot() -> None:
    rows, _, meta = _collect(FixtureSite(no_current=True))
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] == 201
    assert meta["year_partition_totals"] == {"2026": 0}
    assert meta["detail_attempts"] == 0
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]


def test_blank_official_venue_is_preserved_without_invention() -> None:
    site = FixtureSite()
    site.current[0] = replace(site.current[0], venue="")
    site.all_rows[0] = site.current[0]
    site.by_id["9001"] = site.current[0]
    rows, _, meta = _collect(site)
    assert meta["snapshot_complete"] is True
    row = next(row for row in rows if row["raw_fields"]["identity"] == "9001")
    assert row["venue"] == ""
    assert row["raw_fields"]["source_venue"] == ""


@pytest.mark.parametrize(
    "flag,error_fragment",
    [
        ("total_drift", "total/last changed"),
        ("duplicate_identity", "duplicate unfiltered source identities"),
        ("unstable_page_one", "page-one stability recheck changed"),
        ("bad_sentinel", "empty sentinel changed"),
        ("clamped_sentinel", "empty sentinel changed"),
        ("active_drift", "active-page indicator changed"),
        ("missing_last", "advertised last-page link missing"),
        ("year_outside_all", "is absent from all-source"),
        ("year_signature_drift", "differs from all-source"),
    ],
)
def test_list_and_boundary_drift_fail_closed(
    flag: str, error_fragment: str
) -> None:
    rows, _, meta = _collect(FixtureSite(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "flag,error_fragment",
    [
        ("bad_period", "expected exactly two dates"),
        ("institution_mismatch", "institution/insNo binding changed"),
        ("title_mismatch", "heading status/title/code mismatch"),
        ("method_mismatch", "접수방법 list/detail mismatch"),
        ("missing_control", "open online application control changed"),
        ("control_identity_mismatch", "application control identity changed"),
        ("inactive_control", "inactive/offline course exposes application control"),
        ("field_drift", "detail field set changed"),
    ],
)
def test_detail_and_application_control_drift_fail_closed(
    flag: str, error_fragment: str
) -> None:
    rows, _, meta = _collect(FixtureSite(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


def test_fetch_failure_never_promotes_a_partial_snapshot() -> None:
    rows, _, meta = _collect(FixtureSite(detail_failure=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_errors"] > 0
    assert "simulated detail outage" in meta["configured_collection_error"]


def test_parallel_fetch_retries_transient_transport_failures() -> None:
    attempts = 0

    def fetcher(_session, _url: str, _timeout: int) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("transient timeout")
        return "<html><body>ok</body></html>"

    values, errors = cheorwon._fetch_parse_many(
        [("one", "https://www.cwg.go.kr/edu/selectLctreSearch.do", lambda soup: soup.body.get_text(strip=True))],
        fetcher=fetcher,
        session_factory=DummySession,
        timeout=1,
        max_workers=1,
    )

    assert values == {"one": "ok"}
    assert errors == []
    assert attempts == 3


def test_caps_wrong_owner_and_invalid_arguments_fail_before_persistence() -> None:
    rows, _, meta = _collect(FixtureSite(), max_pages=7)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "8 required list requests" in meta["configured_collection_error"]

    rows, _, meta = _collect(FixtureSite(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "3 required current/future-year details" in meta[
        "configured_collection_error"
    ]

    rows, _, meta = cheorwon.collect_cheorwon_education(
        Target("wrong", cheorwon.CHEORWON_REGISTERED_URL),
        fetcher=lambda *_: pytest.fail("wrong owner must not fetch"),
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "canonical Cheorwon owner" in meta["configured_collection_error"]

    rows, _, meta = cheorwon.collect_cheorwon_education(
        _target(), max_pages=True, fetcher=lambda *_: pytest.fail("must not fetch")
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "invalid timeout" in meta["configured_collection_error"]


def test_dedupe_and_post_dedupe_privacy_mutation_are_fail_closed() -> None:
    def drop_one(rows):
        return rows[:-1]

    rows, _, meta = _collect(FixtureSite(), dedupe_rows=drop_one)
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta[
        "configured_collection_error"
    ]

    def mutate(rows):
        rows[0]["phone"] = "010-1234-5678"
        return rows

    rows, _, meta = _collect(FixtureSite(), dedupe_rows=mutate)
    assert rows == []
    assert "forbidden PII" in meta["configured_collection_error"]


def test_embedded_intermediate_keeps_tls_verification_enabled() -> None:
    context = cheorwon._tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
