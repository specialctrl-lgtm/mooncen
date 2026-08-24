from __future__ import annotations

from dataclasses import dataclass
import json
import math
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_jinju as jinju


def _target(
    provider: str = jinju.JINJU_PROVIDER,
    url: str = jinju.JINJU_CANONICAL_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "진주시 통합예약 교육 전체",
        "branch": "경상남도 진주시",
    }


@dataclass
class FakeCourse:
    leaf: jinju.JinjuLeaf
    identity: str
    sequence: int
    status: str = "접수마감"
    expired: bool = False
    reversed_apply: bool = False

    @property
    def title(self) -> str:
        return f"공식 진주 교육 {self.sequence:03d}"

    @property
    def apply_period(self) -> str:
        if self.reversed_apply:
            return "2098-01-20 ~ 2098-01-10" if self.expired else "2099-01-20 ~ 2099-01-10"
        return "2098-01-01 ~ 2098-01-10" if self.expired else "2099-01-01 ~ 2099-01-10"

    @property
    def education_period(self) -> str:
        return "2098-02-01 ~ 2098-02-10" if self.expired else "2099-02-01 ~ 2099-02-10"

    @property
    def facility_full(self) -> str:
        return f"{self.leaf.name} > {self.leaf.name} 교육장"


def _courses(*, info_count: int = 1) -> list[FakeCourse]:
    result: list[FakeCourse] = []
    sequence = 1
    for leaf in jinju.JINJU_EDUCATION_LEAVES:
        count = info_count if leaf.code == "info" else 1
        for _ in range(count):
            result.append(
                FakeCourse(
                    leaf=leaf,
                    identity=f"L{sequence:011d}",
                    sequence=sequence,
                    status="접수중" if sequence == 1 else "접수마감",
                    expired=sequence == 2,
                )
            )
            sequence += 1
    return result


def _methods() -> str:
    return """
    <div class="g2s">
      <a class="g2" title="인터넷 접수 가능"><span class="t1">인터넷</span></a>
      <a class="g2 disabled" title="방문 접수 가능"><span class="t1">방문</span></a>
      <a class="g2 disabled" title="전화 접수 불가"><span class="t1">전화</span></a>
    </div>
    """


def _list_pairs(course: FakeCourse) -> dict[str, str]:
    return {
        "교육구분": f"{course.leaf.name} 구분",
        "신청대상": "진주시민",
        "접수일시": course.apply_period,
        "교육기간": course.education_period,
        "요일시간": "매주 월요일 10:00~12:00",
        "선발방식": "선착순",
        "정원/접수인원/대기자정원": "20명 / 20명 / 5명",
        "신청현황": "3명 (대기자 : 0명)",
        "수강료": "무료",
    }


def _list_pair_html(course: FakeCourse) -> str:
    return "".join(
        f'<li class="di"><b class="dt"><span class="t1">{key} :</span></b>'
        f'<span class="dd">{value}</span></li>'
        for key, value in _list_pairs(course).items()
    )


def _card(course: FakeCourse, *, title_suffix: str = "") -> str:
    href = f"?amode=view&amp;lecture={course.identity}"
    return f"""
    <li class="li1"><div class="w1">
      <div class="w1c1 tac"><a class="figs" href="{href}"><span class="f1"></span></a></div>
      <div class="w1c2"><div class="w1c2c1"><div class="texts">
        <a class="tg1" href="{href}"><em class="g1">{course.status}</em>
          <strong class="t1">{course.title}{title_suffix}</strong></a>
        <div class="cp31dlist1"><ul>{_list_pair_html(course)}</ul></div>
      </div></div><div class="w1c2c2">{_methods()}</div></div>
    </div></li>
    """


def _pager(page: int, last: int, facility_code: str = "") -> str:
    if last == 1:
        return ""
    code = f"gubunCd={facility_code}&amp;" if facility_code else ""
    links = "".join(
        f'<span class="m"><a href="?{code}cpage={value}">{value}</a></span>'
        for value in range(1, last + 1)
        if value != page
    )
    return f"""
    <div class="pagination"><span class="m on"><strong>{page}</strong></span>{links}
      <span class="m last"><a href="?{code}cpage={last}">맨끝</a></span>
    </div>
    """


def _detail_item(class_name: str, label: str, value: str) -> str:
    if class_name == "selection":
        return f'<li class="di {class_name}"><b class="dt"><span class="t1">{label} : {value}</span></b></li>'
    return (
        f'<li class="di {class_name}"><b class="dt"><span class="t1">{label} :</span></b>'
        f'<span class="dd"><span class="t2">{value}</span></span></li>'
    )


def _detail_html(
    course: FakeCourse,
    *,
    title_suffix: str = "",
    outside_address: bool = False,
    application_mode: str = "normal",
) -> str:
    pairs = _list_pairs(course)
    fields = {
        "facilities": ("시설구분", course.facility_full),
        "curriculum": ("교육과정", f"{course.leaf.name} 공식 교육과정"),
        "edu": ("교육구분", pairs["교육구분"]),
        "target": ("신청대상", pairs["신청대상"]),
        "receipt": ("접수일시", pairs["접수일시"]),
        "period": ("교육기간", pairs["교육기간"]),
        "dayhour": ("요일시간", pairs["요일시간"]),
        "selection": ("선발방식", pairs["선발방식"]),
        "quota": ("정원/접수인원/대기자정원", pairs["정원/접수인원/대기자정원"]),
        "application": ("신청현황", pairs["신청현황"]),
        "tuition": ("수강료", pairs["수강료"]),
        "reception": ("접수처", f"{course.leaf.name} 접수처"),
    }
    detail_fields = "".join(
        _detail_item(class_name, label, value)
        for class_name, (label, value) in fields.items()
    )
    address = "경상남도 산청군 테스트로 1" if outside_address else "경상남도 진주시 테스트로 1"
    facility = f"""
      <div id="tabs1pane4"><div class="cp31dlist3"><ul>
        <li class="di"><b class="dt">시설명 :</b><div class="dd">{course.facility_full}</div></li>
        <li class="di"><b class="dt">주소 :</b><div class="dd">{address}</div></li>
        <li class="di"><b class="dt">담당부서 :</b><div class="dd">비공개부서</div></li>
        <li class="di"><b class="dt">담당자 :</b><div class="dd">PII-NAME / 055-000-0000</div></li>
      </ul></div></div>
    """
    control = ""
    if course.status == "접수중" and application_mode != "missing":
        href = f"?amode=ins&amp;lecture={course.identity}"
        if application_mode == "spoof":
            href = f"https://evil.example/apply?amode=ins&amp;lecture={course.identity}"
        control = f'<a id="btn-reserve" class="button large primary" href="{href}">예약하기</a>'
    return f"""
    <html><body><div id="body_content">
      <div class="cp31edu1view1"><div class="hg1"><em class="g1">{course.status}</em>
        <h3 class="h1">{course.title}{title_suffix}</h3></div>
        <div class="w1"><div class="w1c1">{_methods()}</div><div class="w1c2">
          <div class="cp31dlist2"><ul>{detail_fields}</ul></div>
        </div></div></div>
      <div id="tabs1pane3">강사명 PII-INSTRUCTOR</div>
      {facility}{control}
    </div></body></html>
    """


class _Session:
    def close(self) -> None:
        return None


class FakeSite:
    def __init__(self, courses: list[FakeCourse] | None = None) -> None:
        self.courses = courses or _courses()
        self.by_leaf = {
            leaf.code: [course for course in self.courses if course.leaf == leaf]
            for leaf in jinju.JINJU_EDUCATION_LEAVES
        }
        self.by_identity = {course.identity: course for course in self.courses}
        self.calls: list[str] = []
        self.lock = Lock()
        self.page_one_calls: dict[str, int] = {}
        self.sentinel_nonempty_leaf = ""
        self.recheck_mutation_leaf = ""
        self.detail_title_mismatch = ""
        self.outside_address = ""
        self.application_mode: dict[str, str] = {}

    def fetch(self, _session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        parsed = urlparse(url)
        leaf = jinju.JINJU_LEAF_BY_PATH[parsed.path]
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self.lock:
            self.calls.append(url)
        if query.get("amode") == ["view"]:
            identity = query["lecture"][0]
            course = self.by_identity[identity]
            return BeautifulSoup(
                _detail_html(
                    course,
                    title_suffix=" changed" if identity == self.detail_title_mismatch else "",
                    outside_address=identity == self.outside_address,
                    application_mode=self.application_mode.get(identity, "normal"),
                ),
                "lxml",
            )

        page = int((query.get("cpage") or ["1"])[0])
        values = self.by_leaf[leaf.code]
        last = max(1, math.ceil(len(values) / jinju.JINJU_PAGE_SIZE))
        if page > last:
            if leaf.code != self.sentinel_nonempty_leaf:
                return BeautifulSoup("<html><body><div id='body_content'></div></body></html>", "lxml")
            page = last
        page_values = values[(page - 1) * jinju.JINJU_PAGE_SIZE : page * jinju.JINJU_PAGE_SIZE]
        suffix = ""
        if page == 1:
            with self.lock:
                count = self.page_one_calls.get(leaf.code, 0) + 1
                self.page_one_calls[leaf.code] = count
            if leaf.code == self.recheck_mutation_leaf and count > 1:
                suffix = " changed"
        cards = "".join(_card(course, title_suffix=suffix) for course in page_values)
        facility_code = "FAC_001" if leaf.code == "info" and last > 1 else ""
        return BeautifulSoup(
            f"<html><body><div id='body_content'><div class='cp31edu1list1'><ul>{cards}</ul></div>"
            f"{_pager(page, last, facility_code)}</div></body></html>",
            "lxml",
        )


def _collect(
    site: FakeSite,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    return jinju.collect_jinju_education_courses(
        _target(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", 200),
        detail_limit=kwargs.pop("detail_limit", 200),
        fetcher=site.fetch,
        session_factory=_Session,
        today="2099-01-15",
        max_workers=4,
        **kwargs,
    )


def test_fixed_fanout_canonical_identity_and_non_executing_aliases() -> None:
    assert jinju.JINJU_PROVIDER == "MUNI_WWW_JINJU_GO_KR_AC4F2628"
    assert jinju.JINJU_CANDIDATE_ID == "MUNI_IR_69C7C0BA6431"
    assert len(jinju.JINJU_EDUCATION_LEAVES) == 12
    assert len({leaf.path for leaf in jinju.JINJU_EDUCATION_LEAVES}) == 12
    assert not any("09630/09653" in leaf.path for leaf in jinju.JINJU_EDUCATION_LEAVES)
    aliases = jinju.JINJU_NON_EXECUTING_ALIASES
    assert sum(item.provider == "MUNI_WWW_JINJU_GO_KR_5DF28B13" for item in aliases) == 2
    toybank = next(item for item in aliases if item.provider == "MUNI_WWW_JINJU_GO_KR_CC4D7F07")
    assert toybank.ownership == "excluded_toybank"
    assert jinju.is_target(_target())
    assert not jinju.is_target(_target(url=jinju.JINJU_CANONICAL_URL + "?cpage=1"))
    assert not jinju.is_target(_target(provider="MUNI_WRONG"))


def test_complete_snapshot_filters_expired_details_current_and_drops_pii() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == jinju.JINJU_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_total"] == 12
    assert meta["expired_count"] == 1
    assert meta["current_candidate_count"] == len(rows) == 11
    assert meta["required_list_requests"] == 36
    assert meta["list_requests"] == 36
    assert meta["sentinel_count"] == meta["stable_recheck_count"] == 12
    assert meta["duplicate_alias_provider_count"] == 1
    assert meta["application_open_count"] == 1
    assert sum("amode=view" in url for url in site.calls) == 11
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["application_url"].endswith(
        f"amode=ins&lecture={open_row['raw_fields']['identity']}"
    )
    assert open_row["branch"].endswith("교육장")
    assert open_row["municipality_code"] == "4817000000"
    encoded = json.dumps(rows, ensure_ascii=False)
    assert "PII-NAME" not in encoded
    assert "PII-INSTRUCTOR" not in encoded
    assert "055-000-0000" not in encoded
    assert "담당자" not in encoded


def test_declared_multi_page_archive_and_empty_sentinel_are_complete() -> None:
    site = FakeSite(_courses(info_count=11))
    rows, _parser, meta = _collect(site)

    assert meta["configured_collection_error"] == ""
    assert meta["declared_pages_by_leaf"]["info"] == 2
    assert meta["facility_codes_by_leaf"]["info"] == "FAC_001"
    assert meta["source_counts"]["info"] == 11
    assert meta["page_counts"]["info:1"] == 10
    assert meta["page_counts"]["info:2"] == 1
    assert meta["source_total"] == 22
    assert len(rows) == 21
    assert meta["required_list_requests"] == 37


def test_page_and_detail_caps_fail_closed_without_partial_rows() -> None:
    page_rows, _parser, page_meta = _collect(FakeSite(), max_pages=35)
    assert page_rows == []
    assert page_meta["source_cap_reached"] is True
    assert "35 of 36 required list requests" in page_meta["configured_collection_error"]

    detail_rows, _parser, detail_meta = _collect(FakeSite(), detail_limit=10)
    assert detail_rows == []
    assert detail_meta["source_cap_reached"] is True
    assert "10 of 11 required details" in detail_meta["configured_collection_error"]


def test_nonempty_sentinel_and_changed_first_page_fail_closed() -> None:
    sentinel_site = FakeSite()
    sentinel_site.sentinel_nonempty_leaf = "forest"
    rows, _parser, meta = _collect(sentinel_site)
    assert rows == []
    assert "forest: sentinel page is not empty" in meta["configured_collection_error"]

    mutation_site = FakeSite()
    mutation_site.recheck_mutation_leaf = "future"
    rows, _parser, meta = _collect(mutation_site)
    assert rows == []
    assert "future: first page changed during traversal" in meta["configured_collection_error"]


def test_unknown_status_and_duplicate_identity_fail_closed() -> None:
    unknown = _courses()
    unknown[0].status = "확인필요"
    rows, _parser, meta = _collect(FakeSite(unknown))
    assert rows == []
    assert "malformed" in meta["configured_collection_error"]

    duplicated = _courses()
    duplicated[-1].identity = duplicated[0].identity
    rows, _parser, meta = _collect(FakeSite(duplicated))
    assert rows == []
    assert "duplicate source identities" in meta["configured_collection_error"]
    assert "duplicate provider course ids" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("mode", "token"),
    (
        ("title", "detail/list title mismatch"),
        ("address", "facility address is outside Jinju"),
        ("spoof", "invalid application control"),
        ("missing", "internet-open course lacks an application control"),
    ),
)
def test_detail_identity_municipality_and_real_application_contracts(
    mode: str, token: str
) -> None:
    site = FakeSite()
    identity = site.courses[0].identity
    if mode == "title":
        site.detail_title_mismatch = identity
    elif mode == "address":
        site.outside_address = identity
    else:
        site.application_mode[identity] = mode
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert token in meta["configured_collection_error"]


def test_reversed_application_period_is_allowed_only_for_expired_history() -> None:
    historical = _courses()
    historical[1].reversed_apply = True
    rows, _parser, meta = _collect(FakeSite(historical))
    assert meta["configured_collection_error"] == ""
    assert meta["historical_reversed_application_count"] == 1
    assert len(rows) == 11

    current = _courses()
    current[0].reversed_apply = True
    rows, _parser, meta = _collect(FakeSite(current))
    assert rows == []
    assert "current course has reversed application period" in meta["configured_collection_error"]


def test_external_dedupe_cannot_turn_complete_snapshot_into_partial_save() -> None:
    rows, _parser, meta = _collect(FakeSite(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed complete row count 11 to 10" in meta["configured_collection_error"]
