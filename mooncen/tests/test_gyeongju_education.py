from __future__ import annotations

from collections import Counter
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_gyeongju as gyeongju
from Crawler import Crawler_GeneratedYamlTargets as generated


_INTEGRATED_ROWS = (
    ("L0000001", "통합 강좌 01", "청소년수련관", "어학", "예약준비중"),
    ("L0000002", "통합 강좌 02", "안강청소년문화의집", "예능", "예약하기"),
    ("L0000003", "통합 강좌 03", "외동읍민체육회관", "취미", "대기자접수"),
    ("L0000004", "통합 강좌 04", "외동생활체육공원", "취업교육", "온라인완료"),
    ("L0000005", "통합 강좌 05", "북천체육시설", "컴퓨터", "교육중"),
    ("L0000006", "통합 강좌 06", "경주화랑마을 방탈출", "기타", "교육전"),
    ("L0000007", "통합 강좌 07", "경주시여성행복드림센터", "스포츠", "예약준비중"),
    ("L0000008", "통합 강좌 08", "청소년수련관", "취미", "예약하기"),
    ("L0000009", "통합 강좌 09", "북천체육시설", "기타", "교육중"),
    ("L0000010", "기늠정검 확인용", "경주화랑마을 방탈출", "스포츠", "교육중"),
    ("L0000011", "통합 강좌 11", "경주시여성행복드림센터", "스포츠", "교육중"),
)

_CATEGORY_BY_NAME = {name: code for code, name in gyeongju.GYEONGJU_CATEGORY_FILTERS.items()}
_MEMBER_BY_BRANCH = {branch.source_name: branch.member_id for branch in gyeongju.GYEONGJU_BRANCHES}


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.history: list[object] = []


class _Session:
    def close(self) -> None:
        return None


def _target() -> dict[str, str]:
    return {
        "provider": gyeongju.GYEONGJU_PROVIDER,
        "url": gyeongju.GYEONGJU_INTEGRATED_URL,
    }


def _integrated_period(status: str) -> str:
    if status == "교육전":
        return "2026.08.01 ~ 2026.12.31"
    return "2026.07.01 ~ 2026.12.31"


def _integrated_apply_period(status: str) -> str:
    if status == "예약준비중":
        return "2026.07.23 09:00 ~ 2026.08.31 18:00"
    if status in {"예약하기", "대기자접수"}:
        return "2026.07.01 09:00 ~ 2026.07.31 18:00"
    return "2026.06.01 09:00 ~ 2026.06.30 18:00"


def _category_navigation(selected: str = "") -> str:
    items = ['<li><a class="on" id="" href="#">전체</a></li>']
    for code, label in gyeongju.GYEONGJU_CATEGORY_FILTERS.items():
        css = ' class="on"' if code == selected else ""
        items.append(f'<li><a id="{code}"{css} href="#">{label}</a></li>')
    if selected:
        items[0] = '<li><a id="" href="#">전체</a></li>'
    return '<ul class="choice_tab">' + "".join(items) + "</ul>"


def _branch_navigation() -> str:
    branches = [
        *(
            branch
            for branch in gyeongju.GYEONGJU_BRANCHES
            if branch.member_id != "B0000027"
        ),
        gyeongju.GYEONGJU_EMPTY_ACTIVE_BRANCH,
    ]
    anchors = "".join(
        '<li class="snb_depth">'
        f'<a href="{gyeongju.GYEONGJU_INTEGRATED_PATH}?mem_id={branch.member_id}">'
        f"{branch.navigation_name}</a></li>"
        for branch in branches
    )
    return f'<div class="snb_list"><ul>{anchors}</ul></div>'


def _integrated_course_row(record: tuple[str, str, str, str, str]) -> str:
    identity, title, branch, _category, status = record
    detail = (
        f"?prc=detail&amp;lec_id={identity}&amp;mem_id=&amp;pg="
    )
    control = ""
    if status in {"예약준비중", "예약하기", "대기자접수"}:
        control = (
            f'<a href="?prc=rsvinfo&amp;lec_id={identity}&amp;mem_id=&amp;pg=">'
            f"{status}</a>"
        )
    return f"""
    <tr>
      <td class="lecture01"><a href="{detail}">{title}</a></td>
      <td>{branch}</td>
      <td>경주시민</td>
      <td>{_integrated_period(status)}</td>
      <td>{control or status}</td>
    </tr>
    """


def _integrated_list_html(page: int, *, drift: bool = False) -> str:
    if page == 1:
        records = list(_INTEGRATED_ROWS[:10])
    elif page == 2:
        records = list(_INTEGRATED_ROWS[10:])
    else:
        records = []
    if drift and records:
        first = records[0]
        records[0] = (first[0], first[1] + " 변경", *first[2:])
    rows = "".join(_integrated_course_row(record) for record in records)
    if not rows:
        rows = '<tr><td colspan="5">강좌 정보가 없습니다.</td></tr>'
    active = f'<a class="on">{page}</a>' if records else ""
    headers = "".join(f"<th>{label}</th>" for label in gyeongju._INTEGRATED_HEADERS)
    return f"""
    <html><body>
      <div id="page_title"><h3>강좌</h3></div>
      {_category_navigation()}
      {_branch_navigation()}
      <span class="prdc_num">시설 <strong>{len(_INTEGRATED_ROWS)}</strong></span>
      <table class="table_list">
        <thead><tr>{headers}</tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="pgnate">{active}</div>
    </body></html>
    """


def _integrated_filter_html(*, category: str = "", member: str = "", delta: int = 0) -> str:
    if category:
        label = gyeongju.GYEONGJU_CATEGORY_FILTERS[category]
        total = sum(record[3] == label for record in _INTEGRATED_ROWS) + delta
        selector = _category_navigation(category)
        hidden = f'<form id="frmFormSearch"><input name="selItemKind" value="{category}"></form>'
    else:
        total = sum(_MEMBER_BY_BRANCH[record[2]] == member for record in _INTEGRATED_ROWS)
        selector = ""
        hidden = ""
    return (
        "<html><body>"
        f'<span class="prdc_num">시설 <strong>{total}</strong></span>'
        f"{selector}{hidden}"
        "</body></html>"
    )


def _integrated_detail_html(
    record: tuple[str, str, str, str, str],
    *,
    wrong_title: bool = False,
) -> str:
    _identity, title, branch, category, status = record
    if wrong_title:
        title += " 불일치"
    notice = {
        "예약준비중": "예약 준비중",
        "예약하기": "",
        "대기자접수": "",
        "온라인완료": "온라인 예약정원이 모두예약",
        "교육중": "현재 교육중",
        "교육전": "현재 교육전",
    }[status]
    values = (
        ("강좌명", title),
        ("교육구분", category),
        ("교육대상", "경주시민"),
        ("정원", "20명 (잔여 5명)"),
        ("예약방법", "인터넷 예약"),
        ("접수일자", _integrated_apply_period(status)),
        ("교육기간", _integrated_period(status)),
        ("교육시간", "매주 화 10:00~12:00"),
        ("수강료", "무료"),
        ("강사", "홍길동"),
        ("문의전화", "054-700-1234"),
        ("담당자", "김담당"),
        ("교육장소", f"{branch} 배움실"),
        ("붙임문서 dt&gt;", "비공개 처리할 첨부"),
    )
    pairs = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in values)
    notice_html = f'<p class="lecture_notice">{notice}</p>' if notice else ""
    return f'<html><body><dl class="lecture_dl01">{pairs}</dl>{notice_html}</body></html>'


def _lifelong_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for index in range(11):
        current = index < 3
        cancelled = index == 2
        if current:
            period = "2026-07-01 ~ 2026-08-31"
            statuses = ("폐강",) if cancelled else ("접수완료", "교육중")
        else:
            period = "2025-01-01 ~ 2025-02-28"
            statuses = ("접수완료", "교육 종료")
        records.append(
            {
                "sequence": 11 - index,
                "identity": f"202600{index + 1:02d}",
                "title": (
                    "테스트강좌 신청 금지"
                    if index == 1
                    else f"평생학습 강좌 {index + 1:02d}"
                ),
                "period": period,
                "statuses": statuses,
            }
        )
    return records


_LIFELONG_RECORDS = _lifelong_records()


def _lifelong_row(record: dict[str, object]) -> str:
    statuses = "".join(
        (
            '<span class="attend application" '
            f'onclick="request(\'{record["identity"]}\', \'A0504\',\'A2000\','
            '\'N\',\'N\',\'Y\',\'100\',\'N\',\'2026-08-03\')">신청하기</span>'
            if item == "신청하기"
            else f'<span class="attend">{item}</span>'
        )
        for item in record["statuses"]
    )
    capacity = ""
    if record.get("capacity_total") is not None:
        capacity = (
            '<li><span class="tit">신청 / 모집</span>'
            f'<span class="cont">{record["capacity_current"]}명 / '
            f'{record["capacity_total"]}명</span></li>'
        )
    first_phase = (
        '<br><span class="cate">1차 접수</span>'
        f'{record["first_apply_period"]}'
        if record.get("first_apply_period")
        else ""
    )
    second_phase = (
        '<br><span class="cate">2차 접수</span>'
        f'{record["second_apply_period"]}'
        if record.get("second_apply_period")
        else ""
    )
    third_phase = (
        '<br><span class="cate">3차 접수</span>'
        f'{record["third_apply_period"]}'
        if record.get("third_apply_period")
        else ""
    )
    visit_phase = (
        '<p><span class="cate">방문접수</span>'
        f'{record["visit_apply_period"]}</p>'
        if record.get("visit_apply_period")
        else ""
    )
    priority_phase = (
        '<p><span class="cate">우선접수</span>'
        f'{record["priority_apply_period"]}</p>'
        if record.get("priority_apply_period")
        else ""
    )
    apply_period = record.get(
        "apply_period", "2026-06-01 10:00 ~ 2026-06-30 17:00"
    )
    return f"""
    <tr>
      <td>{record['sequence']}</td>
      <td>
        <a class="tit" onclick="viewLecture('{record['identity']}')">{record['title']}</a>
        <ul class="info_util">
          <li><span class="tit">교육기관</span><span class="cont">{gyeongju.GYEONGJU_LIFELONG_BRANCH}</span></li>
          <li><span class="tit">교육 요일</span><span class="cont">월</span></li>
          <li><span class="tit">교육 시간</span><span class="cont">10:00~12:00</span></li>
          <li><span class="tit">수강료</span><span class="cont">무료</span></li>
          {capacity}
          <li><span class="tit">접수방법</span><span class="cont">인터넷 접수(선착순)</span></li>
        </ul>
      </td>
      <td>
        <p><span class="cate">신청기간</span>{apply_period}{first_phase}{second_phase}{third_phase}</p>
        {priority_phase}
        {visit_phase}
        <p><span class="cate">교육기간</span>{record['period']}</p>
      </td>
      <td>{statuses}</td>
    </tr>
    """


def _lifelong_list_html(page: int) -> str:
    if page == 1:
        records = _LIFELONG_RECORDS[:10]
    elif page == 2:
        records = _LIFELONG_RECORDS[10:]
    else:
        records = []
    rows = "".join(_lifelong_row(record) for record in records)
    headers = "".join(f"<th>{label}</th>" for label in gyeongju._LIFELONG_HEADERS)
    active = f'<a class="active">{page}</a>' if records else ""
    return f"""
    <html><body>
      <h3 id="page_tit_id">평생학습 강좌</h3>
      <form id="lectureManagement">
        <input name="menu_idx" value="126">
        <input name="rowCount" value="10">
        <input name="program_type" value="A2000">
        <input name="viewPage" value="{page}">
      </form>
      <table class="apply_list_tbl">
        <thead><tr>{headers}</tr></thead><tbody>{rows}</tbody>
      </table>
      <div id="cms_paging">{active}</div>
      <script>var totalPageCount = '2';</script>
    </body></html>
    """


def _lifelong_detail_html(record: dict[str, object]) -> str:
    detail_statuses = record.get("detail_statuses", record["statuses"])
    statuses = "".join(f'<span class="attend">{item}</span>' for item in detail_statuses)
    if record["statuses"] in {("접수전", "교육전"), ("접수전",)}:
        control = ""
    elif record["statuses"] == ("2차신청 준비중", "접수완료"):
        control = (
            '<a class="apply_btn" href="javascript:void(0);">'
            "2차신청 준비중</a>"
        )
    elif record["statuses"] == ("신청하기", "접수완료"):
        control = (
            '<a class="apply_btn" href="javascript:void(0);" '
            f'onclick="request(\'{record["identity"]}\', \'A0504\',\'A2000\','
            '\'N\',\'N\',\'Y\',\'100\',\'N\',\'2026-08-03\')">신청하기</a>'
        )
    else:
        label = "폐강" if record["statuses"] == ("폐강",) else "수강신청 마감"
        control = f'<a class="apply_btn" href="javascript:void(0);">{label}</a>'
    apply_period = record.get(
        "detail_apply_period",
        "2026-06-01 10:00 ~ 2026-06-30 17:00 "
        "접수는 2026-06-01 10:00 부터 가능합니다",
    )
    values = [
        ("교육기관", gyeongju.GYEONGJU_LIFELONG_BRANCH),
        ("신청 기간 (인터넷접수)", apply_period),
    ]
    if record.get("detail_priority_period"):
        values.append(
            (
                gyeongju._LIFELONG_DETAIL_OPTIONAL_PRIORITY_LABEL,
                record["detail_priority_period"],
            )
        )
    if record.get("capacity_total") is not None:
        values.extend(
            (
                (
                    "모집인원",
                    f'모집 : {record["capacity_total"]}명 '
                    f'(인터넷 {record["capacity_total"]}명) / 후보자 : 8명',
                ),
                (
                    "신청현황",
                    f'신청 : {record["capacity_current"]}명 '
                    f'(인터넷 {record["capacity_current"]}명) / 후보자 : 0명',
                ),
            )
        )
    values.extend(
        (
        ("신청방법", "인터넷 접수(선착순)"),
        ("강좌분류", "인문교양"),
        ("교육 기간", f"{record['period']} (총 16시간)"),
        ("교육 요일", "월"),
        ("교육 시간", "10:00~12:00"),
        ("수강료", "무료"),
        ("재료비", "없음"),
        ("교육대상", "성인"),
        ("성별제한", "무관"),
        ("교육장소", f"{gyeongju.GYEONGJU_LIFELONG_BRANCH} 배움실"),
        ("강사", "강사미정"),
        ("담당팀", "평생학습팀"),
        ("문의전화", "054-700-5678"),
        ("강의목표", "공개 자유문이나 저장하지 않음"),
        ("강좌개요", "공개 자유문이나 저장하지 않음"),
        ("강의교재", "공개 자유문이나 저장하지 않음"),
        ("강좌안내", "공개 자유문이나 저장하지 않음"),
        ("첨부파일", "개인정보 가능 첨부.hwpx"),
        )
    )
    items = "".join(
        f'<li><span class="tit">{label}</span><span class="cont">{value}</span></li>'
        for label, value in values
    )
    return f"""
    <html><body>
      <form id="lectureOne">
        <input name="lect_no" value="{record['identity']}">
        <input name="program_type" value="A2000">
      </form>
      <div id="apply_bbs">
        <div class="view_tit_box"><p class="tit">{record['title']}</p>{statuses}</div>
        <div class="view_util_box"><ul class="info_util">{items}</ul></div>
        <div class="top_area">{control}</div>
        <button class="apply_btn">지도보기</button>
      </div>
    </body></html>
    """


def _adjunct_html(*, title: str, menu_idx: str, special: bool, page: int) -> str:
    headers = "".join(f"<th>{label}</th>" for label in gyeongju._LIFELONG_HEADERS)
    program = (
        '<input name="program_type" value="A2005">'
        if special
        else ""
    )
    return f"""
    <html><body>
      <h3 id="page_tit_id">{title}</h3>
      <form id="lectureManagement">
        <input name="menu_idx" value="{menu_idx}">
        <input name="viewPage" value="{page}">
        {program}
      </form>
      <table class="apply_list_tbl">
        <thead><tr>{headers}</tr></thead><tbody></tbody>
      </table>
      <div id="cms_paging"><a class="active">1</a></div>
    </body></html>
    """


class _Site:
    def __init__(
        self,
        *,
        category_delta: int = 0,
        drift: bool = False,
        wrong_detail: str = "",
        failing_detail: str = "",
    ):
        self.category_delta = category_delta
        self.drift = drift
        self.wrong_detail = wrong_detail
        self.failing_detail = failing_detail
        self.urls: list[str] = []
        self._canonical_page_one_calls = 0
        self._lock = Lock()

    def fetch(self, _session: object, url: str, _timeout: int) -> _Response:
        with self._lock:
            self.urls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == gyeongju.GYEONGJU_INTEGRATED_PATH:
            process = query.get("prc", [""])[0]
            if process == "rsvinfo":
                raise AssertionError("application endpoint must never be fetched")
            if process == "detail":
                identity = query["lec_id"][0]
                if identity == self.failing_detail:
                    return _Response(url, "<html>temporary failure</html>", 500)
                record = next(item for item in _INTEGRATED_ROWS if item[0] == identity)
                return _Response(
                    url,
                    _integrated_detail_html(record, wrong_title=identity == self.wrong_detail),
                )
            category = query.get("selItemKind", [""])[0]
            member = query.get("mem_id", [""])[0]
            if category:
                delta = self.category_delta if category == "SPT" else 0
                return _Response(url, _integrated_filter_html(category=category, delta=delta))
            if member:
                return _Response(url, _integrated_filter_html(member=member))
            page = int(query.get("pg", ["1"])[0] or "1")
            drift = False
            if page == 1:
                with self._lock:
                    self._canonical_page_one_calls += 1
                    drift = self.drift and self._canonical_page_one_calls > 1
            return _Response(url, _integrated_list_html(page, drift=drift))
        if parsed.path == gyeongju.GYEONGJU_LIFELONG_LIST_PATH:
            page = int(query.get("viewPage", ["1"])[0] or "1")
            return _Response(url, _lifelong_list_html(page))
        if parsed.path == gyeongju.GYEONGJU_LIFELONG_DETAIL_PATH:
            identity = query["lect_no"][0]
            record = next(item for item in _LIFELONG_RECORDS if item["identity"] == identity)
            return _Response(url, _lifelong_detail_html(record))
        if parsed.path == gyeongju.GYEONGJU_SPECIAL_PATH:
            page = int(query.get("viewPage", ["1"])[0] or "1")
            return _Response(
                url,
                _adjunct_html(
                    title="특성화 프로그램(사업)",
                    menu_idx="203",
                    special=True,
                    page=page,
                ),
            )
        if parsed.path == gyeongju.GYEONGJU_INSTITUTION_PATH:
            page = int(query.get("viewPage", ["1"])[0] or "1")
            return _Response(
                url,
                _adjunct_html(
                    title="관내 평생교육기관 강좌",
                    menu_idx="125",
                    special=False,
                    page=page,
                ),
            )
        raise AssertionError(f"unexpected request: {url}")


def _collect(site: _Site, **kwargs):
    return gyeongju.collect_gyeongju_education(
        _target(),
        today="2026-07-23",
        max_pages=10,
        detail_limit=30,
        max_workers=3,
        session_factory=_Session,
        fetcher=site.fetch,
        **kwargs,
    )


def test_target_is_exact_and_owner_decisions_are_stable() -> None:
    assert gyeongju.is_gyeongju_education_target(_target()) is True
    invalid = (
        {"provider": "MUNI_OTHER", "url": gyeongju.GYEONGJU_INTEGRATED_URL},
        {"provider": gyeongju.GYEONGJU_PROVIDER, "url": gyeongju.GYEONGJU_LIFELONG_URL},
        {"provider": gyeongju.GYEONGJU_PROVIDER, "url": gyeongju.GYEONGJU_DISCOVERY_DETAIL_URL},
        {"provider": gyeongju.GYEONGJU_PROVIDER, "url": gyeongju.GYEONGJU_INTEGRATED_URL + "?pg=1"},
        {"provider": gyeongju.GYEONGJU_PROVIDER, "url": gyeongju.GYEONGJU_INTEGRATED_URL + "#x"},
        {"provider": gyeongju.GYEONGJU_PROVIDER, "url": gyeongju.GYEONGJU_INTEGRATED_URL.replace("https", "http")},
        {"provider": gyeongju.GYEONGJU_PROVIDER, "url": "https://user@www.gyeongju.go.kr/reserve/lecture/list.jsp"},
        {"provider": gyeongju.GYEONGJU_PROVIDER, "url": "https://www.gyeongju.go.kr:443/reserve/lecture/list.jsp"},
    )
    assert all(not gyeongju.is_gyeongju_education_target(target) for target in invalid)
    assert gyeongju.GYEONGJU_PROVIDER == "MUNI_WWW_GYEONGJU_GO_KR_ADA8A467"
    assert gyeongju.GYEONGJU_CANONICAL_CANDIDATE_ID == "MUNI_IR_4E1F48CB4B18"
    assert gyeongju.GYEONGJU_DISCOVERY_CANDIDATE_ID == "MUNI_IR_2FE200C041D5"
    assert gyeongju.GYEONGJU_CANDIDATE_DECISIONS[
        gyeongju.GYEONGJU_CANONICAL_CANDIDATE_ID
    ].startswith("retarget_incumbent")
    assert "exclude_single_expired" in gyeongju.GYEONGJU_CANDIDATE_DECISIONS[gyeongju.GYEONGJU_DISCOVERY_CANDIDATE_ID]
    assert gyeongju.gyeongju_integrated_list_url(2).endswith("mem_id=&pg=2")
    assert "prc=detail" in gyeongju.gyeongju_integrated_detail_url("L0000001")
    assert "lect_no=20260001" in gyeongju.gyeongju_lifelong_detail_url("20260001")
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        gyeongju.GYEONGJU_PROVIDER
    ] == (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "80",
        "--detail-limit",
        "500",
    )


def test_semantic_duplicate_policy_preserves_distinct_ids_within_one_ledger() -> None:
    base = {
        "title": "동일 모집",
        "start_date": "2099-08-01",
        "end_date": "2099-08-31",
        "branch": "외동읍민체육회관",
    }
    rows = [
        {
            **base,
            "provider_course_id": "reserve:L1",
            "raw_fields": {"ledger": "integrated_reservation_lecture"},
        },
        {
            **base,
            "provider_course_id": "reserve:L2",
            "raw_fields": {"ledger": "integrated_reservation_lecture"},
        },
    ]
    assert gyeongju._semantic_duplicate_count(rows) == 0
    assert gyeongju._same_ledger_semantic_duplicate_count(rows) == 1

    rows.append(
        {
            **base,
            "provider_course_id": "lifelong:1",
            "raw_fields": {"ledger": "lifelong_regular_A2000"},
        }
    )
    assert gyeongju._semantic_duplicate_count(rows) == 1


def test_complete_two_ledger_snapshot_is_fail_closed_and_private_safe() -> None:
    site = _Site()
    rows, parser, meta = _collect(site)

    assert parser == gyeongju.GYEONGJU_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert meta["pagination_complete"] is meta["details_complete"] is True
    assert meta["source_totals"] == {
        "integrated": 11,
        "lifelong": 11,
        "special_program_application": 0,
        "external_institution_directory": 0,
    }
    assert meta["source_total"] == meta["source_rows"] == 22
    assert meta["current_counts"] == {"integrated": 11, "lifelong": 3}
    assert meta["current_source_count"] == 14
    assert meta["expired_source_count"] == 8
    assert meta["returned_count"] == meta["output_rows"] == len(rows) == 11
    assert meta["pages"] == meta["data_pages"] == 4
    assert meta["empty_boundary_pages"] == {
        "integrated": 3,
        "lifelong": 3,
        "special_program_application": 2,
        "external_institution_directory": 2,
    }
    assert meta["page_counts"] == {
        "integrated": {1: 10, 2: 1},
        "lifelong": {1: 10, 2: 1},
    }
    assert meta["list_requests"] == 33
    assert meta["detail_attempts"] == meta["detail_pages"] == 13
    assert meta["logical_requests"] == meta["physical_requests"] == len(site.urls) == 46
    assert meta["request_retry_count"] == 0

    assert meta["category_counts"] == {
        "어학": 1,
        "예능": 1,
        "취미": 2,
        "취업교육": 1,
        "컴퓨터": 1,
        "기타": 2,
        "스포츠": 3,
    }
    assert meta["source_branch_counts"] == {
        "청소년수련관": 2,
        "안강청소년문화의집": 1,
        "외동읍민체육회관": 1,
        "외동생활체육공원": 1,
        "북천체육시설": 2,
        "경주화랑마을 방탈출": 2,
        "경주시여성행복드림센터": 2,
    }
    assert meta["branch_partition_counts"] == {
        "B0000006": 2,
        "B0000025": 1,
        "B0000011": 1,
        "B0000027": 1,
        "B0000031": 2,
        "B0000034": 2,
        "B0000037": 2,
        "B0000032": 0,
    }
    assert meta["excluded_current_counts"] == {
        "functional_test_record": 2,
        "cancelled_course": 1,
    }
    assert meta["returned_by_ledger"] == {
        "integrated_reservation_lecture": 10,
        "lifelong_regular_A2000": 1,
    }
    assert meta["status_counts"] == {
        "SCHEDULED": 2,
        "OPEN": 2,
        "WAITING": 1,
        "CLOSED": 6,
    }
    assert meta["application_control_count"] == 5
    assert meta["actionable_application_count"] == 3
    assert meta["application_endpoint_requests"] == 0
    assert meta["applicant_list_requests"] == 0
    assert meta["attachment_requests"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["required_field_counts"] == {
        "target": 11,
        "fee": 11,
        "period": 11,
        "venue_name": 11,
        "category": 11,
        "schedule_raw": 11,
    }

    assert Counter(row["branch"] for row in rows)["경주화랑마을 방탈출"] == 1
    north = next(row for row in rows if row["branch"] == "북천체육시설")
    hall = next(row for row in rows if row["branch"] == "외동읍민체육회관")
    lifelong = next(row for row in rows if row["branch"] == gyeongju.GYEONGJU_LIFELONG_BRANCH)
    assert north["address"] == north["venue_address"] == "경상북도 경주시 구황동 883-99"
    assert hall["address"] == "경상북도 경주시 외동읍 신기앞길 67-62"
    assert lifelong["address"] == gyeongju.GYEONGJU_LIFELONG_ADDRESS
    assert len({row["provider_course_id"] for row in rows}) == len(rows)
    assert all(row["raw_fields"]["detail_verified"] is True for row in rows)
    assert all(
        row[field]
        for row in rows
        for field in ("target", "fee", "period", "venue_name", "category", "schedule_raw")
    )
    serialized = repr(rows)
    assert "홍길동" not in serialized
    assert "김담당" not in serialized
    assert "054-700-" not in serialized
    assert "개인정보 가능 첨부" not in serialized
    assert all("prc=rsvinfo" not in url for url in site.urls)
    assert all("lect_no=20260002" not in url for url in site.urls)
    assert all("download" not in url and "attachment" not in url for url in site.urls)


def test_modern_lifelong_capacity_phases_and_status_controls() -> None:
    assert gyeongju._LIFELONG_STATUS_MAP[("2차신청 준비중",)] == "SCHEDULED"
    assert gyeongju._LIFELONG_STATUS_MAP[("접수전",)] == "SCHEDULED"
    assert gyeongju._TEST_TITLE_RE.search(
        "(26eeeeeee103) x테스트 신청 x"
    )

    scheduled_record: dict[str, object] = {
        "sequence": 2,
        "identity": "20269991",
        "title": "(26e101) 신규 자격과정 모집인원 우선모집",
        "period": "2026-08-20 ~ 2026-12-03",
        "statuses": ("접수전",),
        "detail_statuses": ("접수전", "교육전"),
        "capacity_current": 0,
        "capacity_total": 16,
        "apply_period": "2026-08-03 10:00 ~ 2026-08-24 17:00",
        "priority_apply_period": "2026-08-03 ~ 2026-08-03 (0명 / 3명)",
        "detail_apply_period": (
            "2026-08-03 10:00 ~ 2026-08-03 17:00 "
            "접수는 2026-08-03 10:00 부터 가능합니다"
        ),
        "detail_priority_period": (
            "2026-08-03 ~ 2026-08-03 (모집 : 인터넷 3명) "
            "접수는 2026-08-03 부터 가능합니다."
        ),
    }
    scheduled_list_soup = BeautifulSoup(
        f"<table><tbody>{_lifelong_row(scheduled_record)}</tbody></table>",
        "lxml",
    )
    scheduled_listed = gyeongju._lifelong_row(
        scheduled_list_soup.select_one("tr"),
        1,
    )
    scheduled, exclusion = gyeongju._lifelong_detail_row(
        scheduled_listed,
        BeautifulSoup(_lifelong_detail_html(scheduled_record), "lxml"),
    )

    assert exclusion == ""
    assert scheduled["status"] == "SCHEDULED"
    assert scheduled["capacity_current"] == 0
    assert scheduled["capacity_total"] == scheduled["capacity"] == 16
    assert scheduled["reservation_available"] is False
    assert scheduled["application_url"] == ""
    assert scheduled["raw_fields"]["source_priority_apply_period"].startswith(
        "2026-08-03"
    )

    second_record: dict[str, object] = {
        "sequence": 3,
        "identity": "20264741",
        "title": "(26e200) 테스트강좌 x신청x",
        "period": "2026-08-24 ~ 2026-12-07",
        "statuses": ("2차신청 준비중", "접수완료"),
        "capacity_current": 2,
        "capacity_total": 10,
        "apply_period": "2026-07-28 10:00 ~ 2026-08-24 17:00",
        "second_apply_period": "2026-08-05 14:00 ~ 2026-08-05 17:00",
        "detail_apply_period": (
            "2026-08-05 14:00 ~ 2026-08-05 17:00 "
            "접수는 2026-08-05 14:00 부터 가능합니다"
        ),
        "detail_statuses": ("2차신청 준비중",),
    }
    second_list_soup = BeautifulSoup(
        f"<table><tbody>{_lifelong_row(second_record)}</tbody></table>",
        "lxml",
    )
    second_listed = gyeongju._lifelong_row(
        second_list_soup.select_one("tr"),
        1,
    )
    second, exclusion = gyeongju._lifelong_detail_row(
        second_listed,
        BeautifulSoup(_lifelong_detail_html(second_record), "lxml"),
    )

    assert exclusion == ""
    assert second["status"] == "SCHEDULED"
    assert second["reservation_available"] is False
    assert second["raw_fields"]["source_second_apply_period"].startswith(
        "2026-08-05"
    )

    functional_record: dict[str, object] = {
        "sequence": 4,
        "identity": "20264744",
        "title": "(26T300) 테스트강좌_신청X_우선대상",
        "period": "2026-08-24 ~ 2026-12-07",
        "statuses": ("3차신청 준비중",),
        "capacity_current": 6,
        "capacity_total": 13,
        "apply_period": "2026-07-28 10:00 ~ 2026-08-24 17:00",
        "third_apply_period": "2026-08-06 10:00 ~ 2026-08-10 17:00",
        "visit_apply_period": "2026-07-28 10:00 ~ 2026-08-04 17:00",
    }
    functional_soup = BeautifulSoup(
        f"<table><tbody>{_lifelong_row(functional_record)}</tbody></table>",
        "lxml",
    )
    functional = gyeongju._lifelong_row(
        functional_soup.select_one("tr"),
        1,
    )

    assert functional["functional_test_record"] is True
    assert functional["status"] == "SCHEDULED"
    assert functional["third_apply_period"].startswith("2026-08-06")
    assert functional["visit_apply_period"].startswith("2026-07-28")

    functional_open_record: dict[str, object] = {
        "sequence": 5,
        "identity": "20264739",
        "title": "(26eeeeee101) 테스트강좌 신청x 모집인원 우선모집",
        "period": "2026-08-20 ~ 2026-12-03",
        "statuses": ("신청하기",),
        "capacity_current": 0,
        "capacity_total": 2,
        "apply_period": "2026-07-28 10:00 ~ 2026-08-24 17:00",
        "first_apply_period": "2026-07-28 10:00 ~ 2026-08-03 17:00",
        "priority_apply_period": "2026-08-28 ~ 2026-08-03 (0명 / 1명)",
    }
    functional_open_soup = BeautifulSoup(
        f"<table><tbody>{_lifelong_row(functional_open_record)}</tbody></table>",
        "lxml",
    )
    functional_open = gyeongju._lifelong_row(
        functional_open_soup.select_one("tr"),
        1,
    )

    assert functional_open["functional_test_record"] is True
    assert functional_open["status"] == "OPEN"
    assert functional_open["application_url"].endswith(
        "lect_no=20264739&menu_idx=126"
    )

    open_record: dict[str, object] = {
        "sequence": 1,
        "identity": "20269992",
        "title": "(26e102) 신규 신청 강좌",
        "period": "2026-08-20 ~ 2026-12-03",
        "statuses": ("신청하기", "접수완료"),
        "detail_statuses": ("접수중",),
        "capacity_current": 2,
        "capacity_total": 10,
        "apply_period": "2026-07-28 10:00 ~ 2026-08-24 17:00",
        "first_apply_period": "2026-07-28 10:00 ~ 2026-08-03 17:00",
        "detail_apply_period": (
            "2026-07-28 10:00 ~ 2026-08-03 17:00 "
            "접수는 2026-07-28 10:00 부터 가능합니다"
        ),
    }
    open_list_soup = BeautifulSoup(
        f"<table><tbody>{_lifelong_row(open_record)}</tbody></table>",
        "lxml",
    )
    open_listed = gyeongju._lifelong_row(open_list_soup.select_one("tr"), 1)
    opened, exclusion = gyeongju._lifelong_detail_row(
        open_listed,
        BeautifulSoup(_lifelong_detail_html(open_record), "lxml"),
    )

    assert exclusion == ""
    assert opened["status"] == "OPEN"
    assert opened["capacity_current"] == 2
    assert opened["capacity_total"] == 10
    assert opened["reservation_available"] is True
    assert opened["application_type"] == "ONLINE_RESERVATION"
    assert opened["application_url"].endswith("lect_no=20269992&menu_idx=126")
    assert opened["raw_fields"]["source_first_apply_period"].startswith(
        "2026-07-28"
    )


@pytest.mark.parametrize(
    ("site", "message"),
    (
        (_Site(category_delta=1), "category partitions"),
        (_Site(drift=True), "stability recheck"),
        (_Site(wrong_detail="L0000001"), "list/detail 강좌명 mismatch"),
        (_Site(failing_detail="L0000001"), "current detail snapshot incomplete"),
    ),
)
def test_source_partition_drift_and_detail_failures_publish_nothing(
    site: _Site,
    message: str,
) -> None:
    rows, _, meta = _collect(site)

    assert rows == []
    assert meta["returned_count"] == 0
    assert meta["snapshot_complete"] is False
    assert meta["full_snapshot_validated"] is False
    assert message in meta["configured_collection_error"]


def test_limits_and_managed_session_requirement_fail_closed() -> None:
    rows, _, meta = gyeongju.collect_gyeongju_education(_target(), today="2026-07-23")
    assert rows == []
    assert "managed session_factory injection" in meta["configured_collection_error"]

    site = _Site()
    rows, _, meta = gyeongju.collect_gyeongju_education(
        _target(),
        today="2026-07-23",
        max_pages=1,
        detail_limit=30,
        session_factory=_Session,
        fetcher=site.fetch,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages reached" in meta["configured_collection_error"]

    site = _Site()
    rows, _, meta = gyeongju.collect_gyeongju_education(
        _target(),
        today="2026-07-23",
        max_pages=10,
        detail_limit=12,
        session_factory=_Session,
        fetcher=site.fetch,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


@pytest.mark.skipif(
    os.getenv("RUN_GYEONGJU_LIVE") != "1",
    reason="opt-in Gyeongju live crawl",
)
def test_live_gyeongju_cutoff_snapshot() -> None:
    rows, parser, meta = gyeongju.collect_gyeongju_education(
        _target(),
        timeout=30,
        max_pages=80,
        detail_limit=300,
        max_workers=3,
        today="2026-07-23",
        allow_raw_requests_for_tests=True,
    )

    assert parser == gyeongju.GYEONGJU_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["source_totals"] == {
        "integrated": 199,
        "lifelong": 327,
        "special_program_application": 0,
        "external_institution_directory": 0,
    }
    assert meta["current_counts"] == {"integrated": 199, "lifelong": 125}
    assert meta["source_total"] == 526
    assert meta["current_source_count"] == 324
    assert meta["excluded_current_counts"] == {
        "functional_test_record": 3,
        "cancelled_course": 5,
    }
    assert meta["returned_count"] == len(rows) == 316
    assert meta["pages"] == 53
    assert meta["empty_boundary_pages"]["integrated"] == 21
    assert meta["empty_boundary_pages"]["lifelong"] == 34
    assert meta["list_requests"] == 82
    assert meta["detail_pages"] == 322
    assert meta["logical_requests"] == meta["physical_requests"] == 404
    assert meta["status_counts"] == {
        "SCHEDULED": 147,
        "OPEN": 9,
        "CLOSED": 160,
    }
    assert meta["application_control_count"] == 55
    assert meta["actionable_application_count"] == 9
    assert meta["privacy_violations"] == 0
    assert meta["application_endpoint_requests"] == 0
    assert meta["attachment_requests"] == 0
