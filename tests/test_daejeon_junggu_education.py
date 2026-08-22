from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from Crawler import municipal_daejeon_junggu as junggu


@dataclass
class JungguTarget:
    provider: str = junggu.DAEJEON_JUNGGU_PROVIDER
    url: str = junggu.DAEJEON_JUNGGU_CANONICAL_URL
    branch: str = junggu.DAEJEON_JUNGGU_MUNICIPALITY_NAME


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _options(values: tuple[tuple[str, str], ...]) -> str:
    return "".join(
        f"<option value='{value}'>{label}</option>" for value, label in values
    )


def _list_row(catalogue: Any, record: dict[str, Any], page: int) -> str:
    identity = record["id"]
    href = f"{catalogue.detail_path}?pageIndex={page}&amp;eduNo={identity}"
    if record.get("one_inwon"):
        href += "&amp;oneInwon="
    title = (
        f"{record['title']}<br>(강사 : 홍길동)"
        if catalogue.key == "lifelong"
        else f"{record['title']}<br>(강사:홍길동)"
    )
    status_control = (
        f"<span class='btn btn-sm btn-secondary'>{record['status']}</span>"
        if record.get("detail_unpublished")
        else f"<a href='{href}'>{record['status']}</a>"
    )
    cells = []
    if catalogue.key == "lifelong":
        cells.append(f"<td data-cell-header='학기 : '>{record['semester']}</td>")
    cells.extend(
        [
            f"<td data-cell-header='강좌명/강사명 : '>{title}</td>",
            (
                "<td data-cell-header='접수기간 : '>"
                f"{record['apply_start']} 09:00<br>{record['apply_end']} 18:00</td>"
            ),
            (
                "<td data-cell-header='교육기간 : '>"
                f"{record['start']}<br>{record['end']}</td>"
            ),
            (
                "<td data-cell-header='신청인원 / 모집인원 : '>"
                f"{record['current']} / {record['capacity']}"
                + (
                    f"<br>대기({record['wait_current']}/{record['wait_total']})"
                    if catalogue.key == "lifelong"
                    else ""
                )
                + "</td>"
            ),
            f"<td data-cell-header='시간 : '>{record['schedule']}</td>",
            (
                "<td data-cell-header='상태 : '><span class='btn-group-vertical'>"
                f"{status_control}</span></td>"
            ),
        ]
    )
    return "<tr>" + "".join(cells) + "</tr>"


def _list_page(
    catalogue: Any,
    records: list[dict[str, Any]],
    *,
    page: int,
    total: int,
) -> str:
    status_options = _options(catalogue.status_options)
    if catalogue.key == "lifelong":
        catalogue_filter = (
            "<select name='searchGroupNo'>"
            "<option value=''>:: 학기 전체 ::</option>"
            "<option value='lec_fixture'>2099년</option></select>"
        )
    else:
        catalogue_filter = (
            "<select name='year'><option value=''>-년도 전체-</option>"
            "<option value='2099'>2099</option></select>"
        )
    headings = "".join(f"<th>{heading}</th>" for heading in catalogue.expected_headers)
    last = max(1, (total + junggu.DAEJEON_JUNGGU_PAGE_SIZE - 1) // junggu.DAEJEON_JUNGGU_PAGE_SIZE)
    pagination = ""
    if total > junggu.DAEJEON_JUNGGU_PAGE_SIZE:
        pagination = (
            "<ul class='pagination'>"
            f"<li><a aria-label='last' href='?pageIndex={last}'>last</a></li>"
            "</ul>"
        )
    return f"""
      <html><head><title>{catalogue.expected_page_title}</title></head><body>
        <div id='contents'><h2>{catalogue.expected_heading}</h2></div>
        <form name='eduSearchForm' method='post' action='{catalogue.list_path}'>
          <input name='pageUnit' value='{junggu.DAEJEON_JUNGGU_PAGE_SIZE}'>
          <input name='pageIndex' value='1'>
          <input name='pageSize' value='{junggu.DAEJEON_JUNGGU_PAGE_SIZE}'>
          <input name='suborgCode' value=''>
          <input name='searchCondition' value='subject'>
          {catalogue_filter}
          <select name='dateType'><option value=''>-기준-</option>
            <option value='date'>예약일</option><option value='reqdate'>교육일</option>
          </select>
          <select name='state'>{status_options}</select>
        </form>
        <div class='program--count'><strong>{total}</strong></div>
        <div class='no-more-tables'><table class='table-default'>
          <thead><tr>{headings}</tr></thead>
          <tbody>{''.join(_list_row(catalogue, item, page) for item in records)}</tbody>
        </table></div>{pagination}
      </body></html>
    """


def _sitemap(site_key: str, *, extra_route: bool = False) -> str:
    title = (
        "대전 중구 평생학습관"
        if site_key == "lll"
        else "대전광역시 중구청"
    )
    routes = set(junggu.DAEJEON_JUNGGU_EXPECTED_SITEMAP_ROUTES[site_key])
    if extra_route:
        routes.add(
            "https://www.djjunggu.go.kr/prog/infoCourse/newedu/kr/sub99/list.do"
        )
    anchors = "".join(f"<a href='{url}'>교육신청</a>" for url in sorted(routes))
    return f"<html><head><title>{title}</title></head><body>{anchors}</body></html>"


def _confirmation(*, broken: bool = False) -> str:
    if broken:
        return "<html><body>공개 목록으로 변경됨</body></html>"
    return """
      <html><body><script>
        alert("본인 확인후에 이용이 가능합니다.");
        location.href="/lll/login.do";
      </script></body></html>
    """


def _detail(
    catalogue: Any,
    record: dict[str, Any],
    *,
    missing_control: bool = False,
    bad_title: bool = False,
    pii_target: bool = False,
) -> str:
    title = "다른 강좌" if bad_title else record["title"]
    target = "문의 user@example.com" if pii_target else "대전 중구 주민"
    control = ""
    if record["status"] in {"모집중", "대기 신청중"} and not missing_control:
        if catalogue.key == "lifelong":
            path = "/prog/lecReserve/lec/lll/sub02_01_02/write.do"
        else:
            path = "/prog/infoReserve/infoedu/kr/sub04_01_02_02/write.do"
        control_text = (
            "대기신청" if record["status"] == "대기 신청중" else "신청하기"
        )
        control = (
            f"<a href='{path}?pageIndex=1&amp;eduNo={record['id']}"
            f"&amp;oneInwon=&amp;resvChk=N'>{control_text}</a>"
        )
    return f"""
      <html><head><title>{catalogue.expected_page_title}</title></head><body>
        <div class='progphoto_wrap'><div class='inner'>
          <div class='thumb'><div class='btn_wrap'>{control}</div></div>
          <div class='info_box'><strong>{title}</strong><ul class='progicon-list'>
            <li><em>교육시간</em>{record['schedule']}</li>
            <li><em>교육기간</em>{record['start']} ~ {record['end']}</li>
            <li><em>접수기간</em>{record['apply_start']} 09:00 ~ {record['apply_end']} 18:00</li>
            <li><em>수업료</em>0</li>
            <li><em>강사</em>홍길동</li>
          </ul></div>
        </div></div>
        <div class='apply-article'><div class='forward-article'>
          <div class='self-accrdt'><div class='item'><strong>교육정원</strong><em>{record['capacity']} 명</em></div></div>
          <div class='self-accrdt'><div class='item'><strong>교육대상</strong><em>{target}</em></div></div>
          <div class='self-accrdt'><div class='item'><strong>교육장소</strong><em>중구 중앙로 100 교육장</em></div></div>
          <div class='self-accrdt'><div class='item'><strong>문의전화</strong><em>042-606-6084</em></div></div>
        </div></div>
        <h2>강좌소개</h2><div>담당자 test@example.com, 강사 홍길동의 자유 서술</div>
      </body></html>
    """


def _records() -> dict[str, list[dict[str, Any]]]:
    return {
        "lifelong": [
            {
                "id": "101",
                "title": "환경교육 실천가",
                "semester": "2099년",
                "apply_start": "2099-07-01",
                "apply_end": "2099-07-22",
                "start": "2099-07-23",
                "end": "2099-08-27",
                "current": 3,
                "capacity": 15,
                "wait_current": 0,
                "wait_total": 5,
                "schedule": "목 09:30~12:30",
                "status": "대기 신청중",
                "one_inwon": True,
            },
            {
                "id": "100",
                "title": "평생학습마을 기획자",
                "semester": "2099년",
                "apply_start": "2099-07-01",
                "apply_end": "2099-07-20",
                "start": "2099-07-20",
                "end": "2099-07-29",
                "current": 15,
                "capacity": 15,
                "wait_current": 0,
                "wait_total": 5,
                "schedule": "월 10:00~13:00",
                "status": "모집예정",
                "detail_unpublished": True,
            },
            {
                "id": "99",
                "title": "종료 평생학습 강좌",
                "semester": "2020년",
                "apply_start": "2020-01-01",
                "apply_end": "2020-01-10",
                "start": "2020-01-15",
                "end": "2020-02-01",
                "current": 10,
                "capacity": 10,
                "wait_current": 0,
                "wait_total": 0,
                "schedule": "수 10:00~12:00",
                "status": "교육종료",
            },
        ],
        "information": [
            {
                "id": "8",
                "title": "디지털 문서 교육",
                "apply_start": "2099-07-01",
                "apply_end": "2099-07-31",
                "start": "2099-08-01",
                "end": "2099-08-03",
                "current": 4,
                "capacity": 20,
                "wait_current": 0,
                "wait_total": 0,
                "schedule": "월 09:30~11:30",
                "status": "모집중",
            },
            {
                "id": "7",
                "title": "종료 정보화 교육",
                "apply_start": "2020-03-01",
                "apply_end": "2020-03-10",
                "start": "2020-03-15",
                "end": "2020-03-17",
                "current": 20,
                "capacity": 20,
                "wait_current": 0,
                "wait_total": 0,
                "schedule": "화 14:00~16:00",
                "status": "모집마감",
            },
        ],
    }


def _fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    nonempty_sentinel: bool = False,
    changed_recheck: bool = False,
    missing_control: bool = False,
    bad_detail_title: bool = False,
    pii_target: bool = False,
    extra_sitemap_route: bool = False,
    broken_confirmation: bool = False,
) -> tuple[dict[str, str], list[DummySession], list[str]]:
    monkeypatch.setattr(junggu, "DAEJEON_JUNGGU_PAGE_SIZE", 2)
    records = _records()
    pages: dict[str, str] = {}
    for site_key, url in junggu.DAEJEON_JUNGGU_SITEMAPS.items():
        pages[url] = _sitemap(
            site_key, extra_route=(extra_sitemap_route and site_key == "kr")
        )
    pages[junggu.DAEJEON_JUNGGU_CONFIRMATION_URL] = _confirmation(
        broken=broken_confirmation
    )

    for catalogue in junggu.DAEJEON_JUNGGU_CATALOGUES:
        source = records[catalogue.key]
        total = len(source)
        last = max(1, (total + junggu.DAEJEON_JUNGGU_PAGE_SIZE - 1) // junggu.DAEJEON_JUNGGU_PAGE_SIZE)
        for page in range(1, last + 1):
            start = (page - 1) * junggu.DAEJEON_JUNGGU_PAGE_SIZE
            chunk = source[start : start + junggu.DAEJEON_JUNGGU_PAGE_SIZE]
            html = _list_page(catalogue, chunk, page=page, total=total)
            url = (
                catalogue.list_url
                if page == 1
                else junggu.daejeon_junggu_list_url(catalogue.key, page)
            )
            pages[url] = html
        sentinel_rows = source[:1] if nonempty_sentinel and catalogue.key == "lifelong" else []
        if sentinel_rows:
            sentinel_rows = [dict(sentinel_rows[0], one_inwon=False)]
        pages[junggu.daejeon_junggu_list_url(catalogue.key, last + 1)] = _list_page(
            catalogue, sentinel_rows, page=last + 1, total=total
        )
        recheck_source = list(source[: junggu.DAEJEON_JUNGGU_PAGE_SIZE])
        if changed_recheck and catalogue.key == "lifelong":
            recheck_source[0] = dict(recheck_source[0], title="변경된 제목")
        pages[junggu.daejeon_junggu_list_url(catalogue.key, 1)] = _list_page(
            catalogue, recheck_source, page=1, total=total
        )
        for record in source:
            if record["end"] < "2099-01-01" or record.get("detail_unpublished"):
                continue
            pages[junggu.daejeon_junggu_detail_url(catalogue.key, record["id"])] = _detail(
                catalogue,
                record,
                missing_control=(missing_control and record["id"] == "101"),
                bad_title=(bad_detail_title and record["id"] == "101"),
                pii_target=(pii_target and record["id"] == "101"),
            )

    sessions: list[DummySession] = []
    calls: list[str] = []

    def factory() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        if url not in pages:
            raise AssertionError(f"unexpected URL {url}")
        return pages[url]

    return {"pages": pages, "factory": factory, "fetch": fetch}, sessions, calls


def _collect(
    monkeypatch: pytest.MonkeyPatch,
    **fixture_kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], list[DummySession], list[str]]:
    fixture, sessions, calls = _fixture(monkeypatch, **fixture_kwargs)
    rows, parser, meta = junggu.collect_daejeon_junggu_education(
        JungguTarget(),
        timeout=5,
        max_pages=20,
        detail_limit=10,
        today="2026-07-21",
        max_workers=4,
        session_factory=fixture["factory"],
        fetcher=fixture["fetch"],
    )
    return rows, parser, meta, sessions, calls


def test_target_scope_urls_aliases_and_overlap_evidence() -> None:
    assert junggu.is_daejeon_junggu_education_target(JungguTarget())
    assert not junggu.is_daejeon_junggu_education_target(
        {
            "provider": junggu.DAEJEON_JUNGGU_PROVIDER,
            "url": junggu.DAEJEON_JUNGGU_INFORMATION_URL,
        }
    )
    assert not junggu.is_daejeon_junggu_education_target(
        {
            "provider": "MUNI_WRONG",
            "url": junggu.DAEJEON_JUNGGU_CANONICAL_URL,
        }
    )
    assert not junggu.is_daejeon_junggu_education_target(
        {
            "provider": junggu.DAEJEON_JUNGGU_PROVIDER,
            "url": junggu.DAEJEON_JUNGGU_CANONICAL_URL + "?pageIndex=1",
        }
    )
    assert junggu.is_daejeon_junggu_owned_alias_target(
        {"url": junggu.DAEJEON_JUNGGU_INFORMATION_URL}
    )
    assert junggu.is_daejeon_junggu_owned_alias_target(
        {"url": junggu.DAEJEON_JUNGGU_DETAIL_ALIAS_URLS[0]}
    )
    assert junggu.daejeon_junggu_list_url("lifelong", 2).endswith("?pageIndex=2")
    assert junggu.daejeon_junggu_list_url("unknown", 1) == ""
    assert junggu.daejeon_junggu_detail_url("information", "8").endswith("?eduNo=8")
    assert junggu.daejeon_junggu_detail_url("lifelong", "x") == ""
    audit = junggu.DAEJEON_JUNGGU_OK_OVERLAP_AUDIT
    assert audit["independent_source_rows"] == 247
    assert audit["ok_junggu_source_rows"] == 381
    assert audit["normalized_title_overlap_count"] == 0
    assert audit["normalized_title_period_overlap_count"] == 0


def test_complete_two_catalogue_snapshot_details_controls_and_pii_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, parser, meta, sessions, calls = _collect(monkeypatch)

    assert parser == junggu.DAEJEON_JUNGGU_PARSER
    assert len(rows) == 3
    assert {
        row["provider_course_id"]
        for row in rows
        if ":unpublished-" not in row["provider_course_id"]
    } == {
        f"{junggu.DAEJEON_JUNGGU_PROVIDER}:lifelong:101",
        f"{junggu.DAEJEON_JUNGGU_PROVIDER}:information:8",
    }
    unpublished = next(
        row for row in rows if ":unpublished-" in row["provider_course_id"]
    )
    assert unpublished["status"] == "SCHEDULED"
    assert unpublished["fee"] == "공식 페이지 미기재"
    assert unpublished["target"] == "공식 페이지 미기재"
    assert unpublished["venue_name"] == "공식 페이지 미기재"
    assert unpublished["raw_fields"]["detail_unpublished"] is True
    assert "#mooncen-item-" in unpublished["raw_url"]
    assert len({row["raw_url"] for row in rows}) == len(rows)
    assert {row["branch"] for row in rows} == {
        "대전 중구 평생학습관",
        "대전광역시 중구 정보화교육장",
    }
    open_rows = [row for row in rows if row["status"] == "OPEN"]
    waiting_rows = [row for row in rows if row["status"] == "WAITING"]
    assert len(open_rows) == len(waiting_rows) == 1
    assert all(
        row["reservation_available"] for row in open_rows + waiting_rows
    )
    assert open_rows[0]["application_type"] == "ONLINE_RESERVATION_LOGIN_REQUIRED"
    assert waiting_rows[0]["application_type"] == "ONLINE_WAITLIST_LOGIN_REQUIRED"
    assert unpublished["application_url"] == ""
    assert unpublished["application_type"] == "INFO_ONLY"
    assert all(row["fee_amount"] == 0 for row in rows)
    assert all(
        row["fee"] == "무료"
        for row in rows
        if not row["raw_fields"]["detail_unpublished"]
    )
    assert all(row["description"] == row["title"] for row in rows)
    assert all("홍길동" not in repr(row) for row in rows)
    assert all("042-" not in repr(row) and "@" not in repr(row) for row in rows)
    assert all(
        set(row["raw_fields"]) <= junggu._SAFE_RAW_FIELDS for row in rows
    )

    assert meta["source_totals"] == {"lifelong": 3, "information": 2}
    assert meta["source_rows"] == 5
    assert meta["source_counts"] == {"lifelong": 3, "information": 2}
    assert meta["current_counts"] == {"lifelong": 2, "information": 1}
    assert meta["current_count"] == meta["returned_count"] == 3
    assert meta["expired_count"] == 2
    assert meta["sitemap_requests"] == 4
    assert meta["sentinel_requests"] == 2
    assert meta["stability_rechecks"] == 2
    assert meta["required_list_requests"] == meta["list_requests"] == 12
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["scheduled_detail_unpublished_count"] == 1
    assert meta["status_counts"] == {"OPEN": 1, "SCHEDULED": 1, "WAITING": 1}
    assert meta["application_control_count"] == 2
    assert meta["confirmation_alias_verified"] is True
    assert meta["sitemaps_complete"] is True
    assert meta["identity_duplicate_count"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["ok_catalogue_is_alias"] is False
    assert meta["pii_payload_persisted"] is False
    assert all(session.closed for session in sessions)
    assert len(calls) == 14


def test_sentinel_stability_sitemap_and_confirmation_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _parser, meta, _sessions, _calls = _collect(
        monkeypatch, nonempty_sentinel=True
    )
    assert rows == []
    assert "immediate post-last page is not empty" in meta["configured_collection_error"]

    rows2, _parser2, meta2, _sessions2, _calls2 = _collect(
        monkeypatch, changed_recheck=True
    )
    assert rows2 == []
    assert "page-one recheck changed" in meta2["configured_collection_error"]

    rows3, _parser3, meta3, _sessions3, _calls3 = _collect(
        monkeypatch, extra_sitemap_route=True
    )
    assert rows3 == []
    assert "sitemap course/reservation fanout changed" in meta3["configured_collection_error"]

    rows4, _parser4, meta4, _sessions4, _calls4 = _collect(
        monkeypatch, broken_confirmation=True
    )
    assert rows4 == []
    assert "identity-verification alert changed" in meta4["configured_collection_error"]


def test_detail_identity_control_and_pii_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _parser, meta, _sessions, _calls = _collect(
        monkeypatch, missing_control=True
    )
    assert rows == []
    assert "waiting course application control changed" in meta["configured_collection_error"]

    rows2, _parser2, meta2, _sessions2, _calls2 = _collect(
        monkeypatch, bad_detail_title=True
    )
    assert rows2 == []
    assert "detail/list title mismatch" in meta2["configured_collection_error"]

    rows3, _parser3, meta3, _sessions3, _calls3 = _collect(
        monkeypatch, pii_target=True
    )
    assert rows3 == []
    assert "PII-like contact data persisted" in meta3["configured_collection_error"]


def test_caps_and_shared_dedupe_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, _sessions, _calls = _fixture(monkeypatch)
    rows, _parser, meta = junggu.collect_daejeon_junggu_education(
        JungguTarget(),
        max_pages=11,
        detail_limit=10,
        today="2026-07-21",
        session_factory=fixture["factory"],
        fetcher=fixture["fetch"],
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap allows 11 of 12" in meta["configured_collection_error"]

    fixture2, _sessions2, _calls2 = _fixture(monkeypatch)
    rows2, _parser2, meta2 = junggu.collect_daejeon_junggu_education(
        JungguTarget(),
        max_pages=20,
        detail_limit=1,
        today="2026-07-21",
        session_factory=fixture2["factory"],
        fetcher=fixture2["fetch"],
    )
    assert rows2 == []
    assert meta2["source_cap_reached"] is True
    assert "detail_limit cap allows 1 of 2" in meta2["configured_collection_error"]

    fixture3, _sessions3, _calls3 = _fixture(monkeypatch)
    rows3, _parser3, meta3 = junggu.collect_daejeon_junggu_education(
        JungguTarget(),
        max_pages=20,
        detail_limit=10,
        today="2026-07-21",
        session_factory=fixture3["factory"],
        fetcher=fixture3["fetch"],
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows3 == []
    assert "dedupe changed official identity cardinality" in meta3["configured_collection_error"]
