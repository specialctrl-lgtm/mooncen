from __future__ import annotations

from collections import Counter
import hashlib
import html
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_ulleung as ulleung


AUDIT_DATE = "2026-07-23"


def _family_record(
    identity: str,
    title: str,
    *,
    status: str = "접수중",
    event: str = "2026-07-24 ~ 2026-08-24",
    apply: str = "2026-07-01 09:00 ~ 2026-08-01 18:00",
    rounds: int = 2,
    venue: str = "경북 울릉군 울릉읍 울릉순환로 286 여성센터",
) -> dict[str, Any]:
    return {
        "id": identity,
        "title": title,
        "status": status,
        "event": event,
        "apply": apply,
        "rounds": rounds,
        "venue": venue,
    }


FAMILY_ROWS = (
    _family_record("606", "다문화가족 진로 탐색"),
    _family_record("605", "가족 소통 프로젝트", status="접수마감"),
    _family_record("604", "여름 드로잉 교실", status="접수마감"),
    _family_record("603", "가족 자조모임"),
    _family_record("602", "지역문화를 활용한 한국어 교육"),
    _family_record("601", "가족봉사단"),
)


def _family_query_href(scope: str, page: int) -> str:
    if scope == "active":
        return (
            f"{ulleung.ULLEUNG_FAMILY_LIST_PATH}?rows=5&cpage={page}"
        )
    return ulleung.ulleung_family_list_url(
        ulleung.date.fromisoformat(AUDIT_DATE), page, scope=scope
    )


def _family_form(scope: str, page: int) -> str:
    status_checked = {
        "all_program_status": scope == "current_all",
        "plan": False,
        "ongoing": scope == "active",
        "finish": False,
    }
    status_html = "".join(
        f'<input id="{identity}" name="status" type="radio"'
        f'{" checked" if checked else ""}>'
        for identity, checked in status_checked.items()
    )
    return f"""
    <div class="program_search">
      <form id="searchForm" name="searchForm" method="get"
            action="{ulleung.ULLEUNG_FAMILY_LIST_PATH}"
            onsubmit="return setForm();">
        <input name="rows" value="5">
        <input name="cpage" value="{page}">
        <input id="area" name="area" value="A009">
        <input id="area_detail" name="area_detail" value="D116">
        <input name="cat" value=""><input name="keyword" value="">
        <ul><li><div class="right">가족센터 &gt; 경북 &gt; 울릉군</div></li></ul>
        <input id="family_program" name="application_type" checked>
        <label for="family_program">가족센터프로그램</label>
        <input id="family_consultion" name="application_type">
        <label for="family_consultion">가족상담</label>
        <input id="multicultural_family_service" name="application_type">
        <label for="multicultural_family_service">다문화가족서비스</label>
        <input id="family_hope_dream" name="application_type">
        <label for="family_hope_dream">온가족보듬</label>
        <div class="program_status">{status_html}</div>
        <select id="program_date_select">
          <option value="program_term" selected>행사기간</option>
        </select>
        <select id="reception_date_select">
          <option value="reception_term" selected>접수기간</option>
        </select>
        <input id="program_start_date_term" value="{AUDIT_DATE}">
        <input id="program_end_date_term" value="2099-12-31">
        <input id="reception_start_date_term" value="2000-01-01">
        <input id="reception_end_date_term" value="2099-01-01">
      </form>
    </div>
    """


def _family_card(row: Mapping[str, Any]) -> str:
    identity = str(row["id"])
    title = str(row["title"])
    status = str(row["status"])
    css = {"접수중": "c0", "접수마감": "c2"}[status]
    escaped_title = html.escape(title, quote=True)
    onclick_web = f"send('{identity}','{escaped_title}','web')"
    onclick_center = f"send('{identity}','{escaped_title}','center')"
    return f"""
    <li class="clearfix">
      <div class="txt">
        <p class="tit"><a href="javascript:void(0);"
          onclick="{onclick_web}">{escaped_title}</a></p>
        <ul>
          <li><p><b>회차정보</b> 총 {row['rounds']}회</p></li>
          <li><p><b>행사기간</b> {row['event']}</p></li>
          <li><p><b>접수기간</b> {row['apply']}</p></li>
          <li><p><b>진행장소</b> {row['venue']} 오시는길</p></li>
        </ul>
      </div>
      <div class="util">
        <div class="state"><span class="{css}">{status}</span>
          <a href="javascript:void(0);" onclick="{onclick_center}">신청하기</a>
        </div>
        <div class="loc"><b></b>경북 &gt; 울릉군</div>
      </div>
    </li>
    """


def _family_page(
    scope: str,
    page: int,
    rows: list[Mapping[str, Any]],
    total: int,
    last_page: int,
) -> str:
    links = "".join(
        (
            f'<b><a href="javascript:void(0);">{candidate}</a></b>'
            if candidate == page and page <= last_page
            else f'<a href="{html.escape(_family_query_href(scope, candidate), quote=True)}">{candidate}</a>'
        )
        for candidate in range(1, last_page + 1)
    )
    return f"""
    <html><body>
      {_family_form(scope, page)}
      <div class="list_option apply_type1">
        <p class="hit">전체 : <span>{total}</span> (<b>{page}</b>/{last_page} 페이지)</p>
      </div>
      <div class="program_list apply_type1"><ul>
        {''.join(_family_card(row) for row in rows)}
      </ul></div>
      <div class="paging"><div id="pagingWrap"><div class="num">{links}</div></div></div>
    </body></html>
    """


def _family_detail(row: Mapping[str, Any], *, drift: str = "") -> str:
    identity = str(row["id"])
    title = str(row["title"])
    hidden_title = "다른 제목" if drift == "title" else title
    field_ids = (
        "center_nm",
        "program_date_time",
        "reception_date_time",
        "participation_target",
        "recruit_personal_cnt",
        "waiting_personal_cnt",
        "program_conts",
        "eposidoe_detail",
        "program_place",
    )
    fields = "".join(
        f'<tr><td class="txt"><span id="{identity_name}"></span></td></tr>'
        for identity_name in field_ids
    )
    return f"""
    <div class="sub_contents"><div class="program_view">
      <input id="seq" name="seq" value="{identity}">
      <input name="familynet_pg_no" value="{identity}">
      <input id="area" name="area" value="A009">
      <input id="area_detail" name="area_detail" value="D116">
      <input id="progNm" value="{html.escape(hidden_title, quote=True)}">
      <table class="view_style_1"><tbody>{fields}</tbody></table>
      <div class="btn_type1"><div class="center">
        <a id="applyBtn" style="display:none;"
           href="javascript:applysMethods.modal.openApply();">신청하기</a>
        <a id="applyCompleteBtn" style="display:none;" href="#">신청완료</a>
        <a href="{ulleung.ULLEUNG_FAMILY_LIST_PATH}">목록</a>
      </div></div>
    </div></div>
    <script>
      var a='/recruitReceipt/getView.do';
      var b='/recruitReceipt/loginCheck.do';
      var c='/recruitReceipt/modal/apply.do';
    </script>
    """


class _Session:
    def close(self) -> None:
        return None


class _FamilyFetcher:
    def __init__(
        self,
        *,
        active_missing: bool = False,
        nonempty_sentinel: bool = False,
        mutate_first_recheck: bool = False,
        detail_drift: str = "",
        retry_first: bool = False,
    ) -> None:
        self.urls: list[str] = []
        self.counts: Counter[str] = Counter()
        self.active_missing = active_missing
        self.nonempty_sentinel = nonempty_sentinel
        self.mutate_first_recheck = mutate_first_recheck
        self.detail_drift = detail_drift
        self.retry_first = retry_first

    def __call__(
        self, _session: Any, method: str, url: str, **_kwargs: Any
    ) -> str:
        assert method == "GET"
        self.urls.append(url)
        self.counts[url] += 1
        if self.retry_first and len(self.urls) == 1:
            raise TimeoutError("synthetic retry")
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == ulleung.ULLEUNG_FAMILY_DETAIL_PATH:
            identity = query["seq"][0]
            row = next(item for item in FAMILY_ROWS if item["id"] == identity)
            return _family_detail(row, drift=self.detail_drift)
        assert parsed.path == ulleung.ULLEUNG_FAMILY_LIST_PATH
        scope = "current_all" if "status" in query else "active"
        page = int(query.get("cpage", ["1"])[0])
        selected = list(FAMILY_ROWS)
        if scope == "active":
            selected = [row for row in selected if row["status"] == "접수중"]
            if self.active_missing:
                selected = selected[:-1]
        last_page = max(1, (len(selected) + 4) // 5)
        start = (page - 1) * 5
        page_rows = selected[start : start + 5]
        if self.nonempty_sentinel and scope == "current_all" and page == last_page + 1:
            page_rows = [selected[0]]
        if (
            self.mutate_first_recheck
            and scope == "current_all"
            and page == 1
            and self.counts[url] >= 2
        ):
            page_rows = [dict(page_rows[0], title="변경된 제목"), *page_rows[1:]]
        return _family_page(scope, page, page_rows, len(selected), last_page)


def _life_record(
    identity: str,
    title: str,
    *,
    event: str = "2024-01-10 ~ 2024-01-10",
    receipt: str = "2024-01-01 09시 ~ 2024-01-05 18시 (접수인원 : 1/10)",
    additional: str = "",
    category: str = "문화예술교육",
    branch: str = "문화체육과",
    target: str = "성인",
    schedule: str = "09:00 ~ 10:00 ( 월 )",
    capacity: int = 10,
) -> dict[str, Any]:
    return {
        "id": identity,
        "title": title,
        "event": event,
        "receipt": receipt,
        "additional": additional,
        "category": category,
        "branch": branch,
        "target": target,
        "schedule": schedule,
        "capacity": capacity,
    }


LIFELONG_ROWS = (
    _life_record(
        "3100",
        "청소년 축구교실",
        event="2026-07-18 ~ 2026-08-02",
        receipt="2026-07-14 10시 ~ 2026-07-16 15시 (접수인원 : 0/20)",
        target="중학생",
        capacity=20,
    ),
    _life_record(
        "3099",
        "도자기 공예",
        event="2026-07-02 ~ 2026-07-30",
        receipt="2026-06-22 09시 ~ 2026-06-26 18시 (접수인원 : 10/10)",
    ),
    _life_record(
        "3098",
        "바이올린 기초",
        event="2026-06-08 ~ 2026-08-24",
        receipt="2026-05-27 09시 ~ 2026-06-04 18시 (접수인원 : 5/5)",
        target="누구나",
        capacity=5,
    ),
    _life_record("2500", "지난 강좌 2500"),
    _life_record(
        "2005",
        ulleung.ULLEUNG_LIFELONG_ADDITIONAL_RECEIPT["2005"][0],
        event="2025-01-06 ~ 2025-01-17",
        receipt="2024-12-16 00시 ~ 2024-12-27 23시 (접수인원 : 0/15)",
        additional=ulleung.ULLEUNG_LIFELONG_ADDITIONAL_RECEIPT["2005"][1],
        target="초등학생",
        capacity=15,
    ),
    _life_record("1500", "지난 강좌 1500", category="시민참여교육"),
    _life_record(
        "1012",
        ulleung.ULLEUNG_LIFELONG_EVENT_CORRECTIONS["1012"][0],
        event=ulleung.ULLEUNG_LIFELONG_EVENT_CORRECTIONS["1012"][1],
        receipt="2024-11-06 00시 ~ 2024-11-11 00시 (접수인원 : 6/10)",
        schedule="1400 ~ 1600 ( 월 )",
        branch="관광문화체육실",
    ),
    _life_record("1000", "지난 강좌 1000", category="인문교양교육"),
    _life_record("900", "지난 강좌 900", branch="미래전략추진단"),
    _life_record("100", "지난 강좌 100", branch="농업기술센터"),
    _life_record(
        "28",
        ulleung.ULLEUNG_LIFELONG_RECEIPT_CORRECTIONS["28"][0],
        event="2024-11-13 ~ 2024-11-13",
        receipt=ulleung.ULLEUNG_LIFELONG_RECEIPT_CORRECTIONS["28"][1],
        capacity=12,
    ),
    _life_record(
        "27",
        ulleung.ULLEUNG_LIFELONG_RECEIPT_CORRECTIONS["27"][0],
        event="2024-11-12 ~ 2024-11-12",
        receipt=ulleung.ULLEUNG_LIFELONG_RECEIPT_CORRECTIONS["27"][1],
        capacity=12,
    ),
    _life_record("13", "최종 지난 강좌"),
)


def _life_form(*, checked_filter: bool = False) -> str:
    targets = "".join(
        f'<input id="t{index}" name="srchTrgt" value="{code}"'
        f'{" checked" if checked_filter and index == 0 else ""}>'
        f'<label for="t{index}">{label}</label>'
        for index, (code, label) in enumerate(
            ulleung.ULLEUNG_LIFELONG_TARGETS.items()
        )
    )
    categories = "".join(
        f'<input id="c{index}" name="srchFld" value="{code}">'
        f'<label for="c{index}">{label}</label>'
        for index, (code, label) in enumerate(
            ulleung.ULLEUNG_LIFELONG_CATEGORY_CODES.items()
        )
    )
    days = "".join(
        f'<label for="week_dy{index}"><input id="week_dy{index}" '
        f'name="srchWeek_dy{index}" value="Y">{label}</label>'
        for index, label in enumerate(("월", "화", "수", "목", "금", "토", "일"))
    )
    times = "".join(
        f'<input id="time{index}" name="srchTmzon" value="{code}">'
        f'<label for="time{index}">{label}</label>'
        for index, (code, label) in enumerate(
            (("A", "오전"), ("B", "오후"), ("C", "야간"))
        )
    )
    statuses = "".join(
        f'<input id="status{index}" name="srchStts" value="{code}">'
        f'<label for="status{index}">{label}</label>'
        for index, (code, label) in enumerate(
            (("A", "접수예정"), ("B", "접수중"), ("C", "접수마감"))
        )
    )
    return f"""
      <input id="srchKwd" name="srchKwd" value="">
      <input id="srchStart" name="srchStart" value="">
      <input id="srchEnd" name="srchEnd" value="">
      {targets}{categories}{days}{times}{statuses}
      <input id="d_search_ch" name="d_search_ch" value="">
      <input id="e_search_arr" name="e_search_arr" value="">
      <input id="fee0" name="srchFee_0" value="isFree"><label for="fee0">무료</label>
      <input id="fee1" name="srchFee_1" value="isPay"><label for="fee1">유료</label>
      <input id="f_search_arr" name="f_search_arr" value="">
    """


def _life_item(row: Mapping[str, Any], *, branch_drift: bool = False) -> str:
    identity = row["id"]
    branch = "외부기관" if branch_drift else row["branch"]
    additional = (
        f'<li class="lctre_rcpt"><span>추가접수</span><p>{row["additional"]}</p></li>'
        if row["additional"]
        else ""
    )
    return f"""
    <div class="lecture_item">
      <div class="lecture_top">
        <p class="lctre_fld_nm">[{row['category']}]</p>
        <p class="lctre_ttl">{html.escape(str(row['title']))}</p>
        <p class="site_nm">{branch}</p>
      </div>
      <ul class="lecture_detail">
        <li class="lctre_ymd"><span>교육기간</span><p>{row['event']}</p></li>
        <li class="lctre_hr"><span>교육시간</span><p>{row['schedule']}</p></li>
        <li class="lctre_rcpt"><span>정규접수</span><p>{row['receipt']}</p></li>
        {additional}
        <li class="trgt_nm"><span>교육대상</span><p>{row['target']}</p></li>
        <li class="bckp_count"><span>후보인원</span><p>0/0</p></li>
      </ul>
      <span class="lctre_status st03">접수마감</span>
      <div class="btn_wrap disabled"><a
        href="?cmd=2&amp;mnu_uid=1846&amp;lctre_uid={identity}">접수마감</a></div>
    </div>
    """


def _life_pager(page: int, *, sentinel: bool) -> str:
    if page == 1:
        middle = (
            '<strong title="현재 페이지">1</strong>'
            '<a href="?pageNo=2&amp;mnu_uid=1846&amp;">2</a>'
        )
        arrows = (
            '<a class="arrow first" title="첫 페이지">처음</a>'
            '<a class="arrow prev" title="이전 10페이지">이전</a>'
            '<a class="arrow next" href="?pageNo=2&amp;mnu_uid=1846&amp;">다음</a>'
            '<a class="arrow last" title="끝 페이지" '
            'href="?pageNo=2&amp;mnu_uid=1846&amp;">마지막</a>'
        )
    elif sentinel:
        middle = (
            '<a href="?pageNo=1&amp;mnu_uid=1846&amp;">1</a>'
            '<a href="?pageNo=2&amp;mnu_uid=1846&amp;">2</a>'
        )
        arrows = (
            '<a class="arrow first" href="?pageNo=1&amp;mnu_uid=1846&amp;">처음</a>'
            '<a class="arrow prev" href="?pageNo=1&amp;mnu_uid=1846&amp;">이전</a>'
            '<a class="arrow next">다음</a>'
            '<a class="arrow last" title="끝 페이지">마지막</a>'
        )
    else:
        middle = (
            '<a href="?pageNo=1&amp;mnu_uid=1846&amp;">1</a>'
            '<strong title="현재 페이지">2</strong>'
        )
        arrows = (
            '<a class="arrow first" href="?pageNo=1&amp;mnu_uid=1846&amp;">처음</a>'
            '<a class="arrow prev" href="?pageNo=1&amp;mnu_uid=1846&amp;">이전</a>'
            '<a class="arrow next">다음</a>'
            '<a class="arrow last" title="끝 페이지">마지막</a>'
        )
    return f'<div class="paging">{arrows}{middle}</div>'


def _life_page(
    page: int,
    rows: list[Mapping[str, Any]],
    *,
    sentinel: bool = False,
    checked_filter: bool = False,
    branch_drift: bool = False,
) -> str:
    return f"""
    <div class="wrap_srch_lecture">
      <form id="frm" name="frm" method="post" action="">
        {_life_form(checked_filter=checked_filter)}
        <div class="lecture_list">
          {''.join(_life_item(row, branch_drift=branch_drift) for row in rows)}
        </div>
        {_life_pager(page, sentinel=sentinel)}
      </form>
    </div>
    """


def _normal_detail_receipt(row: Mapping[str, Any]) -> str:
    return str(row["receipt"]).split(" (접수인원", 1)[0]


def _life_detail(row: Mapping[str, Any], *, drift: str = "") -> str:
    title = "다른 제목" if drift == "title" else str(row["title"])
    event = str(row["event"])
    start, end = [part.strip() for part in event.split("~", 1)]
    schedule = str(row["schedule"])
    time_value, days = schedule.split("(", 1)
    days = days.rstrip(") ")
    detail_event = f"{start}~{end} ({time_value.strip().replace(' ', '')})"
    fields = {
        "교육명": title,
        "접수 일시": _normal_detail_receipt(row),
        "교육 일시": detail_event,
        "교육 요일": days.strip(),
        "장소": "울릉한마음회관 대회의실",
        "교육대상": row["target"],
        "1회 교육시간": "1시간",
        "교육횟수": "4",
        "모집인원": (
            f"신청정원 : {row['capacity']}(온라인 : {row['capacity']}/"
            "오프라인 : 0 )/후보정원:0"
        ),
        "수강료": "0",
        "재료": "개인 준비물",
        "재료비": "0",
        "강사명": "홍길동",
        "지역": "",
        "담당자": "평생교육팀",
        "문의전화": "054)790-0000",
        "교육내용": "민감한 자유문 본문",
        "모집방법": "",
        "모집안내": "안내",
        "주의사항": "주의",
        "첨부파일": "개인정보.xlsx",
    }
    definitions = "".join(
        f"<dt>{label}</dt><dd>{html.escape(str(fields[label]))}</dd>"
        for label in ulleung._LIFELONG_DETAIL_FIELDS
    )
    action = (
        '<a class="blueBtn">변경</a>'
        if drift == "control"
        else '<a class="grayBtn deadline big">접수마감</a>'
    )
    return f"""
      <div class="es_detail formStyle"><dl>{definitions}</dl></div>
      <div class="boardBtn">{action}
        <a class="bt1 can" href="?pageNo=&amp;mnu_uid=1846&amp;">목록</a>
      </div>
      <div id="page_info"><ul class="dataOffer">
        <li><span>담당부서</span> : {row['branch']}</li>
        <li><span>담당자</span> : 평생교육팀</li>
        <li><span>전화번호</span> : 054-790-0000</li>
      </ul></div>
    """


class _LifelongFetcher:
    def __init__(
        self,
        *,
        nonempty_sentinel: bool = False,
        mutate_first_recheck: bool = False,
        checked_filter: bool = False,
        branch_drift: bool = False,
        detail_drift: str = "",
        retry_first: bool = False,
        duplicate: bool = False,
    ) -> None:
        self.urls: list[str] = []
        self.counts: Counter[str] = Counter()
        self.nonempty_sentinel = nonempty_sentinel
        self.mutate_first_recheck = mutate_first_recheck
        self.checked_filter = checked_filter
        self.branch_drift = branch_drift
        self.detail_drift = detail_drift
        self.retry_first = retry_first
        self.duplicate = duplicate

    def __call__(
        self, _session: Any, method: str, url: str, **_kwargs: Any
    ) -> str:
        assert method == "GET"
        self.urls.append(url)
        self.counts[url] += 1
        if self.retry_first and len(self.urls) == 1:
            raise TimeoutError("synthetic retry")
        query = parse_qs(urlparse(url).query, keep_blank_values=True)
        if query.get("cmd") == ["2"]:
            identity = query["lctre_uid"][0]
            row = next(item for item in LIFELONG_ROWS if item["id"] == identity)
            return _life_detail(row, drift=self.detail_drift)
        page = int(query.get("pageNo", ["1"])[0])
        sentinel = page == 3
        start = (page - 1) * 10
        rows: list[Mapping[str, Any]] = list(LIFELONG_ROWS[start : start + 10])
        if self.duplicate and page == 2:
            rows[0] = LIFELONG_ROWS[9]
        if self.nonempty_sentinel and sentinel:
            rows = [LIFELONG_ROWS[0]]
        if (
            self.mutate_first_recheck
            and page == 1
            and self.counts[url] >= 2
        ):
            rows = [dict(rows[0], title="변경된 제목"), *rows[1:]]
        return _life_page(
            page,
            rows,
            sentinel=sentinel,
            checked_filter=self.checked_filter,
            branch_drift=self.branch_drift,
        )


FAMILY_TARGET = {
    "provider": ulleung.ULLEUNG_FAMILY_PROVIDER,
    "url": ulleung.ULLEUNG_FAMILY_URL,
}
LIFELONG_TARGET = {
    "provider": ulleung.ULLEUNG_LIFELONG_PROVIDER,
    "url": ulleung.ULLEUNG_LIFELONG_URL,
}


def test_provider_hashes_target_routing_and_duplicate_alias_decision() -> None:
    assert ulleung.is_ulleung_family_target(FAMILY_TARGET)
    assert ulleung.is_ulleung_lifelong_target(LIFELONG_TARGET)
    assert ulleung.is_ulleung_education_target(FAMILY_TARGET)
    assert hashlib.sha1(ulleung.ULLEUNG_FAMILY_URL.encode()).hexdigest().upper() == (
        ulleung.ULLEUNG_FAMILY_URL_SHA1
    )
    assert hashlib.sha256(ulleung.ULLEUNG_LIFELONG_URL.encode()).hexdigest().upper() == (
        ulleung.ULLEUNG_LIFELONG_URL_SHA256
    )
    alias = {
        "provider": ulleung.ULLEUNG_LIFELONG_ALIAS_PROVIDER,
        "url": ulleung.ULLEUNG_LIFELONG_ALIAS_URL,
    }
    assert not ulleung.is_ulleung_education_target(alias)
    calls: list[str] = []

    def no_fetch(*_args: Any, **_kwargs: Any) -> str:
        calls.append("called")
        raise AssertionError

    rows, parser, meta = ulleung.collect(alias, fetcher=no_fetch)
    assert rows == []
    assert parser == ulleung.ULLEUNG_LIFELONG_PARSER
    assert "canonical" in meta["configured_collection_error"]
    assert calls == []
    assert meta["duplicate_alias_candidate_id"] == "MUNI_IR_02223298A97E"
    assert "deactivate" in meta["duplicate_alias_decision"]


def test_family_complete_snapshot_reconciles_default_active_and_omits_pii() -> None:
    fetcher = _FamilyFetcher()
    rows, parser, meta = ulleung.collect(
        FAMILY_TARGET,
        today=AUDIT_DATE,
        max_workers=1,
        session_factory=_Session,
        fetcher=fetcher,
    )
    assert parser == ulleung.ULLEUNG_FAMILY_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == meta["current_count"] == 6
    assert meta["default_active_total"] == 4
    assert meta["source_status_counts"] == {"접수마감": 2, "접수중": 4}
    assert meta["ledger_pages"] == {"current_all": 2, "active": 1}
    assert meta["sentinel_pages"] == {"current_all": 3, "active": 2}
    assert meta["list_requests"] == 10
    assert meta["logical_requests"] == meta["physical_requests"] == 16
    assert meta["online_application_count"] == 4
    assert all(meta["stable_rechecks"].values())
    assert [row["raw_fields"]["source_identity"] for row in rows] == [
        "606",
        "605",
        "604",
        "603",
        "602",
        "601",
    ]
    assert sum(row["status"] == "OPEN" for row in rows) == 4
    assert all(row["branch"] == "울릉군 가족센터" for row in rows)
    serialized = repr(rows)
    assert "loginCheck.do" not in serialized
    assert "getView.do" not in serialized
    assert "modal/apply.do" not in serialized
    assert not any(
        forbidden in row["raw_fields"]
        for row in rows
        for forbidden in ("phone", "contact", "content", "applicants")
    )
    assert not any(
        forbidden in url
        for url in fetcher.urls
        for forbidden in ("getView.do", "loginCheck.do", "modal/apply.do")
    )


@pytest.mark.parametrize(
    ("fetcher", "message"),
    [
        (_FamilyFetcher(nonempty_sentinel=True), "expected 0 cards"),
        (_FamilyFetcher(active_missing=True), "reconcile"),
        (_FamilyFetcher(mutate_first_recheck=True), "changed on recheck"),
        (_FamilyFetcher(detail_drift="title"), "identity binding"),
    ],
)
def test_family_contract_drift_fails_closed(
    fetcher: _FamilyFetcher, message: str
) -> None:
    rows, _, meta = ulleung.collect(
        FAMILY_TARGET,
        today=AUDIT_DATE,
        max_workers=1,
        session_factory=_Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_family_caps_retry_and_external_dedupe_are_enforced() -> None:
    rows, _, cap_meta = ulleung.collect(
        FAMILY_TARGET,
        today=AUDIT_DATE,
        max_pages=1,
        session_factory=_Session,
        fetcher=_FamilyFetcher(),
    )
    assert rows == [] and cap_meta["source_cap_reached"] is True

    retry = _FamilyFetcher(retry_first=True)
    rows, _, retry_meta = ulleung.collect(
        FAMILY_TARGET,
        today=AUDIT_DATE,
        max_workers=1,
        session_factory=_Session,
        fetcher=retry,
    )
    assert len(rows) == 6
    assert retry_meta["request_retry_count"] == 1

    rows, _, dedupe_meta = ulleung.collect(
        FAMILY_TARGET,
        today=AUDIT_DATE,
        max_workers=1,
        session_factory=_Session,
        fetcher=_FamilyFetcher(),
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "external dedupe" in dedupe_meta["configured_collection_error"]


def test_lifelong_complete_snapshot_full_pages_anomalies_and_privacy() -> None:
    fetcher = _LifelongFetcher()
    rows, parser, meta = ulleung.collect(
        LIFELONG_TARGET,
        today=AUDIT_DATE,
        max_workers=1,
        session_factory=_Session,
        fetcher=fetcher,
    )
    assert parser == ulleung.ULLEUNG_LIFELONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 13
    assert meta["ledger_pages"] == 2
    assert meta["sentinel_page"] == 3
    assert meta["current_count"] == 3
    assert meta["current_ids"] == ["3100", "3099", "3098"]
    assert meta["expired_count"] == 10
    assert meta["event_correction_ids"] == ["1012"]
    assert meta["receipt_correction_ids"] == ["28", "27"]
    assert meta["additional_receipt_ids"] == ["2005"]
    assert meta["source_status_counts"] == {"접수마감": 13}
    assert meta["list_requests"] == 6
    assert meta["logical_requests"] == meta["physical_requests"] == 9
    assert meta["online_application_count"] == 0
    assert all(meta["stable_rechecks"].values())
    assert all(row["status"] == "CLOSED" for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(
        urlparse(row["raw_url"]).hostname == ulleung.ULLEUNG_LIFELONG_HOST
        and parse_qs(urlparse(row["raw_url"]).query).get("mnu_uid") == ["1846"]
        for row in rows
    )
    serialized = repr(rows)
    assert "054)790-0000" not in serialized
    assert "홍길동" not in serialized
    assert "민감한 자유문 본문" not in serialized
    assert "개인정보.xlsx" not in serialized
    assert not any("cmd=3" in url or "login" in url.lower() for url in fetcher.urls)


def test_lifelong_status_class_contract_matches_live_filter_order() -> None:
    html_text = _life_page(1, list(LIFELONG_ROWS[:10]))
    html_text = html_text.replace(
        '<span class="lctre_status st03">접수마감</span>',
        '<span class="lctre_status st02">접수중</span>',
        1,
    ).replace(
        '<div class="btn_wrap disabled"><a',
        '<div class="btn_wrap"><a',
        1,
    ).replace(
        ">접수마감</a>",
        ">수강신청</a>",
        1,
    )
    _, rows, _ = ulleung._parse_lifelong_page(
        BeautifulSoup(html_text, "html.parser"),
        page=1,
    )
    assert rows[0].source_status == "접수중"

    scheduled_html = _life_page(1, list(LIFELONG_ROWS[:10]))
    scheduled_html = scheduled_html.replace(
        '<span class="lctre_status st03">접수마감</span>',
        '<span class="lctre_status st01">접수대기</span>',
        1,
    ).replace(
        ">접수마감</a>",
        ">수강신청</a>",
        1,
    )
    _, scheduled_rows, _ = ulleung._parse_lifelong_page(
        BeautifulSoup(scheduled_html, "html.parser"),
        page=1,
    )
    assert scheduled_rows[0].source_status == "접수대기"
    assert (
        ulleung._lifelong_effective_status(
            scheduled_rows[0],
            ulleung.date.fromisoformat(AUDIT_DATE),
        )
        == "SCHEDULED"
    )

    scheduled_detail = _life_detail(LIFELONG_ROWS[0]).replace(
        ">접수마감</a>",
        ">접수대기</a>",
        1,
    )
    assert (
        ulleung._parse_lifelong_detail(
            BeautifulSoup(scheduled_detail, "html.parser"),
            scheduled_rows[0],
        ).control
        == "scheduled"
    )

    drifted = html_text.replace(
        '<span class="lctre_status st02">접수중</span>',
        '<span class="lctre_status st01">접수중</span>',
        1,
    )
    with pytest.raises(ulleung.UlleungContractError, match="unknown status contract"):
        ulleung._parse_lifelong_page(
            BeautifulSoup(drifted, "html.parser"),
            page=1,
        )


@pytest.mark.parametrize(
    ("fetcher", "message"),
    [
        (_LifelongFetcher(nonempty_sentinel=True), "unexpected course count"),
        (_LifelongFetcher(mutate_first_recheck=True), "changed on recheck"),
        (_LifelongFetcher(checked_filter=True), "target vocabulary"),
        (_LifelongFetcher(branch_drift=True), "unknown owner branch"),
        (_LifelongFetcher(detail_drift="title"), "title mismatch"),
        (_LifelongFetcher(detail_drift="control"), "closed detail control"),
        (_LifelongFetcher(duplicate=True), "duplicate"),
    ],
)
def test_lifelong_contract_drift_fails_closed(
    fetcher: _LifelongFetcher, message: str
) -> None:
    rows, _, meta = ulleung.collect(
        LIFELONG_TARGET,
        today=AUDIT_DATE,
        max_workers=1,
        session_factory=_Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_lifelong_caps_retry_wrong_target_and_external_dedupe() -> None:
    rows, _, page_cap = ulleung.collect(
        LIFELONG_TARGET,
        today=AUDIT_DATE,
        max_pages=1,
        session_factory=_Session,
        fetcher=_LifelongFetcher(),
    )
    assert rows == [] and page_cap["source_cap_reached"] is True

    rows, _, detail_cap = ulleung.collect(
        LIFELONG_TARGET,
        today=AUDIT_DATE,
        detail_limit=2,
        max_workers=1,
        session_factory=_Session,
        fetcher=_LifelongFetcher(),
    )
    assert rows == [] and detail_cap["source_cap_reached"] is True

    retry = _LifelongFetcher(retry_first=True)
    rows, _, retry_meta = ulleung.collect(
        LIFELONG_TARGET,
        today=AUDIT_DATE,
        max_workers=1,
        session_factory=_Session,
        fetcher=retry,
    )
    assert len(rows) == 3 and retry_meta["request_retry_count"] == 1

    rows, _, dedupe_meta = ulleung.collect(
        LIFELONG_TARGET,
        today=AUDIT_DATE,
        max_workers=1,
        session_factory=_Session,
        fetcher=_LifelongFetcher(),
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "external dedupe" in dedupe_meta["configured_collection_error"]

    wrong_family = dict(FAMILY_TARGET, url=FAMILY_TARGET["url"] + "?x=1")
    called: list[str] = []

    def no_fetch(*_args: Any, **_kwargs: Any) -> str:
        called.append("called")
        raise AssertionError

    rows, parser, wrong_meta = ulleung.collect(wrong_family, fetcher=no_fetch)
    assert rows == [] and parser == ulleung.ULLEUNG_FAMILY_PARSER
    assert "Family Center" in wrong_meta["configured_collection_error"]
    assert called == []


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_ULLEUNG_TESTS") != "1",
    reason="set RUN_LIVE_ULLEUNG_TESTS=1 for the audited live two-run contract",
)
def test_live_ulleung_two_consecutive_complete_snapshots() -> None:
    expected = {
        ulleung.ULLEUNG_FAMILY_PROVIDER: {
            "source_total": 7,
            "current_count": 7,
            "default_active_total": 4,
            "list_requests": 10,
            "online_application_count": 4,
            "source_status_counts": {"접수마감": 3, "접수중": 4},
        },
        ulleung.ULLEUNG_LIFELONG_PROVIDER: {
            "source_total": 114,
            "current_count": 11,
            "ledger_pages": 12,
            "list_requests": 16,
            "online_application_count": 0,
            "source_status_counts": {"접수마감": 114},
        },
    }
    targets = (FAMILY_TARGET, LIFELONG_TARGET)
    snapshots: list[dict[str, tuple[list[str], dict[str, Any]]]] = []
    for _ in range(2):
        run: dict[str, tuple[list[str], dict[str, Any]]] = {}
        for target in targets:
            rows, _, meta = ulleung.collect(
                target,
                today=AUDIT_DATE,
                timeout=30,
                max_pages=20,
                detail_limit=20,
                max_workers=4,
            )
            assert meta["configured_collection_error"] == ""
            assert meta["snapshot_complete"] is True
            assert meta["full_snapshot_validated"] is True
            assert all(meta["stable_rechecks"].values())
            assert meta["application_endpoint_fetches"] == 0
            for key, value in expected[target["provider"]].items():
                assert meta[key] == value
            run[target["provider"]] = (
                [row["provider_course_id"] for row in rows],
                {
                    key: meta[key]
                    for key in expected[target["provider"]]
                },
            )
        snapshots.append(run)
    assert snapshots[0] == snapshots[1]
