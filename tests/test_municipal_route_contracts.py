from __future__ import annotations

import ast
from collections import defaultdict, deque
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


SOURCE = Path(__file__).resolve().parents[1] / "Crawler" / "Crawler_MunicipalYaml.py"
LAZY_COLLECTOR_SOURCES = {
    "collect_jne_integrated_library": SOURCE.parent / "jne_integrated_library.py",
    "collect_rda_agricultural_science_programs": (
        SOURCE.parent / "rda_agricultural_science_museum.py"
    ),
}


def _functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert len(functions) == len({function.name for function in functions})
    return {function.name: function for function in functions}


def _lazy_collector_functions() -> dict[str, ast.FunctionDef]:
    collectors: dict[str, ast.FunctionDef] = {}
    for name, source in LAZY_COLLECTOR_SOURCES.items():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        matches = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        assert len(matches) == 1
        collectors[name] = matches[0]
    return collectors


def _reachable(functions: dict[str, ast.FunctionDef], root: str) -> set[str]:
    graph = {
        name: {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions
        }
        for name, function in functions.items()
    }
    reachable = {root}
    queue = deque([root])
    while queue:
        for called in graph[queue.popleft()]:
            if called not in reachable:
                reachable.add(called)
                queue.append(called)
    return reachable


def test_every_municipal_collector_is_reachable_from_dispatch() -> None:
    functions = _functions()
    collectors = {name for name in functions if name.startswith("collect_")}
    assert len(collectors) == 210
    assert collectors <= _reachable(functions, "collect_from_url")


def test_dispatch_has_no_duplicate_provider_key_or_missing_collector() -> None:
    functions = _functions()
    dispatch_collectors = {**functions, **_lazy_collector_functions()}
    dispatch = functions["collect_from_url"]
    parsed_line = next(
        node.lineno
        for node in ast.walk(dispatch)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "parsed_target_url" for target in node.targets)
    )
    providers: dict[str, list[int]] = defaultdict(list)
    route_calls: list[ast.Call] = []
    for node in ast.walk(dispatch):
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id.startswith("collect_")
        ):
            route_calls.append(node.value)
        if not isinstance(node, ast.Compare) or node.lineno < parsed_line:
            continue
        expressions = [node.left, *node.comparators]
        if not any(
            isinstance(expression, ast.Attribute)
            and isinstance(expression.value, ast.Name)
            and expression.value.id == "target"
            and expression.attr == "provider"
            for expression in expressions
        ):
            continue
        for expression in expressions:
            values = expression.elts if isinstance(expression, (ast.List, ast.Set, ast.Tuple)) else [expression]
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    providers[value.value].append(node.lineno)

    assert len(route_calls) == 205
    assert all(call.func.id in dispatch_collectors for call in route_calls)
    assert {provider: lines for provider, lines in providers.items() if len(lines) > 1} == {}


def test_every_dispatch_route_propagates_timeout_and_declared_work_caps() -> None:
    functions = _functions()
    dispatch_collectors = {**functions, **_lazy_collector_functions()}
    dispatch = functions["collect_from_url"]
    route_calls = [
        node.value
        for node in ast.walk(dispatch)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id.startswith("collect_")
    ]
    for call in route_calls:
        keywords = {keyword.arg for keyword in call.keywords}
        parameters = {
            argument.arg
            for argument in dispatch_collectors[call.func.id].args.args
        }
        assert "timeout" in keywords
        for cap in ("max_pages", "detail_limit"):
            if cap in parameters:
                assert cap in keywords

    direct_collectors = {call.func.id for call in route_calls}
    assert not [
        (name, node.lineno)
        for name in direct_collectors
        for node in ast.walk(dispatch_collectors[name])
        if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True
    ]


def test_reachable_http_calls_are_safe_session_based_and_timeout_bounded() -> None:
    functions = _functions()
    reachable = _reachable(functions, "collect_from_url")
    session_function = functions["session"]
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "SafeSession"
        for node in ast.walk(session_function)
    )
    assert not [
        (name, node.lineno)
        for name in reachable
        for node in ast.walk(functions[name])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "requests"
    ]
    session_http_calls = [
        (name, node)
        for name in reachable
        for node in ast.walk(functions[name])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "s"
        and node.func.attr in {"get", "post", "put", "patch", "delete", "request", "head"}
    ]
    assert len(session_http_calls) >= 60
    assert all(any(keyword.arg == "timeout" for keyword in call.keywords) for _name, call in session_http_calls)


def test_reachable_broad_exceptions_are_logged_or_reraised() -> None:
    functions = _functions()
    reachable = _reachable(functions, "collect_from_url")
    silent = []
    for name in reachable:
        for handler in (node for node in ast.walk(functions[name]) if isinstance(node, ast.ExceptHandler)):
            broad = handler.type is None or (
                isinstance(handler.type, ast.Name) and handler.type.id in {"Exception", "BaseException"}
            )
            if not broad:
                continue
            raises = any(isinstance(node, ast.Raise) for statement in handler.body for node in ast.walk(statement))
            logs = any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logger"
                for statement in handler.body
                for node in ast.walk(statement)
            )
            if not raises and not logs:
                silent.append((name, handler.lineno))
    assert silent == []


def test_municipal_module_has_no_external_command_network_bypass() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    assert "curl" not in SOURCE.read_text(encoding="utf-8").lower()
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any(alias.name == "subprocess" for alias in node.names)
    ]
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"subprocess", "os"}
        and node.func.attr in {"run", "Popen", "system", "popen", "spawnl", "spawnv"}
    ]


class _FakeSession:
    def __init__(self) -> None:
        self.headers = {}

    def close(self) -> None:
        pass


def _target(provider: str, url: str) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(provider, provider, provider, url, "test")


def test_taean_fanout_consumes_one_global_page_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, timeout: calls.append((url, timeout)) or BeautifulSoup("<html></html>", "lxml"),
    )
    _rows, _parser, meta = municipal.collect_taean_education_cards(
        _target("MUNI_WWW_TAEAN_GO_KR_ADF2555A", "https://www.taean.go.kr/edu.do"),
        timeout=1,
        max_pages=1,
        detail_limit=0,
    )
    assert len(calls) == 1
    assert meta["pages"] <= 1


def test_aviation_booking_calendar_keeps_only_dates_with_remaining_capacity() -> None:
    available, availability = municipal.aviation_booking_dates(
        "20260717:0,20260718:30,20260719:0,20260721:103,"
    )

    assert available == ["2026-07-18", "2026-07-21"]
    assert availability == {
        "2026-07-17": 0,
        "2026-07-18": 30,
        "2026-07-19": 0,
        "2026-07-21": 103,
    }


def test_daegu_national_science_covers_all_requested_education_menus() -> None:
    assert {url.rsplit("/", 1)[-1] for _key, url in municipal.DAEGU_NATIONAL_SCIENCE_SOURCES} == {
        "personalScienceEdu.do",
        "muhanSangSangIndividual.do",
        "convergenceEdu.do",
        "labEducation.do",
    }


def test_mmca_sections_share_one_global_page_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(municipal, "session", _FakeSession)

    def payload(_session, endpoint, data, timeout):
        calls.append((endpoint, data, timeout))
        return {
            "exhibitionsList": [
                {
                    "exhTitle": "Current exhibition",
                    "exhId": "1",
                    "exhStDt": "2026-01-01",
                    "exhEdDt": "2099-12-31",
                }
            ],
            "paginationInfo": {"totalRecordCount": 1},
        }

    monkeypatch.setattr(municipal, "mmca_json_post", payload)
    _rows, _parser, meta = municipal.collect_mmca_programs(
        _target("NATIONAL_MUSEUM_OF_MODERN_ART", "https://www.mmca.go.kr/"),
        timeout=1,
        max_pages=1,
    )
    assert len(calls) == 1
    assert meta["pages"] <= 1


def test_mmca_collects_general_children_and_gwacheon_children_museum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda *_args, **_kwargs: BeautifulSoup(
            '<div class="detailCont"><dl><dt>교육시간</dt><dd>14:00~15:30</dd></dl>상세 교육 내용</div>',
            "lxml",
        ),
    )

    def payload(_session, endpoint, data, timeout):
        assert endpoint.endswith("/educations/ajaxEduChildList.do")
        code = data["eduBigCd"]
        return {
            "eduList": [
                {
                    "eduId": f"child-{code}",
                    "eduTitle": f"Child program {code}",
                    "eduBigCd": code,
                    "eduTarget": "초등학생",
                    "eduPlaNm": "과천" if code == "05" else "서울",
                    "eduPlaDtl": "교육실",
                    "eduPrice": "0",
                    "eduStDt": "2099-08-01",
                    "eduEdDt": "2099-08-02",
                }
            ],
            "paginationInfo": {"totalRecordCount": 1},
        }

    monkeypatch.setattr(municipal, "mmca_json_post", payload)
    rows, _parser, meta = municipal.collect_mmca_programs(
        _target("NATIONAL_MUSEUM_OF_MODERN_ART", "https://www.mmca.go.kr/educations/eduChildList.do"),
        timeout=1,
        max_pages=2,
    )

    assert {row["category"] for row in rows} == {"어린이 교육", "과천 어린이미술관"}
    assert {row["branch"] for row in rows} == {"서울", "과천 어린이미술관"}
    assert all(row["description"] for row in rows)
    assert meta["section_counts"] == {"children": 1, "gwacheon_children_museum": 1}


def test_mmca_duplicate_sections_merge_the_richer_fields() -> None:
    raw_url = "https://www.mmca.go.kr/educations/educationsDetail.do?eduId=100"
    rows = municipal.coalesce_mmca_program_rows(
        [
            {
                "provider": "NATIONAL_MUSEUM_OF_MODERN_ART",
                "provider_course_id": "child",
                "title": "Summer museum",
                "branch": "Gwacheon Children's Museum",
                "raw_url": raw_url,
                "period": "2099-08-01 ~ 2099-08-03",
                "schedule_raw": "2099-08-01 ~ 2099-08-03",
                "venue_name": "Gwacheon Children's Museum education room",
                "description": "Detailed child-program description",
                "status": "\uac8c\uc2dc",
                "collection_type": "api_json+detail_html",
                "raw_fields": {"child_section": "children"},
            },
            {
                "provider": "NATIONAL_MUSEUM_OF_MODERN_ART",
                "provider_course_id": "education",
                "title": "Summer museum",
                "branch": "Gwacheon",
                "raw_url": raw_url,
                "period": "2099-08-01 ~ 2099-08-03",
                "schedule_raw": "14:00~15:30",
                "venue_name": "Gwacheon",
                "description": "Short API row",
                "status": "OPEN",
                "reservation_available": True,
                "collection_type": "api_json",
                "raw_fields": {},
            },
        ]
    )

    assert len(rows) == 1
    assert rows[0]["branch"] == "Gwacheon Children's Museum"
    assert rows[0]["venue_name"] == "Gwacheon Children's Museum education room"
    assert rows[0]["schedule_raw"] == "14:00~15:30"
    assert rows[0]["description"] == "Detailed child-program description"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is True
    assert rows[0]["raw_fields"]["merged_source_rows"] == 2


def test_nihc_sections_share_one_global_page_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, timeout: calls.append((url, timeout)) or BeautifulSoup("<html></html>", "lxml"),
    )
    _rows, _parser, meta = municipal.collect_national_intangible_heritage_center(
        _target("NATIONAL_INTANGIBLE_HERITAGE_CENTER", "https://www.nihc.go.kr/planweb/board/list.9is"),
        timeout=1,
        max_pages=1,
        detail_limit=0,
    )
    assert len(calls) == 1
    assert meta["pages"] <= 1


def test_gugak_sources_share_one_global_page_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, timeout: calls.append((url, timeout)) or BeautifulSoup("<html></html>", "lxml"),
    )
    _rows, _parser, meta = municipal.collect_gugak_center_programs(
        _target("NATIONAL_GUGAK_CENTER", "https://www.gugak.go.kr/"),
        timeout=1,
        max_pages=1,
        detail_limit=0,
    )
    assert len(calls) == 1
    assert meta["pages"] <= 1


def test_gugak_reuses_unused_academy_detail_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, int] = {}
    monkeypatch.setattr(municipal, "session", _FakeSession)

    def academy(
        _target,
        _session,
        timeout,
        max_pages,
        detail_limit,
        page_budget,
    ):
        assert timeout == 1
        assert max_pages == 100
        assert detail_limit == 10
        page_budget.used = 2
        return [{"provider_course_id": "academy"}], {
            "academy_pages": 2,
            "academy_detail_pages": 2,
            "academy_discovered_links": 2,
            "academy_pagination_complete": True,
            "academy_details_complete": True,
            "academy_page_cap_reached": False,
        }

    def performances(
        _target,
        _session,
        timeout,
        max_pages,
        detail_limit,
        page_budget,
    ):
        captured["max_pages"] = max_pages
        captured["detail_limit"] = detail_limit
        return [{"provider_course_id": "performance"}], {
            "performance_pages": municipal.GUGAK_PERFORMANCE_HORIZON_MONTHS,
            "performance_detail_pages": 1,
            "performance_discovered_links": 1,
            "performance_pagination_complete": True,
            "performance_details_complete": True,
            "performance_page_cap_reached": False,
        }

    monkeypatch.setattr(municipal, "collect_gugak_academy_courses", academy)
    monkeypatch.setattr(municipal, "collect_gugak_performances", performances)

    rows, _parser, meta = municipal.collect_gugak_center_programs(
        _target("NATIONAL_GUGAK_CENTER", "https://www.gugak.go.kr/"),
        timeout=1,
        max_pages=100,
        detail_limit=10,
    )

    assert len(rows) == 2
    assert captured == {"max_pages": 98, "detail_limit": 8}
    assert meta["pagination_complete"] is True
    assert meta["detail_collection_complete"] is True
    assert meta["snapshot_complete"] is True
