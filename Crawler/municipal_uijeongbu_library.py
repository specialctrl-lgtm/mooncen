"""Fail-closed collector for Uijeongbu's six official library course ledgers.

The public ``/module/teach`` pages expose one complete, unpaginated ledger per
library.  Only the public directory, list, and detail documents are requested.
The student application, login, attachment, applicant, and member endpoints are
never called.  A snapshot is atomic: the six-library directory, every source
row, stable directory/list rechecks, every current/future detail, and each
source-bound public application control must reconcile or no rows are returned.

Volunteer recruitment, library tours/path exploration, performances without an
educational contract, notices, and other non-course records are accounted for
but never returned.  Free-form descriptions, instructor/staff names, contacts,
attachments, and applicant/member data are deliberately not persisted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


UIJEONGBU_LIBRARY_PROVIDER = "MUNI_WWW_UILIB_GO_KR_3350DF58"
UIJEONGBU_LIBRARY_URL = "https://www.uilib.go.kr/main/index.do"
UIJEONGBU_LIBRARY_HOST = "www.uilib.go.kr"
UIJEONGBU_LIBRARY_MUNICIPALITY_CODE = "4115000000"
UIJEONGBU_LIBRARY_MUNICIPALITY_NAME = "경기도 의정부시"
UIJEONGBU_LIBRARY_PARSER = (
    "uijeongbu_six_library_complete_teach_ledgers+exact_unpaginated_boundaries+"
    "stable_directory_and_ledgers+all_current_public_details+education_only+"
    "volunteer_experience_performance_exclusion+identity_bound_application+"
    "no_application_login_pii_fetch"
)
UIJEONGBU_LIBRARY_OWNERSHIP_SCOPE = (
    "uijeongbu_official_six_library_public_program_application_ledgers"
)


@dataclass(frozen=True)
class UijeongbuLibraryBranch:
    key: str
    name: str
    short_name: str
    address: str

    @property
    def index_path(self) -> str:
        return f"/{self.key}/index.do"

    @property
    def list_path(self) -> str:
        return f"/{self.key}/module/teach/index.do"

    @property
    def detail_path(self) -> str:
        return f"/{self.key}/module/teach/detail.do"

    @property
    def list_url(self) -> str:
        return f"https://{UIJEONGBU_LIBRARY_HOST}{self.list_path}?menu_idx=24"


UIJEONGBU_LIBRARY_BRANCHES: tuple[UijeongbuLibraryBranch, ...] = (
    UijeongbuLibraryBranch(
        "information", "의정부정보도서관", "정보도서관", "경기도 의정부시 의정로 41"
    ),
    UijeongbuLibraryBranch(
        "science", "의정부과학도서관", "과학도서관", "경기도 의정부시 추동로124번길 52"
    ),
    UijeongbuLibraryBranch(
        "art", "의정부미술도서관", "미술도서관", "경기도 의정부시 민락로 248"
    ),
    UijeongbuLibraryBranch(
        "music", "의정부음악도서관", "음악도서관", "경기도 의정부시 장곡로 280"
    ),
    UijeongbuLibraryBranch(
        "gajaeul", "가재울도서관", "가재울도서관", "경기도 의정부시 평화로 633"
    ),
    UijeongbuLibraryBranch(
        "english", "의정부영어도서관", "영어도서관", "경기도 의정부시 회룡로 79"
    ),
)
_BRANCH_BY_KEY = {branch.key: branch for branch in UIJEONGBU_LIBRARY_BRANCHES}

UIJEONGBU_LIBRARY_LIVE_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 588,
    "source_totals": {
        "information": 72,
        "science": 89,
        "art": 109,
        "music": 203,
        "gajaeul": 24,
        "english": 91,
    },
    "current_candidates": 21,
    "education_current": 18,
    "volunteer_recruitment_excluded": 3,
    "experience_current": 0,
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_CAPACITY_RE = re.compile(r"(\d[\d,]*)\s*(?:명)?\s*/\s*(\d[\d,]*)")
_NONNEGATIVE_ID_RE = re.compile(r"\d+")
_POSITIVE_ID_RE = re.compile(r"[1-9]\d*")
_HOMEPAGE_ID_RE = re.compile(r"h[1-9]\d*")
_STATUS_CONTRACT: Mapping[str, tuple[str, str]] = {
    "status_0": ("수강신청", "OPEN"),
    "status_4": ("접수마감", "CLOSED"),
    "status_5": ("정원마감", "CLOSED"),
    "status_6": ("신청대기", "SCHEDULED"),
    "status_9": ("수강종료", "CLOSED"),
}
_TABLE_HEADERS = ("행사명", "접수인원", "강좌기간", "접수기간", "접수상태")
_DETAIL_REQUIRED = frozenset(
    {
        "강의 분류",
        "강의 설명",
        "강의장소",
        "강의대상",
        "접수기간",
        "강의기간(*)",
        "강의시간",
        "강의요일",
        "현재 참여 / 모집",
    }
)
_EDUCATION_CATEGORIES = frozenset(
    {"문화강좌", "독서행사", "영어동아리 잉글루", "English Friends"}
)
_EXPERIENCE_CATEGORIES = frozenset(
    {"소풍길탐방", "도서관 투어(개인)", "도서관투어"}
)
_VOLUNTEER_CATEGORY = "영어책 읽어주기 봉사단"
_NOTICE_CATEGORIES = frozenset({"공지", "공지사항", "프로그램 안내"})
_EDUCATION_MARKERS = (
    "강연",
    "강좌",
    "저자",
    "독서교실",
    "인문학",
    "북토크",
    "토크콘서트",
    "배우",
    "학습",
    "수업",
    "창작과정",
)
_PERFORMANCE_MARKERS = (
    "공연",
    "영화상영",
    "인형극",
    "마술극",
    "콘서트",
)


class UijeongbuLibraryContractError(ValueError):
    """Raised when the audited public source contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider")).upper()


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _positive(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise UijeongbuLibraryContractError(
            f"{name} must be a positive integer"
        ) from exc
    if result < 1:
        raise UijeongbuLibraryContractError(f"{name} must be a positive integer")
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _exact_target_url(value: str) -> bool:
    got = urlparse(value)
    wanted = urlparse(UIJEONGBU_LIBRARY_URL)
    return bool(
        got.scheme == "https"
        and got.hostname == wanted.hostname
        and got.port is None
        and got.path == wanted.path
        and not got.query
        and not got.params
        and not got.fragment
        and not got.username
        and not got.password
    )


def is_uijeongbu_library_target(target: Any) -> bool:
    return _provider(target) == UIJEONGBU_LIBRARY_PROVIDER and _exact_target_url(
        _target_url(target)
    )


is_target = is_uijeongbu_library_target


def uijeongbu_library_detail_url(
    branch_key: str,
    group_idx: str | int,
    category_idx: str | int,
    teach_idx: str | int,
) -> str:
    branch = _BRANCH_BY_KEY.get(_clean(branch_key))
    group = _clean(group_idx)
    category = _clean(category_idx)
    teach = _clean(teach_idx)
    if branch is None:
        raise UijeongbuLibraryContractError("unknown library branch")
    if not _NONNEGATIVE_ID_RE.fullmatch(group):
        raise UijeongbuLibraryContractError("invalid group identity")
    if not _NONNEGATIVE_ID_RE.fullmatch(category):
        raise UijeongbuLibraryContractError("invalid category identity")
    if not _POSITIVE_ID_RE.fullmatch(teach):
        raise UijeongbuLibraryContractError("invalid teach identity")
    query = urlencode(
        {
            "group_idx": group,
            "teach_idx": teach,
            "menu_idx": "24",
            "category_idx": category,
            "large_category_idx": "0",
        }
    )
    return f"https://{UIJEONGBU_LIBRARY_HOST}{branch.detail_path}?{query}"


def _validate_public_url(value: str) -> tuple[str, Optional[UijeongbuLibraryBranch]]:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != UIJEONGBU_LIBRARY_HOST
        or parsed.port is not None
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise UijeongbuLibraryContractError("request escaped the audited public host")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == "/main/index.do":
        if query:
            raise UijeongbuLibraryContractError("unexpected directory query")
        return "directory", None
    for branch in UIJEONGBU_LIBRARY_BRANCHES:
        if parsed.path == branch.list_path:
            if query != {"menu_idx": ["24"]}:
                raise UijeongbuLibraryContractError("unexpected list query")
            return "list", branch
        if parsed.path == branch.detail_path:
            if set(query) != {
                "group_idx",
                "teach_idx",
                "menu_idx",
                "category_idx",
                "large_category_idx",
            }:
                raise UijeongbuLibraryContractError("unexpected detail query")
            if query["menu_idx"] != ["24"] or query["large_category_idx"] != ["0"]:
                raise UijeongbuLibraryContractError("wrong detail menu identity")
            if not _NONNEGATIVE_ID_RE.fullmatch(query["group_idx"][0]):
                raise UijeongbuLibraryContractError("invalid detail group identity")
            if not _NONNEGATIVE_ID_RE.fullmatch(query["category_idx"][0]):
                raise UijeongbuLibraryContractError("invalid detail category identity")
            if not _POSITIVE_ID_RE.fullmatch(query["teach_idx"][0]):
                raise UijeongbuLibraryContractError("invalid detail teach identity")
            return "detail", branch
    raise UijeongbuLibraryContractError("private or unrelated endpoint blocked")


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return session


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_soup(
    response: Any,
    expected_url: str,
    kind: str,
    branch: Optional[UijeongbuLibraryBranch],
) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise UijeongbuLibraryContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise UijeongbuLibraryContractError("redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url:
        final = urlparse(final_url)
        expected = urlparse(expected_url)
        final_path = final.path.split(";jsessionid", 1)[0]
        if (
            final.scheme != "https"
            or final.hostname != expected.hostname
            or final_path != expected.path
        ):
            raise UijeongbuLibraryContractError(
                "response escaped the audited public endpoint"
            )
    content = getattr(response, "content", b"")
    if content:
        text = bytes(content).decode("utf-8", errors="replace")
    else:
        text = str(getattr(response, "text", "") or "")
    if not text:
        raise UijeongbuLibraryContractError("empty public response")
    soup = BeautifulSoup(text, "lxml")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if kind == "directory":
        if title != "의정부시 도서관 대표홈페이지":
            raise UijeongbuLibraryContractError("directory title changed")
    else:
        if branch is None or not title.startswith(f"{branch.name} >"):
            raise UijeongbuLibraryContractError(f"{kind} branch title changed")
        headings = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h3")]
        if "프로그램신청" not in headings:
            raise UijeongbuLibraryContractError(f"{kind} menu identity changed")
    return soup


class _Runner:
    def __init__(self, session_factory: SessionFactory, timeout: int):
        self._factory = session_factory
        self._timeout = _positive(timeout, "timeout")
        self._session: Any = None
        self.requests = 0

    def __enter__(self) -> "_Runner":
        self._session = self._factory()
        if self._session is None:
            raise UijeongbuLibraryContractError("session factory returned no session")
        return self

    def __exit__(self, *_args: Any) -> None:
        _close(self._session)

    def soup(self, url: str, *, referer: str = "") -> BeautifulSoup:
        kind, branch = _validate_public_url(url)
        response = self._session.get(
            url,
            timeout=self._timeout,
            allow_redirects=False,
            headers={"Referer": referer} if referer else None,
        )
        self.requests += 1
        return _response_soup(response, url, kind, branch)


def _directory_contract(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    for branch in UIJEONGBU_LIBRARY_BRANCHES:
        links = [
            node
            for node in soup.select("a[href]")
            if urlparse(_clean(node.get("href"))).path == branch.index_path
        ]
        labels = {_clean(node.get_text(" ", strip=True)) for node in links}
        if not links or labels != {branch.short_name}:
            raise UijeongbuLibraryContractError(
                f"directory ownership changed for {branch.key}"
            )
        found.append((branch.key, branch.index_path))
    return tuple(found)


def _dates(value: str, field: str, *, single_allowed: bool) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    allowed = {1, 2} if single_allowed else {2}
    if len(matches) not in allowed:
        raise UijeongbuLibraryContractError(f"{field} date range changed")
    parsed = [date(int(year), int(month), int(day)) for year, month, day in matches]
    start, end = parsed[0], parsed[-1]
    if end < start:
        raise UijeongbuLibraryContractError(f"{field} date range is reversed")
    return start, end


def _capacity(value: str, field: str) -> tuple[int, int]:
    matches = _CAPACITY_RE.findall(_clean(value))
    if not matches:
        raise UijeongbuLibraryContractError(f"{field} capacity changed")
    current, total = (int(item.replace(",", "")) for item in matches[0])
    if current < 0 or total < 0:
        raise UijeongbuLibraryContractError(f"{field} capacity changed")
    return current, total


def _unique_ledger_table(soup: BeautifulSoup) -> Tag:
    matches: list[Tag] = []
    for table in soup.select("table"):
        headers = tuple(
            _clean(node.get_text(" ", strip=True)) for node in table.select("thead th")
        )
        if headers == _TABLE_HEADERS:
            matches.append(table)
    if len(matches) != 1:
        raise UijeongbuLibraryContractError("complete ledger table changed")
    return matches[0]


def _list_contract(
    soup: BeautifulSoup, branch: UijeongbuLibraryBranch
) -> list[dict[str, Any]]:
    forms = soup.select("form#teach")
    if len(forms) != 1:
        raise UijeongbuLibraryContractError("public teach form changed")
    form = forms[0]
    expected_action = f"/{branch.key}/module/teach/student/save.do"
    if (
        _clean(form.get("action")) != expected_action
        or _clean(form.get("method")).lower() != "post"
    ):
        raise UijeongbuLibraryContractError("teach form ownership changed")
    menu_values = {
        _clean(node.get("value")) for node in form.select("input[name='menu_idx']")
    }
    if menu_values != {"24"}:
        raise UijeongbuLibraryContractError("teach form menu identity changed")
    if form.select("[name='pageIndex'], [name='viewPage'], [name='currentPage']"):
        raise UijeongbuLibraryContractError("unexpected ledger pagination field")
    for link in soup.select("a[href]"):
        query = parse_qs(urlparse(_clean(link.get("href"))).query)
        if set(query) & {"pageIndex", "viewPage", "currentPage"}:
            raise UijeongbuLibraryContractError("unexpected ledger pagination link")

    table = _unique_ledger_table(soup)
    rows: list[dict[str, Any]] = []
    for source_row in table.select("tbody > tr"):
        cells = source_row.find_all("td", recursive=False)
        classes = [_clean(item) for item in source_row.get("class", [])]
        if len(cells) != 5 or len(classes) != 1 or classes[0] not in _STATUS_CONTRACT:
            raise UijeongbuLibraryContractError("ledger row/status structure changed")
        status_label, status = _STATUS_CONTRACT[classes[0]]
        if _clean(cells[4].get_text(" ", strip=True)) != status_label:
            raise UijeongbuLibraryContractError("ledger status label changed")
        title_links = source_row.select("a.name.detail-btn")
        categories = source_row.select("span.ca")
        if len(title_links) != 1 or len(categories) != 1:
            raise UijeongbuLibraryContractError("ledger identity/category changed")
        title_link = title_links[0]
        title = _clean(title_link.get_text(" ", strip=True))
        category = _clean(categories[0].get_text(" ", strip=True))
        group = _clean(title_link.get("keyvalue1"))
        category_id = _clean(title_link.get("keyvalue2"))
        teach = _clean(title_link.get("keyvalue3"))
        if (
            not title
            or not category
            or _clean(title_link.get("href")) not in {"", "#"}
            or not _NONNEGATIVE_ID_RE.fullmatch(group)
            or not _NONNEGATIVE_ID_RE.fullmatch(category_id)
            or not _POSITIVE_ID_RE.fullmatch(teach)
        ):
            raise UijeongbuLibraryContractError("ledger immutable identity changed")
        start, end = _dates(cells[2].get_text(" ", strip=True), "course", single_allowed=True)
        apply_start, apply_end = _dates(
            cells[3].get_text(" ", strip=True), "application", single_allowed=False
        )
        capacity_current, capacity_total = _capacity(
            cells[1].get_text(" ", strip=True), "list"
        )
        venue_nodes = [
            _clean(node.get_text(" ", strip=True))[len("장소 :") :].strip()
            for node in cells[0].select("dd.con")
            if _clean(node.get_text(" ", strip=True)).startswith("장소 :")
        ]
        target_nodes = [
            _clean(node.get_text(" ", strip=True))[len("대상 :") :].strip()
            for node in cells[0].select("dd.con")
            if _clean(node.get_text(" ", strip=True)).startswith("대상 :")
        ]
        if len(venue_nodes) != 1 or len(target_nodes) != 1:
            raise UijeongbuLibraryContractError("ledger venue/target changed")
        detail_url = uijeongbu_library_detail_url(branch.key, group, category_id, teach)
        application_controls = cells[4].select("a.add")
        has_application = status == "OPEN"
        if has_application:
            if len(application_controls) != 1:
                raise UijeongbuLibraryContractError("open application control changed")
            control = application_controls[0]
            if (
                _clean(control.get("href")) not in {"", "#"}
                or not _HOMEPAGE_ID_RE.fullmatch(_clean(control.get("keyvalue1")))
                or _clean(control.get("keyvalue2")) != group
                or _clean(control.get("keyvalue3")) != category_id
                or _clean(control.get("keyvalue4")) != teach
                or not _POSITIVE_ID_RE.fullmatch(_clean(control.get("keyvalue5")))
                or _clean(control.get("apply_status")) != "1"
            ):
                raise UijeongbuLibraryContractError(
                    "application control is not bound to the row identity"
                )
        elif application_controls:
            raise UijeongbuLibraryContractError(
                "inactive row unexpectedly exposes an application control"
            )
        rows.append(
            {
                "branch_spec": branch,
                "group_idx": group,
                "category_idx": category_id,
                "teach_idx": teach,
                "provider_course_id": (
                    f"uilib:{branch.key}:{group}:{category_id}:{teach}"
                ),
                "title": title,
                "category_raw": category,
                "status": status,
                "status_raw": status_label,
                "start_date": start,
                "end_date": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "capacity_raw": _clean(cells[1].get_text(" ", strip=True)),
                "schedule_raw": _clean(cells[2].get_text(" ", strip=True)),
                "venue": venue_nodes[0],
                "target": target_nodes[0],
                "detail_url": detail_url,
                "has_application": has_application,
            }
        )
    if not rows:
        raise UijeongbuLibraryContractError("official branch ledger unexpectedly empty")
    return rows


def _fingerprint(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("category_raw")),
            _clean(row.get("status")),
            str(row.get("start_date")),
            str(row.get("end_date")),
            _clean(row.get("capacity_raw")),
        )
        for row in rows
    )


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for table_row in soup.select("table tr"):
        cells = table_row.find_all(["th", "td"], recursive=False)
        for index in range(len(cells) - 1):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                continue
            label = _clean(cells[index].get_text(" ", strip=True))
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if not label:
                continue
            if label in result and result[label] != value:
                raise UijeongbuLibraryContractError("ambiguous detail field")
            result[label] = value
    if not _DETAIL_REQUIRED.issubset(result):
        raise UijeongbuLibraryContractError("detail field contract changed")
    return result


def _classify_current(row: Mapping[str, Any], pairs: Mapping[str, str]) -> tuple[str, str]:
    category = _clean(row.get("category_raw"))
    detail_category = _clean(pairs.get("강의 분류"))
    description = _clean(pairs.get("강의 설명"))
    evidence = _clean(f"{row.get('title')} {detail_category} {description}")
    if category == _VOLUNTEER_CATEGORY or _VOLUNTEER_CATEGORY in detail_category:
        if "봉사" not in evidence:
            raise UijeongbuLibraryContractError("volunteer exclusion evidence changed")
        return "non_course", "volunteer_recruitment"
    if category in _NOTICE_CATEGORIES:
        if not any(marker in evidence for marker in ("공지", "안내")):
            raise UijeongbuLibraryContractError("notice exclusion evidence changed")
        return "non_course", "notice"
    if category in _EXPERIENCE_CATEGORIES:
        if not any(marker in evidence for marker in ("투어", "탐방")):
            raise UijeongbuLibraryContractError("experience exclusion evidence changed")
        return "experience", "library_tour_or_path_exploration"
    if category in _EDUCATION_CATEGORIES:
        return "education", "explicit_education_category"
    if category == "문화행사":
        education = [marker for marker in _EDUCATION_MARKERS if marker in evidence]
        performance = [marker for marker in _PERFORMANCE_MARKERS if marker in evidence]
        if education:
            return "education", f"mixed_category_education:{','.join(education[:3])}"
        if performance:
            return "non_course", "performance_without_education_contract"
        raise UijeongbuLibraryContractError(
            "current mixed 문화행사 lacks reviewed education/non-course evidence"
        )
    raise UijeongbuLibraryContractError(
        f"unreviewed current programme category: {category}"
    )


def _enrich_detail(
    soup: BeautifulSoup, source: dict[str, Any]
) -> tuple[Optional[dict[str, Any]], str]:
    headings = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h3")]
    if not headings or headings[-1] != source["title"]:
        raise UijeongbuLibraryContractError("detail title does not match source row")
    pairs = _detail_pairs(soup)
    detail_start, detail_end = _dates(
        pairs["강의기간(*)"], "detail course", single_allowed=False
    )
    detail_apply_start, detail_apply_end = _dates(
        pairs["접수기간"], "detail application", single_allowed=False
    )
    if (detail_start, detail_end) != (source["start_date"], source["end_date"]):
        raise UijeongbuLibraryContractError("detail course period does not match list")
    if (detail_apply_start, detail_apply_end) != (
        source["apply_start"],
        source["apply_end"],
    ):
        raise UijeongbuLibraryContractError(
            "detail application period does not match list"
        )
    detail_current, detail_total = _capacity(
        pairs["현재 참여 / 모집"], "detail"
    )
    if (detail_current, detail_total) != (
        source["capacity_current"],
        source["capacity_total"],
    ):
        raise UijeongbuLibraryContractError("detail capacity does not match list")
    detail_venue = pairs["강의장소"]
    detail_target = pairs["강의대상"]
    if not detail_venue or not detail_target:
        raise UijeongbuLibraryContractError("detail venue/target is empty")
    classification, reason = _classify_current(source, pairs)
    if classification != "education":
        return None, reason

    branch: UijeongbuLibraryBranch = source["branch_spec"]
    open_for_application = bool(source["has_application"])
    detail_url = source["detail_url"]
    row: dict[str, Any] = {
        "provider": UIJEONGBU_LIBRARY_PROVIDER,
        "provider_course_id": source["provider_course_id"],
        "prefer_incoming_provider_course_id": True,
        "title": source["title"],
        "branch": branch.name,
        "branch_code": f"UILIB_{branch.key.upper()}",
        "preserve_branch": True,
        "category": source["category_raw"],
        "category_raw": source["category_raw"],
        "raw_url": detail_url,
        "application_url": detail_url if open_for_application else "",
        "status": source["status"],
        "fee": "",
        "period": f"{detail_start.isoformat()} ~ {detail_end.isoformat()}",
        "start_date": detail_start.isoformat(),
        "end_date": detail_end.isoformat(),
        "apply_period": (
            f"{detail_apply_start.isoformat()} ~ {detail_apply_end.isoformat()}"
        ),
        "schedule_raw": _clean(f"{pairs['강의요일']} {pairs['강의시간']}"),
        "target": detail_target,
        "eligibility_raw": detail_target,
        "capacity": source["capacity_raw"],
        "capacity_current": detail_current,
        "capacity_total": detail_total,
        "capacity_remaining": max(detail_total - detail_current, 0),
        "room": detail_venue,
        "venue_name": detail_venue,
        "address": branch.address,
        "venue_address": branch.address,
        "branch_address_source": "OFFICIAL_UILIB_BRANCH_PAGE",
        "branch_location_verified": True,
        "branch_location_confidence": 100,
        "branch_location_query": branch.list_url,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": UIJEONGBU_LIBRARY_PARSER,
        "program_type": "교육",
        "application_type": (
            "ONLINE_RESERVATION" if open_for_application else "INFO_ONLY"
        ),
        "reservation_available": open_for_application,
        "municipality_code": UIJEONGBU_LIBRARY_MUNICIPALITY_CODE,
        "municipality_name": UIJEONGBU_LIBRARY_MUNICIPALITY_NAME,
        "sido": "경기도",
        "sigungu": "의정부시",
        "raw_fields": {
            "parser": UIJEONGBU_LIBRARY_PARSER,
            "branch_key": branch.key,
            "group_idx": source["group_idx"],
            "category_idx": source["category_idx"],
            "teach_idx": source["teach_idx"],
            "status_raw": source["status_raw"],
            "source_category": source["category_raw"],
            "classification": "education",
            "classification_reason": reason,
            "application_control_verified": open_for_application,
            "application_endpoint_called": False,
            "detail_contract_verified": True,
            "description_persisted": False,
            "instructor_persisted": False,
        },
    }
    return row, reason


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "directory_requests": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_candidates": 0,
        "education_current": 0,
        "experience_current": 0,
        "excluded_non_course_count": 0,
        "returned_count": 0,
        "boundary_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "application_endpoints_called": 0,
        "pii_payload_persisted": False,
        "configured_collection_error": message,
        "ownership_scope": UIJEONGBU_LIBRARY_OWNERSHIP_SCOPE,
        "municipality_code": UIJEONGBU_LIBRARY_MUNICIPALITY_CODE,
    }


def collect_uijeongbu_library_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 50,
    detail_limit: int = 100,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete, current/future, education-only library snapshot."""

    if not is_uijeongbu_library_target(target):
        return [], UIJEONGBU_LIBRARY_PARSER, _failure(
            "target does not match the audited Uijeongbu library owner"
        )

    directory_requests = 0
    list_requests = 0
    detail_attempts = 0
    detail_pages = 0
    source_total = 0
    source_cap_reached = False
    stable_directory = False
    stable_ledgers = False
    source_by_branch: dict[str, list[dict[str, Any]]] = {}
    try:
        allowed_pages = _positive(max_pages, "max_pages")
        allowed_details = _positive(detail_limit, "detail_limit")
        required_ledger_requests = 2 + 2 * len(UIJEONGBU_LIBRARY_BRANCHES)
        if allowed_pages < required_ledger_requests:
            source_cap_reached = True
            raise UijeongbuLibraryContractError(
                f"max_pages cap allows {allowed_pages} of "
                f"{required_ledger_requests} required directory/list requests"
            )
        cutoff = _today(today)
        with _Runner(session_factory or _default_session_factory, timeout) as runner:
            directory = _directory_contract(runner.soup(UIJEONGBU_LIBRARY_URL))
            directory_requests += 1
            for branch in UIJEONGBU_LIBRARY_BRANCHES:
                source_by_branch[branch.key] = _list_contract(
                    runner.soup(branch.list_url, referer=UIJEONGBU_LIBRARY_URL),
                    branch,
                )
                list_requests += 1

            directory_recheck = _directory_contract(
                runner.soup(UIJEONGBU_LIBRARY_URL, referer=UIJEONGBU_LIBRARY_URL)
            )
            directory_requests += 1
            stable_directory = directory_recheck == directory
            if not stable_directory:
                raise UijeongbuLibraryContractError(
                    "six-library directory changed during snapshot"
                )

            for branch in UIJEONGBU_LIBRARY_BRANCHES:
                recheck = _list_contract(
                    runner.soup(branch.list_url, referer=UIJEONGBU_LIBRARY_URL),
                    branch,
                )
                list_requests += 1
                if _fingerprint(recheck) != _fingerprint(source_by_branch[branch.key]):
                    raise UijeongbuLibraryContractError(
                        f"{branch.key} ledger changed during snapshot"
                    )
            stable_ledgers = True

            all_source = [
                row
                for branch in UIJEONGBU_LIBRARY_BRANCHES
                for row in source_by_branch[branch.key]
            ]
            source_total = len(all_source)
            identities = [row["provider_course_id"] for row in all_source]
            if len(identities) != len(set(identities)):
                raise UijeongbuLibraryContractError("duplicate source identities")
            current_source = [row for row in all_source if row["end_date"] >= cutoff]
            if len(current_source) > allowed_details:
                source_cap_reached = True
                raise UijeongbuLibraryContractError(
                    f"detail_limit cap allows {allowed_details} of "
                    f"{len(current_source)} required current/future details"
                )

            education_rows: list[dict[str, Any]] = []
            exclusion_reasons: Counter[str] = Counter()
            experience_current = 0
            for source in current_source:
                detail_attempts += 1
                detail_soup = runner.soup(
                    source["detail_url"], referer=source["branch_spec"].list_url
                )
                detail_pages += 1
                row, reason = _enrich_detail(detail_soup, source)
                if row is None:
                    exclusion_reasons[reason] += 1
                    if reason == "library_tour_or_path_exploration":
                        experience_current += 1
                else:
                    education_rows.append(row)

            result = list((dedupe_rows or _dedupe_default)(education_rows))
            if [row["provider_course_id"] for row in result] != [
                row["provider_course_id"] for row in education_rows
            ]:
                raise UijeongbuLibraryContractError(
                    "dedupe changed a complete ordered snapshot"
                )
            source_totals = {
                branch.key: len(source_by_branch[branch.key])
                for branch in UIJEONGBU_LIBRARY_BRANCHES
            }
            archived_experience_count = sum(
                row["category_raw"] in _EXPERIENCE_CATEGORIES for row in all_source
            )
            meta = {
                "pages": len(UIJEONGBU_LIBRARY_BRANCHES),
                "source_pages": len(UIJEONGBU_LIBRARY_BRANCHES),
                "directory_requests": directory_requests,
                "list_requests": list_requests,
                "required_ledger_requests": required_ledger_requests,
                "physical_requests": runner.requests,
                "source_total": source_total,
                "source_rows": source_total,
                "source_totals": source_totals,
                "branch_ledger_counts": source_totals,
                "current_candidates": len(current_source),
                "current_count": len(current_source),
                "education_current": len(education_rows),
                "experience_current": experience_current,
                "archived_experience_count": archived_experience_count,
                "excluded_non_course_count": sum(exclusion_reasons.values()),
                "exclusion_counts": dict(exclusion_reasons),
                "returned_count": len(result),
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "application_control_count": sum(
                    bool(row.get("reservation_available")) for row in result
                ),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "unpaginated_ledgers": True,
                "stable_directory": stable_directory,
                "stable_ledgers": stable_ledgers,
                "boundary_complete": True,
                "details_complete": detail_pages == len(current_source),
                "snapshot_complete": True,
                "source_cap_reached": False,
                "no_current_data": not education_rows,
                "no_current_reason": (
                    "complete six-library ledgers contain no current education rows"
                    if not education_rows
                    else ""
                ),
                "application_endpoints_called": 0,
                "login_endpoints_called": 0,
                "attachment_endpoints_called": 0,
                "pii_payload_persisted": False,
                "configured_collection_error": "",
                "ownership_scope": UIJEONGBU_LIBRARY_OWNERSHIP_SCOPE,
                "municipality_code": UIJEONGBU_LIBRARY_MUNICIPALITY_CODE,
                "covered_municipalities": [
                    {
                        "code": UIJEONGBU_LIBRARY_MUNICIPALITY_CODE,
                        "sido": "경기도",
                        "sigungu": "의정부시",
                        "full_name": UIJEONGBU_LIBRARY_MUNICIPALITY_NAME,
                    }
                ],
            }
            return result, UIJEONGBU_LIBRARY_PARSER, meta
    except Exception as exc:
        meta = _failure(f"{type(exc).__name__}: {_clean(exc)}")
        meta.update(
            {
                "pages": len(source_by_branch),
                "directory_requests": directory_requests,
                "list_requests": list_requests,
                "source_total": source_total,
                "source_rows": sum(len(rows) for rows in source_by_branch.values()),
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "stable_directory": stable_directory,
                "stable_ledgers": stable_ledgers,
                "source_cap_reached": source_cap_reached,
            }
        )
        return [], UIJEONGBU_LIBRARY_PARSER, meta


collect = collect_uijeongbu_library_courses


__all__ = [
    "UIJEONGBU_LIBRARY_PROVIDER",
    "UIJEONGBU_LIBRARY_URL",
    "UIJEONGBU_LIBRARY_HOST",
    "UIJEONGBU_LIBRARY_MUNICIPALITY_CODE",
    "UIJEONGBU_LIBRARY_MUNICIPALITY_NAME",
    "UIJEONGBU_LIBRARY_PARSER",
    "UIJEONGBU_LIBRARY_BRANCHES",
    "UIJEONGBU_LIBRARY_LIVE_BASELINE",
    "UijeongbuLibraryContractError",
    "collect_uijeongbu_library_courses",
    "is_uijeongbu_library_target",
    "uijeongbu_library_detail_url",
]
