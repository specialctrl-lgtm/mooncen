from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_gyeongnam_sancheong as sancheong


def _record(
    identity: str,
    *,
    title: str,
    branch: str,
    status: str,
    period: str,
    apply: str,
    methods: tuple[str, ...] = ("인터넷",),
    fee: str = "무료",
    lottery: str = "",
    enrolled: int = 2,
    capacity: int = 20,
    waitlisted: int = 1,
) -> dict[str, Any]:
    return {
        "id": identity,
        "title": title,
        "branch": branch,
        "status": status,
        "period": period,
        "apply": apply,
        "methods": methods,
        "fee": fee,
        "category": "무료" if fee == "무료" else "유료",
        "lottery": lottery,
        "enrolled": enrolled,
        "capacity": capacity,
        "waitlisted": waitlisted,
        "target": "산청군민",
        "venue": f"{branch} 프로그램실",
        "schedule": "매주 화 10:00~12:00",
    }


RECORDS = (
    _record(
        "LT000010",
        title="온라인 미래 강좌",
        branch="산청군 평생학습센터",
        status="접수중",
        period="2099-08-01 ~ 2099-08-31 매주 화 10:00~12:00",
        apply="2099-07-01 ~ 2099-07-31",
    ),
    _record(
        "LT000009",
        title="방문 병행 미래 강좌",
        branch="산청읍",
        status="접수중",
        period="2099-08-02 ~ 2099-09-01 매주 수 14:00~16:00",
        apply="2099-07-02 ~ 2099-07-31",
        methods=("인터넷", "방문"),
        fee="20,000원",
    ),
    _record(
        "LT000008",
        title="추첨 예정 강좌",
        branch="단성면",
        status="접수대기",
        period="2099-09-01 ~ 2099-09-30 매주 목 10:00~12:00",
        apply="2099-08-01 ~ 2099-08-20",
        lottery="2099-08-21 17:00",
    ),
    _record(
        "LT000007",
        title="오늘 끝나는 마감 강좌",
        branch="청소년수련관",
        status="접수마감",
        period="2099-07-10 ~ 2099-07-20 09:00~11:00",
        apply="2099-06-01 ~ 2099-06-30",
        methods=("인터넷", "전화"),
    ),
    _record(
        "LT000006",
        title="교육 중 마감 강좌",
        branch="농업인 정보화교육장",
        status="접수마감",
        period="2099-06-01 ~ 2099-12-31 매주 금 13:00~15:00",
        apply="2099-05-01 ~ 2099-05-20",
        methods=("인터넷", "방문", "전화"),
    ),
    *(
        _record(
            f"LT{identity:06d}",
            title=f"종료 강좌 {identity}",
            branch="금서면",
            status="접수마감",
            period="2098-01-01 ~ 2098-02-01 10:00~12:00",
            apply="2097-12-01 ~ 2097-12-20",
        )
        for identity in range(5, 0, -1)
    ),
)


def _target(
    *,
    provider: str = sancheong.SANCHEONG_PROVIDER,
    url: str = sancheong.SANCHEONG_CONFIGURED_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "산청군 교육",
        "branch": sancheong.SANCHEONG_MUNICIPALITY_NAME,
    }


def _search_form(requested_page: int, displayed_page: int) -> str:
    fcd_options = "".join(
        f'<option value="{value}">{label}</option>'
        for value, label in sancheong._FCD_OPTIONS.items()
    )
    stype_options = "".join(
        f'<option value="{value}"{" selected" if value == "title" else ""}>{label}</option>'
        for value, label in sancheong._STYPE_OPTIONS.items()
    )
    return f"""
      <form id="listForm" name="listForm" method="get"
        action="{sancheong.SANCHEONG_LIST_PATH}{f'?cpage={requested_page}' if requested_page > 1 else ''}">
        <input type="hidden" name="cpage" value="1">
        <select name="fcd"><option selected value="">전체기관</option>{fcd_options}</select>
        <select name="stype">{stype_options}</select>
        <input name="sstring" value="">
        <p class="info1">총 10 개의 강좌신청이 있습니다. ({displayed_page}/2 페이지)</p>
      </form>
    """


def _markers(tag: str, attribute: str, values: Iterable[str]) -> str:
    return "".join(
        f'<{tag} {attribute}="{value}">{value}</{tag}>' for value in values
    )


def _card(
    record: Mapping[str, Any],
    *,
    requested_page: int,
    title: str | None = None,
) -> str:
    suffix = f"&cpage={requested_page}" if requested_page > 1 else ""
    lottery = (
        f'<li><span class="t1">추첨발표</span><span class="t2">{record["lottery"]}</span></li>'
        if record["lottery"]
        else ""
    )
    return f"""
      <div class="lst"><a href="?amode=view{suffix}&lectureId={record['id']}">
        <span class="stat">
          <em data-status="{record['status']}">{record['status']}</em>
          <em data-category="{record['category']}">{record['category']}</em>
        </span>
        <strong class="h1">{title or record['title']}</strong>
        <div class="stat2">{_markers('span', 'data-progress', record['methods'])}</div>
        <div class="texts"><ul class="clist">
          <li><span class="t1">접수기간</span><span class="t2">{record['apply']}</span></li>
          <li><span class="t1">교육기간</span><span class="t2">{record['period']}</span></li>
          <li><span class="t1">모집인원</span><span class="t2">정원 {record['capacity']}명 / 접수 {record['enrolled']}명 / 대기 {record['waitlisted']}명</span></li>
          <li><span class="t1">수강료</span><span class="t2">{record['fee']}</span></li>
          {lottery}
        </ul></div>
      </a></div>
    """


def _list_html(
    *,
    requested_page: int,
    displayed_page: int,
    records: tuple[Mapping[str, Any], ...],
    mutate_title: bool = False,
) -> str:
    cards = "".join(
        _card(
            record,
            requested_page=requested_page,
            title="변경된 경계 강좌" if mutate_title and index == 0 else None,
        )
        for index, record in enumerate(records)
    )
    return f"""
      <html><body><div id="body_content">
        {_search_form(requested_page, displayed_page)}
        <div class="cp1list1 full">{cards}</div>
      </div></body></html>
    """


def _detail_html(
    record: Mapping[str, Any],
    *,
    wrong_title: bool = False,
    wrong_period: bool = False,
    wrong_status: bool = False,
    missing_application: bool = False,
    wrong_application: bool = False,
    inactive_application: bool = False,
    wait_application: bool = False,
    empty_institution: bool = False,
    wrong_methods: bool = False,
) -> str:
    title = "다른 강좌" if wrong_title else record["title"]
    period = "2099-01-01 ~ 2099-01-02" if wrong_period else record["period"]
    status = "접수마감" if wrong_status else record["status"]
    lottery = (
        f'<li><span class="t1">추첨발표</span><span class="t2">{record["lottery"]}</span></li>'
        if record["lottery"]
        else ""
    )
    methods = (*record["methods"], "추첨") if record["lottery"] else record["methods"]
    if wrong_methods:
        methods = ("인터넷", "방문")
    progress_markers = "".join(
        (
            '<em data-progress="전화">추첨</em>'
            if value == "추첨"
            else f'<em data-progress="{value}">{value}</em>'
        )
        for value in methods
    )
    show_application = record["status"] == "접수중" and not missing_application
    show_application = show_application or inactive_application
    application_id = "LT999999" if wrong_application else record["id"]
    if show_application and wait_application:
        application = (
            f'<a class="button" href="?amode=agree&lectureId={application_id}">'
            "대기접수</a>"
        )
    elif show_application:
        application = (
            f'<a class="button reserve" href="?amode=agree&lectureId={application_id}">'
            "신청하기</a>"
        )
    else:
        application = ""
    institution = "" if empty_institution else record["branch"]
    return f"""
      <html><body><div id="body_content">
        <div class="edu1view1">
          <div class="hg1">
            <h3 class="h1">{title}</h3>
            <em data-status="{status}">{status}</em>
            <em data-category="{record['category']}">{record['category']}</em>
            {progress_markers}
          </div>
          <div class="wrap1"><div class="texts"><ul class="lst">
            <li><span class="t1">분야</span><span class="t2">생활취미</span></li>
            <li><span class="t1">교육대상</span><span class="t2">{record['target']}</span></li>
            <li><span class="t1">교육장소</span><span class="t2">{record['venue']}</span></li>
            <li><span class="t1">모집인원</span><span class="t2">{record['enrolled']}명 접수 / 총 {record['capacity']} 명 모집 온라인 접수 : {record['capacity']} 명 / 대기인원 : 5 명</span></li>
            <li><span class="t1">교육방법</span><span class="t2">-</span></li>
            <li><span class="t1">접수방법</span><span class="t2">인터넷</span></li>
            {lottery}
            <li><span class="t1">접수기간</span><span class="t2">{record['apply']}</span></li>
            <li><span class="t1">교육기간</span><span class="t2">{period}</span></li>
            <li><span class="t1">교육시간</span><span class="t2">{record['schedule']}</span></li>
            <li><span class="t1">강사명</span><span class="t2">김강사</span></li>
            <li><span class="t1">수강료</span><span class="t2">{record['fee']}</span></li>
          </ul></div></div>
        </div>
          <div id="tabs1pane1">
            <div class="rspnsv"><table class="t3">
              <tr><th>교육기관</th><td>{institution}</td></tr>
              <tr><th>주소</th><td>경상남도 산청군 중앙로 1</td></tr>
              <tr><th>담당자</th><td>김담당</td></tr>
              <tr><th>연락처</th><td>055-970-1234</td></tr>
            </table></div>
            <div class="free-text">문의 010-8888-9999, secret@example.kr</div>
            <table class="t3 applicants">
              <tr><th>신청자</th><th>전화번호</th></tr>
              <tr><td>홍*동</td><td>010-1234-5678</td></tr>
            </table>
            <script>fetch('{sancheong.SANCHEONG_APPLICANT_PATH}?lectureId={record['id']}')</script>
          </div>
          <div class="btns1">
            {application}
            <a class="button list" href="?">목록으로</a>
          </div>
      </div></body></html>
    """


@dataclass
class DummySession:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class FakeSite:
    def __init__(
        self,
        *,
        bad_clamp: bool = False,
        mutate_recheck: bool = False,
        duplicate_source_id: bool = False,
        wrong_title: bool = False,
        wrong_period: bool = False,
        wrong_status: bool = False,
        missing_application: bool = False,
        wrong_application: bool = False,
        inactive_application: bool = False,
        wait_application: bool = False,
        empty_institution: bool = False,
        wrong_methods: bool = False,
    ) -> None:
        self.bad_clamp = bad_clamp
        self.mutate_recheck = mutate_recheck
        self.duplicate_source_id = duplicate_source_id
        self.wrong_title = wrong_title
        self.wrong_period = wrong_period
        self.wrong_status = wrong_status
        self.missing_application = missing_application
        self.wrong_application = wrong_application
        self.inactive_application = inactive_application
        self.wait_application = wait_application
        self.empty_institution = empty_institution
        self.wrong_methods = wrong_methods
        self.calls: list[str] = []
        self.page_calls: Counter[int] = Counter()

    def __call__(
        self,
        _session: Any,
        method: str,
        url: str,
        *,
        timeout: int,
        data: Mapping[str, str],
    ) -> tuple[BeautifulSoup, str]:
        assert method == "GET"
        assert timeout > 0
        assert data == {}
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        assert parsed.path != sancheong.SANCHEONG_APPLICANT_PATH
        assert query.get("amode") != ["agree"]
        if query.get("amode") == ["view"]:
            identity = query["lectureId"][0]
            record = next(item for item in RECORDS if item["id"] == identity)
            html = _detail_html(
                record,
                wrong_title=self.wrong_title and identity == "LT000010",
                wrong_period=self.wrong_period and identity == "LT000010",
                wrong_status=self.wrong_status and identity == "LT000010",
                missing_application=self.missing_application and identity == "LT000010",
                wrong_application=self.wrong_application and identity == "LT000010",
                inactive_application=self.inactive_application and identity == "LT000008",
                wait_application=self.wait_application and identity == "LT000010",
                empty_institution=self.empty_institution and identity == "LT000010",
                wrong_methods=self.wrong_methods and identity == "LT000010",
            )
            return BeautifulSoup(html, "lxml"), url

        requested = int((query.get("cpage") or ["1"])[0])
        self.page_calls[requested] += 1
        if requested == 1:
            rows: tuple[Mapping[str, Any], ...] = RECORDS[:9]
            displayed = 1
            mutate = self.mutate_recheck and self.page_calls[1] > 1
        elif requested == 2:
            rows = RECORDS[9:]
            if self.duplicate_source_id:
                rows = ({**RECORDS[9], "id": RECORDS[8]["id"]},)
            displayed = 2
            mutate = False
        else:
            rows = RECORDS[:9] if self.bad_clamp else RECORDS[9:]
            if self.duplicate_source_id and not self.bad_clamp:
                rows = ({**RECORDS[9], "id": RECORDS[8]["id"]},)
            displayed = 2
            mutate = False
        html = _list_html(
            requested_page=requested,
            displayed_page=displayed,
            records=tuple(rows),
            mutate_title=mutate,
        )
        return BeautifulSoup(html, "lxml"), url


def _collect(
    site: FakeSite,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    return sancheong.collect(
        _target(),
        session_factory=DummySession,
        fetcher=site,
        today="2099-07-20",
        **kwargs,
    )


def test_target_scope_candidate_decisions_and_duplicate_aliases() -> None:
    assert sancheong.is_target(_target())
    assert sancheong.is_target(_target(url=sancheong.SANCHEONG_URL))
    assert sancheong.is_target(
        _target(
            url=(
                f"https://{sancheong.SANCHEONG_HOST}{sancheong.SANCHEONG_LEGACY_PATH}"
                "?srcField=AREA05&key=51"
            )
        )
    )
    assert not sancheong.is_target(_target(provider="OTHER"))
    assert not sancheong.is_target(_target(url=f"{sancheong.SANCHEONG_URL}?cpage=2"))
    assert not sancheong.is_target(
        _target(
            url=(
                f"https://{sancheong.SANCHEONG_HOST}{sancheong.SANCHEONG_LEGACY_PATH}"
                "?key=51&srcField=AREA09"
            )
        )
    )
    assert not sancheong.is_target(
        _target(url=f"http://{sancheong.SANCHEONG_HOST}{sancheong.SANCHEONG_LIST_PATH}")
    )
    assert set(sancheong.SANCHEONG_CANDIDATE_DECISIONS) == {
        "MUNI_IR_46E931CCD66E",
        "MUNI_IR_121A0D1312F1",
        "MUNI_IR_BCE09FA07259",
        "MUNI_IR_2040BF0DC291",
    }
    assert sancheong.SANCHEONG_CANDIDATE_DECISIONS[
        "MUNI_IR_46E931CCD66E"
    ].startswith("include_existing_owner")
    assert all(
        sancheong.SANCHEONG_CANDIDATE_DECISIONS[identity].startswith("exclude_")
        for identity in (
            "MUNI_IR_121A0D1312F1",
            "MUNI_IR_BCE09FA07259",
            "MUNI_IR_2040BF0DC291",
        )
    )
    for alias in sancheong.SANCHEONG_ALIASES:
        assert sancheong.is_gyeongnam_sancheong_alias_target(
            {"provider": alias.provider, "url": alias.url}
        )
        assert not sancheong.is_target({"provider": alias.provider, "url": alias.url})


def test_complete_snapshot_clamp_boundaries_details_controls_branches_and_privacy() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == sancheong.SANCHEONG_PARSER
    assert len(rows) == 5
    assert meta["declared_total"] == meta["source_total"] == 10
    assert meta["data_pages"] == 2
    assert meta["required_list_requests"] == meta["list_requests"] == 5
    assert meta["sentinel_mode"] == "clamped_last_page"
    assert meta["sentinel_count"] == 1
    assert meta["stable_rechecks"] == {"1": True, "2": True}
    assert meta["current_count"] == meta["detail_attempts"] == 5
    assert meta["expired_count"] == 5
    assert meta["duplicate_source_id_count"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["status_counts"] == {"OPEN": 2, "SCHEDULED": 1, "CLOSED": 2}
    assert meta["application_type_counts"] == {
        "ONLINE_RESERVATION": 2,
        "INFO_ONLY": 3,
    }
    assert meta["branch_count"] == 5
    assert set(meta["branch_counts"]) == {
        "산청군 평생학습센터",
        "산청읍",
        "단성면",
        "청소년수련관",
        "농업인 정보화교육장",
    }

    active = [row for row in rows if row["reservation_available"]]
    assert len(active) == 2
    assert all("amode=agree" in row["application_url"] for row in active)
    assert all(row["application_type"] == "ONLINE_RESERVATION" for row in active)
    assert all(row["raw_fields"]["application_form_fetched"] is False for row in rows)
    assert all(row["raw_fields"]["applicant_endpoint_fetched"] is False for row in rows)
    assert all(row["raw_fields"]["detail_validated"] is True for row in rows)
    assert not any("amode=agree" in url for url in site.calls)
    assert not any(sancheong.SANCHEONG_APPLICANT_PATH in url for url in site.calls)

    serialized = repr(rows)
    for excluded in (
        "010-1234-5678",
        "010-8888-9999",
        "055-970-1234",
        "secret@example.kr",
        "김강사",
        "김담당",
        "홍*동",
        "경상남도 산청군 중앙로 1",
    ):
        assert excluded not in serialized


@pytest.mark.parametrize(
    ("site", "message"),
    (
        (FakeSite(bad_clamp=True), "post-last clamp"),
        (FakeSite(mutate_recheck=True), "stable boundary recheck"),
    ),
)
def test_boundary_or_clamp_change_fails_closed(site: FakeSite, message: str) -> None:
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("option", "message"),
    (
        ({"wrong_title": True}, "title mismatch"),
        ({"wrong_period": True}, "education period mismatch"),
        ({"wrong_status": True}, "status mismatch"),
        ({"empty_institution": True}, "education institution is empty"),
        ({"wrong_methods": True}, "application method markers mismatch"),
    ),
)
def test_detail_identity_vocabulary_or_period_change_fails_closed(
    option: Mapping[str, bool], message: str
) -> None:
    rows, _, meta = _collect(FakeSite(**option))
    assert rows == []
    assert meta["details_complete"] is False
    assert "detail LT000010" in meta["configured_collection_error"]
    assert message in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("option", "message"),
    (
        ({"missing_application": True}, "public application control changed"),
        ({"wrong_application": True}, "application route is not course-bound"),
        ({"inactive_application": True}, "inactive course exposes application control"),
    ),
)
def test_public_application_control_must_be_active_and_course_bound(
    option: Mapping[str, bool], message: str
) -> None:
    rows, _, meta = _collect(FakeSite(**option))
    assert rows == []
    assert message in meta["configured_collection_error"]


def test_course_bound_wait_application_control_is_accepted() -> None:
    rows, _, meta = _collect(FakeSite(wait_application=True))

    assert meta["snapshot_complete"] is True
    row = next(item for item in rows if item["raw_fields"]["source_lecture_id"] == "LT000010")
    assert row["reservation_available"] is True
    assert row["application_url"] == sancheong.sancheong_application_url("LT000010")
    assert row["raw_fields"]["application_control_label"] == "대기접수"


def test_caps_duplicate_dedupe_loss_and_pii_leak_fail_closed() -> None:
    rows, _, meta = _collect(FakeSite(), max_pages=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _, meta = _collect(FakeSite(), detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _, meta = _collect(FakeSite(duplicate_source_id=True))
    assert rows == []
    assert meta["duplicate_source_id_count"] == 1
    assert "duplicate source identities" in meta["configured_collection_error"]

    rows, _, meta = _collect(FakeSite(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]

    def leak_phone(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**row, "description": f"{row['description']} 010-7777-8888"} for row in values]

    rows, _, meta = _collect(FakeSite(), dedupe_rows=leak_phone)
    assert rows == []
    assert meta["privacy_violations"] > 0
    assert "PII allowlist violations" in meta["configured_collection_error"]


def test_complete_catalogue_can_validly_have_no_current_rows() -> None:
    site = FakeSite()
    rows, _, meta = sancheong.collect(
        _target(),
        session_factory=DummySession,
        fetcher=site,
        today="2100-01-01",
    )
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["details_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]
    assert meta["detail_attempts"] == 0


def test_invalid_target_missing_managed_session_and_blocked_routes() -> None:
    rows, _, meta = sancheong.collect(_target(provider="OTHER"))
    assert rows == []
    assert "target does not match" in meta["configured_collection_error"]

    rows, _, meta = sancheong.collect(_target())
    assert rows == []
    assert meta["configured_collection_error"] == (
        "managed session_factory injection is required"
    )

    assert sancheong._allowed_request_url(sancheong.sancheong_list_url(2))
    assert sancheong._allowed_request_url(sancheong.sancheong_detail_url("LT000001"))
    assert not sancheong._allowed_request_url(
        sancheong.sancheong_application_url("LT000001")
    )
    assert not sancheong._allowed_request_url(
        f"https://{sancheong.SANCHEONG_HOST}{sancheong.SANCHEONG_APPLICANT_PATH}"
    )
