"""Fail-closed collectors for Yangpyeong's public education owners.

Yangpyeong publishes four disjoint, structured catalogues: the local GSEEK
offline-course portal, the lifelong-learning-centre swimming FMCS, Yangpyeong
Garden education, and the county library event catalogue.  The twelve
resident-centre sites are notice/PDF surfaces rather than another structured
course owner; the single GSEEK resident-centre guide is therefore excluded as
an aggregate shell.

Only audited public registry/list/detail routes are reachable from this
module.  Application, login, applicant, payment, attachment, form and PII
routes are not allowlisted and are never persisted.  Each owner is collected
through its complete source ledger, an immediate empty post-boundary sentinel,
stable first/last/sentinel rechecks, and public details for every current or
future source record before semantic exclusions are applied.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from html import unescape
import hashlib
import json
import math
import re
import ssl
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from utils.outbound_http import OutboundRequestBlocked, SafeSession, _PinnedHTTPAdapter


YANGPYEONG_MUNICIPALITY_CODE = "4183000000"
YANGPYEONG_MUNICIPALITY_NAME = "경기도 양평군"

YANGPYEONG_GSEEK_PROVIDER = "MUNI_YPEDU_GSEEK_KR_41263F0B"
YANGPYEONG_POOL_PROVIDER = "MUNI_WWW_YP21_GO_KR_EA0D7B81"
YANGPYEONG_GARDEN_PROVIDER = "MUNI_WWW_YP21_GO_KR_632CD45F"
YANGPYEONG_LIBRARY_PROVIDER = "MUNI_WWW_YPLIB_GO_KR_C3854B7C"

YANGPYEONG_GSEEK_URL = "https://ypedu.gseek.kr/user/course/offline/list"
YANGPYEONG_GSEEK_CO_SPONSOR_ID = "G000012"
YANGPYEONG_POOL_URL = "https://www.yp21.go.kr/pool/fmcs/9"
YANGPYEONG_GARDEN_URL = "https://www.yp21.go.kr/ypjeongwon/selectGardenEdcWebList.do?key=3852"
YANGPYEONG_LIBRARY_URL = "https://www.yplib.go.kr/libProgramInfo"

YANGPYEONG_GSEEK_CANDIDATE_ID = "MUNI_IR_04D8D249A3A6"
YANGPYEONG_POOL_CANDIDATE_ID = "MUNI_IR_A72076267363"
YANGPYEONG_GARDEN_CANDIDATE_ID = "MUNI_IR_69132969A315"
YANGPYEONG_LIBRARY_CANDIDATE_ID = "MUNI_IR_DB2254AB0395"
YANGPYEONG_LEGACY_GSEEK_CANDIDATE_ID = "MUNI_IR_C1DAFB00F74A"
YANGPYEONG_LEGACY_GSEEK_URL = "https://ypedu.gseek.kr/user/lifeLong/visitLecture/intro"

YANGPYEONG_OWNERS: Mapping[str, Mapping[str, str]] = {
    "gseek": {
        "provider": YANGPYEONG_GSEEK_PROVIDER,
        "url": YANGPYEONG_GSEEK_URL,
        "candidate_id": YANGPYEONG_GSEEK_CANDIDATE_ID,
    },
    "pool": {
        "provider": YANGPYEONG_POOL_PROVIDER,
        "url": YANGPYEONG_POOL_URL,
        "candidate_id": YANGPYEONG_POOL_CANDIDATE_ID,
    },
    "garden": {
        "provider": YANGPYEONG_GARDEN_PROVIDER,
        "url": YANGPYEONG_GARDEN_URL,
        "candidate_id": YANGPYEONG_GARDEN_CANDIDATE_ID,
    },
    "library": {
        "provider": YANGPYEONG_LIBRARY_PROVIDER,
        "url": YANGPYEONG_LIBRARY_URL,
        "candidate_id": YANGPYEONG_LIBRARY_CANDIDATE_ID,
    },
}

YANGPYEONG_RESIDENT_CENTRES = (
    "양평읍 주민자치센터",
    "강상면 주민자치센터",
    "강하면 주민자치센터",
    "양서면 주민자치센터",
    "옥천면 주민자치센터",
    "서종면 주민자치센터",
    "단월면 주민자치센터",
    "청운면 주민자치센터",
    "양동면 주민자치센터",
    "지평면 주민자치센터",
    "용문면 주민자치센터",
    "개군면 주민자치센터",
)

YANGPYEONG_GSEEK_BRANCHES = frozenset(
    {
        "매력캠퍼스(평생학습센터)",
        "우리동네 학습여행",
        "양평문화원",
        "양평군 장애인복지관",
        "양평군 노인복지관",
        "양평 도서관",
        "양평군 주민자치센터",
        "양평친환경농업대학",
    }
)

YANGPYEONG_LIBRARY_BRANCHES: Mapping[str, str] = {
    "MA": "양평도서관",
    "MD": "양서친환경도서관",
    "ME": "양동도서관",
    "MC": "용문도서관",
    "MH": "지평도서관",
    "MI": "강상작은도서관",
    "MF": "강하작은도서관",
    "MB": "옥천작은도서관",
    "MG": "서종작은도서관",
    "MM": "단월작은도서관",
    "MJ": "청운작은도서관",
    "ML": "개군작은도서관",
    "ZA": "양평군 작은도서관",
}

YANGPYEONG_EXCLUDED_BOUNDARIES: Mapping[str, str] = {
    YANGPYEONG_LEGACY_GSEEK_URL: (
        "program introduction page; retarget the incumbent provider to the complete offline-course ledger"
    ),
    "gseek_external_institution_guides": ("fake-capacity annual guide records that point to independent owners"),
    "gseek_resident_centre_guide": ("single aggregate guide for twelve notice/PDF resident-centre sites"),
    "library_confirmation_notices": ("post-selection result notices duplicating the original recruitment row"),
    "library_subscription_services": ("book/content subscription services rather than education or lectures"),
    "resident_centre_sites": ("twelve official notice/image/PDF boards without a stable structured course ledger"),
}

YANGPYEONG_AUDIT_BASELINE: Mapping[str, Mapping[str, int]] = {
    "gseek": {"source": 87, "current": 17, "returned": 10},
    "pool": {"source": 58, "current": 26, "returned": 26},
    "garden": {"source": 76, "current": 2, "returned": 2},
    "library": {"source": 669, "current": 16, "returned": 8},
}

YANGPYEONG_PARSER = (
    "yangpyeong_four_disjoint_official_owners+complete_ledgers+"
    "post_boundary_empty_sentinels+stable_first_last_sentinel_rechecks+"
    "all_current_public_details+official_branch_normalization+"
    "parent_result_service_test_exclusions+no_application_login_attachment_pii_routes"
)
YANGPYEONG_PAGE_SIZE = 1000
YANGPYEONG_GSEEK_PAGE_SIZE = 9
YANGPYEONG_LIBRARY_PAGE_SIZE = 10
YANGPYEONG_MAX_PAGES = 100
YANGPYEONG_MAX_DETAILS = 100
YANGPYEONG_MAX_WORKERS = 4
YANGPYEONG_MAX_BYTES = 5_000_000


class YangpyeongContractError(RuntimeError):
    """Raised when a Yangpyeong source violates its audited public contract."""


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_DATE = re.compile(r"(?<!\d)(20\d{2})[.-](\d{1,2})[.-](\d{1,2})(?!\d)")
_TIME = re.compile(r"(?<!\d)(\d{1,2}:\d{2})(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")
_FORBIDDEN_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "teacher",
        "description",
        "content",
        "attachments",
        "attachment_url",
        "image_url",
        "request_form",
        "applicant",
        "user_list",
    }
)

# www.yplib.go.kr currently omits this official intermediate from its TLS
# handshake.  The managed adapter adds it to the default trust store while
# retaining certificate, hostname and SafeSession DNS-pin verification.
_GLOBALSIGN_RSA_OV_SSL_CA_2018_PEM = """-----BEGIN CERTIFICATE-----
MIIETjCCAzagAwIBAgINAe5fIh38YjvUMzqFVzANBgkqhkiG9w0BAQsFADBMMSAw
HgYDVQQLExdHbG9iYWxTaWduIFJvb3QgQ0EgLSBSMzETMBEGA1UEChMKR2xvYmFs
U2lnbjETMBEGA1UEAxMKR2xvYmFsU2lnbjAeFw0xODExMjEwMDAwMDBaFw0yODEx
MjEwMDAwMDBaMFAxCzAJBgNVBAYTAkJFMRkwFwYDVQQKExBHbG9iYWxTaWduIG52
LXNhMSYwJAYDVQQDEx1HbG9iYWxTaWduIFJTQSBPViBTU0wgQ0EgMjAxODCCASIw
DQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAKdaydUMGCEAI9WXD+uu3Vxoa2uP
UGATeoHLl+6OimGUSyZ59gSnKvuk2la77qCk8HuKf1UfR5NhDW5xUTolJAgvjOH3
idaSz6+zpz8w7bXfIa7+9UQX/dhj2S/TgVprX9NHsKzyqzskeU8fxy7quRU6fBhM
abO1IFkJXinDY+YuRluqlJBJDrnw9UqhCS98NE3QvADFBlV5Bs6i0BDxSEPouVq1
lVW9MdIbPYa+oewNEtssmSStR8JvA+Z6cLVwzM0nLKWMjsIYPJLJLnNvBhBWk0Cq
o8VS++XFBdZpaFwGue5RieGKDkFNm5KQConpFmvv73W+eka440eKHRwup08CAwEA
AaOCASkwggElMA4GA1UdDwEB/wQEAwIBhjASBgNVHRMBAf8ECDAGAQH/AgEAMB0G
A1UdDgQWBBT473/yzXhnqN5vjySNiPGHAwKz6zAfBgNVHSMEGDAWgBSP8Et/qC5F
JK5NUPpjmove4t0bvDA+BggrBgEFBQcBAQQyMDAwLgYIKwYBBQUHMAGGImh0dHA6
Ly9vY3NwMi5nbG9iYWxzaWduLmNvbS9yb290cjMwNgYDVR0fBC8wLTAroCmgJ4Yl
aHR0cDovL2NybC5nbG9iYWxzaWduLmNvbS9yb290LXIzLmNybDBHBgNVHSAEQDA+
MDwGBFUdIAAwNDAyBggrBgEFBQcCARYmaHR0cHM6Ly93d3cuZ2xvYmFsc2lnbi5j
b20vcmVwb3NpdG9yeS8wDQYJKoZIhvcNAQELBQADggEBAJmQyC1fQorUC2bbmANz
EdSIhlIoU4r7rd/9c446ZwTbw1MUcBQJfMPg+NccmBqixD7b6QDjynCy8SIwIVbb
0615XoFYC20UgDX1b10d65pHBf9ZjQCxQNqQmJYaumxtf4z1s4DfjGRzNpZ5eWl0
6r/4ngGPoJVpjemEuunl1Ig423g7mNA2eymw0lIYkN5SQwCuaifIFJ6GlazhgDEw
fpolu4usBCOmmQDo8dIm7A9+O4orkjgTHY+GzYZSR+Y0fFukAj6KYXwidlNalFMz
hriSqHKvoflShx8xpfywgVcvzfTO3PYkz6fiNJBonf6q8amaEsybwMbDqKWwIX7e
SPY=
-----END CERTIFICATE-----"""


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", unescape(str(value or "")).replace("\xa0", " ").replace("\u200b", "")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def owner_for_target(target: Any) -> str:
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    for owner, config in YANGPYEONG_OWNERS.items():
        if provider == config["provider"] and url == config["url"]:
            return owner
    return ""


def is_yangpyeong_target(target: Any) -> bool:
    return bool(owner_for_target(target))


is_target = is_yangpyeong_target


def _positive_int(value: Any, *, zero: bool = False) -> bool:
    text = _clean(value)
    return bool(re.fullmatch(r"\d+", text)) and (zero or int(text) > 0)


def _query(url: str) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(url)
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=20)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise YangpyeongContractError("malformed request URL") from exc
    if len(pairs) != len({key for key, _ in pairs}):
        raise YangpyeongContractError("duplicate request query key")
    if (
        parsed.scheme != "https"
        or port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.params
    ):
        raise YangpyeongContractError("request escaped exact HTTPS public boundary")
    return parsed, dict(pairs)


def gseek_list_data(page: int) -> dict[str, str]:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be positive")
    start = 1 + (page - 1) * YANGPYEONG_GSEEK_PAGE_SIZE
    return {
        "s_sort_by": "",
        "s_row_start": str(start),
        "s_row_end": str(start + YANGPYEONG_GSEEK_PAGE_SIZE),
        "resion": "",
    }


def gseek_detail_url(subject_sn: Any, cycle_sn: Any) -> str:
    subject_sn, cycle_sn = _clean(subject_sn), _clean(cycle_sn)
    if not _positive_int(subject_sn) or not _positive_int(cycle_sn):
        raise ValueError("invalid GSEEK course identity")
    return "https://ypedu.gseek.kr/user/course/offline/view?" + urlencode(
        (("s_sbjct_sn", subject_sn), ("s_sbjct_cycl_sn", cycle_sn))
    )


def pool_company_data() -> dict[str, str]:
    return {"type": "L"}


def pool_category_data() -> dict[str, str]:
    return {"company_code": "YP21NET"}


def pool_list_data(partition: str, page: int) -> dict[str, str]:
    if partition not in {"R", "E"} or not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("invalid FMCS partition/page")
    return {
        "company_code": "YP21NET",
        "mem_no": "",
        "search_type": partition,
        "category_cd": "",
        "category_level": "9",
        "class_nm": "",
        "train_day": "",
        "adult_gubn": "",
        "lecturer_nm": "",
        "page": str(page),
        "page_size": str(YANGPYEONG_PAGE_SIZE),
    }


def pool_detail_url(class_cd: Any) -> str:
    class_cd = _clean(class_cd)
    if not re.fullmatch(r"\d{5}", class_cd):
        raise ValueError("invalid FMCS class identity")
    return "https://www.yp21.go.kr/pool/fmcs/9?" + urlencode(
        (("action", "read"), ("comcd", "YP21NET"), ("classcd", class_cd), ("type", "R"))
    )


def garden_list_url(page: int) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be positive")
    return "https://www.yp21.go.kr/ypjeongwon/selectGardenEdcWebList.do?" + urlencode(
        (
            ("key", "3852"),
            ("pageUnit", str(YANGPYEONG_PAGE_SIZE)),
            ("pageIndex", str(page)),
            ("searchCnd", "all"),
            ("searchKrwd", ""),
        )
    )


def garden_detail_url(garden_no: Any) -> str:
    garden_no = _clean(garden_no)
    if not _positive_int(garden_no):
        raise ValueError("invalid garden course identity")
    return "https://www.yp21.go.kr/ypjeongwon/selectGardenEdcWebView.do?" + urlencode(
        (
            ("gardenNo", garden_no),
            ("key", "3852"),
            ("pageIndex", "1"),
            ("pageUnit", str(YANGPYEONG_PAGE_SIZE)),
            ("searchCnd", "all"),
        )
    )


def library_list_url(page: int) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be positive")
    return "https://www.yplib.go.kr/user/service/culture/event/list?" + urlencode(
        (
            ("manageCode", "ALL"),
            ("searchYear", ""),
            ("searchMonth", ""),
            ("userId", ""),
            ("pageIndex", str(page)),
        )
    )


def library_detail_url(rec_key: Any) -> str:
    rec_key = _clean(rec_key)
    if not _positive_int(rec_key):
        raise ValueError("invalid library event identity")
    return "https://www.yplib.go.kr/user/service/culture/event/detail?" + urlencode(
        (("recKey", rec_key), ("userId", ""))
    )


def _classify_url(
    owner: str,
    method: str,
    url: str,
    data: Optional[Mapping[str, Any]] = None,
) -> str:
    parsed, query = _query(url)
    host, path, method = (parsed.hostname or "").lower(), parsed.path, method.upper()
    form = {str(key): _clean(value) for key, value in (data or {}).items()}
    if owner == "gseek":
        if host != "ypedu.gseek.kr":
            raise YangpyeongContractError("GSEEK host drift")
        if method == "POST" and path == "/user/course/offline/list/search" and not query:
            if set(form) != {"s_sort_by", "s_row_start", "s_row_end", "resion"}:
                raise YangpyeongContractError("GSEEK list form drift")
            if (
                form["s_sort_by"]
                or form["resion"]
                or not all(_positive_int(form[key]) for key in ("s_row_start", "s_row_end"))
            ):
                raise YangpyeongContractError("GSEEK list filter drift")
            start, end = int(form["s_row_start"]), int(form["s_row_end"])
            if end - start != YANGPYEONG_GSEEK_PAGE_SIZE or (start - 1) % YANGPYEONG_GSEEK_PAGE_SIZE:
                raise YangpyeongContractError("GSEEK paging drift")
            return "list"
        if method == "GET" and path == "/user/course/offline/view":
            if set(query) == {"s_sbjct_sn", "s_sbjct_cycl_sn"} and all(_positive_int(query[key]) for key in query):
                return "detail"
    elif owner == "pool":
        if host != "www.yp21.go.kr":
            raise YangpyeongContractError("pool host drift")
        if method == "POST" and not query:
            if path == "/pool/rest/common/company" and form == pool_company_data():
                return "registry"
            if path == "/pool/rest/common/category" and form == pool_category_data():
                return "registry"
            if path == "/pool/rest/lecture/list":
                required = set(pool_list_data("R", 1))
                if set(form) != required or form["search_type"] not in {"R", "E"}:
                    raise YangpyeongContractError("pool list form drift")
                expected = pool_list_data(form["search_type"], int(form["page"]) if _positive_int(form["page"]) else 0)
                if form != expected:
                    raise YangpyeongContractError("pool list scope drift")
                return "list"
        if method == "GET" and path == "/pool/fmcs/9":
            if (
                set(query) == {"action", "comcd", "classcd", "type"}
                and query["action"] == "read"
                and query["comcd"] == "YP21NET"
                and re.fullmatch(r"\d{5}", query["classcd"])
                and query["type"] == "R"
            ):
                return "detail"
    elif owner == "garden":
        if host != "www.yp21.go.kr" or method != "GET":
            raise YangpyeongContractError("garden host/method drift")
        if path == "/ypjeongwon/selectGardenEdcWebList.do":
            if (
                set(query) == {"key", "pageUnit", "pageIndex", "searchCnd", "searchKrwd"}
                and query["key"] == "3852"
                and query["pageUnit"] == str(YANGPYEONG_PAGE_SIZE)
                and query["searchCnd"] == "all"
                and not query["searchKrwd"]
                and _positive_int(query["pageIndex"])
            ):
                return "list"
        if path == "/ypjeongwon/selectGardenEdcWebView.do":
            if (
                set(query) == {"gardenNo", "key", "pageIndex", "pageUnit", "searchCnd"}
                and query["key"] == "3852"
                and query["pageIndex"] == "1"
                and query["pageUnit"] == str(YANGPYEONG_PAGE_SIZE)
                and query["searchCnd"] == "all"
                and _positive_int(query["gardenNo"])
            ):
                return "detail"
    elif owner == "library":
        if host != "www.yplib.go.kr" or method != "GET":
            raise YangpyeongContractError("library host/method drift")
        if path == "/user/service/culture/event/list":
            if (
                set(query) == {"manageCode", "searchYear", "searchMonth", "userId", "pageIndex"}
                and query["manageCode"] == "ALL"
                and not query["searchYear"]
                and not query["searchMonth"]
                and not query["userId"]
                and _positive_int(query["pageIndex"])
            ):
                return "list"
        if path == "/user/service/culture/event/detail":
            if set(query) == {"recKey", "userId"} and _positive_int(query["recKey"]) and not query["userId"]:
                return "detail"
    raise YangpyeongContractError(f"refusing unaudited {owner} {method} route")


@lru_cache(maxsize=1)
def _library_certificate_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=_GLOBALSIGN_RSA_OV_SSL_CA_2018_PEM)
    return context


class _YangpyeongLibraryPinnedAdapter(_PinnedHTTPAdapter):
    """Complete yplib's chain without weakening SafeSession's DNS pinning."""

    def get_connection_with_tls_context(
        self,
        request: requests.PreparedRequest,
        verify: Any,
        proxies: Optional[dict[str, str]] = None,
        cert: Any = None,
    ) -> Any:
        if proxies and any(proxies.values()):
            raise OutboundRequestBlocked("Outbound HTTP proxies are not permitted")
        selected = getattr(request, "_mooncen_selected_address", "")
        original = getattr(request, "_mooncen_original_hostname", "")
        if not selected or original != "www.yplib.go.kr":
            raise OutboundRequestBlocked("Yangpyeong library destination was not validated")
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        host_params["host"] = selected
        pool_kwargs["assert_hostname"] = original
        pool_kwargs["server_hostname"] = original
        pool_kwargs["ssl_context"] = _library_certificate_context()
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=pool_kwargs)


def _library_managed_session(factory: SessionFactory) -> Any:
    current = factory()
    if isinstance(current, SafeSession):
        current.mount("https://", _YangpyeongLibraryPinnedAdapter(max_retries=0))
    return current


def _raw_session(owner: str) -> SafeSession:
    current = SafeSession()
    if owner == "library":
        current.mount("https://", _YangpyeongLibraryPinnedAdapter(max_retries=0))
    current.headers.update(
        {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"),
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    if owner == "pool":
        current.headers["Referer"] = YANGPYEONG_POOL_URL
    return current


def _default_fetcher(
    session: Any,
    method: str,
    url: str,
    *,
    timeout: int,
    data: Optional[Mapping[str, Any]] = None,
) -> Any:
    if method == "GET":
        return session.get(url, timeout=timeout, allow_redirects=False)
    if method == "POST":
        return session.post(url, data=data, timeout=timeout, allow_redirects=False)
    raise YangpyeongContractError("unsupported HTTP method")


def _response_bytes(response: Any, requested_url: str) -> bytes:
    status = int(getattr(response, "status_code", 200))
    if status != 200:
        raise YangpyeongContractError(f"HTTP {status}")
    if getattr(response, "history", None):
        raise YangpyeongContractError("redirect history is forbidden")
    headers = getattr(response, "headers", {}) or {}
    if headers.get("Location") or headers.get("location"):
        raise YangpyeongContractError("redirect location is forbidden")
    response_url = _clean(getattr(response, "url", ""))
    if response_url and response_url != requested_url:
        raise YangpyeongContractError("response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        text = getattr(response, "text", response)
        content = str(text).encode("utf-8")
    if not isinstance(content, (bytes, bytearray)):
        content = bytes(content)
    content = bytes(content)
    if not content or len(content) > YANGPYEONG_MAX_BYTES:
        raise YangpyeongContractError("empty or oversized response")
    return content


def _json_response(response: Any, requested_url: str) -> Any:
    raw = _response_bytes(response, requested_url)
    try:
        return response.json()
    except Exception:
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise YangpyeongContractError("invalid JSON response") from exc


def _html_response(response: Any, requested_url: str) -> BeautifulSoup:
    return BeautifulSoup(_response_bytes(response, requested_url), "html.parser")


class _Requester:
    def __init__(
        self,
        owner: str,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        timeout: int,
        meta: dict[str, Any],
    ) -> None:
        self.owner = owner
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.timeout = timeout
        self.meta = meta
        self.local = threading.local()
        self.lock = threading.Lock()
        self.sessions: list[Any] = []

    def _session(self) -> Any:
        current = getattr(self.local, "session", None)
        if current is None:
            current = self.session_factory()
            self.local.session = current
            with self.lock:
                self.sessions.append(current)
        return current

    def _refresh_session(self) -> None:
        current = getattr(self.local, "session", None)
        if current is not None:
            close = getattr(current, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self.local.session = None

    def _retry(self, attempt: int) -> None:
        with self.lock:
            self.meta["request_retry_count"] += 1
        self._refresh_session()
        base = 0.75 if self.owner == "library" else 0.25
        time.sleep(min(3.0, base * (2**attempt)))

    def request(
        self,
        method: str,
        url: str,
        *,
        data: Optional[Mapping[str, Any]] = None,
    ) -> tuple[Any, str]:
        kind = _classify_url(self.owner, method, url, data)
        counter = {
            "list": "list_requests",
            "detail": "detail_requests",
            "registry": "registry_requests",
        }[kind]
        with self.lock:
            self.meta["logical_requests"] += 1
            self.meta[counter] += 1
        last_error: Optional[Exception] = None
        attempts = 4
        for attempt in range(attempts):
            with self.lock:
                self.meta["physical_requests"] += 1
            try:
                response = self.fetcher(self._session(), method, url, timeout=self.timeout, data=data)
                status = int(getattr(response, "status_code", 200))
                if status in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    self._retry(attempt)
                    continue
                return response, kind
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    self._retry(attempt)
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise YangpyeongContractError("request retries exhausted")

    def json(
        self,
        method: str,
        url: str,
        *,
        data: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        response, _kind = self.request(method, url, data=data)
        return _json_response(response, url)

    def soup(
        self,
        method: str,
        url: str,
        *,
        data: Optional[Mapping[str, Any]] = None,
    ) -> BeautifulSoup:
        response, _kind = self.request(method, url, data=data)
        return _html_response(response, url)

    def close(self) -> None:
        seen: set[int] = set()
        for current in self.sessions:
            if id(current) in seen:
                continue
            seen.add(id(current))
            close = getattr(current, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _parallel_map(values: Sequence[Any], function: Callable[[Any], Any], workers: int) -> list[Any]:
    if not values:
        return []
    if workers <= 1 or len(values) == 1:
        return [function(value) for value in values]
    with ThreadPoolExecutor(max_workers=min(workers, len(values))) as pool:
        return list(pool.map(function, values))


def _parse_date(value: Any, field: str) -> date:
    match = _DATE.search(_clean(value))
    if not match:
        raise YangpyeongContractError(f"missing {field} date")
    try:
        return date(*map(int, match.groups()))
    except ValueError as exc:
        raise YangpyeongContractError(f"invalid {field} date") from exc


def _date_pair(value: Any, field: str) -> tuple[date, date]:
    found = _DATE.findall(_clean(value))
    if len(found) not in {1, 2}:
        raise YangpyeongContractError(f"missing {field} date range")
    if len(found) == 1:
        found *= 2
    start, end = (date(*map(int, item)) for item in found)
    if end < start:
        raise YangpyeongContractError(f"reversed {field} date range")
    return start, end


def _status(value: Any) -> str:
    text = _clean(value)
    if any(token in text for token in ("접수중", "모집중", "신청하기", "진행")):
        return "OPEN"
    if any(token in text for token in ("대기", "예정")):
        return "SCHEDULED"
    if any(token in text for token in ("마감", "종료", "폐강", "취소")):
        return "CLOSED"
    return text


def _fee(value: Any) -> str:
    text = _clean(value)
    if not text or text in {"0", "0.0"}:
        return "무료"
    if text == "무료" or "원" in text or "유료" in text:
        return text
    try:
        return f"{int(float(text.replace(',', ''))):,}원"
    except ValueError:
        return text


def _base_row(provider: str, identity: str, title: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "municipality_code": YANGPYEONG_MUNICIPALITY_CODE,
        "municipality_name": YANGPYEONG_MUNICIPALITY_NAME,
        "provider_course_id": f"{provider}:{identity}",
        "source_course_id": identity,
        "title": title,
        "application_url": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "classification_locked": True,
    }


def _table_pairs(table: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if table is None:
        return pairs
    for row in table.select("tr"):
        cells = row.select(":scope > th, :scope > td")
        for index, node in enumerate(cells[:-1]):
            if node.name == "th" and cells[index + 1].name == "td":
                key = _clean(node.get_text(" ", strip=True)).rstrip(":")
                value = _clean(cells[index + 1].get_text(" ", strip=True))
                if key and key not in pairs:
                    pairs[key] = value
    return pairs


@dataclass(frozen=True)
class _Page:
    requested: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


def _source_hash(rows: Sequence[Mapping[str, Any]], key: str = "source_identity") -> str:
    values = sorted(_clean(row.get(key)) for row in rows)
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _gseek_page(payload: Any, page: int, known_total: Optional[int] = None) -> _Page:
    if not isinstance(payload, list):
        raise YangpyeongContractError("GSEEK list payload changed")
    if not payload:
        if known_total is None:
            raise YangpyeongContractError("GSEEK first page unexpectedly empty")
        return _Page(page, known_total, math.ceil(known_total / YANGPYEONG_GSEEK_PAGE_SIZE), ())
    total_values = {_clean(item.get("d_total_cnt")) for item in payload if isinstance(item, Mapping)}
    if len(total_values) != 1 or not _positive_int(next(iter(total_values), "")):
        raise YangpyeongContractError(f"GSEEK page {page}: declared total changed")
    total = int(next(iter(total_values)))
    if known_total is not None and total != known_total:
        raise YangpyeongContractError(f"GSEEK page {page}: total drift")
    last = math.ceil(total / YANGPYEONG_GSEEK_PAGE_SIZE)
    if page > last:
        raise YangpyeongContractError("GSEEK post-last sentinel is not empty")
    expected = min(YANGPYEONG_GSEEK_PAGE_SIZE, total - (page - 1) * YANGPYEONG_GSEEK_PAGE_SIZE)
    if len(payload) != expected:
        raise YangpyeongContractError(f"GSEEK page {page}: row count changed")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise YangpyeongContractError("GSEEK row is not an object")
        subject, cycle = _clean(item.get("d_sbjct_sn")), _clean(item.get("d_sbjct_cycl_sn"))
        title = _clean(item.get("d_sbjct_nm"))
        branch = _clean(item.get("d_edu_gvmnfc"))
        if (
            not _positive_int(subject)
            or not _positive_int(cycle)
            or not title
            or branch not in YANGPYEONG_GSEEK_BRANCHES
            or _clean(item.get("d_co_sprvsn_id")) != YANGPYEONG_GSEEK_CO_SPONSOR_ID
        ):
            raise YangpyeongContractError("GSEEK identity/title/branch/owner changed")
        start = _parse_date(item.get("d_edu_bgng_dt"), "GSEEK education start")
        end = _parse_date(item.get("d_edu_end_dt"), "GSEEK education end")
        if end < start:
            raise YangpyeongContractError("GSEEK education period reversed")
        rows.append(
            {
                "source_identity": f"{subject}:{cycle}",
                "subject": subject,
                "cycle": cycle,
                "title": title,
                "branch": branch,
                "region": _clean(item.get("d_rgn")),
                "start": start,
                "end": end,
                "apply_start": _clean(item.get("d_reg_dt")),
                "start_time": _clean(item.get("d_edu_start_time")),
                "end_time": _clean(item.get("d_edu_end_time")),
                "days": _clean(item.get("d_edu_wday_cd_nm")),
                "target": _clean(item.get("d_sbjct_trgt_nm")),
                "fee": _clean(item.get("d_sbjct_amt")),
                "capacity": _clean(item.get("d_edu_nope")),
                "current": _clean(item.get("d_aply_cnt")),
                "method": _clean(item.get("d_stdnt_chice_mthd_cd_nm") or item.get("d_rcrt_chice_mthd_cd_nm")),
                "category": _clean(
                    " > ".join(
                        part
                        for part in (
                            _clean(item.get("d_clsf_depth1_nm")),
                            _clean(item.get("d_clsf_depth2_nm")),
                            _clean(item.get("d_clsf_depth3_nm")),
                        )
                        if part
                    )
                ),
                "source_status": _clean(item.get("d_recrut_stts_nm")),
                "intro": _clean(item.get("d_sbjct_intrd_cn")),
            }
        )
    return _Page(page, total, last, tuple(rows))


def _gseek_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple((row["source_identity"], row["title"], row["source_status"], row["end"]) for row in page.rows),
    )


def _gseek_exclusion(source: Mapping[str, Any]) -> str:
    title, intro = _clean(source.get("title")), _clean(source.get("intro"))
    if "테스트" in title or "테스트 강좌" in intro or "수강신청 NO" in intro:
        return "test_record"
    if (
        title == "양평문화원"
        or "프로그램 안내" in title
        or "군립도서관 안내" in title
        or "주민자치센터 프로그램" in title
        or "친환경농업대학 안내" in title
    ):
        return "external_owner_guide_or_aggregate_shell"
    return ""


def _gseek_detail(source: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], str, Counter[str]]:
    title_nodes = soup.select("h2.course-title")
    if len(title_nodes) != 1 or _clean(title_nodes[0].get_text(" ", strip=True)) != source["title"]:
        raise YangpyeongContractError(f"GSEEK {source['source_identity']}: detail title drift")
    main = soup.select_one("#div-offline-course-detail")
    if main is None:
        raise YangpyeongContractError(f"GSEEK {source['source_identity']}: detail shell changed")
    hidden = {_clean(node.get("name")): _clean(node.get("value")) for node in soup.select("input[name]")}
    if hidden.get("s_sbjct_sn") != source["subject"] or hidden.get("s_sbjct_cycl_sn") != source["cycle"]:
        raise YangpyeongContractError(f"GSEEK {source['source_identity']}: detail identity drift")
    controls = len(main.select("a[onclick*='fnAply'], button[onclick*='fnAply']"))
    schedule = _clean(
        " ".join(
            part
            for part in (
                _clean(source.get("days")),
                (
                    f"{source['start_time']}~{source['end_time']}"
                    if source.get("start_time") and source.get("end_time")
                    else _clean(source.get("start_time") or source.get("end_time"))
                ),
            )
            if part
        )
    )
    row = _base_row(YANGPYEONG_GSEEK_PROVIDER, str(source["source_identity"]), str(source["title"]))
    row.update(
        {
            "status": _status(source.get("source_status")),
            "source_status": source.get("source_status"),
            "start_date": source["start"].isoformat(),
            "end_date": source["end"].isoformat(),
            "branch": source["branch"],
            "branch_code": source["branch"],
            "venue": source["branch"],
            "category": source.get("category", ""),
            "schedule": schedule,
            "target": source.get("target", ""),
            "fee": _fee(source.get("fee")),
            "capacity_text": (
                f"{source['current']}/{source['capacity']}"
                if source.get("current") and source.get("capacity")
                else source.get("capacity", "")
            ),
            "application_method": source.get("method", ""),
            "source_url": gseek_detail_url(source["subject"], source["cycle"]),
            "raw_fields": {
                "parser": "yangpyeong_gseek_offline_complete",
                "subject_sn": source["subject"],
                "cycle_sn": source["cycle"],
            },
        }
    )
    return row, _gseek_exclusion(source), Counter(application_controls=controls, sensitive_fields=1)


def _collect_gseek(
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    endpoint = "https://ypedu.gseek.kr/user/course/offline/list/search"

    def load(page: int, total: Optional[int] = None) -> _Page:
        return _gseek_page(requester.json("POST", endpoint, data=gseek_list_data(page)), page, total)

    first = load(1)
    sentinel_number = first.last + 1
    boundaries = {1, first.last, sentinel_number}
    required = first.last + 1 + len(boundaries)
    if required > max_pages:
        raise YangpyeongContractError(f"max_pages cap allows {max_pages} of {required} GSEEK list requests")
    pages = {1: first}
    loaded = _parallel_map(list(range(2, first.last + 1)), lambda page: (page, load(page, first.total)), workers)
    pages.update(loaded)
    sentinel = load(sentinel_number, first.total)
    if sentinel.rows:
        raise YangpyeongContractError("GSEEK sentinel returned rows")
    listed = [dict(row) for page in range(1, first.last + 1) for row in pages[page].rows]
    ids = [row["source_identity"] for row in listed]
    if len(listed) != first.total or len(ids) != len(set(ids)):
        raise YangpyeongContractError("GSEEK full source union changed")
    current = [row for row in listed if row["end"] >= cutoff]
    if len(current) > detail_limit:
        raise YangpyeongContractError(f"detail_limit allows {detail_limit} of {len(current)} GSEEK details")

    def detail(source: Mapping[str, Any]) -> tuple[dict[str, Any], str, Counter[str]]:
        return _gseek_detail(
            source,
            requester.soup("GET", gseek_detail_url(source["subject"], source["cycle"])),
        )

    parsed = _parallel_map(current, detail, workers)
    originals = {1: first, first.last: pages[first.last], sentinel_number: sentinel}
    rechecks: dict[str, bool] = {}
    for page, original in originals.items():
        observed = load(page, first.total)
        rechecks[str(page)] = _gseek_signature(observed) == _gseek_signature(original)
        if not rechecks[str(page)]:
            raise YangpyeongContractError(f"GSEEK page {page}: boundary drift")
    excluded = Counter(reason for _row, reason, _audit in parsed if reason)
    discarded = Counter()
    for _row, _reason, audit in parsed:
        discarded.update(audit)
    rows = [row for row, reason, _audit in parsed if not reason]
    audit = {
        "source_total": first.total,
        "source_rows": len(listed),
        "pages": first.last,
        "empty_sentinel_page": sentinel_number,
        "empty_sentinel_rows": 0,
        "boundary_rechecks": rechecks,
        "current_source_count": len(current),
        "detail_verified": len(parsed),
        "excluded_counts": dict(excluded),
        "application_control_count": discarded["application_controls"],
        "sensitive_detail_fields_discarded": discarded["sensitive_fields"],
        "branch_counts": dict(Counter(row["branch"] for row in rows)),
        "source_identity_count": len(ids),
        "source_identity_sha256": _source_hash(listed),
        "parent_aggregate_exclusion_required": True,
        "parent_aggregate_exclusion_field": "d_co_sprvsn_id",
        "parent_aggregate_exclusion_value": YANGPYEONG_GSEEK_CO_SPONSOR_ID,
        "required_list_requests": required,
    }
    return rows, audit


def _pool_page(payload: Any, partition: str, page: int, known_total: Optional[int] = None) -> _Page:
    if not isinstance(payload, list):
        raise YangpyeongContractError("pool list payload changed")
    if not payload:
        if known_total is None:
            return _Page(page, 0, 0, ())
        return _Page(page, known_total, math.ceil(known_total / YANGPYEONG_PAGE_SIZE), ())
    totals = {_clean(item.get("total_count")) for item in payload if isinstance(item, Mapping)}
    if len(totals) != 1 or not _positive_int(next(iter(totals), "")):
        raise YangpyeongContractError(f"pool {partition} declared total changed")
    total = int(next(iter(totals)))
    if known_total is not None and total != known_total:
        raise YangpyeongContractError(f"pool {partition} total drift")
    last = math.ceil(total / YANGPYEONG_PAGE_SIZE)
    if page > last:
        raise YangpyeongContractError(f"pool {partition} post-last sentinel is not empty")
    expected = min(YANGPYEONG_PAGE_SIZE, total - (page - 1) * YANGPYEONG_PAGE_SIZE)
    if len(payload) != expected:
        raise YangpyeongContractError(f"pool {partition} page cardinality changed")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise YangpyeongContractError("pool row is not an object")
        comcd, class_cd = _clean(item.get("comcd")), _clean(item.get("class_cd"))
        branch, title = _clean(item.get("comnm")), _clean(item.get("class_nm"))
        if (
            comcd != "YP21NET"
            or not re.fullmatch(r"\d{5}", class_cd)
            or branch != "양평군평생학습센터수영장"
            or not title
        ):
            raise YangpyeongContractError("pool row identity/branch/title changed")
        rows.append(
            {
                "source_identity": f"{comcd}:{class_cd}",
                "comcd": comcd,
                "class_cd": class_cd,
                "title": title,
                "branch": branch,
                "source_partition": partition,
                "source_status": _clean(item.get("status")),
                "start_time": _clean(item.get("train_stime")),
                "end_time": _clean(item.get("train_etime")),
                "days": _clean(item.get("train_day_nm")),
                "target": _clean(item.get("target_age_name")),
                "fee": _clean(item.get("course_fee")),
                "capacity": _clean(item.get("capa")),
                "current": _clean(item.get("reg_person")),
                "category1": _clean(item.get("category1")),
                "category2": _clean(item.get("category2")),
            }
        )
    return _Page(page, total, last, tuple(rows))


def _pool_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple((row["source_identity"], row["title"], row["source_status"]) for row in page.rows),
    )


def _pool_detail(source: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], Counter[str]]:
    info = soup.select_one("table.fit")
    pairs = _table_pairs(info)
    required = {"강좌명", "운영센터", "교육장소", "시간/요일", "교육대상", "접수방식", "신청인원/정원"}
    if not required.issubset(pairs):
        raise YangpyeongContractError(f"pool {source['source_identity']}: detail fields changed")
    if pairs["강좌명"] != source["title"] or not pairs["운영센터"].startswith(source["branch"]):
        raise YangpyeongContractError(f"pool {source['source_identity']}: detail title/branch drift")
    fees: list[str] = []
    durations: list[str] = []
    fee_table = soup.select_one("table#fee_list")
    if fee_table is None:
        raise YangpyeongContractError(f"pool {source['source_identity']}: fee table changed")
    for tr in fee_table.select("tbody tr"):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select(":scope > td")]
        if len(cells) >= 4:
            if cells[2] and cells[2] not in fees:
                fees.append(cells[2])
            if cells[3] and cells[3] not in durations:
                durations.append(cells[3])
    row = _base_row(YANGPYEONG_POOL_PROVIDER, str(source["source_identity"]), str(source["title"]))
    row.update(
        {
            "status": _status("접수중" if source["source_status"] == "R" else "마감"),
            "source_status": source["source_status"],
            "source_partition": source["source_partition"],
            "branch": source["branch"],
            "branch_code": source["comcd"],
            "venue": pairs["교육장소"],
            "category": _clean(" > ".join(part for part in (source["category1"], source["category2"]) if part)),
            "schedule": pairs["시간/요일"],
            "target": pairs["교육대상"],
            "fee": _clean(" / ".join(fees)) or _fee(source.get("fee")),
            "period": _clean(" / ".join(durations)),
            "capacity_text": pairs["신청인원/정원"],
            "application_method": pairs["접수방식"],
            "source_url": pool_detail_url(source["class_cd"]),
            "raw_fields": {
                "parser": "yangpyeong_pool_fmcs_complete",
                "company_code": source["comcd"],
                "class_code": source["class_cd"],
                "source_partition": source["source_partition"],
            },
        }
    )
    return row, Counter(
        application_controls=int(soup.select_one("#family_list") is not None),
        sensitive_fields=int("강사명" in pairs),
    )


def _collect_pool(
    requester: _Requester,
    _cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    company_url = "https://www.yp21.go.kr/pool/rest/common/company"
    category_url = "https://www.yp21.go.kr/pool/rest/common/category"
    list_url = "https://www.yp21.go.kr/pool/rest/lecture/list"
    companies = requester.json("POST", company_url, data=pool_company_data())
    categories = requester.json("POST", category_url, data=pool_category_data())
    if companies != [{"comcd": "YP21NET", "comnm": "양평군평생학습센터수영장"}]:
        raise YangpyeongContractError("pool company registry changed")
    if not isinstance(categories, list) or len(categories) != 1:
        raise YangpyeongContractError("pool category registry changed")
    category = categories[0]
    if (
        _clean(category.get("category_code")) != "1000000000"
        or _clean(category.get("category_name")) != "수영"
        or int(category.get("category_level") or 0) != 1
    ):
        raise YangpyeongContractError("pool category binding changed")

    def load(partition: str, page: int, total: Optional[int] = None) -> _Page:
        return _pool_page(
            requester.json("POST", list_url, data=pool_list_data(partition, page)),
            partition,
            page,
            total,
        )

    first = {partition: load(partition, 1) for partition in ("R", "E")}

    def sentinel_page(page: _Page) -> int:
        return page.last + 1 if page.last else 2

    def boundary_pages(page: _Page) -> set[int]:
        values = {1, sentinel_page(page)}
        if page.last:
            values.add(page.last)
        return values

    required = sum(max(1, page.last) + 1 + len(boundary_pages(page)) for page in first.values())
    if required > max_pages:
        raise YangpyeongContractError(f"max_pages cap allows {max_pages} of {required} pool list requests")
    pages: dict[str, dict[int, _Page]] = {partition: {1: first[partition]} for partition in first}
    sentinels: dict[str, _Page] = {}
    for partition in ("R", "E"):
        current_first = first[partition]
        loaded = _parallel_map(
            list(range(2, current_first.last + 1)),
            lambda page, partition=partition, total=current_first.total: (
                page,
                load(partition, page, total),
            ),
            workers,
        )
        pages[partition].update(loaded)
        sentinels[partition] = load(
            partition,
            sentinel_page(current_first),
            current_first.total,
        )
        if sentinels[partition].rows:
            raise YangpyeongContractError(f"pool {partition} sentinel returned rows")
    source_by_partition = {
        partition: [dict(row) for page in range(1, first[partition].last + 1) for row in pages[partition][page].rows]
        for partition in ("R", "E")
    }
    for partition, listed in source_by_partition.items():
        if len(listed) != first[partition].total or len({row["source_identity"] for row in listed}) != len(listed):
            raise YangpyeongContractError(f"pool {partition} source union changed")
    r_ids = {row["source_identity"] for row in source_by_partition["R"]}
    e_ids = {row["source_identity"] for row in source_by_partition["E"]}
    if r_ids & e_ids:
        raise YangpyeongContractError("pool current/ended partitions overlap")
    current = source_by_partition["R"]
    if len(current) > detail_limit:
        raise YangpyeongContractError(f"detail_limit allows {detail_limit} of {len(current)} pool details")

    def detail(source: Mapping[str, Any]) -> tuple[dict[str, Any], Counter[str]]:
        return _pool_detail(source, requester.soup("GET", pool_detail_url(source["class_cd"])))

    parsed = _parallel_map(current, detail, workers)
    rechecks: dict[str, bool] = {}
    for partition in ("R", "E"):
        original_first = first[partition]
        originals = {
            1: original_first,
            sentinel_page(original_first): sentinels[partition],
        }
        if original_first.last:
            originals[original_first.last] = pages[partition][original_first.last]
        for page, original in originals.items():
            observed = load(partition, page, original_first.total)
            key = f"{partition}:{page}"
            rechecks[key] = _pool_signature(observed) == _pool_signature(original)
            if not rechecks[key]:
                raise YangpyeongContractError(f"pool {key}: boundary drift")
    discarded = Counter()
    for _row, audit in parsed:
        discarded.update(audit)
    rows = [row for row, _audit in parsed]
    all_source = source_by_partition["R"] + source_by_partition["E"]
    audit = {
        "source_total": len(all_source),
        "source_rows": len(all_source),
        "partition_totals": {key: len(value) for key, value in source_by_partition.items()},
        "pages": sum(first[key].last for key in first),
        "empty_sentinel_pages": {key: sentinel_page(first[key]) for key in first},
        "boundary_rechecks": rechecks,
        "current_source_count": len(current),
        "detail_verified": len(parsed),
        "company_count": 1,
        "category_count": 1,
        "application_control_count": discarded["application_controls"],
        "sensitive_detail_fields_discarded": discarded["sensitive_fields"],
        "branch_counts": dict(Counter(row["branch"] for row in rows)),
        "source_status_counts": dict(Counter(row["source_status"] for row in current)),
        "source_identity_count": len(all_source),
        "source_identity_sha256": _source_hash(all_source),
        "required_list_requests": required,
        "partition_identity_disjoint": True,
    }
    return rows, audit


def _garden_page(soup: BeautifulSoup, page: int, known_total: Optional[int] = None) -> _Page:
    total_node, page_node = soup.select_one(".post-all"), soup.select_one(".post-page")
    total_match = re.search(
        r"총게시물\s*:\s*([\d,]+)\s*건", _clean(total_node.get_text(" ", strip=True)) if total_node else ""
    )
    page_match = re.search(
        r"페이지\s*:\s*(\d+)\s*/\s*(\d+)", _clean(page_node.get_text(" ", strip=True)) if page_node else ""
    )
    if not total_match or not page_match:
        raise YangpyeongContractError(f"garden page {page}: pagination contract changed")
    total = int(total_match.group(1).replace(",", ""))
    reported, last = map(int, page_match.groups())
    if known_total is not None and total != known_total:
        raise YangpyeongContractError(f"garden page {page}: total drift")
    if reported != page or last != max(1, math.ceil(total / YANGPYEONG_PAGE_SIZE)):
        raise YangpyeongContractError(f"garden page {page}: page declaration changed")
    trs = soup.select("tbody.text_center > tr")
    if page > last:
        if trs:
            raise YangpyeongContractError("garden post-last sentinel is not empty")
        return _Page(page, total, last, ())
    expected = min(YANGPYEONG_PAGE_SIZE, total - (page - 1) * YANGPYEONG_PAGE_SIZE)
    if len(trs) != expected:
        raise YangpyeongContractError(f"garden page {page}: row count changed")
    rows: list[dict[str, Any]] = []
    for tr in trs:
        cells = tr.select(":scope > td")
        if len(cells) != 9:
            raise YangpyeongContractError("garden row shape changed")
        link = cells[8].select_one("a[href]")
        if link is None:
            raise YangpyeongContractError("garden detail control changed")
        detail_query = dict(parse_qsl(urlparse(link.get("href", "")).query, keep_blank_values=True))
        garden_no = _clean(detail_query.get("gardenNo"))
        title = _clean(cells[1].get_text(" ", strip=True))
        if not _positive_int(garden_no) or not title:
            raise YangpyeongContractError("garden identity/title changed")
        start, end = _date_pair(cells[4].get_text(" ", strip=True), "garden education")
        apply_start, apply_end = _date_pair(cells[5].get_text(" ", strip=True), "garden application")
        rows.append(
            {
                "source_identity": garden_no,
                "garden_no": garden_no,
                "title": title,
                "capacity": _clean(cells[2].get_text(" ", strip=True)),
                "venue": _clean(cells[3].get_text(" ", strip=True)),
                "start": start,
                "end": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "method": _clean(cells[6].get_text(" ", strip=True)),
                "source_status": _clean(cells[7].get_text(" ", strip=True)),
            }
        )
    return _Page(page, total, last, tuple(rows))


def _garden_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple((row["source_identity"], row["title"], row["source_status"], row["end"]) for row in page.rows),
    )


def _garden_detail(source: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], str, Counter[str]]:
    table = soup.select_one("table")
    pairs = _table_pairs(table)
    required = {"프로그램명", "장소", "참가비", "수업일", "수업시간", "신청기간", "모집방법", "모집인원"}
    if not required.issubset(pairs):
        raise YangpyeongContractError(f"garden {source['garden_no']}: detail fields changed")
    if pairs["프로그램명"] != source["title"] or pairs["장소"] != source["venue"]:
        raise YangpyeongContractError(f"garden {source['garden_no']}: detail title/venue drift")
    start, end = _date_pair(pairs["수업일"], "garden detail education")
    apply_start, apply_end = _date_pair(pairs["신청기간"], "garden detail application")
    if (start, end, apply_start, apply_end) != (
        source["start"],
        source["end"],
        source["apply_start"],
        source["apply_end"],
    ):
        raise YangpyeongContractError(f"garden {source['garden_no']}: detail date drift")
    row = _base_row(YANGPYEONG_GARDEN_PROVIDER, str(source["garden_no"]), str(source["title"]))
    row.update(
        {
            "status": _status(source["source_status"]),
            "source_status": source["source_status"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_start_date": apply_start.isoformat(),
            "apply_end_date": apply_end.isoformat(),
            "branch": "양평정원",
            "branch_code": "YANGPYEONG_GARDEN",
            "venue": pairs["장소"],
            "category": "정원교육",
            "schedule": pairs["수업시간"],
            "fee": pairs["참가비"],
            "capacity_text": pairs["모집인원"],
            "application_method": pairs["모집방법"],
            "source_url": garden_detail_url(source["garden_no"]),
            "raw_fields": {
                "parser": "yangpyeong_garden_complete",
                "garden_no": source["garden_no"],
            },
        }
    )
    exclusion = (
        "cancelled_or_exhibition_not_course"
        if any(token in source["title"] for token in ("취소되었", "전시회"))
        else ""
    )
    controls = len(soup.select("a[href*='addGardenEdcReqstWebView.do']"))
    return row, exclusion, Counter(application_controls=controls, sensitive_fields=2)


def _collect_garden(
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def load(page: int, total: Optional[int] = None) -> _Page:
        return _garden_page(requester.soup("GET", garden_list_url(page)), page, total)

    first = load(1)
    sentinel_number = first.last + 1
    boundaries = {1, first.last, sentinel_number}
    required = first.last + 1 + len(boundaries)
    if required > max_pages:
        raise YangpyeongContractError(f"max_pages cap allows {max_pages} of {required} garden list requests")
    pages = {1: first}
    pages.update(
        _parallel_map(
            list(range(2, first.last + 1)),
            lambda page: (page, load(page, first.total)),
            workers,
        )
    )
    sentinel = load(sentinel_number, first.total)
    if sentinel.rows:
        raise YangpyeongContractError("garden sentinel returned rows")
    listed = [dict(row) for page in range(1, first.last + 1) for row in pages[page].rows]
    if len(listed) != first.total or len({row["source_identity"] for row in listed}) != len(listed):
        raise YangpyeongContractError("garden full source union changed")
    current = [row for row in listed if row["end"] >= cutoff]
    if len(current) > detail_limit:
        raise YangpyeongContractError(f"detail_limit allows {detail_limit} of {len(current)} garden details")

    def detail(source: Mapping[str, Any]) -> tuple[dict[str, Any], str, Counter[str]]:
        return _garden_detail(source, requester.soup("GET", garden_detail_url(source["garden_no"])))

    parsed = _parallel_map(current, detail, workers)
    originals = {1: first, first.last: pages[first.last], sentinel_number: sentinel}
    rechecks: dict[str, bool] = {}
    for page, original in originals.items():
        observed = load(page, first.total)
        rechecks[str(page)] = _garden_signature(observed) == _garden_signature(original)
        if not rechecks[str(page)]:
            raise YangpyeongContractError(f"garden page {page}: boundary drift")
    excluded = Counter(reason for _row, reason, _audit in parsed if reason)
    discarded = Counter()
    for _row, _reason, audit_item in parsed:
        discarded.update(audit_item)
    rows = [row for row, reason, _audit in parsed if not reason]
    audit = {
        "source_total": first.total,
        "source_rows": len(listed),
        "pages": first.last,
        "empty_sentinel_page": sentinel_number,
        "empty_sentinel_rows": 0,
        "boundary_rechecks": rechecks,
        "current_source_count": len(current),
        "detail_verified": len(parsed),
        "excluded_counts": dict(excluded),
        "application_control_count": discarded["application_controls"],
        "sensitive_detail_fields_discarded": discarded["sensitive_fields"],
        "branch_counts": dict(Counter(row["branch"] for row in rows)),
        "source_identity_count": len(listed),
        "source_identity_sha256": _source_hash(listed),
        "required_list_requests": required,
    }
    return rows, audit


def _library_source(item: Mapping[str, Any]) -> dict[str, Any]:
    rec_key, title = _clean(item.get("recKey")), _clean(item.get("eventName"))
    manage_code = _clean(item.get("manageCode"))
    if not _positive_int(rec_key) or not title or manage_code not in {"ALL", *YANGPYEONG_LIBRARY_BRANCHES}:
        raise YangpyeongContractError("library identity/title/branch code changed")
    start = _parse_date(item.get("eventStartDate"), "library event start")
    end = _parse_date(item.get("eventEndDate"), "library event end")
    apply_start = _parse_date(item.get("takeStartDate"), "library application start")
    apply_end = _parse_date(item.get("takeEndDate"), "library application end")
    date_reversed = end < start
    confirmation = any(
        token in title for token in ("확정자", "확정자 및 대기자", "명단 발표", "명단발표", "확정자 명단")
    )
    if date_reversed and confirmation:
        start, end = end, start
    elif date_reversed:
        raise YangpyeongContractError("library education date range reversed")
    if apply_end < apply_start:
        raise YangpyeongContractError("library application date range reversed")
    return {
        "source_identity": rec_key,
        "rec_key": rec_key,
        "title": title,
        "manage_code": manage_code,
        "start": start,
        "end": end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "event_time": _clean(item.get("eventTime")),
        "target": _clean(item.get("eventTarget")),
        "capacity": _clean(item.get("applicationCnt")),
        "current": _clean(item.get("userApplicationCnt")),
        "wait_capacity": _clean(item.get("waitCnt")),
        "source_status": _clean(item.get("applyEnableStatusDesc") or item.get("eventStateDesc")),
        "source_date_reversed": date_reversed,
    }


def _library_page(payload: Any, page: int, known_total: Optional[int] = None) -> _Page:
    if (
        not isinstance(payload, Mapping)
        or _clean(payload.get("status")) != "OK"
        or not isinstance(payload.get("data"), Mapping)
    ):
        raise YangpyeongContractError("library list envelope changed")
    data = payload["data"]
    try:
        total = int(data.get("totalCount"))
        last = int(data.get("totalPage"))
        reported = int(data.get("pageIndex"))
        page_size = int(data.get("pageSize"))
    except (TypeError, ValueError) as exc:
        raise YangpyeongContractError("library pagination values changed") from exc
    items = data.get("data")
    if not isinstance(items, list) or reported != page or page_size != YANGPYEONG_LIBRARY_PAGE_SIZE:
        raise YangpyeongContractError(f"library page {page}: pagination contract changed")
    if known_total is not None and total != known_total:
        raise YangpyeongContractError(f"library page {page}: total drift")
    if last != math.ceil(total / YANGPYEONG_LIBRARY_PAGE_SIZE):
        raise YangpyeongContractError("library declared final page changed")
    if page > last:
        if items:
            raise YangpyeongContractError("library post-last sentinel is not empty")
        return _Page(page, total, last, ())
    expected = min(YANGPYEONG_LIBRARY_PAGE_SIZE, total - (page - 1) * YANGPYEONG_LIBRARY_PAGE_SIZE)
    if len(items) != expected:
        raise YangpyeongContractError(f"library page {page}: row count changed")
    return _Page(page, total, last, tuple(_library_source(item) for item in items))


def _library_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple((row["source_identity"], row["title"], row["source_status"], row["end"]) for row in page.rows),
    )


def _library_branch(source: Mapping[str, Any]) -> str:
    code, title = _clean(source.get("manage_code")), _clean(source.get("title"))
    if code in YANGPYEONG_LIBRARY_BRANCHES and code != "ZA":
        return YANGPYEONG_LIBRARY_BRANCHES[code]
    tokens = (
        ("양서친환경", "양서친환경도서관"),
        ("양동", "양동도서관"),
        ("용문", "용문도서관"),
        ("지평", "지평도서관"),
        ("강상", "강상작은도서관"),
        ("강하", "강하작은도서관"),
        ("옥천", "옥천작은도서관"),
        ("서종", "서종작은도서관"),
        ("단월", "단월작은도서관"),
        ("청운", "청운작은도서관"),
        ("개군", "개군작은도서관"),
        ("작은도서관", "양평군 작은도서관"),
        ("양평", "양평도서관"),
    )
    for token, branch in tokens:
        if token in title:
            return branch
    if code == "ZA":
        return YANGPYEONG_LIBRARY_BRANCHES[code]
    return "양평군도서관"


def _library_exclusion(title: str) -> str:
    if any(token in title for token in ("확정자", "확정자 및 대기자", "명단 발표", "명단발표")):
        return "confirmation_or_result_duplicate"
    if any(token in title for token in ("구독서비스", "구독형 독서콘텐츠", "책더미", "이용자 모집")):
        return "subscription_or_lending_service"
    education_tokens = (
        "수강생",
        "특강",
        "교실",
        "강좌",
        "프로그램",
        "작가와의 만남",
        "교육",
        "강연",
        "아카데미",
        "수업",
        "독서코칭",
    )
    if not any(token in title for token in education_tokens):
        return "non_education_event"
    return ""


def _library_detail(source: Mapping[str, Any], payload: Any) -> tuple[dict[str, Any], str, Counter[str]]:
    if (
        not isinstance(payload, Mapping)
        or _clean(payload.get("status")) != "OK"
        or not isinstance(payload.get("data"), Mapping)
    ):
        raise YangpyeongContractError(f"library {source['rec_key']}: detail envelope changed")
    data = payload["data"]
    observed = _library_source(data)
    for key in ("source_identity", "title", "start", "end", "apply_start", "apply_end"):
        if observed[key] != source[key]:
            raise YangpyeongContractError(f"library {source['rec_key']}: detail {key} drift")
    branch = _library_branch(source)
    row = _base_row(YANGPYEONG_LIBRARY_PROVIDER, str(source["rec_key"]), str(source["title"]))
    row.update(
        {
            "status": _status(source["source_status"]),
            "source_status": source["source_status"],
            "start_date": source["start"].isoformat(),
            "end_date": source["end"].isoformat(),
            "apply_start_date": source["apply_start"].isoformat(),
            "apply_end_date": source["apply_end"].isoformat(),
            "branch": branch,
            "branch_code": source["manage_code"],
            "venue": branch,
            "category": "도서관 문화강좌",
            "schedule": source["event_time"],
            "target": source["target"],
            "fee": "",
            "capacity_text": (
                f"{source['current']}/{source['capacity']}"
                if source.get("current") and source.get("capacity")
                else source.get("capacity", "")
            ),
            "waitlist_total": source["wait_capacity"],
            "source_url": f"https://www.yplib.go.kr/libProgramInfo/{source['rec_key']}",
            "raw_fields": {
                "parser": "yangpyeong_library_complete_event_ledger",
                "rec_key": source["rec_key"],
                "manage_code": source["manage_code"],
            },
        }
    )
    sensitive = sum(1 for key in ("eventTeacher", "eventContent", "inputWorker", "userList", "fileList") if key in data)
    return row, _library_exclusion(source["title"]), Counter(sensitive_fields=sensitive, application_controls=1)


def _collect_library(
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def load(page: int, total: Optional[int] = None) -> _Page:
        return _library_page(requester.json("GET", library_list_url(page)), page, total)

    first = load(1)
    sentinel_number = first.last + 1
    boundaries = {1, first.last, sentinel_number}
    required = first.last + 1 + len(boundaries)
    if required > max_pages:
        raise YangpyeongContractError(f"max_pages cap allows {max_pages} of {required} library list requests")
    pages = {1: first}
    pages.update(
        _parallel_map(
            list(range(2, first.last + 1)),
            lambda page: (page, load(page, first.total)),
            workers,
        )
    )
    sentinel = load(sentinel_number, first.total)
    if sentinel.rows:
        raise YangpyeongContractError("library sentinel returned rows")
    listed = [dict(row) for page in range(1, first.last + 1) for row in pages[page].rows]
    if len(listed) != first.total or len({row["source_identity"] for row in listed}) != len(listed):
        raise YangpyeongContractError("library full source union changed")
    current = [row for row in listed if row["end"] >= cutoff]
    if len(current) > detail_limit:
        raise YangpyeongContractError(f"detail_limit allows {detail_limit} of {len(current)} library details")

    def detail(source: Mapping[str, Any]) -> tuple[dict[str, Any], str, Counter[str]]:
        return _library_detail(source, requester.json("GET", library_detail_url(source["rec_key"])))

    parsed = _parallel_map(current, detail, workers)
    originals = {1: first, first.last: pages[first.last], sentinel_number: sentinel}
    rechecks: dict[str, bool] = {}
    for page, original in originals.items():
        observed = load(page, first.total)
        rechecks[str(page)] = _library_signature(observed) == _library_signature(original)
        if not rechecks[str(page)]:
            raise YangpyeongContractError(f"library page {page}: boundary drift")
    excluded = Counter(reason for _row, reason, _audit in parsed if reason)
    discarded = Counter()
    for _row, _reason, audit_item in parsed:
        discarded.update(audit_item)
    rows = [row for row, reason, _audit in parsed if not reason]
    audit = {
        "source_total": first.total,
        "source_rows": len(listed),
        "pages": first.last,
        "empty_sentinel_page": sentinel_number,
        "empty_sentinel_rows": 0,
        "boundary_rechecks": rechecks,
        "current_source_count": len(current),
        "detail_verified": len(parsed),
        "excluded_counts": dict(excluded),
        "application_control_count": discarded["application_controls"],
        "sensitive_detail_fields_discarded": discarded["sensitive_fields"],
        "branch_counts": dict(Counter(row["branch"] for row in rows)),
        "source_identity_count": len(listed),
        "source_identity_sha256": _source_hash(listed),
        "historical_confirmation_date_reversed_count": sum(bool(row.get("source_date_reversed")) for row in listed),
        "required_list_requests": required,
    }
    return rows, audit


_COLLECTORS = {
    "gseek": _collect_gseek,
    "pool": _collect_pool,
    "garden": _collect_garden,
    "library": _collect_library,
}


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _initial_meta(owner: str, cutoff: date) -> dict[str, Any]:
    config = YANGPYEONG_OWNERS.get(owner, {})
    return {
        "owner": owner,
        "provider": config.get("provider", ""),
        "canonical_url": config.get("url", ""),
        "candidate_id": config.get("candidate_id", ""),
        "municipality_code": YANGPYEONG_MUNICIPALITY_CODE,
        "audit_date": cutoff.isoformat(),
        "logical_requests": 0,
        "physical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "registry_requests": 0,
        "request_retry_count": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "payment_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "source_cap_reached": False,
        "discovered_links": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "application_endpoints_called": 0,
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = _clean(key).lower()
                child_path = f"{path}.{normalized}" if path else normalized
                if normalized in _FORBIDDEN_KEYS:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple, set)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str):
            if _PHONE.search(value) or _EMAIL.search(value):
                errors.append(f"PII value in {path}")

    walk(row, "")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def collect_yangpyeong_education(
    target: Any,
    timeout: int = 35,
    max_pages: int = YANGPYEONG_MAX_PAGES,
    detail_limit: int = YANGPYEONG_MAX_DETAILS,
    *,
    today: Optional[date | datetime | str] = None,
    max_workers: int = YANGPYEONG_MAX_WORKERS,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Yangpyeong owner snapshot."""

    try:
        cutoff = _today(today)
    except (TypeError, ValueError):
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _initial_meta("", cutoff)
        meta["configured_collection_error"] = "today is invalid"
        return [], YANGPYEONG_PARSER, meta
    owner = owner_for_target(target)
    meta = _initial_meta(owner, cutoff)
    if not owner:
        meta.update(
            {
                "provider": _clean(_target_value(target, "provider")),
                "canonical_url": _clean(_target_value(target, "url")),
                "configured_collection_error": "non-canonical Yangpyeong education target",
            }
        )
        return [], YANGPYEONG_PARSER, meta
    try:
        timeout, max_pages, detail_limit, max_workers = map(int, (timeout, max_pages, detail_limit, max_workers))
        if timeout < 1 or max_pages < 1 or detail_limit < 0 or not 1 <= max_workers <= 16:
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "invalid collection limits"
        return [], YANGPYEONG_PARSER, meta
    if fetcher is None and session_factory is None and not allow_raw_requests_for_tests:
        meta["configured_collection_error"] = (
            "raw requests disabled; inject the managed session/fetcher or explicitly opt in"
        )
        return [], YANGPYEONG_PARSER, meta

    if session_factory is None:

        def current_factory() -> requests.Session:
            return _raw_session(owner)

    elif owner == "library":

        def current_factory() -> requests.Session:
            return _library_managed_session(session_factory)

    else:
        current_factory = session_factory
    requester = _Requester(owner, current_factory, fetcher or _default_fetcher, timeout, meta)
    try:
        rows, audit = _COLLECTORS[owner](requester, cutoff, max_pages, detail_limit, max_workers)
        original_ids = [row["provider_course_id"] for row in rows]
        deduped = list((dedupe_rows or _dedupe_default)(rows))
        if any(not isinstance(row, Mapping) for row in deduped):
            raise YangpyeongContractError("dedupe returned a non-object row")
        if [row.get("provider_course_id") for row in deduped] != original_ids:
            raise YangpyeongContractError("dedupe changed complete owner identity/cardinality")
        privacy = [error for row in deduped for error in _privacy_errors(row)]
        if privacy:
            raise YangpyeongContractError("; ".join(dict.fromkeys(privacy)))
        if any(row.get("application_url") for row in deduped):
            raise YangpyeongContractError("application endpoint escaped output boundary")
        deduped = sorted(
            (dict(row) for row in deduped),
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            ),
        )
        meta.update(audit)
        no_current_data = not deduped
        meta.update(
            {
                "discovered_links": int(meta.get("source_rows") or meta.get("source_total") or 0),
                "pagination_detected": int(meta.get("pages") or 0) > 1,
                "returned_count": len(deduped),
                "status_counts": dict(Counter(row.get("status", "") for row in deduped)),
                "output_identity_sha256": hashlib.sha256(
                    "\n".join(sorted(row["provider_course_id"] for row in deduped)).encode("utf-8")
                ).hexdigest(),
                "owner_identity_disjoint": True,
                "pagination_complete": True,
                "details_complete": meta["detail_requests"] == audit["detail_verified"],
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": no_current_data,
                "no_current_reason": (
                    "complete owner ledger has no current/future education courses" if no_current_data else ""
                ),
                "application_endpoints_called": 0,
            }
        )
        return deduped, YANGPYEONG_PARSER, meta
    except Exception as exc:
        if "max_pages cap" in _clean(exc) or "detail_limit" in _clean(exc):
            meta["source_cap_reached"] = True
        meta.update(
            {
                "returned_count": 0,
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], YANGPYEONG_PARSER, meta
    finally:
        requester.close()


collect_yangpyeong_education_courses = collect_yangpyeong_education
collect = collect_yangpyeong_education

__all__ = [name for name in globals() if name.startswith("YANGPYEONG_")] + [
    "YangpyeongContractError",
    "collect",
    "collect_yangpyeong_education",
    "collect_yangpyeong_education_courses",
    "garden_detail_url",
    "garden_list_url",
    "gseek_detail_url",
    "gseek_list_data",
    "is_target",
    "is_yangpyeong_target",
    "library_detail_url",
    "library_list_url",
    "owner_for_target",
    "pool_detail_url",
    "pool_list_data",
]
