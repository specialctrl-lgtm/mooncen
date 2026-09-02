"""Fail-closed collector for Sejong's official youth programme ledger.

The public ``누리다`` programme-application page is a mixed education and
experience catalogue.  The official start-date filter uses interval-overlap
semantics, so posting only ``eduBgngDt=<crawl day>`` yields every programme
round whose education end is current or future.  The source can repeat an
identical identity at a page boundary; identical copies are reconciled while
conflicting copies fail the whole snapshot.

Only the public list POST and public detail GET are requested.  The application
form, login, applicant history, attachments, file downloads, surveys and every
PII-bearing endpoint are deliberately outside the request allowlist.  Public
details are still checked for every current source identity, including rows
later rejected as committee/club recruitment, notices, surveys or facility
use, so those exclusions are evidence-based rather than title-only shortcuts.

The host currently omits its GlobalSign intermediate from the TLS handshake.
The default transport pins that public intermediate into certifi's trust
context; normal certificate-chain and hostname verification remain enabled.
"""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import ssl
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import certifi
import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter


SEJONG_YOUTH_PROVIDER = "MUNI_WWW2_SEJONG_GO_KR_C0BBF920"
SEJONG_YOUTH_HOST = "www2.sejong.go.kr"
SEJONG_YOUTH_ROOT = f"https://{SEJONG_YOUTH_HOST}"
SEJONG_YOUTH_LIST_PATH = "/youth/prog/progrm/kor/sub03_02/list.do"
SEJONG_YOUTH_DETAIL_PATH = "/youth/prog/progrm/kor/sub03_02/view.do"
SEJONG_YOUTH_APPLICATION_PATH = "/youth/prog/progrmAply/kor/sub03_02/write.do"
SEJONG_YOUTH_CANONICAL_URL = f"{SEJONG_YOUTH_ROOT}{SEJONG_YOUTH_LIST_PATH}"
SEJONG_YOUTH_MUNICIPALITY_CODE = "3611000000"
SEJONG_YOUTH_MUNICIPALITY_NAME = "세종특별자치시"
SEJONG_YOUTH_PAGE_SIZE = 10
SEJONG_YOUTH_MAX_HTML_BYTES = 3_000_000
SEJONG_YOUTH_PARSER = (
    "sejong_nurida_official_mixed_programmes+current_overlap_filter+"
    "declared_pages+empty_sentinel+identical_source_dedupe+stable_edges+"
    "all_current_public_details+course_experience_classifier+"
    "committee_notice_facility_exclusion+verified_tls_intermediate+"
    "no_application_or_pii_calls"
)
SEJONG_YOUTH_OWNERSHIP_SCOPE = (
    "sejong_nurida_public_current_future_youth_education_and_experience"
)

# GlobalSign GCC R6 AlphaSSL CA 2025, fetched from the leaf certificate's
# public AIA URL.  Fingerprint verification below prevents an accidental or
# malicious source-code substitution from silently changing the trust anchor.
_GLOBALSIGN_ALPHA_SSL_2025_DER_B64 = (
    "MIIFjTCCA3WgAwIBAgIRAIN9TriekS/nLK07x2kt3CAwDQYJKoZIhvcNAQELBQAw"
    "TDEgMB4GA1UECxMXR2xvYmFsU2lnbiBSb290IENBIC0gUjYxEzARBgNVBAoTCkds"
    "b2JhbFNpZ24xEzARBgNVBAMTCkdsb2JhbFNpZ24wHhcNMjUwNTIxMDIzNjUyWhcN"
    "MjcwNTIxMDAwMDAwWjBVMQswCQYDVQQGEwJCRTEZMBcGA1UEChMQR2xvYmFsU2ln"
    "biBudi1zYTErMCkGA1UEAxMiR2xvYmFsU2lnbiBHQ0MgUjYgQWxwaGFTU0wgQ0Eg"
    "MjAyNTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAJ/oiu0Bviq52UUE"
    "ADbFWmgu3rC7KDSMoorLN1Wd03McG3Z1aP71DlPCE33838r72Dfuj5M9LXfiQLJp"
    "Au6MwNExmKOzothw4x0zGf5oBYyrCMGm3fBpLPafwYQ3MchBOWMTbf83rKUPLH48"
    "KCJ0MnU8GUl8oA/J81wIvbbKPuNrFf6hvJDccjzc4NyxLz3A89zjV2g5whCg5O0"
    "u9YX4Zxk9JHuc/LvllOJO4waAYLjbWBJkz3rV3ts1SmSYnJqmyRTIjXwQgRvhEYq"
    "tDbRskt0W7M6cPwCze3GTBN2UHNpHkMs3YmVxku68I0aOQn5+uz//fDROP3z1Z/"
    "7IAPteRtECAwEAAaOCAV8wggFbMA4GA1UdDwEB/wQEAwIBhjAdBgNVHSUEFjAUBg"
    "grBgEFBQcDAQYIKwYBBQUHAwIwEgYDVR0TAQH/BAgwBgEB/wIBADAdBgNVHQ4EFg"
    "QUxbSTj28r3B5Iv7cQMIXO0bK7SC0wHwYDVR0jBBgwFoAUrmwFo5MT4qLn4tcc1"
    "sfwf8hnU6AwewYIKwYBBQUHAQEEbzBtMC4GCCsGAQUFBzABhiJodHRwOi8vb2Nz"
    "cDIuZ2xvYmFsc2lnbi5jb20vcm9vdHI2MDsGCCsGAQUFBzAChi9odHRwOi8vc2Vj"
    "dXJlLmdsb2JhbHNpZ24uY29tL2NhY2VydC9yb290LXI2LmNydDA2BgNVHR8ELzAt"
    "MCugKaAnhiVodHRwOi8vY3JsLmdsb2JhbHNpZ24uY29tL3Jvb3QtcjYuY3JsMCEG"
    "A1UdIAQaMBgwCAYGZ4EMAQIBMAwGCisGAQQBoDIKAQMwDQYJKoZIhvcNAQELBQAD"
    "ggIBAB/uvBuZf4CiuSahwiXn4geF52roAH+6jxsEPTXTfb7bbeMDXsYgRRsOTNA7"
    "0ruZTnz5DfFMuBhNoFhIFb0qR1izdy6VkdKOqFPNF2dOFI1EcnY9l2ory9mrzHqV"
    "brL4vzUd17FLUVyjTVU7PAv4nxyhnO1GTeT83YlrdRF31NyR6bvZVTEERHmpbWS"
    "geveJLRtaMzlGWiLZ8IwkH7o6GH3jp/KPtDW4Npu8w64HrRZdN2pqQhi7+YKwfH"
    "M7H+2UdM1BGN0sjOWMVbMSB9MtCsleS2Mb7TRZEbOHxECJLLIluQypZr7Pol3+h"
    "AqrhyKIk+6y+Da0NeDuWxW59Ku4NvClqW1UFX1SpfNGhzVfp/CH+vPM1tySomx2"
    "jE0EnYZuGwVucXPBsp5nUWqUV9+143glVuS7GTg9hFPjNBInn17HbCoIIQIOzj5V"
    "d9bK3A9UGxXNpwenDHEalCsD/4eQYDHPhFE7sNe0D/OXu+FAM02VZkARx37Jp4b"
    "DdujvgL9PvZPR3wThvDN1CTU8Bc3xea3yKFAraKcPZLkhReQUAm2VpR+HSJRPlU"
    "pYizlF9WkLh3KcAVCBJWvnOkVwxyU5QJMcnwW95JlOtx+9100GL99jHE5rs3gXp"
    "7F4bg8H01QT9jVOhBBmQ7nQoXuwI0tqal2QUqZz3eeu62CU7xBwtfYR"
)
SEJONG_YOUTH_INTERMEDIATE_SHA256 = (
    "a883559231f8388daf35ce41c8101040ae8fd9b656434247b9475af592cc08ca"
)

_SPACE_RE = re.compile(r"\s+")
_COUNT_RE = re.compile(
    r"총\s*게시물\s*([\d,]+)\s*개,\s*페이지\s*(\d+)\s*/\s*(\d+)"
)
_IDENTITY_RE = re.compile(r"fn_move_detail\('(\d+)','(\d+)'\)\Z")
_DATETIME_RANGE_RE = re.compile(
    r"(20\d{2})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\Z"
)
_DATE_IN_TEXT_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_LIST_FIELDS = frozenset({"회차", "참여대상", "교육기간", "접수기간", "교육일시"})
_DETAIL_FIELDS = frozenset(
    {
        "회차",
        "참여대상",
        "참여생년",
        "교육기간",
        "활동장소",
        "문의처",
        "준비물",
        "참가비",
        "접수기간",
        "교육일시",
        "교육자료",
    }
)
_ROUND_HEADERS = (
    "회차명",
    "참여생년",
    "교육일시",
    "접수기간",
    "회차자료",
    "모집정원",
    "대기정원",
    "신청자",
    "접수상태",
)
_SOURCE_STATUSES = frozenset({"접수중", "접수예정", "접수마감"})
_SOURCE_METHODS = frozenset({"선착순", "오프라인"})
_SOURCE_CATEGORIES = frozenset(
    {
        "건강/스포츠",
        "인문/과학",
        "환경/모험",
        "진로/직업",
        "문화/예술",
        "봉사/사회참여",
        "정서지원",
        "기타",
    }
)
_EDUCATION_CATEGORIES = frozenset({"인문/과학", "진로/직업", "정서지원"})
_EXPERIENCE_CATEGORIES = frozenset(
    {"건강/스포츠", "환경/모험", "문화/예술", "봉사/사회참여"}
)
_EDUCATION_TITLE_RE = re.compile(
    r"교육|교실|아카데미|멘토링|특강|강좌|배움터|수업"
)
_OTHER_EDUCATION_RE = re.compile(
    r"프로젝트|메이커|글쓰기|웹툰|수채화|목공|요리|베이킹|퍼스널컬러|"
    r"캐리커처|마술|초콜릿|플라워|바리스타|책을\s*읽고"
)
_OTHER_EXPERIENCE_RE = re.compile(r"체험|동아리\s*DAY|카페|게임|대회|봉사|만들기")


class SejongYouthContractError(ValueError):
    """Raised when the reviewed public source contract changes."""


@dataclass(frozen=True)
class ListedProgram:
    program_no: str
    round_no: str
    page: int
    position: int
    status: str
    method: str
    source_category: str
    branch: str
    title: str
    round_name: str
    target: str
    education_period: str
    apply_period: str
    education_datetime: str
    start: datetime
    end: datetime
    apply_start: datetime
    apply_end: datetime
    raw_url: str
    source_date_corrected: bool = False

    @property
    def identity(self) -> str:
        return f"{self.program_no}:{self.round_no}"

    def source_signature(self) -> tuple[Any, ...]:
        return (
            self.program_no,
            self.round_no,
            self.status,
            self.method,
            self.source_category,
            self.branch,
            self.title,
            self.round_name,
            self.target,
            self.education_period,
            self.apply_period,
            self.education_datetime,
            self.raw_url,
            self.source_date_corrected,
        )


@dataclass(frozen=True)
class ListPage:
    total: int
    page: int
    last_page: int
    rows: tuple[ListedProgram, ...]
    no_data: bool


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class _SSLContextAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext) -> None:
        self._context = context
        super().__init__()

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self._context
        super().init_poolmanager(*args, **kwargs)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise SejongYouthContractError("today must be an ISO date") from exc


def _exact_target_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SEJONG_YOUTH_HOST
        and parsed.port is None
        and parsed.path == SEJONG_YOUTH_LIST_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_sejong_youth_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")).upper() == SEJONG_YOUTH_PROVIDER
        and _exact_target_url(_target_value(target, "url"))
    )


is_target = is_sejong_youth_target


def sejong_youth_detail_url(program_no: Any, round_no: Any) -> str:
    program = _clean(program_no)
    round_value = _clean(round_no)
    if not program.isdigit() or not round_value.isdigit():
        return ""
    return f"{SEJONG_YOUTH_ROOT}{SEJONG_YOUTH_DETAIL_PATH}?{urlencode({'progrmNo': program, 'tmeNo': round_value})}"


def sejong_youth_list_payload(page: Any, cutoff: date) -> dict[str, str]:
    page_text = _clean(page)
    if not page_text.isdigit() or int(page_text) < 1:
        raise SejongYouthContractError("list page must be a positive integer")
    return {
        "pageIndex": str(int(page_text)),
        "instCd": "",
        "tyCd": "",
        "maxAge": "",
        "minAge": "",
        "eduBgngDt": cutoff.isoformat(),
        "eduEndDt": "",
        "progrmNm": "",
    }


def _tls_context() -> ssl.SSLContext:
    der = base64.b64decode(_GLOBALSIGN_ALPHA_SSL_2025_DER_B64)
    if hashlib.sha256(der).hexdigest() != SEJONG_YOUTH_INTERMEDIATE_SHA256:
        raise RuntimeError("embedded GlobalSign intermediate fingerprint changed")
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))
    return context


def _default_session_factory() -> requests.Session:
    value = requests.Session()
    value.mount(
        f"https://{SEJONG_YOUTH_HOST}/",
        _SSLContextAdapter(_tls_context()),
    )
    value.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://www2.sejong.go.kr/youth/)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return value


def _safe_response_url(value: Any, *, kind: str) -> bool:
    parsed = urlparse(_clean(value))
    expected_path = (
        SEJONG_YOUTH_LIST_PATH if kind == "list" else SEJONG_YOUTH_DETAIL_PATH
    )
    if not (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SEJONG_YOUTH_HOST
        and parsed.port is None
        and parsed.path == expected_path
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        return False
    if kind == "list":
        return not parsed.query
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        set(query) == {"progrmNo", "tmeNo"}
        and len(query["progrmNo"]) == 1
        and len(query["tmeNo"]) == 1
        and query["progrmNo"][0].isdigit()
        and query["tmeNo"][0].isdigit()
    )


class _Requester:
    def __init__(
        self,
        session_factory: SessionFactory,
        timeout: int,
        request_cap: int,
    ) -> None:
        self.session = session_factory()
        self.timeout = timeout
        self.request_cap = request_cap
        self.requests = 0
        self.list_requests = 0
        self.detail_requests = 0

    def _consume(self) -> None:
        if self.requests >= self.request_cap:
            raise SejongYouthContractError("request budget exhausted")
        self.requests += 1

    def soup(
        self,
        url: str,
        *,
        kind: str,
        payload: Optional[Mapping[str, str]] = None,
    ) -> BeautifulSoup:
        if kind not in {"list", "detail"} or not _safe_response_url(url, kind=kind):
            raise SejongYouthContractError("request left the reviewed public URL allowlist")
        self._consume()
        if kind == "list":
            if payload is None or set(payload) != {
                "pageIndex",
                "instCd",
                "tyCd",
                "maxAge",
                "minAge",
                "eduBgngDt",
                "eduEndDt",
                "progrmNm",
            }:
                raise SejongYouthContractError("list POST payload contract changed")
            response = self.session.post(
                url,
                data=dict(payload),
                timeout=self.timeout,
                allow_redirects=False,
            )
            self.list_requests += 1
        else:
            if payload is not None:
                raise SejongYouthContractError("detail GET cannot have a POST payload")
            response = self.session.get(
                url,
                timeout=self.timeout,
                allow_redirects=False,
            )
            self.detail_requests += 1
        status = int(getattr(response, "status_code", 200))
        if status != 200:
            raise SejongYouthContractError(f"unexpected HTTP status {status}")
        final_url = _clean(getattr(response, "url", url)) or url
        if not _safe_response_url(final_url, kind=kind):
            raise SejongYouthContractError("response left the reviewed HTTPS scope")
        headers = getattr(response, "headers", {}) or {}
        content_type = _clean(headers.get("Content-Type"))
        if content_type and "html" not in content_type.lower():
            raise SejongYouthContractError("response is not HTML")
        content = getattr(response, "content", None)
        if content is None:
            content = str(getattr(response, "text", "")).encode("utf-8")
        if not content or len(content) > SEJONG_YOUTH_MAX_HTML_BYTES:
            raise SejongYouthContractError("HTML response is empty or over the size cap")
        return BeautifulSoup(content, "lxml")

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _parse_datetime_range(
    value: Any,
    field: str,
    *,
    allow_reversed: bool = False,
) -> tuple[datetime, datetime]:
    text = _clean(value)
    match = _DATETIME_RANGE_RE.fullmatch(text)
    if not match:
        raise SejongYouthContractError(f"{field} is not an exact datetime range")
    values = [int(value) for value in match.groups()]
    try:
        start = datetime(*values[:5])
        end = datetime(*values[5:])
    except ValueError as exc:
        raise SejongYouthContractError(f"{field} contains an invalid datetime") from exc
    if end < start and not allow_reversed:
        raise SejongYouthContractError(f"{field} range is reversed")
    return start, end


def _education_datetime_range(
    value: Any,
    education_period: Any,
) -> tuple[datetime, datetime, bool]:
    start, end = _parse_datetime_range(
        value,
        "교육일시",
        allow_reversed=True,
    )
    if end >= start:
        return start, end, False
    source_dates = {
        date(int(year), int(month), int(day))
        for year, month, day in _DATE_IN_TEXT_RE.findall(_clean(education_period))
    }
    corrected_end = datetime.combine(start.date(), end.time())
    if (
        source_dates != {start.date()}
        or corrected_end < start
        or (corrected_end - start).total_seconds() > 12 * 60 * 60
    ):
        raise SejongYouthContractError(
            "reversed 교육일시 cannot be reconciled to its one-day 교육기간"
        )
    return start, corrected_end, True


def _labelled_list(node: Tag, selector: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in node.select(selector):
        labels = item.select(":scope > em")
        if len(labels) != 1:
            raise SejongYouthContractError("labelled programme field contract changed")
        label = _clean(labels[0].get_text(" ", strip=True))
        strings = [_clean(value) for value in item.stripped_strings]
        if not strings or strings[0] != label or label in pairs:
            raise SejongYouthContractError("duplicate or malformed programme field")
        pairs[label] = _clean(" ".join(strings[1:]))
    return pairs


def _parse_list_page(soup: BeautifulSoup, expected_page: int, cutoff: date) -> ListPage:
    markers = soup.select(".program--count")
    if len(markers) != 1:
        raise SejongYouthContractError("list count marker contract changed")
    match = _COUNT_RE.fullmatch(_clean(markers[0].get_text(" ", strip=True)))
    if not match:
        raise SejongYouthContractError("list count text contract changed")
    total = int(match.group(1).replace(",", ""))
    current_page = int(match.group(2))
    last_page = int(match.group(3))
    if current_page != expected_page or last_page < 1:
        raise SejongYouthContractError("list page marker does not match the request")
    roots = soup.select("div.board--card--list.type2.board_reservation")
    if len(roots) != 1:
        raise SejongYouthContractError("programme card root contract changed")
    root = roots[0]
    cards = root.select("button.link[onclick]")
    rows: list[ListedProgram] = []
    for position, card in enumerate(cards, start=1):
        identity = _IDENTITY_RE.fullmatch(_clean(card.get("onclick")))
        items = card.select(":scope > .item")
        if not identity or len(items) != 1:
            raise SejongYouthContractError("programme identity/card contract changed")
        item = items[0]
        badges = [_clean(node.get_text(" ", strip=True)) for node in item.select(".stats-list span")]
        if (
            len(badges) != 3
            or badges[0] not in _SOURCE_STATUSES
            or badges[1] not in _SOURCE_METHODS
            or badges[2] not in _SOURCE_CATEGORIES
        ):
            raise SejongYouthContractError("programme status/method/category contract changed")
        branches = item.select(".title em")
        titles = item.select("strong.tit")
        if len(branches) != 1 or len(titles) != 1:
            raise SejongYouthContractError("programme title/branch contract changed")
        branch = _clean(branches[0].get_text(" ", strip=True))
        title = _clean(titles[0].get_text(" ", strip=True))
        pairs = _labelled_list(item, "ul.ul--block__list > li")
        if set(pairs) != _LIST_FIELDS or not all(
            pairs[field] for field in ("회차", "참여대상", "교육기간", "접수기간", "교육일시")
        ):
            raise SejongYouthContractError("programme list fields changed")
        start, end, source_date_corrected = _education_datetime_range(
            pairs["교육일시"],
            pairs["교육기간"],
        )
        apply_start, apply_end = _parse_datetime_range(pairs["접수기간"], "접수기간")
        if end.date() < cutoff:
            raise SejongYouthContractError("current-overlap filter returned an expired programme")
        program_no, round_no = identity.groups()
        raw_url = sejong_youth_detail_url(program_no, round_no)
        if not branch or not title or not raw_url:
            raise SejongYouthContractError("programme identity fields are empty")
        rows.append(
            ListedProgram(
                program_no=program_no,
                round_no=round_no,
                page=expected_page,
                position=position,
                status=badges[0],
                method=badges[1],
                source_category=badges[2],
                branch=branch,
                title=title,
                round_name=pairs["회차"],
                target=pairs["참여대상"],
                education_period=pairs["교육기간"],
                apply_period=pairs["접수기간"],
                education_datetime=pairs["교육일시"],
                start=start,
                end=end,
                apply_start=apply_start,
                apply_end=apply_end,
                raw_url=raw_url,
                source_date_corrected=source_date_corrected,
            )
        )
    no_data = "등록된 프로그램이 없습니다." in _clean(root.get_text(" ", strip=True))
    if rows and no_data:
        raise SejongYouthContractError("list page mixes programme rows with no-data marker")
    if not rows and not no_data:
        raise SejongYouthContractError("empty list page lacks the official no-data marker")
    return ListPage(total, current_page, last_page, tuple(rows), no_data)


def _page_signature(page: ListPage) -> tuple[Any, ...]:
    return (
        page.total,
        page.page,
        page.last_page,
        page.no_data,
        tuple(row.source_signature() for row in page.rows),
    )


def _non_program_reason(title: str, round_name: str) -> str:
    text = _clean(f"{title} {round_name}")
    normalized = _normalized(text)
    if "만족도조사" in normalized or "설문조사" in normalized:
        return "survey"
    if text.lstrip().startswith("[홍보]") or (
        "프로그램 안내" in text and "홍보" in text
    ):
        return "promotion_notice"
    if text.lstrip().startswith("[이용예약]") or re.search(
        r"대관\s*(?:신청|예약)|공간\s*이용\s*예약", text
    ):
        return "facility_use"
    if "청소년운영위원회" in text or "자치위원회" in text:
        return "committee_membership"
    if "청소년 자치기구" in text or "참여계획단" in text:
        return "youth_governance_membership"
    if re.search(r"마을\s*축제\s*기획단", text):
        return "festival_planning_membership"
    if "방과후아카데미" in text and re.search(r"신규\s*청소년\s*모집", text):
        return "academy_enrolment_notice"
    if re.search(r"동아리.*(?:동아리원|참여\s*청소년|신규\s*위원|참가자)?\s*(?:추가\s*)?모집", text):
        return "club_membership"
    if re.search(r"프로젝트팀.*참여자\s*모집|기획단.*모집", text):
        return "project_team_membership"
    if "자기도전포상제" in text or "국제청소년성취포상제" in text:
        return "award_scheme_membership"
    if "랜선 아웃리치" in text:
        return "outreach_notice"
    return ""


def classify_sejong_youth_program(
    title: Any,
    round_name: Any,
    source_category: Any,
) -> tuple[str, str]:
    """Return ``(education|experience|exclude, reason)`` for one source row."""

    title_text = _clean(title)
    round_text = _clean(round_name)
    category = _clean(source_category)
    reason = _non_program_reason(title_text, round_text)
    if reason:
        return "exclude", reason
    combined = f"{title_text} {round_text}"
    if _EDUCATION_TITLE_RE.search(combined):
        return "education", "explicit_learning_semantics"
    if category in _EDUCATION_CATEGORIES:
        return "education", "official_learning_category"
    if category in _EXPERIENCE_CATEGORIES:
        return "experience", "official_activity_category"
    if category == "기타":
        if _OTHER_EDUCATION_RE.search(combined):
            return "education", "reviewed_other_learning_semantics"
        if _OTHER_EXPERIENCE_RE.search(combined):
            return "experience", "reviewed_other_activity_semantics"
        return "exclude", "unclassified_other"
    return "exclude", "unknown_official_category"


def _form_action_path(value: Any) -> str:
    parsed = urlparse(_clean(value))
    return parsed.path.split(";", 1)[0]


def _integer(value: Any, field: str) -> int:
    text = _clean(value).replace(",", "")
    if not text.isdigit():
        raise SejongYouthContractError(f"{field} is not a non-negative integer")
    return int(text)


def _branch_code(provider: str, branch: str) -> str:
    digest = hashlib.sha1(f"{provider}|{branch}".encode("utf-8")).hexdigest()[:12].upper()
    return f"SEJONG_YOUTH_{digest}"


def _status(value: str) -> str:
    return {
        "접수중": "OPEN",
        "접수예정": "SCHEDULED",
        "접수마감": "CLOSED",
    }[value]


def _row_from_detail(
    target: Any,
    listed: ListedProgram,
    soup: BeautifulSoup,
) -> tuple[Optional[dict[str, Any]], str, str, bool]:
    roots = soup.select("div.photo_wrap.typeB.edue")
    if len(roots) != 1:
        raise SejongYouthContractError("detail programme root contract changed")
    root = roots[0]
    titles = root.select(".info_box > strong.tit")
    branches = root.select(".info_box > strong.tit > em")
    if len(titles) != 1 or len(branches) != 1:
        raise SejongYouthContractError("detail title/branch contract changed")
    detail_branch = _clean(branches[0].get_text(" ", strip=True))
    combined_title = _clean(titles[0].get_text(" ", strip=True))
    detail_title = _clean(combined_title[len(detail_branch) :]) if combined_title.startswith(detail_branch) else ""
    if _normalized(detail_branch) != _normalized(listed.branch) or _normalized(detail_title) != _normalized(listed.title):
        raise SejongYouthContractError("detail/list title or branch mismatch")
    state_texts = {
        _clean(node.get_text(" ", strip=True))
        for node in root.select(".state_box .badge")
        if _clean(node.get_text(" ", strip=True))
    }
    if listed.method not in state_texts or listed.source_category not in state_texts:
        raise SejongYouthContractError("detail/list method or category mismatch")
    pairs = _labelled_list(root, ".info_box > ul.list-1st > li")
    if set(pairs) != _DETAIL_FIELDS:
        raise SejongYouthContractError("detail programme fields changed")
    safe_matches = {
        "회차": listed.round_name,
        "참여대상": listed.target,
        "교육기간": listed.education_period,
        "접수기간": listed.apply_period,
        "교육일시": listed.education_datetime,
    }
    for field, expected in safe_matches.items():
        if _normalized(pairs[field]) != _normalized(expected):
            raise SejongYouthContractError(f"detail/list {field} mismatch")
    detail_start, detail_end, detail_date_corrected = _education_datetime_range(
        pairs["교육일시"],
        pairs["교육기간"],
    )
    apply_start, apply_end = _parse_datetime_range(pairs["접수기간"], "detail 접수기간")
    if (
        (detail_start, detail_end) != (listed.start, listed.end)
        or detail_date_corrected != listed.source_date_corrected
        or (
        apply_start,
        apply_end,
        )
        != (listed.apply_start, listed.apply_end)
    ):
        raise SejongYouthContractError("detail/list parsed datetime mismatch")

    round_tables = []
    for table in soup.select("table"):
        headers = tuple(
            _clean(node.get_text(" ", strip=True))
            for node in table.select("thead th")
        )
        if headers == _ROUND_HEADERS:
            round_tables.append(table)
    if len(round_tables) != 1:
        raise SejongYouthContractError("detail round headers changed")
    round_table = round_tables[0]
    matching_rows: list[tuple[list[str], Tag]] = []
    for table_row in round_table.select("tbody > tr"):
        cells = table_row.select(":scope > td")
        if len(cells) != len(_ROUND_HEADERS):
            raise SejongYouthContractError("detail round cell count changed")
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        if (
            _normalized(values[0]) == _normalized(listed.round_name)
            and _normalized(values[2]) == _normalized(listed.education_datetime)
            and _normalized(values[3]) == _normalized(listed.apply_period)
        ):
            matching_rows.append((values, table_row))
    if len(matching_rows) != 1:
        raise SejongYouthContractError("selected detail round is missing or ambiguous")
    values, table_row = matching_rows[0]
    controls = table_row.select(":scope > td:last-child a.receiptStatus")
    if len(controls) != 1:
        raise SejongYouthContractError("selected round application control changed")
    control = controls[0]
    control_text = _clean(control.get_text(" ", strip=True))
    has_application = False
    if listed.status == "접수중" and listed.method == "선착순":
        has_application = bool(
            control_text == "신청하기"
            and _clean(control.get("href")) == "#"
            and _clean(control.get("data-progrm-no")) == listed.program_no
            and _clean(control.get("data-tme-no")) == listed.round_no
            and "button-write" in (control.get("class") or [])
        )
        if not has_application:
            raise SejongYouthContractError("online application control is not identity-bound")
    elif listed.status == "접수중" and listed.method == "오프라인":
        if control_text != "오프라인접수":
            raise SejongYouthContractError("offline application control changed")
    elif listed.status == "접수예정":
        if control_text != "접수예정":
            raise SejongYouthContractError("scheduled application control changed")
    elif listed.status == "접수마감":
        if control_text not in {"접수마감", "오프라인접수"}:
            raise SejongYouthContractError("closed application control changed")

    forms = soup.select("form#searchForm")
    if len(forms) != 1:
        raise SejongYouthContractError("detail application discovery form changed")
    form = forms[0]
    names = [_clean(node.get("name")) for node in form.select("[name]")]
    if (
        _clean(form.get("method")).lower() != "get"
        or _form_action_path(form.get("action")) != SEJONG_YOUTH_APPLICATION_PATH
        or sorted(names) != ["pageIndex", "progrmNo", "tmeNo"]
    ):
        raise SejongYouthContractError("application discovery form contract changed")

    classification, classification_reason = classify_sejong_youth_program(
        listed.title,
        listed.round_name,
        listed.source_category,
    )
    if classification == "exclude":
        return None, classification, classification_reason, has_application
    provider = _clean(_target_value(target, "provider")).upper()
    education = classification == "education"
    domain_category = "교육·강좌" if education else "체험·견학"
    service_group = "공공강좌" if education else "체험"
    normalized_status = _status(listed.status)
    if has_application:
        application_type = "ONLINE_RESERVATION"
    elif normalized_status == "OPEN" and listed.method == "오프라인":
        application_type = "OFFLINE_APPLY"
    else:
        application_type = "INFO_ONLY"
    capacity_total = _integer(values[5], "모집정원")
    waiting_total = _integer(values[6], "대기정원")
    capacity_current = _integer(values[7], "신청자")
    row = {
        "provider": provider,
        "provider_course_id": f"{provider}:youth:{listed.program_no}:{listed.round_no}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": listed.title,
        "branch": listed.branch,
        "branch_code": _branch_code(provider, listed.branch),
        "preserve_branch": True,
        "branch_url": SEJONG_YOUTH_CANONICAL_URL,
        "category": domain_category,
        "program_type": "교육" if education else "체험",
        "raw_url": listed.raw_url,
        "application_url": listed.raw_url if has_application else "",
        "application_type": application_type,
        "application_method_raw": listed.method,
        "reservation_available": has_application,
        "status": normalized_status,
        "fee": pairs["참가비"],
        "period": f"{listed.start.date().isoformat()} ~ {listed.end.date().isoformat()}",
        "start_date": listed.start.date().isoformat(),
        "end_date": listed.end.date().isoformat(),
        "apply_period": f"{listed.apply_start.date().isoformat()} ~ {listed.apply_end.date().isoformat()}",
        "apply_start": listed.apply_start.date().isoformat(),
        "apply_end": listed.apply_end.date().isoformat(),
        "schedule_raw": listed.education_datetime,
        "target": listed.target,
        "capacity": f"{capacity_current}/{capacity_total}",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "venue_name": pairs["활동장소"],
        "collection_category": "공공예약",
        "domain_category": domain_category,
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": service_group,
        "service_group_policy": "locked",
        "collection_type": SEJONG_YOUTH_PARSER,
        "municipality_code": SEJONG_YOUTH_MUNICIPALITY_CODE,
        "municipality_full_name": SEJONG_YOUTH_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": SEJONG_YOUTH_PARSER,
            "ownership_scope": SEJONG_YOUTH_OWNERSHIP_SCOPE,
            "source_program_no": listed.program_no,
            "source_round_no": listed.round_no,
            "source_status": listed.status,
            "source_method": listed.method,
            "source_category": listed.source_category,
            "source_page": listed.page,
            "source_position": listed.position,
            "source_round_name": listed.round_name,
            "source_reversed_end_date_corrected": listed.source_date_corrected,
            "classification": classification,
            "classification_reason": classification_reason,
            "waiting_capacity_total": waiting_total,
            "detail_verified": True,
            "application_control_present": has_application,
            "application_form_discovered_not_called": True,
            "application_endpoint_fetched": False,
            "login_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "pii_endpoint_fetched": False,
        },
    }
    return row, classification, classification_reason, has_application


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_total": 0,
        "unique_source_count": 0,
        "source_duplicate_count": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "education_count": 0,
        "experience_count": 0,
        "excluded_non_program_count": 0,
        "excluded_reason_counts": {},
        "classification_reason_counts": {},
        "excluded_samples": [],
        "source_status_counts": {},
        "source_category_counts": {},
        "branch_counts": {},
        "application_control_count": 0,
        "sentinel_page": 0,
        "pagination_complete": False,
        "stable_first_page": False,
        "stable_final_page": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "canonical_provider": SEJONG_YOUTH_PROVIDER,
        "canonical_url": SEJONG_YOUTH_CANONICAL_URL,
        "ownership_scope": SEJONG_YOUTH_OWNERSHIP_SCOPE,
        "covered_municipalities": [
            {
                "code": SEJONG_YOUTH_MUNICIPALITY_CODE,
                "sido": SEJONG_YOUTH_MUNICIPALITY_NAME,
                "sigungu": SEJONG_YOUTH_MUNICIPALITY_NAME,
                "full_name": SEJONG_YOUTH_MUNICIPALITY_NAME,
            }
        ],
        "tls_intermediate_sha256": SEJONG_YOUTH_INTERMEDIATE_SHA256,
        "tls_verification_disabled": False,
        "notice_board_requests": 0,
        "application_endpoint_requests": 0,
        "authentication_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "http_methods": ["POST", "GET"],
        "configured_collection_error": "",
    }


def collect_sejong_youth_programs(
    target: Any,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    *,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic current/future mixed Sejong youth snapshot."""

    meta = _base_meta()
    if not is_sejong_youth_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact Sejong Nurida programme ledger"
        )
        return [], SEJONG_YOUTH_PARSER, meta
    try:
        timeout_value = int(timeout)
        page_cap = int(max_pages)
        detail_cap = int(detail_limit)
        cutoff = _today(today)
    except (TypeError, ValueError, SejongYouthContractError) as exc:
        meta["configured_collection_error"] = f"invalid arguments: {_clean(exc)}"
        return [], SEJONG_YOUTH_PARSER, meta
    if timeout_value < 1 or page_cap < 1 or detail_cap < 0:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "invalid timeout/max_pages/detail_limit cap"
        return [], SEJONG_YOUTH_PARSER, meta

    requester: Optional[_Requester] = None
    first_page: Optional[ListPage] = None
    final_page: Optional[ListPage] = None
    unique_rows: list[ListedProgram] = []
    output_rows: list[dict[str, Any]] = []
    excluded: list[tuple[ListedProgram, str]] = []
    classifications: Counter[str] = Counter()
    classification_reasons: Counter[str] = Counter()
    excluded_reasons: Counter[str] = Counter()
    application_controls = 0
    try:
        # One first page, all remaining declared pages, one empty sentinel,
        # every unique detail, then stable first/final edge rechecks.
        requester = _Requester(
            session_factory or _default_session_factory,
            timeout_value,
            request_cap=page_cap + detail_cap + 8,
        )
        first_page = _parse_list_page(
            requester.soup(
                SEJONG_YOUTH_CANONICAL_URL,
                kind="list",
                payload=sejong_youth_list_payload(1, cutoff),
            ),
            1,
            cutoff,
        )
        required_list_requests = first_page.last_page + 3
        if required_list_requests > page_cap:
            meta["source_cap_reached"] = True
            raise SejongYouthContractError(
                f"max_pages={page_cap} is below required list requests={required_list_requests}"
            )
        pages: list[ListPage] = [first_page]
        for page_number in range(2, first_page.last_page + 1):
            pages.append(
                _parse_list_page(
                    requester.soup(
                        SEJONG_YOUTH_CANONICAL_URL,
                        kind="list",
                        payload=sejong_youth_list_payload(page_number, cutoff),
                    ),
                    page_number,
                    cutoff,
                )
            )
        final_page = pages[-1]
        sentinel_number = first_page.last_page + 1
        sentinel = _parse_list_page(
            requester.soup(
                SEJONG_YOUTH_CANONICAL_URL,
                kind="list",
                payload=sejong_youth_list_payload(sentinel_number, cutoff),
            ),
            sentinel_number,
            cutoff,
        )
        meta["sentinel_page"] = sentinel_number
        if (
            sentinel.total != first_page.total
            or sentinel.last_page != first_page.last_page
            or sentinel.rows
            or not sentinel.no_data
        ):
            raise SejongYouthContractError("page after declared end is not an exact empty sentinel")
        for page in pages:
            if page.total != first_page.total or page.last_page != first_page.last_page:
                raise SejongYouthContractError("declared list totals changed during pagination")
            expected_count = (
                SEJONG_YOUTH_PAGE_SIZE
                if page.page < first_page.last_page
                else first_page.total - SEJONG_YOUTH_PAGE_SIZE * (first_page.last_page - 1)
            )
            if len(page.rows) != expected_count or page.no_data:
                raise SejongYouthContractError(
                    f"page {page.page} row count does not reconcile to the declared total"
                )
        raw_rows = [row for page in pages for row in page.rows]
        if len(raw_rows) != first_page.total:
            raise SejongYouthContractError("declared total does not match paginated source rows")
        by_identity: dict[str, ListedProgram] = {}
        duplicate_count = 0
        for listed in raw_rows:
            previous = by_identity.get(listed.identity)
            if previous is None:
                by_identity[listed.identity] = listed
            else:
                duplicate_count += 1
                if previous.source_signature() != listed.source_signature():
                    raise SejongYouthContractError(
                        f"duplicate source identity {listed.identity} has conflicting fields"
                    )
        unique_rows = list(by_identity.values())
        if len(unique_rows) > detail_cap:
            meta["source_cap_reached"] = True
            raise SejongYouthContractError(
                f"detail_limit={detail_cap} is below unique current rows={len(unique_rows)}"
            )

        meta.update(
            {
                "pages": first_page.last_page,
                "source_total": len(raw_rows),
                "unique_source_count": len(unique_rows),
                "source_duplicate_count": duplicate_count,
                "current_source_count": len(unique_rows),
                "detail_attempts": len(unique_rows),
                "pagination_complete": True,
                "source_status_counts": dict(
                    sorted(Counter(row.status for row in unique_rows).items())
                ),
                "source_category_counts": dict(
                    sorted(Counter(row.source_category for row in unique_rows).items())
                ),
            }
        )

        for listed in unique_rows:
            try:
                soup = requester.soup(listed.raw_url, kind="detail")
                row, classification, reason, has_application = _row_from_detail(
                    target,
                    listed,
                    soup,
                )
            except Exception as exc:
                raise SejongYouthContractError(
                    f"detail {listed.identity}: {_clean(exc)}"
                ) from exc
            classifications[classification] += 1
            classification_reasons[reason] += 1
            application_controls += int(has_application)
            if row is None:
                excluded.append((listed, reason))
                excluded_reasons[reason] += 1
            else:
                output_rows.append(row)

        repeated_first = _parse_list_page(
            requester.soup(
                SEJONG_YOUTH_CANONICAL_URL,
                kind="list",
                payload=sejong_youth_list_payload(1, cutoff),
            ),
            1,
            cutoff,
        )
        repeated_final = _parse_list_page(
            requester.soup(
                SEJONG_YOUTH_CANONICAL_URL,
                kind="list",
                payload=sejong_youth_list_payload(first_page.last_page, cutoff),
            ),
            first_page.last_page,
            cutoff,
        )
        if _page_signature(repeated_first) != _page_signature(first_page):
            raise SejongYouthContractError("first-page boundary changed during detail crawl")
        if _page_signature(repeated_final) != _page_signature(final_page):
            raise SejongYouthContractError("final-page boundary changed during detail crawl")

        selected_dedupe = dedupe_rows or _dedupe_default
        deduped = list(selected_dedupe(output_rows))
        if len(deduped) != len(output_rows):
            raise SejongYouthContractError(
                f"dedupe changed complete returned count {len(output_rows)} to {len(deduped)}"
            )
        output_rows = deduped
        branch_counts = Counter(row["branch"] for row in output_rows)
        meta.update(
            {
                "request_count": requester.requests,
                "list_requests": requester.list_requests,
                "detail_pages": requester.detail_requests,
                "returned_count": len(output_rows),
                "education_count": classifications["education"],
                "experience_count": classifications["experience"],
                "excluded_non_program_count": classifications["exclude"],
                "excluded_reason_counts": dict(sorted(excluded_reasons.items())),
                "classification_reason_counts": dict(
                    sorted(classification_reasons.items())
                ),
                "excluded_samples": [
                    {
                        "identity": listed.identity,
                        "title": listed.title,
                        "reason": reason,
                    }
                    for listed, reason in excluded[:25]
                ],
                "branch_counts": dict(sorted(branch_counts.items())),
                "application_control_count": application_controls,
                "stable_first_page": True,
                "stable_final_page": True,
                "details_complete": requester.detail_requests == len(unique_rows),
                "snapshot_complete": True,
                "no_current_data": not output_rows,
                "no_current_reason": (
                    "official current/future ledger contains no eligible education or experience rows"
                    if not output_rows
                    else ""
                ),
            }
        )
        return output_rows, SEJONG_YOUTH_PARSER, meta
    except Exception as exc:
        if requester is not None:
            meta.update(
                {
                    "request_count": requester.requests,
                    "list_requests": requester.list_requests,
                    "detail_pages": requester.detail_requests,
                }
            )
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["snapshot_complete"] = False
        return [], SEJONG_YOUTH_PARSER, meta
    finally:
        if requester is not None:
            requester.close()


collect = collect_sejong_youth_programs
