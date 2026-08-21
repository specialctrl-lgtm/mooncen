from __future__ import annotations

from types import SimpleNamespace

from bs4 import BeautifulSoup

from tools import discover_application_urls as discovery


def test_discovery_seed_urls_walk_current_path_parents_and_root() -> None:
    seeds = discovery.discovery_seed_urls(
        "https://example.com/a/b/view.do?key=2105&page=1#section"
    )

    assert [(seed.kind, seed.url) for seed in seeds] == [
        ("configured_target", "https://example.com/a/b/view.do?key=2105&page=1"),
        ("configured_path", "https://example.com/a/b/view.do"),
        ("parent_path", "https://example.com/a/b/"),
        ("parent_path", "https://example.com/a/"),
        ("site_root", "https://example.com/"),
    ]


def test_normalize_discovery_url_keeps_public_key_and_round_trips_values() -> None:
    assert discovery.normalize_discovery_url("https://example.com/menu?key=2105") == (
        "https://example.com/menu?key=2105"
    )
    assert discovery.normalize_discovery_url("https://example.com/search?q=a%26b%20c") == (
        "https://example.com/search?q=a%26b+c"
    )
    assert discovery.normalize_discovery_url(
        "https://example.com/menu?access_token=secret"
    ) == ""


def test_application_url_alone_is_not_parse_or_schedule_ready() -> None:
    candidate = discovery.Candidate(
        url="https://example.com/course",
        rows=2,
        field_counts={"application_url": 2},
    )

    assert candidate.application_path_ready is True
    assert candidate.parse_ready is False
    assert candidate.registration_schedule_ready is False


def _candidate(
    url: str,
    *,
    verdict: str,
    score: int,
    fields: dict[str, int],
    error: str = "",
) -> discovery.Candidate:
    return discovery.Candidate(
        url=url,
        final_url=url,
        score=score,
        verdict=verdict,
        rows=3,
        field_counts=fields,
        same_organization=True,
        host_allowed=True,
        error=error,
    )


def test_recommendation_requires_registration_schedule_for_automatic_replace() -> None:
    source = "https://example.com/news/view.do?nttNo=1"
    current = _candidate(source, verdict="false_positive", score=5, fields={})
    replacement = _candidate(
        "https://example.com/course/list.do",
        verdict="verified",
        score=90,
        fields={"period": 3, "persisted_registration_schedule": 3},
    )

    recommendation = discovery.recommend_target_url(
        source, current, [current, replacement], min_score=60
    )

    assert recommendation["action"] == "replace_target"
    assert recommendation["recommended_url"] == replacement.url


def test_recommendation_keeps_scheduleless_candidate_for_manual_review() -> None:
    source = "https://example.com/news/view.do?nttNo=1"
    current = _candidate(source, verdict="false_positive", score=5, fields={})
    replacement = _candidate(
        "https://example.com/course/list.do",
        verdict="verified",
        score=90,
        fields={"period": 3},
    )

    recommendation = discovery.recommend_target_url(
        source, current, [current, replacement], min_score=60
    )

    assert recommendation["action"] == "review_candidate"


def test_negative_notice_candidate_cannot_be_verified(monkeypatch) -> None:
    rows = [
        {
            "title": "여름 강좌",
            "period": "2026-07-01 ~ 2026-07-31",
            "apply_period": "2026-06-01 ~ 2026-06-20",
            "raw_url": "https://example.com/notice?nttNo=1",
            "description": "교육기간 접수기간 수강료 대상",
        }
    ]
    monkeypatch.setattr(discovery, "parse_all_courses", lambda *_args: (rows, "generic_card"))
    page = discovery.FetchedPage(
        requested_url="https://example.com/notice?nttNo=1",
        final_url="https://example.com/notice?nttNo=1",
        title="공지사항",
        text="교육기간 접수기간 수강료 대상 개인정보 로그인",
        soup=BeautifulSoup("<html><title>공지사항</title></html>", "lxml"),
        status_code=200,
        content_type="text/html",
    )

    candidate = discovery.fetch_candidate(
        discovery.Candidate(url=page.requested_url),
        "https://example.com/news/view.do?nttNo=1",
        "TEST",
        5,
        http_session=object(),
        prefetched=page,
    )

    assert "negative_url" in candidate.reasons
    assert candidate.verdict != "verified"


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        return self.response


def test_robots_404_allows_but_503_defers() -> None:
    allowed, reason = discovery.robots_allowed(
        _Session(_Response(404)), "https://example.com/course", 5, {}
    )
    assert allowed is True
    assert reason == "robots_not_found"

    allowed, reason = discovery.robots_allowed(
        _Session(_Response(503)), "https://example.com/course", 5, {}
    )
    assert allowed is False
    assert reason == "robots_unavailable_retry"


def test_select_targets_excludes_deprecated_and_duplicate_rows(monkeypatch) -> None:
    rows = [
        {"provider": "GOOD", "crawler_status": "blocked", "priority": 1},
        {"provider": "OLD", "crawler_status": "deprecated", "priority": 1},
        {
            "provider": "DUP",
            "crawler_status": "duplicate_url:GOOD",
            "priority": 1,
        },
    ]
    monkeypatch.setattr(discovery, "_iter_target_rows", lambda _path: rows)
    args = SimpleNamespace(
        provider=["GOOD", "OLD", "DUP"],
        include_culture=False,
        offset=0,
        limit=20,
    )

    assert [row["provider"] for row in discovery.select_targets(args)] == ["GOOD"]

