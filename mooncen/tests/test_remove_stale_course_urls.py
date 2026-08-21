from __future__ import annotations

from requests import Response

from tools.maintenance.remove_stale_course_urls import classify_response


def make_response(status: int, body: str, url: str = "https://example.test/course/1") -> Response:
    response = Response()
    response.status_code = status
    response.url = url
    response._content = body.encode("utf-8")
    response.encoding = "utf-8"
    response.headers["content-type"] = "text/html; charset=utf-8"
    return response


def test_http_404_is_removable():
    verdict = classify_response("https://example.test/missing", "어린이 미술", make_response(404, "not found"))

    assert verdict.removable is True
    assert verdict.reason == "http_404"


def test_korean_missing_content_message_is_removable_when_title_absent():
    verdict = classify_response(
        "https://example.test/course/1",
        "어린이 미술",
        make_response(200, "<main>해당 강좌 정보가 존재하지 않습니다.</main>"),
    )

    assert verdict.removable is True
    assert verdict.reason == "gone_message"


def test_no_data_message_does_not_remove_when_title_is_present():
    verdict = classify_response(
        "https://example.test/course/1",
        "어린이 미술",
        make_response(200, "<main><h1>어린이 미술</h1><section>댓글 데이터가 없습니다.</section></main>"),
    )

    assert verdict.removable is False
    assert verdict.title_present is True


def test_lotte_review_empty_message_does_not_remove_live_course():
    verdict = classify_response(
        "https://example.test/course/1",
        "ECO오감놀이터 햇님이 방긋",
        make_response(
            200,
            """
            <main>
              <h1>(10주) ECO오감놀이터 햇님이 방긋 ※ 휴강</h1>
              <section>강좌소개 감각으로 탐색하고 몸으로 움직이는 재미있는 놀이 세상</section>
              <section>수강후기 정보가 존재하지 않습니다.</section>
            </main>
            """,
        ),
    )

    assert verdict.removable is False
    assert verdict.reason == "title_present"


def test_korean_short_title_tokens_match_live_course():
    verdict = classify_response(
        "https://example.test/course/1",
        "(6주) 리본 키즈 영어 뮤지컬",
        make_response(
            200,
            """
            <main>
              <h1>강좌 예술감각 7/18 리본 키즈 영어 뮤지컬</h1>
              <section>수강후기 정보가 존재하지 않습니다.</section>
            </main>
            """,
        ),
    )

    assert verdict.removable is False
    assert verdict.title_present is True


def test_empty_html_is_unknown_not_removable():
    verdict = classify_response("https://example.test/course/1", "어린이 미술", make_response(200, ""))

    assert verdict.removable is False
    assert verdict.state == "unknown"


def test_cancelled_course_message_is_not_removed_as_missing_content():
    verdict = classify_response(
        "https://example.test/course/1",
        "프리미엄 드럼",
        make_response(200, "<main>폐강되었습니다. 자세한 내용은 지점에 문의하세요.</main>"),
    )

    assert verdict.removable is False


def test_missing_supplies_message_does_not_remove_course():
    verdict = classify_response(
        "https://example.test/course/1",
        "트니트니 A",
        make_response(
            200,
            """
            <main>
              해당 강좌는 개강 후, 재료비 환불이 불가합니다.
              수강 신청 및 취소 환불 안내
              첫시간 안내사항 등록된 준비물이 없습니다.
            </main>
            """,
        ),
    )

    assert verdict.removable is False


def test_403_is_unknown_not_removable():
    verdict = classify_response("https://example.test/course/1", "어린이 미술", make_response(403, "blocked"))

    assert verdict.removable is False
    assert verdict.state == "unknown"
