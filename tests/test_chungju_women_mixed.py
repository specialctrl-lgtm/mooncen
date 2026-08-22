from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import threading

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_chungju_women as chungju


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Target:
    provider: str = chungju.CHUNGJU_WOMEN_PROVIDER
    url: str = chungju.CHUNGJU_WOMEN_URL
    name: str = "충주시 여성문화회관 전체 교육·체험"
    branch: str = "충청북도 충주시"
    source: str = "test"
    priority: int = 1
    region: str = "충청북도"
    extra: dict[str, object] | None = None


class FakeResponse:
    def __init__(self, body: str, url: str, status_code: int = 200) -> None:
        self.content = body.encode("utf-8")
        self.status_code = status_code
        self.url = url
        self.history: list[object] = []


class FakeSession:
    def __init__(
        self,
        responses: dict[str, str | list[str] | tuple[int, str]],
        calls: list[str],
    ) -> None:
        self.responses = responses
        self.calls = calls
        self.counts: dict[str, int] = {}
        self.lock = threading.Lock()

    def get(self, url: str, **_kwargs) -> FakeResponse:
        with self.lock:
            self.calls.append(url)
            index = self.counts.get(url, 0)
            self.counts[url] = index + 1
        value = self.responses.get(url)
        if value is None:
            return FakeResponse("missing", url, 404)
        if isinstance(value, tuple):
            return FakeResponse(value[1], url, value[0])
        if isinstance(value, list):
            body = value[min(index, len(value) - 1)]
        else:
            body = value
        return FakeResponse(body, url)

    def close(self) -> None:
        return None


def _identity(sequence: int) -> str:
    return f"{sequence:032x}"


_COURSES = (
    (18, "(정규강좌) 다이어트댄스(라인)", "2026-09-15", "12-04", "20"),
    (17, "(정규강좌) 차밍스트레칭&근력", "2026-09-15", "12-04", "20"),
    (16, "(정규강좌) 스크린파크골프 A반", "2026-09-15", "10-12", "8"),
    (15, "(특별강좌) 우리가족 달콤한 하루:쌀 클레이", "2026-09-18", "18", "10"),
    (14, "(특별강좌) 평생월급 국민연금 더 받는 방법", "2026-10-07", "07", "20"),
    (13, "(특별강좌) 은퇴후 건강보험료 절감 방법", "2026-10-14", "14", "20"),
    (
        12,
        "(특별강좌) 우리가족 초록추억: 다육이 화분만들기(도우아트)",
        "2026-10-17",
        "17",
        "10",
    ),
    (11, "(특별강좌) 자식보다 필요한 노인장기요양보험", "2026-10-21", "21", "20"),
    (10, "(특별강좌) 슬기로운 자산관리 상속 vs 증여", "2026-10-28", "28", "20"),
    (
        9,
        "선착순추가모집★ (특별강좌) AI를 활용한 이력서 코칭 및 자기소개서 작성법",
        "2026-05-13",
        "13",
        "20",
    ),
    (8, "(정규강좌) 다이어트댄스(라인)", "2026-04-07", "06-12", "20"),
    (7, "(정규강좌) 차밍스트레칭&근력", "2026-04-07", "06-12", "20"),
    (6, "(정규강좌) 방과후 학교지도사", "2026-04-07", "06-12", "20"),
    (5, "(특별강좌) 향으로 쉬는 날: 아로마", "2026-04-07", "07", "20"),
    (4, "(특별강좌) 힐링타임 : 수경식물 가꾸기", "2026-04-14", "14", "20"),
    (3, "(특별강좌) 감성느낌 가죽공예", "2026-04-21", "21", "20"),
    (2, "(특별강좌) 행복한 정리수납", "2026-04-28", "28", "20"),
    (1, "(특별강좌) 나를 돋보이는 퍼스널컬러", "2026-05-19", "19", "20"),
)


def _full_end(start: str, short_end: str) -> str:
    if short_end.count("-") == 1:
        return f"{start[:4]}-{short_end}"
    return f"{start[:8]}{short_end}"


def _row_html(
    sequence: int,
    title: str,
    start: str,
    short_end: str,
    capacity: str,
    *,
    applicants: int = 0,
) -> str:
    current = sequence >= 10
    status = "준비중" if current else "접수마감"
    apply_period = "2026-08-11 ~ 18" if current else "2026-03-04 ~ 11"
    identity = _identity(sequence)
    return f"""
      <li><a class="regist_state_pre"
        href="?action=read&amp;action-value={identity}">
        <dl>
          <dt class="no">번호</dt><dd class="no">{sequence}</dd>
          <dt class="title">제목</dt><dd class="title">{title}</dd>
          <dt class="regist">접수여부</dt><dd class="regist">{status}</dd>
          <dt class="center">기관</dt><dd class="center">{chungju.CHUNGJU_WOMEN_BRANCH}</dd>
          <dt class="lecture_date">교육 기간</dt>
          <dd class="lecture_date">{start} ~ {short_end}</dd>
          <dt class="regist_date">접수기간</dt>
          <dd class="regist_date">{apply_period}</dd>
          <dt class="capacity">정원</dt><dd class="capacity">{capacity}</dd>
          <dt class="count_regist">신청자수</dt><dd class="count_regist">{applicants}</dd>
        </dl>
      </a></li>
    """


def _list_html(rows: list[str], *, page: int, total: int = 18) -> str:
    body = "".join(rows) if rows else '<li class="empty">등록/검색된 정보가 없습니다.</li>'
    return f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
      <div class="modules_lecture"><div class="proc_list">
        <div class="count">총 강좌 수 : {total} 건 (총 1페이지 중 {page}페이지)</div>
        <div class="list"><ul>{body}</ul></div>
      </div></div>
    </body></html>"""


def _detail_html(
    sequence: int,
    title: str,
    start: str,
    short_end: str,
    capacity: str,
    *,
    application_href: str = "#",
) -> str:
    identity = _identity(sequence)
    end = _full_end(start, short_end)
    onclick = (
        "alert('로그인 후 확인 가능합니다.'); return false;"
        if application_href == "#"
        else ""
    )
    write = (
        f'<a class="button action_write" href="{application_href}" '
        f'onclick="{onclick}">신청하기</a>'
    )
    check = (
        '<a class="button action_check" href="#" '
        "onclick=\"alert('로그인 후 확인 가능합니다.'); return false;\">신청확인</a>"
    )
    values = {
        "권역 / 읍면동": "시내권 / 칠금금릉동",
        "기관명": chungju.CHUNGJU_WOMEN_BRANCH,
        "강좌명": title,
        "기수 구분": "하반기",
        "접수방식": "온라인",
        "교육 기간": f"{start} ~ {end}",
        "총교육일": "1일",
        "교육요일": "금",
        "수업시간": "16:00~18:00",
        "접수 기간": "2026-08-11 09:00:00 ~ 2026-08-18 18:00:00",
        "정원": f"{capacity}명",
        "선발방식": "추첨제",
        "우선접수대상": "일반",
        "모집연령": "19 ~ 100세 충주시 거주자",
        "수업료": "무료",
        "강사": "공개 강사명",
        "문의 연락처": "043-000-0000",
        "교육장": "충주여성문화회관 2층 다목적실",
        "교육장주소": "충북 충주시 팽고리산길 45",
        "교육장위치": "",
        "준비물": "",
        "수업 내용": "저장하지 않는 공개 상세 본문",
        "첨부파일": "fixture.jpg",
    }
    table = "".join(
        f'<tr><th scope="row">{key}</th><td>{value}</td></tr>'
        for key, value in values.items()
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
      <div class="modules_lecture"><div class="proc_read">
        <p>{write}{check}</p><table><tbody>{table}</tbody></table>
        <p>
          <a class="button action_preview" href="/rev/File/Preview/{identity}">미리보기</a>
          <a class="button action_download" href="/rev/File/Download/{identity}">다운로드</a>
        </p><p>{write}{check}</p>
      </div></div>
    </body></html>"""


def _valid_responses() -> dict[str, str | list[str] | tuple[int, str]]:
    rows = [_row_html(*course) for course in _COURSES]
    page_one = _list_html(rows, page=1)
    responses: dict[str, str | list[str] | tuple[int, str]] = {
        chungju.CHUNGJU_WOMEN_URL: [page_one, page_one],
        chungju.chungju_women_list_url(2): _list_html([], page=2),
    }
    for course in _COURSES[:9]:
        sequence, title, start, short_end, capacity = course
        responses[chungju.chungju_women_detail_url(_identity(sequence))] = _detail_html(
            sequence, title, start, short_end, capacity
        )
    return responses


def _collect(
    responses: dict[str, str | list[str] | tuple[int, str]],
    **kwargs,
):
    calls: list[str] = []
    session = FakeSession(responses, calls)
    result = chungju.collect_chungju_women_courses(
        Target(),
        timeout=1,
        max_pages=kwargs.pop("max_pages", 10),
        detail_limit=kwargs.pop("detail_limit", 20),
        today=kwargs.pop("today", "2026-08-05"),
        session_factory=lambda: session,
        **kwargs,
    )
    return (*result, calls)


def test_complete_mixed_snapshot_and_safe_fetch_boundary() -> None:
    rows, parser, meta, calls = _collect(_valid_responses())

    assert parser == chungju.CHUNGJU_WOMEN_PARSER
    assert len(rows) == 9
    assert [row["raw_fields"]["source_sequence"] for row in rows] == list(
        range(18, 9, -1)
    )
    assert sum(row["service_family"] == "education" for row in rows) == 7
    assert sum(row["service_family"] == "experience" for row in rows) == 2
    assert {row["service_group"] for row in rows} == {"공공강좌", "체험"}
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["application_type"] == "INFO_ONLY" for row in rows)
    assert all("instructor" not in row and "contact" not in row for row in rows)
    assert all("첨부파일" not in str(row) and "공개 강사명" not in str(row) for row in rows)

    assert meta["source_total"] == meta["source_rows"] == 18
    assert meta["current_count"] == meta["returned_count"] == 9
    assert meta["expired_count"] == 9
    assert meta["education_count"] == 7
    assert meta["experience_count"] == 2
    assert meta["pages"] == meta["required_list_requests"] == 3
    assert meta["data_pages"] == meta["sentinel_pages"] == meta["stable_rechecks"] == 1
    assert meta["detail_attempts"] == meta["detail_pages"] == 9
    assert meta["application_control_count"] == 18
    assert meta["direct_application_control_count"] == 0
    assert meta["attachment_control_count"] == 18
    assert meta["application_urls"] == meta["unsafe_endpoint_calls"] == 0
    assert meta["application_endpoint_calls"] == meta["application_check_endpoint_calls"] == 0
    assert meta["attachment_endpoint_calls"] == meta["download_endpoint_calls"] == 0
    assert meta["pii_endpoint_calls"] == 0
    assert meta["pii_payload_persisted"] is False
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["classification_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""

    assert calls.count(chungju.CHUNGJU_WOMEN_URL) == 2
    assert calls.count(chungju.chungju_women_list_url(2)) == 1
    assert len(calls) == 12
    lowered = "\n".join(calls).lower()
    for token in (
        "action=write",
        "action=check",
        "/login",
        "/member",
        "/applicant",
        "/identity",
        "/file/preview",
        "/file/download",
        "attachment",
    ):
        assert token not in lowered


def test_target_and_route_contracts_are_exact() -> None:
    assert chungju.is_chungju_women_target(Target())
    assert not chungju.is_chungju_women_target(
        Target(url=f"{chungju.CHUNGJU_WOMEN_URL}?page=1")
    )
    assert not chungju.is_chungju_women_target(
        Target(url=chungju.CHUNGJU_WOMEN_URL.replace("https://", "http://"))
    )
    assert not chungju.is_chungju_women_target(Target(provider="OTHER"))
    assert chungju.chungju_women_list_url(1) == chungju.CHUNGJU_WOMEN_URL
    assert chungju.chungju_women_list_url(2).endswith("?page=2")
    assert chungju.chungju_women_list_url(0) == ""
    assert chungju.chungju_women_detail_url(_identity(18)).endswith(
        f"action=read&action-value={_identity(18)}"
    )
    assert chungju.chungju_women_detail_url("unsafe") == ""


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "message"),
    (
        (2, 20, "max_pages cap"),
        (10, 8, "detail_limit cap"),
    ),
)
def test_caps_fail_closed(max_pages: int, detail_limit: int, message: str) -> None:
    rows, _, meta, _ = _collect(
        _valid_responses(), max_pages=max_pages, detail_limit=detail_limit
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_counter_sentinel_and_stable_recheck_drift_fail_atomically() -> None:
    responses = _valid_responses()
    responses[chungju.chungju_women_list_url(2)] = _list_html(
        [_row_html(*_COURSES[-1])], page=2
    )
    rows, _, meta, _ = _collect(responses)
    assert rows == []
    assert "sentinel" in meta["configured_collection_error"]

    responses = _valid_responses()
    changed_rows = [_row_html(*course) for course in _COURSES]
    changed_rows[0] = _row_html(*_COURSES[0], applicants=1)
    first = responses[chungju.CHUNGJU_WOMEN_URL]
    assert isinstance(first, list)
    responses[chungju.CHUNGJU_WOMEN_URL] = [
        first[0],
        _list_html(changed_rows, page=1),
    ]
    rows, _, meta, _ = _collect(responses)
    assert rows == []
    assert "page 1 changed" in meta["configured_collection_error"]


def test_unknown_course_or_detail_application_drift_fails_closed() -> None:
    responses = _valid_responses()
    page = responses[chungju.CHUNGJU_WOMEN_URL]
    assert isinstance(page, list)
    changed = page[0].replace(_COURSES[0][1], "새 미감사 강좌")
    responses[chungju.CHUNGJU_WOMEN_URL] = [changed, changed]
    rows, _, meta, _ = _collect(responses)
    assert rows == []
    assert "unknown exact course classification" in meta["configured_collection_error"]

    responses = _valid_responses()
    sequence, title, start, short_end, capacity = _COURSES[0]
    responses[chungju.chungju_women_detail_url(_identity(sequence))] = _detail_html(
        sequence,
        title,
        start,
        short_end,
        capacity,
        application_href="https://evil.example/apply",
    )
    rows, _, meta, calls = _collect(responses)
    assert rows == []
    assert "unsafe application control" in meta["configured_collection_error"]
    assert all("evil.example" not in call for call in calls)


def test_session_requirement_and_dedupe_cardinality_fail_closed() -> None:
    rows, _, meta = chungju.collect_chungju_women_courses(
        Target(), today="2026-08-05"
    )
    assert rows == []
    assert "managed session_factory" in meta["configured_collection_error"]

    rows, _, meta, _ = _collect(_valid_responses(), dedupe_rows=lambda _rows: [])
    assert rows == []
    assert "dedupe_rows changed" in meta["configured_collection_error"]


def test_router_dispatches_exact_owner(monkeypatch) -> None:
    expected = ([{"ok": True}], "chungju-women", {"snapshot_complete": True})
    captured: dict[str, object] = {}

    def fake_collect(target: object, **kwargs: object):
        assert target == Target()
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(chungju, "collect_chungju_women_courses", fake_collect)
    assert (
        router.collect_from_url(
            Target(), timeout=7, max_depth=0, max_pages=10, detail_limit=20
        )
        == expected
    )
    assert captured["timeout"] == 7
    assert captured["max_pages"] == 10
    assert captured["detail_limit"] == 20


def test_single_target_operational_and_coverage_linkage() -> None:
    target_document = yaml.safe_load(
        (ROOT / "config/crawl_targets/public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    matches = [
        row
        for row in target_document["targets"]
        if row.get("provider") == chungju.CHUNGJU_WOMEN_PROVIDER
    ]
    assert len(matches) == 1
    target = matches[0]
    assert target["url"] == chungju.CHUNGJU_WOMEN_URL
    assert target["crawler_module"] == "Crawler.municipal_chungju_women"
    assert target["crawler_callable"] == "collect_chungju_women_courses"
    assert target["crawler_status"] == "ready"
    assert target["full_snapshot_required"] is True
    assert target["classification_locked"] is True
    assert target["ops_scopes"] == ["education", "experience"]

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    operational_matches = [
        row for row in operational if row.get("provider") == chungju.CHUNGJU_WOMEN_PROVIDER
    ]
    assert len(operational_matches) == 1
    assert operational_matches[0]["target_url"] == chungju.CHUNGJU_WOMEN_URL
    assert operational_matches[0]["row_count"] == 9

    coverage = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )["municipalities"]
    municipality = next(row for row in coverage if row.get("code") == "4313000000")
    assert chungju.CHUNGJU_WOMEN_PROVIDER in municipality["owner_providers"]
    assert chungju.CHUNGJU_WOMEN_PROVIDER in municipality["promoted_providers"]
    assert chungju.CHUNGJU_WOMEN_PROVIDER in municipality["yaml_owner_providers"]
    assert any(
        evidence.get("provider") == chungju.CHUNGJU_WOMEN_PROVIDER
        and evidence.get("target_url") == chungju.CHUNGJU_WOMEN_URL
        for evidence in municipality["evidence"]
    )


@pytest.mark.skipif(
    os.getenv("RUN_CHUNGJU_WOMEN_LIVE") != "1",
    reason="set RUN_CHUNGJU_WOMEN_LIVE=1 for the bounded official snapshot",
)
def test_live_exact_snapshot() -> None:
    rows, parser, meta = chungju.collect_chungju_women_courses(
        Target(),
        timeout=20,
        max_pages=10,
        detail_limit=20,
        today="2026-08-05",
        allow_raw_requests_for_tests=True,
    )
    assert parser == chungju.CHUNGJU_WOMEN_PARSER
    assert len(rows) == 9
    assert meta["source_total"] == 18
    assert meta["expired_count"] == 9
    assert meta["education_count"] == 7
    assert meta["experience_count"] == 2
    assert meta["detail_pages"] == 9
    assert meta["application_urls"] == meta["unsafe_endpoint_calls"] == 0
    assert meta["snapshot_complete"] is True
