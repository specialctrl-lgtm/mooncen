from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from threading import Lock
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_uijeongbu as uijeongbu


@dataclass
class UijeongbuTarget:
    provider: str = uijeongbu.UIJEONGBU_PROVIDER
    url: str = uijeongbu.UIJEONGBU_CANONICAL_URL


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _sugang_row(
    identity: str,
    title: str,
    status: str | tuple[str, ...],
) -> str:
    statuses = (status,) if isinstance(status, str) else status
    status_html = "".join(
        f"<span class='s_btn'>{value}</span>" for value in statuses
    )
    return f"""
      <tr>
        <td>1</td>
        <td class='subject'><a href='javascript:'
          onclick="fn_learning_detail('{identity}'); return false;">
          <span class='tit'>{title}</span>
          <span class='org'>도시교육사업본부</span></a></td>
        <td>교육기간 26.07.20 ~ 26.08.31</td>
        <td><span class='s_type indigo1'>10명</span>
          <span class='s_type red1'><em>일반 인터넷접수</em>
          26.07.01 ~ 26.07.31</span></td>
        <td>{status_html}</td>
        <td><a href='javascript:'>수강정보</a></td>
      </tr>
    """


def _sugang_page(
    *,
    rows: str,
    total: int = 2,
) -> str:
    return f"""
      <html><head><title>평생학습강좌 수강신청</title></head><body>
        <div>총 {total}건 (1/1페이지)</div>
        <table id='bbsList'>
          <thead><tr><th>번호</th><th>강좌명</th><th>교육기간</th>
          <th>신청기간</th><th>상태</th><th>보기</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="6">등록된 강좌가 없습니다.</td></tr>'}</tbody>
        </table>
      </body></html>
    """


def _sugang_detail(
    identity: str,
    title: str,
    *,
    facility: bool = False,
    missing_application: bool = False,
    application_label: str = "신청하기",
    rendered_identity: str = "",
) -> str:
    control = ""
    if not facility and not missing_application:
        control = (
            "<button id='learning_aply_btn' "
            f"onclick=\"fn_learning_apply();\">{application_label}</button>"
        )
    introduction = (
        "자유갤러리 대관장소 시설 사용 안내"
        if facility
        else "시민을 위한 교육 강좌"
    )
    return f"""
      <html><body>
        <input name='learning_id' value='{rendered_identity or identity}'>
        <h2 class='enrolTit'>{title}</h2>
        <dl><dt>교육기간</dt><dd>2026-07-20 ~ 2026-08-31</dd></dl>
        <dl><dt>일반신청기간</dt><dd>2026-07-01 ~ 2026-07-31</dd></dl>
        <dl><dt>교육장소</dt><dd>의정부 교육장</dd></dl>
        <dl><dt>교육시간</dt><dd>화 10:00~12:00</dd></dl>
        <dl><dt>수강료</dt><dd>무료</dd></dl>
        <dl><dt>교육대상</dt><dd>의정부시민</dd></dl>
        <dl><dt>강좌소개</dt><dd>{introduction}</dd></dl>
        {control}
      </body></html>
    """


def _youth_page(
    code: str,
    page: int,
    *,
    identity: str = "",
    title: str = "",
    total: int = 1,
) -> str:
    row = ""
    if identity:
        row = f"""
          <table><tbody><tr>
            <td>1</td><td>{title}</td><td>기초</td><td>19세 이상</td>
            <td>화 10:00~12:00</td><td>2026-07-01 ~ 2026-07-31</td>
            <td>10</td><td>무료</td><td>
              <a href='#none' onclick="javascript:programRegForm(
                '../online/regularProgramRegForm.ui', 'program',
                '{code}', '{identity}');">신청</a>
              <a href='javascript:;' onclick="programDetail.view('{identity}');">상세</a>
            </td>
          </tr></tbody></table>
        """
    pagination = (
        f"<script>fn_page_display('10', '{total}');</script>"
        if total
        else ""
    )
    return f"""
      <html><head><title>의정부시청소년재단 프로그램</title></head><body>
        <form id='programSeachForm'>
          <input id='subMenu' name='subMenu' value='{code}'>
          <input id='pageNumber' name='pageNumber' value='{page}'>
          <input name='pgmCate1' value='{code}'>
          <input name='mobileCheck' value='P'>
        </form>
        {pagination}{row}
      </body></html>
    """


def _uac_page(*, with_expired: bool) -> str:
    row = ""
    if with_expired:
        row = """
          <table><tbody><tr>
            <td><a href='ams_03D.php?lecture=202601010000001'>지난 문화강좌</a></td>
            <td>01.01 ~ 01.02</td><td>화요일</td><td>10:00</td>
            <td>의정부예술의전당</td><td>무료</td>
            <td><img alt='접수마감'></td>
          </tr></tbody></table>
        """
    return f"""
      <html><head><title>의정부문화관광재단</title></head><body>
        {row}<div class='pagging'><a href='#'>1</a></div>
      </body></html>
    """


def _young_card(
    identity: str,
    title: str,
    *,
    period: str = "2026-07-20 ~ 2026-08-31",
    action: str = "신청하기",
) -> str:
    return f"""
      <li><div class='box'>
        <a href='/pages/program/detail/{identity}'><div class='info'>
          <div class='tit'><strong>{title}</strong></div><ul>
          <li>교육기간: {period}</li><li>수업일시: 화 10:00~12:00</li>
          <li>수업료: 0 원</li><li>정원: 10 명</li></ul>
        </div></a></div>
        <a class='btn' href='javascript:;'>{action}</a>
      </li>
    """


def _young_page(*, normal: bool, period: str = "2026-07-20 ~ 2026-08-31") -> str:
    cards = []
    if normal:
        cards.extend(
            [
                _young_card("900", "청년 진로 교육", period=period),
                _young_card(
                    "909",
                    "2026 청년 동아리 회원 모집",
                    period=period,
                ),
            ]
        )
    cards.extend(
        [
            _young_card("886", "[8월] 프로그램 <왕초보 토익특강>", period=period),
            _young_card(
                "885",
                "상시 자소서 첨삭 및 모의면접",
                period=period,
            ),
            _young_card("613", "상시 취업상담", period=period),
            _young_card("697", "상시 마음상담", period=period),
        ]
    )
    return f"""
      <html><head><title>프로그램 신청</title></head><body>
        <ul>{''.join(cards)}</ul>
        <div class='pagination'><a class='last' onclick='fn_goPage(1)'>끝</a></div>
      </body></html>
    """


def _young_detail(identity: str, title: str) -> str:
    return f"""
      <html><body>
        <input name='pgm_pid' value='{identity}'>
        <h2>{title}</h2><div>2026-07-20 ~ 2026-08-31</div>
        <dl><dt>강의실</dt><dd>청년공감터 교육실</dd></dl>
        <dl><dt>강의일시</dt><dd>화 10:00~12:00</dd></dl>
        <dl><dt>수강료</dt><dd>무료</dd></dl>
        <dl><dt>수강자격</dt><dd>의정부 청년</dd></dl>
        <a href='https://apply.example/program/{identity}'>신청하기</a>
      </body></html>
    """


class UijeongbuFixture:
    def __init__(
        self,
        *,
        polluted_sentinel: bool = False,
        drift_page_one: bool = False,
        missing_application: bool = False,
        transient_detail_identity_mismatch: bool = False,
    ) -> None:
        self.polluted_sentinel = polluted_sentinel
        self.drift_page_one = drift_page_one
        self.missing_application = missing_application
        self.transient_detail_identity_mismatch = (
            transient_detail_identity_mismatch
        )
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self._lock = Lock()
        self._sugang_page_one_calls = 0
        self._sugang_detail_calls = 0
        self.youth = {
            "PGM_YNG": ("101", "청소년 진로 교육"),
            "PGM_CATE009": ("201", "청소년 수영 교육"),
            "PGM_CATE011": ("301", "청소년 체육 교육"),
        }
        self.young = {
            "900": "청년 진로 교육",
            "909": "2026 청년 동아리 회원 모집",
            "886": "[8월] 프로그램 <왕초보 토익특강>",
            "885": "상시 자소서 첨삭 및 모의면접",
            "613": "상시 취업상담",
            "697": "상시 마음상담",
        }

    def __call__(
        self,
        session: Any,
        method: str,
        url: str,
        payload: Optional[Mapping[str, str]],
        timeout: int,
    ) -> Any:
        del session, timeout
        values = {str(key): str(value) for key, value in (payload or {}).items()}
        with self._lock:
            self.calls.append((method, url, dict(values)))
        parsed = urlparse(url)
        host, path = (parsed.hostname or "").lower(), parsed.path

        if host == uijeongbu.UIJEONGBU_SUGANG_HOST:
            if path == uijeongbu.UIJEONGBU_SUGANG_LIST_PATH:
                page = int(values.get("pageIndex", "1"))
                if page == 1:
                    with self._lock:
                        self._sugang_page_one_calls += 1
                        changed = (
                            self.drift_page_one
                            and self._sugang_page_one_calls > 1
                        )
                    education_title = (
                        "재조회에서 바뀐 교육" if changed else "시민 환경 교육"
                    )
                    rows = _sugang_row(
                        "LEARNING_TEST1", education_title, "접수중"
                    ) + _sugang_row(
                        "LEARNING_TEST2", "자유갤러리 대관", "마감"
                    )
                    return _sugang_page(rows=rows)
                rows = ""
                if self.polluted_sentinel:
                    rows = _sugang_row(
                        "LEARNING_TEST1", "시민 환경 교육", "접수중"
                    )
                return _sugang_page(rows=rows)
            if path == uijeongbu.UIJEONGBU_SUGANG_DETAIL_PATH:
                identity = parse_qs(parsed.query)["learning_id"][0]
                if identity == "LEARNING_TEST1":
                    with self._lock:
                        self._sugang_detail_calls += 1
                        render_wrong_identity = (
                            self.transient_detail_identity_mismatch
                            and self._sugang_detail_calls == 1
                        )
                    return _sugang_detail(
                        identity,
                        "시민 환경 교육",
                        missing_application=self.missing_application,
                        application_label="일반모집신청",
                        rendered_identity=(
                            "LEARNING_TRANSIENT_WRONG"
                            if render_wrong_identity
                            else ""
                        ),
                    )
                return _sugang_detail(
                    identity, "자유갤러리 대관", facility=True
                )

        if host == uijeongbu.UIJEONGBU_YOUTH_HOST:
            if path == uijeongbu.UIJEONGBU_YOUTH_DETAIL_PATH:
                identity = values["piSeq"]
                code, title = next(
                    (code, item[1])
                    for code, item in self.youth.items()
                    if item[0] == identity
                )
                return {
                    "piSeq": identity,
                    "pgmTitle": title,
                    "operateDtSt": "2026-07-20",
                    "operateDtEd": "2026-08-31",
                    "newClsOnDtmSt": "2026-07-01",
                    "newClsOnDtmEd": "2026-07-31",
                    "officeName": f"교육기관 {code}",
                    "pgmOperate": "화 10:00~12:00",
                    "tuitionYng": "무료",
                    "regularOn": "10",
                    "ageFrom": "19",
                    "ageTo": "24",
                    "regYnOnline": "Y",
                    "regYnOfline": "N",
                    # These source fields must never be copied.
                    "teacherName": "민감강사",
                    "teacherTel": "010-1111-2222",
                    "teacherEmail": "private@example.com",
                    "content": "자유 서술",
                }
            page = int(values.get("pageNumber", "1"))
            if path.endswith("regularProgramList.ui"):
                code = "PGM_YNG"
            elif path.endswith("alwaysProgramList.ui"):
                code = "PGM_ALW"
            elif method == "POST":
                code = values.get("subMenu", "PGM_CATE011")
            else:
                code = "PGM_CATE009"
            if code == "PGM_ALW":
                return _youth_page(code, page, total=0)
            identity, title = self.youth[code]
            return _youth_page(
                code,
                page,
                identity=identity if page == 1 else "",
                title=title,
            )

        if host == uijeongbu.UIJEONGBU_UAC_HOST:
            return _uac_page(with_expired=values.get("page", "1") == "1")

        if host == uijeongbu.UIJEONGBU_YOUNG_CENTER_HOST:
            if path == uijeongbu.UIJEONGBU_YOUNG_CENTER_LIST_PATH:
                return _young_page(normal=values.get("page", "1") == "1")
            if path.startswith("/pages/program/detail/"):
                identity = path.rstrip("/").rsplit("/", 1)[-1]
                return _young_detail(identity, self.young[identity])

        raise AssertionError(f"unexpected request: {method} {url} {values}")


def _collect(
    fixture: UijeongbuFixture,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    return uijeongbu.collect_uijeongbu_education_courses(
        UijeongbuTarget(),
        requester=fixture,
        session_factory=DummySession,
        today=date(2026, 7, 21),
        max_workers=4,
        **kwargs,
    )


def test_complete_origin_owned_snapshot_excludes_facility_and_pii() -> None:
    fixture = UijeongbuFixture()

    rows, parser, meta = _collect(fixture)

    assert parser == uijeongbu.UIJEONGBU_PARSER
    assert len(rows) == 10
    assert meta["source_total"] == 12
    assert meta["source_rows"] == 12
    assert meta["current_count"] == 11
    assert meta["expired_count"] == 1
    assert meta["excluded_facility_count"] == 1
    assert meta["returned_count"] == 10
    assert meta["detail_attempts"] == 11
    assert meta["detail_pages"] == 11
    assert meta["required_list_requests"] == 22
    assert meta["source_totals"] == {
        "sugang": 2,
        "youth_general": 1,
        "youth_swimming": 1,
        "youth_sports": 1,
        "youth_always": 0,
        "uac": 1,
        "young_center": 6,
    }
    assert meta["source_page_counts"] == {
        "sugang": 1,
        "youth_general": 1,
        "youth_swimming": 1,
        "youth_sports": 1,
        "youth_always": 1,
        "uac": 1,
        "young_center": 1,
    }
    assert meta["pagination_complete"] is True
    assert meta["partitions_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["duplicate_identity_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["semantic_overlap_count"] == 0
    assert meta["application_control_count"] == 10
    assert len({row["branch_code"] for row in rows}) == len(
        {row["branch"] for row in rows}
    )
    assert all(row["preserve_branch"] is True for row in rows)
    assert "자유갤러리 대관" not in {row["title"] for row in rows}
    assert {row["municipality_code"] for row in rows} == {"4115000000"}
    serialized = json.dumps(rows, ensure_ascii=False)
    for forbidden in (
        "민감강사",
        "010-1111-2222",
        "private@example.com",
        "자유 서술",
    ):
        assert forbidden not in serialized
    requested = json.dumps(fixture.calls, ensure_ascii=False)
    assert "ui4u.go.kr" not in requested
    assert "API_OFFICE_00000000" not in requested
    assert "API_OFFICE_00000010" not in requested
    assert "OFFICE_00002310" not in requested
    sports_posts = [
        payload
        for method, url, payload in fixture.calls
        if method == "POST"
        and "regularProgramRegList.ui" in url
        and payload.get("subMenu") == "PGM_CATE011"
    ]
    assert [item["pageNumber"] for item in sports_posts] == ["1", "2", "1"]


def test_sugang_education_in_progress_with_closed_registration_is_closed() -> None:
    document = BeautifulSoup(
        _sugang_page(
            rows=_sugang_row(
                "LEARNING_00594084",
                "여름방학 클라이밍",
                ("교육중", "마감"),
            ),
            total=1,
        ),
        "html.parser",
    )

    rows, total, last = uijeongbu._parse_sugang_page(document, 1)

    assert (total, last) == (1, 1)
    assert len(rows) == 1
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["raw_fields"]["source_status"] == "교육중 / 마감"


def test_uac_open_status_without_public_control_preserves_status_only() -> None:
    row = uijeongbu._base_row(
        "uac",
        "202607221412110",
        "상상더하기 전래동화 몸놀이",
        uijeongbu.UIJEONGBU_UAC_BRANCH,
        (
            "https://www.uac.or.kr/newuac/ams/ams_03D.php"
            "?lecture=202607221412110"
        ),
        date(2026, 8, 22),
        date(2026, 11, 14),
    )
    row["status"] = "OPEN"
    row["raw_fields"]["source_status"] = "접수중"
    detail = BeautifulSoup(
        """
        <html><body>
          <table class="academy_D">
            <tr><th>강좌명</th><td>상상더하기 전래동화 몸놀이</td></tr>
            <tr><th>기간</th><td>2026-08-22 - 2026-11-14</td></tr>
            <tr><th>접수상태</th>
              <td><img alt="접수중" src="../ams/img/state_C.gif"></td></tr>
          </table>
        </body></html>
        """,
        "html.parser",
    )

    validated = uijeongbu._validate_uac_detail(row, detail)

    assert validated["status"] == "OPEN"
    assert validated["application_url"] == ""
    assert validated["reservation_available"] is False
    assert validated["raw_fields"]["source_detail_status"] == "접수중"
    assert (
        validated["raw_fields"]["application_surface"]
        == "status_only_no_public_control"
    )


def test_repository_dedupe_callback_uses_single_argument() -> None:
    fixture = UijeongbuFixture()
    calls: list[list[dict[str, Any]]] = []

    rows, _parser, meta = _collect(
        fixture,
        dedupe_rows=lambda current: calls.append(list(current)) or current,
    )

    assert meta["snapshot_complete"] is True
    assert calls == [rows]


def test_youth_application_period_is_all_phase_envelope() -> None:
    source = {
        "source_kind": "youth_general",
        "identity": "10419",
        "title": "[온마을쌤] 가죽소품만들기",
        "catalogue": "general",
        "catalogue_label": "일반프로그램",
        "list_page": 2,
        "list_apply_start": date(2026, 7, 7),
        "list_apply_end": date(2026, 7, 24),
        "list_target": "12세 ~ 19세",
        "list_schedule": "10:00 ~ 12:00",
        "list_capacity": "8",
        "list_fee": "",
        "application_label": "현장신청",
        "course_bound_application": False,
    }
    detail = {
        "piSeq": "10419",
        "pgmTitle": "[온마을쌤] 가죽소품만들기",
        "operateDtSt": "2026-07-28",
        "operateDtEd": "2026-08-13",
        "newClsOnDtmSt": "2026-07-07",
        "newClsOnDtmEd": "2026-07-20",
        "newClsOffDtmSt": "2026-07-20",
        "newClsOffDtmEd": "2026-07-24",
        "reReceptionPeriodOnStart": "2026070710",
        "reReceptionPeriodOnEnd": "2026072023",
        "reReceptionPeriodOffStart": "2026072000",
        "reReceptionPeriodOffEnd": "2026072400",
        "classChangePeriodOnStart": "2026070710",
        "classChangePeriodOnEnd": "2026072023",
        "classChangePeriodOffStart": "2026072000",
        "classChangePeriodOffEnd": "2026072400",
        "receptionPeriodOnStart": "2026070710",
        "receptionPeriodOnEnd": "2026072023",
        "receptionPeriodOffStart": "2026072000",
        "receptionPeriodOffEnd": "2026072400",
        "officeName": "의정부시청소년수련관",
        "pgmOperate": "화, 목 10:00~12:00",
        "tuitionYng": "0",
        "regularOn": "8",
        "ageFrom": "12",
        "ageTo": "19",
        "regYnOnline": "Y",
        # The live group programme exposes an on-site list control while this
        # internal flag is N.  The public control remains authoritative.
        "regYnOfline": "N",
    }

    row = uijeongbu._validate_youth_detail(source, detail)

    assert row["apply_start"] == "2026-07-07"
    assert row["apply_end"] == "2026-07-24"
    assert row["application_type"] == "OFFLINE_APPLICATION"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is False
    assert row["raw_fields"]["application_control_verified"] is True
    assert row["raw_fields"]["offline_application"] is False
    assert row["raw_fields"]["application_phase_count"] == 8
    assert row["raw_fields"]["application_period_contract"] == (
        "list_envelope_of_all_detail_reception_phases"
    )

    wrong_list = dict(source, list_apply_end=date(2026, 7, 23))
    with pytest.raises(
        uijeongbu.UijeongbuContractError,
        match="list/detail application envelope mismatch",
    ):
        uijeongbu._validate_youth_detail(wrong_list, detail)


def test_canonical_and_audited_legacy_targets_share_one_fanout() -> None:
    assert uijeongbu.is_uijeongbu_target(UijeongbuTarget())
    assert uijeongbu.is_uijeongbu_target(
        {
            "provider": uijeongbu.UIJEONGBU_PROVIDER,
            "url": uijeongbu.UIJEONGBU_SUGANG_LIST_URL,
        }
    )
    assert uijeongbu.is_uijeongbu_target(
        {
            "provider": uijeongbu.UIJEONGBU_LEGACY_PROVIDER,
            "url": uijeongbu.UIJEONGBU_LEGACY_URL,
        }
    )
    assert not uijeongbu.is_uijeongbu_target(
        {
            "provider": uijeongbu.UIJEONGBU_LEGACY_PROVIDER,
            "url": "https://www.ui4u.go.kr/reservation/youthProgram/list.do?mId=wrong",
        }
    )
    meta = uijeongbu._base_meta()
    assert meta["canonical_candidate_id"] == "MUNI_IR_76A34D1F10F3"
    assert meta["legacy_candidate_id"] == "MUNI_IR_36EE4B255E26"
    assert set(meta["non_executing_mirror_offices"]) == {
        "API_OFFICE_00000000",
        "API_OFFICE_00000010",
        "OFFICE_00002310",
    }


def test_populated_post_boundary_page_fails_closed() -> None:
    rows, _, meta = _collect(UijeongbuFixture(polluted_sentinel=True))

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sugang sentinel" in meta["configured_collection_error"]


def test_page_one_drift_fails_closed() -> None:
    rows, _, meta = _collect(UijeongbuFixture(drift_page_one=True))

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "page one changed" in meta["configured_collection_error"]


def test_missing_public_application_control_fails_closed() -> None:
    rows, _, meta = _collect(UijeongbuFixture(missing_application=True))

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "public application control missing" in meta["configured_collection_error"]


def test_transient_neuron_detail_identity_mixup_is_retried_exactly() -> None:
    rows, _, meta = _collect(
        UijeongbuFixture(transient_detail_identity_mismatch=True)
    )

    assert len(rows) == 10
    assert meta["snapshot_complete"] is True
    assert meta["detail_retries"] == 1


def test_complete_page_budget_is_enforced() -> None:
    rows, _, meta = _collect(UijeongbuFixture(), max_pages=21)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "complete fan-out boundary" in meta["configured_collection_error"]


def test_young_center_archive_anomalies_have_a_narrow_allowlist() -> None:
    accepted_blank = BeautifulSoup(
        "<html><head><title>프로그램 신청</title></head><body><ul>"
        + _young_card("448", "과거 마감 교육", period=" ~ ", action="마감")
        + "</ul></body></html>",
        "html.parser",
    )
    row = uijeongbu._young_page_rows(accepted_blank, 50)[0]
    assert row["end_date"] == "1970-01-01"
    assert row["raw_fields"]["source_period_missing"] is True

    new_blank = BeautifulSoup(
        "<html><head><title>프로그램 신청</title></head><body><ul>"
        + _young_card("449", "새 공란 교육", period=" ~ ", action="마감")
        + "</ul></body></html>",
        "html.parser",
    )
    with pytest.raises(
        uijeongbu.UijeongbuContractError,
        match="current/new period missing",
    ):
        uijeongbu._young_page_rows(new_blank, 49)

    accepted_reversed = BeautifulSoup(
        "<html><head><title>프로그램 신청</title></head><body><ul>"
        + _young_card(
            "742",
            "과거 역전 교육",
            period="2025-08-02 ~ 2025-07-23",
            action="마감",
        )
        + "</ul></body></html>",
        "html.parser",
    )
    reversed_row = uijeongbu._young_page_rows(accepted_reversed, 18)[0]
    assert reversed_row["start_date"] == "2025-07-23"
    assert reversed_row["end_date"] == "2025-08-02"
    assert reversed_row["raw_fields"]["source_period_reversed"] is True

    new_reversed = BeautifulSoup(
        "<html><head><title>프로그램 신청</title></head><body><ul>"
        + _young_card(
            "999",
            "새 역전 교육",
            period="2026-08-02 ~ 2026-07-23",
            action="마감",
        )
        + "</ul></body></html>",
        "html.parser",
    )
    with pytest.raises(
        uijeongbu.UijeongbuContractError,
        match="reversed period",
    ):
        uijeongbu._young_page_rows(new_reversed, 1)
