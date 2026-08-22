from __future__ import annotations

import calendar
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import json
import math
import threading
import uuid

import pytest
import requests
from bs4 import BeautifulSoup

from Crawler import municipal_yeosu as yeosu


@dataclass
class Target:
    provider: str = yeosu.YEOSU_PROVIDER
    url: str = yeosu.YEOSU_CANONICAL_URL


class DummySession:
    def close(self) -> None:
        pass


def _uid(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"test://yeosu/{value}"))


def _menu_node(
    uid: str,
    name: str,
    kind: str,
    *,
    href: str | None = None,
    children: list[dict] | None = None,
) -> dict:
    return {
        "uid": uid,
        "name": name,
        "menuType": kind,
        "href": href,
        "children": children or [],
    }


def _menu_tree(*, unknown_reservation: bool = False) -> list[dict]:
    sources = {source.key: source for source in yeosu.YEOSU_LECTURE_SOURCES}
    reservations = {
        source.key: source for source in yeosu.YEOSU_RESERVATION_SOURCES
    }
    health = sources["health"]
    digital = sources["digital"]
    children: list[dict] = []
    for source in yeosu.YEOSU_LECTURE_SOURCES:
        nested: list[dict] = []
        if source is health:
            cpr = reservations["cpr"]
            hypertension = reservations["hypertension_diabetes"]
            alias_uid, alias_name = source.aliases[0]
            nested = [
                _menu_node(cpr.menu_uid, "심폐소생술", "RESERVATION"),
                _menu_node(alias_uid, alias_name, "LECTURE"),
                _menu_node(
                    hypertension.menu_uid,
                    "고혈압·당뇨병",
                    "RESERVATION",
                ),
            ]
        elif source is digital:
            alias_uid, alias_name = source.aliases[0]
            nested = [
                _menu_node(alias_uid, alias_name, "LECTURE"),
                _menu_node(_uid("digital-board"), "연간교육일정", "BOARD"),
            ]
        children.append(
            _menu_node(
                source.menu_uid,
                source.label,
                "LECTURE",
                children=nested,
            )
        )
    for source in yeosu.YEOSU_EXPERIENCE_SOURCES:
        children.append(
            _menu_node(
                source.menu_uid,
                source.label.rsplit(" > ", 1)[-1],
                "RESERVATION",
            )
        )
    for uid, (name, href) in yeosu.YEOSU_EXTERNAL_OWNER_LINKS.items():
        children.append(_menu_node(uid, name, "HREF", href=href))
    if unknown_reservation:
        children.append(
            _menu_node(_uid("new-education-reservation"), "신규교육", "RESERVATION")
        )
    return [
        _menu_node(
            yeosu.YEOSU_EDUCATION_ROOT_UID,
            "교육강좌",
            "HREF",
            children=children,
        )
    ]


def _lecture_item(
    identity: str,
    name: str,
    *,
    current: bool = True,
    status: int = 3,
    conflict_fee: bool = False,
) -> dict:
    return {
        "uid": identity,
        "categoryName": "테스트교육",
        "name": name,
        "maxPeople": 20,
        "freeStatus": conflict_fee,
        "price": 30000 if conflict_fee else 10000,
        "recruitStart": "2026-07-01 09:00:00.0",
        "recruitEnd": "2026-07-30 18:00:00.0",
        "classStart": "2026-08-01" if current else "2026-01-01",
        "classEnd": "2026-08-31" if current else "2026-01-31",
        "classStartTime": "10:00",
        "classEndTime": "12:00",
        "onlineStatus": True,
        "directStatus": True,
        "callStatus": False,
        "currentPeople": 5,
        "waitingStatus": status == 1,
        "status": status,
        "recruitType": 2,
        "recruitmentTarget": "여수시민",
        "place": "교육실",
        "classDay": [2, 4],
        # These source fields are intentionally never persisted.
        "teacherName": "홍길동",
        "images": None,
        "menuUid": "",
    }


def _lecture_detail(item: dict, institution_uid: str) -> dict:
    return {
        "uid": item["uid"],
        "name": item["name"],
        "maxPeople": item["maxPeople"],
        "freeStatus": item["freeStatus"],
        "price": item["price"],
        "recruitStart": item["recruitStart"],
        "recruitEnd": item["recruitEnd"],
        "classStart": item["classStart"],
        "classEnd": item["classEnd"],
        "classStartTime": item["classStartTime"] + ":00",
        "classEndTime": item["classEndTime"] + ":00",
        "onlineStatus": item["onlineStatus"],
        "directStatus": item["directStatus"],
        "callStatus": item["callStatus"],
        "institutionUid": institution_uid,
        "classDay": item["classDay"],
        "waitingStatus": item["waitingStatus"],
        "status": item["status"],
        "managerName": "담당자",
        "concatNumber": "061-000-0000",
        "detailDescription": "저장하지 않는 상세 HTML",
        "restriction": "저장하지 않는 유의사항",
    }


def _lecture_form(item: dict, institution_uid: str) -> dict:
    return {
        "uid": item["uid"],
        "institutionUid": institution_uid,
        "name": item["name"],
        "price": item["price"],
        "freeStatus": item["freeStatus"],
        "classStart": item["classStart"],
        "classEnd": item["classEnd"],
        "classStartTime": item["classStartTime"] + ":00",
        "classEndTime": item["classEndTime"] + ":00",
        "waitingStatus": item["waitingStatus"],
        "classDay": item["classDay"],
        "termsPrvcContent": "개인정보 약관",
        "termsPrvc2Content": "제3자 제공 약관",
        "clientFields": [{"name": "휴대폰"}],
        "bankName": "테스트은행",
        "accountNo": "000-000",
        "depositor": "예금주",
    }


def _reservation_item(source: yeosu.YeosuReservationSource) -> dict:
    identity = _uid(f"reservation-{source.key}")
    return {
        "uid": identity,
        "name": "심폐소생술 교육" if source.key == "cpr" else "고혈압·당뇨병 교육",
        "onlineStatus": True,
        "directStatus": source.key == "cpr",
        "callStatus": True,
        "price": 0,
        "rangeInfiniteStatus": True,
        "reserveEnable": False,
        "freeStatus": True,
        "menuUid": source.menu_uid,
    }


def _reservation_detail(
    source: yeosu.YeosuReservationSource, item: dict
) -> dict:
    reversed_period = source.key == "hypertension_diabetes"
    return {
        **{key: item[key] for key in (
            "uid",
            "name",
            "onlineStatus",
            "directStatus",
            "callStatus",
            "price",
            "rangeInfiniteStatus",
            "reserveEnable",
            "freeStatus",
        )},
        "category": {
            "uid": source.category_uid,
            "name": source.label.rsplit(" > ", 1)[-1],
        },
        "useStartDate": "2026-07-25",
        "useEndDate": "2026-07-24" if reversed_period else "2026-08-15",
        "maxPeople": 20,
        "address": "전남광주통합특별시 여수시 테스트로 1",
        "addressDetail": "보건교육실",
        "manager": {"actualName": "담당자", "concatNumber": "061-000-0000"},
        "detailDescription": "저장하지 않는 상세 HTML",
        "inquiries": ["061-000-0000"],
    }


def _calendar(month: str) -> dict:
    year, month_number = (int(value) for value in month.split("-"))
    output: dict[str, dict[str, int]] = {}
    for day in range(1, calendar.monthrange(year, month_number)[1] + 1):
        current = date(year, month_number, day)
        service = current >= date(2026, 7, 21) and current.weekday() in {0, 1, 2}
        output[current.isoformat()] = {
            "waitingMaxCount": 0,
            "closeStatus": 0 if service else 1,
            "seatStatus": 0,
            "holidayStatus": 0 if service else 1,
            "reservationNowCount": 1 if service else 0,
            "standbyStatus": 1 if service else 0,
            "waitingNowCount": 0,
            "reservationMaxCount": 20 if service else 0,
            "waitingStatus": 0,
        }
    return output


def _page(rows: list[dict], page: int, size: int) -> dict:
    total = len(rows)
    pages = math.ceil(total / size) if total else 0
    content = deepcopy(rows[page * size : (page + 1) * size]) if page < pages else []
    return {
        "content": content,
        "totalPages": pages,
        "totalElements": total,
        "size": size,
        "number": page,
        "numberOfElements": len(content),
        "empty": not content,
        "first": page == 0,
        "last": page >= max(0, pages - 1),
    }


class ApiFixture:
    def __init__(
        self,
        *,
        unknown_reservation: bool = False,
        nonempty_sentinel: bool = False,
        unstable_first_page: bool = False,
        form_mismatch: bool = False,
    ) -> None:
        self.unknown_reservation = unknown_reservation
        self.nonempty_sentinel = nonempty_sentinel
        self.unstable_first_page = unstable_first_page
        self.form_mismatch = form_mismatch
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()
        self._page_zero_calls: Counter[tuple[str, str]] = Counter()
        self.lecture_rows: dict[str, list[dict]] = {}
        self.lecture_owner: dict[str, yeosu.YeosuLectureSource] = {}
        self.lecture_by_uid: dict[str, tuple[yeosu.YeosuLectureSource, dict]] = {}
        for index, source in enumerate(yeosu.YEOSU_LECTURE_SOURCES):
            rows = [
                _lecture_item(
                    _uid(f"lecture-{source.key}-current"),
                    f"{source.label} 현재강좌",
                    status=1 if index == 0 else 3,
                    conflict_fee=index == 1,
                )
            ]
            if index == 0:
                rows.extend(
                    [
                        _lecture_item(
                            _uid("lecture-health-current-2"),
                            "보건소 현재강좌 2",
                            status=2,
                        ),
                        _lecture_item(
                            _uid("lecture-health-expired"),
                            "보건소 과거강좌",
                            current=False,
                            status=4,
                        ),
                    ]
                )
            self.lecture_rows[source.institution_uid] = rows
            self.lecture_owner[source.institution_uid] = source
            for item in rows:
                self.lecture_by_uid[item["uid"]] = (source, item)
        self.lecture_rows[yeosu.YEOSU_FOREST_HEALING_EMPTY_LECTURE_UID] = []

        self.reservation_rows: dict[str, list[dict]] = {}
        self.reservation_by_uid: dict[
            str, tuple[yeosu.YeosuReservationSource, dict]
        ] = {}
        for source in yeosu.YEOSU_HEALTH_EDUCATION_SOURCES:
            item = _reservation_item(source)
            self.reservation_rows[source.category_uid] = [item]
            self.reservation_by_uid[item["uid"]] = (source, item)

    def html(self, _session, url: str, _timeout: int):
        assert url == yeosu.YEOSU_CANONICAL_URL
        return BeautifulSoup(
            "<html><head><title>여수시OK통합예약시스템</title></head>"
            "<body><script src='/newok/js/app.abcdef01.js'></script></body></html>",
            "html.parser",
        )

    def menu_detail(self, uid: str) -> dict:
        for source in yeosu.YEOSU_LECTURE_SOURCES:
            for menu_uid, name in source.menu_contracts:
                if uid == menu_uid:
                    return {
                        "uid": uid,
                        "name": name,
                        "menuType": "LECTURE",
                        "lecture": {"uid": source.institution_uid},
                        "reservation": None,
                    }
        for source in yeosu.YEOSU_RESERVATION_SOURCES:
            if uid == source.menu_uid:
                payload = {
                    "uid": uid,
                    "name": source.label.rsplit(" > ", 1)[-1],
                    "menuType": "RESERVATION",
                    "reservation": {"uid": source.category_uid},
                    "lecture": None,
                }
                if source.key == "forest_healing":
                    payload["lecture"] = {
                        "uid": yeosu.YEOSU_FOREST_HEALING_EMPTY_LECTURE_UID
                    }
                return payload
        raise AssertionError(f"unexpected menu {uid}")

    def __call__(self, _session, url: str, params, _timeout: int):
        params = dict(params or {})
        with self._lock:
            self.calls.append((url, params))
        if url == yeosu.YEOSU_MENU_TREE_URL:
            return deepcopy(
                _menu_tree(unknown_reservation=self.unknown_reservation)
            )
        menu_prefix = f"{yeosu.YEOSU_API_BASE}/menu/"
        if url.startswith(menu_prefix):
            return self.menu_detail(url.removeprefix(menu_prefix))
        if url == f"{yeosu.YEOSU_API_BASE}/lecture-item":
            institution = params["institutionUid"]
            page_number = int(params["page"])
            size = int(params["size"])
            rows = self.lecture_rows[institution]
            key = (url, institution)
            if page_number == 0:
                with self._lock:
                    self._page_zero_calls[key] += 1
                    call_number = self._page_zero_calls[key]
            else:
                call_number = 0
            payload = _page(rows, page_number, size)
            if (
                self.nonempty_sentinel
                and institution == yeosu.YEOSU_LECTURE_SOURCES[0].institution_uid
                and page_number == math.ceil(len(rows) / size)
            ):
                payload["content"] = [deepcopy(rows[0])]
                payload["numberOfElements"] = 1
                payload["empty"] = False
            if (
                self.unstable_first_page
                and institution == yeosu.YEOSU_LECTURE_SOURCES[0].institution_uid
                and page_number == 0
                and call_number > 1
                and payload["content"]
            ):
                payload["content"][0]["name"] += " 변경"
            return payload
        form_prefix = f"{yeosu.YEOSU_API_BASE}/lecture-item/form/"
        if url.startswith(form_prefix):
            uid = url.removeprefix(form_prefix)
            source, item = self.lecture_by_uid[uid]
            result = _lecture_form(item, source.institution_uid)
            if self.form_mismatch:
                result["classEnd"] = "2026-09-01"
            return result
        lecture_detail_prefix = f"{yeosu.YEOSU_API_BASE}/lecture-item/"
        if url.startswith(lecture_detail_prefix):
            uid = url.removeprefix(lecture_detail_prefix)
            source, item = self.lecture_by_uid[uid]
            return _lecture_detail(item, source.institution_uid)
        if url == f"{yeosu.YEOSU_API_BASE}/reservation-item":
            category = params["categoryUid"]
            return _page(
                self.reservation_rows[category],
                int(params["page"]),
                int(params["size"]),
            )
        reservation_prefix = f"{yeosu.YEOSU_API_BASE}/reservation-item/"
        if url.startswith(reservation_prefix) and url.endswith("/calendar"):
            return _calendar(params["selectedMonth"])
        if url.startswith(reservation_prefix):
            uid = url.removeprefix(reservation_prefix)
            source, item = self.reservation_by_uid[uid]
            return _reservation_detail(source, item)
        raise AssertionError(f"unexpected URL {url} {params}")


def _collect(monkeypatch, fixture: ApiFixture, **kwargs):
    monkeypatch.setattr(yeosu, "YEOSU_PAGE_SIZE", 2)
    json_getter = kwargs.pop("json_getter", fixture)
    return yeosu.collect_yeosu_education(
        Target(),
        today="2026-07-21",
        max_pages=10,
        detail_limit=100,
        max_workers=1,
        html_fetcher=fixture.html,
        json_getter=json_getter,
        session_factory=DummySession,
        **kwargs,
    )


def test_candidate_audit_selects_one_root_and_split_owners() -> None:
    assert yeosu.YEOSU_PROVIDER == "MUNI_WWW_YEOSU_GO_KR_E2EAB68F"
    assert yeosu.YEOSU_CANONICAL_CANDIDATE_ID == "MUNI_IR_BBEB8411EC3E"
    assert set(yeosu.YEOSU_CANDIDATE_AUDIT) == {
        "MUNI_IR_01EBA3E2FBDC",
        "MUNI_IR_4FD9ACDC1635",
        "MUNI_IR_5C3CA888F738",
        "MUNI_IR_C075FDAFD0E7",
        "MUNI_IR_EC8644414C46",
        "MUNI_IR_F238D031B0CF",
        "MUNI_IR_F750D9CEDF75",
    }
    decisions = {
        key: value["decision"]
        for key, value in yeosu.YEOSU_CANDIDATE_AUDIT.items()
    }
    assert Counter(decisions.values()) == Counter(
        {
            "subset_alias": 3,
            "excluded_information_board": 1,
            "excluded_campground_non_education": 1,
            "excluded_indoor_playground_experience": 1,
            "subset_of_existing_sports_owner": 1,
        }
    )
    assert (
        yeosu.YEOSU_CANDIDATE_AUDIT["MUNI_IR_C075FDAFD0E7"]["owner"]
        == "MUNI_WWW_YUMCORP_OR_KR_FD06010A"
    )


def test_operational_arguments_require_a_complete_snapshot() -> None:
    from Crawler import Crawler_GeneratedYamlTargets as generated

    arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        yeosu.YEOSU_PROVIDER
    ]
    parsed = generated.parse_args(arguments)

    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.allow_partial_save is False
    assert parsed.per_target_limit == 0
    assert parsed.max_pages == 50
    assert parsed.detail_limit == 500


@pytest.mark.parametrize(
    ("provider", "url", "expected"),
    [
        (yeosu.YEOSU_PROVIDER, yeosu.YEOSU_CANONICAL_URL, True),
        ("MUNI_WRONG", yeosu.YEOSU_CANONICAL_URL, False),
        (yeosu.YEOSU_PROVIDER, yeosu.YEOSU_CANONICAL_URL + "/", False),
        (yeosu.YEOSU_PROVIDER, yeosu.YEOSU_CANONICAL_URL + "?page=1", False),
        (yeosu.YEOSU_PROVIDER, yeosu.YEOSU_CANONICAL_URL.replace("https", "http"), False),
        (
            yeosu.YEOSU_PROVIDER,
            yeosu.YEOSU_CANONICAL_URL + "/lecture/" + yeosu.YEOSU_LECTURE_SOURCES[0].menu_uid,
            False,
        ),
    ],
)
def test_target_boundary_is_exact(provider: str, url: str, expected: bool) -> None:
    assert yeosu.is_target(Target(provider=provider, url=url)) is expected


def test_complete_snapshot_exhausts_pages_details_controls_and_discards_pii(
    monkeypatch,
) -> None:
    fixture = ApiFixture()
    rows, parser, meta = _collect(monkeypatch, fixture)

    assert parser == yeosu.YEOSU_PARSER
    assert len(rows) == 10
    assert meta["lecture_history_count"] == 9
    assert meta["expired_lecture_count"] == 1
    assert meta["current_lecture_count"] == 8
    assert meta["current_reservation_education_count"] == 2
    assert meta["sentinel_requests"] == 10
    assert meta["page_one_rechecks"] == 10
    assert meta["detail_pages"] == 10
    assert meta["form_pages"] == 8
    assert meta["calendar_requests"] == 4
    assert meta["pagination_detected"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["education_experience_separated"] is True
    assert meta["pii_excluded"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["fee_conflict_count"] == 1
    assert meta["hour_endpoint_called"] is False
    assert not any("/hour/" in url for url, _params in fixture.calls)
    assert all(row["target"] for row in rows)
    assert all(row["fee"] for row in rows)
    assert all(row["period"] for row in rows)
    assert all(row["venue_name"] or row.get("address") for row in rows)
    assert all(row["category"] for row in rows)
    assert all(row["schedule_raw"] for row in rows)

    identities = [row["provider_course_id"] for row in rows]
    assert len(identities) == len(set(identities))
    assert {row["provider"] for row in rows} == {yeosu.YEOSU_PROVIDER}
    assert {row["municipality_code"] for row in rows} == {"1213000000"}
    assert not {
        source.menu_uid for source in yeosu.YEOSU_EXPERIENCE_SOURCES
    } & {
        row["raw_fields"]["source_menu_uid"] for row in rows
    }

    rendered = json.dumps(rows, ensure_ascii=False)
    for field in yeosu.YEOSU_PII_FIELDS_DISCARDED:
        assert field not in rendered
    assert "홍길동" not in rendered
    assert "061-000-0000" not in rendered
    assert "개인정보 약관" not in rendered
    assert "000-000" not in rendered

    open_lecture = next(
        row
        for row in rows
        if row["raw_fields"]["source_kind"] == "lecture"
        and row["status"] == "OPEN"
    )
    assert open_lecture["application_type"] == "WAITLIST_APPLY"
    assert "/lecture/form/" in open_lecture["application_url"]
    conflict = next(
        row for row in rows if row["raw_fields"].get("source_fee_conflict")
    )
    assert conflict["fee"] == "무료"
    assert conflict["raw_fields"]["source_price"] == 30000
    hypertension = next(
        row for row in rows if "고혈압" in row["title"]
    )
    assert hypertension["raw_fields"]["source_period_conflict"] is True
    assert hypertension["raw_fields"]["hour_endpoint_skipped_for_pii"] is True


def test_lecture_without_published_place_uses_audited_source_label() -> None:
    source = yeosu.YEOSU_LECTURE_SOURCES[1]
    item = _lecture_item(_uid("missing-place"), "장소 미기재 강좌")
    item["place"] = ""

    row = yeosu._lecture_row(Target(), source, item)

    assert row["venue_name"] == source.label
    assert row["raw_fields"]["venue_fallback_to_source"] is True
    assert row["period"] == "2026-08-01 ~ 2026-08-31"


@pytest.mark.parametrize(
    ("fixture_kwargs", "error_text"),
    [
        ({"nonempty_sentinel": True}, "sentinel page is not empty"),
        ({"unstable_first_page": True}, "page zero changed"),
        ({"form_mismatch": True}, "form classEnd mismatch"),
        ({"unknown_reservation": True}, "direct-owner fan-out changed"),
    ],
)
def test_contract_drift_fails_closed(
    monkeypatch, fixture_kwargs: dict, error_text: str
) -> None:
    fixture = ApiFixture(**fixture_kwargs)
    rows, _parser, meta = _collect(monkeypatch, fixture)
    assert rows == []
    assert meta["full_snapshot_validated"] is False
    assert error_text in meta["configured_collection_error"]


def test_in_progress_foreign_language_detail_500_uses_bound_public_form(
    monkeypatch,
) -> None:
    fixture = ApiFixture()
    broken_uid, (_source, broken_item) = next(
        (uid, pair)
        for uid, pair in fixture.lecture_by_uid.items()
        for source, item in (pair,)
        if source.key == "foreign_language" and item["status"] == 3
    )
    broken_item["classStart"] = "2026-03-23"
    broken_item["classEnd"] = "2026-12-05"

    def getter(session, url, params, timeout):
        if url == f"{yeosu.YEOSU_API_BASE}/lecture-item/{broken_uid}":
            response = requests.Response()
            response.status_code = 500
            response.url = url
            raise requests.HTTPError("500 Server Error", response=response)
        return fixture(session, url, params, timeout)

    rows, _parser, meta = _collect(
        monkeypatch,
        fixture,
        json_getter=getter,
    )

    assert len(rows) == 10
    assert meta["form_only_detail_verifications"] == 1
    assert meta["detail_pages"] == 9
    assert meta["details_complete"] is True
    row = next(
        item
        for item in rows
        if item["raw_fields"].get("source_uid") == broken_uid
    )
    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert row["raw_fields"]["detail_verified"] is False
    assert row["raw_fields"]["detail_api_unavailable_status"] == 500
    assert row["raw_fields"]["detail_verification_mode"] == (
        "stable_list+public_form"
    )
    assert row["raw_fields"]["form_verified"] is True


def test_open_lecture_detail_500_remains_fail_closed(monkeypatch) -> None:
    fixture = ApiFixture()
    broken_uid = next(
        uid
        for uid, (_source, item) in fixture.lecture_by_uid.items()
        if item["status"] == 1
    )

    def getter(session, url, params, timeout):
        if url == f"{yeosu.YEOSU_API_BASE}/lecture-item/{broken_uid}":
            response = requests.Response()
            response.status_code = 500
            response.url = url
            raise requests.HTTPError("500 Server Error", response=response)
        return fixture(session, url, params, timeout)

    rows, _parser, meta = _collect(
        monkeypatch,
        fixture,
        json_getter=getter,
    )

    assert rows == []
    assert "500 Server Error" in meta["configured_collection_error"]


def test_caps_and_dedupe_loss_fail_closed(monkeypatch) -> None:
    fixture = ApiFixture()
    monkeypatch.setattr(yeosu, "YEOSU_PAGE_SIZE", 2)
    rows, _parser, meta = yeosu.collect_yeosu_education(
        Target(),
        today="2026-07-21",
        max_pages=1,
        detail_limit=100,
        max_workers=1,
        html_fetcher=fixture.html,
        json_getter=fixture,
        session_factory=DummySession,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    fixture = ApiFixture()
    rows, _parser, meta = _collect(
        monkeypatch,
        fixture,
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert meta["full_snapshot_validated"] is False
    assert "dedupe changed complete row count" in meta["configured_collection_error"]


def test_wrong_target_never_fetches() -> None:
    called = False

    def fail_fetch(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not fetch")

    rows, _parser, meta = yeosu.collect_yeosu_education(
        Target(provider="MUNI_WRONG"),
        html_fetcher=fail_fetch,
        json_getter=fail_fetch,
    )
    assert rows == []
    assert called is False
    assert "exact Yeosu OK integrated root" in meta["configured_collection_error"]
