"""Fail-closed collectors for Gimpo City's official experience ledgers.

The integrated-reservation experience menu owns two independent public
ledgers.  ``webEtcResveList.do`` is the general visit/experience programme
catalogue, while ``selectTnLesureResveListU.do`` is the Gold Waterway leisure
catalogue.  They have different identifiers, pagination and detail contracts,
so they intentionally use separate provider identities.

Only the public list and public detail pages are requested.  Login, identity
verification, application, applicant, payment, cancellation, availability and
personal-reservation endpoints are never called.  Production callers must
inject the repository-managed session factory.  Raw requests are available
only behind an explicit test/live-audit switch.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


GIMPO_MUNICIPALITY_CODE = "4157000000"
GIMPO_MUNICIPALITY_NAME = "경기도 김포시"

GIMPO_EXPERIENCE_PROVIDER = "MUNI_WWW_GIMPO_GO_KR_50CB0A4D"
GIMPO_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_951501314C51"
GIMPO_EXPERIENCE_URL = (
    "https://www.gimpo.go.kr/reserve/webEtcResveList.do?"
    "etcProgramSection=EXPERIENCE&key=113&rep=1"
)
GIMPO_EXPERIENCE_LIST_ENDPOINT = (
    "https://www.gimpo.go.kr/reserve/webEtcResveList.do"
)
GIMPO_EXPERIENCE_DETAIL_ENDPOINT = (
    "https://www.gimpo.go.kr/reserve/webEtcResveView.do"
)
GIMPO_EXPERIENCE_APPLICATION_ENDPOINT = (
    "https://www.gimpo.go.kr/reserve/webEtcResveApplcntAgree.do"
)
GIMPO_EXPERIENCE_BRANCH = "김포시 통합예약 견학·체험"

GIMPO_LEISURE_PROVIDER = "MUNI_WWW_GIMPO_GO_KR_A29FAE31"
GIMPO_LEISURE_CANDIDATE_ID = "MUNI_IR_5154233B1082"
GIMPO_LEISURE_URL = (
    "https://www.gimpo.go.kr/reserve/selectTnLesureResveListU.do?key=113"
)
GIMPO_LEISURE_LIST_ENDPOINT = (
    "https://www.gimpo.go.kr/reserve/selectTnLesureResveListU.do"
)
GIMPO_LEISURE_DETAIL_ENDPOINT = (
    "https://www.gimpo.go.kr/reserve/viewTnLesureResveU.do"
)
GIMPO_LEISURE_APPLICATION_ENDPOINT = (
    "https://www.gimpo.go.kr/reserve/step0TnLesureApplcntViewU.do"
)
GIMPO_LEISURE_BRANCH = "금빛수로 수상레저시설"

GIMPO_EXPERIENCE_PAGE_SIZE = 10
GIMPO_PARSER = (
    "gimpo_official_experience_owner_dispatch+declared_total_complete_pages+"
    "immediate_sentinel+stable_first_page+all_current_public_details+"
    "notice_test_exclusion+no_application_calls"
)
GIMPO_OWNERSHIP_SCOPE = "gimpo_official_integrated_experience_current_future"
GIMPO_LEISURE_OWNERSHIP_SCOPE = "gimpo_official_gold_waterway_current_future"
GIMPO_DEFAULT_MAX_PAGES = 30
GIMPO_DEFAULT_DETAIL_LIMIT = 100
GIMPO_SESSION_REQUEST_LIMIT = 80

GIMPO_LIVE_AUDIT_BASELINE: Mapping[str, Mapping[str, Any]] = {
    "experience": {
        "checked_at": "2026-08-05",
        "source_total": 86,
        "data_pages": 9,
        "current_count": 17,
        "sentinel_page": 10,
        "notice_rows": 2,
        "test_rows": 1,
    },
    "leisure": {
        "checked_at": "2026-08-05",
        "source_total": 8,
        "data_pages": 1,
        "current_count": 8,
        "sentinel_is_clamped_duplicate": True,
    },
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_INTEGER_RE = re.compile(r"\d[\d,]*")
_GENERIC_ID_RE = re.compile(r"[1-9]\d*")
_ADDRESS_CALL_RE = re.compile(
    r"fn_setAddressToMapPosition\(\s*'((?:\\.|[^'])*)'\s*,",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_GENERIC_STATUS = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "완료": "CLOSED",
    "접수마감": "CLOSED",
}
_LEISURE_STATUS = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "완료": "CLOSED",
}
_NOTICE_PREFIXES = ("(공지)", "(공지사항)", "[공지]", "공지사항")
_TEST_MARKERS = ("예약하지 마세요", "테스트 모집", "신청하지 마세요")


class GimpoExperienceContractError(ValueError):
    """Raised when an audited public-source contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _positive(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GimpoExperienceContractError(f"{name} must be a positive integer") from exc
    if result < 1:
        raise GimpoExperienceContractError(f"{name} must be a positive integer")
    return result


def _exact_target(url: str, canonical: str) -> bool:
    parsed, wanted = urlparse(url), urlparse(canonical)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == wanted.hostname
        and parsed.port is None
        and parsed.path == wanted.path
        and parse_qs(parsed.query, keep_blank_values=True)
        == parse_qs(wanted.query, keep_blank_values=True)
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


_TARGETS = {
    GIMPO_EXPERIENCE_PROVIDER: GIMPO_EXPERIENCE_URL,
    GIMPO_LEISURE_PROVIDER: GIMPO_LEISURE_URL,
}


def is_gimpo_experience_target(target: Any) -> bool:
    canonical = _TARGETS.get(_provider(target))
    return bool(canonical and _exact_target(_target_url(target), canonical))


is_target = is_gimpo_experience_target


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


def _response_soup(response: Any, expected_url: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise GimpoExperienceContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise GimpoExperienceContractError("redirects are not accepted")
    final = _clean(getattr(response, "url", ""))
    if final:
        got, wanted = urlparse(final), urlparse(expected_url)
        if (
            got.scheme != "https"
            or got.hostname != wanted.hostname
            or got.path != wanted.path
        ):
            raise GimpoExperienceContractError("response escaped the audited endpoint")
    text = str(getattr(response, "text", "") or "")
    if not text and getattr(response, "content", None):
        text = bytes(response.content).decode("utf-8", errors="replace")
    if not text:
        raise GimpoExperienceContractError("empty public response")
    soup = BeautifulSoup(text, "lxml")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "로그인" in title or "안내메시지" in title:
        raise GimpoExperienceContractError("public detail resolved to a guarded page")
    return soup


def _query_values(parsed: Any) -> dict[str, list[str]]:
    return parse_qs(parsed.query, keep_blank_values=True)


def _assert_safe_public_url(url: str) -> None:
    parsed = urlparse(_clean(url))
    if not (
        parsed.scheme == "https"
        and parsed.hostname == "www.gimpo.go.kr"
        and parsed.port is None
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        raise GimpoExperienceContractError("URL escaped the audited Gimpo host")
    query = _query_values(parsed)
    path = parsed.path
    if path == "/reserve/webEtcResveList.do":
        allowed = {
            "key", "etcProgramSection", "rep", "searchEtcGroup", "pageUnit",
            "searchCnd", "searchKrwd", "pageIndex",
        }
        if set(query) - allowed or query.get("key") != ["113"]:
            raise GimpoExperienceContractError("invalid general-experience list query")
        if query.get("etcProgramSection") != ["EXPERIENCE"]:
            raise GimpoExperienceContractError("invalid general-experience section")
        return
    if path == "/reserve/webEtcResveView.do":
        allowed = {"key", "etcProgramSection", "searchEtcGroup", "searchEtcResveNo"}
        identity = (query.get("searchEtcResveNo") or [""])[0]
        if (
            set(query) - allowed
            or query.get("key") != ["113"]
            or query.get("etcProgramSection") != ["EXPERIENCE"]
            or not _GENERIC_ID_RE.fullmatch(identity)
        ):
            raise GimpoExperienceContractError("invalid general-experience detail query")
        return
    if path == "/reserve/selectTnLesureResveListU.do":
        if set(query) - {"key", "cpn"} or query.get("key") != ["113"]:
            raise GimpoExperienceContractError("invalid leisure list query")
        page = (query.get("cpn") or ["1"])[0]
        if not _GENERIC_ID_RE.fullmatch(page):
            raise GimpoExperienceContractError("invalid leisure page")
        return
    if path == "/reserve/viewTnLesureResveU.do":
        identity = (query.get("srvcNo") or [""])[0]
        if (
            set(query) - {"key", "srvcNo"}
            or query.get("key") != ["113"]
            or not _GENERIC_ID_RE.fullmatch(identity)
        ):
            raise GimpoExperienceContractError("invalid leisure detail query")
        return
    raise GimpoExperienceContractError("login/application/non-public endpoint refused")


class _Runner:
    def __init__(self, factory: SessionFactory, timeout: int) -> None:
        self.factory = factory
        self.timeout = timeout
        self.session: Any = None
        self.requests = 0
        self.sessions_created = 0

    def __enter__(self) -> "_Runner":
        self.session = self.factory()
        self.sessions_created = 1
        headers = getattr(self.session, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                }
            )
        return self

    def __exit__(self, *_: Any) -> None:
        _close(self.session)

    def soup(self, url: str, *, referer: str = "") -> BeautifulSoup:
        _assert_safe_public_url(url)
        if self.requests >= GIMPO_SESSION_REQUEST_LIMIT:
            raise GimpoExperienceContractError("audited session request budget exceeded")
        headers = {"Referer": referer} if referer else None
        self.requests += 1
        response = self.session.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
            headers=headers,
        )
        return _response_soup(response, url)


def _date_range(value: Any) -> tuple[str, str]:
    values = [
        f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        for y, m, d in _DATE_RE.findall(_clean(value))
    ]
    if len(values) < 2:
        raise GimpoExperienceContractError("missing complete public date range")
    for raw in (values[0], values[-1]):
        date.fromisoformat(raw)
    return values[0], values[-1]


def _information(item: Tag, subject_selector: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in item.select("li"):
        subject = node.select_one(subject_selector)
        if subject is None:
            continue
        key = _clean(subject.get_text(" ", strip=True))
        clone = BeautifulSoup(str(node), "lxml").select_one("li")
        if clone is None:
            continue
        cloned_subject = clone.select_one(subject_selector)
        if cloned_subject is not None:
            cloned_subject.decompose()
        result[key] = _clean(clone.get_text(" ", strip=True))
    return result


def _source_total(soup: BeautifulSoup, *, leisure: bool = False) -> int:
    if leisure:
        candidates: set[int] = set()
        nodes = [*soup.select(".participation.list"), *soup.select(".small")]
        for node in nodes:
            match = re.search(
                r"총\s*([\d,]+)\s*건",
                _clean(node.get_text(" ", strip=True)),
            )
            if match:
                candidates.add(int(match.group(1).replace(",", "")))
        if len(candidates) == 1:
            return candidates.pop()
        if candidates:
            raise GimpoExperienceContractError(
                "conflicting declared source totals"
            )
    element = soup.select_one("span.small em.em_black")
    if element is not None:
        match = _INTEGER_RE.search(_clean(element.get_text(" ", strip=True)))
        if match:
            return int(match.group().replace(",", ""))
    raise GimpoExperienceContractError("missing unambiguous declared source total")


def _address_from_detail(soup: BeautifulSoup) -> str:
    match = _ADDRESS_CALL_RE.search(str(soup))
    if not match:
        return ""
    raw = match.group(1).replace("\\'", "'").replace("\\\\", "\\")
    return _clean(raw)


def _redact(value: Any) -> str:
    return _clean(_EMAIL_RE.sub(" ", _PHONE_RE.sub(" ", _clean(value))))


def _is_explicit_non_program(title: str) -> tuple[bool, str]:
    clean = _clean(title)
    if clean.startswith(_NOTICE_PREFIXES):
        return True, "notice"
    if any(marker in clean for marker in _TEST_MARKERS):
        return True, "test"
    return False, ""


def _base_row(
    *, provider: str, identity: str, title: str, raw_url: str, source_url: str,
    branch: str, source_status: str, status: str, apply_range: tuple[str, str],
    event_range: tuple[str, str], owner: str,
) -> dict[str, Any]:
    explicit_non_program, reason = _is_explicit_non_program(title)
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:{owner}:{identity}",
        "title": title,
        "description": title,
        "branch": branch,
        "preserve_branch": True,
        "raw_url": raw_url,
        "source_url": source_url,
        "application_url": "",
        "status": status,
        "course_status": status,
        "reservation_available": status == "OPEN",
        "registration_start_date": apply_range[0],
        "registration_end_date": apply_range[1],
        "start_date": event_range[0],
        "end_date": event_range[1],
        "region": GIMPO_MUNICIPALITY_NAME,
        "municipality_code": GIMPO_MUNICIPALITY_CODE,
        "municipality_full_name": GIMPO_MUNICIPALITY_NAME,
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "category": "체험",
        "program_type": "체험",
        "program_type_source": "official_menu",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "facility_type": "공공기관",
        "raw_fields": {
            "source_status": source_status,
            "official_menu": "견학/체험",
            "owner": owner,
            "explicit_non_program": explicit_non_program,
            "non_program_reason": reason,
            "source_contact_omitted": True,
        },
    }


def gimpo_experience_list_url(page: int) -> str:
    page = _positive(page, "page")
    if page == 1:
        return GIMPO_EXPERIENCE_URL
    return GIMPO_EXPERIENCE_LIST_ENDPOINT + "?" + urlencode(
        {
            "key": "113",
            "etcProgramSection": "EXPERIENCE",
            "searchEtcGroup": "0",
            "pageUnit": str(GIMPO_EXPERIENCE_PAGE_SIZE),
            "searchCnd": "all",
            "searchKrwd": "",
            "pageIndex": str(page),
        }
    )


def gimpo_experience_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _GENERIC_ID_RE.fullmatch(value):
        return ""
    return GIMPO_EXPERIENCE_DETAIL_ENDPOINT + "?" + urlencode(
        {
            "key": "113",
            "etcProgramSection": "EXPERIENCE",
            "searchEtcGroup": "0",
            "searchEtcResveNo": value,
        }
    )


def gimpo_leisure_list_url(page: int) -> str:
    page = _positive(page, "page")
    if page == 1:
        return GIMPO_LEISURE_URL
    return GIMPO_LEISURE_LIST_ENDPOINT + "?" + urlencode(
        {"key": "113", "cpn": str(page)}
    )


def gimpo_leisure_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _GENERIC_ID_RE.fullmatch(value):
        return ""
    return GIMPO_LEISURE_DETAIL_ENDPOINT + "?" + urlencode(
        {"srvcNo": value, "key": "113"}
    )


def _experience_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    form = soup.select_one("form#frm")
    if form is None or _clean(form.get("method")).lower() != "get":
        raise GimpoExperienceContractError("missing general-experience GET form")
    if not _clean(form.get("action")).endswith("webEtcResveList.do"):
        raise GimpoExperienceContractError("general-experience form action changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[type=hidden][name]")
    }
    if hidden.get("key") != "113" or hidden.get("etcProgramSection") != "EXPERIENCE":
        raise GimpoExperienceContractError("general-experience form ownership changed")

    rows: list[dict[str, Any]] = []
    for item in soup.select("li.participation_item"):
        link = item.select_one("a[href*='webEtcResveView.do'][href*='searchEtcResveNo=']")
        label = item.select_one(".participation_label")
        if link is None or label is None:
            raise GimpoExperienceContractError("malformed general-experience list row")
        parsed = urlparse(urljoin(GIMPO_EXPERIENCE_URL, _clean(link.get("href"))))
        identity = (_query_values(parsed).get("searchEtcResveNo") or [""])[0]
        if not _GENERIC_ID_RE.fullmatch(identity):
            raise GimpoExperienceContractError("invalid general-experience identity")
        source_status = _clean(label.get_text(" ", strip=True))
        status = _GENERIC_STATUS.get(source_status)
        if status is None:
            raise GimpoExperienceContractError(
                f"unknown general-experience status {source_status!r}"
            )
        title = _clean(link.get_text(" ", strip=True))
        info = _information(item, ".participation_information_subject")
        apply_range = _date_range(info.get("신청", ""))
        event_range = _date_range(info.get("행사", ""))
        row = _base_row(
            provider=GIMPO_EXPERIENCE_PROVIDER,
            identity=identity,
            title=title,
            raw_url=gimpo_experience_detail_url(identity),
            source_url=GIMPO_EXPERIENCE_URL,
            branch=GIMPO_EXPERIENCE_BRANCH,
            source_status=source_status,
            status=status,
            apply_range=apply_range,
            event_range=event_range,
            owner="experience",
        )
        row["target_audience"] = _clean(info.get("대상"))
        row["venue_address"] = _clean(info.get("장소"))
        row["raw_fields"]["list_has_public_venue"] = bool(row["venue_address"])
        rows.append(row)
    return rows


def _leisure_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    if "견학/체험" not in _clean(soup.get_text(" ", strip=True)):
        raise GimpoExperienceContractError("leisure page left the official experience menu")
    rows: list[dict[str, Any]] = []
    for item in soup.select("li.participation_item"):
        link = item.select_one("a[href*='viewTnLesureResveU.do'][href*='srvcNo=']")
        label = item.select_one(".participation_label")
        if link is None or label is None:
            raise GimpoExperienceContractError("malformed leisure list row")
        parsed = urlparse(urljoin(GIMPO_LEISURE_URL, _clean(link.get("href"))))
        identity = (_query_values(parsed).get("srvcNo") or [""])[0]
        if not _GENERIC_ID_RE.fullmatch(identity):
            raise GimpoExperienceContractError("invalid leisure identity")
        source_status = _clean(label.get_text(" ", strip=True))
        status = _LEISURE_STATUS.get(source_status)
        if status is None:
            raise GimpoExperienceContractError(f"unknown leisure status {source_status!r}")
        title = _clean(link.get_text(" ", strip=True))
        info = _information(item, ".participation_information_subject")
        row = _base_row(
            provider=GIMPO_LEISURE_PROVIDER,
            identity=identity,
            title=title,
            raw_url=gimpo_leisure_detail_url(identity),
            source_url=GIMPO_LEISURE_URL,
            branch=GIMPO_LEISURE_BRANCH,
            source_status=source_status,
            status=status,
            apply_range=_date_range(info.get("신청기간", "")),
            event_range=_date_range(info.get("방문기간", "")),
            owner="leisure",
        )
        row["venue_name"] = GIMPO_LEISURE_BRANCH
        rows.append(row)
    return rows


def _detail_information(soup: BeautifulSoup) -> dict[str, str]:
    return _information(soup, ".participation_info_subject")


def _discover_application_url(
    soup: BeautifulSoup, *, path_token: str, base_url: str
) -> str:
    candidates = {
        urljoin(base_url, _clean(link.get("href")))
        for link in soup.select("a[href]")
        if path_token in _clean(link.get("href"))
    }
    if len(candidates) > 1:
        raise GimpoExperienceContractError("multiple application URLs discovered")
    return next(iter(candidates), "")


def _experience_detail(soup: BeautifulSoup, row: dict[str, Any]) -> None:
    root = soup.select_one(".participation.view .bbs__view")
    heading = root.select_one("h3.h0") if root is not None else None
    if root is None or heading is None:
        raise GimpoExperienceContractError("missing general-experience detail contract")
    if _clean(heading.get_text(" ", strip=True)) != _clean(row.get("title")):
        raise GimpoExperienceContractError("general-experience detail title mismatch")
    info = _detail_information(soup)
    if _date_range(info.get("운영기간", "")) != (
        row["start_date"], row["end_date"]
    ):
        raise GimpoExperienceContractError("general-experience event dates changed")
    if _date_range(info.get("신청기간", "")) != (
        row["registration_start_date"], row["registration_end_date"]
    ):
        raise GimpoExperienceContractError("general-experience application dates changed")
    branch = _clean(info.get("운영기관"))
    if branch:
        row["branch"] = branch
    audience = _clean(info.get("대상"))
    if audience:
        row["target_audience"] = audience
    address = _address_from_detail(soup)
    if address:
        row["venue_address"] = address
    row["schedule"] = _clean(info.get("운영요일"))
    row["application_url"] = _discover_application_url(
        soup,
        path_token="webEtcResveApplcntAgree.do",
        base_url=GIMPO_EXPERIENCE_URL,
    )
    row["raw_fields"]["detail_public_contract"] = True


def _heading_without_badges(heading: Tag) -> str:
    clone = BeautifulSoup(str(heading), "lxml").select_one("h3")
    if clone is None:
        return ""
    for badge in clone.select(".p-badge"):
        badge.decompose()
    return _clean(clone.get_text(" ", strip=True))


def _leisure_detail(soup: BeautifulSoup, row: dict[str, Any]) -> None:
    root = soup.select_one(".participation.view .bbs__view")
    heading = root.select_one("h3.h0") if root is not None else None
    if root is None or heading is None:
        raise GimpoExperienceContractError("missing leisure detail contract")
    if _heading_without_badges(heading) != _clean(row.get("title")):
        raise GimpoExperienceContractError("leisure detail title mismatch")
    info = _detail_information(soup)
    if _date_range(info.get("방문기간", "")) != (
        row["start_date"], row["end_date"]
    ):
        raise GimpoExperienceContractError("leisure event dates changed")
    if _date_range(info.get("신청기간", "")) != (
        row["registration_start_date"], row["registration_end_date"]
    ):
        raise GimpoExperienceContractError("leisure application dates changed")
    row["schedule"] = _clean(
        " ".join(filter(None, (info.get("방문요일"), info.get("방문시간"))))
    )
    row["fee"] = _clean(info.get("이용요금"))
    address = _address_from_detail(soup)
    if address:
        row["venue_address"] = address
    row["application_url"] = _discover_application_url(
        soup,
        path_token="step0TnLesureApplcntViewU.do",
        base_url=GIMPO_LEISURE_URL,
    )
    row["raw_fields"]["detail_public_contract"] = True


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "application_endpoints_called": 0,
        "configured_collection_error": message,
    }


def _collect_owner(
    target: Any,
    *,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    session_factory: SessionFactory,
    today: Optional[date | datetime | str],
    dedupe_rows: Optional[DedupeRows],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    provider = _provider(target)
    leisure = provider == GIMPO_LEISURE_PROVIDER
    cutoff = _today(today)
    allowed_pages = _positive(max_pages, "max_pages")
    allowed_details = _positive(detail_limit, "detail_limit")
    page_url = gimpo_leisure_list_url if leisure else gimpo_experience_list_url
    page_parser = _leisure_page if leisure else _experience_page
    detail_parser = _leisure_detail if leisure else _experience_detail
    canonical_url = GIMPO_LEISURE_URL if leisure else GIMPO_EXPERIENCE_URL

    errors: list[str] = []
    source_cap_reached = False
    page_rows: dict[int, list[dict[str, Any]]] = {}
    source_total = 0
    data_pages = 0
    sentinel_rows: list[dict[str, Any]] = []
    stable_first = False
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    physical_requests = 0
    sessions_created = 0

    try:
        with _Runner(session_factory, timeout) as runner:
            first_soup = runner.soup(canonical_url)
            source_total = _source_total(first_soup, leisure=leisure)
            data_pages = max(1, math.ceil(source_total / GIMPO_EXPERIENCE_PAGE_SIZE))
            required_list_requests = data_pages + 2
            if required_list_requests > allowed_pages:
                source_cap_reached = True
                raise GimpoExperienceContractError(
                    f"max_pages cap allows {allowed_pages} of "
                    f"{required_list_requests} required list requests"
                )
            page_rows[1] = page_parser(first_soup)
            for page in range(2, data_pages + 1):
                page_rows[page] = page_parser(runner.soup(page_url(page)))

            sentinel_rows = page_parser(runner.soup(page_url(data_pages + 1)))
            verify_rows = page_parser(runner.soup(canonical_url))
            stable_first = [r["provider_course_id"] for r in verify_rows] == [
                r["provider_course_id"] for r in page_rows[1]
            ]
            if not stable_first:
                raise GimpoExperienceContractError("first list page changed during snapshot")

            rows = [row for page in range(1, data_pages + 1) for row in page_rows[page]]
            expected_counts = {
                page: min(
                    GIMPO_EXPERIENCE_PAGE_SIZE,
                    max(0, source_total - (page - 1) * GIMPO_EXPERIENCE_PAGE_SIZE),
                )
                for page in range(1, data_pages + 1)
            }
            for page, expected in expected_counts.items():
                if len(page_rows[page]) != expected:
                    raise GimpoExperienceContractError(
                        f"page {page} expected {expected} rows, got {len(page_rows[page])}"
                    )
            if len(rows) != source_total:
                raise GimpoExperienceContractError(
                    f"declared total {source_total} != parsed rows {len(rows)}"
                )

            identities = [row["provider_course_id"] for row in rows]
            if len(identities) != len(set(identities)):
                raise GimpoExperienceContractError("duplicate source identities")
            source_identity_set = set(identities)
            sentinel_new = {
                row["provider_course_id"] for row in sentinel_rows
            } - source_identity_set
            if sentinel_new:
                raise GimpoExperienceContractError("sentinel exposed new source identities")
            sentinel_identities = [
                row["provider_course_id"] for row in sentinel_rows
            ]
            if leisure:
                if sentinel_identities != [
                    row["provider_course_id"] for row in page_rows[1]
                ]:
                    raise GimpoExperienceContractError(
                        "leisure sentinel no longer clamps exactly to page 1"
                    )
            elif sentinel_identities:
                raise GimpoExperienceContractError(
                    "general-experience sentinel is no longer empty"
                )

            explicit_non_program = [
                row for row in rows if row["raw_fields"]["explicit_non_program"]
            ]
            current_rows = [
                row
                for row in rows
                if not row["raw_fields"]["explicit_non_program"]
                and date.fromisoformat(row["end_date"]) >= cutoff
            ]
            if len(current_rows) > allowed_details:
                source_cap_reached = True
                raise GimpoExperienceContractError(
                    f"detail_limit cap allows {allowed_details} of "
                    f"{len(current_rows)} required current/future details"
                )

            for row in current_rows:
                detail_attempts += 1
                try:
                    detail_soup = runner.soup(
                        row["raw_url"], referer=canonical_url
                    )
                    detail_parser(detail_soup, row)
                    detail_pages += 1
                except Exception:
                    detail_errors += 1
                    raise

            result = list((dedupe_rows or _dedupe_default)(current_rows))
            if len(result) != len(current_rows):
                raise GimpoExperienceContractError(
                    "dedupe changed a complete current/future snapshot"
                )

            physical_requests = runner.requests
            sessions_created = runner.sessions_created
            meta = {
                "pages": data_pages + 1,
                "list_requests": required_list_requests,
                "physical_requests": physical_requests,
                "sessions_created": sessions_created,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "detail_errors": detail_errors,
                "source_total": source_total,
                "source_rows": len(rows),
                "data_pages": data_pages,
                "required_list_requests": required_list_requests,
                "sentinel_page": data_pages + 1,
                "sentinel_raw_rows": len(sentinel_rows),
                "sentinel_new_rows": len(sentinel_new),
                "stable_first_page": stable_first,
                "page_counts": {page: len(value) for page, value in page_rows.items()},
                "expired_count": len(rows) - len(current_rows) - len(explicit_non_program),
                "explicit_non_program_count": len(explicit_non_program),
                "notice_count": sum(
                    row["raw_fields"]["non_program_reason"] == "notice"
                    for row in explicit_non_program
                ),
                "test_count": sum(
                    row["raw_fields"]["non_program_reason"] == "test"
                    for row in explicit_non_program
                ),
                "current_count": len(current_rows),
                "returned_count": len(result),
                "branch_count": len({_clean(row.get("branch")) for row in result}),
                "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "pagination_complete": True,
                "details_complete": detail_pages == len(current_rows),
                "snapshot_complete": True,
                "source_cap_reached": False,
                "no_current_data": not current_rows,
                "application_endpoints_called": 0,
                "configured_collection_error": "",
                "ownership_scope": (
                    GIMPO_LEISURE_OWNERSHIP_SCOPE if leisure else GIMPO_OWNERSHIP_SCOPE
                ),
                "municipality_code": GIMPO_MUNICIPALITY_CODE,
            }
            return result, GIMPO_PARSER, meta
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {_redact(exc)}")
        meta = _failure("; ".join(errors))
        meta.update(
            {
                "source_total": source_total,
                "data_pages": data_pages,
                "pages": len(page_rows) + (1 if sentinel_rows else 0),
                "list_requests": len(page_rows) + (1 if sentinel_rows else 0),
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "detail_errors": detail_errors,
                "source_cap_reached": source_cap_reached,
                "stable_first_page": stable_first,
                "physical_requests": physical_requests,
                "sessions_created": sessions_created,
                "ownership_scope": (
                    GIMPO_LEISURE_OWNERSHIP_SCOPE if leisure else GIMPO_OWNERSHIP_SCOPE
                ),
                "municipality_code": GIMPO_MUNICIPALITY_CODE,
            }
        )
        return [], GIMPO_PARSER, meta


def collect_gimpo_experience_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = GIMPO_DEFAULT_MAX_PAGES,
    detail_limit: int = GIMPO_DEFAULT_DETAIL_LIMIT,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if not is_gimpo_experience_target(target):
        return [], GIMPO_PARSER, _failure(
            "target does not match an audited Gimpo official experience owner"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GIMPO_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory
    try:
        return _collect_owner(
            target,
            timeout=_positive(timeout, "timeout"),
            max_pages=max_pages,
            detail_limit=detail_limit,
            session_factory=session_factory,
            today=today,
            dedupe_rows=dedupe_rows,
        )
    except Exception as exc:
        return [], GIMPO_PARSER, _failure(f"{type(exc).__name__}: {_redact(exc)}")


collect = collect_gimpo_experience_courses


__all__ = [
    "GIMPO_EXPERIENCE_APPLICATION_ENDPOINT",
    "GIMPO_EXPERIENCE_BRANCH",
    "GIMPO_EXPERIENCE_CANDIDATE_ID",
    "GIMPO_EXPERIENCE_DETAIL_ENDPOINT",
    "GIMPO_EXPERIENCE_LIST_ENDPOINT",
    "GIMPO_EXPERIENCE_PROVIDER",
    "GIMPO_EXPERIENCE_URL",
    "GIMPO_LEISURE_APPLICATION_ENDPOINT",
    "GIMPO_LEISURE_BRANCH",
    "GIMPO_LEISURE_CANDIDATE_ID",
    "GIMPO_LEISURE_DETAIL_ENDPOINT",
    "GIMPO_LEISURE_LIST_ENDPOINT",
    "GIMPO_LEISURE_PROVIDER",
    "GIMPO_LEISURE_URL",
    "GIMPO_LIVE_AUDIT_BASELINE",
    "GIMPO_MUNICIPALITY_CODE",
    "GIMPO_MUNICIPALITY_NAME",
    "GIMPO_OWNERSHIP_SCOPE",
    "GIMPO_LEISURE_OWNERSHIP_SCOPE",
    "GIMPO_PARSER",
    "GimpoExperienceContractError",
    "collect_gimpo_experience_courses",
    "gimpo_experience_detail_url",
    "gimpo_experience_list_url",
    "gimpo_leisure_detail_url",
    "gimpo_leisure_list_url",
    "is_gimpo_experience_target",
]
