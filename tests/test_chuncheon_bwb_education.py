from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

import pytest

from Crawler import municipal_chuncheon_bwb as bwb
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


def _target(**overrides: Any) -> dict[str, Any]:
    return {
        "provider": bwb.BWB_CHUNCHEON_PROVIDER,
        "url": bwb.BWB_CHUNCHEON_URL,
        "name": "춘천시 배워봄 전체 교육강좌",
        "branch": "강원특별자치도 춘천시",
        **overrides,
    }


def _dl(key: str, value: str) -> str:
    return f"<dl><dt>{key}</dt><dd>{value}</dd></dl>"


def _card(
    identity: str,
    title: str,
    source_status: str,
    *,
    institution: str = "춘천시 평생학습관",
    event_range: str = "2026-08-10 ~ 2026-09-10",
    registration_range: str = "2026-08-01 ~ 2026-08-09",
    capacity: str = "2/10",
    external_url: str = "",
    include_fields: bool = True,
) -> str:
    info = ""
    if include_fields:
        info = '<div class="info">' + "".join(
            (
                _dl("교육기관", institution),
                _dl("교육기간", event_range),
                _dl("교육시간", "월, 수 / 10:00 ~ 12:00"),
                _dl("접수기간", registration_range),
                _dl("모집인원", capacity),
                _dl("대상자", "성인"),
                _dl("강사", "공개강사"),
            )
        ) + "</div>"
    if external_url:
        control = (
            '<div class="btn-wrap"><a class="btn out-class" '
            f'href="{external_url}">외부 수강신청</a></div>'
        )
    else:
        control = (
            '<div class="btn-wrap"><button type="button" '
            f'onclick="fn_view({identity})">상세보기</button></div>'
        )
    return f"""
      <div class="box">
        <div class="tit">
          <em class="label pick">선착순</em>
          <em class="label ing">{source_status}</em>
          <em class="label type">오프라인</em>
          <h3>{title}</h3>
        </div>
        {info}
        {control}
      </div>
    """


def _page(
    total: int,
    rows: str,
    *,
    statuses: Sequence[str] = bwb.BWB_CHUNCHEON_CURRENT_FILTERS,
    page: int = 1,
) -> str:
    status_inputs = "".join(
        f'<input type="checkbox" name="status[]" value="{value}"'
        + (" checked" if value in statuses else "")
        + ">"
        for value in ("receive", "standBy", "complete", "edu", "completeEdu")
    )
    return f"""
      <html><head><title>분야별 | 배워봄 - 춘천시 평생학습 통합플랫폼</title></head>
      <body>
        <form id="frm">
          <input type="hidden" name="pageIndex" value="{page}">
          <input type="hidden" name="backFlag" value="category">
          <input type="hidden" name="lifelongGisuLectureId">
          <input type="hidden" name="city[]" value="32010">
          {status_inputs}
        </form>
        <div class="search-result">강좌수 총 <strong>{total}</strong>건</div>
        <div class="list-wrap">{rows}</div>
      </body></html>
    """


def _detail(
    identity: str,
    title: str,
    source_status: str,
    *,
    institution: str = "춘천시 평생학습관",
    event_range: str = "2026-08-10 ~ 2026-09-10",
    registration_range: str = "2026-08-01 09:00 ~ 2026-08-09 18:00",
    capacity: str = "2/10",
    wrong_title: bool = False,
) -> str:
    control = (
        f'<button onclick="fn_apply({identity})">수강신청</button>'
        if source_status == "접수중"
        else ""
    )
    return f"""
      <html><head><title>강좌정보 | 배워봄 - 춘천시 평생학습 통합플랫폼</title></head>
      <body><div class="view-wrap">
        <div class="view-top"><span>인문교양</span><em class="label">{source_status}</em></div>
        <div class="view-tit"><h3>{'다른 제목' if wrong_title else title}</h3>{control}</div>
        <div class="accordion-wrap">
          <h4>기본정보</h4><div class="accordion-content">
            {_dl('대상자', '성인')}{_dl('모집방식', '선착순')}
            {_dl('접수인원', capacity)}{_dl('일반접수 모집기간', registration_range)}
          </div>
        </div>
        <div class="accordion-wrap">
          <h4>강좌정보</h4><div class="accordion-content">
            {_dl('교육기관', institution)}{_dl('교육장소', '405호')}
            {_dl('교육유형', '오프라인')}{_dl('교육방법', '대면')}
            {_dl('교육기간', event_range)}{_dl('교육시간', '월요일 10:00 ~ 12:00')}
            {_dl('강사', '공개강사')}{_dl('강의소개', '공개 강의 소개 010-1234-5678 private@example.com')}
            {_dl('강의목표', '기초 역량 향상')}
          </div>
        </div>
        <div class="accordion-wrap">
          <h4>신청정보</h4><div class="accordion-content">
            {_dl('수강료', '무료')}{_dl('재료비', '없음')}
          </div>
        </div>
        <div class="accordion-wrap">
          <h4>교육장소</h4><div class="accordion-content">
            {_dl('주소', '강원특별자치도 춘천시 퇴계농공로 40 (24420) /')}
          </div>
        </div>
        <div class="accordion-wrap">
          <h4>교육기관정보</h4><div class="accordion-content">
            {_dl('대표자명', '개인정보')}{_dl('사업자등록번호', '111-22-33333')}
            {_dl('전화번호', '033-000-0000')}
          </div>
        </div>
      </div></body></html>
    """


@dataclass
class _Response:
    text: str
    url: str
    status_code: int = 200
    history: tuple[Any, ...] = ()

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")


class _FixtureSession:
    def __init__(self, pages: Mapping[str, str | list[str]]) -> None:
        self.pages = {
            key: list(value) if isinstance(value, list) else value
            for key, value in pages.items()
        }
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        assert kwargs.get("allow_redirects") is False
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError(f"unexpected GET {url}")
        value = self.pages[url]
        if isinstance(value, list):
            if len(value) > 1:
                text = value.pop(0)
            elif value:
                text = value[0]
            else:
                raise AssertionError(f"exhausted fixture responses for {url}")
        else:
            text = value
        return _Response(text=text, url=url)

    def close(self) -> None:
        pass


def _fixture_pages(*, wrong_detail_title: bool = False) -> dict[str, str]:
    receive_internal = _card("101", "컴퓨터 기초", "접수중")
    receive_external = _card(
        "external",
        "AI 가구제작",
        "접수중",
        institution="한국폴리텍대학 춘천캠퍼스",
        capacity="30",
        external_url="https://example.edu/course/ai-furniture",
    )
    waiting = _card("102", "영어회화", "대기", capacity="0/15")
    ongoing = _card("103", "한글서예", "교육중", capacity="10/10")
    combined = receive_internal + receive_external + waiting + ongoing
    return {
        bwb.BWB_CHUNCHEON_URL: _page(20, "", statuses=()),
        bwb.bwb_chuncheon_list_url(1, ("receive",)): _page(
            2, receive_internal + receive_external, statuses=("receive",)
        ),
        bwb.bwb_chuncheon_list_url(1, ("standBy",)): _page(
            1, waiting, statuses=("standBy",)
        ),
        bwb.bwb_chuncheon_list_url(1, ("edu",)): _page(
            1, ongoing, statuses=("edu",)
        ),
        bwb.bwb_chuncheon_list_url(1): _page(4, combined),
        bwb.bwb_chuncheon_list_url(2): _page(4, "", page=2),
        bwb.bwb_chuncheon_detail_url("101"): _detail(
            "101", "컴퓨터 기초", "접수중", wrong_title=wrong_detail_title
        ),
        bwb.bwb_chuncheon_detail_url("102"): _detail(
            "102", "영어회화", "대기", capacity="0/15"
        ),
        bwb.bwb_chuncheon_detail_url("103"): _detail(
            "103", "한글서예", "교육중", capacity="10/10"
        ),
    }


def _collect(
    pages: Mapping[str, str | list[str]],
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], _FixtureSession]:
    fixture = _FixtureSession(pages)
    rows, parser, meta = bwb.collect(
        _target(),
        timeout=1,
        max_pages=30,
        detail_limit=100,
        session_factory=lambda: fixture,
        today="2026-08-05",
        dedupe_rows=lambda values: values,
        **kwargs,
    )
    return rows, parser, meta, fixture


def test_exact_target_and_repository_hash_identities() -> None:
    assert bwb.is_chuncheon_bwb_target(_target())
    assert not bwb.is_chuncheon_bwb_target(_target(provider="WRONG"))
    assert not bwb.is_chuncheon_bwb_target(
        _target(url=bwb.BWB_CHUNCHEON_URL + "?status[]=receive")
    )
    assert not bwb.is_chuncheon_bwb_target(
        _target(url=bwb.BWB_CHUNCHEON_URL + "#fragment")
    )
    assert not bwb.is_chuncheon_bwb_target(
        _target(url=bwb.BWB_CHUNCHEON_URL.replace("https://", "http://"))
    )
    assert bwb.BWB_CHUNCHEON_PROVIDER == stable_provider(bwb.BWB_CHUNCHEON_URL)
    assert bwb.BWB_CHUNCHEON_CANDIDATE_ID == candidate_id(
        normalized_duplicate_url(bwb.BWB_CHUNCHEON_URL)
    )


def test_production_collection_requires_managed_session() -> None:
    rows, parser, meta = bwb.collect(_target(), today="2026-08-05")
    assert rows == []
    assert parser == bwb.BWB_CHUNCHEON_PARSER
    assert meta["snapshot_complete"] is False
    assert "session_factory" in meta["configured_collection_error"]
    assert meta["application_endpoints_called"] == 0


class _NeverSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> Any:
        self.calls.append(url)
        raise AssertionError("unsafe route reached the network")

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    "url",
    (
        "https://bwb.chuncheon.go.kr/other-service/login-user/",
        "https://bwb.chuncheon.go.kr/enrollment/application/?lifelongGisuLectureId=101",
        "https://bwb.chuncheon.go.kr/enrollment/applicant/list?lifelongGisuLectureId=101",
        "https://bwb.chuncheon.go.kr/cmm/fms/FileDown.do?atchFileId=FILE_1",
        "https://example.edu/course/ai-furniture",
    ),
)
def test_runner_rejects_login_application_pii_download_and_external_routes(url: str) -> None:
    session = _NeverSession()
    with bwb._Runner(lambda: session, 1) as runner:
        with pytest.raises(bwb.ChuncheonBwbContractError, match="endpoint|host"):
            runner.soup(url)
    assert session.calls == []


def test_complete_current_snapshot_is_atomic_and_locked_to_education() -> None:
    rows, parser, meta, fixture = _collect(_fixture_pages())
    assert parser == bwb.BWB_CHUNCHEON_PARSER
    assert len(rows) == 4
    assert meta["archive_total"] == 20
    assert meta["partition_totals"] == {"receive": 2, "standBy": 1, "edu": 1}
    assert meta["source_total"] == meta["source_rows"] == 4
    assert meta["data_pages"] == 1
    assert meta["sentinel_page"] == 2
    assert meta["stable_first_page"] is True
    assert meta["stable_final_page"] is True
    assert meta["detail_pages"] == meta["internal_detail_rows"] == 3
    assert meta["external_list_only_rows"] == 1
    assert meta["snapshot_complete"] is True
    assert meta["application_endpoints_called"] == 0
    assert meta["external_endpoints_called"] == 0
    assert meta["pii_payload_persisted"] is False

    assert {row["municipality_code"] for row in rows} == {"5111000000"}
    assert {row["municipality_full_name"] for row in rows} == {
        "강원특별자치도 춘천시"
    }
    assert {row["service_group"] for row in rows} == {"공공강좌"}
    assert {row["domain_category"] for row in rows} == {"교육·강좌"}
    assert {row["program_type"] for row in rows} == {"교육"}
    assert all(row["classification_locked"] is True for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert {row["status"] for row in rows} == {"OPEN", "SCHEDULED", "CLOSED"}

    external = next(
        row for row in rows if row["raw_fields"]["owner_kind"] == "external"
    )
    assert external["raw_url"] == "https://example.edu/course/ai-furniture"
    assert fixture.calls.count(external["raw_url"]) == 0
    assert all(urlparse(url).hostname == "bwb.chuncheon.go.kr" for url in fixture.calls)
    assert "010-1234-5678" not in str(rows)
    assert "private@example.com" not in str(rows)
    assert "개인정보" not in str(rows)
    assert "111-22-33333" not in str(rows)


def test_notice_and_test_cards_are_counted_but_never_returned_or_detailed() -> None:
    regular = _card("201", "생활영어", "접수중")
    notice = _card("202", "[공지사항] 수강신청 안내", "접수중", include_fields=False)
    test = _card("203", "테스트 강좌 - 신청하지 마세요", "접수중", include_fields=False)
    combined = regular + notice + test
    pages: dict[str, str] = {
        bwb.BWB_CHUNCHEON_URL: _page(30, "", statuses=()),
        bwb.bwb_chuncheon_list_url(1, ("receive",)): _page(
            3, combined, statuses=("receive",)
        ),
        bwb.bwb_chuncheon_list_url(1, ("standBy",)): _page(
            0, "", statuses=("standBy",)
        ),
        bwb.bwb_chuncheon_list_url(1, ("edu",)): _page(
            0, "", statuses=("edu",)
        ),
        bwb.bwb_chuncheon_list_url(1): _page(3, combined),
        bwb.bwb_chuncheon_list_url(2): _page(3, "", page=2),
        bwb.bwb_chuncheon_detail_url("201"): _detail(
            "201", "생활영어", "접수중"
        ),
    }
    rows, _parser, meta, fixture = _collect(pages)
    assert [row["title"] for row in rows] == ["생활영어"]
    assert meta["source_total"] == 3
    assert meta["explicit_non_program_count"] == 2
    assert meta["notice_count"] == 1
    assert meta["test_count"] == 1
    assert meta["detail_pages"] == 1
    assert all("202" not in url and "203" not in url for url in fixture.calls)


def test_post_last_row_fails_closed_without_partial_snapshot() -> None:
    pages = _fixture_pages()
    pages[bwb.bwb_chuncheon_list_url(2)] = _page(
        4, _card("999", "경계 밖 강좌", "접수중"), page=2
    )
    rows, _parser, meta, _fixture = _collect(pages)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


def test_partition_total_mismatch_fails_before_details() -> None:
    pages = _fixture_pages()
    pages[bwb.bwb_chuncheon_list_url(1, ("standBy",))] = _page(
        2,
        _card("102", "영어회화", "대기", capacity="0/15")
        + _card("104", "일본어회화", "대기", capacity="0/15"),
        statuses=("standBy",),
    )
    rows, _parser, meta, fixture = _collect(pages)
    assert rows == []
    assert "partition sum" in meta["configured_collection_error"]
    assert not any("detail-view" in url for url in fixture.calls)


def test_detail_identity_mismatch_discards_entire_snapshot() -> None:
    rows, _parser, meta, _fixture = _collect(
        _fixture_pages(wrong_detail_title=True)
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "title mismatch" in meta["configured_collection_error"]


def test_boundary_change_after_details_discards_entire_snapshot() -> None:
    pages: dict[str, str | list[str]] = dict(_fixture_pages())
    combined_url = bwb.bwb_chuncheon_list_url(1)
    original = str(pages[combined_url])
    changed = original.replace("컴퓨터 기초", "변경된 컴퓨터 기초")
    pages[combined_url] = [original, changed, changed]
    rows, _parser, meta, _fixture = _collect(pages)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "boundary changed" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 3


def test_caps_fail_closed_before_incomplete_collection() -> None:
    fixture = _FixtureSession(_fixture_pages())
    rows, _parser, meta = bwb.collect(
        _target(),
        timeout=1,
        max_pages=7,
        detail_limit=100,
        session_factory=lambda: fixture,
        today="2026-08-05",
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]
    assert not any("detail-view" in url for url in fixture.calls)


def test_dispatcher_injects_managed_session_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any) -> tuple[list[Any], str, dict[str, Any]]:
        captured.update(kwargs)
        return [], "bwb", {"snapshot_complete": True}

    monkeypatch.setattr(bwb, "collect_chuncheon_bwb_courses", collect)
    target = municipal.CrawlTarget(
        provider=bwb.BWB_CHUNCHEON_PROVIDER,
        name="춘천시 배워봄 전체 교육강좌",
        branch="강원특별자치도 춘천시",
        url=bwb.BWB_CHUNCHEON_URL,
        source="test",
    )
    municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=30,
        detail_limit=100,
    )
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "allow_raw_requests_for_tests" not in captured


def test_live_baseline_documents_current_official_partition() -> None:
    baseline = bwb.BWB_CHUNCHEON_LIVE_AUDIT_BASELINE
    assert baseline["archive_total"] == 1749
    assert baseline["partition_totals"] == {
        "receive": 31,
        "standBy": 7,
        "edu": 48,
    }
    assert baseline["source_total"] == 86
    assert baseline["data_pages"] == 10
    assert baseline["sentinel_page"] == 11
    assert baseline["internal_detail_rows"] + baseline["external_list_only_rows"] == 86
