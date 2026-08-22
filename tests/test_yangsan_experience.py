from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from Crawler import municipal_yangsan_experience as yangsan


ROOT = Path(__file__).resolve().parents[1]


def _target(**overrides: Any) -> dict[str, Any]:
    return {
        "provider": yangsan.YANGSAN_EXPERIENCE_PROVIDER,
        "url": yangsan.YANGSAN_EXPERIENCE_URL,
        "name": "양산시 통합예약 체험·견학 전체 일정",
        "branch": "경상남도 양산시",
        **overrides,
    }


def _directory(*, missing_last: bool = False) -> str:
    programs = yangsan.YANGSAN_EXPERIENCE_PROGRAMS[:-1] if missing_last else yangsan.YANGSAN_EXPERIENCE_PROGRAMS
    links = "".join(
        f'<a data-menu-type="P" data-menu-id="{program.mid}" '
        f'data-menu-url="/booking/contents.do?mid={program.mid}">{program.menu_label}</a>'
        for program in programs
    )
    return f'<li id="lnb_03">{links}</li>'


def _page(
    program: yangsan.YangsanExperienceProgram,
    *,
    missing_directory: bool = False,
    bad_form: bool = False,
) -> str:
    closed = "Y" if program.exclusion_reason == "stopped_performance" else "N"
    action = "/booking/wrong.do" if bad_form else program.application_path
    return f"""
    <html><head><meta charset="utf-8"><title>{program.page_title}</title></head><body>
      {_directory(missing_last=missing_directory)}
      <h3>{program.heading}</h3>
      <form id="applyForm" name="applyForm" method="post" action="{action}?mid={program.mid}">
        <input type="hidden" id="appDate" name="APP_DATE">
        <input type="hidden" id="timeIdx" name="TIME_IDX">
      </form>
      <script>
        yh.ia = {{
          prmName: "{program.name}",
          masterIdx: "{program.master}",
          prmUrl: "{program.prm}",
          masterCloseYn: "{closed}"
        }};
        var monthly = {{url: "{program.monthly_path}"}};
      </script>
    </body></html>
    """


@dataclass
class Response:
    body: str | Mapping[str, Any]
    url: str
    status_code: int = 200
    history: tuple[object, ...] = ()

    @property
    def content(self) -> bytes:
        return self.body.encode("utf-8") if isinstance(self.body, str) else b""

    @property
    def text(self) -> str:
        return self.body if isinstance(self.body, str) else ""

    def json(self) -> Mapping[str, Any]:
        if not isinstance(self.body, Mapping):
            raise ValueError("not JSON")
        return self.body


class FixtureSession:
    def __init__(
        self,
        *,
        missing_directory: bool = False,
        bad_form: bool = False,
        unstable_monthly: bool = False,
    ) -> None:
        self.missing_directory = missing_directory
        self.bad_form = bad_form
        self.unstable_monthly = unstable_monthly
        self.calls: list[str] = []
        self.counts: dict[str, int] = {}

    def close(self) -> None:
        pass

    def get(self, url: str, **kwargs: object) -> Response:
        assert kwargs["allow_redirects"] is False
        assert int(kwargs["timeout"]) == 7
        self.calls.append(url)
        self.counts[url] = self.counts.get(url, 0) + 1
        kind, program, month = yangsan._validate_public_url(url)
        if kind == "page":
            return Response(
                _page(
                    program,
                    missing_directory=self.missing_directory,
                    bad_form=self.bad_form and program.master == "140",
                ),
                url,
            )
        assert month is not None
        year, month_number = month
        time_id = str(1000 + int(program.master))
        if kind == "meta":
            blocked: list[dict[str, Any]] = []
            if program.master == "140" and (year, month_number) == (2099, 2):
                blocked = [
                    {
                        "idx": "1",
                        "mIdx": program.master,
                        "dt": "2099-02-01",
                        "timeIdxList": [time_id],
                        "delYn": "N",
                    }
                ]
            return Response(
                {
                    "success": True,
                    "tour": {
                        "mIdx": program.master,
                        "bookableBefore": 3,
                        "bookableTill": 1,
                        "headcnt": 10,
                        "fee": 0,
                        "bookableDaysOfWeekList": [str(day) for day in range(7)],
                        "timeSetList": [
                            {
                                "idx": time_id,
                                "mIdx": program.master,
                                "startTime": "09:00",
                                "endTime": "10:00",
                                "delYn": "N",
                            }
                        ],
                    },
                    "list": blocked,
                },
                url,
            )
        rows: list[dict[str, Any]] = []
        if program.master == "123" and (year, month_number) == (2099, 1):
            rows.append(
                {
                    "SDATE": "2099-01-30",
                    "EDATE": "2099-01-30",
                    "CNT": 1,
                    "TOTAL_CNT": 3,
                }
            )
        if (
            self.unstable_monthly
            and program.master == "140"
            and (year, month_number) == (2099, 1)
            and self.counts[url] > 1
        ):
            rows.append(
                {
                    "SDATE": "2099-01-31",
                    "EDATE": "2099-01-31",
                    "CNT": 1,
                    "TOTAL_CNT": 2,
                }
            )
        return Response({"success": True, "totalCnt": 0, "list": rows}, url)


def _collect(session: FixtureSession, **kwargs: Any):
    result = yangsan.collect_yangsan_experience_courses(
        _target(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", 3),
        detail_limit=kwargs.pop("detail_limit", 20),
        today="2099-01-30",
        session_factory=lambda: session,
        **kwargs,
    )
    return result, session.calls


def test_target_and_public_get_allowlist_are_exact() -> None:
    assert yangsan.is_yangsan_experience_target(_target())
    assert not yangsan.is_yangsan_experience_target(_target(provider="OTHER"))
    assert not yangsan.is_yangsan_experience_target(
        _target(url=yangsan.YANGSAN_EXPERIENCE_URL + "#changed")
    )
    assert not yangsan.is_yangsan_experience_target(
        _target(url=yangsan.YANGSAN_EXPERIENCE_URL + "&extra=1")
    )

    program = yangsan.YANGSAN_EXPERIENCE_PROGRAMS[0]
    assert yangsan._validate_public_url(program.page_url)[0] == "page"
    assert yangsan._validate_public_url(
        yangsan.yangsan_experience_meta_url(program, 2099, 1)
    )[0] == "meta"
    assert yangsan._validate_public_url(
        yangsan.yangsan_experience_monthly_url(program, 2099, 1)
    )[0] == "monthly"
    for unsafe in (
        f"https://{yangsan.YANGSAN_EXPERIENCE_HOST}{program.application_path}?mid={program.mid}",
        f"https://{yangsan.YANGSAN_EXPERIENCE_HOST}/booking/ia/tour/{program.prm}/{program.master}/calendar/daily/list.do?dt=2099-01-30",
        f"https://{yangsan.YANGSAN_EXPERIENCE_HOST}/booking/login.do",
    ):
        with pytest.raises(yangsan.YangsanExperienceContractError):
            yangsan._validate_public_url(unsafe)


def test_complete_fixture_snapshot_and_exclusions() -> None:
    session = FixtureSession()
    (rows, parser, meta), calls = _collect(session)
    assert parser == yangsan.YANGSAN_EXPERIENCE_PARSER
    assert len(rows) == 27
    assert meta["status_counts"] == {"OPEN": 26, "CLOSED": 1}
    assert meta["directory_program_count"] == 11
    assert meta["included_program_count"] == 9
    assert meta["excluded_program_count"] == 2
    assert set(meta["excluded_programs"].values()) == {
        "volunteer_recruitment",
        "stopped_performance",
    }
    assert meta["month_partition_count"] == 18
    assert meta["boundary_recheck_count"] == 18
    assert meta["program_page_requests"] == 22
    assert meta["meta_requests"] == 36
    assert meta["monthly_requests"] == 36
    assert meta["physical_requests"] == 94
    assert meta["snapshot_complete"] is True
    assert {row["service_group"] for row in rows} == {"체험"}
    assert {row["service_group_policy"] for row in rows} == {"locked"}
    assert {row["branch_code"] for row in rows} == {
        f"YANGSAN_EXP_{program.master}"
        for program in yangsan.YANGSAN_EXPERIENCE_PROGRAMS
        if program.included
    }
    assert not any(row["provider_course_id"].endswith(":2099-02-01") for row in rows if row["branch_code"] == "YANGSAN_EXP_140")
    closed = [row for row in rows if row["status"] == "CLOSED"]
    assert len(closed) == 1 and closed[0]["capacity"] == "3/10"
    assert all("/calendar/daily/" not in url for url in calls)
    assert all("/app/" not in url and "login" not in url.lower() for url in calls)


@pytest.mark.parametrize(
    ("session", "error"),
    [
        (FixtureSession(missing_directory=True), "directory changed"),
        (FixtureSession(bad_form=True), "application form identity changed"),
        (FixtureSession(unstable_monthly=True), "month boundary changed"),
    ],
)
def test_contract_drift_fails_atomically(session: FixtureSession, error: str) -> None:
    (rows, _parser, meta), _calls = _collect(session)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in meta["configured_collection_error"]


def test_caps_and_dedupe_fail_atomically() -> None:
    session = FixtureSession()
    (rows, _parser, meta), calls = _collect(session, detail_limit=10)
    assert rows == [] and calls == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    session = FixtureSession()
    (rows, _parser, meta), _calls = _collect(session, max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    session = FixtureSession()
    (rows, _parser, meta), _calls = _collect(
        session, dedupe_rows=lambda values: list(reversed(values))
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_dispatch_target_operational_and_coverage_are_wired(monkeypatch) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    sentinel = ([{"id": 1}], yangsan.YANGSAN_EXPERIENCE_PARSER, {"snapshot_complete": True})
    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(yangsan, "collect_yangsan_experience_courses", collect)
    target = municipal.CrawlTarget(
        provider=yangsan.YANGSAN_EXPERIENCE_PROVIDER,
        name="양산시 체험·견학",
        branch="경상남도 양산시",
        url=yangsan.YANGSAN_EXPERIENCE_URL,
        source="test",
    )
    assert municipal.collect_from_url(
        target, timeout=3, max_pages=20, detail_limit=30
    ) == sentinel
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])

    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/public_reservation.yaml").read_text(encoding="utf-8")
    )["targets"]
    matches = [item for item in targets if item.get("provider") == yangsan.YANGSAN_EXPERIENCE_PROVIDER]
    assert len(matches) == 1
    assert matches[0]["url"] == yangsan.YANGSAN_EXPERIENCE_URL
    assert matches[0]["service_group"] == "체험"
    assert matches[0]["ops_scopes"] == ["experience"]
    assert matches[0]["last_quality"]["snapshot_complete"] is True

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(encoding="utf-8")
    )["entries"]
    matches = [item for item in operational if item.get("provider") == yangsan.YANGSAN_EXPERIENCE_PROVIDER]
    assert len(matches) == 1 and matches[0]["row_count"] == 254

    municipalities = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_coverage.yaml").read_text(encoding="utf-8")
    )["municipalities"]
    coverage = next(item for item in municipalities if item.get("code") == "4833000000")
    assert yangsan.YANGSAN_EXPERIENCE_PROVIDER in coverage["owner_providers"]
    assert yangsan.YANGSAN_EXPERIENCE_PROVIDER in coverage["promoted_providers"]
    assert yangsan.YANGSAN_EXPERIENCE_PROVIDER in coverage["yaml_owner_providers"]
    evidence = next(
        item
        for item in coverage["evidence"]
        if item.get("provider") == yangsan.YANGSAN_EXPERIENCE_PROVIDER
        and item.get("kind") == "operational_allowlist"
    )
    assert evidence["row_count"] == 254
    assert evidence["parser"] == yangsan.YANGSAN_EXPERIENCE_PARSER
