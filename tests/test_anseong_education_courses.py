from __future__ import annotations

from dataclasses import dataclass
import math
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_anseong as anseong


@dataclass
class Target:
    provider: str = anseong.ANSEONG_PROVIDER
    name: str = "안성 배움e"
    branch: str = "경기도 안성시"
    url: str = anseong.ANSEONG_URL


def _headers(source: anseong.AnseongSource) -> str:
    return "".join(f"<th>{value}</th>" for value in source.headers)


def _personal_row(
    identity: str,
    *,
    number: int = 1,
    title: str = "로봇 만들기",
    branch: str = "평생학습관",
    region: str = "안성2동",
    apply_period: str = "2099-01-01 ~ 2099-01-10 / 09:00 ~ 18:00",
    education_period: str = "2099-02-01 ~ 2099-02-03 월 수 / 10:00 ~ 12:00",
    capacity: str = "10 / 20 0 / 5",
    method: str = "인터넷",
    status: str = "접수중",
) -> str:
    href = (
        f"{anseong.ANSEONG_PERSONAL_DETAIL_PATH}?mId={anseong.ANSEONG_MID}"
        f"&eduLctreNo={identity}&page=1"
    )
    return f"""
    <tr>
      <td>{number}</td><td>{region}<br>({branch})</td>
      <td><a href="{href}">{title}</a></td>
      <td>{apply_period}</td><td>선착순</td><td>{capacity}</td>
      <td>{education_period}</td><td>무료</td><td>{method}</td>
      <td><a href="{href}">{status}</a></td>
    </tr>
    """


def _group_row(
    identity: str,
    *,
    number: int = 1,
    title: str = "단체 환경수업",
    branch: str = "안성환경교육센터",
    region: str = "보개면",
    apply_period: str = "2099-01-01 ~ 2099-01-10 / 09:00 ~ 18:00",
    education_date: str = "2099-02-07 ~ 10:00",
    capacity: str = "1 / 10",
    method: str = "인터넷",
    status: str = "접수중",
) -> str:
    href = (
        f"{anseong.ANSEONG_GROUP_DETAIL_PATH}?mId={anseong.ANSEONG_MID}"
        f"&eduGroupLctreNo={identity}&page=1"
    )
    return f"""
    <tr>
      <td>{number}</td><td>{region}<br>({branch})</td>
      <td><a href="{href}">{title}</a></td>
      <td>{apply_period}</td><td>{capacity}</td><td>{education_date}</td>
      <td>무료</td><td>{method}</td><td><a href="{href}">{status}</a></td>
    </tr>
    """


def _list_page(
    source: anseong.AnseongSource,
    *,
    page: int,
    total: int,
    rows: str = "",
) -> str:
    last = max(1, math.ceil(total / anseong.ANSEONG_PAGE_SIZE))
    body = rows or '<tr><td colspan="9">등록된 정보가 없습니다.</td></tr>'
    return f"""
    <html><body>
      <form id="list" action="{source.list_path}">
        <input name="mId" value="{anseong.ANSEONG_MID}">
        <input name="page" value="1">
      </form>
      <p class="page_num">전체 {total:,} 건 [ {page if page <= last else 1} / {last}페이지 ]</p>
      <table class="bod_maintain">
        <thead><tr>{_headers(source)}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </body></html>
    """


def _dl(key: str, value: str) -> str:
    return f"<dl><dt>{key}</dt><dd>{value}</dd></dl>"


def _personal_detail(
    identity: str,
    *,
    title: str = "로봇 만들기",
    detail_status: str = "접수중",
    apply_period: str = "2099-01-01 09:00 ~ 2099-01-10 18:00",
    education_period: str = "2099-02-01 ~ 2099-02-03",
    schedule: str = "월 수 / 10:00 ~ 12:00",
    capacity: str = "신청 10 명 / 모집인원 20 명 (대기신청 0 명 / 대기정원 5 명)",
    venue: str = "안성시 평생학습관 3층 강의실 (경기도 안성시)",
    instructor: str = "김강사",
    gate: bool = True,
) -> str:
    gate_html = '<span class="bod_btn">일반 회원으로 로그인하세요.</span>' if gate else ""
    pairs = "".join(
        (
            _dl("접수기간", apply_period),
            _dl("접수현황", capacity),
            _dl("선발방법", "선착순"),
            _dl("교육기간", education_period),
            _dl("교육시간", schedule),
            _dl("교육장", venue),
            _dl("강사명", instructor),
            _dl("수강료", "무료"),
            _dl("문의전화", "031-678-0000"),
            _dl("강좌소개", "즐거운 교육"),
            _dl("유의사항", "준비물 지참"),
        )
    )
    return f"""
    <html><body><div class="learning_wrap view_wrap"><div class="learning_content">
      <div class="bod_title"><span class="bod_state_type">{detail_status}</span>
        <span class="bod_subject">{title}</span>{gate_html}</div>
      <div class="bod_write">{pairs}</div>
    </div></div></body></html>
    """


def _group_detail(
    identity: str,
    *,
    title: str = "단체 환경수업",
    detail_status: str = "접수중",
    apply_period: str = "2099-01-01 09:00 ~ 2099-01-10 18:00",
    education_date: str = "2099-02-07",
    schedule: str = "10:00 ~ 12:00",
    capacity: str = "신청 1 명 / 모집인원 10 명",
    venue: str = "안성환경교육센터 단체교육장 (경기도 안성시)",
    instructor: str = "이강사",
    gate: bool = True,
) -> str:
    gate_html = '<span class="bod_btn">일반 회원으로 로그인하세요.</span>' if gate else ""
    pairs = "".join(
        (
            _dl("접수기간", apply_period),
            _dl("접수현황", capacity),
            _dl("교육일시", education_date),
            _dl("교육시간", schedule),
            _dl("단체교육장", venue),
            _dl("강사명", instructor),
            _dl("수강료", "무료"),
            _dl("문의전화", "031-678-0000"),
            _dl("강좌소개", "단체 교육"),
            _dl("유의사항", ""),
        )
    )
    return f"""
    <html><body><div class="learning_wrap view_wrap"><div class="learning_content">
      <div class="bod_title"><span class="bod_state_type">{detail_status}</span>
        <span class="bod_subject">{title}</span>{gate_html}</div>
      <div class="bod_write">{pairs}</div>
    </div></div></body></html>
    """


def _fetcher(
    pages: dict[tuple[str, int], str],
    details: dict[tuple[str, str], str],
    calls: list[str] | None = None,
    fail_once: set[tuple[str, str]] | None = None,
):
    failures = set(fail_once or set())

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        if calls is not None:
            calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        for source in anseong.ANSEONG_SOURCES:
            if parsed.path == source.list_path:
                page = int((query.get("page") or ["1"])[0])
                return BeautifulSoup(pages[(source.key, page)], "lxml")
            if parsed.path == source.detail_path:
                identity = query[source.identity_param][0]
                key = (source.key, identity)
                if key in failures:
                    failures.remove(key)
                    raise TimeoutError("transient detail timeout")
                return BeautifulSoup(details[key], "lxml")
        raise AssertionError(url)

    return fetch


def _base_catalogue() -> tuple[dict[tuple[str, int], str], dict[tuple[str, str], str]]:
    personal = anseong.ANSEONG_PERSONAL_SOURCE
    group = anseong.ANSEONG_GROUP_SOURCE
    rows = "".join(
        (
            _personal_row(
                "100",
                number=3,
                status="접수마감",
                apply_period="2099-01-01 ~ 2099-01-10 / 09:00 ~ 18:00",
                capacity="10 / 20 0 / 5",
            ),
            _personal_row(
                "101",
                number=2,
                status="대기접수중",
                apply_period="2099-01-11 ~ 2099-01-20 / 09:00 ~ 18:00",
                capacity="2 / 3 1 / 5",
            ),
            _personal_row(
                "90",
                number=1,
                title="지난 강좌",
                branch="지난 기관",
                apply_period="2020-01-01 ~ 2020-01-02 / 09:00 ~ 18:00",
                education_period="2020-02-01 ~ 2020-02-02 월 / 10:00 ~ 12:00",
                capacity="1 / 10",
                status="접수마감",
            ),
        )
    )
    group_expired = _group_row(
        "1",
        title="단체접수 테스트",
        branch="평생학습관",
        apply_period="2020-01-01 ~ 2020-01-02 / 00:00 ~ 00:00",
        education_date="2020-02-03 ~ 10:00",
        status="접수마감",
    )
    pages = {
        ("personal", 1): _list_page(personal, page=1, total=3, rows=rows),
        ("personal", 2): _list_page(personal, page=2, total=3),
        ("group", 1): _list_page(group, page=1, total=1, rows=group_expired),
        ("group", 2): _list_page(group, page=2, total=1),
    }
    details = {
        ("personal", "100"): _personal_detail(
            "100",
            detail_status="접수중",
            apply_period="2099-01-01 09:00 ~ 2099-01-10 18:00",
            capacity="신청 10 명 / 모집인원 20 명 (대기신청 0 명 / 대기정원 5 명)",
            gate=False,
        ),
        ("personal", "101"): _personal_detail(
            "101",
            detail_status="접수중",
            apply_period="2099-01-11 09:00 ~ 2099-01-20 18:00",
            capacity="신청 2 명 / 모집인원 3 명 (대기신청 1 명 / 대기정원 5 명)",
        ),
    }
    return pages, details


def _collect(
    pages: dict[tuple[str, int], str],
    details: dict[tuple[str, str], str],
    **kwargs,
):
    calls = kwargs.pop("calls", None)
    fail_once = kwargs.pop("fail_once", None)
    return anseong.collect_anseong_education_courses(
        Target(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", 10),
        detail_limit=kwargs.pop("detail_limit", 10),
        fetcher=_fetcher(pages, details, calls, fail_once),
        session_factory=lambda: object(),
        today=kwargs.pop("today", "2099-01-15"),
        max_workers=2,
        **kwargs,
    )


def test_collects_both_catalogues_and_collapses_additional_application_round() -> None:
    pages, details = _base_catalogue()
    calls: list[str] = []

    rows, parser, meta = _collect(pages, details, calls=calls)

    assert parser == anseong.ANSEONG_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"] == f"{anseong.ANSEONG_PROVIDER}:personal:101"
    assert row["title"] == "로봇 만들기"
    assert row["branch"] == "평생학습관"
    assert row["status"] == "OPEN"
    assert row["period"] == "2099-02-01 ~ 2099-02-03"
    assert row["apply_period"] == "2099-01-11 ~ 2099-01-20"
    assert row["venue_name"] == "안성시 평생학습관 3층 강의실 (경기도 안성시)"
    assert row["instructor"] == "김강사"
    assert row["capacity_current"] == 2
    assert row["capacity_total"] == 3
    assert row["waitlist_current"] == 1
    assert row["waitlist_total"] == 5
    assert row["reservation_available"] is True
    assert row["application_url"] == anseong.anseong_detail_url("personal", "101")
    assert row["raw_fields"]["duplicate_application_round_ids"] == ["personal:100"]
    assert meta["personal_source_total"] == 3
    assert meta["group_source_total"] == 1
    assert meta["source_total"] == 4
    assert meta["list_requests"] == 4
    assert meta["current_candidate_count"] == 2
    assert meta["duplicate_application_group_count"] == 1
    assert meta["duplicate_application_round_count"] == 1
    assert meta["current_count"] == 1
    assert meta["expired_count"] == 2
    assert meta["detail_pages"] == 2
    assert meta["detail_status_difference_count"] == 2
    assert meta["snapshot_complete"] is True
    assert len([url for url in calls if "selectEduLctreWebList" in url]) == 2
    assert len([url for url in calls if "selectEduGroupLctreWebList" in url]) == 2


def test_group_current_course_is_collected_and_detailed() -> None:
    personal = anseong.ANSEONG_PERSONAL_SOURCE
    group = anseong.ANSEONG_GROUP_SOURCE
    pages = {
        ("personal", 1): _list_page(personal, page=1, total=0),
        ("personal", 2): _list_page(personal, page=2, total=0),
        ("group", 1): _list_page(
            group, page=1, total=1, rows=_group_row("77")
        ),
        ("group", 2): _list_page(group, page=2, total=1),
    }
    details = {("group", "77"): _group_detail("77")}

    rows, _parser, meta = _collect(pages, details)

    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"].endswith(":group:77")
    assert row["raw_fields"]["source_course_kind"] == "단체강좌"
    assert row["period"] == "2099-02-07 ~ 2099-02-07"
    assert row["schedule_raw"] == "10:00 ~ 12:00"
    assert row["branch"] == "안성환경교육센터"
    assert meta["source_kind_counts"] == {"group": 1}
    assert meta["snapshot_complete"] is True


def test_visit_course_accepts_omitted_list_count_and_nested_branch_name() -> None:
    personal = anseong.ANSEONG_PERSONAL_SOURCE
    group = anseong.ANSEONG_GROUP_SOURCE
    nested_branch = "안성중장년행복캠퍼스(한경국립대학교)"
    pages = {
        ("personal", 1): _list_page(
            personal,
            page=1,
            total=1,
            rows=_personal_row(
                "4015",
                branch=nested_branch,
                region="현수동",
                capacity="- / 10",
                method="방문",
            ),
        ),
        ("personal", 2): _list_page(personal, page=2, total=1),
        ("group", 1): _list_page(group, page=1, total=0),
        ("group", 2): _list_page(group, page=2, total=0),
    }
    details = {
        ("personal", "4015"): _personal_detail(
            "4015",
            capacity="신청 0 명 / 모집인원 10 명",
            gate=False,
        )
    }

    rows, _parser, meta = _collect(pages, details)

    assert len(rows) == 1
    row = rows[0]
    assert row["branch"] == nested_branch
    assert row["raw_fields"]["source_region"] == "현수동"
    assert row["capacity_current"] == 0
    assert row["capacity_total"] == 10
    assert row["application_type"] == "IN_PERSON"
    assert "application_url" not in row
    assert meta["malformed_count"] == 0
    assert meta["snapshot_complete"] is True


def test_max_pages_must_cover_both_catalogues_and_both_sentinels() -> None:
    pages, details = _base_catalogue()

    rows, _parser, meta = _collect(pages, details, max_pages=3)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "3 of 4 required list requests" in meta["configured_collection_error"]


def test_detail_limit_and_external_dedupe_are_fail_closed() -> None:
    pages, details = _base_catalogue()

    limited, _parser, limited_meta = _collect(pages, details, detail_limit=1)
    deduped, _parser, dedupe_meta = _collect(
        pages, details, dedupe_rows=lambda _rows: []
    )

    assert limited == []
    assert limited_meta["source_cap_reached"] is True
    assert "1 of 2 required details" in limited_meta["configured_collection_error"]
    assert deduped == []
    assert "dedupe changed complete row count 1 to 0" in dedupe_meta[
        "configured_collection_error"
    ]


def test_nonempty_sentinel_or_duplicate_identity_fails_snapshot() -> None:
    pages, details = _base_catalogue()
    pages[("personal", 2)] = _list_page(
        anseong.ANSEONG_PERSONAL_SOURCE,
        page=2,
        total=3,
        rows=_personal_row("999"),
    )
    sentinel_rows, _parser, sentinel_meta = _collect(pages, details)

    pages, details = _base_catalogue()
    duplicate_cards = _personal_row("100", number=3) + _personal_row(
        "100", number=2
    ) + _personal_row(
        "90",
        number=1,
        title="지난 강좌",
        branch="지난 기관",
        apply_period="2020-01-01 ~ 2020-01-02 / 09:00 ~ 18:00",
        education_period="2020-02-01 ~ 2020-02-02 월 / 10:00 ~ 12:00",
        capacity="1 / 10",
        status="접수마감",
    )
    pages[("personal", 1)] = _list_page(
        anseong.ANSEONG_PERSONAL_SOURCE,
        page=1,
        total=3,
        rows=duplicate_cards,
    )
    duplicate_rows, _parser, duplicate_meta = _collect(pages, details)

    assert sentinel_rows == []
    assert "sentinel page is not empty" in sentinel_meta["configured_collection_error"]
    assert duplicate_rows == []
    assert "duplicate source identities" in duplicate_meta["configured_collection_error"]


def test_nonterminal_page_must_be_full() -> None:
    pages, details = _base_catalogue()
    pages[("personal", 1)] = _list_page(
        anseong.ANSEONG_PERSONAL_SOURCE,
        page=1,
        total=11,
        rows=_personal_row("100"),
    )
    pages[("personal", 2)] = _list_page(
        anseong.ANSEONG_PERSONAL_SOURCE,
        page=2,
        total=11,
        rows=_personal_row("90"),
    )
    pages[("personal", 3)] = _list_page(
        anseong.ANSEONG_PERSONAL_SOURCE, page=3, total=11
    )

    rows, _parser, meta = _collect(pages, details, max_pages=5)

    assert rows == []
    assert "personal: page 1 is not full" in meta["configured_collection_error"]


def test_detail_contract_title_period_and_application_gate_fail_closed() -> None:
    pages, details = _base_catalogue()
    details[("personal", "101")] = _personal_detail(
        "101",
        title="다른 강좌",
        apply_period="2099-01-11 09:00 ~ 2099-01-20 18:00",
        capacity="신청 2 명 / 모집인원 3 명 (대기신청 1 명 / 대기정원 5 명)",
        gate=False,
    )

    rows, _parser, meta = _collect(pages, details)

    assert rows == []
    error = meta["configured_collection_error"]
    assert "detail/list title mismatch" in error
    assert "missing official login application gate" in error


def test_transient_detail_failure_is_retried() -> None:
    pages, details = _base_catalogue()

    rows, _parser, meta = _collect(
        pages,
        details,
        fail_once={("personal", "101")},
    )

    assert len(rows) == 1
    assert meta["snapshot_complete"] is True


def test_target_url_and_identity_helpers_are_strict() -> None:
    assert anseong.is_anseong_target(Target()) is True
    assert anseong.is_anseong_target(Target(provider="WRONG")) is False
    assert anseong.is_anseong_target(
        Target(
            url=(
                "https://www.anseong.go.kr/edu/portal/edu/eduLctre/"
                "selectEduLctreWebList.do?mId=1400000000&eduInsttNo=69"
            )
        )
    ) is False
    assert anseong.is_anseong_target(Target(url=anseong.ANSEONG_URL + "&page=1")) is False
    assert anseong.anseong_list_url("personal", 2).endswith(
        "mId=1400000000&searchTxt=&page=2"
    )
    assert anseong.anseong_list_url("wrong", 1) == ""
    assert anseong.anseong_list_url("personal", "../2") == ""
    assert anseong.anseong_detail_url("group", "77").endswith(
        "mId=1400000000&eduGroupLctreNo=77"
    )
    assert anseong.anseong_detail_url("personal", "77&evil=1") == ""


def test_managed_fetcher_and_session_factory_are_required() -> None:
    rows, parser, meta = anseong.collect_anseong_education_courses(Target())

    assert rows == []
    assert parser == anseong.ANSEONG_PARSER
    assert "managed fetcher and session_factory" in meta["configured_collection_error"]
