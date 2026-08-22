from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from Crawler import municipal_daejeon_seogu as seogu


@dataclass
class SeoGuTarget:
    provider: str = seogu.DAEJEON_SEOGU_PROVIDER
    url: str = seogu.DAEJEON_SEOGU_CANONICAL_URL
    branch: str = seogu.DAEJEON_SEOGU_MUNICIPALITY_NAME


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _records() -> dict[str, list[dict[str, Any]]]:
    return {
        "lifelong_program": [
            {
                "id": "LEC_000000000101",
                "order": "ORD_000000000201",
                "title": "환경교육 실천가",
                "start": "2099-08-01",
                "end": "2099-08-31",
                "apply_start": "2099-07-01",
                "apply_end": "2099-07-31",
                "status": "접수중",
                "capacity": 20,
                "current": 3,
                "target": "서구 주민",
                "schedule": "목 10:00~12:00",
                "fee": "10,000원",
            },
            {
                "id": "LEC_000000000100",
                "order": "ORD_000000000201",
                "title": "가족 숲체험",
                "start": "2099-08-02",
                "end": "2099-08-02",
                "apply_start": "2099-07-01",
                "apply_end": "2099-07-31",
                "status": "접수중",
                "capacity": 10,
                "current": 4,
                "target": "가족",
                "schedule": "토 10:00~12:00",
                "fee": "무료",
            },
            {
                "id": "LEC_000000000099",
                "order": "ORD_000000000200",
                "title": "종료 평생학습",
                "start": "2020-01-01",
                "end": "2020-02-01",
                "apply_start": "2019-12-01",
                "apply_end": "2019-12-20",
                "status": "결제마감",
                "capacity": 15,
                "current": 15,
                "target": "성인",
                "schedule": "화 10:00~12:00",
                "fee": "무료",
            },
        ],
        "poomasi_school": [
            {
                "id": "LEC_000000000091",
                "order": "ORD_000000000211",
                "title": "품앗이 코딩교실",
                "start": "2099-09-01",
                "end": "2099-09-30",
                "apply_start": "2099-07-01",
                "apply_end": "2099-08-31",
                "status": "대기신청",
                "capacity": 12,
                "current": 12,
                "target": "초등학생",
                "schedule": "수 16:00~18:00",
                "fee": "무료",
            }
        ],
        "seorami_university": [],
        "special_lecture": [
            {
                "id": "LEC_000000000081",
                "order": "ORD_000000000221",
                "title": "생활법률 특강",
                "start": "2099-10-01",
                "end": "2099-10-01",
                "apply_start": "2099-06-01",
                "apply_end": "2099-06-30",
                "status": "접수마감",
                "capacity": 30,
                "current": 30,
                "target": "서구 주민",
                "schedule": "금 19:00~21:00",
                "fee": "무료",
            }
        ],
        "library_galma": [
            {
                "id": "LEC_000000000071",
                "title": "여름 독서교실",
                "source_category": "어린이",
                "start": "2099-08-01",
                "end": "2099-08-04",
                "apply_start": "2099-07-01",
                "apply_end": "2099-07-31",
                "capacity": 15,
                "current": 2,
                "target": "초등학생",
                "schedule": "10:00~12:00",
                "control": "add",
            },
            {
                "id": "LEC_000000000070",
                "title": "가족 AI 체험",
                "source_category": "어린이",
                "start": "2099-08-05",
                "end": "2099-08-05",
                "apply_start": "2099-07-01",
                "apply_end": "2099-07-31",
                "capacity": 10,
                "current": 10,
                "target": "가족",
                "schedule": "19:00~21:00",
                "control": "alert",
            },
        ],
        "library_gasuwon": [
            {
                "id": "LEC_000000000061",
                "title": "종료 도서관 강좌",
                "source_category": "일반인",
                "start": "2020-03-01",
                "end": "2020-03-10",
                "apply_start": "2020-02-01",
                "apply_end": "2020-02-20",
                "capacity": 20,
                "current": 20,
                "target": "성인",
                "schedule": "10:00~12:00",
                "control": "alert",
            }
        ],
        "library_dunsan": [
            {
                "id": "LEC_000000000051",
                "title": "가족 인형극 공연",
                "source_category": "어린이",
                "start": "2099-08-10",
                "end": "2099-08-10",
                "apply_start": "2099-07-01",
                "apply_end": "2099-07-31",
                "capacity": 30,
                "current": 30,
                "target": "가족",
                "schedule": "14:00~15:00",
                "control": "alert",
            }
        ],
        "library_wolpyeong": [
            {
                "id": "LEC_000000000041",
                "title": "시민 글쓰기",
                "source_category": "일반인",
                "start": "2099-09-01",
                "end": "2099-10-31",
                "apply_start": "2099-08-01",
                "apply_end": "2099-08-20",
                "capacity": 20,
                "current": 0,
                "target": "성인",
                "schedule": "10:00~12:00",
                "control": "alert",
            }
        ],
        "library_child": [],
    }


def _lifelong_index(*, changed: bool = False) -> str:
    anchors = [source.list_url for source in seogu.DAEJEON_SEOGU_LIFELONG_SOURCES]
    if changed:
        anchors.append(
            "https://www.seogu.go.kr/learning/damoa/contents/learning/edu/01/"
            "edu.01.001.motion?searchLecDivArray=77&mnucd=MENU0199999"
        )
    return (
        "<html><head><title>대전광역시 서구 평생학습관</title></head><body>"
        + "".join(f"<a href='{url}'>과정</a>" for url in anchors)
        + "</body></html>"
    )


def _library_index(source: Any) -> str:
    roots = "".join(
        f"<a href='{item.root_url}'>{item.label}</a>"
        for item in seogu.DAEJEON_SEOGU_LIBRARY_SOURCES
    )
    return f"""
      <html><head><title>대전광역시 서구 {source.label}</title></head><body>
        {roots}<a href='{source.list_url}'>행사 및 강좌 신청</a>
      </body></html>
    """


def _life_card(source: Any, record: dict[str, Any]) -> str:
    return f"""
      <div class='box'><a onclick="fn_egov_select1(document.getElementById('listForm'),
        '{record['id']}','{record['order']}','36','9999999'); return false;">
        <p class='part'>{source.label}</p><h4>{record['title']}</h4>
        <ul class='list_01'>
          <li>교육 : {record['start'].replace('-', '.')}~{record['end'].replace('-', '.')}</li>
          <li>시간 : {record['schedule']}</li><li>인원 : {record['capacity']}명</li>
          <li>대상 : {record['target']}</li><li>수강료 : {record['fee']}</li>
        </ul><span class='confirm00'>접수현황 <strong>{record['current']}</strong>명</span>
        <span class='progress'>{record['status']}</span>
      </a></div>
    """


def _life_list(source: Any, records: list[dict[str, Any]], page: int, total: int) -> str:
    last = max(1, (total + source.page_size - 1) // source.page_size)
    return f"""
      <html><head><title>온라인 수강신청 : 목록 화면 - 대전광역시 서구 평생학습관</title></head><body>
        <form id='listForm' method='post' action='{source.path}'>
          <input name='mnucd' value='{source.menu_code}'><input name='searchLecDivArray' value='{source.filter_code}'>
          <input name='bmode' value=''><input name='pageIndex' value='{page}'><input name='lecId' value=''>
          <input name='ordCd' value=''><input name='ordSidoCd' value=''><input name='ordLocalCd' value=''>
          <select name='searchCondition'><option value='1'>강좌명</option><option value='2'>강좌장소</option>
            <option value='6'>접수중</option></select>
          <div class='sub_program'><div class='board_search'><div class='count'>
            총 게시물 : {total} 건 현재 {page} / 전체 {last} 페이지
          </div></div><div class='sub_program_list'>
            {''.join(_life_card(source, item) for item in records)}
          </div></div>
        </form>
      </body></html>
    """


def _library_row(record: dict[str, Any], number: int) -> str:
    return f"""
      <tr><td>{number}</td><td>{record['source_category']}</td><td>
        <a href='#' onclick="fn_egov_select(document.getElementById('listForm'),
          '{record['id']}'); return false;">{record['title']}</a></td>
        <td>{record['target']}</td><td>{record['start']} ~ {record['end']}</td>
        <td>{record['schedule']}</td><td>{record['current']} / {record['capacity']}</td></tr>
    """


def _library_list(source: Any, records: list[dict[str, Any]], page: int, total: int) -> str:
    last = max(1, (total + source.page_size - 1) // source.page_size)
    tabs = "".join(
        f"<a href='#' onclick=\"fn_egov_selectTabList(document.getElementById('listForm'),"
        f"'{code}'); return false;\">{label}</a>"
        for label, code in seogu._LIBRARY_TABS
    )
    body = "".join(
        _library_row(record, total - (page - 1) * source.page_size - index)
        for index, record in enumerate(records)
    )
    if not records and total == 0:
        body = "<tr><td colspan='7'>조회된 내용이 없습니다.</td></tr>"
    headers = "".join(f"<th>{item}</th>" for item in seogu._LIBRARY_HEADERS)
    return f"""
      <html><head><title>행사 및 강좌 신청 - 대전광역시 서구 평생학습관</title></head><body>
        <form id='listForm' method='post' action='{source.path}'>
          <input name='mnucd' value='{source.menu_code}'><input name='bmode' value=''>
          <input name='pageIndex' value='{page}'><input name='searchLecGubun' value=''>
          <input name='lecId' value=''>
        </form>{tabs}<p class='total'>총 {total} 건, 현재 {page} / 전체 {last} 페이지</p>
        <table class='tbl_basic_list'><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>
      </body></html>
    """


def _life_detail(
    source: Any,
    record: dict[str, Any],
    *,
    missing_control: bool = False,
    pii_target: bool = False,
) -> str:
    target = "문의 user@example.com" if pii_target else record["target"]
    control = "" if missing_control else "<a href='#' onclick='fn_NonCheck(); return false;'>프로그램신청</a>"
    script = "" if missing_control else f"""
      function fn_NonCheck(){{location.href='/learning/damoa/contents/learning/member/01/member.01.001.motion?mnucd=MENU1000052';}}
      var check='/learning/damoa/Classesinfo/MberCheck.do?lecId={record['id']}';
      fn_payinfoView(document.getElementById('detailForm'),'{record['id']}','{record['order']}','36','9999999');
    """
    fields = {
        "과목명": record["title"],
        "교육일정": record["schedule"],
        "교육대상": target,
        "모집인원": str(record["capacity"]),
        "수강료": record["fee"],
        "교육장소": "서구 평생학습실",
        "교육기관": "서구평생학습관",
        "교육기간": f"{record['start'][2:].replace('-', '.')}~{record['end'][2:].replace('-', '.')}",
        "수강신청기간": (
            f"{record['apply_start'][2:].replace('-', '.')}~{record['apply_end'][2:].replace('-', '.')}"
        ),
        "모집방법": "선착순",
    }
    lis = "".join(
        f"<li><div class='titles'><strong>{name}</strong></div><div class='txts'>{value}</div></li>"
        for name, value in fields.items()
    )
    return f"""
      <html><head><title>온라인 수강신청 : 상세 화면 - 대전광역시 서구 평생학습관</title></head><body>
        <form id='detailForm' method='post' action='{source.path}'>
          <input name='mnucd' value='{source.menu_code}'><input name='bmode' value='detail1'>
          <input name='lecId' value='{record['id']}'><input name='ordCd' value='{record['order']}'>
          <input name='ordSidoCd' value='36'><input name='ordLocalCd' value='9999999'>
          <input name='searchLecDivArray' value='{source.filter_code}'>
        </form><ul class='detail'>{lis}</ul>{control}<script>{script}</script>
      </body></html>
    """


def _library_detail(source: Any, record: dict[str, Any]) -> str:
    if record["control"] == "add":
        application = (
            "fn_egov_addView(document.getElementById('detailForm'),"
            f"'{record['id']}'); return false;"
        )
    else:
        application = "alert('신청 기간이 아닙니다.'); return false;"
    fields = [
        ("제목", f"[ {record['source_category']}강좌 ] {record['title']}"),
        ("일시", f"{record['start']} ~ {record['end']}"),
        ("요일", "월요일"),
        ("시간", record["schedule"]),
        ("신청기간", f"{record['apply_start']} (10시) ~ {record['apply_end']} (23시)"),
        ("강사", "홍길동"),
        ("대상", record["target"]),
        ("모집인원", f"{record['current']} / {record['capacity']}"),
        ("예비인원", "0 / 5"),
        ("파일첨부", "fixture.pdf"),
        ("강의내용", "담당자 042-000-0000 자유 서술"),
    ]
    table = "".join(f"<tr><th>{name}</th><td>{value}</td></tr>" for name, value in fields)
    return f"""
      <html><head><title>행사 및 강좌 신청 : 상세 화면 - 대전광역시 서구 평생학습관</title></head><body>
        <form id='detailForm' method='post' action='{source.path}'>
          <input name='mnucd' value='{source.menu_code}'><input name='bmode' value='detail'>
          <input name='lecId' value='{record['id']}'>
        </form><table class='tbl_basic_view'>{table}</table><div class='btn_area'>
          <a href='#' onclick="{application}">수강신청</a>
          <a href='#' onclick="fn_egov_selectUserList(document.getElementById('detailForm'),
            '{record['id']}'); return false;">수강신청확인</a>
          <a href='#' onclick="fn_egov_selectList(document.getElementById('detailForm')); return false;">목록</a>
        </div>
      </body></html>
    """


def _fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    nonempty_sentinel: bool = False,
    changed_recheck: bool = False,
    changed_index: bool = False,
    missing_control: bool = False,
    pii_target: bool = False,
) -> tuple[dict[str, Any], list[DummySession], list[str]]:
    monkeypatch.setattr(seogu, "DAEJEON_SEOGU_LIFELONG_PAGE_SIZE", 2)
    monkeypatch.setattr(seogu, "DAEJEON_SEOGU_LIBRARY_PAGE_SIZE", 2)
    records = _records()
    pages: dict[str, str] = {
        seogu.DAEJEON_SEOGU_CANONICAL_URL: _lifelong_index(changed=changed_index)
    }
    for source in seogu.DAEJEON_SEOGU_LIBRARY_SOURCES:
        pages[source.root_url] = _library_index(source)
    first_pages: dict[str, str] = {}
    changed_first_pages: dict[str, str] = {}
    for source in seogu.DAEJEON_SEOGU_SOURCES:
        source_records = records[source.key]
        total = len(source_records)
        last = max(1, (total + source.page_size - 1) // source.page_size)
        maker = _life_list if source.kind == "lifelong" else _library_list
        for page in range(1, last + 1):
            start = (page - 1) * source.page_size
            chunk = source_records[start : start + source.page_size]
            value = maker(source, chunk, page, total)
            url = seogu.daejeon_seogu_list_url(source.key, page)
            pages[url] = value
            if page == 1:
                first_pages[url] = value
        sentinel_records: list[dict[str, Any]] = []
        if nonempty_sentinel and source.key == "lifelong_program":
            sentinel_records = source_records[:1]
        pages[seogu.daejeon_seogu_list_url(source.key, last + 1)] = maker(
            source, sentinel_records, last + 1, total
        )
        changed_records = list(source_records[: source.page_size])
        if changed_recheck and source.key == "lifelong_program":
            changed_records[0] = dict(changed_records[0], title="변경된 제목")
        changed_first_pages[source.list_url] = maker(source, changed_records, 1, total)
        for record in source_records:
            if record["end"] < "2099-01-01":
                continue
            family, _evidence = seogu._service_family(record["title"])
            if family == "performance":
                continue
            detail_url = seogu.daejeon_seogu_detail_url(
                source.key,
                record["id"],
                record.get("order", ""),
            )
            if source.kind == "lifelong":
                pages[detail_url] = _life_detail(
                    source,
                    record,
                    missing_control=(missing_control and record["id"].endswith("101")),
                    pii_target=(pii_target and record["id"].endswith("101")),
                )
            else:
                pages[detail_url] = _library_detail(source, record)

    sessions: list[DummySession] = []
    calls: list[str] = []
    call_counts: dict[str, int] = {}

    def factory() -> DummySession:
        value = DummySession()
        sessions.append(value)
        return value

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        call_counts[url] = call_counts.get(url, 0) + 1
        if changed_recheck and url in changed_first_pages and call_counts[url] >= 2:
            return changed_first_pages[url]
        if url not in pages:
            raise AssertionError(f"unexpected URL {url}")
        return pages[url]

    return {"factory": factory, "fetch": fetch}, sessions, calls


def _collect(
    monkeypatch: pytest.MonkeyPatch,
    **fixture_kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], list[DummySession], list[str]]:
    fixture, sessions, calls = _fixture(monkeypatch, **fixture_kwargs)
    rows, parser, meta = seogu.collect_daejeon_seogu_education(
        SeoGuTarget(),
        timeout=5,
        max_pages=40,
        detail_limit=10,
        today="2026-07-21",
        max_workers=4,
        session_factory=fixture["factory"],
        fetcher=fixture["fetch"],
    )
    return rows, parser, meta, sessions, calls


def test_target_scope_aliases_urls_and_external_source_decisions() -> None:
    assert seogu.is_daejeon_seogu_education_target(SeoGuTarget())
    assert not seogu.is_daejeon_seogu_education_target(
        {"provider": seogu.DAEJEON_SEOGU_PROVIDER, "url": seogu.DAEJEON_SEOGU_ALIAS_URLS[2]}
    )
    assert not seogu.is_daejeon_seogu_education_target(
        {"provider": "MUNI_WRONG", "url": seogu.DAEJEON_SEOGU_CANONICAL_URL}
    )
    assert seogu.is_daejeon_seogu_owned_alias_target(
        {"candidate_id": "MUNI_IR_222C74329C58"}
    )
    assert seogu.is_daejeon_seogu_owned_alias_target(
        {"provider": "MUNI_WWW_SEOGU_GO_KR_A27782FE"}
    )
    assert seogu.is_daejeon_seogu_owned_alias_target(
        {"url": seogu.DAEJEON_SEOGU_DETAIL_ALIAS_URL}
    )
    assert seogu.daejeon_seogu_list_url("library_galma", 2).endswith(
        "mnucd=MENU0200030&pageIndex=2"
    )
    assert seogu.daejeon_seogu_list_url("missing", 1) == ""
    assert seogu.daejeon_seogu_detail_url(
        "lifelong_program", "LEC_000000000101", "ORD_000000000201"
    ).endswith("ordSidoCd=36&ordLocalCd=9999999")
    assert seogu.DAEJEON_SEOGU_CANDIDATE_AUDIT["MUNI_IR_72541B622CD4"]["decision"] == (
        "owned_library_shell_alias"
    )
    assert seogu.DAEJEON_SEOGU_OK_OVERLAP_AUDIT[
        "normalized_title_period_overlap_count"
    ] == 0
    assert seogu.DAEJEON_SEOGU_SPORTS_EXCLUSION_AUDIT["active_membership_products"] == 13


def test_complete_nine_source_snapshot_partitions_details_and_pii(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, parser, meta, sessions, calls = _collect(monkeypatch)

    assert parser == seogu.DAEJEON_SEOGU_PARSER
    assert len(rows) == 7
    assert {row["branch"] for row in rows} == {
        "평생학습관프로그램",
        "품앗이스쿨",
        "특강강좌",
        "갈마도서관",
        "월평도서관",
    }
    assert {row["service_group"] for row in rows} == {"공공강좌", "체험"}
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["description"] == row["title"] for row in rows)
    assert all("홍길동" not in repr(row) for row in rows)
    assert all("042-" not in repr(row) and "fixture.pdf" not in repr(row) for row in rows)
    assert all(set(row["raw_fields"]) <= seogu._SAFE_RAW_FIELDS for row in rows)
    assert {row["status"] for row in rows} >= {"OPEN", "WAITLIST", "CLOSED", "SCHEDULED"}
    assert len([row for row in rows if row["reservation_available"]]) == 4
    experience_rows = [row for row in rows if row["service_group"] == "체험"]
    assert {row["title"] for row in experience_rows} == {"가족 숲체험", "가족 AI 체험"}
    assert {row["domain_category"] for row in experience_rows} == {"체험·견학"}
    assert {row["program_type"] for row in experience_rows} == {"체험"}

    assert meta["source_rows"] == 10
    assert meta["current_source_count"] == 8
    assert meta["current_education_count"] == 5
    assert meta["current_experience_count"] == 2
    assert meta["current_program_count"] == meta["returned_count"] == 7
    assert meta["expired_count"] == 2
    assert meta["service_family_counts"] == {
        "education": 7,
        "experience": 2,
        "performance": 1,
    }
    assert meta["current_service_family_counts"] == {
        "education": 5,
        "experience": 2,
        "performance": 1,
    }
    assert meta["excluded_current_count"] == 1
    assert meta["excluded_current_counts"] == {"performance": 1}
    assert meta["index_requests"] == 6
    assert meta["required_list_requests"] == meta["list_requests"] == 28
    assert meta["required_source_requests"] == 34
    assert meta["sentinel_requests"] == meta["stability_rechecks"] == 9
    assert meta["detail_attempts"] == meta["detail_pages"] == 7
    assert meta["domain_category_counts"] == {"교육·강좌": 5, "체험·견학": 2}
    assert meta["service_group_counts"] == {"공공강좌": 5, "체험": 2}
    assert meta["identity_duplicate_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["pii_payload_persisted"] is False
    assert all(session.closed for session in sessions)
    assert len(calls) == 41
    assert any("LEC_000000000070" in url for url in calls)
    assert not any("LEC_000000000051" in url for url in calls)


def test_index_sentinel_and_stability_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _parser, meta, _sessions, _calls = _collect(
        monkeypatch, changed_index=True
    )
    assert rows == []
    assert "lifelong official catalogue fanout changed" in meta["configured_collection_error"]

    rows2, _parser2, meta2, _sessions2, _calls2 = _collect(
        monkeypatch, nonempty_sentinel=True
    )
    assert rows2 == []
    assert "immediate post-last page is not empty" in meta2["configured_collection_error"]

    rows3, _parser3, meta3, _sessions3, _calls3 = _collect(
        monkeypatch, changed_recheck=True
    )
    assert rows3 == []
    assert "page-one recheck changed" in meta3["configured_collection_error"]


def test_detail_control_and_pii_allowlist_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _parser, meta, _sessions, _calls = _collect(
        monkeypatch, missing_control=True
    )
    assert rows == []
    assert "course-bound application/login control changed" in meta["configured_collection_error"]

    rows2, _parser2, meta2, _sessions2, _calls2 = _collect(
        monkeypatch, pii_target=True
    )
    assert rows2 == []
    assert "PII-like contact data persisted" in meta2["configured_collection_error"]


def test_caps_and_external_dedupe_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, _sessions, _calls = _fixture(monkeypatch)
    rows, _parser, meta = seogu.collect_daejeon_seogu_education(
        SeoGuTarget(),
        max_pages=33,
        detail_limit=10,
        today="2026-07-21",
        session_factory=fixture["factory"],
        fetcher=fixture["fetch"],
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap allows 33 of 34" in meta["configured_collection_error"]

    fixture2, _sessions2, _calls2 = _fixture(monkeypatch)
    rows2, _parser2, meta2 = seogu.collect_daejeon_seogu_education(
        SeoGuTarget(),
        max_pages=40,
        detail_limit=4,
        today="2026-07-21",
        session_factory=fixture2["factory"],
        fetcher=fixture2["fetch"],
    )
    assert rows2 == []
    assert meta2["source_cap_reached"] is True
    assert "detail_limit cap allows 4 of 7" in meta2["configured_collection_error"]

    fixture3, _sessions3, _calls3 = _fixture(monkeypatch)
    rows3, _parser3, meta3 = seogu.collect_daejeon_seogu_education(
        SeoGuTarget(),
        max_pages=40,
        detail_limit=10,
        today="2026-07-21",
        session_factory=fixture3["factory"],
        fetcher=fixture3["fetch"],
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows3 == []
    assert "dedupe changed official identity cardinality" in meta3["configured_collection_error"]
