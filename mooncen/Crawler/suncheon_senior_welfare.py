"""Current Suncheon senior welfare program collector.

The stable public notice board publishes each semester's recruitment notice.
Its HWPX timetable contains three independent facility tables.  This module
discovers current/future notices, parses only the public timetable attachment,
and returns one row per branch and displayed class name with all weekday
occurrences preserved.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import io
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
import zipfile

from bs4 import BeautifulSoup
from defusedxml import ElementTree
import requests


PROVIDER = "SUNCHEON_SENIOR_WELFARE_NOTICE"
HOST = "www.sc.go.kr"
BOARD_ID = "bbs_0000000000000063"
LIST_URL = (
    "https://www.sc.go.kr/silver/community/0001/0003/"
    f"?boardId={BOARD_ID}"
)
NOTICE_LIST_URLS = (LIST_URL,)
PARSER = "suncheon_current_recruitment_notice+hwpx_three_branch_timetable"
MAX_RESPONSE_BYTES = 5_000_000
MAX_HWPX_SECTION_BYTES = 5_000_000
MAX_NOTICE_CANDIDATES = 8
MAX_SCHEDULE_OCCURRENCES = 500
HTTP_ATTEMPTS = 3
WEEKDAYS = ("월", "화", "수", "목", "금")

_NOTICE_TITLE_RE = re.compile(
    r"(?P<year>20\d{2}).*?(?P<term>상반기|하반기).*?"
    r"프로그램.*?수강생\s*모집"
)
# The notice uses forms such as ``2026. 7. 6.(월) ~ 12. 11.(금)``.
_DATE_TOKEN = (
    r"(?:(?P<{prefix}_year>20\d{{2}})\s*[./-]\s*)?"
    r"(?P<{prefix}_month>\d{{1,2}})\s*[./-]\s*"
    r"(?P<{prefix}_day>\d{{1,2}})\s*\.?"
    r"(?:\s*\([^()]{{1,10}}\))?"
)
_TIME_TOKEN = r"\d{1,2}\s*:?\s*\d{2}"
_TIME_RANGE_RE = re.compile(
    rf"(?P<start>{_TIME_TOKEN})\s*[-~]\s*(?P<end>{_TIME_TOKEN})"
)
_INLINE_TIME_RANGE_RE = re.compile(
    rf"\(\s*(?P<start>{_TIME_TOKEN})\s*[-~]\s*"
    rf"(?P<end>{_TIME_TOKEN})\s*\)"
)
_SPACE_RE = re.compile(r"\s+")


class SuncheonSeniorContractError(RuntimeError):
    """Raised when the reviewed official source contract changes."""


@dataclass(frozen=True)
class BranchSpec:
    table_index: int
    name: str
    code: str
    address: str
    phone: str
    expected_columns: int


BRANCHES = (
    BranchSpec(
        table_index=0,
        name="용당노인복지관",
        code="YONGDANG",
        address="전라남도 순천시 용당신흥길 66",
        phone="061-749-8417",
        expected_columns=6,
    ),
    BranchSpec(
        table_index=1,
        name="동부노인복지관",
        code="DONGBU",
        address="전라남도 순천시 장선배기길 18",
        phone="061-749-4840",
        expected_columns=18,
    ),
    BranchSpec(
        table_index=2,
        name="남부노인복지관",
        code="NAMBU",
        address="전라남도 순천시 장평로 60",
        phone="061-749-4550",
        expected_columns=6,
    ),
)


@dataclass(frozen=True)
class NoticeCandidate:
    title: str
    year: int
    term: str
    posted_at: date
    url: str
    notice_id: str


@dataclass(frozen=True)
class Notice:
    candidate: NoticeCandidate
    apply_start: date
    apply_end: date
    operation_start: date
    operation_end: date
    target: str
    method: str
    content_text: str
    attachment_url: str
    attachment_name: str
    declared_total_classes: Optional[int]
    declared_branch_classes: dict[str, int]
    apply_year_normalized: bool


@dataclass(frozen=True)
class HwpxCell:
    row: int
    column: int
    row_span: int
    column_span: int
    text: str


@dataclass(frozen=True)
class ScheduleOccurrence:
    branch: BranchSpec
    title: str
    weekday: str
    start_time: str
    end_time: str
    instructor: str
    room: str
    raw_text: str
    table_row: int
    table_column: int


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safe_get(
    request_session: requests.Session,
    url: str,
    *,
    timeout: int,
) -> requests.Response:
    response: Optional[requests.Response] = None
    for attempt in range(HTTP_ATTEMPTS):
        try:
            response = request_session.get(url, timeout=timeout)
            response.raise_for_status()
            break
        except requests.RequestException:
            if attempt + 1 >= HTTP_ATTEMPTS:
                raise
            time.sleep(float(attempt + 1))
    if response is None:
        raise SuncheonSeniorContractError("Suncheon request did not return a response")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise SuncheonSeniorContractError(
            f"Suncheon response exceeded {MAX_RESPONSE_BYTES} bytes"
        )
    return response


def _canonical_notice_url(raw_url: str, base_url: str) -> tuple[str, str]:
    absolute = urljoin(base_url, raw_url)
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query)
    notice_id = _clean((query.get("cntId") or [""])[0])
    board_id = _clean((query.get("boardId") or [BOARD_ID])[0])
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() not in {HOST, "sc.go.kr"}
        or not parsed.path.startswith("/silver/community/")
        or board_id != BOARD_ID
        or not notice_id.isdigit()
    ):
        raise SuncheonSeniorContractError("Suncheon notice link shape changed")
    canonical = urlunparse(
        (
            "https",
            HOST,
            parsed.path,
            "",
            urlencode(
                {
                    "boardId": BOARD_ID,
                    "cntId": notice_id,
                    "mode": "view",
                }
            ),
            "",
        )
    )
    return canonical, notice_id


def _notice_candidates(
    soup: BeautifulSoup,
    *,
    base_url: str,
    current_year: int,
) -> list[NoticeCandidate]:
    candidates: dict[str, NoticeCandidate] = {}
    table = soup.select_one("table.bbsList")
    if table is None:
        raise SuncheonSeniorContractError("Suncheon notice list table is missing")
    for row in table.select("tbody tr"):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
        link = row.select_one("a[href*='cntId=']")
        title = _clean(link.get_text(" ", strip=True) if link else "")
        match = _NOTICE_TITLE_RE.search(title)
        if not match or int(match.group("year")) < current_year:
            continue
        if len(cells) < 5:
            raise SuncheonSeniorContractError("Suncheon notice list row shape changed")
        try:
            posted_at = datetime.strptime(cells[-2], "%Y-%m-%d").date()
        except ValueError as exc:
            raise SuncheonSeniorContractError(
                "Suncheon notice date shape changed"
            ) from exc
        url, notice_id = _canonical_notice_url(
            _clean(link.get("href") if link else ""),
            base_url,
        )
        candidates[notice_id] = NoticeCandidate(
            title=title,
            year=int(match.group("year")),
            term=match.group("term"),
            posted_at=posted_at,
            url=url,
            notice_id=notice_id,
        )
    ordered = sorted(
        candidates.values(),
        key=lambda item: (item.year, item.term == "하반기", item.posted_at, item.notice_id),
        reverse=True,
    )
    if len(ordered) > MAX_NOTICE_CANDIDATES:
        raise SuncheonSeniorContractError(
            f"Suncheon exposed {len(ordered)} current-year notice candidates"
        )
    return ordered


def _latest_term_candidates(
    candidates: list[NoticeCandidate],
) -> list[NoticeCandidate]:
    by_notice_id = {
        candidate.notice_id: candidate
        for candidate in candidates
    }
    ordered = sorted(
        by_notice_id.values(),
        key=lambda item: (
            item.year,
            item.term == "하반기",
            item.posted_at,
            item.notice_id,
        ),
        reverse=True,
    )
    if not ordered:
        return []
    latest_term = (ordered[0].year, ordered[0].term)
    selected = [
        candidate
        for candidate in ordered
        if (candidate.year, candidate.term) == latest_term
    ]
    # Branch categories can publish aliases of one semester notice.  Prefer
    # one source identity for identical title/date contracts.
    unique_contracts: dict[tuple[str, date], NoticeCandidate] = {}
    for candidate in selected:
        unique_contracts.setdefault(
            (candidate.title, candidate.posted_at),
            candidate,
        )
    return list(unique_contracts.values())


def _parse_date_range(
    text: str,
    label: str,
    *,
    default_year: int,
) -> tuple[date, date, str]:
    start_pattern = _DATE_TOKEN.format(prefix="start")
    end_pattern = _DATE_TOKEN.format(prefix="end")
    match = re.search(
        rf"{re.escape(label)}\s*:\s*(?P<raw>{start_pattern}\s*~\s*{end_pattern})",
        text,
    )
    if not match:
        raise SuncheonSeniorContractError(
            f"Suncheon notice did not expose {label}"
        )
    start_year = int(match.group("start_year") or default_year)
    end_year = int(match.group("end_year") or start_year)
    try:
        start = date(
            start_year,
            int(match.group("start_month")),
            int(match.group("start_day")),
        )
        end = date(
            end_year,
            int(match.group("end_month")),
            int(match.group("end_day")),
        )
    except ValueError as exc:
        raise SuncheonSeniorContractError(
            f"Suncheon notice exposed an invalid {label}"
        ) from exc
    if end < start:
        raise SuncheonSeniorContractError(
            f"Suncheon notice exposed a reversed {label}"
        )
    return start, end, _clean(match.group("raw"))


def _numbered_value(text: str, label: str) -> str:
    match = re.search(
        rf"{re.escape(label)}\s*:\s*(?P<value>.*?)"
        r"(?=\s+\d+\.\s*[^:]{1,30}:|$)",
        text,
    )
    return _clean(match.group("value") if match else "")


def _attachment_from_detail(
    soup: BeautifulSoup,
    *,
    detail_url: str,
) -> tuple[str, str]:
    matches: list[tuple[str, str]] = []
    for link in soup.select("a"):
        name = _clean(link.get_text(" ", strip=True))
        action = " ".join(
            str(value or "")
            for value in (link.get("onclick"), link.get("href"))
        )
        path_match = re.search(r"Jnit_boardDownload\(\s*'([^']+)'", action)
        if not path_match or not re.search(
            r"\.hwpx(?:\s|\(|$)",
            name,
            flags=re.IGNORECASE,
        ):
            continue
        path = re.sub(
            r";jsessionid=[^?'\",)]+",
            "",
            path_match.group(1),
            flags=re.IGNORECASE,
        )
        url = urljoin(detail_url, path)
        parsed = urlparse(url)
        if (
            parsed.scheme.lower() != "https"
            or parsed.netloc.lower() not in {HOST, "sc.go.kr"}
            or not parsed.path.startswith(f"/board/file/{BOARD_ID}/")
        ):
            raise SuncheonSeniorContractError(
                "Suncheon HWPX attachment link shape changed"
            )
        matches.append((urlunparse(("https", HOST, parsed.path, "", "", "")), name))
    if len(matches) != 1:
        raise SuncheonSeniorContractError(
            f"Suncheon current notice exposed {len(matches)} HWPX timetables"
        )
    return matches[0]


def _declared_class_counts(text: str) -> tuple[Optional[int], dict[str, int]]:
    total_match = re.search(
        r"\d+\s*개\s*분야\s*,\s*\d+\s*강좌\s*,\s*(\d+)\s*개\s*반",
        text,
    )
    branch_counts: dict[str, int] = {}
    for label, branch_name in (
        ("용당", "용당노인복지관"),
        ("동부", "동부노인복지관"),
        ("남부", "남부노인복지관"),
    ):
        match = re.search(
            rf"{label}(?:복지관)?\s*:\s*[\d,]+\s*명.*?"
            r"\d+\s*개\s*분야\s*,\s*\d+\s*개?\s*강좌\s*"
            r"(?P<count>\d+)\s*개\s*반",
            text,
        )
        if match:
            branch_counts[branch_name] = int(match.group("count"))
    return (
        int(total_match.group(1)) if total_match else None,
        branch_counts,
    )


def _parse_notice(candidate: NoticeCandidate, soup: BeautifulSoup) -> Notice:
    table = soup.select_one("table.bbsView")
    content = table.select_one(".content") if table else None
    if table is None or content is None:
        raise SuncheonSeniorContractError("Suncheon notice detail shape changed")
    title_node = table.select_one("tr th")
    detail_title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if detail_title != candidate.title:
        raise SuncheonSeniorContractError("Suncheon notice title identity changed")
    content_text = _clean(content.get_text(" ", strip=True))
    operation_start, operation_end, _operation_raw = _parse_date_range(
        content_text,
        "운영기간",
        default_year=candidate.year,
    )
    try:
        apply_start, apply_end, _apply_raw = _parse_date_range(
            content_text,
            "모집기간",
            default_year=candidate.year,
        )
    except SuncheonSeniorContractError:
        apply_start, apply_end, _apply_raw = _parse_date_range(
            content_text,
            "신청기간",
            default_year=candidate.year,
        )
    apply_year_normalized = False
    if apply_start.year != candidate.year and candidate.posted_at.year == candidate.year:
        try:
            apply_start = apply_start.replace(year=candidate.year)
            apply_end = apply_end.replace(year=candidate.year)
        except ValueError as exc:
            raise SuncheonSeniorContractError(
                "Suncheon recruitment-year correction failed"
            ) from exc
        apply_year_normalized = True
    if operation_start.year != candidate.year or operation_end < operation_start:
        raise SuncheonSeniorContractError(
            "Suncheon operation period conflicts with notice identity"
        )
    target = _numbered_value(content_text, "신청자격")
    method = _numbered_value(content_text, "신청방법")
    if not target or not method:
        raise SuncheonSeniorContractError(
            "Suncheon notice target or application method is missing"
        )
    attachment_url, attachment_name = _attachment_from_detail(
        soup,
        detail_url=candidate.url,
    )
    declared_total, declared_branches = _declared_class_counts(content_text)
    return Notice(
        candidate=candidate,
        apply_start=apply_start,
        apply_end=apply_end,
        operation_start=operation_start,
        operation_end=operation_end,
        target=target,
        method=method,
        content_text=content_text,
        attachment_url=attachment_url,
        attachment_name=attachment_name,
        declared_total_classes=declared_total,
        declared_branch_classes=declared_branches,
        apply_year_normalized=apply_year_normalized,
    )


def _direct_children(node: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in node if _local_name(child.tag) == name]


def _cell_property(cell: ElementTree.Element, name: str) -> ElementTree.Element:
    matches = _direct_children(cell, name)
    if len(matches) != 1:
        raise SuncheonSeniorContractError(
            f"Suncheon HWPX cell is missing {name}"
        )
    return matches[0]


def _cell_text(cell: ElementTree.Element) -> str:
    return _clean(
        " ".join(
            node.text or ""
            for node in cell.iter()
            if _local_name(node.tag) == "t"
        )
    )


def _table_cells(
    table: ElementTree.Element,
    *,
    expected_columns: int,
) -> tuple[list[HwpxCell], dict[tuple[int, int], str]]:
    try:
        row_count = int(table.attrib["rowCnt"])
        column_count = int(table.attrib["colCnt"])
    except (KeyError, ValueError) as exc:
        raise SuncheonSeniorContractError(
            "Suncheon HWPX table dimensions are missing"
        ) from exc
    if column_count != expected_columns or row_count < 2 or row_count > 100:
        raise SuncheonSeniorContractError(
            f"Suncheon HWPX table dimensions changed: {row_count}x{column_count}"
        )
    cells: list[HwpxCell] = []
    grid: dict[tuple[int, int], str] = {}
    for row in _direct_children(table, "tr"):
        for raw_cell in _direct_children(row, "tc"):
            address = _cell_property(raw_cell, "cellAddr")
            span = _cell_property(raw_cell, "cellSpan")
            try:
                parsed = HwpxCell(
                    row=int(address.attrib["rowAddr"]),
                    column=int(address.attrib["colAddr"]),
                    row_span=int(span.attrib["rowSpan"]),
                    column_span=int(span.attrib["colSpan"]),
                    text=_cell_text(raw_cell),
                )
            except (KeyError, ValueError) as exc:
                raise SuncheonSeniorContractError(
                    "Suncheon HWPX cell coordinates changed"
                ) from exc
            if (
                parsed.row < 0
                or parsed.column < 0
                or parsed.row_span < 1
                or parsed.column_span < 1
                or parsed.row + parsed.row_span > row_count
                or parsed.column + parsed.column_span > column_count
            ):
                raise SuncheonSeniorContractError(
                    "Suncheon HWPX cell coordinates are invalid"
                )
            cells.append(parsed)
            for row_index in range(parsed.row, parsed.row + parsed.row_span):
                for column_index in range(
                    parsed.column,
                    parsed.column + parsed.column_span,
                ):
                    key = (row_index, column_index)
                    if key in grid:
                        raise SuncheonSeniorContractError(
                            "Suncheon HWPX cells overlap"
                        )
                    grid[key] = parsed.text
    return cells, grid


def _normalize_time(token: str, section: str) -> str:
    compact = re.sub(r"\s+", "", token)
    compact_section = re.sub(r"\s+", "", section)
    if ":" in compact:
        hour_text, minute_text = compact.split(":", 1)
    elif len(compact) in {3, 4}:
        hour_text, minute_text = compact[:-2], compact[-2:]
    else:
        raise SuncheonSeniorContractError(
            f"Suncheon timetable time changed: {token}"
        )
    try:
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise SuncheonSeniorContractError(
            f"Suncheon timetable time changed: {token}"
        ) from exc
    if "오후" in compact_section and 1 <= hour < 12:
        hour += 12
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise SuncheonSeniorContractError(
            f"Suncheon timetable time is invalid: {token}"
        )
    return f"{hour:02d}:{minute:02d}"


def _clean_title(value: str) -> str:
    return _clean(value).lstrip("\u25aa\u2022\u00b7 ").strip()


def _parse_inline_occurrence(
    text: str,
    *,
    branch: BranchSpec,
    weekday: str,
    section: str,
    row: int,
    column: int,
) -> ScheduleOccurrence:
    match = _INLINE_TIME_RANGE_RE.search(text)
    if not match:
        raise SuncheonSeniorContractError(
            f"Suncheon inline timetable shape changed: {text}"
        )
    title = _clean_title(text[: match.start()])
    remainder = _clean(text[match.end() :])
    if "/" not in remainder:
        raise SuncheonSeniorContractError(
            f"Suncheon inline timetable venue is missing: {text}"
        )
    instructor, room = (_clean(part) for part in remainder.rsplit("/", 1))
    if not title or not room:
        raise SuncheonSeniorContractError(
            f"Suncheon inline timetable identity is incomplete: {text}"
        )
    return ScheduleOccurrence(
        branch=branch,
        title=title,
        weekday=weekday,
        start_time=_normalize_time(match.group("start"), section),
        end_time=_normalize_time(match.group("end"), section),
        instructor=instructor,
        room=room,
        raw_text=text,
        table_row=row,
        table_column=column,
    )


def _parse_grid_title(value: str) -> tuple[str, str]:
    text = _clean(value)
    match = re.search(r"\s*\((?P<instructor>[^()]*)\)\s*$", text)
    if not match:
        return text, ""
    instructor = _clean(match.group("instructor"))
    if instructor == "자율":
        return text, "자율 운영"
    return _clean(text[: match.start()]), instructor


def _parse_six_column_table(
    table: ElementTree.Element,
    branch: BranchSpec,
) -> list[ScheduleOccurrence]:
    cells, grid = _table_cells(table, expected_columns=6)
    occurrences: list[ScheduleOccurrence] = []
    for cell in cells:
        if cell.row == 0 or cell.column < 1 or not cell.text:
            continue
        weekday = WEEKDAYS[cell.column - 1]
        section = _clean(grid.get((cell.row, 0)))
        occurrences.append(
            _parse_inline_occurrence(
                cell.text,
                branch=branch,
                weekday=weekday,
                section=section,
                row=cell.row,
                column=cell.column,
            )
        )
    return occurrences


def _parse_eighteen_column_table(
    table: ElementTree.Element,
    branch: BranchSpec,
) -> list[ScheduleOccurrence]:
    cells, grid = _table_cells(table, expected_columns=18)
    course_columns = {3 + (index * 3): index for index in range(len(WEEKDAYS))}
    occurrences: list[ScheduleOccurrence] = []
    for cell in cells:
        if cell.row == 0 or cell.column not in course_columns or not cell.text:
            continue
        section = _clean(grid.get((cell.row, 0)))
        time_text = _clean(grid.get((cell.row, cell.column + 1)))
        time_match = _TIME_RANGE_RE.fullmatch(time_text)
        if not time_match:
            raise SuncheonSeniorContractError(
                f"Suncheon grid timetable time changed: {time_text}"
            )
        title, instructor = _parse_grid_title(cell.text)
        floor = _clean(grid.get((cell.row, 1)))
        room = _clean(grid.get((cell.row, 2)))
        if floor and floor not in room:
            room = _clean(f"{floor} {room}")
        if not title or not room:
            raise SuncheonSeniorContractError(
                f"Suncheon grid timetable identity is incomplete: {cell.text}"
            )
        occurrences.append(
            ScheduleOccurrence(
                branch=branch,
                title=title,
                weekday=WEEKDAYS[course_columns[cell.column]],
                start_time=_normalize_time(time_match.group("start"), section),
                end_time=_normalize_time(time_match.group("end"), section),
                instructor=instructor,
                room=room,
                raw_text=_clean(f"{cell.text} {time_text} / {room}"),
                table_row=cell.row,
                table_column=cell.column,
            )
        )
    return occurrences


def parse_hwpx_timetable(
    payload: bytes,
) -> tuple[list[ScheduleOccurrence], dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (OSError, zipfile.BadZipFile) as exc:
        raise SuncheonSeniorContractError(
            "Suncheon timetable is not a valid HWPX archive"
        ) from exc
    with archive:
        try:
            section_info = archive.getinfo("Contents/section0.xml")
        except KeyError as exc:
            raise SuncheonSeniorContractError(
                "Suncheon HWPX section0.xml is missing"
            ) from exc
        if section_info.file_size > MAX_HWPX_SECTION_BYTES:
            raise SuncheonSeniorContractError(
                "Suncheon HWPX section exceeded the reviewed size"
            )
        try:
            root = ElementTree.fromstring(archive.read(section_info))
        except ElementTree.ParseError as exc:
            raise SuncheonSeniorContractError(
                "Suncheon HWPX XML is invalid"
            ) from exc
    tables = [node for node in root.iter() if _local_name(node.tag) == "tbl"]
    if len(tables) != len(BRANCHES):
        raise SuncheonSeniorContractError(
            f"Suncheon HWPX exposed {len(tables)} tables instead of 3"
        )
    occurrences: list[ScheduleOccurrence] = []
    branch_slot_counts: dict[str, int] = {}
    for branch in BRANCHES:
        table = tables[branch.table_index]
        if branch.expected_columns == 6:
            parsed = _parse_six_column_table(table, branch)
        else:
            parsed = _parse_eighteen_column_table(table, branch)
        if not parsed:
            raise SuncheonSeniorContractError(
                f"Suncheon HWPX {branch.name} table is empty"
            )
        branch_slot_counts[branch.name] = len(parsed)
        occurrences.extend(parsed)
    if len(occurrences) > MAX_SCHEDULE_OCCURRENCES:
        raise SuncheonSeniorContractError(
            f"Suncheon HWPX exposed {len(occurrences)} schedule slots"
        )
    return occurrences, {
        "schedule_slots": len(occurrences),
        "branch_schedule_slots": branch_slot_counts,
    }


def _title_key(value: str) -> str:
    return re.sub(r"[\s\u00b7\u318d]+", "", value).casefold()


def _category(title: str) -> str:
    compact = _title_key(title)
    if any(
        token in compact
        for token in ("컴퓨터", "스마트폰", "인터넷", "ai", "한글문서")
    ):
        return "디지털교육"
    if any(
        token in compact
        for token in (
            "요가",
            "체조",
            "댄스",
            "탁구",
            "당구",
            "게이트볼",
            "호흡",
            "헬스",
            "무용",
        )
    ):
        return "건강/체육"
    if any(
        token in compact
        for token in (
            "노래",
            "합창",
            "기타",
            "오카리나",
            "하모니카",
            "농악",
        )
    ):
        return "음악/공연"
    if any(
        token in compact
        for token in ("서예", "수묵", "그림", "화", "pop", "캘리")
    ):
        return "미술/서예"
    if any(
        token in compact
        for token in ("영어", "일본어", "중국어", "한문", "한자", "시사")
    ):
        return "어학/인문"
    return "취미/여가"


def _status(notice: Notice, current_date: date) -> str:
    if notice.apply_start <= current_date <= notice.apply_end:
        return "접수중"
    if current_date < notice.apply_start:
        return "접수예정"
    if current_date <= notice.operation_end:
        return "접수마감"
    return "종료"


def _stable_course_id(notice: Notice, branch: BranchSpec, title: str) -> str:
    digest = hashlib.sha1(
        (
            f"{PROVIDER}|{notice.candidate.notice_id}|{branch.code}|"
            f"{_title_key(title)}|{notice.operation_start.isoformat()}|"
            f"{notice.operation_end.isoformat()}"
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"SUNCHEON_SENIOR_{digest}"


def _group_rows(
    notice: Notice,
    occurrences: list[ScheduleOccurrence],
    *,
    current_date: date,
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str],
        list[ScheduleOccurrence],
    ] = defaultdict(list)
    for occurrence in occurrences:
        grouped[(occurrence.branch.code, _title_key(occurrence.title))].append(
            occurrence
        )
    rows: list[dict[str, Any]] = []
    weekday_order = {weekday: index for index, weekday in enumerate(WEEKDAYS)}
    for group in grouped.values():
        first = group[0]
        branch = first.branch
        segment_groups: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
        for occurrence in group:
            segment_groups[
                (
                    occurrence.start_time,
                    occurrence.end_time,
                    occurrence.room,
                    occurrence.instructor,
                )
            ].add(occurrence.weekday)
        schedule_segments: list[str] = []
        for (start_time, end_time, room, instructor), weekdays in sorted(
            segment_groups.items(),
            key=lambda item: (
                min(weekday_order[day] for day in item[1]),
                item[0][0],
                item[0][2],
            ),
        ):
            ordered_days = sorted(weekdays, key=weekday_order.__getitem__)
            detail = ", ".join(part for part in (room, instructor) if part)
            schedule_segments.append(
                f"{', '.join(ordered_days)} {start_time}~{end_time}"
                + (f" ({detail})" if detail else "")
            )
        rooms = list(dict.fromkeys(item.room for item in group if item.room))
        instructors = list(
            dict.fromkeys(item.instructor for item in group if item.instructor)
        )
        title = first.title
        category = _category(title)
        row_status = _status(notice, current_date)
        course_id = _stable_course_id(notice, branch, title)
        non_enrollment_hint = any(
            token in _title_key(title)
            for token in ("자율", "노래연습", "노래뽐내기")
        )
        description = _clean(
            f"{notice.candidate.title} | {notice.method} | "
            f"문의 {branch.phone}"
        )
        rows.append(
            {
                "provider": PROVIDER,
                "provider_course_id": course_id,
                "title": title,
                "branch": branch.name,
                "branch_code": f"{PROVIDER}_{branch.code}",
                "category": f"노인복지관 평생교육/{category}",
                "collection_category": "복지관",
                "domain_category": "복지/평생교육",
                "source_group": "welfare",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "raw_url": f"{notice.candidate.url}#course-{course_id}",
                "application_type": "OFFLINE_APPLY",
                "application_method_raw": notice.method,
                "status": row_status,
                "period": (
                    f"{notice.operation_start.isoformat()} ~ "
                    f"{notice.operation_end.isoformat()}"
                ),
                "apply_period": (
                    f"{notice.apply_start.isoformat()} ~ "
                    f"{notice.apply_end.isoformat()}"
                ),
                "schedule_raw": "; ".join(schedule_segments),
                "target": notice.target,
                "fee": "수강료 복지관 문의",
                "instructor": ", ".join(instructors),
                "venue_name": branch.name,
                "venue_address": branch.address,
                "address": branch.address,
                "room": ", ".join(rooms),
                "contact": branch.phone,
                "description": description,
                "raw_fields": {
                    "parser": PARSER,
                    "notice_id": notice.candidate.notice_id,
                    "notice_title": notice.candidate.title,
                    "attachment_name": notice.attachment_name,
                    "attachment_url": notice.attachment_url,
                    "target": notice.target,
                    "fee": "수강료 복지관 문의",
                    "category": category,
                    "schedule": "; ".join(schedule_segments),
                    "source_occurrences": [
                        {
                            "weekday": item.weekday,
                            "start_time": item.start_time,
                            "end_time": item.end_time,
                            "instructor": item.instructor,
                            "room": item.room,
                            "raw_text": item.raw_text,
                            "table_row": item.table_row,
                            "table_column": item.table_column,
                        }
                        for item in group
                    ],
                    "non_enrollment_operating_hint": non_enrollment_hint,
                    "apply_year_normalized_from_notice_typo": (
                        notice.apply_year_normalized
                    ),
                    "privacy_scope": (
                        "public recruitment notice and timetable attachment only"
                    ),
                },
            }
        )
    return rows


def collect(
    target: Any,
    *,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    dedupe_rows: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    session_factory: Callable[[], requests.Session],
    today: Optional[date] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if _clean(getattr(target, "provider", "")) != PROVIDER:
        raise SuncheonSeniorContractError("Unexpected Suncheon provider")
    parsed_target = urlparse(_clean(getattr(target, "url", "")))
    if (
        parsed_target.scheme.lower() != "https"
        or parsed_target.netloc.lower() not in {HOST, "sc.go.kr"}
        or not parsed_target.path.startswith("/silver/community/")
        or (parse_qs(parsed_target.query).get("boardId") or [""])[0] != BOARD_ID
    ):
        raise SuncheonSeniorContractError(
            "Suncheon target must be the reviewed official notice list"
        )
    if max_pages < len(NOTICE_LIST_URLS):
        raise SuncheonSeniorContractError(
            "Suncheon notice collection requires max_pages >= 1"
        )
    current_date = today or date.today()
    request_session = session_factory()
    detail_pages = 0
    attachment_pages = 0
    try:
        discovered_candidates: list[NoticeCandidate] = []
        for list_url in NOTICE_LIST_URLS:
            list_response = _safe_get(
                request_session,
                list_url,
                timeout=timeout,
            )
            discovered_candidates.extend(
                _notice_candidates(
                    BeautifulSoup(list_response.text, "html.parser"),
                    base_url=list_response.url or list_url,
                    current_year=current_date.year,
                )
            )
        candidates = _latest_term_candidates(discovered_candidates)
        if len(candidates) > detail_limit:
            raise SuncheonSeniorContractError(
                f"Suncheon notice details require {len(candidates)} pages; "
                f"detail_limit={detail_limit}"
            )
        notices: list[Notice] = []
        occurrence_meta: list[dict[str, Any]] = []
        all_rows: list[dict[str, Any]] = []
        for candidate in candidates:
            detail_response = _safe_get(
                request_session,
                candidate.url,
                timeout=timeout,
            )
            detail_pages += 1
            notice = _parse_notice(
                candidate,
                BeautifulSoup(detail_response.text, "html.parser"),
            )
            if notice.operation_end < current_date:
                continue
            attachment_response = _safe_get(
                request_session,
                notice.attachment_url,
                timeout=timeout,
            )
            attachment_pages += 1
            occurrences, parsed_meta = parse_hwpx_timetable(
                attachment_response.content
            )
            notices.append(notice)
            occurrence_meta.append(parsed_meta)
            all_rows.extend(
                _group_rows(
                    notice,
                    occurrences,
                    current_date=current_date,
                )
            )
    finally:
        closer = getattr(request_session, "close", None)
        if callable(closer):
            closer()

    rows = dedupe_rows(all_rows)
    declared_total = sum(
        notice.declared_total_classes or 0 for notice in notices
    )
    schedule_slots = sum(
        int(item.get("schedule_slots") or 0) for item in occurrence_meta
    )
    meta = {
        "pages": len(NOTICE_LIST_URLS),
        "detail_pages": detail_pages + attachment_pages,
        "discovered_links": len(candidates),
        "reservation_discovery_links": 0,
        "pagination_detected": False,
        "pagination_complete": True,
        "pagination_exhausted": True,
        "source_cap_reached": False,
        "recursion_depth": 0,
        "no_current_data": not rows,
        "no_current_reason": (
            "no current or future senior welfare recruitment timetable"
            if not rows
            else ""
        ),
        "current_notice_count": len(notices),
        "attachment_pages": attachment_pages,
        "schedule_slots": schedule_slots,
        "schedule_groups": len(rows),
        "declared_enrollment_classes": declared_total,
        "published_schedule_group_difference": (
            len(rows) - declared_total if declared_total else 0
        ),
        "branch_schedule_slots": {
            branch_name: sum(
                int(item.get("branch_schedule_slots", {}).get(branch_name) or 0)
                for item in occurrence_meta
            )
            for branch_name in (branch.name for branch in BRANCHES)
        },
        "privacy_scope": (
            "public recruitment notice and HWPX timetable only; "
            "no application or applicant endpoints"
        ),
    }
    return rows, PARSER, meta
