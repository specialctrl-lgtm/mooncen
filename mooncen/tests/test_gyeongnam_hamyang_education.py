from __future__ import annotations

from collections import Counter
from html import escape
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_gyeongnam_hamyang as hamyang


MUNICIPAL_TARGET = {
    "provider": hamyang.HAMYANG_PROVIDER,
    "url": hamyang.HAMYANG_CONFIGURED_URL,
}
WELFARE_TARGET = {
    "provider": hamyang.HAMYANG_WELFARE_PROVIDER,
    "url": hamyang.HAMYANG_WELFARE_URL,
}


class _Session:
    def close(self) -> None:
        pass


def _session_factory() -> _Session:
    return _Session()


def _option(value: str, label: str, selected: bool = False) -> str:
    marker = " selected" if selected else ""
    return f'<option value="{escape(value)}"{marker}>{escape(label)}</option>'


def _control(
    *,
    label: str,
    active: bool,
    path: str,
    course_id: int,
    requested_page: int,
    term: hamyang.WelfareTerm | None = None,
    bad_active_route: bool = False,
    bad_inactive_href: bool = False,
) -> str:
    if not active:
        href = ' href="/identity/check"' if bad_inactive_href else ""
        return f'<a class="button muted"{href}>{escape(label)}</a>'
    if bad_active_route:
        href = f"{path}?amode=insert"
    else:
        values = ["amode=insert", f"courseIdx={course_id}"]
        if requested_page > 1:
            values.append(f"cpage={requested_page}")
        if term is not None:
            values.extend((f"sessionIdx={term.identity}", f"syear={term.year}"))
        href = path + "?" + "&".join(values)
    return f'<a class="button" href="{escape(href, quote=True)}">{escape(label)}</a>'


def _municipal_card(
    row: dict[str, Any],
    *,
    catalogue: hamyang.HamyangCatalogue,
    requested_page: int,
    bad_active_route: bool = False,
    bad_inactive_href: bool = False,
) -> str:
    control = _control(
        label="참가가능" if row["active"] else "참가불가",
        active=row["active"],
        path=catalogue.path,
        course_id=row["course_id"],
        requested_page=requested_page,
        bad_active_route=bad_active_route and row["active"],
        bad_inactive_href=bad_inactive_href and not row["active"],
    )
    fields = (
        ("교육기간", row["period"]),
        ("교육시간", row["schedule"]),
        ("신청인원", "3명"),
        ("장소", row["venue"]),
    )
    items = "".join(
        '<li><span class="t1">{}</span><span class="t2">{}</span></li>'.format(
            escape(label), escape(str(value))
        )
        for label, value in fields
    )
    return (
        '<li class="column">'
        f'<div class="hg1"><h2 class="h1">{escape(row["title"])}</h2></div>'
        f'<div class="tg1"><ul>{items}</ul></div>'
        f'<div class="btns">{control}</div>'
        "</li>"
    )


def _municipal_html(
    catalogue: hamyang.HamyangCatalogue,
    all_rows: list[dict[str, Any]],
    requested_page: int,
    *,
    sentinel_mutation: bool = False,
    boundary_mutation: bool = False,
    bad_active_route: bool = False,
    bad_inactive_href: bool = False,
) -> str:
    last = max(1, (len(all_rows) + hamyang.HAMYANG_PAGE_SIZE - 1) // hamyang.HAMYANG_PAGE_SIZE)
    displayed = min(requested_page, last)
    start = (displayed - 1) * hamyang.HAMYANG_PAGE_SIZE
    page_rows = [dict(row) for row in all_rows[start : start + hamyang.HAMYANG_PAGE_SIZE]]
    if page_rows and (sentinel_mutation or boundary_mutation):
        page_rows[0]["title"] += " 변조"
    action = catalogue.path
    if requested_page > 1:
        action += f"?cpage={requested_page}"
    cards = "".join(
        _municipal_card(
            row,
            catalogue=catalogue,
            requested_page=requested_page,
            bad_active_route=bad_active_route,
            bad_inactive_href=bad_inactive_href,
        )
        for row in page_rows
    )
    return f"""
    <html><body class="site_depart lv1_01662 {catalogue.body_section_class}">
      <h1 class="hb1 h1">{catalogue.heading}</h1>
      <form id="listForm" name="listForm" method="get" action="{escape(action, quote=True)}">
        <input type="hidden" name="cpage" value="1">
        <select name="stype"><option value="name" selected>교육과정명</option></select>
        <input name="sstring" value="">
      </form>
      <div id="body_content">
        <div class="info1">총 {len(all_rows)}건의 과정이 있습니다. ({displayed}/{last}페이지)</div>
        <div class="edu1list1"><ul>{cards}</ul></div>
      </div>
    </body></html>
    """


def _welfare_inventory_html(
    terms: tuple[hamyang.WelfareTerm, ...], selected: str
) -> str:
    return _option("0", "전체") + "".join(
        _option(term.identity, term.label, term.identity == selected) for term in terms
    )


def _welfare_controls_html(
    terms: tuple[hamyang.WelfareTerm, ...], selected: str, *, discovery: bool
) -> str:
    selected_term = next((term for term in terms if term.identity == selected), terms[-1])
    year = terms[-1].year if discovery else selected_term.year
    return f"""
      <select name="sessionIdx">{_welfare_inventory_html(terms, selected)}</select>
      <select name="syear">{_option(year, year, True)}</select>
      <select name="applyFlag">
        {_option('', '전체')}{_option('Y', '신청가능')}{_option('F', '신청불가')}
      </select>
      <select name="stype">{_option('title', '교육명', True)}{_option('teacher_idx', '강사명')}</select>
      <input name="sstring" value="">
    """


def _welfare_card(
    row: dict[str, Any],
    *,
    term: hamyang.WelfareTerm,
    requested_page: int,
    bad_active_route: bool = False,
    bad_inactive_href: bool = False,
) -> str:
    control = _control(
        label="신청가능" if row["active"] else "신청불가",
        active=row["active"],
        path=hamyang.HAMYANG_WELFARE_PATH,
        course_id=row["course_id"],
        requested_page=requested_page,
        term=term,
        bad_active_route=bad_active_route and row["active"],
        bad_inactive_href=bad_inactive_href and not row["active"],
    )
    fields = (
        ("교육대상", "함양군민"),
        ("신청기간", "2099.07.01 ~ 2099.07.31"),
        ("교육기간", row["period"]),
        ("교육시간", "10:00~12:00"),
        ("교육요일", "토요일"),
        ("교육정원", "12명"),
        ("신청현황", "4명"),
        ("강사", "김강사 010-1234-5678 teacher@example.org"),
    )
    items = "".join(
        '<li><span class="t1">{}</span><span class="t2">{}</span></li>'.format(
            escape(label), escape(str(value))
        )
        for label, value in fields
    )
    return (
        '<li class="column">'
        f'<div class="hg1"><h2 class="h1">{escape(row["title"])}</h2>'
        f'<span class="t1">{escape(term.label)} 과정</span></div>'
        f'<div class="tg1"><ul>{items}</ul></div>'
        f'<div class="btns">{control}</div>'
        '<aside>신청자 홍길동 010-9999-8888 applicant@example.org</aside>'
        "</li>"
    )


def _welfare_discovery_html(
    terms: tuple[hamyang.WelfareTerm, ...], *, inventory_mutation: bool = False
) -> str:
    displayed_terms = list(terms)
    if inventory_mutation:
        displayed_terms[-1] = hamyang.WelfareTerm(
            displayed_terms[-1].identity, "잘못된 학기", displayed_terms[-1].year
        )
    inventory = tuple(displayed_terms)
    controls = _welfare_controls_html(
        inventory, inventory[-1].identity, discovery=True
    )
    return f"""
    <html><body><h1 class="hb1 h1">신청하기</h1>
      <form id="listForm" name="listForm" method="get" action="{hamyang.HAMYANG_WELFARE_PATH}">
        <input type="hidden" name="cpage" value="1">{controls}
      </form><div id="body_content"><div class="edu1list1"><ul></ul></div></div>
    </body></html>
    """


def _welfare_html(
    term: hamyang.WelfareTerm,
    terms: tuple[hamyang.WelfareTerm, ...],
    all_rows: list[dict[str, Any]],
    requested_page: int,
    *,
    inventory_mutation: bool = False,
    sentinel_mutation: bool = False,
    boundary_mutation: bool = False,
    bad_active_route: bool = False,
    bad_inactive_href: bool = False,
) -> str:
    last = max(1, (len(all_rows) + hamyang.HAMYANG_WELFARE_PAGE_SIZE - 1) // hamyang.HAMYANG_WELFARE_PAGE_SIZE)
    displayed = min(requested_page, last)
    start = (displayed - 1) * hamyang.HAMYANG_WELFARE_PAGE_SIZE
    page_rows = [dict(row) for row in all_rows[start : start + hamyang.HAMYANG_WELFARE_PAGE_SIZE]]
    if page_rows and (sentinel_mutation or boundary_mutation):
        page_rows[0]["title"] += " 변조"
    inventory = list(terms)
    if inventory_mutation:
        inventory[0] = hamyang.WelfareTerm(
            inventory[0].identity, "변경된 학기", inventory[0].year
        )
    inventory_tuple = tuple(inventory)
    controls = _welfare_controls_html(
        inventory_tuple, term.identity, discovery=False
    )
    action = urlparse(hamyang.hamyang_welfare_term_url(term, requested_page))
    action_value = action.path + ("?" + action.query if action.query else "")
    cards = "".join(
        _welfare_card(
            row,
            term=term,
            requested_page=requested_page,
            bad_active_route=bad_active_route,
            bad_inactive_href=bad_inactive_href,
        )
        for row in page_rows
    )
    return f"""
    <html><body><h1 class="hb1 h1">신청하기</h1>
      <form id="listForm" name="listForm" method="get" action="{escape(action_value, quote=True)}">
        <input type="hidden" name="cpage" value="1">{controls}
      </form>
      <div id="body_content">
        <div class="info1">총 {len(all_rows)}건의 게시물이 있습니다. ({displayed}/{last}페이지)</div>
        <div class="edu1list1"><ul>{cards}</ul></div>
      </div>
    </body></html>
    """


def _municipal_rows(
    catalogue: hamyang.HamyangCatalogue, count: int, current: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(count):
        future = index < current
        rows.append(
            {
                "course_id": (10000 if catalogue.key == "resident_hamyang_eup" else 20000) + index,
                "title": f"{catalogue.key} 강좌 {index:02d}",
                "period": "2099.08.01 ~ 2099.08.31" if future else "2020.01.01 ~ 2020.01.31",
                "schedule": f"매주 토요일 {10 + index % 4}:00~12:00",
                "venue": f"{catalogue.branch} 강의실 {index:02d}",
                "active": future and index == 0,
            }
        )
    return rows


def _welfare_rows(term: hamyang.WelfareTerm, count: int, current: int) -> list[dict[str, Any]]:
    return [
        {
            "course_id": int(term.identity) * 1000 + index,
            "title": f"{term.label} 복지 강좌 {index:02d}",
            "period": "2099.08.01 ~ 2099.08.31" if index < current else "2020.01.01 ~ 2020.01.31",
            "active": index == 0 and current > 0,
        }
        for index in range(count)
    ]


class _FakeSite:
    def __init__(self, mutation: str = "") -> None:
        self.mutation = mutation
        self.calls: list[str] = []
        self.counts: Counter[tuple[str, str, int]] = Counter()
        self.lock = threading.Lock()
        self.catalogue_rows = {
            hamyang.HAMYANG_CATALOGUES[0].key: _municipal_rows(
                hamyang.HAMYANG_CATALOGUES[0], 12, 1
            ),
            hamyang.HAMYANG_CATALOGUES[1].key: _municipal_rows(
                hamyang.HAMYANG_CATALOGUES[1], 11, 2
            ),
        }
        self.terms = (
            hamyang.WelfareTerm("19", "2099년 상반기", "2099"),
            hamyang.WelfareTerm("20", "2099년 하반기(추가)", "2099"),
        )
        self.term_rows = {
            "19": _welfare_rows(self.terms[0], 10, 3),
            "20": _welfare_rows(self.terms[1], 1, 1),
        }
        if mutation == "municipal_duplicate_id":
            self.catalogue_rows[hamyang.HAMYANG_CATALOGUES[0].key][1] = dict(
                self.catalogue_rows[hamyang.HAMYANG_CATALOGUES[0].key][0]
            )
        if mutation == "municipal_semantic_duplicate":
            source = self.catalogue_rows[hamyang.HAMYANG_CATALOGUES[0].key][0]
            target = self.catalogue_rows[hamyang.HAMYANG_CATALOGUES[1].key][0]
            for key in ("title", "period", "schedule", "venue"):
                target[key] = source[key]
        if mutation == "welfare_duplicate_id":
            self.term_rows["19"][1] = dict(self.term_rows["19"][0])

    def fetch(
        self,
        _session: _Session,
        method: str,
        url: str,
        *,
        timeout: int,
        data: dict[str, Any],
    ) -> str:
        assert method == "GET"
        assert timeout >= 1
        assert data == {}
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        assert "amode" not in query
        assert parsed.path not in {
            hamyang.HAMYANG_RESIDENT_CHECK_PATH,
            hamyang.HAMYANG_SPECIAL_CHECK_PATH,
            hamyang.HAMYANG_WELFARE_CHECK_PATH,
        }
        requested_page = int((query.get("cpage") or ["1"])[0])
        term_id = (query.get("sessionIdx") or [""])[0]
        key = (parsed.path, term_id, requested_page)
        with self.lock:
            self.calls.append(url)
            self.counts[key] += 1
            occurrence = self.counts[key]

        if parsed.path == hamyang.HAMYANG_WELFARE_PATH:
            if not term_id:
                return _welfare_discovery_html(
                    self.terms,
                    inventory_mutation=self.mutation == "welfare_discovery_inventory",
                )
            term = next(term for term in self.terms if term.identity == term_id)
            rows = self.term_rows[term_id]
            last = max(1, (len(rows) + hamyang.HAMYANG_WELFARE_PAGE_SIZE - 1) // hamyang.HAMYANG_WELFARE_PAGE_SIZE)
            return _welfare_html(
                term,
                self.terms,
                rows,
                requested_page,
                inventory_mutation=self.mutation == "welfare_inventory",
                sentinel_mutation=(
                    self.mutation == "welfare_sentinel"
                    and term_id == "19"
                    and requested_page == last + 1
                ),
                boundary_mutation=(
                    self.mutation == "welfare_boundary"
                    and term_id == "19"
                    and requested_page == 1
                    and occurrence > 1
                ),
                bad_active_route=self.mutation == "welfare_bad_active_route",
                bad_inactive_href=self.mutation == "welfare_bad_inactive_href",
            )

        catalogue = next(item for item in hamyang.HAMYANG_CATALOGUES if item.path == parsed.path)
        rows = self.catalogue_rows[catalogue.key]
        last = max(1, (len(rows) + hamyang.HAMYANG_PAGE_SIZE - 1) // hamyang.HAMYANG_PAGE_SIZE)
        return _municipal_html(
            catalogue,
            rows,
            requested_page,
            sentinel_mutation=(
                self.mutation == "municipal_sentinel"
                and catalogue.key == "resident_hamyang_eup"
                and requested_page == last + 1
            ),
            boundary_mutation=(
                self.mutation == "municipal_boundary"
                and catalogue.key == "resident_hamyang_eup"
                and requested_page == 1
                and occurrence > 1
            ),
            bad_active_route=self.mutation == "municipal_bad_active_route",
            bad_inactive_href=self.mutation == "municipal_bad_inactive_href",
        )


def _run_municipal(
    site: _FakeSite,
    *,
    today: str = "2099-07-20",
    max_pages: int = 20,
    dedupe_rows: Any = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    return hamyang.collect_gyeongnam_hamyang_education_courses(
        MUNICIPAL_TARGET,
        max_pages=max_pages,
        detail_limit=250,
        session_factory=_session_factory,
        fetcher=site.fetch,
        today=today,
        dedupe_rows=dedupe_rows,
    )


def _run_welfare(
    site: _FakeSite,
    *,
    today: str = "2099-07-20",
    max_pages: int = 20,
    dedupe_rows: Any = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    return hamyang.collect_gyeongnam_hamyang_education_courses(
        WELFARE_TARGET,
        max_pages=max_pages,
        detail_limit=250,
        session_factory=_session_factory,
        fetcher=site.fetch,
        today=today,
        max_workers=2,
        dedupe_rows=dedupe_rows,
    )


def test_target_candidate_and_alias_decisions_are_exact() -> None:
    assert hamyang.is_target(MUNICIPAL_TARGET)
    assert hamyang.is_target(WELFARE_TARGET)
    assert not hamyang.is_target(
        {"provider": hamyang.HAMYANG_PROVIDER, "url": hamyang.HAMYANG_SPECIAL_URL}
    )
    assert hamyang.is_gyeongnam_hamyang_alias_target(
        {
            "provider": hamyang.HAMYANG_SPECIAL_PROVIDER,
            "url": hamyang.HAMYANG_SPECIAL_URL,
        }
    )
    assert set(hamyang.HAMYANG_CANDIDATE_DECISIONS) == {
        "MUNI_IR_6F4744156061",
        "MUNI_IR_BBAEE33C7D10",
        "MUNI_IR_BD02D885AA7F",
        "MUNI_IR_D5D209E1BEF2",
    }
    assert "include_under_existing_municipal_owner" in hamyang.HAMYANG_CANDIDATE_DECISIONS[
        "MUNI_IR_BD02D885AA7F"
    ]
    assert len(hamyang.HAMYANG_SEPARATE_SURFACES) == 3


def test_municipal_complete_two_catalogue_snapshot_and_application_contract() -> None:
    site = _FakeSite()
    rows, parser, meta = _run_municipal(site)

    assert parser == hamyang.HAMYANG_PARSER
    assert len(rows) == 3
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 23
    assert meta["current_count"] == 3
    assert meta["expired_count"] == 20
    assert meta["required_list_requests"] == meta["list_requests"] == 10
    assert meta["catalogue_totals"] == {
        "resident_hamyang_eup": 12,
        "education_special_zone": 11,
    }
    assert meta["sentinel_counts"] == {
        "resident_hamyang_eup": 2,
        "education_special_zone": 1,
    }
    assert meta["duplicate_source_id_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["branch_count"] == 2
    assert meta["status_counts"] == {"OPEN": 2, "CLOSED": 1}
    assert meta["application_type_counts"] == {
        "ONLINE_RESERVATION": 2,
        "INFO_ONLY": 1,
    }
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["raw_fields"]["detail_required"] is False for row in rows)
    assert all(row["raw_fields"]["application_form_fetched"] is False for row in rows)
    assert all("amode=" not in url for url in site.calls)
    assert all(urlparse(url).path not in meta["blocked_pii_paths"] for url in site.calls)
    active = [row for row in rows if row["reservation_available"]]
    assert len(active) == 2
    assert all("amode=insert&courseIdx=" in row["application_url"] for row in active)


def test_municipal_no_current_is_valid_complete_empty_snapshot() -> None:
    rows, _parser, meta = _run_municipal(_FakeSite(), today="2100-01-01")
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["source_total"] == 23
    assert meta["expired_count"] == 23
    assert not meta["configured_collection_error"]


def test_municipal_page_cap_fails_closed_before_partial_save() -> None:
    rows, _parser, meta = _run_municipal(_FakeSite(), max_pages=9)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "max_pages cap" in meta["configured_collection_error"]


def test_municipal_sentinel_and_boundary_mutations_fail_closed() -> None:
    for mutation, marker in (
        ("municipal_sentinel", "post-last clamp changed"),
        ("municipal_boundary", "boundary changed"),
    ):
        rows, _parser, meta = _run_municipal(_FakeSite(mutation))
        assert rows == []
        assert meta["snapshot_complete"] is False
        assert marker in meta["configured_collection_error"]


def test_municipal_duplicate_classes_fail_closed() -> None:
    for mutation, marker in (
        ("municipal_duplicate_id", "duplicate source identities"),
        ("municipal_semantic_duplicate", "semantic duplicates across catalogues"),
    ):
        rows, _parser, meta = _run_municipal(_FakeSite(mutation))
        assert rows == []
        assert marker in meta["configured_collection_error"]


def test_municipal_application_controls_are_fail_closed() -> None:
    for mutation, marker in (
        ("municipal_bad_active_route", "safe course-bound insert route"),
        ("municipal_bad_inactive_href", "inactive application control changed"),
    ):
        rows, _parser, meta = _run_municipal(_FakeSite(mutation))
        assert rows == []
        assert marker in meta["configured_collection_error"]


def test_municipal_dedupe_loss_and_pii_injection_fail_closed() -> None:
    rows, _parser, meta = _run_municipal(
        _FakeSite(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]

    def leak(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = [dict(value) for value in values]
        result[0]["phone"] = "010-1234-5678"
        return result

    rows, _parser, meta = _run_municipal(_FakeSite(), dedupe_rows=leak)
    assert rows == []
    assert meta["privacy_violations"] > 0
    assert "PII allowlist violations" in meta["configured_collection_error"]


def test_welfare_all_terms_complete_snapshot_excludes_instructor_and_applicants() -> None:
    site = _FakeSite()
    rows, parser, meta = _run_welfare(site)

    assert parser == hamyang.HAMYANG_WELFARE_PARSER
    assert len(rows) == 4
    assert meta["snapshot_complete"] is True
    assert meta["term_count"] == 2
    assert meta["term_totals"] == {"19": 10, "20": 1}
    assert meta["source_total"] == 11
    assert meta["current_count"] == 4
    assert meta["expired_count"] == 7
    assert meta["required_list_requests"] == meta["list_requests"] == 9
    assert meta["sentinel_counts"] == {"19": 1, "20": 1}
    assert meta["duplicate_source_id_count"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["branch_counts"] == {"함양군 종합사회복지관": 4}
    assert meta["status_counts"] == {"OPEN": 2, "CLOSED": 2}
    serialized = repr(rows)
    assert "김강사" not in serialized
    assert "홍길동" not in serialized
    assert "teacher@example.org" not in serialized
    assert "applicant@example.org" not in serialized
    assert "010-" not in serialized
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["raw_fields"]["instructor_excluded"] is True for row in rows)
    assert all("amode=" not in url for url in site.calls)
    assert all(urlparse(url).path not in meta["blocked_pii_paths"] for url in site.calls)


def test_welfare_legacy_term_may_predate_the_bounded_year_selector() -> None:
    controls = BeautifulSoup(
        """
        <form>
          <select name="syear">
            <option value="2026">2026</option><option value="2025">2025</option>
            <option value="2024">2024</option><option value="2023">2023</option>
            <option value="2022">2022</option><option value="2021">2021</option>
          </select>
          <select name="applyFlag">
            <option value="">전체</option><option value="Y">신청가능</option>
            <option value="F">신청불가</option>
          </select>
          <select name="stype">
            <option value="title" selected>교육명</option>
            <option value="teacher_idx">강사명</option>
          </select>
          <input name="sstring" value="">
        </form>
        """,
        "lxml",
    ).select_one("form")
    term = hamyang.WelfareTerm("2", "2019년 하반기", "2019")
    assert hamyang._welfare_search_controls(controls, expected_term=term) == []

    controls.select_one("select[name='syear'] option")["selected"] = ""
    assert "welfare legacy year selector contract changed" in hamyang._welfare_search_controls(
        controls, expected_term=term
    )


def test_welfare_no_current_is_valid_complete_empty_snapshot() -> None:
    rows, _parser, meta = _run_welfare(_FakeSite(), today="2100-01-01")
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["source_total"] == 11
    assert meta["expired_count"] == 11
    assert not meta["configured_collection_error"]


def test_welfare_page_cap_fails_closed_before_partial_save() -> None:
    rows, _parser, meta = _run_welfare(_FakeSite(), max_pages=8)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "max_pages cap" in meta["configured_collection_error"]


def test_welfare_inventory_sentinel_and_boundary_mutations_fail_closed() -> None:
    for mutation, marker in (
        ("welfare_discovery_inventory", "malformed welfare term option"),
        ("welfare_inventory", "term inventory/selection changed"),
        ("welfare_sentinel", "post-last clamp changed"),
        ("welfare_boundary", "boundary changed"),
    ):
        rows, _parser, meta = _run_welfare(_FakeSite(mutation))
        assert rows == []
        assert meta["snapshot_complete"] is False
        assert marker in meta["configured_collection_error"]


def test_welfare_duplicate_and_application_control_mutations_fail_closed() -> None:
    for mutation, marker in (
        ("welfare_duplicate_id", "duplicate source identities"),
        ("welfare_bad_active_route", "safe course-bound insert route"),
        ("welfare_bad_inactive_href", "inactive application control changed"),
    ):
        rows, _parser, meta = _run_welfare(_FakeSite(mutation))
        assert rows == []
        assert marker in meta["configured_collection_error"]


def test_welfare_dedupe_loss_and_pii_injection_fail_closed() -> None:
    rows, _parser, meta = _run_welfare(
        _FakeSite(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]

    def leak(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = [dict(value) for value in values]
        result[0]["instructor"] = "김강사"
        result[0]["email"] = "teacher@example.org"
        return result

    rows, _parser, meta = _run_welfare(_FakeSite(), dedupe_rows=leak)
    assert rows == []
    assert meta["privacy_violations"] >= 2
    assert "PII allowlist violations" in meta["configured_collection_error"]


def test_invalid_target_and_unmanaged_raw_session_fail_closed() -> None:
    rows, _parser, meta = hamyang.collect_gyeongnam_hamyang_education_courses(
        {"provider": hamyang.HAMYANG_PROVIDER, "url": "https://example.org/"}
    )
    assert rows == []
    assert meta["scope"] == "invalid"
    assert "does not match" in meta["configured_collection_error"]

    rows, _parser, meta = hamyang.collect_gyeongnam_hamyang_education_courses(
        MUNICIPAL_TARGET
    )
    assert rows == []
    assert "managed session_factory injection is required" in meta[
        "configured_collection_error"
    ]
