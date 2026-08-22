from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import inspect
import math
from typing import Any

from Crawler import municipal_wando as wando


@dataclass(frozen=True)
class Target:
    provider: str
    url: str
    branch: str = "완도군 교육"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _source(provider: str) -> wando.WandoSource:
    return next(source for source in wando.WANDO_SOURCES if source.provider == provider)


def _target(provider: str = wando.WANDO_LIFELONG_PROVIDER) -> Target:
    source = _source(provider)
    return Target(source.provider, source.url)


def _items(
    provider: str,
    *,
    total: int,
    current_count: int,
    institutions: tuple[str, ...] = ("완도군",),
) -> list[dict[str, Any]]:
    source = _source(provider)
    offset = 700 if source.menu == "490" else 900
    result: list[dict[str, Any]] = []
    for index in range(total):
        current = index < current_count
        status = "접수중" if index == 0 and current else (
            "접수예정" if current else "마감"
        )
        result.append(
            {
                "number": total - index,
                "identity": f"INFO_{offset + index:015d}",
                "title": f"완도 교육 {source.menu}-{total - index}",
                "capacity_current": 0,
                "capacity_total": 20 + index,
                "start": "2026-07-19" if current else "2025-01-01",
                "end": "2026-12-31" if current else "2025-12-31",
                "apply_start": "2026-07-01" if current else "2025-01-01",
                "apply_end": "2026-08-31" if current else "2025-01-31",
                "venue": f"완도 교육장 {index % 7}",
                "institution": institutions[index % len(institutions)],
                "status": status,
            }
        )
    return result


def _pager(source: wando.WandoSource, last_page: int, active_page: int) -> str:
    links = []
    for page in range(1, last_page + 1):
        selected = ' class="on"' if page == active_page else ""
        links.append(
            f'<a{selected} href="/wando/sub.cs?m={source.menu}&amp;currentPageNo={page}">{page}</a>'
        )
    return '<div class="paging">' + "".join(links) + "</div>"


def _list_page(
    source: wando.WandoSource,
    items: list[dict[str, Any]],
    *,
    page: int,
    last_page: int,
) -> str:
    rows = []
    for item in items:
        rows.append(
            f"""
            <tr>
              <td>{item['number']}</td>
              <td><a href="/wando/sub.cs?m={source.detail_menu}&amp;infoId={item['identity']}">{item['title']}</a></td>
              <td><span>{item['capacity_current']}</span> /{item['capacity_total']} 명</td>
              <td>{item['start']} ~ {item['end']}</td>
              <td>{item['venue']}</td><td>{item['institution']}</td><td>{item['status']}</td>
            </tr>
            """
        )
    if not rows:
        rows.append(
            '<tr><td class="nolist" colspan="7">등록(검색)된 데이터가 없습니다.</td></tr>'
        )
    active = page if page <= last_page else 0
    return f"""
      <html lang="ko"><head><title>{source.catalogue_name}&lt;완도대표홈페이지</title></head>
      <body><table class="board_t1"><thead><tr>
        <th>번호</th><th>강좌명</th><th>정원</th><th>수강기간</th>
        <th>장소</th><th>기관</th><th>접수</th>
      </tr></thead><tbody>{''.join(rows)}</tbody></table>
      {_pager(source, last_page, active)}</body></html>
    """


def _detail_page(
    source: wando.WandoSource,
    item: dict[str, Any],
    *,
    wrong_title: bool = False,
    wrong_identity: bool = False,
) -> str:
    identity = "INFO_000000000009999" if wrong_identity else item["identity"]
    title = "다른 강좌" if wrong_title else item["title"]
    return f"""
      <html lang="ko"><head><title>{title}&lt;완도대표홈페이지</title>
      <link rel="canonical" href="http://www.wando.go.kr/wando/sub.cs?m={source.detail_menu}&amp;infoId={identity}">
      </head><body><table class="board_t1_view"><tbody>
        <tr><th>강좌명</th><td>{title}</td></tr>
        <tr><th>교육대상</th><td>완도군민</td></tr>
        <tr><th>수강료</th><td>무료</td></tr>
        <tr><th>신청기간</th><td>{item['apply_start']} ~ {item['apply_end']}</td></tr>
        <tr><th>교육기간</th><td>{item['start']} ~ {item['end']}</td></tr>
        <tr><th>교육장소</th><td>{item['venue']}</td></tr>
        <tr><th>교육기관</th><td>{item['institution']}</td></tr>
        <tr><th>문의전화</th><td>061-000-0000</td></tr>
        <tr><th>강좌소개/강의계획</th><td>완도 교육 상세</td></tr>
        <tr><th>강사소개</th><td>강사명 : 홍길동</td></tr>
        <tr><th>모집정원</th><td>{item['capacity_total']} 명</td></tr>
      </tbody></table></body></html>
    """


def _fixture(
    provider: str = wando.WANDO_LIFELONG_PROVIDER,
    *,
    total: int = 16,
    current_count: int = 2,
    institutions: tuple[str, ...] = ("완도군",),
    wrong_last_number: bool = False,
    wrong_detail_title: bool = False,
    wrong_detail_identity: bool = False,
):
    source = _source(provider)
    items = _items(
        provider,
        total=total,
        current_count=current_count,
        institutions=institutions,
    )
    if wrong_last_number:
        items[-1]["number"] = 2
    last_page = math.ceil(total / wando.WANDO_PAGE_SIZE)
    mapping: dict[str, str] = {
        wando.WANDO_ROOT_URL: (
            '<html lang="ko"><head><title>완도대표홈페이지</title></head><body></body></html>'
        )
    }
    for page in range(1, last_page + 1):
        start = (page - 1) * wando.WANDO_PAGE_SIZE
        end = start + wando.WANDO_PAGE_SIZE
        mapping[wando.wando_list_url(provider, page)] = _list_page(
            source,
            items[start:end],
            page=page,
            last_page=last_page,
        )
    sentinel = last_page + 1
    mapping[wando.wando_list_url(provider, sentinel)] = _list_page(
        source, [], page=sentinel, last_page=last_page
    )
    for index, item in enumerate(items[:current_count]):
        mapping[wando.wando_detail_url(provider, item["identity"])] = _detail_page(
            source,
            item,
            wrong_title=wrong_detail_title and index == 0,
            wrong_identity=wrong_detail_identity and index == 0,
        )

    calls: list[str] = []
    sessions: list[DummySession] = []

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        return mapping[url]

    def make_session() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    return source, items, mapping, fetch, make_session, calls, sessions


def test_exact_routes_and_url_builders_are_fail_closed() -> None:
    assert wando.is_wando_education_target(_target())
    assert wando.is_wando_education_target(_target(wando.WANDO_LITERACY_PROVIDER))
    assert not wando.is_wando_education_target(
        Target("OTHER", wando.WANDO_LIFELONG_URL)
    )
    assert not wando.is_wando_education_target(
        Target(wando.WANDO_LIFELONG_PROVIDER, wando.WANDO_LIFELONG_URL + "#fragment")
    )
    assert wando.wando_list_url(wando.WANDO_LIFELONG_PROVIDER, 1) == wando.WANDO_LIFELONG_URL
    assert wando.wando_list_url(wando.WANDO_LIFELONG_PROVIDER, 2).endswith(
        "m=490&currentPageNo=2"
    )
    assert wando.wando_list_url("OTHER", 1) == ""
    assert wando.wando_list_url(wando.WANDO_LIFELONG_PROVIDER, "1&admin=1") == ""
    identity = "INFO_000000000000700"
    assert wando.wando_detail_url(wando.WANDO_LIFELONG_PROVIDER, identity).endswith(
        "m=886&infoId=INFO_000000000000700"
    )
    assert wando.wando_detail_url(
        wando.WANDO_LIFELONG_PROVIDER, identity + "&admin=1"
    ) == ""


def test_complete_two_page_snapshot_warms_session_and_enriches_current_rows() -> None:
    source, items, _mapping, fetch, make_session, calls, sessions = _fixture()

    rows, parser, meta = wando.collect_wando_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today=date(2026, 7, 19),
        max_pages=3,
        detail_limit=2,
    )

    assert parser == wando.WANDO_PARSER
    assert calls[0] == wando.WANDO_ROOT_URL
    assert calls[1] == source.url
    assert calls[2] == wando.wando_list_url(source.provider, 2)
    assert calls[3] == wando.wando_list_url(source.provider, 3)
    assert len(rows) == 2
    assert [row["status"] for row in rows] == ["OPEN", "SCHEDULED"]
    assert {row["provider_course_id"] for row in rows} == {
        f"{source.provider}:{items[0]['identity']}",
        f"{source.provider}:{items[1]['identity']}",
    }
    assert all(row["municipality_code"] == "1285000000" for row in rows)
    assert all(row["municipality_full_name"] == "전남광주통합특별시 완도군" for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["branch"] == "완도군" for row in rows)
    assert all(row["apply_period"] == "2026-07-01 ~ 2026-08-31" for row in rows)
    assert all(row["raw_fields"]["detail_valid"] for row in rows)
    assert meta == {
        **meta,
        "pages": 3,
        "required_list_requests": 3,
        "source_total": 16,
        "source_pages": 2,
        "discovered_links": 16,
        "expired_count": 14,
        "current_count": 2,
        "returned_count": 2,
        "detail_attempts": 2,
        "detail_pages": 2,
        "pagination_complete": True,
        "details_complete": True,
        "snapshot_complete": True,
        "source_cap_reached": False,
        "branch_count": 1,
        "no_current_data": False,
    }
    assert len(sessions) == 1 and sessions[0].closed


def test_complete_literacy_history_returns_safe_empty_snapshot_and_six_branches() -> None:
    institutions = (
        "보길도꿈꾸는학교",
        "고금비전한글학교",
        "노화섬사랑평생교육원",
        "금일소망한글학교",
        "완도평생교육원",
        "완도군",
    )
    source, _items_value, _mapping, fetch, make_session, calls, _sessions = _fixture(
        wando.WANDO_LITERACY_PROVIDER,
        total=6,
        current_count=0,
        institutions=institutions,
    )

    rows, _parser, meta = wando.collect_wando_education(
        _target(wando.WANDO_LITERACY_PROVIDER),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=0,
    )

    assert rows == []
    assert calls == [
        wando.WANDO_ROOT_URL,
        source.url,
        wando.wando_list_url(source.provider, 2),
    ]
    assert meta["source_total"] == 6
    assert meta["source_branch_count"] == 6
    assert meta["current_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "all official 완도군 문해교육 rows have ended"


def test_page_cap_cannot_publish_a_partial_snapshot() -> None:
    source, _items_value, _mapping, fetch, make_session, calls, _sessions = _fixture()

    rows, _parser, meta = wando.collect_wando_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=2,
        detail_limit=2,
    )

    assert rows == []
    assert calls == [wando.WANDO_ROOT_URL, source.url]
    assert meta["required_list_requests"] == 3
    assert meta["source_cap_reached"] is True
    assert meta["pagination_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "max_pages cap allows 2 of 3" in meta["configured_collection_error"]


def test_noncontinuous_source_numbering_fails_the_whole_snapshot() -> None:
    _source_value, _items_value, _mapping, fetch, make_session, _calls, _sessions = _fixture(
        wrong_last_number=True
    )

    rows, _parser, meta = wando.collect_wando_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=3,
        detail_limit=2,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert "source numbering" in meta["configured_collection_error"]


def test_current_detail_mismatch_fails_the_whole_snapshot() -> None:
    _source_value, _items_value, _mapping, fetch, make_session, _calls, _sessions = _fixture(
        wrong_detail_title=True
    )

    rows, _parser, meta = wando.collect_wando_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=3,
        detail_limit=2,
    )

    assert rows == []
    assert meta["detail_attempts"] == 2
    assert meta["detail_pages"] == 1
    assert meta["detail_errors"] == 1
    assert meta["snapshot_complete"] is False
    assert "detail title mismatch" in meta["configured_collection_error"]


def test_current_detail_identity_mismatch_fails_the_whole_snapshot() -> None:
    _source_value, _items_value, _mapping, fetch, make_session, _calls, _sessions = _fixture(
        wrong_detail_identity=True
    )

    rows, _parser, meta = wando.collect_wando_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-07-19",
        max_pages=3,
        detail_limit=2,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail canonical identity mismatch" in meta["configured_collection_error"]


def test_production_path_requires_managed_http_and_never_disables_tls() -> None:
    rows, _parser, meta = wando.collect_wando_education(_target())
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "managed fetcher" in meta["configured_collection_error"]

    source = inspect.getsource(wando)
    assert "verify=False" not in source
    assert "ThreadPoolExecutor" not in source
