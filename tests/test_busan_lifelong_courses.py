from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from Crawler import municipal_busan_lifelong as busan


def _target(
    provider: str = busan.BUSAN_LIFELONG_PROVIDER,
    url: str = busan.BUSAN_LIFELONG_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "부산평생학습플랫폼",
        "branch": "부산광역시",
    }


@dataclass
class FakeResponse:
    body: str
    url: str
    status_code: int = 200

    @property
    def content(self) -> bytes:
        return self.body.encode("utf-8")

    @property
    def history(self) -> list[Any]:
        return []


def _office_html(*, drift: bool = False) -> str:
    offices = list(busan.BUSAN_LIFELONG_EXPECTED_OFFICES)
    if drift:
        offices[-1] = busan.BusanOffice(
            offices[-1].code,
            "해운대구 평생학습관",
            offices[-1].municipality_code,
            offices[-1].municipality_name,
            offices[-1].ownership,
        )
    options = ["<option value=''>기관선택</option>"]
    options.extend(
        f"<option value='{office.code}'>{office.name}</option>" for office in offices
    )
    return (
        "<html><body><select id='o_search_ch'>"
        + "".join(options)
        + "</select></body></html>"
    )


def _source_row(
    sequence: int,
    *,
    office: busan.BusanOffice,
    identity: str,
    title: str,
    start: str,
    end: str,
    status: str = "접수중",
    external_url: str = "",
    list_only: bool = False,
) -> dict[str, str | int]:
    return {
        "sequence": sequence,
        "office_code": office.code,
        "office_name": office.name,
        "identity": identity,
        "title": title,
        "start": start,
        "end": end,
        "apply_start": "2099.07.01",
        "apply_end": "2099.07.31",
        "status": status,
        "external_url": external_url,
        "list_only": list_only,
    }


def _row_html(row: Mapping[str, Any]) -> str:
    if row.get("list_only"):
        title_action = "href='' target='_blank'"
        action = "<a href=''><span class='button'>수강신청</span></a>"
    elif row["external_url"]:
        title_action = f"href='{row['external_url']}' target='_blank'"
        action = (
            f"<a href='{row['external_url']}'><span class='button'>수강신청</span></a>"
        )
    else:
        onclick = f"fn_learning_detail('{row['identity']}'); return false;"
        title_action = f"href='javascript:;' onclick=\"{onclick}\""
        action = (
            f"<a href='javascript:;' onclick=\"{onclick}\">"
            "<span class='button'>수강신청</span></a>"
        )
    return f"""
      <tr>
        <td>{row['sequence']}</td>
        <td class="subject"><a {title_action}>
          <span class="tit">{row['title']}</span>
          <span class="org">{row['office_name']}</span>
        </a></td>
        <td class="type"><span>무료</span><br><span>홍길동</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          {row['start']}~{row['end']}<pre>월, 10:00~12:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          {row['apply_start']}~{row['apply_end']} ( 접수인원 : 3 )</span></td>
        <td><span class="s_type2 mint"><em class="hidden">선착순</em></span>
          <span class="s_btn blue">{row['status']}</span></td>
        <td>{action}</td>
      </tr>
    """


def _list_html(
    office: busan.BusanOffice,
    page: int,
    rows: list[Mapping[str, Any]],
    last: int,
    *,
    form_page: int | None = None,
) -> str:
    options = []
    for expected in busan.BUSAN_LIFELONG_EXPECTED_OFFICES:
        selected = " selected" if expected.code == office.code else ""
        options.append(
            f"<option value='{expected.code}'{selected}>{expected.name}</option>"
        )
    body = "".join(_row_html(row) for row in rows)
    if not rows:
        body = "<tr><td colspan='7'>등록된 교육강좌가 없습니다.</td></tr>"
    effective_page = page if form_page is None else form_page
    return f"""
      <html><body>
      <form id="learningVO" method="post" action="{busan.BUSAN_LIFELONG_LIST_PATH}">
        <input name="inst_id" value="{office.code}">
        <input name="display_type" value="2">
        <input name="pageIndex" value="{effective_page}">
        <input name="l_search_ch" value="0">
        <select id="o_search_ch">{''.join(options)}</select>
        <select id="learning_state"><option value="0" selected>전체</option></select>
      </form>
      <table><thead><tr>
        <th>번호</th><th>강좌명 / 교육기관</th><th>재료비 / 강사</th>
        <th>교육기간 / 교육시간</th><th>신청기간 / 접수인원 / 대기자</th>
        <th>상태</th><th>보기</th>
      </tr></thead><tbody>{body}</tbody></table>
      <a class="page_nextend" href="?pageIndex={last}"
         onclick="fn_list({last},'');return false;">마지막</a>
      </body></html>
    """


def _internal_detail_html(
    row: Mapping[str, Any], *, active: bool = True, wrong_title: bool = False
) -> str:
    title = "다른 제목" if wrong_title else row["title"]
    waitlist = row.get("status") == "대기접수"
    control_label = "대기자신청" if waitlist else "일반모집신청"
    control = (
        "<a id='learning_aply_btn' onclick='fn_learning_apply(); return false;'>"
        f"{control_label}</a>"
        if active
        else ""
    )
    status = (
        "일반 마감 대기 접수중"
        if active and waitlist
        else "접수중" if active else "접수종료"
    )
    return f"""
      <html><body><form id="learningVO" method="post">
        <input name="inst_id" value="{row['office_code']}">
        <input name="lng_id" value="{row['identity']}">
      </form>
      <h2 class="enrolTit"><span>[{row['office_name']}]</span>{title}</h2>
      <div class="form_group"><dl><dt>교육기간</dt>
        <dd>2099.08.01 ~ 2099.08.31</dd></dl></div>
      <div class="form_group"><dl><dt>일반모집기간</dt>
        <dd>2099.07.01 ~ 2099.07.31</dd></dl></div>
      <div class="form_group"><dl><dt>교육대상</dt><dd>부산시민</dd></dl>
        <dl><dt>문의전화</dt><dd>051-220-5548</dd></dl></div>
      <div class="form_group"><dl><dt>교육장소</dt><dd>평생학습관 1강의실</dd></dl>
        <dl><dt>수강료</dt><dd>무료</dd></dl></div>
      <div class="form_group"><dl><dt>강사</dt><dd>개인 강사명</dd></dl></div>
      <div class="form_group"><dl><dt>신청상태</dt><dd>{status}</dd></dl></div>
      {control}</body></html>
    """


def _external_detail_html(
    row: Mapping[str, Any], *, active: bool = False, wrong_period: bool = False
) -> str:
    period = "2098-01-01 ~ 2098-01-02" if wrong_period else "2099-08-01 ~ 2099-08-31"
    control = (
        "<a href='/apply?id=99'>신청하기</a>" if active else "<span>접수마감</span>"
    )
    return f"""
      <html><body><h1>{row['title']}</h1><div>교육기간 {period}</div>
      <div>문의전화 051-123-4567 teacher@example.test</div>{control}</body></html>
    """


class FakeBackend:
    def __init__(
        self,
        *,
        multipage: bool = False,
        office_drift: bool = False,
        break_sequence: bool = False,
        mutate_recheck: bool = False,
        bad_internal_title: bool = False,
        bad_external_period: bool = False,
        external_active: bool = False,
        include_list_only: bool = False,
        waitlist_internal: bool = False,
        bad_sentinel: bool = False,
        clamp_sentinel: bool = False,
    ) -> None:
        self.office_drift = office_drift
        self.break_sequence = break_sequence
        self.mutate_recheck = mutate_recheck
        self.bad_internal_title = bad_internal_title
        self.bad_external_period = bad_external_period
        self.external_active = external_active
        self.bad_sentinel = bad_sentinel
        self.clamp_sentinel = clamp_sentinel
        self.calls: Counter[tuple[str, int]] = Counter()
        self.list_request_count = 0
        self.rows: dict[str, list[dict[str, Any]]] = {
            office.code: [] for office in busan.BUSAN_LIFELONG_OWNED_OFFICES
        }
        municipal = busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002731"]
        self.internal = _source_row(
            2,
            office=municipal,
            identity="LEARNING_00090001",
            title="부산 시민 인권교육",
            start="2099.08.01",
            end="2099.08.31",
            status="대기접수" if waitlist_internal else "접수중",
        )
        self.external = _source_row(
            1,
            office=municipal,
            identity="https://example.go.kr/course?id=99",
            title="외부 연계 시민강좌",
            start="2099.08.01",
            end="2099.08.31",
            external_url="https://example.go.kr/course?id=99",
        )
        self.rows[municipal.code] = [self.internal, self.external]
        self.list_only: dict[str, Any] | None = None
        if include_list_only:
            self.list_only = _source_row(
                3,
                office=municipal,
                identity="",
                title="공식 상세경로 미제공 부산 강좌",
                start="2099.09.01",
                end="2099.09.15",
                list_only=True,
            )
            self.rows[municipal.code] = [self.list_only, *self.rows[municipal.code]]
        if multipage:
            values = []
            for sequence in range(103, 2, -1):
                start_date = "2020.01.02" if sequence == 50 else "2020.01.01"
                end_date = "2020.01.01" if sequence == 50 else "2020.01.02"
                old_row = _source_row(
                    sequence,
                    office=municipal,
                    identity=f"LEARNING_OLD_{sequence:04d}",
                    title=f"부산 과거강좌 {sequence}",
                    start=start_date,
                    end=end_date,
                    status="접수종료",
                )
                if sequence == 60:
                    old_row["apply_start"] = "2020.02.01"
                    old_row["apply_end"] = "2020.01.01"
                values.append(old_row)
            self.rows[municipal.code] = [*values, self.internal, self.external]

    def session(self) -> "FakeSession":
        return FakeSession(self)

    def list_html(self, office_code: str, page: int) -> str:
        self.list_request_count += 1
        self.calls[(office_code, page)] += 1
        office = busan.BUSAN_LIFELONG_OFFICE_BY_CODE[office_code]
        values = self.rows[office_code]
        last = max(1, math.ceil(len(values) / busan.BUSAN_LIFELONG_PAGE_SIZE))
        requested_page = page
        form_page = page
        if page == last + 1 and self.clamp_sentinel:
            requested_page = last
            form_page = last
        start = (requested_page - 1) * busan.BUSAN_LIFELONG_PAGE_SIZE
        page_rows = [dict(row) for row in values[start : start + busan.BUSAN_LIFELONG_PAGE_SIZE]]
        if page == last + 1 and self.bad_sentinel:
            page_rows = [dict(values[-1])] if values else [dict(self.internal)]
        if (
            self.break_sequence
            and office_code == "OFFICE_00002731"
            and requested_page == 2
            and page_rows
        ):
            page_rows[0]["sequence"] = 999
        if (
            self.mutate_recheck
            and office_code == "OFFICE_00002731"
            and page == 1
            and self.calls[(office_code, page)] >= 2
            and page_rows
        ):
            page_rows[0]["title"] += " 변경"
        return _list_html(
            office,
            page,
            page_rows,
            last,
            form_page=form_page,
        )


class FakeSession:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend

    def get(self, url: str, timeout: int) -> FakeResponse:
        assert timeout > 0
        if url == busan.BUSAN_LIFELONG_URL:
            return FakeResponse(
                _office_html(drift=self.backend.office_drift),
                busan.BUSAN_LIFELONG_URL,
            )
        if url == self.backend.external["external_url"]:
            return FakeResponse(
                _external_detail_html(
                    self.backend.external,
                    active=self.backend.external_active,
                    wrong_period=self.backend.bad_external_period,
                ),
                url,
            )
        raise AssertionError(f"unexpected GET {url}")

    def post(
        self, url: str, data: Mapping[str, str], timeout: int
    ) -> FakeResponse:
        assert timeout > 0
        parsed = urlparse(url)
        if parsed.path == busan.BUSAN_LIFELONG_LIST_PATH:
            assert data == {
                "display_type": "2",
                "pageUnit": "100",
                "l_search_ch": "0",
                "inst_id": data["inst_id"],
                "pageIndex": data["pageIndex"],
            }
            body = self.backend.list_html(data["inst_id"], int(data["pageIndex"]))
            return FakeResponse(body, busan.BUSAN_LIFELONG_LIST_URL)
        if parsed.path == busan.BUSAN_LIFELONG_DETAIL_PATH:
            identity = parse_qs(parsed.query)["lng_id"][0]
            assert identity == self.backend.internal["identity"]
            return FakeResponse(
                _internal_detail_html(
                    self.backend.internal,
                    active=True,
                    wrong_title=self.backend.bad_internal_title,
                ),
                url,
            )
        raise AssertionError(f"unexpected POST {url}")

    def close(self) -> None:
        return None


def _collect(backend: FakeBackend, **kwargs: Any):
    return busan.collect_busan_lifelong_courses(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 200),
        detail_limit=kwargs.pop("detail_limit", 20),
        session_factory=backend.session,
        today="2099-07-20",
        max_workers=4,
        **kwargs,
    )


def test_exact_office_ownership_and_municipality_mapping() -> None:
    assert len(busan.BUSAN_LIFELONG_EXPECTED_OFFICES) == 35
    assert len(busan.BUSAN_LIFELONG_OWNED_OFFICES) == 1
    assert len(busan.BUSAN_LIFELONG_EXCLUDED_OFFICES) == 34
    assert len(busan.BUSAN_LIFELONG_COVERED_MUNICIPALITIES) == 1
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002760"].municipality_name
        == ""
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002790"].municipality_name
        == ""
    )
    names = {
        item["full_name"] for item in busan.BUSAN_LIFELONG_COVERED_MUNICIPALITIES
    }
    assert "부산광역시" in names
    assert "부산광역시 기장군" not in names
    assert "부산광역시 금정구" not in names
    assert "부산광역시 사상구" not in names
    assert "부산광역시 연제구" not in names
    assert "부산광역시 수영구" not in names
    assert "부산광역시 해운대구" not in names
    assert "부산광역시 중구" not in names
    assert "부산광역시 서구" not in names
    assert "부산광역시 영도구" not in names
    assert "부산광역시 남구" not in names
    assert "부산광역시 동래구" not in names
    assert "부산광역시 부산진구" not in names
    assert "부산광역시 북구" not in names
    assert "부산광역시 강서구" not in names
    assert "부산광역시 동구" not in names
    assert "부산광역시 사하구" not in names
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002681"].ownership
        == "duplicate_dedicated_junggu_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002641"].ownership
        == "duplicate_dedicated_seogu_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002680"].ownership
        == "duplicate_dedicated_yeongdo_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002634"].ownership
        == "duplicate_dedicated_namgu_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002682"].ownership
        == "duplicate_dedicated_dongnae_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002710"].ownership
        == "duplicate_dedicated_busanjin_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002650"].ownership
        == "duplicate_dedicated_bukgu_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002800"].ownership
        == "duplicate_dedicated_bukgu_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002642"].ownership
        == "duplicate_dedicated_donggu_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002632"].ownership
        == "duplicate_dedicated_saha_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002790"].ownership
        == "duplicate_dedicated_saha_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002686"].ownership
        == "duplicate_dedicated_gangseo_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002635"].ownership
        == "duplicate_dedicated_haeundae_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002660"].ownership
        == "duplicate_dedicated_geumjeong_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002633"].ownership
        == "duplicate_dedicated_sasang_owner"
    )
    for code in ("OFFICE_00002670", "OFFICE_00002760", "OFFICE_00002910", "OFFICE_00002770"):
        assert (
            busan.BUSAN_LIFELONG_OFFICE_BY_CODE[code].ownership
            == "duplicate_dedicated_yeonje_owner"
        )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002661"].ownership
        == "duplicate_dedicated_suyeong_owner"
    )
    assert (
        busan.BUSAN_LIFELONG_OFFICE_BY_CODE["OFFICE_00002631"].ownership
        == "duplicate_dedicated_gijang_owner"
    )


def test_target_match_is_exact_and_unfiltered() -> None:
    assert busan.is_busan_lifelong_target(_target())
    assert not busan.is_busan_lifelong_target(
        _target(url=busan.BUSAN_LIFELONG_URL + "?inst_id=OFFICE_00002631")
    )
    assert not busan.is_busan_lifelong_target(_target(provider="OTHER"))
    assert not busan.is_busan_lifelong_target(
        _target(url=busan.BUSAN_LIFELONG_URL.replace("https://", "http://"))
    )


def test_only_audited_saha_legacy_http_identity_is_upgraded() -> None:
    legacy = (
        "http://www.saha.go.kr/edu/lecture/view.do?"
        "mId=0201010000&seq=5943"
    )
    assert busan._safe_external_url(legacy) == legacy.replace("http://", "https://")
    assert busan._safe_external_url(
        "http://www.saha.go.kr/edu/lecture/view.do?mId=0201010000&seq=x"
    ) == ""
    assert busan._safe_external_url(
        "http://www.saha.go.kr/edu/lecture/view.do?mId=other&seq=5943"
    ) == ""
    assert busan._safe_external_url(
        "http://www.saha.go.kr/other/view.do?mId=0201010000&seq=5943"
    ) == ""
    assert busan._safe_external_url(
        "http://www.gijang.go.kr/lll/index.gijang?idx=2043"
    ) == ""


def test_complete_archive_current_details_application_and_privacy() -> None:
    backend = FakeBackend(multipage=True, external_active=False)
    rows, parser, meta = _collect(backend)
    assert parser == busan.BUSAN_LIFELONG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_total"] == 103
    assert meta["source_rows"] == 103
    assert meta["expired_count"] == 101
    assert meta["historical_reversed_period_count"] == 1
    assert meta["historical_reversed_apply_period_count"] == 1
    assert meta["current_count"] == 2
    assert len(rows) == 2
    assert meta["declared_pages_by_office"]["OFFICE_00002731"] == 2
    assert set(meta["sentinel_modes"].values()) == {"empty"}
    assert meta["stable_recheck_count"] == 2
    internal = next(
        row for row in rows if row["raw_fields"]["identity_kind"] == "internal"
    )
    external = next(
        row for row in rows if row["raw_fields"]["identity_kind"] == "external"
    )
    assert internal["branch"] == "부산광역시"
    assert internal["branch_code"] == "2600000000"
    assert internal["provider_organizer"] == "부산여성가족과 평생교육진흥원"
    assert internal["application_url"] == internal["raw_url"]
    assert internal["status"] == "OPEN"
    assert external["application_url"] == ""
    assert external["status"] == "CLOSED"
    serialized = repr(rows)
    assert "051-" not in serialized
    assert "teacher@example.test" not in serialized
    assert "개인 강사명" not in serialized
    assert "phone" not in serialized.lower()
    assert "instructor" not in serialized.lower()


def test_external_application_url_comes_from_verified_control() -> None:
    rows, _parser, meta = _collect(FakeBackend(external_active=True))
    assert meta["snapshot_complete"] is True
    external = next(
        row for row in rows if row["raw_fields"]["identity_kind"] == "external"
    )
    assert external["application_url"] == "https://example.go.kr/apply?id=99"
    assert external["reservation_available"] is True


def test_waitlist_control_is_not_promoted_without_detail_evidence() -> None:
    rows, _parser, meta = _collect(FakeBackend(waitlist_internal=True))
    assert meta["snapshot_complete"] is True
    internal = next(
        row for row in rows if row["raw_fields"]["identity_kind"] == "internal"
    )
    assert internal["status"] == "OPEN"
    assert internal["application_type"] == "WAITLIST_APPLY"
    assert internal["application_url"] == internal["raw_url"]
    assert internal["raw_fields"]["detail_application_control_label"] == "대기자신청"


def test_explicit_list_only_contract_has_stable_semantic_id_and_no_application() -> None:
    rows, _parser, meta = _collect(FakeBackend(include_list_only=True))
    assert meta["snapshot_complete"] is True
    assert meta["current_count"] == 3
    assert meta["list_only_detail_count"] == 1
    assert meta["detail_verified_count"] == 3
    row = next(
        row
        for row in rows
        if row["raw_fields"]["identity_kind"] == "list_only_semantic_v1"
    )
    assert row["raw_fields"]["identity"].startswith("LIST_ONLY_V1:")
    assert row["provider_course_id"].startswith(
        f"{busan.BUSAN_LIFELONG_PROVIDER}:course:"
    )
    assert row["application_url"] == ""
    assert row["application_type"] == "INFO_ONLY"
    assert row["raw_fields"]["detail_verification_mode"] == (
        "complete_list_contract_no_source_route"
    )


def test_office_selector_drift_fails_before_any_archive_request() -> None:
    backend = FakeBackend(office_drift=True)
    rows, _parser, meta = _collect(backend)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "office selector drift" in meta["configured_collection_error"]
    assert backend.list_request_count == 0


def test_low_page_cap_fails_before_any_archive_request() -> None:
    backend = FakeBackend()
    rows, _parser, meta = _collect(backend, max_pages=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]
    assert backend.list_request_count == 0


def test_sequence_gap_and_changed_recheck_are_fail_closed() -> None:
    broken = FakeBackend(multipage=True, break_sequence=True)
    rows, _parser, meta = _collect(broken)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "source sequence" in meta["configured_collection_error"]

    changed = FakeBackend(mutate_recheck=True)
    rows, _parser, meta = _collect(changed)
    assert rows == []
    assert "page signature changed" in meta["configured_collection_error"]


def test_sentinel_contract_accepts_explicit_last_page_clamp_only() -> None:
    rows, _parser, meta = _collect(FakeBackend(clamp_sentinel=True))
    assert meta["snapshot_complete"] is True
    assert rows
    assert set(meta["sentinel_modes"].values()) == {"clamped_last"}

    rows, _parser, meta = _collect(FakeBackend(bad_sentinel=True))
    assert rows == []
    assert "not empty/clamped-last" in meta["configured_collection_error"]


def test_detail_title_or_period_mismatch_is_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeBackend(bad_internal_title=True))
    assert rows == []
    assert "detail/list title mismatch" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeBackend(bad_external_period=True))
    assert rows == []
    assert "education period mismatch" in meta["configured_collection_error"]


def test_meta_documents_legacy_partial_snapshot_and_ownership_aliases() -> None:
    rows, _parser, meta = _collect(FakeBackend())
    assert rows
    assert meta["legacy_partial_count"] == 740
    assert "fixed pages per office" in meta["legacy_partial_reason"]
    providers = set(meta["superseded_providers"])
    assert "BUSAN_LIFELONG_PLATFORM" in providers
    assert "MUNI_WWW_GIJANG_GO_KR_592C4B5E" in providers
    assert len(meta["excluded_offices"]) == 34
    assert all("phone" not in item for item in meta["excluded_offices"])
    junggu = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002681"
    )
    assert "identity-equivalent duplicate" in junggu["reason"]
    assert "MUNI_WWW_BSJUNGGU_GO_KR_C443BFF0" in junggu["reason"]
    seogu = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002641"
    )
    assert "identity-equivalent duplicate" in seogu["reason"]
    assert "MUNI_WWW_BSSEOGU_GO_KR_AACF30BC" in seogu["reason"]
    yeongdo = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002680"
    )
    assert "atomically owned" in yeongdo["reason"]
    assert "MUNI_WWW_YEONGDO_GO_KR_33400564" in yeongdo["reason"]
    namgu = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002634"
    )
    assert "atomically owned" in namgu["reason"]
    assert "MUNI_WWW_BSNAMGU_GO_KR_664BF631" in namgu["reason"]
    dongnae = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002682"
    )
    assert "atomically owned" in dongnae["reason"]
    assert "MUNI_WWW_DONGNAE_GO_KR_742D8C71" in dongnae["reason"]
    busanjin = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002710"
    )
    assert "atomically owned" in busanjin["reason"]
    assert "MUNI_WWW_BUSANJIN_GO_KR_5881F59A" in busanjin["reason"]
    bukgu = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002650"
    )
    assert "atomically owned" in bukgu["reason"]
    assert "MUNI_WWW_BSBUKGU_GO_KR_E60701D6" in bukgu["reason"]
    haeundae = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002635"
    )
    assert "atomically owned" in haeundae["reason"]
    assert "MUNI_WWW_HAEUNDAE_GO_KR_E2AD27FA" in haeundae["reason"]
    geumjeong = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002660"
    )
    assert "atomically owned" in geumjeong["reason"]
    assert "MUNI_RESERVE_BUSAN_GO_KR_2CB22A99" in geumjeong["reason"]
    sasang = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002633"
    )
    assert "atomically owned" in sasang["reason"]
    assert "SASANG_RESERVATION" in sasang["reason"]
    yeonje = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002670"
    )
    assert "atomically owned" in yeonje["reason"]
    assert "MUNI_WWW_YEONJE_GO_KR_73BA35A2" in yeonje["reason"]
    suyeong = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002661"
    )
    assert "atomically owned" in suyeong["reason"]
    assert "MUNI_WWW_SUYEONG_GO_KR_41E9DDEB" in suyeong["reason"]
    gijang = next(
        item for item in meta["excluded_offices"] if item["code"] == "OFFICE_00002631"
    )
    assert "atomically owned" in gijang["reason"]
    assert "MUNI_WWW_GIJANG_GO_KR_592C4B5E" in gijang["reason"]
