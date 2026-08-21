from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_pyeongtaek as pyeongtaek


@dataclass(frozen=True)
class Target:
    provider: str
    url: str
    branch: str = pyeongtaek.PYEONGTAEK_MUNICIPALITY_NAME


class DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FixtureFetcher:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, _session: DummySession, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        self.calls.append(url)
        return BeautifulSoup(self.pages[url], "lxml")


def sessions() -> tuple[list[DummySession], Any]:
    created: list[DummySession] = []

    def factory() -> DummySession:
        value = DummySession()
        created.append(value)
        return value

    return created, factory


def table(headers: list[str], body: str) -> str:
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{header}</th>" for header in headers)
        + f"</tr></thead><tbody>{body}</tbody></table>"
    )


REGULAR_HEADERS = [
    "번호", "기수(학습공간)", "강좌명(접수기간)", "과목유형", "교육시간",
    "수강료", "신청현황(신청/정원)",
]
ONGOING_HEADERS = [
    "번호", "사업명", "강좌명", "장소", "교육기간", "시간/요일",
    "확정인원 /신청인원 /정원", "신청방식",
]
PTLIB_HEADERS = [
    "기관명", "프로그램명", "강좌기간", "대상", "온라인 (신청/정원)", "상태",
]
GOE_HEADERS = ["강좌명", "접수인원", "강좌기간", "접수기간", "접수상태"]


def empty_page(headers: list[str], message: str = "검색결과가 없습니다.") -> str:
    return table(headers, f'<tr><td colspan="{len(headers)}">{message}</td></tr>')


def pair_table(pairs: list[tuple[str, str]]) -> str:
    return "<table>" + "".join(
        f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in pairs
    ) + "</table>"


def regular_page(
    branch: pyeongtaek.RegularBranch,
    identity: str,
    *,
    include_capacity: bool = True,
) -> str:
    title = f"[{branch.key}] 미래 강좌"
    capacity = "1 / 10 " if include_capacity else ""
    body = f"""
    <tr><th>1</th><td>2099-2 {branch.key}[테스트]</td>
      <td><a onclick="goView('{identity}')"><strong>{title}</strong>
        <small>(2099-07-01 ~ 2099-07-10)</small></a></td>
      <td>문화예술교육</td><td>화 10:00 ~ 12:00</td><td>10,000원</td>
      <td>{capacity}<span class="stat-bagde">접수중</span></td></tr>
    """
    return (
        '<div id="contents">2099년 제2기 정기교육 프로그램 안내 '
        '교육기간 : 2099. 8. 3.(월) ~ 2099. 12. 19.(토)'
        + table(REGULAR_HEADERS, body)
        + "</div>"
    )


def regular_detail(
    branch: pyeongtaek.RegularBranch,
    *,
    include_capacity: bool = True,
) -> str:
    pairs = [
            ("강좌명", f"[{branch.key}] 미래 강좌"),
            ("학습공간", f"{branch.key}[테스트]"),
            ("기수", "2099-2"),
            ("교육장소", "101호"),
            ("과목유형", "문화예술교육"),
            ("교육대상", "평택시민"),
            ("교육일정", "2099-08-03 ~ 2099-12-19"),
            ("교육시간/요일", "화 10:00 ~ 12:00"),
            ("수강료", "10,000원"),
            ("상태", "접수중"),
        ]
    if include_capacity:
        pairs.insert(-1, ("인원(신청/정원)", "1 / 10"))
    return pair_table(pairs)


def ongoing_page() -> str:
    rows = """
    <tr><th>2</th><td>미래사업</td><td><span class="stat-bagde">신청중</span>
      <a href="/learning/eduProgram/view.do?eIdx=901">[북부] 미래 상시강좌</a></td>
      <td>북부학습공간 201호</td><td>2099.08.01 ~ 2099.09.01</td>
      <td>10:00 ~ 12:00 (화)</td><td>0/1/10</td><td>온라인접수</td></tr>
    <tr><th>1</th><td>과거사업</td><td><span class="stat-bagde">수강종료</span>
      <a href="/learning/eduProgram/view.do?eIdx=900">과거 상시강좌</a></td>
      <td>외부 교육장</td><td>2020.01.01 ~ 2020.02.01</td>
      <td>10:00 ~ 12:00</td><td>1/1/1</td><td>방문접수</td></tr>
    """
    return table(ONGOING_HEADERS, rows)


def ongoing_detail() -> str:
    return pair_table(
        [
            ("강좌명", "[북부] 미래 상시강좌"),
            ("교육기간", "2099.08.01 ~ 2099.09.01"),
            ("접수기간", "2099.07.01 ~ 2099.07.31"),
            ("강의시간", "10:00 ~ 12:00 (화)"),
            ("대상", "평택시민"),
            ("정원", "10명"),
            ("신청인원", "1명"),
            ("확정인원", "0명"),
            ("기관", "평택시 평생학습과"),
            ("장소", "북부학습공간 201호"),
            ("수강료", "무료"),
            ("접수방법", "온라인접수"),
        ]
    )


def lifelong_fixture(*, include_regular_capacity: bool = True) -> dict[str, str]:
    pages: dict[str, str] = {}
    for offset, branch in enumerate(pyeongtaek.REGULAR_BRANCHES, start=1):
        identity = str(800 + offset)
        pages[pyeongtaek.regular_list_url(branch.code, 1)] = regular_page(
            branch,
            identity,
            include_capacity=include_regular_capacity,
        )
        pages[pyeongtaek.regular_list_url(branch.code, 2)] = empty_page(REGULAR_HEADERS)
        pages[pyeongtaek.regular_detail_url(identity)] = regular_detail(
            branch,
            include_capacity=include_regular_capacity,
        )
    pages[pyeongtaek.ongoing_list_url(1)] = ongoing_page()
    pages[pyeongtaek.ongoing_list_url(2)] = empty_page(ONGOING_HEADERS)
    pages[pyeongtaek.ongoing_detail_url("901")] = ongoing_detail()
    return pages


def ptlib_fixture() -> dict[str, str]:
    body = """
    <tr><td>초록</td><td><a onclick="fnDetail('701')">미래 도서관 강좌</a></td>
      <td>2099.08.01(토) ~ 2099.08.20(목)</td><td>성인</td><td>1/10</td>
      <td>접수중</td></tr>
    <tr><td>배다리</td><td><a onclick="fnDetail('700')">과거 도서관 강좌</a></td>
      <td>2020.01.01(수)</td><td>성인</td><td>10/10</td><td>종료</td></tr>
    """
    detail = pair_table(
        [
            ("문화행사명", "미래 도서관 강좌"),
            ("기관명", "지산초록도서관"),
            ("장소", "지하강의실"),
            ("모집기간", "2099.07.01 09:00 ~ 2099.07.31 18:00"),
            ("교육기간", "2099.08.01 ~ 2099.08.20"),
            ("시간", "토 10:00~12:00"),
            ("대상", "성인"),
            # PTLIB legitimately uses a channel-specific label here.
            ("방문접수", "1/10 (신청/정원) 접수중"),
        ]
    )
    return {
        pyeongtaek.ptlib_list_url(1): table(PTLIB_HEADERS, body),
        # PTLIB's real sentinel is an exact empty table without a message.
        pyeongtaek.ptlib_list_url(2): table(PTLIB_HEADERS, ""),
        pyeongtaek.ptlib_detail_url("701"): detail,
    }


def goe_list(source: pyeongtaek.GoeSource, *, current: bool) -> str:
    identity = {"lifelong": "601", "reading": "602", "parent": "603"}[source.key]
    period = "2099-08-01 ~ 2099-09-01" if current else "2020-01-01 ~ 2020-02-01"
    status = "수강신청" if current else "수강종료"
    body = f"""
    <tr><td><dl><dt><a class="detail-btn" keyValue1="28" keyValue2="0"
      keyValue3="{identity}" keyValue4="1" keyValue5="{source.large_code}">
      {source.category} 미래 강좌</a></dt><dd class="con">대상 : 시민</dd></dl></td>
      <td>온라인 1 / 10 대기자 0 / 2</td><td>{period} 화 10:00 ~ 12:00</td>
      <td>2099-07-01 10:00 ~ 2099-07-31 18:00</td><td>{status}</td></tr>
    """
    return table(GOE_HEADERS, body)


def goe_detail() -> str:
    return pair_table(
        [
            ("강의 분류", "평생교육프로그램"),
            ("강의 설명", "미래 강좌 상세"),
            ("강의장소", "1층 강의실"),
            ("강사명", "홍길동"),
            ("준비물 및 재료비", "무료"),
            ("강의대상", "시민"),
            ("접수기간", "2099-07-01 10:00 ~ 2099-07-31 18:00"),
            ("강의기간(*)", "2099-08-01 ~ 2099-09-01"),
            ("강의시간", "10:00 ~ 12:00"),
            ("강의요일", "화요일"),
            ("모집방식", "선착순"),
            ("현재 참여 / 모집", "1 / 10"),
            ("현재 대기자 / 대기자", "0 / 2"),
        ]
    )


def goe_fixture() -> dict[str, str]:
    pages: dict[str, str] = {}
    for source in pyeongtaek.GOE_SOURCES:
        current = source.key != "parent"
        identity = {"lifelong": "601", "reading": "602", "parent": "603"}[source.key]
        pages[pyeongtaek.goe_list_url(source, 1)] = goe_list(source, current=current)
        pages[pyeongtaek.goe_list_url(source, 2)] = empty_page(
            GOE_HEADERS, "등록된 프로그램이 없습니다."
        )
        if current:
            item = {"identity": identity, "group_idx": "28", "category_idx": "0"}
            pages[pyeongtaek.goe_detail_url(source, item)] = goe_detail()
    return pages


def run_lifelong(pages: dict[str, str], *, max_pages: int = 8, detail_limit: int = 4):
    created, factory = sessions()
    fetcher = FixtureFetcher(pages)
    target = Target(
        pyeongtaek.PYEONGTAEK_LIFELONG_PROVIDER,
        pyeongtaek.PYEONGTAEK_LIFELONG_INSTRUCTION_URL,
    )
    result = pyeongtaek.collect_pyeongtaek_lifelong_courses(
        target,
        timeout=7,
        max_pages=max_pages,
        detail_limit=detail_limit,
        fetcher=fetcher,
        session_factory=factory,
        today="2026-07-20",
    )
    return result, fetcher, created


def test_lifelong_unions_complete_disjoint_catalogues_with_exact_branches() -> None:
    (rows, parser, meta), fetcher, created = run_lifelong(lifelong_fixture())

    assert parser == pyeongtaek.PYEONGTAEK_LIFELONG_PARSER
    assert len(rows) == 4
    assert meta["source_total"] == 5
    assert meta["source_totals"] == {
        "regular:북부": 1,
        "regular:남부": 1,
        "regular:서부": 1,
        "ongoing": 2,
    }
    assert meta["pages"] == 8
    assert meta["sentinel_pages"] == 4
    assert meta["detail_pages"] == 4
    assert meta["snapshot_complete"] is True
    assert {row["branch"] for row in rows} == {
        "북부학습공간", "남부학습공간", "서부학습공간",
    }
    assert len({row["provider_course_id"] for row in rows}) == 4
    assert any(":regular:" in row["provider_course_id"] for row in rows)
    assert any(":ongoing:" in row["provider_course_id"] for row in rows)
    ongoing = next(
        row for row in rows if ":ongoing:" in row["provider_course_id"]
    )
    assert ongoing["branch_code"] == "regular:1"
    assert ongoing["address"] == pyeongtaek.REGULAR_BRANCHES[0].address
    assert ongoing["venue_address"] == pyeongtaek.REGULAR_BRANCHES[0].address
    assert len(fetcher.calls) == 12
    assert all(session.closed for session in created)


def test_lifelong_accepts_officially_omitted_regular_capacity() -> None:
    (rows, _parser, meta), _, _ = run_lifelong(
        lifelong_fixture(include_regular_capacity=False)
    )

    assert meta["snapshot_complete"] is True
    regular = [row for row in rows if ":regular:" in row["provider_course_id"]]
    assert len(regular) == 3
    assert all(row["capacity"] == "" for row in regular)
    assert all(row["raw_fields"]["capacity_omitted"] is True for row in regular)


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "expected"),
    [(7, 4, "max_pages"), (8, 3, "detail_limit")],
)
def test_lifelong_caps_fail_closed(
    max_pages: int, detail_limit: int, expected: str
) -> None:
    (rows, _, meta), _, _ = run_lifelong(
        lifelong_fixture(), max_pages=max_pages, detail_limit=detail_limit
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert expected in meta["configured_collection_error"]


def test_lifelong_detail_failure_never_publishes_partial_snapshot() -> None:
    pages = lifelong_fixture()
    pages.pop(pyeongtaek.ongoing_detail_url("901"))
    (rows, _, meta), _, _ = run_lifelong(pages)
    assert rows == []
    assert meta["detail_attempts"] == 4
    assert meta["detail_pages"] == 3
    assert "KeyError" in meta["configured_collection_error"]


def test_ptlib_complete_catalogue_current_filter_and_branch_owner() -> None:
    created, factory = sessions()
    fetcher = FixtureFetcher(ptlib_fixture())
    target = Target(pyeongtaek.PTLIB_PROVIDER, pyeongtaek.PTLIB_URL)
    rows, parser, meta = pyeongtaek.collect_ptlib_courses(
        target,
        timeout=7,
        max_pages=2,
        detail_limit=1,
        fetcher=fetcher,
        session_factory=factory,
        today="2026-07-20",
    )

    assert parser == pyeongtaek.PTLIB_PARSER
    assert len(rows) == 1
    assert rows[0]["provider_course_id"].endswith(":lecture:701")
    assert rows[0]["branch"] == "지산초록도서관"
    assert rows[0]["branch_code"] == "MD"
    assert rows[0]["raw_url"] == pyeongtaek.ptlib_detail_url("701")
    assert meta["source_total"] == 2
    assert meta["current_count"] == 1
    assert meta["snapshot_complete"] is True
    assert all(session.closed for session in created)


def test_ptlib_schema_or_detail_cap_fails_closed() -> None:
    pages = ptlib_fixture()
    pages[pyeongtaek.ptlib_list_url(1)] = pages[pyeongtaek.ptlib_list_url(1)].replace(
        "기관명", "시설명", 1
    )
    created, factory = sessions()
    target = Target(pyeongtaek.PTLIB_PROVIDER, pyeongtaek.PTLIB_URL)
    rows, _, meta = pyeongtaek.collect_ptlib_courses(
        target,
        timeout=7,
        max_pages=2,
        detail_limit=1,
        fetcher=FixtureFetcher(pages),
        session_factory=factory,
        today="2026-07-20",
    )
    assert rows == []
    assert "headers changed" in meta["configured_collection_error"]

    created, factory = sessions()
    rows, _, meta = pyeongtaek.collect_ptlib_courses(
        target,
        timeout=7,
        max_pages=2,
        detail_limit=0,
        fetcher=FixtureFetcher(ptlib_fixture()),
        session_factory=factory,
        today="2026-07-20",
    )
    assert rows == []
    assert "detail_limit" in meta["configured_collection_error"]


def test_goe_is_distinct_provider_and_proves_three_catalogue_sentinels() -> None:
    created, factory = sessions()
    fetcher = FixtureFetcher(goe_fixture())
    target = Target(
        pyeongtaek.PYEONGTAEK_GOE_PROVIDER,
        pyeongtaek.PYEONGTAEK_GOE_ROOT_URL,
    )
    rows, parser, meta = pyeongtaek.collect_pyeongtaek_goe_courses(
        target,
        timeout=7,
        max_pages=6,
        detail_limit=2,
        fetcher=fetcher,
        session_factory=factory,
        today="2026-07-20",
    )

    assert parser == pyeongtaek.PYEONGTAEK_GOE_PARSER
    assert len(rows) == 2
    assert all(row["provider"] != pyeongtaek.PTLIB_PROVIDER for row in rows)
    assert {row["branch"] for row in rows} == {pyeongtaek.PYEONGTAEK_GOE_BRANCH}
    assert {row["address"] for row in rows} == {pyeongtaek.PYEONGTAEK_GOE_ADDRESS}
    assert meta["source_totals"] == {"lifelong": 1, "reading": 1, "parent": 1}
    assert meta["pages"] == 6
    assert meta["sentinel_pages"] == 3
    assert meta["detail_pages"] == 2
    assert meta["snapshot_complete"] is True
    assert all(session.closed for session in created)


def test_exact_routes_stable_ids_and_tls_verification_contract() -> None:
    lifelong = Target(
        pyeongtaek.PYEONGTAEK_LIFELONG_PROVIDER,
        pyeongtaek.PYEONGTAEK_LIFELONG_INSTRUCTION_URL,
    )
    ptlib = Target(pyeongtaek.PTLIB_PROVIDER, pyeongtaek.PTLIB_URL)
    goe = Target(pyeongtaek.PYEONGTAEK_GOE_PROVIDER, pyeongtaek.PYEONGTAEK_GOE_ROOT_URL)
    assert pyeongtaek.is_target(lifelong)
    assert pyeongtaek.is_target(ptlib)
    assert pyeongtaek.is_target(goe)
    assert not pyeongtaek.is_target(
        Target(pyeongtaek.PTLIB_PROVIDER, "https://evil.example/lectureList.do")
    )
    assert not pyeongtaek.is_target(
        Target(pyeongtaek.PYEONGTAEK_GOE_PROVIDER, "http://lib.goe.go.kr/pt/index.do")
    )

    first, _, _ = run_lifelong(lifelong_fixture())[0]
    second, _, _ = run_lifelong(lifelong_fixture())[0]
    assert [row["provider_course_id"] for row in first] == [
        row["provider_course_id"] for row in second
    ]
    source = inspect.getsource(pyeongtaek)
    assert "verify=False" not in source
    assert "allow_redirects=False" in source
