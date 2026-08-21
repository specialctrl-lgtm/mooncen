from __future__ import annotations

from dataclasses import dataclass, replace
import math
import ssl
from typing import Any
from urllib.parse import parse_qs, urlparse

from Crawler import municipal_anyang as anyang


def _target(
    provider: str = anyang.ANYANG_PROVIDER,
    url: str = anyang.ANYANG_CANONICAL_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "안양시 공식 교육·강좌",
        "branch": "경기도 안양시",
    }


@dataclass(frozen=True)
class FakeLearning:
    sequence: int
    identity: str
    branch_code: str
    title: str
    status: str
    start: str
    end: str
    apply_start: str
    apply_end: str
    schedule: str = "월 (10:00~12:00)"
    fee: str = "10,000원"
    target: str = "안양시민"
    room: str = "교육실"
    capacity: int = 20

    @property
    def branch(self) -> anyang.AnyangLearningBranch:
        return next(
            item
            for item in anyang.ANYANG_LEARNING_BRANCHES
            if item.code == self.branch_code
        )


@dataclass(frozen=True)
class FakeReserve:
    sequence: int
    identity: str
    institution_code: str
    program: str
    title: str
    status: str
    start: str
    end: str
    apply_start: str
    apply_end: str
    venue: str
    target: str = "안양시민"
    method: str = "인터넷 접수"
    selection: str = "선착순"
    schedule: str = "월요일 14:00 ~ 16:00"
    fee: str = "무료"
    capacity: int = 20
    current: int = 2

    @property
    def institution(self) -> anyang.AnyangReserveInstitution:
        return next(
            item
            for item in anyang.ANYANG_RESERVE_INSTITUTIONS
            if item.code == self.institution_code
        )


def _learning_courses() -> list[FakeLearning]:
    branches = ("DS", "MS", "DW", "MW")
    values: list[FakeLearning] = []
    statuses = ("접수기간", "접수예정", "접수마감", "교육중")
    for index in range(12):
        sequence = 12 - index
        current = index < 4
        values.append(
            FakeLearning(
                sequence=sequence,
                identity=str(9000 + sequence),
                branch_code=branches[index % len(branches)],
                title=f"안양 평생학습 강좌 {sequence}",
                status=statuses[index] if current else "교육종료",
                start="2099-03-01" if current else "2098-03-01",
                end="2099-06-30" if current else "2098-06-30",
                apply_start=(
                    "2099-01-10"
                    if index == 1
                    else "2098-12-20" if current else "2098-02-01"
                ),
                apply_end="2099-01-15" if current else "2098-02-10",
            )
        )
    return values


def _reserve_courses() -> list[FakeReserve]:
    return [
        FakeReserve(
            sequence=3,
            identity="7701",
            institution_code="2",
            program="시민교육",
            title="안양시청 현재 강좌",
            status="모집중",
            start="2099-03-01",
            end="2099-04-30",
            apply_start="2098-12-20",
            apply_end="2099-01-31",
            venue="본관 교육장",
        ),
        FakeReserve(
            sequence=2,
            identity="7702",
            institution_code="67",
            program="건축교육",
            title="만안 건축 현재 강좌",
            status="모집마감",
            start="2099-02-01",
            end="2099-02-28",
            apply_start="2098-12-01",
            apply_end="2098-12-20",
            venue="김중업건축박물관 교육관",
            method="방문접수",
        ),
        FakeReserve(
            sequence=1,
            identity="7600",
            institution_code="57",
            program="취업지원프로그램",
            title="종료된 취업 강좌",
            status="교육종료",
            start="2098-03-01",
            end="2098-03-31",
            apply_start="2098-02-01",
            apply_end="2098-02-10",
            venue="일자리센터 교육장",
        ),
    ]


def _learning_form(region: str = "") -> str:
    def options(values: tuple[tuple[str, str], ...], selected: str) -> str:
        return "".join(
            f'<option value="{value}"'
            f'{" selected=\"selected\"" if value == selected else ""}>{label}</option>'
            for value, label in values
        )

    return f"""
    <form name="frmSearch" method="post" action="list.asp">
      <select name="s1">{options(anyang._LEARNING_REGION_OPTIONS, region)}</select>
      <select name="s2">{options(anyang._LEARNING_TARGET_OPTIONS, "")}</select>
      <select name="s3">{options(anyang._LEARNING_CATEGORY_OPTIONS, "")}</select>
    </form>
    """


def _learning_list_html(
    values: list[FakeLearning], *, page: int, region: str = "", sentinel_row: bool = False
) -> str:
    total = len(values)
    last = max(1, math.ceil(total / anyang.ANYANG_LEARNING_PAGE_SIZE))
    start = (page - 1) * anyang.ANYANG_LEARNING_PAGE_SIZE
    page_values = values[start : start + anyang.ANYANG_LEARNING_PAGE_SIZE]
    if page > last and sentinel_row and values:
        page_values = values[:1]
    rows = []
    for index, item in enumerate(page_values):
        display = total - start - index if region else item.sequence
        detail = (
            f"{item.branch.detail_prefix}viewOk.asp?NUM={item.identity}"
        )
        rows.append(
            f"""
            <tr><td>{display}</td><td>{item.branch.name}</td>
              <td><a href="{detail}">{item.title}</a></td>
              <td>{item.apply_start.replace('-', '.')} ~ {item.apply_end.replace('-', '.')}</td>
              <td>{item.start.replace('-', '.')} ~ {item.end.replace('-', '.')}</td>
              <td>{item.schedule}</td><td>{item.status}</td></tr>
            """
        )
    headers = "".join(f"<th>{value}</th>" for value in anyang._LEARNING_HEADERS)
    return f"""
    <html><body>{_learning_form(region)}
      <table><tr>{headers}</tr>{''.join(rows)}</table>
      <ul><li><a href="list.asp?page={last}&s1={region}">
        <img alt="마지막 페이지"/></a></li></ul>
    </body></html>
    """


def _learning_detail_html(
    item: FakeLearning,
    *,
    mismatch: bool = False,
    unsafe_control: bool = False,
    public_course: bool = False,
) -> str:
    apply_key = "방문 접수 기간" if item.branch_code in {"MS", "DS"} else "인터넷 접수 기간"
    control = ""
    if item.status == "접수기간":
        onclick = (
            "location.href='/unsafe'"
            if unsafe_control
            else "alert('로그인후 수강신청을 할수 있습니다.');"
        )
        control = (
            f'<a href="#edu_introduce_write" onclick="{onclick}">'
            '<img alt="강좌접수 신청"/></a>'
        )
    title = item.title + (" 변경" if mismatch else "")
    return f"""
    <html><body><table><tbody>
      <tr><th>교육기관</th><td>{item.branch.detail_names[0]}</td></tr>
      <tr><th>강좌명</th><td>{title}</td></tr>
      <tr><th>우선접수 기간</th><td></td></tr>
      <tr><th>{apply_key}</th><td>{'해당없음 (공개강좌)' if public_course else f'{item.apply_start} 09시 ~ {item.apply_end} 18시'}</td></tr>
      <tr><th>교육기간</th><td>{item.start} ~ {item.end}</td></tr>
      <tr><th>수강료</th><td>{item.fee}</td></tr>
      <tr><th>교육일시</th><td>{item.schedule}</td></tr>
      <tr><th>강의실</th><td>{item.room}</td></tr>
      <tr><th rowspan="2">정원 설정</th><th>전체정원 (우선+인터넷)</th><td>{item.capacity}</td></tr>
      <tr><th>인터넷 모집 정원</th><td>{item.capacity}</td></tr>
      <tr><th>교육대상</th><td>{item.target}</td></tr>
      <tr><th>강사명</th><td>수집하면 안 되는 강사명</td></tr>
    </tbody></table>{control}
    <table><tr><th>이름</th><th>연락처</th></tr><tr><td>홍OO</td><td>010-0000-0000</td></tr></table>
    </body></html>
    """


def _reserve_options(selected: str = "") -> str:
    values = (("", "전체"),) + tuple(
        (item.code, item.name) for item in anyang.ANYANG_RESERVE_INSTITUTIONS
    )
    return "".join(
        f'<option value="{value}"'
        f'{" selected=\"selected\"" if value == selected else ""}>{label}</option>'
        for value, label in values
    )


def _reserve_list_html(
    values: list[FakeReserve], *, page: int, institution_code: str = "", sentinel_row: bool = False
) -> str:
    total = len(values)
    last = max(1, math.ceil(total / anyang.ANYANG_RESERVE_PAGE_SIZE))
    start = (page - 1) * anyang.ANYANG_RESERVE_PAGE_SIZE
    page_values = values[start : start + anyang.ANYANG_RESERVE_PAGE_SIZE]
    if page > last and sentinel_row and values:
        page_values = values[:1]
    rows = []
    for index, item in enumerate(page_values):
        display = total - start - index if institution_code else item.sequence
        detail = (
            f"./eduLctreWebView.do?key={anyang.ANYANG_RESERVE_KEY}"
            f"&eduLctreNo={item.identity}"
        )
        rows.append(
            f"""
            <tr><td>{display}</td><td>{item.program}</td>
              <td><a href="{detail}"><b>{item.title}</b></a><br/>
                {item.institution.name}<br/>{item.venue}</td>
              <td>{item.start}~{item.end}<div>{item.schedule}</div></td>
              <td><span>총 모집인원 : {item.capacity}명</span>
                <span>(인터넷 접수인원 : {item.current}명)</span>
                <span>(방문 접수인원 : 0명)</span>
                <span>접수기간</span>{item.apply_start} ~ {item.apply_end}</td>
              <td>{item.method}</td>
              <td><span class="state type1">{item.selection}</span>
                  <span class="state type2">{item.status}</span></td>
              <td><a href="{detail}">상세보기</a></td></tr>
            """
        )
    headers = "".join(f"<th>{value}</th>" for value in anyang._RESERVE_HEADERS)
    return f"""
    <html><body>
      <form id="lctreSearchForm">
        <select name="searchInsttNo">{_reserve_options(institution_code)}</select>
      </form>
      <div class="small">총 {total} 건 [ {page} /{last} 페이지]</div>
      <table><tr>{headers}</tr>{''.join(rows)}</table>
    </body></html>
    """


def _reserve_detail_html(
    item: FakeReserve, *, mismatch: bool = False, unsafe_control: bool = False
) -> str:
    title = item.title + (" 변경" if mismatch else "")
    control = ""
    if item.status == "모집중":
        path = (
            "/reserve/unsafeApply.do"
            if unsafe_control
            else anyang.ANYANG_RESERVE_APPLICATION_PATH
        )
        control = (
            f'<a class="p-button write" href="{path}?key={anyang.ANYANG_RESERVE_KEY}'
            f'&eduLctreNo={item.identity}">신청</a>'
        )
    return f"""
    <html><body><table><tbody>
      <tr><th>프로그램명</th><td>{item.program}</td></tr>
      <tr><th>강좌명</th><td>{title}<span>{item.status}</span></td>
          <th>선발방식</th><td>{item.selection}</td></tr>
      <tr><th>모집기간</th><td>{item.apply_start} ~ {item.apply_end}</td>
          <th>운영기간</th><td>{item.start} ~ {item.end}</td></tr>
      <tr><th>교육기관/장소</th><td>{item.institution.name} {item.venue}</td></tr>
      <tr><th>모집인원 및 신청현황</th>
          <td>정원 : {item.current}명 / {item.capacity}명 /
              대기자 정원 : 0명 / 5명</td></tr>
      <tr><th>교육시간</th><td>{item.schedule}</td></tr>
      <tr><th>담당자/연락처</th><td>수집하면 안 되는 담당자 / 031-000-0000</td></tr>
      <tr><th>강사명</th><td>수집하면 안 되는 강사명</td></tr>
      <tr><th>수강료</th><td>{item.fee}</td></tr>
      <tr><th>교육내용</th><td>
        수집하지 않는 자유서술 ○ 교육대상 : {item.target}
        ○ 모집기간 : {item.apply_start} ~ {item.apply_end}</td></tr>
    </tbody></table>{control}</body></html>
    """


def _application_gate_html(*, changed: bool = False) -> str:
    message = "인증 정책 변경" if changed else "본인인증 후 이용이 가능합니다."
    return f"""
    <html><head><title>알림 후 이동</title></head><body><script>
      alert("{message}");
      $(location).attr("href", "/loginView.do?rurl=/reserve/eduLctreWebView.do");
    </script></body></html>
    """


class FakeSession:
    def close(self) -> None:
        pass


class FixtureSite:
    def __init__(
        self,
        *,
        region_delta: int = 0,
        sentinel_nonempty: str = "",
        detail_mismatch: str = "",
        unsafe_control: str = "",
        partition_overlap: bool = False,
        mutate_recheck: str = "",
        gate_changed: bool = False,
        historic_reversed_period: bool = False,
        current_reversed_period: bool = False,
        public_course_id: str = "",
    ) -> None:
        self.learning = _learning_courses()
        if historic_reversed_period:
            self.learning[4] = replace(
                self.learning[4],
                apply_start="2098-09-19",
                apply_end="2097-09-30",
            )
        if current_reversed_period:
            self.learning[0] = replace(
                self.learning[0],
                start="2099-09-19",
                end="2098-09-30",
            )
        self.reserve = _reserve_courses()
        self.region_delta = region_delta
        self.sentinel_nonempty = sentinel_nonempty
        self.detail_mismatch = detail_mismatch
        self.unsafe_control = unsafe_control
        self.partition_overlap = partition_overlap
        self.mutate_recheck = mutate_recheck
        self.gate_changed = gate_changed
        self.public_course_id = public_course_id
        self.calls: list[str] = []
        self.learning_first_calls = 0
        self.reserve_first_calls = 0

    def __call__(self, _session: Any, url: str, _timeout: int) -> str:
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.hostname == anyang.ANYANG_LEARNING_HOST:
            if parsed.path == anyang.ANYANG_LEARNING_PATH:
                page = int((query.get("Page") or query.get("page") or ["1"])[0])
                region = (query.get("s1") or [""])[0]
                if region == "MM":
                    values = [
                        item
                        for item in self.learning
                        if item.branch.municipality_code == anyang.ANYANG_MANAN_CODE
                    ]
                    if self.region_delta:
                        values = values[: max(0, len(values) + self.region_delta)]
                elif region == "DD":
                    values = [
                        item
                        for item in self.learning
                        if item.branch.municipality_code == anyang.ANYANG_DONGAN_CODE
                    ]
                else:
                    values = self.learning
                    if page == 1:
                        self.learning_first_calls += 1
                        if self.mutate_recheck == "learning" and self.learning_first_calls > 1:
                            values = [replace(values[0], title="재검증 중 변경")] + values[1:]
                last = max(1, math.ceil(len(values) / anyang.ANYANG_LEARNING_PAGE_SIZE))
                return _learning_list_html(
                    values,
                    page=page,
                    region=region,
                    sentinel_row=(
                        self.sentinel_nonempty == "learning" and page > last
                    ),
                )
            branch_code = parsed.path.strip("/").split("/", 1)[0]
            identity = (query.get("NUM") or query.get("num") or [""])[0]
            item = next(
                value
                for value in self.learning
                if value.branch_code == branch_code and value.identity == identity
            )
            return _learning_detail_html(
                item,
                mismatch=identity == self.detail_mismatch,
                unsafe_control=identity == self.unsafe_control,
                public_course=identity == self.public_course_id,
            )

        assert parsed.hostname == anyang.ANYANG_RESERVE_HOST
        if parsed.path == anyang.ANYANG_RESERVE_PATH:
            page = int((query.get("pageIndex") or ["1"])[0])
            institution = (query.get("searchInsttNo") or [""])[0]
            if institution:
                values = [
                    item
                    for item in self.reserve
                    if item.institution_code == institution
                ]
                if self.partition_overlap and institution == "57":
                    values = values + [self.reserve[0]]
            else:
                values = self.reserve
                if page == 1:
                    self.reserve_first_calls += 1
                    if self.mutate_recheck == "reserve" and self.reserve_first_calls > 1:
                        values = [replace(values[0], title="재검증 중 변경")] + values[1:]
            last = max(1, math.ceil(len(values) / anyang.ANYANG_RESERVE_PAGE_SIZE))
            return _reserve_list_html(
                values,
                page=page,
                institution_code=institution,
                sentinel_row=(self.sentinel_nonempty == "reserve" and page > last),
            )
        if parsed.path == anyang.ANYANG_RESERVE_DETAIL_PATH:
            identity = (query.get("eduLctreNo") or [""])[0]
            item = next(value for value in self.reserve if value.identity == identity)
            return _reserve_detail_html(
                item,
                mismatch=identity == self.detail_mismatch,
                unsafe_control=identity == self.unsafe_control,
            )
        assert parsed.path == anyang.ANYANG_RESERVE_APPLICATION_PATH
        return _application_gate_html(changed=self.gate_changed)


def _collect(site: FixtureSite, **kwargs: Any):
    return anyang.collect_anyang_education_courses(
        _target(),
        fetcher=site,
        session_factory=FakeSession,
        today="2099-01-01",
        max_workers=4,
        **kwargs,
    )


def test_complete_two_source_snapshot_and_district_coverage() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == anyang.ANYANG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["partitions_complete"] is True
    assert meta["details_complete"] is True
    assert meta["learning_total"] == 12
    assert meta["learning_region_totals"] == {"MM": 6, "DD": 6}
    assert meta["reserve_total"] == 3
    assert sum(meta["reserve_institution_totals"].values()) == 3
    assert meta["source_total"] == 15
    assert meta["source_current_count"] == 6
    assert meta["current_count"] == 6
    assert meta["expired_count"] == 9
    assert meta["returned_count"] == 6
    assert meta["detail_pages"] == 6
    assert meta["application_gate_pages"] == 1
    assert len(rows) == 6
    assert meta["source_kind_counts"] == {"learning": 4, "reserve": 2}
    assert meta["municipality_counts"] == {
        "경기도 안양시 동안구": 3,
        "경기도 안양시 만안구": 3,
    }
    assert {row["municipality_code"] for row in rows} == {
        anyang.ANYANG_MANAN_CODE,
        anyang.ANYANG_DONGAN_CODE,
    }
    assert all(row["category"] == "education" for row in rows)
    assert all(row["target"] for row in rows)
    assert meta["required_field_counts"] == {
        "target": 6,
        "fee": 6,
        "start_date": 6,
        "end_date": 6,
        "venue_name": 6,
        "category": 6,
        "schedule_raw": 6,
    }
    assert all(
        set(row["raw_fields"]) <= anyang.ANYANG_RAW_FIELD_ALLOWLIST
        for row in rows
    )
    serialized = repr(rows)
    assert "수집하면 안 되는" not in serialized
    assert "010-0000-0000" not in serialized


def test_application_controls_are_source_authoritative() -> None:
    rows, _parser, meta = _collect(FixtureSite())
    by_id = {row["provider_course_id"]: row for row in rows}

    learning_open = by_id[f"{anyang.ANYANG_PROVIDER}:learning:DS:9012"]
    assert learning_open["status"] == "OPEN"
    assert learning_open["reservation_available"] is True
    assert learning_open["application_type"] == "ONLINE_LOGIN_REQUIRED"
    assert learning_open["application_url"] == learning_open["raw_url"]

    learning_scheduled = by_id[
        f"{anyang.ANYANG_PROVIDER}:learning:MS:9011"
    ]
    assert learning_scheduled["status"] == "SCHEDULED"
    assert learning_scheduled["reservation_available"] is False
    assert learning_scheduled["application_url"] == ""
    assert learning_scheduled["raw_fields"]["application_control"] == (
        "not_yet_offered_before_application_window"
    )

    learning_closed = by_id[
        f"{anyang.ANYANG_PROVIDER}:learning:DW:9010"
    ]
    assert learning_closed["status"] == "CLOSED"
    assert learning_closed["reservation_available"] is False
    assert learning_closed["application_url"] == ""
    assert learning_closed["raw_fields"]["application_control"] == (
        "not_offered_after_application_closed"
    )

    reserve_open = by_id[f"{anyang.ANYANG_PROVIDER}:reserve:7701"]
    assert reserve_open["status"] == "OPEN"
    assert reserve_open["reservation_available"] is True
    assert reserve_open["application_type"] == "ONLINE_IDENTITY_REQUIRED"
    assert urlparse(reserve_open["application_url"]).path == (
        anyang.ANYANG_RESERVE_APPLICATION_PATH
    )
    assert meta["application_gate_attempts"] == 1

    closed = by_id[f"{anyang.ANYANG_PROVIDER}:reserve:7702"]
    assert closed["reservation_available"] is False
    assert closed["application_url"] == ""


def test_region_partition_mismatch_fails_closed() -> None:
    rows, _parser, meta = _collect(FixtureSite(region_delta=-1))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "MM/DD totals" in meta["configured_collection_error"]


def test_institution_partition_overlap_fails_closed() -> None:
    rows, _parser, meta = _collect(FixtureSite(partition_overlap=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "institution" in meta["configured_collection_error"]


def test_list_sentinel_and_recheck_mutations_fail_closed() -> None:
    for site in (
        FixtureSite(sentinel_nonempty="learning"),
        FixtureSite(sentinel_nonempty="reserve"),
        FixtureSite(mutate_recheck="learning"),
        FixtureSite(mutate_recheck="reserve"),
    ):
        rows, _parser, meta = _collect(site)
        assert rows == []
        assert meta["snapshot_complete"] is False
        assert meta["configured_collection_error"]


def test_detail_and_application_changes_fail_closed() -> None:
    for site in (
        FixtureSite(detail_mismatch="9012"),
        FixtureSite(detail_mismatch="7701"),
        FixtureSite(unsafe_control="9012"),
        FixtureSite(unsafe_control="7701"),
        FixtureSite(gate_changed=True),
    ):
        rows, _parser, meta = _collect(site)
        assert rows == []
        assert meta["snapshot_complete"] is False
        assert meta["configured_collection_error"]


def test_only_provably_historic_reversed_dates_are_tolerated() -> None:
    rows, _parser, meta = _collect(FixtureSite(historic_reversed_period=True))
    assert len(rows) == 6
    assert meta["snapshot_complete"] is True
    assert meta["period_anomaly_count"] == 1
    assert meta["period_anomaly_ids"] == ["9008"]

    rows, _parser, meta = _collect(FixtureSite(current_reversed_period=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "reversed current-year date range" in meta["configured_collection_error"]


def test_scheduled_status_after_application_start_fails_closed() -> None:
    site = FixtureSite()
    site.learning[1] = replace(site.learning[1], apply_start="2098-12-20")

    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "scheduled status is past" in meta["configured_collection_error"]


def test_reserve_target_is_required_and_description_is_not_retained() -> None:
    rows, _parser, meta = _collect(FixtureSite())
    reserve = next(
        row
        for row in rows
        if row["provider_course_id"]
        == f"{anyang.ANYANG_PROVIDER}:reserve:7701"
    )
    assert reserve["target"] == "안양시민"
    assert "교육대상" not in repr(reserve)

    site = FixtureSite()
    site.reserve[0] = replace(site.reserve[0], target="")
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "target absent" in meta["configured_collection_error"]


def test_functional_test_record_is_excluded_before_detail_fetch() -> None:
    site = FixtureSite()
    site.reserve[0] = replace(site.reserve[0], title="탁구(추첨 테스트)")

    rows, _parser, meta = _collect(site)

    excluded_id = f"{anyang.ANYANG_PROVIDER}:reserve:7701"
    assert meta["snapshot_complete"] is True
    assert meta["source_current_count"] == 6
    assert meta["current_count"] == 5
    assert meta["functional_test_exclusion_count"] == 1
    assert meta["functional_test_exclusion_ids"] == [excluded_id]
    assert all(row["provider_course_id"] != excluded_id for row in rows)
    assert not any(
        urlparse(url).path == anyang.ANYANG_RESERVE_DETAIL_PATH
        and parse_qs(urlparse(url).query).get("eduLctreNo") == ["7701"]
        for url in site.calls
    )


def test_audited_public_course_uses_list_period_and_login_control() -> None:
    rows, _parser, meta = _collect(FixtureSite(public_course_id="9012"))
    assert meta["snapshot_complete"] is True
    row = next(
        item
        for item in rows
        if item["provider_course_id"]
        == f"{anyang.ANYANG_PROVIDER}:learning:DS:9012"
    )
    assert row["apply_period"] == "2098-12-20 ~ 2099-01-15"
    assert row["raw_fields"]["application_control"] == (
        "anonymous_login_alert_public_course"
    )


def test_caps_fail_closed_before_returning_partial_rows() -> None:
    rows, _parser, meta = _collect(FixtureSite(), max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FixtureSite(), detail_limit=5)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_target_boundary_alias_contract_and_url_builders() -> None:
    assert anyang.is_anyang_target(_target()) is True
    assert anyang.is_anyang_target(
        _target(url=anyang.ANYANG_CANONICAL_URL + "?Page=1")
    ) is False
    assert anyang.is_anyang_target(
        _target(provider="MUNI_LEARNING_ANYANG_GO_KR_1038BD6F")
    ) is False
    assert anyang.is_anyang_target(
        _target(url="http://learning.anyang.go.kr/ay_network/Lecture_Search/list.asp")
    ) is False
    assert anyang.is_anyang_target(
        _target(url="https://learning.anyang.go.kr.evil.test/ay_network/Lecture_Search/list.asp")
    ) is False

    aliases = {item.provider: item for item in anyang.ANYANG_NON_EXECUTING_ALIASES}
    assert aliases["MUNI_LEARNING_ANYANG_GO_KR_1038BD6F"].ownership == (
        "complete_duplicate_shell"
    )
    assert aliases["MUNI_LEARNING_ANYANG_GO_KR_B549E6DB"].ownership == (
        "overlapping_branch_subset"
    )
    assert aliases["MUNI_LEARNING_ANYANG_GO_KR_97B9BE64"].ownership == (
        "overlapping_branch_subset"
    )
    assert all(alias.provider != anyang.ANYANG_PROVIDER for alias in aliases.values())

    assert parse_qs(urlparse(anyang.anyang_learning_list_url(2, "MM")).query)["Page"] == ["2"]
    reserve_query = parse_qs(
        urlparse(anyang.anyang_reserve_list_url(3, "67")).query
    )
    assert reserve_query["pageIndex"] == ["3"]
    assert reserve_query["searchInsttNo"] == ["67"]
    assert urlparse(anyang.anyang_reserve_detail_url("7701")).path == (
        anyang.ANYANG_RESERVE_DETAIL_PATH
    )


def test_legacy_tls_session_keeps_certificate_and_hostname_verification() -> None:
    session = anyang.anyang_session_factory()
    try:
        assert session.verify is True
        adapter = session.get_adapter("https://learning.anyang.go.kr/")
        assert isinstance(adapter, anyang._AnyangLegacyTLSAdapter)
        context = adapter.context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.options & getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    finally:
        session.close()


def test_missing_managed_session_factory_fails_closed() -> None:
    rows, _parser, meta = anyang.collect_anyang_education_courses(_target())
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "session_factory injection" in meta["configured_collection_error"]
