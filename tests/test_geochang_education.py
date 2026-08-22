from __future__ import annotations

from copy import deepcopy
import os
from typing import Any

import pytest
import requests

from Crawler import municipal_geochang as geochang


TARGET = {
    "provider": geochang.GEOCHANG_PROVIDER,
    "url": geochang.GEOCHANG_CANONICAL_URL,
    "extra": {
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "operator_type": "지자체/공공기관",
        "source_group": "lifelong_learning",
    },
}


def _shell(content: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>평생학습강좌 | 거창군평생학습센터</title></head>
<body>
<div id="header"><h1><a href="/"><img src="/images/common/logo_geochangeducity.png" alt="로고"></a></h1></div>
{content}
<div id="footer">(50132) 경상남도 거창군 거창읍 중앙로 103 신청사 5층 / 대표전화
Copyrightⓒ 거창군평생교육센터 All rights reserved.</div>
</body></html>"""


def _search_forms() -> str:
    return """
<form name="searchForm_tab" method="GET" action="/E0003/30020201.asp">
  <input type="text" name="st" id="search_name_tab" value="">
</form>
<form name="search_form" action="30020201.asp" method="post">
  <input type="text" name="st" value="">
  <a href="#" onclick="javascript:Search();"><span>검색</span></a>
</form>"""


def _card(
    identity: str,
    title: str,
    status: str,
    apply_period: str,
    event_period: str,
    target: str,
    venue: str,
    *,
    control: str = "",
) -> str:
    detail = f"30020203.asp?lc={identity}"
    if control == "external":
        extra = '<span><a href="https://gcyka.or.kr/program" target="_blank">별도링크</a></span>'
    elif control == "application":
        extra = (
            f'<span><a href="30020501.asp?lc={identity}&amp;path=/03Sub/03_Apply.asp'
            f'&amp;query=lc={identity}&amp;lcat=50">수강신청</a></span>'
            f'<span><a href="{detail}#lec_list">명단</a></span>'
        )
    else:
        extra = ""
    return f"""
<div class="listover e0002001">
  <a href="{detail}"><div class="lec_title"><span>{status}</span>{title}</div></a>
  <div class="lec_left_img"><img src="/images/lec_img/{identity}.png" alt="course"></div>
  <div class="lec_left_wrap"><ul>
    <li><b>접수</b><span>{apply_period}</span></li>
    <li><b>교육</b><span>{event_period}</span></li>
    <li><b>대상</b><span>{target}</span></li>
    <li><b>장소</b><span>{venue}</span></li>
    <li><b>문의</b><span>055-000-0000</span></li>
  </ul></div>
  <div class="lec_right_wrap"><a href="{detail}">자세히</a>{extra}</div>
</div>"""


def _pager(page: int, pages: int, sentinel: bool = False) -> str:
    items = []
    for number in range(1, pages + 1):
        if number == page and not sentinel:
            items.append(f'<li class="on"><a href="#">{number}</a></li>')
        else:
            items.append(
                f'<li><a href="30020201.asp?page={number}&amp;lc=&amp;search_date=">{number}</a></li>'
            )
    return '<div class="paging_wrap"><ul class="paging">' + "".join(items) + "</ul></div>"


def _list_page(page: int, cards: list[str], *, total: int = 6, pages: int = 2) -> str:
    sentinel = page == pages + 1
    return _shell(
        _search_forms()
        + '<div id="sub_contents">'
        + f'<div style="text-align:right;border-bottom:2px solid #999;">총 <span>{total}</span>건의 강좌가 있습니다.(<span>{page}</span>/{pages}페이지)</div>'
        + "".join(cards)
        + _pager(page, pages, sentinel)
        + "</div>"
    )


def _detail_page(
    identity: str,
    title: str,
    branch: str,
    apply_period: str,
    event_period: str,
    target: str,
    venue: str,
    capacity: int,
    method: str,
    fee: str,
) -> str:
    return _shell(
        f"""
<div class="sub0202_view_wrap">
  <div class="con01">
    <div class="left"><div class="img_wrap"><img src="/images/lec_img/{identity}.png"></div></div>
    <div class="right"><div class="tit"><em>{title}</em></div><div class="txt"><ul>
      <li><span>기관</span>{branch} (문의 055-111-2222)</li>
      <li><span>접수</span>{apply_period}</li>
      <li><span>일정</span>{event_period} / 매주 화요일 / 10:00~12:00</li>
      <li><span>대상</span>{target}</li>
      <li><span>장소</span>{venue}</li>
    </ul></div></div>
  </div>
  <h4>강좌 커리큘럼</h4>
  <table class="basic_tbl01">
    <tr><th>학습 목표</th><td>문의 055-999-9999 - 의도적으로 저장하면 안 되는 본문</td></tr>
    <tr><th>학습 계획</th><td><a href="/images/lec_doc/private.jpg">계획서</a></td></tr>
    <tr><th>개인준비물</th><td></td></tr>
  </table>
  <h4>강좌 상세안내</h4>
  <table class="basic_tbl01">
    <tr><th>강 사 명</th><td>홍길동</td></tr>
    <tr><th>수 강 료</th><td>{fee}</td></tr>
    <tr><th>교육정원</th><td>온라인 <b>{capacity}</b>명</td></tr>
    <tr><th>접수방법</th><td>{method}</td></tr>
    <tr><th>별로링크</th><td><a href="https://unsafe.example/apply">+ 바로접속</a></td></tr>
  </table>
  <h4>접수자 정보<a name="lec_list"></a></h4>
  <table class="basic_tbl01"><tr><th>순번</th><th>이름</th><th>휴대전화</th></tr>
    <tr><td>1</td><td>김**</td><td>*123</td></tr></table>
</div>"""
    )


COURSES = {
    "10": {
        "title": "가족 역사교실",
        "status": "예정",
        "apply": "2026.08.01 ~ 2026.08.10",
        "event": "2026.08.20 ~ 2026.08.20",
        "target": "기타 / 30명",
        "venue": "거창군 외",
        "branch": "거창흥사단",
        "method": "홈페이지 : 기관 별도",
        "fee": "25,000",
        "control": "external",
    },
    "11": {
        "title": "거창 아카데미",
        "status": "접수중",
        "apply": "2026.07.01 ~ 2026.08.31",
        "event": "2026.09.01 ~ 2026.09.30",
        "target": "성인 / 20명",
        "venue": "청소년수련관",
        "branch": "거창군평생교육센터",
        "method": "바로접수 : 평생학습센터",
        "fee": "무료",
        "control": "application",
    },
    "12": {
        "title": "지난 강좌",
        "status": "종료",
        "apply": "2025.01.01 ~ 2025.01.02",
        "event": "2025.02.01 ~ 2025.02.02",
        "target": "성인 / 10명",
        "venue": "거창대학 평생교육원",
        "branch": "국립창원대학교(거창캠퍼스) 평생교육원",
        "method": "전화접수",
        "fee": "무료",
        "control": "",
    },
}


def _course_card(identity: str) -> str:
    row = COURSES[identity]
    return _card(
        identity,
        row["title"],
        row["status"],
        row["apply"],
        row["event"],
        row["target"],
        row["venue"],
        control=row["control"],
    )


def _course_detail(identity: str) -> str:
    row = COURSES[identity]
    capacity = int(row["target"].split("/")[-1].replace("명", "").strip())
    return _detail_page(
        identity,
        row["title"],
        row["branch"],
        row["apply"],
        row["event"],
        row["target"],
        row["venue"],
        capacity,
        row["method"],
        row["fee"],
    )


def _fixture_pages() -> dict[str, str]:
    return {
        geochang.GEOCHANG_CANONICAL_URL: _list_page(
            1,
            [
                _course_card("10"),
                _course_card("11"),
                _course_card("12"),
                _course_card("12"),
                _course_card("12"),
            ],
        ),
        geochang.GEOCHANG_CANONICAL_URL + "?page=2&lc=&search_date=": _list_page(
            2, [_course_card("12")]
        ),
        geochang.GEOCHANG_CANONICAL_URL + "?page=3&lc=&search_date=": _list_page(3, []),
        geochang.GEOCHANG_CANONICAL_URL.replace("30020201", "30020203")
        + "?lc=10": _course_detail("10"),
        geochang.GEOCHANG_CANONICAL_URL.replace("30020201", "30020203")
        + "?lc=11": _course_detail("11"),
    }


class _Response:
    def __init__(self, url: str, html: str, *, status: int = 200, final_url: str | None = None):
        self.url = final_url or url
        self.content = html.encode("utf-8")
        self.status_code = status
        self.headers = {"content-type": "text/html"}
        self.history: list[Any] = []

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class _Session:
    def __init__(self, pages: dict[str, str | list[str]]):
        self.pages = deepcopy(pages)
        self.requests: list[str] = []
        self.closed = False

    def get(self, url: str, **_: Any) -> _Response:
        self.requests.append(url)
        value = self.pages[url]
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"no fixture response left for {url}")
            html = value.pop(0)
        else:
            html = value
        return _Response(url, html)

    def close(self) -> None:
        self.closed = True


def _collect(
    pages: dict[str, str | list[str]] | None = None,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], _Session]:
    fake = _Session(pages or _fixture_pages())
    current_today = kwargs.pop("today", "2026-07-23")
    rows, parser, meta = geochang.collect_geochang_education(
        TARGET,
        today=current_today,
        session_factory=lambda: fake,
        **kwargs,
    )
    return rows, parser, meta, fake


def test_exact_provider_and_candidate_alias_ownership() -> None:
    assert geochang.is_geochang_education_target(TARGET)
    for url in geochang.GEOCHANG_DETAIL_CANDIDATE_URLS:
        assert geochang.is_geochang_education_target({**TARGET, "url": url})
    assert not geochang.is_geochang_education_target({**TARGET, "url": geochang.GEOCHANG_GUIDE_URL})
    assert not geochang.is_geochang_education_target({**TARGET, "url": "https://www.geochang.go.kr/"})
    assert not geochang.is_geochang_education_target({**TARGET, "provider": "new-duplicate"})
    assert geochang.GEOCHANG_CANDIDATE_AUDIT[geochang.GEOCHANG_GUIDE_CANDIDATE_ID][
        "decision"
    ].startswith("excluded_application_guide")
    assert len(geochang.GEOCHANG_EXCLUDED_EVIDENCE) == 3


def test_complete_ledger_duplicate_sentinel_details_and_privacy_contract() -> None:
    rows, parser, meta, fake = _collect()
    assert parser == geochang.GEOCHANG_PARSER
    assert [row["provider_course_id"].rsplit(":", 1)[-1] for row in rows] == ["10", "11"]
    assert meta["source_rows"] == 6
    assert meta["source_identity_count"] == 3
    assert meta["duplicate_source_rows"] == 3
    assert meta["duplicate_source_lc"] == ["12", "12", "12"]
    assert meta["current_source_count"] == 2
    assert meta["expired_source_count"] == 4
    assert meta["pages"] == 2
    assert meta["post_last_page"] == 3
    assert meta["exact_empty_sentinel"]
    assert meta["first_page_rechecked"]
    assert meta["final_page_rechecked"]
    assert meta["sentinel_rechecked"]
    assert meta["details_complete"]
    assert meta["source_requests"] == 8
    assert meta["request_attempts"] == 8
    assert meta["list_requests"] == 6
    assert meta["detail_pages"] == 2
    assert meta["post_last_requests"] == 2
    assert meta["snapshot_complete"] and meta["full_snapshot_validated"]
    assert meta["branch_counts"] == {"거창흥사단": 1, "거창군평생교육센터": 1}
    assert meta["internal_application_control_count"] == 1
    assert meta["actionable_internal_application_control_count"] == 1
    assert meta["roster_fragment_control_count"] == 1
    assert meta["external_operator_control_count"] == 1
    assert fake.closed
    assert all("30020501" not in url for url in fake.requests)
    assert all("#lec_list" not in url for url in fake.requests)
    assert all("gcyka.or.kr" not in url for url in fake.requests)
    for row in rows:
        assert row["application_url"] == ""
        assert row["raw_url"].startswith(
            "https://educity.geochang.go.kr/E0003/30020203.asp?lc="
        )
        assert row["program_type"] == "교육"
        assert row["municipality_code"] == "4888000000"
        assert row["raw_fields"]["applicant_section_cells_parsed"] is False
        assert row["raw_fields"]["application_endpoint_fetched"] is False
        assert row["raw_fields"]["pii_endpoint_fetched"] is False
        serialized = repr(row)
        assert "055-" not in serialized
        assert "홍길동" not in serialized
        assert "김**" not in serialized
        assert "lec_doc" not in serialized
        assert "unsafe.example" not in serialized


def test_source_status_and_application_semantics_are_kept_separate() -> None:
    rows, _, meta, _ = _collect()
    by_id = {row["provider_course_id"].rsplit(":", 1)[-1]: row for row in rows}
    assert by_id["10"]["raw_status"] == "예정"
    assert by_id["10"]["status"] == "SCHEDULED"
    assert by_id["10"]["reservation_available"] is False
    assert by_id["10"]["application_type"] == "INFO_ONLY_SOURCE_STATUS"
    assert by_id["11"]["raw_status"] == "접수중"
    assert by_id["11"]["status"] == "OPEN"
    assert by_id["11"]["reservation_available"] is True
    assert by_id["11"]["application_type"] == "ONLINE_IDENTITY_REQUIRED_ENDPOINT_UNSTORED"
    assert meta["source_status_counts"] == {"예정": 1, "접수중": 1, "종료": 4}


def test_managed_session_and_bounded_limits_fail_closed() -> None:
    rows, _, meta = geochang.collect_geochang_education(TARGET, today="2026-07-23")
    assert not rows
    assert meta["configured_collection_error"] == "managed session_factory injection is required"

    rows, _, meta, fake = _collect(max_pages=2)
    assert not rows
    assert meta["source_cap_reached"]
    assert meta["source_requests"] == 1
    assert fake.requests == [geochang.GEOCHANG_CANONICAL_URL]

    rows, _, meta, fake = _collect(detail_limit=1)
    assert not rows
    assert meta["source_cap_reached"]
    assert meta["source_requests"] == 3
    assert all("30020203" not in url for url in fake.requests)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 0},
        {"max_pages": 301},
        {"detail_limit": 501},
        {"today": "2026/07/23"},
    ],
)
def test_invalid_or_unbounded_arguments_fail_closed(kwargs: dict[str, Any]) -> None:
    rows, _, meta, fake = _collect(**kwargs)
    assert not rows
    assert meta["configured_collection_error"]
    assert fake.requests == []


def test_exact_empty_sentinel_is_mandatory() -> None:
    pages = _fixture_pages()
    pages[geochang.GEOCHANG_CANONICAL_URL + "?page=3&lc=&search_date="] = _list_page(
        3, [_course_card("12")]
    )
    rows, _, meta, _ = _collect(pages)
    assert not rows
    assert "expected 0 source rows" in meta["configured_collection_error"]


def test_conflicting_duplicate_identity_fails_closed() -> None:
    pages = _fixture_pages()
    conflicting = _card(
        "12",
        "다른 제목",
        "종료",
        COURSES["12"]["apply"],
        COURSES["12"]["event"],
        COURSES["12"]["target"],
        COURSES["12"]["venue"],
    )
    pages[geochang.GEOCHANG_CANONICAL_URL + "?page=2&lc=&search_date="] = _list_page(2, [conflicting])
    rows, _, meta, _ = _collect(pages)
    assert not rows
    assert "conflicting duplicate source rows" in meta["configured_collection_error"]


def test_first_final_and_sentinel_recheck_detect_drift() -> None:
    pages = _fixture_pages()
    first = pages[geochang.GEOCHANG_CANONICAL_URL]
    assert isinstance(first, str)
    drifted = first.replace("가족 역사교실", "변경된 역사교실", 1)
    pages[geochang.GEOCHANG_CANONICAL_URL] = [first, drifted]
    rows, _, meta, _ = _collect(pages)
    assert not rows
    assert "first page changed" in meta["configured_collection_error"]


def test_form_detail_and_branch_drift_fail_closed() -> None:
    pages = _fixture_pages()
    first = pages[geochang.GEOCHANG_CANONICAL_URL]
    assert isinstance(first, str)
    pages[geochang.GEOCHANG_CANONICAL_URL] = first.replace('method="GET"', 'method="POST"', 1)
    rows, _, meta, _ = _collect(pages)
    assert not rows
    assert "header GET search form changed" in meta["configured_collection_error"]

    pages = _fixture_pages()
    detail_url = geochang.GEOCHANG_CANONICAL_URL.replace("30020201", "30020203") + "?lc=10"
    detail = pages[detail_url]
    assert isinstance(detail, str)
    pages[detail_url] = detail.replace("거창흥사단 (문의", "미등록기관 (문의", 1)
    rows, _, meta, _ = _collect(pages)
    assert not rows
    assert "unknown official branch" in meta["configured_collection_error"]


def test_dedupe_and_privacy_sabotage_fail_closed() -> None:
    rows, _, meta, _ = _collect(dedupe_rows=lambda rows: rows[:-1])
    assert not rows
    assert "dedupe changed" in meta["configured_collection_error"]

    def add_phone(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows[0]["phone"] = "055-123-4567"
        return rows

    rows, _, meta, _ = _collect(dedupe_rows=add_phone)
    assert not rows
    assert "forbidden key phone" in meta["configured_collection_error"]


def test_retry_is_bounded_and_logical_request_count_is_stable() -> None:
    fake = _Session(_fixture_pages())
    attempts = 0

    def flaky(session: _Session, url: str, timeout: int) -> _Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise requests.Timeout("once")
        return session.get(url, timeout=timeout)

    rows, _, meta = geochang.collect_geochang_education(
        TARGET,
        today="2026-07-23",
        session_factory=lambda: fake,
        fetcher=flaky,
    )
    assert len(rows) == 2
    assert meta["source_requests"] == 8
    assert meta["request_attempts"] == 9
    assert attempts == 9


def test_request_allowlist_rejects_application_search_fragment_and_query_drift() -> None:
    assert geochang._allowed_request_url(geochang.GEOCHANG_CANONICAL_URL)
    assert geochang._allowed_request_url(
        geochang.GEOCHANG_CANONICAL_URL + "?page=153&lc=&search_date="
    )
    assert geochang._allowed_request_url(
        geochang.GEOCHANG_CANONICAL_URL.replace("30020201", "30020203") + "?lc=1992"
    )
    assert not geochang._allowed_request_url(geochang.GEOCHANG_GUIDE_URL)
    assert not geochang._allowed_request_url(
        geochang.GEOCHANG_CANONICAL_URL + "?st=AI"
    )
    assert not geochang._allowed_request_url(
        geochang.GEOCHANG_CANONICAL_URL.replace("30020201", "30020501") + "?lc=1"
    )
    assert not geochang._allowed_request_url(
        geochang.GEOCHANG_CANONICAL_URL.replace("30020201", "30020203") + "?lc=1#lec_list"
    )


def test_live_baseline_and_candidate_hashes_are_internally_consistent() -> None:
    baseline = geochang.GEOCHANG_LIVE_AUDIT_BASELINE
    assert baseline["source_rows"] == 758
    assert baseline["source_identity_count"] + baseline["duplicate_source_rows"] == 758
    assert baseline["pages"] * baseline["page_size"] - 2 == baseline["source_rows"]
    assert baseline["requests_per_snapshot"] == (
        baseline["pages"] + 1 + baseline["current_rows"] + 3
    )
    assert baseline["two_snapshot_requests"] == baseline["requests_per_snapshot"] * 2
    assert len(geochang.GEOCHANG_DETAIL_CANDIDATE_IDS) == 4
    assert set(baseline["candidate_lc_pages"]) == {"1992", "1991", "2006", "1985"}


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_GEOCHANG_TESTS") != "1",
    reason="set RUN_LIVE_GEOCHANG_TESTS=1 for the bounded two-snapshot live audit",
)
def test_live_two_identical_complete_snapshots() -> None:
    snapshots = []
    for _ in range(2):
        rows, parser, meta = geochang.collect_geochang_education(
            TARGET,
            today="2026-07-23",
            timeout=30,
            max_pages=200,
            detail_limit=100,
            allow_raw_requests_for_tests=True,
        )
        assert parser == geochang.GEOCHANG_PARSER
        assert meta["configured_collection_error"] == ""
        assert meta["source_rows"] == 758
        assert meta["source_identity_count"] == 752
        assert meta["duplicate_source_rows"] == 6
        assert meta["pages"] == 152
        assert meta["post_last_page"] == 153
        assert meta["current_source_count"] == 42
        assert meta["returned_count"] == 42
        assert meta["source_requests"] == 198
        assert meta["list_requests"] == 156
        assert meta["detail_pages"] == 42
        assert meta["branch_counts"] == geochang.GEOCHANG_LIVE_AUDIT_BASELINE["branch_counts"]
        assert meta["source_status_counts"] == geochang.GEOCHANG_LIVE_AUDIT_BASELINE[
            "source_status_counts"
        ]
        assert meta["current_source_status_counts"] == geochang.GEOCHANG_LIVE_AUDIT_BASELINE[
            "current_source_status_counts"
        ]
        assert meta["source_lc_sha256"] == geochang.GEOCHANG_LIVE_AUDIT_BASELINE[
            "ordered_source_lc_sha256"
        ]
        assert meta["current_lc_sha256"] == geochang.GEOCHANG_LIVE_AUDIT_BASELINE[
            "ordered_current_lc_sha256"
        ]
        assert all(row["application_url"] == "" for row in rows)
        assert all("055-" not in repr(row) for row in rows)
        snapshots.append((rows, meta))
    first_rows, first_meta = snapshots[0]
    second_rows, second_meta = snapshots[1]
    assert first_rows == second_rows
    keys = (
        "source_rows",
        "source_identity_count",
        "duplicate_source_rows",
        "source_lc_sha256",
        "current_lc_sha256",
        "source_status_counts",
        "current_source_status_counts",
        "branch_counts",
        "source_requests",
    )
    assert {key: first_meta[key] for key in keys} == {
        key: second_meta[key] for key in keys
    }
