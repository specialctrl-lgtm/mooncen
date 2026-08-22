from __future__ import annotations

from datetime import date

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal
from Crawler.Crawler_MunicipalYaml import CrawlTarget
from Crawler.Crawler_MunicipalYaml import normalize_suwon_library_date_range
from Crawler.Crawler_MunicipalYaml import suwon_library_course_expired


REFERENCE_DATE = date(2026, 7, 26)


def test_suwon_library_expiration_uses_education_end_date_when_present() -> None:
    assert suwon_library_course_expired(
        "2026-06-01 ~ 2026-06-30",
        "2026-05-01 ~ 2026-05-31",
        "접수마감",
        REFERENCE_DATE,
    )
    assert not suwon_library_course_expired(
        "2026-08-01 ~ 2026-08-31",
        "2026-05-01 ~ 2026-05-31",
        "접수마감",
        REFERENCE_DATE,
    )


def test_suwon_library_expiration_falls_back_to_closed_application_period() -> None:
    assert suwon_library_course_expired(
        "~ (토요일)",
        "2017-10-11 ~ 2017-10-21",
        "접수마감",
        REFERENCE_DATE,
    )
    assert not suwon_library_course_expired(
        "~ (토요일)",
        "2017-10-11 ~ 2017-10-21",
        "접수중",
        REFERENCE_DATE,
    )


def test_suwon_library_normalizes_two_digit_end_year() -> None:
    assert (
        normalize_suwon_library_date_range("2016.12.20 ~ 17.01.06")
        == "2016-12-20 ~ 2017-01-06"
    )


def test_suwon_library_expiration_rejects_stale_malformed_application_range() -> None:
    assert suwon_library_course_expired(
        "~ (매주 수)",
        "2013-09-24 ~ 2013-09-31",
        "접수마감",
        REFERENCE_DATE,
    )
    assert not suwon_library_course_expired(
        "~ (매주 수)",
        "2026-05-24 ~ 2026-05-32",
        "접수마감",
        REFERENCE_DATE,
    )


def test_suwon_library_accepts_an_exhausted_empty_snapshot(monkeypatch) -> None:
    empty_page = municipal.BeautifulSoup(
        '<div class="lecture-list"></div>',
        "html.parser",
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda *_args, **_kwargs: empty_page,
    )
    target = CrawlTarget(
        provider="SUWON_LIBRARY_ME",
        name="Test library",
        branch="Test library",
        url=(
            "https://www.suwonlib.go.kr/reserve/lecture/lectureList.do"
            "?searchManageCdArray=ME&currentPageNo=1"
        ),
        source="test",
    )

    rows, parser, meta = municipal.collect_suwon_library_lectures(
        target,
        timeout=1,
        max_pages=2,
    )

    assert rows == []
    assert parser == "suwon_library_lecture_list"
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is True


def test_suwon_library_stops_when_the_server_repeats_its_last_page(monkeypatch) -> None:
    repeated_page = municipal.BeautifulSoup(
        """
        <div class="lecture-list">
          <li>
            <div class="title"><a onclick="fnDetail('1')">Old course</a></div>
            <div class="info">
              <span>援먯쑁湲곌컙 : 2020-01-01 ~ 2020-01-31</span>
            </div>
            <div class="info_r">?묒닔留덇컧</div>
          </li>
        </div>
        <div class="pagination"></div>
        """,
        "html.parser",
    )
    requests: list[str] = []
    monkeypatch.setattr(municipal, "session", lambda: object())

    def fake_fetch(_session, url: str, **_kwargs):
        requests.append(url)
        return repeated_page

    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(
        municipal,
        "suwon_library_course_expired",
        lambda *_args, **_kwargs: True,
    )
    target = CrawlTarget(
        provider="SUWON_LIBRARY_ME",
        name="Test library",
        branch="Test library",
        url=(
            "https://www.suwonlib.go.kr/reserve/lecture/lectureList.do"
            "?searchManageCdArray=ME&currentPageNo=1&recordCountPerPage=1"
        ),
        source="test",
    )

    rows, _parser, meta = municipal.collect_suwon_library_lectures(
        target,
        timeout=1,
        max_pages=200,
    )

    assert rows == []
    assert len(requests) == 2
    assert meta["pagination_exhausted"] is True
    assert meta["source_cap_reached"] is False
    assert meta["no_current_data"] is True


def test_suwon_library_registry_allows_the_longest_history() -> None:
    providers = sorted(
        provider
        for provider in generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES
        if provider.startswith("SUWON_LIBRARY_")
    )

    assert len(providers) == 19
    for provider in providers:
        arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider]
        assert arguments[arguments.index("--max-pages") + 1] == "200"
