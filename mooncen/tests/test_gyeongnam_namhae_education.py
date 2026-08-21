from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_gyeongnam_namhae as namhae


def _record(
    identity: str,
    *,
    title: str,
    facility: str,
    status: str,
    period: str,
    apply: str,
    method: str = "온라인접수",
) -> dict[str, str]:
    return {
        "id": identity,
        "title": title,
        "facility": facility,
        "status": status,
        "period": period,
        "apply": apply,
        "method": method,
        "target": "군민 누구나",
        "time": "매주 화요일 10:00~12:00",
        "selection": "선착순",
        "capacity": "20 명",
        "fee": "무료",
        "venue": f"{facility} 2층 강의실",
    }


RECORDS = (
    _record(
        "112",
        title="온라인 미래 강좌",
        facility="평생학습관",
        status="신청하기",
        period="2099.08.01. ~ 2099.08.31",
        apply="2099.07.01 09:00:00. ~ 2099.07.31 17:59:59",
        method="온라인접수, 전화접수",
    ),
    _record(
        "111",
        title="대기자 미래 강좌",
        facility="여성인력개발센터",
        status="대기자신청",
        period="2099.08.02. ~ 2099.09.01",
        apply="2099.07.01 09:00:00. ~ 2099.07.31 17:59:59",
    ),
    _record(
        "110",
        title="오늘 끝나는 마감 강좌",
        facility="농업기술센터",
        status="접수마감",
        period="2099.07.10. ~ 2099.07.20",
        apply="2099.06.01 09:00:00. ~ 2099.06.30 17:59:59",
        method="전화접수, 방문접수",
    ),
    _record(
        "109",
        title="접수 예정 강좌",
        facility="화전도서관",
        status="접수대기",
        period="2099.09.01. ~ 2099.09.30",
        apply="2099.08.01 09:00:00. ~ 2099.08.20 17:59:59",
    ),
    *(
        _record(
            str(identity),
            title=f"종료 강좌 {identity}",
            facility="군민정보화교육장",
            status="",
            period="2098.01.01. ~ 2098.02.01",
            apply="2097.12.01 09:00:00. ~ 2097.12.20 17:59:59",
            method="방문접수",
        )
        for identity in range(108, 100, -1)
    ),
)


def _target(
    *,
    provider: str = namhae.NAMHAE_PROVIDER,
    url: str = namhae.NAMHAE_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "남해군 교육",
        "branch": "경상남도 남해군",
    }


def _search_form(displayed_page: int) -> str:
    return f"""
      <form id="frmLecture" name="frmLecture" method="get"
        action="{namhae.NAMHAE_LIST_PATH}?pageCd={namhae.NAMHAE_PAGE_CD}">
        <input type="hidden" name="amode" value="">
        <input type="hidden" name="_url" value="?">
        <input type="hidden" name="cpage" value="{displayed_page}">
        <input type="hidden" name="pageCd" value="{namhae.NAMHAE_PAGE_CD}">
        <input type="hidden" name="siteGubun" value="">
        <input type="hidden" name="facCode" value="">
        <input type="hidden" name="orderGb" value="">
        <select name="starget"><option selected value="">교육대상</option><option value="0">군민</option></select>
        <select name="scategory"><option selected value="">교육분류</option><option value="10">취미</option></select>
        <select name="splace"><option selected value="">교육시설</option><option value="FA0010">평생학습관</option></select>
        <select name="stype"><option value="title">과정명</option></select>
        <input type="text" name="sstring" value="">
      </form>
    """


def _card(record: Mapping[str, str], *, requested_page: int, title: str | None = None) -> str:
    query = (
        f"?amode=view&idx={record['id']}&pageCd={namhae.NAMHAE_PAGE_CD}"
        + (f"&cpage={requested_page}" if requested_page > 1 else "")
    )
    status = (
        f'<a class="button primary radius">{record["status"]}</a>'
        if record["status"]
        else ""
    )
    return f"""
      <li class="li1">
        <a class="col a1" href="{query}">
          <span class="col texts">
            <strong class="t1"><strong>[{record['facility']}]</strong>
              {title or record['title']}</strong>
            <span class="t2">교육기간 : {record['period']}</span>
            <span class="t2">모집기간 : {record['apply']}</span>
            <span class="t2">모집인원 : {record['capacity']}</span>
            <span class="t2">접수방법 : {record['method']}</span>
            <span class="t2">교육시간 : {record['time']}</span>
            <span class="t2">모집대상 : {record['target']}</span>
            <span class="t2">선정방식 : {record['selection']}</span>
          </span>
        </a>
        <div class="col btns">{status}<a href="/plan.pdf">강의계획서</a></div>
      </li>
    """


def _list_html(
    requested_page: int,
    displayed_page: int,
    rows: tuple[Mapping[str, str], ...],
    *,
    title_override: str | None = None,
) -> str:
    cards = "".join(
        _card(
            record,
            requested_page=requested_page,
            title=title_override if index == 0 else None,
        )
        for index, record in enumerate(rows)
    )
    return f"""
      <html><body><div id="body_content">
        {_search_form(displayed_page)}
        <div class="left"><div class="info1">
          총 <b class="em">12</b>건의 교육이 있습니다.
          (<b>{displayed_page}</b>/<b>2</b> 페이지)
        </div></div>
        <div class="list1f1t2b2"><ul class="lst1">{cards}</ul></div>
      </div></body></html>
    """


def _detail_html(
    record: Mapping[str, str],
    *,
    wrong_title: bool = False,
    wrong_period: bool = False,
    wrong_control_id: bool = False,
) -> str:
    title = "다른 강좌" if wrong_title else record["title"]
    period = "2099.01.01 ~ 2099.01.02" if wrong_period else record["period"]
    if record["status"] in {"신청하기", "대기자신청"}:
        identity = "999" if wrong_control_id else record["id"]
        control = (
            f'<a class="button primary large radius" '
            f'href="?amode=ins&lecIdx={identity}&pageCd={namhae.NAMHAE_PAGE_CD}">'
            f'{record["status"]}</a>'
        )
    else:
        control = ""
    return f"""
      <html><body>
        <div class="view1pic1info1 panel5 pd4pct">
          <div class="texts"><h1 class="h1">{title}</h1>
            <div class="info1"><table class="t3 ttvam"><tbody>
              <tr><th>교육기간</th><td>{period}</td></tr>
              <tr><th>교육시간</th><td>{record['time']}</td></tr>
              <tr><th>수강료</th><td>{record['fee']}</td></tr>
              <tr><th>준비물</th><td>담당자 자유본문 010-1234-5678</td></tr>
              <tr><th>접수기간</th><td>{record['apply']}</td></tr>
              <tr><th>모집대상</th><td>{record['target']}</td></tr>
              <tr><th>모집인원</th><td>{record['capacity']}</td></tr>
              <tr><th>모집지역</th><td>남해군</td></tr>
              <tr><th>교육장소</th><td>{record['venue']}</td></tr>
              <tr><th>접수방법</th><td>{record['method']}</td></tr>
              <tr><th>이용문의</th><td>055-860-1234</td></tr>
              <tr><th>선정방식</th><td>{record['selection']}</td></tr>
            </tbody></table></div>
          </div>{control}
        </div>
        <div class="free-body">강사 개인 소개와 연락처 010-9999-9999</div>
        <table class="applicants"><thead><tr><th>이름</th><th>연락처</th></tr></thead>
          <tbody><tr><td>김OO</td><td>010-****-1234</td></tr></tbody></table>
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
        wrong_control_id: bool = False,
        unknown_status: bool = False,
        duplicate_identity: bool = False,
    ) -> None:
        self.bad_clamp = bad_clamp
        self.mutate_recheck = mutate_recheck
        self.wrong_title = wrong_title
        self.wrong_period = wrong_period
        self.wrong_control_id = wrong_control_id
        self.unknown_status = unknown_status
        self.duplicate_identity = duplicate_identity
        self.calls: Counter[tuple[str, str]] = Counter()
        self.sessions: list[DummySession] = []
        self.application_fetches = 0

    def session_factory(self) -> DummySession:
        current = DummySession()
        self.sessions.append(current)
        return current

    def _records(self) -> tuple[dict[str, str], ...]:
        rows = [dict(record) for record in RECORDS]
        if self.unknown_status:
            rows[3]["status"] = "임의상태"
        if self.duplicate_identity:
            rows[1]["id"] = rows[0]["id"]
        return tuple(rows)

    def fetcher(
        self,
        _session: DummySession,
        method: str,
        url: str,
        *,
        timeout: int,
        data: Mapping[str, str],
    ) -> tuple[BeautifulSoup, str]:
        assert method == "GET"
        assert timeout > 0
        assert data == {}
        parsed = urlparse(url)
        assert parsed.hostname == namhae.NAMHAE_HOST
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query.get("amode") == ["ins"]:
            self.application_fetches += 1
            raise AssertionError("application forms must never be fetched")
        records = self._records()
        if query.get("amode") == ["view"]:
            identity = query["idx"][0]
            self.calls[("detail", identity)] += 1
            record = next(item for item in records if item["id"] == identity)
            html = _detail_html(
                record,
                wrong_title=self.wrong_title and identity == "112",
                wrong_period=self.wrong_period and identity == "112",
                wrong_control_id=self.wrong_control_id and identity == "112",
            )
            return BeautifulSoup(html, "lxml"), url

        requested = int((query.get("cpage") or ["1"])[0])
        self.calls[("list", str(requested))] += 1
        if requested == 1:
            rows = records[:10]
            override = (
                "재조회 중 바뀐 강좌"
                if self.mutate_recheck and self.calls[("list", "1")] >= 2
                else None
            )
            html = _list_html(1, 1, rows, title_override=override)
        elif requested == 2:
            html = _list_html(2, 2, records[10:])
        elif requested == 3:
            override = "잘못된 sentinel 행" if self.bad_clamp else None
            html = _list_html(3, 2, records[10:], title_override=override)
        else:
            raise AssertionError(f"unexpected list page {requested}")
        return BeautifulSoup(html, "lxml"), url


def _collect(site: FakeSite, **kwargs: Any):
    return namhae.collect_gyeongnam_namhae_education_courses(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 5),
        detail_limit=kwargs.pop("detail_limit", 4),
        session_factory=site.session_factory,
        fetcher=site.fetcher,
        today=kwargs.pop("today", "2099-07-20"),
        max_workers=1,
        **kwargs,
    )


def test_candidates_exact_target_alias_and_separate_provincial_sources() -> None:
    assert namhae.NAMHAE_CANDIDATE_DECISIONS == {
        "MUNI_IR_13A401B839CA": "schedule_existing_as_complete_unfiltered_catalogue",
        "MUNI_IR_896653A02C78": "reject_low_value_unverified_wikipedia",
    }
    assert namhae.is_gyeongnam_namhae_education_target(_target())
    assert not namhae.is_gyeongnam_namhae_education_target(
        _target(url=namhae.NAMHAE_URL + "&splace=FA0004")
    )
    assert not namhae.is_gyeongnam_namhae_education_target(
        _target(url=namhae.namhae_detail_url("112"))
    )
    alias = namhae.NAMHAE_ALIASES[0]
    assert namhae.is_gyeongnam_namhae_alias_target(
        _target(provider=alias.provider, url=alias.url)
    )
    assert set(namhae.NAMHAE_SEPARATE_PROVINCIAL_PROVIDERS) == {
        "MUNI_WWW_GNDAMOA_OR_KR_8127C6EE",
        "MUNI_WWW_GNDAMOA_OR_KR_CBAEF94B",
    }


def test_complete_snapshot_clamp_boundaries_details_controls_and_pii() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == namhae.NAMHAE_PARSER
    assert len(rows) == 4
    assert meta["declared_total"] == meta["source_total"] == 12
    assert meta["data_pages"] == 2
    assert meta["list_requests"] == meta["required_list_requests"] == 5
    assert meta["sentinel_mode"] == "clamped_last"
    assert meta["sentinel_count"] == 2
    assert meta["stable_rechecks"] == {"1": True, "2": True}
    assert meta["current_count"] == 4
    assert meta["expired_count"] == 8
    assert meta["detail_attempts"] == meta["detail_pages"] == 4
    assert meta["privacy_violations"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert site.application_fetches == 0
    assert all(session.closed for session in site.sessions)

    by_id = {row["raw_fields"]["source_education_id"]: row for row in rows}
    assert by_id["112"]["status"] == "OPEN"
    assert by_id["112"]["application_type"] == "ONLINE_RESERVATION"
    assert by_id["112"]["application_url"] == namhae.namhae_application_url("112")
    assert by_id["111"]["application_type"] == "WAITLIST_APPLY"
    assert by_id["110"]["status"] == "CLOSED"
    assert by_id["110"]["application_url"] == ""
    assert by_id["109"]["status"] == "SCHEDULED"
    assert by_id["109"]["application_type"] == "INFO_ONLY"
    assert all(row["municipality_code"] == "4884000000" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["raw_fields"]["application_form_fetched"] is False for row in rows)

    serialized = repr(rows)
    assert "055-" not in serialized
    assert "010-" not in serialized
    assert "김OO" not in serialized
    assert "강사 개인" not in serialized
    assert "담당자 자유본문" not in serialized


def test_list_and_detail_caps_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(), max_pages=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(), detail_limit=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_clamp_and_boundary_mutation_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(bad_clamp=True))
    assert rows == []
    assert "clamp differs" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(mutate_recheck=True))
    assert rows == []
    assert "stable boundary recheck changed" in meta["configured_collection_error"]


def test_detail_identity_title_and_period_contracts_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(wrong_title=True))
    assert rows == []
    assert "list/detail title mismatch" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(wrong_period=True))
    assert rows == []
    assert "list/detail education period mismatch" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(wrong_control_id=True))
    assert rows == []
    assert "malformed application control" in meta["configured_collection_error"]


def test_unknown_current_status_duplicate_identity_and_dedupe_loss_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(unknown_status=True))
    assert rows == []
    assert "unknown current source status" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(duplicate_identity=True))
    assert rows == []
    assert "duplicate source identities" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        FakeSite(), dedupe_rows=lambda source: source[:1]
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]


def test_complete_archive_is_valid_empty_snapshot_without_detail_fetches() -> None:
    site = FakeSite()
    rows, _parser, meta = _collect(site, today="2100-01-01", detail_limit=0)
    assert rows == []
    assert meta["source_total"] == 12
    assert meta["current_count"] == 0
    assert meta["expired_count"] == 12
    assert meta["detail_attempts"] == 0
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]
    assert not any(kind == "detail" for kind, _identity in site.calls)


def test_managed_session_is_required_and_alias_is_not_collected() -> None:
    rows, _parser, meta = namhae.collect_gyeongnam_namhae_education_courses(
        _target(), timeout=5, max_pages=5, detail_limit=4
    )
    assert rows == []
    assert meta["configured_collection_error"] == "managed session_factory injection is required"

    alias = namhae.NAMHAE_ALIASES[0]
    site = FakeSite()
    rows, _parser, meta = namhae.collect_gyeongnam_namhae_education_courses(
        _target(provider=alias.provider, url=alias.url),
        session_factory=site.session_factory,
        fetcher=site.fetcher,
    )
    assert rows == []
    assert "canonical Gyeongnam Namhae" in meta["configured_collection_error"]
    assert site.sessions == []
