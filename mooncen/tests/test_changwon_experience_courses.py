from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import Crawler_EducationExperience as experience_aggregate
from Crawler import Crawler_MunicipalIntegratedReservation as municipal
from Crawler import municipal_changwon as education
from Crawler import municipal_changwon_experience as experience


def _target(
    url: str = experience.CHANGWON_EXPERIENCE_CANONICAL_URL,
    provider: str = education.CHANGWON_PROVIDER,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "창원 체험", "branch": "창원시"}


@dataclass(frozen=True)
class FakeExperience:
    leaf: experience.ChangwonExperienceLeaf
    sequence: int
    ongoing: bool = False
    expired: bool = False

    @property
    def identity(self) -> str:
        return f"EX{100000 + self.sequence}"

    @property
    def title(self) -> str:
        return f"공식 창원 체험 {self.sequence:03d}"

    @property
    def status(self) -> str:
        return "접수마감" if self.expired else "접수중"

    @property
    def status_class(self) -> str:
        return "s2" if self.expired else "s1"

    @property
    def operation_period(self) -> str:
        if self.ongoing:
            return "상시"
        if self.expired:
            return "2098-01-01 ~ 2098-12-31"
        return "2099-07-01 ~ 2099-12-31"

    @property
    def application_period(self) -> str:
        if self.ongoing:
            return "상시"
        if self.expired:
            return "2098-01-01 ~ 2098-12-31"
        return "2099-07-01 ~ 2099-07-31"

    @property
    def href(self) -> str:
        return f"?amode=view&expId={self.identity}&cpage=9&fcd=T001"


def _pair_html(pairs: Mapping[str, str], *, detail: bool = False) -> str:
    left = "span" if detail else "b"
    suffix = "" if detail else " :"
    return "".join(
        f'<li class="di"><{left} class="dt">{key}{suffix}</{left}>'
        f'<span class="dd">{value}</span></li>'
        for key, value in pairs.items()
    )


def _card_html(value: FakeExperience, *, mutate: bool = False) -> str:
    pairs = {
        "시설명": value.leaf.name,
        "접수일시": value.application_period,
        "운영기간": value.operation_period,
        "장소": f"창원시 {value.leaf.district or '중앙'} 테스트장",
    }
    title = value.title + (" 변경" if mutate else "")
    methods = (
        '<div class="g2s"><span class="g2">전화</span></div>'
        if value.expired
        else '<div class="g2s"><span class="g2">인터넷</span></div>'
    )
    return f"""
    <li><div class="w1">
      <div class="w1c1"><a class="figs" href="{value.href}">
        <em class="g1 {value.status_class}">{value.status}</em>
      </a></div>
      <div class="w1c2"><a class="tg1" href="{value.href}">
        <span class="cate">무료</span><span class="h1">{title}</span>
      </a>{methods}<div class="cp31dlist1">{_pair_html(pairs)}</div></div>
    </div></li>
    """


def _pagination(
    last: int,
    active: int,
    leaf: experience.ChangwonExperienceLeaf,
    *,
    pagination_fcd: str | None = None,
) -> str:
    nodes = []
    fcd = leaf.pagination_fcd if pagination_fcd is None else pagination_fcd
    for page in range(1, last + 1):
        if page == active:
            nodes.append(f'<span class="m on"><a>{page}</a></span>')
        else:
            query = f"fcd={fcd}&amp;" if fcd else ""
            nodes.append(
                f'<span class="m"><a href="?{query}cpage={page}">{page}</a></span>'
            )
    return '<div class="pagination"><span class="pages">' + "".join(nodes) + "</span></div>"


def _list_html(
    leaf: experience.ChangwonExperienceLeaf,
    values: list[FakeExperience],
    page: int,
    *,
    mutate_repeat: bool = False,
    pagination_fcd: str | None = None,
) -> str:
    last = max(1, ceil(len(values) / experience.CHANGWON_EXPERIENCE_PAGE_SIZE))
    active = min(page, last)
    start = (active - 1) * experience.CHANGWON_EXPERIENCE_PAGE_SIZE
    selected = values[start : start + experience.CHANGWON_EXPERIENCE_PAGE_SIZE]
    cards = (
        "".join(
            _card_html(
                value,
                mutate=mutate_repeat and page > last and index == 0,
            )
            for index, value in enumerate(selected)
        )
        if selected
        else "<li>등록된 자료가 없습니다.</li>"
    )
    return (
        f'<div class="cp31edu1list1"><ul>{cards}</ul></div>'
        + _pagination(last, active, leaf, pagination_fcd=pagination_fcd)
    )


def _detail_html(
    value: FakeExperience,
    *,
    mismatch: bool = False,
    reservation_tab_for_closed: bool = False,
) -> str:
    detail_pairs = {
        "시설구분": f"{value.leaf.name} - {value.leaf.group}",
        "대상자": "창원시민",
        "접수기간": value.application_period,
        "운영기간": value.operation_period + (" 변경" if mismatch else ""),
        "장소": f"창원시 {value.leaf.district or '중앙'} 테스트장",
        "승인방식": "자동승인",
    }
    show_reservation_tab = not value.expired or reservation_tab_for_closed
    reservation = (
        '<div class="infomenu1"><a class="reserve1" href="#tabs1pane4">예약하기</a></div>'
        if show_reservation_tab
        else ""
    )
    calendar_pane = (
        '<div id="tabs1pane4"></div>' if show_reservation_tab else ""
    )
    return f"""
    <div class="cp31edu1view1"><div class="w1">
      <div class="w1c1"><em class="g1 {value.status_class}">{value.status}</em></div>
      <div class="w1c2"><h3 class="h1">{value.title}</h3>
        <div class="cp31dlist2">{_pair_html(detail_pairs, detail=True)}</div>
        {reservation}
      </div>
    </div></div>
    <div id="tabs1pane1"><span class="blind">소개</span>공식 체험 설명</div>
    {calendar_pane}
    <div id="tabs1pane2">예약 안내</div>
    <div id="tabs1pane3"><div class="detail1box">
      <h4 class="h1">{value.leaf.name}</h4><ul>
        <li>주소 : 경상남도 창원시 {value.leaf.district or '중앙'} 테스트로 1</li>
        <li>연락처 : 055-000-0000</li>
      </ul>
    </div></div>
    """


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSite:
    def __init__(
        self,
        *,
        repeat_mismatch: bool = False,
        detail_mismatch: str = "",
        burim_pagination_fcd: str | None = None,
    ) -> None:
        leaf = experience.CHANGWON_EXPERIENCE_LEAVES[0]
        self.values = [
            FakeExperience(leaf, sequence, ongoing=sequence == 1, expired=sequence > 10)
            for sequence in range(1, 13)
        ]
        self.by_identity = {value.identity: value for value in self.values}
        self.repeat_mismatch = repeat_mismatch
        self.detail_mismatch = detail_mismatch
        self.burim_pagination_fcd = burim_pagination_fcd
        self.calls: list[str] = []
        self.sessions: list[FakeSession] = []

    def session_factory(self) -> FakeSession:
        current = FakeSession()
        self.sessions.append(current)
        return current

    def fetcher(self, _session: FakeSession, url: str, timeout: int) -> str:
        assert timeout > 0
        self.calls.append(url)
        parsed = urlparse(url)
        assert "/calendar" not in parsed.path
        assert "agree" not in parsed.query
        leaf = experience.CHANGWON_EXPERIENCE_LEAF_BY_PATH[parsed.path]
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query.get("amode") == ["view"]:
            value = self.by_identity[query["expId"][0]]
            return _detail_html(
                value, mismatch=value.identity == self.detail_mismatch
            )
        page = int((query.get("cpage") or ["1"])[0])
        values = self.values if leaf == experience.CHANGWON_EXPERIENCE_LEAVES[0] else []
        last = max(1, ceil(len(values) / experience.CHANGWON_EXPERIENCE_PAGE_SIZE))
        return _list_html(
            leaf,
            values,
            page,
            mutate_repeat=self.repeat_mismatch and page > last,
            pagination_fcd=(
                self.burim_pagination_fcd
                if leaf.code == "burim_craft"
                else None
            ),
        )


def _collect(site: FakeSite, **kwargs: Any):
    return experience.collect_changwon_experience_courses(
        _target(),
        timeout=10,
        max_pages=100,
        detail_limit=100,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        dedupe_rows=lambda rows: rows,
        today="2099-07-20",
        **kwargs,
    )


def test_collects_fixed_fanout_and_filters_expired_without_calendar_calls() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == experience.CHANGWON_EXPERIENCE_PARSER
    assert len(rows) == 10
    assert meta["source_total"] == 12
    assert meta["current_count"] == 10
    assert meta["expired_count"] == 2
    assert meta["ongoing_count"] == 1
    assert meta["required_list_requests"] == 47
    assert meta["pages"] == 47
    assert meta["detail_attempts"] == 12
    assert meta["detail_pages"] == 12
    assert meta["snapshot_complete"] is True
    assert meta["calendar_requests"] == 0
    assert meta["application_requests"] == 0
    assert meta["application_open_count"] == 10
    assert all(row["domain_category"] == "체험·견학" for row in rows)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["municipality_code"] == "4812500000" for row in rows)
    assert all("cpage=" not in row["raw_url"] for row in rows)
    assert all(row["reservation_available"] for row in rows)
    assert all(row["application_url"] == row["raw_url"] for row in rows)
    assert not any("calendar" in url for url in site.calls)
    assert all(session.closed for session in site.sessions)


def test_out_of_range_page_must_repeat_exact_final_signature() -> None:
    site = FakeSite(repeat_mismatch=True)
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is False
    assert "does not repeat final-page signature" in meta["configured_collection_error"]


def test_every_detail_must_match_list_contract() -> None:
    site = FakeSite(detail_mismatch="EX100004")
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["detail_errors"] == 1
    assert "detail/list 운영기간 mismatch" in meta["configured_collection_error"]


def test_closed_status_template_tab_does_not_enable_reservation() -> None:
    leaf = experience.CHANGWON_EXPERIENCE_LEAVES[0]
    value = FakeExperience(leaf, 99, expired=True)
    list_soup = BeautifulSoup(_list_html(leaf, [value], 1), "lxml")
    rows, no_data, malformed = experience._parse_list_page(
        _target(), leaf, list_soup, page=1, source_url=leaf.url
    )
    assert no_data is False
    assert malformed == 0
    assert len(rows) == 1

    detail_soup = BeautifulSoup(
        _detail_html(value, reservation_tab_for_closed=True), "lxml"
    )
    errors = experience._enrich_detail(
        rows[0], leaf, detail_soup, experience.date(2099, 7, 20)
    )

    assert errors == []
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["application_url"] == ""
    assert rows[0]["reservation_available"] is False
    assert rows[0]["application_type"] == "INFORMATION_ONLY"
    assert rows[0]["raw_fields"]["reservation_tab_present"] is True


def test_unpaired_reservation_tab_still_fails_closed() -> None:
    leaf = experience.CHANGWON_EXPERIENCE_LEAVES[0]
    value = FakeExperience(leaf, 100, expired=True)
    list_soup = BeautifulSoup(_list_html(leaf, [value], 1), "lxml")
    rows, _no_data, malformed = experience._parse_list_page(
        _target(), leaf, list_soup, page=1, source_url=leaf.url
    )
    assert malformed == 0

    detail_soup = BeautifulSoup(
        _detail_html(value, reservation_tab_for_closed=True), "lxml"
    )
    detail_soup.select_one("#tabs1pane4").decompose()
    errors = experience._enrich_detail(
        rows[0], leaf, detail_soup, experience.date(2099, 7, 20)
    )

    assert errors == ["detail EX100100: ambiguous reservation tab contract"]


def test_exact_target_and_23_leaf_registry() -> None:
    assert experience.is_changwon_experience_target(_target()) is True
    assert experience.is_changwon_experience_target(
        _target(url=experience.CHANGWON_EXPERIENCE_CANONICAL_URL + "?cpage=1")
    ) is False
    assert experience.is_changwon_experience_target(
        _target(provider="MUNI_WWW_CHANGWON_GO_KR_58EAB232")
    ) is False
    assert len(experience.CHANGWON_EXPERIENCE_LEAVES) == 23
    assert len({leaf.code for leaf in experience.CHANGWON_EXPERIENCE_LEAVES}) == 23
    assert len({leaf.path for leaf in experience.CHANGWON_EXPERIENCE_LEAVES}) == 23
    burim = next(
        leaf for leaf in experience.CHANGWON_EXPERIENCE_LEAVES
        if leaf.code == "burim_craft"
    )
    assert burim.pagination_fcd == "F051"
    assert experience.changwon_experience_list_url(burim, 2).endswith(
        "?fcd=F051&cpage=2"
    )


def test_burim_pagination_requires_its_fixed_official_fcd() -> None:
    burim = next(
        leaf for leaf in experience.CHANGWON_EXPERIENCE_LEAVES
        if leaf.code == "burim_craft"
    )
    values = [FakeExperience(burim, sequence) for sequence in range(1, 13)]
    official = BeautifulSoup(
        _list_html(burim, values, 1),
        "lxml",
    )
    drifted = BeautifulSoup(
        _list_html(burim, values, 1, pagination_fcd="F999"),
        "lxml",
    )

    assert experience._page_contract(official, burim) == (2, 1)
    assert experience._page_contract(drifted, burim) == (0, 0)


def test_burim_fixed_fcd_keeps_clamped_last_page_proof() -> None:
    burim = next(
        leaf for leaf in experience.CHANGWON_EXPERIENCE_LEAVES
        if leaf.code == "burim_craft"
    )
    values = [FakeExperience(burim, sequence) for sequence in range(1, 13)]
    final_url = experience.changwon_experience_list_url(burim, 2)
    clamp_url = experience.changwon_experience_list_url(burim, 3)
    final_soup = BeautifulSoup(_list_html(burim, values, 2), "lxml")
    clamp_soup = BeautifulSoup(_list_html(burim, values, 3), "lxml")

    assert final_url.endswith("?fcd=F051&cpage=2")
    assert clamp_url.endswith("?fcd=F051&cpage=3")
    assert experience._page_contract(final_soup, burim) == (2, 2)
    assert experience._page_contract(clamp_soup, burim) == (2, 2)
    final_rows, final_no_data, final_malformed = experience._parse_list_page(
        _target(), burim, final_soup, page=2, source_url=final_url
    )
    clamp_rows, clamp_no_data, clamp_malformed = experience._parse_list_page(
        _target(), burim, clamp_soup, page=3, source_url=clamp_url
    )
    assert final_malformed == clamp_malformed == 0
    assert experience._page_signature(
        final_rows, final_no_data
    ) == experience._page_signature(clamp_rows, clamp_no_data)


def test_education_and_experience_are_both_owned_by_municipal_aggregate() -> None:
    provider = education.CHANGWON_PROVIDER
    selected = [
        target
        for target in municipal.load_municipal_targets(scheduled_providers=set())
        if target["provider"] == provider
    ]

    assert {
        target["url"] for target in selected
    } == {
        education.CHANGWON_CANONICAL_URL,
        experience.CHANGWON_EXPERIENCE_CANONICAL_URL,
    }
    assert {
        (target["url"], target["service_group"], target["domain_category"])
        for target in selected
    } == {
        (education.CHANGWON_CANONICAL_URL, "공공강좌", "교육·강좌"),
        (experience.CHANGWON_EXPERIENCE_CANONICAL_URL, "체험", "체험·견학"),
    }
    assert provider in experience_aggregate.aggregate_owned_provider_names()
    assert provider not in experience_aggregate.experience_provider_names(
        scheduled_providers=set()
    )


def test_caps_never_publish_partial_snapshot() -> None:
    site = FakeSite()
    rows, _parser, meta = experience.collect_changwon_experience_courses(
        _target(),
        max_pages=46,
        detail_limit=100,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2099-07-20",
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "46 of 47" in meta["configured_collection_error"]

    site = FakeSite()
    rows, _parser, meta = experience.collect_changwon_experience_courses(
        _target(),
        max_pages=100,
        detail_limit=11,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2099-07-20",
    )
    assert rows == []
    assert meta["detail_attempts"] == 0
    assert meta["source_cap_reached"] is True
    assert "11 of 12" in meta["configured_collection_error"]


def test_host_pacer_serializes_requests_with_injected_clock() -> None:
    clock = [100.0]
    sleeps: list[float] = []
    calls: list[str] = []

    def monotonic() -> float:
        return clock[0]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    wrapped = education.changwon_paced_fetcher(
        lambda _session, url, _timeout: calls.append(url),
        delay_seconds=10,
        pacer=education.ChangwonHostPacer(),
        monotonic_fn=monotonic,
        sleep_fn=sleeper,
    )
    wrapped(None, "https://www.changwon.go.kr/one", 10)
    wrapped(None, "https://www.changwon.go.kr/two", 10)
    wrapped(None, "https://www.changwon.go.kr/three", 10)

    assert calls == [
        "https://www.changwon.go.kr/one",
        "https://www.changwon.go.kr/two",
        "https://www.changwon.go.kr/three",
    ]
    assert sleeps == [10.0, 10.0]
