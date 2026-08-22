from __future__ import annotations

from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup
import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import rda_agricultural_science_museum as rda


ROOT = Path(__file__).resolve().parents[1]


def _target(url: str = rda.RDA_RESERVATION_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=rda.RDA_PROVIDER,
        name=rda.RDA_BRANCH,
        branch=rda.RDA_BRANCH,
        url=url,
        source="test",
        extra={},
    )


def _intro() -> BeautifulSoup:
    return BeautifulSoup(
        """
        <main>
          <h1>예약센터</h1>
          <p>주말 전시해설은 총 3회 진행되며, 정원은 20명(1회 당)입니다.</p>
          <p>토 15시 30분, 일 10시 30분, 14시 30분</p>
          <p>※ 예약승인 완료자만 달력에 표기됩니다.</p>
        </main>
        """,
        "html.parser",
    )


def _form(*, extra_program: str = "") -> BeautifulSoup:
    return BeautifulSoup(
        f"""
        <main>
          <p>전시해설은 사전 예약해야 하며, 자유관람은 단체(10인 이상)인 경우만 예약이 필요합니다.</p>
          <form id="reserveForm" name="reserveForm" method="post">
            <input type="radio" name="program_term" id="program_term1" value="자유">
            <label for="program_term1">자유관람</label>
            <input type="radio" name="program_term" id="program_term2" value="전시">
            <label for="program_term2">전시해설</label>
            {extra_program}
            <select id="time" name="time">
              <option value="">시간 선택</option>
              <option value="오전 10시">오전 10시</option>
              <option value="오전 11시">오전 11시</option>
              <option value="오후 1시">오후 1시</option>
              <option value="오후 2시">오후 2시</option>
              <option value="오후 3시">오후 3시</option>
              <option value="오후 4시">오후 4시</option>
            </select>
          </form>
          <script>
            var startDay = '2026-08-05';
            var untilDay = '2026-09-04';
          </script>
          <script src="/js/uiux2025/aeh/ati/ati_reservationCenterIns.js?ver=20260805084254"></script>
        </main>
        """,
        "html.parser",
    )


def _script(*, free_day_rule: str = "day != 0 && day != 6 && day != 1") -> str:
    return f"""
      $(document).ready(function() {{
        var holiDays = ["2026-08-07"];
        if(type == "자유") {{
          return [({free_day_rule})];
        }} else {{
          return [(day != 1)];
        }}
        if(programTerm == "전시" && day == "6") {{
          doAgeSet(4);
        }} else if(programTerm == "전시" && day == "0") {{
          doAgeSet(5);
        }} else {{
          doAgeSet(6);
        }}
      }});
      function doAgeSet(no) {{
        if(no==4) {{
          $("#time").append('<option value="">시간 선택</option>');
          $("#time").append('<option value="오후 3시30분">오후 3시 30분</option>');
        }}
        if(no==5) {{
          $("#time").append('<option value="">시간 선택</option>');
          $("#time").append('<option value="오전 10시30분">오전 10시 30분</option>');
          $("#time").append('<option value="오후 2시30분">오후 2시 30분</option>');
        }}
        if(no==6) {{
          $("#time").append('<option value="">시간 선택</option>');
          $("#time").append('<option value="오전 10시">오전 10시</option>');
          $("#time").append('<option value="오전 11시">오전 11시</option>');
          $("#time").append('<option value="오후 1시">오후 1시</option>');
          $("#time").append('<option value="오후 2시">오후 2시</option>');
          $("#time").append('<option value="오후 3시">오후 3시</option>');
          $("#time").append('<option value="오후 4시">오후 4시</option>');
        }}
      }}
      function smtAlert() {{
        var maxCount = 20;
        $.ajax({{ type: "post", url: "/aehBoard/reserverCheck2.do" }});
      }}
    """


def test_reservation_get_contract_builds_program_date_session_rows(
    monkeypatch,
) -> None:
    fetched_html: list[str] = []

    def fake_fetch_soup(_client, url: str, timeout: int):
        assert timeout == 5
        fetched_html.append(url)
        if url == rda.RDA_INTRO_URL:
            return _intro()
        if url == rda.RDA_RESERVATION_URL:
            return _form()
        raise AssertionError(f"unexpected HTML GET: {url}")

    class GetOnlySession:
        def post(self, *_args, **_kwargs):
            raise AssertionError("collector must never issue POST")

    script_urls: list[str] = []

    def fake_script(_client, url: str, *, timeout: int) -> str:
        assert timeout == 5
        script_urls.append(url)
        return _script()

    monkeypatch.setattr(rda, "session", GetOnlySession)
    monkeypatch.setattr(rda, "fetch_soup", fake_fetch_soup)
    monkeypatch.setattr(rda, "_read_only_script_text", fake_script)

    rows, parser, meta = rda.collect_rda_agricultural_science_programs(
        _target(),
        timeout=5,
        max_pages=3,
        today=date(2026, 8, 5),
    )

    assert parser == rda.RDA_PARSER
    assert fetched_html == [rda.RDA_INTRO_URL, rda.RDA_RESERVATION_URL]
    assert script_urls == [
        "https://www.rda.go.kr/js/uiux2025/aeh/ati/ati_reservationCenterIns.js?ver=20260805084254"
    ]
    assert rows
    assert {row["title"] for row in rows} == {
        "농업과학관 자유관람",
        "농업과학관 전시해설",
    }
    assert all(row["status"] == "SCHEDULED" for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
    assert {row["municipality_code"] for row in rows} == {
        rda.RDA_MUNICIPALITY_CODE
    }
    assert {row["municipality_full_name"] for row in rows} == {
        rda.RDA_MUNICIPALITY_NAME
    }
    assert all(row["municipality_region_verified"] is True for row in rows)
    assert all(
        row["raw_fields"]["occupancy_checked"] is False for row in rows
    )
    assert all("aoz_notice" not in row["raw_url"] for row in rows)

    # The public holiday is removed from both programme schedules.
    assert not any(row["start_date"] == "2026-08-07" for row in rows)
    saturday = [
        row
        for row in rows
        if row["start_date"] == "2026-08-08"
        and row["program_type"] == "전시해설"
    ]
    sunday = [
        row
        for row in rows
        if row["start_date"] == "2026-08-09"
        and row["program_type"] == "전시해설"
    ]
    assert [row["schedule_raw"] for row in saturday] == ["2026-08-08 15:30"]
    assert [row["schedule_raw"] for row in sunday] == [
        "2026-08-09 10:30",
        "2026-08-09 14:30",
    ]
    assert all(row["capacity_total"] == 20 for row in saturday + sunday)
    assert not any(
        row["program_type"] == "관람" and row["start_date"] in {"2026-08-08", "2026-08-09"}
        for row in rows
    )
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["approved_reservation_calendar_called"] is False
    assert meta["occupancy_endpoints_called"] is False
    assert meta["application_endpoint_called"] is False


def test_contract_fails_closed_on_unreviewed_programme() -> None:
    extra = """
      <input type="radio" name="program_term" id="program_term3" value="공지">
      <label for="program_term3">과학관 공지</label>
    """
    with pytest.raises(rda.RdaReservationContractError, match="programme catalogue"):
        rda.parse_rda_reservation_contract(
            _intro(),
            _form(extra_program=extra),
            _script(),
            today=date(2026, 8, 5),
        )


def test_contract_fails_closed_when_public_day_rule_drifts() -> None:
    with pytest.raises(rda.RdaReservationContractError, match="free-visit weekday"):
        rda.parse_rda_reservation_contract(
            _intro(),
            _form(),
            _script(free_day_rule="day != 1"),
            today=date(2026, 8, 5),
        )


def test_notice_board_target_is_rejected_before_any_request(monkeypatch) -> None:
    monkeypatch.setattr(
        rda,
        "session",
        lambda: (_ for _ in ()).throw(AssertionError("no request expected")),
    )
    with pytest.raises(ValueError, match="reservation-centre GET form"):
        rda.collect_rda_agricultural_science_programs(
            _target(
                "https://www.rda.go.kr/aehBoard/aoz_notice.do?mode=list&prgId=aoz_notice&tab=01"
            ),
            timeout=5,
            max_pages=3,
            today=date(2026, 8, 5),
        )


def test_dispatch_uses_dedicated_read_only_rda_collector(monkeypatch) -> None:
    expected = ([{"title": "ok"}], rda.RDA_PARSER, {"pages": 2})
    monkeypatch.setattr(
        rda,
        "collect_rda_agricultural_science_programs",
        lambda target, timeout, max_pages: expected,
    )

    result = municipal.crawl_experience_from_url(
        _target(),
        timeout=5,
        max_depth=0,
        max_pages=3,
        detail_limit=1,
    )

    assert result == expected


def test_active_configs_use_reservation_form_and_quarantine_notice_board() -> None:
    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/museum_science.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    target = next(
        row for row in targets if row["provider"] == rda.RDA_PROVIDER
    )
    assert target["url"] == rda.RDA_RESERVATION_URL
    assert "aoz_notice" not in target["url"]
    assert target["collection_type"] == (
        "reservation_form_get+public_schedule_contract+no_occupancy_or_submit"
    )
    assert target["municipality_code"] == rda.RDA_MUNICIPALITY_CODE
    assert target["municipality_full_name"] == rda.RDA_MUNICIPALITY_NAME
    assert target["municipality_region_verified"] is True

    registry = yaml.safe_load(
        (ROOT / "config/generated_yaml_crawler_registry.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    registered = next(
        row for row in registry if row["provider"] == rda.RDA_PROVIDER
    )
    assert registered["url"] == rda.RDA_RESERVATION_URL
    assert registered["enabled"] is True
