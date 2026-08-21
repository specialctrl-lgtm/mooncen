from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import json
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_yangyang as yangyang


@dataclass(frozen=True)
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    course_type: str
    status: str
    start: str
    end: str
    apply_start: str
    apply_end: str
    total: int = 12
    current: int = 2
    fee: str = "20,000원"
    venue: str = "308호"
    term: str = "하반기"
    schedule: str = "[수요일]18시30분~20시30분"
    target: str = "없음"
    selection: str = "무작위"
    payment: str = "2026-08-03 09시~2026-08-05 18시"


class DummySession:
    def close(self) -> None:
        pass


def _target() -> Target:
    return Target(yangyang.YANGYANG_PROVIDER, yangyang.YANGYANG_CANONICAL_URL)


def _courses() -> list[Course]:
    current = [
        Course(
            "473",
            "테스트",
            "주간반",
            "수강신청",
            "2026-08-24",
            "2026-12-04",
            "2026-07-21",
            "2026-07-31",
            total=1,
            current=0,
            fee="테스트",
            venue="테스트",
            schedule="[테스트]테스트",
        ),
        Course(
            "500",
            "영어로 세계 여행",
            "야간반",
            "수강신청",
            "2026-08-24",
            "2026-12-04",
            "2026-07-20",
            "2026-07-31",
            total=12,
            current=3,
            fee="30,000",
            venue="304호",
        ),
        Course(
            "501",
            "생활 도예",
            "주간반",
            "신청대기중",
            "2026-08-24",
            "2026-12-04",
            "2026-07-27",
            "2026-07-31",
            total=15,
            current=4,
            venue="101호",
        ),
        Course(
            "502",
            "기초 회화",
            "주간반",
            "신청마감",
            "2026-03-01",
            "2026-10-01",
            "2026-02-01",
            "2026-02-05",
            total=10,
            current=10,
            fee="무료",
            venue="401호",
        ),
    ]
    historical = [
        Course(
            str(450 - index),
            f"지난 강좌 {index + 1}",
            "주간반" if index % 2 == 0 else "야간반",
            "신청마감",
            "2025-01-10",
            "2025-06-30",
            "2024-12-01",
            "2024-12-10",
            total=10,
            current=10,
            venue="305호",
        )
        for index in range(13)
    ]
    return current + historical


def _special_courses() -> list[Course]:
    return [
        Course(
            "600",
            "여름 특별 강좌",
            "주간반",
            "신청대기중",
            "2026-08-01",
            "2026-08-20",
            "2026-07-25",
            "2026-07-28",
            total=20,
            current=0,
            fee="25,000원",
            venue="대회의실",
        ),
        Course(
            "601",
            "지난 특별 강좌",
            "야간반",
            "신청마감",
            "2025-03-01",
            "2025-03-10",
            "2025-02-01",
            "2025-02-05",
            total=20,
            current=20,
            venue="동아리1호실",
        ),
    ]


def _partition_values(partition: str) -> tuple[str, str]:
    return {
        "regular_all": ("", ""),
        "regular_day": ("0", ""),
        "regular_night": ("1", ""),
        "special_all": ("", "1"),
    }[partition]


def _search_form(partition: str) -> str:
    lc_type, lco_type = _partition_values(partition)
    return f"""
      <form class="local_sch01 local_sch" id="fsearch" name="fsearch" method="get">
        <input type="hidden" name="lc_type" value="{lc_type}">
        <input type="hidden" name="lco_type" value="{lco_type}">
        <select id="sfl" name="sfl">
          <option value="lc_title">강의명</option>
          <option value="lc_lecweek">강의요일</option>
        </select>
        <input class="required" name="stx" required value="">
        <input type="submit" value="검색">
      </form>
    """


def _tabs(partition: str) -> str:
    if partition == "special_all":
        return ""
    values = (
        ("전체", "class_list.php", "regular_all"),
        ("주간반", "class_list.php?lc_type=0", "regular_day"),
        ("야간반", "class_list.php?lc_type=1", "regular_night"),
    )
    return "".join(
        f'<a class="btn btn-outline-primary'
        f'{" active" if key == partition else ""}" href="{href}">{label}</a>'
        for label, href, key in values
    )


def _control(item: Course, partition: str, *, conflict: bool = False) -> str:
    lc_type, lco_type = _partition_values(partition)
    if item.status == "수강신청":
        linked = "999" if conflict else item.identity
        if lc_type:
            # Mirrors the live template's identical duplicate lc_type field.
            query = f"lc_type={lc_type}&lc_idx={linked}&lc_type={lc_type}&lco_type="
        else:
            query = f"lc_idx={linked}&lc_type=&lco_type={lco_type}"
        return (
            f'<a class="btn btn-primary float-right" '
            f'href="./reserve_write.php?{query}">{item.status}</a>'
        )
    if item.status == "신청대기중":
        # The source has both wrong-ID and empty disabled links.  Neither is
        # persisted or followed.
        href = (
            f"./reserve_write.php?lc_idx=473&lc_type={lc_type}&lco_type={lco_type}"
            if item.identity == "501"
            else ""
        )
        return (
            f'<a class="btn btn-warning disabled float-right" href="{href}">'
            f"{item.status}</a>"
        )
    return (
        f'<a class="btn btn-danger disabled float-right" href="">'
        f"{item.status}</a>"
    )


def _card(
    item: Course,
    number: int,
    partition: str,
    *,
    conflict_active_control: bool = False,
) -> str:
    fields = (
        ("년도", item.start[:4]),
        ("강의기간", f"{item.start} ~ {item.end}"),
        ("기수", item.term),
        ("강의시간", item.schedule),
        ("정원/신청인원", f"{item.total}/{item.current}"),
        ("접수기간", f"{item.apply_start} 09시~{item.apply_end} 18시"),
        ("수강료", item.fee),
        ("강의장소", item.venue),
        ("강의구분", item.course_type),
        ("납부기간", item.payment),
        ("모집제한", item.target),
        ("선발기준", item.selection),
        # Deliberate PII-like arbitrary content: it must be discarded.
        ("강의내용", "강사 033-670-2777 teacher@example.org 비공개 설명"),
    )
    values = "".join(
        f'<div class="col-3 col-sm-2 border-right th_st">{escape(key)}</div>'
        f'<div class="col-9 col-sm-4 td_st">{escape(value)}</div>'
        for key, value in fields
    )
    split_marker = '<div class="col-3 col-sm-2 border-right th_st">수강료'
    split_at = values.find(split_marker)
    primary_values = values[:split_at]
    detail_values = values[split_at:]
    control = _control(item, partition, conflict=conflict_active_control)
    return f"""
      <div class="req_list">
        <div class="title py-3">{number}. {escape(item.title)}{control}</div>
        <div class="p-2 border border-success rounded-lg clear">
          <div class="row">{primary_values}</div>
          <div class="row collapse" id="list_{item.identity}">
            {detail_values}
            <!-- <div class="th_st">강사명</div><div>홍길동</div>
                 <div class="th_st">경력사항</div><div>비공개</div> -->
          </div>
          <a class="btn btn-outline-info btn-block collapsed more py-0"
             data-toggle="collapse" href="#list_{item.identity}" title="내용 더보기">
             <span class="plus">더보기</span><span class="mlus">닫기</span>
          </a>
        </div>
      </div>
    """


def _list_html(
    items: list[Course],
    partition: str,
    page: int,
    *,
    title_drift: bool = False,
    field_drift: bool = False,
    conflict_active_control: bool = False,
    bad_sentinel: bool = False,
) -> str:
    total = len(items)
    last = max(1, (total + yangyang.YANGYANG_PAGE_SIZE - 1) // yangyang.YANGYANG_PAGE_SIZE)
    start = (page - 1) * yangyang.YANGYANG_PAGE_SIZE
    page_items = items[start : start + yangyang.YANGYANG_PAGE_SIZE]
    injected_sentinel_row = bad_sentinel and page == last + 1 and bool(items)
    if injected_sentinel_row:
        page_items = [items[-1]]
    cards = "".join(
        _card(
            replace(item, title=f"{item.title} 변경" if title_drift and index == 0 else item.title),
            1 if injected_sentinel_row else total - (start + index),
            partition,
            conflict_active_control=conflict_active_control,
        )
        for index, item in enumerate(page_items)
    )
    if field_drift:
        cards = cards.replace("선발기준", "선발방식", 1)
    if page_items:
        marker = f'<span class="pg_current">{page}</span>'
        no_rows = ""
    else:
        marker = ""
        no_rows = '<h3>등록된 강의가 없습니다.</h3>'
    links = []
    if total or page > 1:
        for number in range(1, last + 1):
            if page_items and number == page:
                continue
            links.append(
                f'<a class="pg_page" href="{yangyang.yangyang_list_url(partition, number)}">'
                f"{number} 페이지</a>"
            )
        if page_items and page < last:
            links.append(
                f'<a class="pg_page pg_end" href="{yangyang.yangyang_list_url(partition, last)}">'
                "맨끝</a>"
            )
    heading = "특별강좌신청" if partition == "special_all" else "강좌신청"
    return f"""
      <html><head><title>강의목록 | 양양군평생학습관</title></head><body>
        <h1>{heading}</h1>{_search_form(partition)}{_tabs(partition)}
        {cards}{no_rows}<div class="pg_wrap">{marker}{''.join(links)}</div>
      </body></html>
    """


def _auth_html(*, drift: bool = False) -> str:
    message = "로그인이 필요합니다." if drift else "실명인증으로 로그인 후 사용하십시오."
    redirect = (
        "https://edu.yangyang.go.kr/page/login.php"
        if drift
        else (
            "https://edu.yangyang.go.kr/page/credit.php?"
            "reurl=/lecture/class_list.php?lc_type=0&loc_type=0"
        )
    )
    return f"""
      <html><head><title>오류안내 페이지 | 양양군평생학습관</title></head>
      <body><p>{message}</p><script>
        alert("{message}"); document.location.replace("{redirect}");
      </script></body></html>
    """


class FixtureSite:
    def __init__(
        self,
        *,
        regular: list[Course] | None = None,
        special: list[Course] | None = None,
        recheck_drift: bool = False,
        day_signature_drift: bool = False,
        field_drift: bool = False,
        bad_sentinel: bool = False,
        auth_drift: bool = False,
        conflict_active_control: bool = False,
    ) -> None:
        self.regular = list(_courses() if regular is None else regular)
        self.special = list(_special_courses() if special is None else special)
        self.recheck_drift = recheck_drift
        self.day_signature_drift = day_signature_drift
        self.field_drift = field_drift
        self.bad_sentinel = bad_sentinel
        self.auth_drift = auth_drift
        self.conflict_active_control = conflict_active_control
        self.calls: dict[tuple[str, int], int] = {}
        self.lock = Lock()

    def _partition(self, query: dict[str, list[str]]) -> str:
        if query.get("lco_type", [""])[0] == "1":
            return "special_all"
        if query.get("lc_type", [""])[0] == "0":
            return "regular_day"
        if query.get("lc_type", [""])[0] == "1":
            return "regular_night"
        return "regular_all"

    def __call__(self, _session: object, url: str, _timeout: int) -> str:
        parsed = urlparse(url)
        if parsed.path == yangyang.YANGYANG_APPLICATION_PATH:
            return _auth_html(drift=self.auth_drift)
        assert parsed.scheme == "https"
        assert parsed.netloc == yangyang.YANGYANG_HOST
        assert parsed.path == yangyang.YANGYANG_LIST_PATH
        query = parse_qs(parsed.query, keep_blank_values=True)
        partition = self._partition(query)
        page = int(query.get("page", ["1"])[0])
        with self.lock:
            key = (partition, page)
            self.calls[key] = self.calls.get(key, 0) + 1
            call = self.calls[key]
        if partition == "special_all":
            items = self.special
        elif partition == "regular_day":
            items = [item for item in self.regular if item.course_type == "주간반"]
        elif partition == "regular_night":
            items = [item for item in self.regular if item.course_type == "야간반"]
        else:
            items = self.regular
        title_drift = (
            (self.recheck_drift and partition == "regular_all" and page == 1 and call > 1)
            or (self.day_signature_drift and partition == "regular_day" and page == 1)
        )
        last = max(1, (len(items) + yangyang.YANGYANG_PAGE_SIZE - 1) // yangyang.YANGYANG_PAGE_SIZE)
        return _list_html(
            items,
            partition,
            page,
            title_drift=title_drift,
            field_drift=self.field_drift and partition == "regular_all" and page == 1,
            bad_sentinel=self.bad_sentinel and partition == "regular_all" and page == last + 1,
            conflict_active_control=(
                self.conflict_active_control and partition == "regular_all" and page == 1
            ),
        )


def _collect(site: FixtureSite, **kwargs):
    return yangyang.collect_yangyang_education(
        _target(),
        today="2026-07-21",
        fetcher=site,
        session_factory=DummySession,
        max_workers=4,
        **kwargs,
    )


def test_complete_partitions_special_catalogue_controls_and_pii_allowlist() -> None:
    rows, parser, meta = _collect(FixtureSite())

    assert parser == yangyang.YANGYANG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["required_list_requests"] == 13
    assert meta["list_requests"] == 13
    assert meta["list_rechecks"] == 4
    assert meta["sentinel_pages"] == 4
    assert meta["source_rows"] == 19
    assert meta["regular_all_count"] == 17
    assert meta["regular_day_count"] + meta["regular_night_count"] == 17
    assert meta["special_all_count"] == 2
    assert meta["partition_union_complete"] is True
    assert meta["current_count"] == 5
    assert meta["detail_attempts"] == meta["detail_pages"] == 5
    assert meta["semantic_excluded_count"] == 1
    assert meta["application_gate_attempts"] == 2
    assert meta["application_gate_pages"] == 2
    assert meta["request_count"] == 15
    assert meta["returned_count"] == len(rows) == 4
    assert meta["actionable_count"] == 1
    assert meta["pii_payload_persisted"] is False

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    assert set(by_id) == {"500", "501", "502", "600"}
    assert "473" not in by_id
    active = by_id["500"]
    assert active["branch"] == "양양군평생학습관"
    assert active["venue"] == active["room"] == "304호"
    assert active["venue_name"] == "304호"
    assert active["capacity_total"] == 12
    assert active["capacity_current"] == 3
    assert active["capacity_remaining"] == 9
    assert active["price"] == 30000
    assert active["fee"] == "30,000"
    assert active["reservation_available"] is True
    assert "lc_idx=500" in active["application_url"]
    assert active["raw_fields"]["real_name_auth_gate_verified"] is True
    assert active["description"] == active["title"]
    assert by_id["501"]["application_url"] == ""
    assert by_id["501"]["reservation_available"] is False
    assert by_id["501"]["raw_fields"]["application_control_contract"] == (
        "disabled_inert_url_discarded"
    )
    assert by_id["502"]["price"] == 0
    assert by_id["600"]["raw_fields"]["source_catalogue"] == "special"
    persisted = json.dumps(rows, ensure_ascii=False)
    assert "033-670-2777" not in persisted
    assert "teacher@example.org" not in persisted
    assert "강의내용" not in persisted
    assert "강사명" not in persisted
    assert "경력사항" not in persisted


@pytest.mark.parametrize(
    "target",
    [
        Target("WRONG", yangyang.YANGYANG_CANONICAL_URL),
        Target(yangyang.YANGYANG_PROVIDER, "http://edu.yangyang.go.kr/lecture/class_list.php"),
        Target(yangyang.YANGYANG_PROVIDER, yangyang.YANGYANG_SPECIAL_URL),
        Target(yangyang.YANGYANG_PROVIDER, yangyang.YANGYANG_CANONICAL_URL + "#list_473"),
        Target("MUNI_EDU_YANGYANG_GO_KR_8EB7CE85", "https://edu.yangyang.go.kr/"),
    ],
)
def test_target_is_exact_and_aliases_cannot_collect(target: Target) -> None:
    assert yangyang.is_yangyang_target(target) is False
    rows, _parser, meta = yangyang.collect_yangyang_education(target)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "target does not match" in meta["configured_collection_error"]


def test_page_cap_is_global_and_never_returns_partial_rows() -> None:
    rows, _parser, meta = _collect(FixtureSite(), max_pages=12)
    assert rows == []
    assert meta["required_list_requests"] == 13
    assert meta["list_requests"] == 4
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "max_pages cap" in meta["configured_collection_error"]


def test_detail_cap_fails_closed_after_complete_pagination() -> None:
    rows, _parser, meta = _collect(FixtureSite(), detail_limit=4)
    assert rows == []
    assert meta["list_requests"] == meta["required_list_requests"] == 13
    assert meta["source_cap_reached"] is True
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is False
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_page_one_recheck_drift_invalidates_snapshot() -> None:
    rows, _parser, meta = _collect(FixtureSite(recheck_drift=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "page one changed during traversal" in meta["configured_collection_error"]


def test_nonempty_immediate_sentinel_invalidates_snapshot() -> None:
    rows, _parser, meta = _collect(FixtureSite(bad_sentinel=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "immediate post-last page is not empty/stable" in meta[
        "configured_collection_error"
    ]


def test_regular_day_night_signature_must_equal_all_partition() -> None:
    rows, _parser, meta = _collect(FixtureSite(day_signature_drift=True))
    assert rows == []
    assert meta["partition_union_complete"] is False
    assert "partition signature differs" in meta["configured_collection_error"]


def test_active_application_control_must_bind_to_its_course() -> None:
    rows, _parser, meta = _collect(FixtureSite(conflict_active_control=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "active control identity differs" in meta["configured_collection_error"]


def test_real_name_auth_gate_drift_invalidates_every_row() -> None:
    rows, _parser, meta = _collect(FixtureSite(auth_drift=True))
    assert rows == []
    assert meta["application_gate_attempts"] == 2
    assert meta["application_gate_pages"] == 0
    assert meta["snapshot_complete"] is False
    assert "real-name gate changed" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("redirect", "message"),
    [
        (
            "https://edu.yangyang.go.kr/page/credit.php?"
            "reurl=/lecture/class_list.php?lc_type=0&loc_type=1",
            "auth location changed",
        ),
        (
            "https://edu.yangyang.go.kr/page/credit.php?"
            "reurl=/lecture/class_list.php?lc_type=1&loc_type=0",
            "auth return target changed",
        ),
        (
            "https://edu.yangyang.go.kr/page/credit.php?"
            "reurl=/lecture/class_list.php?lc_type=0&loc_type=0&next=1",
            "auth redirect query changed",
        ),
    ],
)
def test_real_name_auth_gate_query_is_exact(
    redirect: str,
    message: str,
) -> None:
    html = f"""
      <html><head><title>오류안내 페이지 | 양양군평생학습관</title></head>
      <body><p>실명인증으로 로그인 후 사용하십시오.</p><script>
        alert("실명인증으로 로그인 후 사용하십시오.");
        document.location.replace("{redirect}");
      </script></body></html>
    """

    with pytest.raises(yangyang.YangyangContractError, match=message):
        yangyang._validate_auth_gate(yangyang._coerce_soup(html), "500")


def test_embedded_detail_field_drift_invalidates_every_row() -> None:
    rows, _parser, meta = _collect(FixtureSite(field_drift=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "embedded-detail fields changed" in meta["configured_collection_error"]


def test_phone_or_email_in_persisted_title_fails_closed() -> None:
    courses = _courses()
    courses[1] = replace(courses[1], title="문의 033-670-2777")
    rows, _parser, meta = _collect(FixtureSite(regular=courses))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "phone/email reached persisted allowlist" in meta[
        "configured_collection_error"
    ]


def test_dedupe_cannot_silently_shrink_complete_snapshot() -> None:
    rows, _parser, meta = _collect(
        FixtureSite(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed complete count" in meta["configured_collection_error"]


def test_verified_empty_catalogues_are_not_an_error() -> None:
    rows, _parser, meta = _collect(FixtureSite(regular=[], special=[]))
    assert rows == []
    assert meta["required_list_requests"] == 12
    assert meta["list_requests"] == 12
    assert meta["source_rows"] == 0
    assert meta["current_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["partition_union_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert "no current/future" in meta["no_current_reason"]


def test_conflicting_duplicate_query_parameter_is_rejected() -> None:
    with pytest.raises(yangyang.YangyangContractError):
        yangyang._official_url(
            "https://edu.yangyang.go.kr/lecture/reserve_write.php?"
            "lc_idx=500&lc_type=0&lc_type=1&lco_type=",
            path=yangyang.YANGYANG_APPLICATION_PATH,
        )


def test_default_transport_keeps_tls_verification_and_redirects_disabled(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class Response:
        status_code = 200
        headers: dict[str, str] = {}
        content = b"<html><body>ok</body></html>"

    class Session:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.verify = True

        def get(self, url: str, **kwargs):
            calls.append({"url": url, "verify": self.verify, **kwargs})
            return Response()

    monkeypatch.setattr(yangyang.requests, "Session", Session)
    session = yangyang._default_session_factory()
    yangyang._default_fetcher(session, yangyang.YANGYANG_CANONICAL_URL, 17)
    assert calls == [
        {
            "url": yangyang.YANGYANG_CANONICAL_URL,
            "verify": True,
            "timeout": 17,
            "allow_redirects": False,
        }
    ]


def test_ownership_audit_keeps_library_and_notice_boards_out_of_owner() -> None:
    audit = yangyang.YANGYANG_CANDIDATE_AUDIT
    assert audit[yangyang.YANGYANG_CANONICAL_CANDIDATE_ID]["owner"] == (
        yangyang.YANGYANG_PROVIDER
    )
    assert audit["MUNI_IR_7EB5E8E1192F"]["owner"] == yangyang.YANGYANG_PROVIDER
    assert audit["MUNI_IR_91CEDCA0D277"]["owner"] == ""
    assert audit["MUNI_IR_987E88A3B3B4"]["owner"] == (
        "MUNI_LIB_GWE_GO_KR_CB6B94A3"
    )
    assert yangyang.YANGYANG_LIBRARY_PROGRAM_URL.endswith(
        "/yylib/menu/2614/lecture-event/list/all"
    )
    assert yangyang.YANGYANG_DISCOVERY_AUDIT["separate_library_live_rows"] == 4
