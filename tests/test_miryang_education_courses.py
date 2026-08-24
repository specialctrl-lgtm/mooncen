from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import inspect
import math
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_miryang as miryang


@dataclass(frozen=True)
class YeyakTarget:
    provider: str = miryang.MIRYANG_YEYAK_PROVIDER
    name: str = "밀양시 통합예약 교육"
    branch: str = miryang.MIRYANG_MUNICIPALITY_NAME
    url: str = miryang.MIRYANG_YEYAK_URL


@dataclass(frozen=True)
class LifelongTarget:
    provider: str = miryang.MIRYANG_LIFELONG_PROVIDER
    name: str = "밀양시 평생학습 교육"
    branch: str = miryang.MIRYANG_MUNICIPALITY_NAME
    url: str = miryang.MIRYANG_LIFELONG_URL


class DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _source_by_code(code: str) -> miryang.MiryangYeyakSource:
    return next(source for source in miryang.MIRYANG_YEYAK_SOURCES if source.code == code)


def _yeyak_item(
    identity: str,
    title: str,
    *,
    current: bool,
    status: str = "접수마감",
    source_code: str = "child_learning",
    venue: str = "문화교실",
) -> dict[str, Any]:
    return {
        "identity": identity,
        "title": title,
        "source_code": source_code,
        "category": "취미,여가",
        "apply_start": "2099-06-01" if current else "2020-01-01",
        "apply_end": "2099-06-30" if current else "2020-01-31",
        "start": "2099-07-01" if current else "2020-02-01",
        "end": "2099-08-31" if current else "2020-03-01",
        "schedule": "월요일 10:00~12:00",
        "capacity": "20명/5명",
        "current_capacity": "3명",
        "fee": "무료",
        "venue": venue,
        "status": status,
        "methods": ["인터넷"] if status in {"접수중", "접수대기"} else ["방문"],
        "target": "밀양시민",
    }


def _inventory_links() -> str:
    return "".join(
        f'<a class="education-source" href="{source.inventory_path}">{source.name}</a>'
        for source in miryang.MIRYANG_YEYAK_SOURCES
    )


def _yeyak_card(item: dict[str, Any]) -> str:
    source = _source_by_code(item["source_code"])
    href = (
        f"?amode=view&amp;lectureId={item['identity']}&amp;"
        f"{source.key_name}={source.key_value}"
    )
    methods = "".join(
        f'<span data-progress="{method}">{method}</span>' for method in item["methods"]
    )
    pairs = (
        ("교육분류", item["category"]),
        ("접수기간", f"{item['apply_start']} 09:00 ~ {item['apply_end']} 18:00"),
        ("교육기간", f"{item['start']} ~ {item['end']}"),
        ("요일시간", item["schedule"]),
        ("온라인 정원/대기정원", item["capacity"]),
        ("신청현황", item["current_capacity"]),
        ("수강료", item["fee"]),
        ("교육장소", item["venue"]),
    )
    return f"""
      <div class="lst"><a href="{href}" data-progress="{item['status']}">
        <strong class="h1"><em data-category="무료">무료</em>{item['title']}</strong>
        <div class="g2s">{methods}</div>
        <ul class="clist">
          {''.join(f'<li><span class="t1">{key}</span><span class="t2">{value}</span></li>' for key, value in pairs)}
        </ul>
      </a></div>
    """


def _yeyak_list_page(
    source: miryang.MiryangYeyakSource,
    items: list[dict[str, Any]],
    *,
    total: int,
    last: int,
) -> str:
    pager = "" if last == 1 else f'<a href="?cpage={last}">맨끝</a>'
    return f"""
      <html><body>{_inventory_links()}
        <div>총 <b class="em">{total}</b>건의 자료가 있습니다.</div>
        {''.join(_yeyak_card(item) for item in items)}
        <div class="pagination">{pager}</div>
      </body></html>
    """


def _yeyak_detail_page(
    item: dict[str, Any],
    *,
    omit_application: bool = False,
    wrong_title: bool = False,
) -> str:
    source = _source_by_code(item["source_code"])
    title = "다른 강좌" if wrong_title else item["title"]
    values = (
        ("교육분류", item["category"]),
        ("교육과정", f"{item['title']} 과정 안내"),
        ("교육대상", item["target"]),
        ("접수기간", f"{item['apply_start']} 09:00 ~ {item['apply_end']} 18:00"),
        ("교육기간", f"{item['start']} ~ {item['end']}"),
        ("요일시간", item["schedule"]),
        ("교육장소", item["venue"]),
        ("승인방식", "자동승인"),
        ("온라인 정원/대기정원", item["capacity"]),
        ("신청현황", item["current_capacity"]),
        ("수강료", item["fee"]),
    )
    rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in values)
    application = ""
    if item["status"] == "접수중" and not omit_application:
        application = (
            f'<a class="button reserve" href="?amode=agree&amp;{source.key_name}='
            f'{source.key_value}&amp;lectureId={item["identity"]}">신청하기</a>'
        )
    return f"""
      <html><body><table class="t3">
        <caption>{title} 교육내용으로 교육분류 등 제공</caption>
        <tbody>{rows}</tbody>
      </table>{application}</body></html>
    """


def _yeyak_fixture(
    *,
    corrupt_sentinel: bool = False,
    duplicate_identity: bool = False,
    omit_application: bool = False,
    wrong_detail_title: bool = False,
):
    child = [
        _yeyak_item("LT9001", "현재 열린 강좌", current=True, status="접수중"),
        _yeyak_item("LT9002", "현재 마감 강좌", current=True),
    ]
    child.extend(
        _yeyak_item(f"LT{8000 - index}", f"지난 강좌 {index}", current=False)
        for index in range(8)
    )
    digital = [
        _yeyak_item(
            "LT7001",
            "예정 정보화 강좌",
            current=True,
            status="접수대기",
            source_code="digital_literacy",
            venue="정보화교육장",
        )
    ]
    resident = [
        _yeyak_item(
            "LT6001",
            "지난 주민 강좌",
            current=False,
            source_code="resident_centers",
            venue="상동면행정복지센터",
        )
    ]
    if duplicate_identity:
        digital[0]["identity"] = child[0]["identity"]

    by_source = {
        "child_learning": child,
        "digital_literacy": digital,
        "city_library": [],
        "womens_center": [],
        "resident_centers": resident,
    }
    pages: dict[tuple[str, int], str] = {}
    details: dict[str, str] = {}
    for source in miryang.MIRYANG_YEYAK_SOURCES:
        items = by_source[source.code]
        last = max(1, math.ceil(len(items) / miryang.MIRYANG_YEYAK_PAGE_SIZE))
        for page in range(1, last + 1):
            start = (page - 1) * miryang.MIRYANG_YEYAK_PAGE_SIZE
            page_items = items[start : start + miryang.MIRYANG_YEYAK_PAGE_SIZE]
            pages[(source.code, page)] = _yeyak_list_page(
                source, page_items, total=len(items), last=last
            )
        final_items = items[(last - 1) * miryang.MIRYANG_YEYAK_PAGE_SIZE :]
        sentinel_items = list(final_items)
        if corrupt_sentinel and source.code == "child_learning":
            sentinel_items = [dict(final_items[0], identity="LT9999")]
        pages[(source.code, last + 1)] = _yeyak_list_page(
            source, sentinel_items, total=len(items), last=last
        )
        for item in items:
            if item["end"].startswith("2099"):
                details[item["identity"]] = _yeyak_detail_page(
                    item,
                    omit_application=omit_application and item["identity"] == "LT9001",
                    wrong_title=wrong_detail_title and item["identity"] == "LT9001",
                )

    calls: list[str] = []
    sessions: list[DummySession] = []

    def make_session() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    def fetch(_session: DummySession, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        calls.append(url)
        parsed = urlparse(url)
        source = next(source for source in miryang.MIRYANG_YEYAK_SOURCES if source.path == parsed.path)
        query = parse_qs(parsed.query)
        if query.get("amode") == ["view"]:
            return BeautifulSoup(details[query["lectureId"][0]], "lxml")
        page = int((query.get("cpage") or ["1"])[0])
        return BeautifulSoup(pages[(source.code, page)], "lxml")

    return fetch, make_session, calls, sessions


def _lifelong_items(total: int = 12) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index in range(total):
        current = index < 2
        result.append(
            {
                "identity": str(300 - index),
                "title": f"밀양 평생강좌 {index}",
                "branch": "밀양시",
                "apply_start": "2099-06-01" if current else "2020-01-01",
                "apply_end": "2099-06-30" if current else "2020-01-31",
                "start": "2099-07-01" if current else "2020-02-01",
                "end": "2099-08-31" if current else "2020-03-01",
                "status": "접수완료",
                "venue": f"평생학습센터{index + 1}",
                "schedule": "매주 월 14:00 ~ 16:00",
            }
        )
    return result


def _lifelong_page(items: list[dict[str, str]], *, total: int, last: int) -> str:
    rows = "".join(
        f"""
        <tr><td>{total - index}</td><td>{item['branch']}</td>
          <td><a href="default.php?mod=o&amp;idx={item['identity']}&amp;ci=&amp;kind=&amp;ky=&amp;wd=&amp;st=&amp;page=1">{item['title']}</a>
              모집인원 : 15(+10)명 / 정원 40명</td>
          <td><span class="s01">접수기간</span> <strong>{item['apply_start']} ~ {item['apply_end']}</strong>
              <span class="s02">교육기간</span> {item['start']} ~ {item['end']}</td>
          <td>{item['status']}</td></tr>
        """
        for index, item in enumerate(items)
    )
    pager = "".join(f'<a href="default.php?page={page}">{page}</a>' for page in range(1, last + 1))
    return f"""
      <html><body><div>총 <em class="em">{total}</em> 개의 교육과정이 있습니다.</div>
        <table class="basic_edu"><thead><tr>
          <th>순번</th><th>구분</th><th>교육강좌명</th><th>접수 및 교육기간</th><th>접수방법</th>
        </tr></thead><tbody>{rows}</tbody></table><div class="pagination">{pager}</div>
      </body></html>
    """


def _lifelong_detail(item: dict[str, str], *, wrong_title: bool = False) -> str:
    title = "다른 강좌" if wrong_title else item["title"]
    description = (
        f"{title} 안내 / ○ 교육기간: {item['start']} ~ {item['end']} / "
        f"○ 교육대상: 밀양시민 15명 / ○ 교육장소: {item['venue']} / "
        f"○ 수 강 료: 무료 / {item['schedule']}"
    )
    values = (
        ("위치", ""),
        ("접수기간", f"{item['apply_start']} ~ {item['apply_end']}"),
        ("교육기간", f"{item['start']} ~ {item['end']}"),
        ("접수처", "055-359-6005"),
        ("교육시간", ""),
        ("수강료", "무료"),
        ("준비물", ""),
        ("모집대상", "밀양시민 15명"),
        ("모집방법", ""),
        ("모집인원", "15(+10)명"),
        ("신청인원", "10명"),
        ("교육과정", description),
        ("수강안내", ""),
    )
    return "<html><body><table>" + "".join(
        f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in values
    ) + "</table></body></html>"


def _lifelong_fixture(*, nonempty_sentinel: bool = False, wrong_title: bool = False):
    items = _lifelong_items()
    last = math.ceil(len(items) / miryang.MIRYANG_LIFELONG_PAGE_SIZE)
    pages: dict[int, str] = {}
    for page in range(1, last + 1):
        start = (page - 1) * miryang.MIRYANG_LIFELONG_PAGE_SIZE
        pages[page] = _lifelong_page(
            items[start : start + miryang.MIRYANG_LIFELONG_PAGE_SIZE],
            total=len(items),
            last=last,
        )
    pages[last + 1] = _lifelong_page(
        [items[0]] if nonempty_sentinel else [], total=len(items), last=last
    )
    details = {
        item["identity"]: _lifelong_detail(
            item, wrong_title=wrong_title and item["identity"] == items[0]["identity"]
        )
        for item in items[:2]
    }
    calls: list[str] = []
    sessions: list[DummySession] = []

    def make_session() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    def fetch(_session: DummySession, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        calls.append(url)
        query = parse_qs(urlparse(url).query)
        if query.get("mod") == ["o"]:
            return BeautifulSoup(details[query["idx"][0]], "lxml")
        page = int((query.get("page") or ["1"])[0])
        return BeautifulSoup(pages[page], "lxml")

    return items, fetch, make_session, calls, sessions


def test_exact_canonical_targets_and_audited_aliases() -> None:
    assert miryang.is_miryang_yeyak_target(YeyakTarget())
    assert miryang.is_miryang_lifelong_target(LifelongTarget())
    assert not miryang.is_miryang_yeyak_target(
        YeyakTarget(url="http://yeyak.miryang.go.kr/")
    )
    assert not miryang.is_miryang_lifelong_target(
        LifelongTarget(url=miryang.MIRYANG_LIFELONG_URL + "?st=e")
    )
    assert "MUNI_YEYAK_MIRYANG_GO_KR_3800E0A0" not in {
        miryang.MIRYANG_YEYAK_PROVIDER,
        miryang.MIRYANG_LIFELONG_PROVIDER,
    }
    assert "00001/00044/00063.web" in miryang.MIRYANG_YEYAK_WRONG_CATEGORY_URLS[0]
    assert all("st=e" in url for url in miryang.MIRYANG_LIFELONG_DUPLICATE_FILTER_URLS)


def test_collects_complete_integrated_inventory_and_current_details() -> None:
    fetch, make_session, calls, sessions = _yeyak_fixture()
    rows, parser, meta = miryang.collect_miryang_yeyak_courses(
        YeyakTarget(),
        timeout=7,
        max_pages=11,
        detail_limit=3,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
        dedupe_rows=lambda values: values,
    )

    assert parser == miryang.MIRYANG_YEYAK_PARSER
    assert len(rows) == 3
    assert meta["source_total"] == 12
    assert meta["source_rows"] == 12
    assert meta["required_list_requests"] == 11
    assert meta["current_count"] == 3
    assert meta["expired_count"] == 9
    assert meta["detail_pages"] == 3
    assert meta["snapshot_complete"] is True
    assert set(meta["sentinel_modes"].values()) <= {"clamped_final_page", "empty"}
    assert meta["duplicate_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["application_url"] == miryang.miryang_yeyak_application_url(
        _source_by_code("child_learning"), "LT9001"
    )
    assert open_row["reservation_available"] is True
    scheduled = next(row for row in rows if row["status"] == "SCHEDULED")
    assert scheduled["branch"] == "시민정보화교육"
    assert "application_url" not in scheduled
    assert len(calls) == 14
    assert sessions and all(session.closed for session in sessions)


def test_integrated_caps_and_sentinel_fail_closed() -> None:
    fetch, make_session, *_ = _yeyak_fixture()
    rows, _, meta = miryang.collect_miryang_yeyak_courses(
        YeyakTarget(), timeout=7, max_pages=10, detail_limit=3,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    fetch, make_session, *_ = _yeyak_fixture(corrupt_sentinel=True)
    rows, _, meta = miryang.collect_miryang_yeyak_courses(
        YeyakTarget(), timeout=7, max_pages=11, detail_limit=3,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "sentinel" in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_integrated_detail_and_duplicate_fail_closed() -> None:
    fetch, make_session, *_ = _yeyak_fixture(omit_application=True)
    rows, _, meta = miryang.collect_miryang_yeyak_courses(
        YeyakTarget(), timeout=7, max_pages=11, detail_limit=3,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "no application URL" in meta["configured_collection_error"]

    fetch, make_session, *_ = _yeyak_fixture(duplicate_identity=True)
    rows, _, meta = miryang.collect_miryang_yeyak_courses(
        YeyakTarget(), timeout=7, max_pages=11, detail_limit=3,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert meta["duplicate_count"] == 1


def test_lifelong_collects_all_pages_empty_sentinel_and_current_details() -> None:
    items, fetch, make_session, calls, sessions = _lifelong_fixture()
    rows, parser, meta = miryang.collect_miryang_lifelong_courses(
        LifelongTarget(),
        timeout=7,
        max_pages=3,
        detail_limit=2,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
        dedupe_rows=lambda values: values,
    )

    assert parser == miryang.MIRYANG_LIFELONG_PARSER
    assert len(rows) == 2
    assert meta["source_total"] == len(items) == 12
    assert meta["page_counts"] == {1: 10, 2: 2, 3: 0}
    assert meta["sentinel_mode"] == "empty"
    assert meta["expired_count"] == 10
    assert meta["current_count"] == 2
    assert meta["detail_pages"] == 2
    assert meta["snapshot_complete"] is True
    assert rows[0]["branch"] == "평생학습센터1"
    assert rows[0]["schedule_raw"] == "매주 월 14:00 ~ 16:00"
    assert rows[0]["fee"] == "무료"
    assert "application_url" not in rows[0]
    assert len(calls) == 5
    assert sessions and all(session.closed for session in sessions)


def test_lifelong_caps_sentinel_and_detail_contract_fail_closed() -> None:
    _, fetch, make_session, *_ = _lifelong_fixture()
    rows, _, meta = miryang.collect_miryang_lifelong_courses(
        LifelongTarget(), timeout=7, max_pages=2, detail_limit=2,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert meta["source_cap_reached"] is True

    _, fetch, make_session, *_ = _lifelong_fixture(nonempty_sentinel=True)
    rows, _, meta = miryang.collect_miryang_lifelong_courses(
        LifelongTarget(), timeout=7, max_pages=3, detail_limit=2,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "sentinel page is not empty" in meta["configured_collection_error"]

    _, fetch, make_session, *_ = _lifelong_fixture(wrong_title=True)
    rows, _, meta = miryang.collect_miryang_lifelong_courses(
        LifelongTarget(), timeout=7, max_pages=3, detail_limit=2,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "detail title mismatch" in meta["configured_collection_error"]


def test_date_typo_correction_is_narrow_and_preserves_evidence() -> None:
    start, end, corrected = miryang._correct_yeyak_period(
        "LT002038", date(2026, 7, 28), date(2060, 8, 25)
    )
    assert start == date(2026, 7, 28)
    assert end == date(2026, 8, 25)
    assert corrected is True
    _, unchanged, corrected = miryang._correct_yeyak_period(
        "LT999999", date(2026, 7, 28), date(2060, 8, 25)
    )
    assert unchanged == date(2060, 8, 25)
    assert corrected is False


def test_managed_injection_is_required_and_tls_is_never_disabled() -> None:
    rows, _, meta = miryang.collect_miryang_yeyak_courses(YeyakTarget())
    assert rows == []
    assert "managed fetcher" in meta["configured_collection_error"]
    rows, _, meta = miryang.collect_miryang_lifelong_courses(LifelongTarget())
    assert rows == []
    assert "managed fetcher" in meta["configured_collection_error"]

    source = inspect.getsource(miryang)
    assert "verify=False" not in source
    assert "verify = False" not in source
