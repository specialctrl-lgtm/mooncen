from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
import math
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_daejeon_daedeok as dd


@dataclass
class Target:
    provider: str = dd.DAEJEON_DAEDEOK_PROVIDER
    url: str = dd.DAEJEON_DAEDEOK_CANONICAL_URL


class _Session:
    def close(self) -> None:
        pass


def _iso(value: str) -> date:
    return date.fromisoformat(value)


def _short(value: str) -> str:
    parsed = _iso(value)
    return f"{parsed.year % 100:02d}.{parsed.month:02d}.{parsed.day:02d}"


def _course(
    source_key: str,
    number: int,
    *,
    current: bool,
    missing_apply_start: bool = False,
    venue: str | None = None,
) -> dict[str, Any]:
    source = dd.DAEJEON_DAEDEOK_SOURCE_BY_KEY[source_key]
    if source.kind == "resident":
        identity = str(9000 + number)
        institution = "오정동"
        local = "1000012"
    else:
        identity = f"LEC_{number:012d}"
        institution = "인구정책과"
        local = "9999999"
    start = "2026-08-01" if current else "2025-02-01"
    end = "2026-09-30" if current else "2025-03-31"
    apply_start = None if missing_apply_start else (
        "2026-07-01" if current else "2025-01-01"
    )
    apply_end = "2026-07-31" if current else "2025-01-31"
    return {
        "source_key": source_key,
        "identity": identity,
        "order": f"ORD_{number:012d}",
        "sido": "35",
        "local": local,
        "title": f"{source.label} 강좌 {number}",
        "institution": institution,
        "detail_institution": institution,
        "venue": venue if venue is not None else f"{source.label} 강의실 {number}",
        "start": start,
        "end": end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule": "화요일 10:00~12:00",
        "target": "성인",
        "capacity": 20,
        "fee": "10,000원",
        "status": "접수중" if current else "접수마감",
    }


class FixtureSite:
    def __init__(self) -> None:
        self.education: dict[str, list[dict[str, Any]]] = {
            "resident": [
                _course("resident", 1, current=True, venue=""),
                *[
                    _course(
                        "resident",
                        number,
                        current=False,
                        missing_apply_start=number == 2,
                    )
                    for number in range(2, 12)
                ],
            ],
            "lifelong_01": [_course("lifelong_01", 101, current=True)],
            "lifelong_05": [_course("lifelong_05", 201, current=False)],
            "lifelong_07": [_course("lifelong_07", 301, current=True)],
            "lifelong_08": [_course("lifelong_08", 401, current=False)],
        }
        self.delivery = [
            {
                "serial": str(number),
                "identity": str(500 + number),
                "category": "문화예술",
                "title": f"배달강좌 템플릿 {number}",
                "status": "신청 마감",
            }
            for number in range(1, 12)
        ]
        self.get_calls: list[tuple[str, int]] = []
        self.post_calls: list[tuple[str, dict[str, str]]] = []
        self.sentinel_source = ""
        self.drift_source = ""
        self.detail_fault = ""
        self.detail_fault_identity = "9001"
        self.fail_post_identity = ""
        self._page_one_calls: Counter[str] = Counter()

    @staticmethod
    def session_factory() -> _Session:
        return _Session()

    @staticmethod
    def _source_from_url(url: str) -> dd.DaedeokSource:
        parsed = urlparse(url)
        for source in dd.DAEJEON_DAEDEOK_SOURCES:
            if parsed.hostname == source.host and parsed.path == source.path:
                return source
        raise AssertionError(f"unexpected fixture URL {url}")

    def fetcher(self, _session: Any, url: str, _timeout: int) -> str:
        source = self._source_from_url(url)
        query = parse_qs(urlparse(url).query)
        page = int(query.get("pageIndex", ["1"])[0])
        self.get_calls.append((source.key, page))
        if page == 1:
            self._page_one_calls[source.key] += 1
        drift = (
            source.key == self.drift_source
            and page == 1
            and self._page_one_calls[source.key] >= 2
        )
        if source.kind == "delivery":
            rows = [dict(row) for row in self.delivery]
            if drift:
                rows[0]["title"] += " 변경"
            return self._delivery_html(source, rows, page)
        rows = [dict(row) for row in self.education[source.key]]
        if drift:
            rows[0]["title"] += " 변경"
        return self._education_html(source, rows, page)

    def poster(
        self,
        _session: Any,
        url: str,
        data: dict[str, str],
        _timeout: int,
    ) -> str:
        source = self._source_from_url(url)
        payload = dict(data)
        self.post_calls.append((source.key, payload))
        identity = payload["lecId"]
        if identity == self.fail_post_identity:
            raise RuntimeError("fixture POST failure")
        row = next(
            item for item in self.education[source.key] if item["identity"] == identity
        )
        return self._detail_html(source, row, payload)

    def _page_rows(
        self, source: dd.DaedeokSource, rows: list[dict[str, Any]], page: int
    ) -> list[dict[str, Any]]:
        last = max(1, math.ceil(len(rows) / dd.DAEJEON_DAEDEOK_PAGE_SIZE))
        if source.key == self.sentinel_source and page == last + 1:
            return [dict(rows[0])]
        start = (page - 1) * dd.DAEJEON_DAEDEOK_PAGE_SIZE
        return rows[start : start + dd.DAEJEON_DAEDEOK_PAGE_SIZE]

    @staticmethod
    def _hidden(name: str, value: str) -> str:
        return f'<input type="hidden" name="{name}" value="{value}">'

    def _education_html(
        self, source: dd.DaedeokSource, rows: list[dict[str, Any]], page: int
    ) -> str:
        page_rows = self._page_rows(source, rows, page)
        total = len(rows)
        last = max(1, math.ceil(total / dd.DAEJEON_DAEDEOK_PAGE_SIZE))
        body = "".join(self._education_row(source, row) for row in page_rows)
        if not body:
            body = (
                f'<tr><td colspan="{len(source.headers)}">'
                "등록된 자료가 없습니다.</td></tr>"
            )
        hidden = "".join(
            self._hidden(name, value)
            for name, value in {
                "mnucd": source.menu_code,
                "searchLecDivArray": "",
                "bmode": "",
                "pageIndex": str(page),
                "lecId": "",
                "ordCd": "",
                "ordSidoCd": "",
                "ordLocalCd": "",
            }.items()
        )
        classes = "table" if source.kind == "resident" else "table simple"
        headers = "".join(f"<th>{header}</th>" for header in source.headers)
        return f"""
        <html><head><title>{source.list_title}</title></head><body>
          <form id="listForm" method="post" action="{source.path}">{hidden}</form>
          <span class="counter">Total {total} ｜ {page} / {last}</span>
          <table class="{classes}"><thead><tr>{headers}</tr></thead>
            <tbody>{body}</tbody></table>
        </body></html>
        """

    @staticmethod
    def _range(row: dict[str, Any], prefix: str) -> str:
        start = row[f"{prefix}_start"]
        end = row[f"{prefix}_end"]
        return f"{start or ''}~<em>{end}</em>"

    def _education_row(
        self, source: dd.DaedeokSource, row: dict[str, Any]
    ) -> str:
        application = self._range(row, "apply")
        period = f"{row['start']}~<em>{row['end']}</em>"
        identity = (
            f"'{row['identity']}','{row['order']}','{row['sido']}','{row['local']}'"
        )
        if source.kind == "resident":
            values = [
                "1",
                row["institution"],
                row["title"],
                application,
                period,
                row["schedule"],
                f"{row['capacity']}명",
                row["target"],
                row["fee"],
                row["status"],
            ]
            cells = "".join(f"<td>{value}</td>" for value in values)
            return (
                f'<tr onclick="fn_egov_select1({identity}); return false;">'
                f"{cells}</tr>"
            )
        onclick = (
            "fn_egov_select1(document.getElementById('listForm'),"
            f"{identity}); return false;"
        )
        title_value = (
            f'<a onclick="{onclick}">{row["title"]}</a>'
            f'<em><a onclick="{onclick}">[상세보기]</a></em>'
        )
        values = ["1", title_value, application, period, row["schedule"]]
        if source.key == "lifelong_05":
            values.extend(
                [
                    row["venue"],
                    f"0 / <em><strong>인원</strong>{row['capacity']}명</em>",
                    row["target"],
                    row["fee"],
                    row["status"],
                ]
            )
        else:
            values.extend(
                [
                    f"{row['capacity']}명",
                    row["target"],
                    row["fee"],
                    row["status"],
                ]
            )
        cells = "".join(
            f'<td><span class="add-head">{source.headers[index]}</span>'
            f'<span class="tds">{value}</span></td>'
            for index, value in enumerate(values)
        )
        return f"<tr>{cells}</tr>"

    def _delivery_html(
        self, source: dd.DaedeokSource, rows: list[dict[str, Any]], page: int
    ) -> str:
        page_rows = self._page_rows(source, rows, page)
        total = len(rows)
        last = max(1, math.ceil(total / dd.DAEJEON_DAEDEOK_PAGE_SIZE))
        body = ""
        for row in page_rows:
            onclick = f"return fn_egov_selectInfo('{row['identity']}','list')"
            body += f"""
              <tr><td>{row['serial']}</td>
                <td><a href="#view" onclick="{onclick}">{row['category']}</a></td>
                <td><a href="#view" onclick="{onclick}">{row['title']}</a></td>
                <td>{row['status']}</td></tr>
            """
        if not body:
            body = '<tr><td colspan="4">등록된 자료가 없습니다.</td></tr>'
        hidden = "".join(
            self._hidden(name, value)
            for name, value in {
                "mnucd": source.menu_code,
                "bmode": "listInfo",
                "seq": "",
                "pageIndex": "1",
            }.items()
        )
        headers = "".join(f"<th>{header}</th>" for header in source.headers)
        return f"""
        <html><head><title>{source.list_title}</title></head><body>
          <form id="listForm" method="post" action="{source.path}">{hidden}</form>
          <div class="count">총 강좌 : {total} {page} /{last}</div>
          <table class="table"><thead><tr>{headers}</tr></thead>
            <tbody>{body}</tbody></table>
        </body></html>
        """

    def _detail_html(
        self,
        source: dd.DaedeokSource,
        original: dict[str, Any],
        payload: dict[str, str],
    ) -> str:
        row = dict(original)
        hidden_identity = row["identity"]
        control = (
            "alert('폐강된 강좌입니다.'); return false;"
            if row["status"] == "폐강"
            else "fn_NonCheck(); return false;"
        )
        if row["identity"] == self.detail_fault_identity:
            if self.detail_fault == "identity":
                hidden_identity = "999999"
            elif self.detail_fault == "title":
                row["title"] += " 불일치"
            elif self.detail_fault == "period":
                row["end"] = "2026-10-01"
            elif self.detail_fault == "application_period":
                row["apply_end"] = "2026-07-30"
            elif self.detail_fault == "institution":
                row["detail_institution"] = "다른기관"
            elif self.detail_fault == "location":
                row["venue"] = ""
            elif self.detail_fault == "control":
                control = "changed(); return false;"
        hidden = "".join(
            self._hidden(name, value)
            for name, value in {
                "mnucd": source.menu_code,
                "bmode": "" if source.kind == "resident" else "detail1",
                "pageIndex": payload["pageIndex"],
                "lecId": hidden_identity,
                "ordCd": row["order"],
                "ordSidoCd": row["sido"],
                "ordLocalCd": row["local"],
            }.items()
        )
        fields = {
            "프로그램명": row["title"],
            "교육일정": row["schedule"],
            "교육대상": row["target"],
            "수강료": row["fee"],
            "모집인원": str(row["capacity"]),
            "교육장소": row["venue"],
            "교육기관": row["detail_institution"],
            "교육기간": f"{_short(row['start'])}~{_short(row['end'])}",
            "수강신청기간": (
                f"{_short(row['apply_start'])}~{_short(row['apply_end'])}"
            ),
            "모집방법": "선착순",
            # Deliberately present on the source; none may reach persistence.
            "강 사 명": "홍길동",
            "강의내용": "연락처 042-608-0000 arbitrary@example.com",
            "수강료납부안내": "계좌와 신청자 정보를 입력하세요",
        }
        lis = "".join(
            '<li><div class="titles"><strong>'
            f"{name}</strong></div><div class=\"txts\">{value}</div></li>"
            for name, value in fields.items()
        )
        return f"""
        <html><head><title>{source.detail_title}</title></head><body>
          <form id="detailForm" method="post" action="{source.path}">{hidden}</form>
          <div class="board_view"><ul class="detail">{lis}</ul></div>
          <div class="al_right">
            <span class="btn_type_green"><a href="#" onclick="{control}">수강신청하기</a></span>
            <span class="btn_type_gray"><a href="#" onclick="fn_egov_selectList(document.getElementById('detailForm')); return false;">프로그램 목록보기</a></span>
          </div>
        </body></html>
        """


def _collect(site: FixtureSite, **overrides: Any):
    values = {
        "today": "2026-07-21",
        "session_factory": site.session_factory,
        "fetcher": site.fetcher,
        "poster": site.poster,
        "max_workers": 4,
    }
    values.update(overrides)
    return dd.collect_daejeon_daedeok_education(Target(), **values)


def test_canonical_target_and_owned_aliases_are_exact() -> None:
    assert dd.is_daejeon_daedeok_education_target(Target())
    assert not dd.is_daejeon_daedeok_education_target(
        Target(url=dd.DAEJEON_DAEDEOK_CANONICAL_URL + "#fragment")
    )
    assert not dd.is_daejeon_daedeok_education_target(
        Target(provider="MUNI_WRONG")
    )
    alias = Target(
        provider="MUNI_WWW_DAEDEOK_GO_KR_F1987640",
        url=dd.DAEJEON_DAEDEOK_EDUCATION_SOURCES[3].list_url,
    )
    assert dd.is_daejeon_daedeok_owned_alias_target(alias)
    assert "bmode=listInfo" in dd.DAEJEON_DAEDEOK_DELIVERY_SOURCE.list_url


def test_complete_atomic_snapshot_posts_every_current_detail_and_excludes_pii() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == dd.DAEJEON_DAEDEOK_PARSER
    assert len(rows) == 3
    assert meta["source_totals"] == {
        "resident": 11,
        "lifelong_01": 1,
        "lifelong_05": 1,
        "lifelong_07": 1,
        "lifelong_08": 1,
        "delivery_info": 11,
    }
    assert meta["current_counts"] == {
        "resident": 1,
        "lifelong_01": 1,
        "lifelong_07": 1,
    }
    assert meta["archived_missing_apply_start_count"] == 1
    assert meta["delivery_information_count"] == 11
    assert meta["delivery_closed_count"] == 11
    assert meta["delivery_waiting_count"] == 0
    assert meta["delivery_real_offering_count"] == 0
    assert meta["delivery_emitted_count"] == 0
    assert meta["branch_fallback_count"] == 1
    assert meta["required_list_requests"] == 20
    assert meta["list_requests"] == 20
    assert meta["sentinel_requests"] == 6
    assert meta["stability_rechecks"] == 6
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""

    assert site.get_calls.count(("resident", 1)) == 2
    assert site.get_calls.count(("resident", 2)) == 1
    assert site.get_calls.count(("resident", 3)) == 1
    assert site.get_calls.count(("delivery_info", 1)) == 2
    assert site.get_calls.count(("delivery_info", 2)) == 1
    assert site.get_calls.count(("delivery_info", 3)) == 1
    assert len(site.post_calls) == 3
    assert all(payload["bmode"] == "detail1" for _, payload in site.post_calls)
    assert {
        (payload["lecId"], payload["ordCd"], payload["ordSidoCd"], payload["ordLocalCd"])
        for _, payload in site.post_calls
    } == {
        ("9001", "ORD_000000000001", "35", "1000012"),
        ("LEC_000000000101", "ORD_000000000101", "35", "9999999"),
        ("LEC_000000000301", "ORD_000000000301", "35", "9999999"),
    }
    resident = next(row for row in rows if row["raw_fields"]["source_key"] == "resident")
    assert resident["branch"] == resident["provider_organizer"] == "오정동"
    assert resident["raw_fields"]["education_location_source"] == (
        "official_list_institution_fallback"
    )
    assert all(row["raw_fields"]["detail_verified"] for row in rows)
    assert all(row["raw_fields"]["application_control_present"] for row in rows)
    payload = repr(rows)
    for forbidden in (
        "홍길동",
        "042-608-0000",
        "arbitrary@example.com",
        "강의내용",
        "applicant_count",
        "source_html",
    ):
        assert forbidden not in payload


def test_caps_fail_closed_before_partial_snapshot() -> None:
    site = FixtureSite()
    rows, _, meta = _collect(site, max_pages=19)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["list_requests"] == 6
    assert meta["detail_attempts"] == 0
    assert "20 required list requests" in meta["configured_collection_error"]

    site = FixtureSite()
    rows, _, meta = _collect(site, detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert "3 required details" in meta["configured_collection_error"]


def test_nonempty_post_last_sentinel_fails_closed() -> None:
    site = FixtureSite()
    site.sentinel_source = "resident"
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["detail_attempts"] == 0
    assert "immediate post-last page is not empty" in meta["configured_collection_error"]


def test_page_one_recheck_drift_fails_closed() -> None:
    site = FixtureSite()
    site.drift_source = "lifelong_07"
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["detail_attempts"] == 0
    assert "page-one recheck changed" in meta["configured_collection_error"]


def test_current_missing_application_start_fails_but_expired_one_is_counted() -> None:
    site = FixtureSite()
    site.education["resident"][0]["apply_start"] = None
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["detail_attempts"] == 0
    assert "application period must contain two dates for current rows" in meta[
        "configured_collection_error"
    ]

    site = FixtureSite()
    rows, _, meta = _collect(site)
    assert len(rows) == 3
    assert meta["archived_missing_apply_start_count"] == 1


def test_closed_course_with_reversed_official_application_period_keeps_the_course() -> None:
    site = FixtureSite()
    course = site.education["resident"][0]
    course["status"] = "접수마감"
    course["apply_start"] = "2026-07-02"
    course["apply_end"] = "2026-07-01"
    rows, _, meta = _collect(site)

    assert meta["snapshot_complete"] is True
    assert meta["current_application_period_anomaly_count"] == 1
    normalized = next(
        row for row in rows if row["raw_fields"]["source_key"] == "resident"
    )
    assert normalized["apply_period"] == ""
    assert normalized["apply_start"] == ""
    assert normalized["apply_end"] == ""
    assert "2026-07-02" in normalized["raw_fields"]["source_application_period"]
    assert "2026-07-01" in normalized["raw_fields"]["source_application_period"]


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("identity", "detail identity field lecId changed"),
        ("title", "detail/list title mismatch"),
        ("period", "detail/list education period mismatch"),
        ("application_period", "detail/list application period mismatch"),
        ("institution", "detail/list institution mismatch"),
        ("control", "course-bound application controls changed"),
    ],
)
def test_every_current_detail_contract_fails_the_whole_snapshot(
    fault: str, message: str
) -> None:
    site = FixtureSite()
    site.detail_fault = fault
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["detail_pages"] == 2
    assert meta["detail_errors"] >= 1
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_lifelong_missing_official_location_fails_closed() -> None:
    site = FixtureSite()
    site.detail_fault_identity = "LEC_000000000101"
    site.detail_fault = "location"
    rows, _, meta = _collect(site)
    assert rows == []
    assert "official location missing" in meta["configured_collection_error"]


def test_delivery_roster_never_becomes_an_undated_emitted_course() -> None:
    site = FixtureSite()
    site.delivery[0]["status"] = "신청 가능"
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["delivery_real_offering_count"] == 1
    assert meta["delivery_emitted_count"] == 0
    assert meta["detail_attempts"] == 0
    assert "without a proven dated-offering contract" in meta[
        "configured_collection_error"
    ]


def test_delivery_waiting_template_status_is_audited_but_not_emitted() -> None:
    site = FixtureSite()
    site.delivery[0]["status"] = "신청 대기중"
    rows, _, meta = _collect(site)

    assert len(rows) == 3
    assert meta["snapshot_complete"] is True
    assert meta["delivery_waiting_count"] == 1
    assert meta["delivery_real_offering_count"] == 0
    assert meta["delivery_emitted_count"] == 0


def test_current_cancelled_course_requires_the_official_cancelled_control() -> None:
    site = FixtureSite()
    site.education["lifelong_07"][0]["status"] = "폐강"
    rows, _, meta = _collect(site)

    assert meta["snapshot_complete"] is True
    cancelled = next(
        row
        for row in rows
        if row["raw_fields"]["source_key"] == "lifelong_07"
    )
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["application_type"] == "INFO_ONLY"
    assert not cancelled["reservation_available"]
    assert not cancelled["application_url"]
    assert cancelled["raw_fields"]["application_control_contract"] == (
        "detail_form_identity_plus_official_cancelled_gate"
    )


def test_duplicate_identity_across_sources_fails_closed() -> None:
    site = FixtureSite()
    resident = site.education["resident"][0]
    duplicate = site.education["lifelong_01"][0]
    for name in ("identity", "order", "sido", "local"):
        duplicate[name] = resident[name]
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["identity_duplicate_count"] == 1
    assert meta["detail_attempts"] == 0
    assert "duplicate official identities across education sources" in meta[
        "configured_collection_error"
    ]


def test_one_detail_fetch_failure_and_dedupe_cardinality_change_return_no_partial() -> None:
    site = FixtureSite()
    site.fail_post_identity = "LEC_000000000301"
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["detail_pages"] == 2
    assert meta["detail_errors"] >= 1

    site = FixtureSite()
    rows, _, meta = _collect(site, dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed official identity cardinality" in meta[
        "configured_collection_error"
    ]


def test_noncanonical_target_performs_no_network_requests() -> None:
    site = FixtureSite()
    rows, parser, meta = dd.collect_daejeon_daedeok_education(
        Target(provider="MUNI_OTHER"),
        today="2026-07-21",
        session_factory=site.session_factory,
        fetcher=site.fetcher,
        poster=site.poster,
    )
    assert rows == []
    assert parser == dd.DAEJEON_DAEDEOK_PARSER
    assert site.get_calls == []
    assert site.post_calls == []
    assert "canonical Daedeok-gu owner" in meta["configured_collection_error"]
