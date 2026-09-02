from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_jeju_education_office as jje


@dataclass
class Target:
    provider: str
    url: str


class DummySession:
    def close(self) -> None:
        pass


def _target(kind: str) -> Target:
    url = jje.JJE_EDUCATION_URL if kind == "education" else jje.JJE_EXPERIENCE_URL
    return Target(jje.JJE_RESERVATION_PROVIDER, url)


def _source_rows(kind: str) -> dict[str, list[dict[str, str]]]:
    prefix = "ED" if kind == "education" else "EX"
    rows: dict[str, list[dict[str, str]]] = {"0": [], "1": []}
    for index in range(12):
        status_filter = "0" if index < 11 else "1"
        is_jeju = index % 2 == 0
        if kind == "education":
            branch = "제주학생문화원" if is_jeju else "공공도서관(송악도서관)"
            title = f"2026 제주 교육강좌 {index + 1}"
            if index == 0:
                title = "(테스트) 2026 제주 교육강좌"
            place = "제주시 교육실" if is_jeju else "서귀포시 송악도서관"
        else:
            branch = "제주유아교육진흥원 회천분원(제주시)" if is_jeju else "제주유아교육진흥원 본원(서귀포시)"
            title = f"2026 제주 가족체험 {index + 1}"
            place = "제주시 회천분원" if is_jeju else "서귀포시 본원"
        rows[status_filter].append(
            {
                "identity": f"{prefix}_{index + 1:013d}",
                "title": title,
                "branch": branch,
                "period": "2026-08-20 ~ 2026-08-21",
                "apply_period": "2026-08-01 09:00 ~ 2026-08-19 18:00",
                "target": "제주도민",
                "status": "예정" if status_filter == "0" else "접수중",
                "place": place,
            }
        )
    return rows


class FixtureSite:
    def __init__(self, **flags: bool):
        self.values = {kind: _source_rows(kind) for kind in ("education", "experience")}
        self.flags = flags
        self.calls: list[str] = []
        self.url_counts: dict[str, int] = {}

    def __call__(self, _session: DummySession, url: str, timeout: int) -> str:
        assert timeout > 0
        kind = jje._request_kind(url)
        self.calls.append(url)
        self.url_counts[url] = self.url_counts.get(url, 0) + 1
        ledger, request_type = kind.split("_", 1)
        if request_type == "list":
            return self._list_html(ledger, url)
        return self._detail_html(ledger, url)

    def _list_html(self, kind: str, url: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        status_filter = query["reserveStatus"][0]
        page = int(query["startPage"][0])
        spec = jje.LEDGERS[kind]
        values = [dict(value) for value in self.values[kind][status_filter]]
        if self.flags.get("missing_municipality") and kind == "experience":
            values[0]["branch"] = "제주교육기관"
            values[0]["place"] = "본원 체험실"
        start = (page - 1) * jje.JJE_PAGE_SIZE
        page_values = values[start : start + jje.JJE_PAGE_SIZE]
        last_page = max(1, (len(values) + jje.JJE_PAGE_SIZE - 1) // jje.JJE_PAGE_SIZE)
        if self.flags.get("sentinel_not_empty") and page == last_page + 1:
            page_values = [dict(values[-1])]
        if not page_values:
            return '<html><li class="list_none"><span>게시물이 없습니다.</span></li></html>'
        rows = []
        for offset, value in enumerate(page_values):
            sequence = len(values) - start - offset
            if page == last_page + 1:
                sequence = 1
            title = value["title"]
            if self.flags.get("boundary_drift") and page == 1 and self.url_counts[url] > 1:
                title += " 변경"
            href = jje.detail_url(kind, value["identity"])
            rows.append(
                f"""
                <tr>
                  <td data-cell-header="순번">{sequence}</td>
                  <td data-cell-header="기관명">{value["branch"]}</td>
                  <td data-cell-header="{spec.title_header}"><a href="{href}">{title}</a></td>
                  <td data-cell-header="운영기간">{value["period"]}</td>
                  <td data-cell-header="접수기간">{value["apply_period"]}</td>
                  <td data-cell-header="{spec.target_header}">{value["target"]}</td>
                  <td data-cell-header="예약상태">{value["status"]}</td>
                </tr>
                """
            )
        return (
            f"<html><table><caption>{spec.caption_token}</caption><tbody>" + "".join(rows) + "</tbody></table></html>"
        )

    def _detail_html(self, kind: str, url: str) -> str:
        spec = jje.LEDGERS[kind]
        query = parse_qs(urlparse(url).query)
        identity = query[spec.sid_key][0]
        value = next(
            row for partition in self.values[kind].values() for row in partition if row["identity"] == identity
        )
        value = dict(value)
        if self.flags.get("missing_municipality") and kind == "experience":
            value["branch"] = "제주교육기관"
            value["place"] = "본원 체험실"
        title = value["title"]
        if self.flags.get("detail_mismatch") and identity.endswith("0000000000002"):
            title += " 변경"
        period = value["period"] + " (목~금) 10:00~12:00"
        if self.flags.get("period_drift") and identity.endswith("0000000000002"):
            period = "2026-09-20 ~ 2026-09-21"
        page_title = "교육/강좌" if kind == "education" else "견학/체험"
        return f"""
        <html><head><title>{page_title} &gt; 상세보기 | 통합예약시스템</title></head>
        <body><div class="reser_view"><h3>{title}</h3><ul>
          <li><em>운영기관</em><span>{value["branch"]}</span></li>
          <li><em>{spec.period_label}</em><span>{period}</span></li>
          <li><em>신청기간</em><span>{value["apply_period"]}</span></li>
          <li><em>{spec.place_label}</em><span>{value["place"]}</span></li>
          <li><em>신청방법</em><span>일반사용자 본인인증</span></li>
          <li><em>문의</em><span>064-000-0000</span></li>
        </ul></div>
        <div class="private-body">비공개 신청자 010-9999-9999</div>
        <a href="/attachment/private.pdf">첨부</a></body></html>
        """


def _collect(kind: str, site: FixtureSite, **kwargs):
    return jje.collect_jeju_education_office_reservations(
        _target(kind), session_factory=DummySession, fetcher=site, **kwargs
    )


def test_exact_two_owners_and_read_only_route_boundary() -> None:
    assert jje.target_kind(_target("education")) == "education"
    assert jje.target_kind(_target("experience")) == "experience"
    assert jje.target_kind(Target("wrong", jje.JJE_EDUCATION_URL)) is None
    assert jje._request_kind(jje.list_url("education", "0", 1)) == "education_list"
    assert jje._request_kind(jje.detail_url("experience", "EX_0000000000001")) == ("experience_detail")
    for url in (
        "https://org.jje.go.kr/reserve/jjeFacility/list.jje?menuCd=x",
        "https://org.jje.go.kr/reserve/jjeEducation/apply.jje?educationSid=ED_0000000000001",
        "https://org.jje.go.kr/login/login.jje",
        "https://org.jje.go.kr/notice/list.jje",
    ):
        with pytest.raises(jje.JejuEducationOfficeContractError):
            jje._request_kind(url)


def test_both_sibling_ledgers_are_complete_locked_and_municipality_exact() -> None:
    site = FixtureSite()
    education, parser, education_meta = _collect("education", site)
    experience, _, experience_meta = _collect("experience", site)

    assert parser == jje.JJE_RESERVATION_PARSER
    assert len(education) == education_meta["returned_count"] == 11
    assert education_meta["source_total"] == 12
    assert education_meta["excluded_test_count"] == 1
    assert len(experience) == experience_meta["source_total"] == 12
    assert education_meta["data_pages"] == experience_meta["data_pages"] == 3
    assert education_meta["list_requests"] == experience_meta["list_requests"] == 8
    assert education_meta["detail_requests"] == 11
    assert experience_meta["detail_requests"] == 12
    for rows, meta, scope, service_group in (
        (education, education_meta, "education", "공공강좌"),
        (experience, experience_meta, "experience", "체험"),
    ):
        assert meta["ops_scope"] == scope
        assert meta["snapshot_complete"] is True
        assert meta["full_snapshot_validated"] is True
        assert meta["application_endpoint_requests"] == 0
        assert meta["pii_values_persisted"] == 0
        assert set(meta["municipality_counts"]) == {
            jje.JJE_JEJU_NAME,
            jje.JJE_SEOGWIPO_NAME,
        }
        assert all(row["service_group"] == service_group for row in rows)
        assert all(row["service_group_policy"] == "locked" for row in rows)
        assert all(row["municipality_region_verified"] is True for row in rows)
        assert {row["municipality_code"] for row in rows} == {
            jje.JJE_JEJU_CODE,
            jje.JJE_SEOGWIPO_CODE,
        }
    assert not any("테스트" in row["title"] for row in education)
    assert all("apply.jje" not in url and "/login/" not in url for url in site.calls)


@pytest.mark.parametrize(
    "site,error",
    [
        (FixtureSite(boundary_drift=True), "first category boundary changed"),
        (FixtureSite(sentinel_not_empty=True), "sentinel is not empty"),
        (FixtureSite(detail_mismatch=True), "title identity mismatch"),
        (FixtureSite(period_drift=True), "operation period mismatch"),
        (FixtureSite(missing_municipality=True), "municipality evidence missing"),
    ],
)
def test_contract_drift_fails_the_whole_snapshot(site: FixtureSite, error: str) -> None:
    kind = "experience" if site.flags.get("missing_municipality") else "education"
    rows, _, meta = _collect(kind, site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in meta["configured_collection_error"]


def test_caps_wrong_owner_and_dedupe_fail_before_partial_save() -> None:
    rows, _, meta = _collect("education", FixtureSite(), max_pages=1)
    assert rows == []
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _, meta = _collect("education", FixtureSite(), detail_limit=10)
    assert rows == []
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _, meta = _collect("education", FixtureSite(), dedupe_fn=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed official current identity cardinality" in meta["configured_collection_error"]

    rows, _, meta = jje.collect_jeju_education_office_reservations(
        Target("wrong", jje.JJE_EDUCATION_URL),
        fetcher=lambda *_: pytest.fail("wrong owner must not fetch"),
    )
    assert rows == []
    assert "registered Jeju education-office ledger" in meta["configured_collection_error"]


def test_free_body_contact_applicant_and_attachment_are_not_persisted() -> None:
    rows, _, meta = _collect("experience", FixtureSite())
    payload = repr(rows)
    assert "비공개 신청자" not in payload
    assert "010-9999-9999" not in payload
    assert "064-000-0000" not in payload
    assert "private.pdf" not in payload
    assert all(row["description"] == row["title"] for row in rows)
    assert meta["pii_values_persisted"] == 0


@pytest.mark.skipif(
    os.getenv("JJE_EDUCATION_OFFICE_LIVE") != "1",
    reason="set JJE_EDUCATION_OFFICE_LIVE=1 for the live audit",
)
def test_live_official_two_complete_snapshots() -> None:
    for kind in ("education", "experience"):
        rows, parser, meta = jje.collect_jeju_education_office_reservations(
            _target(kind), timeout=40, max_pages=20, detail_limit=200
        )
        assert parser == jje.JJE_RESERVATION_PARSER
        assert meta["configured_collection_error"] == ""
        assert meta["snapshot_complete"] is True
        assert rows
        assert all(row["municipality_region_verified"] is True for row in rows)
