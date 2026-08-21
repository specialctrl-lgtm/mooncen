from __future__ import annotations

from dataclasses import dataclass
from html import escape
import math
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

import pytest

from Crawler import municipal_gochang as gc


@dataclass(frozen=True)
class Target:
    provider: str = gc.GOCHANG_PROVIDER
    url: str = gc.GOCHANG_CANONICAL_URL


class DummySession:
    def close(self) -> None:
        return None


class Response:
    def __init__(
        self,
        url: str,
        body: bytes | str,
        status_code: int = 200,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status_code = status_code
        self.headers = headers or {}


def _item(
    catalogue: str,
    identity: str,
    title: str,
    *,
    period: str,
    apply_period: str,
    venue: str,
    target: str = "고창군민",
    status: str = "교육종료",
    capacity: str = "3/20",
) -> dict[str, str]:
    return {
        "catalogue": catalogue,
        "identity": identity,
        "title": title,
        "period": period,
        "apply_period": apply_period,
        "venue": venue,
        "target": target,
        "status": status,
        "capacity": capacity,
    }


def _fixture_items() -> dict[str, list[dict[str, str]]]:
    return {
        "WOMEN": [
            _item(
                "WOMEN",
                "GORE9000001",
                "여성회관 지난 강좌",
                period="2098-03-01 ~ 2098-06-30",
                apply_period="2098-02-01 ~ 2098-02-10",
                venue="고창군여성회관 2층 강의실",
            )
        ],
        "JBNU": [
            _item(
                "JBNU",
                "GORE9000011",
                "전북대 미래 강좌 A",
                period="2099-08-01 ~ 2099-10-31",
                apply_period="2099-07-01 ~ 2099-07-10",
                venue="고창캠퍼스 대학본부 1층 강의실",
                status="접수완료",
                capacity="20/15",
            ),
            _item(
                "JBNU",
                "GORE9000012",
                "전북대 미래 강좌 B",
                period="2099-08-02 ~ 2099-11-30",
                apply_period="2099-07-02 ~ 2099-07-11",
                venue="고창캠퍼스 공학관 실습실",
                status="교육중",
                capacity="15/15",
            ),
        ],
        "LIBRARY": [
            _item(
                "LIBRARY",
                "GORE9000021",
                "황윤석도서관 미래 강좌 A",
                period="2099-08-03 ~ 2099-08-30",
                apply_period="2099-07-03 ~ 2099-08-20",
                venue="고창황윤석도서관 문화강좌실",
                status="온라인 접수중",
                capacity="4/30",
            ),
            _item(
                "LIBRARY",
                "GORE9000022",
                "황윤석도서관 미래 강좌 B",
                period="2099-08-04 ~ 2099-09-30",
                apply_period="2099-07-04 ~ 2099-08-21",
                venue="고창황윤석도서관 동아리실",
                status="온라인 접수중",
                capacity="6/20",
            ),
            _item(
                "LIBRARY",
                "GORE9000023",
                "성호도서관 미래 강좌",
                period="2099-08-05 ~ 2099-10-01",
                apply_period="2099-07-05 ~ 2099-07-20",
                venue="고창군립성호도서관 2층 강의실",
                target="초등학생 10명",
                status="접수완료",
                capacity="12/10",
            ),
            _item(
                "LIBRARY",
                "GORE0000729",
                "고창군립성호도서관 문화가 있는 날(5월) [말랑말랑 창의 놀이 뇌블럭]",
                period="2026-05-29 ~ 2026-05-27",
                apply_period="2026-05-01 ~ 2026-05-20",
                venue="고창군립성호도서관 2층 강의실",
            ),
            _item(
                "LIBRARY",
                "GORE0000376",
                "성호도서관 문화행사 '책으로 크는 아이들'(8월)",
                period="2023-08-01 ~ 2023-08-30",
                apply_period="2023-08-01 ~ 2023-07-27",
                venue="고창군립성호도서관 2층 강의실",
            ),
            _item(
                "LIBRARY",
                "GORE0000192",
                "시 한 수, 나무 한 그루(흥덕)(화)",
                period="2021-11-09 ~ 2021-10-26",
                apply_period="2021-10-01 ~ 2021-10-20",
                venue="고창군립성호도서관 2층 강의실",
            ),
            _item(
                "LIBRARY",
                "GORE0000164",
                "인문학에 물들다",
                period="2021-07-01 ~ 2021-07-31",
                apply_period="2021-06-01 ~ 2021-06-20",
                venue="고창황윤석도서관 문화강좌실",
                capacity="2/0",
            ),
            _item(
                "LIBRARY",
                "GORE9000029",
                "도서관 지난 강좌",
                period="2098-01-01 ~ 2098-01-31",
                apply_period="2097-12-01 ~ 2097-12-20",
                venue="고창황윤석도서관 문화강좌실",
            ),
        ],
        "AGRI": [],
        "CULTURE": [
            _item(
                "CULTURE",
                "GORE9000041",
                "지역문화 지난 강좌",
                period="2098-04-01 ~ 2098-04-30",
                apply_period="2098-03-01 ~ 2098-03-20",
                venue="신재효판소리공원 세미나실",
            )
        ],
        "LIFELONG": [
            _item(
                "LIFELONG",
                "GORE9000051",
                "평생학습 지난 강좌",
                period="2098-05-01 ~ 2098-05-31",
                apply_period="2098-04-01 ~ 2098-04-20",
                venue="고창군립도서관 문화강좌실",
            )
        ],
    }


_STATUS_HTML = {
    "온라인 접수중": ("rec rec02", "교육신청", "possible possible01 blink", True),
    "접수완료": ("rec rec03", "접수마감", "possible possible02", False),
    "교육중": ("rec rec04", "접수마감", "possible possible02", False),
    "교육종료": ("rec rec04", "접수마감", "possible possible02", False),
}


def _detail_href(item: dict[str, str], page: int) -> str:
    catalogue = gc.GOCHANG_CATALOGUE_BY_CODE[item["catalogue"]]
    query = {
        "menuCd": catalogue.detail_menu,
        "reUniqId": item["identity"],
        "searchCondition": "RE_NAME",
        "searchKeyword": "",
        "orderField": "",
        "orderSort": "desc",
        "searchDateGubun": "3",
        "startPage": page,
    }
    return "/index.gochang?" + urlencode(query)


def _card(item: dict[str, str], page: int) -> str:
    rec_class, possible_text, possible_class, is_open = _STATUS_HTML[item["status"]]
    href = _detail_href(item, page)
    possible_href = f" href='{escape(href)}'" if is_open else ""
    return f"""
      <li><dl><dt><a href="{escape(href)}">{escape(item['title'])}</a></dt>
        <dd><strong>교육기간</strong> {escape(item['period'])}</dd>
        <dd><strong>접수기간</strong> {escape(item['apply_period'])}</dd>
        <dd><strong>교육장</strong>{escape(item['venue'])}</dd>
        <dd><strong>모집대상</strong>{escape(item['target'])}</dd>
      </dl><p class="{rec_class}">{item['status']}<span>{item['capacity']}</span></p>
      <a class="{possible_class}"{possible_href}><span>{possible_text}</span></a></li>
    """


def _list_html(
    catalogue: str,
    requested: int,
    items: list[dict[str, str]],
    *,
    declared_total: int | None = None,
    active: int | None = None,
) -> str:
    total = len(items) if declared_total is None else declared_total
    body = "".join(_card(item, requested) for item in items)
    if not items:
        body = "<li>검색된 자료가 없습니다.</li>"
    pager = (
        "<p class='bbs_page'><span class='on'><a href='#'>"
        f"{active}</a></span></p>"
        if active is not None
        else "<p class='bbs_page'><span><a href='#'>1</a></span></p>"
    )
    return f"""
      <html><body><form name="listForm" action="/index.gochang">
        <input type="hidden" name="startPage" value="{requested}"></form>
        <ul class="search_result"><li class="last">검색된 결과 :
          <span>{total}</span>건</li></ul>
        <div class="bbs_list01"><ul>{body}</ul></div>{pager}
      </body></html>
    """


def _detail_html(
    item: dict[str, str],
    *,
    wrong_title: bool = False,
    wrong_period: bool = False,
    wrong_application: bool = False,
    unsafe_action: bool = False,
    force_form: bool = False,
) -> str:
    title = "다른 강의" if wrong_title else item["title"]
    period = "2099-08-01 ~ 2099-08-02" if wrong_period else item["period"]
    fields = (
        ("강의명", title),
        ("접수기간", item["apply_period"]),
        ("교육기간", period),
        ("교육시간", "화요일 19:00 ~ 21:00"),
        ("교육장", item["venue"]),
        ("강사명", "개인 강사명"),
        ("수강료", "0원"),
        ("교육대상", item["target"]),
        ("신청/정원", item["capacity"].replace("/", " / ")),
        ("문의담당자", "063-560-0000"),
        ("문의전화", "063-560-0000"),
        ("교육내용", "자유 서술 본문과 person@example.kr"),
        ("강의자료", "강의계획서.hwpx"),
        ("접수상태", item["status"]),
    )
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
        for label, value in fields
    )
    is_open = _STATUS_HTML[item["status"]][3]
    form = ""
    if is_open or force_form:
        identity = "GORE9999999" if wrong_application else item["identity"]
        action = (
            "https://evil.example/apply"
            if unsafe_action
            else "/user/jlibEpr/traineeWriteAct.gochang"
        )
        form = f"""
          <form action="{escape(action)}" method="post" id="writeForm" name="writeForm">
            <input type="hidden" name="menuCd" value="DOM_000002406006002000">
            <input type="hidden" name="reUniqId" value="{identity}">
            <div class="res_box" id="writeId"><div>
              <h4>신청자 정보입력</h4><label>신청자명</label>
              <input name="aplyHp" value="063-000-0000">
              <input name="aplyEmail" value="person@example.kr">
            </div></div><input type="submit" value="예약하기">
          </form>
        """
    return f"""
      <html><body><div class="res_box"><div><h4>{escape(title)}</h4>
        <table class="view_table"><tbody>{rows}</tbody></table></div></div>
        {form}
      </body></html>
    """


class Source:
    def __init__(self, mode: str = "complete") -> None:
        self.mode = mode
        self.items = _fixture_items()
        self.calls: list[str] = []
        self.page_calls: dict[tuple[str, int], int] = {}
        self.lock = Lock()

    def __call__(self, _session: Any, url: str, _timeout: int) -> Response:
        with self.lock:
            self.calls.append(url)
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        list_catalogue = next(
            (
                item
                for item in gc.GOCHANG_CATALOGUES
                if item.list_menu == query.get("menuCd")
            ),
            None,
        )
        if list_catalogue is not None:
            requested = int(query["startPage"])
            key = (list_catalogue.code, requested)
            with self.lock:
                self.page_calls[key] = self.page_calls.get(key, 0) + 1
                call_number = self.page_calls[key]
            if self.mode == "redirect" and list_catalogue.code == "WOMEN" and requested == 1:
                return Response(url, "<not valid html", status_code=302)
            source = [dict(item) for item in self.items[list_catalogue.code]]
            if self.mode == "duplicate_identity" and list_catalogue.code == "JBNU":
                source[1]["identity"] = source[0]["identity"]
            if self.mode == "unknown_status" and list_catalogue.code == "WOMEN":
                source[0]["status"] = "새 상태"
            if self.mode == "pii_target" and list_catalogue.code == "WOMEN":
                source[0]["target"] = "person@example.kr"
            total = len(source)
            last = max(1, math.ceil(total / gc.GOCHANG_PAGE_SIZE))
            if requested <= last:
                start = (requested - 1) * gc.GOCHANG_PAGE_SIZE
                rows = source[start : start + gc.GOCHANG_PAGE_SIZE]
                active = requested
            elif total:
                start = (last - 1) * gc.GOCHANG_PAGE_SIZE
                rows = source[start : start + gc.GOCHANG_PAGE_SIZE]
                active = None
            else:
                rows = []
                active = 1
            if (
                self.mode == "boundary_drift"
                and list_catalogue.code == "WOMEN"
                and requested == 1
                and call_number > 1
            ):
                rows[0]["title"] += " 변경"
            if (
                self.mode == "sentinel_drift"
                and list_catalogue.code == "WOMEN"
                and requested == last + 1
            ):
                rows[0]["title"] += " 센티널 변경"
            declared = total
            if (
                self.mode == "declared_total_drift"
                and list_catalogue.code == "LIBRARY"
                and requested == 2
            ):
                declared += 1
            return Response(
                url,
                _list_html(
                    list_catalogue.code,
                    requested,
                    rows,
                    declared_total=declared,
                    active=active,
                ),
            )

        detail_catalogue = next(
            (
                item
                for item in gc.GOCHANG_CATALOGUES
                if item.detail_menu == query.get("menuCd")
            ),
            None,
        )
        if detail_catalogue is not None:
            identity = query.get("reUniqId", "")
            item = next(
                row
                for row in self.items[detail_catalogue.code]
                if row["identity"] == identity
            )
            if self.mode == "response_escape" and identity == "GORE9000011":
                return Response("https://evil.example/course", "<html></html>")
            return Response(
                url,
                _detail_html(
                    item,
                    wrong_title=self.mode == "detail_title" and identity == "GORE9000011",
                    wrong_period=self.mode == "detail_period" and identity == "GORE9000011",
                    wrong_application=(
                        self.mode == "wrong_application" and identity == "GORE9000021"
                    ),
                    unsafe_action=(
                        self.mode == "unsafe_action" and identity == "GORE9000021"
                    ),
                    force_form=(
                        self.mode == "closed_form" and identity == "GORE9000011"
                    ),
                ),
            )
        raise AssertionError(
            f"unexpected request (applicant actions/files must not be fetched): {url}"
        )


@pytest.fixture(autouse=True)
def compact_fixture_page(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if request.node.name == "test_live_gochang_exact_2026_07_22_snapshot":
        return
    monkeypatch.setattr(gc, "GOCHANG_PAGE_SIZE", 3)


def _collect(source: Source, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    options: dict[str, Any] = {
        "today": "2099-07-22",
        "timeout": 10,
        "max_pages": 20,
        "detail_limit": 100,
        "max_workers": 4,
        "session_factory": DummySession,
        "fetcher": source,
    }
    options.update(kwargs)
    rows, parser, meta = gc.collect(Target(), **options)
    assert parser == gc.GOCHANG_PARSER
    return rows, meta


def test_canonical_identity_catalogues_and_discovery_exclusion() -> None:
    assert gc.GOCHANG_PROVIDER == "MUNI_WWW_GOCHANG_GO_KR_45FFAF60"
    assert gc.GOCHANG_CANDIDATE_ID == "MUNI_IR_D31E2CD73D94"
    assert gc.GOCHANG_MUNICIPALITY_CODE == "5279000000"
    assert gc.is_target(Target())
    assert not gc.is_target(Target(url=gc.GOCHANG_CANONICAL_URL + "&extra=1"))
    assert not gc.is_target(Target(url=gc.GOCHANG_CANONICAL_URL.replace("https", "http")))
    assert not gc.is_target(Target(url=gc.GOCHANG_CANONICAL_URL + "#fragment"))
    assert tuple(item.code for item in gc.GOCHANG_CATALOGUES) == (
        "WOMEN",
        "JBNU",
        "LIBRARY",
        "AGRI",
        "CULTURE",
        "LIFELONG",
    )
    assert gc.GOCHANG_SPORTS_REVIEW_CANDIDATE_ID == "MUNI_IR_629D7F9D4DEB"
    assert "http_only_sports" in gc.GOCHANG_DISCOVERY_AUDIT[
        "sports_review_candidate"
    ]["decision"]


def test_complete_six_catalogue_snapshot_is_atomic_pii_safe_and_application_bound() -> None:
    source = Source()
    rows, meta = _collect(source)
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert len(rows) == meta["returned_count"] == meta["current_source_count"] == 5
    assert meta["source_total"] == meta["source_rows"] == 13
    assert meta["historical_invalid_count"] == 4
    assert meta["expired_count"] == meta["archived_rows_skipped_before_detail"] == 8
    assert meta["data_pages"] == 8
    assert meta["list_requests"] == meta["required_list_requests"] == 21
    assert meta["sentinel_requests"] == 6
    assert meta["boundary_rechecks"] == 7
    assert meta["detail_pages"] == meta["detail_attempts"] == 5
    assert meta["catalogue_source_counts"] == {
        "WOMEN": 1,
        "JBNU": 2,
        "LIBRARY": 8,
        "AGRI": 0,
        "CULTURE": 1,
        "LIFELONG": 1,
    }
    assert meta["catalogue_current_counts"] == {
        "WOMEN": 0,
        "JBNU": 2,
        "LIBRARY": 3,
        "AGRI": 0,
        "CULTURE": 0,
        "LIFELONG": 0,
    }
    assert meta["sentinel_mode_counts"] == {
        "exact_last_page_clamp": 5,
        "structural_empty_catalogue_clamp": 1,
    }
    assert meta["status_counts"] == {"CLOSED": 3, "OPEN": 2}
    assert meta["branch_counts"] == {
        "고창군립성호도서관": 1,
        "고창황윤석도서관": 2,
        "전북대학교 고창캠퍼스": 2,
    }
    assert meta["application_control_count"] == 2
    assert {row["program_type"] for row in rows} == {"교육"}
    assert {row["municipality_full_name"] for row in rows} == {
        gc.GOCHANG_MUNICIPALITY_NAME
    }
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["raw_fields"]["detail_verified"] is True for row in rows)
    open_rows = [row for row in rows if row["status"] == "OPEN"]
    assert len(open_rows) == 2
    assert all(row["application_url"] == row["raw_url"] for row in open_rows)
    assert all(row["reservation_available"] is True for row in open_rows)
    assert all(
        row["application_url"] == "" for row in rows if row["status"] == "CLOSED"
    )
    payload = repr(rows)
    assert "063-560-0000" not in payload
    assert "person@example.kr" not in payload
    assert "개인 강사명" not in payload
    assert "강의계획서.hwpx" not in payload
    assert "자유 서술 본문" not in payload
    assert "traineeWriteAct" not in payload
    assert meta["forbidden_applicant_endpoint_requests"] == 0
    assert not any("traineeWriteAct" in url for url in source.calls)
    assert not any("researchFileDown" in url for url in source.calls)


@pytest.mark.parametrize(
    "mode",
    [
        "boundary_drift",
        "sentinel_drift",
        "declared_total_drift",
        "duplicate_identity",
        "unknown_status",
        "pii_target",
        "detail_title",
        "detail_period",
        "wrong_application",
        "unsafe_action",
        "closed_form",
        "response_escape",
    ],
)
def test_source_detail_application_and_privacy_drift_are_atomically_empty(
    mode: str,
) -> None:
    rows, meta = _collect(Source(mode))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_redirect_is_rejected_before_body_parsing() -> None:
    source = Source("redirect")
    rows, meta = _collect(source)
    assert rows == []
    assert "HTTP 302" in meta["configured_collection_error"]
    assert "list container" not in meta["configured_collection_error"]


def test_default_fetcher_explicitly_disables_redirects() -> None:
    class Session:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def get(self, url: str, **kwargs: Any) -> Response:
            self.kwargs = kwargs
            return Response(url, "<html></html>")

    session = Session()
    gc._default_fetcher(session, gc.gochang_list_url("WOMEN", 1), 7)
    assert session.kwargs == {"timeout": 7, "allow_redirects": False}


def test_caps_and_dedupe_cardinality_fail_closed() -> None:
    rows, meta = _collect(Source(), max_pages=7)
    assert rows == []
    assert meta["source_cap_reached"] is True
    rows, meta = _collect(Source(), detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    rows, meta = _collect(Source(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_noncanonical_target_never_fetches() -> None:
    source = Source()
    rows, parser, meta = gc.collect(
        Target(provider="MUNI_WRONG", url=gc.GOCHANG_CANONICAL_URL),
        session_factory=DummySession,
        fetcher=source,
    )
    assert rows == []
    assert parser == gc.GOCHANG_PARSER
    assert meta["configured_collection_error"]
    assert source.calls == []


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_TESTS") != "1",
    reason="set RUN_LIVE_MUNICIPAL_TESTS=1 for the official live audit",
)
def test_live_gochang_exact_2026_07_22_snapshot() -> None:
    rows, parser, meta = gc.collect(
        Target(),
        today="2026-07-22",
        timeout=45,
        max_pages=100,
        detail_limit=100,
        max_workers=4,
    )
    assert parser == gc.GOCHANG_PARSER
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["source_total"] == meta["source_rows"] == 299
    assert meta["unique_identity_count"] == 299
    assert meta["identity_duplicate_count"] == 0
    assert meta["historical_invalid_count"] == 4
    assert meta["data_pages"] == 34
    assert meta["list_requests"] == 50
    assert meta["sentinel_requests"] == 6
    assert meta["boundary_rechecks"] == 10
    assert meta["catalogue_page_counts"] == {
        "WOMEN": 2,
        "JBNU": 2,
        "LIBRARY": 26,
        "AGRI": 1,
        "CULTURE": 2,
        "LIFELONG": 1,
    }
    assert meta["page_sizes"] == {
        "WOMEN": [10, 7],
        "JBNU": [10, 2],
        "LIBRARY": [10] * 25 + [6],
        "AGRI": [0],
        "CULTURE": [10, 3],
        "LIFELONG": [1],
    }
    assert meta["catalogue_source_counts"] == {
        "WOMEN": 17,
        "JBNU": 12,
        "LIBRARY": 256,
        "AGRI": 0,
        "CULTURE": 13,
        "LIFELONG": 1,
    }
    assert meta["catalogue_current_counts"] == {
        "WOMEN": 0,
        "JBNU": 8,
        "LIBRARY": 9,
        "AGRI": 0,
        "CULTURE": 0,
        "LIFELONG": 0,
    }
    assert len(rows) == meta["current_source_count"] == meta["detail_pages"] == 17
    assert meta["expired_count"] == 282
    assert meta["status_counts"] == {"CLOSED": 12, "OPEN": 5}
    assert meta["branch_counts"] == {
        "고창군립성호도서관": 3,
        "고창황윤석도서관": 6,
        "전북대학교 고창캠퍼스": 8,
    }
    assert meta["application_control_count"] == 5
    assert meta["sentinel_mode_counts"] == {
        "exact_last_page_clamp": 5,
        "structural_empty_catalogue_clamp": 1,
    }
    payload = repr(rows)
    assert "문의전화" not in payload
    assert "강사명" not in payload
    assert "교육내용" not in payload
    assert "traineeWriteAct" not in payload
