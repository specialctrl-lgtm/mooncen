from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from html import escape
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_hwacheon as hwacheon


@dataclass(frozen=True)
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    target: str
    apply_start: str
    apply_end: str
    operation: str
    current: int
    total: int
    status: str
    place: str
    category: str = "문화예술"
    method: str = "온라인"
    fee: str = "무료"
    waiting: int = 10


class DummySession:
    def close(self) -> None:
        pass


def _target() -> Target:
    return Target(hwacheon.HWACHEON_PROVIDER, hwacheon.HWACHEON_CANONICAL_URL)


def _current_courses() -> list[Course]:
    return [
        Course(
            "3001",
            "야간 라떼아트 자격증과정",
            "성인",
            "2026-07-20",
            "2026-07-31",
            "2026년 하반기 / 금 18:30~21:30",
            2,
            10,
            "모집중",
            "기타(산천어커피박물관)",
            "직업능력",
            fee="40,000원/재료비 180,000원",
            waiting=100,
        ),
        Course(
            "3002",
            "사내권역 여름 문화강좌",
            "청소년",
            "2026-07-01",
            "2026-07-10",
            "2026. 8. 1. ~ 2026. 12. 31. / 토 10:00~12:00",
            10,
            10,
            "운영대기",
            "사내종합문화센터",
        ),
        Course(
            "3003",
            "청소년수련관 연간 문화강좌",
            "청소년",
            "2025-12-12",
            "2025-12-20",
            "2026. 1. 6. ~ 2026. 12. 31. / 화 17:00~17:50",
            8,
            2,
            "수강중",
            "화천청소년수련관",
        ),
        Course(
            "3004",
            "취소된 미래 강좌",
            "성인",
            "2026-06-01",
            "2026-06-10",
            "2026. 9. 1. ~ 2026. 10. 31. / 수 10:00~12:00",
            0,
            10,
            "폐강",
            "화천평생학습관",
        ),
    ]


def _historical_courses(count: int) -> list[Course]:
    return [
        Course(
            str(2000 - index),
            f"완료 강좌 {index + 1}",
            "성인",
            "2024-01-01",
            "2024-01-10",
            "2024. 2. 1. ~ 2024. 3. 1. / 목 10:00~12:00",
            index % 10,
            10,
            "수강완료",
            "화천평생학습관",
        )
        for index in range(count)
    ]


def _form(page: int, from_date: str, to_date: str, *, drift: bool = False) -> str:
    rendered_from = "" if drift else from_date
    return f"""
      <form id="searchForm" method="post">
        <input name="mode" value="LIST">
        <input name="pageIndex" value="{page}">
        <input name="lectureSeq" value="0">
        <select name="target"><option value="" selected>전체</option></select>
        <select name="classification"><option value="" selected>전체</option></select>
        <select name="place"><option value="" selected>전체</option></select>
        <input name="searchFromDate" value="{escape(rendered_from)}">
        <input name="searchToDate" value="{escape(to_date)}">
        <input name="receiptStartDate" value="">
        <input name="receiptEndDate" value="">
        <input name="searchKeyword" value="">
        <input type="checkbox" name="searchStatus" value="RECRUIT">
        <input type="checkbox" name="searchStatus" value="RECEIPT">
        <input type="checkbox" name="searchStatus" value="TAKING">
        <input type="checkbox" name="searchStatus" value="COMPLETE">
        <input type="checkbox" name="searchStatus" value="OFFLINE">
      </form>
    """


def _list_row(item: Course, number: int) -> str:
    return f"""
      <tr data-lecture-seq="{item.identity}">
        <td>{number}</td><td>{escape(item.target)}</td>
        <td><a class="btn-view" href="#">{escape(item.title)}</a></td>
        <td>{item.apply_start}~ {item.apply_end}</td>
        <td>{escape(item.operation)}</td><td>{item.current} / {item.total}</td>
        <td><a class="btn-view" href="#">{item.status}</a></td>
      </tr>
    """


def _list_html(
    page_rows: list[tuple[Course, int]],
    *,
    total: int,
    page: int,
    from_date: str,
    to_date: str,
    active_page: int | None = None,
    bad_sentinel: bool = False,
    form_drift: bool = False,
) -> str:
    last = max(1, (total + 9) // 10)
    rows = "".join(_list_row(item, number) for item, number in page_rows)
    if not rows and not bad_sentinel:
        rows = '<tr><td colspan="7">등록된 강좌가 없습니다.</td></tr>'
    active = ""
    if page_rows:
        active = (
            f'<a class="pager-link active">'
            f"{active_page if active_page is not None else page}</a>"
        )
    headers = "".join(f"<th>{value}</th>" for value in hwacheon._LIST_HEADERS)
    return f"""
      <html><head><title>강좌목록 | 화천군평생교육 &gt; 수강신청 &gt; 강좌목록</title></head>
      <body>{_form(page, from_date, to_date, drift=form_drift)}
        <table class="skinTb width768">
          <caption>{hwacheon._LIST_CAPTION}</caption>
          <thead><tr>{headers}</tr></thead><tbody>{rows}</tbody>
        </table><span class="pager-num">{page}/{last}</span>{active}
      </body></html>
    """


def _detail_html(
    item: Course,
    *,
    title_mismatch: bool = False,
    target_mismatch: bool = False,
    application_mismatch: bool = False,
    capacity_mismatch: bool = False,
    status_mismatch: bool = False,
    missing_control: bool = False,
    inactive_control: bool = False,
    malformed_control: bool = False,
    identity_mismatch: bool = False,
    missing_login_gate: bool = False,
    field_drift: bool = False,
) -> str:
    title = "다른 강좌" if title_mismatch else item.title
    target = "다른 대상" if target_mismatch else item.target
    apply_end = "2026-08-01" if application_mismatch else item.apply_end
    current = item.current + 1 if capacity_mismatch else item.current
    state = "수강완료" if status_mismatch else item.status
    first = [
        ("강좌명", title),
        ("교육대상", target),
        ("강좌분야", item.category),
        ("교육장소", item.place),
        ("운영기간", item.operation),
        ("수강료", item.fee),
        ("유의사항", "개인 사정과 신청 정보를 쓰지 마세요."),
        ("문의", "033-440-2143"),
    ]
    second = [
        ("접수방법", item.method),
        (
            "접수기간",
            f'{item.apply_start} 10:00 ~ {apply_end} 17:00 '
            f'<span class="stateBtn">{state}</span>',
        ),
        (
            "모집인원",
            f"접수인원 {current}명 / 모집 {item.total}명 / 대기 {item.waiting}명",
        ),
        ("문의", "033-440-2143"),
    ]
    third = [
        ("강사명", "김강사"),
        ("강의내용", "상세한 비공개 강의 설명"),
        ("강의계획서", "private-plan.pdf"),
    ]
    if field_drift:
        third.pop()

    def group(values: list[tuple[str, str]]) -> str:
        body = "".join(
            f'<div class="skinTb-tr"><div class="skinTb-th">{key}</div>'
            f'<div class="skinTb-td">{value}</div></div>'
            for key, value in values
        )
        return f'<div class="info_list"><div class="skinTb">{body}</div></div>'

    show_control = not missing_control
    control = ""
    if show_control:
        href = "/wrong" if malformed_control else "#"
        control = f'<a class="v5 application-btn" href="{href}">수강신청</a>'
        if inactive_control:
            control += '<a class="v5 application-btn" href="#">수강신청</a>'
    script = ""
    if not missing_login_gate:
        script = (
            '<script>$(".application-btn").click(function(){'
            'location.href="/portal/service/login";});</script>'
        )
    bound = "9999" if identity_mismatch else item.identity
    return f"""
      <html><head><title>강좌보기 | 화천군평생교육 &gt; 수강신청 &gt; 강좌보기</title></head>
      <body><form id="lectureViewForm" method="post">
        <input name="lectureSeq" value="{bound}">
      </form>{group(first)}{group(second)}{group(third)}{control}{script}</body></html>
    """


class FixtureSite:
    def __init__(
        self,
        *,
        no_current: bool = False,
        total_drift: bool = False,
        duplicate_identity: bool = False,
        unstable_page_one: bool = False,
        bad_sentinel: bool = False,
        clamped_sentinel: bool = False,
        active_drift: bool = False,
        current_outside_all: bool = False,
        current_signature_drift: bool = False,
        current_number_drift: bool = False,
        form_drift: bool = False,
        detail_failure: bool = False,
        blank_place: bool = False,
        **detail_flags: bool,
    ) -> None:
        self.current = [] if no_current else _current_courses()
        if blank_place and self.current:
            self.current[0] = replace(self.current[0], place="")
        self.historical = _historical_courses(21 - len(self.current))
        self.all_rows = self.current + self.historical
        self.by_id = {item.identity: item for item in self.all_rows}
        self.total_drift = total_drift
        self.duplicate_identity = duplicate_identity
        self.unstable_page_one = unstable_page_one
        self.bad_sentinel = bad_sentinel
        self.clamped_sentinel = clamped_sentinel
        self.active_drift = active_drift
        self.current_outside_all = current_outside_all
        self.current_signature_drift = current_signature_drift
        self.current_number_drift = current_number_drift
        self.form_drift = form_drift
        self.detail_failure = detail_failure
        self.detail_flags = detail_flags
        self.calls: dict[tuple[bool, int], int] = {}
        self.detail_calls: list[str] = []
        self._lock = Lock()

    def _list(self, current: bool, requested_page: int) -> str:
        with self._lock:
            key = (current, requested_page)
            self.calls[key] = self.calls.get(key, 0) + 1
            call_number = self.calls[key]
        source = list(self.current if current else self.all_rows)
        if self.current_outside_all and current and source:
            source[0] = replace(source[0], identity="9999")
        if self.current_signature_drift and current and source:
            source[0] = replace(source[0], title="필터에서 바뀐 제목")
        if self.duplicate_identity and not current and len(source) > 5:
            source[5] = replace(source[5], identity=source[4].identity)
        if self.unstable_page_one and not current and requested_page == 1 and call_number > 1:
            source[0] = replace(source[0], title="불안정한 재확인 제목")
        total = len(source)
        last = max(1, (total + 9) // 10)
        shown_page = last if self.clamped_sentinel and requested_page == last + 1 else requested_page
        start = (shown_page - 1) * 10
        items = source[start : start + 10]
        numbers = [total - start - index for index in range(len(items))]
        if self.current_number_drift and current and shown_page == 1 and numbers:
            numbers[0] += 1
        page_rows = list(zip(items, numbers))
        shown_total = total + int(self.total_drift and not current and shown_page == 2)
        if shown_total != total and page_rows:
            page_rows = [
                (item, number + shown_total - total) for item, number in page_rows
            ]
        from_date = "2026-07-21" if current else ""
        to_date = hwacheon.HWACHEON_FILTER_END if current else ""
        if self.bad_sentinel and requested_page == last + 1:
            fake = _historical_courses(1)[0]
            page_rows = [(fake, 1)]
        return _list_html(
            page_rows,
            total=shown_total,
            page=shown_page,
            from_date=from_date,
            to_date=to_date,
            active_page=(1 if self.active_drift and shown_page == 2 else None),
            bad_sentinel=bool(page_rows),
            form_drift=(self.form_drift and current),
        )

    def fetch(self, _session, url: str, payload, _timeout: int) -> str:
        parsed = urlparse(url)
        if parsed.path == hwacheon.HWACHEON_LIST_PATH:
            assert payload is not None
            current = bool(payload.get("searchFromDate"))
            page = int(payload["pageIndex"])
            return self._list(current, page)
        if parsed.path == hwacheon.HWACHEON_DETAIL_PATH:
            assert payload is None
            identity = (parse_qs(parsed.query).get("lectureSeq") or [""])[0]
            with self._lock:
                self.detail_calls.append(identity)
            if self.detail_failure and identity == "3001":
                raise RuntimeError("simulated detail outage")
            item = self.by_id[identity]
            flags = {
                name: bool(enabled)
                and identity == ("3002" if name == "inactive_control" else "3001")
                for name, enabled in self.detail_flags.items()
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
    return hwacheon.collect_hwacheon_education(_target(), **options)


def test_candidate_provider_and_separate_library_boundaries_are_explicit() -> None:
    assert set(hwacheon.HWACHEON_CANDIDATE_AUDIT) == {
        "MUNI_IR_9500D672F3F2",
        "MUNI_IR_CFFAAE7FD965",
        "MUNI_IR_F6578EDB38BF",
        "MUNI_IR_DB2B3B149783",
        "MUNI_IR_60F5B84EA67B",
        "MUNI_IR_1280E2FAFC4B",
    }
    assert (
        hwacheon.HWACHEON_CANDIDATE_AUDIT[
            hwacheon.HWACHEON_CANONICAL_CANDIDATE_ID
        ]["decision"]
        == "include_new_official_complete_catalogue_owner"
    )
    assert all(
        value["decision"].startswith("excluded")
        for key, value in hwacheon.HWACHEON_CANDIDATE_AUDIT.items()
        if key != hwacheon.HWACHEON_CANONICAL_CANDIDATE_ID
    )
    assert hwacheon.HWACHEON_DISCOVERY_AUDIT["unfiltered_total"] == 1165
    assert hwacheon.HWACHEON_DISCOVERY_AUDIT["duplicate_source_identities"] == 0
    assert hwacheon.HWACHEON_DISCOVERY_AUDIT["official_overlap_total"] == 211
    assert hwacheon.is_hwacheon_education_target(_target())
    assert hwacheon.is_hwacheon_separate_library_target(
        Target("library", hwacheon.HWACHEON_LIBRARY_PROGRAM_URL)
    )
    for candidate_id, url in (
        ("MUNI_IR_CFFAAE7FD965", hwacheon.HWACHEON_SPORT_NOTICE_URL),
        ("MUNI_IR_F6578EDB38BF", hwacheon.HWACHEON_NEWS_NOTICE_URL),
        ("MUNI_IR_DB2B3B149783", hwacheon.HWACHEON_LIBRARY_MAIN_URL),
        ("MUNI_IR_60F5B84EA67B", hwacheon.HWACHEON_GENERAL_HOMEPAGE_URL),
        ("MUNI_IR_1280E2FAFC4B", hwacheon.HWACHEON_YOUTH_NOTICE_URL),
    ):
        assert hwacheon.is_hwacheon_excluded_candidate(
            Target("candidate", url, candidate_id)
        )


def test_payload_and_detail_url_builders_are_bounded_and_unfiltered() -> None:
    assert hwacheon.hwacheon_list_payload(2) == {
        "mode": "LIST",
        "pageIndex": "2",
        "lectureSeq": "0",
        "target": "",
        "classification": "",
        "place": "",
        "searchFromDate": "",
        "searchToDate": "",
        "receiptStartDate": "",
        "receiptEndDate": "",
        "searchKeyword": "",
    }
    current = hwacheon.hwacheon_list_payload(
        3,
        search_from_date="2026-07-21",
        search_to_date=hwacheon.HWACHEON_FILTER_END,
    )
    assert current["pageIndex"] == "3"
    assert current["searchFromDate"] == "2026-07-21"
    assert current["searchToDate"] == "9999-12-31"
    assert hwacheon.hwacheon_list_payload(0) == {}
    assert hwacheon.hwacheon_list_payload(True) == {}
    assert hwacheon.hwacheon_list_payload(
        1, search_from_date="2026-07-21"
    ) == {}
    assert hwacheon.hwacheon_detail_url("3001").endswith("lectureSeq=3001")
    assert hwacheon.hwacheon_detail_url("bad") == ""


def test_complete_snapshot_uses_both_full_lists_details_and_source_places() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)
    assert parser == hwacheon.HWACHEON_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["source_rows"] == 21
    assert meta["page_counts"] == {1: 10, 2: 10, 3: 1}
    assert meta["current_filter_rows"] == 4
    assert meta["current_filter_page_counts"] == {1: 4}
    assert meta["list_requests"] == meta["required_list_requests"] == 8
    assert meta["sentinel_requests"] == 2
    assert meta["stability_rechecks"] == 2
    assert meta["identity_duplicate_count"] == 0
    assert meta["partition_identity_duplicate_count"] == 0
    assert meta["detail_attempts"] == meta["detail_pages"] == 4
    assert meta["cancelled_current_filter_count"] == 1
    assert meta["current_source_count"] == 3
    assert meta["returned_count"] == 3
    assert meta["public_application_control_count"] == 3
    assert meta["actionable_application_control_count"] == 1
    assert meta["branch_counts"] == {
        "기타(산천어커피박물관)": 1,
        "사내종합문화센터": 1,
        "화천청소년수련관": 1,
    }
    assert meta["institution_counts"] == {
        "기타": 1,
        "사내종합문화센터": 1,
        "화천청소년수련관": 1,
    }
    assert set(site.detail_calls) == {"3001", "3002", "3003", "3004"}

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    assert set(by_id) == {"3001", "3002", "3003"}
    assert by_id["3001"]["branch"] == "기타(산천어커피박물관)"
    assert by_id["3001"]["provider_organizer"] == "기타"
    assert by_id["3001"]["raw_fields"]["education_institution"] == "기타"
    assert by_id["3001"]["status"] == "OPEN"
    assert by_id["3001"]["reservation_available"] is True
    assert by_id["3001"]["application_type"] == "ONLINE_RESERVATION"
    assert parse_qs(urlparse(by_id["3001"]["application_url"]).query) == {
        "lectureSeq": ["3001"]
    }
    assert by_id["3001"]["start_date"] == ""
    assert by_id["3001"]["end_date"] == ""
    assert by_id["3002"]["start_date"] == "2026-08-01"
    assert by_id["3002"]["end_date"] == "2026-12-31"
    assert by_id["3002"]["status"] == "CLOSED"
    assert by_id["3002"]["reservation_available"] is False
    assert by_id["3002"]["application_url"] == ""
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["raw_fields"]["service_family"] == "education" for row in rows)
    assert all(row["municipality_code"] == "5179000000" for row in rows)
    payload = repr(rows)
    assert "033-440-2143" not in payload
    assert "김강사" not in payload
    assert "private-plan.pdf" not in payload
    assert "상세한 비공개 강의 설명" not in payload
    assert meta["pii_payload_persisted"] is False
    assert meta["configured_collection_error"] == ""


def test_zero_current_filter_is_a_complete_no_data_snapshot() -> None:
    rows, _, meta = _collect(FixtureSite(no_current=True))
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] == 21
    assert meta["current_filter_rows"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]


def test_blank_official_place_is_preserved_without_invention() -> None:
    rows, _, meta = _collect(FixtureSite(blank_place=True))
    assert meta["snapshot_complete"] is True
    row = next(row for row in rows if row["raw_fields"]["identity"] == "3001")
    assert row["branch"] == ""
    assert row["venue"] == ""
    assert row["raw_fields"]["source_place"] == ""


@pytest.mark.parametrize(
    "flag,error_fragment",
    [
        ("total_drift", "total/last changed"),
        ("duplicate_identity", "duplicate unfiltered source identities"),
        ("unstable_page_one", "page-one stability recheck changed"),
        ("bad_sentinel", "immediate empty sentinel changed"),
        ("clamped_sentinel", "immediate empty sentinel changed"),
        ("active_drift", "active-page indicator changed"),
        ("current_outside_all", "is absent from all-source"),
        ("current_signature_drift", "differs from all-source"),
        ("current_number_drift", "list number sequence changed"),
        ("form_drift", "form field searchFromDate changed"),
    ],
)
def test_list_filter_and_boundary_drift_fail_closed(
    flag: str, error_fragment: str
) -> None:
    rows, _, meta = _collect(FixtureSite(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "flag,error_fragment",
    [
        ("title_mismatch", "list/detail title mismatch"),
        ("target_mismatch", "list/detail target mismatch"),
        ("application_mismatch", "list/detail application dates mismatch"),
        ("capacity_mismatch", "list/detail capacity mismatch"),
        ("status_mismatch", "list/detail public status mismatch"),
        ("missing_control", "course-bound application control count changed"),
        ("inactive_control", "course-bound application control count changed"),
        ("malformed_control", "application control changed"),
        ("identity_mismatch", "detail form course identity changed"),
        ("missing_login_gate", "public login application gate changed"),
        ("field_drift", "field set/order changed"),
    ],
)
def test_detail_and_application_contract_drift_fail_closed(
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


def test_caps_wrong_owner_and_invalid_arguments_fail_before_persistence() -> None:
    rows, _, meta = _collect(FixtureSite(), max_pages=7)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "8 required list requests" in meta["configured_collection_error"]

    rows, _, meta = _collect(FixtureSite(), detail_limit=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "4 required current-filter details" in meta["configured_collection_error"]

    rows, _, meta = hwacheon.collect_hwacheon_education(
        Target("wrong", hwacheon.HWACHEON_CANONICAL_URL),
        fetcher=lambda *_: pytest.fail("wrong owner must not fetch"),
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "canonical Hwacheon owner" in meta["configured_collection_error"]

    rows, _, meta = hwacheon.collect_hwacheon_education(
        _target(), max_pages=True, fetcher=lambda *_: pytest.fail("must not fetch")
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "invalid timeout" in meta["configured_collection_error"]


def test_dedupe_and_post_dedupe_privacy_mutation_are_fail_closed() -> None:
    rows, _, meta = _collect(FixtureSite(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta[
        "configured_collection_error"
    ]

    def mutate(values):
        values[0]["phone"] = "010-1234-5678"
        return values

    rows, _, meta = _collect(FixtureSite(), dedupe_rows=mutate)
    assert rows == []
    assert "forbidden PII" in meta["configured_collection_error"]


def test_default_transport_keeps_tls_verification_enabled() -> None:
    session = hwacheon._default_session_factory()
    try:
        assert session.verify is True
    finally:
        session.close()


def test_cutoff_accepts_date_and_rejects_invalid_iso_value() -> None:
    rows, _, meta = _collect(FixtureSite(), today=date(2026, 7, 21))
    assert len(rows) == 3
    assert meta["official_filter_from"] == "2026-07-21"

    rows, _, meta = _collect(FixtureSite(), today="not-a-date")
    assert rows == []
    assert "today must be an ISO date" in meta["configured_collection_error"]
