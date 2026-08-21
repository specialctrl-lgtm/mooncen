from __future__ import annotations

from collections import Counter
from html import escape
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import municipal_hscity_culture as culture


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200) -> None:
        self.url = url
        self.text = html
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.history: list[Any] = []


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(**updates: str) -> dict[str, str]:
    target = {
        "provider": culture.HSCITY_CULTURE_PROVIDER,
        "url": culture.HSCITY_CULTURE_LIST_URL,
    }
    target.update(updates)
    return target


def _card(
    service_type: str,
    service_id: str,
    *,
    title: str,
    branch: str,
    place: str,
    institution_id: str,
    apply: bool,
    apply_identity: str | None = None,
) -> str:
    service = culture.HSCITY_CULTURE_SERVICES[service_type]
    identity = apply_identity or service_id
    application = (
        f"""
        <a class="sub-card-btn orange half-margin" href="#none"
           onclick="javascript:fnApply('{service_type}', '{identity}'); return false;">
          신청하기
        </a>
        """
        if apply
        else '<a class="sub-card-btn none half-margin">접수예정</a>'
    )
    return f"""
      <div class="sub-card-item">
        <div class="sub-card-img-box">
          <a class="sub-card-img-link" href="#none"
             onclick="fnDetail('{service_type}', '{service_id}', '');">
            <img src="/attach/{service_type}/{service_id}.jpg"
                 alt="{escape(title, quote=True)}" />
          </a>
        </div>
        <div class="sub-card-info-box"><div class="sub-card-info">
          <p class="sub-card-info-title">
            <a href="#none" onclick="fnDetail('{service_type}', '{service_id}', '');">
              {escape(title)}
            </a>
          </p>
          <ul class="sub-card-info-list"
              onclick="javascript:fnDetail('{service_type}', '{service_id}', ''); return false;">
            <li><dl class="sub-card-desc"><dt class="sub-card-desc-title">분류</dt>
              <dd class="sub-card-desc-txt">{service.category}</dd></dl></li>
            <li><dl class="sub-card-desc"><dt class="sub-card-desc-title">기관</dt>
              <dd class="sub-card-desc-txt">{escape(branch)}</dd></dl></li>
            <li><dl class="sub-card-desc"><dt class="sub-card-desc-title">장소</dt>
              <dd class="sub-card-desc-txt">{escape(place)}</dd></dl></li>
            <li><dl class="sub-card-desc"><dt class="sub-card-desc-title">비용</dt>
              <dd class="sub-card-desc-txt">무료</dd></dl></li>
          </ul>
          <div class="sub-card-btn-box">
            {application}
            <button class="sub-card-btn white"
              onclick="fnInterestInfoRegistProc('{institution_id}', '{service_type}', '{service_id}', '', '{escape(title, quote=True)}'); return false;">
              관심정보에 담기
            </button>
          </div>
        </div></div>
      </div>
    """


def _list_page(cards: list[str], total: int, page: int) -> str:
    active = (
        f'<ul class="page-list"><li class="active"><a class="num">{page}</a></li></ul>'
        if cards
        else '<ul class="page-list"></ul>'
    )
    return f"""
      <html><body>
        <form><input name="recordCountPerPage" value="15" /></form>
        <p class="table-total">총 항목 수 :
          <span class="num">{total}</span> 건
        </p>
        <div class="sub-card-list style01">{''.join(cards)}</div>
        {active}
      </body></html>
    """


def _detail_page(
    title: str,
    branch: str,
    place: str,
    *,
    target: str,
    period: str,
    application: bool,
    description: str,
) -> str:
    fields = (
        ("운영기관", f"{branch} 바로가기"),
        ("장소", place),
        ("주요대상", target),
        ("이용료", "무료"),
        ("신청기간", "2099-07-01 09:00 ~ 2099-07-31 18:00"),
        *((("이용기간", period),) if period else ()),
        ("정원수", "20명"),
        ("선정방법", "선착순"),
        ("부대시설", ""),
        ("문의처", "031-5189-0000"),
    )
    pairs = "".join(
        f"""
        <dl class="item-desc"><dt class="desc-title">{label}</dt>
          <dd class="desc-txt">{escape(value)}</dd></dl>
        """
        for label, value in fields
    )
    controls = (
        """
        <button onclick="fnApply(); return false;">신청하기</button>
        <button onclick="fnApply(); return false;">신청하기</button>
        """
        if application
        else ""
    )
    return f"""
      <html><body>
        <p class="detail-info-head-title">{escape(title)}</p>
        <div class="detail-info-list">{pairs}{controls}</div>
        <div class="detail-tab-item active">
          <div class="detail-tab info-tab">{escape(description)}</div>
        </div>
      </body></html>
    """


class _FixtureSite:
    definitions = {
        ("401", "ready"): {
            "service_type": "exprn",
            "service_id": "101",
            "title": "2099년 8월 1일 예정 체험",
            "branch": "만세 체험관",
            "place": "만세 체험실",
            "institution_id": "11",
            "target": "어린이",
            "period": "2099-08-01",
            "description": "운영시간 10:00 ~ 11:30",
        },
        ("402", "apply"): {
            "service_type": "visit",
            "service_id": "202",
            "title": "효행 전시 관람",
            "branch": "효행 박물관",
            "place": "효행 전시실",
            "institution_id": "22",
            "target": "누구나",
            "period": "2099-08-02 ~ 2099-08-30",
            "description": "매주 토요일 14:00 ~ 15:00",
        },
        ("404", "apply"): {
            "service_type": "festival",
            "service_id": "303",
            "title": "동탄 시민 축제",
            "branch": "동탄 문화재단",
            "place": "동탄 야외공연장",
            "institution_id": "33",
            "target": "",
            "period": "",
            "description": "상세 회차는 신청 화면에서 선택합니다.",
        },
    }

    def __init__(
        self,
        *,
        global_extra: bool = False,
        wrong_apply_identity: bool = False,
        missing_visit_detail_application: bool = False,
        wrong_detail_title: bool = False,
    ) -> None:
        self.global_extra = global_extra
        self.wrong_apply_identity = wrong_apply_identity
        self.missing_visit_detail_application = (
            missing_visit_detail_application
        )
        self.wrong_detail_title = wrong_detail_title
        self.calls: Counter[str] = Counter()
        self.sessions: list[_Session] = []
        self.lock = Lock()

    def session_factory(self) -> _Session:
        current = _Session()
        with self.lock:
            self.sessions.append(current)
        return current

    def _definition_card(
        self,
        definition: dict[str, str],
        status: str,
    ) -> str:
        return _card(
            definition["service_type"],
            definition["service_id"],
            title=definition["title"],
            branch=definition["branch"],
            place=definition["place"],
            institution_id=definition["institution_id"],
            apply=status == "apply",
            apply_identity=(
                "999"
                if self.wrong_apply_identity
                and definition["service_id"] == "202"
                else None
            ),
        )

    def fetcher(
        self,
        _session: _Session,
        url: str,
        _timeout: int,
    ) -> _Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self.lock:
            self.calls[url] += 1

        if parsed.path == culture.HSCITY_CULTURE_LIST_PATH:
            if not query:
                definitions = list(self.definitions.items())
                cards = [
                    self._definition_card(definition, status)
                    for (_district, status), definition in definitions
                ]
                return _Response(url, _list_page(cards, len(cards), 1))

            district = query["searchAreaEmd"][0]
            status = query["statusCd"][0]
            page = int(query["currentPageNo"][0])
            assert query["recordCountPerPage"] == ["15"]
            definitions = [
                (key, value)
                for key, value in self.definitions.items()
                if key[1] == status and (not district or key[0] == district)
            ]
            if self.global_extra and not district and status == "apply":
                definitions.append(
                    (
                        ("401", "apply"),
                        {
                            "service_type": "exprn",
                            "service_id": "999",
                            "title": "전역에만 있는 체험",
                            "branch": "누락 체험관",
                            "place": "누락 체험실",
                            "institution_id": "99",
                            "target": "누구나",
                            "period": "2099-09-01",
                            "description": "10:00 ~ 11:00",
                        },
                    )
                )
            cards = (
                [
                    self._definition_card(definition, key[1])
                    for key, definition in definitions
                ]
                if page == 1
                else []
            )
            return _Response(
                url,
                _list_page(cards, len(definitions), page),
            )

        for (_district, status), definition in self.definitions.items():
            service = culture.HSCITY_CULTURE_SERVICES[
                definition["service_type"]
            ]
            if parsed.path != service.detail_path:
                continue
            identity = query[service.identity_name][0]
            if identity != definition["service_id"]:
                continue
            title = (
                "상세 제목 변경"
                if self.wrong_detail_title and identity == "202"
                else definition["title"]
            )
            detail_application = (
                status == "apply"
                and definition["service_type"] != "festival"
                and not (
                    self.missing_visit_detail_application
                    and identity == "202"
                )
            )
            return _Response(
                url,
                _detail_page(
                    title,
                    definition["branch"],
                    definition["place"],
                    target=definition["target"],
                    period=definition["period"],
                    application=detail_application,
                    description=definition["description"],
                ),
            )
        raise AssertionError(f"unexpected URL: {url}")


def _collect(site: _FixtureSite, **updates: Any):
    options = {
        "timeout": 5,
        "max_pages": 50,
        "detail_limit": 10,
        "today": "2099-07-01",
        "fetch_attempts": 1,
        "max_workers": 3,
        "fetcher": site.fetcher,
        "session_factory": site.session_factory,
        "sleeper": lambda _seconds: None,
    }
    options.update(updates)
    return culture.collect_hscity_culture(_target(), **options)


def test_complete_three_service_snapshot_and_required_fields() -> None:
    site = _FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == culture.HSCITY_CULTURE_PARSER
    assert len(rows) == 3
    assert meta["source_rows"] == 3
    assert meta["pages"] == meta["list_requests"] == 21
    assert meta["detail_pages"] == 3
    assert meta["network_requests"] == 24
    assert meta["sentinel_requests"] == 5
    assert meta["stability_rechecks"] == 5
    assert meta["global_union_matches"] is True
    assert meta["snapshot_complete"] is True
    assert meta["district_counts"] == {"401": 1, "402": 1, "404": 1}
    assert meta["status_counts"] == {"apply": 2, "ready": 1}
    assert meta["service_type_counts"] == {
        "exprn": 1,
        "festival": 1,
        "visit": 1,
    }
    assert meta["field_counts"] == {
        "target": 3,
        "fee": 3,
        "date": 3,
        "place": 3,
        "category": 3,
        "time": 3,
        "exact_time": 2,
    }
    assert meta["reservation_available_count"] == 1
    assert meta["target_fallback_count"] == 1
    assert meta["period_fallback_count"] == 1
    assert meta["schedule_fallback_count"] == 1

    by_id = {row["provider_course_id"]: row for row in rows}
    assert set(by_id) == {"exprn:101", "visit:202", "festival:303"}
    assert by_id["exprn:101"]["status"] == "접수예정"
    assert "application_url" not in by_id["exprn:101"]
    assert by_id["visit:202"]["application_url"].endswith(
        "/visitDetail.do?visitIdx=202"
    )
    assert by_id["festival:303"]["reservation_available"] is False
    assert "application_url" not in by_id["festival:303"]
    assert by_id["festival:303"]["target"] == "대상 별도 안내"
    assert by_id["festival:303"]["period"] == "회차별 일정 선택"
    assert by_id["festival:303"]["schedule_raw"] == "회차별 시간 선택"
    assert all(session.closed for session in site.sessions)


@pytest.mark.parametrize(
    ("site", "message"),
    [
        (
            _FixtureSite(global_extra=True),
            "district union did not match global current set",
        ),
        (
            _FixtureSite(wrong_apply_identity=True),
            "culture application identity mismatch",
        ),
        (
            _FixtureSite(missing_visit_detail_application=True),
            "open culture detail lost application control",
        ),
        (
            _FixtureSite(wrong_detail_title=True),
            "culture list/detail title mismatch",
        ),
    ],
)
def test_contract_drift_fails_atomically(
    site: _FixtureSite,
    message: str,
) -> None:
    rows, _, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]
    assert all(session.closed for session in site.sessions)


def test_caps_and_target_scope_fail_without_partial_rows() -> None:
    rows, _, meta = _collect(_FixtureSite(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit 2 capped 3" in meta["configured_collection_error"]

    rows, _, meta = _collect(_FixtureSite(), max_pages=20)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap 20 exhausted" in meta["configured_collection_error"]

    site = _FixtureSite()
    rows, _, meta = culture.collect_hscity_culture(
        _target(url="https://yeyak.hscity.go.kr/"),
        fetcher=site.fetcher,
        session_factory=site.session_factory,
    )
    assert rows == []
    assert site.calls == Counter()
    assert "target does not match" in meta["configured_collection_error"]


def test_canonical_routes_and_date_fallbacks() -> None:
    assert culture.is_hscity_culture_target(_target())
    assert not culture.is_hscity_culture_target(
        _target(provider="MUNI_WRONG")
    )
    assert not culture.is_hscity_culture_target(
        _target(url=culture.HSCITY_CULTURE_LIST_URL + "?statusCd=apply")
    )
    assert culture.hscity_culture_detail_url("visit", "202") == (
        "https://yeyak.hscity.go.kr/1012/3008/"
        "visitDetail.do?visitIdx=202"
    )
    assert culture.hscity_culture_detail_url("festival", "303") == (
        "https://yeyak.hscity.go.kr/1071/3010/"
        "festivalDetail.do?festivalIdx=303"
    )
    assert culture.hscity_culture_detail_url("exprn", "101") == (
        "https://yeyak.hscity.go.kr/1013/3009/"
        "exprnDetail.do?exprnIdx=101"
    )

    period, reason = culture._period_from_source(
        "",
        "2099년 8월 1주차 (08/03 ~ 08/07) 체험",
        "",
        today=culture.date(2099, 7, 1),
    )
    assert period == "2099-08-03 ~ 2099-08-07"
    assert reason == "title_date"


def test_target_registry_metadata_and_main_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    document = yaml.safe_load(
        (
            municipal.ROOT
            / "config"
            / "crawl_targets"
            / "public_reservation.yaml"
        ).read_text(encoding="utf-8")
    )
    configured = next(
        row
        for row in document["targets"]
        if row["provider"] == culture.HSCITY_CULTURE_PROVIDER
    )
    assert configured["url"] == culture.HSCITY_CULTURE_LIST_URL
    assert configured["service_group"] == "체험"
    assert configured["service_group_policy"] == "locked"
    assert configured["full_snapshot_required"] is True

    target = municipal.CrawlTarget(
        provider=culture.HSCITY_CULTURE_PROVIDER,
        name=configured["name"],
        branch=configured["branch"],
        url=configured["url"],
        source="test",
        priority=2,
        region="경기도",
        extra=configured,
    )
    sentinel = (
        [{"provider_course_id": "exprn:1"}],
        culture.HSCITY_CULTURE_PARSER,
        {"pages": 1},
    )
    monkeypatch.setattr(
        culture,
        "collect_hscity_culture",
        lambda *_args, **_kwargs: sentinel,
    )
    assert municipal.collect_from_url(
        target,
        timeout=5,
        max_depth=0,
        max_pages=100,
        detail_limit=1000,
    ) == sentinel
