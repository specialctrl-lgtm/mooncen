from __future__ import annotations

from dataclasses import dataclass

import pytest

from Crawler import Crawler_Homeplus as homeplus_module


@dataclass
class FakeResponse:
    text: str


def _result_html(total: int, lecture_ids: list[str]) -> str:
    items = "".join(f'<li id="liLecture_{lecture_id}"></li>' for lecture_id in lecture_ids)
    return f'<div id="divTotalCnt">{total}</div><ul>{items}</ul>'


def _crawler(stores: list[dict[str, str]]) -> homeplus_module.HomeplusCrawler:
    crawler = homeplus_module.HomeplusCrawler.__new__(homeplus_module.HomeplusCrawler)
    crawler.list_url = "https://mschool.homeplus.co.kr/Lecture/SearchResult"
    crawler.search_api_url = "https://mschool.homeplus.co.kr/Lecture/GetSearchResult"
    crawler.had_errors = False
    crawler.crawl_complete = True
    crawler._get_store_lookup = lambda: {
        store["StoreCode"]: store
        for store in stores
    }
    crawler._homeplus_headers = lambda **_kwargs: {}
    crawler.apply_branch_reception_period = lambda *_args, **_kwargs: None
    return crawler


def test_homeplus_api_uses_store_scopes_and_stops_at_each_store_total(monkeypatch: pytest.MonkeyPatch) -> None:
    stores = [
        {"StoreCode": "0001", "StoreName": "첫번째점"},
        {"StoreCode": "0002", "StoreName": "두번째점"},
    ]
    crawler = _crawler(stores)
    first_page_ids = [f"A{index:02d}" for index in range(20)]
    pages = {
        ("0001", 1): (21, first_page_ids),
        ("0001", 2): (21, ["A20"]),
        ("0002", 1): (1, ["B00"]),
    }
    lecture_store = {
        **{lecture_id: "0001" for lecture_id in [*first_page_ids, "A20"]},
        "B00": "0002",
    }
    requests: list[dict[str, object]] = []
    saved: list[str] = []

    def request(method: str, _url: str, **kwargs) -> FakeResponse:
        if method == "GET":
            return FakeResponse("")
        data = kwargs["data"]
        requests.append(data)
        key = (str(data["prm[0][Data][StoreCode]"]), int(data["page"]))
        total, lecture_ids = pages[key]
        return FakeResponse(_result_html(total, lecture_ids))

    def parse(item, _branch_cache):
        lecture_id = item["id"].replace("liLecture_", "")
        store_code = lecture_store[lecture_id]
        return {
            "provider_course_id": f"{store_code}:{lecture_id}",
            "branch_code": store_code,
            "branch": f"{store_code}점",
            "raw_url": f"https://mschool.homeplus.co.kr/Lecture/Detail?LectureMasterID={lecture_id}",
        }

    crawler._request_with_retry = request
    crawler._parse_course_item = parse
    crawler.save_course = lambda course: saved.append(course["provider_course_id"]) or True
    monkeypatch.setenv("HOMEPLUS_DETAIL_LIMIT", "0")
    monkeypatch.setattr(homeplus_module.time, "sleep", lambda _seconds: None)

    assert crawler.scrape_courses_api() == 22
    assert len(saved) == 22
    assert crawler.crawl_complete is True
    assert crawler.had_errors is False
    assert [
        (request["prm[0][Data][StoreCode]"], request["page"])
        for request in requests
    ] == [("0001", 1), ("0001", 2), ("0002", 1)]
    assert all("prm" not in request for request in requests)


def test_homeplus_api_rejects_a_response_that_ignores_the_store_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = _crawler([{"StoreCode": "0001", "StoreName": "첫번째점"}])
    crawler._request_with_retry = lambda method, _url, **_kwargs: (
        FakeResponse("") if method == "GET" else FakeResponse(_result_html(1, ["A00"]))
    )
    crawler._parse_course_item = lambda _item, _branch_cache: {
        "provider_course_id": "9999:A00",
        "branch_code": "9999",
        "branch": "다른점",
        "raw_url": "https://mschool.homeplus.co.kr/Lecture/Detail?LectureMasterID=A00",
    }
    crawler.save_course = lambda _course: True
    monkeypatch.setenv("HOMEPLUS_DETAIL_LIMIT", "0")

    with pytest.raises(ValueError, match="ignored its store filter"):
        crawler.scrape_courses_api()

    assert crawler.crawl_complete is False


def test_homeplus_api_requires_a_known_requested_branch() -> None:
    crawler = _crawler([{"StoreCode": "0001", "StoreName": "첫번째점"}])

    with pytest.raises(ValueError, match="requested branch was not found"):
        crawler._course_search_stores(branch_code_filter="9999")
