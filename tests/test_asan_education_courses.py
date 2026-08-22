from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from typing import Any

import pytest

from Crawler import municipal_asan as asan


@dataclass
class AsanTarget:
    provider: str = asan.ASAN_PROVIDER
    url: str = asan.ASAN_CANONICAL_URL
    branch: str = asan.ASAN_MUNICIPALITY_NAME


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _total_marker(total: int, page: int, last: int) -> str:
    return f"<div class='count'>총 {total}건 ( {page}/{last}페이지)</div>"


def _office_page(
    *, page: int, renamed: bool = False, status_order: bool = False
) -> str:
    offices = list(asan.ASAN_EXPECTED_OFFICES) if page == 1 else []
    if status_order:
        promoted_codes = {"OFFICE_00002334", "OFFICE_00002360"}
        offices = [
            *offices[:7],
            *(office for office in offices if office.code in promoted_codes),
            *(office for office in offices[7:] if office.code not in promoted_codes),
        ]
    links = []
    for index, office in enumerate(offices):
        name = "변경된 기관명" if renamed and index == 0 else office.name
        status = (
            "<span class='type2'>접수예정</span>"
            if office.code in {"OFFICE_00002334", "OFFICE_00002360"}
            else ("<span class='type'>접수중</span>" if index % 3 == 0 else "")
        )
        links.append(
            "<a href='javascript:;' "
            f"onclick=\"fn_learning_list('{office.code}'); return false;\">"
            f"{status}<strong>{name}</strong>"
            "<span class='txt'>T.041-537-3906</span></a>"
        )
    return (
        "<html><body>"
        + _total_marker(len(asan.ASAN_EXPECTED_OFFICES), page, 1)
        + "".join(links)
        + "</body></html>"
    )


def _learning_row(record: dict[str, Any]) -> str:
    control_class = "blue" if record["action"] == "수강신청" else "gray"
    status_badges = "".join(
        f"<span class='s_btn'>{status}</span>"
        for status in record.get("status_badges", [record["status"]])
    )
    return f"""
      <tr>
        <td>{record['sequence']}</td>
        <td class='subject tal'><a href='javascript:;'
          onclick="fn_learning_detail('{record['id']}'); return false;">
          <span class='tit'>{record['title']}</span>
          <span class='org'>{record['office']}</span></a></td>
        <td class='type tac'>{record['type']}</td>
        <td class='tal period'><span class='s_type blue'>
          <em class='hidden'>교육기간</em>{record['period']}</span>
          <span><pre>{record['schedule']}</pre></span></td>
        <td class='tal'><span class='s_type indigo3'>
          <em class='hidden'>총모집인원</em>{record['capacity']}</span>
          <span class='s_type red3'><em class='hidden'>일반 접수</em>
          {record['apply_period']}</span></td>
        <td class='mobile'>{status_badges}</td>
        <td class='tac'><a href='javascript:;'><span class='button {control_class}'>
          {record['action']}</span></a></td>
      </tr>
    """


def _learning_page(
    records: list[dict[str, Any]], *, page: int, total: int
) -> str:
    last = max(1, (total + asan.ASAN_PAGE_SIZE - 1) // asan.ASAN_PAGE_SIZE)
    headings = (
        "<thead><tr><th>번호</th><th>강좌명/교육기관</th><th>강좌유형</th>"
        "<th>교육기간</th><th>접수기간 / 모집인원</th><th>상태</th><th>보기</th>"
        "</tr></thead>"
    )
    return (
        "<html><body>"
        + _total_marker(total, page, last)
        + "<table class='tbl lecture'>"
        + headings
        + "<tbody>"
        + "".join(_learning_row(record) for record in records)
        + "</tbody></table></body></html>"
    )


def _media_page(
    records: list[dict[str, Any]], *, page: int, total: int
) -> str:
    last = max(1, (total + asan.ASAN_MEDIA_PAGE_SIZE - 1) // asan.ASAN_MEDIA_PAGE_SIZE)
    cards = "".join(
        f"""
          <li><a href='#' onclick="fn_detail('{record['id']}');return false;">
            <span class='tit'>{record['title']}</span>
            <span class='date_hits'><span class='date'>교육 : {record['period']}</span></span>
          </a></li>
        """
        for record in records
    )
    return (
        "<html><body>"
        + _total_marker(total, page, last)
        + f"<div class='media_lst'><ul>{cards}</ul></div></body></html>"
    )


def _dl(key: str, value: str) -> str:
    return f"<dl><dt>{key}</dt><dd>{value}</dd></dl>"


def _learning_detail(
    record: dict[str, Any],
    *,
    bad_title: bool = False,
    omit_control: bool = False,
) -> str:
    start, end = [part.strip() for part in record["period"].split("~")]
    title = "다른 강좌명" if bad_title else record["title"]
    fields = "".join(
        [
            _dl("회차명", "테스트 회차"),
            _dl("강좌분류", "인문교양 > 시민역량"),
            _dl("교육대상", "성인"),
            _dl("문의전화", "041-537-3907"),
            _dl("교육장소", "아산시평생학습관 3층 강의실"),
            _dl("교육기간", record["period"]),
            _dl("교육시간", record["schedule"]),
            _dl("수강료", "무료"),
            _dl("접수인원", f"총모집인원 {record['capacity']} 일반 접수"),
            _dl("신청상태", f"일반 {record['status']}"),
            _dl("강좌소개", "문의 041-537-3907, 강사 홍길동"),
            _dl("강사", "홍길동"),
        ]
    )
    control = ""
    if record["action"] == "수강신청" and not omit_control:
        control = (
            "<a id='learning_aply_btn' href='javascript:;' "
            "onclick='fn_learning_apply(); return false;'>수강신청하기</a>"
        )
    return f"""
      <html><body><form id='learningVO'>
        <input name='inst_id' value='{record['office_code']}'>
        <input name='lng_id' value='{record['id']}'>
        <input name='lng_nm' value='{title}'>
        <input name='alife_edu_bgng_ymd' value='{start}'>
        <input name='alife_edu_end_ymd' value='{end}'>
        <h2 class='enrolTit'><span>[{record['office']}]</span>{title}</h2>
        <div class='form_group'>{fields}</div>{control}
      </form></body></html>
    """


def _media_detail(record: dict[str, Any], *, bad_identity: bool = False) -> str:
    identity = "LEARNING_BAD_ID" if bad_identity else record["id"]
    fields = "".join(
        [
            _dl("회차명", "행복아산 시민아카데미"),
            _dl("강좌분류", "인문교양 > 시민역량"),
            _dl("교육대상", "성인"),
            _dl("문의전화", "041-537-3372"),
            _dl("교육기관/교육장소", "아산시평생학습관 / 아산아트홀"),
            _dl("교육기간", "상시"),
            _dl("교육시간", "상시"),
            _dl("수강료", "무료"),
            _dl("모집정원", "상시"),
            _dl("교육상태", "교육중"),
            _dl("강좌소개", "문의 041-537-3372, 강사 정보는 저장 금지"),
        ]
    )
    return f"""
      <html><body><form>
        <input name='lng_id' value='{identity}'>
        <input name='lng_nm' value='{record['title']}'>
        <input name='alife_edu_bgng_ymd' value='2099-07-01'>
        <p class='tit'><span>[아산시평생학습관]</span>{record['title']}</p>
        <div class='form_group'>{fields}</div>
        <a id='learning_aply_btn' href='javascript:;'
          onclick='fn_learning_apply(); return false;'>일반모집신청</a>
      </form></body></html>
    """


def _records() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    office0 = asan.ASAN_EXPECTED_OFFICES[0]
    office1 = asan.ASAN_EXPECTED_OFFICES[1]
    office2 = asan.ASAN_EXPECTED_OFFICES[2]
    learning = [
        {
            "sequence": 5,
            "id": "LEARNING_OPEN_001",
            "title": "공개 시민강좌",
            "office": office0.name,
            "office_code": office0.code,
            "type": "오프라인 강좌",
            "period": "2099-07-21 ~ 2099-08-31",
            "schedule": "월 10:00 ~ 12:00",
            "capacity": "20명",
            "apply_period": "2099-07-01 ~ 2099-07-31",
            "status": "접수중",
            "action": "수강신청",
        },
        {
            "sequence": 4,
            "id": "LEARNING_FACILITY_001",
            "title": "헬스 시설 이용",
            "office": office1.name,
            "office_code": office1.code,
            "type": "체육 시설",
            "period": "2099-07-01 ~ 2099-09-30",
            "schedule": "상시",
            "capacity": "30명",
            "apply_period": "2099-06-01 ~ 2099-06-30",
            "status": "교육중",
            "action": "수강정보",
        },
        {
            "sequence": 3,
            "id": "LEARNING_SCHEDULED_001",
            "title": "예정 시민강좌",
            "office": office2.name,
            "office_code": office2.code,
            "type": "오프라인 강좌",
            "period": "2099-09-01 ~ 2099-10-31",
            "schedule": "수 14:00 ~ 16:00",
            "capacity": "15명",
            "apply_period": "2099-08-01 ~ 2099-08-20",
            "status": "대기",
            "action": "수강정보",
        },
        {
            "sequence": 2,
            "id": "LEARNING_EXPIRED_001",
            "title": "종료 시민강좌",
            "office": office0.name,
            "office_code": office0.code,
            "type": "화상 강좌",
            "period": "2000-01-01 ~ 2000-02-01",
            "schedule": "화 10:00 ~ 11:00",
            "capacity": "10명",
            "apply_period": "1999-12-01 ~ 1999-12-20",
            "status": "교육완료",
            "action": "수강정보",
        },
        {
            "sequence": 1,
            "id": "LEARNING_DEV_001",
            "title": "테스트 강좌입니다. 신청하지 마세요",
            "office": asan.ASAN_HIDDEN_DEVELOPMENT_OFFICE.name,
            "office_code": asan.ASAN_HIDDEN_DEVELOPMENT_OFFICE.code,
            "type": "오프라인 강좌",
            "period": "2099-07-01 ~ 2099-08-01",
            "schedule": "월 09:00 ~ 10:00",
            "capacity": "10명",
            "apply_period": "2099-06-01 ~ 2099-06-20",
            "status": "마감",
            "action": "수강정보",
        },
    ]
    media = [
        {
            "id": "LEARNING_MEDIA_001",
            "title": "행복아산 시민아카데미 동영상",
            "period": "상시",
        }
    ]
    return learning, media


def _fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    renamed_office: bool = False,
    status_order: bool = False,
    nonempty_learning_sentinel: bool = False,
    changed_learning_recheck: bool = False,
    bad_learning_title: bool = False,
    missing_learning_control: bool = False,
    bad_media_identity: bool = False,
    duplicate_media_identity: bool = False,
) -> tuple[Any, Any, list[DummySession], list[dict[str, Any]], list[dict[str, Any]]]:
    monkeypatch.setattr(asan, "ASAN_PAGE_SIZE", 2)
    monkeypatch.setattr(asan, "ASAN_MEDIA_PAGE_SIZE", 2)
    learning, media = _records()
    if duplicate_media_identity:
        media[0]["id"] = learning[0]["id"]
    pages: dict[str, str] = {}
    pages[asan.asan_office_list_url(1)] = _office_page(
        page=1, renamed=renamed_office, status_order=status_order
    )
    pages[asan.asan_office_list_url(2)] = _office_page(page=2)

    total = len(learning)
    last = (total + asan.ASAN_PAGE_SIZE - 1) // asan.ASAN_PAGE_SIZE
    for page in range(1, last + 1):
        start = (page - 1) * asan.ASAN_PAGE_SIZE
        pages[asan.asan_learning_list_url(page)] = _learning_page(
            learning[start : start + asan.ASAN_PAGE_SIZE], page=page, total=total
        )
    pages[asan.asan_learning_list_url(last + 1)] = _learning_page(
        learning[:1] if nonempty_learning_sentinel else [],
        page=last + 1,
        total=total,
    )

    media_total = len(media)
    media_last = max(
        1, (media_total + asan.ASAN_MEDIA_PAGE_SIZE - 1) // asan.ASAN_MEDIA_PAGE_SIZE
    )
    pages[asan.asan_media_list_url(1)] = _media_page(
        media, page=1, total=media_total
    )
    pages[asan.asan_media_list_url(media_last + 1)] = _media_page(
        [], page=media_last + 1, total=media_total
    )

    for record in (learning[0], learning[2]):
        pages[asan.asan_learning_detail_url(record["id"])] = _learning_detail(
            record,
            bad_title=(bad_learning_title and record is learning[0]),
            omit_control=(missing_learning_control and record is learning[0]),
        )
    pages[asan.asan_media_detail_url(media[0]["id"])] = _media_detail(
        media[0], bad_identity=bad_media_identity
    )

    calls: list[str] = []
    call_counts: Counter[str] = Counter()

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        call_counts[url] += 1
        if (
            changed_learning_recheck
            and url == asan.asan_learning_list_url(1)
            and call_counts[url] == 2
        ):
            changed = [dict(record) for record in learning[: asan.ASAN_PAGE_SIZE]]
            changed[0]["title"] = "재확인 중 변경된 제목"
            return _learning_page(changed, page=1, total=total)
        if url not in pages:
            raise AssertionError(f"unexpected GET {url}")
        return pages[url]

    fetch.calls = calls  # type: ignore[attr-defined]
    sessions: list[DummySession] = []

    def factory() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    return fetch, factory, sessions, learning, media


def test_exact_target_aliases_and_url_builders() -> None:
    assert asan.is_asan_education_target(AsanTarget())
    assert not asan.is_asan_education_target(
        AsanTarget(url=asan.ASAN_OFFICE_URL)
    )
    assert not asan.is_asan_education_target(
        AsanTarget(provider="OTHER")
    )
    assert asan.asan_learning_list_url(3).endswith("pageIndex=3&pageUnit=50")
    assert asan.asan_learning_detail_url("LEARNING_ABC_123").endswith(
        "lng_id=LEARNING_ABC_123"
    )
    assert not asan.asan_learning_detail_url("../../unsafe")
    assert asan.is_asan_ownership_alias_target({"url": asan.ASAN_LANDING_URL})
    assert asan.is_asan_ownership_alias_target({"url": asan.ASAN_OFFICE_URL})
    assert asan.is_asan_ownership_alias_target({"url": asan.ASAN_MEDIA_URL})
    assert asan.is_asan_excluded_non_course_target(
        {"url": asan.ASAN_EXCLUDED_NON_COURSE_URLS[-1]}
    )


def test_complete_snapshot_excludes_development_and_facility_and_redacts_pii(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch, factory, sessions, learning, media = _fixture(monkeypatch)
    rows, parser, meta = asan.collect_asan_courses(
        AsanTarget(),
        max_pages=30,
        detail_limit=20,
        fetcher=fetch,
        session_factory=factory,
        dedupe_rows=lambda values: values,
        today="2099-07-21",
        max_workers=3,
    )

    assert parser == asan.ASAN_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 5
    assert meta["visible_office_source_rows"] == 4
    assert meta["excluded_development_count"] == 1
    assert meta["excluded_facility_count"] == 1
    assert meta["eligible_learning_source_count"] == 3
    assert meta["expired_learning_count"] == 1
    assert meta["current_learning_count"] == 2
    assert meta["media_source_total"] == 1
    assert meta["current_media_count"] == 1
    assert meta["current_count"] == 3
    assert meta["returned_count"] == 3
    assert meta["detail_attempts"] == 3
    assert meta["detail_pages"] == 3
    assert meta["reservation_discovery_links"] == 2
    assert meta["duplicate_identity_count"] == 0
    assert meta["required_page_requests"] == 11
    assert {row["title"] for row in rows} == {
        learning[0]["title"],
        learning[2]["title"],
        media[0]["title"],
    }
    assert {row["municipality_code"] for row in rows} == {
        asan.ASAN_MUNICIPALITY_CODE
    }
    by_title = {row["title"]: row for row in rows}
    assert by_title[learning[0]["title"]]["status"] == "OPEN"
    assert by_title[learning[0]["title"]]["application_url"] == (
        asan.asan_learning_detail_url(learning[0]["id"])
    )
    assert by_title[learning[2]["title"]]["status"] == "SCHEDULED"
    assert not by_title[learning[2]["title"]]["application_url"]
    assert by_title[media[0]["title"]]["status"] == "OPEN"
    assert asan.asan_learning_detail_url(learning[1]["id"]) not in fetch.calls
    assert asan.asan_learning_detail_url(learning[3]["id"]) not in fetch.calls
    assert asan.asan_learning_detail_url(learning[4]["id"]) not in fetch.calls
    assert fetch.calls.count(asan.asan_learning_list_url(1)) == 2
    assert asan.asan_learning_list_url(4) in fetch.calls
    assert sessions and all(session.closed for session in sessions)
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "041-537" not in serialized
    assert "홍길동" not in serialized
    assert "강좌소개" not in serialized


def test_institution_status_badges_and_dynamic_order_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch, factory, sessions, _learning, _media = _fixture(
        monkeypatch, status_order=True
    )
    rows, _parser, meta = asan.collect_asan_courses(
        AsanTarget(),
        max_pages=30,
        detail_limit=20,
        fetcher=fetch,
        session_factory=factory,
        today="2099-07-21",
    )
    assert len(rows) == 3
    assert meta["snapshot_complete"] is True
    assert sessions and all(session.closed for session in sessions)


def test_exact_trailing_duplicate_status_is_normalized() -> None:
    learning, _media = _records()
    learning[0]["status_badges"] = ["교육중", "대기", "대기"]
    soup = asan.BeautifulSoup(
        _learning_page(learning[:1], page=1, total=1), "lxml"
    )
    rows, errors = asan._parse_learning_page(
        soup, page=1, cutoff=asan.date(2099, 7, 21)
    )
    assert errors == []
    assert rows[0]["raw_fields"]["source_status_values"] == ["교육중", "대기"]
    assert rows[0]["raw_fields"]["source_status_values_raw"] == [
        "교육중",
        "대기",
        "대기",
    ]
    assert rows[0]["raw_fields"]["trailing_duplicate_source_status"] is True
    assert rows[0]["status"] == "SCHEDULED"


def test_three_distinct_status_badges_fail_closed() -> None:
    learning, _media = _records()
    learning[0]["status_badges"] = ["교육중", "대기", "접수중"]
    soup = asan.BeautifulSoup(
        _learning_page(learning[:1], page=1, total=1), "lxml"
    )
    rows, errors = asan._parse_learning_page(
        soup, page=1, cutoff=asan.date(2099, 7, 21)
    )
    assert rows == []
    assert any("expected one or two source status badges" in error for error in errors)


@pytest.mark.parametrize(
    "fixture_kwargs,error_token",
    [
        ({"renamed_office": True}, "institution directory changed"),
        (
            {"nonempty_learning_sentinel": True},
            "immediate post-last page is not empty",
        ),
        ({"changed_learning_recheck": True}, "page-one recheck changed"),
        ({"duplicate_media_identity": True}, "duplicate source identities"),
    ],
)
def test_source_contract_failures_return_nothing(
    monkeypatch: pytest.MonkeyPatch,
    fixture_kwargs: dict[str, bool],
    error_token: str,
) -> None:
    fetch, factory, sessions, _learning, _media = _fixture(
        monkeypatch, **fixture_kwargs
    )
    rows, _parser, meta = asan.collect_asan_courses(
        AsanTarget(),
        max_pages=30,
        detail_limit=20,
        fetcher=fetch,
        session_factory=factory,
        today="2099-07-21",
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_token in meta["configured_collection_error"]
    assert sessions and all(session.closed for session in sessions)


@pytest.mark.parametrize(
    "fixture_kwargs,error_token",
    [
        ({"bad_learning_title": True}, "detail title"),
        ({"missing_learning_control": True}, "application-control mismatch"),
        ({"bad_media_identity": True}, "media detail identity mismatch"),
    ],
)
def test_detail_contract_failures_return_nothing(
    monkeypatch: pytest.MonkeyPatch,
    fixture_kwargs: dict[str, bool],
    error_token: str,
) -> None:
    fetch, factory, sessions, _learning, _media = _fixture(
        monkeypatch, **fixture_kwargs
    )
    rows, _parser, meta = asan.collect_asan_courses(
        AsanTarget(),
        max_pages=30,
        detail_limit=20,
        fetcher=fetch,
        session_factory=factory,
        today="2099-07-21",
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_token in meta["configured_collection_error"]
    assert meta["detail_errors"] >= 1
    assert sessions and all(session.closed for session in sessions)


def test_page_and_detail_caps_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch, factory, sessions, _learning, _media = _fixture(monkeypatch)
    rows, _parser, meta = asan.collect_asan_courses(
        AsanTarget(),
        max_pages=10,
        detail_limit=20,
        fetcher=fetch,
        session_factory=factory,
        today="2099-07-21",
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    fetch, factory, sessions, _learning, _media = _fixture(monkeypatch)
    rows, _parser, meta = asan.collect_asan_courses(
        AsanTarget(),
        max_pages=30,
        detail_limit=2,
        fetcher=fetch,
        session_factory=factory,
        today="2099-07-21",
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_wrong_target_fails_without_network() -> None:
    called = False

    def fetch(_session: Any, _url: str, _timeout: int) -> str:
        nonlocal called
        called = True
        raise AssertionError

    rows, _parser, meta = asan.collect_asan_courses(
        AsanTarget(provider="WRONG"), fetcher=fetch
    )
    assert rows == []
    assert called is False
    assert "exact Asan canonical" in meta["configured_collection_error"]
