from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_pyeongchang as pyeongchang


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    start: str = "2026-01-01"
    end: str = "2026-01-31"
    apply_start: str = "2025-12-01"
    apply_end: str = "2025-12-31"
    status: str = "교육마감"
    agency: str = "평창군청"
    category: str = "문화예술"
    schedule: str = "매주 화요일 10:00~12:00"
    target: str = "평창군민"
    venue: str = "평창군 평생학습관"
    region: str = "전지역"
    method: str = "온라인예약"
    fee: str = "무료"
    capacity_total: int = 20
    capacity_current: int = 3
    waiting_current: int = 0


@dataclass(frozen=True)
class ExternalCourse:
    title: str = pyeongchang.PYEONGCHANG_EXTERNAL_LIBRARY_TITLE
    start: str = "2026-03-03"
    end: str = "2026-05-13"
    apply_start: str = "2026-02-10"
    apply_end: str = "2026-02-20"
    status: str = "교육마감"
    agency: str = "평창교육도서관"
    target: str = "성인"
    capacity_total: int = 12
    capacity_current: int = 12
    waiting_current: int = 0


class DummySession:
    def __init__(self) -> None:
        self.current_course = ""

    def close(self) -> None:
        return None


def _target() -> Target:
    return Target(
        pyeongchang.PYEONGCHANG_PROVIDER,
        pyeongchang.PYEONGCHANG_CANONICAL_URL,
        pyeongchang.PYEONGCHANG_CANONICAL_CANDIDATE_ID,
    )


def _courses(*, current: bool = True) -> list[Course | ExternalCourse]:
    rows: list[Course | ExternalCourse] = [ExternalCourse()]
    rows.extend(
        Course(f"GJLI{number:04d}", f"과거 평생학습 강좌 {number}")
        for number in range(438, 0, -1)
    )
    if current:
        rows[1] = Course(
            "GJLI0438",
            "현재 평생학습 강좌",
            start="2026-08-01",
            end="2026-08-31",
            apply_start="2026-07-01",
            apply_end="2026-07-31",
            status="접수중",
        )
    return rows


def _form(page: int, *, filter_drift: bool = False) -> str:
    all_controls = "".join(
        f'<input type="checkbox" name="{name}" value="all"'
        f'{"" if filter_drift and name == "allStatus" else " checked"}>'
        for name in ("allField", "allTarget", "allAgency", "allArea", "allStatus")
    )
    item_controls = "".join(
        f'<input type="checkbox" name="{name}" value="x">'
        for name in ("fieldList", "targetList", "agencyList", "areaList", "statusList")
    )
    return f"""
      <form id="eduCourseForm" name="eduCourseForm" method="post"
            action="{pyeongchang.PYEONGCHANG_LIST_PATH}">
        <input name="pageIndex" value="{page}">
        <input name="searchCondition" value="LECTURE_NAME">
        <input name="pageViewType" value="list">
        <input name="courseNo" value="">
        <input name="mode" value="">
        <input name="searchKeyword" value="">
        <input name="studyStartDate" value="">
        <input name="studyEndDate" value="">
        {all_controls}{item_controls}
      </form>
    """


def _list_row(item: Course | ExternalCourse, number: int, *, unknown_external: bool = False) -> str:
    if isinstance(item, ExternalCourse):
        href = "https://evil.example/courses" if unknown_external else pyeongchang.PYEONGCHANG_EXTERNAL_LIBRARY_URL
        link = (
            f'<a href="{escape(href)}" target="_blank">'
            f'<em class="lecture-site-name">평생학습관 &gt; {escape(item.agency)}</em>'
            f'<b>{escape(item.title)}</b></a>'
        )
    else:
        link = (
            f'<a href="javascript:fnView(\'{item.identity}\');">'
            f'<em class="lecture-site-name">평생학습관 &gt; {escape(item.agency)}</em>'
            f'<b>{escape(item.title)}</b></a>'
        )
    return f"""
      <tr><td>{number}</td><td>{link}
        <em class="lecture-info lecture-target"><span>대상</span>{escape(item.target)}</em>
        <em class="lecture-info lecture-accept-date"><span>접수기간</span>{item.apply_start} ~ {item.apply_end}</em>
        <em class="lecture-info lecture-accept-close"><span>접수마감</span>{item.apply_end}</em>
        <em class="lecture-info lecture-capacity"><span>정원/모집/대기</span>{item.capacity_total}명/{item.capacity_current}명/{item.waiting_current}명</em>
        <em class="lecture-info lecture-education-date"><span>교육기간</span>{item.start} ~ {item.end}</em>
      </td><td><span class="state-bx">{item.status}</span></td></tr>
    """


def _list_html(
    page: int,
    rows: list[Course | ExternalCourse],
    *,
    filter_drift: bool = False,
    total_drift: bool = False,
    unknown_external: bool = False,
    bad_sentinel: bool = False,
) -> str:
    total = len(rows) - (1 if total_drift else 0)
    last = max(1, (total + pyeongchang.PYEONGCHANG_PAGE_SIZE - 1) // pyeongchang.PYEONGCHANG_PAGE_SIZE)
    start = (page - 1) * pyeongchang.PYEONGCHANG_PAGE_SIZE
    page_rows = rows[start : start + pyeongchang.PYEONGCHANG_PAGE_SIZE]
    if page_rows:
        body = "".join(
            _list_row(
                item,
                len(rows) - start - offset,
                unknown_external=unknown_external and start + offset == 0,
            )
            for offset, item in enumerate(page_rows)
        )
        pager = f'<a class="pager-link active" onclick="return false;">{page}</a>'
    else:
        body = (
            ""
            if bad_sentinel
            else '<tr><td colspan="3">등록된 내용이 없습니다.</td></tr>'
        )
        pager = ""
    return f"""
      <html><head><title>일반강좌정보 - 목록 | 평창군 평생학습관 &gt; 강좌신청 &gt; 강좌정보 &gt; 일반강좌정보</title></head>
      <body>{_form(page, filter_drift=filter_drift)}
        <p>총 {total} 건의 강좌가 있습니다. ({page}/{last}페이지)</p>
        <div class="lecture-list"><table class="skinTb width1000"><tbody>{body}</tbody></table></div>
        {pager}
      </body></html>
    """


def _hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{name}" value="{escape(value)}">'


def _detail_html(
    course: Course,
    *,
    agency: str | None = None,
    missing_control: bool = False,
) -> str:
    control = ""
    if course.status == "접수중" and not missing_control:
        control = '<a class="btn-education-applicant" href="javascript:;">신청하기</a>'
    fields = (
        ("주관기관", agency if agency is not None else course.agency),
        ("교육기간", f"{course.start} ~ {course.end}"),
        ("분야", course.category),
        ("교육시간", course.schedule),
        ("교육대상", course.target),
        ("교육장소", course.venue),
        ("지역", course.region),
        ("접수방법", course.method),
        ("강사명", "개인 강사"),
        ("접수기간", f"{course.apply_start} 09:00:00 ~ {course.apply_end} 18:00:00"),
        ("수강료", course.fee),
        ("접수현황", f"{course.capacity_total}명/{course.capacity_current}명/{course.waiting_current}명"),
        ("전화번호", "033-123-4567"),
    )
    pairs = "".join(f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in fields)
    form_fields = "".join(
        _hidden(name, value)
        for name, value in (
            ("pageIndex", "1"),
            ("searchCondition", "LECTURE_NAME"),
            ("searchKeyword", ""),
            ("courseNo", course.identity),
            ("pageViewType", "list"),
            ("studyStartDate", ""),
            ("studyEndDate", ""),
            ("mode", "list"),
        )
    )
    return f"""
      <html><head><title>일반강좌정보 - 상세 | 평창군 평생학습관 &gt; 강좌신청 &gt; 강좌정보 &gt; 일반강좌정보</title></head>
      <body>
        <form id="viewForm" name="viewForm" method="post" action="{pyeongchang.PYEONGCHANG_LIST_PATH}">{form_fields}</form>
        <div id="contentsArea"><div class="class-title-bx">
          <h4>{escape(course.title)}<span class="state-bx">{course.status}</span></h4>{control}
        </div><dl class="list-dl1 v2">{pairs}</dl>
        <div class="description">담당자 private@example.org / 010-1234-5678</div>
        <a href="/private.pdf">첨부파일</a></div>
        <script>$(".btn-education-applicant").click(function(event){{
          const form = document.getElementById("viewForm"); form.mode.value = "form"; form.submit();
        }});</script>
      </body></html>
    """


def _application_html(*, bad_frame: bool = False) -> str:
    frame = "/wrong" if bad_frame else (
        f"{pyeongchang.PYEONGCHANG_AUTH_PATH}?"
        f"menuPath={pyeongchang.PYEONGCHANG_AUTH_MENU_PATH}&amp;mode=form"
    )
    return f"""
      <html><head><title>일반강좌정보 본인인증 | 평창군 평생학습관 &gt; 강좌신청 &gt; 강좌정보 &gt; 일반강좌정보</title></head>
      <body><div id="contentsArea"><iframe class="confirmIframe" src="{frame}"></iframe></div></body></html>
    """


def _auth_html(identity: str, *, identity_mismatch: bool = False) -> str:
    bound = "GJLI9999" if identity_mismatch else identity
    return f"""
      <html><body><p>휴대폰 본인인증</p>
        <form id="nextForm" name="nextForm" method="post" action="{pyeongchang.PYEONGCHANG_LIST_PATH}">
          {_hidden("courseNo", bound)}
        </form>
        <form name="reqPCCForm"><input name="reqInfo" value="encrypted-secret-token"></form>
        <a class="btn-self-certification">인증하기</a>
      </body></html>
    """


class FixtureSite:
    def __init__(self, *, current: bool = True, **flags: bool) -> None:
        self.rows = _courses(current=current)
        self.by_id = {
            item.identity: item for item in self.rows if isinstance(item, Course)
        }
        self.flags = flags
        self.calls: Counter[str] = Counter()
        self._page_one_calls = 0
        self._lock = Lock()

    def __call__(self, session: DummySession, url: str, timeout: int) -> str:
        assert timeout > 0
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        mode = query.get("mode", [""])[0]
        if parsed.path == pyeongchang.PYEONGCHANG_AUTH_PATH:
            self.calls["auth"] += 1
            return _auth_html(
                session.current_course,
                identity_mismatch=self.flags.get("auth_identity_mismatch", False),
            )
        if mode == "view":
            identity = query["courseNo"][0]
            self.calls["detail"] += 1
            course = self.by_id[identity]
            return _detail_html(
                course,
                agency="다른 기관" if self.flags.get("detail_agency_mismatch") else None,
                missing_control=self.flags.get("missing_control", False),
            )
        if mode == "form":
            identity = query["courseNo"][0]
            session.current_course = identity
            self.calls["application"] += 1
            return _application_html(bad_frame=self.flags.get("bad_frame", False))

        page = int(query.get("pageIndex", ["1"])[0])
        with self._lock:
            self.calls[f"list:{page}"] += 1
            if page == 1:
                self._page_one_calls += 1
                page_one_call = self._page_one_calls
            else:
                page_one_call = 0
        rows = self.rows
        if self.flags.get("duplicate_identity"):
            rows = list(rows)
            rows[2] = replace(rows[2], identity=rows[1].identity)  # type: ignore[arg-type]
        if self.flags.get("unstable_page_one") and page_one_call > 1:
            rows = list(rows)
            rows[0] = replace(rows[0], title="변경된 외부 강좌")  # type: ignore[arg-type]
        return _list_html(
            page,
            rows,
            filter_drift=self.flags.get("filter_drift", False) and page == 1,
            total_drift=self.flags.get("total_drift", False) and page == 2,
            unknown_external=self.flags.get("unknown_external", False),
            bad_sentinel=self.flags.get("bad_sentinel", False) and page == 45,
        )


def _collect(site: FixtureSite, **kwargs):
    return pyeongchang.collect_pyeongchang_education(
        _target(),
        today="2026-07-21",
        max_workers=1,
        session_factory=DummySession,
        fetcher=site,
        **kwargs,
    )


def test_target_and_candidate_boundaries_are_exact() -> None:
    assert pyeongchang.is_pyeongchang_education_target(_target())
    assert not pyeongchang.is_pyeongchang_education_target(
        Target(pyeongchang.PYEONGCHANG_PROVIDER, "https://pc.go.kr/pcedu")
    )
    for candidate_id, audit in pyeongchang.PYEONGCHANG_CANDIDATE_AUDIT.items():
        target = Target(str(audit["provider"]), str(audit["url"]), candidate_id)
        assert pyeongchang.is_pyeongchang_excluded_candidate(target)
        assert not pyeongchang.is_pyeongchang_education_target(target)
    assert pyeongchang.is_pyeongchang_discovery_alias_target(
        Target("anything", pyeongchang.PYEONGCHANG_DISCOVERY_ALIAS_URLS[0])
    )
    assert pyeongchang.is_pyeongchang_separate_facility_target(
        Target("anything", pyeongchang.PYEONGCHANG_DEPRECATED_FACILITY_URL)
    )


def test_url_builders_reject_unsafe_or_invalid_identity() -> None:
    assert pyeongchang.pyeongchang_list_url(1) == pyeongchang.PYEONGCHANG_CANONICAL_URL
    assert "pageIndex=2" in pyeongchang.pyeongchang_list_url(2)
    assert "courseNo=GJLI0438" in pyeongchang.pyeongchang_detail_url("GJLI0438")
    assert "mode=form" in pyeongchang.pyeongchang_application_url("GJLI0438")
    assert not pyeongchang.pyeongchang_list_url(True)
    assert not pyeongchang.pyeongchang_detail_url("../../etc/passwd")
    assert pyeongchang._split_site("평생학습관") == (
        "평생학습관",
        "평생학습관",
    )
    with pytest.raises(pyeongchang.PyeongchangContractError):
        pyeongchang._split_site("다른 포털")


def test_external_library_boundary_uses_verified_owner_not_moving_row_number() -> None:
    soup = pyeongchang._coerce_soup(
        f"<table><tbody>{_list_row(ExternalCourse(), 440)}</tbody></table>"
    )
    parsed = pyeongchang._parse_list_row(
        soup.select_one("tr"),
        page=13,
        expected_number=440,
        cutoff=pyeongchang._today("2026-07-29"),
    )

    assert parsed["external_reference"] is True
    assert parsed["source_row_number"] == 440
    assert parsed["raw_url"] == pyeongchang.PYEONGCHANG_EXTERNAL_LIBRARY_URL


def test_complete_snapshot_traverses_boundary_detail_and_course_bound_auth() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == pyeongchang.PYEONGCHANG_PARSER
    assert len(rows) == 1
    assert rows[0]["provider_course_id"].endswith(":GJLI0438")
    assert rows[0]["application_type"] == "ONLINE_RESERVATION"
    assert rows[0]["reservation_available"] is True
    assert rows[0]["raw_fields"]["detail_verified"] is True
    assert rows[0]["raw_fields"]["application_control_verified"] is True
    assert rows[0]["raw_fields"]["mobile_auth_verified"] is True
    assert meta["source_rows"] == 439
    assert meta["internal_source_count"] == 438
    assert meta["external_reference_count"] == 1
    assert meta["external_current_count"] == 0
    assert meta["list_requests"] == meta["required_list_requests"] == 46
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 1
    assert meta["detail_attempts"] == meta["detail_pages"] == 1
    assert meta["auth_attempts"] == meta["auth_verified"] == 1
    assert meta["auth_pages"] == 2
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert site.calls["list:45"] == 1
    assert site.calls["list:1"] == 2


def test_pii_and_auth_tokens_are_not_persisted() -> None:
    rows, _, meta = _collect(FixtureSite())
    payload = repr(rows)
    assert "private@example.org" not in payload
    assert "010-1234-5678" not in payload
    assert "033-123-4567" not in payload
    assert "개인 강사" not in payload
    assert "encrypted-secret-token" not in payload
    assert "private.pdf" not in payload
    assert rows[0]["description"] == rows[0]["title"]
    assert set(rows[0]["raw_fields"]) <= pyeongchang._SAFE_RAW_FIELDS
    assert meta["pii_payload_persisted"] is False


def test_complete_historical_snapshot_is_valid_zero_current_data() -> None:
    site = FixtureSite(current=False)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["current_source_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["auth_attempts"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["configured_collection_error"] == ""


def test_officially_blank_current_venue_is_preserved_without_invention() -> None:
    site = FixtureSite()
    site.rows[1] = replace(site.rows[1], venue="")  # type: ignore[arg-type]
    site.by_id["GJLI0438"] = site.rows[1]  # type: ignore[assignment]
    rows, _, meta = _collect(site)
    assert len(rows) == 1
    assert rows[0]["venue"] == ""
    assert rows[0]["raw_fields"]["source_venue"] == ""
    assert meta["snapshot_complete"] is True


@pytest.mark.parametrize(
    "flag,error_fragment",
    [
        ("filter_drift", "all-filter checkbox contract changed"),
        ("total_drift", "total/page/last changed"),
        ("unknown_external", "unknown external course ownership"),
        ("duplicate_identity", "duplicate source identities"),
        ("unstable_page_one", "page-one stability recheck changed"),
        ("bad_sentinel", "empty sentinel changed"),
    ],
)
def test_list_contract_drift_fails_closed(flag: str, error_fragment: str) -> None:
    rows, _, meta = _collect(FixtureSite(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "flag,error_fragment",
    [
        ("detail_agency_mismatch", "주관기관 list/detail mismatch"),
        ("missing_control", "open online application control changed"),
        ("bad_frame", "authentication iframe"),
        ("auth_identity_mismatch", "course identity mismatch"),
    ],
)
def test_detail_and_application_drift_fails_closed(flag: str, error_fragment: str) -> None:
    rows, _, meta = _collect(FixtureSite(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


def test_caps_and_wrong_owner_fail_before_partial_persistence() -> None:
    rows, _, meta = _collect(FixtureSite(), max_pages=45)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "46 required list requests" in meta["configured_collection_error"]

    rows, _, meta = _collect(FixtureSite(), detail_limit=0)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "1 required current details" in meta["configured_collection_error"]

    rows, _, meta = pyeongchang.collect_pyeongchang_education(
        Target("wrong", pyeongchang.PYEONGCHANG_CANONICAL_URL),
        fetcher=lambda *_: pytest.fail("wrong owner must not fetch"),
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "canonical Pyeongchang owner" in meta["configured_collection_error"]


def test_post_dedupe_privacy_mutation_is_rejected() -> None:
    def mutate(rows):
        rows[0]["phone"] = "010-1234-5678"
        return rows

    rows, _, meta = _collect(FixtureSite(), dedupe_rows=mutate)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "forbidden PII" in meta["configured_collection_error"]


def test_deduper_may_not_change_official_identity_cardinality() -> None:
    rows, _, meta = _collect(FixtureSite(), dedupe_rows=lambda values: [])
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]
