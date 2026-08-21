"""
공용 유틸 함수 모음.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()


def setup_logger(name: str = __name__, log_file: str | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file and os.environ.get("MOONCEN_OPS_SERVICE_ACTION") != "1":
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def parse_date(date_str):
    if not date_str:
        return None

    date_formats = [
        "%Y-%m-%d",
        "%Y.%m.%d",
        "%Y/%m/%d",
        "%Y년 %m월 %d일",
    ]

    for fmt in date_formats:
        try:
            return datetime.strptime(str(date_str).strip(), fmt).date()
        except ValueError:
            continue
    return None


def extract_number(text):
    if not text:
        return 0
    numbers = re.findall(r"\d[\d,]*", str(text))
    if numbers:
        return int(numbers[0].replace(",", ""))
    return 0


def extract_krw_amount(text):
    if not text:
        return 0

    value = str(text).strip()
    if any(token in value for token in ("\ubb34\ub8cc", "\ubb34\uc0c1")):
        return 0

    won_matches = re.findall(r"(\d[\d,]*)\s*\uc6d0", value)
    if won_matches:
        return int(won_matches[0].replace(",", ""))

    total = 0
    man_match = re.search(r"(\d+(?:\.\d+)?)\s*\ub9cc\s*\uc6d0?", value)
    if man_match:
        total += int(float(man_match.group(1)) * 10000)

    cheon_match = re.search(r"(\d+(?:\.\d+)?)\s*\ucc9c\s*\uc6d0?", value)
    if cheon_match:
        total += int(float(cheon_match.group(1)) * 1000)

    if total:
        return total

    return extract_number(value)


_FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
_MATERIAL_FEE_KEYWORD_PATTERN = r"(?:재료비|교재비)"


def _material_fee_amount_from_match(number: str, unit: str | None = None) -> int:
    normalized = number.translate(_FULLWIDTH_DIGIT_TRANS).replace(",", "")
    if unit is None and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", normalized):
        normalized = normalized.replace(".", "")
    value = float(normalized)
    if unit == "만":
        value *= 10000
    elif unit == "천":
        value *= 1000
    return int(value)


def extract_material_fee_amount(*texts) -> int:
    combined = clean_text(" ".join(str(text or "") for text in texts))
    if not combined:
        return 0

    if not re.search(_MATERIAL_FEE_KEYWORD_PATTERN, combined):
        return 0

    no_fee_pattern = rf"{_MATERIAL_FEE_KEYWORD_PATTERN}[^.!?\n\r]{{0,25}}(?:없음|무료|무상|별도\s*없음)"
    if re.search(no_fee_pattern, combined):
        return 0

    amount = r"([0-9０-９][0-9０-９,.]*)\s*(만|천)?\s*원"
    patterns = (
        rf"{_MATERIAL_FEE_KEYWORD_PATTERN}[^0-9０-９]{{0,30}}{amount}",
        rf"{amount}[^.!?\n\r]{{0,20}}{_MATERIAL_FEE_KEYWORD_PATTERN}",
    )
    for pattern in patterns:
        match = re.search(pattern, combined)
        if match:
            number = match.group(1)
            unit = match.group(2) if len(match.groups()) > 1 else None
            return _material_fee_amount_from_match(number, unit)

    return 0


def infer_course_status(*texts, default="OPEN"):
    combined = " ".join(str(text or "") for text in texts)
    normalized = re.sub(r"\s+", "", combined)
    if not normalized:
        return default
    upper = normalized.upper()
    if upper in {"OPEN", "CLOSED", "WAITING", "SCHEDULED", "DEADLINE"}:
        return upper

    closed_tokens = (
        "접수마감",
        "신청마감",
        "모집마감",
        "수강마감",
        "마감되었습니다",
        "접수종료",
        "신청종료",
        "모집종료",
        "접수완료",
        "모집완료",
        "수강신청불가",
        "신청불가",
        "폐강",
        "[마감]",
        "마감]",
        "마감)",
    )
    waiting_tokens = ("대기접수", "대기신청", "대기자")
    scheduled_tokens = ("접수예정", "신청예정", "오픈예정")
    deadline_tokens = ("마감임박", "마감임박")
    open_tokens = ("접수중", "신청가능", "수강신청", "장바구니담기")

    if any(token in normalized for token in closed_tokens):
        return "CLOSED"
    if any(token in normalized for token in waiting_tokens):
        return "WAITING"
    if any(token in normalized for token in scheduled_tokens):
        return "SCHEDULED"
    if any(token in normalized for token in deadline_tokens):
        return "DEADLINE"
    if any(token in normalized for token in open_tokens):
        return "OPEN"
    return default


def clean_text(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def clean_instructor_name(text):
    value = clean_text(text)
    if not value:
        return None

    value = re.sub(r"^(?:강사명|지도강사|강사)\s*[:：]?\s*", "", value).strip()
    value = re.sub(r"\s*(?:강사님\s*소개|강사\s*소개|강사님|강사|선생님\s*소개|선생님|소개|프로필)\s*$", "", value).strip()
    value = re.sub(r"\s+", " ", value)
    if not value or value in {"미정", "강사 미정", "-"}:
        return None
    return value[:100]


def ensure_dir(directory):
    os.makedirs(directory, exist_ok=True)


LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
