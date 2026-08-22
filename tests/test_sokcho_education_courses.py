from __future__ import annotations

from dataclasses import dataclass
import json
import ssl
from typing import Any
from urllib.parse import urlparse

import pytest
import requests
from bs4 import BeautifulSoup

from Crawler import municipal_sokcho as sokcho


@dataclass
class SokchoTarget:
    provider: str = sokcho.SOKCHO_PROVIDER
    url: str = sokcho.SOKCHO_DISCOVERY_SHELL_URL
    branch: str = sokcho.SOKCHO_MUNICIPALITY_NAME


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeFetcher:
    def __init__(self, queues: dict[str, list[Any]]) -> None:
        self.queues = {url: list(values) for url, values in queues.items()}
        self.calls: list[str] = []

    def __call__(self, _session: Any, url: str, _timeout: int) -> Any:
        self.calls.append(url)
        values = self.queues.get(url)
        if not values:
            raise AssertionError(f"unexpected or exhausted URL {url}")
        return values.pop(0)


def _factory(sessions: list[DummySession]):
    def create() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    return create


def _tr(label: str, value: str) -> str:
    return f"<tr><th>{label}</th><td>{value}</td></tr>"


def _center_record(
    *,
    identity: str,
    title: str,
    sequence: int,
    category: str,
    query_name: str,
    query_value: str,
) -> dict[str, str | int]:
    return {
        "id": identity,
        "title": title,
        "sequence": sequence,
        "category": category,
        "query_name": query_name,
        "query_value": query_value,
        "period": "2099-08-01 ~ 2099-12-01",
        "apply": "2099-07-20 09시~2099-07-24 18시",
        "capacity": "15 / 3",
        "room": "3층 강의실",
    }


def _center_item(record: dict[str, Any]) -> str:
    identity_name = "lco_idx" if record["query_name"] == "lco_type" else "lc_idx"
    href = (
        "reserve_write.php?"
        f"{identity_name}={record['id']}&{record['query_name']}={record['query_value']}"
    )
    rows = "".join(
        [
            _tr("년도", "2099"),
            _tr("강의기간", record["period"]),
            _tr("기수", "하반기"),
            _tr("강의시간", "[월요일]10:00~12:00"),
            _tr("접수기간", record["apply"]),
            _tr("정원/신청", record["capacity"]),
            _tr("선발기준", "선착순"),
            _tr("강의구분", record["category"]),
            _tr("강의장소", record["room"]),
            _tr("납부기간", "2099-07-25 09시~2099-07-26 18시"),
            _tr("모집제한", "없음"),
            _tr("수강료", "40,000"),
            _tr("강사명", "홍길동"),
            _tr("강의내용", "연락 033-635-2525 staff@example.invalid"),
        ]
    )
    return f"""
      <div class="list_div" id="list_{record['sequence']}">
        <ul class="list_div_title">
          <li class="title">{record['sequence']}. {record['title']}</li>
          <li class="but"><a href="{href}">수강신청</a></li>
        </ul>
        <table summary="강의주요정보테이블">{rows}</table>
      </div>
    """


def _center_page(
    partition: sokcho.SokchoCenterPartition,
    records: list[dict[str, Any]],
    *,
    page: int,
    last: int = 1,
    sentinel: bool = False,
) -> str:
    links = "".join(
        f'<a href="/lecture/class_list.php?{partition.query_name}={partition.query_value}&page={number}">{number} 페이지</a>'
        for number in range(1, last + 1)
        if sentinel or number != page
    )
    current = "" if sentinel else f'<strong class="pg_current">{page}</strong>'
    nav = f'<nav class="pg_wrap"><span class="pg">{links}{current}</span></nav>'
    return f"""
      <html><head><title>강의목록 | 속초평생교육문화센터</title></head><body>
      <form><input name="{partition.query_name}" value="{partition.query_value}"></form>
      {''.join(_center_item(record) for record in records)}
      {nav}
      </body></html>
    """


def _center_auth(
    partition: sokcho.SokchoCenterPartition, identity: str, requested_url: str
) -> sokcho.FetchedPage:
    query = {
        "mode": "write",
        partition.query_name: partition.query_value,
        partition.identity_name: identity,
    }
    if partition.identity_name == "lc_idx":
        query["lco_idx"] = "0"
    final_url = "https://edu.sokcho.go.kr/sci/pcc_seed.php?" + "&".join(
        f"{key}={value}" for key, value in query.items()
    )
    html = f"""
      <html><head><title>본인인증 | 속초평생교육문화센터</title></head><body>
      본인인증 강좌신청
      <form action="/lecture/reserve_write.php" method="get">
        <input name="{partition.query_name}" value="{partition.query_value}">
        <input name="{partition.identity_name}" value="{identity}">
      </form></body></html>
    """
    return sokcho.FetchedPage(
        BeautifulSoup(html, "lxml"), requested_url, final_url, True
    )


def _library_record(
    *,
    identity: str,
    title: str,
    status: str,
    capacity: str = "1 / 10 (대기자 : 0 / 5)",
    period: str = "2099-08-01 ~ 2099-09-01",
    apply: str = "2099-07-20 10:00 ~ 2099-07-24 18:00",
) -> dict[str, str]:
    return {
        "id": identity,
        "title": title,
        "status": status,
        "capacity": capacity,
        "period": period,
        "apply": apply,
        "room": "문화강좌실",
        "target": "초등학생",
    }


def _library_control(source: sokcho.SokchoLectureSource, record: dict[str, str]) -> str:
    status = record["status"]
    identity = record["id"]
    if status == "OPEN":
        if source.layout == "gwe":
            return f'<button class="applyStatusButton" data-event-id="{identity}">접수중</button>'
        return f'<button class="applyButton" data-event-id="{identity}">신청</button>'
    if status == "WAITLIST":
        return f'<button class="reserveApplyButton" data-event-id="{identity}">대기자신청</button>'
    if status == "SCHEDULED":
        return '<button class="btn--disabled">접수예정</button>'
    return ""


def _dl_pairs(record: dict[str, str], source: sokcho.SokchoLectureSource) -> str:
    course_label = "운영기간" if source.layout == "gwe" else "강의기간"
    apply_label = "신청기간" if source.layout == "gwe" else "접수기간"
    return f"""
      <dl>
        <dt>{course_label}</dt><dd>{record['period']}</dd>
        <dt>{apply_label}</dt><dd>{record['apply']}</dd>
        <dt>모집인원</dt><dd>{record['capacity']}</dd>
        <dt>장소</dt><dd>{record['room']}</dd>
        <dt>{'신청대상' if source.layout == 'gwe' else '참가대상'}</dt><dd>{record['target']}</dd>
        <dt>{'신청방법' if source.layout == 'gwe' else '모집방법'}</dt><dd>선착순</dd>
        <dt>참가비</dt><dd>0</dd>
        <dt>강사명</dt><dd>개인강사</dd>
      </dl>
    """


def _library_item(
    source: sokcho.SokchoLectureSource, record: dict[str, str]
) -> str:
    base_path = urlparse(source.list_url).path.rsplit("/list/all", 1)[0]
    href = f"{base_path}/{record['id']}"
    pairs = _dl_pairs(record, source)
    control = _library_control(source, record)
    if source.layout == "city":
        return f"""
          <article class="list_box"><h4 class="title">
            <a href="{href}"></a><span class="main_title">{record['title']}</span>
          </h4>{pairs}<div class="btn_wrap">{control}</div></article>
        """
    if source.layout == "english":
        return f"""
          <article class="lecture-item"><div class="lecture-item__info">
            <h3 class="lecture-item__title"><a href="{href}">{record['title']}</a></h3>
            {pairs}{control}</div></article>
        """
    return f"""
      <article class="lecture_item"><span class="lecture_item__library">{source.branch}</span>
        <h4 class="lecture_item__title"><a href="{href}">{record['title']}</a></h4>
        {pairs}<div class="lecture_item__button">{control}
          <button class="registrationCheckButton" data-event-id="{record['id']}">등록확인</button>
        </div></article>
    """


def _library_page(
    source: sokcho.SokchoLectureSource,
    records: list[dict[str, str]],
    *,
    total: int,
    index: int,
    sentinel: bool = False,
) -> str:
    expected_title = {
        "city": "속초시립도서관",
        "english": "어린이영어도서관",
        "gwe": "프로그램신청",
    }[source.layout]
    if source.layout == "gwe":
        marker = f"<div class='total'>전체 {total} 건</div>"
    elif sentinel:
        marker = ""
    else:
        last = max(1, (total + sokcho.SOKCHO_LECTURE_PAGE_SIZE - 1) // sokcho.SOKCHO_LECTURE_PAGE_SIZE)
        marker = f"<div class='count'>총 {total} 건 ( {index + 1} /{last} PAGE)</div>"
    return f"""
      <html><head><title>{expected_title}</title></head><body>
      {marker}{''.join(_library_item(source, record) for record in records)}
      </body></html>
    """


def _detail_control(source: sokcho.SokchoLectureSource, record: dict[str, str]) -> str:
    identity = record["id"]
    status = record["status"]
    if status == "OPEN":
        label = "신청"
        return f'<button id="applyButton" data-event-id="{identity}">{label}</button>'
    if status == "WAITLIST":
        return f'<button id="reserveApplyButton" data-event-id="{identity}">대기자신청</button>'
    if status == "SCHEDULED":
        return "<button class='btn'>접수예정</button>"
    return ""


def _library_detail(
    source: sokcho.SokchoLectureSource,
    record: dict[str, str],
    *,
    bad_title: bool = False,
    missing_control: bool = False,
) -> str:
    title = "변경된 강좌명" if bad_title else record["title"]
    pairs = _dl_pairs(record, source)
    if source.layout == "gwe":
        pairs += f"<dl><dt>도서관</dt><dd>{source.branch}</dd></dl>"
    control = "" if missing_control else _detail_control(source, record)
    description = "<p>담당자 033-111-2222 private@example.invalid</p>"
    if source.layout == "city":
        return f"""
          <html><head><title>{source.branch}</title></head><body><section class="section">
          <h4 class="title"><span class="main_title">{title}</span></h4>
          {pairs}{description}{control}</section></body></html>
        """
    if source.layout == "english":
        return f"""
          <html><head><title>{source.branch}</title></head><body><div class="content-area">
          <h3 class="lecture-detail-title">{title}</h3>{pairs}{description}{control}
          </div></body></html>
        """
    return f"""
      <html><head><title>프로그램신청</title></head><body>
      <div class="lecture_detail"><h4 class="lecture_detail__title">{title}</h4>
      {pairs}{description}{control}</div></body></html>
    """


def _fixture(
    *,
    nonempty_center_sentinel: bool = False,
    changed_center_recheck: bool = False,
    changed_city_recheck: bool = False,
    duplicate_city_identity: bool = False,
    bad_center_application_identity: bool = False,
    bad_library_detail_title: bool = False,
    missing_library_detail_control: bool = False,
) -> tuple[FakeFetcher, Any, list[DummySession], dict[str, Any]]:
    queues: dict[str, list[Any]] = {}
    records: dict[str, Any] = {}

    for partition in sokcho.SOKCHO_CENTER_PARTITIONS:
        if partition.code == "center_day":
            page_records = [
                _center_record(
                    identity="100",
                    title="주간 테스트 강좌",
                    sequence=1,
                    category=partition.name,
                    query_name=partition.query_name,
                    query_value=partition.query_value,
                )
            ]
        elif partition.code == "center_night":
            page_records = [
                _center_record(
                    identity="200",
                    title="야간 테스트 강좌",
                    sequence=1,
                    category=partition.name,
                    query_name=partition.query_name,
                    query_value=partition.query_value,
                )
            ]
        else:
            page_records = []
        records[partition.code] = page_records
        first = _center_page(partition, page_records, page=1)
        recheck_records = [dict(record) for record in page_records]
        if changed_center_recheck and partition.code == "center_day":
            recheck_records[0]["title"] = "재확인에서 변경된 강좌"
        recheck = _center_page(partition, recheck_records, page=1)
        queues[partition.list_url(1)] = [first, recheck]
        sentinel_records: list[dict[str, Any]] = []
        if nonempty_center_sentinel and partition.code == "center_day":
            sentinel_records = [
                _center_record(
                    identity="999",
                    title="센티널 침범",
                    sequence=16,
                    category=partition.name,
                    query_name=partition.query_name,
                    query_value=partition.query_value,
                )
            ]
        queues[partition.list_url(2)] = [
            _center_page(
                partition, sentinel_records, page=2, sentinel=True
            )
        ]

    city = sokcho._LECTURE_BY_CODE["city_library"]
    english = sokcho._LECTURE_BY_CODE["english_library"]
    gwe = sokcho._LECTURE_BY_CODE["gwe_education_library"]
    gwe_empty = sokcho._LECTURE_BY_CODE["gwe_education_culture_center"]
    library_records: dict[str, list[dict[str, str]]] = {
        city.code: [
            _library_record(identity="300", title="시립 열린 강좌", status="OPEN"),
            _library_record(
                identity="301",
                title="시립 정원마감 강좌",
                status="FULL",
                capacity="10 / 10 (대기자 : 5 / 5)",
            ),
        ],
        english.code: [
            _library_record(
                identity="350",
                title="영어 예정 강좌",
                status="SCHEDULED",
                apply="2099-07-22 10:00 ~ 2099-07-24 18:00",
            )
        ],
        gwe.code: [
            _library_record(identity="400", title="교육청 열린 강좌", status="OPEN"),
            _library_record(
                identity="401",
                title=sokcho.SOKCHO_EXCLUDED_PRACTICE_TITLES[0],
                status="OPEN",
            ),
        ],
        gwe_empty.code: [],
    }
    if duplicate_city_identity:
        library_records[city.code][1]["id"] = library_records[city.code][0]["id"]
    records.update(library_records)
    for source in sokcho.SOKCHO_LECTURE_SOURCES:
        source_records = library_records[source.code]
        total = len(source_records)
        first = _library_page(source, source_records, total=total, index=0)
        recheck_records = [dict(record) for record in source_records]
        if changed_city_recheck and source.code == city.code:
            recheck_records[0]["title"] = "재확인 변경"
        recheck = _library_page(source, recheck_records, total=total, index=0)
        queues[source.page_url(0)] = [first, recheck]
        queues[source.page_url(1)] = [
            _library_page(source, [], total=total, index=1, sentinel=True)
        ]

    for partition in sokcho.SOKCHO_CENTER_PARTITIONS:
        for record in records[partition.code]:
            identity, raw_url = sokcho._center_application_identity(
                partition,
                f"reserve_write.php?{partition.identity_name}={record['id']}&{partition.query_name}={partition.query_value}",
            )
            auth_identity = (
                "9999"
                if bad_center_application_identity and partition.code == "center_day"
                else identity
            )
            queues[raw_url] = [
                _center_auth(partition, auth_identity, raw_url)
            ]

    for source in sokcho.SOKCHO_LECTURE_SOURCES:
        for record in library_records[source.code]:
            if record["title"] in sokcho.SOKCHO_EXCLUDED_PRACTICE_TITLES:
                continue
            path = urlparse(source.list_url).path.rsplit("/list/all", 1)[0]
            detail_url = f"https://{source.host}{path}/{record['id']}"
            queues[detail_url] = [
                _library_detail(
                    source,
                    record,
                    bad_title=(
                        bad_library_detail_title
                        and source.code == city.code
                        and record["id"] == "300"
                    ),
                    missing_control=(
                        missing_library_detail_control
                        and source.code == city.code
                        and record["id"] == "300"
                    ),
                )
            ]

    sessions: list[DummySession] = []
    fetcher = FakeFetcher(queues)
    return fetcher, _factory(sessions), sessions, records


def _collect(fetcher: FakeFetcher, factory: Any, **kwargs: Any):
    return sokcho.collect_sokcho_education_courses(
        SokchoTarget(),
        max_pages=50,
        detail_limit=50,
        fetcher=fetcher,
        session_factory=factory,
        dedupe_rows=lambda rows: rows,
        today="2099-07-21",
        max_workers=4,
        **kwargs,
    )


def test_complete_snapshot_covers_five_sources_and_excludes_practice_pii() -> None:
    fetcher, factory, sessions, records = _fixture()
    rows, parser, meta = _collect(fetcher, factory)

    assert parser == sokcho.SOKCHO_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 7
    assert meta["source_rows"] == 7
    assert meta["excluded_non_course_count"] == 1
    assert meta["current_count"] == 6
    assert meta["returned_count"] == 6
    assert meta["required_page_requests"] == 21
    assert meta["detail_attempts"] == 6
    assert meta["detail_pages"] == 6
    assert meta["reservation_discovery_links"] == 4
    assert meta["branch_counts"] == {
        sokcho.SOKCHO_CENTER_BRANCH: 2,
        "속초시립도서관": 2,
        "어린이영어도서관": 1,
        "속초교육도서관": 1,
    }
    assert meta["lecture_source_totals"] == {
        "city_library": 2,
        "english_library": 1,
        "gwe_education_library": 2,
        "gwe_education_culture_center": 0,
    }
    assert sokcho.SOKCHO_EXCLUDED_PRACTICE_TITLES[0] not in {
        row["title"] for row in rows
    }
    practice_id = records["gwe_education_library"][1]["id"]
    assert not any(
        f"/lecture-event/{practice_id}" in url and "list/" not in url
        for url in fetcher.calls
    )
    assert fetcher.calls.count(sokcho._CENTER_BY_CODE["center_day"].list_url(1)) == 2
    assert fetcher.calls.count(sokcho._LECTURE_BY_CODE["city_library"].page_url(0)) == 2
    by_title = {row["title"]: row for row in rows}
    assert by_title["시립 열린 강좌"]["status"] == "OPEN"
    assert by_title["시립 열린 강좌"]["application_url"]
    assert by_title["시립 정원마감 강좌"]["status"] == "FULL"
    assert not by_title["시립 정원마감 강좌"]["application_url"]
    assert by_title["영어 예정 강좌"]["status"] == "SCHEDULED"
    assert not by_title["영어 예정 강좌"]["application_url"]
    assert {row["municipality_code"] for row in rows} == {
        sokcho.SOKCHO_MUNICIPALITY_CODE
    }
    assert all(row["target"] for row in rows)
    assert all(row["fee"] for row in rows)
    assert all(row["schedule_raw"] for row in rows)
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "홍길동" not in serialized
    assert "개인강사" not in serialized
    assert "033-" not in serialized
    assert "example.invalid" not in serialized
    assert "description" not in serialized
    assert sessions and all(session.closed for session in sessions)


@pytest.mark.parametrize(
    "fixture_kwargs,error_token",
    [
        ({"nonempty_center_sentinel": True}, "immediate post-last page is not empty"),
        ({"changed_center_recheck": True}, "page-one recheck changed"),
        ({"changed_city_recheck": True}, "page-one recheck changed"),
        ({"duplicate_city_identity": True}, "duplicate source identities"),
    ],
)
def test_source_contract_failures_return_nothing(
    fixture_kwargs: dict[str, bool], error_token: str
) -> None:
    fetcher, factory, sessions, _records = _fixture(**fixture_kwargs)
    rows, _parser, meta = _collect(fetcher, factory)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_token in meta["configured_collection_error"]
    assert sessions and all(session.closed for session in sessions)


@pytest.mark.parametrize(
    "fixture_kwargs,error_token",
    [
        ({"bad_center_application_identity": True}, "application final identity mismatch"),
        ({"bad_library_detail_title": True}, "detail title mismatch"),
        (
            {"missing_library_detail_control": True},
            "application state/control is ambiguous",
        ),
    ],
)
def test_detail_and_application_contract_failures_return_nothing(
    fixture_kwargs: dict[str, bool], error_token: str
) -> None:
    fetcher, factory, sessions, _records = _fixture(**fixture_kwargs)
    rows, _parser, meta = _collect(fetcher, factory)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_errors"] >= 1
    assert error_token in meta["configured_collection_error"]
    assert sessions and all(session.closed for session in sessions)


def test_page_and_detail_caps_fail_closed() -> None:
    fetcher, factory, sessions, _records = _fixture()
    rows, _parser, meta = sokcho.collect_sokcho_education_courses(
        SokchoTarget(),
        max_pages=20,
        detail_limit=50,
        fetcher=fetcher,
        session_factory=factory,
        today="2099-07-21",
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]
    assert sessions and all(session.closed for session in sessions)

    fetcher, factory, sessions, _records = _fixture()
    rows, _parser, meta = sokcho.collect_sokcho_education_courses(
        SokchoTarget(),
        max_pages=50,
        detail_limit=5,
        fetcher=fetcher,
        session_factory=factory,
        today="2099-07-21",
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]
    assert sessions and all(session.closed for session in sessions)


@pytest.mark.parametrize(
    ("label", "expected_status"),
    [("신청마감", "CLOSED"), ("신청인원초과", "FULL")],
)
def test_center_terminal_control_uses_semantic_identity_without_fetch(
    label: str,
    expected_status: str,
) -> None:
    partition = sokcho.SOKCHO_CENTER_PARTITIONS[0]
    record = _center_record(
        identity="1001",
        title="종료 제어 강좌",
        sequence=1,
        category=partition.name,
        query_name=partition.query_name,
        query_value=partition.query_value,
    )
    soup = BeautifulSoup(
        _center_page(partition, [record], page=1),
        "lxml",
    )
    item = soup.select_one(".list_div")
    action = item.select_one(".list_div_title .but a")
    action["href"] = "#"
    action["title"] = f"{label} 가기"
    action.string = label

    row, errors = sokcho._center_row(  # noqa: SLF001
        partition,
        item,
        page=1,
        cutoff=sokcho.date(2099, 7, 22),
    )

    assert errors == []
    assert row is not None
    assert row["status"] == expected_status
    assert row["raw_url"].endswith("#list_1")
    assert row["raw_fields"]["identity"].startswith("semantic-")
    assert row["raw_fields"]["application_identity"] == ""
    assert row["raw_fields"]["detail_verified"] is True
    assert row["raw_fields"]["application_control_verified"] is True


def test_city_library_exact_empty_ledger_has_zero_total() -> None:
    source = sokcho.SOKCHO_LECTURE_SOURCES[0]
    soup = BeautifulSoup(
        """
        <html><head><title>속초시립도서관</title></head><body>
          <div class="result_wrap">
            <div class="list_box_wrap culture">
              <p class="no_result">조회되는 문화강좌가 없습니다.</p>
            </div>
          </div>
        </body></html>
        """,
        "lxml",
    )

    assert sokcho._lecture_total(source, soup, index=0) == (0, 1, 1)  # noqa: SLF001
    soup.select_one("p.no_result").string = "일시 오류"
    with pytest.raises(ValueError, match="declared total"):
        sokcho._lecture_total(source, soup, index=0)  # noqa: SLF001


def test_target_ownership_tls_and_exclusions_are_explicit() -> None:
    assert sokcho.is_sokcho_education_target(SokchoTarget())
    assert sokcho.is_sokcho_education_target(
        SokchoTarget(url=sokcho.SOKCHO_CANONICAL_URL)
    )
    assert not sokcho.is_sokcho_education_target(
        SokchoTarget(provider="MUNI_WRONG")
    )
    assert not sokcho.is_sokcho_education_target(
        SokchoTarget(url="https://edu.sokcho.go.kr/bbs/board.php?bo_table=notice")
    )
    context = sokcho._sokcho_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    configured = sokcho.configure_sokcho_verified_session(requests.Session())
    adapter = configured.get_adapter("https://edu.sokcho.go.kr/")
    assert adapter._context.verify_mode == ssl.CERT_REQUIRED
    assert adapter._context.check_hostname is True
    configured.close()
    assert len(sokcho.SOKCHO_SECTIGO_INTERMEDIATE_SHA256) == 64
    assert "BEGIN CERTIFICATE" in sokcho.SOKCHO_SECTIGO_INTERMEDIATE_PEM
    assert "MUNI_LIB_GWE_GO_KR_6A5F40FB" in (
        sokcho.SOKCHO_SUPERSEDED_PROVIDER_CANDIDATES
    )
    assert any("field_trip" in url for url in sokcho.SOKCHO_EXCLUDED_NON_COURSE_URLS)


def test_wrong_target_returns_without_network() -> None:
    called = False

    def fetcher(_session: Any, _url: str, _timeout: int) -> Any:
        nonlocal called
        called = True
        raise AssertionError("must not fetch")

    rows, parser, meta = sokcho.collect_sokcho_education_courses(
        SokchoTarget(provider="WRONG"), fetcher=fetcher
    )
    assert rows == []
    assert parser == sokcho.SOKCHO_PARSER
    assert called is False
    assert "not the exact Sokcho" in meta["configured_collection_error"]
