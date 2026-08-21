from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_imsil as imsil


def _record(
    identity: str,
    title: str,
    branch: str,
    *,
    receipt_status: str = "접수종료",
    event_status: str = "행사종료",
    apply: str = "2024.01.01 ~ 2024.01.02",
    event: str = "2024.01.03 ~ 2024.01.03",
    current: int = 0,
    wait: int = 0,
    capacity: int = 10,
    target: str = "임실군민",
    venue: str = "임실군립도서관",
    schedule: str = "수 10:00~12:00",
    fee: str = "무료",
    method: str = "인터넷",
    control: str = "closed",
) -> dict[str, Any]:
    return {
        "id": identity,
        "title": title,
        "branch": branch,
        "receipt_status": receipt_status,
        "event_status": event_status,
        "apply": apply,
        "event": event,
        "current": current,
        "wait": wait,
        "capacity": capacity,
        "target": target,
        "venue": venue,
        "schedule": schedule,
        "fee": fee,
        "method": method,
        "control": control,
    }


RECORDS = (
    _record(
        "768",
        "지사랑 작은도서관 <지구를 살리는 업사이클링>",
        "지사랑",
        receipt_status="접수진행",
        event_status="행사대기",
        apply="2026.07.22 ~ 2026.07.27",
        event="2026.07.28 ~ 2026.07.29",
        capacity=14,
        target="초1-중3",
        venue="지사랑 작은도서관",
        schedule="화 목",
        method="방문/접수",
        control="none",
    ),
    _record(
        "767",
        "[문화가 있는 날- 7월] 나만의 그림책, 한 권",
        "오수",
        receipt_status="접수종료",
        event_status="행사진행",
        apply="2026.07.08 ~ 2026.07.21",
        event="2026.07.22 ~ 2026.07.29",
        target="고등학생 이상",
        venue="오수도서관 3층 프로그램실",
        schedule="수",
        method="인터넷",
        control="login",
    ),
    _record(
        "764",
        imsil.IMSIL_EMPTY_APPLICATION_PERIODS["764"],
        "무지개빛",
        apply="~",
        event="2026.05.18 ~ 2026.05.18",
        capacity=13,
        target="성인(남)",
        venue="무지개빛 작은도서관",
    ),
    _record(
        "761",
        "2026년 이야기꽃할머니(책놀이지도사 자격증반) 모집 안내",
        "임실",
        receipt_status="접수종료",
        event_status="행사진행",
        apply="2026.04.17 ~ 2026.05.14",
        event="2026.05.15 ~ 2026.10.16",
        current=21,
        capacity=10,
        target="55세이상 여성",
        venue="군립도서관",
        schedule="격주 금, 오전 10시",
        method="인터넷",
        control="closed",
    ),
    _record(
        "760",
        imsil.IMSIL_NONCOURSE_IDENTITIES["760"],
        "전체",
        receipt_status="접수진행",
        event_status="행사진행",
        apply="2026.05.04 ~ 2026.11.01",
        event="2026.05.04 ~ 2026.11.01",
        current=28,
        capacity=35,
        target="36개월~취학 전",
        venue="군립도서관(임실, 오수)",
        control="login",
    ),
    _record(
        "759",
        imsil.IMSIL_NONCOURSE_IDENTITIES["759"],
        "전체",
        receipt_status="접수진행",
        event_status="행사진행",
        apply="2026.05.04 ~ 2026.11.01",
        event="2026.05.04 ~ 2026.11.01",
        current=9,
        capacity=25,
        target="19~35개월",
        venue="군립도서관(임실, 오수)",
        control="login",
    ),
    _record(
        "758",
        imsil.IMSIL_NONCOURSE_IDENTITIES["758"],
        "전체",
        receipt_status="접수진행",
        event_status="행사진행",
        apply="2026.05.04 ~ 2026.11.01",
        event="2026.05.04 ~ 2026.11.01",
        current=20,
        capacity=20,
        target="임신부, 0~18개월",
        venue="군립도서관(임실, 오수)",
        control="closed",
    ),
    _record("701", imsil.IMSIL_NONCOURSE_IDENTITIES["701"], "전체"),
    _record("700", imsil.IMSIL_NONCOURSE_IDENTITIES["700"], "전체"),
    _record("699", imsil.IMSIL_NONCOURSE_IDENTITIES["699"], "전체"),
    _record(
        "556",
        imsil.IMSIL_EMPTY_APPLICATION_PERIODS["556"],
        "아낌없이",
        apply="~",
        event="2022.06.23 ~ 2022.06.23",
        capacity=8,
        target="성인",
        venue="아낌없이주는나무 작은도서관",
    ),
    _record(
        "550",
        imsil.IMSIL_ONE_SIDED_EVENT_PERIODS["550"],
        "임실",
        apply="2022.04.05 ~ 2022.04.19",
        event="2022.04.23 ~",
        current=13,
        capacity=15,
    ),
    _record(
        "549",
        imsil.IMSIL_NONCOURSE_IDENTITIES["549"],
        "전체",
        apply="2022.04.05 ~ 2022.04.19",
        event="2022.04.20 ~",
        current=129,
        capacity=70,
    ),
)


def _target(
    *, provider: str = imsil.IMSIL_PROVIDER, url: str = imsil.IMSIL_URL
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "임실군립도서관",
        "branch": imsil.IMSIL_MUNICIPALITY_NAME,
    }


def _filter_rows(source_filter: str) -> list[dict[str, Any]]:
    if source_filter == "A":
        return [row for row in RECORDS if row["branch"] in imsil.IMSIL_COUNTY_BRANCHES]
    if source_filter == "B":
        return [row for row in RECORDS if row["branch"] in imsil.IMSIL_SMALL_BRANCHES]
    return list(RECORDS)


def _status_class(status: str, *, receipt: bool) -> str:
    if status.endswith("종료"):
        return "eventBtn3"
    if status.endswith("대기"):
        return "eventBtn1"
    return "eventBtn2"


def _card(record: Mapping[str, Any], *, title: str | None = None) -> str:
    wait = f"({record['wait']})" if record["wait"] else ""
    marker = '<span class="new">NEW</span>' if record["id"] == "768" else ""
    return f"""
      <li>
        <a href="#this" name="title" onclick="return false;">
          <span class="{imsil.IMSIL_BRANCH_CLASSES[str(record['branch'])]}">{record['branch']}</span>
          <b class="eventBtn {_status_class(str(record['receipt_status']), receipt=True)}">{record['receipt_status']}</b>
          <b class="eventBtn {_status_class(str(record['event_status']), receipt=False)}">{record['event_status']}</b>
          <h3>{title or record['title']}{marker}</h3>
          <ol>
            <li><dl><dt>접수</dt><dd>{record['apply']}</dd></dl></li>
            <li><dl><dt>수강</dt><dd>{record['event']}</dd></dl></li>
            <li><dl><dt>접수현황</dt><dd>{record['current']}{wait} / {record['capacity']}</dd></dl></li>
            <li><dl><dt>대상</dt><dd>{record['target']}</dd></dl></li>
          </ol>
        </a>
        <input id="IDX" type="hidden" value="{record['id']}">
        <input id="BBS_ID" type="hidden" value="{imsil.IMSIL_BBS_ID}">
      </li>
    """


def _tabs(source_filter: str) -> str:
    values = []
    for code, label in imsil.IMSIL_FILTERS.items():
        active = ' class="active"' if code == source_filter else ""
        values.append(
            f'<li{active}><a href="#this" name="libClassClick" '
            f'onclick="return false;">{label}</a>'
            f'<input id="AGENCY_CLASS_CD" type="hidden" value="{code}"></li>'
        )
    return f'<ul class="sub_tab_nav">{"".join(values)}</ul>'


def _pager(page: int, last_page: int, *, sentinel: bool) -> str:
    links = []
    for value in range(1, last_page + 1):
        focus = ' class="focus"' if value == page and not sentinel else ""
        links.append(
            f'<li><a{focus} href="#none" '
            f'onclick="javascript:fn_movePage(\'{value}\'); return false;">'
            f"{value}</a></li>"
        )
    return f'<div class="paging"><ol>{"".join(links)}</ol></div>'


def _list_html(
    source_filter: str,
    page: int,
    *,
    title_overrides: Mapping[str, str] | None = None,
    sentinel_record: Mapping[str, Any] | None = None,
    duplicate_first: bool = False,
    bad_status: bool = False,
) -> str:
    rows = _filter_rows(source_filter)
    total = len(rows)
    last_page = max(1, (total + imsil.IMSIL_PAGE_SIZE - 1) // imsil.IMSIL_PAGE_SIZE)
    selected = rows[(page - 1) * imsil.IMSIL_PAGE_SIZE : page * imsil.IMSIL_PAGE_SIZE]
    if page > last_page:
        selected = [dict(sentinel_record)] if sentinel_record is not None else []
    else:
        selected = [dict(row) for row in selected]
    if duplicate_first and len(selected) > 1:
        selected[1]["id"] = selected[0]["id"]
    if bad_status and selected:
        selected[0]["receipt_status"] = "접수알수없음"
    overrides = dict(title_overrides or {})
    cards = "".join(
        _card(row, title=overrides.get(str(row["id"]))) for row in selected
    )
    return f"""
      <html><body><div class="contents"><div class="subTapBox">
        {_tabs(source_filter)}
        <div class="sub_tab"><div class="boardSearch">
          <p>전체 <span class="bold">{total}</span>건 · 현재페이지
             <span class="bold">{page}/1</span></p>
          <form name="frm">
            <input name="pageNo" type="hidden" value="{page}">
            <input name="searchFiled" type="hidden" value="">
            <input name="searchValue" type="hidden" value="">
            <input name="agencyClassCd" type="hidden" value="{source_filter}">
            <input name="agencyCd" type="hidden" value="000000">
            <select name="searchS"><option value="SUBJECT">제목</option><option value="CONTENT">내용</option></select>
            <input name="searchI" value="">
          </form>
        </div><ul class="eventListBox">{cards}</ul>
        {_pager(page, last_page, sentinel=page > last_page)}
        </div>
      </div></div></body></html>
    """


def _detail_html(
    record: Mapping[str, Any],
    *,
    title_override: str = "",
    branch_override: str = "",
    control_override: str = "",
    route_override: str = "",
) -> str:
    branch = branch_override or str(record["branch"])
    control = control_override or str(record["control"])
    controls = {
        "none": "",
        "login": '<a class="btn1" href="#" id="applyLogin">신청</a>',
        "closed": '<a class="btn3" href="#">마감</a>',
    }[control]
    application_path = route_override or imsil.IMSIL_APPLICATION_PATH
    return f"""
      <html><body><div class="contents"><div class="tableBox">
        <table class="tbView"><tbody>
          <tr><td class="PrTitle" colspan="4">
            <span class="viewBul {imsil.IMSIL_BRANCH_CLASSES[branch]}">{branch}</span>
            <b class="eventBtn {_status_class(str(record['receipt_status']), receipt=True)}">{record['receipt_status']}</b>
            <b class="eventBtn {_status_class(str(record['event_status']), receipt=False)}">{record['event_status']}</b>
            <br>{title_override or record['title']}
          </td></tr>
          <tr><th>접수기간</th><td colspan="3">{record['apply']}</td></tr>
          <tr><th>운영기간</th><td>{record['event']}</td><th>운영장소</th><td>{record['venue']}</td></tr>
          <tr><th>운영시간</th><td>{record['schedule']}</td><th>수강료 및 재료비</th><td>{record['fee']}</td></tr>
          <tr><th>신청대상</th><td>{record['target']}</td><th>접수방법</th><td>{record['method']}</td></tr>
          <tr><th>신청인원</th><td>{record['current']} / {record['capacity']} (신청 / 정원)</td><th>문의처</th><td>063-640-3063</td></tr>
          <tr><th>대기인원</th><td colspan="3">{record['wait']} / 10 (신청 / 정원)</td></tr>
          <tr><th>강의계획서</th><td colspan="3">teacher@example.org 계획서.hwp</td></tr>
          <tr><td class="tbl_cnts" colspan="4">본문 010-1234-5678 private@example.org</td></tr>
        </tbody></table>
      </div><div class="rightBox"><a class="btn2" href="#this" id="list">목록</a>{controls}</div>
      <script>
        var mi = 'MN0131'; var acc = 'Z'; var ik = '{record['id']}';
        var bbsId = '{imsil.IMSIL_BBS_ID}';
        var applicationRoute = '{application_path};jsessionid=TEST?mi=' + mi;
        var loginRoute = '{imsil.IMSIL_LOGIN_PATH};jsessionid=TEST?mi=MN0143';
      </script></div></body></html>
    """


class _Session:
    def __init__(self, closed: list[bool]):
        self._closed = closed

    def close(self) -> None:
        self._closed.append(True)


class FixtureSource:
    def __init__(self, **options: Any):
        self.options = options
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.counts: Counter[tuple[str, int]] = Counter()
        self.closed: list[bool] = []
        self.failures_left = int(options.get("failures", 0))

    def session_factory(self) -> _Session:
        return _Session(self.closed)

    def __call__(
        self,
        session: Any,
        method: str,
        url: str,
        *,
        timeout: int,
        data: Mapping[str, str],
    ) -> str:
        del session, timeout
        payload = dict(data)
        self.calls.append((method, url, payload))
        if self.failures_left:
            self.failures_left -= 1
            raise TimeoutError("synthetic transient failure")
        if method == "POST":
            source_filter = payload["agencyClassCd"]
            page = int(payload["pageNo"])
            self.counts[(source_filter, page)] += 1
            overrides: dict[str, str] = {}
            if (
                self.options.get("mutate_first_recheck")
                and source_filter == "Z"
                and page == 1
                and self.counts[(source_filter, page)] >= 2
            ):
                overrides["768"] = "재검사 중 바뀐 제목"
            if self.options.get("partition_mismatch") and source_filter == "B":
                overrides["768"] = "필터에서 바뀐 제목"
            if self.options.get("noncourse_title_drift") and source_filter == "Z":
                overrides["760"] = "재사용된 교육 강좌"
            sentinel_record = None
            if self.options.get("nonempty_sentinel") and source_filter == "Z":
                last = (len(_filter_rows("Z")) + 9) // 10
                if page == last + 1:
                    sentinel_record = RECORDS[-1]
            return _list_html(
                source_filter,
                page,
                title_overrides=overrides,
                sentinel_record=sentinel_record,
                duplicate_first=bool(
                    self.options.get("duplicate_identity")
                    and source_filter == "Z"
                    and page == 1
                ),
                bad_status=bool(
                    self.options.get("bad_status")
                    and source_filter == "Z"
                    and page == 1
                ),
            )
        if method != "GET":
            raise AssertionError(method)
        query = parse_qs(urlparse(url).query)
        identity = query["ik"][0]
        record = next(row for row in RECORDS if row["id"] == identity)
        return _detail_html(
            record,
            title_override=(
                "상세에서 바뀐 제목"
                if self.options.get("detail_title_drift") and identity == "768"
                else ""
            ),
            branch_override=(
                "무지개빛"
                if self.options.get("detail_branch_drift") and identity == "768"
                else ""
            ),
            control_override=(
                "login"
                if self.options.get("full_control_drift") and identity == "758"
                else ""
            ),
            route_override=(
                "/changed/application.do"
                if self.options.get("application_route_drift") and identity == "768"
                else ""
            ),
        )


def _collect(source: FixtureSource, **kwargs: Any):
    return imsil.collect(
        _target(),
        today="2026-07-23",
        max_workers=1,
        session_factory=source.session_factory,
        fetcher=source,
        **kwargs,
    )


def test_complete_snapshot_reconciles_partitions_details_controls_and_privacy() -> None:
    source = FixtureSource()
    rows, parser, meta = _collect(source)

    assert parser == imsil.IMSIL_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["ledger_totals"] == {"Z": 13, "A": 3, "B": 3}
    assert meta["ledger_pages"] == {"Z": 2, "A": 1, "B": 1}
    assert meta["sentinel_pages"] == {"Z": 3, "A": 2, "B": 2}
    assert meta["sentinel_counts"] == {"Z": 0, "A": 0, "B": 0}
    assert all(meta["stable_rechecks"].values())
    assert meta["source_total"] == 13
    assert meta["education_total"] == 6
    assert meta["current_raw_count"] == 6
    assert meta["current_count"] == 3
    assert meta["excluded_current_count"] == 3
    assert meta["expired_count"] == 3
    assert meta["global_only_ids"] == ["760", "759", "758", "701", "700", "699", "549"]
    assert meta["empty_application_period_ids"] == ["764", "556"]
    assert meta["one_sided_event_period_ids"] == ["550", "549"]
    assert meta["detail_pages"] == 6
    assert meta["detail_login_control_count"] == 3
    assert meta["stale_login_control_count"] == 1
    assert meta["excluded_noncourse_login_control_count"] == 2
    assert meta["capacity_close_override_count"] == 1
    assert meta["online_application_count"] == 0
    assert meta["offline_application_count"] == 1
    assert meta["application_endpoint_fetches"] == 0
    assert meta["list_requests"] == 14
    assert meta["logical_requests"] == 20
    assert meta["physical_requests"] == 20

    assert [row["provider_course_id"].rsplit(":", 1)[-1] for row in rows] == [
        "768",
        "767",
        "761",
    ]
    assert [row["branch"] for row in rows] == [
        "지사랑 작은도서관",
        "임실군립오수도서관",
        "임실군립도서관",
    ]
    assert [row["status"] for row in rows] == ["OPEN", "CLOSED", "CLOSED"]
    assert [row["application_type"] for row in rows] == [
        "OFFLINE_APPLICATION",
        "INFORMATION_ONLY",
        "INFORMATION_ONLY",
    ]
    assert not any(row["reservation_available"] for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "5275000000" for row in rows)
    material = repr(rows)
    assert "063-640-3063" not in material
    assert "010-1234-5678" not in material
    assert "teacher@example.org" not in material
    assert "private@example.org" not in material
    assert all(method in {"GET", "POST"} for method, _, _ in source.calls)
    assert not any(imsil.IMSIL_APPLICATION_PATH in url for _, url, _ in source.calls)
    assert not any(imsil.IMSIL_LOGIN_PATH in url for _, url, _ in source.calls)
    assert len(source.closed) == 7


def test_provider_hashes_owner_decision_and_wrong_targets_do_not_fetch() -> None:
    assert hashlib.sha1(imsil.IMSIL_URL.encode()).hexdigest().upper() == imsil.IMSIL_PROVIDER_URL_SHA1
    assert hashlib.sha256(imsil.IMSIL_URL.encode()).hexdigest().upper() == imsil.IMSIL_CANONICAL_URL_SHA256
    assert hashlib.sha1(imsil.IMSIL_INCUMBENT_URL.encode()).hexdigest().upper() == imsil.IMSIL_INCUMBENT_URL_SHA1
    assert hashlib.sha256(imsil.IMSIL_INCUMBENT_URL.encode()).hexdigest().upper() == imsil.IMSIL_INCUMBENT_URL_SHA256
    assert imsil.IMSIL_PROVIDER.endswith(imsil.IMSIL_PROVIDER_URL_SHA1[:8])
    assert imsil.IMSIL_CANONICAL_CANDIDATE_ID.endswith(imsil.IMSIL_CANONICAL_URL_SHA256[:12])
    assert "deactivate" in imsil.IMSIL_INCUMBENT_DECISION
    assert "do not retarget" in imsil.IMSIL_INCUMBENT_DECISION

    for target in (
        _target(provider=imsil.IMSIL_INCUMBENT_PROVIDER, url=imsil.IMSIL_INCUMBENT_URL),
        _target(provider=imsil.IMSIL_PROVIDER, url=imsil.IMSIL_URL + "&x=1"),
        _target(provider="OTHER", url=imsil.IMSIL_URL),
    ):
        source = FixtureSource()
        rows, _, meta = imsil.collect(
            target, session_factory=source.session_factory, fetcher=source
        )
        assert rows == []
        assert "outside canonical Imsil scope" in meta["configured_collection_error"]
        assert source.calls == []


def test_page_and_detail_caps_fail_closed_without_partial_rows() -> None:
    page_source = FixtureSource()
    rows, _, meta = _collect(page_source, max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]
    assert len(page_source.calls) == 1

    detail_source = FixtureSource()
    rows, _, meta = _collect(detail_source, detail_limit=5)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "raw current count 6" in meta["configured_collection_error"]
    assert meta["list_requests"] == 14
    assert not any(method == "GET" for method, _, _ in detail_source.calls)


@pytest.mark.parametrize(
    ("option", "message"),
    [
        ("nonempty_sentinel", "expected 0 cards"),
        ("mutate_first_recheck", "first page changed on recheck"),
        ("partition_mismatch", "filtered/canonical row mismatch"),
        ("noncourse_title_drift", "non-course exclusion binding changed"),
        ("duplicate_identity", "duplicate or not descending"),
        ("bad_status", "source status changed"),
        ("detail_title_drift", "list/detail mismatch"),
        ("detail_branch_drift", "list/detail mismatch"),
        ("full_control_drift", "full programme is not closed"),
        ("application_route_drift", "application route changed"),
    ],
)
def test_contract_drift_fails_closed(option: str, message: str) -> None:
    source = FixtureSource(**{option: True})
    rows, _, meta = _collect(source)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_transient_failure_retries_without_weakening_snapshot() -> None:
    source = FixtureSource(failures=1)
    rows, _, meta = _collect(source)
    assert len(rows) == 3
    assert meta["snapshot_complete"] is True
    assert meta["logical_requests"] == 20
    assert meta["physical_requests"] == 21
    assert meta["request_retry_count"] == 1


def test_scheduled_programme_may_expose_a_preopen_login_shell() -> None:
    program = imsil._ListedProgram(
        identity="771",
        title="여름 프로그램",
        branch_label="임실",
        branch="임실군립도서관",
        receipt_status="접수대기",
        event_status="행사대기",
        apply_start=date(2026, 7, 29),
        apply_end=date(2026, 8, 10),
        raw_apply_period="2026.07.29 ~ 2026.08.10",
        event_start=date(2026, 8, 11),
        event_end=date(2026, 8, 11),
        raw_event_period="2026.08.11 ~ 2026.08.11",
        target="초등학생",
        capacity_total=10,
        page=1,
        source_filter="Z",
        detail_url=imsil.imsil_detail_url("771"),
    )
    detail = imsil._Detail(
        venue="1층 다목적실",
        schedule="화 10:00-11:40",
        fee="해당없음",
        target="초등학생",
        method="인터넷",
        control="login",
        current_applicants=0,
    )
    status, application_type, application_url, available = imsil._application_state(
        program, detail, date(2026, 7, 26)
    )
    assert (status, application_type, application_url, available) == (
        "SCHEDULED",
        "INFORMATION_ONLY",
        "",
        False,
    )


def test_external_dedupe_cannot_drop_a_complete_identity() -> None:
    source = FixtureSource()
    rows, _, meta = _collect(source, dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "external dedupe changed complete identity snapshot" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_IMSIL_LIVE_TESTS") != "1",
    reason="set RUN_IMSIL_LIVE_TESTS=1 for the audited live contract",
)
def test_live_snapshot_is_exact_and_stable_across_two_runs() -> None:
    snapshots = []
    for _ in range(2):
        rows, parser, meta = imsil.collect(
            _target(), today="2026-07-23", max_workers=3
        )
        assert parser == imsil.IMSIL_PARSER
        assert meta["configured_collection_error"] == ""
        assert meta["snapshot_complete"] is True
        assert meta["ledger_totals"] == {"Z": 256, "A": 205, "B": 44}
        assert meta["ledger_pages"] == {"Z": 26, "A": 21, "B": 5}
        assert meta["sentinel_pages"] == {"Z": 27, "A": 22, "B": 6}
        assert meta["sentinel_counts"] == {"Z": 0, "A": 0, "B": 0}
        assert all(meta["stable_rechecks"].values())
        assert meta["source_total"] == 256
        assert meta["education_total"] == 249
        assert meta["source_status_counts"] == {
            "접수종료/행사종료": 250,
            "접수종료/행사진행": 2,
            "접수진행/행사대기": 1,
            "접수진행/행사진행": 3,
        }
        assert meta["branch_counts"] == {
            "무지개빛": 13,
            "아낌없이": 6,
            "오수": 28,
            "임실": 177,
            "전체": 7,
            "지사랑": 15,
            "필봉": 10,
        }
        assert meta["filter_branch_counts"]["A"] == {"오수": 28, "임실": 177}
        assert meta["filter_branch_counts"]["B"] == {
            "무지개빛": 13,
            "아낌없이": 6,
            "지사랑": 15,
            "필봉": 10,
        }
        assert meta["global_only_ids"] == ["760", "759", "758", "701", "700", "699", "549"]
        assert meta["current_raw_count"] == 6
        assert meta["current_count"] == 3
        assert meta["excluded_current_count"] == 3
        assert meta["expired_count"] == 246
        assert meta["detail_pages"] == 6
        assert meta["detail_login_control_count"] == 3
        assert meta["stale_login_control_count"] == 1
        assert meta["excluded_noncourse_login_control_count"] == 2
        assert meta["capacity_close_override_count"] == 1
        assert meta["online_application_count"] == 0
        assert meta["offline_application_count"] == 1
        assert meta["application_endpoint_fetches"] == 0
        assert meta["list_requests"] == 64
        assert meta["logical_requests"] == 70
        assert meta["physical_requests"] == 70
        assert [row["provider_course_id"].rsplit(":", 1)[-1] for row in rows] == [
            "768",
            "767",
            "761",
        ]
        assert [row["branch"] for row in rows] == [
            "지사랑 작은도서관",
            "임실군립오수도서관",
            "임실군립도서관",
        ]
        assert [row["status"] for row in rows] == ["OPEN", "CLOSED", "CLOSED"]
        assert not any(row["reservation_available"] for row in rows)
        snapshots.append(
            (
                [(row["provider_course_id"], row["title"], row["branch"], row["status"]) for row in rows],
                meta["ledger_totals"],
                meta["branch_counts"],
                meta["source_status_counts"],
            )
        )
    assert snapshots[0] == snapshots[1]
