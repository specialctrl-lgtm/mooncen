from __future__ import annotations

from collections import Counter
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import municipal_ulsan_junggu as junggu


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=junggu.ULSAN_JUNGGU_PROVIDER,
        name="울산 중구 평생학습관 전체 강좌",
        branch=junggu.ULSAN_JUNGGU_BRANCH,
        url=junggu.ULSAN_JUNGGU_URL,
        source="test",
    )


def _list_row(
    ordinal: int,
    identity: str,
    title: str,
    source_status: str,
    status_class: str,
    *,
    period: str,
    apply_period: str,
) -> str:
    values = (
        ("번호", str(ordinal)),
        ("분류", "일반"),
        (
            "강좌명",
            f'<span class="label {status_class}">{source_status}</span>'
            f'<a href="{junggu.ULSAN_JUNGGU_LIST_PATH}" '
            f'onclick="fn_view(\'{identity}\');return false;">{title}</a>',
        ),
        ("접수기간", apply_period),
        ("교육기간", period),
        ("수강료", "10,000원"),
        ("재료비", "-"),
        ("현재 신청/ 대기인원", "3 / 0"),
        ("현장신청", "1"),
        ("신청", "<button>신청</button>"),
    )
    definitions = "".join(
        f"<li><dl><dt>{label}</dt><dd>{value}</dd></dl></li>"
        for label, value in values
    )
    return f"<li><ul class='inner_list'>{definitions}</ul></li>"


def _list_page(page: int, *, bad_sentinel: bool = False, drift: bool = False) -> str:
    rows = ""
    if page == 1:
        title = "현재 신청 강좌 변경" if drift else "현재 신청 강좌"
        rows = (
            _list_row(
                3,
                "PRG_0000000000000003",
                title,
                "신청중",
                "label-danger",
                period="2099-08-01 ~ 2099-08-31",
                apply_period="2099-07-01 ~ 2099-07-31",
            )
            + _list_row(
                2,
                "PRG_0000000000000002",
                "교육 중 추가 신청 강좌",
                "교육중",
                "label-warning",
                period="2099-07-01 ~ 2099-12-31",
                apply_period="2099-07-01 ~ 2099-08-31",
            )
        )
    elif page == 2 or bad_sentinel:
        rows = _list_row(
            1,
            "PRG_0000000000000001",
            "종료 강좌",
            "교육종료",
            "label-default",
            period="2098-01-01 ~ 2098-02-01",
            apply_period="2097-12-01 ~ 2097-12-31",
        )
    return f"""
    <html><head><title>울산 중구 강좌</title></head><body>
      <form id="listForm" method="get" action="{junggu.ULSAN_JUNGGU_LIST_PATH}">
        <input name="exec" value="list">
        <input name="currentPage" value="{page}">
        <input name="pagePerCount" value="{junggu.ULSAN_JUNGGU_PAGE_SIZE}">
        <input name="eduCategory" value="">
        <input name="prgId" value="">
        <input name="eduState" value="">
        <input name="searchKey" value="">
      </form>
      <ul>{rows}</ul>
    </body></html>
    """


def _detail(
    identity: str,
    title: str,
    *,
    source_status: str,
    period: str,
    apply_period: str,
    target: str,
    venue: str,
    application: bool,
) -> str:
    control = (
        f"<button onclick=\"fn_apply('{identity}');\">신청</button>"
        if application
        else ""
    )
    rows = (
        ("일반/특강", "일반"),
        ("강좌기관", "평생학습강좌"),
        ("강좌명", title),
        ("교육대상", target),
        ("교육장소", venue),
        ("접수기간", apply_period),
        ("교육기간", period),
        ("교육시간", "( 화 ) 10:00 ~ 12:00"),
        ("모집정원", "모집인원 : 20 명 / 대기인원 : 5 명"),
        ("현재 신청/대기인원", "신청인원 : 3 명 / 대기인원 : 0 명"),
        ("현장신청", "현장신청 : 1 명"),
        ("강사명", "저장하면 안 되는 강사"),
        ("수강료", "10,000 원"),
        ("재료(교재)비", ""),
        ("강좌소개", "저장하면 안 되는 자유 본문 010-0000-0000"),
    )
    table_rows = "".join(
        f"<tr><th>{label}</th><td>{value}</td></tr>"
        for label, value in rows
    )
    return f"""
    <html><head><title>{title}</title></head><body>
      <table class="table_view"><tbody>{table_rows}</tbody></table>
      {control}
      <div data-source-status="{source_status}"></div>
    </body></html>
    """


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Backend:
    def __init__(
        self,
        *,
        bad_sentinel: bool = False,
        drift: bool = False,
        malformed_control: bool = False,
    ) -> None:
        self.bad_sentinel = bad_sentinel
        self.drift = drift
        self.malformed_control = malformed_control
        self.calls: Counter[tuple[str, str]] = Counter()
        self.sessions: list[_Session] = []
        self.lock = Lock()

    def session(self) -> _Session:
        current = _Session()
        self.sessions.append(current)
        return current

    def fetch(self, _session: _Session, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.hostname == junggu.ULSAN_JUNGGU_HOST
        assert parsed.path == junggu.ULSAN_JUNGGU_LIST_PATH
        query = parse_qs(parsed.query, keep_blank_values=True)
        mode = query["exec"][0]
        if mode == "list":
            page = int(query["currentPage"][0])
            with self.lock:
                self.calls[("list", str(page))] += 1
                count = self.calls[("list", str(page))]
            return BeautifulSoup(
                _list_page(
                    page,
                    bad_sentinel=self.bad_sentinel,
                    drift=self.drift and page == 1 and count >= 2,
                ),
                "lxml",
            )
        assert mode == "view"
        identity = query["prgId"][0]
        with self.lock:
            self.calls[("detail", identity)] += 1
        if identity == "PRG_0000000000000003":
            html = _detail(
                identity,
                "현재 신청 강좌",
                source_status="신청중",
                period="2099-08-01 ~ 2099-08-31",
                apply_period="2099-07-01 09:00 ~ 2099-07-31 18:00",
                target="울산 중구 주민",
                venue="울산 중구 공식 강의실",
                application=True,
            )
        else:
            html = _detail(
                identity,
                "교육 중 추가 신청 강좌",
                source_status="교육중",
                period="2099-07-01 ~ 2099-12-31",
                apply_period="2099-07-01 09:00 ~ 2099-08-31 18:00",
                target="",
                venue="",
                application=True,
            )
        if self.malformed_control:
            html = html.replace(identity, "PRG_0000000000009999", 1)
        return BeautifulSoup(html, "lxml")


def _collect(backend: _Backend, **kwargs: Any):
    return junggu.collect_ulsan_junggu_courses(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 2),
        today="2099-07-20",
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        max_workers=2,
        **kwargs,
    )


def test_complete_snapshot_fields_controls_omissions_and_privacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(junggu, "ULSAN_JUNGGU_PAGE_SIZE", 2)
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == junggu.ULSAN_JUNGGU_PARSER
    assert len(rows) == 2
    assert meta["source_total"] == 3
    assert meta["data_pages"] == 2
    assert meta["page_counts"] == {"1": 2, "2": 1}
    assert meta["sentinel_page"] == 3
    assert meta["required_list_requests"] == meta["list_requests"] == 5
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["network_requests"] == 7
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["status_counts"] == {
        "신청중": 1,
        "교육중": 1,
        "교육종료": 1,
    }

    by_id = {
        row["raw_fields"]["source_program_id"]: row
        for row in rows
    }
    normal = by_id["PRG_0000000000000003"]
    ongoing = by_id["PRG_0000000000000002"]
    assert normal["status"] == ongoing["status"] == "OPEN"
    assert normal["target"] == "울산 중구 주민"
    assert ongoing["target"] == "공식 페이지 대상 미기재"
    assert ongoing["raw_fields"]["target_source_omission"] is True
    assert normal["venue_name"] == "울산 중구 공식 강의실"
    assert ongoing["venue_name"] == "공식 페이지 장소 미기재"
    assert ongoing["raw_fields"]["venue_source_omission"] is True
    assert all(row["schedule_raw"] == "( 화 ) 10:00 ~ 12:00" for row in rows)
    assert all(row["fee"] == "10,000 원" for row in rows)
    assert all(
        row["application_url"]
        == junggu.ulsan_junggu_application_url(
            row["raw_fields"]["source_program_id"]
        )
        for row in rows
    )
    serialized = repr(rows)
    assert "저장하면 안 되는 강사" not in serialized
    assert "저장하면 안 되는 자유 본문" not in serialized
    assert "010-0000-0000" not in serialized
    assert all(session.closed for session in backend.sessions)


@pytest.mark.parametrize(
    ("backend", "needle"),
    (
        (_Backend(bad_sentinel=True), "sentinel is not empty"),
        (_Backend(drift=True), "changed on stability recheck"),
        (_Backend(malformed_control=True), "malformed application control"),
    ),
)
def test_contract_failures_discard_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    backend: _Backend,
    needle: str,
) -> None:
    monkeypatch.setattr(junggu, "ULSAN_JUNGGU_PAGE_SIZE", 2)
    rows, _parser, meta = _collect(backend)
    assert rows == []
    assert needle in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_caps_and_wrong_owner_fail_without_partial_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(junggu, "ULSAN_JUNGGU_PAGE_SIZE", 2)
    rows, _parser, meta = _collect(_Backend(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    target = _target()
    target.url = junggu.ULSAN_JUNGGU_URL + "?currentPage=1"
    rows, _parser, meta = junggu.collect_ulsan_junggu_courses(target)
    assert rows == []
    assert "exact canonical" in meta["configured_collection_error"]


def test_shared_router_dispatches_to_specialized_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = ([{"title": "specialized"}], "parser", {"snapshot_complete": True})
    monkeypatch.setattr(
        junggu,
        "collect_ulsan_junggu_courses",
        lambda *_args, **_kwargs: sentinel,
    )
    assert municipal.collect_from_url(
        _target(),
        timeout=5,
        max_depth=0,
        max_pages=140,
        detail_limit=300,
    ) == sentinel
