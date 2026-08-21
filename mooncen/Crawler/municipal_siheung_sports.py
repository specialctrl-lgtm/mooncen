"""Fail-closed collector for Siheung Urban Corporation FMCS courses.

The public FMCS site has two catalogue tabs whose backend meanings are easy to
confuse.  ``search_type=R`` is the site's current/new-registration catalogue;
the separate ``E`` tab is an ended/template ledger and is not merged here.

The collector verifies the exact eight-company owner boundary, reads every
declared page for each company, requires an immediate empty sentinel, rechecks
the first/final page boundaries, and validates every current course detail.
It never submits the application form and never visits login, member, file, or
resident-verification endpoints.  Detail contact values are used only to
verify the official company name and are not persisted.

This module intentionally has no dependency on the shared municipal router so
that the router can import it without a cycle.  Production callers must inject
their managed session factory.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


SIHEUNG_SPORTS_PROVIDER = "MUNI_SPORTSAPP_SHSI_OR_KR_6239E7D6"
SIHEUNG_SPORTS_URL = "https://sportsapp.shsi.or.kr/fmcs/3"
SIHEUNG_SPORTS_HOST = "sportsapp.shsi.or.kr"
SIHEUNG_SPORTS_PATH = "/fmcs/3"
SIHEUNG_SPORTS_COMPANY_API = "https://sportsapp.shsi.or.kr/rest/common/company"
SIHEUNG_SPORTS_LECTURE_API = "https://sportsapp.shsi.or.kr/rest/lecture/list"
SIHEUNG_SPORTS_APPLICATION_URL = "https://sportsapp.shsi.or.kr/fmcs/3?action=write"
SIHEUNG_SPORTS_LOGIN_PATH = "/fmcs/21"

SIHEUNG_SPORTS_PAGE_SIZE = 50
SIHEUNG_SPORTS_SEARCH_TYPE = "R"
SIHEUNG_SPORTS_DEFAULT_MAX_PAGES = 32
SIHEUNG_SPORTS_DEFAULT_DETAIL_LIMIT = 300
SIHEUNG_SPORTS_DEFAULT_MAX_REQUESTS = 400
SIHEUNG_SPORTS_SESSION_REQUEST_LIMIT = 90
SIHEUNG_SPORTS_PARSER = (
    "siheung_urban_corporation_fmcs_current_new_registration_all_companies+"
    "declared_pages+empty_sentinels+stable_edges+all_details+pii_minimized"
)

SIHEUNG_MUNICIPALITY_CODE = "4139000000"
SIHEUNG_MUNICIPALITY_NAME = "경기도 시흥시"
SIHEUNG_SPORTS_OWNER_NAME = "시흥도시공사"
SIHEUNG_SSOC_SEPARATE_OWNER_URL = "https://siheung.gseek.kr/user/course/offline/list"

# The API sequence and names are the official branch ledger.  A change is an
# ownership/branch migration that must be reviewed rather than guessed.
SIHEUNG_SPORTS_COMPANIES: tuple[tuple[str, str], ...] = (
    ("SIHEUNG01", "[하중]시흥국민체육센터"),
    ("SIHEUNG02", "정왕평생학습관"),
    ("SIHEUNG07", "시흥능곡어울림센터"),
    ("SIHEUNG08", "[정왕]시흥어울림국민체육센터"),
    ("SIHEUNG09", "장곡동생활체육시설"),
    ("SIHEUNG11", "다니생활체육관"),
    ("SIHEUNG12", "장곡문화체육센터"),
    ("SIHEUNG14", "목감2어울림센터"),
)
SIHEUNG_SPORTS_COMPANY_NAMES = dict(SIHEUNG_SPORTS_COMPANIES)

# Dated audit evidence only; pagination completeness is derived from each live
# response's total_count and sentinel rather than frozen to these values.
SIHEUNG_SPORTS_AUDITED_AT = "2026-07-23"
SIHEUNG_SPORTS_AUDITED_CURRENT_TOTAL = 241
SIHEUNG_SPORTS_AUDITED_COMPANY_TOTALS: Mapping[str, int] = {
    "SIHEUNG01": 75,
    "SIHEUNG02": 0,
    "SIHEUNG07": 42,
    "SIHEUNG08": 44,
    "SIHEUNG09": 13,
    "SIHEUNG11": 2,
    "SIHEUNG12": 30,
    "SIHEUNG14": 35,
}

_STATUS_MAP: Mapping[str, str] = {"R": "OPEN", "E": "CLOSED"}
_REQUIRED_ITEM_FIELDS = frozenset(
    {
        "comcd",
        "comnm",
        "class_cd",
        "class_nm",
        "train_stime",
        "train_etime",
        "course_fee",
        "receive_etime",
        "status",
        "receive_kind",
        "target_age_name",
        "sports_cd",
        "train_day_nm",
        "capa",
        "reg_person",
        "teacher_name",
        "total_count",
        "category1",
        "category2",
    }
)
_SPACE_RE = re.compile(r"\s+")
_CLASS_CODE_RE = re.compile(r"\d{5}")
_TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_TEST_TITLE_RE = re.compile(r"(?:^|\W)(?:test|dummy)(?:\W|$)|테스트|연습용|샘플", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,2})[- )]\d{3,4}[- ]\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]


class SiheungSportsContractError(RuntimeError):
    """Raised when the public FMCS contract is no longer the audited one."""


@dataclass(frozen=True)
class _CompanyBoundary:
    code: str
    name: str
    total: int
    data_pages: int
    sentinel_page: int


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\s\u200b]+", "", _clean(value)).casefold()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _positive_int(value: Any, label: str) -> int:
    raw = _clean(value)
    if not raw.isdigit() or int(raw) < 1:
        raise SiheungSportsContractError(f"{label} must be a positive integer")
    return int(raw)


def _integer(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    if not re.fullmatch(r"\d+", raw):
        return None
    return int(raw)


def _contains_pii(value: Any) -> bool:
    text = _clean(value)
    return bool(_PHONE_RE.search(text) or _EMAIL_RE.search(text))


def is_siheung_sports_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == SIHEUNG_SPORTS_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.hostname is not None
        and parsed.hostname.rstrip(".").lower() == SIHEUNG_SPORTS_HOST
        and parsed.netloc.lower() == SIHEUNG_SPORTS_HOST
        and parsed.path == SIHEUNG_SPORTS_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_siheung_sports_target


def siheung_sports_detail_url(company_code: Any, class_code: Any) -> str:
    company = _clean(company_code)
    identity = _clean(class_code)
    if company not in SIHEUNG_SPORTS_COMPANY_NAMES or not _CLASS_CODE_RE.fullmatch(identity):
        return ""
    return f"{SIHEUNG_SPORTS_URL}?" + urlencode(
        (("action", "read"), ("comcd", company), ("classcd", identity), ("type", SIHEUNG_SPORTS_SEARCH_TYPE))
    )


def _safe_detail_url(value: Any, company_code: str, class_code: str) -> bool:
    parsed = urlparse(_clean(value))
    return (
        parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == SIHEUNG_SPORTS_HOST
        and parsed.path == SIHEUNG_SPORTS_PATH
        and not parsed.params
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True)
        == {
            "action": ["read"],
            "comcd": [company_code],
            "classcd": [class_code],
            "type": [SIHEUNG_SPORTS_SEARCH_TYPE],
        }
    )


def siheung_sports_list_payload(company_code: Any, page: Any) -> dict[str, str]:
    company = _clean(company_code)
    raw_page = _clean(page)
    if company not in SIHEUNG_SPORTS_COMPANY_NAMES or not raw_page.isdigit() or int(raw_page) < 1:
        return {}
    return {
        "company_code": company,
        "mem_no": "",
        "search_type": SIHEUNG_SPORTS_SEARCH_TYPE,
        "category_cd": "",
        "category_level": "9",
        "class_nm": "",
        "train_day": "",
        "adult_gubn": "",
        "lecturer_nm": "",
        "page": str(int(raw_page)),
        "page_size": str(SIHEUNG_SPORTS_PAGE_SIZE),
    }


def _payload_signature(payload: list[Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _identity_sha(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [_clean(row.get("provider_course_id")) for row in rows]
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _validate_response(response: Any, expected_url: str) -> None:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise SiheungSportsContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise SiheungSportsContractError("HTTP redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != expected_url:
        raise SiheungSportsContractError("response URL escaped the audited endpoint")


def _response_soup(response: Any, expected_url: str) -> BeautifulSoup:
    _validate_response(response, expected_url)
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise SiheungSportsContractError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _response_json(response: Any, expected_url: str) -> list[Any]:
    _validate_response(response, expected_url)
    payload = response.json()
    if not isinstance(payload, list):
        raise SiheungSportsContractError("FMCS endpoint did not return a JSON list")
    return payload


class _Runner:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        timeout: int,
        max_requests: int,
        sleeper: Sleeper,
    ) -> None:
        self._session_factory = session_factory
        self._timeout = timeout
        self._max_requests = max_requests
        self._sleeper = sleeper
        self._session: Any = None
        self._session_requests = SIHEUNG_SPORTS_SESSION_REQUEST_LIMIT
        self.physical_requests = 0
        self.retry_count = 0
        self.sessions_created = 0
        self.method_counts: Counter[str] = Counter()

    def close(self) -> None:
        _close_quietly(self._session)
        self._session = None

    def _new_session(self) -> None:
        self.close()
        self._session = self._session_factory()
        self.sessions_created += 1
        self._session_requests = 0
        headers = getattr(self._session, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                    "Referer": SIHEUNG_SPORTS_URL,
                }
            )

    def _ensure_session(self) -> None:
        if self._session is None or self._session_requests >= SIHEUNG_SPORTS_SESSION_REQUEST_LIMIT:
            self._new_session()

    def _attempt(self, method: str, operation: Callable[[Any], Any]) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(2):
            if self.physical_requests >= self._max_requests:
                raise SiheungSportsContractError(
                    f"max_requests cap {self._max_requests} exhausted"
                )
            self._ensure_session()
            self._session_requests += 1
            self.physical_requests += 1
            self.method_counts[method] += 1
            try:
                return operation(self._session)
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    self.retry_count += 1
                    self._new_session()
                    self._sleeper(0.1)
        assert last_error is not None
        raise last_error

    def get_soup(self, url: str) -> BeautifulSoup:
        return self._attempt(
            "GET",
            lambda session: _response_soup(
                session.get(url, timeout=self._timeout, allow_redirects=False),
                url,
            ),
        )

    def post_companies(self) -> list[Any]:
        return self._attempt(
            "POST",
            lambda session: _response_json(
                session.post(
                    SIHEUNG_SPORTS_COMPANY_API,
                    data={"type": "L"},
                    timeout=self._timeout,
                    allow_redirects=False,
                    headers={
                        "Referer": SIHEUNG_SPORTS_URL,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                ),
                SIHEUNG_SPORTS_COMPANY_API,
            ),
        )

    def post_lectures(self, company_code: str, page: int) -> list[Any]:
        data = siheung_sports_list_payload(company_code, page)
        if not data:
            raise SiheungSportsContractError("unsafe FMCS list payload")
        return self._attempt(
            "POST",
            lambda session: _response_json(
                session.post(
                    SIHEUNG_SPORTS_LECTURE_API,
                    data=data,
                    timeout=self._timeout,
                    allow_redirects=False,
                    headers={
                        "Referer": SIHEUNG_SPORTS_URL,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                ),
                SIHEUNG_SPORTS_LECTURE_API,
            ),
        )


def _landing_errors(soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "수강신청(교육/강좌 목록)" not in title or SIHEUNG_SPORTS_OWNER_NAME not in title:
        errors.append("landing page title is not the Siheung Urban Corporation course catalogue")
    if soup.select_one(".modules_fmcs_lecture .proc_list") is None:
        errors.append("landing page lost the FMCS lecture-list container")
    script_text = "\n".join(script.get_text(" ", strip=True) for script in soup.select("script"))
    compact_script = re.sub(r"\s+", "", script_text)
    for marker in (
        "varIS_SHOW_ALL=true",
        "varCOMPANY_CODE=''",
        "varREAD_LINK_URL='?action=read'",
    ):
        if marker not in compact_script:
            errors.append(f"landing page lost script contract {marker}")
    form = soup.select_one('.proc_list form#search input[name="lecture_type"]')
    if form is None:
        errors.append("landing page lost the official lecture scope control")
    tab_values = {
        _clean(link.get("data-value"))
        for link in soup.select(".proc_list .list_tab a[data-value]")
    }
    if not {"R", "E"}.issubset(tab_values):
        errors.append("landing page no longer separates current and ended catalogue tabs")
    return errors


def _company_contract(payload: list[Any]) -> tuple[list[tuple[str, str]], list[str]]:
    rows: list[tuple[str, str]] = []
    errors: list[str] = []
    for index, item in enumerate(payload, 1):
        if not isinstance(item, Mapping):
            errors.append(f"company row {index} is not an object")
            continue
        code = _clean(item.get("comcd"))
        name = _clean(item.get("comnm"))
        if not code or not name:
            errors.append(f"company row {index} lacks code/name")
            continue
        rows.append((code, name))
    if tuple(rows) != SIHEUNG_SPORTS_COMPANIES:
        errors.append("official eight-company code/name/order contract changed")
    if len({code for code, _ in rows}) != len(rows):
        errors.append("company API exposed duplicate codes")
    return rows, errors


def _row_error_prefix(item: Mapping[str, Any]) -> str:
    company = _clean(item.get("comcd")) or "?"
    identity = _clean(item.get("class_cd")) or "?"
    return f"{company}/{identity}"


def _validate_item(
    item: Any,
    *,
    company_code: str,
    company_name: str,
    declared_total: int,
) -> tuple[Optional[Mapping[str, Any]], list[str]]:
    if not isinstance(item, Mapping):
        return None, [f"{company_code}: non-object lecture row"]
    errors: list[str] = []
    prefix = _row_error_prefix(item)
    missing = sorted(field for field in _REQUIRED_ITEM_FIELDS if field not in item)
    if missing:
        errors.append(f"{prefix}: missing fields {','.join(missing)}")
    if _clean(item.get("comcd")) != company_code:
        errors.append(f"{prefix}: company code escaped its partition")
    if _clean(item.get("comnm")) != company_name:
        errors.append(f"{prefix}: official company name changed")
    identity = _clean(item.get("class_cd"))
    if not _CLASS_CODE_RE.fullmatch(identity):
        errors.append(f"{prefix}: malformed class code")
    title = _clean(item.get("class_nm"))
    if not title:
        errors.append(f"{prefix}: empty title")
    elif _TEST_TITLE_RE.search(title):
        errors.append(f"{prefix}: unaudited test/sample course title")
    if _clean(item.get("status")) not in _STATUS_MAP:
        errors.append(f"{prefix}: status left the current R/E contract")
    if _clean(item.get("receive_kind")) != "10":
        errors.append(f"{prefix}: receive_kind left the audited online contract")
    if _integer(item.get("total_count")) != declared_total:
        errors.append(f"{prefix}: total_count changed inside a partition")
    for field in (
        "course_fee",
        "target_age_name",
        "sports_cd",
        "train_day_nm",
        "teacher_name",
        "category1",
        "category2",
    ):
        if not _clean(item.get(field)):
            errors.append(f"{prefix}: empty {field}")
    for field in ("class_nm", "target_age_name", "teacher_name", "category1", "category2"):
        if _contains_pii(item.get(field)):
            errors.append(f"{prefix}: public list field {field} unexpectedly contains PII")
    for field in ("train_stime", "train_etime", "receive_etime"):
        if not _TIME_RE.fullmatch(_clean(item.get(field))):
            errors.append(f"{prefix}: malformed {field}")
    capacity = _integer(item.get("capa"))
    registered = _integer(item.get("reg_person"))
    if capacity is None or registered is None:
        errors.append(f"{prefix}: malformed capacity")
    elif registered > capacity:
        errors.append(f"{prefix}: registered count exceeds capacity")
    return (None if errors else item), errors


def _stable_branch_code(provider: str, company_code: str) -> str:
    digest = hashlib.sha1(f"{provider}|{company_code}".encode("utf-8")).hexdigest()[:12].upper()
    return f"SIHEUNG_SPORTS_{digest}"


def _build_row(target: Any, item: Mapping[str, Any]) -> dict[str, Any]:
    provider = _provider(target)
    company = _clean(item.get("comcd"))
    identity = _clean(item.get("class_cd"))
    status_code = _clean(item.get("status"))
    category1 = _clean(item.get("category1"))
    category2 = _clean(item.get("category2"))
    capacity = _integer(item.get("capa"))
    registered = _integer(item.get("reg_person"))
    detail_url = siheung_sports_detail_url(company, identity)
    row: dict[str, Any] = {
        "provider": provider,
        "provider_course_id": f"{provider}:class:{company}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(item.get("class_nm")),
        "branch": SIHEUNG_SPORTS_COMPANY_NAMES[company],
        "branch_code": _stable_branch_code(provider, company),
        "preserve_branch": True,
        "branch_url": SIHEUNG_SPORTS_URL,
        "raw_url": detail_url,
        "status": _STATUS_MAP[status_code],
        "reservation_available": status_code == "R",
        "schedule": _clean(
            f"{item.get('train_day_nm')} {_clean(item.get('train_stime'))} ~ {_clean(item.get('train_etime'))}"
        ),
        "schedule_raw": _clean(
            f"{item.get('train_day_nm')} {_clean(item.get('train_stime'))} ~ {_clean(item.get('train_etime'))}"
        ),
        "target": _clean(item.get("target_age_name")),
        "instructor": _clean(item.get("teacher_name")),
        "fee": _clean(item.get("course_fee")),
        "capacity": capacity,
        "capacity_total": capacity,
        "capacity_current": registered,
        "category": f"{category1} > {category2}",
        "program_type": "강좌",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "collection_type": (
            "current_new_registration_company_pages+sentinels+stable_edges+all_details"
        ),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "region": SIHEUNG_MUNICIPALITY_NAME,
        "municipality_code": SIHEUNG_MUNICIPALITY_CODE,
        "municipality_full_name": SIHEUNG_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": SIHEUNG_SPORTS_PARSER,
            "official_company_code": company,
            "official_class_code": identity,
            "source_status": status_code,
            "source_scope": SIHEUNG_SPORTS_SEARCH_TYPE,
            "source_scope_label": "신규접수",
            "sports_code": _clean(item.get("sports_cd")),
            "receive_kind": _clean(item.get("receive_kind")),
            "receive_end_time": _clean(item.get("receive_etime")),
            "list_train_day": _clean(item.get("train_day_nm")),
            "list_start_time": _clean(item.get("train_stime")),
            "list_end_time": _clean(item.get("train_etime")),
            "list_capacity_total": capacity,
            "list_capacity_current": registered,
            "category1": category1,
            "category2": category2,
            "clear_application_url": status_code != "R",
        },
    }
    if status_code == "R":
        # The public detail URL is safe discovery evidence.  The actual write
        # form and login redirect are deliberately never called.
        row["application_url"] = detail_url
        row["application_type"] = "ONLINE_RESERVATION"
    return row


def _detail_pairs(soup: BeautifulSoup) -> tuple[dict[str, str], list[str]]:
    pairs: dict[str, str] = {}
    errors: list[str] = []
    for tr in soup.select(".modules_fmcs_lecture .proc_read table tr, .proc_read table tr"):
        heading = tr.find("th")
        value = tr.find("td")
        if heading is None or value is None:
            continue
        key = _clean(heading.get_text(" ", strip=True))
        text = _clean(value.get_text(" ", strip=True))
        if not key:
            continue
        if key in pairs and pairs[key] != text:
            errors.append(f"detail exposed conflicting {key} fields")
        else:
            pairs[key] = text
    return pairs, errors


def _detail_contract(row: dict[str, Any], soup: BeautifulSoup) -> tuple[list[str], bool, bool]:
    raw = row.get("raw_fields") if isinstance(row.get("raw_fields"), dict) else {}
    company = _clean(raw.get("official_company_code"))
    identity = _clean(raw.get("official_class_code"))
    status_code = _clean(raw.get("source_status"))
    prefix = f"{company}/{identity}"
    errors: list[str] = []
    container = soup.select_one(".modules_fmcs_lecture .proc_read, .proc_read")
    if container is None:
        return [f"{prefix}: detail container is missing"], False, False

    for field, expected in (
        ("comcd", company),
        ("classcd", identity),
        ("type", SIHEUNG_SPORTS_SEARCH_TYPE),
    ):
        values = container.select(f'input[name="{field}"]')
        if len(values) != 1 or _clean(values[0].get("value")) != expected:
            errors.append(f"{prefix}: detail hidden {field} mismatch")

    detail_status_inputs = container.select('input[name="status"]')
    detail_status = (
        _clean(detail_status_inputs[0].get("value"))
        if len(detail_status_inputs) == 1
        else ""
    )
    if detail_status not in _STATUS_MAP:
        errors.append(f"{prefix}: detail status left the current R/E contract")

    tokens = container.select('input[name="SecurityToken"]')
    if len(tokens) != 1 or not _clean(tokens[0].get("value")):
        errors.append(f"{prefix}: detail application token contract is missing")

    pairs, pair_errors = _detail_pairs(soup)
    errors.extend(f"{prefix}: {error}" for error in pair_errors)
    required_labels = (
        "강좌명",
        "운영센터",
        "교육장소",
        "시간/요일",
        "교육대상",
        "강사명",
        "접수방식",
        "정원/신청인원",
    )
    missing = [label for label in required_labels if not _clean(pairs.get(label))]
    if missing:
        errors.append(f"{prefix}: detail fields missing {','.join(missing)}")
        return errors, False, False

    if _normalized(pairs["강좌명"]) != _normalized(row.get("title")):
        errors.append(f"{prefix}: detail title mismatch")
    center_text = pairs["운영센터"]
    center_name = _clean(center_text.split("/", 1)[0])
    if _normalized(center_name) != _normalized(row.get("branch")):
        errors.append(f"{prefix}: detail company mismatch")

    schedule = pairs["시간/요일"]
    for expected in (
        _clean(raw.get("list_train_day")),
        _clean(raw.get("list_start_time")),
        _clean(raw.get("list_end_time")),
    ):
        if expected and _normalized(expected) not in _normalized(schedule):
            errors.append(f"{prefix}: detail schedule mismatch")
            break
    if _normalized(pairs["교육대상"]) != _normalized(row.get("target")):
        errors.append(f"{prefix}: detail target mismatch")
    if _normalized(pairs["강사명"]) != _normalized(row.get("instructor")):
        errors.append(f"{prefix}: detail instructor mismatch")

    capacity_values = [
        int(value.replace(",", ""))
        for value in re.findall(r"\d[\d,]*", pairs["정원/신청인원"])
    ]
    if len(capacity_values) < 2 or capacity_values[1] > capacity_values[0]:
        errors.append(f"{prefix}: detail capacity is malformed")

    venue = pairs["교육장소"]
    application_method = pairs["접수방식"]
    if _contains_pii(venue) or _contains_pii(application_method):
        errors.append(f"{prefix}: detail safe fields unexpectedly contain PII")

    forms = [form for form in container.select("form[action]")]
    if not forms and getattr(container, "name", "") == "form" and container.get("action"):
        forms = [container]
    if len(forms) != 1:
        errors.append(f"{prefix}: detail application form contract changed")
    else:
        action_url = urljoin(_clean(row.get("raw_url")), _clean(forms[0].get("action")))
        if action_url != SIHEUNG_SPORTS_APPLICATION_URL:
            errors.append(f"{prefix}: detail application action escaped the audited path")

    controls = [
        button
        for button in container.select("button")
        if _normalized(button.get_text(" ", strip=True)) == _normalized("수강신청")
    ]
    application_control = len(controls) == 1
    if detail_status == "R":
        if not application_control:
            errors.append(f"{prefix}: open course lost its application control")
        else:
            onclick = _clean(controls[0].get("onclick"))
            if SIHEUNG_SPORTS_LOGIN_PATH not in onclick:
                errors.append(f"{prefix}: logged-out application control no longer routes to login")
    elif detail_status == "E" and controls:
        errors.append(f"{prefix}: closed course unexpectedly exposes an application control")

    pii_omitted = any(_contains_pii(value) for value in pairs.values())
    if not errors:
        detail_capacity_total, detail_capacity_current = capacity_values[:2]
        row["venue_name"] = venue
        row["application_method"] = application_method
        row["schedule"] = schedule
        row["schedule_raw"] = schedule
        row["status"] = _STATUS_MAP[detail_status]
        row["capacity"] = detail_capacity_total
        row["capacity_total"] = detail_capacity_total
        row["capacity_current"] = detail_capacity_current
        row["reservation_available"] = detail_status == "R"
        if detail_status == "R":
            row["application_url"] = _clean(row.get("raw_url"))
            row["application_type"] = "ONLINE_RESERVATION"
        else:
            row.pop("application_url", None)
            row.pop("application_type", None)
        row["raw_fields"].update(
            {
                "detail_identity_verified": True,
                "detail_status_verified": True,
                "detail_status": detail_status,
                "detail_status_refreshed": detail_status != status_code,
                "detail_capacity_refreshed": (
                    detail_capacity_total != row.get("raw_fields", {}).get("list_capacity_total")
                    or detail_capacity_current
                    != row.get("raw_fields", {}).get("list_capacity_current")
                ),
                "detail_company_verified": True,
                "detail_application_form_discovered": True,
                "detail_application_control": application_control,
                "detail_contact_pii_omitted": pii_omitted,
                "application_endpoint_called": False,
            }
        )
    return errors, application_control, pii_omitted


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("branch")),
        _normalized(row.get("title")),
        _normalized(row.get("schedule")),
        _normalized(row.get("target")),
    )


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "main_discovery_pages": 0,
        "company_requests": 0,
        "list_requests": 0,
        "physical_requests": 0,
        "retry_count": 0,
        "sessions_created": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "discovered_links": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "pagination_detected": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
        "application_endpoints_called": 0,
        "login_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "pii_endpoints_called": 0,
    }


def collect_siheung_sports_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = SIHEUNG_SPORTS_DEFAULT_MAX_PAGES,
    detail_limit: int = SIHEUNG_SPORTS_DEFAULT_DETAIL_LIMIT,
    *,
    max_requests: int = SIHEUNG_SPORTS_DEFAULT_MAX_REQUESTS,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current Siheung Urban Corporation course snapshot."""

    meta = _base_meta()
    if not is_siheung_sports_target(target):
        meta["configured_collection_error"] = (
            "target does not match the provider-owned canonical Siheung sports route"
        )
        return [], SIHEUNG_SPORTS_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], SIHEUNG_SPORTS_PARSER, meta
        session_factory = _default_session_factory

    try:
        timeout = _positive_int(timeout, "timeout")
        max_pages = _positive_int(max_pages, "max_pages")
        detail_limit = _positive_int(detail_limit, "detail_limit")
        max_requests = _positive_int(max_requests, "max_requests")
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], SIHEUNG_SPORTS_PARSER, meta

    minimum_partition_requests = len(SIHEUNG_SPORTS_COMPANIES)
    if max_pages < minimum_partition_requests:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of at least "
                    f"{minimum_partition_requests} required company pages/sentinels"
                ),
            }
        )
        return [], SIHEUNG_SPORTS_PARSER, meta
    if max_requests < 2 + minimum_partition_requests:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_requests cap allows {max_requests} of at least "
                    f"{2 + minimum_partition_requests} discovery requests"
                ),
            }
        )
        return [], SIHEUNG_SPORTS_PARSER, meta

    runner = _Runner(
        session_factory=session_factory,
        timeout=timeout,
        max_requests=max_requests,
        sleeper=sleeper,
    )
    errors: list[str] = []
    boundaries: dict[str, _CompanyBoundary] = {}
    payloads: dict[tuple[str, int], list[Any]] = {}
    edge_signatures: dict[str, dict[str, str]] = {}
    rows: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []
    source_total = 0
    required_page_requests = 0
    required_edge_rechecks = 0
    required_logical_requests = 0
    stability_rechecks = 0
    sentinel_requests = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    application_controls = 0
    pii_omissions = 0
    duplicate_count = 0
    duplicate_url_count = 0
    semantic_duplicate_count = 0
    malformed_count = 0
    source_cap_reached = False
    landing_loaded = False
    company_loaded = False

    try:
        try:
            landing = runner.get_soup(SIHEUNG_SPORTS_URL)
            landing_loaded = True
            errors.extend(_landing_errors(landing))
        except Exception as exc:
            errors.append(f"landing page: fetch {type(exc).__name__}")

        companies: list[tuple[str, str]] = []
        if not errors:
            try:
                company_payload = runner.post_companies()
                company_loaded = True
                companies, company_errors = _company_contract(company_payload)
                errors.extend(company_errors)
            except Exception as exc:
                errors.append(f"company API: fetch {type(exc).__name__}")

        # Phase one reads page one from every official company.  This exposes
        # all declared totals before any remaining pages or details are read.
        if not errors:
            for company_code, company_name in companies:
                try:
                    payload = runner.post_lectures(company_code, 1)
                    payloads[(company_code, 1)] = payload
                except Exception as exc:
                    errors.append(
                        f"{company_code}: page 1 fetch {type(exc).__name__}"
                    )
                    break
                if not payload:
                    boundaries[company_code] = _CompanyBoundary(
                        company_code, company_name, 0, 0, 1
                    )
                    continue
                totals = {
                    _integer(item.get("total_count"))
                    for item in payload
                    if isinstance(item, Mapping)
                }
                if len(totals) != 1 or None in totals:
                    errors.append(f"{company_code}: page 1 has no stable declared total")
                    continue
                total = next(iter(totals))
                assert total is not None
                if total < 1:
                    errors.append(f"{company_code}: nonempty page declared a zero total")
                    continue
                data_pages = math.ceil(total / SIHEUNG_SPORTS_PAGE_SIZE)
                boundaries[company_code] = _CompanyBoundary(
                    company_code,
                    company_name,
                    total,
                    data_pages,
                    data_pages + 1,
                )

        if not errors and len(boundaries) == len(SIHEUNG_SPORTS_COMPANIES):
            source_total = sum(boundary.total for boundary in boundaries.values())
            required_page_requests = sum(
                boundary.data_pages + 1 for boundary in boundaries.values()
            )
            required_edge_rechecks = sum(
                2 if boundary.data_pages > 1 else 1
                for boundary in boundaries.values()
            )
            required_logical_requests = (
                2 + required_page_requests + required_edge_rechecks + source_total
            )
            if required_page_requests > max_pages:
                source_cap_reached = True
                errors.append(
                    f"max_pages cap allows {max_pages} of {required_page_requests} "
                    "required company data/sentinel requests"
                )
            if source_total > detail_limit:
                source_cap_reached = True
                errors.append(
                    f"detail_limit cap allows {detail_limit} of {source_total} "
                    "required current-course details"
                )
            if required_logical_requests > max_requests:
                source_cap_reached = True
                errors.append(
                    f"max_requests cap allows {max_requests} of "
                    f"{required_logical_requests} required logical requests"
                )
        elif not errors:
            errors.append("company boundary census is incomplete")

        # Phase two fills the declared pages and reads exactly one immediate
        # empty sentinel for each company.  For an empty company page one is
        # itself the sentinel.
        if not errors:
            for company_code, _company_name in companies:
                boundary = boundaries[company_code]
                if boundary.data_pages == 0:
                    sentinel_requests += 1
                    if payloads[(company_code, 1)]:
                        errors.append(f"{company_code}: empty-company sentinel is not empty")
                    continue
                for page in range(2, boundary.sentinel_page + 1):
                    try:
                        payloads[(company_code, page)] = runner.post_lectures(
                            company_code, page
                        )
                    except Exception as exc:
                        errors.append(
                            f"{company_code}: page {page} fetch {type(exc).__name__}"
                        )
                        break
                sentinel_requests += 1
                if payloads.get((company_code, boundary.sentinel_page), [None]):
                    errors.append(
                        f"{company_code}: immediate post-final sentinel is not empty"
                    )

        # Validate every page and build rows only after all sentinels exist.
        if not errors:
            for company_code, company_name in companies:
                boundary = boundaries[company_code]
                exposed = 0
                for page in range(1, boundary.data_pages + 1):
                    payload = payloads.get((company_code, page), [])
                    expected = min(
                        SIHEUNG_SPORTS_PAGE_SIZE,
                        boundary.total - ((page - 1) * SIHEUNG_SPORTS_PAGE_SIZE),
                    )
                    if len(payload) != expected:
                        errors.append(
                            f"{company_code}: page {page} exposed {len(payload)}; "
                            f"expected {expected}"
                        )
                    exposed += len(payload)
                    for item in payload:
                        valid_item, item_errors = _validate_item(
                            item,
                            company_code=company_code,
                            company_name=company_name,
                            declared_total=boundary.total,
                        )
                        malformed_count += len(item_errors)
                        errors.extend(item_errors)
                        if valid_item is not None:
                            rows.append(_build_row(target, valid_item))
                if exposed != boundary.total:
                    errors.append(
                        f"{company_code}: declared {boundary.total} rows but exposed {exposed}"
                    )
            if len(rows) != source_total:
                errors.append(
                    f"declared total {source_total} does not match {len(rows)} parsed rows"
                )

        # Recheck stable first/final page identities before details can change
        # the elapsed-time window.  Empty companies recheck their page-one
        # sentinel as their only boundary.
        if not errors:
            for company_code, _company_name in companies:
                boundary = boundaries[company_code]
                edge_pages = [1]
                if boundary.data_pages > 1:
                    edge_pages.append(boundary.data_pages)
                edge_signatures[company_code] = {}
                for index, page in enumerate(edge_pages):
                    original = payloads[(company_code, page)]
                    label = "first" if index == 0 else "last"
                    signature = _payload_signature(original)
                    edge_signatures[company_code][label] = signature
                    try:
                        repeated = runner.post_lectures(company_code, page)
                        stability_rechecks += 1
                    except Exception as exc:
                        errors.append(
                            f"{company_code}: {label} page recheck {type(exc).__name__}"
                        )
                        break
                    if _payload_signature(repeated) != signature:
                        errors.append(
                            f"{company_code}: {label} page signature changed during census"
                        )
                if errors:
                    break

        identities = [_clean(row.get("provider_course_id")) for row in rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate company/class identities")
        urls = [_clean(row.get("raw_url")) for row in rows]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate canonical detail URLs")
        for row in rows:
            raw = row.get("raw_fields", {})
            if not _safe_detail_url(
                row.get("raw_url"),
                _clean(raw.get("official_company_code")),
                _clean(raw.get("official_class_code")),
            ):
                errors.append(
                    f"{_clean(row.get('provider_course_id'))}: unsafe detail URL"
                )

        if not errors:
            semantic = [_semantic_key(row) for row in rows]
            semantic_duplicate_count = len(semantic) - len(set(semantic))
            if semantic_duplicate_count:
                errors.append(
                    f"{semantic_duplicate_count} duplicate current semantic signatures"
                )

        if not errors:
            for row in rows:
                detail_attempts += 1
                try:
                    soup = runner.get_soup(_clean(row.get("raw_url")))
                    row_errors, has_control, omitted_pii = _detail_contract(row, soup)
                    application_controls += int(has_control)
                    pii_omissions += int(omitted_pii)
                    if row_errors:
                        detail_errors += len(row_errors)
                        errors.extend(row_errors)
                    else:
                        detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail fetch "
                        f"{type(exc).__name__}"
                    )

        if not errors:
            cleaned = [_clean_row(row) for row in rows]
            if dedupe_rows is None:
                result = cleaned
            else:
                try:
                    result = list(dedupe_rows(cleaned))
                except Exception as exc:
                    errors.append(f"dedupe_rows {type(exc).__name__}")
                    result = []
            if not errors:
                before_ids = [_clean(row.get("provider_course_id")) for row in cleaned]
                after_ids = [_clean(row.get("provider_course_id")) for row in result]
                if after_ids != before_ids:
                    errors.append(
                        "dedupe changed complete current-course identity sequence"
                    )
                    result = []

        unique_errors = list(dict.fromkeys(errors))
        snapshot_complete = not unique_errors
        pagination_complete = bool(
            snapshot_complete
            and len(payloads) == required_page_requests
            and sentinel_requests == len(SIHEUNG_SPORTS_COMPANIES)
            and stability_rechecks == required_edge_rechecks
            and len(rows) == source_total
        )
        details_complete = bool(
            snapshot_complete
            and detail_attempts == source_total
            and detail_pages == source_total
            and detail_errors == 0
        )
        company_totals = {
            code: boundaries[code].total
            for code, _ in SIHEUNG_SPORTS_COMPANIES
            if code in boundaries
        }
        company_data_pages = {
            code: boundaries[code].data_pages
            for code, _ in SIHEUNG_SPORTS_COMPANIES
            if code in boundaries
        }
        company_sentinel_pages = {
            code: boundaries[code].sentinel_page
            for code, _ in SIHEUNG_SPORTS_COMPANIES
            if code in boundaries
        }
        page_counts = {
            code: {
                page: len(payloads.get((code, page), []))
                for page in range(1, boundary.sentinel_page + 1)
            }
            for code, boundary in boundaries.items()
        }
        source_status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status")) for row in rows
        )
        status_counts = Counter(_clean(row.get("status")) for row in result)
        branch_counts = Counter(_clean(row.get("branch")) for row in rows)
        meta.update(
            {
                "pages": len(payloads),
                "main_discovery_pages": int(landing_loaded),
                "company_requests": int(company_loaded),
                "list_requests": len(payloads) + stability_rechecks,
                "api_requests": int(company_loaded) + len(payloads) + stability_rechecks,
                "physical_requests": runner.physical_requests,
                "request_method_counts": dict(runner.method_counts),
                "retry_count": runner.retry_count,
                "sessions_created": runner.sessions_created,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "detail_errors": detail_errors,
                "source_total": source_total,
                "source_rows": len(rows),
                "current_count": len(rows),
                "returned_count": len(result),
                "discovered_links": len(rows),
                "company_count": len(boundaries),
                "official_company_count": len(SIHEUNG_SPORTS_COMPANIES),
                "active_company_count": sum(total > 0 for total in company_totals.values()),
                "empty_company_count": sum(total == 0 for total in company_totals.values()),
                "company_names": dict(SIHEUNG_SPORTS_COMPANIES),
                "company_totals": company_totals,
                "company_data_pages": company_data_pages,
                "company_sentinel_pages": company_sentinel_pages,
                "page_counts": page_counts,
                "required_page_requests": required_page_requests,
                "sentinel_requests": sentinel_requests,
                "sentinel_kind": "per_company_immediate_empty",
                "stability_rechecks": stability_rechecks,
                "required_edge_rechecks": required_edge_rechecks,
                "edge_signatures": edge_signatures,
                "first_identity": (
                    _clean(rows[0].get("raw_fields", {}).get("official_company_code"))
                    + ":"
                    + _clean(rows[0].get("raw_fields", {}).get("official_class_code"))
                    if rows
                    else ""
                ),
                "last_identity": (
                    _clean(rows[-1].get("raw_fields", {}).get("official_company_code"))
                    + ":"
                    + _clean(rows[-1].get("raw_fields", {}).get("official_class_code"))
                    if rows
                    else ""
                ),
                "source_identity_sha256": _identity_sha(rows) if rows else "",
                "output_identity_sha256": _identity_sha(result) if result else "",
                "source_status_counts": dict(source_status_counts),
                "status_counts": dict(status_counts),
                "branch_count": len(branch_counts),
                "branch_counts": dict(branch_counts),
                "venue_count": len(
                    {_clean(row.get("venue_name")) for row in result if row.get("venue_name")}
                ),
                "malformed_count": malformed_count,
                "duplicate_count": duplicate_count,
                "duplicate_url_count": duplicate_url_count,
                "semantic_duplicate_count": semantic_duplicate_count,
                "pii_omission_count": pii_omissions,
                "detail_status_refresh_count": sum(
                    bool(row.get("raw_fields", {}).get("detail_status_refreshed"))
                    for row in rows
                ),
                "detail_capacity_refresh_count": sum(
                    bool(row.get("raw_fields", {}).get("detail_capacity_refreshed"))
                    for row in rows
                ),
                "application_form_discovery_count": detail_pages,
                "application_control_count": application_controls,
                "reservation_discovery_links": sum(
                    bool(row.get("application_url")) for row in result
                ),
                "required_logical_requests": required_logical_requests,
                "max_requests": max_requests,
                "session_request_limit": SIHEUNG_SPORTS_SESSION_REQUEST_LIMIT,
                "pagination_detected": any(
                    pages > 1 for pages in company_data_pages.values()
                ),
                "pagination_complete": pagination_complete,
                "details_complete": details_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_required": True,
                "source_cap_reached": source_cap_reached,
                "no_current_data": bool(snapshot_complete and not rows),
                "no_current_reason": (
                    "official Siheung Urban Corporation current/new-registration catalogue is empty"
                    if snapshot_complete and not rows
                    else ""
                ),
                "configured_collection_error": "; ".join(unique_errors),
                "source_scope": SIHEUNG_SPORTS_SEARCH_TYPE,
                "source_scope_label": "신규접수",
                "excluded_ended_scope": "E",
                "ownership_system": SIHEUNG_SPORTS_OWNER_NAME,
                "ownership_companies": dict(SIHEUNG_SPORTS_COMPANIES),
                "separate_ssoc_owner_url": SIHEUNG_SSOC_SEPARATE_OWNER_URL,
                "audited_at": SIHEUNG_SPORTS_AUDITED_AT,
                "audited_current_total": SIHEUNG_SPORTS_AUDITED_CURRENT_TOTAL,
                "audited_company_totals": dict(
                    SIHEUNG_SPORTS_AUDITED_COMPANY_TOTALS
                ),
                "application_endpoints_called": 0,
                "login_endpoints_called": 0,
                "attachment_endpoints_called": 0,
                "pii_endpoints_called": 0,
            }
        )
        if unique_errors:
            return [], SIHEUNG_SPORTS_PARSER, meta
        return result, SIHEUNG_SPORTS_PARSER, meta
    finally:
        runner.close()


collect = collect_siheung_sports_courses


__all__ = [
    "SIHEUNG_MUNICIPALITY_CODE",
    "SIHEUNG_MUNICIPALITY_NAME",
    "SIHEUNG_SPORTS_APPLICATION_URL",
    "SIHEUNG_SPORTS_AUDITED_AT",
    "SIHEUNG_SPORTS_AUDITED_COMPANY_TOTALS",
    "SIHEUNG_SPORTS_AUDITED_CURRENT_TOTAL",
    "SIHEUNG_SPORTS_COMPANIES",
    "SIHEUNG_SPORTS_COMPANY_API",
    "SIHEUNG_SPORTS_DEFAULT_DETAIL_LIMIT",
    "SIHEUNG_SPORTS_DEFAULT_MAX_PAGES",
    "SIHEUNG_SPORTS_DEFAULT_MAX_REQUESTS",
    "SIHEUNG_SPORTS_HOST",
    "SIHEUNG_SPORTS_LECTURE_API",
    "SIHEUNG_SPORTS_PAGE_SIZE",
    "SIHEUNG_SPORTS_PARSER",
    "SIHEUNG_SPORTS_PROVIDER",
    "SIHEUNG_SPORTS_SEARCH_TYPE",
    "SIHEUNG_SPORTS_SESSION_REQUEST_LIMIT",
    "SIHEUNG_SPORTS_URL",
    "SIHEUNG_SSOC_SEPARATE_OWNER_URL",
    "SiheungSportsContractError",
    "collect",
    "collect_siheung_sports_courses",
    "is_siheung_sports_target",
    "is_target",
    "siheung_sports_detail_url",
    "siheung_sports_list_payload",
]
