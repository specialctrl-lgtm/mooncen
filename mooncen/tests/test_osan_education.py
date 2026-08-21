from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_osan as osan


TOKEN = "12345678-1234-1234-1234-123456789ABC"


@dataclass
class FakeResponse:
    text: str
    url: str
    status_code: int = 200
    history: tuple = ()

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")


class FakeSession:
    def __init__(self, number: int):
        self.number = number
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _options(values: tuple[tuple[str, str], ...]) -> str:
    return '<option value="">전체</option>' + "".join(
        f'<option value="{code}">{name}</option>' for code, name in values
    )


def _bootstrap(*, registry_drift: bool = False) -> str:
    institutions = list(osan.OSAN_INSTITUTIONS)
    if registry_drift:
        institutions[-1] = (institutions[-1][0], "변경된 기관")
    return f"""
    <html><head><title>오산시 교육포털 | 교육신청 교육별 신청목록</title></head>
    <body><form id="searchFrm">
      <input type="hidden" name="CSRFToken" value="{TOKEN}">
      <select id="insttList">{_options(tuple(institutions))}</select>
      <select id="edcLclasList">{_options(osan.OSAN_CATEGORIES)}</select>
      <select id="eduSttusList">{_options(osan.OSAN_STATUSES)}</select>
    </form></body></html>
    """


def _lft(
    identity: str,
    status: str,
    title: str,
    branch: str,
    institution: str,
    business: str,
    period: str,
    schedule: str = "월 (10:00~11:00)",
) -> dict[str, str]:
    return {
        "identity": identity,
        "type": "LFT",
        "status": status,
        "title": title,
        "branch": branch,
        "institution": institution,
        "business": business,
        "period": period,
        "schedule": schedule,
    }


def _dlv(*, drift: bool = False) -> dict[str, str]:
    return {
        "identity": "DLV0000001",
        "type": "DLV",
        "status": "접수마감",
        "title": "배달강좌 강사 템플릿",
        "branch": "오산시평생학습관",
        "institution": "C00032",
        "business": "A99999" if drift else "A00001",
        "period": "",
        "schedule": "",
    }


def _source_rows(
    *,
    dlv_drift: bool = False,
    test_title: bool = False,
    audited_pseudo: bool = False,
):
    title1 = "테스트 샘플 강좌" if test_title else "그림책과 레고"
    rows = {
        "01": [
            _lft(
                "LFT1000001", "접수예정", title1, "햇살마루도서관",
                "C00034", "A00009", "2026-08-01 ~ 2026-08-02",
            )
        ],
        "02": [
            _lft(
                "LFT1000002", "접수중", "시민 글쓰기", "중앙도서관",
                "C00025", "A00009", "2026-08-03 ~ 2026-08-10",
            ),
            _lft(
                "LFT1000003", "대기접수", "목공 체험", "하천녹지과",
                "C00006", "A00016", "2026-08-04 ~ 2026-08-04",
            ),
        ],
        "03": [
            _lft(
                "LFT1000004", "접수마감", "천문 교실", "유엔군 초전기념관",
                "C00159", "A00321", "2026-08-05 ~ 2026-08-05",
            ),
            _dlv(drift=dlv_drift),
        ],
        "04": [
            _lft(
                "LFT1000005", "교육진행중", "라인댄스", "초평동 행정복지센터",
                "C00031", "A00009", "2026-06-01 ~ 2026-08-31",
                schedule="수 (10:00~11:00)",
            ),
            _lft(
                "LT00013792", "교육진행중", "한글서예", "대원1동 행정복지센터",
                "C00011", "A00009", "2022-01-03 ~ 2202-03-14",
            ),
        ],
        "05": [
            _lft(
                "LFT1000006", "교육종료", "지난 강좌", "꿈두레도서관",
                "C00008", "A00009", "2025-01-01 ~ 2025-01-02",
            )
        ],
        "06": [
            _lft(
                "LFT1000007", "폐강", "취소 강좌", "청학도서관",
                "C00028", "A00009", "2025-02-01 ~ 2025-02-02",
            )
        ],
    }
    if audited_pseudo:
        rows["02"].append(
            _lft(
                "LFT0029288",
                "접수중",
                "결제테스트",
                "중앙동 행정복지센터",
                "C00026",
                "A00009",
                "2026-08-01 ~ 2026-08-01",
                schedule="토 (09:00~12:00)",
            )
        )
    return rows


def _card(row: dict[str, str]) -> str:
    common = f"""
      <a class="btn_dtls" href="javascript:void(0);"
         data-ecd="{row['identity']}" data-ty="{row['type']}"
         data-bsns="{row['business']}" data-instt="{row['institution']}" data-st="1">
        <div class="flex items-center gap-2 mb-3">
          <span>{row['status']}</span><span>{row['branch']}</span>
        </div>
        <h3>{row['title']}</h3>
    """
    if row["type"] == "DLV":
        fields = """
        <div class="flex flex-col gap-1 text-sm text-gray-600 xl:hidden">
          <div><span>강사명</span><span>|</span><span>010-9999-9999</span></div>
        </div>
        """
        capacity = ""
    else:
        fields = f"""
        <div class="flex flex-col gap-1 text-sm text-gray-600 xl:hidden">
          <div><span>교육기간</span><span>|</span><span>{row['period']}</span></div>
          <div><span>교육일시</span><span>|</span><span>{row['schedule']}</span></div>
          <div><span>교육대상</span><span>|</span><span>오산시민</span></div>
          <div><span>수강료</span><span>|</span><span>0원</span></div>
        </div>
        """
        capacity = '<div class="st-num"><span>0</span><span>/10명</span></div>'
    return common + fields + capacity + "</a>"


def _fragment(rows: list[dict[str, str]], total: int) -> str:
    return (
        '<html><body><div class="class_list">'
        + "".join(_card(row) for row in rows)
        + f'</div><input id="totalRecordCount" name="totalRecordCount" value="{total}">'
        + "</body></html>"
    )


def _detail(
    row: dict[str, str],
    *,
    control_drift: bool = False,
    pii_label_drift: bool = False,
    second_application_period: bool = False,
    third_application_period: bool = False,
) -> str:
    application = row["status"] in {"접수예정", "접수중", "대기접수"}
    application_dl = (
        "<dl><dt>1차신청기간</dt><dd>2026-07-22 10:00 ~ "
        "2026-07-31 18:00 오산시민</dd></dl>"
        if application
        else ""
    )
    if second_application_period and row["identity"] == "LFT1000001":
        application_dl += (
            "<dl><dt>2차신청기간</dt><dd>2026-08-01 09:00 ~ "
            "2026-08-05 17:00 지역제한없음</dd></dl>"
        )
    if third_application_period and row["identity"] == "LFT1000001":
        application_dl += (
            "<dl><dt>3차신청기간</dt><dd>2026-08-02 09:00 ~ "
            "2026-08-07 17:00 지역제한없음</dd></dl>"
        )
    detail_status = "접수중" if row["status"] == "대기접수" else row["status"]
    control = (
        '<a id="btn_reqst" class="reserve_btn" '
        'href="javascript:fn_reqst();">신청하기</a>'
        if row["status"] in {"접수중", "대기접수"} and not control_drift
        else ""
    )
    schedule = row["schedule"]
    if row["identity"] == "LFT1000005":
        schedule += " (총1시간0분)"
    contact_label = "휴대전화" if pii_label_drift else "문의"
    # 공지사항 deliberately has no dd: production contains this malformed but
    # private-only shape.  Its value must never be read.
    notice = (
        "<dl><dt>공지사항</dt>개인 연락처 010-7777-7777</dl>"
        if row["identity"] == "LFT1000002"
        else ""
    )
    return f"""
    <html><head><title>오산시 교육포털 | 교육신청 평생교육 교육상세정보</title></head>
    <body><div id="content"><div class="class_detail_wrap">
      <div class="detail_content ct_info">
        <div class="detail_txt">비공개 설명 010-8888-8888 private@example.com</div>
        <div class="class_detail_view">
          <dl><dt>사업명</dt><dd>합성 사업</dd></dl>
          <dl><dt>기관</dt><dd>{row['branch']}</dd></dl>
          <dl><dt>모집공고일</dt><dd>2026-07-20</dd></dl>
          {application_dl}
          <dl><dt>선정방식</dt><dd>선착순</dd></dl>
          <dl><dt>신청인원 및 정원</dt><dd>0/10명</dd></dl>
          <dl><dt>강사</dt><dd>홍길동 010-1111-1111</dd></dl>
          <dl><dt>교육방법</dt><dd>오프라인</dd></dl>
          <dl><dt>교육기간</dt><dd>{row['period']}</dd></dl>
          <dl><dt>교육일시</dt><dd>{schedule}</dd></dl>
          <dl><dt>교육대상</dt><dd>오산시민</dd></dl>
          <dl><dt>교육장소</dt><dd>{row.get('venue', '오산시 강의실')}</dd></dl>
          <dl><dt>수강료</dt><dd>무료</dd></dl>
          <dl><dt>수료율</dt><dd>70%</dd></dl>
          <dl><dt>준비물</dt><dd>개인정보 메모</dd></dl>
          <dl><dt>재료비</dt><dd>0원</dd></dl>
          {notice}
          <dl><dt>{contact_label}</dt><dd>031-123-4567</dd></dl>
        </div>
      </div>
      <div class="class_list"><div class="class_box">
        <span class="class_state">{detail_status}</span><b>{row['title']}</b>
      </div>{control}</div>
    </div></div>
    <form id="frmReqst">
      <input name="edcCode" value="{row['identity']}">
      <input name="edcTy" value="LFT">
      <input name="unitBsnsId" value="{row['business']}">
      <input name="insttId" value="{row['institution']}">
      <input name="insttNm" value="{row['branch']}">
    </form>
    <script>function fn_reqst() {{ return '/app/app0102/selectEdcReqstLft.do'; }}</script>
    </body></html>
    """


class Harness:
    def __init__(
        self,
        *,
        registry_drift: bool = False,
        dlv_drift: bool = False,
        test_title: bool = False,
        control_drift: bool = False,
        pii_label_drift: bool = False,
        second_application_period: bool = False,
        third_application_period: bool = False,
        audited_pseudo: bool = False,
    ):
        self.registry_drift = registry_drift
        self.rows = _source_rows(
            dlv_drift=dlv_drift,
            test_title=test_title,
            audited_pseudo=audited_pseudo,
        )
        self.control_drift = control_drift
        self.pii_label_drift = pii_label_drift
        self.second_application_period = second_application_period
        self.third_application_period = third_application_period
        self.sessions: list[FakeSession] = []
        self.calls: list[dict] = []
        self.bootstrap_session: FakeSession | None = None

    def session_factory(self) -> FakeSession:
        session = FakeSession(len(self.sessions) + 1)
        self.sessions.append(session)
        return session

    def requester(self, session, method, url, timeout, payload, headers):
        self.calls.append(
            {
                "session": session,
                "method": method,
                "url": url,
                "timeout": timeout,
                "payload": payload,
                "headers": dict(headers),
            }
        )
        parsed = urlparse(url)
        if parsed.path == osan.OSAN_LIST_PATH:
            assert method == "GET"
            self.bootstrap_session = session
            return FakeResponse(
                _bootstrap(registry_drift=self.registry_drift), url
            )
        if parsed.path == osan.OSAN_API_PATH:
            assert method == "POST"
            assert session is self.bootstrap_session
            assert headers["X-CSRF-TOKEN"] == TOKEN
            assert headers["ajaxAt"] == "Y"
            assert payload and set(payload) == {"reqData"}
            request_data = payload["reqData"]
            assert request_data["CSRFToken"] == TOKEN
            page = int(request_data["pageIndex"])
            status = request_data.get("eduSttusList")
            if status is None:
                all_rows = [item for code in self.rows for item in self.rows[code]]
                return FakeResponse(_fragment(all_rows, len(all_rows)), url)
            values = self.rows[status]
            return FakeResponse(
                _fragment(values if page == 1 else [], len(values) if page == 1 else 0),
                url,
            )
        if parsed.path == osan.OSAN_DETAIL_PATH:
            assert method == "GET"
            query = parse_qs(parsed.query)
            assert query["edcTy"] == ["LFT"]
            identity = query["edcCode"][0]
            row = next(
                item
                for code in osan.OSAN_ACTIVE_STATUS_CODES
                for item in self.rows[code]
                if item["identity"] == identity
            )
            return FakeResponse(
                _detail(
                    row,
                    control_drift=self.control_drift,
                    pii_label_drift=self.pii_label_drift,
                    second_application_period=self.second_application_period,
                    third_application_period=self.third_application_period,
                ),
                url,
            )
        raise AssertionError(f"unexpected URL {url}")


def _collect(harness: Harness, **kwargs):
    return osan.collect_osan_education(
        {"provider": osan.OSAN_PROVIDER, "url": osan.OSAN_CANONICAL_URL},
        timeout=5,
        max_pages=kwargs.pop("max_pages", 30),
        detail_limit=kwargs.pop("detail_limit", 20),
        max_requests=kwargs.pop("max_requests", 40),
        today="2026-07-22",
        requester=harness.requester,
        session_factory=harness.session_factory,
        max_workers=kwargs.pop("max_workers", 4),
        **kwargs,
    )


def test_owner_and_canonical_contract() -> None:
    assert osan.OSAN_PROVIDER == "MUNI_WWW_OSANEDU_GO_KR_8A50CEDC"
    assert osan.OSAN_CANDIDATE_ID == "MUNI_IR_C980368128AF"
    assert osan.OSAN_LEGACY_REDIRECT_CANDIDATE_ID == "MUNI_IR_EA9C2D144222"
    assert osan.is_osan_education_target(
        {"provider": osan.OSAN_PROVIDER, "url": osan.OSAN_CANONICAL_URL}
    )
    assert not osan.is_osan_education_target(
        {"provider": osan.OSAN_PROVIDER, "url": "https://www.osanedu.go.kr/"}
    )
    audit = osan.OSAN_OWNER_BOUNDARY_AUDIT
    assert audit[osan.OSAN_LEGACY_HOME_PROVIDER]["duplicate_of"] == osan.OSAN_PROVIDER
    assert audit[osan.OSAN_LEGACY_BUSINESS_PROVIDER]["duplicate_of"] == osan.OSAN_PROVIDER
    assert audit[osan.OSAN_SPORTS_PROVIDER_CANDIDATE]["decision"] == "separate_owner_not_collected_here"
    assert osan.OSAN_AUDITED_PSEUDO_COURSES["LFT0029288"]["title"] == "결제테스트"


def test_complete_synthetic_snapshot_and_csrf_privacy_contract() -> None:
    harness = Harness()
    rows, parser, meta = _collect(harness)

    assert parser == osan.OSAN_PARSER
    assert len(rows) == 5
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["unfiltered_total"] == 9
    assert meta["six_status_totals"] == {
        "01": 1, "02": 2, "03": 2, "04": 2, "05": 1, "06": 1
    }
    assert meta["active_source_count"] == 7
    assert meta["active_lft_count"] == 6
    assert meta["dlv_excluded_count"] == 1
    assert meta["stale_lft_count"] == 1
    assert meta["audited_stale_period_anomaly_count"] == 1
    assert meta["current_lft_count"] == meta["returned_count"] == 5
    assert meta["test_or_notice_row_count"] == 0
    assert meta["bootstrap_requests"] == 1
    assert meta["list_requests"] == 19
    assert meta["detail_requests"] == 5
    assert meta["pages"] == 4
    assert meta["detail_pages"] == 5
    assert meta["network_requests"] == 25
    assert meta["sentinel_requests"] == 4
    assert meta["stability_rechecks"] == 8
    assert meta["private_values_read"] == 0
    assert meta["application_control_count"] == 2
    assert meta["status_counts"] == {
        "SCHEDULED": 1, "OPEN": 1, "WAITING": 1, "CLOSED": 2
    }
    assert Counter(row["status"] for row in rows) == Counter(meta["status_counts"])
    assert len({row["provider_course_id"] for row in rows}) == 5
    assert all(row["category"] == "교육" for row in rows)
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["schedule_raw"] for row in rows)
    assert all(row["venue_name"] for row in rows)
    assert all(row["target"] for row in rows)
    assert all("DLV" not in row["provider_course_id"] for row in rows)
    assert all(
        "010-" not in repr(row) and "031-" not in repr(row)
        and "private@example.com" not in repr(row)
        for row in rows
    )
    assert all(
        not any(
            private in row["raw_fields"]
            for private in ("강사", "준비물", "문의", "공지사항", "참고자료", "신청조건")
        )
        for row in rows
    )
    assert sum(bool(row["application_url"]) for row in rows) == 2
    assert next(row for row in rows if row["title"] == "라인댄스")["schedule"] == "수 (10:00~11:00)"
    api_calls = [call for call in harness.calls if urlparse(call["url"]).path == osan.OSAN_API_PATH]
    assert len(api_calls) == 19
    assert all(call["session"] is harness.bootstrap_session for call in api_calls)
    assert all(session.closed for session in harness.sessions)


def test_physical_detail_venue_replaces_department_branch() -> None:
    harness = Harness()
    woodwork = next(
        row
        for row in harness.rows["02"]
        if row["identity"] == "LFT1000003"
    )
    woodwork["venue"] = (
        "경기도 오산시 오산천로 52 맑음터공원 내 온마을목공체험장"
    )

    rows, _parser, meta = _collect(harness)

    assert meta["snapshot_complete"] is True
    row = next(
        item
        for item in rows
        if item["provider_course_id"].endswith("LFT1000003")
    )
    assert row["branch"] == "온마을목공체험장"
    assert row["venue_address"] == "경기도 오산시 오산천로 52"
    assert row["raw_fields"]["source_institution_branch"] == "하천녹지과"


def test_two_phase_application_period_uses_complete_public_window() -> None:
    rows, _parser, meta = _collect(Harness(second_application_period=True))

    assert meta["snapshot_complete"] is True
    row = next(value for value in rows if value["provider_course_id"].endswith("LFT1000001"))
    assert row["apply_start"] == "2026-07-22 10:00"
    assert row["apply_end"] == "2026-08-05 17:00"
    assert row["application_period"] == "2026-07-22 10:00 ~ 2026-08-05 17:00"
    assert row["raw_fields"]["primary_application_period"].startswith(
        "2026-07-22 10:00"
    )
    assert row["raw_fields"]["secondary_application_period"].startswith(
        "2026-08-01 09:00"
    )


def test_three_phase_application_period_uses_last_public_window() -> None:
    rows, _parser, meta = _collect(
        Harness(
            second_application_period=True,
            third_application_period=True,
        )
    )

    assert meta["snapshot_complete"] is True
    row = next(value for value in rows if value["provider_course_id"].endswith("LFT1000001"))
    assert row["apply_start"] == "2026-07-22 10:00"
    assert row["apply_end"] == "2026-08-07 17:00"
    assert list(row["raw_fields"]["application_period_phases"]) == [
        "1차신청기간",
        "2차신청기간",
        "3차신청기간",
    ]


def test_exact_audited_payment_test_course_is_excluded() -> None:
    rows, _parser, meta = _collect(Harness(audited_pseudo=True))

    assert meta["snapshot_complete"] is True
    assert meta["test_or_notice_row_count"] == 1
    assert meta["audited_pseudo_excluded_ids"] == ["LFT0029288"]
    assert not any("LFT0029288" in row["provider_course_id"] for row in rows)


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        (Harness(registry_drift=True), "institution registry changed"),
        (Harness(dlv_drift=True), "DLV legacy instructor-catalogue"),
        (Harness(test_title=True), "test/sample/notice"),
        (Harness(control_drift=True), "application control/status mismatch"),
        (Harness(pii_label_drift=True), "unknown detail label"),
    ],
)
def test_contract_drift_is_atomic(harness: Harness, expected: str) -> None:
    rows, _parser, meta = _collect(harness)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert expected in meta["configured_collection_error"]
    assert meta["private_values_read"] == 0


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"max_pages": 18}, "max_pages cap"),
        ({"detail_limit": 4}, "detail_limit cap"),
        ({"max_requests": 24}, "max_requests cap"),
    ],
)
def test_collection_caps_fail_closed(kwargs: dict, expected: str) -> None:
    rows, _parser, meta = _collect(Harness(), **kwargs)
    assert rows == []
    assert expected in meta["configured_collection_error"]
    assert meta["source_cap_reached"] is True


def test_external_dedupe_cannot_drop_a_current_identity() -> None:
    rows, _parser, meta = _collect(Harness(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed complete identity set" in meta["configured_collection_error"]


def test_wrong_provider_or_legacy_url_never_touches_network() -> None:
    harness = Harness()
    for target in (
        {"provider": "MUNI_OTHER", "url": osan.OSAN_CANONICAL_URL},
        {
            "provider": osan.OSAN_PROVIDER,
            "url": "https://www.osanedu.go.kr/app/app0101/selectBsnsView.do",
        },
    ):
        rows, _parser, meta = osan.collect_osan_education(
            target,
            requester=harness.requester,
            session_factory=harness.session_factory,
        )
        assert rows == []
        assert "exact canonical" in meta["configured_collection_error"]
    assert harness.calls == []


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_OSAN_EDUCATION_TESTS") != "1",
    reason="set RUN_LIVE_OSAN_EDUCATION_TESTS=1 for the complete live audit",
)
def test_live_complete_osan_csrf_ledger_contract() -> None:
    rows, parser, meta = osan.collect_osan_education(
        {"provider": osan.OSAN_PROVIDER, "url": osan.OSAN_CANONICAL_URL},
        timeout=30,
        max_pages=150,
        detail_limit=600,
        max_requests=800,
        today="2026-07-22",
        max_workers=10,
    )
    assert parser == osan.OSAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["bootstrap_requests"] == 1
    assert set(meta["six_status_totals"]) == {"01", "02", "03", "04", "05", "06"}
    assert sum(meta["six_status_totals"].values()) == meta["unfiltered_total"]
    assert len(rows) == meta["returned_count"] == meta["current_lft_count"]
    assert meta["pages"] >= 4
    assert meta["detail_pages"] == len(rows)
    assert len(rows) >= 264
    assert meta["dlv_excluded_count"] >= 259
    assert meta["test_or_notice_row_count"] == 0
    assert meta["private_values_read"] == 0
    assert meta["sentinel_requests"] == 4
    assert meta["stability_rechecks"] == 8
    assert meta["network_requests"] == (
        meta["bootstrap_requests"]
        + meta["list_requests"]
        + meta["detail_requests"]
    )
    assert sum(meta["branch_counts"].values()) == len(rows)
    assert sum(meta["business_counts"].values()) == len(rows)
    assert sum(meta["raw_status_counts"].values()) == len(rows)
    assert sum(meta["detail_schema_variants"].values()) == len(rows)
