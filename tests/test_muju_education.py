from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_muju as muju


def _record(
    identity: str,
    *,
    title: str,
    category: str,
    status: str,
    period: str,
    apply: str,
    target: str = "무주군민",
    capacity: str = "[선착순] 0 / 10 명",
    venue: str = "무주군평생교육원 103호",
    schedule: str = "10:00~12:00",
    fee: str = "무료",
) -> dict[str, str]:
    start, end = [value.strip() for value in apply.split("~")]
    return {
        "id": identity,
        "title": title,
        "category": category,
        "status": status,
        "period": period,
        "apply": apply,
        "detail_apply": f"{start[:10]} ~ {end[:10]}",
        "target": target,
        "capacity": capacity,
        "venue": venue,
        "schedule": schedule,
        "fee": fee,
    }


ONLINE_RECORDS = (
    _record(
        "194",
        title="한국전통 매듭공예",
        category="평생교육활성화",
        status="수강신청",
        period="2026-06-30 ~ 2026-08-18",
        apply="2026-06-01 09:00 ~ 2026-07-29 23:00",
        capacity="[선착순] 5 / 10 명",
    ),
    _record(
        "202",
        title="2026 공무원시험 준비반 운영(추가모집)",
        category="직업능력",
        status="신청준비",
        period="2026-08-15 ~ 2026-06-30",
        apply="2026-07-27 09:00 ~ 2026-07-31 18:00",
        capacity="[선착순] 0 / 3 명",
        venue="",
        schedule="",
    ),
    _record(
        "13",
        title="2023년 공무원시험 준비반",
        category="직업능력",
        status="기간종료",
        period="2023-03-01 ~ 2023-12-31",
        apply="2023-01-25 09:00 ~ 2023-02-08 18:00",
        target="",
        capacity="[선착순] 20 / 20 명 (대기:20명)",
    ),
    _record(
        "30",
        title="한글, 생활문해교실",
        category="기초문해",
        status="기간종료",
        period="2023-03-13 ~ 2023-12-29",
        apply="2023-02-13 09:00 ~ 2023-03-02 18:00",
        target="",
    ),
    _record(
        "31",
        title="2023년 공무원시험 준비반(추가모집)",
        category="직업능력",
        status="기간종료",
        period="2023-03-14 ~ 2023-12-31",
        apply="2023-03-06 09:00 ~ 2023-03-10 18:00",
        target="",
        capacity="[선착순] 7 / 10 명",
    ),
    _record(
        "198",
        title="미술의 기초, 스케치 한걸음",
        category="평생교육활성화",
        status="기간종료",
        period="2026-07-03 ~ 2026-08-21",
        apply="2026-06-01 09:00 ~ 2026-06-14 23:00",
        capacity="[선착순] 10 / 10 명",
    ),
)


REGULAR_ROWS = (
    (
        "미술의 기초, 스케치 한걸음",
        "무주군민",
        "10",
        "7.3 ~ 8.21",
        "8(16)",
        "개인 연락처 010-9999-9999가 들어간 비저장 설명",
        "103호",
    ),
    (
        "초등학력 인정반",
        "일반군민",
        "",
        "3.24 ~ 6.30",
        "",
        "초등학력 보완 프로그램",
        "101호",
    ),
    (
        "예비중학반",
        "일반군민",
        "",
        "3.4 ~ 12.18",
        "",
        "중등학력 보완 프로그램 teacher@example.org",
        "102호",
    ),
)


ALL_LEARNING_ROWS = (
    ("무주읍", "경로당", "노래와함께 언제나 청춘", "노인(고령자)", "4.7 ~ 7.31", "설명"),
    ("무풍면", "고도마을회관", "실버공예", "일반주민", "7.1 ~ 8.31", "설명"),
    ("설천면", "마을회관", "좌도풍물", "일반주민", "4.7 ~ 7.21", "설명"),
    ("적상면", "마을회관", "한지공예", "일반주민", "3.9 ~ 6.8", "설명"),
    ("안성면", "마을회관", "토탈공예", "일반주민", "4.2 ~ 6.4", "설명"),
    ("부남면", "마을회관", "난타", "일반주민", "4.1 ~ 6.30", "설명"),
)


def _target(
    *, provider: str = muju.MUJU_PROVIDER, url: str = muju.MUJU_URL
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "무주군평생교육원",
        "branch": muju.MUJU_MUNICIPALITY_NAME,
    }


def _form() -> str:
    category_options = ['<option value="">분류선택</option>']
    category_options.extend(
        f'<option value="{code}">{label}</option>'
        for code, label in muju.MUJU_CATEGORY_OPTIONS.items()
    )
    return f"""
      <form name="frm_edu" method="get" action="list.do">
        <input type="hidden" name="sh_ct_idx" value="">
        <select name="sh_ct_idx2">{''.join(category_options)}</select>
        <select name="v_search">
          <option selected value="">검색항목</option>
          <option value="edu_subject">교육명</option>
          <option value="edu_content">내용</option>
        </select>
        <input name="v_keyword" value="">
      </form>
    """


def _detail_prepage(page: int) -> str:
    value = muju.MUJU_LIST_PATH
    if page > 1:
        value += f"?v_page={page}"
    return value


def _card(record: Mapping[str, str], page: int, *, title_override: str = "") -> str:
    identity = record["id"]
    detail_query = urlencode(
        (("edu_idx", identity), ("prepage", _detail_prepage(page)))
    )
    if record["status"] not in {"수강신청", "신청준비", "신청마감", "기간종료"}:
        control = (
            f'<a class="btn_sm btn_unknown" href="#javascript:;">'
            f'{record["status"]}</a>'
        )
    elif record["status"] == "수강신청":
        control_query = urlencode(
            (("edu_idx", identity), ("prepage", _detail_prepage(page)))
        )
        control = (
            f'<a class="btn_sm btn_ing" href="regist.do?{control_query}">'
            "수강신청</a>"
        )
    elif record["status"] == "신청준비":
        control = '<a class="btn_sm btn_prepare" href="#javascript:;">신청준비</a>'
    elif record["status"] == "신청마감":
        control = '<a class="btn_sm btn_end" href="#javascript:;">신청마감</a>'
    else:
        control = '<a class="btn_sm btn_close" href="#javascript:;">기간종료</a>'
    target = (
        f'<dl><dt>수강대상</dt><dd>{record["target"]}</dd></dl>'
        if record["target"]
        else ""
    )
    return f"""
      <li>
        <div class="cont">
          <p class="tit"><a href="view.do?{detail_query}">
            [{record['category']}] {title_override or record['title']}
          </a></p>
          <div class="sm_box">
            <dl><dt>신청기간</dt><dd>{record['apply']}</dd></dl>
            <dl><dt>운영기간</dt><dd>{record['period']}</dd></dl>
            {target}
            <dl><dt>모집인원</dt><dd>{record['capacity']}</dd></dl>
          </div>
        </div>
        <div class="btn_box">
          {control}
          <a class="btn_sm btn_check" href="/lifelongedu/main/site/mylib/myEdu.do?prepage=%2Flifelongedu%2Fmain%2Fedusat%2Flist.do">등록확인</a>
        </div>
      </li>
    """


def _pagination(page: int, last_page: int, *, sentinel: bool) -> str:
    values = []
    for current in range(1, last_page + 1):
        if current == page and not sentinel:
            values.append(f"<strong>{current}</strong>")
        else:
            values.append(
                f'<a data-page="{current}" href="?v_page={current}">{current}</a>'
            )
    return f'<div class="board_paginate">{"".join(values)}</div>'


def _list_html(
    records: tuple[Mapping[str, str], ...],
    *,
    requested_page: int,
    displayed_page: int,
    total: int,
    last_page: int,
    sentinel: bool,
    title_override: str = "",
) -> str:
    cards = "".join(
        _card(
            record,
            requested_page,
            title_override=title_override if index == 0 else "",
        )
        for index, record in enumerate(records)
    )
    return f"""
      <html><body><div id="board">
        {_form()}
        <div class="board_total_left">총 <strong class="eng">{total}</strong>개의 프로그램이 등록되어 있습니다.</div>
        <div class="lesson no_top"><ul>{cards}</ul></div>
        {_pagination(displayed_page, last_page, sentinel=sentinel)}
      </div></body></html>
    """


def _detail_html(
    record: Mapping[str, str],
    *,
    page: int,
    title_override: str = "",
    period_override: str = "",
    control_identity: str = "",
) -> str:
    status, badge_class = {
        "수강신청": ("신청중", "btn_receipt"),
        "신청준비": ("신청준비", "btn_prepare"),
        "신청마감": ("신청마감", "btn_end"),
        "기간종료": ("기간종료", "btn_close"),
    }[record["status"]]
    optional = ""
    if record["schedule"]:
        optional += f'<tr><td><dl class="info"><dt>강좌시간</dt><dd>{record["schedule"]}</dd></dl></td></tr>'
    if record["target"]:
        optional += f'<tr><td><dl class="info"><dt>수강대상</dt><dd>{record["target"]}</dd></dl></td></tr>'
    if record["venue"]:
        optional += f'<tr><td><dl class="info"><dt>강의실</dt><dd>{record["venue"]}</dd></dl></td></tr>'
    if record["fee"]:
        optional += f'<tr><td><dl class="info"><dt>참가비</dt><dd>{record["fee"]}</dd></dl></td></tr>'
    application = ""
    if record["status"] == "수강신청":
        identity = control_identity or record["id"]
        inner = muju.MUJU_DETAIL_PATH + "?" + urlencode(
            (("edu_idx", record["id"]), ("prepage", _detail_prepage(page)))
        )
        query = urlencode((("edu_idx", identity), ("prepage", inner)))
        application = (
            f'<a class="con_btn btn_receipt" href="regist.do?{query}">신청</a>'
        )
    back = muju.MUJU_LIST_PATH + (f"?v_page={page}" if page > 1 else "")
    return f"""
      <html><body><div id="board">
        <div class="table_bview"><table><thead><tr>
          <th class="th_none" scope="col">{title_override or record['title']}
            <a class="btn_sm {badge_class}" href="#javascript:;">{status}</a>
          </th>
        </tr></thead><tbody>
          <tr><td><dl class="info"><dt>강좌기간</dt><dd>{period_override or record['period']}</dd></dl></td></tr>
          <tr><td><dl class="info"><dt>신청기간</dt><dd>{record['detail_apply']}</dd></dl></td></tr>
          {optional}
          <tr><td><dl class="info"><dt>모집인원</dt><dd>{record['capacity']}</dd></dl></td></tr>
          <tr><td><dl class="info"><dt>강사</dt><dd>민감강사 010-1234-5678</dd></dl></td></tr>
          <tr><td><dl class="info"><dt>첨부파일</dt><dd><a href="down.do?edu_idx={record['id']}">teacher@example.org 계획서</a></dd></dl></td></tr>
          <tr><td class="content">개인 자유서술과 연락처 063-320-2254</td></tr>
        </tbody></table></div>
        <div class="btn_w">{application}<a class="con_btn gray" href="{back}">목록</a></div>
      </div></body></html>
    """


def _static_html(source_key: str, rows: tuple[tuple[str, ...], ...]) -> str:
    source = next(item for item in muju.MUJU_STATIC_SOURCES if item.key == source_key)
    heading = (
        "2026년 상반기 무주군평생교육원 프로그램 운영계획"
        if source_key == "regular_plan"
        else "2026년 전세대 학습공간 모두배움터 운영 현황"
    )
    headers = "".join(f"<th>{value}</th>" for value in source.headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"""
      <html><body><div id="contents">
        <h2>{source.label}</h2><h3 class="tit">{heading}</h3>
        <table class="table1"><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>
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
        bad_sentinel: bool = False,
        mutate_first_recheck: bool = False,
        mutate_static_recheck: bool = False,
        unknown_status: bool = False,
        bad_category: bool = False,
        duplicate_identity: bool = False,
        detail_title_mismatch: bool = False,
        detail_period_mismatch: bool = False,
        wrong_control_id: bool = False,
        fail_once_detail: bool = False,
    ) -> None:
        self.bad_sentinel = bad_sentinel
        self.mutate_first_recheck = mutate_first_recheck
        self.mutate_static_recheck = mutate_static_recheck
        self.unknown_status = unknown_status
        self.bad_category = bad_category
        self.duplicate_identity = duplicate_identity
        self.detail_title_mismatch = detail_title_mismatch
        self.detail_period_mismatch = detail_period_mismatch
        self.wrong_control_id = wrong_control_id
        self.fail_once_detail = fail_once_detail
        self.calls: Counter[tuple[str, str]] = Counter()
        self.sessions: list[DummySession] = []
        self.application_fetches = 0

    def session_factory(self) -> DummySession:
        current = DummySession()
        self.sessions.append(current)
        return current

    def _records(self) -> tuple[dict[str, str], ...]:
        records = [dict(record) for record in ONLINE_RECORDS]
        if self.unknown_status:
            records[0]["status"] = "임의상태"
        if self.bad_category:
            records[0]["category"] = "임의분류"
        if self.duplicate_identity:
            records[-1]["id"] = records[0]["id"]
        return tuple(records)

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
        assert parsed.hostname == muju.MUJU_HOST
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == muju.MUJU_APPLICATION_PATH:
            self.application_fetches += 1
            raise AssertionError("application endpoint must never be fetched")
        if parsed.path == muju.MUJU_DETAIL_PATH:
            identity = query["edu_idx"][0]
            self.calls[("detail", identity)] += 1
            if (
                self.fail_once_detail
                and identity == "194"
                and self.calls[("detail", identity)] == 1
            ):
                raise ConnectionError("synthetic transient reset")
            records = self._records()
            record = next(item for item in records if item["id"] == identity)
            page = 1 if identity != "198" else 2
            html = _detail_html(
                record,
                page=page,
                title_override=(
                    "다른 상세 제목"
                    if self.detail_title_mismatch and identity == "194"
                    else ""
                ),
                period_override=(
                    "2026-01-01 ~ 2026-01-02"
                    if self.detail_period_mismatch and identity == "194"
                    else ""
                ),
                control_identity=(
                    "999" if self.wrong_control_id and identity == "194" else ""
                ),
            )
            return BeautifulSoup(html, "lxml"), url
        if parsed.path == muju.MUJU_LIST_PATH:
            requested = int((query.get("v_page") or ["1"])[0])
            self.calls[("list", str(requested))] += 1
            records = self._records()
            last_page = (len(records) + muju.MUJU_PAGE_SIZE - 1) // muju.MUJU_PAGE_SIZE
            displayed = min(requested, last_page)
            start = (displayed - 1) * muju.MUJU_PAGE_SIZE
            selected = records[start : start + muju.MUJU_PAGE_SIZE]
            title_override = ""
            if requested > last_page and self.bad_sentinel:
                title_override = "변조된 sentinel 강좌"
            if (
                requested == 1
                and self.mutate_first_recheck
                and self.calls[("list", "1")] > 1
            ):
                title_override = "재확인 중 변조된 강좌"
            html = _list_html(
                selected,
                requested_page=requested,
                displayed_page=displayed,
                total=len(records),
                last_page=last_page,
                sentinel=requested > last_page,
                title_override=title_override,
            )
            return BeautifulSoup(html, "lxml"), url
        if parsed.path == muju.MUJU_CONTENTS_PATH:
            idx = query["idx"][0]
            source_key = "regular_plan" if idx == "4363" else "all_learning"
            self.calls[("static", source_key)] += 1
            rows = list(REGULAR_ROWS if source_key == "regular_plan" else ALL_LEARNING_ROWS)
            if (
                self.mutate_static_recheck
                and source_key == "all_learning"
                and self.calls[("static", source_key)] > 1
            ):
                values = list(rows[0])
                values[2] = "재확인 중 변조된 프로그램"
                rows[0] = tuple(values)
            return BeautifulSoup(_static_html(source_key, tuple(rows)), "lxml"), url
        raise AssertionError(f"unexpected route {url}")


def _collect(site: FakeSite, **kwargs: Any):
    return muju.collect(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 5),
        detail_limit=kwargs.pop("detail_limit", 10),
        max_workers=1,
        today=kwargs.pop("today", "2026-07-23"),
        session_factory=site.session_factory,
        fetcher=site.fetcher,
        **kwargs,
    )


def test_target_hashes_candidates_and_separate_owner_boundaries() -> None:
    assert muju.is_target(_target())
    assert not muju.is_target(_target(provider=muju.MUJU_AGRICULTURE_REVIEW_PROVIDER))
    assert not muju.is_target(_target(url=muju.MUJU_URL + "?v_page=1"))
    assert not muju.is_target(_target(url=muju.MUJU_AGRICULTURE_CANDIDATE_URL))
    assert hashlib.sha1(muju.MUJU_URL.encode()).hexdigest().upper().startswith(
        muju.MUJU_PROVIDER_URL_SHA1
    )
    assert hashlib.sha256(muju.MUJU_URL.encode()).hexdigest().upper().startswith(
        muju.MUJU_CANONICAL_URL_SHA256
    )
    assert hashlib.sha1(
        muju.MUJU_AGRICULTURE_CANDIDATE_URL.encode()
    ).hexdigest().upper().startswith(muju.MUJU_AGRICULTURE_URL_SHA1)
    assert hashlib.sha256(
        muju.MUJU_AGRICULTURE_CANDIDATE_URL.encode()
    ).hexdigest().upper().startswith(muju.MUJU_AGRICULTURE_URL_SHA256)
    assert muju.MUJU_CANDIDATE_DECISIONS == {
        "MUNI_IR_8EDCCB379970": (
            "keep_incumbent_same_canonical_url_and_upgrade_complete_owner"
        ),
        "MUNI_IR_18D70C921457": (
            "exclude_agriculture_section_landing_not_course_identity_ledger"
        ),
    }
    assert "https://library.muju.go.kr/main/edusat/list.do?sh_ct_idx=4" in (
        muju.MUJU_OWNER_BOUNDARIES
    )
    assert muju.MUJU_OWNER_BOUNDARIES[muju.MUJU_REGULAR_PLAN_URL].startswith(
        "same_owner"
    )


def test_closed_application_status_variants_are_explicit() -> None:
    assert muju._STATUS_CONTRACT["신청마감"] == (
        "CLOSED",
        "btn_end",
        "신청마감",
    )
    assert muju._STATUS_CONTRACT["기간종료"] == (
        "CLOSED",
        "btn_close",
        "기간종료",
    )


def test_complete_three_ledger_snapshot_controls_branches_duplicates_and_privacy() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == muju.MUJU_PARSER
    assert len(rows) == 6
    assert meta["ledger_totals"] == {
        "edusat": 6,
        "regular_plan": 3,
        "all_learning": 6,
    }
    assert meta["ledger_pages"] == {
        "edusat": 2,
        "regular_plan": 1,
        "all_learning": 1,
    }
    assert meta["ledger_years"] == {"regular_plan": 2026, "all_learning": 2026}
    assert meta["source_total"] == 15
    assert meta["source_unique_total"] == 14
    assert meta["regular_plan_mirror_count"] == 1
    assert meta["regular_plan_date_drift_count"] == 0
    assert set(meta["regular_plan_mirror_bindings"].values()) == {"198"}
    assert meta["source_date_correction_ids"] == ["202"]
    assert meta["source_status_counts"] == {
        "기간종료": 4,
        "수강신청": 1,
        "신청준비": 1,
    }
    assert meta["category_counts"] == {
        "기초문해": 1,
        "학력보완": 0,
        "직업능력": 3,
        "문화예술": 0,
        "인문교양": 0,
        "시민참여": 0,
        "원데이클래스": 0,
        "지역으뜸인재육성사업": 0,
        "평생교육활성화": 2,
    }
    assert meta["all_learning_district_counts"] == {
        district: 1 for district in muju.MUJU_ALL_LEARNING_DISTRICTS
    }
    assert meta["ledger_current_counts"] == {
        "edusat": 3,
        "regular_plan": 1,
        "all_learning": 2,
    }
    assert meta["current_count"] == 6
    assert meta["expired_count"] == 8
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["application_control_count"] == 1
    assert meta["list_requests"] == 5
    assert meta["static_requests"] == 4
    assert meta["logical_requests"] == meta["physical_requests"] == 12
    assert meta["sentinel_page"] == 3
    assert meta["sentinel_count"] == 1
    assert all(meta["stable_rechecks"].values())
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["pii_payload_persisted"] is False
    assert meta["configured_collection_error"] == ""
    assert site.application_fetches == 0
    assert all(session.closed for session in site.sessions)

    by_identity = {
        row["raw_fields"]["source_identity"]: row
        for row in rows
        if row["raw_fields"]["source_kind"] == "edusat"
    }
    assert by_identity["194"]["status"] == "OPEN"
    assert by_identity["194"]["reservation_available"] is True
    assert by_identity["194"]["application_type"] == "ONLINE_APPLICATION"
    assert parse_qs(urlparse(by_identity["194"]["application_url"]).query)[
        "edu_idx"
    ] == ["194"]
    assert by_identity["202"]["end_date"] == "2027-06-30"
    assert by_identity["202"]["status"] == "SCHEDULED"
    assert by_identity["202"]["raw_fields"]["event_end_corrected"] is True
    assert by_identity["198"]["status"] == "CLOSED"
    assert by_identity["198"]["reservation_available"] is False

    assert not any(row["title"] == "미술의 기초, 스케치 한걸음" and row["raw_fields"]["source_kind"] == "regular_plan" for row in rows)
    assert any(row["title"] == "예비중학반" for row in rows)
    assert {row["branch"] for row in rows if row["raw_fields"]["source_kind"] == "all_learning"} == {
        "무주읍 경로당",
        "무풍면 고도마을회관",
    }
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["municipality_code"] == "5273000000" for row in rows)
    assert all(
        row["raw_fields"]["application_form_fetched"] is False for row in rows
    )

    serialized = repr(rows)
    assert "010-" not in serialized
    assert "063-" not in serialized
    assert "teacher@example.org" not in serialized
    assert "민감강사" not in serialized
    assert "개인 자유서술" not in serialized
    assert "capacity_current" not in serialized
    assert "대기:20명" not in serialized


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"max_pages": 1}, "max_pages cap"),
        ({"detail_limit": 2}, "detail_limit cap"),
    ],
)
def test_required_caps_fail_closed(kwargs: dict[str, int], error: str) -> None:
    rows, _, meta = _collect(FakeSite(), **kwargs)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert error in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


@pytest.mark.parametrize(
    ("site", "error"),
    [
        (FakeSite(bad_sentinel=True), "immediate post-last clamp"),
        (FakeSite(mutate_first_recheck=True), "first page changed on recheck"),
        (FakeSite(mutate_static_recheck=True), "static page changed on recheck"),
        (FakeSite(unknown_status=True), "unknown source status"),
        (FakeSite(bad_category=True), "unknown source category"),
        (FakeSite(duplicate_identity=True), "duplicate source identities"),
        (FakeSite(detail_title_mismatch=True), "list/detail title mismatch"),
        (FakeSite(detail_period_mismatch=True), "list/detail operation period mismatch"),
        (FakeSite(wrong_control_id=True), "malformed application route"),
    ],
)
def test_structural_identity_detail_and_stability_drift_fail_closed(
    site: FakeSite, error: str
) -> None:
    rows, _, meta = _collect(site)
    assert rows == []
    assert error in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False
    assert site.application_fetches == 0


def test_transient_detail_request_is_retried_without_application_fetch() -> None:
    site = FakeSite(fail_once_detail=True)
    rows, _, meta = _collect(site)
    assert len(rows) == 6
    assert meta["request_retry_count"] == 1
    assert meta["physical_requests"] == meta["logical_requests"] + 1
    assert site.calls[("detail", "194")] == 2
    assert site.application_fetches == 0


def test_external_dedupe_cannot_drop_audited_identities() -> None:
    rows, _, meta = _collect(FakeSite(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "external dedupe changed complete identity snapshot" in meta[
        "configured_collection_error"
    ]


def test_invalid_parameters_and_wrong_target_do_not_touch_network() -> None:
    site = FakeSite()
    rows, _, meta = muju.collect(
        _target(provider="MUNI_WRONG"),
        session_factory=site.session_factory,
        fetcher=site.fetcher,
    )
    assert rows == []
    assert "outside canonical Muju scope" in meta["configured_collection_error"]
    assert site.sessions == []

    rows, _, meta = muju.collect(
        _target(),
        max_pages=0,
        session_factory=site.session_factory,
        fetcher=site.fetcher,
    )
    assert rows == []
    assert "are invalid" in meta["configured_collection_error"]
    assert site.sessions == []


@pytest.mark.skipif(
    os.getenv("RUN_MUJU_LIVE_TESTS") != "1",
    reason="set RUN_MUJU_LIVE_TESTS=1 for official live validation",
)
def test_live_complete_muju_snapshot() -> None:
    rows, parser, meta = muju.collect(
        _target(),
        timeout=30,
        max_pages=30,
        detail_limit=20,
        max_workers=4,
        today="2026-07-23",
    )
    assert parser == muju.MUJU_PARSER
    assert meta["ledger_totals"] == {
        "edusat": 125,
        "regular_plan": 14,
        "all_learning": 22,
    }
    assert meta["ledger_pages"] == {
        "edusat": 25,
        "regular_plan": 1,
        "all_learning": 1,
    }
    assert meta["ledger_years"] == {"regular_plan": 2026, "all_learning": 2026}
    assert meta["source_total"] == 161
    assert meta["source_unique_total"] == 149
    assert meta["source_status_counts"] == {
        "기간종료": 123,
        "수강신청": 1,
        "신청준비": 1,
    }
    assert meta["category_counts"] == {
        "기초문해": 1,
        "학력보완": 0,
        "직업능력": 29,
        "문화예술": 12,
        "인문교양": 31,
        "시민참여": 5,
        "원데이클래스": 40,
        "지역으뜸인재육성사업": 0,
        "평생교육활성화": 7,
    }
    assert meta["all_learning_district_counts"] == {
        "무주읍": 4,
        "무풍면": 3,
        "설천면": 4,
        "적상면": 3,
        "안성면": 5,
        "부남면": 3,
    }
    assert meta["regular_plan_mirror_count"] == 12
    assert meta["regular_plan_date_drift_count"] == 2
    assert meta["source_date_correction_ids"] == ["93", "114", "146", "202"]
    assert meta["ledger_current_counts"] == {
        "edusat": 8,
        "regular_plan": 1,
        "all_learning": 9,
    }
    assert meta["current_count"] == len(rows) == 18
    assert meta["expired_count"] == 131
    assert meta["detail_pages"] == 8
    assert meta["application_control_count"] == 1
    assert meta["list_requests"] == 28
    assert meta["static_requests"] == 4
    assert meta["logical_requests"] == 40
    assert meta["sentinel_mode"] == "exact_post_last_repeated_final_identity_page"
    assert meta["sentinel_page"] == 26
    assert meta["sentinel_count"] == 5
    assert all(meta["stable_rechecks"].values())
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["pii_payload_persisted"] is False
    assert sum(row["reservation_available"] for row in rows) == 1
    assert {row["municipality_code"] for row in rows} == {"5273000000"}
    serialized = repr(rows)
    assert "010-" not in serialized
    assert "063-" not in serialized
    assert "@" not in serialized
