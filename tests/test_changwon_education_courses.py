from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import ceil
from threading import Lock
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from Crawler import municipal_changwon as changwon


OFFICIAL_COUNTS = (
    9,
    11,
    8,
    8,
    8,
    0,
    0,
    35,
    0,
    0,
    0,
    0,
    3,
    1,
    1,
    1,
    0,
    15,
    8,
    14,
    1,
    0,
    5,
    7,
    38,
)


def _target(
    provider: str = changwon.CHANGWON_PROVIDER,
    url: str = changwon.CHANGWON_CANONICAL_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "창원시 일상플러스 통합예약 교육 전체",
        "branch": "경상남도 창원시",
    }


@dataclass(frozen=True)
class FakeCourse:
    leaf: changwon.ChangwonLeaf
    identity: str
    sequence: int
    source_status: str
    expired: bool
    district: str

    @property
    def title(self) -> str:
        return f"공식 창원 교육 {self.sequence:04d}"

    @property
    def list_title(self) -> str:
        return f"{self.title}({self.leaf.name})"

    @property
    def fee(self) -> str:
        return "무료" if self.sequence % 2 == 0 else "10,000원"

    @property
    def badge(self) -> str:
        return "무료" if self.fee == "무료" else "유료"

    @property
    def status_class(self) -> str:
        if self.source_status == "접수중":
            return "s1"
        if self.source_status == "접수대기":
            return "s3"
        return "s2"

    @property
    def education_period(self) -> str:
        if self.expired:
            return "2098-08-01 ~ 2098-08-31"
        if self.source_status == "접수대기":
            return "2099-09-01 ~ 2099-09-30"
        return "2099-08-01 ~ 2099-08-31"

    @property
    def application_period(self) -> str:
        if self.expired:
            return "2098-07-01 09:00 ~ 2098-07-31 18:00"
        if self.source_status == "접수중":
            return "2099-07-01 09:00 ~ 2099-07-31 18:00"
        if self.source_status == "접수대기":
            return "2099-08-01 09:00 ~ 2099-08-31 18:00"
        return "2099-06-01 09:00 ~ 2099-06-30 18:00"

    @property
    def facility_address(self) -> str:
        if not self.district:
            return "경상남도 창원시 중앙대로 1"
        return f"경상남도 창원시 {self.district} 테스트로 1"

    @property
    def detail_href(self) -> str:
        return f"?amode=view&lectureId={self.identity}&fcd=F001"


def _courses(counts: tuple[int, ...] = OFFICIAL_COUNTS) -> list[FakeCourse]:
    statuses = (
        ["접수마감"] * 13
        + ["접수중"] * 58
        + ["인원마감"] * 36
        + ["접수마감"] * 63
        + ["접수대기"] * 3
    )
    assert len(statuses) == sum(OFFICIAL_COUNTS)
    districts = ("의창구", "성산구", "마산합포구", "마산회원구", "진해구", "")
    result: list[FakeCourse] = []
    sequence = 1
    for leaf, count in zip(changwon.CHANGWON_LEAVES, counts):
        for _ in range(count):
            status = statuses[sequence - 1] if counts == OFFICIAL_COUNTS else "접수마감"
            district = leaf.district or districts[(sequence - 1) % len(districts)]
            result.append(
                FakeCourse(
                    leaf=leaf,
                    identity=(
                        "LT003513" if sequence == 1 else f"LT{100000 + sequence}"
                    ),
                    sequence=sequence,
                    source_status=status,
                    expired=counts == OFFICIAL_COUNTS and sequence <= 13,
                    district=district,
                )
            )
            sequence += 1
    return result


def _list_pairs(
    course: FakeCourse,
    *,
    overcapacity_identity: str = "",
    empty_instructor: bool = False,
) -> dict[str, str]:
    return {
        "교육과정": f"교육과정 {course.sequence}",
        "접수일시": course.application_period,
        "교육기간": course.education_period,
        "요일시간": "10:00 ~ 12:00 (월)",
        "신청대상자": "창원시민",
        "정원/대기정원": "20명/5명",
        "신청현황": (
            "26명"
            if course.identity == overcapacity_identity
            else "3명 (후순위자 : 2 명)"
            if course.sequence % 7 == 0
            else "3명"
        ),
        "수강료": course.fee,
        "교육장소": f"{course.leaf.name} 강의실",
        "강사명": "" if empty_instructor else "공식 강사",
    }


def _pair_html(pairs: Mapping[str, str], *, detail: bool = False) -> str:
    left = "span" if detail else "b"
    suffix = "" if detail else " :"
    return "".join(
        f'<li class="di"><{left} class="dt">{key}{suffix}</{left}>'
        f'<span class="dd">{value}</span></li>'
        for key, value in pairs.items()
    )


def _pagination_html(last: int, active: int, leaf: changwon.ChangwonLeaf) -> str:
    values = []
    for page in range(1, last + 1):
        if page == active:
            values.append(f'<span class="m on"><a>{page}</a></span>')
        else:
            values.append(
                f'<span class="m"><a href="?cpage={page}">{page}</a></span>'
            )
    return '<div class="pagination"><span class="pages">' + "".join(values) + "</span></div>"


def _card_html(
    course: FakeCourse,
    *,
    mutate_title: bool = False,
    overcapacity_identity: str = "",
    empty_instructor: bool = False,
) -> str:
    pairs = _list_pairs(
        course,
        overcapacity_identity=overcapacity_identity,
        empty_instructor=empty_instructor,
    )
    title = course.list_title + (" 변경" if mutate_title else "")
    method = (
        '<div class="g2s"><span class="g2 s1">전화</span></div>'
        if course.sequence == 19
        else '<div class="g2s"><span class="g2 s1">인터넷</span></div>'
    )
    return f"""
    <li class="li1">
      <div class="w1">
        <div class="w1c1"><a class="figs" href="{course.detail_href}">
          <em class="g1 {course.status_class}">{course.source_status}</em>
        </a></div>
        <div class="w1c2"><div class="texts">
          <a class="tg1" href="{course.detail_href}">
            <span class="cate">{course.badge}</span><span class="h1">{title}</span>
          </a>
          {method}
          <div class="cp31dlist1"><ul class="dl1">{_pair_html(pairs)}</ul></div>
        </div></div>
      </div>
    </li>
    """


def _list_html(
    leaf: changwon.ChangwonLeaf,
    values: list[FakeCourse],
    *,
    requested_page: int,
    mutate_repeat: bool = False,
    malformed_pagination: bool = False,
    overcapacity_identity: str = "",
    empty_instructor_identity: str = "",
) -> str:
    last = max(1, ceil(len(values) / changwon.CHANGWON_PAGE_SIZE))
    active = min(requested_page, last)
    start = (active - 1) * changwon.CHANGWON_PAGE_SIZE
    page_values = values[start : start + changwon.CHANGWON_PAGE_SIZE]
    if page_values:
        cards = "".join(
            _card_html(
                course,
                mutate_title=(
                    mutate_repeat
                    and requested_page > last
                    and index == 0
                ),
                overcapacity_identity=overcapacity_identity,
                empty_instructor=course.identity == empty_instructor_identity,
            )
            for index, course in enumerate(page_values)
        )
    else:
        cards = '<li>등록된 자료가 없습니다.</li>'
    if malformed_pagination:
        pagination = (
            '<div class="pagination"><span class="pages">'
            '<span class="m on"><a>1</a></span>'
            '<span class="m"><a href="?cpage=3">3</a></span>'
            "</span></div>"
        )
    else:
        pagination = _pagination_html(last, active, leaf)
    return f'<html><body><div class="cp31edu1list1"><ul>{cards}</ul></div>{pagination}</body></html>'


def _detail_html(
    course: FakeCourse,
    *,
    mismatch: bool = False,
    instructor_mismatch: bool = False,
    empty_instructor: bool = False,
    missing_application: bool = False,
    overcapacity_identity: str = "",
) -> str:
    pairs = _list_pairs(
        course,
        overcapacity_identity=overcapacity_identity,
        empty_instructor=empty_instructor,
    )
    detail_pairs = {
        "시설구분": f"{course.leaf.name} - {course.leaf.group}",
        "교육과정": pairs["교육과정"] + (" 불일치" if mismatch else ""),
        "교육대상": pairs["신청대상자"],
        "접수일시": pairs["접수일시"],
        "교육기간": pairs["교육기간"],
        "요일시간": pairs["요일시간"],
        "승인방식": "자동승인",
        "정원/대기정원": "20명 / 5 명",
        "신청현황": (
            "26명" if course.identity == overcapacity_identity else "3명"
        ),
        "수강료": pairs["수강료"],
        "강사명": (
            "공식 변경 강사진" if instructor_mismatch else pairs["강사명"]
        ),
        "재료비": "",
        "교육장소": pairs["교육장소"],
    }
    application = ""
    if (
        course.source_status == "접수중"
        and course.sequence != 19
        and not missing_application
    ):
        label = "대기접수" if course.sequence % 13 == 0 else "예약하기"
        application = (
            '<a class="button primary" '
            f'href="?amode=agree&fcd=F001&lectureId={course.identity}">{label}</a>'
        )
    elif course.source_status == "인원마감":
        application = '<button class="button">인원마감</button>'
    return f"""
    <html><body>
      <div class="cp31edu1view1"><div class="w1">
        <div class="w1c1"><em class="g1 {course.status_class}">{course.source_status}</em></div>
        <div class="w1c2"><h3 class="h1">{course.title}</h3>
          <div class="cp31dlist2"><ul class="dl1">{_pair_html(detail_pairs, detail=True)}</ul></div>
          <div class="infomenu1">{application}<a href="?lectureId={course.identity}">목록으로</a></div>
        </div></div>
      </div></div>
      <div class="tabs1cont">
        <div class="tabs1pane" id="tabs1pane1"><h3 class="blind">교육강좌안내</h3>공식 상세 설명 {course.sequence}</div>
        <div class="tabs1pane" id="tabs1pane4"><h3 class="blind">시설안내</h3>
          <div class="detail1box"><h4 class="h1">{course.leaf.name}</h4><ul>
            <li>주소 : {course.facility_address}</li><li>연락처 : 055-000-0000</li>
          </ul></div>
        </div>
      </div>
    </body></html>
    """


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeChangwonSite:
    def __init__(
        self,
        counts: tuple[int, ...] = OFFICIAL_COUNTS,
        *,
        repeat_mismatch_leaf: str = "",
        malformed_pagination_leaf: str = "",
        detail_mismatch_identity: str = "",
        instructor_mismatch_identity: str = "",
        list_empty_instructor_identity: str = "",
        detail_empty_instructor_identity: str = "",
        missing_application_identity: str = "",
        overcapacity_identity: str = "LT003513",
    ) -> None:
        self.values = _courses(counts)
        self.by_leaf = {
            leaf.code: [value for value in self.values if value.leaf == leaf]
            for leaf in changwon.CHANGWON_LEAVES
        }
        self.by_identity = {value.identity: value for value in self.values}
        self.repeat_mismatch_leaf = repeat_mismatch_leaf
        self.malformed_pagination_leaf = malformed_pagination_leaf
        self.detail_mismatch_identity = detail_mismatch_identity
        self.instructor_mismatch_identity = instructor_mismatch_identity
        self.list_empty_instructor_identity = list_empty_instructor_identity
        self.detail_empty_instructor_identity = detail_empty_instructor_identity
        self.missing_application_identity = missing_application_identity
        self.overcapacity_identity = overcapacity_identity
        self.calls: list[str] = []
        self.sessions: list[FakeSession] = []
        self._lock = Lock()

    def session_factory(self) -> FakeSession:
        current = FakeSession()
        with self._lock:
            self.sessions.append(current)
        return current

    def fetcher(self, _session: FakeSession, url: str, timeout: int) -> str:
        assert timeout > 0
        with self._lock:
            self.calls.append(url)
        parsed = urlparse(url)
        leaf = changwon.CHANGWON_LEAF_BY_PATH[parsed.path]
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query.get("amode") == ["view"]:
            identity = query["lectureId"][0]
            course = self.by_identity[identity]
            return _detail_html(
                course,
                mismatch=identity == self.detail_mismatch_identity,
                instructor_mismatch=(
                    identity == self.instructor_mismatch_identity
                ),
                empty_instructor=(
                    identity == self.detail_empty_instructor_identity
                ),
                missing_application=identity == self.missing_application_identity,
                overcapacity_identity=self.overcapacity_identity,
            )
        page = int((query.get("cpage") or ["1"])[0])
        values = self.by_leaf[leaf.code]
        last = max(1, ceil(len(values) / changwon.CHANGWON_PAGE_SIZE))
        return _list_html(
            leaf,
            values,
            requested_page=page,
            mutate_repeat=(
                leaf.code == self.repeat_mismatch_leaf and page > last
            ),
            malformed_pagination=(
                leaf.code == self.malformed_pagination_leaf and page == 1
            ),
            overcapacity_identity=self.overcapacity_identity,
            empty_instructor_identity=self.list_empty_instructor_identity,
        )


def _collect(site: FakeChangwonSite, **kwargs: Any):
    return changwon.collect_changwon_education_courses(
        _target(),
        timeout=10,
        max_pages=200,
        detail_limit=2000,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        dedupe_rows=lambda rows: rows,
        today="2099-07-20",
        max_workers=6,
        **kwargs,
    )


def test_collects_complete_173_archive_and_only_current_future_rows() -> None:
    site = FakeChangwonSite()
    rows, parser, meta = _collect(site)

    assert parser == changwon.CHANGWON_PARSER
    assert len(rows) == 160
    assert meta["source_total"] == 173
    assert meta["current_count"] == 160
    assert meta["expired_count"] == 13
    assert meta["required_list_requests"] == 54
    assert meta["pages"] == 54
    assert meta["detail_attempts"] == 173
    assert meta["detail_pages"] == 173
    assert meta["source_group_counts"] == {
        "가족": 5,
        "문화예술": 44,
        "반려동물": 7,
        "정보화·IT": 44,
        "주민자치센터": 35,
        "평생학습": 38,
    }
    assert meta["source_status_counts"] == {
        "인원마감": 36,
        "접수대기": 3,
        "접수마감": 76,
        "접수중": 58,
    }
    assert meta["application_open_count"] == 57
    assert meta["official_over_capacity_count"] == 1
    assert meta["official_over_capacity_ids"] == ["LT003513"]
    assert meta["duplicate_identity_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert len({row["provider_course_id"] for row in rows}) == len(rows)
    assert len({row["raw_url"] for row in rows}) == len(rows)
    assert all(row["end_date"] >= "2099-07-20" for row in rows)
    assert sum(row["status"] == "OPEN" for row in rows) == 58
    assert all(
        not row["application_url"]
        or (row["status"] == "OPEN" and row["reservation_available"])
        for row in rows
    )
    assert all(
        row["application_url"].startswith("https://www.changwon.go.kr/")
        for row in rows
        if row["application_url"]
    )
    assert sum(meta["municipality_counts"].values()) == 173
    assert set(meta["municipality_counts"]) == {
        item["full_name"] for item in changwon.CHANGWON_COVERED_MUNICIPALITIES
    }
    phone = next(row for row in rows if row["raw_fields"]["detail_id"] == "LT100019")
    assert phone["status"] == "OPEN"
    assert phone["application_type"] == "PHONE_APPLY"
    assert phone["application_url"] == ""
    assert phone["raw_fields"]["application_methods"] == ["전화"]
    assert any(
        row["raw_fields"]["application_control_label"] == "대기접수"
        and row["application_url"]
        for row in rows
    )
    drift = next(row for row in rows if row["raw_fields"]["detail_id"] == "LT100014")
    assert drift["raw_fields"]["list_wait_current"] == 2
    assert drift["raw_fields"]["detail_wait_current"] == 0
    assert all(session.closed for session in site.sessions)


def test_over_capacity_exception_is_never_allowed_for_current_course() -> None:
    site = FakeChangwonSite(overcapacity_identity="LT100014")
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["detail_errors"] == 1
    assert meta["official_over_capacity_count"] == 0
    assert meta["official_over_capacity_ids"] == []
    assert "application count exceeds capacity plus waitlist" in meta[
        "configured_collection_error"
    ]


def test_fixed_district_leaf_and_detail_address_produce_evidenced_branches() -> None:
    site = FakeChangwonSite()
    rows, _parser, meta = _collect(site)

    fixed = next(
        row
        for row in rows
        if row["raw_fields"]["leaf_code"] == "it_masanhappo"
    )
    assert fixed["municipality_code"] == "4812500000"
    assert fixed["branch"] == "경상남도 창원시 마산합포구"
    assert (
        fixed["raw_fields"]["municipality_evidence"]["field"]
        == "official_district_leaf"
    )

    citywide = next(row for row in rows if row["municipality_code"] == "4812000000")
    assert citywide["branch"] == "경상남도 창원시"
    assert citywide["raw_fields"]["municipality_evidence"]["field"].startswith(
        "citywide"
    )
    assert meta["municipality_evidence_counts"]["official_district_leaf"] > 0


def test_complete_empty_fanout_is_authoritative_no_current_data() -> None:
    site = FakeChangwonSite((0,) * len(changwon.CHANGWON_LEAVES))
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["source_total"] == 0
    assert meta["required_list_requests"] == 50
    assert meta["pages"] == 50
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert "fan-out is empty" in meta["no_current_reason"]


def test_out_of_range_page_must_repeat_exact_final_signature() -> None:
    site = FakeChangwonSite(repeat_mismatch_leaf="resident_masanhappo")
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["source_total"] == 173
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is False
    assert "does not repeat final-page signature" in meta["configured_collection_error"]


def test_non_contiguous_declared_pagination_fails_closed() -> None:
    site = FakeChangwonSite(malformed_pagination_leaf="resident_masanhappo")
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is False
    assert "first-page pagination contract mismatch" in meta["configured_collection_error"]
    assert "fan-out discovery is incomplete" in meta["configured_collection_error"]


def test_every_detail_must_match_its_list_card() -> None:
    site = FakeChangwonSite(detail_mismatch_identity="LT100071")
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["detail_attempts"] == 173
    assert meta["detail_pages"] == 172
    assert meta["detail_errors"] == 1
    assert meta["snapshot_complete"] is False
    assert "detail/list 교육과정 mismatch" in meta["configured_collection_error"]


def test_instructor_drift_uses_detail_without_quarantining_snapshot() -> None:
    identity = "LT100071"
    site = FakeChangwonSite(instructor_mismatch_identity=identity)
    rows, _parser, meta = _collect(site)

    assert len(rows) == 160
    assert meta["snapshot_complete"] is True
    assert meta["detail_authoritative_drift_count"] == 1
    assert meta["detail_authoritative_drift_ids"] == [identity]
    row = next(
        item for item in rows if item["raw_fields"]["detail_id"] == identity
    )
    assert row["instructor"] == "공식 변경 강사진"
    assert row["raw_fields"]["detail_authoritative_drift"] == {
        "field": "강사명",
        "list_value": "공식 강사",
        "detail_value": "공식 변경 강사진",
        "authority": "detail",
        "evidence": "identity and all core list/detail fields matched",
    }


def test_matching_official_blank_instructor_is_optional_metadata() -> None:
    identity = "LT100071"
    site = FakeChangwonSite(
        list_empty_instructor_identity=identity,
        detail_empty_instructor_identity=identity,
    )
    rows, _parser, meta = _collect(site)

    assert len(rows) == 160
    assert meta["snapshot_complete"] is True
    row = next(
        item for item in rows if item["raw_fields"]["detail_id"] == identity
    )
    assert row["instructor"] == ""
    assert row["raw_fields"]["instructor_contract"] == (
        "official_blank_in_list_and_detail"
    )
    assert row["schedule_raw"] == "10:00 ~ 12:00 (월)"
    assert row["venue_name"].endswith("강의실")
    assert row["start_date"] == "2099-08-01"
    assert row["end_date"] == "2099-08-31"


def test_one_sided_blank_instructor_still_fails_closed() -> None:
    identity = "LT100071"
    site = FakeChangwonSite(detail_empty_instructor_identity=identity)
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["detail_errors"] == 1
    assert meta["snapshot_complete"] is False
    assert "one-sided empty instructor value" in meta[
        "configured_collection_error"
    ]


def test_open_source_status_requires_real_safe_agree_link() -> None:
    site = FakeChangwonSite(missing_application_identity="LT100014")
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["detail_errors"] == 1
    assert meta["snapshot_complete"] is False
    assert "internet-open status lacks an active application control" in meta["configured_collection_error"]


def test_caps_never_publish_partial_snapshot() -> None:
    page_site = FakeChangwonSite()
    rows, _parser, meta = changwon.collect_changwon_education_courses(
        _target(),
        max_pages=53,
        detail_limit=2000,
        fetcher=page_site.fetcher,
        session_factory=page_site.session_factory,
        today="2099-07-20",
    )
    assert rows == []
    assert meta["required_list_requests"] == 54
    assert meta["source_cap_reached"] is True
    assert "53 of 54" in meta["configured_collection_error"]

    detail_site = FakeChangwonSite()
    rows, _parser, meta = changwon.collect_changwon_education_courses(
        _target(),
        max_pages=200,
        detail_limit=172,
        fetcher=detail_site.fetcher,
        session_factory=detail_site.session_factory,
        today="2099-07-20",
    )
    assert rows == []
    assert meta["source_total"] == 173
    assert meta["detail_attempts"] == 0
    assert meta["source_cap_reached"] is True
    assert "172 of 173" in meta["configured_collection_error"]


def test_target_and_alias_ownership_contracts_are_explicit() -> None:
    assert changwon.is_changwon_education_target(_target()) is True
    assert (
        changwon.is_changwon_education_target(
            _target(url=changwon.CHANGWON_CANONICAL_URL + "?cpage=1")
        )
        is False
    )
    assert (
        changwon.is_changwon_education_target(
            _target(provider="MUNI_WWW_CHANGWON_GO_KR_91257ACE")
        )
        is False
    )
    assert len(changwon.CHANGWON_LEAVES) == 25
    assert len({leaf.code for leaf in changwon.CHANGWON_LEAVES}) == 25
    assert len({leaf.path for leaf in changwon.CHANGWON_LEAVES}) == 25
    aliases = {alias.provider: alias for alias in changwon.CHANGWON_NON_EXECUTING_ALIASES}
    assert aliases["MUNI_WWW_CHANGWON_GO_KR_2B9F3D84"].ownership == "subset"
    assert aliases["MUNI_WWW_CHANGWON_GO_KR_91257ACE"].ownership == "subset"
    assert aliases["MUNI_WWW_CHANGWON_GO_KR_CC9D014E"].ownership == "subset"
    assert aliases["MUNI_WWW_CHANGWON_GO_KR_C06A834D"].ownership == "excluded_training"
    assert (
        aliases["MUNI_WWW_CHANGWON_GO_KR_C06A834D"].url
        in changwon.CHANGWON_EXCLUDED_TRAINING_URLS
    )
    assert (
        aliases["MUNI_WWW_CHANGWON_GO_KR_C06A834D"].url
        not in changwon.CHANGWON_OWNERSHIP_ALIAS_URLS
    )


def test_source_count_partition_matches_audited_live_catalogue() -> None:
    values = _courses()
    assert len(values) == 173
    assert Counter(value.source_status for value in values) == Counter(
        {"접수중": 58, "인원마감": 36, "접수마감": 76, "접수대기": 3}
    )
    assert sum(
        1 for value in values if value.leaf.group == "정보화·IT"
    ) == 44
    assert sum(
        1 for value in values if value.leaf.group == "주민자치센터"
    ) == 35
    assert sum(1 for value in values if value.leaf.code == "lifelong") == 38
