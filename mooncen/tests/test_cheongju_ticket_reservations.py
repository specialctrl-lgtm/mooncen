from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalIntegratedReservation as aggregate
from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import municipal_cheongju_ticket as ticket


ROOT = Path(__file__).resolve().parents[1]
TARGET_PATH = ROOT / "config" / "crawl_targets" / "public_reservation.yaml"
OPERATIONAL_PATH = ROOT / "config" / "municipal_integrated_reservation_operational.yaml"
COVERAGE_PATH = ROOT / "config" / "municipal_integrated_reservation_coverage.yaml"
REGISTRY_PATH = ROOT / "config" / "generated_yaml_crawler_registry.yaml"


@dataclass
class DummySession:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


def _target(catalogue: ticket.CheongjuTicketCatalogue) -> dict[str, str]:
    return {
        "provider": ticket.CHEONGJU_TICKET_PROVIDER,
        "url": catalogue.canonical_url,
        "name": f"청주시 통합예약 {catalogue.name} 전체",
        "branch": "충청북도 청주시",
    }


def _record(
    source_id: int | None,
    *,
    title: str,
    status: str,
    start: str,
    end: str,
    institution: str = "청주시 운영기관",
    venue: str = "청주시 교육장",
    target: str = "성인",
    fee: str = "무료",
    external_url: str = "",
) -> dict[str, Any]:
    return {
        "id": source_id,
        "title": title,
        "status": status,
        "institution": institution,
        "venue": venue,
        "target": target,
        "fee": fee,
        "apply_start": "2099-01-01" if end.startswith("2099") else "2020-01-01",
        "apply_end": "2099-01-02" if end.startswith("2099") else "2020-01-02",
        "start": start,
        "end": end,
        "external_url": external_url,
    }


def _card(
    catalogue: ticket.CheongjuTicketCatalogue,
    row: dict[str, Any],
    page: int,
) -> str:
    if row["external_url"]:
        href = row["external_url"]
    elif catalogue.kind == "education":
        href = (
            f"./selectEduLctreWebView.do?key=19&lctreNo={row['id']}&viewMode=card"
        )
    else:
        href = (
            f"./selectExprnWebView.do?exprnNo={row['id']}&viewMode=card&"
            f"pageUnit=8&pageIndex={page}&searchCnd=all&key=8"
        )
    return f"""
    <li><a href="{href}">
      <div class="option"><span class="stateType">{row['status']}</span>
      <span class="organ">{row['institution']}</span><span class="pay">{row['fee']}</span></div>
      <span class="title">{row['title']}</span>
      <ul class="prgInformation">
        <li><span>장소</span>{row['venue']}</li>
        <li><span>대상</span>{row['target']}</li>
        <li><span>접수</span>{row['apply_start']} ~ {row['apply_end']}</li>
        <li><span>운영</span>{row['start']} ~ {row['end']}</li>
      </ul>
    </a></li>
    """


def _list_html(
    catalogue: ticket.CheongjuTicketCatalogue,
    records: list[dict[str, Any]],
    page: int,
    *,
    title_suffix: str = "",
    bad_card_href: bool = False,
) -> str:
    total = len(records)
    total_pages = (total + ticket.CHEONGJU_TICKET_PAGE_SIZE - 1) // ticket.CHEONGJU_TICKET_PAGE_SIZE
    start = (page - 1) * ticket.CHEONGJU_TICKET_PAGE_SIZE
    page_rows = [dict(row) for row in records[start : start + ticket.CHEONGJU_TICKET_PAGE_SIZE]]
    if title_suffix and page_rows:
        page_rows[0]["title"] += title_suffix
    cards = "".join(_card(catalogue, row, page) for row in page_rows)
    if page > total_pages and not page_rows:
        cards = (
            '<li class="noDataList">'
            '검색하신 내용을 찾을 수 없습니다. 조건을 바꾸어 다시 검색해보세요.'
            "</li>"
        )
    if bad_card_href and page_rows:
        cards = cards.replace(
            _card(catalogue, page_rows[0], page),
            _card(
                catalogue,
                {**page_rows[0], "external_url": "https://ticket.cheongju.go.kr/www/selectBbsNttView.do?bbsNo=1&nttNo=99"},
                page,
            ),
            1,
        )
    return f"""
    <html><body>
      <a href="/www/selectBbsNttList.do?bbsNo=1&key=70">공지사항</a>
      <div class="listWrap thumbnail show">
        <div class="dataCount">총 : <em>{total}</em>건 / 페이지 {page}/{total_pages}</div>
        <ul>{cards}</ul>
      </div>
      <div class="listWrap detail"><table><tbody><tr><td>duplicate visual view</td></tr></tbody></table></div>
    </body></html>
    """


def _detail_html(
    catalogue: ticket.CheongjuTicketCatalogue,
    row: dict[str, Any],
    *,
    bad_title: bool = False,
    application_mismatch: bool = False,
) -> str:
    source_id = row["id"]
    title = row["title"] + (" changed" if bad_title else "")
    address = (
        "충북 청주시 상당구 상당로 1 청주시 교육장"
        if source_id % 2
        else "충북 청주시 흥덕구 직지대로 2 청주시 교육장"
    )
    if catalogue.kind == "education":
        fields = {
            "운영기관": row["institution"],
            "강좌명": title,
            "대상": row["target"],
            "장소": row["venue"],
            "주소": address,
            "접수기간": f"{row['apply_start']} 09:00 ~ {row['apply_end']} 18:00",
            "운영기간": f"{row['start']} ~ {row['end']}",
            "운영요일": "화",
            "운영시간": "10:00~12:00",
            "모집인원": "모집인원: 20 명 / 신청인원: 2 명",
            "이용요금": row["fee"],
            "선별방법": "선착순",
            "예약방법": "온라인",
        }
        application_path = "eduAplctAgreWebView.do"
    else:
        fields = {
            "운영기관": row["institution"],
            "대상": row["target"],
            "장소": row["venue"],
            "주소": address,
            "접수기간": f"{row['apply_start']} 09:00 ~ {row['apply_end']} 18:00",
            "체험기간": f"{row['start']} ~ {row['end']}",
            "모집수": "25 명",
            "체험요금": row["fee"],
            "선별방법": "선착순",
            "예약방법": "온라인",
        }
        application_path = "exprnApplCalendarWebView.do"
    rows = "".join(
        f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in fields.items()
    )
    application = ""
    if row["status"] in {"접수예정", "접수중", "추가모집", "대기자접수"}:
        bound_id = source_id + 1 if application_mismatch else source_id
        application = (
            f'<a href="./{application_path}?{catalogue.identity_param}={bound_id}&key={catalogue.key}">신청하기</a>'
        )
    return f"""
    <html><body><main id="contents">
      <div class="viewProgram simpleInformation"><div class="title">
        <span class="stateType">{row['status']}</span><strong>{title}</strong>
      </div><div class="linkGroup">{application}
        <a href="./selectBbsNttList.do?bbsNo=1&key=70">공지사항</a>
      </div></div>
      <h4 class="noLine">{catalogue.name}정보</h4>
      <div class="itemWrap"><table><tbody>{rows}</tbody></table></div>
      <h4>상세내용</h4><div class="itemWrap">
        안전한 공개 설명 010-1234-5678 teacher@example.test 900101-1234567
      </div>
    </main></body></html>
    """


class FakeTicketSite:
    def __init__(
        self,
        catalogue: ticket.CheongjuTicketCatalogue,
        records: list[dict[str, Any]],
        *,
        mutate_boundary: bool = False,
        bad_detail: bool = False,
        bad_card_href: bool = False,
        application_mismatch: bool = False,
    ) -> None:
        self.catalogue = catalogue
        self.records = records
        self.mutate_boundary = mutate_boundary
        self.bad_detail = bad_detail
        self.bad_card_href = bad_card_href
        self.application_mismatch = application_mismatch
        self.calls: list[str] = []
        self.pages: Counter[int] = Counter()

    def __call__(self, _session: object, url: str, timeout: int) -> str:
        assert timeout > 0
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == self.catalogue.list_path:
            page = int(query["pageIndex"][0])
            self.pages[page] += 1
            suffix = " MUTATED" if self.mutate_boundary and page == 1 and self.pages[page] > 1 else ""
            return _list_html(
                self.catalogue,
                self.records,
                page,
                title_suffix=suffix,
                bad_card_href=self.bad_card_href,
            )
        assert parsed.path == self.catalogue.detail_path
        source_id = int(query[self.catalogue.identity_param][0])
        row = next(row for row in self.records if row["id"] == source_id)
        return _detail_html(
            self.catalogue,
            row,
            bad_title=self.bad_detail,
            application_mismatch=self.application_mismatch,
        )


def _education_records() -> list[dict[str, Any]]:
    return [
        _record(101, title="현재 접수 강좌", status="접수중", start="2099-02-01", end="2099-02-28"),
        _record(102, title="현재 운영 강좌", status="운영중", start="2099-02-01", end="2099-12-31"),
        *[
            _record(
                103 + index,
                title=f"종료 강좌 {index}",
                status="종료",
                start="2020-02-01",
                end="2020-02-28",
            )
            for index in range(7)
        ],
    ]


def _experience_records() -> list[dict[str, Any]]:
    external = list(ticket.CHEONGJU_TICKET_EXTERNAL_EXPERIENCES)
    return [
        _record(
            201,
            title="현재 생태 체험",
            status="접수예정",
            start="2099-03-01",
            end="2099-03-31",
            venue="생태 체험장",
            target="아동 | 성인",
        ),
        _record(
            None,
            title="문암생태공원캠핑장",
            status="접수중",
            start="2099-01-01",
            end="2099-12-31",
            institution="캠핑장",
            venue="문암생태공원캠핑장",
            target="제한없음",
            fee="유료",
            external_url=external[0],
        ),
        _record(
            None,
            title="오창미래지농촌테마공원캠핑장",
            status="접수중",
            start="2099-01-01",
            end="2099-12-31",
            institution="캠핑장",
            venue="오창미래지농촌테마공원캠핑장",
            target="제한없음",
            fee="유료",
            external_url=external[1],
        ),
        _record(202, title="종료 체험", status="종료", start="2020-01-01", end="2020-01-02"),
    ]


def _collect(site: FakeTicketSite, *, detail_limit: int = 20):
    return ticket.collect_cheongju_ticket_reservations(
        _target(site.catalogue),
        fetcher=site,
        session_factory=DummySession,
        today="2099-01-15",
        max_pages=20,
        detail_limit=detail_limit,
        max_workers=1,
    )


def test_education_collects_every_page_current_details_and_excludes_notice_routes() -> None:
    site = FakeTicketSite(ticket.CHEONGJU_TICKET_EDUCATION, _education_records())

    rows, parser, meta = _collect(site)

    assert parser == ticket.CHEONGJU_TICKET_EDUCATION_PARSER
    assert [row["title"] for row in rows] == ["현재 접수 강좌", "현재 운영 강좌"]
    assert meta["source_total"] == 9
    assert meta["pages"] == 2
    assert meta["list_requests"] == 3
    assert meta["list_recheck_requests"] == 3
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["expired_count"] == 7
    assert meta["current_count"] == meta["returned_count"] == 2
    assert meta["notice_links_excluded"] == 2
    assert meta["notice_board_requests"] == 0
    assert meta["application_endpoint_requests"] == 0
    assert meta["authentication_endpoint_requests"] == 0
    assert meta["snapshot_complete"] is True
    assert all(row["collection_category"] == "공공예약" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["application_url"] == rows[0]["raw_url"]
    assert rows[1]["status"] == "CLOSED"
    assert rows[1]["application_url"] == ""
    assert {row["municipality_code"] for row in rows} == {"4311100000", "4311300000"}
    assert len({row["branch_code"] for row in rows}) == 2
    assert all(len(row["branch_code"]) <= 50 for row in rows)
    serialized = str(rows)
    assert "010-1234-5678" not in serialized
    assert "teacher@example.test" not in serialized
    assert "900101-1234567" not in serialized
    assert all("selectBbs" not in url for url in site.calls)
    assert all("Aplct" not in url and "Appl" not in url for url in site.calls)


def test_same_operator_in_two_districts_keeps_two_writer_branches(monkeypatch) -> None:
    rows, _parser, _meta = _collect(
        FakeTicketSite(ticket.CHEONGJU_TICKET_EDUCATION, _education_records())
    )
    writer = municipal.MunicipalDbWriter(ticket.CHEONGJU_TICKET_PROVIDER)
    branch_calls: list[tuple[str, str, str]] = []
    course_branch_ids: list[str] = []

    def save_branch(
        branch_code: str,
        _name: str,
        *_args: Any,
        region_sido: str = "",
        region_sigungu: str = "",
        **_kwargs: Any,
    ) -> str:
        branch_calls.append((branch_code, region_sido, region_sigungu))
        return f"branch-{len(branch_calls)}"

    def save_course(course: dict[str, Any]) -> bool:
        course_branch_ids.append(str(course["branch_id"]))
        return True

    @contextmanager
    def fake_db_cursor(*_args: Any, **_kwargs: Any):
        yield object()

    monkeypatch.setattr(writer, "save_branch", save_branch)
    monkeypatch.setattr(writer, "save_course", save_course)
    monkeypatch.setattr(municipal, "get_db_cursor", fake_db_cursor)
    monkeypatch.setattr(
        municipal,
        "delete_empty_branches_for_provider",
        lambda *_args, **_kwargs: None,
    )

    assert writer.save_rows(rows) == 2
    assert len(branch_calls) == 2
    assert {call[1:] for call in branch_calls} == {
        ("충청북도", "청주시 상당구"),
        ("충청북도", "청주시 흥덕구"),
    }
    assert course_branch_ids == ["branch-1", "branch-2"]


def test_experience_keeps_two_exact_external_rows_without_fetching_them() -> None:
    site = FakeTicketSite(ticket.CHEONGJU_TICKET_EXPERIENCE, _experience_records())

    rows, parser, meta = _collect(site)

    assert parser == ticket.CHEONGJU_TICKET_EXPERIENCE_PARSER
    assert len(rows) == 3
    assert meta["source_total"] == 4
    assert meta["current_internal_count"] == 1
    assert meta["current_external_count"] == 2
    assert meta["detail_attempts"] == meta["detail_pages"] == 1
    assert meta["snapshot_complete"] is True
    assert all(row["collection_category"] == "공공예약" for row in rows)
    assert all(row["domain_category"] == "체험·견학" for row in rows)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    external = [row for row in rows if row["raw_fields"].get("external_reference")]
    assert {row["municipality_code"] for row in external} == {"4311300000", "4311400000"}
    assert all(row["application_url"] == row["raw_url"] for row in external)
    assert all(row["raw_fields"]["external_endpoint_fetched"] is False for row in external)
    assert all(not url.startswith("https://munam.cheongju.go.kr") for url in site.calls)


def test_exact_targets_reject_search_shell_aliases_and_extra_query_without_fetch() -> None:
    site = FakeTicketSite(ticket.CHEONGJU_TICKET_EDUCATION, _education_records())
    for url in (
        ticket.CHEONGJU_TICKET_SEARCH_URL,
        ticket.CHEONGJU_TICKET_EDUCATION_URL + "&pageIndex=1",
        ticket.CHEONGJU_TICKET_EDUCATION_URL.replace("https://", "http://"),
        ticket.CHEONGJU_TICKET_EDUCATION_URL.replace("ticket.", "www.ticket."),
    ):
        rows, _parser, meta = ticket.collect_cheongju_ticket_reservations(
            _target(ticket.CHEONGJU_TICKET_EDUCATION) | {"url": url},
            fetcher=site,
            session_factory=DummySession,
        )
        assert rows == []
        assert meta["snapshot_complete"] is False
    assert site.calls == []


def test_notice_card_detail_mismatch_and_unbound_application_fail_closed() -> None:
    cases = (
        FakeTicketSite(ticket.CHEONGJU_TICKET_EDUCATION, _education_records(), bad_card_href=True),
        FakeTicketSite(ticket.CHEONGJU_TICKET_EDUCATION, _education_records(), bad_detail=True),
        FakeTicketSite(ticket.CHEONGJU_TICKET_EDUCATION, _education_records(), application_mismatch=True),
    )
    for site in cases:
        rows, _parser, meta = _collect(site)
        assert rows == []
        assert meta["snapshot_complete"] is False
        assert meta["configured_collection_error"]
        assert all("selectBbs" not in url for url in site.calls)
        assert all("Aplct" not in url and "Appl" not in url for url in site.calls)


def test_boundary_mutation_and_detail_cap_fail_closed() -> None:
    mutating = FakeTicketSite(
        ticket.CHEONGJU_TICKET_EDUCATION,
        _education_records(),
        mutate_boundary=True,
    )
    rows, _parser, meta = _collect(mutating)
    assert rows == []
    assert "boundary page 1 changed" in meta["configured_collection_error"]

    capped = FakeTicketSite(ticket.CHEONGJU_TICKET_EDUCATION, _education_records())
    rows, _parser, meta = _collect(capped, detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert not any("selectEduLctreWebView" in url for url in capped.calls)


def test_target_operational_coverage_and_atomic_provider_contract(monkeypatch) -> None:
    targets = yaml.safe_load(TARGET_PATH.read_text(encoding="utf-8"))["targets"]
    configured = [
        row for row in targets if row.get("provider") == ticket.CHEONGJU_TICKET_PROVIDER
    ]
    assert {row["url"] for row in configured} == {
        ticket.CHEONGJU_TICKET_EDUCATION_URL,
        ticket.CHEONGJU_TICKET_EXPERIENCE_URL,
    }
    assert {row["service_group"] for row in configured} == {"공공강좌", "체험"}
    assert {
        (
            row["collection_category"],
            row["domain_category"],
            row["service_group"],
            row["service_group_policy"],
        )
        for row in configured
    } == {
        ("공공예약", "교육·강좌", "공공강좌", "locked"),
        ("공공예약", "체험·견학", "체험", "locked"),
    }
    assert all(row["service_group_policy"] == "locked" for row in configured)
    assert {
        row["service_group"]: row["ops_scopes"] for row in configured
    } == {"공공강좌": ["education"], "체험": ["experience"]}
    assert all(row["full_snapshot_required"] is True for row in configured)
    assert all(row["crawler_status"] == "ready" for row in configured)

    entries = aggregate.load_operational_entries(OPERATIONAL_PATH)
    operational = [
        row for row in entries if row["provider"] == ticket.CHEONGJU_TICKET_PROVIDER
    ]
    assert {row["target_url"] for row in operational} == {
        ticket.CHEONGJU_TICKET_EDUCATION_URL,
        ticket.CHEONGJU_TICKET_EXPERIENCE_URL,
    }
    assert [row["row_count"] for row in operational] == [433, 99]
    selected = aggregate.select_operational_targets(
        targets, operational, scheduled_providers=set()
    )
    selected_ticket = [
        row for row in selected if row.get("provider") == ticket.CHEONGJU_TICKET_PROVIDER
    ]
    assert len(selected_ticket) == 2
    assert {row["service_group"] for row in selected_ticket} == {"공공강좌", "체험"}
    assert {
        (row["domain_category"], row["service_group_policy"])
        for row in selected_ticket
    } == {("교육·강좌", "locked"), ("체험·견학", "locked")}

    coverage = yaml.safe_load(COVERAGE_PATH.read_text(encoding="utf-8"))["municipalities"]
    cheongju_rows = [row for row in coverage if str(row.get("code", "")).startswith("4311") and row.get("code") in {"4311000000", "4311100000", "4311200000", "4311300000", "4311400000"}]
    assert len(cheongju_rows) == 5
    assert all(ticket.CHEONGJU_TICKET_PROVIDER in row["owner_providers"] for row in cheongju_rows)
    assert all(ticket.CHEONGJU_TICKET_PROVIDER in row["promoted_providers"] for row in cheongju_rows)
    assert all(ticket.CHEONGJU_TICKET_PROVIDER in row["yaml_owner_providers"] for row in cheongju_rows)

    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    registry_rows = registry["targets"] if isinstance(registry, dict) else registry
    assert ticket.CHEONGJU_TICKET_PROVIDER not in {row["provider"] for row in registry_rows}
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[ticket.CHEONGJU_TICKET_PROVIDER] == (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "700",
    )

    sentinel = ([{"title": "ticket sentinel"}], ticket.CHEONGJU_TICKET_EDUCATION_PARSER, {"pages": 2})
    monkeypatch.setattr(ticket, "collect_cheongju_ticket_reservations", lambda *_args, **_kwargs: sentinel)
    target = municipal.CrawlTarget(
        provider=ticket.CHEONGJU_TICKET_PROVIDER,
        name="청주시 통합예약 교육·강좌 전체",
        branch="충청북도 청주시",
        url=ticket.CHEONGJU_TICKET_EDUCATION_URL,
        source="test",
        priority=1,
        region="충청북도 청주시",
        extra={"source_group": "municipal_reservation"},
    )
    assert municipal.collect_from_url(target, max_pages=100, detail_limit=700) == sentinel
