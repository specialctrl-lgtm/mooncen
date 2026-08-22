from types import SimpleNamespace

from backend.routers.courses import _display_title, _readable_metadata


def test_course_title_decodes_entities_as_plain_response_text():
    course = SimpleNamespace(
        ai_title_result={},
        ai_title_processed=False,
        title="가족 &lt;생태&gt; 체험 &amp; 놀이",
    )

    assert _display_title(course) == "가족 <생태> 체험 & 놀이"


def test_mojibake_metadata_is_not_exposed_as_a_filter_label():
    assert _readable_metadata("??깃문??덈뮸") is None
    assert _readable_metadata("평생학습") == "평생학습"
