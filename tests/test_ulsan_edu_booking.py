from __future__ import annotations

from Crawler import Crawler_MunicipalYaml as municipal


class FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self):
        self.pages: list[int] = []

    def get(self, _url: str, *, params: dict[str, str], **_kwargs) -> FakeResponse:
        page = int(params["q_currPage"])
        self.pages.append(page)
        if page == 1:
            return FakeResponse(
                """
                <div class="prgr-booking-list"><ul class="list">
                  <li class="item">
                    <a onclick="opViewReservation('123')"></a>
                    <div class="tit_wrap">
                      <span class="title">천문 관측 교실</span>
                      <span class="status">접수중</span>
                    </div>
                    <ul class="info">
                      <li><span class="tit">기관</span>울산과학관</li>
                      <li><span class="tit">운영기간</span>2099년 8월 1일</li>
                    </ul>
                  </li>
                </ul></div>
                <div class="pagination"><a onclick="opMovePage(2)">2</a></div>
                """
            )
        return FakeResponse('<div class="prgr-booking-list"><ul class="list"></ul></div>')


def target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="ULSAN_EDU_BOOKING",
        name="울산광역시교육청 통합예약",
        branch="울산광역시교육청 통합예약",
        url=municipal.ULSAN_EDU_BOOKING_LIST_URL,
        source="test",
        priority=1,
        region="울산광역시",
        extra={
            "collection_category": "평생학습",
            "domain_category": "평생학습",
            "source_group": "lifelong_learning",
        },
    )


def test_ulsan_official_institution_rows_split_museums_from_library_education() -> None:
    assert municipal.ulsan_booking_institution_metadata("울산과학관") == {
        "collection_category": "과학관",
        "domain_category": "과학관",
        "source_group": "museum_science",
        "service_group": "체험",
        "service_group_policy": "locked",
    }
    assert municipal.ulsan_booking_institution_metadata("울산시립미술관")[
        "collection_category"
    ] == "미술관"
    assert municipal.ulsan_booking_institution_metadata("울산중부도서관") == {
        "collection_category": "도서관",
        "domain_category": "도서관",
        "source_group": "library",
        "service_group": "공공강좌",
        "service_group_policy": "inferred",
    }


def test_ulsan_mixed_catalogue_does_not_promote_unrelated_branches() -> None:
    assert municipal.ulsan_booking_institution_metadata("울산진로진학지원센터") == {}
    assert municipal.ulsan_booking_institution_metadata("유아교육진흥원") == {}


def test_ulsan_collector_marks_only_a_naturally_exhausted_catalogue_complete(
    monkeypatch,
) -> None:
    exhausted = FakeSession()
    monkeypatch.setattr(municipal, "session", lambda: exhausted)
    rows, _, meta = municipal.collect_ulsan_edu_booking(
        target(),
        timeout=10,
        max_pages=10,
        detail_limit=0,
    )
    assert exhausted.pages == [1, 2]
    assert meta["pagination_complete"] is True
    assert meta["source_total"] == 1
    assert meta["expired_count"] == 0
    assert rows[0]["service_group"] == "체험"
    assert rows[0]["service_group_policy"] == "locked"

    capped = FakeSession()
    monkeypatch.setattr(municipal, "session", lambda: capped)
    _, _, capped_meta = municipal.collect_ulsan_edu_booking(
        target(),
        timeout=10,
        max_pages=1,
        detail_limit=0,
    )
    assert capped.pages == [1]
    assert capped_meta["pagination_complete"] is False
