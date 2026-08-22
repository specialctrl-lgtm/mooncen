from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_jeongseon_library as library


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = library.JEONGSEON_LIBRARY_CANDIDATE_ID


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    apply_period: str
    operation_period: str
    status: str = "신청마감"
    category: str = "독서문화프로그램"
    target: str = "초등 3~6학년"
    capacity: str = "6 / 16 (대기자 : 0 / 4)"
    venue: str = "문화 1실"
    material: str = "-"
    participation: str = "-"


class DummySession:
    def close(self) -> None:
        pass


def _target() -> Target:
    return Target(library.JEONGSEON_LIBRARY_PROVIDER, library.JEONGSEON_LIBRARY_LIST_URL)


def _courses(count: int = 6) -> list[Course]:
    return [
        Course(
            str(9300 - index),
            f"정선 공개 강좌 {index + 1}",
            "2026.07.01 09:00 ~ 2026.07.31 17:00",
            "2026.07.23 10:00 ~ 2026.08.31 12:00",
            status="접수중" if index == 0 else "신청마감",
            category="평생학습프로그램" if index == 0 else "독서문화프로그램",
            material="5,000원" if index == 0 else "-",
        )
        for index in range(count)
    ]


def _pairs(values: list[tuple[str, str]]) -> str:
    return "<dl>" + "".join(f"<dt>{key}</dt><dd>{value}</dd>" for key, value in values) + "</dl>"


def _list_html(courses: list[Course], total: int, *, empty: bool = False) -> str:
    if empty:
        body = '<li class="no_data">조회되는 문화강좌가 없습니다.</li>'
    else:
        body = "".join(
            f"""
            <li class="lecture_item">
              <strong class="lecture_item__library">정선교육도서관</strong>
              <h4 class="lecture_item__title"><a href="/jslib/menu/3388/lecture-event/{item.identity}">{item.title}</a></h4>
              {_pairs([
                  ('신청기간', item.apply_period), ('운영기간', item.operation_period),
                  ('신청대상', item.target), ('모집방법', '선착순'), ('모집인원', item.capacity),
              ])}
              <div class="lecture_item__button">
                <button>{item.status}</button>
                <button data-category-name="{item.category}" data-event-id="{item.identity}">등록확인</button>
              </div>
            </li>
            """
            for item in courses
        )
    return f"""
    <html><head><title>프로그램신청</title></head><body>
      <div class="lecture_result_top__count">전체 <strong>{total}</strong>건</div>
      <ul class="lecture_result_list">{body}</ul>
    </body></html>
    """


def _detail_html(item: Course, *, branch: str = "정선교육도서관", phone_leak: bool = False) -> str:
    venue = "문화 1실 033-123-4567" if phone_leak else item.venue
    return f"""
    <html><head><title>프로그램신청</title></head><body>
      <article class="lecture_detail">
        <h4 class="lecture_detail__title">{item.title} {item.status}</h4>
        {_pairs([
            ('강사명', '홍길동'), ('도서관', branch), ('운영기간', item.operation_period.split(' ')[0] + ' ~ ' + item.operation_period.split(' ~ ')[1].split(' ')[0]),
            ('운영시간', '10:00 ~ 12:00 매주 토요일'), ('신청방법', '인터넷'), ('신청기간', item.apply_period),
            ('신청자격', '정회원 / 준회원 / 비회원'), ('신청대상', item.target), ('모집인원', '선착순 : ' + item.capacity),
            ('준비물', '-'), ('재료비', item.material), ('참가비', item.participation), ('장소', venue),
        ])}
        <input name="applicantName" value="비공개 신청자">
        <input name="phone" value="010-9999-9999">
      </article>
    </body></html>
    """


class FixtureSite:
    def __init__(self, courses: list[Course] | None = None, **flags: bool):
        self.courses = courses or _courses()
        self.flags = flags
        self.calls: list[str] = []

    def __call__(self, _session: DummySession, url: str, timeout: int) -> str:
        assert timeout > 0
        self.calls.append(url)
        parsed = urlparse(url)
        identity = parsed.path.rsplit("/", 1)[-1]
        if (
            parsed.path.startswith(library.JEONGSEON_LIBRARY_DETAIL_PREFIX)
            and identity.isdigit()
        ):
            item = next(value for value in self.courses if value.identity == identity)
            return _detail_html(
                item,
                branch="다른도서관" if self.flags.get("wrong_detail_owner") else library.JEONGSEON_LIBRARY_BRANCH,
                phone_leak=self.flags.get("phone_leak", False),
            )
        query = parse_qs(parsed.query)
        page = int(query.get("page", ["0"])[0])
        total = len(self.courses) - (1 if self.flags.get("total_drift") and page else 0)
        start = page * library.JEONGSEON_LIBRARY_PAGE_SIZE
        rows = self.courses[start : start + library.JEONGSEON_LIBRARY_PAGE_SIZE]
        if self.flags.get("unknown_status") and page == 0:
            rows = [Course(**{**rows[0].__dict__, "status": "알수없음"}), *rows[1:]]
        if self.flags.get("wrong_list_owner") and rows:
            value = _list_html(rows, total).replace("정선교육도서관", "다른도서관", 1)
            return value
        return _list_html(rows, total, empty=not rows)


def _collect(site: FixtureSite, **kwargs):
    return library.collect_jeongseon_library(
        _target(),
        today="2026-07-23",
        max_workers=1,
        session_factory=DummySession,
        fetcher=site,
        **kwargs,
    )


def test_owner_identity_urls_and_request_allowlist() -> None:
    assert library.is_jeongseon_library_target(_target())
    assert not library.is_jeongseon_library_target(Target("wrong", _target().url))
    assert library.jeongseon_library_list_url(0) == library.JEONGSEON_LIBRARY_LIST_URL
    assert library.jeongseon_library_list_url(2).endswith("?page=2")
    assert library.jeongseon_library_detail_url("9178").endswith("/9178")
    assert library._request_kind(library.jeongseon_library_list_url(1)) == "list"
    assert library._request_kind(library.jeongseon_library_detail_url("9178")) == "detail"
    with pytest.raises(library.JeongseonLibraryContractError):
        library._request_kind("https://lib.gwe.go.kr/jslib/menu/4242/user/my/lecture-event/list")
    with pytest.raises(library.JeongseonLibraryContractError):
        library._request_kind("https://lib.gwe.go.kr/api/homepage/jslib/lecture-event/9178/applies")
    with pytest.raises(library.JeongseonLibraryContractError):
        library.jeongseon_library_detail_url("../../login")


def test_complete_snapshot_has_stable_identity_status_and_official_branch() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == library.JEONGSEON_LIBRARY_PARSER
    assert len(rows) == meta["source_rows"] == meta["source_total"] == 6
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["empty_sentinel_page"] == 1
    assert meta["empty_sentinel_verified"] is True
    assert meta["list_requests"] == 4
    assert meta["detail_requests"] == 6
    assert meta["applicant_endpoint_requests"] == 0
    assert len(meta["source_identity_sha256"]) == 64
    assert meta["source_branch_counts"] == {"정선교육도서관": 6}
    assert all(row["branch"] == "정선교육도서관" for row in rows)
    assert all(row["municipality_code"] == "5177000000" for row in rows)
    assert all(row["collection_category"] == "공공예약" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert rows[0]["provider_course_id"] == "gwe-jslib:9300"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_url"].endswith("/9300")
    assert rows[0]["fee"] == "재료비 5,000원"
    assert rows[1]["status"] == "CLOSED"
    assert rows[1]["application_url"] == ""


def test_complete_ledger_with_no_current_rows_is_successful_empty_snapshot() -> None:
    site = FixtureSite()
    rows, parser, meta = library.collect_jeongseon_library(
        _target(),
        today="2099-01-01",
        max_workers=1,
        session_factory=DummySession,
        fetcher=site,
    )
    assert parser == library.JEONGSEON_LIBRARY_PARSER
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]
    assert meta["details_complete"] is True
    assert meta["detail_requests"] == 0


def test_zero_based_multipage_total_and_empty_sentinel_are_complete() -> None:
    rows, _, meta = _collect(FixtureSite(_courses(12)), max_pages=4, detail_limit=20)
    assert len(rows) == 12
    assert meta["data_pages"] == 2
    assert meta["page_counts"] == {0: 10, 1: 2}
    assert meta["empty_sentinel_page"] == 2
    assert meta["list_requests"] == 5


@pytest.mark.parametrize(
    "site,error",
    [
        (FixtureSite(unknown_status=True), "status changed"),
        (FixtureSite(wrong_list_owner=True), "identity/owner changed"),
        (FixtureSite(wrong_detail_owner=True), "fields/owner changed"),
        (FixtureSite(phone_leak=True), "unsafe venue"),
        (FixtureSite(total_drift=True), "stability check failed"),
    ],
)
def test_contract_drift_fails_closed(site: FixtureSite, error: str) -> None:
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in meta["configured_collection_error"]


def test_caps_and_wrong_owner_fail_before_partial_save() -> None:
    rows, _, meta = _collect(FixtureSite(_courses(12)), max_pages=2, detail_limit=20)
    assert rows == []
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _, meta = _collect(FixtureSite(), detail_limit=5)
    assert rows == []
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _, meta = library.collect_jeongseon_library(
        Target("wrong", library.JEONGSEON_LIBRARY_LIST_URL),
        fetcher=lambda *_: pytest.fail("wrong owner must not fetch"),
    )
    assert rows == []
    assert "registered Jeongseon library owner" in meta["configured_collection_error"]


def test_expired_rows_do_not_consume_detail_limit() -> None:
    expired = Course(
        "9400",
        "종료 강좌",
        "2026.01.01 09:00 ~ 2026.01.02 17:00",
        "2026.01.03 10:00 ~ 2026.01.04 12:00",
    )
    site = FixtureSite([expired, *_courses(2)])
    rows, _, meta = _collect(site, detail_limit=2)
    assert len(rows) == 2
    assert meta["source_rows"] == 3
    assert meta["expired_source_count"] == 1
    assert not any(url.endswith("/9400") for url in site.calls)


def test_detail_and_applicant_payload_are_not_persisted() -> None:
    rows, _, meta = _collect(FixtureSite())
    payload = repr(rows)
    assert "비공개 신청자" not in payload
    assert "010-9999-9999" not in payload
    assert all(set(row["raw_fields"]) <= library._SAFE_RAW_FIELDS for row in rows)
    assert all(row["description"] == row["title"] for row in rows)
    assert meta["pii_values_persisted"] == 0


def test_dedupe_cardinality_change_fails_closed() -> None:
    rows, _, meta = _collect(
        FixtureSite(),
        dedupe_fn=lambda values: list(values)[:-1],
    )
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("JEONGSEON_LIBRARY_LIVE") != "1",
    reason="set JEONGSEON_LIBRARY_LIVE=1 for the official live audit",
)
def test_live_official_complete_snapshot() -> None:
    rows, parser, meta = library.collect_jeongseon_library(
        _target(), timeout=40, max_pages=20, detail_limit=200, max_workers=6
    )
    assert parser == library.JEONGSEON_LIBRARY_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert rows
    assert all(row["branch"] == "정선교육도서관" for row in rows)
    assert all(row["end_date"] >= "2026-07-23" for row in rows)
