"""Fail-closed collectors for Gunpo's independent public education owners.

The municipal reservation portal, information-literacy site, culture
foundation, media centre, library, urban corporation, youth foundation,
Picture Book Dream Maru and Youth Flying each publish a separate public
catalogue.  This module keeps those identity namespaces separate while using a
single exact-target dispatcher.

Every owner is read with complete pagination, an immediate post-boundary empty
sentinel and stable boundary rechecks.  Details are requested for every
current/future course.  Only public list/detail GET routes are allowlisted;
application, login, applicant, payment, attachment and account routes are never
called.  Free text, instructor/contact data and application schemas are
discarded rather than persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from functools import lru_cache
import hashlib
import json
import math
import re
import ssl
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, TypeVar
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from utils.outbound_http import OutboundRequestBlocked, SafeSession, _PinnedHTTPAdapter


GUNPO_MUNICIPALITY_CODE = "4141000000"
GUNPO_MUNICIPALITY_NAME = "경기도 군포시"

GUNPO_CITY_PROVIDER = "MUNI_CTM_GUNPO_GO_KR_2ADC8672"
GUNPO_INFO_PROVIDER = "MUNI_SSO_GUNPO_GO_KR_C6EB5B7F"
GUNPO_FOUNDATION_PROVIDER = "MUNI_WWW_GUNPOCF_OR_KR_72C2BA1D"
GUNPO_MEDIA_PROVIDER = "MUNI_WWW_GPMEDIA_OR_KR_6517BB69"
GUNPO_LIBRARY_PROVIDER = "MUNI_WWW_GUNPOLIB_GO_KR_6657561E"
GUNPO_URBAN_PROVIDER = "MUNI_WWW_GUNPOUC_OR_KR_C6BD9C41"
GUNPO_YOUTH_PROVIDER = "MUNI_WWW_GPYF_OR_KR_85203167"
GUNPO_PICTUREBOOK_PROVIDER = "MUNI_WWW_GUNPO_GO_KR_FE43B335"
GUNPO_FLYING_PROVIDER = "MUNI_WWW_GUNPOYCF_OR_KR_ED267E43"

GUNPO_CITY_URL = "https://ctm.gunpo.go.kr/portal/webEdcLctreList.do?key=1008274&rep=1"
GUNPO_INFO_URL = "https://sso.gunpo.go.kr/infoedu/selectEdcLctreWebList.do?key=2115&eduCourseGrupCode=EDCGRU01"
GUNPO_FOUNDATION_URL = "https://www.gunpocf.or.kr/cf/lctre/regularLctreList"
GUNPO_MEDIA_URL = "https://www.gpmedia.or.kr/front/edcExprn/edcRequestList"
GUNPO_LIBRARY_URL = "https://www.gunpolib.go.kr/#/libprg/culture-lecture"
GUNPO_LIBRARY_NORMALIZED_URL = "https://www.gunpolib.go.kr/"
GUNPO_URBAN_URL = "https://www.gunpouc.or.kr/fmcs/155"
GUNPO_YOUTH_URL = "https://www.gpyf.or.kr/yeyak/fmcs/1"
GUNPO_PICTUREBOOK_URL = "https://www.gunpo.go.kr/picturebook/ko/M000000141/edu/progrm/list"
GUNPO_FLYING_URL = "https://www.gunpoycf.or.kr/program"

GUNPO_PARSER = (
    "gunpo_nine_owner_complete_ledgers+post_boundary_empty_sentinels+"
    "stable_boundary_rechecks+all_current_public_details+official_branches+"
    "education_partition_only+get_only_no_application_login_pii_endpoints"
)
GUNPO_PAGE_SIZE = 1000
GUNPO_MAX_PAGES = 250
GUNPO_MAX_DETAILS = 1_500
GUNPO_MAX_WORKERS = 16
GUNPO_MAX_BYTES = 4_000_000

GUNPO_OWNERS: Mapping[str, Mapping[str, str]] = {
    "city": {"provider": GUNPO_CITY_PROVIDER, "url": GUNPO_CITY_URL},
    "info": {"provider": GUNPO_INFO_PROVIDER, "url": GUNPO_INFO_URL},
    "foundation": {
        "provider": GUNPO_FOUNDATION_PROVIDER,
        "url": GUNPO_FOUNDATION_URL,
    },
    "media": {"provider": GUNPO_MEDIA_PROVIDER, "url": GUNPO_MEDIA_URL},
    "library": {"provider": GUNPO_LIBRARY_PROVIDER, "url": GUNPO_LIBRARY_URL},
    "urban": {"provider": GUNPO_URBAN_PROVIDER, "url": GUNPO_URBAN_URL},
    "youth": {"provider": GUNPO_YOUTH_PROVIDER, "url": GUNPO_YOUTH_URL},
    "picturebook": {
        "provider": GUNPO_PICTUREBOOK_PROVIDER,
        "url": GUNPO_PICTUREBOOK_URL,
    },
    "flying": {"provider": GUNPO_FLYING_PROVIDER, "url": GUNPO_FLYING_URL},
}

GUNPO_DUPLICATE_ALIASES: Mapping[str, str] = {
    "MUNI_WWW_GUNPO_GO_KR_09F1E7BC": GUNPO_CITY_PROVIDER,
    "https://www.gunpo.go.kr/portal/index.do": GUNPO_CITY_URL,
    "https://infoedu.gunpo.go.kr/portal/index.do": GUNPO_CITY_URL,
    "https://www.gunpo.go.kr/edui/index.do": GUNPO_CITY_URL,
}

GUNPO_EXCLUDED_BOUNDARIES: Mapping[str, str] = {
    "city_event_recruitment": (
        "mixed 행사/모집 ledger; education records cannot be selected by a stable source category"
    ),
    "bangjja_home": "navigation shell whose application link enters the mixed event ledger",
    "youth_notice_boards": "notice/attachment duplicates of the youth FMCS catalogue",
    "industrial_promotion": "facility rental and business notices, no public course ledger",
    "young_space_project_boards": "notice duplicates; canonical structured owner is /program",
}

GUNPO_FOUNDATION_BRANCHES: Mapping[str, tuple[str, str]] = {
    "21200002": ("100004", "군포시평생학습마을"),
    "21200004": ("100005", "군포시평생학습원"),
    "21200003": ("100003", "군포시생활문화센터"),
    "21200001": ("100002", "군포문화예술회관"),
}
GUNPO_CITY_BRANCHES = frozenset(
    {
        "군포1동 주민자치회",
        "군포2동 주민자치회",
        "산본1동 주민자치회",
        "산본2동 주민자치회",
        "금정동 주민자치회",
        "재궁동 주민자치회",
        "오금동 주민자치회",
        "수리동 주민자치회",
        "궁내동 주민자치센터",
        "광정동 주민자치회",
        "대야동 주민자치회",
        "송부동 주민자치회",
        "노루목작은도서관",
    }
)
GUNPO_LIBRARY_BRANCHES = frozenset(
    {
        "누리천문대",
        "당동도서관",
        "대야도서관",
        "부곡도서관",
        "산본도서관",
        "어린이도서관",
        "중앙도서관",
    }
)
GUNPO_URBAN_BRANCHES: Mapping[str, str] = {
    "GUNPO01": "군포국민체육센터",
    "GUNPO02": "시민체육광장",
    "GUNPO03": "생활체육시설(송죽다목적체육관/소규모체육시설)",
    "GUNPO06": "송정복합체육센터",
}
GUNPO_URBAN_ARCHIVE_BRANCHES: Mapping[str, str] = {
    **GUNPO_URBAN_BRANCHES,
    # Retired from the live company selector but still present in the complete
    # ended-course ledger under this exact source-published official name.
    "GUNPO05": "군포도시공사 부곡체육시설",
}
GUNPO_YOUTH_BRANCHES: Mapping[str, str] = {"GUNPOYF01": "군포시청소년수련관"}
GUNPO_YOUTH_CATEGORIES: Mapping[str, str] = {
    "1000000000": "수영사업",
    "1010000000": "건강체육사업",
    "1020000000": "교육사업",
    "1030000000": "교육사업-단기프로그램",
    "1070000000": "교육사업-방학특강",
    "1080000000": "건강체육-방학특강",
    "1100000000": "건강체육-단기프로그램",
}

GUNPO_OWNER_AUDIT_BASELINE: Mapping[str, Mapping[str, Any]] = {
    "city": {"source": 641, "current": 156, "excluded_test": 1},
    "info": {"source": 13, "current": 13},
    "foundation": {"source": 224, "current": 204},
    "media": {"source": 205, "current": 6},
    "library": {"source": 5816, "current": 96},
    "urban": {"source": 318, "current": 5},
    "youth": {"source": 266, "current": 130},
    "picturebook": {"source": 59, "current": 0},
    "flying": {"source": 99, "current_education": 3},
}


class GunpoContractError(ValueError):
    """Raised when an official Gunpo owner violates its audited contract."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
T = TypeVar("T")

_SPACE = re.compile(r"\s+")
_DATE = re.compile(r"(?<!\d)(20\d{2})[.-](\d{2})[.-](\d{2})(?!\d)")
_SHORT_DATE = re.compile(r"(?<!\d)(\d{2})[.](\d{2})[.](\d{2})(?!\d)")
_INT = re.compile(r"\d[\d,]*")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")
_RESIDENT_ID = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "teacher",
        "content",
        "description",
        "attachments",
        "attachment_url",
        "image_url",
        "request_form",
        "applicant",
    }
)

# www.gpmedia.or.kr currently omits this issuing intermediate from its TLS
# handshake.  Supplying the official Sectigo intermediate preserves full leaf,
# hostname and root validation; verification is never disabled on the managed
# production session.
_SECTIGO_RSA_DOMAIN_VALIDATION_SECURE_SERVER_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIGEzCCA/ugAwIBAgIQfVtRJrR2uhHbdBYLvFMNpzANBgkqhkiG9w0BAQwFADCB
iDELMAkGA1UEBhMCVVMxEzARBgNVBAgTCk5ldyBKZXJzZXkxFDASBgNVBAcTC0pl
cnNleSBDaXR5MR4wHAYDVQQKExVUaGUgVVNFUlRSVVNUIE5ldHdvcmsxLjAsBgNV
BAMTJVVTRVJUcnVzdCBSU0EgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkwHhcNMTgx
MTAyMDAwMDAwWhcNMzAxMjMxMjM1OTU5WjCBjzELMAkGA1UEBhMCR0IxGzAZBgNV
BAgTEkdyZWF0ZXIgTWFuY2hlc3RlcjEQMA4GA1UEBxMHU2FsZm9yZDEYMBYGA1UE
ChMPU2VjdGlnbyBMaW1pdGVkMTcwNQYDVQQDEy5TZWN0aWdvIFJTQSBEb21haW4g
VmFsaWRhdGlvbiBTZWN1cmUgU2VydmVyIENBMIIBIjANBgkqhkiG9w0BAQEFAAOC
AQ8AMIIBCgKCAQEA1nMz1tc8INAA0hdFuNY+B6I/x0HuMjDJsGz99J/LEpgPLT+N
TQEMgg8Xf2Iu6bhIefsWg06t1zIlk7cHv7lQP6lMw0Aq6Tn/2YHKHxYyQdqAJrkj
eocgHuP/IJo8lURvh3UGkEC0MpMWCRAIIz7S3YcPb11RFGoKacVPAXJpz9OTTG0E
oKMbgn6xmrntxZ7FN3ifmgg0+1YuWMQJDgZkW7w33PGfKGioVrCSo1yfu4iYCBsk
Haswha6vsC6eep3BwEIc4gLw6uBK0u+QDrTBQBbwb4VCSmT3pDCg/r8uoydajotY
uK3DGReEY+1vVv2Dy2A0xHS+5p3b4eTlygxfFQIDAQABo4IBbjCCAWowHwYDVR0j
BBgwFoAUU3m/WqorSs9UgOHYm8Cd8rIDZsswHQYDVR0OBBYEFI2MXsRUrYrhd+mb
+ZsF4bgBjWHhMA4GA1UdDwEB/wQEAwIBhjASBgNVHRMBAf8ECDAGAQH/AgEAMB0G
A1UdJQQWMBQGCCsGAQUFBwMBBggrBgEFBQcDAjAbBgNVHSAEFDASMAYGBFUdIAAw
CAYGZ4EMAQIBMFAGA1UdHwRJMEcwRaBDoEGGP2h0dHA6Ly9jcmwudXNlcnRydXN0
LmNvbS9VU0VSVHJ1c3RSU0FDZXJ0aWZpY2F0aW9uQXV0aG9yaXR5LmNybDB2Bggr
BgEFBQcBAQRqMGgwPwYIKwYBBQUHMAKGM2h0dHA6Ly9jcnQudXNlcnRydXN0LmNv
bS9VU0VSVHJ1c3RSU0FBZGRUcnVzdENBLmNydDAlBggrBgEFBQcwAYYZaHR0cDov
L29jc3AudXNlcnRydXN0LmNvbTANBgkqhkiG9w0BAQwFAAOCAgEAMr9hvQ5Iw0/H
ukdN+Jx4GQHcEx2Ab/zDcLRSmjEzmldS+zGea6TvVKqJjUAXaPgREHzSyrHxVYbH
7rM2kYb2OVG/Rr8PoLq0935JxCo2F57kaDl6r5ROVm+yezu/Coa9zcV3HAO4OLGi
H19+24rcRki2aArPsrW04jTkZ6k4Zgle0rj8nSg6F0AnwnJOKf0hPHzPE/uWLMUx
RP0T7dWbqWlod3zu4f+k+TY4CFM5ooQ0nBnzvg6s1SQ36yOoeNDT5++SR2RiOSLv
xvcRviKFxmZEJCaOEDKNyJOuB56DPi/Z+fVGjmO+wea03KbNIaiGCpXZLoUmGv38
sbZXQm2V0TP2ORQGgkE49Y9Y3IBbpNV9lXj9p5v//cWoaasm56ekBYdbqbe4oyAL
l6lFhd2zi+WJN44pDfwGF/Y4QA5C5BIG+3vzxhFoYt/jmPQT2BVPi7Fp2RBgvGQq
6jG35LWjOhSbJuMLe/0CjraZwTiXWTb2qHSihrZe68Zk6s+go/lunrotEbaGmAhY
LcmsJWTyXnW0OMGuf1pGg+pRyrbxmRE1a6Vqe8YAsOf4vmSyrcjC8azjUeqkk+B5
yOGBQMkKW+ESPMFgKuOXwIlCypTPRpgSabuY0MLTDXJLR27lk8QyKGOHQ+SwMj4K
00u/I5sUKUErmgQfky3xxzlIPK1aEn8=
-----END CERTIFICATE-----"""


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _exact_url(value: Any, expected: str) -> bool:
    left, right = urlparse(_clean(value)), urlparse(expected)
    return (
        left.scheme == right.scheme
        and left.netloc == right.netloc
        and left.path == right.path
        and left.params == right.params == ""
        and left.fragment == right.fragment
        and parse_qsl(left.query, keep_blank_values=True) == parse_qsl(right.query, keep_blank_values=True)
    )


def owner_for_target(target: Any) -> str:
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    if provider == GUNPO_LIBRARY_PROVIDER and url == GUNPO_LIBRARY_NORMALIZED_URL:
        return "library"
    for owner, config in GUNPO_OWNERS.items():
        if provider == config["provider"] and _exact_url(url, config["url"]):
            return owner
    return ""


def is_gunpo_education_target(target: Any) -> bool:
    return bool(owner_for_target(target))


is_target = is_gunpo_education_target


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _parse_date(value: str) -> date:
    match = _DATE.search(_clean(value))
    if not match:
        raise GunpoContractError(f"missing date: {_clean(value)[:80]}")
    return date(*(int(part) for part in match.groups()))


def _parse_short_dates(value: str) -> tuple[date, date]:
    matches = _SHORT_DATE.findall(_clean(value))
    if len(matches) != 2:
        raise GunpoContractError("two abbreviated dates required")
    parsed = tuple(date(2000 + int(y), int(m), int(d)) for y, m, d in matches)
    return parsed[0], parsed[1]


def _all_dates(value: str) -> list[date]:
    return [date(int(y), int(m), int(d)) for y, m, d in _DATE.findall(_clean(value))]


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise GunpoContractError(f"{label} must be an integer")
    try:
        parsed = int(str(value).replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise GunpoContractError(f"{label} must be an integer") from exc
    if parsed < 0:
        raise GunpoContractError(f"{label} must be non-negative")
    return parsed


def _response_bytes(response: Any) -> bytes:
    status = int(getattr(response, "status_code", 200))
    if status != 200 or getattr(response, "history", []):
        raise GunpoContractError(f"unexpected HTTP response {status}")
    content = getattr(response, "content", b"")
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not content or len(content) > GUNPO_MAX_BYTES:
        raise GunpoContractError("empty or oversized public response")
    return bytes(content)


def _soup(response: Any) -> BeautifulSoup:
    return BeautifulSoup(_response_bytes(response), "html.parser")


def _json(response: Any) -> Any:
    _response_bytes(response)
    try:
        return response.json()
    except Exception:
        try:
            return json.loads(_response_bytes(response).decode("utf-8"))
        except Exception as exc:
            raise GunpoContractError("invalid JSON response") from exc


@lru_cache(maxsize=1)
def _media_certificate_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=_SECTIGO_RSA_DOMAIN_VALIDATION_SECURE_SERVER_CA_PEM)
    return context


class _GunpoMediaPinnedAdapter(_PinnedHTTPAdapter):
    """Keep SafeSession's DNS pinning while completing gpmedia's chain."""

    def get_connection_with_tls_context(
        self,
        request: requests.PreparedRequest,
        verify: Any,
        proxies: Optional[dict[str, str]] = None,
        cert: Any = None,
    ) -> Any:
        if proxies and any(proxies.values()):
            raise OutboundRequestBlocked("Outbound HTTP proxies are not permitted")
        selected_address = getattr(request, "_mooncen_selected_address", "")
        original_hostname = getattr(request, "_mooncen_original_hostname", "")
        if not selected_address or original_hostname != "www.gpmedia.or.kr":
            raise OutboundRequestBlocked("Gunpo media destination was not validated")
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        host_params["host"] = selected_address
        pool_kwargs["assert_hostname"] = original_hostname
        pool_kwargs["server_hostname"] = original_hostname
        pool_kwargs["ssl_context"] = _media_certificate_context()
        return self.poolmanager.connection_from_host(
            **host_params,
            pool_kwargs=pool_kwargs,
        )


def _media_managed_session(factory: SessionFactory) -> Any:
    current = factory()
    if isinstance(current, SafeSession):
        current.mount("https://", _GunpoMediaPinnedAdapter(max_retries=0))
    return current


def _raw_session(owner: str) -> SafeSession:
    session = SafeSession()
    if owner == "media":
        session.mount("https://", _GunpoMediaPinnedAdapter(max_retries=0))
    session.headers.update(
        {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"),
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    last_error: Optional[Exception] = None
    for _attempt in range(3):
        try:
            return session.get(url, timeout=timeout, allow_redirects=False)
        except requests.RequestException as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _query(url: str) -> tuple[Any, list[tuple[str, str]], dict[str, str]]:
    parsed = urlparse(url)
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=20)
    except ValueError as exc:
        raise GunpoContractError("malformed request query") from exc
    if len(pairs) != len({key for key, _ in pairs}):
        raise GunpoContractError("duplicate request query key")
    return parsed, pairs, dict(pairs)


def _numeric(value: str, *, zero: bool = False) -> bool:
    return bool(re.fullmatch(r"\d+", value)) and (zero or int(value) > 0)


def _classify_url(owner: str, url: str) -> str:
    parsed, _pairs, query = _query(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.params or parsed.fragment:
        raise GunpoContractError("request escaped exact HTTPS public boundary")
    host, path = (parsed.hostname or "").lower(), parsed.path
    if owner == "city":
        if host != "ctm.gunpo.go.kr":
            raise GunpoContractError("city host drift")
        if path == "/portal/webEdcLctreList.do":
            if (
                set(query) != {"key", "rep", "pageUnit", "pageIndex"}
                or (query["key"], query["rep"], query["pageUnit"]) != ("1008274", "1", "1000")
                or not _numeric(query["pageIndex"])
            ):
                raise GunpoContractError("city list binding drift")
            return "list"
        if path == "/portal/edcLctreView.do":
            if (
                set(query) != {"key", "searchLctreKey"}
                or query["key"] != "1008274"
                or not _numeric(query["searchLctreKey"])
            ):
                raise GunpoContractError("city detail binding drift")
            return "detail"
    elif owner == "info":
        if host != "sso.gunpo.go.kr":
            raise GunpoContractError("information owner host drift")
        if path == "/infoedu/selectEdcLctreWebList.do":
            required = {"key", "eduCourseGrupCode", "pageUnit", "pageIndex"}
            if (
                set(query) != required
                or query["key"] != "2115"
                or query["eduCourseGrupCode"] != "EDCGRU01"
                or query["pageUnit"] != "1000"
                or not _numeric(query["pageIndex"])
            ):
                raise GunpoContractError("information list binding drift")
            return "list"
        if path == "/infoedu/selectEdcLctreWebView.do":
            required = {"key", "eduCourseGrupCode", "edcLctreNo"}
            if (
                set(query) != required
                or query["key"] != "2115"
                or query["eduCourseGrupCode"] != "EDCGRU01"
                or not _numeric(query["edcLctreNo"])
            ):
                raise GunpoContractError("information detail binding drift")
            return "detail"
    elif owner == "foundation":
        if host != "www.gunpocf.or.kr":
            raise GunpoContractError("foundation host drift")
        if path == "/cf/lctre/regularLctreList":
            required = {"pageIndex", "searchAgentCode", "searchGbCode1", "searchGbCode2", "searchText"}
            if (
                set(query) != required
                or query["searchAgentCode"] not in GUNPO_FOUNDATION_BRANCHES
                or query["searchGbCode1"]
                or query["searchGbCode2"]
                or query["searchText"]
                or not _numeric(query["pageIndex"])
            ):
                raise GunpoContractError("foundation list binding drift")
            return "list"
        if path.startswith("/cf/lctre/regularLctreView/"):
            required = {"siteCd", "lctreInfoNo", "agentCode", "gbCode1", "gbCode2", "suKangNo"}
            agent = query.get("agentCode", "")
            numeric_keys = ("siteCd", "agentCode", "gbCode1", "gbCode2", "suKangNo")
            if (
                set(query) != required
                or agent not in GUNPO_FOUNDATION_BRANCHES
                or path.rsplit("/", 1)[-1] != query["siteCd"]
                or query["siteCd"] != GUNPO_FOUNDATION_BRANCHES[agent][0]
                or not all(_numeric(query[key]) for key in numeric_keys)
                or (query["lctreInfoNo"] and not _numeric(query["lctreInfoNo"]))
            ):
                raise GunpoContractError("foundation detail binding drift")
            return "detail"
    elif owner == "media":
        if host != "www.gpmedia.or.kr":
            raise GunpoContractError("media host drift")
        if path == "/front/edcExprn/edcRequestList":
            if query and (set(query) != {"pageIndex"} or not _numeric(query["pageIndex"])):
                raise GunpoContractError("media list binding drift")
            return "list"
        if path == "/front/edcExprn/edcRequestView":
            if set(query) != {"mediaEdcNo", "pageIndex"} or not all(_numeric(query[key]) for key in query):
                raise GunpoContractError("media detail binding drift")
            return "detail"
    elif owner == "library":
        if host != "www.gunpolib.go.kr" or not path.startswith("/pyxis-api/1/library-programs"):
            raise GunpoContractError("library API boundary drift")
        if path == "/pyxis-api/1/library-programs":
            required = {"libraryProgramTypeId", "orderBy", "hideBranch", "hideContents", "hideReview", "offset", "max"}
            if (
                set(query) != required
                or query["libraryProgramTypeId"] not in {"1", "3"}
                or query["orderBy"] != "ORDER BY receiptBeginDate DESC"
                or any(query[key] != "true" for key in ("hideBranch", "hideContents", "hideReview"))
                or not _numeric(query["offset"], zero=True)
                or query["max"] != "1000"
            ):
                raise GunpoContractError("library list binding drift")
            return "list"
        if re.fullmatch(r"/pyxis-api/1/library-programs/[1-9]\d*", path) and not query:
            return "detail"
    elif owner in {"urban", "youth"}:
        expected_host = "www.gunpouc.or.kr" if owner == "urban" else "www.gpyf.or.kr"
        prefix = "" if owner == "urban" else "/yeyak"
        if host != expected_host:
            raise GunpoContractError("FMCS host drift")
        if path == prefix + "/rest/common/company" and not query:
            return "registry"
        if path == prefix + "/rest/common/category":
            if owner != "youth" or set(query) != {"company_code"} or query["company_code"] != "GUNPOYF01":
                raise GunpoContractError("FMCS category binding drift")
            return "registry"
        if path == prefix + "/rest/lecture/list":
            required = {
                "company_code",
                "search_type",
                "category_cd",
                "category_level",
                "class_nm",
                "train_day",
                "adult_gubn",
                "lecturer_nm",
                "page",
                "page_size",
            }
            if (
                set(query) != required
                or query["search_type"] not in {"R", "E"}
                or query["page_size"] != "1000"
                or not _numeric(query["page"])
            ):
                raise GunpoContractError("FMCS list binding drift")
            if owner == "urban":
                if query["company_code"] or query["category_cd"] or query["category_level"] != "9":
                    raise GunpoContractError("urban unfiltered list drift")
            elif (
                query["company_code"] != "GUNPOYF01"
                or query["category_cd"] not in GUNPO_YOUTH_CATEGORIES
                or query["category_level"] != "1"
            ):
                raise GunpoContractError("youth partition binding drift")
            if any(query[key] for key in ("class_nm", "train_day", "adult_gubn", "lecturer_nm")):
                raise GunpoContractError("FMCS search filter drift")
            return "list"
        expected_page = "/fmcs/155" if owner == "urban" else "/yeyak/fmcs/1"
        if path == expected_page:
            if (
                set(query) != {"action", "comcd", "classcd", "type"}
                or query["action"] != "read"
                or query["type"] != "R"
                or not re.fullmatch(r"[A-Z0-9]+", query["comcd"])
                or not re.fullmatch(r"\d+", query["classcd"])
            ):
                raise GunpoContractError("FMCS detail binding drift")
            return "detail"
    elif owner == "picturebook":
        if host != "www.gunpo.go.kr":
            raise GunpoContractError("picturebook host drift")
        base = "/picturebook/ko/M000000141/api/edu/progrm/"
        if path == base + "list":
            required = {"searchWrd", "searchBgngYmd", "searchEndYmd", "pageUnit", "pageIndex", "searchGroupCdIds"}
            if (
                set(query) != required
                or any(query[key] for key in ("searchWrd", "searchBgngYmd", "searchEndYmd", "searchGroupCdIds"))
                or query["pageUnit"] != "1000"
                or not _numeric(query["pageIndex"])
            ):
                raise GunpoContractError("picturebook list binding drift")
            return "list"
        if path == base + "view" and set(query) == {"eduProgrmNo"} and _numeric(query["eduProgrmNo"]):
            return "detail"
    elif owner == "flying":
        if host != "www.gunpoycf.or.kr":
            raise GunpoContractError("Flying host drift")
        if path == "/program":
            if query and (set(query) != {"page"} or not _numeric(query["page"])):
                raise GunpoContractError("Flying list binding drift")
            return "list"
        if re.fullmatch(r"/program/[1-9]\d*", path) and not query:
            return "detail"
    raise GunpoContractError(f"refusing unaudited {owner} route")


class _Requester:
    def __init__(
        self,
        owner: str,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        timeout: int,
        workers: int,
        meta: dict[str, Any],
        custom_fetcher: bool,
    ) -> None:
        self.owner = owner
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.timeout = timeout
        self.workers = workers
        self.meta = meta
        self.custom_fetcher = custom_fetcher
        self.session = session_factory()

    def _one(self, session: Any, url: str, expected: str) -> Any:
        kind = _classify_url(self.owner, url)
        if kind != expected:
            raise GunpoContractError(f"request kind {kind} is not {expected}")
        return self.fetcher(session, url, self.timeout)

    def get(self, url: str, kind: str) -> Any:
        response = self._one(self.session, url, kind)
        self._account(kind, 1)
        return response

    def many(self, urls: Sequence[str], kind: str) -> list[Any]:
        if not urls:
            return []
        if self.custom_fetcher or len(urls) == 1:
            results = [self._one(self.session, url, kind) for url in urls]
        else:

            def work(url: str) -> Any:
                session = self.session_factory()
                try:
                    return self._one(session, url, kind)
                finally:
                    close = getattr(session, "close", None)
                    if callable(close):
                        close()

            indexed: dict[int, Any] = {}
            with ThreadPoolExecutor(max_workers=min(self.workers, len(urls))) as pool:
                futures = {pool.submit(work, url): index for index, url in enumerate(urls)}
                for future in as_completed(futures):
                    indexed[futures[future]] = future.result()
            results = [indexed[index] for index in range(len(urls))]
        self._account(kind, len(results))
        return results

    def _account(self, kind: str, count: int) -> None:
        self.meta["source_requests"] += count
        self.meta[f"{kind}_requests"] += count

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _table_pairs(root: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in root.select("tr"):
        cells = row.select(":scope > th, :scope > td")
        index = 0
        while index + 1 < len(cells):
            if cells[index].name != "th":
                index += 1
                continue
            key = _clean(cells[index].get_text(" ", strip=True))
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if key:
                fields[key] = value
            index += 2
    return fields


def _dt_pairs(root: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for dt in root.select("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            fields[_clean(dt.get_text(" ", strip=True))] = _clean(dd.get_text(" ", strip=True))
    return fields


def _status(value: str) -> str:
    text = _clean(value)
    if any(token in text for token in ("폐강", "취소")):
        return "CANCELLED"
    if any(token in text for token in ("접수중", "모집 중", "접수가능", "모집중", "대기신청")):
        return "OPEN"
    if any(token in text for token in ("대기중", "접수대기", "접수예정")):
        return "SCHEDULED"
    if any(token in text for token in ("마감", "종료", "완료")):
        return "CLOSED"
    return "UNKNOWN"


def _base_output(owner: str, identity: str) -> dict[str, Any]:
    provider = GUNPO_OWNERS[owner]["provider"]
    return {
        "provider": provider,
        "municipality_code": GUNPO_MUNICIPALITY_CODE,
        "municipality_name": GUNPO_MUNICIPALITY_NAME,
        "municipality_full_name": GUNPO_MUNICIPALITY_NAME,
        "region": GUNPO_MUNICIPALITY_NAME,
        "provider_course_id": f"{provider}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "source_course_id": identity,
        "application_url": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "program_type": "강좌",
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_OUTPUT_KEYS:
        errors.append("forbidden PII/free-text key")
    safe_url_keys = {"source_url", "raw_url", "branch_url", "application_url"}
    payload = repr({key: value for key, value in row.items() if key not in safe_url_keys})
    if _PHONE.search(payload) or _EMAIL.search(payload) or _RESIDENT_ID.search(payload):
        errors.append("PII-like value escaped allowlist")
    return errors


def _signature(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def city_list_url(page: int) -> str:
    return "https://ctm.gunpo.go.kr/portal/webEdcLctreList.do?" + urlencode(
        (("key", "1008274"), ("rep", "1"), ("pageUnit", "1000"), ("pageIndex", str(page)))
    )


def city_detail_url(course_id: str) -> str:
    return "https://ctm.gunpo.go.kr/portal/edcLctreView.do?" + urlencode(
        (("key", "1008274"), ("searchLctreKey", str(course_id)))
    )


def _parse_city_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    root = soup.select_one("#contents") or soup
    text = _clean(root.get_text(" ", strip=True))
    total_match = re.search(r"총\s*([\d,]+)\s*건", text)
    if not total_match:
        raise GunpoContractError("city declared total missing")
    rows: list[dict[str, Any]] = []
    for tr in root.select("table tbody tr"):
        anchor = tr.select_one("a[href*='edcLctreView.do']")
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
        if anchor is None:
            continue
        parsed = urlparse(urljoin(GUNPO_CITY_URL, anchor.get("href", "")))
        values = dict(parse_qsl(parsed.query, keep_blank_values=True))
        identity = values.get("searchLctreKey", "")
        if not _numeric(identity) or len(cells) < 8:
            raise GunpoContractError("city row identity/schema drift")
        dates = _all_dates(cells[4])
        if len(dates) < 4 or len(dates) % 2:
            raise GunpoContractError(f"city {identity}: paired list dates required")
        # Some resident-centre rows prepend a two-date priority-registration
        # window.  The canonical application and education pairs are always
        # the final four dates and are revalidated on the public detail page.
        dates = dates[-4:]
        numbers = [int(x.replace(",", "")) for x in _INT.findall(cells[5])]
        rows.append(
            {
                "identity": identity,
                "title": _clean(anchor.get_text(" ", strip=True)),
                "raw_status": cells[0],
                "organizer": cells[2],
                "target": cells[3],
                "apply_start": dates[0],
                "apply_end": dates[1],
                "start": dates[2],
                "end": dates[3],
                "schedule": cells[4].split("강의시간", 1)[-1].lstrip(" :"),
                "capacity": numbers[0] if numbers else 0,
                "selection_fee": cells[6],
                "method": cells[7],
                "detail_url": city_detail_url(identity),
            }
        )
    return {"page": page, "total": int(total_match.group(1).replace(",", "")), "rows": rows}


def _normalize_city_branch(organizer: str, venue: str) -> str:
    branch = _clean(organizer)
    replacements = {
        "군포2동주민자치회": "군포2동 주민자치회",
        "산본 1동 주민자치회": "산본1동 주민자치회",
        "산본1동주민자치회": "산본1동 주민자치회",
        "산본2동주민자치회": "산본2동 주민자치회",
        "금정동주민자치회": "금정동 주민자치회",
        "광정동주민자치회": "광정동 주민자치회",
        "궁내동주민자치센터": "궁내동 주민자치센터",
        "대야동주민자치회": "대야동 주민자치회",
        "군포1동주민자치회": "군포1동 주민자치회",
        "오금동주민자치회": "오금동 주민자치회",
        "재궁동주민자치회": "재궁동 주민자치회",
        "수리동주민자치회": "수리동 주민자치회",
        "송부동주민자치회": "송부동 주민자치회",
    }
    if branch:
        normalized = replacements.get(branch, branch)
        if normalized not in GUNPO_CITY_BRANCHES:
            raise GunpoContractError(f"unaudited current city branch: {normalized}")
        return normalized
    venue = _clean(venue)
    if venue.startswith("노루목작은도서관"):
        return "노루목작은도서관"
    if venue.startswith("산본1동 커뮤니티센터"):
        return "산본1동 주민자치회"
    raise GunpoContractError("city row has no audited official branch")


def _city_detail(row: Mapping[str, Any], soup: BeautifulSoup) -> tuple[Optional[dict[str, Any]], int]:
    root = soup.select_one("#contents") or soup
    fields = _table_pairs(root)
    required = {"강좌영역", "강좌상태", "신청기간", "교육기간", "강의시간", "강의장소", "수강대상", "정원", "주최"}
    if not required.issubset(fields):
        raise GunpoContractError(f"city {row['identity']}: detail fields drift")
    dates = _all_dates(fields["신청기간"] + " " + fields["교육기간"])
    if len(dates) < 4 or dates[:4] != [row["apply_start"], row["apply_end"], row["start"], row["end"]]:
        raise GunpoContractError(f"city {row['identity']}: detail dates drift")
    text = _clean(root.get_text(" ", strip=True))
    if row["title"] not in text:
        raise GunpoContractError(f"city {row['identity']}: detail title drift")
    application_controls = sum(
        "Agree" in _clean(node.get("href")) or "신청" in _clean(node.get_text(" ", strip=True))
        for node in root.select("a[href]")
        if "목록" not in _clean(node.get_text(" ", strip=True))
    )
    if "테스트" in row["title"]:
        return None, application_controls
    branch = _normalize_city_branch(row["organizer"] or fields["주최"], fields["강의장소"])
    out = _base_output("city", f"lecture:{row['identity']}")
    out.update(
        {
            "title": row["title"],
            "status": _status(fields["강좌상태"]),
            "source_status": fields["강좌상태"],
            "start_date": row["start"].isoformat(),
            "end_date": row["end"].isoformat(),
            "apply_start_date": row["apply_start"].isoformat(),
            "apply_end_date": row["apply_end"].isoformat(),
            "schedule": fields["강의시간"],
            "branch": branch,
            "venue": fields["강의장소"],
            "category": fields["강좌영역"],
            "target": fields["수강대상"],
            "fee": row["selection_fee"],
            "capacity": row["capacity"],
            "source_url": row["detail_url"],
            "raw_fields": {
                "source_owner": "city",
                "application_control_present": bool(application_controls),
                "service_family": "education",
            },
        }
    )
    return out, application_controls


def info_list_url(page: int) -> str:
    return "https://sso.gunpo.go.kr/infoedu/selectEdcLctreWebList.do?" + urlencode(
        (("key", "2115"), ("eduCourseGrupCode", "EDCGRU01"), ("pageUnit", "1000"), ("pageIndex", str(page)))
    )


def info_detail_url(course_id: str) -> str:
    return "https://sso.gunpo.go.kr/infoedu/selectEdcLctreWebView.do?" + urlencode(
        (("key", "2115"), ("eduCourseGrupCode", "EDCGRU01"), ("edcLctreNo", str(course_id)))
    )


def _parse_info_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    root = soup.select_one("#contents") or soup
    rows: list[dict[str, Any]] = []
    for tr in root.select("table tbody tr"):
        anchor = tr.select_one("a[href*='selectEdcLctreWebView.do']")
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
        if anchor is None:
            continue
        values = dict(
            parse_qsl(urlparse(urljoin(GUNPO_INFO_URL, anchor.get("href", ""))).query, keep_blank_values=True)
        )
        identity = values.get("edcLctreNo", "")
        if not _numeric(identity) or len(cells) != 7:
            raise GunpoContractError("information row schema drift")
        dates = _all_dates(cells[2])
        counts = [int(x.replace(",", "")) for x in _INT.findall(cells[4])]
        if len(dates) != 4 or len(counts) != 2:
            raise GunpoContractError(f"information {identity}: list fields drift")
        rows.append(
            {
                "identity": identity,
                "title": _clean(anchor.get_text(" ", strip=True)),
                "apply_start": dates[0],
                "apply_end": dates[1],
                "start": dates[2],
                "end": dates[3],
                "schedule": cells[3],
                "applicants": counts[0],
                "capacity": counts[1],
                "method": cells[5],
                "raw_status": cells[6],
                "detail_url": info_detail_url(identity),
            }
        )
    text = _clean(root.get_text(" ", strip=True))
    total_match = re.search(r"총\s*([\d,]+)\s*건", text)
    total = int(total_match.group(1).replace(",", "")) if total_match else (len(rows) if page == 1 else 0)
    return {"page": page, "total": total, "rows": rows}


def _info_detail(row: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], int]:
    root = soup.select_one("#contents") or soup
    fields = _table_pairs(root)
    required = {"교육과정", "교육장소", "교육기간", "교육대상", "접수기간", "교육시간", "접수방법", "접수정보"}
    # The list uses compact labels such as ``생성형 AI (화)`` while the same
    # record's detail uses ``생성형 AI 화요반``.  The exact, non-redirecting
    # numeric detail route plus dates/time is the stable identity contract.
    if not required.issubset(fields) or not fields["교육과정"]:
        raise GunpoContractError(f"information {row['identity']}: detail schema drift")
    dates = _all_dates(fields["접수기간"] + " " + fields["교육기간"])
    if len(dates) < 4 or dates[:4] != [row["apply_start"], row["apply_end"], row["start"], row["end"]]:
        raise GunpoContractError(f"information {row['identity']}: detail dates drift")
    controls = sum("신청" in _clean(node.get_text(" ", strip=True)) for node in root.select("a,button"))
    out = _base_output("info", f"lecture:{row['identity']}")
    out.update(
        {
            "title": row["title"],
            "status": _status(row["raw_status"]),
            "source_status": row["raw_status"],
            "start_date": row["start"].isoformat(),
            "end_date": row["end"].isoformat(),
            "apply_start_date": row["apply_start"].isoformat(),
            "apply_end_date": row["apply_end"].isoformat(),
            "schedule": fields["교육시간"],
            "branch": "군포시정보교육센터",
            "venue": fields["교육장소"],
            "category": "정보화교육",
            "target": fields["교육대상"],
            "fee": "무료",
            "capacity": row["capacity"],
            "applicants": row["applicants"],
            "source_url": row["detail_url"],
            "raw_fields": {
                "source_owner": "info",
                "application_control_present": bool(controls),
                "service_family": "education",
            },
        }
    )
    return out, controls


def foundation_list_url(agent: str, page: int) -> str:
    return (
        GUNPO_FOUNDATION_URL
        + "?"
        + urlencode(
            (
                ("pageIndex", str(page)),
                ("searchAgentCode", agent),
                ("searchGbCode1", ""),
                ("searchGbCode2", ""),
                ("searchText", ""),
            )
        )
    )


def foundation_detail_url(identity: Sequence[str]) -> str:
    site, lecture, agent, group1, group2, course = identity
    return f"https://www.gunpocf.or.kr/cf/lctre/regularLctreView/{site}?" + urlencode(
        (
            ("siteCd", site),
            ("lctreInfoNo", lecture),
            ("agentCode", agent),
            ("gbCode1", group1),
            ("gbCode2", group2),
            ("suKangNo", course),
        )
    )


_FOUNDATION_LINK = re.compile(
    r"fnGoViewPage\('([0-9]+)','([0-9]*)','([0-9]+)','([0-9]+)',\s*'([0-9]+)',\s*'([0-9]+)'\);"
)


def _parse_foundation_page(soup: BeautifulSoup, agent: str, page: int) -> dict[str, Any]:
    root = soup.select_one("#contents") or soup
    pages = [int(value) for value in re.findall(r"fnLinkPage\((\d+)\)", str(root))]
    last = max([1, *pages])
    rows: list[dict[str, Any]] = []
    for tr in root.select("table tbody tr"):
        anchor = tr.select_one("a[onclick*='fnGoViewPage']")
        if anchor is None:
            continue
        match = _FOUNDATION_LINK.fullmatch(_clean(anchor.get("onclick")))
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
        if match is None or len(cells) != 7 or match.group(3) != agent:
            raise GunpoContractError("foundation row identity/schema drift")
        identity = match.groups()
        start, end = _parse_short_dates(cells[2])
        title = _clean(anchor.get_text(" ", strip=True)).split("▶", 1)[0].strip()
        rows.append(
            {
                "identity": identity,
                "title": title,
                "category": cells[0],
                "start": start,
                "end": end,
                "fee": cells[3],
                "capacity": (_positive_int(_INT.search(cells[4]).group(), "capacity") if _INT.search(cells[4]) else 0),
                "availability": cells[5],
                "raw_status": cells[6],
                "branch": GUNPO_FOUNDATION_BRANCHES[agent][1],
                "detail_url": foundation_detail_url(identity),
            }
        )
    return {"agent": agent, "page": page, "last": last, "rows": rows}


def _foundation_detail(row: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], int, int]:
    root = soup.select_one("#contents") or soup
    text = _clean(root.get_text(" ", strip=True))
    if row["title"] not in text:
        raise GunpoContractError("foundation detail title drift")
    short_dates = [date(2000 + int(y), int(m), int(d)) for y, m, d in _SHORT_DATE.findall(text)]
    if row["start"] not in short_dates or row["end"] not in short_dates:
        raise GunpoContractError("foundation detail period drift")
    controls = sum("fnRegist" in _clean(node.get("onclick")) for node in root.select("[onclick]"))
    attachments = sum("gfnFileDownload" in _clean(node.get("onclick")) for node in root.select("[onclick]"))
    out = _base_output("foundation", "lecture:" + ":".join(row["identity"]))
    out.update(
        {
            "title": row["title"],
            "status": _status(row["raw_status"]),
            "source_status": row["raw_status"],
            "start_date": row["start"].isoformat(),
            "end_date": row["end"].isoformat(),
            "apply_start_date": "",
            "apply_end_date": "",
            "schedule": "",
            "branch": row["branch"],
            "venue": row["branch"],
            "category": row["category"],
            "target": "",
            "fee": row["fee"],
            "capacity": row["capacity"],
            "source_url": row["detail_url"],
            "raw_fields": {
                "source_owner": "foundation",
                "application_control_present": bool(controls),
                "attachments_discarded": attachments,
                "service_family": "education",
            },
        }
    )
    return out, controls, attachments


def media_list_url(page: int) -> str:
    return GUNPO_MEDIA_URL if page == 1 else GUNPO_MEDIA_URL + "?" + urlencode((("pageIndex", str(page)),))


def media_detail_url(course_id: str, page: int) -> str:
    return "https://www.gpmedia.or.kr/front/edcExprn/edcRequestView?" + urlencode(
        (("mediaEdcNo", str(course_id)), ("pageIndex", str(page)))
    )


def _parse_media_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    root = soup.select_one("#contents") or soup
    text = _clean(root.get_text(" ", strip=True))
    total_match = re.search(r"전체\s*([\d,]+)\s*현재", text)
    page_match = re.search(r"현재\s*([\d,]+)\s*/\s*([\d,]+)\s*페이지", text)
    if not total_match or not page_match:
        raise GunpoContractError("media total/page declaration drift")
    total = int(total_match.group(1).replace(",", ""))
    last = int(page_match.group(2).replace(",", ""))
    rows: list[dict[str, Any]] = []
    for item in root.select(".listBody li"):
        anchor = item.select_one("a[href^='javascript:fnGoViewPage']")
        if anchor is None:
            continue
        match = re.fullmatch(r"javascript:fnGoViewPage\('([1-9]\d*)'\)", _clean(anchor.get("href")))
        fields = _dt_pairs(item)
        required = {"교육기간", "접수기간", "모집정원", "수강료", "모집방법"}
        if match is None or not required.issubset(fields):
            raise GunpoContractError("media list row schema drift")
        start, end = _parse_short_dates(fields["교육기간"])
        apply_start, apply_end = _parse_short_dates(fields["접수기간"])
        capacity_match = _INT.search(fields["모집정원"])
        status_node = item.select_one(".catagoryName")
        raw_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
        identity = match.group(1)
        rows.append(
            {
                "identity": identity,
                "title": _clean(anchor.get_text(" ", strip=True)),
                "raw_status": raw_status,
                "start": start,
                "end": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "capacity": int(capacity_match.group().replace(",", "")) if capacity_match else 0,
                "fee": fields["수강료"],
                "selection": fields["모집방법"],
                "source_page": page,
                "detail_url": media_detail_url(identity, page),
            }
        )
    return {"page": page, "total": total, "last": last, "rows": rows}


def _media_detail(row: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], int]:
    root = soup.select_one("#contents") or soup
    fields = _dt_pairs(root)
    required = {"모집기간", "모집정원", "수강료", "모집방법", "교육기간", "교육시간", "교육장소", "대상"}
    if not required.issubset(fields) or row["title"] not in _clean(root.get_text(" ", strip=True)):
        raise GunpoContractError(f"media {row['identity']}: detail schema drift")
    event_dates = _all_dates(fields["교육기간"])
    apply_dates = _all_dates(fields["모집기간"])
    if event_dates[:2] != [row["start"], row["end"]] or apply_dates[:2] != [row["apply_start"], row["apply_end"]]:
        raise GunpoContractError(f"media {row['identity']}: detail dates drift")
    controls = len(root.select("button#btnRequest"))
    out = _base_output("media", f"media:{row['identity']}")
    out.update(
        {
            "title": row["title"],
            "status": _status(row["raw_status"]),
            "source_status": row["raw_status"],
            "start_date": row["start"].isoformat(),
            "end_date": row["end"].isoformat(),
            "apply_start_date": row["apply_start"].isoformat(),
            "apply_end_date": row["apply_end"].isoformat(),
            "schedule": fields["교육시간"],
            "branch": "군포시미디어센터",
            "venue": fields["교육장소"],
            "category": "미디어교육",
            "target": fields["대상"],
            "fee": fields["수강료"],
            "capacity": row["capacity"],
            "source_url": row["detail_url"],
            "raw_fields": {
                "source_owner": "media",
                "application_control_present": bool(controls),
                "service_family": "education",
            },
        }
    )
    return out, controls


def library_list_url(program_type: int, offset: int) -> str:
    return "https://www.gunpolib.go.kr/pyxis-api/1/library-programs?" + urlencode(
        (
            ("libraryProgramTypeId", str(program_type)),
            ("orderBy", "ORDER BY receiptBeginDate DESC"),
            ("hideBranch", "true"),
            ("hideContents", "true"),
            ("hideReview", "true"),
            ("offset", str(offset)),
            ("max", "1000"),
        )
    )


def library_detail_url(course_id: int | str) -> str:
    return f"https://www.gunpolib.go.kr/pyxis-api/1/library-programs/{course_id}"


def _library_payload(payload: Any, program_type: int, offset: int, total: Optional[int] = None) -> dict[str, Any]:
    if (
        not isinstance(payload, Mapping)
        or payload.get("success") is not True
        or not isinstance(payload.get("data"), Mapping)
    ):
        raise GunpoContractError("library API envelope drift")
    data = payload["data"]
    observed_total = _positive_int(data.get("totalCount"), "library total")
    if total is not None and observed_total != total:
        raise GunpoContractError("library total changed across chunks")
    if (
        _positive_int(data.get("offset"), "library offset") != offset
        or _positive_int(data.get("max"), "library max") != 1000
    ):
        raise GunpoContractError("library chunk boundary drift")
    raw_rows = data.get("list")
    if not isinstance(raw_rows, list):
        raise GunpoContractError("library rows missing")
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        if not isinstance(item, Mapping):
            raise GunpoContractError("library row is not an object")
        identity = _positive_int(item.get("id"), "library id")
        type_data, branch_data = item.get("libraryProgramType"), item.get("branch")
        if (
            not isinstance(type_data, Mapping)
            or _positive_int(type_data.get("id"), "library type") != program_type
            or not isinstance(branch_data, Mapping)
        ):
            raise GunpoContractError(f"library {identity}: type/branch drift")
        branch = _clean(branch_data.get("name"))
        if branch not in GUNPO_LIBRARY_BRANCHES:
            raise GunpoContractError(f"library {identity}: unaudited official branch {branch}")
        start, end = _parse_date(_clean(item.get("beginDate"))), _parse_date(_clean(item.get("endDate")))
        apply_start, apply_end = (
            _parse_date(_clean(item.get("receiptBeginDate"))),
            _parse_date(_clean(item.get("receiptEndDate"))),
        )
        rows.append(
            {
                "identity": identity,
                "program_type": program_type,
                "type_name": _clean(type_data.get("name")),
                "title": _clean(item.get("title")),
                "raw_status": _clean(item.get("state")),
                "branch": branch,
                "start": start,
                "end": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "target": _clean(item.get("target")),
                "venue": _clean(item.get("place")),
                "capacity": _positive_int(item.get("quota", 0), "library quota"),
                "applicants": _positive_int(item.get("requestorCnt", 0), "library applicants"),
                "selection": _clean((item.get("libraryProgramReceiptType") or {}).get("name")),
                "detail_url": library_detail_url(identity),
            }
        )
    return {"program_type": program_type, "offset": offset, "total": observed_total, "rows": rows}


_LIBRARY_STATUS = {
    "RECEIPT": "OPEN",
    "WAITING": "WAITING",
    "STANDBY": "SCHEDULED",
    "END": "CLOSED",
    "CLOSE": "CLOSED",
    "CLOSED": "CLOSED",
    "CANCEL": "CANCELLED",
}


def _library_detail(row: Mapping[str, Any], payload: Any) -> tuple[dict[str, Any], int, int]:
    if (
        not isinstance(payload, Mapping)
        or payload.get("success") is not True
        or not isinstance(payload.get("data"), Mapping)
    ):
        raise GunpoContractError("library detail envelope drift")
    item = payload["data"]
    branch = item.get("branch") or {}
    type_data = item.get("libraryProgramType") or {}
    checks = (
        _positive_int(item.get("id"), "library detail id") == row["identity"],
        _positive_int(type_data.get("id"), "library detail type") == row["program_type"],
        _clean(item.get("title")) == row["title"],
        _clean(branch.get("name")) == row["branch"],
        _parse_date(_clean(item.get("beginDate"))) == row["start"],
        _parse_date(_clean(item.get("endDate"))) == row["end"],
    )
    if not all(checks):
        raise GunpoContractError(f"library {row['identity']}: detail identity drift")
    sensitive = sum(
        key in item and item.get(key) not in (None, "", [], {}) for key in ("content", "instructor", "requestForm")
    )
    attachments = int(bool(item.get("attachments"))) + int(bool(item.get("isAttachable")))
    out = _base_output("library", f"type:{row['program_type']}:program:{row['identity']}")
    out.update(
        {
            "title": row["title"],
            "status": _LIBRARY_STATUS.get(row["raw_status"], "UNKNOWN"),
            "source_status": row["raw_status"],
            "start_date": row["start"].isoformat(),
            "end_date": row["end"].isoformat(),
            "apply_start_date": row["apply_start"].isoformat(),
            "apply_end_date": row["apply_end"].isoformat(),
            "schedule": "",
            "branch": row["branch"],
            "venue": row["venue"],
            "category": row["type_name"],
            "target": row["target"],
            "fee": "",
            "capacity": row["capacity"],
            "applicants": row["applicants"],
            "source_url": row["detail_url"],
            "raw_fields": {
                "source_owner": "library",
                "selection_method": row["selection"],
                "sensitive_fields_discarded": sensitive,
                "attachments_discarded": attachments,
                "service_family": "education",
            },
        }
    )
    return out, sensitive, attachments


def fmcs_company_url(owner: str) -> str:
    root = "https://www.gunpouc.or.kr" if owner == "urban" else "https://www.gpyf.or.kr/yeyak"
    return root + "/rest/common/company"


def fmcs_category_url() -> str:
    return "https://www.gpyf.or.kr/yeyak/rest/common/category?" + urlencode((("company_code", "GUNPOYF01"),))


def fmcs_list_url(owner: str, status: str, page: int, category: str = "") -> str:
    root = "https://www.gunpouc.or.kr" if owner == "urban" else "https://www.gpyf.or.kr/yeyak"
    company = "" if owner == "urban" else "GUNPOYF01"
    level = "9" if owner == "urban" else "1"
    return (
        root
        + "/rest/lecture/list?"
        + urlencode(
            (
                ("company_code", company),
                ("search_type", status),
                ("category_cd", category),
                ("category_level", level),
                ("class_nm", ""),
                ("train_day", ""),
                ("adult_gubn", ""),
                ("lecturer_nm", ""),
                ("page", str(page)),
                ("page_size", "1000"),
            )
        )
    )


def fmcs_detail_url(owner: str, comcd: str, classcd: str) -> str:
    base = GUNPO_URBAN_URL if owner == "urban" else GUNPO_YOUTH_URL
    return base + "?" + urlencode((("action", "read"), ("comcd", comcd), ("classcd", classcd), ("type", "R")))


def _fmcs_registry(payload: Any, expected: Mapping[str, str], *, categories: bool = False) -> dict[str, str]:
    if not isinstance(payload, list):
        raise GunpoContractError("FMCS registry envelope drift")
    code_key, name_key = ("category_code", "category_name") if categories else ("comcd", "comnm")
    observed = {_clean(item.get(code_key)): _clean(item.get(name_key)) for item in payload if isinstance(item, Mapping)}
    if observed != dict(expected):
        raise GunpoContractError(f"FMCS official {'category' if categories else 'branch'} registry drift")
    return observed


def _fmcs_page(payload: Any, owner: str, status: str, page: int, category: str = "") -> dict[str, Any]:
    if isinstance(payload, Mapping) and payload.get("error"):
        raise GunpoContractError("FMCS list returned an error envelope")
    if not isinstance(payload, list):
        raise GunpoContractError("FMCS list envelope drift")
    rows: list[dict[str, Any]] = []
    total = 0
    for item in payload:
        if not isinstance(item, Mapping):
            raise GunpoContractError("FMCS list item drift")
        comcd, classcd = _clean(item.get("comcd")), _clean(item.get("class_cd"))
        if not re.fullmatch(r"[A-Z0-9]+", comcd) or not re.fullmatch(r"\d+", classcd):
            raise GunpoContractError("FMCS course identity drift")
        branch_registry = GUNPO_URBAN_ARCHIVE_BRANCHES if owner == "urban" else GUNPO_YOUTH_BRANCHES
        if branch_registry.get(comcd) != _clean(item.get("comnm")):
            raise GunpoContractError("FMCS row branch drift")
        row_total = _positive_int(item.get("total_count", len(payload)), "FMCS total")
        total = row_total if not total else total
        if total != row_total:
            raise GunpoContractError("FMCS repeated total drift")
        item_status = _clean(item.get("status"))
        if item_status and item_status not in {"R", "E", "W"}:
            raise GunpoContractError("FMCS source status drift")
        rows.append(
            {
                "identity": f"{comcd}:{classcd}",
                "comcd": comcd,
                "classcd": classcd,
                "title": _clean(item.get("class_nm")),
                "branch": _clean(item.get("comnm")),
                "raw_status": item_status or status,
                "partition": status,
                "category_partition": category,
                "category": " > ".join(filter(None, (_clean(item.get("category1")), _clean(item.get("category2"))))),
                "schedule": " / ".join(
                    filter(
                        None,
                        (
                            _clean(item.get("train_day_nm")),
                            "~".join(
                                filter(
                                    None,
                                    (
                                        _clean(item.get("train_stime")),
                                        _clean(item.get("train_etime")),
                                    ),
                                )
                            ),
                        ),
                    )
                ),
                "target": _clean(item.get("target_age_name")),
                "fee": _clean(item.get("course_fee")),
                "capacity": _positive_int(item.get("capa", 0), "FMCS capacity"),
                "applicants": _positive_int(item.get("reg_person", 0), "FMCS applicants"),
                "detail_url": fmcs_detail_url(owner, comcd, classcd),
            }
        )
    if not payload:
        total = 0
    return {"owner": owner, "status": status, "category": category, "page": page, "total": total, "rows": rows}


def _fmcs_is_current(row: Mapping[str, Any]) -> bool:
    # FMCS search_type=R can include ended rows, so the row status is authoritative.
    return _clean(row.get("raw_status")) == "R"


def _fmcs_detail(owner: str, row: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], int]:
    root = soup.select_one("#contents") or soup
    text = _clean(root.get_text(" ", strip=True))
    if row["title"] not in text or row["branch"] not in text or "강좌 상세" not in text:
        raise GunpoContractError(f"FMCS {row['identity']}: detail identity drift")
    forms = root.select("form#form_lecture_reg")
    if (
        len(forms) != 1
        or _clean(forms[0].get("method")).lower() != "post"
        or "action=write" not in _clean(forms[0].get("action"))
    ):
        raise GunpoContractError(f"FMCS {row['identity']}: application control drift")
    normalized_status = (
        "OPEN"
        if row["raw_status"] == "R" and (not row["capacity"] or row["applicants"] < row["capacity"])
        else "CLOSED"
    )
    out = _base_output(owner, f"lecture:{row['identity']}")
    out.update(
        {
            "title": row["title"],
            "status": normalized_status,
            "source_status": row["raw_status"],
            "start_date": "",
            "end_date": "",
            "apply_start_date": "",
            "apply_end_date": "",
            "schedule": row["schedule"],
            "branch": row["branch"],
            "venue": row["branch"],
            "category": row["category"],
            "target": row["target"],
            "fee": row["fee"],
            "capacity": row["capacity"],
            "applicants": row["applicants"],
            "source_url": row["detail_url"],
            "raw_fields": {
                "source_owner": owner,
                "source_partition": row["partition"],
                "category_partition": row["category_partition"],
                "application_control_present": True,
                "service_family": "education",
            },
        }
    )
    return out, 1


def picturebook_list_url(page: int) -> str:
    return "https://www.gunpo.go.kr/picturebook/ko/M000000141/api/edu/progrm/list?" + urlencode(
        (
            ("searchWrd", ""),
            ("searchBgngYmd", ""),
            ("searchEndYmd", ""),
            ("pageUnit", "1000"),
            ("pageIndex", str(page)),
            ("searchGroupCdIds", ""),
        )
    )


def picturebook_detail_url(course_id: int | str) -> str:
    return "https://www.gunpo.go.kr/picturebook/ko/M000000141/api/edu/progrm/view?" + urlencode(
        (("eduProgrmNo", str(course_id)),)
    )


def _picturebook_page(payload: Any, page: int, total: Optional[int] = None) -> dict[str, Any]:
    if (
        not isinstance(payload, Mapping)
        or not isinstance(payload.get("pagination"), Mapping)
        or not isinstance(payload.get("list"), list)
    ):
        raise GunpoContractError("picturebook list envelope drift")
    observed_total = _positive_int(payload.get("total"), "picturebook total")
    if total is not None and observed_total != total:
        raise GunpoContractError("picturebook total changed")
    pagination = payload["pagination"]
    if (
        _positive_int(pagination.get("currentPageNo"), "picturebook page") != page
        or _positive_int(pagination.get("totalRecordCount"), "picturebook pagination total") != observed_total
    ):
        raise GunpoContractError("picturebook pagination drift")
    rows: list[dict[str, Any]] = []
    for item in payload["list"]:
        identity = _positive_int(item.get("eduProgrmNo"), "picturebook id")
        start = date.fromisoformat(datetime.strptime(_clean(item.get("eduBgngYmd")), "%Y%m%d").date().isoformat())
        end = date.fromisoformat(datetime.strptime(_clean(item.get("eduEndYmd")), "%Y%m%d").date().isoformat())
        rows.append(
            {
                "identity": identity,
                "title": _clean(item.get("eduProgrmNm")),
                "category": _clean(item.get("eduGroupCdNm")),
                "start": start,
                "end": end,
                "apply_start": _parse_date(_clean(item.get("rcptBgngPnttm"))),
                "apply_end": _parse_date(_clean(item.get("rcptEndPnttm"))),
                "venue": _clean(item.get("eduPlace")),
                "target": _clean(item.get("eduTrgt")),
                "fee": _positive_int(item.get("tutfee", 0), "picturebook fee"),
                "capacity": _positive_int(item.get("mxmmAplyNope") or 0, "picturebook capacity"),
                "selection": _clean(item.get("rcptTyCdNm")),
                "detail_url": picturebook_detail_url(identity),
            }
        )
    return {"page": page, "total": observed_total, "rows": rows}


def _picturebook_detail(row: Mapping[str, Any], payload: Any, cutoff: date) -> tuple[dict[str, Any], int]:
    if not isinstance(payload, Mapping):
        raise GunpoContractError("picturebook detail envelope drift")
    if (
        _positive_int(payload.get("eduProgrmNo"), "picturebook detail id") != row["identity"]
        or _clean(payload.get("eduProgrmNm")) != row["title"]
        or _clean(payload.get("eduPlace")) != row["venue"]
    ):
        raise GunpoContractError("picturebook detail identity drift")
    start = datetime.strptime(_clean(payload.get("eduBgngYmd")), "%Y%m%d").date()
    end = datetime.strptime(_clean(payload.get("eduEndYmd")), "%Y%m%d").date()
    if (start, end) != (row["start"], row["end"]):
        raise GunpoContractError("picturebook detail period drift")
    sensitive = sum(bool(payload.get(key)) for key in ("eduIntrcnCn", "crclmCn")) + int("requestForm" in payload)
    status = (
        "OPEN"
        if row["apply_start"] <= cutoff <= row["apply_end"]
        else "SCHEDULED"
        if cutoff < row["apply_start"]
        else "CLOSED"
    )
    out = _base_output("picturebook", f"program:{row['identity']}")
    out.update(
        {
            "title": row["title"],
            "status": status,
            "source_status": status,
            "start_date": row["start"].isoformat(),
            "end_date": row["end"].isoformat(),
            "apply_start_date": row["apply_start"].isoformat(),
            "apply_end_date": row["apply_end"].isoformat(),
            "schedule": ",".join(
                _clean(item.get("weekCdNm"))
                for item in payload.get("crseFyerSchdulList", [])
                if isinstance(item, Mapping)
            ),
            "branch": "그림책꿈마루",
            "venue": row["venue"],
            "category": row["category"],
            "target": row["target"],
            "fee": str(row["fee"]),
            "capacity": _positive_int(payload.get("mxmmAplyNope") or row["capacity"], "picturebook detail capacity"),
            "source_url": row["detail_url"],
            "raw_fields": {
                "source_owner": "picturebook",
                "selection_method": row["selection"],
                "sensitive_fields_discarded": sensitive,
                "service_family": "education",
            },
        }
    )
    return out, sensitive


def flying_list_url(page: int) -> str:
    return GUNPO_FLYING_URL if page == 1 else GUNPO_FLYING_URL + "?" + urlencode((("page", str(page)),))


def flying_detail_url(course_id: int | str) -> str:
    return f"https://www.gunpoycf.or.kr/program/{course_id}"


def _parse_flying_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    text = _clean(soup.get_text(" ", strip=True))
    total_match = re.search(r"Total\s*([\d,]+)건", text)
    if not total_match:
        raise GunpoContractError("Flying declared total missing")
    total = int(total_match.group(1).replace(",", ""))
    pages = [int(value) for value in re.findall(r"[?&]page=(\d+)", str(soup))]
    last = max([1, *pages])
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for anchor in soup.select("a[href]"):
        parsed = urlparse(urljoin(GUNPO_FLYING_URL, anchor.get("href", "")))
        match = re.fullmatch(r"/program/([1-9]\d*)", parsed.path)
        if not match:
            continue
        identity = int(match.group(1))
        if identity in seen:
            continue
        seen.add(identity)
        field = anchor.select_one("ul.field")
        dates = anchor.select("ul.date li span")
        title_node = anchor.select_one("p.program_tit")
        if field is None or len(field.select("li")) < 2 or len(dates) != 2 or title_node is None:
            raise GunpoContractError("Flying program card schema drift")
        raw_status = _clean(field.select("li")[0].get_text(" ", strip=True))
        category = _clean(field.select("li")[1].get_text(" ", strip=True))
        apply_dates = _all_dates(dates[0].get_text(" ", strip=True))
        event_dates = _all_dates(dates[1].get_text(" ", strip=True))
        if len(apply_dates) not in {0, 1, 2} or len(event_dates) not in {0, 1, 2}:
            raise GunpoContractError("Flying program dates drift")
        apply_start = apply_dates[0] if apply_dates else None
        apply_end = apply_dates[-1] if apply_dates else None
        start = event_dates[0] if event_dates else None
        end = event_dates[-1] if event_dates else None
        rows.append(
            {
                "identity": identity,
                "title": _clean(title_node.get_text(" ", strip=True)),
                "raw_status": raw_status,
                "category": category,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "start": start,
                "end": end,
                "detail_url": flying_detail_url(identity),
            }
        )
    return {"page": page, "total": total, "last": last, "rows": rows}


def _flying_detail(row: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], int]:
    text = _clean(soup.get_text(" ", strip=True))
    if row["title"] not in text or row["category"] not in text:
        raise GunpoContractError(f"Flying {row['identity']}: detail identity drift")
    dates = _all_dates(text)
    if any(value not in dates for value in (row["apply_start"], row["apply_end"], row["start"], row["end"])):
        raise GunpoContractError(f"Flying {row['identity']}: detail period drift")

    def field(label: str) -> str:
        node = soup.find(string=lambda value: value and _clean(value) == label)
        if node is None:
            return ""
        parent = node.parent
        sibling = parent.find_next_sibling() if parent else None
        return _clean(sibling.get_text(" ", strip=True)) if sibling else ""

    venue = field("진행장소") or "군포시 청년공간 플라잉"
    target = field("모집대상")
    schedule = field("진행시간")
    controls = sum(
        "신청" in _clean(node.get_text(" ", strip=True))
        and urlparse(urljoin(row["detail_url"], node.get("href", ""))).hostname not in {"", "www.gunpoycf.or.kr"}
        for node in soup.select("a[href]")
    )
    out = _base_output("flying", f"program:{row['identity']}")
    out.update(
        {
            "title": row["title"],
            "status": _status(row["raw_status"]),
            "source_status": row["raw_status"],
            "start_date": row["start"].isoformat(),
            "end_date": row["end"].isoformat(),
            "apply_start_date": row["apply_start"].isoformat(),
            "apply_end_date": row["apply_end"].isoformat(),
            "schedule": schedule,
            "branch": "군포시 청년공간 플라잉",
            "venue": venue,
            "category": row["category"],
            "target": target,
            "fee": "",
            "capacity": 0,
            "source_url": row["detail_url"],
            "raw_fields": {
                "source_owner": "flying",
                "application_control_present": bool(controls),
                "service_family": "education",
            },
        }
    )
    return out, controls


def _initial_meta(owner: str, cutoff: date) -> dict[str, Any]:
    config = GUNPO_OWNERS[owner]
    return {
        "owner": owner,
        "provider": config["provider"],
        "canonical_url": config["url"],
        "municipality_code": GUNPO_MUNICIPALITY_CODE,
        "municipality_name": GUNPO_MUNICIPALITY_NAME,
        "cutoff_date": cutoff.isoformat(),
        "source_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "registry_requests": 0,
        "application_requests": 0,
        "login_requests": 0,
        "applicant_requests": 0,
        "payment_requests": 0,
        "attachment_requests": 0,
        "post_requests": 0,
        "forbidden_endpoint_requests": 0,
        "http_methods": ["GET"],
        "source_total_count": 0,
        "source_total": 0,
        "source_rows": 0,
        "discovered_links": 0,
        "current_source_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "excluded_partition_count": 0,
        "excluded_test_count": 0,
        "application_controls_discovered": 0,
        "sensitive_fields_discarded": 0,
        "attachments_discarded": 0,
        "pages": 0,
        "detail_pages": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "sentinel_verified": False,
        "boundary_recheck_verified": False,
        "details_complete": False,
        "pii_safe": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "source_cap_reached": False,
        "detail_cap_reached": False,
        "failure_atomic": True,
        "configured_collection_error": "",
    }


def _ensure_list_budget(requester: _Requester, limit: int, additional: int) -> None:
    required = requester.meta["list_requests"] + additional
    requester.meta["required_list_requests"] = max(requester.meta.get("required_list_requests", 0), required)
    if required > limit:
        requester.meta["source_cap_reached"] = True
        raise GunpoContractError(f"max_pages cap allows {limit} of {required} required list requests")


def _ensure_detail_budget(meta: dict[str, Any], limit: int, required: int) -> None:
    meta["required_detail_requests"] = required
    if required > limit:
        meta["detail_cap_reached"] = True
        raise GunpoContractError(f"detail_limit cap allows {limit} of {required} current detail requests")


def _assert_unique(rows: Sequence[Mapping[str, Any]], label: str) -> None:
    identities = [str(row["identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise GunpoContractError(f"{label} catalogue contains duplicate identities")


def _collapse_identical_identity_duplicates(
    rows: Sequence[Mapping[str, Any]], label: str
) -> tuple[list[dict[str, Any]], int]:
    unique: list[dict[str, Any]] = []
    by_identity: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for source_row in rows:
        row = dict(source_row)
        identity = str(row["identity"])
        previous = by_identity.get(identity)
        if previous is None:
            by_identity[identity] = row
            unique.append(row)
            continue
        if previous != row:
            raise GunpoContractError(f"{label} catalogue contains conflicting duplicate identity {identity}")
        duplicates += 1
    return unique, duplicates


def _rows_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    return _signature(list(rows))


def _single_html_ledger(
    requester: _Requester,
    max_pages: int,
    url_builder: Callable[[int], str],
    parser: Callable[[BeautifulSoup, int], dict[str, Any]],
    label: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _ensure_list_budget(requester, max_pages, 1)
    first = parser(_soup(requester.get(url_builder(1), "list")), 1)
    total = first["total"]
    rows = first["rows"]
    if total != len(rows):
        raise GunpoContractError(f"{label} declared total does not match page-one rows")
    _assert_unique(rows, label)
    _ensure_list_budget(requester, max_pages, 3)
    responses = requester.many([url_builder(2), url_builder(1), url_builder(2)], "list")
    sentinel = parser(_soup(responses[0]), 2)
    first_recheck = parser(_soup(responses[1]), 1)
    sentinel_recheck = parser(_soup(responses[2]), 2)
    if sentinel["rows"] or sentinel_recheck["rows"]:
        raise GunpoContractError(f"{label} immediate post-boundary sentinel is not empty")
    if _rows_signature(first_recheck["rows"]) != _rows_signature(rows):
        raise GunpoContractError(f"{label} page-one boundary changed on recheck")
    if _rows_signature(sentinel_recheck["rows"]) != _rows_signature(sentinel["rows"]):
        raise GunpoContractError(f"{label} sentinel changed on recheck")
    if first_recheck["total"] != total:
        raise GunpoContractError(f"{label} declared total changed on recheck")
    return list(rows), {
        "declared_total": total,
        "page_counts": {"1": len(rows), "2_sentinel": 0},
        "sentinel_page": 2,
    }


def _collect_city(
    requester: _Requester, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ledger, audit = _single_html_ledger(requester, max_pages, city_list_url, _parse_city_page, "city")
    current = [row for row in ledger if row["end"] >= cutoff]
    _ensure_detail_budget(requester.meta, detail_limit, len(current))
    responses = requester.many([row["detail_url"] for row in current], "detail")
    output: list[dict[str, Any]] = []
    controls = 0
    excluded_test = 0
    for row, response in zip(current, responses):
        parsed, discovered = _city_detail(row, _soup(response))
        controls += discovered
        if parsed is None:
            excluded_test += 1
        else:
            output.append(parsed)
    audit.update(
        {
            "source_total_count": len(ledger),
            "current_source_count": len(current),
            "excluded_test_count": excluded_test,
            "application_controls_discovered": controls,
            "branch_counts": dict(sorted(Counter(row["branch"] for row in output).items())),
        }
    )
    return output, audit


def _collect_info(
    requester: _Requester, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ledger, audit = _single_html_ledger(requester, max_pages, info_list_url, _parse_info_page, "information education")
    current = [row for row in ledger if row["end"] >= cutoff]
    _ensure_detail_budget(requester.meta, detail_limit, len(current))
    responses = requester.many([row["detail_url"] for row in current], "detail")
    output: list[dict[str, Any]] = []
    controls = 0
    for row, response in zip(current, responses):
        parsed, discovered = _info_detail(row, _soup(response))
        controls += discovered
        output.append(parsed)
    audit.update(
        {
            "source_total_count": len(ledger),
            "current_source_count": len(current),
            "application_controls_discovered": controls,
            "branch_counts": {"군포시정보교육센터": len(output)},
        }
    )
    return output, audit


def _collect_foundation(
    requester: _Requester, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    agents = list(GUNPO_FOUNDATION_BRANCHES)
    _ensure_list_budget(requester, max_pages, len(agents))
    first_responses = requester.many([foundation_list_url(agent, 1) for agent in agents], "list")
    first = {
        agent: _parse_foundation_page(_soup(response), agent, 1) for agent, response in zip(agents, first_responses)
    }
    requests_to_make: list[tuple[str, int, str, str]] = []
    for agent in agents:
        last = first[agent]["last"]
        if last < 1:
            raise GunpoContractError("foundation invalid last page")
        requests_to_make.extend((agent, page, "data", foundation_list_url(agent, page)) for page in range(2, last + 1))
        requests_to_make.extend(
            [
                (agent, last + 1, "sentinel", foundation_list_url(agent, last + 1)),
                (agent, 1, "first_recheck", foundation_list_url(agent, 1)),
                (agent, last, "last_recheck", foundation_list_url(agent, last)),
                (agent, last + 1, "sentinel_recheck", foundation_list_url(agent, last + 1)),
            ]
        )
    _ensure_list_budget(requester, max_pages, len(requests_to_make))
    fetched_responses = requester.many([item[3] for item in requests_to_make], "list")
    fetched: dict[tuple[str, int, str], dict[str, Any]] = {}
    for item, response in zip(requests_to_make, fetched_responses):
        agent, page, role, _url = item
        fetched[(agent, page, role)] = _parse_foundation_page(_soup(response), agent, page)
    ledger: list[dict[str, Any]] = []
    page_counts: dict[str, dict[str, int]] = {}
    for agent in agents:
        last = first[agent]["last"]
        pages = [first[agent]] + [fetched[(agent, page, "data")] for page in range(2, last + 1)]
        branch_rows = [row for page_data in pages for row in page_data["rows"]]
        _assert_unique(branch_rows, f"foundation {agent}")
        sentinel = fetched[(agent, last + 1, "sentinel")]
        first_recheck = fetched[(agent, 1, "first_recheck")]
        last_recheck = fetched[(agent, last, "last_recheck")]
        sentinel_recheck = fetched[(agent, last + 1, "sentinel_recheck")]
        if sentinel["rows"] or sentinel_recheck["rows"]:
            raise GunpoContractError(f"foundation {agent}: sentinel is not empty")
        if first_recheck["last"] != last:
            raise GunpoContractError(f"foundation {agent}: advertised boundary changed")
        if _rows_signature(first_recheck["rows"]) != _rows_signature(first[agent]["rows"]):
            raise GunpoContractError(f"foundation {agent}: first boundary changed")
        if _rows_signature(last_recheck["rows"]) != _rows_signature(pages[-1]["rows"]):
            raise GunpoContractError(f"foundation {agent}: last boundary changed")
        if last_recheck["last"] != pages[-1]["last"]:
            raise GunpoContractError(f"foundation {agent}: last marker changed")
        if _rows_signature(sentinel_recheck["rows"]) != _rows_signature(sentinel["rows"]):
            raise GunpoContractError(f"foundation {agent}: sentinel changed")
        if sentinel_recheck["last"] != sentinel["last"]:
            raise GunpoContractError(f"foundation {agent}: sentinel marker changed")
        ledger.extend(branch_rows)
        page_counts[agent] = {
            **{str(page["page"]): len(page["rows"]) for page in pages},
            f"{last + 1}_sentinel": 0,
        }
    _assert_unique(ledger, "foundation full")
    current = [row for row in ledger if row["end"] >= cutoff]
    _ensure_detail_budget(requester.meta, detail_limit, len(current))
    # This legacy servlet intermittently stalls when many detail responses are
    # opened together.  Four readers stays below its observed connection cap.
    prior_workers = requester.workers
    requester.workers = min(requester.workers, 4)
    try:
        responses = requester.many([row["detail_url"] for row in current], "detail")
    finally:
        requester.workers = prior_workers
    output: list[dict[str, Any]] = []
    controls = attachments = 0
    for row, response in zip(current, responses):
        parsed, discovered, discarded = _foundation_detail(row, _soup(response))
        controls += discovered
        attachments += discarded
        output.append(parsed)
    return output, {
        "source_total_count": len(ledger),
        "current_source_count": len(current),
        "page_counts": page_counts,
        "application_controls_discovered": controls,
        "attachments_discarded": attachments,
        "branch_counts": dict(sorted(Counter(row["branch"] for row in output).items())),
    }


def _collect_media(
    requester: _Requester, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _ensure_list_budget(requester, max_pages, 1)
    first = _parse_media_page(_soup(requester.get(media_list_url(1), "list")), 1)
    total, last = first["total"], first["last"]
    if last != max(1, math.ceil(total / 10)):
        raise GunpoContractError("media declared total/last-page mismatch")
    requests_to_make = [(page, "data", media_list_url(page)) for page in range(2, last + 1)] + [
        (last + 1, "sentinel", media_list_url(last + 1)),
        (1, "first_recheck", media_list_url(1)),
        (last, "last_recheck", media_list_url(last)),
        (last + 1, "sentinel_recheck", media_list_url(last + 1)),
    ]
    _ensure_list_budget(requester, max_pages, len(requests_to_make))
    responses = requester.many([item[2] for item in requests_to_make], "list")
    fetched = {
        (page, role): _parse_media_page(_soup(response), page)
        for (page, role, _url), response in zip(requests_to_make, responses)
    }
    pages = [first] + [fetched[(page, "data")] for page in range(2, last + 1)]
    for page_data in pages:
        if (page_data["total"], page_data["last"]) != (total, last):
            raise GunpoContractError("media total/page boundary drift")
        expected = 10 if page_data["page"] < last else total - 10 * (last - 1)
        if len(page_data["rows"]) != expected:
            raise GunpoContractError(f"media page {page_data['page']}: row-count gap")
    ledger = [row for page_data in pages for row in page_data["rows"]]
    if len(ledger) != total:
        raise GunpoContractError("media declared total does not match parsed ledger")
    _assert_unique(ledger, "media")
    sentinel = fetched[(last + 1, "sentinel")]
    first_recheck = fetched[(1, "first_recheck")]
    last_recheck = fetched[(last, "last_recheck")]
    sentinel_recheck = fetched[(last + 1, "sentinel_recheck")]
    if sentinel["rows"] or sentinel_recheck["rows"]:
        raise GunpoContractError("media sentinel is not empty")
    if any(
        (item["total"], item["last"]) != (total, last)
        for item in (sentinel, first_recheck, last_recheck, sentinel_recheck)
    ):
        raise GunpoContractError("media boundary marker changed")
    if _rows_signature(first_recheck["rows"]) != _rows_signature(first["rows"]):
        raise GunpoContractError("media first boundary changed")
    if _rows_signature(last_recheck["rows"]) != _rows_signature(pages[-1]["rows"]):
        raise GunpoContractError("media last boundary changed")
    if _rows_signature(sentinel_recheck["rows"]) != _rows_signature(sentinel["rows"]):
        raise GunpoContractError("media sentinel changed")
    current = [row for row in ledger if row["end"] >= cutoff]
    _ensure_detail_budget(requester.meta, detail_limit, len(current))
    detail_responses = requester.many([row["detail_url"] for row in current], "detail")
    output: list[dict[str, Any]] = []
    controls = 0
    for row, response in zip(current, detail_responses):
        parsed, discovered = _media_detail(row, _soup(response))
        controls += discovered
        output.append(parsed)
    return output, {
        "source_total_count": len(ledger),
        "current_source_count": len(current),
        "declared_total": total,
        "last_page": last,
        "page_counts": {**{str(item["page"]): len(item["rows"]) for item in pages}, f"{last + 1}_sentinel": 0},
        "application_controls_discovered": controls,
        "branch_counts": {"군포시미디어센터": len(output)},
    }


def _collect_library(
    requester: _Requester, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    program_types = (1, 3)
    _ensure_list_budget(requester, max_pages, len(program_types))
    first_responses = requester.many([library_list_url(program_type, 0) for program_type in program_types], "list")
    first = {
        program_type: _library_payload(_json(response), program_type, 0)
        for program_type, response in zip(program_types, first_responses)
    }
    requests_to_make: list[tuple[int, int, str, str]] = []
    last_offsets: dict[int, int] = {}
    for program_type in program_types:
        total = first[program_type]["total"]
        last_offset = ((total - 1) // GUNPO_PAGE_SIZE) * GUNPO_PAGE_SIZE if total else 0
        last_offsets[program_type] = last_offset
        requests_to_make.extend(
            (program_type, offset, "data", library_list_url(program_type, offset))
            for offset in range(GUNPO_PAGE_SIZE, last_offset + 1, GUNPO_PAGE_SIZE)
        )
        sentinel_offset = last_offset + GUNPO_PAGE_SIZE
        requests_to_make.extend(
            [
                (program_type, sentinel_offset, "sentinel", library_list_url(program_type, sentinel_offset)),
                (program_type, 0, "first_recheck", library_list_url(program_type, 0)),
                (program_type, last_offset, "last_recheck", library_list_url(program_type, last_offset)),
                (program_type, sentinel_offset, "sentinel_recheck", library_list_url(program_type, sentinel_offset)),
            ]
        )
    _ensure_list_budget(requester, max_pages, len(requests_to_make))
    responses = requester.many([item[3] for item in requests_to_make], "list")
    fetched: dict[tuple[int, int, str], dict[str, Any]] = {}
    for item, response in zip(requests_to_make, responses):
        program_type, offset, role, _url = item
        fetched[(program_type, offset, role)] = _library_payload(
            _json(response), program_type, offset, first[program_type]["total"]
        )
    ledger: list[dict[str, Any]] = []
    chunk_counts: dict[str, dict[str, int]] = {}
    declared_totals: dict[str, int] = {}
    duplicate_source_count = 0
    for program_type in program_types:
        total = first[program_type]["total"]
        last_offset = last_offsets[program_type]
        chunks = [first[program_type]] + [
            fetched[(program_type, offset, "data")]
            for offset in range(GUNPO_PAGE_SIZE, last_offset + 1, GUNPO_PAGE_SIZE)
        ]
        for chunk in chunks:
            expected = min(GUNPO_PAGE_SIZE, max(0, total - chunk["offset"]))
            if len(chunk["rows"]) != expected:
                raise GunpoContractError(f"library type {program_type} offset {chunk['offset']}: chunk gap")
        raw_type_rows = [row for chunk in chunks for row in chunk["rows"]]
        if len(raw_type_rows) != total:
            raise GunpoContractError(f"library type {program_type}: declared total mismatch")
        type_rows, duplicate_count = _collapse_identical_identity_duplicates(
            raw_type_rows, f"library type {program_type}"
        )
        duplicate_source_count += duplicate_count
        _assert_unique(type_rows, f"library type {program_type}")
        sentinel_offset = last_offset + GUNPO_PAGE_SIZE
        sentinel = fetched[(program_type, sentinel_offset, "sentinel")]
        first_recheck = fetched[(program_type, 0, "first_recheck")]
        last_recheck = fetched[(program_type, last_offset, "last_recheck")]
        sentinel_recheck = fetched[(program_type, sentinel_offset, "sentinel_recheck")]
        if sentinel["rows"] or sentinel_recheck["rows"]:
            raise GunpoContractError(f"library type {program_type}: sentinel is not empty")
        if _rows_signature(first_recheck["rows"]) != _rows_signature(chunks[0]["rows"]):
            raise GunpoContractError(f"library type {program_type}: first boundary changed")
        if _rows_signature(last_recheck["rows"]) != _rows_signature(chunks[-1]["rows"]):
            raise GunpoContractError(f"library type {program_type}: last boundary changed")
        if _rows_signature(sentinel_recheck["rows"]) != _rows_signature(sentinel["rows"]):
            raise GunpoContractError(f"library type {program_type}: sentinel changed")
        ledger.extend(type_rows)
        declared_totals[str(program_type)] = total
        chunk_counts[str(program_type)] = {
            **{str(chunk["offset"]): len(chunk["rows"]) for chunk in chunks},
            f"{sentinel_offset}_sentinel": 0,
        }
    _assert_unique(ledger, "library full")
    current = [row for row in ledger if row["end"] >= cutoff]
    _ensure_detail_budget(requester.meta, detail_limit, len(current))
    responses = requester.many([row["detail_url"] for row in current], "detail")
    output: list[dict[str, Any]] = []
    sensitive = attachments = 0
    for row, response in zip(current, responses):
        parsed, discarded, discarded_attachments = _library_detail(row, _json(response))
        sensitive += discarded
        attachments += discarded_attachments
        output.append(parsed)
    return output, {
        "source_total_count": sum(declared_totals.values()),
        "declared_source_count": sum(declared_totals.values()),
        "unique_source_count": len(ledger),
        "duplicate_source_count": duplicate_source_count,
        "current_source_count": len(current),
        "declared_totals": declared_totals,
        "chunk_counts": chunk_counts,
        "sensitive_fields_discarded": sensitive,
        "attachments_discarded": attachments,
        "branch_counts": dict(sorted(Counter(row["branch"] for row in output).items())),
    }


def _collect_fmcs(
    owner: str,
    requester: _Requester,
    _cutoff: date,
    max_pages: int,
    detail_limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    company_response = requester.get(fmcs_company_url(owner), "registry")
    branches = GUNPO_URBAN_BRANCHES if owner == "urban" else GUNPO_YOUTH_BRANCHES
    _fmcs_registry(_json(company_response), branches)
    if owner == "youth":
        _fmcs_registry(
            _json(requester.get(fmcs_category_url(), "registry")),
            GUNPO_YOUTH_CATEGORIES,
            categories=True,
        )
        categories = list(GUNPO_YOUTH_CATEGORIES)
    else:
        categories = [""]
    partitions = [(category, status) for category in categories for status in ("R", "E")]
    _ensure_list_budget(requester, max_pages, len(partitions))
    first_responses = requester.many(
        [fmcs_list_url(owner, status, 1, category) for category, status in partitions],
        "list",
    )
    first = {
        (category, status): _fmcs_page(_json(response), owner, status, 1, category)
        for (category, status), response in zip(partitions, first_responses)
    }
    requests_to_make: list[tuple[str, str, int, str, str]] = []
    for category, status in partitions:
        requests_to_make.extend(
            [
                (category, status, 2, "sentinel", fmcs_list_url(owner, status, 2, category)),
                (category, status, 1, "first_recheck", fmcs_list_url(owner, status, 1, category)),
                (category, status, 2, "sentinel_recheck", fmcs_list_url(owner, status, 2, category)),
            ]
        )
    _ensure_list_budget(requester, max_pages, len(requests_to_make))
    responses = requester.many([item[4] for item in requests_to_make], "list")
    fetched: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for item, response in zip(requests_to_make, responses):
        category, status, page, role, _url = item
        fetched[(category, status, page, role)] = _fmcs_page(_json(response), owner, status, page, category)
    ledger: list[dict[str, Any]] = []
    partition_counts: dict[str, int] = {}
    for category, status in partitions:
        page = first[(category, status)]
        key = f"{category or 'ALL'}:{status}"
        if len(page["rows"]) != page["total"]:
            raise GunpoContractError(f"FMCS {owner} {key}: declared total mismatch")
        sentinel = fetched[(category, status, 2, "sentinel")]
        first_recheck = fetched[(category, status, 1, "first_recheck")]
        sentinel_recheck = fetched[(category, status, 2, "sentinel_recheck")]
        if sentinel["rows"] or sentinel_recheck["rows"]:
            raise GunpoContractError(f"FMCS {owner} {key}: sentinel is not empty")
        if _rows_signature(first_recheck["rows"]) != _rows_signature(page["rows"]):
            raise GunpoContractError(f"FMCS {owner} {key}: boundary changed")
        if _rows_signature(sentinel_recheck["rows"]) != _rows_signature(sentinel["rows"]):
            raise GunpoContractError(f"FMCS {owner} {key}: sentinel changed")
        ledger.extend(page["rows"])
        partition_counts[key] = len(page["rows"])
    _assert_unique(ledger, f"FMCS {owner} full")
    current = [row for row in ledger if _fmcs_is_current(row)]
    _ensure_detail_budget(requester.meta, detail_limit, len(current))
    responses = requester.many([row["detail_url"] for row in current], "detail")
    output: list[dict[str, Any]] = []
    controls = 0
    for row, response in zip(current, responses):
        parsed, discovered = _fmcs_detail(owner, row, _soup(response))
        controls += discovered
        output.append(parsed)
    return output, {
        "source_total_count": len(ledger),
        "current_source_count": len(current),
        "partition_counts": partition_counts,
        "source_status_counts": dict(sorted(Counter(row["raw_status"] for row in ledger).items())),
        "partition_status_counts": dict(
            sorted(Counter(f"{row['partition']}:{row['raw_status']}" for row in ledger).items())
        ),
        "cross_partition_status_count": sum(
            row["raw_status"] not in ({"R"} if row["partition"] == "R" else {"E", "W"}) for row in ledger
        ),
        "official_branch_registry": dict(branches),
        "official_category_registry": dict(GUNPO_YOUTH_CATEGORIES) if owner == "youth" else {},
        "application_controls_discovered": controls,
        "branch_counts": dict(sorted(Counter(row["branch"] for row in output).items())),
    }


def _collect_picturebook(
    requester: _Requester, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _ensure_list_budget(requester, max_pages, 1)
    first = _picturebook_page(_json(requester.get(picturebook_list_url(1), "list")), 1)
    if len(first["rows"]) != first["total"]:
        raise GunpoContractError("picturebook declared total mismatch")
    _assert_unique(first["rows"], "picturebook")
    _ensure_list_budget(requester, max_pages, 3)
    responses = requester.many(
        [picturebook_list_url(2), picturebook_list_url(1), picturebook_list_url(2)],
        "list",
    )
    sentinel = _picturebook_page(_json(responses[0]), 2, first["total"])
    first_recheck = _picturebook_page(_json(responses[1]), 1, first["total"])
    sentinel_recheck = _picturebook_page(_json(responses[2]), 2, first["total"])
    if sentinel["rows"] or sentinel_recheck["rows"]:
        raise GunpoContractError("picturebook sentinel is not empty")
    if _rows_signature(first_recheck["rows"]) != _rows_signature(first["rows"]):
        raise GunpoContractError("picturebook first boundary changed")
    if _rows_signature(sentinel_recheck["rows"]) != _rows_signature(sentinel["rows"]):
        raise GunpoContractError("picturebook sentinel changed")
    ledger = first["rows"]
    current = [row for row in ledger if row["end"] >= cutoff]
    _ensure_detail_budget(requester.meta, detail_limit, len(current))
    detail_responses = requester.many([row["detail_url"] for row in current], "detail")
    output: list[dict[str, Any]] = []
    sensitive = 0
    for row, response in zip(current, detail_responses):
        parsed, discarded = _picturebook_detail(row, _json(response), cutoff)
        sensitive += discarded
        output.append(parsed)
    return output, {
        "source_total_count": len(ledger),
        "current_source_count": len(current),
        "declared_total": first["total"],
        "page_counts": {"1": len(ledger), "2_sentinel": 0},
        "sensitive_fields_discarded": sensitive,
        "branch_counts": {"그림책꿈마루": len(output)},
    }


def _collect_flying(
    requester: _Requester, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _ensure_list_budget(requester, max_pages, 1)
    first = _parse_flying_page(_soup(requester.get(flying_list_url(1), "list")), 1)
    total, last = first["total"], first["last"]
    requests_to_make = [(page, "data", flying_list_url(page)) for page in range(2, last + 1)] + [
        (last + 1, "sentinel", flying_list_url(last + 1)),
        (1, "first_recheck", flying_list_url(1)),
        (last, "last_recheck", flying_list_url(last)),
        (last + 1, "sentinel_recheck", flying_list_url(last + 1)),
    ]
    _ensure_list_budget(requester, max_pages, len(requests_to_make))
    responses = requester.many([item[2] for item in requests_to_make], "list")
    fetched = {
        (page, role): _parse_flying_page(_soup(response), page)
        for (page, role, _url), response in zip(requests_to_make, responses)
    }
    pages = [first] + [fetched[(page, "data")] for page in range(2, last + 1)]
    if any((item["total"], item["last"]) != (total, last) for item in pages):
        raise GunpoContractError("Flying total/page boundary drift")
    ledger = [row for item in pages for row in item["rows"]]
    if len(ledger) != total:
        raise GunpoContractError("Flying declared total does not match ledger")
    _assert_unique(ledger, "Flying")
    sentinel = fetched[(last + 1, "sentinel")]
    first_recheck = fetched[(1, "first_recheck")]
    last_recheck = fetched[(last, "last_recheck")]
    sentinel_recheck = fetched[(last + 1, "sentinel_recheck")]
    if sentinel["rows"] or sentinel_recheck["rows"]:
        raise GunpoContractError("Flying sentinel is not empty")
    if any(
        (item["total"], item["last"]) != (total, last)
        for item in (sentinel, first_recheck, last_recheck, sentinel_recheck)
    ):
        raise GunpoContractError("Flying boundary marker changed")
    if _rows_signature(first_recheck["rows"]) != _rows_signature(first["rows"]):
        raise GunpoContractError("Flying first boundary changed")
    if _rows_signature(last_recheck["rows"]) != _rows_signature(pages[-1]["rows"]):
        raise GunpoContractError("Flying last boundary changed")
    if _rows_signature(sentinel_recheck["rows"]) != _rows_signature(sentinel["rows"]):
        raise GunpoContractError("Flying sentinel changed")
    source_current = [row for row in ledger if row["end"] is not None and row["end"] >= cutoff]

    def is_public_education(row: Mapping[str, Any]) -> bool:
        if row["category"] != "교육":
            return False
        # The owner has one administratively misclassified committee-recruitment
        # record in the education category.  Keep it in the audited source
        # ledger but not in the public course result partition.
        return not bool(
            re.search(
                r"(?:위원회|서포터즈|입주자|멤버십).*(?:모집|선발)",
                row["title"],
            )
        )

    category_current = [row for row in source_current if row["category"] == "교육"]
    current = [row for row in category_current if is_public_education(row)]
    if any(
        value is None for row in current for value in (row["apply_start"], row["apply_end"], row["start"], row["end"])
    ):
        raise GunpoContractError("Flying current education row has incomplete dates")
    _ensure_detail_budget(requester.meta, detail_limit, len(current))
    detail_responses = requester.many([row["detail_url"] for row in current], "detail")
    output: list[dict[str, Any]] = []
    controls = 0
    for row, response in zip(current, detail_responses):
        parsed, discovered = _flying_detail(row, _soup(response))
        controls += discovered
        output.append(parsed)
    return output, {
        "source_total_count": len(ledger),
        "current_source_count": len(source_current),
        "education_current_count": len(current),
        "excluded_partition_count": len(source_current) - len(current),
        "excluded_non_course_count": len(category_current) - len(current),
        "declared_total": total,
        "last_page": last,
        "page_counts": {**{str(item["page"]): len(item["rows"]) for item in pages}, f"{last + 1}_sentinel": 0},
        "application_controls_discovered": controls,
        "branch_counts": {"군포시 청년공간 플라잉": len(output)},
    }


_OWNER_COLLECTORS: Mapping[
    str,
    Callable[[_Requester, date, int, int], tuple[list[dict[str, Any]], dict[str, Any]]],
] = {
    "city": _collect_city,
    "info": _collect_info,
    "foundation": _collect_foundation,
    "media": _collect_media,
    "library": _collect_library,
    "urban": lambda requester, cutoff, max_pages, detail_limit: _collect_fmcs(
        "urban", requester, cutoff, max_pages, detail_limit
    ),
    "youth": lambda requester, cutoff, max_pages, detail_limit: _collect_fmcs(
        "youth", requester, cutoff, max_pages, detail_limit
    ),
    "picturebook": _collect_picturebook,
    "flying": _collect_flying,
}


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def collect_gunpo_education_courses(
    target: Any,
    timeout: int = 35,
    max_pages: int = GUNPO_MAX_PAGES,
    detail_limit: int = GUNPO_MAX_DETAILS,
    *,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GUNPO_MAX_WORKERS,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one fail-closed, current Gunpo owner snapshot.

    Production routing supplies the repository HTTP fetcher.  Direct network
    access is intentionally opt-in so tests cannot silently leave the fixture
    boundary.
    """

    cutoff = _today(today)
    owner = owner_for_target(target)
    if not owner:
        meta = _initial_meta("city", cutoff)
        meta.update(
            {
                "owner": "",
                "provider": _clean(_target_value(target, "provider")),
                "canonical_url": _clean(_target_value(target, "url")),
                "configured_collection_error": "non-canonical Gunpo education target",
            }
        )
        return [], GUNPO_PARSER, meta
    meta = _initial_meta(owner, cutoff)
    try:
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        request_timeout = int(timeout)
        workers = int(max_workers)
        if min(allowed_pages, request_timeout, workers) < 1 or allowed_details < 0:
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "invalid collection limits"
        return [], GUNPO_PARSER, meta
    if fetcher is None and session_factory is None and not allow_raw_requests_for_tests:
        meta["configured_collection_error"] = (
            "raw requests disabled; inject the managed session/fetcher or explicitly opt in"
        )
        return [], GUNPO_PARSER, meta
    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or (lambda: _raw_session(owner))
    if owner == "media" and session_factory is not None:

        def media_factory() -> requests.Session:
            return _media_managed_session(session_factory)

        current_factory = media_factory
    requester = _Requester(
        owner,
        current_factory,
        current_fetcher,
        request_timeout,
        workers,
        meta,
        fetcher is not None,
    )
    try:
        rows, audit = _OWNER_COLLECTORS[owner](requester, cutoff, allowed_pages, allowed_details)
        meta.update(audit)
        if meta["detail_requests"] != len(rows) + meta.get("excluded_test_count", 0):
            raise GunpoContractError("not every eligible current row has a public detail")
        original_ids = [row.get("provider_course_id") for row in rows]
        deduper = dedupe_rows or _dedupe_default
        deduped = list(deduper(rows))
        if any(not isinstance(row, Mapping) for row in deduped):
            raise GunpoContractError("dedupe returned a non-object row")
        if [row.get("provider_course_id") for row in deduped] != original_ids:
            raise GunpoContractError("dedupe changed Gunpo owner identity/cardinality")
        privacy_errors = [error for row in deduped for error in _privacy_errors(row)]
        if privacy_errors:
            raise GunpoContractError("; ".join(dict.fromkeys(privacy_errors)))
        if any(row.get("application_url") for row in deduped):
            raise GunpoContractError("application endpoint escaped output boundary")
        rows = sorted(
            (dict(row) for row in deduped),
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            ),
        )
        meta.update(
            {
                "source_total": int(meta.get("source_total_count") or 0),
                "source_rows": int(meta.get("source_total_count") or 0),
                "discovered_links": int(meta.get("source_total_count") or 0),
                "current_count": int(meta.get("current_source_count") or 0),
                "pages": int(meta.get("list_requests") or 0),
                "detail_pages": int(meta.get("detail_requests") or 0),
                "pagination_detected": int(meta.get("list_requests") or 0) > 1,
                "returned_count": len(rows),
                "title_identity_hash": _signature(
                    [
                        (
                            row["title"],
                            row["start_date"],
                            row["end_date"],
                            row["branch"],
                        )
                        for row in rows
                    ]
                ),
                "snapshot_identity_hash": _signature([row["provider_course_id"] for row in rows]),
                "pagination_complete": True,
                "sentinel_verified": True,
                "boundary_recheck_verified": True,
                "details_complete": True,
                "pii_safe": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not rows,
                "no_current_reason": (
                    "complete owner ledger has no current/future education courses" if not rows else ""
                ),
                "configured_collection_error": "",
            }
        )
        return rows, GUNPO_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "returned_count": 0,
                "pagination_complete": False,
                "sentinel_verified": False,
                "boundary_recheck_verified": False,
                "details_complete": False,
                "pii_safe": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "no_current_data": False,
                "no_current_reason": "",
                "configured_collection_error": _clean(exc) or type(exc).__name__,
            }
        )
        return [], GUNPO_PARSER, meta
    finally:
        requester.close()


collect = collect_gunpo_education_courses


__all__ = [
    "GUNPO_MUNICIPALITY_CODE",
    "GUNPO_MUNICIPALITY_NAME",
    "GUNPO_CITY_PROVIDER",
    "GUNPO_INFO_PROVIDER",
    "GUNPO_FOUNDATION_PROVIDER",
    "GUNPO_MEDIA_PROVIDER",
    "GUNPO_LIBRARY_PROVIDER",
    "GUNPO_URBAN_PROVIDER",
    "GUNPO_YOUTH_PROVIDER",
    "GUNPO_PICTUREBOOK_PROVIDER",
    "GUNPO_FLYING_PROVIDER",
    "GUNPO_CITY_URL",
    "GUNPO_INFO_URL",
    "GUNPO_FOUNDATION_URL",
    "GUNPO_MEDIA_URL",
    "GUNPO_LIBRARY_URL",
    "GUNPO_URBAN_URL",
    "GUNPO_YOUTH_URL",
    "GUNPO_PICTUREBOOK_URL",
    "GUNPO_FLYING_URL",
    "GUNPO_OWNERS",
    "GUNPO_DUPLICATE_ALIASES",
    "GUNPO_EXCLUDED_BOUNDARIES",
    "GUNPO_FOUNDATION_BRANCHES",
    "GUNPO_CITY_BRANCHES",
    "GUNPO_LIBRARY_BRANCHES",
    "GUNPO_URBAN_BRANCHES",
    "GUNPO_URBAN_ARCHIVE_BRANCHES",
    "GUNPO_YOUTH_BRANCHES",
    "GUNPO_YOUTH_CATEGORIES",
    "GUNPO_OWNER_AUDIT_BASELINE",
    "GUNPO_PARSER",
    "GunpoContractError",
    "owner_for_target",
    "is_gunpo_education_target",
    "is_target",
    "city_list_url",
    "city_detail_url",
    "info_list_url",
    "info_detail_url",
    "foundation_list_url",
    "foundation_detail_url",
    "media_list_url",
    "media_detail_url",
    "library_list_url",
    "library_detail_url",
    "fmcs_company_url",
    "fmcs_category_url",
    "fmcs_list_url",
    "fmcs_detail_url",
    "picturebook_list_url",
    "picturebook_detail_url",
    "flying_list_url",
    "flying_detail_url",
    "collect_gunpo_education_courses",
    "collect",
]
