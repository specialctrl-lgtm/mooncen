from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import os
from threading import Lock
from typing import Any
from urllib.parse import quote

import pytest

from Crawler import municipal_iksan as ik


@dataclass(frozen=True)
class Target:
    provider: str = ik.IKSAN_PROVIDER
    url: str = ik.IKSAN_CANONICAL_URL


class DummySession:
    def close(self) -> None:
        return None


class Response:
    def __init__(
        self,
        url: str,
        body: bytes | str,
        status_code: int = 200,
    ) -> None:
        self.url = url
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status_code = status_code


def _identity(index: int) -> str:
    return f"{index:032x}"


def _fixture_items() -> list[dict[str, Any]]:
    progress = {
        "MOHYEON": ("DEADLINE", "UPCOMING", "ONLINE"),
        "WOMEN_CENTER": ("DEADLINE", "ONGOING", "ONLINE"),
        "INFO_EDU": ("PROCEEDING", "UPCOMING", "OFFLINE"),
        "CITIZEN_RECODER": ("DEADLINE", "FINISHED", "ONLINE"),
        "ART_CENTER": ("PROCEEDING", "UPCOMING", "ONLINE"),
        "ONEDAY02": ("DEADLINE", "UPCOMING", "ONLINE"),
        "LIFE_EDU": ("DEADLINE", "ONGOING", "ONLINE"),
        "wg02": ("ADVANCE", "UPCOMING", "ONLINE"),
        "global02": ("PROCEEDING", "UPCOMING", "ONLINE"),
        "gm01": ("SCHEDULED", "UPCOMING", "ONLINE"),
    }
    result: list[dict[str, Any]] = []
    for index, facility in enumerate(ik.IKSAN_FACILITIES, 1):
        item_progress, usage_progress, booking_type = progress[facility.code]
        external_url = (
            "http://museum.example.test/apply?course=8" if facility.code == "wg02" else None
        )
        result.append(
            {
                "itemUid": _identity(index),
                "itemTitle": f"{facility.category} 공식 강좌 {index}",
                "facilityInfo": {
                    "fcltCode": facility.code,
                    "fcltName": facility.api_name,
                    "fcltUid": facility.fclt_uid,
                    "instUid": facility.inst_uid,
                    "rsvtType": "EDUCATION",
                },
                "fcltUid": facility.fclt_uid,
                "instUid": facility.inst_uid,
                "beginDate": "2099-08-01",
                "endDate": "2099-08-31",
                "applyBeginDate": "2099-07-01 09:00",
                "applyEndDate": "2099-07-31 18:00",
                "itemProgress": item_progress,
                "usageProgress": usage_progress,
                "bookingType": booking_type,
                "maxCapacity": 20,
                "applyCount": index,
                "waitCapacity": 5,
                "waitCount": 1,
                "baseFee": 0,
                "itemInfo3": f"{facility.branch} 교육실",
                "itemInfo4": "익산시민",
                "timeInfo": "화 10:00 ~ 12:00",
                "externalUrl": external_url,
                # These upstream fields prove that the collector's output is
                # an allowlist rather than a copy of the API object.
                "itemInfo1": "개인 강사명",
                "itemInfo2": "063-000-0000",
                "paymentInfo": "테스트은행 000-00-000000",
                "explanation": "자유 서술 원문",
            }
        )
    return result


def _api_page(
    items: list[dict[str, Any]],
    *,
    page: int,
    total: int,
    page_size: int,
) -> bytes:
    total_pages = (total + page_size - 1) // page_size if total else 0
    payload = {
        "result": {
            "content": items,
            "number": page - 1,
            "size": page_size,
            "numberOfElements": len(items),
            "totalPages": total_pages,
            "totalElements": total,
            "first": page == 1,
            "last": page >= max(1, total_pages),
            "empty": not items,
        }
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _fields_html(*, venue: str, target: str) -> str:
    fields = (
        ("접수기간", "2099-07-01 ~ 2099-07-31"),
        ("교육기간", "2099-08-01 ~ 2099-08-31"),
        ("교육시간", "화 10:00 ~ 12:00"),
        ("교육장소", venue),
        ("교육대상", target),
        ("강사명", "개인 강사명"),
        ("전화번호", "063-000-0000"),
    )
    return "".join(
        f"<li><dl><dt>{key}</dt><dd>{escape(value)}</dd></dl></li>"
        for key, value in fields
    )


def _common_detail(
    item: dict[str, Any],
    facility: ik.IksanFacility,
    *,
    wrong_title: bool = False,
    wrong_application: bool = False,
    pii_target: bool = False,
) -> str:
    identity = item["itemUid"]
    title = "다른 강좌" if wrong_title else item["itemTitle"]
    target = "person@example.kr" if pii_target else item["itemInfo4"]
    progress, booking = item["itemProgress"], item["bookingType"]
    if progress == "PROCEEDING" and booking == "ONLINE":
        if facility.site == "global":
            control_identity = _identity(99) if wrong_application else identity
            control = (
                "<a class='button' id='btn_pass' href='javascript:void(0);' "
                "onclick=\"alert('로그인 후 이용할 수 있습니다.'); "
                f"fn_reserv_auth('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','{control_identity}'); "
                "return false;\">신청하기</a>"
            )
        else:
            control = (
                "<a class='button' id='btn_pass' href='javascript:void(0);'>"
                "신청하기</a>"
            )
    elif progress == "PROCEEDING" and booking == "OFFLINE":
        control = "<a class='button' href='javascript:void(0);'>오프라인</a>"
    elif progress == "ADVANCE" and booking == "ONLINE":
        control = (
            f"<a class='button white' href='{escape(item['externalUrl'])}'>바로가기</a>"
        )
    elif progress == "SCHEDULED":
        control = "<a class='button black' href='javascript:void(0);'>진행예정</a>"
    else:
        control = "<a class='button black' href='javascript:void(0);'>접수마감</a>"
    detail_url = ik.iksan_detail_url(facility.code, identity)
    return_url = ik.iksan_detail_url(
        facility.code, _identity(99) if wrong_application else identity
    )
    login = (
        "<li data-util='login'><a href='/login/index.do?returnUrl="
        f"{quote(return_url, safe='')}'>로그인</a></li>"
    )
    return f"""
      <html><body>{login}<article data-subarea='system_view'>
        <div class='view_top'><div class='txt_area'>
          <h3 class='tit_area'>{escape(title)}</h3>
          <ul class='info_list'>{_fields_html(venue=item['itemInfo3'], target=target)}</ul>
          <div class='btn_area'>{control}</div>
        </div></div>
        <p data-detail-url='{escape(detail_url)}'>상세설명과 첨부파일은 수집하지 않음</p>
      </article></body></html>
    """


def _lifelong_detail(item: dict[str, Any]) -> str:
    return f"""
      <html><body><div id='boardWrap'>
        <ul class='app_class_list col01'><li>
          <span class='state state2'>교육중</span>
          <strong class='tit'>{escape(item['itemTitle'])}</strong>
          <dl class='period dt_dl'><dt>신청기간</dt><dd>2099.07.01 ~ 2099.07.31</dd></dl>
          <dl class='medium dt_dl'><dt>교육기간</dt><dd>2099.08.01 ~ 2099.08.31</dd></dl>
          <dl class='dt_dl'><dt>교육시간</dt><dd>화 10:00 ~ 12:00</dd></dl>
        </li></ul>
        <div class='view_table'><ul class='view_basics_list'>
          <li><strong class='tit'>교육장소</strong><p>{escape(item['itemInfo3'])}</p></li>
          <li><strong class='tit'>교육대상</strong><p>{escape(item['itemInfo4'])}</p></li>
          <li><strong class='tit'>강사명</strong><p>개인 강사명</p></li>
          <li><strong class='tit'>문의처</strong><p>063-000-0000</p></li>
        </ul></div><div class='btnArea'><a class='btn white'>목록</a></div>
      </div></body></html>
    """


class Source:
    def __init__(self, mode: str = "complete") -> None:
        self.mode = mode
        self.items = _fixture_items()
        self.calls: list[str] = []
        self.page_one_calls = 0
        self.lock = Lock()

    def __call__(self, _session: Any, url: str, _timeout: int) -> Response:
        with self.lock:
            self.calls.append(url)
        if url == ik.iksan_api_url(1):
            if self.mode == "redirect":
                return Response(url, "{not valid json", status_code=302)
            with self.lock:
                self.page_one_calls += 1
                call_number = self.page_one_calls
            items = [dict(item) for item in self.items]
            if self.mode == "unknown_facility":
                items[0] = dict(items[0])
                items[0]["facilityInfo"] = dict(items[0]["facilityInfo"])
                items[0]["facilityInfo"]["fcltCode"] = "NEW_EDU"
            if self.mode == "boundary_drift" and call_number > 1:
                items[0] = dict(items[0])
                items[0]["itemTitle"] = "경계에서 바뀐 강좌"
            return Response(
                url,
                _api_page(
                    items,
                    page=1,
                    total=len(items),
                    page_size=ik.IKSAN_PAGE_SIZE,
                ),
            )
        if url == ik.iksan_api_url(2):
            items = self.items[:1] if self.mode == "nonempty_sentinel" else []
            return Response(
                url,
                _api_page(
                    items,
                    page=2,
                    total=len(self.items),
                    page_size=ik.IKSAN_PAGE_SIZE,
                ),
            )
        for item in self.items:
            facility = ik.IKSAN_FACILITY_BY_CODE[item["facilityInfo"]["fcltCode"]]
            detail_url = ik.iksan_detail_url(facility.code, item["itemUid"])
            if url != detail_url:
                continue
            if facility.site == "lll":
                return Response(url, _lifelong_detail(item))
            return Response(
                url,
                _common_detail(
                    item,
                    facility,
                    wrong_title=self.mode == "detail_title" and facility.code == "ART_CENTER",
                    wrong_application=(
                        self.mode == "wrong_application"
                        and facility.code in {"ART_CENTER", "global02"}
                    ),
                    pii_target=self.mode == "pii_target" and facility.code == "MOHYEON",
                ),
            )
        raise AssertionError(f"unexpected request (applicant endpoints must not be fetched): {url}")


@pytest.fixture(autouse=True)
def compact_fixture_page(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if request.node.name == "test_live_iksan_exact_2026_07_22_snapshot":
        return
    monkeypatch.setattr(ik, "IKSAN_PAGE_SIZE", len(ik.IKSAN_FACILITIES))


def _collect(source: Source, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    options: dict[str, Any] = {
        "today": "2099-07-22",
        "timeout": 10,
        "max_pages": 10,
        "detail_limit": 100,
        "max_workers": 10,
        "session_factory": DummySession,
        "fetcher": source,
    }
    options.update(kwargs)
    rows, parser, meta = ik.collect(
        Target(),
        **options,
    )
    assert parser == ik.IKSAN_PARSER
    return rows, meta


def test_canonical_identity_routes_aliases_and_exclusion() -> None:
    assert ik.IKSAN_PROVIDER == "MUNI_WWW_IKSAN_GO_KR_05CBD6EA"
    assert ik.IKSAN_CANDIDATE_ID == "MUNI_IR_AB9D4FA82479"
    assert ik.is_target(Target())
    assert not ik.is_target(Target(url="https://www.iksan.go.kr/reserve/"))
    assert not ik.is_target(Target(url=ik.IKSAN_LLL_ALIAS_URL))
    assert not ik.is_target(Target(provider=ik.IKSAN_LLL_PROVIDER_ALIAS, url=ik.IKSAN_LLL_ALIAS_URL))
    assert set(ik.IKSAN_FACILITY_BY_CODE) == {
        "MOHYEON",
        "WOMEN_CENTER",
        "INFO_EDU",
        "CITIZEN_RECODER",
        "ART_CENTER",
        "ONEDAY02",
        "LIFE_EDU",
        "wg02",
        "global02",
        "gm01",
    }
    assert ik.IKSAN_DISCOVERY_AUDIT["lifelong_alias"]["decision"].endswith(
        "not_separate_owner"
    )
    assert "without_identity_bound_application" in ik.IKSAN_DISCOVERY_AUDIT[
        "lifelong_external_directory"
    ]["decision"]


def test_complete_snapshot_keeps_all_education_categories_and_blocks_pii() -> None:
    source = Source()
    rows, meta = _collect(source)
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert len(rows) == len(ik.IKSAN_FACILITIES) == meta["returned_count"]
    assert meta["source_total"] == len(rows)
    assert meta["page_sizes"] == [len(rows)]
    assert meta["empty_sentinel_page"] == 2
    assert meta["detail_pages"] == len(rows)
    assert {row["raw_fields"]["source_facility_code"] for row in rows} == set(
        ik.IKSAN_FACILITY_BY_CODE
    )
    assert any(row["category"] == "예술의전당" for row in rows)
    assert any(row["category"] == "박물관 교육·행사" for row in rows)
    assert {row["program_type"] for row in rows} == {"교육"}
    assert {row["municipality_full_name"] for row in rows} == {
        ik.IKSAN_MUNICIPALITY_NAME
    }
    global_row = next(
        row for row in rows if row["raw_fields"]["source_facility_code"] == "global02"
    )
    assert global_row["reservation_available"] is True
    assert global_row["application_url"] == global_row["raw_url"]
    museum = next(
        row for row in rows if row["raw_fields"]["source_facility_code"] == "wg02"
    )
    assert museum["application_type"] == "EXTERNAL_HTTP_INFO_ONLY"
    assert museum["application_url"] == ""
    assert museum["raw_fields"]["insecure_external_control_blocked"] is True
    payload = repr(rows)
    assert "063-000-0000" not in payload
    assert "개인 강사명" not in payload
    assert "테스트은행" not in payload
    assert "자유 서술 원문" not in payload
    assert meta["forbidden_application_endpoint_requests"] == 0
    assert not any("fileDownload" in url for url in source.calls)


@pytest.mark.parametrize(
    "mode",
    [
        "nonempty_sentinel",
        "boundary_drift",
        "unknown_facility",
        "detail_title",
        "wrong_application",
        "pii_target",
    ],
)
def test_contract_and_privacy_drift_are_atomically_empty(mode: str) -> None:
    rows, meta = _collect(Source(mode))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_redirect_is_rejected_before_body_parsing() -> None:
    source = Source("redirect")
    rows, meta = _collect(source)
    assert rows == []
    assert "HTTP 302" in meta["configured_collection_error"]
    assert "invalid API JSON" not in meta["configured_collection_error"]
    assert source.calls == [ik.iksan_api_url(1)]


def test_default_fetcher_explicitly_disables_redirects() -> None:
    class Session:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def get(self, _url: str, **kwargs: Any) -> Response:
            self.kwargs = kwargs
            return Response(ik.IKSAN_API_URL, "{}")

    session = Session()
    ik._default_fetcher(session, ik.IKSAN_API_URL, 7)
    assert session.kwargs == {"timeout": 7, "allow_redirects": False}


def test_caps_and_dedupe_cardinality_fail_closed() -> None:
    rows, meta = _collect(Source(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    rows, meta = _collect(Source(), detail_limit=9)
    assert rows == []
    assert meta["source_cap_reached"] is True
    rows, meta = _collect(Source(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_noncanonical_target_never_fetches() -> None:
    source = Source()
    rows, _parser, meta = ik.collect(
        Target(provider=ik.IKSAN_LLL_PROVIDER_ALIAS, url=ik.IKSAN_LLL_ALIAS_URL),
        session_factory=DummySession,
        fetcher=source,
    )
    assert rows == []
    assert meta["configured_collection_error"]
    assert source.calls == []


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_TESTS") != "1",
    reason="set RUN_LIVE_MUNICIPAL_TESTS=1 for the official live audit",
)
def test_live_iksan_exact_2026_07_22_snapshot() -> None:
    rows, _parser, meta = ik.collect(
        Target(),
        today="2026-07-22",
        timeout=30,
        max_pages=20,
        detail_limit=500,
        max_workers=12,
    )
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["source_total"] == 294
    assert meta["data_pages"] == 3
    assert meta["page_sizes"] == [100, 100, 94]
    assert meta["empty_sentinel_page"] == 4
    assert meta["current_source_count"] == len(rows) == 124
    assert meta["detail_pages"] == 124
    assert meta["source_facility_counts"] == {
        "WOMEN_CENTER": 95,
        "LIFE_EDU": 90,
        "wg02": 57,
        "MOHYEON": 24,
        "global02": 12,
        "ART_CENTER": 5,
        "ONEDAY02": 4,
        "gm01": 3,
        "INFO_EDU": 2,
        "CITIZEN_RECODER": 2,
    }
    assert meta["current_facility_counts"] == {
        "WOMEN_CENTER": 48,
        "LIFE_EDU": 30,
        "MOHYEON": 24,
        "global02": 12,
        "wg02": 4,
        "INFO_EDU": 2,
        "gm01": 2,
        "ART_CENTER": 1,
        "ONEDAY02": 1,
    }
    assert "paymentInfo" not in repr(rows)
    assert "itemInfo1" not in repr(rows)
    assert "itemInfo2" not in repr(rows)
    assert meta["forbidden_application_endpoint_requests"] == 0
