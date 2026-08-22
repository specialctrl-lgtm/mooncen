from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_dongjak as dongjak


@dataclass
class Target:
    provider: str = dongjak.DONGJAK_EDUCATION_PROVIDER
    url: str = dongjak.DONGJAK_EDUCATION_URL
    branch: str = "서울특별시 동작구"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


ITEMS: dict[str, dict[str, str]] = {
    "101": {
        "title": "미래 과학교실",
        "status": "접수중",
        "selection": "선착순",
        "apply": "2026-07-01~2026-07-30",
        "operation": "2026-07-20~2026-08-10",
        "branch": "동작구청",
        "department": "교육정책과",
        "venue": "동작구청 4층 대강당",
    },
    "102": {
        "title": "가족 수학놀이터",
        "status": "인원마감",
        "selection": "추첨",
        "apply": "2026-06-01~2026-06-30",
        "operation": "2026-07-01~2026-12-31",
        "branch": "동작문화재단",
        "department": "생활문화팀",
        "venue": "사당생활문화센터",
    },
    "103": {
        "title": "지난 세무교육",
        "status": "마감",
        "selection": "선착순",
        "apply": "2025-01-01~2025-01-10",
        "operation": "",
        "branch": "동작구청",
        "department": "징수과",
        "venue": "동작구청",
    },
    "104": {
        "title": "지난 교육 104",
        "status": "마감",
        "selection": "선착순",
        "apply": "2025-02-01~2025-02-10",
        "operation": "2025-02-20~2025-02-21",
    },
    "105": {
        "title": "지난 교육 105",
        "status": "마감",
        "selection": "선착순",
        "apply": "2025-03-01~2025-03-10",
        "operation": "2025-03-20~2025-03-21",
    },
    "106": {
        "title": "지난 교육 106",
        "status": "마감",
        "selection": "선착순",
        "apply": "2025-04-01~2025-04-10",
        "operation": "2025-04-20~2025-04-21",
    },
    "107": {
        "title": "지난 교육 107",
        "status": "마감",
        "selection": "선착순",
        "apply": "2025-05-01~2025-05-10",
        "operation": "2025-05-20~2025-05-21",
    },
    "108": {
        "title": "지난 교육 108",
        "status": "마감",
        "selection": "선착순",
        "apply": "2025-06-01~2025-06-10",
        "operation": "2025-06-20~2025-06-21",
    },
    "109": {
        "title": "지난 교육 109",
        "status": "마감",
        "selection": "선착순",
        "apply": "2025-07-01~2025-07-10",
        "operation": "2025-07-20~2025-07-21",
    },
    "110": {
        "title": "지난 교육 110",
        "status": "마감",
        "selection": "선착순",
        "apply": "2025-08-01~2025-08-10",
        "operation": "2025-08-20~2025-08-21",
    },
}


def _card(identity: str) -> str:
    item = ITEMS[identity]
    operation = (
        f"<li><span>{item['operation']}</span></li>" if item["operation"] else ""
    )
    return f"""
      <li>
        <a href="{dongjak.DONGJAK_DETAIL_PATH}?prgSn={identity}&amp;tmplatSeCd=91&amp;menuNo=1600007&amp;useAt=Y&amp;pageIndex=1">
          <div class="info-desc">
            <div class="status"><span>{item['status']}</span><span>{item['selection']}</span></div>
            <p class="title">{item['title']}</p>
            <div class="desc_4">공식 교육 프로그램</div>
            <div class="info"><span class="unchrgd">무료</span><span>교육</span></div>
          </div>
          <div class="details"><ul>
            <li><b>대상</b><span>초등학생</span></li>
            <li><b>신청</b><span>{item['apply']}</span></li>
            {operation}
          </ul></div>
        </a>
      </li>
    """


def _list_html(
    page: int,
    *,
    last_page: int = 2,
    duplicate: bool = False,
    expired_duplicate: bool = False,
    recheck_changed: bool = False,
) -> str:
    if page == 1:
        identities = ["101", "102", "103", "104", "105", "106", "107", "108"]
        if recheck_changed:
            identities[0] = "109"
    else:
        identities = [
            "101" if duplicate else ("104" if expired_duplicate else "109"),
            "110",
        ]
    cards = "".join(_card(identity) for identity in identities)
    return f"""
      <main><ul class="card-list">{cards}</ul></main>
      <div class="paginationSet"><ul>
        <li class="active"><span><em title="현재목록"><span>{page}</span></em></span></li>
        <li class="i end"><a title="마지막 목록"
          href="{dongjak.DONGJAK_LIST_PATH}?tmplatSeCd=91&amp;menuNo=1600007&amp;useAt=Y&amp;pageIndex={last_page}">끝 목록</a></li>
      </ul></div>
    """


def _detail_html(identity: str, *, bad_title: bool = False) -> str:
    item = ITEMS[identity]
    title = "다른 강좌" if bad_title else item["title"]
    operation = item["operation"] or "~"
    application = (
        f"""
        <a class="s-btn b-resve"
          href="{dongjak.DONGJAK_APPLICATION_PATH}?prgSn={identity}&amp;tmplatSeCd=91&amp;menuNo=1600007&amp;useAt=Y&amp;pageIndex=1">예약하기</a>
        """
        if identity == "101"
        else ""
    )
    past_body = "<p>행사일 2025. 1. 15.</p>" if identity == "103" else ""
    return f"""
      <div class="program-resve-wrap">
        <div class="subject"><h4>{title}</h4></div>
        <div class="program-info"><div class="desc-box">
          <dl><dt>대 상</dt><dd>초등학생</dd></dl>
          <dl><dt>운영장소</dt><dd>{item.get('venue', '동작구청')}</dd></dl>
          <dl><dt>신청기간</dt><dd>{item['apply']}</dd></dl>
          <dl><dt>운영기간</dt><dd>{operation}</dd></dl>
          <dl><dt>이용요금</dt><dd>0 원</dd></dl>
          <dl><dt>문의전화</dt><dd>02-820-0000</dd></dl>
          <dl><dt>담당부서</dt><dd>{item.get('department', '교육정책과')}</dd></dl>
          <dl><dt>운영기관</dt><dd>{item.get('branch', '동작구청')}</dd></dl>
          <dl><dt>모집방법</dt><dd>인터넷 : 정원 20명 / 대기자 5명 / 신청 7명</dd></dl>
          <dl><dt>선정방식</dt><dd>{item['selection']}</dd></dl>
        </div></div>
        <div class="btn-box">{application}</div>
        <div class="txt-per-box">{past_body}</div>
      </div>
    """


class FixtureFetcher:
    def __init__(
        self,
        *,
        duplicate: bool = False,
        expired_duplicate: bool = False,
        page_drift: bool = False,
        recheck_drift: bool = False,
        recheck_drift_once: bool = False,
        bad_detail: str = "",
    ) -> None:
        self.duplicate = duplicate
        self.expired_duplicate = expired_duplicate
        self.page_drift = page_drift
        self.recheck_drift = recheck_drift
        self.recheck_drift_once = recheck_drift_once
        self.bad_detail = bad_detail
        self.calls: list[str] = []
        self.page_one_calls = 0

    def __call__(self, _session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == dongjak.DONGJAK_LIST_PATH:
            assert query["tmplatSeCd"] == ["91"]
            assert query["menuNo"] == ["1600007"]
            assert query["useAt"] == ["Y"]
            page = int(query["pageIndex"][0])
            if page == 1:
                self.page_one_calls += 1
            return _soup(
                _list_html(
                    page,
                    last_page=3 if self.page_drift and page == 2 else 2,
                    duplicate=self.duplicate,
                    expired_duplicate=self.expired_duplicate,
                    recheck_changed=(
                        page == 1
                        and (
                            (
                                self.recheck_drift
                                and self.page_one_calls % 2 == 0
                            )
                            or (
                                self.recheck_drift_once
                                and self.page_one_calls == 2
                            )
                        )
                    ),
                )
            )
        if parsed.path == dongjak.DONGJAK_DETAIL_PATH:
            identity = query["prgSn"][0]
            assert identity in {"101", "102", "103"}
            assert query == {
                "prgSn": [identity],
                "tmplatSeCd": ["91"],
                "menuNo": ["1600007"],
                "useAt": ["Y"],
            }
            return _soup(_detail_html(identity, bad_title=identity == self.bad_detail))
        raise AssertionError(f"unexpected fixture URL: {url}")


def _collect(
    fetcher: FixtureFetcher,
    *,
    target: Target | None = None,
    max_pages: int = 2,
    detail_limit: int = 3,
    dedupe_rows: Any = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], list[DummySession]]:
    sessions: list[DummySession] = []

    def session_factory() -> DummySession:
        session = DummySession()
        sessions.append(session)
        return session

    rows, parser, meta = dongjak.collect_dongjak_education_courses(
        target or Target(),
        timeout=7,
        max_pages=max_pages,
        detail_limit=detail_limit,
        fetcher=fetcher,
        session_factory=session_factory,
        dedupe_rows=dedupe_rows,
        today="2026-07-19",
        max_workers=4,
    )
    return rows, parser, meta, sessions


def test_complete_pages_recheck_current_filter_details_and_branches() -> None:
    rows, parser, meta, sessions = _collect(FixtureFetcher())

    assert parser == dongjak.DONGJAK_PARSER
    assert [row["raw_fields"]["program_id"] for row in rows] == ["101", "102"]
    assert all(session.closed for session in sessions)
    assert meta["list_pages"] == 2
    assert meta["list_requests"] == 3
    assert meta["page_one_rechecks"] == 1
    assert meta["total_count"] == 10
    assert meta["unique_id_count"] == 10
    assert meta["duplicate_count"] == 0
    assert meta["ambiguous_list_period_count"] == 1
    assert meta["expired_by_list_count"] == 7
    assert meta["ambiguous_expired_count"] == 1
    assert meta["expired_count"] == 8
    assert meta["current_count"] == 2
    assert meta["detail_required_count"] == 3
    assert meta["detail_attempts"] == 3
    assert meta["detail_pages"] == 3
    assert meta["request_count"] == 6
    assert meta["branch_counts"] == {"동작구청": 1, "동작문화재단": 1}
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False

    by_id = {row["raw_fields"]["program_id"]: row for row in rows}
    open_row = by_id["101"]
    assert open_row["provider_course_id"] == (
        f"{dongjak.DONGJAK_EDUCATION_PROVIDER}:prg:101"
    )
    assert open_row["branch"] == "동작구청"
    assert open_row["branch_code"].startswith("DONGJAK_BRANCH_")
    assert open_row["preserve_branch"] is True
    assert open_row["municipality_code"] == "1159000000"
    assert open_row["municipality_full_name"] == "서울특별시 동작구"
    assert open_row["domain_category"] == "교육·강좌"
    assert open_row["service_group"] == "공공강좌"
    assert open_row["service_group_policy"] == "locked"
    assert open_row["capacity_current"] == 7
    assert open_row["capacity_total"] == 20
    assert open_row["waitlist_total"] == 5
    assert open_row["reservation_available"] is True
    assert open_row["period"] == "2026-07-20 ~ 2026-08-10"
    assert open_row["schedule_raw"] == "시간 별도 안내"
    assert open_row["fee"] == "0 원"
    assert open_row["target"] == "초등학생"
    assert open_row["venue_name"] == "동작구청 4층 대강당"
    assert open_row["category_raw"] == "교육"
    assert open_row["raw_fields"]["source_time_omitted"] is True
    assert urlparse(open_row["application_url"]).path == dongjak.DONGJAK_APPLICATION_PATH
    assert open_row["raw_fields"]["detail_identity_verified"] is True

    closed_row = by_id["102"]
    assert closed_row["status"] == "CLOSED"
    assert closed_row["end_date"] == "2026-12-31"
    assert closed_row["reservation_available"] is False
    assert "application_url" not in closed_row


def test_exact_provider_route_and_url_builders_reject_scope_expansion() -> None:
    assert dongjak.is_dongjak_education_target(Target()) is True
    invalid = (
        Target(provider="WRONG"),
        Target(url="http://www.dongjak.go.kr/yeyak/progrm/master/yeyak/list.do?tmplatSeCd=91&menuNo=1600007"),
        Target(url="https://www.dongjak.go.kr/yeyak/progrm/master/yeyak/list.do?menuNo=1600007"),
        Target(url="https://www.dongjak.go.kr/yeyak/progrm/master/yeyak/list.do?tmplatSeCd=92&menuNo=1600008"),
        Target(url=dongjak.DONGJAK_EDUCATION_URL + "&pageIndex=2"),
        Target(url="https://evil.example/yeyak/progrm/master/yeyak/list.do?tmplatSeCd=91&menuNo=1600007"),
    )
    assert all(not dongjak.is_dongjak_education_target(target) for target in invalid)

    detail = dongjak.dongjak_detail_url("101")
    parsed = urlparse(detail)
    assert parsed.scheme == "https"
    assert parsed.netloc == dongjak.DONGJAK_HOST
    assert parsed.path == dongjak.DONGJAK_DETAIL_PATH
    assert parse_qs(parsed.query) == {
        "prgSn": ["101"],
        "tmplatSeCd": ["91"],
        "menuNo": ["1600007"],
        "useAt": ["Y"],
    }
    assert dongjak.dongjak_detail_url("../101") == ""

    list_url = dongjak.dongjak_list_url(3)
    assert parse_qs(urlparse(list_url).query) == {
        "tmplatSeCd": ["91"],
        "menuNo": ["1600007"],
        "useAt": ["Y"],
        "pageIndex": ["3"],
    }


def test_caps_fail_closed_without_partial_rows() -> None:
    fetcher = FixtureFetcher()
    rows, _parser, meta, sessions = _collect(fetcher, max_pages=1)
    assert rows == []
    assert all(session.closed for session in sessions)
    assert len(fetcher.calls) == 1
    assert meta["source_cap_reached"] is True
    assert "below declared 2" in meta["configured_collection_error"]

    fetcher = FixtureFetcher()
    rows, _parser, meta, _sessions = _collect(fetcher, detail_limit=2)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_required_count"] == 3
    assert meta["detail_attempts"] == 0
    assert meta["source_cap_reached"] is True
    assert "below required 3 details" in meta["configured_collection_error"]


def test_duplicate_identity_or_pagination_drift_discards_snapshot() -> None:
    rows, _parser, meta, _sessions = _collect(FixtureFetcher(duplicate=True))
    assert rows == []
    assert meta["duplicate_count"] == 1
    assert meta["snapshot_complete"] is False
    assert "duplicate program identities" in meta["configured_collection_error"]

    rows, _parser, meta, _sessions = _collect(FixtureFetcher(page_drift=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "pagination contract" in meta["configured_collection_error"]


def test_identical_expired_history_duplicate_is_audited_without_blocking_current_snapshot() -> None:
    rows, _parser, meta, _sessions = _collect(
        FixtureFetcher(expired_duplicate=True)
    )

    assert [row["raw_fields"]["program_id"] for row in rows] == ["101", "102"]
    assert meta["source_exposed_count"] == 10
    assert meta["unique_total_count"] == 9
    assert meta["duplicate_count"] == 1
    assert meta["benign_expired_duplicate_count"] == 1
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta


def test_page_one_insertion_drift_discards_snapshot() -> None:
    rows, _parser, meta, _sessions = _collect(FixtureFetcher(recheck_drift=True))

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "page 1 changed" in meta["configured_collection_error"]


def test_transient_page_one_drift_restarts_the_complete_snapshot() -> None:
    fetcher = FixtureFetcher(recheck_drift_once=True)

    rows, _parser, meta, sessions = _collect(fetcher)

    assert [row["raw_fields"]["program_id"] for row in rows] == ["101", "102"]
    assert meta["snapshot_complete"] is True
    assert meta["snapshot_attempts"] == 2
    assert fetcher.page_one_calls == 4
    assert all(session.closed for session in sessions)


def test_one_detail_identity_mismatch_discards_all_current_rows() -> None:
    rows, _parser, meta, _sessions = _collect(FixtureFetcher(bad_detail="102"))

    assert rows == []
    assert meta["detail_attempts"] == 3
    assert meta["detail_pages"] == 3
    assert meta["detail_errors"] == 1
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "102: detail title mismatch" in meta["configured_collection_error"]


def test_invalid_target_never_calls_network() -> None:
    fetcher = FixtureFetcher()
    rows, parser, meta, sessions = _collect(
        fetcher,
        target=Target(url="https://evil.example/"),
    )

    assert rows == []
    assert parser == dongjak.DONGJAK_PARSER
    assert fetcher.calls == []
    assert sessions == []
    assert meta["snapshot_complete"] is False
    assert "exact Dongjak education provider route" in meta["configured_collection_error"]


def test_dedupe_count_change_discards_complete_snapshot() -> None:
    rows, _parser, meta, _sessions = _collect(
        FixtureFetcher(), dedupe_rows=lambda values: values[:-1]
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed complete row count 2 to 1" in meta["configured_collection_error"]
