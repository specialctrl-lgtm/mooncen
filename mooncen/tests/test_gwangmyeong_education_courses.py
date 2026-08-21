from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import pytest
import requests

from Crawler import municipal_gwangmyeong as gm


@dataclass
class Target:
    provider: str = gm.GWANGMYEONG_PROVIDER
    url: str = gm.GWANGMYEONG_CANONICAL_URL
    branch: str = gm.GWANGMYEONG_MUNICIPALITY_NAME


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(self, url: str, text: str, status_code: int = 200) -> None:
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.history: list[Any] = []


def _total(total: int, page: int, last: int) -> str:
    return f"<div class='count'>총 {total}건 ( {page}/{last}페이지)</div>"


def _office_page(
    offices: tuple[gm.GwangmyeongOffice, ...],
    *,
    page: int,
    declared_total: int,
) -> str:
    cards = ""
    if page == 1:
        for index, office in enumerate(offices):
            cards += f"""
              <li class='green'><a href='javascript:;'
                onclick="fn_learning_list('{office.code}'); return false;">
                <strong>{office.name}</strong></a>
                <input class='check_arr' value='{office.code}'></li>
            """
    return (
        "<html><body>"
        + _total(declared_total, page, 1)
        + f"<ul class='e_lst_type01'>{cards}</ul></body></html>"
    )


def _headings() -> str:
    return (
        "<thead><tr><th>번호</th><th>강좌명/교육기관</th><th>강좌유형</th>"
        "<th>교육기간</th><th>신청기간 / 접수인원</th><th>상태</th><th>보기</th>"
        "</tr></thead>"
    )


def _learning_row(record: dict[str, Any]) -> str:
    if record["kind"] == "internal":
        action = f"fn_learning_detail('{record['identity']}'); return false;"
    elif record["kind"] == "external":
        action = f"fn_learning_ex_detail('{record['url']}'); return false;"
    else:
        action = "siblingDomainRedirect('/index.do?menu_id=00005120');"
    return f"""
      <tr>
        <td>{record['sequence']}</td>
        <td class='subject'><a href='javascript:;' onclick="{action}">
          <span class='tit'>{record['title']}</span>
          <span class='org'>{record['office']}</span></a></td>
        <td>{record.get('type', '오프라인 강좌')}</td>
        <td>교육기간 {record['period']}<pre>{record.get('schedule', '월 10:00 ~ 12:00')}</pre></td>
        <td>일반 인터넷접수 {record.get('apply_period', '2099.07.01 ~ 2099.07.31')} / 10명</td>
        <td>{record.get('source_status', '접수중 교육대기')}</td>
        <td>수강정보</td>
      </tr>
    """


def _learning_page(
    records: list[dict[str, Any]], *, page: int, total: int, page_size: int
) -> str:
    last = max(1, (total + page_size - 1) // page_size)
    body = "".join(_learning_row(record) for record in records)
    if not records and page > last:
        body = "<tr><td colspan='7'>등록된 강좌가 없습니다.</td></tr>"
    return (
        "<html><body>"
        + _total(total, page, last)
        + "<table class='lecture'>"
        + _headings()
        + f"<tbody>{body}</tbody></table></body></html>"
    )


def _media_page(*, page: int, activated: bool = False) -> str:
    card = (
        "<a onclick=\"fn_detail('LEARNING_MEDIA_1')\"><span class='tit'>영상</span></a>"
        if activated
        else ""
    )
    total = 1 if activated else 0
    return f"<html><body>{_total(total, page, 1)}{card}</body></html>"


def _gmcc_expired_page(category: str) -> str:
    return f"""
      <html><body>
        <a href='/product_new/item.php?it_id=EXPIRED_{category}&amp;ca_id={category}'>
          종료 문화강좌 {category} 접수기간 2000.01.01 ~ 2000.01.02
          강좌기간 2000.02.01 ~ 2000.02.28 수업시간 월 10:00 강사명 홍길동
          수강신청 마감
        </a>
      </body></html>
    """


def _gmlib_tombstone_page() -> str:
    return """
      <html><head><title>알림 페이지</title>
        <script>alert("등록된 강좌가 없습니다.");</script>
        <script>history.go(-1)</script>
      </head><body><a>소하도서관</a></body></html>
    """


def _internal_detail(
    record: dict[str, Any],
    *,
    bad_title: bool = False,
    location: str = "",
) -> str:
    title = "다른 강좌" if bad_title else record["title"]
    location_field = (
        f"<dl><dt>교육장소</dt><dd>{location}</dd></dl>" if location else ""
    )
    return f"""
      <html><body><form id='learningVO'>
        <input name='lng_id' value='{record['identity']}'>
        <h2 class='enrolTit'>[{record['office']}] {title}</h2>
        <dl><dt>교육기간</dt><dd>{record['period']}</dd></dl>
        {location_field}
        <a id='learning_aply_btn' onclick='fn_learning_apply(); return false;'>수강신청하기</a>
      </form></body></html>
    """


def _external_detail(record: dict[str, Any], *, bad_title: bool = False) -> str:
    title = "다른 문화행사" if bad_title else record["title"]
    return f"""
      <html><body><main><h2>{title}</h2><p>소하도서관</p><p>{record['period']}</p>
        <button onclick="location.href='login?leCode=100'">LOGIN</button>
      </main></body></html>
    """


def _civic_detail(record: dict[str, Any]) -> str:
    return f"""
      <html><body><main><h2>광명자치대학 입학신청</h2>
        <p>2099년 광명자치대학</p>
        <p>{record['apply_period'].replace('.', '-')}</p>
        <a>신청마감</a>
      </main></body></html>
    """


@pytest.fixture
def complete_source(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    offices = (
        gm.GwangmyeongOffice("OFFICE_A", "광명시평생학습원"),
        gm.GwangmyeongOffice("API_OFFICE_B", "광명시도서관"),
    )
    monkeypatch.setattr(gm, "GWANGMYEONG_PAGE_SIZE", 2)
    monkeypatch.setattr(gm, "GWANGMYEONG_EXPECTED_OFFICES", offices)
    monkeypatch.setattr(gm, "GWANGMYEONG_OFFICE_BY_CODE", {x.code: x for x in offices})
    monkeypatch.setattr(gm, "GWANGMYEONG_OFFICE_BY_NAME", {x.name: x for x in offices})
    monkeypatch.setattr(gm, "GWANGMYEONG_OFFICE_DECLARED_TOTAL", 3)
    monkeypatch.setattr(gm, "GWANGMYEONG_KNOWN_REVERSED_PERIOD_SEQUENCES", frozenset())

    external_url = (
        "https://gmlib.gm.go.kr/front/index.php?g_page=event&"
        "m_page=event14&act=lecture_view&leCode=100&siteCode=ST04"
    )
    records = [
        {
            "sequence": 8,
            "identity": "LEARNING_OPEN_8",
            "kind": "internal",
            "title": "공개 교육 강좌",
            "office": offices[0].name,
            "period": "2099.07.21 ~ 2099.08.31",
        },
        {
            "sequence": 7,
            "identity": "LEARNING_FACILITY_7",
            "kind": "internal",
            "title": "체력단련실 3분기",
            "office": offices[0].name,
            "period": "2099.07.01 ~ 2099.09.30",
        },
        {
            "sequence": 6,
            "identity": "LEARNING_EXPERIENCE_6",
            "kind": "internal",
            "title": "농장 체험 프로그램",
            "office": offices[0].name,
            "period": "2099.07.01 ~ 2099.09.30",
        },
        {
            "sequence": 5,
            "identity": "LEARNING_PERFORMANCE_5",
            "kind": "internal",
            "title": "[오픈시네마] 영화",
            "office": offices[0].name,
            "period": "2099.07.01 ~ 2099.09.30",
        },
        {
            "sequence": 4,
            "identity": "LEARNING_EXPIRED_4",
            "kind": "internal",
            "title": "종료 교육",
            "office": offices[0].name,
            "period": "2000.01.01 ~ 2000.02.01",
            "source_status": "교육완료 접수마감",
        },
        {
            "sequence": 3,
            "identity": "LEARNING_TEST_3",
            "kind": "internal",
            "title": "테스트 강좌입니다",
            "office": gm.GWANGMYEONG_HIDDEN_TEST_OFFICE_NAME,
            "period": "2099.07.01 ~ 2099.09.30",
        },
        {
            "sequence": 2,
            "identity": "external:2",
            "kind": "external",
            "url": external_url,
            "title": "도서관 글쓰기 교육",
            "office": offices[1].name,
            "type": "",
            "period": "2099.07.22 ~ 2099.08.22",
        },
        {
            "sequence": 1,
            "identity": "civic:1",
            "kind": "civic",
            "title": "2099학년도 광명자치대학 모집",
            "office": offices[0].name,
            "period": "2099.07.01 ~ 2099.10.29",
            "apply_period": "2099.07.01 ~ 2099.07.31",
            "source_status": "선발식 마감",
        },
    ]
    mapping: dict[str, str] = {}
    for page in range(1, 5):
        chunk = records[(page - 1) * 2 : page * 2]
        mapping[gm.gwangmyeong_learning_list_url(page)] = _learning_page(
            chunk, page=page, total=len(records), page_size=2
        )
    mapping[gm.gwangmyeong_learning_list_url(5)] = _learning_page(
        [], page=5, total=len(records), page_size=2
    )
    mapping[gm.gwangmyeong_office_list_url(1)] = _office_page(
        offices, page=1, declared_total=3
    )
    mapping[gm.gwangmyeong_office_list_url(2)] = _office_page(
        offices, page=2, declared_total=3
    )
    mapping[gm.gwangmyeong_media_list_url(1)] = _media_page(page=1)
    mapping[gm.gwangmyeong_media_list_url(2)] = _media_page(page=2)
    for category in gm.GWANGMYEONG_GMCC_CATEGORIES:
        mapping[gm.gwangmyeong_gmcc_list_url(category, 1)] = (
            _gmcc_expired_page(category)
        )
        mapping[gm.gwangmyeong_gmcc_list_url(category, 2)] = (
            "<html><body>등록된 강좌가 없습니다.</body></html>"
        )
    mapping[gm.gwangmyeong_learning_detail_url("LEARNING_OPEN_8")] = _internal_detail(records[0])
    mapping[gm.gwangmyeong_learning_detail_url("LEARNING_EXPERIENCE_6")] = (
        _internal_detail(
            records[2],
            location="광명농장 경기 광명시 가학로85번길 142",
        )
    )
    mapping[external_url] = _external_detail(records[6])
    mapping[gm.GWANGMYEONG_CIVIC_UNIVERSITY_URL] = _civic_detail(records[7])

    calls: list[str] = []

    def fetcher(_session: Any, url: str, _timeout: int) -> FakeResponse:
        calls.append(url)
        if url not in mapping:
            raise AssertionError(f"unexpected URL {url}")
        return FakeResponse(url, mapping[url])

    return {
        "offices": offices,
        "records": records,
        "mapping": mapping,
        "calls": calls,
        "fetcher": fetcher,
    }


def _collect(source: dict[str, Any], **overrides: Any):
    values = {
        "today": "2099-07-21",
        "max_pages": 30,
        "detail_limit": 10,
        "fetcher": source["fetcher"],
        "session_factory": DummySession,
        "max_workers": 3,
    }
    values.update(overrides)
    return gm.collect_gwangmyeong_education_courses(Target(), **values)


def test_target_and_url_contract_is_strict() -> None:
    assert gm.is_gwangmyeong_education_target(Target())
    assert not gm.is_gwangmyeong_education_target(
        Target(url=gm.GWANGMYEONG_LANDING_URL)
    )
    assert not gm.is_gwangmyeong_education_target(
        Target(provider="ANOTHER_PROVIDER")
    )
    assert gm.is_gwangmyeong_ownership_alias_target(
        Target(url=gm.GWANGMYEONG_LANDING_URL)
    )
    assert all(
        gm.is_gwangmyeong_ownership_alias_target(Target(url=url))
        for url in gm.GWANGMYEONG_GMCC_CATEGORY_URLS
    )
    assert "pageIndex=2" in gm.gwangmyeong_learning_list_url(2)
    assert not gm.gwangmyeong_learning_list_url(0)
    assert gm.gwangmyeong_learning_detail_url("LEARNING_ABC")
    assert not gm.gwangmyeong_learning_detail_url("../bad")


@pytest.mark.parametrize("record_index", [0, 6, 7])
def test_learning_identity_does_not_depend_on_list_sequence(
    complete_source: dict[str, Any], record_index: int
) -> None:
    record = dict(complete_source["records"][record_index])
    first, first_errors = gm._parse_learning_page(
        gm.BeautifulSoup(
            _learning_page([record], page=1, total=1, page_size=1),
            "html.parser",
        ),
        page=1,
    )
    shifted = {**record, "sequence": record["sequence"] + 1000}
    second, second_errors = gm._parse_learning_page(
        gm.BeautifulSoup(
            _learning_page([shifted], page=1, total=1, page_size=1),
            "html.parser",
        ),
        page=1,
    )

    assert not first_errors
    assert not second_errors
    assert first[0]["provider_course_id"] == second[0]["provider_course_id"]
    assert (
        first[0]["raw_fields"]["legacy_provider_course_id"]
        != second[0]["raw_fields"]["legacy_provider_course_id"]
    )


def test_shared_gmcc_external_catalogue_url_keeps_distinct_course_ids() -> None:
    values = {
        "action_kind": "external",
        "identity": "external:1",
        "raw_url": f"{gm.GWANGMYEONG_GMCC_LIST_URL}?ca_id=01",
        "office": "광명문화원",
        "start": gm.date(2099, 6, 1),
        "end": gm.date(2099, 8, 31),
    }
    first = gm._course_id(title="서예와 사군자", **values)
    second = gm._course_id(title="어린이 치어리딩", **values)

    assert first != second
    assert first == gm._course_id(title="서예와 사군자", **values)


def test_parallel_fetch_retries_once_with_a_fresh_session() -> None:
    url = gm.gwangmyeong_learning_list_url(1)
    calls = 0
    sessions: list[DummySession] = []

    def factory() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    def fetcher(_session: Any, requested_url: str, _timeout: int) -> FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.ReadTimeout("temporary")
        return FakeResponse(requested_url, "<html><body>ok</body></html>")

    values, errors = gm._fetch_many(
        [("page", url)],
        fetcher=fetcher,
        session_factory=factory,
        timeout=5,
        max_workers=1,
        expected_hosts={gm.GWANGMYEONG_HOST},
    )

    assert not errors
    assert "page" in values
    assert calls == 2
    assert len(sessions) == 2
    assert all(current.closed for current in sessions)


def test_complete_snapshot_partitions_non_education_and_avoids_pii(
    complete_source: dict[str, Any],
) -> None:
    rows, parser, meta = _collect(complete_source)
    assert parser == gm.GWANGMYEONG_PARSER
    assert [row["title"] for row in rows] == [
        "공개 교육 강좌",
        "농장 체험 프로그램",
        "도서관 글쓰기 교육",
        "2099학년도 광명자치대학 모집",
    ]
    assert meta["source_total"] == 8
    assert meta["source_rows"] == 8
    assert meta["current_source_count"] == 7
    assert meta["current_education_count"] == 3
    assert meta["current_experience_count"] == 1
    assert meta["fixed_venue_experience_count"] == 1
    assert meta["excluded_unfixed_experience_count"] == 0
    assert meta["current_partition_counts"] == {
        "education": 3,
        "facility": 1,
        "experience": 1,
        "performance": 1,
        "development": 1,
    }
    assert meta["detail_attempts"] == meta["detail_pages"] == 4
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert rows[0]["application_url"] == rows[0]["raw_url"]
    assert rows[1]["application_url"] == rows[1]["raw_url"]
    assert rows[1]["domain_category"] == "체험·견학"
    assert rows[1]["service_group"] == "체험"
    assert rows[1]["program_type"] == "체험"
    assert rows[1]["service_group_policy"] == "locked"
    assert rows[1]["classification_locked"] is True
    assert rows[1]["venue_address"] == "광명농장 경기 광명시 가학로85번길 142"
    assert rows[2]["application_url"] == rows[2]["raw_url"]
    assert rows[3]["application_url"] == ""
    assert rows[2]["branch"] == "소하도서관"
    assert rows[2]["branch_code"] == "API_OFFICE_00000020:ST04"
    for row in rows:
        assert "description" not in row
        assert "instructor" not in row
        assert "contact" not in row
        assert "applicants" not in row
    assert not any(
        token in url.lower()
        for url in complete_source["calls"]
        for token in ("/apply", "/login", "download", "attachment", "applicant")
    )


def test_experience_without_fixed_address_is_audited_but_not_emitted(
    complete_source: dict[str, Any],
) -> None:
    record = complete_source["records"][2]
    detail_url = gm.gwangmyeong_learning_detail_url(record["identity"])
    complete_source["mapping"][detail_url] = _internal_detail(
        record,
        location="광명시 평생학습원 2층 체험실",
    )

    rows, _, meta = _collect(complete_source)

    assert all(row["title"] != "농장 체험 프로그램" for row in rows)
    assert meta["detail_attempts"] == meta["detail_pages"] == 4
    assert meta["ilms_current_experience_count"] == 1
    assert meta["current_experience_count"] == 0
    assert meta["excluded_unfixed_experience_count"] == 1
    assert meta["excluded_unfixed_experience_reason_counts"] == {
        "fixed_gwangmyeong_address_missing": 1
    }
    assert meta["snapshot_complete"] is True


@pytest.mark.parametrize(
    ("title", "partition"),
    [
        ("생태 체험 예약 안내", "notice"),
        ("여름 체험 축제", "event"),
        ("체력단련실 체험", "facility"),
        ("[오픈시네마] 체험 영화", "performance"),
        ("농장 체험 프로그램", "experience"),
    ],
)
def test_experience_partition_rejects_notice_event_and_facility_shells(
    title: str,
    partition: str,
) -> None:
    assert gm._partition(title, "광명시평생학습원") == partition


def test_every_page_sentinel_and_page_one_recheck_are_requested(
    complete_source: dict[str, Any],
) -> None:
    _rows, _parser, meta = _collect(complete_source)
    calls = complete_source["calls"]
    assert calls.count(gm.gwangmyeong_learning_list_url(1)) == 2
    assert calls.count(gm.gwangmyeong_learning_list_url(5)) == 1
    assert calls.count(gm.gwangmyeong_office_list_url(1)) == 2
    assert calls.count(gm.gwangmyeong_office_list_url(2)) == 1
    assert calls.count(gm.gwangmyeong_media_list_url(1)) == 2
    assert calls.count(gm.gwangmyeong_media_list_url(2)) == 1
    assert meta["required_page_requests"] == 21
    assert meta["list_requests"] == 21
    for category in gm.GWANGMYEONG_GMCC_CATEGORIES:
        assert calls.count(gm.gwangmyeong_gmcc_list_url(category, 1)) == 2
        assert calls.count(gm.gwangmyeong_gmcc_list_url(category, 2)) == 1


def test_page_or_detail_caps_fail_closed(complete_source: dict[str, Any]) -> None:
    rows, _parser, meta = _collect(complete_source, max_pages=20)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "request cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(complete_source, detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_directory_change_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    url = gm.gwangmyeong_office_list_url(1)
    complete_source["mapping"][url] = complete_source["mapping"][url].replace(
        "광명시도서관", "이름이 바뀐 기관"
    )
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert "official institution directory changed" in meta["configured_collection_error"]


def test_directory_featured_order_may_change_when_membership_is_identical(
    complete_source: dict[str, Any],
) -> None:
    offices = tuple(reversed(complete_source["offices"]))
    page_one = gm.gwangmyeong_office_list_url(1)
    complete_source["mapping"][page_one] = _office_page(
        offices, page=1, declared_total=3
    )

    rows, _parser, meta = _collect(complete_source)

    assert len(rows) == 4
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""


def test_nonempty_post_last_page_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    complete_source["mapping"][gm.gwangmyeong_learning_list_url(5)] = _learning_page(
        [complete_source["records"][0]], page=5, total=8, page_size=2
    )
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert "immediate post-last page is not empty" in meta["configured_collection_error"]


def test_current_detail_mismatch_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    record = complete_source["records"][0]
    detail_url = gm.gwangmyeong_learning_detail_url(record["identity"])
    complete_source["mapping"][detail_url] = _internal_detail(record, bad_title=True)
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is False
    assert "internal detail title mismatch" in meta["configured_collection_error"]


def test_transient_semantic_detail_shell_is_retried_once(
    complete_source: dict[str, Any],
) -> None:
    record = complete_source["records"][0]
    detail_url = gm.gwangmyeong_learning_detail_url(record["identity"])
    base_fetcher = complete_source["fetcher"]
    attempts = 0

    def transient_fetcher(session: Any, url: str, timeout: int) -> FakeResponse:
        nonlocal attempts
        if url == detail_url:
            attempts += 1
            if attempts == 1:
                return FakeResponse(url, _internal_detail(record, bad_title=True))
        return base_fetcher(session, url, timeout)

    rows, _parser, meta = _collect(
        complete_source, fetcher=transient_fetcher
    )

    assert len(rows) == 4
    assert attempts == 2
    assert meta["detail_semantic_retry_attempts"] == 1
    assert meta["detail_semantic_retry_recovered"] == 1
    assert meta["snapshot_complete"] is True


def test_persistent_exact_library_tombstone_is_audited_and_excluded(
    complete_source: dict[str, Any],
) -> None:
    record = complete_source["records"][6]
    detail_url = record["url"]
    complete_source["mapping"][detail_url] = _gmlib_tombstone_page()

    rows, _parser, meta = _collect(complete_source)

    assert [row["title"] for row in rows] == [
        "공개 교육 강좌",
        "농장 체험 프로그램",
        "2099학년도 광명자치대학 모집",
    ]
    assert complete_source["calls"].count(detail_url) == 2
    assert meta["detail_attempts"] == 4
    assert meta["detail_pages"] == 3
    assert meta["detail_verified_count"] == 4
    assert meta["persistent_library_tombstone_count"] == 1
    assert meta["persistent_library_tombstone_sequences"] == [2]
    assert meta["detail_failed_course_count"] == 0
    assert meta["snapshot_complete"] is True


def test_closed_external_http500_tombstone_requires_the_exact_audited_row() -> None:
    raw_url = next(iter(gm.GWANGMYEONG_KNOWN_CLOSED_EXTERNAL_HTTP500))
    expected = gm.GWANGMYEONG_KNOWN_CLOSED_EXTERNAL_HTTP500[raw_url]
    row = {
        "title": expected["title"],
        "branch": expected["branch"],
        "period": expected["period"],
        "apply_period": expected["apply_period"],
        "status": "CLOSED",
        "raw_url": raw_url,
        "raw_fields": {
            "action_kind": "external",
            "source_status": expected["source_status"],
        },
    }
    errors = [f"{raw_url}: HTTPError: 500 Server Error: audited fixture"]

    assert gm._known_closed_external_http500_tombstone(row, errors)
    assert not gm._known_closed_external_http500_tombstone(
        {**row, "status": "OPEN"}, errors
    )
    assert not gm._known_closed_external_http500_tombstone(
        {**row, "title": "다른 강좌"}, errors
    )
    assert not gm._known_closed_external_http500_tombstone(
        row, [f"{raw_url}: ReadTimeout: temporary"]
    )


def test_nonexact_library_shell_still_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    record = complete_source["records"][6]
    detail_url = record["url"]
    complete_source["mapping"][detail_url] = """
      <html><head><title>알림 페이지</title></head>
      <body><script>alert('일시적인 오류입니다.');</script>소하도서관</body></html>
    """

    rows, _parser, meta = _collect(complete_source)

    assert rows == []
    assert complete_source["calls"].count(detail_url) == 2
    assert meta["persistent_library_tombstone_count"] == 0
    assert meta["snapshot_complete"] is False
    assert "external detail title mismatch" in meta["configured_collection_error"]


def test_youth_detail_supplies_the_official_branch_name() -> None:
    row = {
        "title": "청소년 코딩",
        "raw_url": (
            "https://gmyouth.or.kr/www/viewLectureWebView.do?key=846&lectureNo=1"
        ),
        "start_date": "2099-07-01",
        "end_date": "2099-08-01",
        "status": "OPEN",
        "branch": "광명청소년재단",
        "branch_code": "API_OFFICE_00000030",
        "raw_fields": {"list_sequence": 1, "action_kind": "external"},
    }
    soup = gm.BeautifulSoup(
        """
        <html><body><main><h2>청소년 코딩</h2><p>2099.07.01 ~ 2099.08.01</p>
          <table><tr><th>기관명</th><td>오름청소년활동센터</td></tr></table>
        </main></body></html>
        """,
        "lxml",
    )

    assert gm._validate_external_detail(row, soup) == []
    assert row["branch"] == "오름청소년활동센터"
    assert row["branch_code"] == "API_OFFICE_00000030:오름청소년활동센터"


def test_video_catalogue_activation_fails_closed(
    complete_source: dict[str, Any],
) -> None:
    complete_source["mapping"][gm.gwangmyeong_media_list_url(1)] = _media_page(
        page=1, activated=True
    )
    rows, _parser, meta = _collect(complete_source)
    assert rows == []
    assert "video catalogue" in meta["configured_collection_error"]


def test_culture_centre_generic_list_resolves_unique_item() -> None:
    rows = [
        {
            "title": "서예와 사군자[중급]",
            "start_date": "2099-06-01",
            "end_date": "2099-08-31",
            "raw_fields": {"list_sequence": 10},
        }
    ]
    soup = gm.BeautifulSoup(
        """
        <a href='/product_new/item.php?it_id=1&ca_id=01'>
          서예와 사군자[중급] 접수기간 2099.05.01 ~ 2099.05.10
          강좌기간 2099.06.01 ~ 2099.08.31
        </a>
        """,
        "lxml",
    )
    resolved, errors = gm._resolve_gmcc_details(rows, soup)
    assert errors == []
    assert resolved == {
        10: "https://www.gmcc.or.kr/product_new/item.php?it_id=1&ca_id=01"
    }


def test_culture_centre_catalogue_checks_all_categories_and_boundaries() -> None:
    mapping: dict[str, str] = {}
    for category in ("01", "02", "03"):
        item_id = f"TEST_{category}"
        mapping[gm.gwangmyeong_gmcc_list_url(category, 1)] = f"""
          <html><body>
              <a href='/product_new/item.php?it_id={item_id}&amp;ca_id={category}'>
                공식 교육 {category} 접수기간 2099.01.01 ~ 2099.01.10
                강좌기간 2099.02.01 ~ 2099.02.28 수업시간 월 10:00 강사명 홍길동
                수강신청 예정
              </a>
          </body></html>
        """
        mapping[gm.gwangmyeong_gmcc_list_url(category, 2)] = (
            "<html><body>등록된 강좌가 없습니다.</body></html>"
        )
    page_one_url = gm.gwangmyeong_gmcc_list_url("01", 1)
    mapping[page_one_url] = mapping[page_one_url].replace(
        "</body>",
        "<a href='/product_new/list.php?ca_id=01&amp;page=2'>2</a></body>",
    )
    mapping[gm.gwangmyeong_gmcc_list_url("01", 2)] = """
      <html><body>
        <a href='/product_new/item.php?it_id=TEST_01_B&amp;ca_id=01'>
          두 번째 공식 교육 접수기간 2099.01.01 ~ 2099.01.10
          강좌기간 2099.02.01 ~ 2099.02.28 수업시간 화 10:00 강사명 홍길동
          수강신청 예정
        </a>
      </body></html>
    """
    mapping[gm.gwangmyeong_gmcc_list_url("01", 3)] = (
        "<html><body>등록된 강좌가 없습니다.</body></html>"
    )

    calls: list[str] = []

    def fetcher(_session: Any, url: str, _timeout: int) -> FakeResponse:
        calls.append(url)
        return FakeResponse(url, mapping[url])

    ilms_rows = [
        {
            "title": "공식 교육 01",
            "start_date": "2099-02-01",
            "end_date": "2099-02-28",
            "raw_fields": {"list_sequence": 88},
        },
        {
            "title": "지난 학기 문화강좌",
            "start_date": "2098-01-01",
            "end_date": "2098-02-01",
            "raw_fields": {"list_sequence": 77},
        }
    ]
    resolved, official_rows, meta, errors, requests = gm._audit_gmcc_catalogue(
        ilms_rows,
        fetcher=fetcher,
        session_factory=DummySession,
        timeout=5,
        max_workers=2,
    )

    assert errors == []
    assert resolved == {
        88: "https://www.gmcc.or.kr/product_new/item.php?it_id=TEST_01&ca_id=01"
    }
    assert requests == meta["gmcc_required_list_requests"] == 10
    assert meta["gmcc_list_requests"] == 10
    assert meta["gmcc_advertised_last_pages"] == {"01": 2, "02": 1, "03": 1}
    assert meta["gmcc_category_counts"] == {"01": 2, "02": 1, "03": 1}
    assert meta["gmcc_official_catalogue_count"] == 4
    assert meta["gmcc_special_catalogue_count"] == 1
    assert meta["gmcc_regular_ilms_count"] == 2
    assert meta["gmcc_regular_resolved_count"] == 1
    assert meta["gmcc_regular_stale_mirror_count"] == 1
    assert len(official_rows) == 4
    assert {row["status"] for row in official_rows} == {"SCHEDULED"}
    assert len(calls) == 10
    assert {
        row["raw_fields"]["catalogue"] for row in official_rows
    } == {"gmcc_culture_school", "gmcc_special"}

    calls.clear()
    resolved, official_rows, meta, errors, requests = gm._audit_gmcc_catalogue(
        [],
        fetcher=fetcher,
        session_factory=DummySession,
        timeout=5,
        max_workers=2,
        max_requests=9,
    )
    assert resolved == {}
    assert official_rows == []
    assert meta["gmcc_required_list_requests"] == 10
    assert meta["gmcc_source_cap_reached"] is True
    assert requests == len(calls) == 3
    assert "request cap" in "; ".join(errors)


def test_noncanonical_target_never_fetches(complete_source: dict[str, Any]) -> None:
    rows, _parser, meta = gm.collect_gwangmyeong_education_courses(
        Target(url=gm.GWANGMYEONG_LANDING_URL),
        fetcher=complete_source["fetcher"],
        session_factory=DummySession,
    )
    assert rows == []
    assert complete_source["calls"] == []
    assert meta["configured_collection_error"] == "non-canonical Gwangmyeong target"


def test_canonical_and_legacy_provider_ids_route_only_the_canonical_ledger() -> None:
    assert gm.is_gwangmyeong_education_target(Target())
    assert gm.is_gwangmyeong_education_target(
        Target(provider=gm.GWANGMYEONG_LEGACY_PROVIDER)
    )
    assert not gm.is_gwangmyeong_education_target(
        Target(provider=gm.GWANGMYEONG_LEGACY_PROVIDER, url=gm.GWANGMYEONG_LANDING_URL)
    )


def test_default_fetcher_recovers_after_two_transient_timeouts() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

    class Session:
        calls = 0

        def get(self, _url: str, *, timeout: int) -> Response:
            assert timeout == 20
            self.calls += 1
            if self.calls < 3:
                raise requests.ReadTimeout("transient")
            return Response()

    current = Session()
    assert isinstance(gm._default_fetcher(current, gm.GWANGMYEONG_CANONICAL_URL, 20), Response)
    assert current.calls == 3


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_CRAWLER_TESTS") != "1",
    reason="set RUN_LIVE_CRAWLER_TESTS=1 for the complete official Gwangmyeong audit",
)
def test_live_gwangmyeong_snapshot_is_complete_or_atomically_empty() -> None:
    rows, parser, meta = gm.collect_gwangmyeong_education_courses(
        Target(),
        timeout=40,
        max_pages=200,
        detail_limit=1000,
        max_workers=gm.GWANGMYEONG_MAX_WORKERS,
    )

    assert parser == gm.GWANGMYEONG_PARSER
    assert meta["source_rows"] == meta["source_total"]
    assert meta["source_total"] >= 4_900
    assert meta["office_declared_total"] == 74
    assert meta["office_count"] == 73
    assert meta["office_hidden_count"] == 1
    assert meta["media_source_total"] == 0
    assert meta["gmcc_official_catalogue_count"] == 36
    assert meta["current_education_count"] > 0
    assert not meta["source_cap_reached"]
    if meta["snapshot_complete"]:
        assert len(rows) == (
            meta["current_education_count"] + meta["current_experience_count"]
        )
        assert meta["detail_failed_course_count"] == 0
        assert meta["configured_collection_error"] == ""
    else:
        assert rows == []
        assert meta["detail_failed_course_count"] > 0
        assert meta["configured_collection_error"]
