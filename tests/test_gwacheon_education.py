from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
import json
import os
import threading
from typing import Any

import pytest

from Crawler import municipal_gwacheon as gwacheon


class Response:
    def __init__(self, url: str, *, html: str | None = None, payload: Any = None) -> None:
        self.url = url
        self.status_code = 200
        self.history: list[object] = []
        self._payload = payload
        if html is not None:
            self.content = html.encode("utf-8")
            self.headers = {"Content-Type": "text/html; charset=UTF-8"}
        else:
            self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.headers = {"Content-Type": "application/json; charset=UTF-8"}

    def json(self) -> Any:
        return self._payload


class Session:
    def close(self) -> None:
        pass


def _landing(*, bad_registry: bool = False) -> str:
    places = list(gwacheon.GWACHEON_PLACE_REGISTRY)
    if bad_registry:
        places[-1] = (places[-1][0], "변경된 장소")
    options = "".join(
        f'<option value="{escape(code)}">{escape(label)}</option>'
        for code, label in places
    )
    return f'''<form id="listForm" method="post" action="{gwacheon.GWACHEON_LIST_PATH}?mId={gwacheon.GWACHEON_MID}">
      <input id="page" name="page" value="1"><input id="idx" name="idx" value="">
      <select id="searchPlaceIdx" name="searchPlaceIdx">{options}</select>
      <input id="allAgeTypeBtn" checked><input name="qualifiedGenderType" value="A" checked>
      <input id="allLecTimeWeekTypeBtn" checked><input name="searchStageState" value="Y">
    </form><script src="/reservation/js/unit/gcedu/edu/app/list.js"></script>'''


def _row(number: int) -> dict[str, Any]:
    if number == 2:
        place, title = gwacheon.GWACHEON_TEST_BRANCH, "내부 시스템 확인"
    elif number == 3:
        place, title = gwacheon.GWACHEON_ADMIN_BRANCH, "2026년 행정모니터 모집_과천시민 인증"
    else:
        place, title = "평생학습센터", f"공개 강좌 {number}"
    return {
        "placeName": place,
        "isFree": True,
        "money": 0,
        "fee": "무료",
        "isOnline": None,
        "ageTypeList": ["성인"],
        "categoryName": "인문교양교육",
        "title": title,
        "nowAppCnt": 3,
        "appCnt": 10,
        "stageStateAlias": "접수 중",
        "lecOpenDate": "2026-08-01",
        "lecCloseDate": "2026-09-01",
        "timeList": [
            {
                "idx": number,
                "pIdx": 100 + number,
                "startTime": "10:00",
                "endTime": "12:00",
                "createDate": 1_700_000_000_000 + number,
            }
        ],
        "lecDays": "화",
        "stageIdx": number,
        "programIdx": 100 + number,
        "extraYn": "N",
    }


def _pagination(page: int) -> dict[str, int]:
    return {
        "currentPageNo": page,
        "recordCountPerPage": 10,
        "pageSize": 10,
        "totalRecordCount": 10,
        "totalPageCount": 1,
        "firstPageNoOnPageList": ((page - 1) // 10) * 10 + 1,
        "lastPageNoOnPageList": 1,
        "firstRecordIndex": (page - 1) * 10,
        "lastRecordIndex": page * 10,
        "firstPageNo": 1,
        "lastPageNo": 1,
    }


def _payload(page: int, *, bad_sentinel: bool = False) -> dict[str, Any]:
    if page == 1:
        rows = [_row(number) for number in range(1, 11)]
    elif page == 2:
        # The production archive contains one byte-for-byte duplicate identity.
        rows = [_row(11), _row(1)]
    elif bad_sentinel and page == 3:
        rows = [_row(12)]
    else:
        rows = []
    return {"totalCnt": 12, "list": rows, "pagination": _pagination(page)}


def _detail(
    row: dict[str, Any], *, bad_branch: bool = False, bad_application: bool = False
) -> str:
    place = "미등록 장소" if bad_branch and row["stageIdx"] == 1 else row["placeName"]
    place_code = gwacheon.GWACHEON_PLACE_CODE_BY_NAME.get(row["placeName"], "99")
    stage_value = row["stageIdx"] + 1 if bad_application and row["stageIdx"] == 1 else row["stageIdx"]
    return f'''<div class="bod_app_detail"><h4><div class="icons">
      <span data-type="접수 중">접수 중</span></div><strong><span class="point">[인문교양교육]</span> {escape(row["title"])}</strong></h4>
      <div class="view_detail">
        <dl><dt>접수기간</dt><dd>2026-07-01 09:00 ~ 2026-07-31 18:00</dd></dl>
        <dl><dt>학습장/강의실</dt><dd>{escape(place)} / 1강의실</dd></dl>
        <dl><dt>교육기간</dt><dd>2026-08-01 ~ 2026-09-01</dd></dl>
        <dl><dt>교육시간/요일</dt><dd>10:00 ~ 12:00 화</dd></dl>
        <dl><dt>수강료</dt><dd>무료</dd></dl><dl><dt>교육대상</dt><dd>성인</dd></dl>
        <dl><dt>모집인원/방법</dt><dd>모집인원 10 / 신청자 3 (선착순)</dd></dl>
        <dl><dt>강사소개</dt><dd>discard</dd></dl><dl><dt>강사명</dt><dd>discard</dd></dl>
      </div><div class="btn-wrap-box"><dl><dt>문의전화</dt><dd>discard</dd></dl></div>
      <div class="view_cont">discard</div><dl class="view_file"><dt>관련파일</dt><dd>
        <a onclick="fn_egov_downFile('{'a' * 64}','{'b' * 64}'); return false;">자료</a>
        <a onclick="fn_egov_preview('{'a' * 64}','{'b' * 64}'); return false;">미리보기</a>
      </dd></dl></div>
      <form id="apply" method="post" action="{gwacheon.GWACHEON_APPLICATION_PATH}?mId={gwacheon.GWACHEON_MID}">
        <input name="placeIdx" value="{place_code}"><input name="stageIdx" value="{stage_value}">
        <input name="programIdx" value="{row["programIdx"]}"><input name="extraYn" value="N">
      </form>'''


@dataclass
class Fixture:
    bad_registry: bool = False
    bad_sentinel: bool = False
    bad_branch: bool = False
    bad_application: bool = False
    calls: list[tuple[str, str, dict[str, str] | None]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def fetch(
        self,
        _session: object,
        method: str,
        url: str,
        *,
        timeout: int,
        data: dict[str, str] | None = None,
    ) -> Response:
        del timeout
        with self.lock:
            self.calls.append((method, url, dict(data) if data else None))
        if method == "GET" and url == gwacheon.GWACHEON_CANONICAL_URL:
            return Response(url, html=_landing(bad_registry=self.bad_registry))
        if method == "POST":
            assert url == gwacheon.GWACHEON_JSON_URL and data is not None
            page = int(data["page"])
            return Response(
                url, payload=_payload(page, bad_sentinel=self.bad_sentinel)
            )
        parsed = __import__("urllib.parse").parse.urlparse(url)
        query = dict(__import__("urllib.parse").parse.parse_qsl(parsed.query))
        number = int(query["stageIdx"])
        return Response(
            url,
            html=_detail(
                _row(number),
                bad_branch=self.bad_branch,
                bad_application=self.bad_application,
            ),
        )


def _target(
    provider: str = gwacheon.GWACHEON_PROVIDER,
    url: str = gwacheon.GWACHEON_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url}


def test_complete_json_ledger_excludes_test_and_administrative_rows() -> None:
    fixture = Fixture()
    rows, parser, meta = gwacheon.collect_gwacheon_education(
        _target(),
        today="2026-07-23",
        session_factory=Session,
        fetcher=fixture.fetch,
        max_workers=3,
    )
    assert parser == gwacheon.GWACHEON_PARSER and len(rows) == 9
    assert meta["source_total"] == meta["source_rows"] == 12
    assert meta["source_identity_count"] == 11
    assert meta["source_duplicate_count"] == 1
    assert meta["discovered_links"] == 11
    assert meta["pagination_detected"] is True
    assert meta["pagination_complete"] is True
    assert meta["current_source_count"] == meta["detail_verified"] == 11
    assert meta["excluded_test_count"] == meta["excluded_non_course_count"] == 1
    assert meta["returned_count"] == 9 and meta["snapshot_complete"] is True
    assert meta["no_current_data"] is False and meta["no_current_reason"] == ""
    assert meta["list_requests"] == 6 and meta["detail_pages"] == 11
    assert meta["application_control_count"] == 11
    assert meta["attachment_fields_discarded"] == 11
    assert meta["pii_values_persisted"] == 0
    assert {row["branch"] for row in rows} == {"평생학습센터"}
    assert all(row["application_url"] == "" for row in rows)
    assert not any(
        gwacheon.GWACHEON_APPLICATION_PATH in url
        or "download" in url.lower()
        or "login" in url.lower()
        for _method, url, _data in fixture.calls
    )


def test_owner_and_route_allowlist_are_exact() -> None:
    assert gwacheon.is_gwacheon_education_target(_target())
    assert not gwacheon.is_gwacheon_education_target(
        _target(provider=gwacheon.GWACHEON_NOTICE_PROVIDER)
    )
    assert not gwacheon.is_gwacheon_education_target(
        _target(url=gwacheon.GWACHEON_CANONICAL_URL.replace("https://", "http://"))
    )
    assert not gwacheon.is_gwacheon_education_target(
        _target(url=gwacheon.GWACHEON_CANONICAL_URL + "&searchStageState=Y")
    )
    assert gwacheon.gwacheon_source_identity(1, 101, "N") == (
        f"{gwacheon.GWACHEON_PROVIDER}:stage:1:program:101:extra:N"
    )


@pytest.mark.parametrize(
    ("fixture", "fragment"),
    [
        (Fixture(bad_registry=True), "official place registry changed"),
        (Fixture(bad_sentinel=True), "exposes 1 rows, expected 0"),
        (Fixture(bad_branch=True), "official place drift"),
        (Fixture(bad_application=True), "application identity drift"),
    ],
)
def test_contract_drift_fails_closed(fixture: Fixture, fragment: str) -> None:
    rows, _, meta = gwacheon.collect_gwacheon_education(
        _target(),
        today="2026-07-23",
        session_factory=Session,
        fetcher=fixture.fetch,
        max_workers=3,
    )
    assert rows == [] and fragment in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_caps_fail_before_expansion_or_detail_requests() -> None:
    fixture = Fixture()
    rows, _, meta = gwacheon.collect_gwacheon_education(
        _target(),
        today="2026-07-23",
        max_pages=5,
        session_factory=Session,
        fetcher=fixture.fetch,
    )
    assert rows == [] and meta["source_cap_reached"] is True
    assert meta["landing_pages"] == 1 and meta["list_requests"] == 1
    assert meta["detail_pages"] == 0 and len(fixture.calls) == 2


@pytest.mark.skipif(
    os.environ.get("RUN_GWACHEON_LIVE") != "1", reason="opt-in live audit"
)
def test_live_complete_snapshot() -> None:
    rows, _, meta = gwacheon.collect_gwacheon_education(
        _target(), today="2026-07-23"
    )
    assert meta["source_total"] == 5539
    assert meta["source_identity_count"] == 5538
    assert meta["source_duplicate_count"] == 1
    assert meta["source_identity_sha256"] == (
        "048f24d9cd00855d7721fb55af77dce4a9626047d9b3611386917d8b1c6f8510"
    )
    assert meta["current_source_count"] == 335 and len(rows) == 329
    assert meta["excluded_test_count"] == 5
    assert meta["excluded_non_course_count"] == 1
    assert meta["status_counts"] == {
        "CANCELLED": 7,
        "CLOSED": 39,
        "OPEN": 279,
        "SCHEDULED": 4,
    }
    assert meta["snapshot_complete"] is True
    assert meta["reservation_endpoint_requests"] == 0
    assert meta["attachment_endpoint_requests"] == 0
