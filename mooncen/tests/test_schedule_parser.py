from data_parser import ScheduleParser


def test_schedule_parser_normalizes_end_time_after_midnight_for_database():
    parsed = ScheduleParser().parse("Monday 23:15 ~ 24:05")

    assert parsed["time_start"] == "23:15:00"
    assert parsed["time_end"] == "00:05:00"
    assert parsed["duration_minutes"] == 50


def test_schedule_parser_rejects_time_beyond_end_of_day():
    parsed = ScheduleParser().parse("Monday 23:15 ~ 25:05")

    assert parsed["time_start"] is None
    assert parsed["time_end"] is None
    assert parsed["duration_minutes"] is None
