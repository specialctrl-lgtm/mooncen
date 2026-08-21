from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import threading

import pytest
import yaml

from Crawler import municipal_chungju_rev as chungju


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Target:
    provider: str = chungju.CHUNGJU_REV_PROVIDER
    url: str = chungju.CHUNGJU_REV_URL


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


def identity_for(category_id: str) -> str:
    return f"{int(category_id):032x}"


def category_html(*, omit: str = "") -> str:
    values = [(chungju.CHUNGJU_REV_ALL_CATEGORY, "전체보기")]
    values.extend(chungju.CHUNGJU_REV_CATEGORIES.items())
    return '<div class="category">' + "".join(
        (
            '<a href="?document_category_srl='
            f'{category_id}">{name}</a>'
        )
        for category_id, name in values
        if category_id != omit
    ) + "</div>"


def row_html(
    category_id: str,
    identity: str,
    sequence: int,
    *,
    title: str,
    institution: str,
    status: str,
    education_period: tuple[str, str],
    application_period: tuple[str, str],
    page: int = 1,
) -> str:
    return f"""
      <li><a class="regist_state_end"
        href="?action=read&amp;action-value={identity}&amp;page={page}&amp;document_category_srl={category_id}">
        <dl>
          <dt class="no">번호</dt><dd class="no">{sequence}</dd>
          <dt class="title">제목</dt><dd class="title">{title}</dd>
          <dt class="regist">접수여부</dt><dd class="regist">{status}</dd>
          <dt class="center">기관</dt><dd class="center">{institution}</dd>
          <dt class="lecture_date">교육 기간</dt>
          <dd class="lecture_date">{education_period[0]} ~ {education_period[1]}</dd>
          <dt class="regist_date">접수기간</dt>
          <dd class="regist_date">{application_period[0]} ~ {application_period[1]}</dd>
          <dt class="capacity">정원</dt><dd class="capacity">20</dd>
          <dt class="count_regist">신청자수</dt><dd class="count_regist">7</dd>
        </dl>
      </a></li>
    """


def list_html(
    total: int,
    displayed_page: int,
    advertised_last: int,
    rows: list[str],
    *,
    include_categories: bool = False,
    omit_category: str = "",
) -> str:
    body = "".join(rows) or "<li>등록/검색된 정보가 없습니다.</li>"
    categories = category_html(omit=omit_category) if include_categories else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
      <div class="modules_lecture"><div class="proc_list">
        {categories}
        <div class="count">총 강좌 수 : <strong>{total}</strong>건
          (총 {advertised_last}페이지 중 {displayed_page}페이지)</div>
        <div class="list"><ul>{body}</ul></div>
      </div></div>
    </body></html>"""


def detail_html(
    category_name: str,
    identity: str,
    *,
    title: str,
    institution: str,
    education_period: tuple[str, str],
    application_period: tuple[str, str],
    actual_application: bool = False,
    application_href: str = "",
    town: str = "",
) -> str:
    if application_href:
        href = application_href
        onclick = ""
    elif actual_application:
        href = (
            f"?action=write&amp;action-value={identity}"
            f"&amp;document_category_srl={next(key for key, value in chungju.CHUNGJU_REV_CATEGORIES.items() if value == category_name)}"
        )
        onclick = ""
    else:
        href = "#"
        onclick = "alert('로그인 후 확인 가능합니다.'); return false;"
    control = (
        f'<a class="button action_write" href="{href}" '
        f'onclick="{onclick}">신청하기</a>'
    )
    detail_town = town or f"권역 / {category_name}"
    values = {
        "권역 / 읍면동": detail_town,
        "기관명": institution,
        "강좌명": title,
        "기수 구분": "2026년",
        "접수방식": "온라인+방문",
        "교육 기간": f"{education_period[0]} ~ {education_period[1]}",
        "총교육일": "총 20일",
        "교육요일": "월, 수",
        "수업시간": "10:00~12:00",
        "접수 기간": f"{application_period[0]} ~ {application_period[1]}",
        "정원": "20명 (예비 5명)",
        "선발방식": "추첨제",
        "우선접수대상": "일반",
        "모집연령": "0 ~ 99세",
        "수업료": "무료",
        "강사": "홍길동",
        "문의 연락처": "043-000-0000",
        "교육장": "주민자치실",
        "교육장주소": "충북 충주시 테스트로 1",
        "준비물": "필기도구",
        "수업 내용": "상세 강의 내용",
    }
    table = "".join(
        f'<tr><th scope="row">{key}</th><td>{value}</td></tr>'
        for key, value in values.items()
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
      <div class="modules_lecture"><div class="proc_read">
        <p>{control}</p><table><tbody>{table}</tbody></table><p>{control}</p>
      </div></div>
    </body></html>"""


def valid_responses() -> dict[str, str | tuple[int, str]]:
    responses: dict[str, str | tuple[int, str]] = {}
    for category_id, category_name in chungju.CHUNGJU_REV_CATEGORIES.items():
        identity = identity_for(category_id)
        title = f"{category_name} 강좌"
        institution = f"{category_name} 주민자치센터"
        expired = category_id == "34"
        education = (
            ("2025-01-01", "2024-06-30")
            if expired
            else ("2026-07-01", "2026-12-31")
        )
        application = (
            ("2024-01-01", "2024-01-10")
            if expired
            else ("2026-07-01", "2026-08-01")
        )
        status = "접수중" if category_id == "35" else "접수마감"
        row = row_html(
            category_id,
            identity,
            1,
            title=title,
            institution=institution,
            status=status,
            education_period=education,
            application_period=application,
        )
        page_one = list_html(
            1,
            1,
            1,
            [row],
            include_categories=category_id == chungju.CHUNGJU_REV_ENTRY_CATEGORY,
        )
        responses[chungju.chungju_rev_list_url(category_id, 1)] = page_one
        responses[chungju.chungju_rev_list_url(category_id, 2)] = list_html(
            1, 2, 1, []
        )
        responses[chungju.chungju_rev_detail_url(category_id, identity)] = detail_html(
            category_name,
            identity,
            title=title,
            institution=institution,
            education_period=education,
            application_period=application,
            actual_application=category_id == "35",
            town="- / -" if expired else "",
        )
        if category_id == chungju.CHUNGJU_REV_ENTRY_CATEGORY:
            responses[chungju.CHUNGJU_REV_URL] = page_one
    return responses


def collect(
    responses: dict[str, str | tuple[int, str]],
    **kwargs,
):
    session_factory, calls = factory_for(responses)
    result = chungju.collect_chungju_rev_courses(
        Target(),
        timeout=1,
        max_pages=kwargs.pop("max_pages", 50),
        detail_limit=kwargs.pop("detail_limit", 25),
        today=kwargs.pop("today", date(2026, 7, 20)),
        max_workers=4,
        session_factory=session_factory,
        **kwargs,
    )
    return (*result, calls)


def test_target_and_url_contract_are_exact() -> None:
    assert chungju.is_chungju_rev_target(Target()) is True
    assert chungju.is_chungju_rev_target(
        Target(url=chungju.CHUNGJU_REV_URL + "&page=1")
    ) is False
    assert chungju.is_chungju_rev_target(
        Target(url=chungju.CHUNGJU_REV_URL.replace("https://", "http://"))
    ) is False
    assert chungju.is_chungju_rev_target(Target(provider="OTHER")) is False
    assert chungju.chungju_rev_list_url("37", 3).endswith(
        "page=3&document_category_srl=37"
    )
    identity = identity_for("37")
    assert chungju.chungju_rev_detail_url("37", identity).endswith(
        f"action=read&action-value={identity}&document_category_srl=37"
    )
    assert chungju.chungju_rev_detail_url("999", identity) == ""


def test_complete_categories_sentinels_and_all_details_are_required() -> None:
    rows, parser, meta, calls = collect(valid_responses())

    assert parser == chungju.CHUNGJU_REV_PARSER
    assert len(rows) == 24
    assert all(row["provider_course_id"].startswith(chungju.CHUNGJU_REV_PROVIDER) for row in rows)
    assert rows[0]["branch_code"].startswith("CHUNGJU_REV_BRANCH_")
    assert rows[0]["branch"].endswith("주민자치센터")
    assert rows[0]["capacity_total"] == 20
    assert rows[0]["capacity_current"] == 7
    assert rows[0]["venue_name"] == "주민자치실"
    open_row = next(row for row in rows if row["category_raw"] == "살미면")
    assert open_row["application_url"] == chungju.chungju_rev_application_url(
        "35", identity_for("35")
    )
    assert open_row["application_type"] == "ONLINE_RESERVATION"
    assert sum(bool(row.get("application_url")) for row in rows) == 1
    assert meta["source_total"] == meta["source_rows"] == 25
    assert meta["expired_count"] == 1
    assert meta["current_count"] == meta["returned_count"] == 24
    assert meta["pages"] == meta["required_list_requests"] == 50
    assert meta["data_pages"] == meta["sentinel_pages"] == 25
    assert meta["detail_attempts"] == meta["detail_pages"] == 25
    assert meta["historical_reversed_education_period_count"] == 1
    assert meta["detail_list_mismatch_count"] == 0
    assert meta["duplicate_count"] == meta["duplicate_url_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["ownership_disjoint_from"] == [chungju.CHUNGJU_GOODEDU_PROVIDER]
    assert chungju.chungju_rev_detail_url("34", identity_for("34")) in calls
    assert Counter(calls)[chungju.CHUNGJU_REV_URL] == 1


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "message"),
    (
        (49, 25, "max_pages cap"),
        (50, 24, "detail_limit cap"),
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


def test_missing_official_category_fails_closed() -> None:
    responses = valid_responses()
    entry = responses[chungju.CHUNGJU_REV_URL]
    assert isinstance(entry, str)
    responses[chungju.CHUNGJU_REV_URL] = entry.replace(
        '<a href="?document_category_srl=59">목행용탄동</a>', ""
    )

    rows, _, meta, _ = collect(responses)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "category identifiers/labels/order changed" in meta["configured_collection_error"]


def test_declared_total_or_nonempty_sentinel_fails_closed() -> None:
    responses = valid_responses()
    category_id = "34"
    identity = identity_for(category_id)
    institution = "주덕읍 주민자치센터"
    row = row_html(
        category_id,
        identity,
        1,
        title="주덕읍 강좌",
        institution=institution,
        status="접수마감",
        education_period=("2025-01-01", "2024-06-30"),
        application_period=("2024-01-01", "2024-01-10"),
    )
    responses[chungju.chungju_rev_list_url(category_id, 1)] = list_html(
        2, 1, 1, [row]
    )

    rows, _, meta, _ = collect(responses)
    assert rows == []
    assert "terminal page row count mismatch" in meta["configured_collection_error"]

    responses = valid_responses()
    leak = row_html(
        category_id,
        f"{999:032x}",
        1,
        title="sentinel leak",
        institution=institution,
        status="접수마감",
        education_period=("2026-07-01", "2026-12-31"),
        application_period=("2026-01-01", "2026-01-10"),
        page=2,
    )
    responses[chungju.chungju_rev_list_url(category_id, 2)] = list_html(
        1, 2, 1, [leak]
    )
    rows, _, meta, _ = collect(responses)
    assert rows == []
    assert "immediate sentinel is not empty" in meta["configured_collection_error"]


def test_duplicate_action_value_fails_closed() -> None:
    responses = valid_responses()
    duplicate_identity = identity_for("34")
    category_id = "35"
    row = row_html(
        category_id,
        duplicate_identity,
        1,
        title="살미면 중복 강좌",
        institution="살미면 주민자치센터",
        status="접수중",
        education_period=("2026-07-01", "2026-12-31"),
        application_period=("2026-07-01", "2026-08-01"),
    )
    responses[chungju.chungju_rev_list_url(category_id, 1)] = list_html(
        1, 1, 1, [row]
    )

    rows, _, meta, _ = collect(responses)

    assert rows == []
    assert meta["duplicate_count"] == 1
    assert "duplicate source identities" in meta["configured_collection_error"]


def test_detail_list_mismatch_or_external_application_fails_closed() -> None:
    responses = valid_responses()
    category_id = "35"
    identity = identity_for(category_id)
    responses[chungju.chungju_rev_detail_url(category_id, identity)] = detail_html(
        "살미면",
        identity,
        title="다른 제목",
        institution="살미면 주민자치센터",
        education_period=("2026-07-01", "2026-12-31"),
        application_period=("2026-07-01", "2026-08-01"),
        actual_application=True,
    )
    rows, _, meta, _ = collect(responses)
    assert rows == []
    assert meta["detail_pages"] == 24
    assert "detail/list title mismatch" in meta["configured_collection_error"]

    responses = valid_responses()
    responses[chungju.chungju_rev_detail_url(category_id, identity)] = detail_html(
        "살미면",
        identity,
        title="살미면 강좌",
        institution="살미면 주민자치센터",
        education_period=("2026-07-01", "2026-12-31"),
        application_period=("2026-07-01", "2026-08-01"),
        application_href="https://evil.example/apply",
    )
    rows, _, meta, _ = collect(responses)
    assert rows == []
    assert "unexpected Chungju reservation route" in meta["configured_collection_error"]


def test_target_metadata_records_the_live_complete_snapshot() -> None:
    document = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    targets = {row["provider"]: row for row in document["targets"]}
    target = targets[chungju.CHUNGJU_REV_PROVIDER]

    assert target["crawler_status"] == "ready"
    assert target["source_group"] == "municipal_reservation"
    assert target["domain_category"] == "교육·강좌"
    assert target["service_group"] == "공공강좌"
    assert target["collection_type"] == chungju.CHUNGJU_REV_PARSER
    assert target["full_snapshot_required"] is True
    assert target["ownership_scope"] == chungju.CHUNGJU_REV_OWNERSHIP_SCOPE
    assert target["ownership_disjoint_from"] == [chungju.CHUNGJU_GOODEDU_PROVIDER]
    quality = target["last_quality"]
    assert quality["source_total"] == quality["source_rows"] == 534
    assert quality["pages"] == 64
    assert quality["data_pages"] == 39
    assert quality["sentinel_pages"] == 25
    assert quality["current_rows"] == 129
    assert quality["expired_rows"] == 405
    assert quality["detail_pages"] == 534
    assert quality["current_branch_count"] == 29
    assert quality["application_urls"] == 0
    assert quality["detail_list_mismatch_count"] == 0
    assert quality["duplicate_count"] == quality["duplicate_url_count"] == 0
    assert quality["historical_reversed_education_period_count"] == 1
    assert quality["semantic_overlap_with_goodedu_key63"] == 0
    assert quality["snapshot_complete"] is True


def test_generated_registry_requires_a_complete_chungju_rev_run() -> None:
    document = yaml.safe_load(
        (ROOT / "config" / "generated_yaml_crawler_registry.yaml").read_text(
            encoding="utf-8"
        )
    )
    registry = {row["provider"]: row for row in document["targets"]}
    arguments = registry[chungju.CHUNGJU_REV_PROVIDER]["arguments"]

    assert arguments == [
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "700",
    ]
    assert "--allow-partial-save" not in arguments
