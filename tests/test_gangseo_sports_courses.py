from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_gangseo_sports as gangseo


NOTICE_ID = "d4d01137dc075f40bd1cad3962d9d19b"


@dataclass
class Target:
    provider: str = gangseo.GANGSEO_SPORTS_PROVIDER
    url: str = gangseo.GANGSEO_SPORTS_URL
    branch: str = "서울특별시 강서구"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _list_item(
    spec: gangseo.ScheduleSpec,
    *,
    total: int = gangseo.GANGSEO_EXPECTED_DECLARED_COUNT,
    status: str = "E",
) -> dict[str, Any]:
    return {
        "comcd": gangseo.GANGSEO_COMPANY_CODE,
        "comnm": gangseo.GANGSEO_COMPANY_NAME,
        "class_cd": spec.class_cd,
        "class_nm": spec.title,
        "train_stime": spec.start_time,
        "train_etime": spec.end_time,
        "course_fee": "0",
        "train_sdate": None,
        "train_edate": None,
        "receive_etime": "18:00",
        "receive_kind": "20",
        "status": status,
        "target_age_name": spec.target,
        "sports_cd": "03" if spec.period_kind == "summer_notice" else "01",
        "train_day_nm": spec.train_day,
        "capa": "20",
        "reg_person": "3",
        "teacher_no": None,
        "teacher_name": "미지정",
        "total_count": total,
        "category1": spec.category1,
        "category2": spec.category2,
    }


def _schedule_html(path: str, *, remove_text: str = "") -> str:
    headings = {
        "/fmcs/102": "생활체육교실 이용안내",
        "/fmcs/103": "어린이축구 · 청소년풋살 · 여성축구 · 배구교실 이용안내",
        "/fmcs/104": "2026년 여름방학 청소년 체육교실",
    }
    parts = [headings[path]]
    for spec in gangseo.GANGSEO_SCHEDULE_SPECS.values():
        if spec.source_path == path:
            parts.extend(spec.source_signatures)
    text = " | ".join(parts)
    if remove_text:
        text = text.replace(remove_text, "")
    return f"<main><h3>{headings[path]}</h3><table><tr><td>{text}</td></tr></table></main>"


def _notice_list_html() -> str:
    return f"""
      <table><tr><td class="title">
        <a href="?action=read&amp;action-value={NOTICE_ID}">
          2026년 여름방학 청소년 체육교실 모집 안내
        </a>
      </td></tr></table>
    """


def _notice_detail_html(*, remove_text: str = "") -> str:
    blocks = ["<h3>2026년 여름방학 청소년 체육교실 모집 안내</h3>"]
    for spec in gangseo.GANGSEO_SCHEDULE_SPECS.values():
        if spec.period_kind != "summer_notice":
            continue
        evidence = " | ".join(spec.notice_signatures)
        blocks.append(
            f"<tr><td>{evidence}</td><td>7. 27.(월) ~ 8. 14.(금)</td></tr>"
        )
    text = "<table>" + "".join(blocks[1:]) + "</table>"
    html = blocks[0] + text
    if remove_text:
        html = html.replace(remove_text, "")
    return f"<article class='proc_read'>{html}</article>"


def _detail_html(
    item: dict[str, Any],
    *,
    hidden_class_cd: str | None = None,
) -> str:
    class_cd = hidden_class_cd or item["class_cd"]
    receipt_method = "" if item["class_cd"] == "00017" else "대기접수(추첨)"
    return f"""
      <form class="proc_read">
        <input type="hidden" name="comcd" value="{item['comcd']}">
        <input type="hidden" name="classcd" value="{class_cd}">
        <input type="hidden" name="type" value="{item['status']}">
        <input type="hidden" name="status" value="{item['status']}">
        <input type="hidden" name="SecurityToken" value="test-token">
        <table><tbody>
          <tr><th>강좌명</th><td>{item['class_nm']}</td></tr>
          <tr><th>운영센터</th><td>{item['comnm']} / 02-2600-6412</td></tr>
          <tr><th>시간/요일</th><td>{item['train_stime']} ~ {item['train_etime']} / {item['train_day_nm']}</td></tr>
          <tr><th>교육대상</th><td>{item['target_age_name']}</td></tr>
          <tr><th>강사명</th><td>{item['teacher_name']}</td></tr>
          <tr><th>접수방식</th><td>{receipt_method}</td></tr>
          <tr><th>신청인원/정원</th><td>마감</td></tr>
        </tbody></table>
      </form>
    """


class FixtureFetcher:
    def __init__(
        self,
        *,
        declared_total: int = gangseo.GANGSEO_EXPECTED_DECLARED_COUNT,
        duplicate: bool = False,
        schedule_remove: str = "",
        notice_remove: str = "",
        bad_detail_class: str = "",
        open_class: str = "",
    ) -> None:
        self.declared_total = declared_total
        self.schedule_remove = schedule_remove
        self.notice_remove = notice_remove
        self.bad_detail_class = bad_detail_class
        self.calls: list[str] = []
        self.items = [
            _list_item(
                spec,
                total=declared_total,
                status="R" if spec.class_cd == open_class else "E",
            )
            for spec in gangseo.GANGSEO_SCHEDULE_SPECS.values()
        ]
        if duplicate:
            self.items[-1] = dict(self.items[0])

    def __call__(self, _session: object, url: str, timeout: int) -> Any:
        assert timeout == 7
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == "/rest/common/company":
            assert query == {"type": ["L"]}
            return [
                {
                    "comcd": gangseo.GANGSEO_COMPANY_CODE,
                    "comnm": gangseo.GANGSEO_COMPANY_NAME,
                }
            ]
        if parsed.path == "/rest/lecture/list":
            # The source's default is 20; the collector must explicitly ask for
            # 100 and must not apply an R/E status filter.
            assert query == {
                "company_code": [gangseo.GANGSEO_COMPANY_CODE],
                "page": ["1"],
                "page_size": ["100"],
            }
            return self.items
        if parsed.path in gangseo.GANGSEO_SCHEDULE_URLS:
            return _soup(
                _schedule_html(
                    parsed.path,
                    remove_text=self.schedule_remove if parsed.path == "/fmcs/102" else "",
                )
            )
        if parsed.path == "/fmcs/30" and not parsed.query:
            return _soup(_notice_list_html())
        if parsed.path == "/fmcs/30":
            assert query == {"action": ["read"], "action-value": [NOTICE_ID]}
            return _soup(_notice_detail_html(remove_text=self.notice_remove))
        if parsed.path == gangseo.GANGSEO_SPORTS_PATH:
            assert query["action"] == ["read"]
            assert query["comcd"] == [gangseo.GANGSEO_COMPANY_CODE]
            class_cd = query["classcd"][0]
            item = next(item for item in self.items if item["class_cd"] == class_cd)
            assert query["type"] == [item["status"]]
            return _soup(
                _detail_html(
                    item,
                    hidden_class_cd="99999" if class_cd == self.bad_detail_class else None,
                )
            )
        raise AssertionError(f"unexpected fixture URL: {url}")


def _collect(
    fetcher: FixtureFetcher,
    *,
    target: Target | None = None,
    max_pages: int = 2,
    detail_limit: int = 100,
    dedupe_rows: Any = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], DummySession]:
    session = DummySession()
    rows, parser, meta = gangseo.collect_gangseo_sports_courses(
        target or Target(),
        timeout=7,
        max_pages=max_pages,
        detail_limit=detail_limit,
        fetcher=fetcher,
        session_factory=lambda: session,
        dedupe_rows=dedupe_rows,
        today="2026-07-19",
    )
    return rows, parser, meta, session


def test_full_snapshot_uses_page_size_100_enriches_all_details_and_filters_only_expired_park_golf() -> None:
    fetcher = FixtureFetcher(open_class="00058")
    dedupe_calls: list[list[str]] = []

    def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        dedupe_calls.append([row["provider_course_id"] for row in rows])
        return rows

    rows, parser, meta, session = _collect(fetcher, dedupe_rows=dedupe)

    assert parser == gangseo.GANGSEO_PARSER
    assert len(rows) == 25
    assert len({row["provider_course_id"] for row in rows}) == 25
    assert len(dedupe_calls) == 1
    assert session.closed is True
    assert meta["total_count"] == 26
    assert meta["raw_row_count"] == 26
    assert meta["unique_id_count"] == 26
    assert meta["expired_count"] == 1
    assert meta["current_count"] == 25
    assert meta["detail_attempts"] == 26
    assert meta["detail_pages"] == 26
    assert meta["detail_exempt_count"] == 0
    assert meta["official_list_only_exempt_count"] == 0
    assert meta["details_complete"] is True
    assert meta["enrichment_pages"] == 5
    assert meta["request_count"] == 33
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert meta["no_current_data"] is False
    assert "configured_collection_error" not in meta

    by_code = {row["raw_fields"]["official_class_code"]: row for row in rows}
    assert "00061" not in by_code
    assert by_code["00058"]["period"] == (
        "2026-04-01 ~ 2026-06-30 / 2026-09-01 ~ 2026-11-30"
    )
    assert by_code["00045"]["period"] == "2026-07-27 ~ 2026-08-14"
    assert by_code["00045"]["branch"] == "로뎀태권도"
    assert by_code["00045"]["venue_address"].endswith("내발산동 663-10, 2층")
    assert by_code["00058"]["reservation_available"] is True
    assert by_code["00058"]["application_url"] == by_code["00058"]["raw_url"]

    for class_cd, row in by_code.items():
        assert row["provider_course_id"] == (
            f"{gangseo.GANGSEO_SPORTS_PROVIDER}:GANGSEO04:{class_cd}"
        )
        assert row["raw_fields"]["stable_key"] == f"GANGSEO04|{class_cd}"
        assert row["raw_fields"]["detail_identity_verified"] is True
        assert row["raw_fields"]["detail_status_verified"] is True
        assert row["branch"] == row["venue_name"]
        assert row["preserve_branch"] is True
        assert row["branch_code"].startswith("GANGSEO04_")
        assert row["category"].count(">") == 1
        assert row["domain_category"] == "교육·강좌"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["municipality_code"] == "1150000000"
        assert row["municipality_full_name"] == "서울특별시 강서구"
        parsed = urlparse(row["raw_url"])
        assert parsed.scheme == "https"
        assert parsed.netloc == gangseo.GANGSEO_SPORTS_HOST
        assert parsed.path == gangseo.GANGSEO_SPORTS_PATH
        assert parse_qs(parsed.query)["classcd"] == [class_cd]
        if class_cd != "00058":
            assert row["reservation_available"] is False
            assert "application_url" not in row


def test_declared_count_drift_fails_closed_before_enrichment_or_details() -> None:
    rows, _parser, meta, session = _collect(FixtureFetcher(declared_total=27))

    assert rows == []
    assert session.closed is True
    assert meta["snapshot_complete"] is False
    assert meta["declared_count"] == 27
    assert meta["detail_attempts"] == 0
    assert meta["request_count"] == 2
    assert "drifted from 26 to 27" in meta["configured_collection_error"]


def test_duplicate_comcd_class_identity_fails_closed() -> None:
    rows, _parser, meta, _session = _collect(FixtureFetcher(duplicate=True))

    assert rows == []
    assert meta["unique_id_count"] == 25
    assert meta["snapshot_complete"] is False
    assert "duplicate" in meta["configured_collection_error"]


def test_request_caps_fail_closed_without_partial_rows() -> None:
    fetcher = FixtureFetcher()
    rows, _parser, meta, _session = _collect(fetcher, max_pages=0)
    assert rows == []
    assert fetcher.calls == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    fetcher = FixtureFetcher()
    rows, _parser, meta, _session = _collect(fetcher, detail_limit=25)
    assert rows == []
    assert fetcher.calls == []
    assert meta["source_cap_reached"] is True
    assert meta["details_complete"] is False
    assert "detail_limit cap 25" in meta["configured_collection_error"]


def test_schedule_or_notice_date_drift_fails_closed() -> None:
    rows, _parser, meta, _session = _collect(
        FixtureFetcher(schedule_remove="마실파크골프")
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["enrichment_errors"] >= 1
    assert "00061" in meta["configured_collection_error"]

    rows, _parser, meta, _session = _collect(
        FixtureFetcher(notice_remove="7. 27.(월) ~ 8. 14.(금)")
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "explicit operating date range" in meta["configured_collection_error"]


def test_one_detail_identity_error_discards_entire_snapshot() -> None:
    rows, _parser, meta, _session = _collect(
        FixtureFetcher(bad_detail_class="00044")
    )

    assert rows == []
    assert meta["detail_attempts"] == 26
    assert meta["detail_verified_count"] == 25
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "00044" in meta["configured_collection_error"]
    assert "hidden:classcd" in meta["configured_collection_error"]


def test_exact_target_validator_rejects_unsafe_or_ambiguous_routes() -> None:
    assert gangseo.is_gangseo_sports_target(Target()) is True
    invalid = (
        Target(provider="WRONG"),
        Target(url="http://sports.gangseo.seoul.kr/fmcs/27"),
        Target(url="https://sports.gangseo.seoul.kr/fmcs/27?center=GANGSEO04"),
        Target(url="https://sports.gangseo.seoul.kr/fmcs/27#lecture_R"),
        Target(url="https://sports.gangseo.seoul.kr/fmcs/43"),
        Target(url="https://user@sports.gangseo.seoul.kr/fmcs/27"),
        Target(url="https://evil.example/fmcs/27"),
    )
    assert all(not gangseo.is_gangseo_sports_target(target) for target in invalid)

    fetcher = FixtureFetcher()
    rows, parser, meta, _session = _collect(
        fetcher,
        target=Target(url="https://evil.example/fmcs/27"),
    )
    assert rows == []
    assert parser == gangseo.GANGSEO_PARSER
    assert fetcher.calls == []
    assert meta["snapshot_complete"] is False
    assert "exact Gangseo sports provider route" in meta["configured_collection_error"]


def test_detail_url_builder_accepts_only_stable_official_identity() -> None:
    url = gangseo.gangseo_detail_url("GANGSEO04", "00058", "E")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == gangseo.GANGSEO_SPORTS_HOST
    assert parse_qs(parsed.query) == {
        "action": ["read"],
        "comcd": ["GANGSEO04"],
        "classcd": ["00058"],
        "type": ["E"],
    }
    assert gangseo.gangseo_detail_url("OTHER", "00058", "E") == ""
    assert gangseo.gangseo_detail_url("GANGSEO04", "../58", "E") == ""
    assert gangseo.gangseo_detail_url("GANGSEO04", "00058", "javascript:1") == ""
