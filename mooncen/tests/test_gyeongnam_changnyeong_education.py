from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_gyeongnam_changnyeong as changnyeong


def _record(
    identity: str,
    *,
    title: str,
    facility: str,
    status: str,
    period: str,
    apply: str,
    method: str = "온라인, 현장",
    control: bool = False,
    capacity_schema: int = 2,
    time: str = "매주 화요일 10:00~12:00",
) -> dict[str, Any]:
    return {
        "id": identity,
        "title": title,
        "facility": facility,
        "status": status,
        "period": period,
        "apply": apply,
        "method": method,
        "control": control,
        "capacity_schema": capacity_schema,
        "target": "창녕군민 누구나",
        "time": time,
        "capacity": "20 명",
        "fee": "무료",
        "venue": f"{facility} 2층 강의실",
    }


RECORDS = (
    _record(
        "200",
        title="온라인 미래 강좌",
        facility="군청 평생학습관",
        status="접수중",
        period="2099.08.01. ~ 2099.08.31.",
        apply="2099.07.01. ~ 2099.07.31.",
        control=True,
        capacity_schema=3,
    ),
    _record(
        "199",
        title="대기자 미래 강좌",
        facility="창녕군여성회관",
        status="대기자신청",
        period="2099.08.02. ~ 2099.09.01.",
        apply="2099.07.01. ~ 2099.07.31.",
        control=True,
        capacity_schema=3,
    ),
    _record(
        "198",
        title="현장 접수 강좌",
        facility="창녕군청소년수련관",
        status="접수중",
        period="2099.08.03. ~ 2099.09.03.",
        apply="2099.07.01. ~ 2099.07.31.",
        method="현장",
        capacity_schema=4,
        time="",
    ),
    _record(
        "197",
        title="접수 예정 강좌",
        facility="남지청소년문화의집",
        status="접수대기",
        period="2099.09.01. ~ 2099.09.30.",
        apply="2099.08.01. ~ 2099.08.20.",
        capacity_schema=1,
    ),
    _record(
        "196",
        title="오늘 끝나는 마감 강좌",
        facility="영산청소년문화의집",
        status="신청마감",
        period="2099.07.10. ~ 2099.07.20.",
        apply="2099.06.01. ~ 2099.06.30.",
        method="전화, 현장",
    ),
    *(
        _record(
            str(identity),
            title=f"종료 강좌 {identity}",
            facility="영산도서관",
            status="신청완료" if identity == 195 else "신청마감",
            period="2098.01.01. ~ 2098.02.01.",
            apply="2097.12.01. ~ 2097.12.20.",
            method="현장",
        )
        for identity in range(195, 187, -1)
    ),
)


def _target(
    *,
    provider: str = changnyeong.CHANGNYEONG_PROVIDER,
    url: str = changnyeong.CHANGNYEONG_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "창녕군 통합교육",
        "branch": "경상남도 창녕군",
    }


def _options(values: Mapping[str, str]) -> str:
    return "".join(
        f'<option value="{key}">{value}</option>' for key, value in values.items()
    )


def _search_form(
    requested_page: int, *, bad_facility_vocabulary: bool = False
) -> str:
    facilities = dict(changnyeong.CHANGNYEONG_FACILITIES)
    if bad_facility_vocabulary:
        facilities["999"] = "임의 교육장"
    action_query = "" if requested_page == 1 else f"?cpage={requested_page}"
    return f"""
      <form id="frmLecture" name="frmLecture" method="get"
        action="{changnyeong.CHANGNYEONG_LIST_PATH}{action_query}">
        <select name="facCode">
          <option selected value="">전체</option>{_options(facilities)}
        </select>
        <select name="applyGubun">
          <option selected value="">전체</option>
          <option value="1">접수중</option><option value="2">교육신청</option>
          <option value="3">신청마감</option><option value="4">접수대기</option>
          <option value="5">대기자신청</option><option value="6">신청완료</option>
        </select>
        <select name="stype"><option selected value="title">과정명</option></select>
        <input name="sstring" value="">
      </form>
    """


def _badge_label(status: str) -> str:
    return "교육신청" if status in {"접수중", "교육신청"} else status


def _card(
    record: Mapping[str, Any],
    *,
    requested_page: int,
    title: str | None = None,
) -> str:
    page_query = "" if requested_page == 1 else f"&amp;cpage={requested_page}"
    return f"""
      <li class="column"><div class="w1">
        <a class="a1" href="?amode=view&amp;idx={record['id']}{page_query}">
          <div class="tg1">
            <i class="c" data-progress="{record['status']}">
              {_badge_label(str(record['status']))}
            </i>
            <strong class="t1">[{record['facility']}] - {title or record['title']}</strong>
          </div>
          <div class="tg2">
            <span class="place1">[{record['facility']}]</span>
            <span class="li1"><span class="t1">모집인원</span>
              <span class="t2">{record['capacity']}</span></span>
            <span class="li1"><span class="t1">교육기간</span>
              <span class="t2">{record['period']}</span></span>
            <span class="li1"><span class="t1">교육시간</span>
              <span class="t2">{record['time']}</span></span>
            <span class="li1"><span class="t1">모집기간</span>
              <span class="t2">{record['apply']}</span></span>
            <span class="li1"><span class="t1">접수방법</span>
              <span class="t2">{record['method']}</span></span>
          </div>
        </a>
      </div></li>
    """


def _pagination(displayed_page: int) -> str:
    if displayed_page == 1:
        links = """
          <span class="pages"><span class="m on"><a>1</a></span>
            <span class="m"><a href="?&amp;cpage=2">2</a></span></span>
          <span class="control"><span class="m last">
            <a href="?&amp;cpage=2">»</a></span></span>
        """
    else:
        links = """
          <span class="control"><span class="m first">
            <a href="?&amp;cpage=1">«</a></span></span>
          <span class="pages"><span class="m"><a href="?&amp;cpage=1">1</a></span>
            <span class="m on"><a>2</a></span></span>
        """
    return f'<div class="pagination">{links}</div>'


def _list_html(
    requested_page: int,
    displayed_page: int,
    rows: tuple[Mapping[str, Any], ...],
    *,
    title_override: str | None = None,
    bad_facility_vocabulary: bool = False,
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
        {_search_form(requested_page, bad_facility_vocabulary=bad_facility_vocabulary)}
        <div class="cp8card1"><ul>{cards}</ul></div>
        {_pagination(displayed_page)}
      </div></body></html>
    """


def _capacity_table(record: Mapping[str, Any]) -> str:
    headings = ["모집정원"]
    values = [record["capacity"]]
    schema = int(record["capacity_schema"])
    if schema in {2, 3}:
        headings.append("신청인원")
        values.append("7명")
    if schema in {3, 4}:
        headings.append("대기인원")
        values.append("5 / 1 명")
    return f"""
      <table class="tbl1 t2"><thead><tr>
        {''.join(f'<th>{value}</th>' for value in headings)}
      </tr></thead><tbody><tr>
        {''.join(f'<td>{value}</td>' for value in values)}
      </tr></tbody></table>
    """


def _detail_html(
    record: Mapping[str, Any],
    *,
    wrong_title: bool = False,
    wrong_period: bool = False,
    wrong_control_id: bool = False,
) -> str:
    title = "다른 강좌" if wrong_title else record["title"]
    period = "2099.01.01. ~ 2099.01.02." if wrong_period else record["period"]
    control = ""
    if record["control"]:
        identity = "999" if wrong_control_id else record["id"]
        label = _badge_label(str(record["status"]))
        control = (
            f'<div class="infomenu1"><a class="button large primary" '
            f'href="?amode=ins_realname&lecIdx={identity}&">{label}</a></div>'
        )
    return f"""
      <html><body><div id="body_content">
        <div class="cp31edu1view1"><div class="w1"><div class="w1c2">
          <div class="texts"><h2 class="hb1 h2">{title}</h2><ul class="bu">
            <li><span class="dt">시설구분 :</span><span class="dd">{record['facility']}</span></li>
            <li><span class="dt">접수방법 :</span><span class="dd">{record['method']}</span></li>
            <li><span class="dt">교육기간 :</span><span class="dd">{period}</span></li>
            <li><span class="dt">교육시간 :</span><span class="dd">{record['time']}</span></li>
            <li><span class="dt">모집기간 :</span><span class="dd">
              {str(record['apply']).replace(' ~ ', ' 09시 00분 ~ ')} 18시 00분</span></li>
            <li><span class="dt">대상 :</span><span class="dd">{record['target']}</span></li>
            <li><span class="dt">강사명 :</span><span class="dd">김강사</span></li>
            <li><span class="dt">교육장소 :</span><span class="dd">{record['venue']}</span></li>
            <li><span class="dt">수강료 :</span><span class="dd">{record['fee']}</span></li>
            <li><span class="dt">문의전화 :</span><span class="dd">055-530-1234</span></li>
          </ul></div>
        </div></div></div>
        {_capacity_table(record)}
        <h3>교육소개</h3><div class="panel1">
          강사 개인 소개 010-9999-9999 <a href="/private-plan.pdf">첨부파일</a>
        </div>
        {control}
        <table class="applicants"><tr><td>김OO</td><td>010-****-1234</td></tr></table>
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
        wrong_title: bool = False,
        wrong_period: bool = False,
        wrong_control_id: bool = False,
        unknown_status: bool = False,
        duplicate_identity: bool = False,
        semantic_duplicate: bool = False,
        bad_facility_vocabulary: bool = False,
        fail_once_detail: bool = False,
    ) -> None:
        self.bad_clamp = bad_clamp
        self.mutate_recheck = mutate_recheck
        self.wrong_title = wrong_title
        self.wrong_period = wrong_period
        self.wrong_control_id = wrong_control_id
        self.unknown_status = unknown_status
        self.duplicate_identity = duplicate_identity
        self.semantic_duplicate = semantic_duplicate
        self.bad_facility_vocabulary = bad_facility_vocabulary
        self.fail_once_detail = fail_once_detail
        self.calls: Counter[tuple[str, str]] = Counter()
        self.sessions: list[DummySession] = []
        self.application_fetches = 0

    def session_factory(self) -> DummySession:
        current = DummySession()
        self.sessions.append(current)
        return current

    def _records(self) -> tuple[dict[str, Any], ...]:
        rows = [dict(record) for record in RECORDS]
        if self.unknown_status:
            rows[3]["status"] = "임의상태"
        if self.duplicate_identity:
            rows[1]["id"] = rows[0]["id"]
        if self.semantic_duplicate:
            rows[1].update(
                {
                    "title": rows[0]["title"],
                    "facility": rows[0]["facility"],
                    "period": rows[0]["period"],
                    "time": rows[0]["time"],
                }
            )
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
        assert parsed.hostname == changnyeong.CHANGNYEONG_HOST
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query.get("amode") == ["ins_realname"]:
            self.application_fetches += 1
            raise AssertionError("application forms must never be fetched")

        records = self._records()
        if query.get("amode") == ["view"]:
            identity = query["idx"][0]
            self.calls[("detail", identity)] += 1
            if (
                self.fail_once_detail
                and identity == "200"
                and self.calls[("detail", identity)] == 1
            ):
                raise ConnectionError("synthetic transient reset")
            record = next(item for item in records if item["id"] == identity)
            html = _detail_html(
                record,
                wrong_title=self.wrong_title and identity == "200",
                wrong_period=self.wrong_period and identity == "200",
                wrong_control_id=self.wrong_control_id and identity == "200",
            )
            return BeautifulSoup(html, "lxml"), url

        requested = int((query.get("cpage") or ["1"])[0])
        self.calls[("list", str(requested))] += 1
        if requested == 1:
            override = (
                "재조회 중 바뀐 강좌"
                if self.mutate_recheck
                and self.calls[("list", "1")] >= 2
                else None
            )
            html = _list_html(
                1,
                1,
                records[:9],
                title_override=override,
                bad_facility_vocabulary=self.bad_facility_vocabulary,
            )
        elif requested == 2:
            html = _list_html(
                2,
                2,
                records[9:],
                bad_facility_vocabulary=self.bad_facility_vocabulary,
            )
        elif requested == 3:
            override = "잘못된 sentinel 행" if self.bad_clamp else None
            html = _list_html(
                3,
                2,
                records[9:],
                title_override=override,
                bad_facility_vocabulary=self.bad_facility_vocabulary,
            )
        else:
            raise AssertionError(f"unexpected list page {requested}")
        return BeautifulSoup(html, "lxml"), url


def _collect(site: FakeSite, **kwargs: Any):
    return changnyeong.collect_gyeongnam_changnyeong_education_courses(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 5),
        detail_limit=kwargs.pop("detail_limit", 5),
        session_factory=site.session_factory,
        fetcher=site.fetcher,
        today=kwargs.pop("today", "2099-07-20"),
        max_workers=1,
        **kwargs,
    )


def test_candidate_ids_target_alias_and_separate_public_owner() -> None:
    assert changnyeong.CHANGNYEONG_CANDIDATE_DECISIONS == {
        "MUNI_IR_04BAD6FB9F65": "schedule_new_complete_unfiltered_education_ledger",
        "MUNI_IR_35DA60303072": "retarget_redirect_alias_to_canonical_ledger",
        "MUNI_IR_CC8A962EE8FB": "exclude_separate_provincial_library_owner",
        "MUNI_IR_633F4DEC9CBE": "exclude_metadata_page_not_course_ledger",
    }
    assert len(changnyeong.CHANGNYEONG_FACILITIES) == 31
    assert changnyeong.is_gyeongnam_changnyeong_education_target(_target())
    assert not changnyeong.is_gyeongnam_changnyeong_education_target(
        _target(url=changnyeong.CHANGNYEONG_URL + "?facCode=001")
    )
    assert not changnyeong.is_gyeongnam_changnyeong_education_target(
        _target(url=changnyeong.changnyeong_detail_url("200"))
    )
    alias = changnyeong.CHANGNYEONG_ALIASES[0]
    assert changnyeong.is_gyeongnam_changnyeong_alias_target(
        _target(provider=alias.provider, url=alias.url)
    )
    assert changnyeong.CHANGNYEONG_SEPARATE_PUBLIC_PROVIDERS == (
        "MUNI_CNLIB_GNE_GO_KR_A3514402",
    )


def test_complete_snapshot_clamp_details_controls_branches_and_pii() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == changnyeong.CHANGNYEONG_PARSER
    assert len(rows) == 5
    assert meta["source_total"] == meta["inferred_total"] == 13
    assert meta["data_pages"] == 2
    assert meta["list_requests"] == meta["required_list_requests"] == 5
    assert meta["sentinel_mode"] == "clamped_last"
    assert meta["sentinel_count"] == 4
    assert meta["stable_rechecks"] == {"1": True, "2": True}
    assert meta["current_count"] == 5
    assert meta["expired_count"] == 8
    assert meta["detail_attempts"] == meta["detail_pages"] == 5
    assert meta["semantic_duplicate_count"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["active_status_without_control_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert site.application_fetches == 0
    assert all(session.closed for session in site.sessions)

    by_id = {row["raw_fields"]["source_education_id"]: row for row in rows}
    assert by_id["200"]["status"] == "OPEN"
    assert by_id["200"]["application_type"] == "ONLINE_RESERVATION"
    assert by_id["200"]["application_url"] == (
        changnyeong.changnyeong_application_url("200")
    )
    assert by_id["199"]["status"] == "WAITING"
    assert by_id["199"]["application_type"] == "WAITLIST_APPLY"
    assert by_id["198"]["status"] == "OPEN"
    assert by_id["198"]["reservation_available"] is False
    assert by_id["198"]["schedule_raw"] == "공식 페이지 시간 미기재"
    assert by_id["198"]["raw_fields"]["schedule_source_omission"] is True
    assert by_id["198"]["capacity_current"] is None
    assert by_id["198"]["raw_fields"]["source_capacity_schema"] == [
        "모집정원",
        "대기인원",
    ]
    assert by_id["197"]["status"] == "SCHEDULED"
    assert by_id["196"]["status"] == "CLOSED"
    assert all(row["municipality_code"] == "4874000000" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(
        row["raw_fields"]["application_form_fetched"] is False for row in rows
    )

    serialized = repr(rows)
    assert "055-" not in serialized
    assert "010-" not in serialized
    assert "김OO" not in serialized
    assert "김강사" not in serialized
    assert "강사 개인 소개" not in serialized


def test_caps_clamp_boundaries_and_official_vocabulary_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(), max_pages=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(), detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(bad_clamp=True))
    assert rows == []
    assert "clamp differs" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(mutate_recheck=True))
    assert rows == []
    assert "stable boundary recheck changed" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(bad_facility_vocabulary=True))
    assert rows == []
    assert "official facility vocabulary changed" in meta["configured_collection_error"]


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


def test_unknown_status_identity_semantic_and_dedupe_loss_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(unknown_status=True))
    assert rows == []
    assert "unknown source status" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(duplicate_identity=True))
    assert rows == []
    assert "duplicate source identities" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(semantic_duplicate=True))
    assert rows == []
    assert meta["semantic_duplicate_count"] == 1
    assert "duplicate current semantic signatures" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        FakeSite(), dedupe_rows=lambda source: source[:1]
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]


def test_retry_accounting_archive_and_managed_session_contract() -> None:
    site = FakeSite(fail_once_detail=True)
    rows, _parser, meta = _collect(site)
    assert len(rows) == 5
    assert meta["network_retry_count"] == 1
    assert site.calls[("detail", "200")] == 2

    archived = FakeSite()
    rows, _parser, meta = _collect(
        archived, today="2100-01-01", detail_limit=0
    )
    assert rows == []
    assert meta["source_total"] == 13
    assert meta["current_count"] == 0
    assert meta["expired_count"] == 13
    assert meta["detail_attempts"] == 0
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]
    assert not any(kind == "detail" for kind, _identity in archived.calls)

    rows, _parser, meta = (
        changnyeong.collect_gyeongnam_changnyeong_education_courses(
            _target(), timeout=5, max_pages=5, detail_limit=5
        )
    )
    assert rows == []
    assert meta["configured_collection_error"] == (
        "managed session_factory injection is required"
    )

    alias = changnyeong.CHANGNYEONG_ALIASES[0]
    site = FakeSite()
    rows, _parser, meta = (
        changnyeong.collect_gyeongnam_changnyeong_education_courses(
            _target(provider=alias.provider, url=alias.url),
            session_factory=site.session_factory,
            fetcher=site.fetcher,
        )
    )
    assert rows == []
    assert "canonical Gyeongnam Changnyeong" in meta["configured_collection_error"]
    assert site.sessions == []
