from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from bs4 import BeautifulSoup

from Crawler import municipal_seongdong as seongdong


@dataclass(frozen=True)
class Target:
    provider: str
    url: str


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(kind: str = "education") -> Target:
    return Target(
        seongdong.SEONGDONG_INTEGRATED_PROVIDER,
        (
            seongdong.SEONGDONG_EXPERIENCE_URL
            if kind == "experience"
            else seongdong.SEONGDONG_INTEGRATED_URL
        ),
    )


def _education_items() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number in range(12, 0, -1):
        current = number in {12, 11, 10, 9, 7, 6}
        kind = "native"
        title = f"성동 교육 강좌 {number}"
        identity = str(9000 + number)
        href = (
            "./webEduDetail.do?"
            f"eduMngNo={identity}&amp;&amp;cpn=1&amp;key=4833"
        )
        branch = "성동 AI·미래기술체험센터"
        category = "정보화"
        if number == 12:
            branch = seongdong.SEONGDONG_DOKSEODANG_BRANCH
            category = "교양"
        elif number == 11:
            title = "이동노동자 대상 노무상담"
            category = "기타"
        elif number == 10:
            kind = "sports"
            title = "토요 수영 교실"
            identity = "SUNGDONG02:00414:R"
            href = (
                "http://sports.happysd.or.kr/fmcs/191?"
                "action=read&amp;comcd=SUNGDONG02&amp;classcd=00414&amp;type=R"
            )
            branch = "용답체육센터"
            category = "스포츠"
        elif number == 9:
            kind = "ccic"
            title = "음악으로 노올자"
            identity = "04:10776"
            href = (
                "https://ccic.sd.go.kr/main/main.php?categoryid=06&amp;"
                "menuid=04&amp;groupid=02&amp;board=view&amp;no=10776"
            )
            branch = "성동구육아종합지원센터"
            category = "영유아교육"
        elif number == 8:
            kind = "fifty_plus"
            title = "노후를 지키는 자산관리"
            identity = "73222494"
            href = "https://50plus.or.kr/sdc/education-detail.do?id=73222494"
            branch = "성동50플러스센터"
            category = "평생교육"
        elif number == 7:
            title = "2026 성동 AI 서포터즈 모집"
            category = "기타"
        rows.append(
            {
                "number": number,
                "identity": identity,
                "kind": kind,
                "title": title,
                "href": href,
                "branch": branch,
                "category": category,
                "apply_start": "2026-08-01" if current else "2026-06-01",
                "apply_end": "2026-08-20" if current else "2026-06-10",
                "start": "2026-08-05" if current else "2026-06-11",
                "end": "2026-08-31" if current else "2026-06-30",
                "venue": f"성동 교육장 {number}",
                "target": "성동구민",
                "capacity_current": 1,
                "capacity_total": 20,
                "status": "접수중" if current else "접수마감",
            }
        )
    return rows


def _experience_items() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number in range(4, 0, -1):
        current = number >= 2
        category = "육아" if number == 3 else "체험"
        title = (
            "공동육아나눔터 8월 공간 이용"
            if number == 3
            else f"성동 체험 프로그램 {number}"
        )
        rows.append(
            {
                "number": number,
                "identity": str(1000 + number),
                "title": title,
                "category": category,
                "branch": "공동육아나눔터" if number == 3 else "성동생명안전배움터",
                "apply_start": "2026-08-01" if current else "2026-06-01",
                "apply_end": "2026-08-20" if current else "2026-06-10",
                "start": "2026-08-05" if current else "2026-06-11",
                "end": "2026-08-31" if current else "2026-06-30",
                "venue": f"성동 체험장 {number}",
                "target": "청소년",
                "capacity_total": number + 2,
                "status": "접수중" if current else "접수마감",
            }
        )
    return rows


def _education_list_row(item: dict[str, Any]) -> str:
    return f"""
      <tr>
        <td>{item['number']}</td>
        <td><a href="{item['href']}">{item['title']}</a></td>
        <td>{item['apply_start']} ~ {item['apply_end']}<br>
            {item['start']} ~ {item['end']}</td>
        <td>{item['venue']}</td><td>{item['target']}</td>
        <td>{item['status']}</td>
        <td>{item['capacity_current']} / {item['capacity_total']} (0 / 0)</td>
      </tr>
    """


def _education_list_page(items: list[dict[str, Any]]) -> str:
    return f"""
      <html><body><div class="p-pagination">
        <a href="?key=4833&amp;cpn=1">1</a>
        <a href="?key=4833&amp;cpn=2">2</a>
      </div><table><tbody>
      {''.join(_education_list_row(item) for item in items)}
      </tbody></table></body></html>
    """


def _experience_list_row(item: dict[str, Any]) -> str:
    return f"""
      <tr>
        <td>{item['number']}</td><td>2026</td>
        <td><a href="./webExcursionsProgramView.do?programNumber={item['identity']}&amp;key=4836">{item['title']}</a></td>
        <td>{item['apply_start']} ~ {item['apply_end']}<br>
            {item['start']} ~ {item['end']}</td>
        <td>{item['venue']}</td><td>{item['target']}</td><td>무료</td>
        <td>{item['capacity_total']}</td><td>선착순</td><td>{item['status']}</td>
      </tr>
    """


def _experience_list_page(items: list[dict[str, Any]]) -> str:
    return f"""
      <html><body><div class="p-pagination">
        <a href="./webExcursionsProgramList.do?key=4836&amp;pageUnit=9&amp;pageIndex=1">1</a>
      </div><table><tbody>
      {''.join(_experience_list_row(item) for item in items)}
      </tbody></table></body></html>
    """


def _native_detail(item: dict[str, Any], *, bad_dates: bool = False) -> str:
    end = "2026-09-01" if bad_dates else item["end"]
    return f"""
      <html><head><title>{item['title']} 상세내용 - 교육/강좌 -신속예약</title></head><body>
      <table><tbody>
        <tr><th>구분</th><td>{item['category']}</td></tr>
        <tr><th>운영기관</th><td>{item['branch']}</td></tr>
        <tr><th>대상</th><td>{item['target']}</td></tr>
        <tr><th>장소</th><td>{item['venue']}</td></tr>
        <tr><th>주소</th><td>서울 성동구 살곶이길 327</td></tr>
        <tr><th>접수기간</th><td>{item['apply_start']} ~ {item['apply_end']}</td></tr>
        <tr><th>운영기간</th><td>{item['start']} ~ {end}</td></tr>
        <tr><th>운영시간</th><td>10:00 ~ 12:00</td></tr>
        <tr><th>운영요일</th><td>토</td></tr>
        <tr><th>모집인원</th><td>{item['capacity_total']}명 (1/{item['capacity_total']})</td></tr>
        <tr><th>이용요금</th><td>무료</td></tr>
        <tr><th>선별방법</th><td>선착순</td></tr>
        <tr><th>예약방법</th><td>온라인 접수</td></tr>
      </tbody></table>
      <form method="post" action="./webEduRcept.do"><input name="applicant" value=""></form>
      </body></html>
    """


def _sports_detail(item: dict[str, Any]) -> str:
    return f"""
      <html><head><title>수강신청(교육/강좌 상세) &lt; 온라인신청</title></head><body>
      <table><tbody>
        <tr><th>수강신청 상태</th><td>기존회원 : {item['apply_start']} 07:00 ~ {item['apply_end']} 23:59</td></tr>
        <tr><th>강좌명</th><td>{item['title']}</td></tr>
        <tr><th>운영센터</th><td>{item['branch']} /</td></tr>
        <tr><th>교육기간</th><td>{item['start']} ~ {item['end']}</td></tr>
        <tr><th>시간/요일</th><td>12:00 ~ 12:50 / 토</td></tr>
        <tr><th>교육대상</th><td>{item['target']}</td></tr>
        <tr><th>강습장소</th><td>수영장</td></tr>
        <tr><th>접수방식</th><td>대기접수(배정승인)</td></tr>
      </tbody></table>
      <form method="post" action="?action=write"><input name="SecurityToken" value="secret"></form>
      </body></html>
    """


def _ccic_detail(item: dict[str, Any]) -> str:
    return f"""
      <html><head><title>성동구육아종합지원센터</title></head><body><table><tbody>
        <tr><th>행사명</th><td>{item['title']}</td></tr>
        <tr><th>정원</th><td>정원 : 0 / 6</td></tr>
        <tr><th>행사일시</th><td>{item['start']} ~ {item['end']} / 16:00 ~ 16:40</td></tr>
        <tr><th>신청기간</th><td>{item['apply_start']} 10:00 ~ {item['apply_end']} 18:00</td></tr>
        <tr><th>행사장소</th><td>{item['venue']}</td><th>행사대상</th><td>{item['target']}</td></tr>
      </tbody></table></body></html>
    """


def _experience_detail(item: dict[str, Any]) -> str:
    return f"""
      <html><head><title>{item['title']} 상세 - 체험/견학 -신속예약</title></head><body>
      <table><tbody>
        <tr><th>구분</th><td>{item['category']}</td></tr>
        <tr><th>운영기관</th><td>{item['branch']}</td></tr>
        <tr><th>대상</th><td>{item['target']}</td></tr>
        <tr><th>장소</th><td>{item['venue']}</td></tr>
        <tr><th>접수기간</th><td>{item['apply_start']} ~ {item['apply_end']}</td></tr>
        <tr><th>운영기간</th><td>{item['start']} ~ {item['end']}</td></tr>
        <tr><th>운영시간</th><td>10:00 ~ 12:00</td></tr>
        <tr><th>운영요일</th><td>토요일</td></tr>
        <tr><th>모집인원(회차별)</th><td>{item['capacity_total']}</td></tr>
        <tr><th>이용요금</th><td>무료</td></tr>
        <tr><th>선별방법</th><td>선착순</td></tr>
        <tr><th>예약방법</th><td>온라인 접수</td></tr>
      </tbody></table>
      <a href="./webExcursionsProgramReqst.do?programNumber={item['identity']}">신청</a>
      </body></html>
    """


def _fixture(*, malformed_numbering: bool = False, bad_detail_dates: bool = False):
    education = _education_items()
    experience = _experience_items()
    mapping = {
        seongdong.seongdong_education_list_url(1): _education_list_page(education[:9]),
        seongdong.seongdong_education_list_url(2): _education_list_page(education[9:]),
        seongdong.seongdong_experience_list_url(1): _experience_list_page(experience),
    }
    if malformed_numbering:
        mapping[seongdong.seongdong_education_list_url(2)] = mapping[
            seongdong.seongdong_education_list_url(2)
        ].replace("<td>3</td>", "<td>2</td>", 1)

    for item in education:
        if item["number"] not in {12, 11, 10, 9, 7, 6}:
            continue
        if item["kind"] == "native":
            url = seongdong.seongdong_education_detail_url(
                item["identity"], page=1 if item["number"] >= 4 else 2
            )
            mapping[url] = _native_detail(
                item,
                bad_dates=bad_detail_dates and item["number"] == 12,
            )
        elif item["kind"] == "sports":
            mapping[
                "https://sports.happysd.or.kr/fmcs/191?"
                "action=read&comcd=SUNGDONG02&classcd=00414&type=R"
            ] = _sports_detail(item)
        elif item["kind"] == "ccic":
            mapping[
                "https://ccic.sd.go.kr/main/main.php?"
                "categoryid=06&menuid=04&groupid=02&board=view&no=10776"
            ] = _ccic_detail(item)

    for item in experience:
        if item["number"] >= 2:
            mapping[
                seongdong.seongdong_experience_detail_url(item["identity"])
            ] = _experience_detail(item)

    sessions: list[DummySession] = []
    calls: list[str] = []

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        return mapping[url]

    def make_session() -> DummySession:
        session = DummySession()
        sessions.append(session)
        return session

    return education, experience, mapping, fetch, make_session, sessions, calls


def test_exact_integrated_target_and_public_url_builders() -> None:
    assert seongdong.SEONGDONG_INTEGRATED_PROVIDER.endswith("A8C20229")
    assert seongdong.SEONGDONG_INTEGRATED_URL.endswith("webEduList.do?key=4833")
    assert seongdong.is_seongdong_integrated_target(_target())
    assert seongdong.is_seongdong_integrated_target(_target("experience"))
    assert not seongdong.is_seongdong_integrated_target(
        Target("MUNI_WRONG", seongdong.SEONGDONG_INTEGRATED_URL)
    )
    assert not seongdong.is_seongdong_integrated_target(
        Target(
            seongdong.SEONGDONG_INTEGRATED_PROVIDER,
            seongdong.SEONGDONG_INTEGRATED_URL + "&cpn=1",
        )
    )
    assert seongdong.seongdong_education_list_url(2).endswith("key=4833&cpn=2")
    assert seongdong.seongdong_experience_list_url(3).endswith(
        "key=4836&pageUnit=9&pageIndex=3"
    )
    assert "eduMngNo=823" in seongdong.seongdong_education_detail_url("823")
    assert "programNumber=1004" in seongdong.seongdong_experience_detail_url("1004")
    assert seongdong.seongdong_education_detail_url("823&admin=true") == ""
    assert not seongdong._allowed_public_url(
        "https://sports.happysd.or.kr/fmcs/191?"
        "action=write&comcd=SUNGDONG02&classcd=00414&type=R"
    )
    assert not seongdong._allowed_public_url(
        "https://www.sd.go.kr/booking/webExcursionsProgramReqst.do?programNumber=1004"
    )


def test_ccic_menu_05_public_event_detail_is_an_exact_education_identity() -> None:
    item = next(row.copy() for row in _education_items() if row["kind"] == "ccic")
    item.update(
        {
            "identity": "05:10796",
            "title": "도전! 성동아빠 육아골든벨",
            "href": (
                "https://ccic.sd.go.kr/main/main.php?categoryid=06&amp;"
                "menuid=05&amp;groupid=02&amp;board=view&amp;no=10796"
            ),
        }
    )
    parsed = seongdong._parse_education_page(
        BeautifulSoup(_education_list_page([item]), "html.parser"),
        1,
    )[0]

    assert parsed["detail_kind"] == "ccic_education"
    assert parsed["identity"] == "05:10796"
    assert parsed["identity_fields"] == {
        "categoryid": "06",
        "menuid": "05",
        "groupid": "02",
        "board": "view",
        "no": "10796",
    }
    assert seongdong._allowed_public_url(parsed["detail_url"])

    row, reason = seongdong._detail_result(
        _target(),
        parsed,
        BeautifulSoup(_ccic_detail(item), "html.parser"),
    )
    assert reason == ""
    assert row is not None
    assert row["provider_course_id"].endswith(":ccic:05:10796")
    assert row["title"] == item["title"]
    assert not seongdong._allowed_public_url(
        parsed["detail_url"].replace("menuid=05", "menuid=06")
    )


def test_complete_integrated_snapshot_locks_categories_and_preserves_owner_identity() -> None:
    _education, _experience, _mapping, fetch, make_session, sessions, calls = _fixture()
    education_rows, parser, education_meta = seongdong.collect_seongdong_integrated_courses(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today=date(2026, 8, 5),
        max_pages=2,
        detail_limit=6,
        max_workers=1,
    )
    experience_rows, experience_parser, experience_meta = (
        seongdong.collect_seongdong_integrated_courses(
            _target("experience"),
            fetcher=fetch,
            session_factory=make_session,
            today=date(2026, 8, 5),
            max_pages=1,
            detail_limit=3,
            max_workers=1,
        )
    )
    rows = education_rows + experience_rows

    assert parser == experience_parser == seongdong.SEONGDONG_PARSER
    assert len(education_rows) == 4
    assert len(experience_rows) == 2
    assert len(rows) == 6
    assert len({row["provider_course_id"] for row in rows}) == 6
    assert any(
        row["provider_course_id"].endswith(":eduMngNo:9012")
        and row["branch_code"] == "SEONGDONG_EDU_AGENCY_305"
        for row in rows
    )
    assert any(":sports:SUNGDONG02:00414:R" in row["provider_course_id"] for row in rows)
    assert any(":ccic:04:10776" in row["provider_course_id"] for row in rows)
    assert any(":programNumber:1004" in row["provider_course_id"] for row in rows)
    assert {row["domain_category"] for row in education_rows} == {"교육·강좌"}
    assert {row["service_group"] for row in education_rows} == {"공공강좌"}
    assert {row["domain_category"] for row in experience_rows} == {"체험·견학"}
    assert {row["service_group"] for row in experience_rows} == {"체험"}
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["application_url"] == row["raw_url"] for row in rows)
    assert all("SecurityToken" not in row["description"] for row in rows)
    assert all("상담" not in row["title"] and "모집" not in row["title"] for row in rows)
    assert all("공간 이용" not in row["title"] for row in rows)

    assert education_meta["catalogue_kind"] == "education"
    assert education_meta["source_total"] == education_meta["education_source_total"] == 12
    assert education_meta["experience_source_total"] == 0
    assert education_meta["pages"] == education_meta["declared_pages"] == 2
    assert education_meta["current_candidate_count"] == 6
    assert education_meta["detail_pages"] == education_meta["detail_required_count"] == 6
    assert education_meta["current_count"] == education_meta["education_current_count"] == 4
    assert education_meta["experience_current_count"] == 0
    assert education_meta["excluded_non_course_counts"] == {
        "counselling": 1,
        "recruitment": 1,
    }
    assert education_meta["legacy_subset_identity_preserved_count"] == 1

    assert experience_meta["catalogue_kind"] == "experience"
    assert experience_meta["source_total"] == experience_meta["experience_source_total"] == 4
    assert experience_meta["education_source_total"] == 0
    assert experience_meta["pages"] == experience_meta["declared_pages"] == 1
    assert experience_meta["current_candidate_count"] == 3
    assert experience_meta["detail_pages"] == experience_meta["detail_required_count"] == 3
    assert experience_meta["current_count"] == experience_meta["experience_current_count"] == 2
    assert experience_meta["education_current_count"] == 0
    assert experience_meta["excluded_non_course_counts"] == {
        "experience_category:육아": 1,
    }
    for meta in (education_meta, experience_meta):
        assert meta["pagination_complete"] is True
        assert meta["details_complete"] is True
        assert meta["snapshot_complete"] is True
        assert meta["application_endpoint_requests"] == 0
        assert meta["application_form_submissions"] == 0
    assert not any("Reqst" in url or "action=write" in url for url in calls)
    assert len(calls) == 3 + 9
    assert len(sessions) == 2 + 9
    assert all(session.closed for session in sessions)


def test_page_and_detail_caps_fail_closed_without_partial_rows() -> None:
    _edu, _exp, _mapping, fetch, make_session, sessions, calls = _fixture()
    rows, _parser, meta = seongdong.collect_seongdong_integrated_courses(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-08-05",
        max_pages=1,
        detail_limit=9,
        max_workers=1,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages=1" in meta["configured_collection_error"]
    assert calls == [seongdong.seongdong_education_list_url(1)]
    assert all(session.closed for session in sessions)

    _edu, _exp, _mapping, fetch, make_session, _sessions, calls = _fixture()
    rows, _parser, meta = seongdong.collect_seongdong_integrated_courses(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-08-05",
        max_pages=2,
        detail_limit=5,
        max_workers=1,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert "detail_limit=5" in meta["configured_collection_error"]
    assert calls == [
        seongdong.seongdong_education_list_url(1),
        seongdong.seongdong_education_list_url(2),
    ]


def test_numbering_or_detail_drift_fails_the_catalogue_snapshot_closed() -> None:
    _edu, _exp, _mapping, fetch, make_session, _sessions, _calls = _fixture(
        malformed_numbering=True
    )
    rows, _parser, meta = seongdong.collect_seongdong_integrated_courses(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-08-05",
        max_pages=2,
        detail_limit=6,
        max_workers=1,
    )
    assert rows == []
    assert "not continuous" in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False

    _edu, _exp, _mapping, fetch, make_session, _sessions, _calls = _fixture(
        bad_detail_dates=True
    )
    rows, _parser, meta = seongdong.collect_seongdong_integrated_courses(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-08-05",
        max_pages=2,
        detail_limit=6,
        max_workers=1,
    )
    assert rows == []
    assert "dates mismatch" in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_noncanonical_target_and_identity_removing_dedupe_fail_closed() -> None:
    _edu, _exp, _mapping, fetch, make_session, _sessions, calls = _fixture()
    rows, _parser, meta = seongdong.collect_seongdong_integrated_courses(
        Target("MUNI_OTHER", seongdong.SEONGDONG_INTEGRATED_URL),
        fetcher=fetch,
        session_factory=make_session,
        today="2026-08-05",
    )
    assert rows == [] and calls == []
    assert "canonical" in meta["configured_collection_error"]

    rows, _parser, meta = seongdong.collect_seongdong_integrated_courses(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        dedupe_rows=lambda values: values[:-1],
        today="2026-08-05",
        max_pages=2,
        detail_limit=6,
        max_workers=1,
    )
    assert rows == []
    assert "deduplication removed" in meta["configured_collection_error"]
