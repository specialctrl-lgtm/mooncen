from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from Crawler import municipal_songpa as songpa


@dataclass
class FakeResponse:
    html: str = ""
    payload: Any = None
    status_code: int = 200

    @property
    def content(self) -> bytes:
        return self.html.encode("utf-8")

    @property
    def text(self) -> str:
        return self.html

    def json(self) -> Any:
        if self.payload is None:
            raise ValueError("no JSON payload")
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _item(
    identity: int,
    *,
    title: str,
    branch: str,
    group_idx: int,
    start: str = "2026-07-01",
    end: str = "2026-09-30",
    status: str = "신청마감",
    method: str = "방문",
    fee: int = 0,
    registration_start: str = "2026-06-20",
    registration_end: str = "2026-09-15",
    registration_start_time: str = "09:00",
    registration_end_time: str = "18:00",
) -> dict[str, Any]:
    return {
        "id": str(identity),
        "title": title,
        "branch": branch,
        "group_idx": group_idx,
        "registration_start": registration_start,
        "registration_end": registration_end,
        "registration_start_time": registration_start_time,
        "registration_end_time": registration_end_time,
        "start": start,
        "end": end,
        "status": status,
        "method": method,
        "fee": fee,
        "schedule": "매주 월, 수",
    }


def _list_page(items: list[dict[str, Any]], *, total: int, page_count: int) -> str:
    grid = []
    table = []
    for item in items:
        href = f"/learn/youth/program/lecture_view.do?lecture_idx={item['id']}"
        fee = f"{item['fee']:,}원"
        grid.append(
            f"""
            <li><a class="program_link" href="{href}">
              <span class="desc_box">
                <span>{item['method']}</span>
                <span class="lec_tit">{item['title']}</span>
                <span class="loca">{item['branch']} / </span>
                <i>접수기간:{item['registration_start']}~{item['registration_end']}<br>
                   교육기간:{item['start']}~{item['end']}</i>
              </span>
            </a></li>
            """
        )
        table.append(
            f"""
            <li><a class="program_link" href="{href}">
              <section class="table">
                <div class="col">{item['id']}</div>
                <div class="col"><p>{item['title']}</p></div>
                <div class="col">{item['branch']}</div>
                <div class="col">{item['registration_start']} {item['registration_start_time']} ~<br>{item['registration_end']} {item['registration_end_time']}</div>
                <div class="col">{item['schedule']}</div>
                <div class="col">{fee}</div>
                <div class="col"><span class="status">{item['status']}</span></div>
              </section>
            </a></li>
            """
        )
    return f"""
    <html><body>
      <div class="prog_list">
        <div class="prog_list_top"><p><span>''</span>에 대한 <span>{total}</span>개의 강의</p></div>
        <div class="grid_list"><ul>{''.join(grid)}</ul></div>
        <div class="list_type"><ul>{''.join(table)}</ul></div>
      </div>
      <div class="current_m"><span class="total">{page_count}</span></div>
    </body></html>
    """


def _detail(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "lecture_idx": int(item["id"]),
        "name": item["title"],
        "grp_name": item["branch"],
        "group_idx": item["group_idx"],
        "start_dt": item["start"],
        "end_dt": item["end"],
        "start_time": "10:00",
        "end_time": "12:00",
        "fee": item["fee"],
        "use_yn": "Y",
        "student_qty": 20,
        "tgt_detail": "송파구민",
        "teacher_nm": "공공강사",
        "cont": f"<p>{item['title']} 공식 소개</p>",
        "reg_start_st": f"{item['registration_start']} {item['registration_start_time']}:00",
        "reg_end_dt": f"{item['registration_end']} {item['registration_end_time']}:00",
        "p_idx": "1",
        "p_name": "자치회관",
        "part_code_idx": 17,
        "tgt_code": "30",
        "reg_method": "2",
        "status_code": 99,
        "study_place_idx": 101,
    }


class FakeSongpaSource:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.details = {item["id"]: _detail(item) for item in items}
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []
        self.detail_failures: dict[str, int] = {}
        self.closed = 0

    def factory(self) -> "FakeSession":
        return FakeSession(self)


class FakeSession:
    def __init__(self, source: FakeSongpaSource) -> None:
        self.source = source

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        assert url == songpa.SONGPA_EDUCATION_URL
        assert kwargs["allow_redirects"] is False
        data = dict(kwargs["data"])
        self.source.post_calls.append(data)
        page = int(data["page"])
        start = (page - 1) * songpa.SONGPA_PAGE_SIZE
        page_items = self.source.items[start : start + songpa.SONGPA_PAGE_SIZE]
        pages = max(1, math.ceil(len(self.source.items) / songpa.SONGPA_PAGE_SIZE))
        return FakeResponse(
            html=_list_page(page_items, total=len(self.source.items), page_count=pages)
        )

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        assert kwargs["allow_redirects"] is False
        assert kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"
        parsed = urlparse(url)
        assert parsed.path == songpa.SONGPA_DETAIL_API_PATH
        identity = parse_qs(parsed.query)["lecture_idx"][0]
        self.source.get_calls.append(identity)
        remaining_failures = self.source.detail_failures.get(identity, 0)
        if remaining_failures:
            self.source.detail_failures[identity] = remaining_failures - 1
            raise requests.ReadTimeout("temporary detail timeout")
        return FakeResponse(payload=self.source.details[identity])

    def close(self) -> None:
        self.source.closed += 1


def _target() -> dict[str, str]:
    return {
        "provider": songpa.SONGPA_EDUCATION_PROVIDER,
        "url": songpa.SONGPA_EDUCATION_URL,
    }


def _two_page_source() -> FakeSongpaSource:
    items = [
        _item(
            1000 + index,
            title=f"공공강좌 {index}",
            branch="가락1동 자치회관" if index < 6 else "송파구 평생학습원",
            group_idx=2 if index < 6 else 30,
            status=("신청가능" if index == 0 else "대기신청" if index == 1 else "신청마감"),
            method=("온라인" if index < 2 else "방문"),
            fee=(10_000 if index == 0 else 0),
        )
        for index in range(11)
    ]
    items.append(
        _item(
            1011,
            title="종료 강좌",
            branch="가락1동 자치회관",
            group_idx=2,
            start="2026-01-01",
            end="2026-06-30",
        )
    )
    items.append(
        _item(
            1012,
            title="기간 오류 강좌",
            branch="송파구 평생학습원",
            group_idx=30,
            start="2026-07-02",
            end="2026-05-18",
        )
    )
    return FakeSongpaSource(items)


def test_songpa_route_and_detail_helpers_are_provider_owned() -> None:
    assert songpa.is_songpa_education_target(_target())
    assert not songpa.is_songpa_education_target(
        {**_target(), "provider": "MUNI_OTHER"}
    )
    assert not songpa.is_songpa_education_target(
        {**_target(), "url": f"{songpa.SONGPA_EDUCATION_URL}?page=1"}
    )
    assert not songpa.is_songpa_education_target(
        {**_target(), "url": songpa.SONGPA_EDUCATION_URL.replace("https://", "http://")}
    )
    assert songpa.songpa_detail_url("16658").endswith("lecture_idx=16658")
    assert songpa.songpa_detail_api_url("16658").endswith("lecture_idx=16658")
    assert songpa.songpa_detail_url("1&admin=true") == ""
    assert songpa.songpa_detail_api_url("not-an-id") == ""


def test_songpa_collects_complete_current_future_snapshot_with_locked_metadata() -> None:
    source = _two_page_source()

    rows, parser, meta = songpa.collect_songpa_education_courses(
        _target(),
        max_pages=2,
        detail_limit=20,
        session_factory=source.factory,
        today=date(2026, 7, 19),
        max_workers=1,
    )

    assert parser == songpa.SONGPA_PARSER
    assert len(rows) == 11
    assert meta == {
        **meta,
        "pages": 2,
        "declared_pages": 2,
        "detail_pages": 11,
        "detail_attempts": 11,
        "detail_required_count": 11,
        "required_detail_count": 11,
        "pagination_complete": True,
        "details_complete": True,
        "snapshot_complete": True,
        "source_cap_reached": False,
        "source_total": 13,
        "discovered_links": 13,
        "expired_count": 1,
        "invalid_period_count": 1,
        "current_candidate_count": 11,
        "current_count": 11,
        "branch_count": 2,
        "full_snapshot_required": True,
    }
    assert meta["branch_counts"] == {
        "가락1동 자치회관": 6,
        "송파구 평생학습원": 5,
    }
    assert source.get_calls == [str(1000 + index) for index in range(11)]
    assert source.post_calls == [
        {
            "page": "1",
            "searchKind2": "1",
            "searchSDate": "2026-07-19",
            "searchEDate": "2099-12-31",
        },
        {
            "page": "2",
            "searchKind2": "1",
            "searchSDate": "2026-07-19",
            "searchEDate": "2099-12-31",
        },
    ]
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["collection_category"] == "공공예약" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["municipality_code"] == "1171000000" for row in rows)
    assert all(row["preserve_branch"] is True for row in rows)
    assert len({row["provider_course_id"] for row in rows}) == len(rows)
    assert rows[0]["provider_course_id"].endswith(":lecture:1000")
    assert rows[0]["branch_code"] == "SONGPA_LEARN_GROUP_2"
    assert rows[0]["fee"] == "10,000원"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is True
    assert rows[1]["status"] == "WAITLIST"
    assert rows[2]["status"] == "CLOSED"
    assert rows[2]["reservation_available"] is False
    assert rows[0]["target"] == "송파구민"
    assert rows[0]["instructor"] == "공공강사"
    assert rows[0]["raw_fields"]["detail_valid"] is True
    assert rows[0]["raw_fields"]["lecture_idx"] == "1000"
    assert re.fullmatch(r"2026-07-01 ~ 2026-09-30", rows[0]["period"])


def test_songpa_retries_a_transient_detail_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    source = FakeSongpaSource(
        [_item(1000, title="재시도 강좌", branch="송파구청", group_idx=2)]
    )
    source.detail_failures["1000"] = 1
    monkeypatch.setattr(songpa, "SONGPA_DETAIL_RETRY_BACKOFF_SECONDS", 0)

    rows, _parser, meta = songpa.collect_songpa_education_courses(
        _target(),
        max_pages=1,
        detail_limit=1,
        session_factory=source.factory,
        today=date(2026, 7, 19),
        max_workers=1,
    )

    assert len(rows) == 1
    assert meta["snapshot_complete"] is True
    assert source.get_calls == ["1000", "1000"]


def test_songpa_preserves_only_the_exact_official_reversed_registration_anomaly() -> None:
    anomaly = _item(
        16644,
        title="[학부모 특강] AI시대의 경제와 자녀 교육",
        branch="송파구 진학학습지원센터",
        group_idx=43,
        status="접수대기",
        method="온라인",
        registration_start="2026-08-06",
        registration_end="2026-07-08",
        registration_start_time="10:00",
        registration_end_time="00:00",
    )
    source = FakeSongpaSource([anomaly])

    rows, _parser, meta = songpa.collect_songpa_education_courses(
        _target(),
        max_pages=1,
        detail_limit=1,
        session_factory=source.factory,
        today=date(2026, 7, 19),
        max_workers=1,
    )

    assert len(rows) == 1
    assert "registration_start" not in rows[0]
    assert "registration_end" not in rows[0]
    assert rows[0]["raw_fields"]["official_reversed_registration_period"] is True
    assert meta["snapshot_complete"] is True

    unknown = {**anomaly, "id": "16643"}
    source = FakeSongpaSource([unknown])
    rows, _parser, meta = songpa.collect_songpa_education_courses(
        _target(),
        max_pages=1,
        detail_limit=1,
        session_factory=source.factory,
        today=date(2026, 7, 19),
        max_workers=1,
    )
    assert rows == []
    assert "unsupported reversed registration period" in meta[
        "configured_collection_error"
    ]


def test_songpa_uses_verified_location_and_explicit_blank_target_evidence() -> None:
    item = _item(
        17000,
        title="대상 미기재 강좌",
        branch="송파구 평생학습원",
        group_idx=30,
    )
    source = FakeSongpaSource([item])
    source.details["17000"]["tgt_detail"] = ""

    rows, _parser, meta = songpa.collect_songpa_education_courses(
        _target(),
        max_pages=1,
        detail_limit=1,
        session_factory=source.factory,
        today=date(2026, 7, 19),
        max_workers=1,
    )

    assert meta["snapshot_complete"] is True
    assert rows[0]["venue_name"] == "송파구 평생학습원"
    assert rows[0]["target"] == "대상 별도 안내"
    assert rows[0]["apply_period"] == "2026-06-20 ~ 2026-09-15"
    assert rows[0]["raw_fields"]["official_detail_target_blank"] is True


def test_songpa_music_studio_uses_official_facility_location() -> None:
    item = _item(
        16699,
        title="송파런 1인1악기 챌린지-바이올린 기초심화(청소년)",
        branch="뮤직스튜디오",
        group_idx=39,
    )
    source = FakeSongpaSource([item])
    source.details["16699"]["study_place_idx"] = 34
    source.details["16699"]["p_name"] = "송파런"

    rows, _parser, meta = songpa.collect_songpa_education_courses(
        _target(),
        max_pages=1,
        detail_limit=1,
        session_factory=source.factory,
        today=date(2026, 7, 19),
        max_workers=1,
    )

    assert meta["snapshot_complete"] is True
    assert rows[0]["venue_address"] == "서울특별시 송파구 올림픽로 326"
    assert rows[0]["branch_lat"] == 37.5144533
    assert rows[0]["branch_location_verified"] is True


def test_songpa_fails_closed_when_list_page_cap_is_too_low() -> None:
    source = _two_page_source()

    rows, _parser, meta = songpa.collect_songpa_education_courses(
        _target(),
        max_pages=1,
        detail_limit=20,
        session_factory=source.factory,
        today="2026-07-19",
        max_workers=1,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert meta["pages"] == 1
    assert meta["declared_pages"] == 2
    assert meta["detail_attempts"] == 0
    assert source.get_calls == []
    assert "max_pages cap" in meta["configured_collection_error"]


def test_songpa_fails_closed_when_detail_limit_is_partial() -> None:
    source = _two_page_source()

    rows, _parser, meta = songpa.collect_songpa_education_courses(
        _target(),
        max_pages=2,
        detail_limit=10,
        session_factory=source.factory,
        today="2026-07-19",
        max_workers=1,
    )

    assert rows == []
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert meta["detail_required_count"] == 11
    assert meta["detail_attempts"] == 10
    assert meta["detail_pages"] == 10
    assert "detail_limit cap" in meta["configured_collection_error"]


@pytest.mark.parametrize("field,bad_value,error_text", [
    ("name", "다른 제목", "detail title mismatch"),
    ("grp_name", "다른 기관", "detail branch mismatch"),
    ("end_dt", "2026-10-01", "detail course period mismatch"),
    ("lecture_idx", 999999, "detail identity mismatch"),
])
def test_songpa_fails_closed_on_detail_identity_mismatch(
    field: str,
    bad_value: Any,
    error_text: str,
) -> None:
    source = _two_page_source()
    source.details["1000"][field] = bad_value

    rows, _parser, meta = songpa.collect_songpa_education_courses(
        _target(),
        max_pages=2,
        detail_limit=20,
        session_factory=source.factory,
        today="2026-07-19",
        max_workers=1,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["details_complete"] is False
    assert meta["detail_errors"] == 1
    assert error_text in meta["configured_collection_error"]


def test_songpa_rejects_duplicate_ids_across_declared_pages_before_details() -> None:
    source = _two_page_source()
    source.items[-1] = {**source.items[-1], "id": source.items[0]["id"]}
    source.details = {item["id"]: _detail(item) for item in source.items}

    rows, _parser, meta = songpa.collect_songpa_education_courses(
        _target(),
        max_pages=2,
        detail_limit=20,
        session_factory=source.factory,
        today="2026-07-19",
        max_workers=1,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert meta["duplicate_count"] == 1
    assert source.get_calls == []
    assert "duplicate lecture identities" in meta["configured_collection_error"]


def test_songpa_rejects_noncanonical_target_without_network() -> None:
    source = _two_page_source()
    target = {**_target(), "url": f"{songpa.SONGPA_EDUCATION_URL}#fragment"}

    rows, parser, meta = songpa.collect_songpa_education_courses(
        target,
        session_factory=source.factory,
    )

    assert rows == []
    assert parser == songpa.SONGPA_PARSER
    assert meta["snapshot_complete"] is False
    assert source.post_calls == []
    assert source.get_calls == []
    assert "canonical Songpa" in meta["configured_collection_error"]
