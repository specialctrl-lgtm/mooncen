from __future__ import annotations

from collections import Counter
import hashlib
import html
import os
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from Crawler import municipal_seocheon as seocheon


class _Response:
    def __init__(
        self,
        url: str,
        body: str,
        status_code: int = 200,
        *,
        redirected: bool = False,
    ) -> None:
        self.url = url
        self.content = body.encode("utf-8")
        self.status_code = status_code
        self.history = [object()] if redirected else []


class _Session:
    def __init__(self, scenario: "_Scenario") -> None:
        self.scenario = scenario
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target() -> dict[str, str]:
    return {
        "provider": seocheon.SEOCHEON_PROVIDER,
        "url": seocheon.SEOCHEON_CANONICAL_URL,
    }


def _record(
    identity: str,
    *,
    title: str,
    status: str,
    event_period: str,
    apply_period: str,
    branch: str,
    target_badge: str = "성인",
    target_detail: str | None = None,
    category: str = "교육",
    kind: str = "합성 교육과정",
    current: int = 1,
    total: int = 10,
    wait_current: int | None = 0,
    wait_total: int | None = 2,
    instructor: str = "-",
    room: str = "종합교육센터 1",
    fee: str = "무료",
    content: str = "",
    attachment: bool = False,
    image: bool = False,
) -> dict[str, object]:
    return {
        "identity": identity,
        "title": title,
        "status": status,
        "event_period": event_period,
        "apply_period": apply_period,
        "branch": branch,
        "target_badge": target_badge,
        "target_detail": target_badge if target_detail is None else target_detail,
        "category": category,
        "kind": kind,
        "capacity_current": current,
        "capacity_total": total,
        "wait_current": wait_current,
        "wait_total": wait_total,
        "instructor": instructor,
        "room": room,
        "fee": fee,
        "minimum": 2,
        "weekdays": "월, 수",
        "schedule": "매주 월, 수 10:00~12:00",
        "contact": "041-950-0000",
        "content": content,
        "attachment": attachment,
        "image": image,
    }


def _records() -> list[dict[str, object]]:
    rows = [
        _record(
            "1011",
            title="현재 모집중 교육",
            status="모집중",
            event_period="2026-08-01 ~ 2026-10-31",
            apply_period="2026-07-01 09:00 ~ 2026-07-31 18:00",
            branch="서천군",
            content="안내 본문",
            attachment=True,
            image=True,
        ),
        _record(
            "1010",
            title="향후 모집예정 교육",
            status="모집예정",
            event_period="2026-09-01 ~ 2026-11-30",
            apply_period="2026-08-01 09:00 ~ 2026-08-05 18:00",
            branch="종합교육센터",
        ),
        _record(
            "1009",
            title="현재 마감 교육",
            status="모집완료",
            event_period="2026-06-01 ~ 2026-08-31",
            apply_period="2026-05-01 09:00 ~ 2026-05-10 18:00",
            branch="군산대학교",
            instructor="강사 개인정보",
            fee="10,000",
        ),
        _record(
            "1008",
            title="기타 대상 현재 교육",
            status="모집완료",
            event_period="2026-07-23 ~ 2026-07-23",
            apply_period="2026-06-01 09:00 ~ 2026-06-05 18:00",
            branch="군산대학교 평생교육원",
            target_badge="기타",
            target_detail="서천관내 직장인",
            wait_current=None,
            wait_total=None,
            content="폐기할 자유서술",
        ),
        _record(
            "1007",
            title="과거 교육 1",
            status="모집완료",
            event_period="2025-01-01 ~ 2025-02-01",
            apply_period="2024-12-01 09:00 ~ 2024-12-05 18:00",
            branch="서천군",
        ),
    ]
    rows.extend(
        _record(
            str(identity),
            title=f"과거 교육 {number}",
            status="모집완료",
            event_period="2024-01-01 ~ 2024-02-01",
            apply_period="2023-12-01 09:00 ~ 2023-12-05 18:00",
            branch="서천군",
            wait_current=None,
            wait_total=None,
        )
        for number, identity in enumerate(range(1006, 1001, -1), 2)
    )
    rows.append(
        _record(
            "407",
            title="기간 오류 과거 교육",
            status="모집완료",
            event_period="2015-01-07 ~ 2015-01-05",
            apply_period="2014-11-19 00:00 ~ 2015-01-05 00:00",
            branch="서천군",
            wait_current=None,
            wait_total=None,
        )
    )
    return rows


def _options(values: tuple[tuple[str, str], ...], *, drift: bool = False) -> str:
    current = values[:-1] if drift else values
    return "".join(
        f'<option value="{html.escape(value, quote=True)}">{html.escape(label)}</option>'
        for value, label in current
    )


def _list_form(page: int, *, duplicate_hidden: bool = False, option_drift: bool = False) -> str:
    duplicate = '<input type="hidden" name="pageIndex" value="1">' if duplicate_hidden else ""
    return f"""
    <form id="searchForm" name="searchForm" method="post"
          action="{seocheon.SEOCHEON_LIST_PATH}">
      <input type="hidden" name="pageIndex" value="{page}">{duplicate}
      <input type="hidden" name="lctrSn" value="">
      <select name="searchEduTrgtSe">{_options(seocheon._TARGET_OPTIONS, drift=option_drift)}</select>
      <select name="searchRcrtStts">{_options(seocheon._STATUS_OPTIONS)}</select>
      <input type="text" name="searchBgngYmd" value="">
      <input type="text" name="searchEndYmd" value="">
      <input type="text" name="searchKeyword" value="">
    </form>
    """


def _page_href(page: int) -> str:
    return "?" + urlencode({"pageIndex": page})


def _pager(
    requested_page: int,
    last: int,
    *,
    bad_href: bool = False,
    hidden_last: bool = False,
) -> str:
    active = requested_page if requested_page <= last else None
    visible_last = last - 1 if hidden_last else last
    links = []
    for number in range(1, visible_last + 1):
        href = _page_href(number)
        if bad_href and number == visible_last:
            href = "https://example.invalid/escape?pageIndex=" + str(number)
        if number == active:
            links.append(
                f'<a class="page-link active" href="{html.escape(href, quote=True)}" '
                'onclick="return false;"><span class="sr-only">현재페이지 </span>'
                f"{number}</a>"
            )
        else:
            links.append(
                f'<a class="page-link" href="{html.escape(href, quote=True)}" '
                f'onclick="fn_egov_select_linkPage({number});return false;">{number}</a>'
            )
    bounded = min(requested_page, last)
    if requested_page > last:
        previous = following = last
    else:
        previous = max(1, bounded - 1)
        following = min(last, bounded + 1)
    return f"""
    <div class="pe-pagination">
      <a class="page-navi prev" href="{_page_href(previous)}"
         onclick="fn_egov_select_linkPage({previous});return false;">이전</a>
      <div class="page-links">{''.join(links)}</div>
      <a class="page-navi next" href="{_page_href(following)}"
         onclick="fn_egov_select_linkPage({following});return false;">다음</a>
    </div>
    """


def _capacity(record: dict[str, object]) -> str:
    result = f"{record['capacity_current']} / {record['capacity_total']}"
    if record["wait_current"] is not None:
        result += f" (대기 {record['wait_current']}/{record['wait_total']})"
    return result


def _card(record: dict[str, object]) -> str:
    fields = {
        "강좌구분": record["kind"],
        "접수기간": record["apply_period"],
        "교육기간": record["event_period"],
        "강사명": record["instructor"],
        "신청/모집인원(명)": _capacity(record),
    }
    items = "".join(
        f'<li><strong class="subjact">{label}</strong><span class="con">'
        f"{html.escape(str(fields[label]))}</span></li>"
        for label in seocheon._LIST_LABELS
    )
    return f"""
    <li class="structured-item"><a class="structured-item-link"
        href="javascript:fn_search_view('{record['identity']}')">
      <div class="card-top"><span class="pe-badge">{html.escape(str(record['target_badge']))}</span>
      <span class="pe-badge">{html.escape(str(record['category']))}</span>
      <span class="pe-badge">{html.escape(str(record['status']))}</span></div>
      <p class="c-tit"><span class="span">{html.escape(str(record['title']))}</span></p>
      <ul class="c-info-list">{items}</ul>
    </a></li>
    """


def _shell(body: str, *, bad_footer: bool = False) -> str:
    address = (
        "(33637) 충청남도 서천군 서천읍 잘못된로 1"
        if bad_footer
        else "(33637) 충청남도 서천군 서천읍 서림로 19"
    )
    return (
        "<html><head><title>강좌목록 &lt; 수강신청 &lt; 평생학습포털</title></head>"
        f'<body>{body}<footer id="foot_layout"><address>{address}</address></footer></body></html>'
    )


def _list_html(
    page: int,
    last: int,
    records: list[dict[str, object]],
    *,
    sentinel_text: str = "등록된 강좌가 없습니다.",
    duplicate_hidden: bool = False,
    option_drift: bool = False,
    bad_href: bool = False,
    hidden_last: bool = False,
    bad_footer: bool = False,
) -> str:
    ledger = (
        '<ul class="pe-structured-list">'
        + ("".join(_card(record) for record in records) if records else f'<li class="structured-item">{sentinel_text}</li>')
        + "</ul>"
    )
    return _shell(
        _list_form(page, duplicate_hidden=duplicate_hidden, option_drift=option_drift)
        + ledger
        + _pager(page, last, bad_href=bad_href, hidden_last=hidden_last),
        bad_footer=bad_footer,
    )


def _detail_html(
    record: dict[str, object],
    page: int,
    *,
    title_drift: bool = False,
    capacity_drift: bool = False,
    branch_drift: bool = False,
    target_drift: bool = False,
    missing_control: bool = False,
    extra_control: bool = False,
    handler_drift: bool = False,
    action_identity_drift: bool = False,
    detail_form_drift: bool = False,
) -> str:
    identity = str(record["identity"])
    detail_identity = "9999" if action_identity_drift else identity
    values = {
        "강좌명": str(record["title"]) + (" 변경" if title_drift else ""),
        "강좌구분": record["kind"],
        "강좌분야": record["category"],
        "교육대상": "불일치 대상" if target_drift else record["target_detail"],
        "접수기간": record["apply_period"],
        "교육기간": record["event_period"],
        "교육요일": record["weekdays"],
        "교육일정": record["schedule"],
        "최소모집인원(명)": record["minimum"],
        "최대모집인원(명)": int(record["capacity_total"]) + (1 if capacity_drift else 0),
        "강사명": record["instructor"],
        "교육장소명": record["room"],
        "교육기관명": "새로운 기관" if branch_drift else record["branch"],
        "수업료(원)": record["fee"],
        "문의처": record["contact"],
        "강좌내용": record["content"],
    }
    table_rows = []
    for label in seocheon._DETAIL_LABELS:
        extra = ""
        if label == "강좌내용":
            if record["attachment"]:
                extra += '<a href="/unsafe/download.do?file=1">첨부</a>'
            if record["image"]:
                extra += '<img src="/unsafe/private.jpg" alt="본문 이미지">'
        table_rows.append(
            f"<tr><th>{label}</th><td>{html.escape(str(values[label]))}{extra}</td></tr>"
        )
    detail_page = page + 1 if detail_form_drift else page
    hidden_names = (
        "pageIndex", "lctrSn", "searchLctrSeSn", "searchLctrFld",
        "searchEduTrgtSe", "searchRcrtStts", "searchBgngYmd",
        "searchEndYmd", "searchKeyword",
    )
    hidden = "".join(
        f'<input type="hidden" name="{name}" value="'
        + (str(detail_page) if name == "pageIndex" else identity if name == "lctrSn" else "")
        + '">'
        for name in hidden_names
    )
    session_id = "A" * 32
    action = (
        f"{seocheon.SEOCHEON_DETAIL_PATH};jsessionid={session_id}?"
        + urlencode({"pageIndex": page, "lctrSn": detail_identity})
    )
    application_path = "/unsafe/write.do" if handler_drift else seocheon.SEOCHEON_APPLICATION_PATH
    controls = ""
    if record["status"] == "모집중" and not missing_control:
        controls = '<div class="btn-wrap"><button type="button" onclick="fn_search_write()">신청하기</button></div>'
    elif extra_control:
        controls = '<div class="btn-wrap"><button type="button" onclick="fn_search_write()">신청하기</button></div>'
    body = f"""
    <form id="searchForm" method="post" action="{seocheon.SEOCHEON_DETAIL_PATH}">{hidden}</form>
    <form id="actionForm" method="post" action="{html.escape(action, quote=True)}">
      <input type="hidden" name="lctrSn" value="{detail_identity}">
    </form>
    <table><caption>강좌 상세에 대한 정보 제공</caption><tbody>{''.join(table_rows)}</tbody></table>
    {controls}
    <script>function fn_search_write() {{
      document.actionForm.action = '{application_path};jsessionid={session_id}';
      document.actionForm.submit();
    }}</script>
    """
    return _shell(body)


class _Scenario:
    def __init__(self, mutation: str = "") -> None:
        self.mutation = mutation
        self.rows = _records()
        if mutation == "duplicate_identity":
            self.rows[5]["identity"] = self.rows[0]["identity"]
        if mutation == "pii_in_raw_field":
            self.rows[0]["kind"] = "과정 010-1234-5678"
        if mutation == "historical_exception_drift":
            self.rows[-1]["event_period"] = "2015-01-01 ~ 2015-01-05"
        if mutation == "unknown_status":
            self.rows[0]["status"] = "새상태"
        self.calls: list[str] = []
        self.occurrences: Counter[str] = Counter()
        self.sessions: list[_Session] = []

    def session_factory(self) -> _Session:
        current = _Session(self)
        self.sessions.append(current)
        return current

    def fetch(self, _session: _Session, url: str, _timeout: int) -> _Response:
        self.calls.append(url)
        self.occurrences[url] += 1
        if self.mutation == "transient" and len(self.calls) == 1:
            return _Response(url, "temporary", 503)
        parsed = urlparse(url)
        values = parse_qs(parsed.query, keep_blank_values=True)
        response_url = url + "#changed" if self.mutation == "response_url" and len(self.calls) == 1 else url
        redirected = self.mutation == "redirect" and len(self.calls) == 1
        if parsed.path == seocheon.SEOCHEON_DETAIL_PATH:
            identity = values["lctrSn"][0]
            page = int(values["pageIndex"][0])
            record = next(row for row in self.rows if row["identity"] == identity)
            body = _detail_html(
                record,
                page,
                title_drift=self.mutation == "detail_title" and identity == "1011",
                capacity_drift=self.mutation == "detail_capacity" and identity == "1011",
                branch_drift=self.mutation == "detail_branch" and identity == "1011",
                target_drift=self.mutation == "detail_target" and identity == "1011",
                missing_control=self.mutation == "missing_control" and identity == "1011",
                extra_control=self.mutation == "inactive_control" and identity == "1010",
                handler_drift=self.mutation == "handler_drift" and identity == "1011",
                action_identity_drift=self.mutation == "action_identity" and identity == "1011",
                detail_form_drift=self.mutation == "detail_form" and identity == "1011",
            )
            return _Response(response_url, body, redirected=redirected)

        page = int(values["pageIndex"][0])
        last = (len(self.rows) + seocheon.SEOCHEON_PAGE_SIZE - 1) // seocheon.SEOCHEON_PAGE_SIZE
        start = (page - 1) * seocheon.SEOCHEON_PAGE_SIZE
        records = [dict(row) for row in self.rows[start : start + seocheon.SEOCHEON_PAGE_SIZE]]
        if self.mutation == "short_page" and page == 1:
            records = records[:-1]
        if self.mutation == "sentinel_not_empty" and page == last + 1:
            records = [dict(self.rows[-1])]
        if (
            self.mutation == "unstable_first"
            and page == 1
            and self.occurrences[url] > 1
        ):
            records[0]["title"] = str(records[0]["title"]) + " 변경"
        body = _list_html(
            page,
            last,
            records,
            sentinel_text="변경된 빈 페이지" if self.mutation == "sentinel_text" and page == last + 1 else "등록된 강좌가 없습니다.",
            duplicate_hidden=self.mutation == "duplicate_hidden" and len(self.calls) == 1,
            option_drift=self.mutation == "option_drift" and len(self.calls) == 1,
            bad_href=self.mutation == "pager_href" and len(self.calls) == 1,
            hidden_last=self.mutation == "pager_last" and len(self.calls) == 1,
            bad_footer=self.mutation == "bad_footer" and len(self.calls) == 1,
        )
        return _Response(response_url, body, redirected=redirected)


def _run(
    scenario: _Scenario,
    **kwargs: object,
) -> tuple[list[dict[str, object]], str, dict[str, object]]:
    return seocheon.collect_seocheon_education(
        _target(),
        today="2026-07-23",
        session_factory=scenario.session_factory,
        fetcher=scenario.fetch,
        **kwargs,
    )


def test_existing_provider_is_retained_on_migrated_owner() -> None:
    assert hashlib.sha1(seocheon.SEOCHEON_LEGACY_URL.encode()).hexdigest()[:8].upper() == (
        "096AAB21"
    )
    assert hashlib.sha256(seocheon.SEOCHEON_LEGACY_URL.encode()).hexdigest()[:12].upper() == (
        "E1565CC62D6C"
    )
    assert hashlib.sha256(seocheon.SEOCHEON_CANONICAL_URL.encode()).hexdigest() == (
        seocheon.SEOCHEON_CANONICAL_URL_SHA256
    )
    assert seocheon.SEOCHEON_EXISTING_PROVIDER_AUDIT["new_provider_required"] is False
    assert seocheon.SEOCHEON_CANDIDATE_AUDIT[seocheon.SEOCHEON_LEGACY_CANDIDATE_ID][
        "canonical_url"
    ] == seocheon.SEOCHEON_CANONICAL_URL


def test_canonical_target_and_municipal_override_are_enriched_once() -> None:
    from Crawler import Crawler_GeneratedYamlTargets as generated

    root = Path(__file__).resolve().parents[1]
    target_document = generated.load_unique_yaml(
        root / "config" / "crawl_targets" / "lifelong_learning.yaml"
    )
    matches = [
        row
        for row in target_document["targets"]
        if row.get("provider") == seocheon.SEOCHEON_PROVIDER
    ]
    assert len(matches) == 1
    target = matches[0]
    assert target["url"] == seocheon.SEOCHEON_CANONICAL_URL
    assert target["crawler_module"] == "Crawler.municipal_seocheon"
    assert target["crawler_callable"] == "collect"
    assert target["source_group"] == "municipal_reservation"
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert target["municipality_code"] == seocheon.SEOCHEON_MUNICIPALITY_CODE
    assert target["max_pages"] == seocheon.SEOCHEON_RECOMMENDED_MAX_PAGES
    assert target["detail_limit"] == seocheon.SEOCHEON_RECOMMENDED_DETAIL_LIMIT
    assert target["last_quality"]["source_total_count"] == 1316
    assert target["last_quality"]["collected"] == 34
    assert generated._is_registry_target(
        {**target, "_target_file": "lifelong_learning.yaml"}
    ) is False

    override_document = generated.load_unique_yaml(
        root / "config" / "municipal_integrated_reservation_overrides.yaml"
    )
    blocks = [
        row
        for row in override_document["municipalities"]
        if row.get("code") == seocheon.SEOCHEON_MUNICIPALITY_CODE
    ]
    assert len(blocks) == 1
    candidates = blocks[0]["candidates"]
    promoted = [row for row in candidates if row.get("status") == "candidate"]
    assert [(row.get("provider"), row.get("url")) for row in promoted] == [
        (seocheon.SEOCHEON_PROVIDER, seocheon.SEOCHEON_CANONICAL_URL)
    ]
    legacy = [row for row in candidates if row.get("url") == seocheon.SEOCHEON_LEGACY_URL]
    assert len(legacy) == 1
    assert legacy[0]["status"] == "excluded"
    assert "superseded" in legacy[0]["exclusion_reason"]


def test_generated_arguments_are_complete_and_stale_safe() -> None:
    from Crawler import Crawler_GeneratedYamlTargets as generated

    arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        seocheon.SEOCHEON_PROVIDER
    ]
    assert arguments == (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "400",
        "--detail-limit",
        "100",
    )
    parsed = generated.parse_args(list(arguments))
    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.allow_partial_save is False
    assert parsed.per_target_limit == 0
    assert parsed.max_pages == seocheon.SEOCHEON_RECOMMENDED_MAX_PAGES
    assert parsed.detail_limit == seocheon.SEOCHEON_RECOMMENDED_DETAIL_LIMIT


def test_generated_engine_recognizes_complete_uncapped_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_GeneratedYamlTargets as generated
    from Crawler.Crawler_MunicipalYaml import CrawlTarget

    scenario = _Scenario()

    def fake_collect(
        _target: object,
        *,
        max_pages: int,
        detail_limit: int,
        **_kwargs: object,
    ):
        return _run(
            scenario,
            max_pages=max_pages,
            detail_limit=detail_limit,
        )

    monkeypatch.setattr(generated, "collect_from_url", fake_collect)
    target = CrawlTarget(
        provider=seocheon.SEOCHEON_PROVIDER,
        name="서천군 전체 교육강좌",
        branch="서천군",
        url=seocheon.SEOCHEON_CANONICAL_URL,
        source="test",
    )
    result = generated._collect_single_target(
        target,
        per_target_limit=0,
        max_depth=0,
        max_pages=400,
        detail_limit=100,
        timeout=1,
    )
    assert result.report.success is True
    assert result.report.pages == 3
    assert result.report.detail_pages == 4
    assert result.report.discovered_links == 11
    assert result.report.pagination_detected is True
    assert result.page_cap_reached is False
    assert result.detail_cap_reached is False
    assert result.collection_complete is True


def test_legacy_provider_url_is_retargeted_before_specialized_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    sentinel = ([{"title": "retargeted"}], seocheon.SEOCHEON_PARSER, {"snapshot_complete": True})
    seen: list[object] = []

    def fake_collect(target: object, **_kwargs: object):
        seen.append(target)
        return sentinel

    monkeypatch.setattr(seocheon, "collect_seocheon_education", fake_collect)
    target = municipal.CrawlTarget(
        provider=seocheon.SEOCHEON_PROVIDER,
        name="서천군 구 후보",
        branch="서천군",
        url=seocheon.SEOCHEON_LEGACY_URL,
        source="test",
    )
    assert municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=400,
        detail_limit=100,
    ) == sentinel
    assert len(seen) == 1
    assert getattr(seen[0], "url") == seocheon.SEOCHEON_CANONICAL_URL


def test_target_requires_exact_retained_provider_and_canonical_url() -> None:
    assert seocheon.is_seocheon_education_target(_target())
    for target in (
        {"provider": seocheon.SEOCHEON_PROVIDER, "url": seocheon.SEOCHEON_LEGACY_URL},
        {"provider": seocheon.SEOCHEON_PROVIDER, "url": seocheon.SEOCHEON_CANONICAL_URL + "?x=1"},
        {"provider": "new-provider", "url": seocheon.SEOCHEON_CANONICAL_URL},
    ):
        assert not seocheon.is_seocheon_education_target(target)
    scenario = _Scenario()
    rows, _, meta = seocheon.collect_seocheon_education(
        {"provider": "wrong", "url": seocheon.SEOCHEON_CANONICAL_URL},
        session_factory=scenario.session_factory,
        fetcher=scenario.fetch,
    )
    assert rows == []
    assert "does not match" in str(meta["configured_collection_error"])
    assert scenario.calls == []


def test_raw_network_requires_explicit_test_opt_in() -> None:
    rows, _, meta = seocheon.collect_seocheon_education(_target(), today="2026-07-23")
    assert rows == []
    assert meta["configured_collection_error"] == "managed session_factory injection is required"


def test_complete_synthetic_snapshot_is_current_complete_and_private() -> None:
    scenario = _Scenario()
    rows, parser, meta = _run(scenario)
    assert parser == seocheon.SEOCHEON_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert len(rows) == 4
    assert meta["source_total_count"] == 11
    assert meta["current_source_count"] == 4
    assert meta["expired_source_count"] == 6
    assert meta["historical_unknown_period_count"] == 1
    assert meta["source_pages"] == 3
    assert meta["pages"] == 3
    assert meta["discovered_links"] == 11
    assert meta["pagination_detected"] is True
    assert meta["final_page_size"] == 1
    assert meta["sentinel_page"] == 4
    assert meta["list_requests"] == 7
    assert meta["detail_requests"] == 4
    assert meta["source_requests"] == 11
    assert meta["request_attempts"] == 11
    assert meta["source_raw_status_counts"] == {"모집중": 1, "모집예정": 1, "모집완료": 9}
    assert meta["current_raw_status_counts"] == {"모집중": 1, "모집예정": 1, "모집완료": 2}
    assert meta["status_counts"] == {"OPEN": 1, "SCHEDULED": 1, "CLOSED": 2}
    assert meta["application_control_count"] == 1
    assert meta["contacts_discarded"] == 4
    assert meta["instructors_discarded"] == 1
    assert meta["free_text_cells_discarded"] == 2
    assert meta["attachment_links_discarded"] == 1
    assert meta["images_discarded"] == 1
    assert [row["provider_course_id"] for row in rows] == [
        f"{seocheon.SEOCHEON_PROVIDER}:lctrSn:{identity}"
        for identity in ("1011", "1010", "1009", "1008")
    ]
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_url"] == rows[0]["raw_url"]
    assert rows[1]["status"] == "SCHEDULED"
    assert rows[1]["application_url"] == ""
    assert rows[2]["fee_amount"] == 10000
    assert rows[3]["end_date"] == "2026-07-23"
    assert rows[3]["target"] == "서천관내 직장인"
    assert rows[3]["waitlist_current"] is None
    assert set(row["branch"] for row in rows) == {
        "서천군", "종합교육센터", "군산대학교", "군산대학교 평생교육원"
    }
    assert all(row["collection_category"] == "공공예약" for row in rows)
    assert all(row["program_type"] == "교육" for row in rows)
    assert not any(
        any(token in url.lower() for token in ("aplcnt", "login", "download", "private.jpg"))
        for url in scenario.calls
    )
    assert scenario.sessions and all(current.closed for current in scenario.sessions)


def test_retry_is_bounded_and_accounted() -> None:
    scenario = _Scenario("transient")
    rows, _, meta = _run(scenario)
    assert len(rows) == 4
    assert meta["source_requests"] == 11
    assert meta["request_attempts"] == 12
    assert len(scenario.calls) == 12


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_identity", "duplicate lctrSn identity"),
        ("short_page", "pre-final page size drift"),
        ("sentinel_not_empty", "mixed rows/sentinel"),
        ("sentinel_text", "exact empty-page sentinel drift"),
        ("unstable_first", "source boundaries changed"),
        ("pager_href", "request escaped exact Seocheon HTTPS host"),
        ("pager_last", "pagination contract drift"),
        ("duplicate_hidden", "search control registry drift"),
        ("option_drift", "option registry drift"),
        ("bad_footer", "official Seocheon footer drift"),
        ("unknown_status", "source status drift"),
        ("historical_exception_drift", "historical period exception changed"),
        ("detail_title", "list/detail structured data drift"),
        ("detail_capacity", "list/detail structured data drift"),
        ("detail_branch", "official institution drift"),
        ("detail_target", "list/detail structured data drift"),
        ("missing_control", "open application control drift"),
        ("inactive_control", "inactive course exposes application control"),
        ("handler_drift", "application handler drift"),
        ("action_identity", "application owner action drift"),
        ("detail_form", "detail identity/filter binding drift"),
        ("pii_in_raw_field", "PII-like value"),
        ("response_url", "response URL drift"),
        ("redirect", "redirect history is not allowed"),
    ],
)
def test_contract_drift_fails_closed(mutation: str, message: str) -> None:
    rows, _, meta = _run(_Scenario(mutation))
    assert rows == []
    assert message in str(meta["configured_collection_error"])
    assert meta["snapshot_complete"] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://www.seocheon.go.kr/prog/lctr/life/sub03_01/list.do;jsessionid=A?pageIndex=1",
        "https://www.seocheon.go.kr/prog/lctr/life/sub03_01/list.do?pageIndex=1#x",
        "https://www.seocheon.go.kr/prog/lctr/life/sub03_01/list.do?pageIndex=1&pageIndex=2",
        "https://www.seocheon.go.kr/prog/lctr/life/sub03_01/list.do?pageIndex=1&broken",
        "https://www.seocheon.go.kr/prog/lctr/life/sub03_01/view.do?pageIndex=1&lctrSn=2&extra=1",
        "https://www.seocheon.go.kr/prog/lctrAplcnt/life/sub03_01/write.do?pageIndex=1&lctrSn=2",
        "https://example.invalid/prog/lctr/life/sub03_01/list.do?pageIndex=1",
    ],
)
def test_request_allowlist_rejects_unsafe_or_malformed_url(url: str) -> None:
    with pytest.raises(seocheon.SeocheonContractError):
        seocheon._validate_fetch_url(url)


def test_effective_caps_are_hard_and_runner_bounds_are_compatible() -> None:
    scenario = _Scenario()
    rows, _, meta = _run(scenario, max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "declared last page 3 exceeds" in str(meta["configured_collection_error"])

    scenario = _Scenario()
    rows, _, meta = _run(scenario, detail_limit=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "current details exceed" in str(meta["configured_collection_error"])

    scenario = _Scenario()
    rows, _, meta = _run(scenario, max_pages=1500, detail_limit=3000)
    assert len(rows) == 4
    assert meta["snapshot_complete"] is True

    for kwargs in ({"max_pages": 2001}, {"detail_limit": 3001}, {"timeout": 0}):
        scenario = _Scenario()
        rows, _, meta = _run(scenario, **kwargs)
        assert rows == []
        assert "ValueError" in str(meta["configured_collection_error"])
        assert scenario.calls == []


def test_session_factory_failure_and_dedupe_loss_are_fail_closed() -> None:
    def broken_factory() -> _Session:
        raise RuntimeError("session unavailable")

    rows, _, meta = seocheon.collect_seocheon_education(
        _target(), today="2026-07-23", session_factory=broken_factory
    )
    assert rows == []
    assert "session_factory failed" in str(meta["configured_collection_error"])

    rows, _, meta = _run(_Scenario(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe_rows changed complete identity cardinality" in str(
        meta["configured_collection_error"]
    )


@pytest.mark.skipif(
    os.getenv("RUN_SEOCHEON_LIVE") != "1",
    reason="live Seocheon audit is opt-in",
)
def test_seocheon_live_two_exact_snapshots() -> None:
    snapshots: list[list[dict[str, object]]] = []
    baseline = seocheon.SEOCHEON_LIVE_AUDIT_BASELINE
    for _ in range(2):
        rows, parser, meta = seocheon.collect_seocheon_education(
            _target(),
            today=baseline["cutoff"],
            allow_raw_requests_for_tests=True,
        )
        assert parser == seocheon.SEOCHEON_PARSER
        assert meta["configured_collection_error"] == ""
        assert meta["snapshot_complete"] is True
        assert len(rows) == baseline["current_total"]
        for meta_key, baseline_key in (
            ("source_total_count", "source_total"),
            ("current_source_count", "current_total"),
            ("expired_source_count", "expired_dated_source"),
            ("historical_unknown_period_count", "historical_unknown_periods"),
            ("source_pages", "source_pages"),
            ("final_page_size", "final_page_size"),
            ("sentinel_page", "sentinel_page"),
            ("source_raw_status_counts", "source_raw_status_counts"),
            ("current_raw_status_counts", "current_raw_status_counts"),
            ("status_counts", "status_counts"),
            ("current_branch_counts", "current_branch_counts"),
            ("current_page_counts", "current_page_counts"),
            ("current_ids", "current_ids"),
            ("application_control_count", "application_control_count"),
            ("contacts_discarded", "contacts_discarded"),
            ("instructors_discarded", "instructors_discarded"),
            ("free_text_cells_discarded", "free_text_cells_discarded"),
            ("attachment_links_discarded", "attachment_links_discarded"),
            ("images_discarded", "images_discarded"),
            ("list_requests", "list_requests"),
            ("detail_requests", "detail_requests"),
            ("source_requests", "source_requests"),
        ):
            assert meta[meta_key] == baseline[baseline_key]
        assert meta["identity_union_count"] == baseline["source_total"]
        assert meta["identity_duplicate_count"] == 0
        assert meta["application_endpoint_requests"] == 0
        assert meta["login_endpoint_requests"] == 0
        assert meta["applicant_endpoint_requests"] == 0
        assert meta["attachment_endpoint_requests"] == 0
        assert meta["download_endpoint_requests"] == 0
        assert meta["application_form_submissions"] == 0
        snapshots.append(rows)
    assert snapshots[0] == snapshots[1]
