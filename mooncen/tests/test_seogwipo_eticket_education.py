from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from Crawler import municipal_seogwipo_eticket as seogwipo


@dataclass(frozen=True)
class Target:
    provider: str
    url: str


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


def _target() -> Target:
    return Target(
        seogwipo.SEOGWIPO_ETICKET_PROVIDER,
        seogwipo.SEOGWIPO_ETICKET_TARGET_URL,
    )


def _records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    identities = [
        "GD2600331",
        "GD2600327",
        "GD2600316",
        "GD2600308",
        "GD2600297",
        "GD2600284",
        "GD2600275",
    ]
    days = [7, 6, 5, 4, 3, 31, 24]
    months = [8, 8, 8, 8, 8, 7, 7]
    for index, (identity, month, day) in enumerate(zip(identities, months, days)):
        education = identity == "GD2600327"
        title = (
            "어린이건강체험관 개인교육(8. 6.)"
            if education
            else f"어린이건강체험관 자유관람({month}. {day}.)"
        )
        event_date = f"2026-{month:02d}-{day:02d}"
        registration_start = "2026-07-10" if month == 8 else "2026-06-27"
        registration_end_day = day - 1
        registration_end = f"2026-{month:02d}-{registration_end_day:02d}"
        records.append(
            {
                "identity": identity,
                "title": title,
                "event_date": event_date,
                "registration_start": registration_start,
                "registration_end": registration_end,
                "capacity": 20 if education else 30,
                "start_time": "13:30",
                "end_time": "14:30" if education else "17:30",
                "venue": "어린이 건강체험관(서호남로 12, 3층)",
                "company": "서귀포시 건강생활지원센터",
                "education": education,
                "index": index,
            }
        )
    historical = [
        ("어린이건강체험관 자유관람(7. 3.)", "2026-07-03", "2026-07-02", 30),
        ("어린이건강체험관 자유관람(7. 10.)", "2026-07-10", "2026-07-09", 30),
        ("어린이건강체험관 개인교육(7. 9.)", "2026-07-09", "2026-07-08", 20),
        ("어린이건강체험관 자유관람(6. 12.)", "2026-06-12", "2026-06-11", 30),
        ("어린이건강체험관 자유관람(6. 5.)", "2026-06-05", "2026-06-04", 30),
        ("어린이건강체험관 개인교육(6. 11.)", "2026-06-11", "2026-06-09", 20),
        ("어린이건강체험관 자유관람(5. 29.)", "2026-05-29", "2026-05-28", 30),
        ("어린이건강체험관 자유관람(5. 22.)", "2026-05-22", "2026-05-21", 30),
        ("어린이건강체험관 자유관람(5. 15.)", "2026-05-15", "2026-05-14", 30),
        ("어린이건강체험관 개인교육(5. 6.)", "2026-05-06", "2026-05-04", 20),
        ("어린이건강체험관자유관람(5. 8.)", "2026-05-08", "2026-05-07", 30),
    ]
    for title, event_date, registration_end, capacity in historical:
        records.append(
            {
                "identity": "",
                "title": title,
                "event_date": event_date,
                "registration_start": "2026-04-21",
                "registration_end": registration_end,
                "capacity": capacity,
                "start_time": "13:30",
                "end_time": "14:30" if "교육" in title else "17:30",
                "venue": "어린이 건강체험관(서호남로 12, 3층)",
                "company": "서귀포시 건강생활지원센터",
                "education": "교육" in title,
                "index": len(records),
            }
        )
    return records


def _list_html(records: list[dict[str, Any]], *, malformed_link: bool = False) -> str:
    cards = []
    for record in records:
        if record["identity"]:
            link_identity = (
                f"{record['identity']}?admin=true"
                if malformed_link and record["education"]
                else record["identity"]
            )
            control = (
                '<a class="btn btn-primary btn-block" href="javascript:window.open('
                f"'https://ticket.seogwipo.go.kr/ticket/{link_identity}', "
                "'_blank', 'width=1440, height=1000')\">신청하기</a>"
            )
        else:
            control = '<a class="btn btn-secondary btn-block">신청마감</a>'
        cards.append(
            f"""
            <li><div><h5>{record['title']}</h5>
              <p class="count">정원 <span>{record['capacity']}</span></p>
              <ul class="p-detail">
                <li><p>행사기간</p><span>{record['event_date']}~{record['event_date']}</span></li>
                <li><p>행사시간</p><span>00:00~00:00</span></li>
                <li><p>접수기간</p><span>{record['registration_start']}~{record['registration_end']}</span></li>
                <li><p>행사장소</p><span>{record['venue']}</span></li>
              </ul>
              {control}
            </div></li>
            """
        )
    return f"<html><body><div class='parking-list'><ul>{''.join(cards)}</ul></div></body></html>"


def _ticket_page(identity: str) -> str:
    return f"<html><body><input id='gdSeq' name='gdSeq' value='{identity}'></body></html>"


def _api_datetime(day: str, time_value: str) -> str:
    return day.replace("-", "") + time_value.replace(":", "")


class FakeSource:
    def __init__(
        self,
        *,
        duplicate_list_identity: bool = False,
        header_extra_identity: bool = False,
        goods_title_mismatch: bool = False,
        ticket_redirect: bool = False,
    ) -> None:
        self.records = _records()
        if duplicate_list_identity:
            self.records[6] = {**self.records[6], "identity": self.records[0]["identity"]}
        self.header_extra_identity = header_extra_identity
        self.goods_title_mismatch = goods_title_mismatch
        self.ticket_redirect = ticket_redirect
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = 0

    def factory(self) -> "FakeSession":
        return FakeSession(self)

    def by_id(self, identity: str) -> dict[str, Any]:
        return next(record for record in self.records if record["identity"] == identity)

    def header_items(self) -> list[dict[str, str]]:
        items = [
            {"gdSeq": record["identity"], "gdName": record["title"]}
            for record in self.records
            if record["identity"]
        ]
        if self.header_extra_identity:
            items.append({"gdSeq": "GD2600999", "gdName": "별도 교육"})
        return items


class FakeSession:
    def __init__(self, source: FakeSource) -> None:
        self.source = source

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.source.calls.append(("GET", url, kwargs))
        assert kwargs["allow_redirects"] is False
        assert kwargs["verify"] is True
        if url == seogwipo.SEOGWIPO_ETICKET_LIST_URL:
            return FakeResponse(html=_list_html(self.source.records))
        identity = url.rsplit("/", 1)[-1]
        status = 302 if self.source.ticket_redirect and identity == "GD2600327" else 200
        return FakeResponse(html=_ticket_page(identity), status_code=status)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.source.calls.append(("POST", url, kwargs))
        assert kwargs["allow_redirects"] is False
        assert kwargs["verify"] is True
        identity = kwargs["files"]["gdSeq"][1]
        assert kwargs["headers"]["Referer"] == seogwipo.seogwipo_ticket_url(identity)
        record = self.source.by_id(identity)
        if url == seogwipo.SEOGWIPO_GOODS_INFO_URL:
            title = "다른 상품" if self.source.goods_title_mismatch and record["education"] else record["title"]
            payload = {
                "data": {
                    "gdSeq": identity,
                    "gdName": title,
                    "gdOpenDt": _api_datetime(record["event_date"], record["start_time"]),
                    "gdCloseDt": _api_datetime(record["event_date"], record["end_time"]),
                    "gdSaleStartdt": _api_datetime(record["registration_start"], "09:00"),
                    "gdSaleEnddt": _api_datetime(record["registration_end"], "00:00"),
                    "gdCategory": "7",
                    "gdKind": "1",
                    "gdFreeYn": "N",
                    "gdFrontViewYn": "Y",
                    "gdSalePauseYn": "N",
                    "gdRsNotice": "어린이 대상 공식 프로그램",
                    "companyVO": {"companyName": record["company"]},
                }
            }
        elif url == seogwipo.SEOGWIPO_GOODS_INFORMATION_URL:
            payload = {
                "data": {
                    "gdName": record["title"],
                    "gdDesc": "<p>공식 상세 정보</p>",
                    "companyName": record["company"],
                    "tel": "064-760-6483",
                }
            }
        elif url == seogwipo.SEOGWIPO_HEADER_INFO_URL:
            payload = {
                "data": {
                    "gdName": record["title"],
                    "companyName": record["company"],
                },
                "dataList": self.source.header_items(),
            }
        else:
            raise AssertionError(f"unexpected POST {url}")
        return FakeResponse(payload=payload)

    def close(self) -> None:
        self.source.closed += 1


def test_exact_target_and_safe_ticket_identity_contract() -> None:
    assert seogwipo.is_seogwipo_eticket_target(_target())
    assert not seogwipo.is_seogwipo_eticket_target(
        Target("OTHER", seogwipo.SEOGWIPO_ETICKET_TARGET_URL)
    )
    assert not seogwipo.is_seogwipo_eticket_target(
        Target(
            seogwipo.SEOGWIPO_ETICKET_PROVIDER,
            seogwipo.SEOGWIPO_ETICKET_TARGET_URL + "?admin=true",
        )
    )
    assert not seogwipo.is_seogwipo_eticket_target(
        Target(
            seogwipo.SEOGWIPO_ETICKET_PROVIDER,
            seogwipo.SEOGWIPO_ETICKET_TARGET_URL.replace("https://", "http://"),
        )
    )
    assert seogwipo.seogwipo_ticket_url("GD2600327").endswith("/ticket/GD2600327")
    assert seogwipo.seogwipo_ticket_url("GD2600327?admin=true") == ""
    assert seogwipo.seogwipo_ticket_url("../../admin") == ""
    assert seogwipo.is_explicit_education_title("어린이건강체험관 개인교육")
    assert seogwipo.is_explicit_education_title("여름방학 과학 교실")
    assert not seogwipo.is_explicit_education_title("어린이건강체험관 자유관람")
    assert not seogwipo.is_explicit_education_title("시민 축제")
    assert not seogwipo.is_explicit_education_title("2026년 교육 공지사항")
    assert not seogwipo.is_explicit_education_title("교육 게시판")


def test_live_shaped_page_returns_only_the_explicit_education_product() -> None:
    source = FakeSource()

    rows, parser, meta = seogwipo.collect_seogwipo_eticket_education(
        _target(),
        session_factory=source.factory,
        today=date(2026, 7, 19),
        max_pages=1,
        detail_limit=7,
    )

    assert parser == seogwipo.SEOGWIPO_ETICKET_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"].endswith(":GD2600327")
    assert row["title"] == "어린이건강체험관 개인교육(8. 6.)"
    assert row["branch"] == "서귀포시 건강생활지원센터"
    assert row["venue_name"] == "어린이 건강체험관"
    assert row["venue_address"] == "서호남로 12, 3층"
    assert row["schedule_raw"] == "13:30 ~ 14:30"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["application_url"] == (
        "https://ticket.seogwipo.go.kr/ticket/GD2600327"
    )
    assert row["municipality_code"] == "5013000000"
    assert row["municipality_full_name"] == "제주특별자치도 서귀포시"
    assert row["municipality_region_verified"] is True
    assert row["service_group"] == "공공강좌"
    assert row["service_group_policy"] == "locked"
    assert meta == {
        **meta,
        "pages": 1,
        "required_list_requests": 1,
        "declared_pages": 1,
        "declared_total_available": False,
        "completeness_basis": (
            "official_single_page+relevant_ticket_header_education_union+"
            "closed_list_fingerprint"
        ),
        "source_total": 18,
        "discovered_links": 7,
        "historical_unlinked_count": 11,
        "header_union_count": 7,
        "header_education_union_count": 1,
        "malformed_count": 0,
        "duplicate_count": 0,
        "explicit_education_count": 4,
        "excluded_non_education_count": 14,
        "expired_count": 11,
        "current_count": 1,
        "returned_count": 1,
        "current_education_source_count": 1,
        "linked_education_count": 1,
        "required_detail_count": 1,
        "detail_attempts": 1,
        "detail_pages": 1,
        "detail_api_calls": 3,
        "required_list_only_count": 0,
        "list_only_closed_education_count": 0,
        "ignored_current_non_education_count": 6,
        "pagination_detected": False,
        "pagination_complete": True,
        "pagination_exhausted": True,
        "details_complete": True,
        "snapshot_complete": True,
        "source_cap_reached": False,
        "reservation_discovery_links": 1,
        "no_current_data": False,
    }
    assert meta["branch_counts"] == {"서귀포시 건강생활지원센터": 1}
    assert len(source.calls) == 1 + (1 * 4)
    assert source.closed == 1


def test_closed_current_education_uses_stable_official_list_identity_get_only() -> None:
    def closed_source() -> FakeSource:
        current = FakeSource()
        # The live portal removes links from both a closed education card and
        # a closed free-viewing card while they remain current on the list.
        current.records[1] = {**current.records[1], "identity": ""}
        current.records[2] = {**current.records[2], "identity": ""}
        return current

    source = closed_source()
    rows, parser, meta = seogwipo.collect_seogwipo_eticket_education(
        _target(),
        session_factory=source.factory,
        today=date(2026, 8, 5),
        max_pages=1,
        detail_limit=100,
    )

    assert parser == seogwipo.SEOGWIPO_ETICKET_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "어린이건강체험관 개인교육(8. 6.)"
    assert row["provider_course_id"].startswith(
        f"{seogwipo.SEOGWIPO_ETICKET_PROVIDER}:LIST-"
    )
    assert row["raw_url"] == seogwipo.SEOGWIPO_ETICKET_LIST_URL
    assert "application_url" not in row
    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert row["branch"] == "어린이 건강체험관"
    assert row["municipality_full_name"] == "제주특별자치도 서귀포시"
    assert row["municipality_region_verified"] is True
    assert row["raw_fields"]["list_only_closed_education"] is True
    assert row["raw_fields"]["ticket_page_validated"] is False
    assert row["raw_fields"]["goods_detail_validated"] is False
    assert all("자유관람" not in item["title"] for item in rows)
    assert [method for method, _url, _kwargs in source.calls] == ["GET"]
    assert meta["current_education_source_count"] == 1
    assert meta["linked_education_count"] == 0
    assert meta["required_detail_count"] == 0
    assert meta["detail_api_calls"] == 0
    assert meta["required_list_only_count"] == 1
    assert meta["list_only_closed_education_count"] == 1
    assert meta["ignored_current_non_education_count"] == 2
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["returned_count"] == 1

    repeated = closed_source()
    repeated_rows, _parser, repeated_meta = (
        seogwipo.collect_seogwipo_eticket_education(
            _target(),
            session_factory=repeated.factory,
            today=date(2026, 8, 5),
            max_pages=1,
            detail_limit=100,
        )
    )
    assert repeated_meta["snapshot_complete"] is True
    assert repeated_rows[0]["provider_course_id"] == row["provider_course_id"]
    assert repeated_rows[0]["raw_url"] == row["raw_url"]
    assert [method for method, _url, _kwargs in repeated.calls] == ["GET"]


def test_closed_current_education_with_missing_official_field_fails_closed() -> None:
    source = FakeSource()
    source.records[1] = {
        **source.records[1],
        "identity": "",
        "venue": "",
    }

    rows, _parser, meta = seogwipo.collect_seogwipo_eticket_education(
        _target(),
        session_factory=source.factory,
        today=date(2026, 8, 5),
        max_pages=1,
        detail_limit=100,
    )

    assert rows == []
    assert meta["malformed_count"] == 1
    assert meta["snapshot_complete"] is False
    assert "malformed official list cards" in meta["configured_collection_error"]
    assert [method for method, _url, _kwargs in source.calls] == ["GET"]


@pytest.mark.parametrize(
    "source, detail_limit, expected_error",
    [
        (FakeSource(duplicate_list_identity=True), 7, "duplicate official goods identities"),
        (
            FakeSource(header_extra_identity=True),
            7,
            "header education sale-goods union",
        ),
        (FakeSource(goods_title_mismatch=True), 7, "list/goods title mismatch"),
        (FakeSource(ticket_redirect=True), 7, "redirects are not accepted"),
        (FakeSource(), 0, "detail_limit cap"),
    ],
)
def test_snapshot_fails_closed_on_completeness_or_detail_contract_errors(
    source: FakeSource,
    detail_limit: int,
    expected_error: str,
) -> None:
    rows, _parser, meta = seogwipo.collect_seogwipo_eticket_education(
        _target(),
        session_factory=source.factory,
        today=date(2026, 7, 19),
        max_pages=1,
        detail_limit=detail_limit,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert expected_error in meta["configured_collection_error"]


def test_malformed_application_control_is_not_followed() -> None:
    source = FakeSource()

    class MalformedSession(FakeSession):
        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            if url == seogwipo.SEOGWIPO_ETICKET_LIST_URL:
                self.source.calls.append(("GET", url, kwargs))
                return FakeResponse(html=_list_html(self.source.records, malformed_link=True))
            return super().get(url, **kwargs)

    rows, _parser, meta = seogwipo.collect_seogwipo_eticket_education(
        _target(),
        session_factory=lambda: MalformedSession(source),
        today=date(2026, 7, 19),
        max_pages=1,
        detail_limit=7,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["malformed_count"] == 1
    assert "malformed official list cards" in meta["configured_collection_error"]
    assert not any(
        "admin=true" in url for method, url, _kwargs in source.calls if method == "GET"
    )


def test_managed_session_is_required_by_default() -> None:
    rows, parser, meta = seogwipo.collect_seogwipo_eticket_education(_target())

    assert rows == []
    assert parser == seogwipo.SEOGWIPO_ETICKET_PARSER
    assert meta["snapshot_complete"] is False
    assert "managed session_factory" in meta["configured_collection_error"]
