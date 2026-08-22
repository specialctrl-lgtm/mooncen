import re
from typing import Optional

from utils import clean_text


LOTTE_DESCRIPTION_TAIL_MARKERS = [
    '강좌소개 더보기',
    '총 주문 금액',
    '장바구니',
    '대기신청',
    '대기 신청하기',
    '재료비/대여료 선택',
    '수강신청하기-재료비 옵션선택',
    '재료비 옵션선택',
    '옵션정보',
    '옵션선택',
    '문의처',
    '접수기간',
]

LOTTE_DESCRIPTION_NOTICE_MARKERS = [
    '■ 수강신청시 주의사항',
    '수강신청시 주의사항',
    '※ 수강신청시 주의사항',
]

DATE_TOKEN_RE = re.compile(
    r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
    r"|\d{8}"
    r"|\d{1,2}[.\-/]\d{1,2}"
)


def has_lotte_description_noise(text: str) -> bool:
    value = clean_text(text)
    return any(marker in value for marker in LOTTE_DESCRIPTION_TAIL_MARKERS + LOTTE_DESCRIPTION_NOTICE_MARKERS)


def has_lotte_apply_period_noise(text: str) -> bool:
    value = clean_text(text)
    return any(marker in value for marker in LOTTE_DESCRIPTION_TAIL_MARKERS) or "강좌소개" in value


def _format_date_value(value) -> Optional[str]:
    if not value:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y.%m.%d")
    text = clean_text(value).replace("-", ".").replace("/", ".")
    match = re.match(r"^(\d{4})[.](\d{1,2})[.](\d{1,2})$", text)
    if match:
        return f"{match.group(1)}.{int(match.group(2)):02d}.{int(match.group(3)):02d}"
    return text or None


def clean_lotte_apply_period_raw(text: str, start_value=None, end_value=None) -> Optional[str]:
    start = _format_date_value(start_value)
    end = _format_date_value(end_value)
    if start and end:
        return f"{start}~{end}" if start != end else start
    if start or end:
        return start or end

    value = clean_text(text)
    if not value:
        return None
    tokens = DATE_TOKEN_RE.findall(value)
    if len(tokens) >= 2:
        return f"{tokens[0]}~{tokens[1]}"
    if len(tokens) == 1:
        return tokens[0]
    return None if has_lotte_apply_period_noise(value) else value


def clean_lotte_description_text(text: str) -> Optional[str]:
    """Keep only course-intro text from LOTTE detail-page copy."""
    value = clean_text(text)
    if not value:
        return None
    if any(value.startswith(marker) for marker in LOTTE_DESCRIPTION_NOTICE_MARKERS):
        return None

    intro_match = re.search(r'강좌소개\s*((?:<|〈).+)$', value)
    if intro_match:
        value = intro_match.group(1)
    elif any(marker in value for marker in LOTTE_DESCRIPTION_TAIL_MARKERS) and '강좌소개' in value:
        value = value.split('강좌소개', 1)[1]

    value = re.sub(r'^(?:강좌소개|강좌정보|수강후기)\s+', '', value).strip()

    cut_positions = [
        value.find(marker)
        for marker in LOTTE_DESCRIPTION_TAIL_MARKERS
        if value.find(marker) >= 0
    ]
    for marker in LOTTE_DESCRIPTION_NOTICE_MARKERS:
        idx = value.find(marker)
        if idx > 20:
            cut_positions.append(idx)

    if cut_positions:
        value = value[:min(cut_positions)]

    value = re.sub(r'\s*본 강좌는\s*\[?수강신청하기-?\s*재료비\s*옵션선택\]?.*$', '', value)
    value = re.sub(r'\s+', ' ', value).strip()
    value = value.strip(' -:：')

    if len(value) < 10:
        return None
    return value
