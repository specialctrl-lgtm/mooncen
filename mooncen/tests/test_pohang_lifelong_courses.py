from __future__ import annotations

from dataclasses import dataclass, replace
from math import ceil
from typing import Any
from urllib.parse import parse_qs, urlparse

from Crawler import municipal_pohang_lifelong as pohang


def _target(
    provider: str = pohang.POHANG_PROVIDER,
    url: str = pohang.POHANG_CANONICAL_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "포항시 평생학습원 전체 수강신청",
        "branch": "경상북도 포항시",
    }


@dataclass(frozen=True)
class FakeCourse:
    sequence: int
    identity: str
    group: str
    title: str
    institution: str
    status: str
    education_start: str
    education_end: str
    apply_start: str
    apply_end: str
    target: str = "일반성인과정"
    topic: str = "인문·교양"
    capacity_current: int = 3
    capacity_total: int = 20
    raw_education_range: str = ""

    @property
    def education_range(self) -> str:
        return self.raw_education_range or (
            f"{self.education_start} ~ {self.education_end}"
        )

    @property
    def apply_range(self) -> str:
        return f"{self.apply_start} ~ {self.apply_end}"


def _courses() -> list[FakeCourse]:
    definitions = (
        ("BL", "뱃머리평생학습원 / 302호", "접수중"),
        ("LC", "여성문화관 / 206호", "접수전"),
        ("CC", "호동관(철강로388) 1층 탁구장", "접수완료"),
        ("RC", "죽도동주민센터 / 죽도동민복지회관", "접수완료"),
        ("PL", "포항시립중앙아트센터 / 강의실", "접수완료"),
        ("CL", "포항시민배움터 / 배움실", "접수완료"),
    )
    result: list[FakeCourse] = []
    for index, (group, institution, status) in enumerate(definitions):
        sequence = 12 - index
        if status == "접수중":
            apply_start, apply_end = "2098-12-20", "2099-01-10"
        elif status == "접수전":
            apply_start, apply_end = "2099-02-01", "2099-02-10"
        else:
            apply_start, apply_end = "2098-12-01", "2098-12-15"
        result.append(
            FakeCourse(
                sequence=sequence,
                identity=str(7000 + sequence),
                group=group,
                title=f"포항 공식 현재 강좌 {sequence}",
                institution=institution,
                status=status,
                education_start="2099-03-01",
                education_end="2099-06-30",
                apply_start=apply_start,
                apply_end=apply_end,
            )
        )
    for index, (group, institution, _) in enumerate(definitions):
        sequence = 6 - index
        result.append(
            FakeCourse(
                sequence=sequence,
                identity=str(7000 + sequence),
                group=group,
                title=f"포항 공식 종료 강좌 {sequence}",
                institution=institution,
                status="접수완료",
                education_start="2098-03-01",
                education_end="2098-06-30",
                apply_start="2098-02-01",
                apply_end="2098-02-10",
            )
        )
    return result


def _form_html(selected: str = "") -> str:
    options = []
    values = (("", "전체"),) + tuple(
        (item.code, item.label) for item in pohang.POHANG_SOURCE_GROUPS
    )
    for value, label in values:
        flag = ' selected="selected"' if value == selected else ""
        options.append(f'<option value="{value}"{flag}>{label}</option>')
    return (
        '<form name="search" method="get">'
        '<select name="sc_cl_dbr">'
        + "".join(options)
        + "</select></form>"
    )


def _pager_html(current: int, last: int, *, sentinel: bool = False) -> str:
    values = []
    for page in range(max(1, last - 9), last + 1):
        if page == current and not sentinel:
            values.append(f'<a class="active" href="#">{page}</a>')
        else:
            values.append(f'<a href="?page={page}">{page}</a>')
    return '<div class="board_list_paging"><div class="inner">' + "".join(values) + "</div></div>"


def _row_html(course: FakeCourse, display_sequence: int) -> str:
    group = next(
        item for item in pohang.POHANG_SOURCE_GROUPS if item.code == course.group
    )
    return f"""
    <tr>
      <td class="bdl_none"><span class="m_th">번호</span>{display_sequence}</td>
      <td><span class="m_th">교육기관</span><span class="type {group.badge_class}">{group.badge}</span></td>
      <td><span class="m_th">강좌명</span><a class="subject" href="{pohang.pohang_detail_url(course.identity)}">{course.title}</a></td>
      <td><span class="m_th">교육주제</span>{course.topic}</td>
      <td><span class="m_th">기간/대상</span>
        - 신청 : {course.apply_range}<br/>
        - 교육 : {course.education_range}<br/>
        - 대상 : {course.target}
      </td>
      <td><span class="m_th">정원</span>{course.capacity_current}/{course.capacity_total}</td>
      <td><span class="m_th">강좌상태</span><span class="attend finish">{course.status}</span></td>
    </tr>
    """


def _list_html(
    values: list[FakeCourse],
    *,
    page: int,
    source_group: str = "",
    declared_total: int | None = None,
    sentinel_nonempty: bool = False,
) -> str:
    total = len(values) if declared_total is None else declared_total
    last = max(1, ceil(total / pohang.POHANG_PAGE_SIZE))
    start = (page - 1) * pohang.POHANG_PAGE_SIZE
    page_values = values[start : start + pohang.POHANG_PAGE_SIZE]
    if page > last and sentinel_nonempty:
        page_values = values[:1]
    if page_values:
        rows = "".join(
            _row_html(
                course,
                course.sequence if not source_group else total - start - index,
            )
            for index, course in enumerate(page_values)
        )
    else:
        rows = '<tr><td class="bdl_none" colspan="7">자료가 없습니다.</td></tr>'
    headers = "".join(f"<th>{value}</th>" for value in pohang._LIST_HEADERS)
    return f"""
    <html><body>
      {_form_html(source_group)}
      <div class="list_num">총 {total}건 [ <strong>{page}</strong>/{last} 페이지 ]</div>
      <table class="bbs_list_table"><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>
      {_pager_html(page, last, sentinel=page > last)}
    </body></html>
    """


def _detail_html(course: FakeCourse, *, mutate_period: bool = False, ambiguous: bool = False) -> str:
    group = next(
        item for item in pohang.POHANG_SOURCE_GROUPS if item.code == course.group
    )
    education_end = "2099-07-01" if mutate_period else course.education_end
    extra = (
        f'<a href="/page/classm/class_apply.php?id_no={course.identity}">신청하기</a>'
        if ambiguous
        else ""
    )
    return f"""
    <html><body>
      <table class="bbs_view_table"><tbody>
        <tr><th class="v_subject" colspan="2"><span class="attend">{course.status}</span>{course.title}<span class="essential">* 중복 수강 가능 강좌 *</span></th></tr>
        <tr><th>교육기관</th><td><span class="type {group.badge_class}">{group.badge}</span>{course.institution}</td></tr>
        <tr><th>교육주제</th><td>{course.topic}</td></tr>
        <tr><th>교육대상</th><td>{course.target} (18세이상 100세이하)</td></tr>
        <tr><th>수강료</th><td>10,000원</td></tr>
        <tr><th>재료비</th><td>5,000원</td></tr>
        <tr><th>교육시간</th><td>총 20시간 『월 : 10:00 ~ 12:00』</td></tr>
        <tr><th>접수기간</th><td>{course.apply_start}[09:00] ~ {course.apply_end}[18:00]</td></tr>
        <tr><th>교육기간</th><td>{course.education_start} ~ {education_end}</td></tr>
        <tr><th>모집인원</th><td>{course.capacity_total}명</td></tr>
        <tr><th>선발방식</th><td>추첨</td></tr>
        <tr><th>지역제한</th><td>포항시</td></tr>
        <tr><th>담당부서</th><td>담당 홍길동 (☎ 010-9999-9999)</td></tr>
        <tr><th>강사명</th><td>개인정보강사</td></tr>
      </tbody></table>
      <p>회원님은 로그인하지 않았습니다. 로그인바랍니다.</p>
      <a class="login_btn" href="{pohang.POHANG_BASE_URL}{pohang.POHANG_LOGIN_PATH}">로그인</a>
      {extra}
    </body></html>
    """


class FakeSession:
    def close(self) -> None:
        pass


class FixtureSite:
    def __init__(
        self,
        *,
        courses: list[FakeCourse] | None = None,
        partition_delta: tuple[str, int] | None = None,
        mutate_recheck: bool = False,
        sentinel_nonempty: bool = False,
        mutate_detail: str = "",
        ambiguous_detail: str = "",
    ) -> None:
        self.courses = courses or _courses()
        self.partition_delta = partition_delta
        self.mutate_recheck = mutate_recheck
        self.sentinel_nonempty = sentinel_nonempty
        self.mutate_detail = mutate_detail
        self.ambiguous_detail = ambiguous_detail
        self.page_one_calls = 0
        self.urls: list[str] = []

    def __call__(self, _session: Any, url: str, _timeout: int) -> str:
        self.urls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == pohang.POHANG_DETAIL_PATH:
            identity = (query.get("id_no") or [""])[0]
            course = next(item for item in self.courses if item.identity == identity)
            return _detail_html(
                course,
                mutate_period=identity == self.mutate_detail,
                ambiguous=identity == self.ambiguous_detail,
            )
        assert parsed.path == pohang.POHANG_LIST_PATH
        page = int((query.get("page") or ["1"])[0])
        source_group = (query.get("sc_cl_dbr") or [""])[0]
        if source_group:
            values = [item for item in self.courses if item.group == source_group]
            declared_total = len(values)
            if self.partition_delta and self.partition_delta[0] == source_group:
                declared_total += self.partition_delta[1]
            return _list_html(
                values,
                page=page,
                source_group=source_group,
                declared_total=declared_total,
            )
        values = list(self.courses)
        if page == 1:
            self.page_one_calls += 1
            if self.mutate_recheck and self.page_one_calls > 1:
                values[0] = replace(values[0], title=values[0].title + " 변경")
        return _list_html(
            values,
            page=page,
            sentinel_nonempty=self.sentinel_nonempty,
        )


def _collect(site: FixtureSite, **kwargs: Any):
    return pohang.collect_pohang_lifelong_courses(
        _target(),
        fetcher=site,
        session_factory=FakeSession,
        today="2099-01-01",
        max_pages=100,
        detail_limit=100,
        max_workers=2,
        **kwargs,
    )


def test_target_urls_provider_candidate_and_nonexecuting_aliases_are_exact() -> None:
    assert pohang.is_pohang_lifelong_target(_target())
    assert not pohang.is_pohang_lifelong_target(
        _target(url=pohang.POHANG_CANONICAL_URL + "?sc_cl_dbr=LC")
    )
    assert not pohang.is_pohang_lifelong_target(
        _target(provider="MUNI_LIFETIMEEDU_POHANG_GO_KR_67F22341")
    )
    assert pohang.POHANG_PROVIDER == "MUNI_LIFETIMEEDU_POHANG_GO_KR_4D8BE3DA"
    assert pohang.POHANG_CANDIDATE_ID == "MUNI_IR_F65D6D320381"
    assert {item.provider for item in pohang.POHANG_NON_EXECUTING_ALIASES} == {
        "MUNI_LIFETIMEEDU_POHANG_GO_KR_BADCC25B",
        "MUNI_LIFETIMEEDU_POHANG_GO_KR_67F22341",
        "MUNI_LIFETIMEEDU_POHANG_GO_KR_B50BAC11",
        "MUNI_LIFETIMEEDU_POHANG_GO_KR_876C964E",
    }
    assert {item.ownership for item in pohang.POHANG_NON_EXECUTING_ALIASES} == {
        "subset",
        "excluded_discovery_shell",
        "excluded_notice",
    }


def test_url_builders_reject_noise_and_keep_stable_official_identity() -> None:
    assert pohang.pohang_list_url() == pohang.POHANG_CANONICAL_URL
    assert pohang.pohang_list_url(2) == pohang.POHANG_CANONICAL_URL + "?page=2"
    assert pohang.pohang_list_url(1, "LC") == (
        pohang.POHANG_CANONICAL_URL + "?sc_cl_dbr=LC"
    )
    assert pohang.pohang_detail_url("7012") == (
        pohang.POHANG_BASE_URL + pohang.POHANG_DETAIL_PATH + "?id_no=7012"
    )
    for value in ("0", "abc", "1&admin=1", ""):
        try:
            pohang.pohang_detail_url(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe identity accepted: {value}")


def test_complete_catalogue_returns_current_rows_with_verified_branches_and_pii_allowlist() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == pohang.POHANG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == meta["source_rows"] == 12
    assert meta["declared_pages"] == 2
    assert meta["sentinel_page"] == 3
    assert meta["required_list_requests"] == meta["pages"] == 10
    assert meta["source_group_counts"] == {
        "BL": 2,
        "CC": 2,
        "CL": 2,
        "LC": 2,
        "PL": 2,
        "RC": 2,
    }
    assert meta["partition_declared_counts"] == {
        "BL": 2,
        "LC": 2,
        "CC": 2,
        "PL": 2,
        "CL": 2,
        "RC": 2,
    }
    assert meta["current_count"] == meta["detail_pages"] == len(rows) == 6
    assert meta["expired_count"] == 6
    assert meta["detail_errors"] == 0
    assert meta["login_required_count"] == 2
    assert meta["reservation_discovery_links"] == 0

    by_group = {row["raw_fields"]["source_group"]: row for row in rows}
    assert by_group["BL"]["branch"] == "뱃머리평생교육관"
    assert by_group["BL"]["municipality_code"] == pohang.POHANG_NAMGU_CODE
    assert by_group["LC"]["branch"] == "여성문화관"
    assert by_group["LC"]["municipality_code"] == pohang.POHANG_BUKGU_CODE
    assert by_group["CC"]["branch"] == "복합문화센터(호동관)"
    assert by_group["CC"]["municipality_code"] == pohang.POHANG_NAMGU_CODE
    assert by_group["RC"]["branch"] == "죽도동주민센터"
    assert by_group["RC"]["municipality_code"] == pohang.POHANG_BUKGU_CODE
    assert by_group["PL"]["municipality_code"] == pohang.POHANG_CITY_CODE
    assert by_group["CL"]["municipality_code"] == pohang.POHANG_CITY_CODE
    assert by_group["BL"]["status"] == "OPEN"
    assert by_group["BL"]["application_type"] == "ONLINE_LOGIN_REQUIRED"
    assert by_group["BL"]["application_url"] == ""
    assert by_group["BL"]["reservation_available"] is False

    for row in rows:
        assert set(row["raw_fields"]) <= pohang.POHANG_RAW_FIELD_ALLOWLIST
        assert row["provider_course_id"].endswith(
            row["raw_fields"]["source_identity"]
        )
    serialized = repr(rows)
    assert "010-9999-9999" not in serialized
    assert "홍길동" not in serialized
    assert "개인정보강사" not in serialized


def test_partition_total_mismatch_fails_the_whole_snapshot_closed() -> None:
    rows, _, meta = _collect(FixtureSite(partition_delta=("CL", 1)))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "partition totals" in meta["configured_collection_error"]


def test_early_closed_status_preserves_declared_total_and_partition_contracts() -> None:
    values = _courses()
    values[0] = replace(values[0], status="조기마감")

    rows, _, meta = _collect(FixtureSite(courses=values))

    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == meta["source_rows"] == 12
    assert meta["source_group_counts"] == meta["partition_declared_counts"]
    assert meta["partitions_complete"] is True
    row = next(
        row
        for row in rows
        if row["raw_fields"]["source_identity"] == values[0].identity
    )
    assert row["raw_fields"]["source_status"] == "조기마감"
    assert row["status"] == "CLOSED"


def test_source_sequence_gap_and_duplicate_identity_fail_closed() -> None:
    values = _courses()
    values[1] = replace(values[1], sequence=10)
    values[2] = replace(values[2], identity=values[0].identity)
    rows, _, meta = _collect(FixtureSite(courses=values))
    assert rows == []
    assert meta["duplicate_identity_count"] == 1
    assert "numbering is not continuous" in meta["configured_collection_error"]


def test_nonempty_sentinel_and_page_one_change_each_fail_closed() -> None:
    rows, _, meta = _collect(FixtureSite(sentinel_nonempty=True))
    assert rows == []
    assert "expected 0 rows" in meta["configured_collection_error"]

    rows, _, meta = _collect(FixtureSite(mutate_recheck=True))
    assert rows == []
    assert "page one changed" in meta["configured_collection_error"]


def test_current_detail_mismatch_or_unreviewed_apply_control_fails_closed() -> None:
    current_identity = _courses()[0].identity
    rows, _, meta = _collect(FixtureSite(mutate_detail=current_identity))
    assert rows == []
    assert meta["detail_errors"] == 1
    assert "education period mismatch" in meta["configured_collection_error"]

    rows, _, meta = _collect(FixtureSite(ambiguous_detail=current_identity))
    assert rows == []
    assert "unreviewed application control" in meta["configured_collection_error"]


def test_unknown_current_status_fails_closed() -> None:
    values = _courses()
    values[0] = replace(values[0], status="새로운상태")
    rows, _, meta = _collect(FixtureSite(courses=values))
    assert rows == []
    assert "unknown current status" in meta["configured_collection_error"]


def test_provably_expired_invalid_calendar_date_is_recorded_not_emitted() -> None:
    values = _courses()
    expired = values[-1]
    compact = values[-2]
    values[-1] = replace(
        expired,
        raw_education_range="2098-13-40 ~ 2098-14-60",
    )
    values[-2] = replace(
        compact,
        raw_education_range="20980301 ~ 20980630",
    )
    rows, _, meta = _collect(FixtureSite(courses=values))
    assert meta["snapshot_complete"] is True
    assert len(rows) == 6
    assert meta["period_anomaly_count"] == 2
    assert meta["period_anomaly_ids"] == [compact.identity, expired.identity]
    assert all(
        row["raw_fields"]["source_identity"]
        not in {expired.identity, compact.identity}
        for row in rows
    )


def test_caps_and_managed_session_requirement_fail_without_partial_rows() -> None:
    site = FixtureSite()
    rows, _, meta = pohang.collect_pohang_lifelong_courses(
        _target(),
        fetcher=site,
        session_factory=FakeSession,
        today="2099-01-01",
        max_pages=9,
        detail_limit=100,
        max_workers=1,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _, meta = pohang.collect_pohang_lifelong_courses(_target())
    assert rows == []
    assert "managed session_factory" in meta["configured_collection_error"]
