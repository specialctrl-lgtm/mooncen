"""
공용 데이터 파서.
target, schedule_raw 같은 자유 텍스트를 구조화된 값으로 정리한다.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple


DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]

NON_TARGET_AGE_PHRASE_RE = re.compile(
    r"0\s*(?:\uC138|\uC0B4)\s*\uBD80\uD130\s*\uC2DC\uC791(?:\uD558\uB294)?"
)


def strip_non_target_age_phrases(text: str) -> str:
    """Remove title phrases like '0세부터 시작하는' that are not age targets."""
    return NON_TARGET_AGE_PHRASE_RE.sub(" ", text or "")


def _age_group_from_month_range(min_month: Optional[int], max_month: Optional[int]) -> Optional[str]:
    month = max_month if max_month is not None else min_month
    if month is None:
        return None
    if month <= 23:
        return "INFANT"
    if month <= 83:
        return "TODDLER"
    if month <= 167:
        return "CHILD"
    if month <= 239:
        return "TEEN"
    if month <= 719:
        return "ADULT"
    return "SENIOR"


def _full_birth_year(value: str, current_year: Optional[int] = None) -> int:
    year = int(value)
    if year >= 1000:
        return year
    if current_year:
        century = current_year // 100 * 100
        candidate = century + year
        if candidate > current_year + 1:
            candidate -= 100
        return candidate
    return 2000 + year if year <= 30 else 1900 + year


def _birth_year_to_month_age(value: str, current_year: Optional[int] = None) -> Optional[int]:
    current = current_year or datetime.now().year
    year = _full_birth_year(value, current)
    age = current - year
    if 0 <= age <= 120:
        return age * 12
    return None


def _age_year_month_bounds(age: int) -> tuple[int, int]:
    start = age * 12
    return start, start + 11


def _ordered_age_bounds(
    minimum: Optional[int],
    maximum: Optional[int],
) -> tuple[Optional[int], Optional[int]]:
    if minimum is not None and maximum is not None and minimum > maximum:
        return maximum, minimum
    return minimum, maximum


def _explicit_age_month_range(value: object) -> tuple[Optional[int], Optional[int]]:
    text = strip_non_target_age_phrases(str(value or ""))
    if not text:
        return None, None
    current_year = datetime.now().year

    match = re.search(r"(\d{2,4})\s*(?:\ub144(?:\uc0dd)?)?\s*[~-]\s*\uc131\uc778", text)
    if match:
        month_age = _birth_year_to_month_age(match.group(1), current_year)
        if month_age is not None:
            return month_age, None

    match = re.search(r"\uc131\uc778\s*[~-]\s*(\d{2,4})\s*(?:\ub144(?:\uc0dd)?)?", text)
    if match:
        month_age = _birth_year_to_month_age(match.group(1), current_year)
        if month_age is not None:
            return month_age, None

    match = re.search(r"\ub9cc?\s*(\d{1,2})\s*\uc138\s*[~-]\s*\uc131\uc778", text)
    if match:
        return int(match.group(1)) * 12, None

    for pattern, mode in (
        (r"(\d{1,3})\s*\uac1c\uc6d4\s*[~-]\s*(\d{2,4})\s*\ub144\uc0dd", "month_birth"),
        (r"(\d{1,3})\s*\uac1c\uc6d4\s*[~-]\s*\ub9cc?\s*(\d{1,2})\s*\uc138", "month_year_range"),
        (r"\ub9cc?\s*(\d{1,2})\s*\uc138\s*[~-]\s*(\d{1,3})\s*\uac1c\uc6d4", "year_month_range"),
        (r"(\d{1,3})\s*[~-]\s*(\d{1,3})\s*\uac1c\uc6d4", "month_range"),
        (r"(\d{2,4})\s*[~-]\s*(\d{2,4})\s*\ub144\uc0dd", "birth_range"),
        (r"\ub9cc\s*(\d{1,2})\s*[~-]\s*(\d{1,2})\s*\uc138", "year_range"),
        (r"(\d{1,2})\s*[~-]\s*(\d{1,2})\s*\uc138", "year_range"),
        (r"\ucd08\ub4f1\s*(\d+)\s*[~-]\s*(\d+)\s*\ud559\ub144", "grade_range"),
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        if mode == "month_birth":
            month_age = int(match.group(1))
            birth_month = _birth_year_to_month_age(match.group(2), current_year)
            if birth_month is not None:
                return min(month_age, birth_month), max(month_age, birth_month)
        if mode == "month_year_range":
            month_age = int(match.group(1))
            year_min, year_max = _age_year_month_bounds(int(match.group(2)))
            return min(month_age, year_min), max(month_age, year_max)
        if mode == "year_month_range":
            year_min, year_max = _age_year_month_bounds(int(match.group(1)))
            month_age = int(match.group(2))
            return min(year_min, month_age), max(year_max, month_age)
        if mode == "month_range":
            return int(match.group(1)), int(match.group(2))
        if mode == "birth_range":
            first = _birth_year_to_month_age(match.group(1), current_year)
            second = _birth_year_to_month_age(match.group(2), current_year)
            if first is not None and second is not None:
                return min(first, second), max(first, second)
        if mode == "year_range":
            start_age = int(match.group(1))
            end_age = int(match.group(2))
            return start_age * 12, end_age * 12 + 11
        if mode == "grade_range":
            start_age = 6 + int(match.group(1))
            end_age = 6 + int(match.group(2))
            return start_age * 12, end_age * 12 + 11

    match = re.search(r"(\d{1,3})\s*\uac1c\uc6d4\s*(?:\uc774\uc0c1|\ubd80\ud130)", text)
    if match:
        return int(match.group(1)), None
    match = re.search(r"(\d{1,3})\s*\uac1c\uc6d4\s*(?:\uc774\ud558|\uae4c\uc9c0)", text)
    if match:
        return 0, int(match.group(1))
    match = re.search(r"(\d{1,3})\s*\uac1c\uc6d4", text)
    if match:
        month = int(match.group(1))
        return month, month
    match = re.search(r"(\d{2,4})\s*\ub144\uc0dd", text)
    if match:
        month = _birth_year_to_month_age(match.group(1), current_year)
        if month is not None:
            return month, month
    match = re.search(r"\ub9cc\s*(\d{1,2})\s*\uc138\s*(?:\uc774\uc0c1|\ubd80\ud130)", text)
    if match:
        return int(match.group(1)) * 12, None
    match = re.search(r"(\d{1,2})\s*\uc138\s*(?:\uc774\uc0c1|\ubd80\ud130)", text)
    if match:
        return int(match.group(1)) * 12, None
    match = re.search(r"(\d{1,2})\s*\uc138\s*(?:\uc774\ud558|\uae4c\uc9c0)", text)
    if match:
        return 0, int(match.group(1)) * 12 + 11
    match = re.search(r"(\d{1,2})\s*\uc138", text)
    if match:
        return _age_year_month_bounds(int(match.group(1)))
    match = re.search(r"\ucd08\ub4f1\s*(\d+)\s*\ud559\ub144", text)
    if match:
        return _age_year_month_bounds(6 + int(match.group(1)))
    return None, None


def explicit_age_month_range(value: object) -> tuple[Optional[int], Optional[int]]:
    return _ordered_age_bounds(*_explicit_age_month_range(value))


def parse_crawler_target(text: str, parser: Optional["TargetParser"] = None) -> Dict:
    target_parser = parser or TargetParser()
    parsed = target_parser.parse(text or "")
    min_month, max_month = explicit_age_month_range(text)
    explicit = min_month is not None or max_month is not None
    if explicit:
        parsed["min_age"] = min_month
        parsed["max_age"] = max_month
        parsed["age_group"] = _age_group_from_month_range(min_month, max_month) or parsed.get("age_group")
    else:
        parsed["min_age"] = None
        parsed["max_age"] = None
    parsed["age_is_explicit"] = explicit
    return parsed


class TargetParser:
    AGE_GROUP_KEYWORDS = {
        "INFANT": ["영아", "베이비", "아기", "infant", "baby"],
        "TODDLER": ["유아", "미취학", "유치원", "5세", "6세", "7세", "toddler", "preschool"],
        "CHILD": ["초등", "어린이", "아동", "child", "kids", "kid"],
        "TEEN": ["중등", "중학생", "고등", "청소년", "10대", "teen"],
        "ADULT": ["성인", "직장인", "일반", "주부", "엄마", "아빠", "adult"],
        "SENIOR": ["시니어", "어르신", "중장년", "실버", "senior"],
        "ALL": ["전체", "누구나", "전연령", "모든", "all"],
    }

    PARENT_KEYWORDS = ["보호자", "부모", "엄마", "아빠", "부모님", "엄마와", "아빠와", "함께"]
    TAG_KEYWORDS = ["직장인", "주부", "부모", "유아", "초등", "중등", "고등", "성인", "시니어"]
    GROUP_DEFAULTS = {
        "INFANT": {"min_age": 0, "max_age": 2, "tags": ["영아"]},
        "TODDLER": {"min_age": 3, "max_age": 7, "tags": ["유아"]},
        "CHILD": {"min_age": 8, "max_age": 13, "tags": ["초등"]},
        "TEEN": {"min_age": 14, "max_age": 19, "tags": ["청소년"]},
        "ADULT": {"min_age": 20, "max_age": 59, "tags": ["성인"]},
        "SENIOR": {"min_age": 60, "max_age": 120, "tags": ["시니어"]},
        "ALL": {"min_age": None, "max_age": None, "tags": ["전체"]},
    }

    def parse(self, text: str) -> Dict:
        if not text:
            return self._empty_result()

        source = strip_non_target_age_phrases(text.strip())
        result = self._empty_result()

        keyword_age_group = self._detect_age_group(source)

        ages = self._extract_months(source)
        if not ages:
            ages = self._extract_birth_year(source)
        if not ages:
            ages = self._extract_school_grade(source)
        if not ages:
            ages = self._extract_age_range(source)

        if ages:
            ages = _ordered_age_bounds(*ages)
            result["min_age"], result["max_age"] = ages
            result["age_group"] = self._age_range_to_group(result["min_age"], result["max_age"])
        else:
            result["age_group"] = keyword_age_group

        if result["age_group"] and result["min_age"] is None and result["max_age"] is None:
            defaults = self.GROUP_DEFAULTS.get(result["age_group"], {})
            result["min_age"] = defaults.get("min_age")
            result["max_age"] = defaults.get("max_age")

        result["with_parent"] = self._detect_parent(source)
        result["tags"] = self._extract_tags(source)
        if result["age_group"]:
            defaults = self.GROUP_DEFAULTS.get(result["age_group"], {})
            result["tags"] = sorted(set(result["tags"] + defaults.get("tags", [])))
        if result["with_parent"]:
            result["tags"] = sorted(set(result["tags"] + ["부모"]))
        return result

    def _empty_result(self) -> Dict:
        return {
            "age_group": None,
            "min_age": None,
            "max_age": None,
            "with_parent": False,
            "tags": [],
        }

    def _detect_age_group(self, text: str) -> Optional[str]:
        lowered = text.lower()
        if any(keyword in text for keyword in ["초등", "어린이", "아동"]):
            return "CHILD"
        for group, keywords in self.AGE_GROUP_KEYWORDS.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                return group
        return None

    def _extract_age_range(self, text: str) -> Optional[Tuple[int, int]]:
        match = re.search(r"(\d+)\s*세\s*[~-]\s*성인", text)
        if match:
            return int(match.group(1)), 120

        match = re.search(r"(\d+)\s*세\s*[~-]\s*(?:유치(?:원|부)?|미취학)", text)
        if match:
            return int(match.group(1)), 7

        match = re.search(r"(\d+)\s*세\s*[~-]\s*(?:초등(?:생|학생)?|아동|어린이)", text)
        if match:
            return int(match.group(1)), 13

        match = re.search(r"(\d+)\s*[~-]\s*(\d+)\s*세", text)
        if match:
            return int(match.group(1)), int(match.group(2))

        match = re.search(r"(\d+)\s*세\s*이상", text)
        if match:
            return int(match.group(1)), 120

        match = re.search(r"(\d+)\s*세", text)
        if match:
            age = int(match.group(1))
            return age, age

        return None

    def _extract_birth_year(self, text: str) -> Optional[Tuple[int, int]]:
        current_year = datetime.now().year

        match = re.search(r"(\d{4})\s*[~-]\s*(\d{4})\s*년생", text)
        if match:
            y1 = int(match.group(1))
            y2 = int(match.group(2))
            ages = [current_year - y1, current_year - y2]
            return min(ages), max(ages)

        match = re.search(r"(\d{2})\s*[~-]\s*(\d{2})\s*년생", text)
        if match:
            y1 = int(match.group(1))
            y2 = int(match.group(2))
            y1 += 2000 if y1 <= 30 else 1900
            y2 += 2000 if y2 <= 30 else 1900
            ages = [current_year - y1, current_year - y2]
            return min(ages), max(ages)

        match = re.search(r"(\d{4})\s*[~-]\s*(\d{2})\s*년생", text)
        if match:
            y1 = int(match.group(1))
            y2 = (y1 // 100) * 100 + int(match.group(2))
            ages = [current_year - y1, current_year - y2]
            return min(ages), max(ages)

        match = re.search(r"(\d{4})\s*년생", text)
        if match:
            age = current_year - int(match.group(1))
            return age, age

        return None

    def _extract_school_grade(self, text: str) -> Optional[Tuple[int, int]]:
        match = re.search(r"초등\s*(\d+)\s*[~-]\s*(\d+)\s*학년", text)
        if match:
            start_grade = int(match.group(1))
            end_grade = int(match.group(2))
            return 6 + start_grade, 6 + end_grade

        match = re.search(r"초등\s*(\d+)\s*학년", text)
        if match:
            age = 6 + int(match.group(1))
            return age, age

        if "중학생" in text or "중등" in text:
            return 13, 15
        if "고등학생" in text or "고등" in text:
            return 16, 18
        return None

    def _extract_months(self, text: str) -> Optional[Tuple[int, Optional[int]]]:
        match = re.search(r"(\d+)\s*개월\s*[~-]\s*(?:유치(?:원|부)?|미취학)", text)
        if match:
            start_month = int(match.group(1))
            return start_month // 12, 7

        match = re.search(r"(\d+)\s*개월\s*[~-]\s*(?:초등(?:생|학생)?|아동|어린이)", text)
        if match:
            start_month = int(match.group(1))
            return start_month // 12, 13

        match = re.search(r"(\d+)\s*[~-]\s*(\d+)\s*개월", text)
        if match:
            start_month = int(match.group(1))
            end_month = int(match.group(2))
            start_age = start_month // 12
            end_age = max((end_month + 11) // 12, start_age)
            return start_age, end_age

        match = re.search(r"(\d+)\s*개월\s*이상", text)
        if match:
            start_month = int(match.group(1))
            return start_month // 12, None

        match = re.search(r"(\d+)\s*개월\s*이하", text)
        if match:
            end_month = int(match.group(1))
            return 0, max((end_month + 11) // 12, 0)

        match = re.search(r"(\d+)\s*개월", text)
        if match:
            month = int(match.group(1))
            age = month // 12
            return age, age
        return None

    def _detect_parent(self, text: str) -> bool:
        return any(keyword in text for keyword in self.PARENT_KEYWORDS)

    def _extract_tags(self, text: str) -> List[str]:
        tags = [keyword for keyword in self.TAG_KEYWORDS if keyword in text]
        return sorted(set(tags))

    def _age_to_group(self, age: Optional[int]) -> Optional[str]:
        if age is None:
            return None
        if age <= 2:
            return "INFANT"
        if age <= 7:
            return "TODDLER"
        if age <= 13:
            return "CHILD"
        if age <= 19:
            return "TEEN"
        if age <= 59:
            return "ADULT"
        return "SENIOR"

    def _age_range_to_group(self, min_age: Optional[int], max_age: Optional[int]) -> Optional[str]:
        if min_age is None:
            return None
        if min_age == 2 and max_age is None:
            return "TODDLER"
        if max_age is not None and min_age <= 13 and max_age >= 20:
            return "ALL"
        if max_age is not None and min_age <= 2 < max_age <= 7:
            return "TODDLER"
        if max_age is not None and min_age <= 7 < max_age <= 13:
            return "CHILD"
        return self._age_to_group(min_age)


class ScheduleParser:
    def parse(self, text: str) -> Dict:
        if not text:
            return self._empty_result()

        source = text.strip()
        result = self._empty_result()
        result["days"] = self._extract_days(source)

        times = self._extract_time_range(source)
        if times:
            duration_minutes = self._duration_minutes(*times)
            normalized_times = tuple(self._normalize_database_time(value) for value in times)
            if duration_minutes and duration_minutes > 0 and all(normalized_times):
                result["time_start"], result["time_end"] = normalized_times
                result["duration_minutes"] = duration_minutes

        result["frequency"] = self._extract_frequency(source)
        return result

    def _empty_result(self) -> Dict:
        return {
            "days": [],
            "time_start": None,
            "time_end": None,
            "frequency": "WEEKLY",
            "duration_minutes": None,
        }

    def _extract_days(self, text: str) -> List[str]:
        return [day for day in DAY_NAMES if day in text]

    def _extract_time_range(self, text: str) -> Optional[Tuple[str, str]]:
        match = re.search(r"(\d{1,2}):(\d{2})\s*[~-]\s*(\d{1,2}):(\d{2})", text)
        if match:
            sh, sm, eh, em = match.groups()
            return f"{int(sh):02d}:{sm}:00", f"{int(eh):02d}:{em}:00"

        match = re.search(r"(오전|오후)\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", text)
        if match:
            meridiem, hour, minute = match.groups()
            h = int(hour)
            m = int(minute or 0)
            if meridiem == "오후" and h < 12:
                h += 12
            if meridiem == "오전" and h == 12:
                h = 0
            start = f"{h:02d}:{m:02d}:00"
            end = f"{min(h + 1, 23):02d}:{m:02d}:00"
            return start, end

        match = re.search(r"(\d{1,2})\s*시\s*[~-]\s*(\d{1,2})\s*시", text)
        if match:
            sh, eh = match.groups()
            return f"{int(sh):02d}:00:00", f"{int(eh):02d}:00:00"

        match = re.search(r"(\d{1,2})\s*[~-]\s*(\d{1,2})\s*시", text)
        if match:
            sh, eh = match.groups()
            return f"{int(sh):02d}:00:00", f"{int(eh):02d}:00:00"

        return None

    def _extract_frequency(self, text: str) -> str:
        if "격주" in text:
            return "BIWEEKLY"
        if "매월" in text or "월 1회" in text or "월간" in text:
            return "MONTHLY"
        if "비정기" in text:
            return "IRREGULAR"
        return "WEEKLY"

    def _duration_minutes(self, start_time: str, end_time: str) -> Optional[int]:
        try:
            start_h, start_m, _ = start_time.split(":")
            end_h, end_m, _ = end_time.split(":")
            start = int(start_h) * 60 + int(start_m)
            end = int(end_h) * 60 + int(end_m)
            duration = end - start
            return duration if duration > 0 else None
        except Exception:
            return None

    def _normalize_database_time(self, value: str) -> Optional[str]:
        try:
            hour_text, minute_text, second_text = value.split(":")
            hour = int(hour_text)
            minute = int(minute_text)
            second = int(second_text)
        except (TypeError, ValueError):
            return None

        if not 0 <= hour <= 24 or not 0 <= minute <= 59 or not 0 <= second <= 59:
            return None
        if hour == 24:
            hour = 0
        return f"{hour:02d}:{minute:02d}:{second:02d}"
