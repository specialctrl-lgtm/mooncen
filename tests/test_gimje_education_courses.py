from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_gimje as gimje


@dataclass(frozen=True)
class Target:
    provider: str = gimje.GIMJE_PROVIDER
    url: str = gimje.GIMJE_URL
    branch: str = "김제시"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSite:
    def __init__(self, routes: dict[str, str | list[str]]) -> None:
        self.routes = routes
        self.calls: list[str] = []
        self.counts: dict[str, int] = {}
        self.sessions: list[DummySession] = []

    def fetcher(self, _session: Any, url: str, _timeout: int) -> str:
        self.calls.append(url)
        count = self.counts.get(url, 0)
        self.counts[url] = count + 1
        value = self.routes[url]
        if isinstance(value, list):
            return value[min(count, len(value) - 1)]
        return value

    def session_factory(self) -> DummySession:
        current = DummySession()
        self.sessions.append(current)
        return current


def _root_html() -> str:
    menus = (
        gimje.GIMJE_EDUCATION_MENU,
        *(source.menu for source in gimje.GIMJE_CHILD_CATALOGUES),
        gimje.GIMJE_FACILITY_MENU,
        gimje.GIMJE_EXPERIENCE_MENU,
    )
    links = "".join(
        f'<a href="/index.gimje?menuCd={menu}">{menu}</a>' for menu in menus
    )
    return (
        "<html><head><title>김제시 통합 예약 시스템</title></head>"
        f"<body>{links}</body></html>"
    )


def _record(
    identity: str,
    *,
    title: str,
    category: str,
    status: str,
    venue: str,
    period: str,
    apply_period: str,
    method: str = "온라인",
    fee: str = "무료",
    schedule: str = "월, 수요일 / 10:00 ~ 12:00",
    capacity: str = "20명",
    instructor: str = "김강사",
) -> dict[str, str]:
    return {
        "id": identity,
        "title": title,
        "category": category,
        "status": status,
        "venue": venue,
        "period": period,
        "apply_period": apply_period,
        "method": method,
        "fee": fee,
        "schedule": schedule,
        "capacity": capacity,
        "instructor": instructor,
    }


def _card(source: gimje.GimjeCatalogue, record: dict[str, str]) -> str:
    return f"""
      <li><a href="/index.gimje?menuCd={source.menu}&amp;ieduSid={record['id']}">
        <p class="title"><span class="cate orange">{record['category']}</span>
          <strong>{record['title']}</strong><span class="btn_view">자세히보기</span></p>
        <div class="con after"><p class="state state02"><span>{record['status']}</span></p><ul>
          <li><strong>접수기간</strong><span>{record['apply_period']}</span></li>
          <li><strong>교육장</strong><span>{record['venue']}</span></li>
          <li><strong>교육기간</strong><span>{record['period']}</span></li>
          <li><strong>교육료</strong><span>{record['fee']}</span></li>
          <li><strong>교육시간</strong><span>{record['schedule']}</span></li>
          <li><strong>모집인원</strong><span>{record['capacity']}</span></li>
          <li><strong>강사명</strong><span>{record['instructor']}</span></li>
          <li><strong>접수방법</strong><span>{record['method']}</span></li>
        </ul></div>
      </a></li>
    """


def _list_html(
    source: gimje.GimjeCatalogue,
    page: int,
    total: int,
    records: list[dict[str, str]],
) -> str:
    cards = "".join(_card(source, record) for record in records)
    return f"""
      <html><head><title>김제시 통합 예약 시스템</title></head><body>
        <form method="GET">
          <input name="pageIndex" value="{page}">
          <input name="menuCd" value="{source.menu}">
          <input name="iedupSid" value="">
        </form>
        <div class="system_list con_inner"><p class="total">Total <strong>{total}</strong></p>
          <ul class="edu_list">{cards}</ul>
        </div>
      </body></html>
    """


def _detail_html(
    record: dict[str, str],
    *,
    application: bool,
    title: str | None = None,
    application_href: str | None = None,
    pii_form: bool = False,
) -> str:
    code = {
        "직업능력": "A",
        "문화예술": "B",
        "인문교양": "C",
        "취업대비": "D",
        "방학특강": "F",
    }[record["category"]]
    href = application_href or (
        f"/index.gimje?menuCd={gimje.GIMJE_EDUCATION_MENU}"
        f"&amp;ieduSid={record['id']}&amp;type=rsv&amp;category={code}"
    )
    button = (
        f'<li><a class="btn btn_blue" href="{href}">접수하기</a></li>'
        if application
        else ""
    )
    form = '<form><input name="residentRegistrationNumber"></form>' if pii_form else ""
    return f"""
      <html><head><title>김제시 통합 예약 시스템</title></head><body>
      <section class="edu_view con_inner">
        <h4><span class="place">{record['venue']}</span>
          <strong>[{record['category']}] {title or record['title']}</strong></h4>
        <table class="edu_view_table"><tbody>
          <tr><th>수강대상</th><td>김제시민</td><th>모집인원</th><td>{record['capacity']}</td></tr>
          <tr><th>교육기간</th><td><strong>{record['period']}</strong></td>
              <th>교육시간</th><td>{record['schedule'].replace(' ~ ', '~')}</td></tr>
          <tr><th>교육료</th><td>{record['fee']}</td><th>면제대상</th><td>해당자</td></tr>
          <tr><th>접수기간</th><td><strong>{record['apply_period']}</strong>
                <span class="state">{record['status']}</span></td>
              <th>접수방법</th><td>{record['method']}</td></tr>
          <tr><th>강사명</th><td>{record['instructor']}</td><th>문의처</th><td>063-540-0000</td></tr>
          <tr><th>교육소개</th><td colspan="3">공개 강좌 안내</td></tr>
          <tr><th>첨부파일</th><td colspan="3"></td></tr>
        </tbody></table>
        <ul class="inline_btn"><li><a href="/index.gimje?menuCd={gimje.GIMJE_EDUCATION_MENU}">목록보기</a></li>{button}</ul>
        <div class="tab_detail"><div class="detail_con"><div class="text" id="detail02">
          <table class="place_table"><tbody>
            <tr><th>교육장</th><td>{record['venue']}</td><th>주소</th><td>전북특별자치도 김제시 교육로 1</td></tr>
            <tr><th>문의처</th><td>063-540-0000</td><th>홈페이지</th><td>https://www.gimje.go.kr</td></tr>
            <tr><th>부가이용</th><td colspan="3">주차</td></tr>
          </tbody></table>
        </div></div></div>
        {form}
      </section></body></html>
    """


def _complete_site(*, all_expired: bool = False) -> tuple[FakeSite, dict[str, dict[str, str]]]:
    lifelong = _record(
        "IEDU_000000000000901",
        title="김제 자격증 교실",
        category="직업능력",
        status="접수중" if not all_expired else "교육완료",
        venue="평생학습관",
        period="2026-08-01 ~ 2026-11-30" if not all_expired else "2025-01-01 ~ 2025-03-01",
        apply_period="2026-07-20 09:00 ~ 2026-07-25 18:00" if not all_expired else "2024-12-01 09:00 ~ 2024-12-10 18:00",
    )
    citizen = _record(
        "IEDU_000000000000902",
        title="시민 컴퓨터 교실",
        category="인문교양",
        status="접수중" if not all_expired else "교육완료",
        venue="시민정보화교육장",
        period="2026-09-01 ~ 2026-09-30" if not all_expired else "2025-04-01 ~ 2025-04-30",
        apply_period="2026-07-01 09:00 ~ 2026-07-10 23:59" if not all_expired else "2025-03-01 09:00 ~ 2025-03-10 23:59",
        method="온라인, 방문, 전화",
        instructor="",
    )
    home = _record(
        "IEDU_000000000000903",
        title="집콕 인문학",
        category="인문교양",
        status="교육완료",
        venue="집콕 평생학습교실",
        period="2025-01-01 ~ 2025-02-01",
        apply_period="상시",
        method="전화",
    )
    records = {"lifelong": lifelong, "citizen": citizen, "home": home}
    aggregate_records = [lifelong, citizen, home]
    child_records = {
        "citizen_it": [citizen],
        "lifelong": [lifelong],
        "home_learning": [home],
    }
    routes: dict[str, str | list[str]] = {gimje.GIMJE_ROOT_URL: _root_html()}
    routes[gimje.gimje_list_url("aggregate", 1)] = _list_html(
        gimje.GIMJE_AGGREGATE, 1, 3, aggregate_records
    )
    routes[gimje.gimje_list_url("aggregate", 2)] = _list_html(
        gimje.GIMJE_AGGREGATE, 2, 3, []
    )
    for source in gimje.GIMJE_CHILD_CATALOGUES:
        owned = child_records[source.key]
        routes[gimje.gimje_list_url(source.key, 1)] = _list_html(
            source, 1, len(owned), owned
        )
        routes[gimje.gimje_list_url(source.key, 2)] = _list_html(
            source, 2, len(owned), []
        )
    routes[gimje.gimje_detail_url(lifelong["id"])] = _detail_html(
        lifelong, application=not all_expired
    )
    routes[gimje.gimje_detail_url(citizen["id"])] = _detail_html(
        citizen, application=False
    )
    routes[gimje.gimje_detail_url(home["id"])] = _detail_html(home, application=False)
    return FakeSite(routes), records


def _collect(site: FakeSite, **kwargs: Any):
    return gimje.collect_gimje_education_courses(
        Target(),
        timeout=3,
        max_pages=2,
        detail_limit=10,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today=date(2026, 7, 21),
        max_workers=1,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("OTHER", gimje.GIMJE_URL),
        (gimje.GIMJE_PROVIDER, gimje.GIMJE_EDUCATION_URL),
        (gimje.GIMJE_PROVIDER, gimje.GIMJE_URL + "#fragment"),
        (gimje.GIMJE_PROVIDER, "http://www.gimje.go.kr/reserve/index.gimje"),
        (gimje.GIMJE_PROVIDER, "https://evil.example/reserve/index.gimje"),
    ],
)
def test_target_boundary_is_exact(provider: str, url: str) -> None:
    target = Target(provider=provider, url=url)
    assert gimje.is_gimje_education_target(target) is False
    rows, parser, meta = gimje.collect_gimje_education_courses(target)
    assert rows == []
    assert parser == gimje.GIMJE_PARSER
    assert meta["snapshot_complete"] is False


def test_url_builders_are_strict_and_keep_stable_identity() -> None:
    assert gimje.gimje_list_url("aggregate", 1) == gimje.GIMJE_EDUCATION_URL
    assert parse_qs(urlparse(gimje.gimje_list_url("lifelong", 7)).query) == {
        "menuCd": ["DOM_000001801002000000"],
        "pageIndex": ["7"],
    }
    identity = "IEDU_000000000000901"
    assert parse_qs(urlparse(gimje.gimje_detail_url(identity)).query) == {
        "menuCd": [gimje.GIMJE_EDUCATION_MENU],
        "ieduSid": [identity],
    }
    assert parse_qs(
        urlparse(gimje.gimje_application_url(identity, "직업능력")).query
    ) == {
        "menuCd": [gimje.GIMJE_EDUCATION_MENU],
        "ieduSid": [identity],
        "type": ["rsv"],
        "category": ["A"],
    }
    assert gimje.gimje_list_url("aggregate", "1&admin=1") == ""
    assert gimje.gimje_list_url("other", 1) == ""
    assert gimje.gimje_detail_url(identity + "&admin=1") == ""
    assert gimje.gimje_application_url(identity, "알수없음") == ""


def test_complete_partitioned_snapshot_enriches_only_current_rows() -> None:
    site, records = _complete_site()
    rows, parser, meta = _collect(site)

    assert parser == gimje.GIMJE_PARSER
    assert [row["title"] for row in rows] == [
        records["lifelong"]["title"],
        records["citizen"]["title"],
    ]
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["pagination_complete"] is True
    assert meta["child_union_complete"] is True
    assert meta["details_complete"] is True
    assert meta["total_count"] == 3
    assert meta["expired_count"] == 1
    assert meta["current_count"] == 2
    assert meta["list_pages"] == 4
    assert meta["sentinel_requests"] == 4
    assert meta["page_one_rechecks"] == 4
    assert meta["detail_pages"] == 2
    assert meta["application_control_count"] == 1
    assert meta["pii_pages_fetched"] == 0
    assert meta["catalogue_row_counts"] == {
        "김제시 교육강좌": 3,
        "시민정보화교육장": 1,
        "평생학습관": 1,
        "집콕 평생학습교실": 1,
    }
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["program_type"] == "교육" for row in rows)
    assert all(row["municipality_code"] == "5221000000" for row in rows)
    assert all(row["raw_fields"]["pii_safe_public_detail_only"] for row in rows)
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_type"] == "ONLINE_RESERVATION"
    assert rows[0]["application_url"].startswith(gimje.GIMJE_ROOT_URL)
    assert rows[1]["status"] == "OPEN"
    assert rows[1]["reservation_available"] is False
    assert "application_url" not in rows[1]
    assert not any("type=rsv" in url for url in site.calls)
    assert set(url for url in site.calls if "ieduSid=" in url) == {
        gimje.gimje_detail_url(records["lifelong"]["id"]),
        gimje.gimje_detail_url(records["citizen"]["id"]),
    }
    assert all(current.closed for current in site.sessions)


def test_complete_all_expired_snapshot_is_valid_and_fetches_no_details() -> None:
    site, records = _complete_site(all_expired=True)
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["detail_required_count"] == 0
    assert meta["detail_pages"] == 0
    assert not any(
        url == gimje.gimje_detail_url(record["id"])
        for record in records.values()
        for url in site.calls
    )


def test_fails_closed_when_immediate_post_last_page_has_a_row() -> None:
    site, records = _complete_site()
    site.routes[gimje.gimje_list_url("aggregate", 2)] = _list_html(
        gimje.GIMJE_AGGREGATE, 2, 3, [records["lifelong"]]
    )
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "immediate post-last page 2 is not empty" in meta["configured_collection_error"]


def test_fails_closed_when_child_catalogues_do_not_partition_aggregate() -> None:
    site, _records = _complete_site()
    source = next(item for item in gimje.GIMJE_CHILD_CATALOGUES if item.key == "home_learning")
    orphan = _record(
        "IEDU_000000000000999",
        title="고아 강좌",
        category="인문교양",
        status="교육완료",
        venue=source.venue,
        period="2025-01-01 ~ 2025-02-01",
        apply_period="상시",
        method="전화",
    )
    site.routes[gimje.gimje_list_url(source.key, 1)] = _list_html(
        source, 1, 1, [orphan]
    )
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "do not exactly cover aggregate identities" in meta["configured_collection_error"]


def test_fails_closed_when_page_one_changes_during_traversal() -> None:
    site, records = _complete_site()
    changed = dict(records["lifelong"], title="변경된 강좌명")
    first = site.routes[gimje.gimje_list_url("aggregate", 1)]
    assert isinstance(first, str)
    site.routes[gimje.gimje_list_url("aggregate", 1)] = [
        first,
        _list_html(
            gimje.GIMJE_AGGREGATE,
            1,
            3,
            [changed, records["citizen"], records["home"]],
        ),
    ]
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "page 1 changed during traversal" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("title", "detail title mismatch"),
        ("unsafe_apply", "unsafe application control"),
        ("pii_form", "unexpected form in public detail"),
    ],
)
def test_fails_closed_on_detail_contract_or_pii_boundary(
    mutation: str, expected: str
) -> None:
    site, records = _complete_site()
    record = records["lifelong"]
    kwargs: dict[str, Any] = {"application": True}
    if mutation == "title":
        kwargs["title"] = "다른 강좌"
    elif mutation == "unsafe_apply":
        kwargs["application_href"] = (
            "https://evil.example/index.gimje?"
            f"menuCd={gimje.GIMJE_EDUCATION_MENU}&amp;ieduSid={record['id']}"
            "&amp;type=rsv&amp;category=A"
        )
    else:
        kwargs["pii_form"] = True
    site.routes[gimje.gimje_detail_url(record["id"])] = _detail_html(record, **kwargs)

    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert expected in meta["configured_collection_error"]


def test_caps_fail_closed_before_detail_requests() -> None:
    site, records = _complete_site()
    rows, _parser, meta = gimje.collect_gimje_education_courses(
        Target(),
        max_pages=0,
        detail_limit=10,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2026-07-21",
        max_workers=1,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap 0" in meta["configured_collection_error"]
    assert not any("ieduSid=" in url for url in site.calls)

    site2, records2 = _complete_site()
    rows2, _parser2, meta2 = gimje.collect_gimje_education_courses(
        Target(),
        max_pages=2,
        detail_limit=1,
        fetcher=site2.fetcher,
        session_factory=site2.session_factory,
        today="2026-07-21",
        max_workers=1,
    )
    assert rows2 == []
    assert meta2["source_cap_reached"] is True
    assert "detail_limit cap 1" in meta2["configured_collection_error"]
    assert not any(
        url == gimje.gimje_detail_url(record["id"])
        for record in records2.values()
        for url in site2.calls
    )


def test_injected_fetcher_and_session_factory_are_atomic() -> None:
    site, _records = _complete_site()
    rows, _parser, meta = gimje.collect_gimje_education_courses(
        Target(), fetcher=site.fetcher
    )
    assert rows == []
    assert "must be injected together" in meta["configured_collection_error"]
