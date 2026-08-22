from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from Crawler import Crawler_EducationExperience as experience_aggregate
from Crawler import Crawler_GeneratedYamlTargets as generated_targets
from Crawler import Crawler_MunicipalIntegratedReservation as municipal_aggregate
from Crawler.Crawler_MunicipalYaml import MunicipalDbWriter
from Crawler import municipal_insiseol as insiseol


ROOT = Path(__file__).resolve().parents[1]


def _education_target(**updates: str) -> dict[str, str]:
    value = {
        "provider": insiseol.INSISEOL_PROVIDER,
        "url": insiseol.INSISEOL_EDUCATION_CANONICAL_URL,
        "name": "인천시설공단 공개 수강",
        "branch": "인천광역시",
    }
    value.update(updates)
    return value


def _experience_target(**updates: str) -> dict[str, str]:
    value = {
        "provider": insiseol.INSISEOL_PROVIDER,
        "url": insiseol.INSISEOL_EXPERIENCE_CANONICAL_URL,
        "name": "인천시설공단 공개 체험",
        "branch": "인천광역시",
    }
    value.update(updates)
    return value


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _pairs(values: dict[str, str]) -> str:
    return "".join(f"<dl><dt>{key}</dt><dd>{value}</dd></dl>" for key, value in values.items())


@dataclass(frozen=True)
class FakeCourse:
    partition: insiseol.InsiseolCoursePartition
    identity: str
    open: bool = False

    @property
    def title(self) -> str:
        return f"공식 공개 강좌 {self.identity}"

    @property
    def status(self) -> str:
        return "정시접수중" if self.open else "접수마감"


def _course_card(value: FakeCourse, *, pgno: int = 1) -> str:
    query = [("prgm_seq", value.identity)]
    if value.partition.cate2:
        query.append(("cate2", value.partition.cate2))
    query.append(("prgmdiv", value.partition.prgmdiv))
    query.append(("pgno", str(pgno)))
    from urllib.parse import urlencode

    href = "/program/programInfoDetail.do?" + urlencode(query)
    fields = {
        "정시접수": "07.01 10:00~08.31 18:00",
        "교육분야": value.partition.name,
        "교육일정": "2099.08.01 ~ 2099.12.31",
        "교육시간": "토 10:00~12:00",
        "수 강 료": "무료",
        "주야구분": "오전",
        "수강정원": "20 명",
        "접수구분": "선착순",
    }
    lis = "".join(f'<li><span class="wfont">{key} :</span>{value}</li>' for key, value in fields.items())
    return (
        "<li><div>"
        f'<p class="tit"><a href="{href}">{value.title}</a></p>'
        f'<p class="tag_state">{value.status}</p>'
        f'<ul class="lec_info">{lis}</ul>'
        "</div></li>"
    )


def _course_list(values: list[FakeCourse], *, pgno: int = 1, mutate: bool = False) -> str:
    cards = "".join(_course_card(value, pgno=pgno) for value in values)
    if mutate:
        cards = cards.replace(values[0].title, values[0].title + " 변경", 1)
    return (
        '<div class="search_array"><span class="written">'
        f"전체 {len(values)}건, 현재페이지 1/1"
        "</span></div>"
        f'<div class="board_list"><ul class="lecList lecH">{cards}</ul></div>'
    )


def _course_detail(value: FakeCourse, *, mismatch: bool = False) -> str:
    pairs = {
        "교육기관": value.partition.institution,
        "분야": value.partition.name,
        "정시 접수": "2099.07.01 10시 00분 ~ 2099.08.31 18시 00분",
        "교육 대상": "인천시민",
        "교육기간": "2099-08-01 ~ 2099-12-31" + (" 변경" if mismatch else ""),
        "교육 요일": "토",
        "교육 시간": "10:00~12:00",
        "수강료": "무료",
        "재료비": "",
        "정원": "3 / 20 명",
        "대기": "0 / 5 명",
        "강의실": value.partition.institution + " 강의실",
        "문의처": "032-000-0000",
    }
    control = (
        "<a class='btn btn_ok' href='#' "
        "onclick=\"alert('로그인을 하신 후에 이용 가능합니다'); return false;\">수강신청</a>"
        if value.open
        else ""
    )
    return (
        '<div id="detail_con"><div class="title"><div>'
        f'<span class="tag_state">{value.status}</span><span class="tag_state">교육전</span>'
        f'</div><p class="margin_t10">{value.title}</p></div>'
        f'<div class="board_view">{_pairs(pairs)}</div>{control}</div>'
    )


class FakeEducationSite:
    def __init__(self, *, mismatch_identity: str = "", mutate_repeat: bool = False) -> None:
        self.courses: dict[str, list[FakeCourse]] = {}
        self.by_id: dict[str, FakeCourse] = {}
        for index, partition in enumerate(insiseol.INSISEOL_COURSE_PARTITIONS, start=1):
            values = [
                FakeCourse(partition, str(index * 100 + 1), open=index == 1),
                FakeCourse(partition, str(index * 100 + 2)),
            ]
            self.courses[partition.code] = values
            self.by_id.update({value.identity: value for value in values})
        self.mismatch_identity = mismatch_identity
        self.mutate_repeat = mutate_repeat
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
        assert parsed.path not in {
            "/login/newresSSO.do",
            "/program/programInfoFileDownload.do",
        }
        query = parse_qs(parsed.query)
        if parsed.path.endswith("programInfoDetail.do"):
            value = self.by_id[query["prgm_seq"][0]]
            return _course_detail(value, mismatch=value.identity == self.mismatch_identity)
        partition = next(
            item
            for item in insiseol.INSISEOL_COURSE_PARTITIONS
            if query.get("prgmdiv") == [item.prgmdiv] and (not item.cate2 or query.get("cate2") == [item.cate2])
        )
        page = int((query.get("pgno") or ["1"])[0])
        return _course_list(
            self.courses[partition.code],
            pgno=page,
            mutate=self.mutate_repeat and page == 2,
        )


def _collect_education(site: FakeEducationSite, **kwargs: Any):
    return insiseol.collect_insiseol_education_courses(
        _education_target(),
        timeout=10,
        max_pages=20,
        detail_limit=20,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        dedupe_rows=lambda rows: rows,
        today="2099-07-20",
        **kwargs,
    )


EXPERIENCE_VALUES = {
    "songdopark": [
        ("23", "RC 가족", "032-1", "0원", "20명"),
        ("22", "RC 동호인", "032-1", "0원", "20명"),
        ("6", "생태체험", "032-2", "0원", "20명"),
    ],
    "seaside": [
        ("2", "염전 단체", "032-3", "0원", "30명"),
        ("19", "염전 개인", "032-3", "0원", "30명"),
        ("3", "숲 단체", "032-3", "0원", "35명"),
        ("18", "숲 개인", "032-3", "0원", "15명"),
    ],
    "childsee": [("22", "4D 캐롤", "032-4", "3,000원", "72명"), ("21", "4D 마법여행", "032-4", "3,000원", "72명")],
    "chongnapark": [("21", "청라 숲 개인", "032-5", "0원", "30명"), ("8", "청라 숲 단체", "032-5", "0원", "2팀")],
}


def _experience_list(
    leaf: insiseol.InsiseolExperienceLeaf,
    *,
    page: int,
    mutate: bool = False,
) -> str:
    rows = []
    values = EXPERIENCE_VALUES[leaf.code]
    for number, (identity, title, phone, fee, capacity) in enumerate(values, start=1):
        path = (
            f"/childsee/childSeeInfoDetail.do?see_seq={identity}"
            if leaf.code == "childsee"
            else f"/see/seeInfoDetail.do?see_seq={identity}&inst_cd={leaf.inst_cd}"
        )
        if page == 2:
            path += "&pgno=2"
        shown_title = title + (" 변경" if mutate and number == 1 else "")
        rows.append(
            "<tr>"
            f"<td>{number}</td><td class='title'><a href='{path}'>{shown_title}</a></td>"
            f"<td>{phone}</td><td>{fee}</td><td>{capacity}</td><td>0명</td>"
            "</tr>"
        )
    return (
        f'<span class="written">전체 {len(values)}건, 현재페이지 1/21</span>'
        '<div class="board_list"><table class="general_board"><tbody>' + "".join(rows) + "</tbody></table></div>"
    )


def _experience_detail(
    leaf: insiseol.InsiseolExperienceLeaf,
    identity: str,
    *,
    unsafe: bool = False,
) -> str:
    title, phone, fee, capacity = next(
        (title, phone, fee, capacity)
        for value_id, title, phone, fee, capacity in EXPERIENCE_VALUES[leaf.code]
        if value_id == identity
    )
    info = "공식 체험 안내"
    if leaf.code == "songdopark" and identity == "6":
        info = "운영기간: 2099년 4월~12월 공식 체험 안내"
    elif leaf.code == "seaside" and identity in {"2", "19"}:
        info = "운영기간 6월 1일~9월 30일 공식 체험 안내"
    elif leaf.code == "seaside":
        info = "운영기간 4월 1일 ~ 5월 29일, 10월 1일 ~ 11월 20일 공식 체험 안내"
    elif leaf.code == "chongnapark":
        info = "운영기간 : 2099년 4월 6일 ~ 11월 20일 공식 체험 안내"
    pairs = {
        "관람 인원": capacity,
        "문의처": phone,
        "기본 요금": "무료" if fee == "0원" else fee,
        "관람시간": "10:00 ~ 11:30",
        "이용안내": info,
    }
    if leaf.code == "childsee":
        pairs["예약 안내"] = (
            '<a class="conbtn" href="/childsee/childSeeScheduleMonth.do?see_seq=1">상설전시관 예약하러 가기</a>'
            '<a class="conbtn" href="/mypage/see.jsp">마이페이지 관람 이력현황</a>'
        )
        title_html = f'<div class="title">{title}<span class="tag_state">신청 가능</span></div>'
        control = ""
    else:
        title_html = f'<div class="title">{title}</div>'
        if unsafe:
            control = '<a class="btn btn_ok" href="https://evil.example/apply">신청 하기</a>'
        elif leaf.code == "seaside":
            control = '<a class="btn btn_ok" href="#" onclick="fnSeaside2Alim(); return false;">신청 하기</a>'
        else:
            control = f'<a class="btn btn_ok" href="/see/seeScheduleMonth.do?see_seq={identity}">신청 하기</a>'
    return f'<div id="detail_con"><div class="board_view">{title_html}{_pairs(pairs)}{control}</div></div>'


class FakeExperienceSite:
    def __init__(self, *, mutate_repeat: bool = False, unsafe_identity: str = "") -> None:
        self.mutate_repeat = mutate_repeat
        self.unsafe_identity = unsafe_identity
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
        assert "Schedule" not in parsed.path
        assert not parsed.path.startswith(("/login", "/mypage"))
        query = parse_qs(parsed.query)
        if parsed.path.endswith("InfoDetail.do"):
            leaf = (
                insiseol.INSISEOL_EXPERIENCE_LEAF_BY_CODE["childsee"]
                if parsed.path.startswith("/childsee/")
                else next(item for item in insiseol.INSISEOL_EXPERIENCE_LEAVES if item.inst_cd == query["inst_cd"][0])
            )
            identity = query["see_seq"][0]
            return _experience_detail(
                leaf,
                identity,
                unsafe=identity == self.unsafe_identity,
            )
        leaf = (
            insiseol.INSISEOL_EXPERIENCE_LEAF_BY_CODE["childsee"]
            if parsed.path.startswith("/childsee/")
            else next(item for item in insiseol.INSISEOL_EXPERIENCE_LEAVES if item.inst_cd == query["inst_cd"][0])
        )
        page = int((query.get("pgno") or ["1"])[0])
        return _experience_list(
            leaf,
            page=page,
            mutate=self.mutate_repeat and page == 2 and leaf.code == "songdopark",
        )


def _collect_experience(site: FakeExperienceSite, **kwargs: Any):
    return insiseol.collect_insiseol_experience_courses(
        _experience_target(),
        timeout=10,
        max_pages=20,
        detail_limit=20,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        dedupe_rows=lambda rows: rows,
        today="2099-07-20",
        **kwargs,
    )


def test_exact_sibling_targets_and_fixed_public_fanouts() -> None:
    assert insiseol.is_insiseol_education_target(_education_target()) is True
    assert insiseol.is_insiseol_experience_target(_experience_target()) is True
    assert (
        insiseol.is_insiseol_education_target(_education_target(url=insiseol.INSISEOL_EDUCATION_CANONICAL_URL + "?x=1"))
        is False
    )
    assert insiseol.is_insiseol_experience_target(_experience_target(provider="INCHEON_RESERVATION")) is False
    assert len(insiseol.INSISEOL_COURSE_PARTITIONS) == 3
    assert len(insiseol.INSISEOL_EXPERIENCE_LEAVES) == 4
    assert len(insiseol.INSISEOL_EXPERIENCE_ITEM_REGIONS) == 11


def test_education_complete_snapshot_and_item_bound_login_control() -> None:
    site = FakeEducationSite()
    rows, parser, meta = _collect_education(site)

    assert parser == insiseol.INSISEOL_EDUCATION_PARSER
    assert len(rows) == 6
    assert meta["source_total"] == 6
    assert meta["required_list_requests"] == 9
    assert meta["pages"] == 9
    assert meta["detail_pages"] == 6
    assert meta["snapshot_complete"] is True
    assert meta["application_open_count"] == 1
    assert {row["service_group"] for row in rows} == {"공공강좌"}
    assert {row["domain_category"] for row in rows} == {"교육·강좌"}
    assert {row["municipality_code"] for row in rows} == {"2820000000", "2824500000"}
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["application_url"] == open_row["raw_url"]
    assert open_row["reservation_available"] is True
    assert not any("login" in url.lower() for url in site.calls)
    assert all(session.closed for session in site.sessions)


def test_education_detail_and_clamp_drift_fail_whole_snapshot_closed() -> None:
    site = FakeEducationSite(mismatch_identity="202")
    rows, _parser, meta = _collect_education(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "education period mismatch" in meta["configured_collection_error"]

    site = FakeEducationSite(mutate_repeat=True)
    rows, _parser, meta = _collect_education(site)
    assert rows == []
    assert meta["detail_attempts"] == 0
    assert "does not repeat final signature" in meta["configured_collection_error"]


def test_education_caps_cannot_publish_partial_snapshot() -> None:
    site = FakeEducationSite()
    rows, _parser, meta = insiseol.collect_insiseol_education_courses(
        _education_target(),
        max_pages=8,
        detail_limit=20,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2099-07-20",
    )
    assert rows == []
    assert meta["source_cap_reached"] is True

    site = FakeEducationSite()
    rows, _parser, meta = insiseol.collect_insiseol_education_courses(
        _education_target(),
        max_pages=20,
        detail_limit=5,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2099-07-20",
    )
    assert rows == []
    assert meta["detail_attempts"] == 0
    assert meta["source_cap_reached"] is True


def test_experience_complete_snapshot_region_split_and_no_schedule_calls() -> None:
    site = FakeExperienceSite()
    rows, parser, meta = _collect_experience(site)

    assert parser == insiseol.INSISEOL_EXPERIENCE_PARSER
    assert len(rows) == 11
    assert meta["source_total"] == 11
    assert meta["pages"] == 12
    assert meta["detail_pages"] == 11
    assert meta["snapshot_complete"] is True
    assert meta["application_open_count"] == 5
    assert meta["prerequisite_count"] == 2
    assert meta["temporarily_disabled_count"] == 4
    assert meta["current_municipality_counts"] == {
        "인천광역시 계양구": 2,
        "인천광역시 서해구": 4,
        "인천광역시 연수구": 1,
        "인천광역시 영종구": 4,
    }
    assert {row["service_group"] for row in rows} == {"체험"}
    assert {row["domain_category"] for row in rows} == {"체험·견학"}
    assert meta["calendar_requests"] == meta["application_requests"] == meta["auth_requests"] == 0
    assert not any("Schedule" in url for url in site.calls)
    assert all(session.closed for session in site.sessions)


def test_locked_target_metadata_keeps_item_regions_through_branch_promotion() -> None:
    public = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    configured = {
        target["url"]: target
        for target in public
        if target.get("provider") == insiseol.INSISEOL_PROVIDER
    }
    education_rows, _parser, _meta = _collect_education(FakeEducationSite())
    experience_rows, _parser, _meta = _collect_experience(FakeExperienceSite())
    writer = MunicipalDbWriter(insiseol.INSISEOL_PROVIDER)

    def branch_region_counts(rows: list[dict[str, Any]], url: str) -> Counter[tuple[str, str]]:
        target = generated_targets.to_crawl_target(configured[url])
        generated_targets.apply_target_metadata(rows, target)
        counts: Counter[tuple[str, str]] = Counter()
        for row in rows:
            writer.normalize_branch_split_row(row)
            branch = writer.branch_info_from_row(row)
            counts[(branch["region_sido"], branch["region_sigungu"])] += 1
        return counts

    assert branch_region_counts(
        education_rows,
        insiseol.INSISEOL_EDUCATION_CANONICAL_URL,
    ) == Counter(
        {
            ("인천광역시", "계양구"): 2,
            ("인천광역시", "남동구"): 4,
        }
    )
    assert branch_region_counts(
        experience_rows,
        insiseol.INSISEOL_EXPERIENCE_CANONICAL_URL,
    ) == Counter(
        {
            ("인천광역시", "서해구"): 4,
            ("인천광역시", "연수구"): 1,
            ("인천광역시", "영종구"): 4,
            ("인천광역시", "계양구"): 2,
        }
    )


def test_experience_repeat_or_unsafe_control_fails_whole_snapshot_closed() -> None:
    site = FakeExperienceSite(mutate_repeat=True)
    rows, _parser, meta = _collect_experience(site)
    assert rows == []
    assert meta["detail_attempts"] == 0
    assert "pgno=2" in meta["configured_collection_error"]

    site = FakeExperienceSite(unsafe_identity="23")
    rows, _parser, meta = _collect_experience(site)
    assert rows == []
    assert meta["detail_errors"] == 1
    assert "unsafe application control" in meta["configured_collection_error"]


def test_host_pacer_is_shared_and_deterministic() -> None:
    clock = [10.0]
    sleeps: list[float] = []
    calls: list[str] = []

    def monotonic() -> float:
        return clock[0]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    wrapped = insiseol.insiseol_paced_fetcher(
        lambda _session, url, _timeout: calls.append(url),
        delay_seconds=0.25,
        pacer=insiseol.InsiseolHostPacer(),
        monotonic_fn=monotonic,
        sleep_fn=sleeper,
    )
    wrapped(None, "https://reserve.insiseol.or.kr/one", 10)
    wrapped(None, "https://reserve.insiseol.or.kr/two", 10)
    wrapped(None, "https://reserve.insiseol.or.kr/three", 10)

    assert len(calls) == 3
    assert sleeps == [0.25, 0.25]


def test_locked_siblings_are_owned_once_by_municipal_aggregate() -> None:
    public = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "public_reservation.yaml").read_text(encoding="utf-8")
    )["targets"]
    configured = [target for target in public if target.get("provider") == insiseol.INSISEOL_PROVIDER]
    assert {
        (
            target["url"],
            target["service_group"],
            target["domain_category"],
            target["service_group_policy"],
        )
        for target in configured
    } == {
        (
            insiseol.INSISEOL_EDUCATION_CANONICAL_URL,
            "공공강좌",
            "교육·강좌",
            "locked",
        ),
        (
            insiseol.INSISEOL_EXPERIENCE_CANONICAL_URL,
            "체험",
            "체험·견학",
            "locked",
        ),
    }
    assert configured[0]["municipality_code"] == "2820000000"
    assert configured[1]["municipality_code"] == "2818500000"
    assert not any(
        municipality["code"] == "2800000000"
        for target in configured
        for municipality in target["covered_municipalities"]
    )

    selected = [
        target
        for target in municipal_aggregate.load_municipal_targets(scheduled_providers=set())
        if target["provider"] == insiseol.INSISEOL_PROVIDER
    ]
    assert {target["url"] for target in selected} == {
        insiseol.INSISEOL_EDUCATION_CANONICAL_URL,
        insiseol.INSISEOL_EXPERIENCE_CANONICAL_URL,
    }
    assert insiseol.INSISEOL_PROVIDER in experience_aggregate.aggregate_owned_provider_names()
    assert insiseol.INSISEOL_PROVIDER not in experience_aggregate.experience_provider_names(scheduled_providers=set())
    arguments = generated_targets.parse_args(
        generated_targets.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[insiseol.INSISEOL_PROVIDER]
    )
    assert arguments.save_db is True
    assert arguments.mark_stale is True
    assert arguments.allow_partial_save is False
    assert arguments.per_target_limit == 0
    assert arguments.max_pages == 100
    assert arguments.detail_limit == 500
