from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_gyeongnam_hadong as hadong


def _record(
    identity: str,
    *,
    title: str,
    organization: str,
    status: str,
    period: str,
    apply: str,
    method: str = "온라인신청",
    schedule: str = "매주 화요일 10:00~12:00",
    waiting: bool = True,
) -> dict[str, Any]:
    return {
        "id": identity,
        "title": title,
        "organization": organization,
        "status": status,
        "period": period,
        "apply": apply,
        "method": method,
        "waiting": waiting,
        "capacity": "1 / 20 명",
        "wait_capacity": "0 / 5 명",
        "schedule": schedule,
        "target": "하동군민",
        "fee": "무료",
        "venue": f"{organization} 강의실",
    }


RECORDS = (
    _record(
        "210",
        title="온라인 미래 강좌",
        organization="평생학습",
        status="접수중",
        period="2099.08.01. ~ 2099.08.31",
        apply="2099.07.01. ~ 2099.07.31",
        method="온라인신청 , 전화접수",
        schedule="",
    ),
    _record(
        "209",
        title="대기자 미래 강좌",
        organization="교육혁신",
        status="대기접수",
        period="2099.08.02. ~ 2099.09.01",
        apply="2099.07.01. ~ 2099.07.31",
    ),
    _record(
        "208",
        title="접수 예정 강좌",
        organization="화개면",
        status="접수대기",
        period="2099.09.01. ~ 2099.09.30",
        apply="2099.08.01. ~ 2099.08.20",
        method="신청바로가기",
    ),
    _record(
        "207",
        title="오늘 끝나는 마감 강좌",
        organization="진교면",
        status="접수마감",
        period="2099.07.10. ~ 2099.07.20",
        apply="2099.06.01. ~ 2099.06.30",
        method="",
        waiting=False,
    ),
    _record(
        "206",
        title="교육 중 강좌",
        organization="하동군청소년수련관",
        status="교육중",
        period="2099.06.01. ~ 2099.12.31",
        apply="2099.05.01. ~ 2099.05.20",
        method="온라인신청 , 전화접수 , 내방접수",
    ),
    *(
        _record(
            str(identity),
            title=f"종료 강좌 {identity}",
            organization="적량면",
            status="교육종료",
            period="2098.01.01. ~ 2098.02.01",
            apply="2097.12.01. ~ 2097.12.20",
        )
        for identity in range(205, 200, -1)
    ),
)


def _target(
    *,
    provider: str = hadong.HADONG_PROVIDER,
    url: str = hadong.HADONG_CONFIGURED_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "하동군 교육",
        "branch": "경상남도 하동군",
    }


def _search_form(requested_page: int) -> str:
    options = "".join(
        f'<option value="{value}">{label}</option>'
        for value, label in hadong._DONG_OPTIONS.items()
    )
    action_query = (
        ""
        if requested_page == 1
        else f"?facCode={hadong.HADONG_FAC_CODE}&cpage={requested_page}"
    )
    return f"""
      <form id="frmLecture" name="frmLecture" method="get"
        action="{hadong.HADONG_LIST_PATH}{action_query}">
        <input type="hidden" name="facCode" value="{hadong.HADONG_FAC_CODE}">
        <select name="dong"><option selected value="">읍면동 선택</option>{options}</select>
        <select name="target"><option selected value="">대상</option>
          <option value="004">성인</option></select>
        <select name="stype"><option value="title">과정명</option></select>
        <input name="sstring" value="">
      </form>
    """


def _control(record: Mapping[str, Any], *, page: int) -> str:
    suffix = f"&cpage={page}" if page > 1 else ""
    if record["status"] in {"접수중", "대기접수"}:
        label = "교육신청" if record["status"] == "접수중" else "대기자신청"
        return (
            f'<a data-online="Y" href="?amode=ins&lecIdx={record["id"]}'
            f'&facCode={hadong.HADONG_FAC_CODE}{suffix}">{label}</a>'
        )
    if record["status"] == "접수대기":
        return '<a data-online="N">접수대기</a>'
    return ""


def _card(
    record: Mapping[str, Any],
    *,
    page: int,
    title: str | None = None,
) -> str:
    suffix = f"&cpage={page}" if page > 1 else ""
    waiting = (
        f'<li>대기정원 : {record["wait_capacity"]}</li>'
        if record["waiting"]
        else ""
    )
    return f"""
      <li class="column"><div class="w1">
        <div class="tg1 hybrid3row2">
          <div><strong class="t1">{title or record['title']}</strong></div>
          <div><b class="t2">{record['status']}</b></div>
        </div>
        <div class="tg1"><ul>
          <li>운영기관 : {record['organization']}</li>
          <li>교육기간 : {record['period']}</li>
          <li>접수기간 : {record['apply']}</li>
          <li>신청방법 : {record['method']}</li>
          <li>접수정원 : {record['capacity']}</li>
          {waiting}
          <li>문의전화 : 055-880-1234</li>
          <li>요일/시간 : {record['schedule']}</li>
          <li>교육대상 : {record['target']}</li>
          <li>수강료 : {record['fee']}</li>
        </ul></div>
        <div class="btns">
          {_control(record, page=page)}
          <a href="?amode=view&idx={record['id']}&facCode={hadong.HADONG_FAC_CODE}{suffix}">상세보기</a>
        </div>
      </div></li>
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
            page=requested_page,
            title="변경된 경계 강좌" if mutate_title and index == 0 else None,
        )
        for index, record in enumerate(records)
    )
    return f"""
      <html><body><div id="body_content">
        {_search_form(requested_page)}
        <div class="info1">총 10 건의 교육이 있습니다. ( {displayed_page} /2 페이지)</div>
        <div class="card1t3b1"><div class="wrap1"><ul class="even-grid">
          {cards}
        </ul></div></div>
      </div></body></html>
    """


def _detail_html(
    record: Mapping[str, Any],
    *,
    wrong_title: bool = False,
    wrong_period: bool = False,
) -> str:
    title = "다른 강좌" if wrong_title else record["title"]
    period = "2099.01.01~2099.01.02" if wrong_period else record["period"]
    waiting = (
        f'<li>대기정원 : {record["wait_capacity"]}</li>'
        if record["waiting"]
        else ""
    )
    return f"""
      <html><body>
        <div id="body_content">
          <div class="view1pic1info1"><div class="texts">
            <h1 class="h1">{title}</h1>
            <div class="info1"><div class="panel10">
              <ul>
                <li>운영기관 : {record['organization']}</li>
                <li>교육기간 : {period}</li>
                <li>접수기간 : {record['apply']}</li>
                <li>문의전화 : 010-1234-5678</li>
                <li>교육대상 : {record['target']}</li>
                <li>교육장소 : {record['venue']}</li>
                <li>요일/시간 : {record['schedule']}</li>
                <li>수강료 : {record['fee']}</li>
                <li>접수정원 : {record['capacity']}</li>
                {waiting}
                <li>신청방법 : {record['method']}</li>
                <li>강의자료 : <a href="/Download.do?name=plan.hwp">계획서</a></li>
                <li>준비물 : 담당자에게 문의 010-9999-9999</li>
                <li>강사명 : 김강사</li>
              </ul>
            </div></div>
          </div></div>
          <div class="panel0">저장하면 안 되는 자유 본문 010-8888-8888</div>
        </div>
        <table class="applicants"><tr><th>신청자</th></tr><tr><td>김OO</td></tr></table>
      </body></html>
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
        wrong_title: bool = False,
        wrong_period: bool = False,
        duplicate_source_id: bool = False,
    ) -> None:
        self.bad_clamp = bad_clamp
        self.mutate_recheck = mutate_recheck
        self.wrong_title = wrong_title
        self.wrong_period = wrong_period
        self.duplicate_source_id = duplicate_source_id
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
        if query.get("amode") == ["ins"]:
            raise AssertionError("application form must never be fetched")
        if query.get("amode") == ["view"]:
            identity = query["idx"][0]
            record = next(item for item in RECORDS if item["id"] == identity)
            html = _detail_html(
                record,
                wrong_title=self.wrong_title and identity == "210",
                wrong_period=self.wrong_period and identity == "210",
            )
            return BeautifulSoup(html, "lxml"), url

        requested = int((query.get("cpage") or ["1"])[0])
        self.page_calls[requested] += 1
        if requested == 1:
            rows = RECORDS[:9]
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
        return (
            BeautifulSoup(
                _list_html(
                    requested_page=requested,
                    displayed_page=displayed,
                    records=tuple(rows),
                    mutate_title=mutate,
                ),
                "lxml",
            ),
            url,
        )


def _collect(
    site: FakeSite,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    return hadong.collect(
        _target(),
        session_factory=DummySession,
        fetcher=site,
        today="2099-07-20",
        **kwargs,
    )


def test_target_scope_candidate_decisions_and_duplicate_aliases() -> None:
    assert hadong.is_target(_target())
    assert hadong.is_target(_target(url=hadong.HADONG_URL))
    assert hadong.is_target(
        _target(url=f"{hadong.HADONG_URL}?facCode={hadong.HADONG_FAC_CODE}")
    )
    assert not hadong.is_target(_target(provider="OTHER"))
    assert not hadong.is_target(_target(url=f"{hadong.HADONG_URL}?cpage=2"))
    assert not hadong.is_target(_target(url="http://www.hadong.go.kr/edu.web"))
    assert hadong.HADONG_CANDIDATE_DECISIONS["MUNI_IR_174FEF33F767"].startswith(
        "include_existing_owner"
    )
    assert hadong.HADONG_CANDIDATE_DECISIONS["MUNI_IR_5A5CE379E392"].startswith(
        "exclude_general_notice"
    )
    for alias in hadong.HADONG_ALIASES:
        assert hadong.is_gyeongnam_hadong_alias_target(
            {"provider": alias.provider, "url": alias.url}
        )
        assert not hadong.is_target({"provider": alias.provider, "url": alias.url})


def test_complete_snapshot_clamp_boundaries_details_controls_and_privacy() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == hadong.HADONG_PARSER
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
    assert sum(meta["branch_counts"].values()) == 5

    active = [row for row in rows if row["reservation_available"]]
    assert len(active) == 2
    assert all("amode=ins" in row["application_url"] for row in active)
    assert all(row["application_type"] == "ONLINE_RESERVATION" for row in active)
    assert all(row["raw_fields"]["application_form_fetched"] is False for row in rows)
    assert all(row["raw_fields"]["detail_validated"] is True for row in rows)
    assert not any("amode=ins" in url for url in site.calls)
    blank_time = next(
        row for row in rows if row["provider_course_id"].endswith(":210")
    )
    assert blank_time["schedule_raw"] == "시간 별도 안내"
    assert blank_time["raw_fields"]["source_schedule"] == ""

    serialized = repr(rows)
    for excluded in (
        "010-1234-5678",
        "010-9999-9999",
        "010-8888-8888",
        "김강사",
        "김OO",
        "계획서",
        "저장하면 안 되는 자유 본문",
    ):
        assert excluded not in serialized


def test_empty_application_method_is_rejected_for_an_open_course() -> None:
    records = ({**RECORDS[0], "method": ""}, *RECORDS[1:9])
    soup = BeautifulSoup(
        _list_html(requested_page=1, displayed_page=1, records=records),
        "lxml",
    )

    rows, errors = hadong._parse_list_page(soup, source_page=1)

    assert len(rows) == 8
    assert "page 1 row 1: unknown application method" in errors


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


@pytest.mark.parametrize("mode", ("wrong_title", "wrong_period"))
def test_detail_identity_or_period_change_fails_closed(mode: str) -> None:
    site = FakeSite(**{mode: True})
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["details_complete"] is False
    assert "detail 210" in meta["configured_collection_error"]


def test_caps_duplicate_and_dedupe_loss_fail_closed() -> None:
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


def test_complete_catalogue_can_validly_have_no_current_rows() -> None:
    site = FakeSite()
    rows, _, meta = hadong.collect(
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


def test_invalid_target_or_missing_managed_session_is_rejected() -> None:
    rows, _, meta = hadong.collect(_target(provider="OTHER"))
    assert rows == []
    assert "target does not match" in meta["configured_collection_error"]

    rows, _, meta = hadong.collect(_target())
    assert rows == []
    assert meta["configured_collection_error"] == (
        "managed session_factory injection is required"
    )
