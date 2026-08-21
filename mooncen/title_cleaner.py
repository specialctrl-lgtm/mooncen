import re
from typing import Tuple


DATE_TOKEN = r"(?<![\d./-])(?:1[0-2]|0?[1-9])[./](?:3[01]|[12]\d|0?[1-9])(?![\d./-])"
FULL_DATE_TOKEN = r"\d{4}[./-](?:1[0-2]|0?[1-9])[./-](?:3[01]|[12]\d|0?[1-9])"
COMPACT_DATE_TOKEN = r"\d{3,4}"
WEEKDAY_TOKEN = r"(?:\uC6D4|\uD654|\uC218|\uBAA9|\uAE08|\uD1A0|\uC77C)"
WEEKDAY_WORD_TOKEN = r"(?:\uC6D4\uC694|\uD654\uC694|\uC218\uC694|\uBAA9\uC694|\uAE08\uC694|\uD1A0\uC694|\uC77C\uC694)"
TIME_TOKEN = r"(?:[01]?\d|2[0-3])(?::[0-5]\d|\uC2DC(?:\s*[0-5]?\d\uBD84?)?)"
TIME_RANGE_TOKEN = rf"(?:{TIME_TOKEN}\s*[~-]\s*{TIME_TOKEN}|{TIME_TOKEN})"
ENGLISH_AGE_LABEL_TOKEN = r"(?:Baby|Toddler|Kids?|Child|Teen|Adult|Senior|All)"
TARGET_AGE_TOKEN = (
    r"(?:\d+\s*[~-]\s*\d+\s*(?:\uB144\uC0DD|\uAC1C\uC6D4)"
    r"|\d+\s*\uAC1C\uC6D4\s*[~-]\s*\uB9CC?\s*\d+\s*\uC138"
    r"|\uB9CC?\s*\d+\s*\uC138\s*[~-]\s*\d+\s*\uAC1C\uC6D4"
    r"|\d+\s*\uAC1C\uC6D4\s*(?:\uC774\uC0C1|\uC774\uD558|\uBD80\uD130)?"
    r"|\d+\s*\uC138\s*(?:\uC774\uC0C1|\uC774\uD558|\uBD80\uD130|\uAE4C\uC9C0)?"
    r"|\uB9CC\s*\d+\s*[~-]\s*\d+\s*\uC138"
    r"|\d{2,4}\s*\uB144\uC0DD\s*(?:\uC774\uC0C1|\uC774\uD558|\uBD80\uD130)?)"
)


def _squash(value: str) -> str:
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*[\(\[\{]\s*[\)\]\}]\s*", " ", value)
    value = re.sub(r"\s*[（［｛]\s*[）］｝]\s*", " ", value)
    value = re.sub(r"\s+([)\]])", r"\1", value)
    value = re.sub(r"([(\[])\s+", r"\1", value)
    value = re.sub(r"\s*([\u3163\u2502\uFF5C|])\s*", r"\1", value)
    value = re.sub(r"^\s*[-_:|\u2502\u3163\uFF5C\u00B7,.)\]\u2665\u2661\u2605\u2606\u25CE\u25CB\u25CF]+\s*", "", value)
    value = re.sub(r"\s*[-_:|\u2502\u3163\uFF5C\u00B7,<,([]+\s*$", "", value)
    return value.strip()


def clean_course_title(raw_title: str) -> Tuple[str, str]:
    """Return a display title and the schedule-ish fragments removed from it."""
    original = _squash(raw_title or "")
    title = original
    removed: list[str] = []

    def remove(pattern: str, repl: str = " ", flags: int = 0) -> None:
        nonlocal title

        def remember(match: re.Match) -> str:
            fragment = _squash(match.group(0))
            if fragment:
                removed.append(fragment)
            return repl

        title = re.sub(pattern, remember, title, flags=flags)
        title = _squash(title)

    def remove_target_age_keep_note() -> None:
        nonlocal title

        def remember(match: re.Match) -> str:
            content = match.group(1)
            if not re.search(TARGET_AGE_TOKEN, content):
                return match.group(0)

            note = re.sub(TARGET_AGE_TOKEN, " ", content)
            note = re.sub(rf"\s*/\s*{WEEKDAY_TOKEN}\s*/\s*{TIME_TOKEN}\s*", " ", note)
            note = re.sub(rf"\s*/\s*{TIME_TOKEN}\s*", " ", note)
            note = re.sub(rf"\b{WEEKDAY_TOKEN}\b", " ", note)
            note = re.sub(r"\s*[,/|ㅣ│]+\s*", " ", note)
            note = _squash(note)

            fragment = _squash(match.group(0))
            if fragment:
                removed.append(fragment)
            return f" {note} " if note else " "

        title = re.sub(r"[\(\[]([^)\]]*(?:\uB144\uC0DD|\uAC1C\uC6D4|\uC138)[^)\]]*)[\)\]]", remember, title)
        title = _squash(title)

    remove(r"^[*+]+\s*")
    remove(r"^\uBD04\s*[*]+\s*")
    remove(r"^\uC774\uC6D4\]\s*")
    remove(r"^(?:[\(\[]\s*(?:\d+\s*\uC8FC\s*)?\uC911\uB3C4\s*[\)\]]\s*)+")
    remove(
        rf"^(?:\[\s*(?:\d+\s*\uC8FC(?:\s*/\s*(?:{WEEKDAY_TOKEN}|\uC2E0\uC124))?|"
        rf"\uC2E0\uC124\s*/\s*\d+\s*\uC8FC|\d+\s*\uD68C(?:\s*\uC218\uAC15)?|ONLY|Only|only|"
        r"\uC815\uC6D0\s*\d+\s*\uBA85|1\s*DAY|1DAY|\uC6D0\uB370\uC774|\uD2B9\uAC15|"
        r"\uC815\uADDC\uD2B9\uAC15|\d{1,2}\s*\uC6D4\s*\uAC1C\uAC15|"
        r"\d{1,2}\s*\uC6D4\s*\uB2E8\uAE30\s*\uAC15\uC88C|\uB2E8\uAE30\s*\uAC15\uC88C|"
        r"\uAC00\uC131\uBE44\uAC15\uC88C)\s*\]\s*)+"
    )
    remove(r"^(?:\uAC1C\uAC15\uD655\uC815\s*)?(?:\d+\s*\uC8FC|\d+\s*\uD68C)\s+")
    remove(r"^(?:\uAC1C\uAC15\uD655\uC815\s*)?\d+\s*\uC8FC\s*[\u2605\u2606]\s*")
    remove(r"^\uAC1C\uAC15\uD655\uC815\s+")
    remove(r"^\s*[\(\[]\s*\uC2DC\uC791\s*[\)\]]\s*")
    remove(
        r"^(?:\[(?:\uCD94\uAC00|\uB85C\uBE44|\uB85C\uBE44\uC774\uBCA4\uD2B8|WITH MOM|\uAC1C\uAC15\uD655\uC815|ZOOM)\]\s*)+",
        flags=re.IGNORECASE,
    )

    remove(
        rf"^\[\s*{ENGLISH_AGE_LABEL_TOKEN}\s*\]\s*{DATE_TOKEN}\s*(?:\({WEEKDAY_TOKEN}\))?\s*(?:\uAC1C\uAC15)?\s*",
        flags=re.IGNORECASE,
    )
    remove(rf"^\[\s*{ENGLISH_AGE_LABEL_TOKEN}\s*\]\s*", flags=re.IGNORECASE)

    # Leading dates: [8/30], 5/17(일)10:10, 0516(토) 14:00, 6/28)
    remove(rf"^\[?\s*{FULL_DATE_TOKEN}\s*(?:[)\]]|\({WEEKDAY_TOKEN}\))?\s*(?:{TIME_RANGE_TOKEN})?\s*\]?\s*")
    remove(rf"^\[\s*{DATE_TOKEN}\s*\({WEEKDAY_TOKEN}\)\s*(?:{TIME_RANGE_TOKEN})?\s*/\s*", repl="[")
    remove(rf"^\[\s*{DATE_TOKEN}\s*(?:\uAC1C\uAC15|\uD734\uAC15)?\s*\]\s*")
    remove(rf"^\[?\s*{DATE_TOKEN}\s*(?:[)\]]|\({WEEKDAY_TOKEN}\))?\s*(?:{TIME_RANGE_TOKEN})?\s*\]?\s*")
    remove(rf"^\s*{COMPACT_DATE_TOKEN}\s*\({WEEKDAY_TOKEN}\)\s*(?:{TIME_RANGE_TOKEN})?\s*")
    remove(rf"^\[?\uC678\uBD80\s*{DATE_TOKEN}\]?\s*")
    remove(r"^\[\d{1,2}\uC6D4\]\s*")
    remove(r"^\[\s*\d{1,2}\s*\u6708\s*[-~]\s*\d+\s*\uAC15\s*\]\s*")

    # Malformed LOTTE prefixes: 목][4회], 토][11회], 11회]
    remove(rf"^{WEEKDAY_TOKEN}\s*<[^>]*\d+\s*\uD68C\s*>\s*")
    remove(rf"^{WEEKDAY_TOKEN}\]\s*\[\d+\s*\uD68C\]\s*")
    remove(rf"^[\[\(]?\s*{WEEKDAY_TOKEN}\s*[\]\)\u3163\u2502\uFF5C|:：_-]\s*")
    remove(r"^\d+\s*\uC8FC\]\s*")
    remove(r"^\d+\s*\uD68C\]\s*")
    remove(rf"\[\s*{DATE_TOKEN}\s*(?:\uAC1C\uAC15|\uD734\uAC15)?\s*\]")

    # A leading clock time is schedule data, but keep 1:1 class names intact.
    if not re.match(r"^\s*1\s*:\s*1\b", title):
        remove(rf"^{TIME_TOKEN}\s+")

    # Date/time tokens can appear after a category tag. Remove only the token,
    # not the following theme text.
    remove(rf"\s*{FULL_DATE_TOKEN}\s*\({WEEKDAY_TOKEN}\)\s*(?:{TIME_RANGE_TOKEN})?\s*")
    remove(rf"\s*{FULL_DATE_TOKEN}\s+{TIME_RANGE_TOKEN}\s*")
    remove(rf"\s*{FULL_DATE_TOKEN}\s*")
    remove(rf"\[\s*{DATE_TOKEN}\s*\({WEEKDAY_TOKEN}\)\s*(?:{TIME_RANGE_TOKEN})?\s*/\s*", repl="[")
    remove(rf"\s*{DATE_TOKEN}\s*\({WEEKDAY_TOKEN}\)\s*(?:{TIME_RANGE_TOKEN})?\s*")
    remove(rf"\s*{COMPACT_DATE_TOKEN}\s*\({WEEKDAY_TOKEN}\)\s*(?:{TIME_RANGE_TOKEN})?\s*")
    remove(rf"\s*{DATE_TOKEN}\s*(?:/|\u3163|\u2502|\uFF5C|\|)?\s*{WEEKDAY_TOKEN}\s*/\s*{TIME_TOKEN}\s*")
    remove(rf"\s*{DATE_TOKEN}\s+{TIME_RANGE_TOKEN}\s*")
    remove(rf"\s*{DATE_TOKEN}\s*")
    remove(rf"\s*[\u3163\u2502\uFF5C|]\s*{WEEKDAY_TOKEN}\s*{TIME_TOKEN}\s*[\u3163\u2502\uFF5C|]?\s*(?:\uB9CC)?\s*$")
    remove(rf"\s*{TIME_TOKEN}\s*[~-]\s*{TIME_TOKEN}\s*$")
    remove(rf"\s*{TIME_TOKEN}\s*[~-]\s*$")
    remove(rf"\s*{TIME_TOKEN}\s*$")
    remove(rf"\s*[\(\[]\s*{WEEKDAY_TOKEN}\s*[\)\]]\s*")
    remove(rf"(?<!\S){WEEKDAY_WORD_TOKEN}(?!\S)")
    remove(rf"(?<!\S){WEEKDAY_TOKEN}(?!\S)")
    remove(r"^\s*(?:\uD574\uD53C\uC544\uC6CC\s*[\u3163\u2502\uFF5C|]\s*)?(?:ONLY|Only|only)\s+")

    # Remove standalone schedule-y bracket blocks, but preserve age/grade tags.
    remove(
        r"\[(?:\uC6D4|\uD654|\uC218|\uBAA9|\uAE08|\uD1A0|\uC77C|\uC6D4,\uC218|\uD654,\uBAA9|\uC218,\uAE08|\uD1A0\uC694|\uC77C\uC694|\uD3C9\uC77C)[^\]]{0,12}\]\s*"
    )
    remove(r"\[(?:\d{1,2}\uC2DC|\d{1,2}:\d{2})\]\s*")
    if not re.match(r"^\s*1\s*:\s*1\b", title):
        remove(rf"^{TIME_TOKEN}\s+")

    # Category labels at the very front are not the course title.
    remove(
        r"^\[(?:\uC77C\uC694\uD2B9\uAC15|\uACFC\uD559\uD2B9\uAC15|\uB9E4\uC9C1\uC1FC\uACF5\uC5F0|\uC601\uC5B4\uBBA4\uC9C0\uCEEC|\uC77C\uC77C|\uB85C\uBE44|\uB85C\uBE44\uC774\uBCA4\uD2B8)\]\s*"
    )

    # Trailing bracket metadata such as [2020~22년생/일/10:00] belongs to
    # target/schedule fields, not the display title.
    remove(
        rf"\s*\[[^\]]*(?:\uB144\uC0DD|\uAC1C\uC6D4|\uC138)[^\]]*/\s*{WEEKDAY_TOKEN}\s*/\s*{TIME_TOKEN}\s*\]\s*$"
    )
    remove_target_age_keep_note()
    remove(rf"\s*{TARGET_AGE_TOKEN}\s*")
    remove(r"\s*[\(\[][^)\]]*\d+\s*[~-]\s*\d+\s*(?:\uB144\uC0DD|\uAC1C\uC6D4)[^)\]]*[\)\]]\s*")
    remove(r"\s*[\(\[][^)\]]*\d+\s*\uAC1C\uC6D4\s*(?:\uC774\uC0C1|\uC774\uD558|\uBD80\uD130)?[^)\]]*[\)\]]\s*")
    remove(r"\s*[\(\[][^)\]]*\d+\s*\uC138\s*(?:\uC774\uC0C1|\uC774\uD558|\uBD80\uD130|\uAE4C\uC9C0)?[^)\]]*[\)\]]\s*")

    title = _squash(title)
    title = re.sub(
        r"\s*\u203B\s*(?:\uD734\uAC15|\uB3C4\uAD6C\uB300\uC5EC\uBE44.*|"
        r"\uC218\uAC15\uB8CC.*\uD560\uC778.*|\uD560\uC778.*|\uD2B9\uAC15\uC8FC\uC81C.*|"
        r"\uC7AC\uB8CC\uBE44.*|\uC900\uBE44\uBB3C.*)$",
        "",
        title,
    )
    title = re.sub(r"\s*\u203B\s*[~-]?\s*$", "", title)
    title = re.sub(r"\s*[\(\[]\s*(?:\uD734\uAC15|\uC885\uAC15|\uB9C8\uAC10)\s*[\)\]]\s*$", "", title)
    title = re.sub(r"\s*[\(\[]\s*[~-]\s*[\)\]]\s*$", "", title)
    title = re.sub(r"\s*[_-]\s*\uD734\uAC15\s*$", "", title)
    title = re.sub(r"\s+\d{1,2}\s*[~-]\s*$", "", title)
    title = re.sub(r"\s*[~-]\s*\uB9CC\s*$", "", title)
    title = _squash(title)

    # Guardrails: keep the original if the cleaner became too aggressive.
    if len(title) < 3 or len(title) < max(3, len(original) * 0.18):
        return original, ""

    return title, " | ".join(dict.fromkeys(removed))
