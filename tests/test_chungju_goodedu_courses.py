from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import threading

import pytest
import yaml

from Crawler import municipal_chungju_goodedu as chungju


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Target:
    provider: str = chungju.CHUNGJU_GOODEDU_PROVIDER
    url: str = chungju.CHUNGJU_GOODEDU_URL


class FakeResponse:
    def __init__(self, body: str, status_code: int = 200) -> None:
        self.content = body.encode("utf-8")
        self.status_code = status_code
        self.history: list[object] = []


class FakeSession:
    def __init__(
        self,
        responses: dict[str, str | tuple[int, str]],
        calls: list[str],
        lock: threading.Lock,
    ) -> None:
        self.responses = responses
        self.calls = calls
        self.lock = lock

    def get(self, url: str, **_kwargs) -> FakeResponse:
        with self.lock:
            self.calls.append(url)
        value = self.responses.get(url)
        if value is None:
            return FakeResponse("missing", 404)
        if isinstance(value, tuple):
            return FakeResponse(value[1], value[0])
        return FakeResponse(value)

    def close(self) -> None:
        return None


def factory_for(responses: dict[str, str | tuple[int, str]]):
    calls: list[str] = []
    lock = threading.Lock()

    def factory() -> FakeSession:
        return FakeSession(responses, calls, lock)

    return factory, calls


def form_html() -> str:
    return f"""
      <form id="searchVO" name="searchForm" method="get"
            action="{chungju.CHUNGJU_GOODEDU_LIST_PATH}?key=63">
        <input name="key" value="63">
        <input name="searchGroupNo" value="">
        <select name="searchInsttNo"><option selected value="">전체</option></select>
        <select name="searchCtgryNo"><option selected value="">전체</option></select>
        <select name="searchProgrsSttus"><option selected value="">전체</option></select>
        <input name="searchLctreNm" value="">
      </form>
    """


def row_html(
    identity: str,
    sequence: int,
    *,
    title: str,
    status: str,
    apply_period: tuple[str, str],
    education_period: tuple[str, str],
    institution: str = "충주시 평생학습관",
    fee: str = "무료",
    selection: str = "추첨제",
) -> str:
    application = ""
    if status == "접수중":
        application = (
            '<a href="./addEdcLctreReqestView.do?'
            f'edcLctreNo={identity}&amp;key=63">수강신청</a>'
        )
    return f"""
      <tr>
        <td>{sequence}</td>
        <td class="text_left">
          <a href="./selectEdcLctreView.do?edcLctreNo={identity}&amp;key=63">
            {title}<br>(매주 화)<br>10:00 ~ 12:00
          </a>
        </td>
        <td>{selection}</td>
        <td>{institution}<br>{fee}</td>
        <td>접수 : {apply_period[0]} ~ {apply_period[1]}<br>
            교육 : {education_period[0]} ~ {education_period[1]}</td>
        <td><span class="btnB"><span>{status}</span></span>{application}</td>
      </tr>
    """


def list_html(
    total: int,
    displayed_page: int,
    advertised_last: int,
    rows: list[str],
) -> str:
    body = "".join(rows) or (
        '<tr><td colspan="6">등록된 교육강좌가 없습니다.</td></tr>'
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
      {form_html()}
      <div class="category">
        <span class="board_dot">총 게시물 {total} 개</span>
        <span class="board_dot">페이지 {displayed_page} / {advertised_last}</span>
      </div>
      <table class="bbs_default_list">
        <caption>강좌안내 목록</caption>
        <thead><tr>
          <th>번호</th><th>강좌명/교육시간</th><th>선발방식</th>
          <th>교육기관/수강료</th><th>신청/교육기간</th><th>상태</th>
        </tr></thead>
        <tbody>{body}</tbody>
      </table>
    </body></html>"""


def detail_html(
    title: str,
    *,
    apply_period: tuple[str, str],
    education_period: tuple[str, str],
    institution: str = "충주시 평생학습관",
    fee: str = "무료",
    selection: str = "추첨제",
    include_institution: bool = True,
) -> str:
    institution_table = ""
    if include_institution:
        institution_table = f"""
          <table class="bbs_view"><caption>기관 상세정보</caption><tbody>
            <tr><th scope="row">기관명</th><td>{institution}</td></tr>
          </tbody></table>
        """
    return f"""<!doctype html><html><head><meta charset="utf-8">
      <title>{title} - 강좌정보(상세)</title></head><body>
      <table class="bbs_view"><caption>강좌 상세보기</caption><tbody>
        <tr><th scope="row">강좌명</th><td>{title}</td>
            <th scope="row">교육대상</th><td>충주시민</td></tr>
        <tr><th scope="row">접수기간</th>
            <td>{apply_period[0]} ~ {apply_period[1]}</td>
            <th scope="row">교육기간</th>
            <td>{education_period[0]} ~ {education_period[1]} (매주 화)</td></tr>
        <tr><th scope="row">교육시간</th><td>10:00 ~ 12:00</td>
            <th scope="row">접수방식</th><td>온라인접수</td></tr>
        <tr><th scope="row">선발방식</th><td>{selection}</td>
            <th scope="row">교육장소</th><td>학습실</td></tr>
        <tr><th scope="row">추첨인원</th><td>20명</td>
            <th scope="row">신청인원</th><td>7명</td></tr>
        <tr><th scope="row">수강료</th><td>{fee}</td></tr>
        <tr><th scope="row">강의개요</th><td colspan="3">상세 강의 소개</td></tr>
        <tr><th scope="row">교재 및 참고자료</th><td colspan="3"></td></tr>
        <tr><th scope="row">참고사항</th><td colspan="3"></td></tr>
      </tbody></table>
      <table class="bbs_view"><caption>강사 상세정보</caption><tbody>
        <tr><th scope="row">강사명</th><td>홍길동</td></tr>
      </tbody></table>
      {institution_table}
    </body></html>"""


def valid_responses() -> dict[str, str | tuple[int, str]]:
    first = row_html(
        "301",
        3,
        title="현재 공개 강좌",
        status="접수중",
        apply_period=("2026.07.10", "2026.07.22"),
        education_period=("2026.07.20", "2026.08.20"),
    )
    second = row_html(
        "302",
        2,
        title="향후 공개 강좌",
        status="접수대기",
        apply_period=("2026.08.01", "2026.08.10"),
        education_period=("2026.08.20", "2026.09.20"),
        institution="서충주도서관",
        fee="20,000원",
        selection="선착순",
    )
    expired = row_html(
        "299",
        1,
        title="종료 강좌",
        status="교육종료",
        apply_period=("2026.06.01", "2026.06.10"),
        education_period=("2026.06.20", "2026.07.19"),
    )
    responses: dict[str, str | tuple[int, str]] = {
        chungju.chungju_goodedu_list_url(1): list_html(3, 1, 1, [first, second, expired]),
        chungju.chungju_goodedu_list_url(2): list_html(3, 2, 1, []),
        chungju.chungju_goodedu_detail_url("301"): detail_html(
            "현재 공개 강좌",
            apply_period=("2026.07.10", "2026.07.22"),
            education_period=("2026.07.20", "2026.08.20"),
        ),
        chungju.chungju_goodedu_detail_url("302"): detail_html(
            "향후 공개 강좌",
            apply_period=("2026.08.01", "2026.08.10"),
            education_period=("2026.08.20", "2026.09.20"),
            institution="서충주도서관",
            fee="20,000원",
            selection="선착순",
            include_institution=False,
        ),
    }
    return responses


def collect(
    responses: dict[str, str | tuple[int, str]],
    **kwargs,
):
    session_factory, calls = factory_for(responses)
    result = chungju.collect_chungju_goodedu_courses(
        Target(),
        timeout=1,
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 2),
        today=kwargs.pop("today", date(2026, 7, 20)),
        max_workers=2,
        session_factory=session_factory,
        **kwargs,
    )
    return (*result, calls)


def test_target_and_url_contract_rejects_subset_or_filtered_routes() -> None:
    assert chungju.is_chungju_goodedu_target(Target()) is True
    assert chungju.is_chungju_goodedu_target(
        Target(url=chungju.CHUNGJU_GOODEDU_SUBSET_URL)
    ) is False
    assert chungju.is_chungju_goodedu_target(
        Target(url=chungju.CHUNGJU_GOODEDU_URL + "&searchInsttNo=1")
    ) is False
    assert chungju.is_chungju_goodedu_target(
        Target(provider=chungju.CHUNGJU_GOODEDU_SUBSET_PROVIDER)
    ) is False
    assert chungju.chungju_goodedu_list_url(6).endswith(
        "key=63&pageUnit=1000&pageIndex=6"
    )
    assert chungju.chungju_goodedu_detail_url("5939").endswith(
        "edcLctreNo=5939&key=63"
    )


@pytest.mark.parametrize(
    ("institution", "title", "venue", "location_key"),
    (
        (
            chungju.CHUNGJU_GOODEDU_REGULAR_INSTITUTION,
            "생활영어(본관)",
            "2-1강의실",
            "main",
        ),
        (
            chungju.CHUNGJU_GOODEDU_REGULAR_INSTITUTION,
            "생활영어(연수동분관)",
            "연수동 분관(나눔터)",
            "yeonsu",
        ),
        (
            chungju.CHUNGJU_GOODEDU_REGULAR_INSTITUTION,
            "요가(호암직동분관)",
            "호암직동 분관(배움터)",
            "hoam",
        ),
        (
            chungju.CHUNGJU_GOODEDU_REGULAR_INSTITUTION,
            "생활 일본어(서충주분관)",
            "서충주분관(배움터)",
            "seochungju",
        ),
        (
            chungju.CHUNGJU_GOODEDU_REGULAR_INSTITUTION,
            "홈베이킹(금릉동분관)",
            "금릉동분관(제빵실)",
            "geumneung",
        ),
        (
            chungju.CHUNGJU_GOODEDU_REGULAR_INSTITUTION,
            "생활택견교실(국제무예센터)",
            "외부강의(국제무예센터)",
            "international_martial_arts",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "VR 체험",
            "달천동 행정복지센터 4층 VR실",
            "dalcheon_admin",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "영상 만들기",
            "중앙탑면 서충주생활문화센터 2층 마루연습실",
            "seochungju_living_culture",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "인문학",
            "서충주도서관",
            "seochungju_library",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "예술 강좌",
            "호암예술관",
            "hoam_arts",
        ),
        (
            "건국대학교 글로컬캠퍼스 부설 평생교육원",
            "골프 아카데미",
            "건국 운동장(KU스타디움)",
            "konkuk_lifelong",
        ),
        (
            "한국교통대학교 부설 평생교육원",
            "시민 강좌",
            "지정강의실",
            "ut_lifelong",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "디지털 캘리그라피",
            "목행동평생학습센터(글로리북카페)",
            "mokhaeng_glory_book",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "원예교육복지사",
            "지현문화플랫폼 4층",
            "jihyeon_culture_platform",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "모던민화",
            "교현동 평생학습센터(충주생활문화센터)",
            "gyohyeon_living_culture",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "천아트",
            "엄정면 평생학습센터(엄정면꿈터도서관)",
            "eomjeong_dream_library",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "근력운동",
            "노은면평생학습센터(노은면어울림센터)",
            "noeun_community",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "붓펜 캘리그라피",
            "대소원면 엄마의 정원 마음치유센터",
            "daesowon_mom_garden",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "베이킹",
            "중앙탑면 서충주청소년문화의집 2층 유스키친",
            "seochungju_youth",
        ),
        (
            chungju.CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
            "붓펜 캘리그라피",
            "수안보면 작은도서관",
            "suanbo_small_library",
        ),
    ),
)
def test_known_education_locations_resolve_to_physical_branches(
    institution: str,
    title: str,
    venue: str,
    location_key: str,
) -> None:
    location = chungju.chungju_goodedu_location(institution, title, venue)

    assert location is not None
    assert location["key"] == location_key
    assert location["address"].startswith("충청북도 충주시 ")
    assert location["branch_code"].startswith("CHUNGJU_GOODEDU_BRANCH_")
    assert 36.0 < location["lat"] < 38.0
    assert 127.0 < location["lon"] < 129.0


def test_unknown_regular_location_is_not_assigned_to_a_room() -> None:
    assert (
        chungju.chungju_goodedu_location(
            chungju.CHUNGJU_GOODEDU_REGULAR_INSTITUTION,
            "새 강좌",
            "미확인 강의실",
        )
        is None
    )


def test_complete_pages_sentinel_and_current_details_are_required() -> None:
    rows, parser, meta, calls = collect(valid_responses())

    assert parser == chungju.CHUNGJU_GOODEDU_PARSER
    assert [row["title"] for row in rows] == ["현재 공개 강좌", "향후 공개 강좌"]
    assert rows[0]["capacity_total"] == 20
    assert rows[0]["capacity_current"] == 7
    assert rows[0]["target"] == "충주시민"
    assert rows[0]["instructor"] == "홍길동"
    assert rows[0]["application_url"] == chungju.chungju_goodedu_application_url("301")
    assert rows[1].get("application_url", "") == ""
    assert rows[1]["application_type"] == "INFO_ONLY"
    assert rows[1]["branch"] == "서충주도서관"
    assert meta["source_total"] == 3
    assert meta["source_rows"] == 3
    assert meta["expired_count"] == 1
    assert meta["current_count"] == meta["returned_count"] == 2
    assert meta["pages"] == meta["required_list_requests"] == 2
    assert meta["page_counts"] == {1: 3, 2: 0}
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert meta["configured_collection_error"] == ""
    assert meta["ownership_aliases"] == [chungju.CHUNGJU_GOODEDU_SUBSET_URL]
    call_counts = Counter(calls)
    assert call_counts[chungju.chungju_goodedu_list_url(1)] == 1
    assert call_counts[chungju.chungju_goodedu_list_url(2)] == 1
    assert call_counts[chungju.chungju_goodedu_detail_url("301")] == 1
    assert call_counts[chungju.chungju_goodedu_detail_url("302")] == 1
    assert chungju.chungju_goodedu_detail_url("299") not in call_counts


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "message"),
    (
        (1, 2, "max_pages cap"),
        (2, 1, "detail_limit cap"),
    ),
)
def test_collection_caps_fail_closed(
    max_pages: int,
    detail_limit: int,
    message: str,
) -> None:
    rows, _, meta, _ = collect(
        valid_responses(), max_pages=max_pages, detail_limit=detail_limit
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert message in meta["configured_collection_error"]


def test_nonempty_sentinel_fails_closed() -> None:
    responses = valid_responses()
    unexpected = row_html(
        "400",
        1,
        title="sentinel leak",
        status="접수대기",
        apply_period=("2026.08.01", "2026.08.10"),
        education_period=("2026.08.20", "2026.09.20"),
    )
    responses[chungju.chungju_goodedu_list_url(2)] = list_html(3, 2, 1, [unexpected])

    rows, _, meta, _ = collect(responses)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel page is not empty" in meta["configured_collection_error"]


def test_duplicate_source_identity_fails_closed() -> None:
    responses = valid_responses()
    duplicate = row_html(
        "301",
        2,
        title="중복 ID 강좌",
        status="접수대기",
        apply_period=("2026.08.01", "2026.08.10"),
        education_period=("2026.08.20", "2026.09.20"),
    )
    first = row_html(
        "301",
        3,
        title="현재 공개 강좌",
        status="접수중",
        apply_period=("2026.07.10", "2026.07.22"),
        education_period=("2026.07.20", "2026.08.20"),
    )
    expired = row_html(
        "299",
        1,
        title="종료 강좌",
        status="교육종료",
        apply_period=("2026.06.01", "2026.06.10"),
        education_period=("2026.06.20", "2026.07.19"),
    )
    responses[chungju.chungju_goodedu_list_url(1)] = list_html(
        3, 1, 1, [first, duplicate, expired]
    )

    rows, _, meta, _ = collect(responses)

    assert rows == []
    assert meta["duplicate_count"] == 1
    assert "duplicate source identities" in meta["configured_collection_error"]


def test_detail_list_mismatch_fails_closed() -> None:
    responses = valid_responses()
    responses[chungju.chungju_goodedu_detail_url("302")] = detail_html(
        "다른 제목",
        apply_period=("2026.08.01", "2026.08.10"),
        education_period=("2026.08.20", "2026.09.20"),
        institution="서충주도서관",
        fee="20,000원",
        selection="선착순",
        include_institution=False,
    )

    rows, _, meta, _ = collect(responses)

    assert rows == []
    assert meta["detail_pages"] == 1
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "detail/list title mismatch" in meta["configured_collection_error"]


def test_shared_router_dispatches_only_the_canonical_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as router

    captured: dict[str, object] = {}

    def fake_collect(target, **kwargs):
        captured["target"] = target
        captured.update(kwargs)
        return [{"title": "routed"}], chungju.CHUNGJU_GOODEDU_PARSER, {
            "snapshot_complete": True
        }

    monkeypatch.setattr(chungju, "collect_chungju_goodedu_courses", fake_collect)
    target = router.CrawlTarget(
        provider=chungju.CHUNGJU_GOODEDU_PROVIDER,
        name="충주시 평생학습관",
        branch="충청북도 충주시",
        url=chungju.CHUNGJU_GOODEDU_URL,
        source="test",
    )

    rows, parser, meta = router.collect_from_url(
        target,
        timeout=3,
        max_depth=0,
        max_pages=6,
        detail_limit=200,
    )

    assert rows == [{"title": "routed"}]
    assert parser == chungju.CHUNGJU_GOODEDU_PARSER
    assert meta["snapshot_complete"] is True
    assert captured["target"] is target
    assert captured["max_pages"] == 6
    assert captured["detail_limit"] == 200
    assert callable(captured["session_factory"])


def test_target_owns_key63_and_disables_the_key11_subset() -> None:
    document = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(
            encoding="utf-8"
        )
    )
    targets = {row["provider"]: row for row in document["targets"]}
    canonical = targets[chungju.CHUNGJU_GOODEDU_PROVIDER]
    subset = targets[chungju.CHUNGJU_GOODEDU_SUBSET_PROVIDER]

    assert canonical["crawler_status"] == "ready"
    assert canonical["collection_type"] == chungju.CHUNGJU_GOODEDU_PARSER
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["service_group"] == "공공강좌"
    assert canonical["full_snapshot_required"] is True
    assert canonical["ownership_scope"] == chungju.CHUNGJU_OWNERSHIP_SCOPE
    assert canonical["ownership_aliases"] == [chungju.CHUNGJU_GOODEDU_SUBSET_URL]
    assert canonical["superseded_providers"] == [
        chungju.CHUNGJU_GOODEDU_SUBSET_PROVIDER
    ]
    quality = canonical["last_quality"]
    assert quality["source_total"] == quality["source_rows"] == 4029
    assert quality["pages"] == 6
    assert quality["current_rows"] == quality["detail_pages"] == 162
    assert quality["reservation_application_links"] == 24
    assert quality["duplicate_count"] == quality["duplicate_url_count"] == 0
    assert quality["snapshot_complete"] is True

    assert subset["collection_type"] == "duplicate"
    assert subset["crawler_status"] == (
        f"duplicate_url:{chungju.CHUNGJU_GOODEDU_PROVIDER}"
    )
    assert subset["duplicate_of"] == chungju.CHUNGJU_GOODEDU_PROVIDER
    assert subset["last_quality"]["audited_source_total"] == 1384
    assert subset["last_quality"]["audited_current_rows"] == 101
    assert subset["last_quality"]["exact_subset"] is True


def test_operational_aggregate_is_the_only_registry_route_and_override_is_complete() -> None:
    from Crawler import Crawler_GeneratedYamlTargets as generated

    arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        chungju.CHUNGJU_GOODEDU_PROVIDER
    ]
    parsed = generated.parse_args(list(arguments))
    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.per_target_limit == 0
    assert parsed.max_pages >= 6
    assert parsed.detail_limit >= 162
    assert parsed.allow_partial_save is False

    registry = yaml.safe_load(
        (ROOT / "config" / "generated_yaml_crawler_registry.yaml").read_text(
            encoding="utf-8"
        )
    )
    providers = {row["provider"] for row in registry["targets"]}
    assert chungju.CHUNGJU_GOODEDU_PROVIDER not in providers
    assert chungju.CHUNGJU_GOODEDU_SUBSET_PROVIDER not in providers
    assert not (
        ROOT
        / "Crawler"
        / "generated_yaml"
        / f"{chungju.CHUNGJU_GOODEDU_PROVIDER}.py"
    ).exists()
    assert not (
        ROOT
        / "Crawler"
        / "generated_yaml"
        / f"{chungju.CHUNGJU_GOODEDU_SUBSET_PROVIDER}.py"
    ).exists()
