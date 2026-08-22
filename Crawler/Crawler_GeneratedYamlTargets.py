from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yaml
import soupsieve
from psycopg2.extras import RealDictCursor

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import Crawler.Crawler_MunicipalYaml as municipal_yaml_module
import DB.course_lifecycle as course_lifecycle_module
from Crawler.Crawler_MunicipalYaml import (
    CrawlTarget,
    MunicipalDbWriter,
    ProviderReport,
    crawl_experience_from_url as collect_from_url,
    print_table,
    sample_rows,
    score_fields,
    write_report,
)
from Crawler.Crawler_YamlSources import parse_date_range, provider_course_id_from_row
from DB.course_lifecycle import mark_stale_courses, utc_now
from DB.db_utils import get_db_connection
from service_group import (
    SERVICE_GROUP_EXPERIENCE,
    infer_experience_institution_source_group,
    infer_service_group,
)
from utils import clean_text, setup_logger
from utils.outbound_http import OutboundRequestBlocked, outbound_request_budget
from utils.text_quality import provider_code_label, readable_text
from utils.source_endpoint import canonical_source_endpoint


ROOT = PROJECT_ROOT
TARGETS_FILE = ROOT / "config" / "collected_yaml_crawl_targets.yaml"
TARGET_DIR = ROOT / "config" / "crawl_targets"
REGISTRY_FILE = ROOT / "config" / "generated_yaml_crawler_registry.yaml"
PRODUCTION_PROVIDERS_FILE = ROOT / "config" / "production_crawler_providers.yaml"
MUNICIPAL_OPERATIONAL_FILE = (
    ROOT / "config" / "municipal_integrated_reservation_operational.yaml"
)
MUNICIPALITY_MASTER_FILE = ROOT / "config" / "municipal_course_search_targets.yaml"
OFFICIAL_MUNICIPALITY_COUNT = 269
PROVIDER_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,49}\Z")
MAX_URL_LENGTH = 8192
MAX_TARGETS_PER_RUN = 500
MAX_ROWS_PER_TARGET = 5000
MAX_PAGES = 2_000
MAX_DETAIL_PAGES = 3000
MAX_RECURSION_DEPTH = 3
MAX_REQUEST_TIMEOUT_SECONDS = 60
MAX_PARALLEL_WORKERS = 8
MAX_REQUESTS_PER_TARGET = 4_250
REQUEST_HOPS_PER_LOGICAL_REQUEST = 2
TRANSIENT_ZERO_ROW_RETRY_BACKOFF_SECONDS = 1.0
CONCRETE_RESULT_MANIFEST_PATH_ENV = "CRAWLER_CONCRETE_RESULT_PATH"
SCHEDULED_PROVIDER_ENV = "CRAWLER_SCHEDULED_PROVIDER"
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "password",
    "secret",
    "session",
    "sessionid",
    "token",
}
_DB_WRITE_LOCK = RLock()
_RETRYABLE_TRANSPORT_ERROR_PATTERN = re.compile(
    r"\b(?:"
    r"RequestException|ConnectTimeout|ReadTimeout|ConnectionError|SSLError|"
    r"ProxyError|MaxRetryError|ProtocolError|NewConnectionError|"
    r"NameResolutionError|ConnectionResetError|RemoteDisconnected"
    r")\b|"
    r"https?connectionpool|strict tls request failed|"
    r"temporary failure in name resolution|getaddrinfo failed|"
    r"connection (?:aborted|refused|reset|broken)|"
    r"(?:connect|read) timed out|unexpected_eof_while_reading",
    re.IGNORECASE,
)
_NON_RETRYABLE_CONTRACT_ERROR_PATTERN = re.compile(
    r"stable (?:boundary )?recheck|changed during (?:a )?stable recheck|"
    r"contract(?:error)?\b|pagination declaration drifted|"
    r"declaration (?:drift|mismatch)|does not match|"
    r"duplicate (?:official|source|course|provider|target|identity)|"
    r"required fields? (?:is|are )?missing|invalid selector|"
    r"\bparser error\b|\b(?:row|page|detail|source) cap (?:allows|reached)",
    re.IGNORECASE,
)


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"Duplicate YAML mapping key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_unique_yaml(path: Path) -> Any:
    # UniqueKeyLoader subclasses SafeLoader and only tightens mapping-key handling.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)  # noqa: S506


EXCLUDED_URL_DOMAIN_TOKENS = ("e-ncom.co.kr",)
REVIEWED_EXCLUDED_DOMAIN_PROVIDERS = frozenset(
    {
        "GIMHAE_DONGBU_SENIOR_NOTICE",
    }
)
EXCLUDED_URL_PATH_TOKENS = (
    "/news/",
    "/m_news/",
    "/attaches/",
    "articleview",
    "articleView",
    "view.php?key=",
    "/notice/detail/",
    "bbsMsgDetail",
    "selectBbsDetail",
    "selectBbsNttView",
    "selectNttList",
    "selectBoardView",
    "common/bbs/selectBbsDetail",
    "/board/view.",
    "/board/view/",
    "board/download",
    "/download.do",
    "doViewBoardItem",
    "bbs/board.php?bo_table=notice",
    "/media/board/",
    "boardList.do?boardId=",
    "cmmBoardView.do",
    "selectEminwonNewsView.do",
    "notice?idx=",
    "mode=view",
    "articleSeq=",
    "nttId=",
    "ntatcSeq=",
    "openData/view",
    ".pdf",
    ".hwp",
    ".hwpx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".zip",
)
EXCLUDED_URL_MEDIA_DOMAINS = (
    "asiatoday.co.kr",
    "boeuni.com",
    "brcity.kr",
    "cctimes.kr",
    "cfnews.kr",
    "domin.co.kr",
    "elovejc.kr",
    "ggilbo.com",
    "gndomin.com",
    "gukjenews.com",
    "hyundaiilbo.com",
    "idaegu.co.kr",
    "igangbuk.com",
    "igimpo.com",
    "imedialife.co.kr",
    "jeollailbo.com",
    "jjn.co.kr",
    "jnilbo.com",
    "jntoday.co.kr",
    "joongdo.co.kr",
    "kbsm.net",
    "khan.co.kr",
    "kjilbo.co.kr",
    "kmaeil.com",
    "kwtotalnews.kr",
    "kyongbuk.co.kr",
    "mygoyang.com",
    "newsfire.co.kr",
    "pointe.co.kr",
    "seoulilbo.com",
    "todayan.com",
    "welfarehello.com",
    "yangsanilbo.com",
    "yg21.co.kr",
    "yongin21.co.kr",
    "zsick.com",
)
WORKING_CRAWLER_STATUSES = {"ready", "partial", "candidate", "generated"}
REGISTRY_CRAWLER_STATUSES = {
    "ready",
    "partial",
    "needs_discovery",
    "needs_parser",
    "blocked",
    "candidate",
    "generated",
}
DISABLED_REGISTRY_STATUSES = {"blocked", "needs_discovery", "needs_parser"}
RECHECKABLE_CRAWLER_STATUSES = {"no_current_data"}
NON_EXECUTABLE_STATUSES = {"deprecated", "excluded_url_shape"}
REQUIRED_TARGET_FIELDS = {
    "provider",
    "name",
    "branch",
    "collection_category",
    "domain_category",
    "operator_type",
    "source_group",
    "collection_type",
    "crawler_status",
    "priority",
    "url",
    "source",
    "origin",
}
SELECTOR_FIELDS = {
    "item_selector",
    "list_selector",
    "detail_selector",
    "next_selector",
    "title_selector",
}
JSON_PATH_FIELDS = {"items_path", "json_path", "next_path", "title_path"}
DEDICATED_PROVIDER_NAMES = {
    "BABSANG_WELFARE_PROGRAM",
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
    "SAHASILVER_COURSE",
    "SEONGNAM_BAEUMSOOP",
    "YONGIN_LIFELONG_LEARNING",
    "ESONGPA_SPORTS_CULTURE",
    "SEOUL_PUBLIC_SERVICE",
}
MIXED_ROW_CLASSIFICATION_PROVIDERS = frozenset(
    {
        # The district-wide catalogue includes both ordinary public courses and
        # institution rows such as 영도도서관.  Its collector classifies those
        # institution rows before target-level metadata defaults are applied.
        "MUNI_WWW_YEONGDO_GO_KR_33400564",
        # These reviewed municipal catalogues contain an explicit mixture of
        # education and experience courses. Their collectors emit the exact
        # canonical pair domain_category=체험·견학, service_group=체험 from
        # reviewed row/detail evidence before target defaults are applied.
        "MUNI_WWW_GEUMCHEON_GO_KR_237EA1EA",
        "MUNI_WWW_GUMI_GO_KR_51F967B3",
        "MUNI_WWW_SEOGU_GO_KR_E4434123",
        "MUNI_WWW_ANDONG_GO_KR_1430676F",
        "MUNI_LIB_ANDONG_GO_KR_6B34DA7C",
        "MUNI_LIB_ANDONG_GO_KR_F96F2899",
        "MUNI_WWW_GOYANG_GO_KR_AFE8FBDD",
        "MUNI_SUGANG_GM_GO_KR_F136DD19",
        "MUNI_RESVE_YONGIN_GO_KR_221336AC",
        "MUNI_WWW_HSG_GO_KR_7452F27B",
        "MUNI_HSYOUTHCENTER_HSG_GO_KR_46DEDE77",
        "MUNI_LIB_JEONGSEON_GO_KR_DD359707",
        "SUWON_RESERV_EDUCATION",
    }
)
ROW_LEVEL_INSTITUTION_CLASSIFICATION_KEYS = frozenset(
    {
        "collection_category",
        "domain_category",
        "source_group",
        "service_group",
        "service_group_policy",
    }
)


def production_scheduled_provider_names(
    path: Path = PRODUCTION_PROVIDERS_FILE,
) -> set[str]:
    if not path.is_file():
        return set()
    document = load_unique_yaml(path) or {}
    providers = document.get("providers") if isinstance(document, dict) else []
    if not isinstance(providers, list):
        raise ValueError(f"{path}: providers must be a list")
    return {
        clean_text(provider).upper() for provider in providers if clean_text(provider)
    }


def municipal_operational_provider_names(
    path: Path = MUNICIPAL_OPERATIONAL_FILE,
) -> set[str]:
    """Return providers owned by the bounded municipal aggregate.

    The operational allowlist is the source of truth for aggregate scheduling.
    Existing YAML owners promoted into that allowlist must not also receive a
    generated per-provider wrapper, regardless of which historical target file
    or ``origin`` value they retain.
    """
    if not path.is_file():
        return set()
    document = load_unique_yaml(path) or {}
    entries = document.get("entries") if isinstance(document, dict) else []
    if not isinstance(entries, list):
        raise ValueError(f"{path}: entries must be a list")
    return {
        clean_text(entry.get("provider")).upper()
        for entry in entries
        if isinstance(entry, dict) and clean_text(entry.get("provider"))
    }


PRODUCTION_SCHEDULED_PROVIDER_NAMES = production_scheduled_provider_names()
MUNICIPAL_OPERATIONAL_PROVIDER_NAMES = municipal_operational_provider_names()
DEFAULT_GENERATED_ARGUMENTS = (
    "--save-db",
    "--per-target-limit",
    "50",
    "--allow-partial-save",
)
EXPERIENCE_FULL_SNAPSHOT_ARGUMENTS = (
    "--save-db",
    "--mark-stale",
    "--per-target-limit",
    "0",
    "--max-pages",
    "100",
    "--detail-limit",
    "1000",
)
INSTITUTION_FULL_SNAPSHOT_ARGUMENTS = EXPERIENCE_FULL_SNAPSHOT_ARGUMENTS
GENERATED_PROVIDER_ARGUMENT_OVERRIDES = {
    "INCHEON_RESERVATION": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "200",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_DAEDEOK_GO_KR_360B9B7C": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "350",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_GONGJU_GO_KR_7CBA2D38": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "500",
    ),
    "MUNI_PALDAL_SUWON_GO_KR_D78BD1B4": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_DAMYANG_GO_KR_0D972ECA": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_JANGSEONG_GO_KR_531090D8": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_HAENAMEDU_OR_KR_00C5EA00": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "300",
    ),
    "MUNI_WWW_HADONG_GO_KR_73A18CEA": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "500",
    ),
    "MUNI_SHINAN_FAMILYNET_OR_KR_EEF98418": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "40",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_BRCN_GO_KR_9A0DF147": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_GBGS_GO_KR_87106AA0": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_GBGS_GO_KR_999BABE7": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_HWASUN_GO_KR_830A293C": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_GOHEUNG_GO_KR_CEE514D6": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "200",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_JANGHEUNG_GO_KR_0392DE78": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_YEONSU_GO_KR_CB4C41BB": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "400",
        "--detail-limit",
        "700",
    ),
    "MUNI_WWW_YEOSU_GO_KR_E2EAB68F": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_JEONGEUP_GO_KR_C8631DF4": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "40",
        "--detail-limit",
        "50",
    ),
    "MUNI_WWW_DANGJIN_GO_KR_3C378AA6": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "100",
    ),
    "NATIONAL_MUSEUM_OF_MODERN_ART": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "500",
        "--detail-limit",
        "3000",
    ),
    "ULSAN_EDU_BOOKING": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "3000",
    ),
    "JNE_LIBRARY_READING_INTEGRATED": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "250",
        "--detail-limit",
        "1000",
    ),
    "JNE_LIBRARY_LECTURE_INTEGRATED": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_HONGCHEON_GO_KR_F5083BE8": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "200",
    ),
    "MUNI_HONGCHEONLIB_GO_KR_17726A2C": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "200",
    ),
    "HAMYANG_WELFARE_OFFICIAL_COURSE": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "160",
        "--detail-limit",
        "0",
    ),
    "MUNI_SUGANG_ASAN_GO_KR_FF504CD1": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1200",
        "--detail-limit",
        "1000",
    ),
    "MUNI_EDU_SOKCHO_GO_KR_8E237F28": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "200",
    ),
    "ANYANG_LIFELONG_LEARNING": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1200",
        "--detail-limit",
        "1000",
    ),
    "DAEJEON_OK_RESERVATION": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1200",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_DJJUNGGU_GO_KR_6A89B08A": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_DONGGU_GO_KR_9A7A5E6F": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "100",
    ),
    "ULSAN_BUKGU_PUBLIC_RESERVATION": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "1000",
    ),
    "MUNI_USBL_BUKGU_ULSAN_KR_A68023CB": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "100",
    ),
    "MUNI_LIFETIMEEDU_POHANG_GO_KR_4D8BE3DA": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1200",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_JINJU_GO_KR_AC4F2628": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1200",
        "--detail-limit",
        "1000",
    ),
    "MUNI_E_JEONJU_GO_KR_00EEA994": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "100",
    ),
    "SEJONG_SJFMC_EDUCATION": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "200",
    ),
    "DAEGU_RESERVATION": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1200",
        "--detail-limit",
        "1200",
    ),
    "MUNI_WWW_CHANGWON_GO_KR_74865AEB": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "200",
    ),
    "MUNI_RESERVE_INSISEOL_OR_KR_EC4B7776": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "500",
    ),
    "MUNI_LLL_CHEONGJU_GO_KR_DA1AAEA1": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1200",
        "--detail-limit",
        "1200",
    ),
    "MUNI_TICKET_CHEONGJU_GO_KR_72C8D1D9": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "700",
    ),
    "MUNI_WWW_CHUNGJU_GO_KR_7EE8620A": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "700",
    ),
    "MUNI_RESERV_BUCHEON_GO_KR_9DFFD792": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_UIWANG_GO_KR_2A9DF9A4": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "250",
        "--detail-limit",
        "2000",
    ),
    "MUNI_YEYAK_HSCITY_GO_KR_2DFD650A": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "80",
        "--detail-limit",
        "1200",
    ),
    "ICHEON_WORKER_WELFARE": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "1000",
    ),
    "GIMHAE_DONGBU_SENIOR_NOTICE": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "200",
    ),
    "ESHARE_PUBLIC_COURSE": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "1000",
    ),
    "NATIONAL_FOREST_EDUCATION_CENTER": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1000",
        "--detail-limit",
        "100",
    ),
    "SAHA_SOCIAL_WELFARE_DIRECTORY": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "100",
    ),
    "SUNCHEON_SENIOR_WELFARE_NOTICE": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "5",
        "--detail-limit",
        "10",
    ),
    "MUNI_WWW_GOYANG_GO_KR_AFE8FBDD": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_DDM_GO_KR_315F4471": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "500",
    ),
    "GANGBUK_RESERVATION": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_MAPO_GO_KR_7852A077": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "500",
    ),
    "MUNI_YEDU_YONGSAN_GO_KR_4E97CC33": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_GN_GO_KR_42137567": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_GN_GO_KR_E6671160": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_JONGNO_GO_KR_20A7CFB0": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "150",
        "--detail-limit",
        "800",
    ),
    "MUNI_YEYAK_DOBONGSISEOL_OR_KR_0A2D506D": (
        *DEFAULT_GENERATED_ARGUMENTS,
        "--max-pages",
        "120",
        "--detail-limit",
        "1000",
    ),
    "MUNI_YEYAK_DOBONG_GO_KR_C2700A4B": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_ANSEONG_GO_KR_5751E139": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "300",
        "--detail-limit",
        "400",
    ),
    "MUNI_GOODEDU_CHUNGJU_GO_KR_66F13E51": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "6",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_MICHUHOL_GO_KR_06925037": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "60",
        "--detail-limit",
        "300",
    ),
    "MUNI_WWW_NAJU_GO_KR_406D58D1": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "130",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_NAJU_GO_KR_DE1B1AE9": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "50",
    ),
    "MUNI_YEYAK_MIRYANG_GO_KR_0741D829": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "120",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_MIRYANG_GO_KR_F66F2E07": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "100",
    ),
    "MUNI_GIMPO_GSEEK_KR_6685FD9C": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "60",
        "--detail-limit",
        "400",
    ),
    "MUNI_GURI_GSEEK_KR_2E5F409F": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_GURI_GO_KR_E0C65498": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "60",
        "--detail-limit",
        "400",
    ),
    "MUNI_WWW_YDP_GO_KR_02AFDA7A": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "120",
        "--detail-limit",
        "1200",
    ),
    "MUNI_WWW_GWANGJIN_GO_KR_E19CED53": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "200",
    ),
    "MUNI_BOOKING_GWANGJIN_OR_KR_5B158A7E": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "1200",
    ),
    "MUNI_WWW_JUNGNANG_GO_KR_2B41C2EF": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "100",
    ),
    "MUNI_EDUCENTER_JUNGNANG_GO_KR_BDA8816B": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "250",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_JUNGNANGIMC_OR_KR_D39C4013": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "0",
    ),
    "MUNI_WWW_NOWON_KR_FBD1F92A": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "350",
        "--detail-limit",
        "2000",
    ),
    "MUNI_WWW_NOWONSC_KR_6285DA5D": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_SDM_GO_KR_C00D6125": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_SDM_GO_KR_A9E2A1F7": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "5",
        "--detail-limit",
        "100",
    ),
    "MUNI_CS_SSCMC_OR_KR_08CEA525": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "600",
    ),
    "MUNI_EDU_EUNPYEONG_GO_KR_DA5DB65F": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "80",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_EPLEARNING_OR_KR_E230FB67": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_EFMC_OR_KR_C846830E": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_YANGCHEON_GO_KR_BF8AB775": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "100",
    ),
    "MUNI_LIFESTUDY_YANGCHEON_GO_KR_9F2085A4": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_GEUMCHEON_GO_KR_237EA1EA": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "300",
    ),
    "MUNI_GEUMCHEONLIB_SEOUL_KR_E6151FD4": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "200",
    ),
    "GANGSEO_RESERVATION": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "100",
    ),
    "MUNI_LIB_GANGSEO_SEOUL_KR_520A90A3": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "300",
    ),
    "MUNI_SPORTS_GANGSEO_SEOUL_KR_8F1FDD36": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "5",
        "--detail-limit",
        "100",
    ),
    "MUNI_DOKSEODANG_SD_GO_KR_A8C20229": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "250",
        "--detail-limit",
        "2000",
    ),
    "MUNI_LIFE_GANGNAM_GO_KR_9C474A31": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_DONGJAK_GO_KR_25A73CFC": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_GWANAK_GO_KR_51D9DCB4": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "400",
    ),
    "MUNI_WWW_GURO_GO_KR_A4A5D3E3": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "300",
    ),
    "MUNI_WWW_JUNGGU_SEOUL_KR_DC13188E": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_SEOCHO_GO_KR_0866A56C": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_SONGPA_GO_KR_982793EC": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_GANGDONG_GO_KR_EBC10BD8": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "200",
    ),
    "MUNI_HEALTH_GANGDONG_GO_KR_50454384": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "100",
    ),
    "MUNI_LLL_GANGDONG_GO_KR_E8F6E943": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_GDLIBRARY_OR_KR_7E7ADF81": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "300",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_50PLUS_OR_KR_65A625B6": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "100",
    ),
    "MUNI_SLC_GANGDONG_OR_KR_A54F60C1": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "100",
    ),
    "MUNI_JUMIN_GANGDONG_GO_KR_935D7DD2": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_SEOHAE_GO_KR_679107DA": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "500",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_USC_GO_KR_AFF8D61A": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "300",
        "--detail-limit",
        "100",
    ),
    "MUNI_ETICKET_SEOGWIPO_GO_KR_C87B50AB": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_WANDO_GO_KR_AFCA6FD7": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_WANDO_GO_KR_64D0194B": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_YC_GO_KR_54558363": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "120",
        "--detail-limit",
        "200",
    ),
    "MUNI_LIFELONG_MOKPO_GO_KR_0E89BA53": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "200",
    ),
    "MUNI_LMS_SCHC_GO_KR_A117B76B": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "400",
        "--detail-limit",
        "300",
    ),
    "MUNI_WWW_SC_GO_KR_84C9C74F": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "200",
    ),
    "MUNI_SCBAY_SUNCHEON_GO_KR_CC4EA34E": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "50",
    ),
    "MUNI_WWW_GOKMG_OR_KR_58036A89": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "60",
        "--detail-limit",
        "100",
    ),
    "MUNI_GSLIB_JNE_GO_KR_80914C01": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "100",
    ),
    "MUNI_GSLIB_JNE_GO_KR_F1BD0233": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "100",
    ),
    "MUNI_EDU_GWANGSAN_GO_KR_C778CD6A": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "500",
    ),
    "MUNI_EDU_DALSEO_DAEGU_KR_14975995": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "1200",
    ),
    "MUNI_EDU_EUMSEONG_GO_KR_DEC266D9": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "3",
        "--detail-limit",
        "300",
    ),
    "MUNI_EDU_YEONGJONG_GO_KR_14BD08B1": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "300",
    ),
    "MUNI_WWW_JEMULPO_GO_KR_59B67D72": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "200",
        "--detail-limit",
        "300",
    ),
    "MUNI_WWW_GEOMDAN_GO_KR_5EA2A3D3": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "300",
        "--detail-limit",
        "400",
    ),
    "MUNI_WWW_OSANEDU_GO_KR_8A50CEDC": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "150",
        "--detail-limit",
        "600",
    ),
    "MUNI_WWW_SEOGU_GWANGJU_KR_10B34AC9": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_GEUMSAN_GO_KR_3E799FCC": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "400",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_JINCHEON_GO_KR_081643A9": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_YESAN_GO_KR_AC1B96E1": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "150",
        "--detail-limit",
        "200",
    ),
    "MUNI_TYLIB_GNE_GO_KR_7D159AC1": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "50",
    ),
    "MUNI_WWW_GHLIB_GO_KR_AAEB8BF2": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_JP_GO_KR_44B42971": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_DH_GO_KR_1A4CE8CA": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "120",
        "--detail-limit",
        "300",
    ),
    "SASANG_RESERVATION": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_GBMG_GO_KR_E3F4EA45": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "50",
    ),
    "MUNI_WWW_SACHEON_GO_KR_2360B3E8": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "300",
        "--detail-limit",
        "1200",
    ),
    "SUWON_RESERV_EDUCATION": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "300",
    ),
    "MUNI_LLL_BUSAN_GO_KR_944C621B": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "200",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_BSJUNGGU_GO_KR_C443BFF0": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "250",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_BSSEOGU_GO_KR_AACF30BC": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "250",
        "--detail-limit",
        "75",
    ),
    "MUNI_WWW_YEONGDO_GO_KR_33400564": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "300",
        "--detail-limit",
        "150",
    ),
    "MUNI_WWW_BSNAMGU_GO_KR_664BF631": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "200",
        "--detail-limit",
        "150",
    ),
    "MUNI_WWW_DONGNAE_GO_KR_742D8C71": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "150",
    ),
    "MUNI_WWW_BSBUKGU_GO_KR_E60701D6": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "450",
        "--detail-limit",
        "300",
    ),
    "MUNI_WWW_BUSANJIN_GO_KR_5881F59A": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "250",
        "--detail-limit",
        "300",
    ),
    "MUNI_WWW_ANDONG_GO_KR_1430676F": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "200",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_GOCHANG_GO_KR_45FFAF60": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "100",
    ),
    "MUNI_LIB_ANDONG_GO_KR_6B34DA7C": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "5",
        "--detail-limit",
        "20",
    ),
    "MUNI_LIB_ANDONG_GO_KR_F96F2899": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "5",
        "--detail-limit",
        "20",
    ),
    "MUNI_WWW_TAEAN_GO_KR_ADF2555A": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "300",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_SB_GO_KR_FF615DE7": (
        *DEFAULT_GENERATED_ARGUMENTS,
        "--max-pages",
        "30",
        "--detail-limit",
        "1200",
    ),
    "MUNI_WWW_JPYOUTH_CO_KR_5E838FBF": (
        *DEFAULT_GENERATED_ARGUMENTS,
        "--max-pages",
        "14",
        "--detail-limit",
        "250",
    ),
    "BUSAN_DONGGU_RESERVATION": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "300",
        "--detail-limit",
        "200",
    ),
    "MUNI_WWW_SAHA_GO_KR_ED7CDFC9": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "150",
        "--detail-limit",
        "100",
    ),
    "MUNI_LLL_BSGANGSEO_GO_KR_0691B6EB": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "150",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_HAEUNDAE_GO_KR_E2AD27FA": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "80",
        "--detail-limit",
        "250",
    ),
    "MUNI_WWW_ULSANNAMGU_GO_KR_A846A0A3": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "20",
    ),
    "MUNI_WWW_ULSANNAMGU_GO_KR_254055C7": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "120",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_PYEONGTAEK_GO_KR_54DAD706": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "250",
    ),
    "MUNI_WWW_PTLIB_GO_KR_D9537B1F": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "100",
    ),
    "MUNI_LIB_GOE_GO_KR_9D32284E": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "10",
        "--detail-limit",
        "20",
    ),
    "MUNI_WWW_NAMWON_GO_KR_37D4EA88": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "2000",
    ),
    "MUNI_WWW_NAMGU_GWANGJU_KR_8A2E3D93": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "300",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_ICBP_GO_KR_61AE4CB0": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "1200",
        "--detail-limit",
        "700",
    ),
    "MUNI_YEYAK_DSSISEOL_OR_KR_8334ABCD": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "120",
        "--detail-limit",
        "500",
    ),
    "MUNI_YANGGU_GO_KR_19704EDA": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "2",
        "--detail-limit",
        "100",
    ),
    "MUNI_LLL_PAJU_GO_KR_F639C571": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "500",
    ),
    "MUNI_PAJU_PCY_OR_KR_412053A6": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "20",
        "--detail-limit",
        "300",
    ),
    "MUNI_CN_SEOCHEON_GO_KR_096AAB21": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "400",
        "--detail-limit",
        "100",
    ),
    "MUNI_WWW_GCCITY_GO_KR_854A9E81": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "700",
        "--detail-limit",
        "600",
    ),
    "MUNI_JUMIN_NYJ_GO_KR_4D92ADDF": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "200",
        "--detail-limit",
        "1500",
    ),
    "MUNI_SUGANG_GM_GO_KR_F136DD19": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "900",
        "--detail-limit",
        "1000",
    ),
    "MUNI_WWW_SIHEUNG_GO_KR_0A4570AD": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "230",
        "--detail-limit",
        "400",
    ),
    "MUNI_SPORTSAPP_SHSI_OR_KR_6239E7D6": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "32",
        "--detail-limit",
        "300",
    ),
    "MUNI_WWW_HANAM_GO_KR_553EE539": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "240",
        "--detail-limit",
        "650",
    ),
    "MUNI_WWW_HANAM_GO_KR_04578639": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "240",
        "--detail-limit",
        "650",
    ),
    "MUNI_ONLINE_HNYOUTH_KR_6F390C33": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "240",
        "--detail-limit",
        "650",
    ),
    "MUNI_WWW_HDREAM_OR_KR_064EE411": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "240",
        "--detail-limit",
        "650",
    ),
    "MUNI_WWW_HANAMLIB_GO_KR_EE810F0A": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "240",
        "--detail-limit",
        "650",
    ),
    "MUNI_WWW_ICHEON_GO_KR_1B4316ED": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "450",
        "--detail-limit",
        "500",
    ),
    "MUNI_ICHEON_GSEEK_KR_18B68AC1": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "450",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_ICHEONLIB_GO_KR_76E3CE6D": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "450",
        "--detail-limit",
        "500",
    ),
    "MUNI_WWW_ARTIC_OR_KR_9B6E3C8E": (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "450",
        "--detail-limit",
        "500",
    ),
}
_SUWON_LIBRARY_ARGUMENTS = (
    "--save-db",
    "--mark-stale",
    "--per-target-limit",
    "0",
    "--max-pages",
    "200",
    "--detail-limit",
    "1000",
)
GENERATED_PROVIDER_ARGUMENT_OVERRIDES.update(
    {
        provider: _SUWON_LIBRARY_ARGUMENTS
        for provider in (
            "SUWON_LIBRARY_MA",
            "SUWON_LIBRARY_MB",
            "SUWON_LIBRARY_MD",
            "SUWON_LIBRARY_ME",
            "SUWON_LIBRARY_MF",
            "SUWON_LIBRARY_MG",
            "SUWON_LIBRARY_MH",
            "SUWON_LIBRARY_MI",
            "SUWON_LIBRARY_MT",
            "SUWON_LIBRARY_MU",
            "SUWON_LIBRARY_MV",
            "SUWON_LIBRARY_MW",
            "SUWON_LIBRARY_MX",
            "SUWON_LIBRARY_MY",
            "SUWON_LIBRARY_MZ",
            "SUWON_LIBRARY_SB",
            "SUWON_LIBRARY_SC",
            "SUWON_LIBRARY_SD",
            "SUWON_LIBRARY_SE",
        )
    }
)
_GUNPO_DEDICATED_ARGUMENTS = (
    "--save-db",
    "--mark-stale",
    "--per-target-limit",
    "0",
    "--max-pages",
    "250",
    "--detail-limit",
    "1500",
)
GENERATED_PROVIDER_ARGUMENT_OVERRIDES.update(
    {
        provider: _GUNPO_DEDICATED_ARGUMENTS
        for provider in (
            "MUNI_CTM_GUNPO_GO_KR_2ADC8672",
            "MUNI_SSO_GUNPO_GO_KR_C6EB5B7F",
            "MUNI_WWW_GUNPOCF_OR_KR_72C2BA1D",
            "MUNI_WWW_GPMEDIA_OR_KR_6517BB69",
            "MUNI_WWW_GUNPOLIB_GO_KR_6657561E",
            "MUNI_WWW_GUNPOUC_OR_KR_C6BD9C41",
            "MUNI_WWW_GPYF_OR_KR_85203167",
            "MUNI_WWW_GUNPO_GO_KR_FE43B335",
            "MUNI_WWW_GUNPOYCF_OR_KR_ED267E43",
        )
    }
)
_GYEONGGI_GWANGJU_DEDICATED_ARGUMENTS = (
    "--save-db",
    "--mark-stale",
    "--per-target-limit",
    "0",
    "--max-pages",
    "700",
    "--detail-limit",
    "1200",
)
GENERATED_PROVIDER_ARGUMENT_OVERRIDES.update(
    {
        provider: _GYEONGGI_GWANGJU_DEDICATED_ARGUMENTS
        for provider in (
            "MUNI_GJEDU_GSEEK_KR_F929637E",
            "MUNI_WWW_GJCITY_GO_KR_CF520672",
            "MUNI_LIB_GJCITY_GO_KR_56EBD1BF",
            "MUNI_WWW_GJCITY_GO_KR_4BA53CE8",
            "MUNI_WWW_GJCITY_GO_KR_5B834C82",
            "MUNI_WWW_GJYOUTH_OR_KR_E2AB883F",
        )
    }
)
_YANGPYEONG_DEDICATED_ARGUMENTS = (
    "--save-db",
    "--mark-stale",
    "--per-target-limit",
    "0",
    "--max-pages",
    "100",
    "--detail-limit",
    "100",
)
GENERATED_PROVIDER_ARGUMENT_OVERRIDES.update(
    {
        provider: _YANGPYEONG_DEDICATED_ARGUMENTS
        for provider in (
            "MUNI_YPEDU_GSEEK_KR_41263F0B",
            "MUNI_WWW_YP21_GO_KR_EA0D7B81",
            "MUNI_WWW_YP21_GO_KR_632CD45F",
            "MUNI_WWW_YPLIB_GO_KR_C3854B7C",
        )
    }
)
_YONGIN_DEDICATED_LIMITS = {
    "MUNI_RESVE_YONGIN_GO_KR_221336AC": (20, 100),
    "MUNI_JACHI_YONGIN_GO_KR_10340408": (60, 250),
    "MUNI_JACHI_YONGIN_GO_KR_60025DB9": (70, 800),
    "MUNI_JACHI_YONGIN_GO_KR_91C5118C": (50, 600),
    "MUNI_LIB_YONGIN_GO_KR_B7626320": (60, 250),
    "MUNI_WWW_YICF_OR_KR_B2E137D5": (10, 40),
    "MUNI_YIYF_OR_KR_F56DFD54": (5, 20),
    "MUNI_SPORTS_YIYF_OR_KR_206DDBA6": (35, 260),
}
GENERATED_PROVIDER_ARGUMENT_OVERRIDES.update(
    {
        provider: (
            "--save-db",
            "--mark-stale",
            "--per-target-limit",
            "0",
            "--max-pages",
            str(max_pages),
            "--detail-limit",
            str(detail_limit),
        )
        for provider, (max_pages, detail_limit) in _YONGIN_DEDICATED_LIMITS.items()
    }
)
_SAMCHEOK_DEDICATED_LIMITS = {
    "MUNI_WWW_SAMCHEOK_GO_KR_AEA01740": (20, 300),
    "MUNI_YOUTH_SAMCHEOK_GO_KR_96E8E691": (10, 100),
    "MUNI_DGYOUTH_SAMCHEOK_GO_KR_C683FA1B": (10, 100),
    "MUNI_WDYOUTH_SAMCHEOK_GO_KR_AE04F451": (10, 100),
}
_HOENGSEONG_DEDICATED_LIMITS = {
    "MUNI_WWW_HSG_GO_KR_7452F27B": (20, 100),
    "MUNI_LIB_HSG_GO_KR_F84FF98D": (2, 25),
    "MUNI_LIB_GWE_GO_KR_5CEF7967": (2, 25),
    "MUNI_HSYOUTHCENTER_HSG_GO_KR_46DEDE77": (5, 50),
    "MUNI_HS_CULTURE_OR_KR_B2E1E14F": (1, 50),
    "MUNI_HSG_FAMILYNET_OR_KR_4676E082": (10, 50),
}
_CHEONAN_DEDICATED_LIMITS = {
    "MUNI_WWW_CHEONAN_GO_KR_478DFA4B": (5, 50),
    "MUNI_WWW_CHEONAN_GO_KR_5BC13FB4": (60, 50),
    "MUNI_WWW_CHEONAN_GO_KR_7F8F5560": (5, 100),
    "MUNI_WWW_CHEONAN_GO_KR_C97CA6FD": (5, 100),
    "MUNI_WWW_CHEONAN_GO_KR_EA8D366B": (3, 100),
    "MUNI_WWW_CHEONANLIFEEDU_ORG_41183F3B": (5, 50),
    "MUNI_WWW_XN_2Z1BR4K89DEOA28DJVFZVASSQ98BDZK_KR_81F": (20, 50),
}
_JEJU_CITY_DEDICATED_LIMITS = {
    "MUNI_WWW_JEJU_GO_KR_2B65844D": (30, 100),
    "MUNI_WWW_JEJUSI_GO_KR_72D06B44": (40, 100),
    "MUNI_WWW_JEJUSI_GO_KR_A449522B": (25, 50),
    "MUNI_WWW_JEJU_GO_KR_6E577892": (100, 50),
    "MUNI_WWW_JEJU_GO_KR_310502FA": (20, 100),
    "MUNI_AGRI_JEJU_GO_KR_84F944BE": (35, 50),
    "MUNI_WWW_JEJUSI_GO_KR_F9643CD9": (25, 50),
    "MUNI_JJDREAMLIB_OR_KR_1A8AAB7D": (35, 50),
}
_JEONGSEON_LIBRARY_DEDICATED_LIMITS = {
    "MUNI_LIB_GWE_GO_KR_20A09F24": (20, 200),
    "MUNI_LIB_JEONGSEON_GO_KR_DD359707": (30, 300),
}
_GYEONGJU_DEDICATED_LIMITS = {
    "MUNI_WWW_GYEONGJU_GO_KR_ADA8A467": (80, 500),
}
_ULSAN_JUNGGU_DEDICATED_LIMITS = {
    "MUNI_WWW_JUNGGU_ULSAN_KR_9703AC0F": (140, 300),
}
GENERATED_PROVIDER_ARGUMENT_OVERRIDES.update(
    {
        provider: (
            "--save-db",
            "--mark-stale",
            "--per-target-limit",
            "0",
            "--max-pages",
            str(max_pages),
            "--detail-limit",
            str(detail_limit),
        )
        for provider, (max_pages, detail_limit) in {
            **_SAMCHEOK_DEDICATED_LIMITS,
            **_HOENGSEONG_DEDICATED_LIMITS,
            **_CHEONAN_DEDICATED_LIMITS,
            **_JEJU_CITY_DEDICATED_LIMITS,
            **_JEONGSEON_LIBRARY_DEDICATED_LIMITS,
            **_GYEONGJU_DEDICATED_LIMITS,
            **_ULSAN_JUNGGU_DEDICATED_LIMITS,
        }.items()
    }
)
DUPLICATE_QUERY_DROP_PARAMS = {
    "_",
    "callback",
    "currentpage",
    "currentpageno",
    "currpage",
    "nowpage",
    "page",
    "pageindex",
    "pageno",
    "pageunit",
    "pagesize",
    "recordcountperpage",
    "rows",
    "timestamp",
    "token",
    "ts",
    "viewtype",
}

logger = setup_logger(__name__, "logs/crawler_generated_yaml_targets.log")


def validate_provider(provider: Any) -> str:
    value = clean_text(provider).upper()
    if not PROVIDER_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid provider identifier: {value!r}")
    return value


def safe_module_name(provider: str) -> str:
    return validate_provider(provider)


SAFE_RAW_URL_FRAGMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")


def normalize_http_url(
    value: Any,
    *,
    required: bool = False,
    preserve_safe_fragment: bool = False,
) -> str:
    raw = "" if value is None else str(value)
    if any(ord(character) < 32 for character in raw):
        raise ValueError("URL contains invalid control characters")
    text = clean_text(raw)
    if not text:
        if required:
            raise ValueError("URL is required")
        return ""
    if len(text) > MAX_URL_LENGTH:
        raise ValueError("URL is too long")
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL userinfo is not permitted")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL port is invalid") from exc
    hostname = parsed.hostname.lower().rstrip(".")
    if not hostname or any(character.isspace() for character in hostname):
        raise ValueError("URL hostname is invalid")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    fragment = (
        parsed.fragment
        if preserve_safe_fragment
        and SAFE_RAW_URL_FRAGMENT_PATTERN.fullmatch(parsed.fragment)
        else ""
    )
    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            re.sub(r"/{2,}", "/", parsed.path or "/"),
            "",
            parsed.query,
            fragment,
        )
    )


def safe_log_url(value: Any) -> str:
    try:
        parsed = urlparse(normalize_http_url(value, required=True))
    except ValueError:
        return "[invalid-url]"
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def redact_sensitive_text(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""

    def redact_url(match: re.Match[str]) -> str:
        return safe_log_url(match.group(0).rstrip(".,);]"))

    text = re.sub(r"https?://[^\s<>'\"]+", redact_url, text, flags=re.IGNORECASE)
    key_pattern = "|".join(
        re.escape(key) for key in sorted(SENSITIVE_QUERY_KEYS, key=len, reverse=True)
    )
    return re.sub(
        rf"(?i)\b({key_pattern})\b\s*[:=]\s*[^\s,;&]+",
        r"\1=[REDACTED]",
        text,
    )[:1000]


def reject_tls_downgrade(request_url: Any, response: Any) -> Any:
    if urlparse(str(request_url)).scheme.lower() != "https":
        return response
    response_urls = [
        str(getattr(item, "url", ""))
        for item in [*(getattr(response, "history", []) or []), response]
    ]
    if any(urlparse(url).scheme.lower() == "http" for url in response_urls):
        response.close()
        raise OutboundRequestBlocked("HTTPS request attempted a plaintext redirect")
    return response


def target_url(target: dict[str, Any]) -> str:
    value = (
        target.get("url")
        or target.get("list_url")
        or target.get("base_url")
        or target.get("main_url")
    )
    return normalize_http_url(value) if clean_text(value) else ""


def normalized_duplicate_url(url: str) -> str:
    text = clean_text(url)
    if not text:
        return ""
    parsed = urlparse(text)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in DUPLICATE_QUERY_DROP_PARAMS:
            continue
        query_pairs.append((key, value))
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def municipal_operational_target_municipalities(
    path: Path = MUNICIPAL_OPERATIONAL_FILE,
    municipality_master_path: Path = MUNICIPALITY_MASTER_FILE,
) -> dict[tuple[str, str], tuple[tuple[str, str], ...]]:
    """Return reviewed municipality pairs keyed by exact operational target.

    Provider names are not sufficient ownership evidence because one provider
    can have operational and non-operational sibling URLs.  Keep this compact
    reader independent of the aggregate module (which imports this module) and
    validate the geography needed by the DB writer before exposing a contract.
    """

    if not path.is_file():
        return {}
    document = load_unique_yaml(path) or {}
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("municipal operational manifest must have version: 1")
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise ValueError("municipal operational manifest entries must be a list")

    master_document = load_unique_yaml(municipality_master_path) or {}
    if not isinstance(master_document, dict) or master_document.get("version") != 1:
        raise ValueError("municipality master must have version: 1")
    master_rows = master_document.get("municipalities")
    if (
        not isinstance(master_rows, list)
        or len(master_rows) != OFFICIAL_MUNICIPALITY_COUNT
    ):
        raise ValueError(
            f"municipality master must contain exactly {OFFICIAL_MUNICIPALITY_COUNT} municipalities"
        )
    master_by_code: dict[str, tuple[str, str, str]] = {}
    master_name_codes: dict[str, str] = {}
    for master_index, municipality in enumerate(master_rows, start=1):
        if not isinstance(municipality, Mapping):
            raise ValueError(
                f"municipality master row {master_index}: must be a mapping"
            )
        code = readable_text(municipality.get("code"))
        sido = readable_text(municipality.get("sido"))
        sigungu = readable_text(municipality.get("sigungu"))
        full_name = readable_text(municipality.get("full_name"))
        derived_sido, derived_sigungu = (
            municipal_yaml_module.municipality_region_from_row(
                {
                    "municipality_code": code,
                    "municipality_full_name": full_name,
                }
            )
        )
        if (
            not re.fullmatch(r"\d{10}", code)
            or not full_name
            or not derived_sido
            or not derived_sigungu
            or sido != derived_sido
            or sigungu != derived_sigungu
            or code in master_by_code
            or (full_name in master_name_codes and master_name_codes[full_name] != code)
        ):
            raise ValueError(
                f"municipality master row {master_index}: invalid or conflicting municipality"
            )
        master_by_code[code] = (sido, sigungu, full_name)
        master_name_codes[full_name] = code

    contracts: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
    normalized_url_providers: dict[str, str] = {}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, Mapping):
            raise ValueError(f"operational entry {index}: must be a mapping")
        provider = validate_provider(entry.get("provider"))
        target = normalize_http_url(entry.get("target_url"), required=True)
        normalized_url = readable_text(entry.get("normalized_url"))
        if normalized_url != normalized_duplicate_url(target):
            raise ValueError(
                f"operational entry {index}: normalized_url does not match target_url"
            )
        key = (provider, normalized_url)
        if key in contracts:
            raise ValueError(
                f"operational entry {index}: duplicate provider/url allowlist key"
            )
        previous_provider = normalized_url_providers.get(normalized_url)
        if previous_provider and previous_provider != provider:
            raise ValueError(
                f"operational entry {index}: normalized_url has conflicting providers"
            )

        raw_municipalities = entry.get("municipalities")
        if not isinstance(raw_municipalities, list) or not raw_municipalities:
            raise ValueError(
                f"operational entry {index}: municipalities must be a non-empty list"
            )
        pairs: list[tuple[str, str]] = []
        code_names: dict[str, str] = {}
        name_codes: dict[str, str] = {}
        for municipality_index, municipality in enumerate(raw_municipalities, start=1):
            if not isinstance(municipality, Mapping):
                raise ValueError(
                    f"operational entry {index} municipality {municipality_index}: must be a mapping"
                )
            code = readable_text(municipality.get("code"))
            full_name = readable_text(municipality.get("full_name"))
            region_sido, region_sigungu = (
                municipal_yaml_module.municipality_region_from_row(
                    {
                        "municipality_code": code,
                        "municipality_full_name": full_name,
                    }
                )
            )
            declared_sido = readable_text(municipality.get("sido"))
            declared_sigungu = readable_text(municipality.get("sigungu"))
            if (
                not re.fullmatch(r"\d{10}", code)
                or not region_sido
                or not region_sigungu
                or declared_sido != region_sido
                or declared_sigungu != region_sigungu
                or master_by_code.get(code)
                != (declared_sido, declared_sigungu, full_name)
                or (code in code_names and code_names[code] != full_name)
                or (full_name in name_codes and name_codes[full_name] != code)
                or (code, full_name) in pairs
            ):
                raise ValueError(
                    f"operational entry {index} municipality {municipality_index}: "
                    "invalid or conflicting municipality pair"
                )
            code_names[code] = full_name
            name_codes[full_name] = code
            pairs.append((code, full_name))
        contracts[key] = tuple(pairs)
        normalized_url_providers[normalized_url] = provider
    return contracts


MUNICIPAL_OPERATIONAL_TARGET_MUNICIPALITIES = (
    municipal_operational_target_municipalities()
)


def target_scope_keys(target: dict[str, Any]) -> list[str]:
    explicit_scope = clean_text(
        target.get("crawl_scope") or target.get("collection_scope")
    )
    if explicit_scope:
        return [f"scope:{explicit_scope.lower()}"]
    urls: list[str] = []
    for key in ("url", "list_url", "base_url"):
        value = clean_text(target.get(key))
        if value:
            urls.append(value)
    for value in target.get("list_urls") or []:
        if clean_text(value):
            urls.append(clean_text(value))
    keys = [normalized_duplicate_url(url) for url in urls]
    return [key for key in dict.fromkeys(keys) if key]


def explicit_duplicate_reason(target: dict[str, Any]) -> str:
    duplicate_of = clean_text(target.get("duplicate_of"))
    if duplicate_of:
        return f"duplicate_of:{duplicate_of}"
    blocked_reason = clean_text(target.get("blocked_reason"))
    if blocked_reason.lower().startswith("duplicate_of:"):
        return blocked_reason
    error_kind = clean_text(
        (target.get("last_quality") or {}).get("error_kind")
        if isinstance(target.get("last_quality"), dict)
        else ""
    )
    if error_kind.lower().startswith("duplicate_of:"):
        return error_kind
    if clean_text(target.get("collection_type")).lower() == "duplicate":
        return "duplicate_collection_type"
    status = clean_text(target.get("crawler_status") or target.get("status"))
    if status.lower().startswith("duplicate_url:"):
        return status
    return ""


def url_has_excluded_domain(url: str) -> bool:
    value = url.lower()
    return any(
        token in value
        for token in EXCLUDED_URL_DOMAIN_TOKENS + EXCLUDED_URL_MEDIA_DOMAINS
    )


def url_has_excluded_path(url: str) -> bool:
    return any(token.lower() in url.lower() for token in EXCLUDED_URL_PATH_TOKENS)


def validate_target_row(target: dict[str, Any], *, label: str = "target") -> None:
    missing = sorted(
        field for field in REQUIRED_TARGET_FIELDS if target.get(field) in (None, "", [])
    )
    if missing:
        raise ValueError(f"{label}: missing required fields: {', '.join(missing)}")
    validate_provider(target.get("provider"))
    for field in (
        "name",
        "branch",
        "collection_category",
        "domain_category",
        "operator_type",
        "source_group",
        "collection_type",
        "source",
        "origin",
    ):
        value = target.get(field)
        if not isinstance(value, str) or not clean_text(value):
            raise ValueError(f"{label}: {field} must be a non-empty string")
        if any(
            ord(character) < 32 and character not in "\t\n\r" for character in value
        ):
            raise ValueError(f"{label}: {field} contains control characters")
    priority = target.get("priority")
    if (
        isinstance(priority, bool)
        or not isinstance(priority, int)
        or not 1 <= priority <= 9
    ):
        raise ValueError(f"{label}: priority must be an integer from 1 to 9")
    status = clean_text(target.get("crawler_status") or target.get("status")).lower()
    allowed_statuses = (
        WORKING_CRAWLER_STATUSES
        | REGISTRY_CRAWLER_STATUSES
        | RECHECKABLE_CRAWLER_STATUSES
        | NON_EXECUTABLE_STATUSES
    )
    if status not in allowed_statuses and not status.startswith("duplicate_url:"):
        raise ValueError(f"{label}: unsupported crawler_status={status!r}")

    def validate_config_url(value: Any) -> None:
        normalized = normalize_http_url(value, required=True)
        if re.search(r";j?sessionid=", urlparse(normalized).path, re.IGNORECASE):
            raise ValueError(f"{label}: URL contains a persisted session identifier")
        sensitive_keys = {
            key.lower()
            for key, _ in parse_qsl(urlparse(normalized).query, keep_blank_values=True)
            if key.lower() in SENSITIVE_QUERY_KEYS
        }
        if sensitive_keys:
            raise ValueError(
                f"{label}: URL contains secret-bearing query keys: {', '.join(sorted(sensitive_keys))}"
            )

    validate_config_url(target.get("url"))
    for field in ("base_url", "list_url", "source_url", "main_url"):
        if target.get(field):
            validate_config_url(target.get(field))
    list_urls = target.get("list_urls") or []
    if not isinstance(list_urls, list) or len(list_urls) > 100:
        raise ValueError(f"{label}: list_urls must be a list with at most 100 entries")
    for value in list_urls:
        validate_config_url(value)
    duplicate_of = clean_text(target.get("duplicate_of"))
    if duplicate_of:
        validate_provider(duplicate_of)
    if status.startswith("duplicate_url:") and not duplicate_of:
        raise ValueError(f"{label}: duplicate_url status requires duplicate_of")
    if target.get("last_quality") is not None and not isinstance(
        target.get("last_quality"), dict
    ):
        raise ValueError(f"{label}: last_quality must be a mapping")
    for field in SELECTOR_FIELDS:
        if field not in target:
            continue
        selector = target.get(field)
        if not isinstance(selector, str) or len(selector) > 500:
            raise ValueError(
                f"{label}: {field} must be a CSS selector string of at most 500 characters"
            )
        try:
            soupsieve.compile(selector)
        except Exception as exc:
            raise ValueError(f"{label}: invalid CSS selector in {field}") from exc
    for field in JSON_PATH_FIELDS:
        if field not in target:
            continue
        json_path = target.get(field)
        if (
            not isinstance(json_path, str)
            or len(json_path) > 500
            or not re.fullmatch(
                r"\$?(?:(?:\.[A-Za-z_][A-Za-z0-9_-]*)|(?:\[(?:\d+|\*)\]))+",
                json_path,
            )
        ):
            raise ValueError(f"{label}: invalid restricted JSON path in {field}")


def _load_target_rows(path: Path) -> list[dict[str, Any]]:
    data = load_unique_yaml(path) or {}
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"Invalid target document version: {path}")
    targets = data.get("targets") or []
    if not isinstance(targets, list):
        raise ValueError(f"Invalid target file: {path}")
    defaults = {
        key: readable_text(data.get(key))
        for key in (
            "collection_category",
            "domain_category",
            "source_group",
            "operator_type",
            "service_group",
        )
        if readable_text(data.get(key))
    }
    rows: list[dict[str, Any]] = []
    for index, target in enumerate(targets, start=1):
        if not isinstance(target, dict):
            raise ValueError(f"{path}:{index}: target must be a mapping")
        merged = {**defaults, **target}
        validate_target_row(merged, label=f"{path}:{index}")
        rows.append(merged)
    return rows


def _iter_target_rows(path: Path) -> list[dict[str, Any]]:
    if path == TARGETS_FILE and TARGET_DIR.exists():
        rows: list[dict[str, Any]] = []
        for target_file in sorted(TARGET_DIR.glob("*.yaml")):
            if target_file.name == "index.yaml":
                continue
            for row in _load_target_rows(target_file):
                row.setdefault("_target_file", target_file.name)
                rows.append(row)
        return rows
    return _load_target_rows(path)


def _is_working_target(
    target: dict[str, Any],
    extra_statuses: Optional[set[str]] = None,
    *,
    dedicated_provider: str = "",
) -> bool:
    provider = clean_text(target.get("provider")).upper()
    if provider in DEDICATED_PROVIDER_NAMES and provider != dedicated_provider:
        return False
    if explicit_duplicate_reason(target):
        return False
    status = clean_text(target.get("crawler_status") or target.get("status")).lower()
    url = target_url(target)
    if not url:
        return False
    if (
        url_has_excluded_domain(url)
        and provider not in REVIEWED_EXCLUDED_DOMAIN_PROVIDERS
    ):
        return False
    if url_has_excluded_path(url):
        return False
    allowed_statuses = set(WORKING_CRAWLER_STATUSES)
    if extra_statuses:
        allowed_statuses.update(extra_statuses)
    return status in allowed_statuses


def _is_registry_target(target: dict[str, Any]) -> bool:
    provider = clean_text(target.get("provider")).upper()
    if provider in DEDICATED_PROVIDER_NAMES:
        return False
    if provider == "SEOSAN_WELFARE_TOTAL_RESERVATION":
        # This target is executed by run_crawlers' full-snapshot static command.
        # It must remain available to the shared YAML loader used by that
        # command and by aggregate selection, but must not get a second wrapper.
        return False
    if (
        provider in MUNICIPAL_OPERATIONAL_PROVIDER_NAMES
        and provider not in PRODUCTION_SCHEDULED_PROVIDER_NAMES
    ):
        return False
    if (
        clean_text(target.get("source_group")).lower() == "municipal_reservation"
        and (
            clean_text(target.get("origin")).lower() == "live_validated"
            or clean_text(target.get("_target_file")).lower()
            == "municipal_integrated_reservation.yaml"
        )
        and provider not in PRODUCTION_SCHEDULED_PROVIDER_NAMES
    ):
        # These providers are selected through the bounded municipal aggregate.
        # An explicitly production-scheduled provider remains in the registry;
        # the municipal aggregate excludes it to preserve exactly one route.
        # Registering every other promoted URL separately would exhaust the
        # worker's provider registry while providing a second path to the rows.
        return False
    status = clean_text(target.get("crawler_status") or target.get("status")).lower()
    url = target_url(target)
    if not provider or not url:
        return False
    if (
        url_has_excluded_domain(url)
        and provider not in REVIEWED_EXCLUDED_DOMAIN_PROVIDERS
    ):
        return False
    return status in REGISTRY_CRAWLER_STATUSES


def load_yaml_targets(
    path: Path = TARGETS_FILE,
    extra_statuses: Optional[set[str]] = None,
    *,
    dedicated_provider: str = "",
) -> list[dict[str, Any]]:
    if dedicated_provider:
        dedicated_provider = validate_provider(dedicated_provider)
        if dedicated_provider not in DEDICATED_PROVIDER_NAMES:
            raise ValueError(
                f"Provider is not registered as dedicated: {dedicated_provider}"
            )
    return dedupe_targets(
        [
            target
            for target in _iter_target_rows(path)
            if _is_working_target(
                target,
                extra_statuses=extra_statuses,
                dedicated_provider=dedicated_provider,
            )
        ]
    )


def load_registry_targets(path: Path = TARGETS_FILE) -> list[dict[str, Any]]:
    return [target for target in _iter_target_rows(path) if _is_registry_target(target)]


def target_preference_key(target: dict[str, Any]) -> tuple[int, int, str, str]:
    status = clean_text(target.get("crawler_status") or target.get("status")).lower()
    status_rank = {
        "ready": 0,
        "partial": 1,
        "generated": 2,
        "candidate": 3,
        "needs_parser": 4,
        "needs_discovery": 5,
        "blocked": 6,
    }.get(status, 9)
    return (
        int(target.get("priority") or 9),
        status_rank,
        clean_text(target.get("source")),
        clean_text(target.get("provider")),
    )


def dedupe_targets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for item in sorted(items, key=target_preference_key):
        provider = clean_text(item.get("provider"))
        keys = target_scope_keys(item)
        duplicate_owner = next((seen[key] for key in keys if key in seen), "")
        if duplicate_owner:
            logger.debug(
                "Skipping duplicate target %s; overlaps with %s",
                provider,
                duplicate_owner,
            )
            continue
        for key in keys:
            seen[key] = provider
        selected.append(item)
    return selected


def to_crawl_target(item: dict[str, Any]) -> CrawlTarget:
    # Keep direct generated-provider runs consistent with the EXPERIENCE_TARGETS
    # macro.  The import is intentionally lazy to avoid a module import cycle.
    from Crawler.Crawler_EducationExperience import (
        is_experience_target,
        prepare_experience_target,
    )

    provider = clean_text(item.get("provider"))[:50]
    # Seoul T200 is a locked, dedicated ledger with its own complete global +
    # 25-district census. Generic main-site discovery would run a second
    # ownership path and can overwrite the direct collector's fail-closed meta.
    if provider != "SEOUL_PUBLIC_SERVICE" and is_experience_target(item):
        item = prepare_experience_target(item)
    fallback_label = provider_code_label(provider, target_url(item))
    name = readable_text(item.get("name"), item.get("branch"), fallback_label)
    branch = readable_text(item.get("branch"), name, fallback_label)
    return CrawlTarget(
        provider=provider,
        name=name,
        branch=branch,
        url=target_url(item),
        source=clean_text(item.get("source")),
        priority=int(item.get("priority") or 9),
        region=readable_text(item.get("region")),
        extra=item,
    )


def apply_target_metadata(rows: list[dict[str, Any]], target: CrawlTarget) -> None:
    source_endpoint = canonical_source_endpoint(target.url)
    if not source_endpoint:
        raise ValueError("crawl target has no safe canonical source endpoint")
    for row in rows:
        row.setdefault("source_endpoint", source_endpoint)
        raw_fields = row.setdefault("raw_fields", {})
        if isinstance(raw_fields, dict):
            raw_fields.setdefault("source_endpoint", source_endpoint)
    metadata_keys = (
        "collection_category",
        "domain_category",
        "source_group",
        "operator_type",
        "service_group",
        "service_group_policy",
        "municipality_code",
        "municipality_full_name",
        "collection_type",
    )
    metadata = {
        key: readable_text(target.extra.get(key))
        for key in metadata_keys
        if readable_text(target.extra.get(key))
    }
    if "collection_category" not in metadata and metadata.get("domain_category"):
        metadata["collection_category"] = metadata["domain_category"]
    metadata.setdefault(
        "service_group",
        infer_service_group(
            provider=target.provider,
            collection_category=metadata.get("collection_category"),
            domain_category=metadata.get("domain_category"),
            source_group=metadata.get("source_group"),
            operator_type=metadata.get("operator_type"),
            branch_name=target.branch,
            raw_url=target.url,
        ),
    )
    if not metadata:
        return
    locked = metadata.get("service_group_policy", "").lower() == "locked"
    locked_keys = {
        "collection_category",
        "domain_category",
        "source_group",
        "service_group",
        "service_group_policy",
    }
    municipality_keys = {"municipality_code", "municipality_full_name"}

    def trusted_municipality_pair(values: Mapping[str, Any]) -> tuple[str, str] | None:
        code = readable_text(values.get("municipality_code"))
        full_name = readable_text(values.get("municipality_full_name"))
        if not re.fullmatch(r"\d{10}", code):
            return None
        region_sido, region_sigungu = (
            municipal_yaml_module.municipality_region_from_row(
                {
                    "municipality_code": code,
                    "municipality_full_name": full_name,
                }
            )
        )
        if not region_sido or not region_sigungu:
            return None
        return code, full_name

    target_municipality = trusted_municipality_pair(metadata)
    canonical_declared = bool(
        readable_text(target.extra.get("municipality_code"))
        or readable_text(target.extra.get("municipality_full_name"))
    )

    # Target-local municipality declarations remain the narrow opt-in contract
    # for standalone targets. Operational ownership and coverage come from the
    # exact provider+URL manifest map below, so legacy sibling metadata cannot
    # accidentally widen that reviewed boundary.
    target_allowed_municipalities: set[tuple[str, str]] = set()
    target_declarations_valid = (
        not canonical_declared or target_municipality is not None
    )
    covered_declared = "covered_municipalities" in target.extra
    covered_values = target.extra.get("covered_municipalities")
    if covered_declared and not isinstance(covered_values, list):
        target_declarations_valid = False
        covered_rows: list[Any] = []
    else:
        covered_rows = covered_values or []
        if covered_declared and not covered_rows:
            target_declarations_valid = False

    code_names: dict[str, str] = {}
    name_codes: dict[str, str] = {}
    covered_pairs: set[tuple[str, str]] = set()
    for covered in covered_rows:
        if not isinstance(covered, Mapping):
            target_declarations_valid = False
            continue
        pair = trusted_municipality_pair(
            {
                "municipality_code": covered.get("code"),
                "municipality_full_name": covered.get("full_name"),
            }
        )
        if pair is None:
            target_declarations_valid = False
            continue
        code, full_name = pair
        region_sido, region_sigungu = (
            municipal_yaml_module.municipality_region_from_row(
                {
                    "municipality_code": code,
                    "municipality_full_name": full_name,
                }
            )
        )
        declared_sido = readable_text(covered.get("sido"))
        declared_sigungu = readable_text(covered.get("sigungu"))
        if (
            (declared_sido and declared_sido != region_sido)
            or (declared_sigungu and declared_sigungu != region_sigungu)
            or (code in code_names and code_names[code] != full_name)
            or (full_name in name_codes and name_codes[full_name] != code)
        ):
            target_declarations_valid = False
            continue
        code_names[code] = full_name
        name_codes[full_name] = code
        covered_pairs.add(pair)

    if target_municipality is not None:
        target_allowed_municipalities.add(target_municipality)
    target_allowed_municipalities.update(covered_pairs)
    if (
        canonical_declared
        and covered_declared
        and target_municipality not in covered_pairs
    ):
        target_declarations_valid = False
    municipality_contract_valid = bool(
        target_declarations_valid and target_allowed_municipalities
    )

    target_municipality_region_verified = bool(
        target_municipality is not None
        and target.extra.get("municipality_region_verified") is True
    )
    provider = clean_text(target.provider).upper()
    operational_key = (provider, normalized_duplicate_url(target.url))
    manifest_municipalities = MUNICIPAL_OPERATIONAL_TARGET_MUNICIPALITIES.get(
        operational_key
    )
    operational_target_bound = manifest_municipalities is not None
    allowed_municipalities = set(manifest_municipalities or ())
    canonical_operational_municipality = (
        manifest_municipalities[0] if manifest_municipalities else None
    )
    operational_municipality_contract = bool(
        operational_target_bound
        and (not canonical_declared or target_municipality in allowed_municipalities)
    )
    for row in rows:
        original_code = readable_text(row.get("municipality_code"))
        original_full_name = readable_text(row.get("municipality_full_name"))
        original_municipality = trusted_municipality_pair(row)
        municipality_missing = not original_code and not original_full_name
        municipality_verification_vetoed = (
            row.get("municipality_region_verified") is False
        )

        if operational_target_bound:
            # Single-municipality operational targets may safely supply their
            # sole canonical pair when a collector has no row-level geography.
            # Invalid/partial row evidence is a conflict, not a missing value.
            if (
                operational_municipality_contract
                and len(allowed_municipalities) == 1
                and municipality_missing
                and not municipality_verification_vetoed
                and canonical_operational_municipality in allowed_municipalities
            ):
                row["municipality_code"], row["municipality_full_name"] = (
                    canonical_operational_municipality
                )
                original_municipality = canonical_operational_municipality

            # Multi-municipality targets only trust a complete pair that the
            # collector supplied before target metadata was applied.  This is
            # what prevents a representative city from swallowing district rows.
            if (
                operational_municipality_contract
                and not municipality_verification_vetoed
                and original_municipality in allowed_municipalities
                and (len(allowed_municipalities) == 1 or not municipality_missing)
            ):
                row["municipality_region_verified"] = True
            else:
                if municipality_verification_vetoed or not municipality_missing:
                    # Preserve collector evidence for diagnostics while an
                    # explicit False veto prevents legacy provider-level writer
                    # exceptions from trusting conflicts or out-of-scope rows.
                    row["municipality_region_verified"] = False
                else:
                    row.pop("municipality_region_verified", None)
        elif provider in MUNICIPAL_OPERATIONAL_PROVIDER_NAMES:
            # A provider can own additional experience or legacy sibling URLs.
            # Exact provider+URL manifest ownership is required before granting
            # municipality trust; keep any collector evidence intact.
            if municipality_verification_vetoed or not municipality_missing:
                row["municipality_region_verified"] = False
            else:
                row.pop("municipality_region_verified", None)
        else:
            # Preserve the narrow explicit opt-in used by reviewed standalone
            # single-municipality targets.  A partial or conflicting collector
            # pair is never replaced and never verified.
            if (
                target_municipality_region_verified
                and municipality_contract_valid
                and municipality_missing
                and not municipality_verification_vetoed
            ):
                row["municipality_code"], row["municipality_full_name"] = (
                    target_municipality
                )
                original_municipality = target_municipality
            elif (
                not municipality_verification_vetoed
                and original_municipality is None
                and target_municipality is not None
            ):
                # Retain legacy display metadata without granting branch-region
                # trust.  Partial/invalid evidence remains fail-closed.
                if municipality_missing:
                    row["municipality_code"], row["municipality_full_name"] = (
                        target_municipality
                    )
                    original_municipality = target_municipality
            if (
                target_municipality_region_verified
                and not municipality_verification_vetoed
                and municipality_contract_valid
                and original_municipality == target_municipality
            ):
                row["municipality_region_verified"] = True
            elif (
                target_municipality_region_verified
                and not municipality_verification_vetoed
            ):
                row.pop("municipality_region_verified", None)
        explicit_course_level_experience = bool(
            readable_text(row.get("domain_category")) == "체험·견학"
            and readable_text(row.get("service_group")) == SERVICE_GROUP_EXPERIENCE
        )
        preserve_row_mixed_classification = bool(
            provider in MIXED_ROW_CLASSIFICATION_PROVIDERS
            and readable_text(row.get("service_group_policy")).lower()
            in {"inferred", "locked"}
            and (
                explicit_course_level_experience
                or infer_experience_institution_source_group(
                    source_group=row.get("source_group"),
                    branch_name=row.get("branch"),
                    collection_category=row.get("collection_category"),
                    domain_category=row.get("domain_category"),
                )
            )
        )
        for key, value in metadata.items():
            if key in municipality_keys:
                continue
            if (
                preserve_row_mixed_classification
                and key in ROW_LEVEL_INSTITUTION_CLASSIFICATION_KEYS
            ):
                continue
            if locked and key in locked_keys:
                row[key] = value
            else:
                row.setdefault(key, value)
        effective_program_type = municipal_yaml_module.normalize_program_type(
            row.get("program_type"),
            row.get("title_raw"),
            row.get("title"),
            row.get("category_raw") or row.get("category"),
            row.get("collection_category"),
            row.get("domain_category"),
            row.get("source_group"),
            row.get("description"),
            row.get("raw_url"),
        )
        effective_service_group = infer_service_group(
            provider=target.provider,
            collection_category=row.get("collection_category")
            or row.get("domain_category"),
            domain_category=row.get("domain_category"),
            source_group=row.get("source_group"),
            operator_type=row.get("operator_type"),
            branch_name=row.get("branch"),
            venue_name=row.get("venue_name")
            or row.get("venue")
            or row.get("place")
            or row.get("room"),
            raw_url=row.get("raw_url") or target.url,
            title=" ".join(
                value
                for value in (
                    readable_text(row.get("title_raw")),
                    readable_text(row.get("title")),
                )
                if value
            ),
            category_raw=row.get("category_raw") or row.get("category"),
            program_type=effective_program_type,
            service_group=row.get("service_group"),
        )
        if effective_service_group != SERVICE_GROUP_EXPERIENCE:
            continue
        experience_defaults = {
            "target": "\uc804\uccb4",
            "fee": "\uc694\uae08 \ubcc4\ub3c4 \uc548\ub0b4",
            "period": "\uc77c\uc815 \ubcc4\ub3c4 \uc548\ub0b4",
            "venue_name": readable_text(
                row.get("branch"),
                target.branch,
                target.name,
                "\uc7a5\uc18c \ubcc4\ub3c4 \uc548\ub0b4",
            ),
            "category": readable_text(
                row.get("domain_category"),
                target.extra.get("domain_category"),
                "\uccb4\ud5d8",
            ),
            "schedule_raw": "\uc2dc\uac04 \ubcc4\ub3c4 \uc548\ub0b4",
        }
        for key, value in experience_defaults.items():
            if not readable_text(row.get(key)):
                row[key] = value


def select_targets(
    items: list[dict[str, Any]],
    providers: Optional[list[str]],
    source: Optional[str],
    max_priority: Optional[int],
    offset: int,
    limit: Optional[int],
) -> list[CrawlTarget]:
    selected: list[CrawlTarget] = []
    provider_set = {provider.upper() for provider in providers or []}
    for item in items:
        provider = clean_text(item.get("provider")).upper()
        if provider_set and provider not in provider_set:
            continue
        if source and clean_text(item.get("source")) != source:
            continue
        if max_priority is not None and int(item.get("priority") or 9) > max_priority:
            continue
        if not clean_text(
            item.get("url") or item.get("list_url") or item.get("base_url")
        ):
            continue
        selected.append(to_crawl_target(item))

    selected.sort(key=lambda target: (target.priority, target.source, target.provider))
    if offset:
        selected = selected[offset:]
    if limit is not None:
        selected = selected[:limit]
    return selected


def build_registry(items: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[tuple[dict[str, Any], bool, str]]] = defaultdict(list)
    seen_scope_keys: dict[str, str] = {}
    for item in sorted(items, key=target_preference_key):
        provider = validate_provider(item.get("provider"))
        status = clean_text(item.get("crawler_status") or item.get("status")).lower()
        url = target_url(item)
        duplicate_reason = explicit_duplicate_reason(item)
        duplicate_owner = ""
        scope_keys = target_scope_keys(item)
        for scope_key in scope_keys:
            if scope_key in seen_scope_keys:
                duplicate_owner = seen_scope_keys[scope_key]
                break
        enabled = (
            status in WORKING_CRAWLER_STATUSES
            and not url_has_excluded_path(url)
            and not duplicate_reason
            and not duplicate_owner
        )
        disabled_reason = ""
        if duplicate_reason:
            disabled_reason = duplicate_reason
        elif duplicate_owner:
            disabled_reason = f"duplicate_url:{duplicate_owner}"
        elif status in DISABLED_REGISTRY_STATUSES:
            disabled_reason = status
        elif url_has_excluded_path(url):
            disabled_reason = "excluded_url_shape"
        if enabled:
            for scope_key in scope_keys:
                seen_scope_keys.setdefault(scope_key, provider)
        grouped[provider].append((item, enabled, disabled_reason))

    rows: list[dict[str, Any]] = []
    for provider, provider_items in grouped.items():
        executable = [entry for entry in provider_items if entry[1]]
        representative = min(
            executable or provider_items,
            key=lambda entry: target_preference_key(entry[0]),
        )[0]
        status = clean_text(
            representative.get("crawler_status") or representative.get("status")
        ).lower()
        url = target_url(representative)
        crawler = f"Crawler/generated_yaml/{safe_module_name(provider)}.py"
        arguments_override = GENERATED_PROVIDER_ARGUMENT_OVERRIDES.get(provider)
        if arguments_override is None:
            institution_source_group = infer_experience_institution_source_group(
                source_group=representative.get("source_group"),
                name=representative.get("name"),
                branch_name=representative.get("branch"),
                collection_category=representative.get("collection_category"),
                domain_category=representative.get("domain_category"),
            )
            inferred_service_group = infer_service_group(
                provider=representative.get("provider"),
                collection_category=representative.get("collection_category"),
                domain_category=representative.get("domain_category"),
                source_group=representative.get("source_group"),
                operator_type=representative.get("operator_type"),
                branch_name=representative.get("branch"),
                raw_url=target_url(representative),
                service_group=representative.get("service_group"),
            )
            arguments_override = (
                EXPERIENCE_FULL_SNAPSHOT_ARGUMENTS
                if institution_source_group
                or inferred_service_group == SERVICE_GROUP_EXPERIENCE
                else DEFAULT_GENERATED_ARGUMENTS
            )
        arguments = list(arguments_override)
        command = " ".join(("python", "-X", "utf8", crawler, *arguments))
        disabled_reasons = list(
            dict.fromkeys(
                reason
                for _, enabled, reason in provider_items
                if not enabled and reason
            )
        )
        rows.append(
            {
                "provider": provider,
                "name": readable_text(
                    representative.get("name"),
                    representative.get("branch"),
                    provider_code_label(provider, url),
                ),
                "source": clean_text(representative.get("source")),
                "priority": int(representative.get("priority") or 9),
                "url": url,
                "crawler": crawler,
                "command": command,
                "arguments": arguments,
                "status": status,
                "target_status": status,
                "enabled": bool(executable),
                "disabled_reason": ""
                if executable
                else (disabled_reasons[0] if disabled_reasons else status),
                "target_count": len(provider_items),
                "enabled_target_count": len(executable),
                "target_statuses": sorted(
                    {
                        clean_text(
                            entry[0].get("crawler_status") or entry[0].get("status")
                        ).lower()
                        for entry in provider_items
                    }
                ),
            }
        )
    rows.sort(key=lambda row: (row["priority"], row["source"], row["provider"]))
    for index, row in enumerate(rows, start=1):
        row["index"] = index
    rows = [{"index": row.pop("index"), **row} for row in rows]

    by_source: dict[str, int] = {}
    by_priority: dict[int, int] = {}
    by_status: dict[str, int] = {}
    for row in rows:
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
        by_priority[row["priority"]] = by_priority.get(row["priority"], 0) + 1
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    return {
        "version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_file": "config/crawl_targets"
        if TARGET_DIR.exists()
        else "config/collected_yaml_crawl_targets.yaml",
        "summary": {
            "providers": len(rows),
            "source_targets": len(items),
            "enabled_providers": sum(1 for row in rows if row["enabled"]),
            "enabled_targets": sum(int(row["enabled_target_count"]) for row in rows),
            "by_source": by_source,
            "by_priority": dict(sorted(by_priority.items())),
            "by_status": dict(sorted(by_status.items())),
        },
        "targets": rows,
    }


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_registry(path: Path = REGISTRY_FILE) -> Path:
    items = load_registry_targets()
    data = build_registry(items)
    _atomic_write_text(
        path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140)
    )
    return path


@dataclass
class _CollectionResult:
    target: CrawlTarget
    report: ProviderReport
    rows: list[dict[str, Any]]
    stale_cutoff: datetime
    row_cap_reached: bool = False
    page_cap_reached: bool = False
    detail_cap_reached: bool = False
    recursion_cap_reached: bool = False
    collection_complete: bool = False
    persistence_succeeded: bool = False
    collection_attempts: int = 1


def _concrete_provider_result_rows(
    results: list[_CollectionResult],
    *,
    save_db: bool,
) -> list[dict[str, Any]]:
    """Build concrete-provider evidence from committed provider transactions."""
    grouped: dict[str, list[_CollectionResult]] = defaultdict(list)
    for result in results:
        grouped[result.target.provider].append(result)

    rows: list[dict[str, Any]] = []
    for provider, provider_results in sorted(grouped.items()):
        targets_total = len(provider_results)
        targets_succeeded = sum(
            1 for result in provider_results if result.report.success
        )
        persistence_succeeded = bool(
            save_db
            and provider_results
            and all(result.persistence_succeeded for result in provider_results)
        )
        rows.append(
            {
                "provider": provider,
                "success": bool(
                    persistence_succeeded and targets_succeeded == targets_total
                ),
                "targets_total": targets_total,
                "targets_succeeded": targets_succeeded,
                "collected_courses": sum(
                    max(int(result.report.collected or 0), 0)
                    for result in provider_results
                ),
                "saved_courses": sum(
                    max(int(result.report.saved or 0), 0) for result in provider_results
                ),
            }
        )
    return rows


def _write_concrete_provider_result_manifest(
    results: list[_CollectionResult],
    *,
    save_db: bool,
) -> Optional[Path]:
    """Atomically publish concrete-provider persistence evidence for the parent worker."""
    raw_path = os.getenv(CONCRETE_RESULT_MANIFEST_PATH_ENV, "").strip()
    if not raw_path:
        return None
    if len(raw_path) > MAX_URL_LENGTH or any(
        ord(character) < 32 for character in raw_path
    ):
        raise ValueError("concrete provider result manifest path is invalid")

    scheduled_provider = validate_provider(os.getenv(SCHEDULED_PROVIDER_ENV, ""))
    manifest = {
        "version": 1,
        "crawl_batch_id": os.getenv("CRAWL_BATCH_ID", "").strip(),
        "scheduled_provider": scheduled_provider,
        "save_db": bool(save_db),
        "providers": _concrete_provider_result_rows(results, save_db=save_db),
    }
    path = Path(raw_path)
    _atomic_write_text(
        path,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    return path


def _row_identity(row: dict[str, Any]) -> str:
    return "|".join(
        (
            clean_text(row.get("raw_url")),
            clean_text(row.get("title")).casefold(),
            clean_text(row.get("branch")).casefold(),
            clean_text(row.get("period")),
            clean_text(row.get("schedule_raw")),
        )
    )


def normalize_collected_rows(
    rows: Any,
    target: CrawlTarget,
    *,
    maximum_rows: int = MAX_ROWS_PER_TARGET,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise TypeError("Collector rows must be a list")
    normalized: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    dropped = 0
    invalid_urls = 0
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            dropped += 1
            continue
        row = dict(raw_row)
        title = clean_text(row.get("title"))
        if not title:
            dropped += 1
            continue
        row["title"] = title
        row["provider"] = target.provider
        row["branch"] = clean_text(row.get("branch")) or target.branch
        source_raw = row.get("raw_fields")
        if not isinstance(source_raw, dict):
            source_raw = {}
        target_text = clean_text(
            row.get("target")
            or row.get("eligibility_raw")
            or source_raw.get("target")
            or source_raw.get("eligibility")
        )
        if target_text:
            row["target"] = target_text
        else:
            row["target"] = "대상 별도 안내"
            row["target_source_omission"] = True
        fee_values = (
            row.get("fee"),
            row.get("fee_raw"),
            row.get("material_fee"),
            source_raw.get("fee"),
            source_raw.get("fee_raw"),
            source_raw.get("source_fee"),
            source_raw.get("tuition"),
            source_raw.get("price"),
            source_raw.get("material_fee"),
        )
        fee_text = next(
            (
                clean_text(value)
                for value in fee_values
                if value is not None and clean_text(value)
            ),
            "",
        )
        if fee_text:
            row["fee"] = fee_text
        else:
            row["fee"] = "요금 별도 안내"
            row["fee_source_omission"] = True
        period_text = clean_text(
            row.get("period")
            or source_raw.get("period")
            or source_raw.get("event_period")
            or source_raw.get("date")
        )
        if period_text:
            row["period"] = period_text
        elif not any(
            (
                clean_text(row.get("start_date")),
                clean_text(row.get("end_date")),
                row.get("schedule_dates"),
                row.get("calendar_dates"),
                row.get("class_dates"),
                row.get("date_list"),
            )
        ):
            row["period"] = "날짜 별도 안내"
            row["date_source_omission"] = True
        venue_text = clean_text(
            row.get("venue_name")
            or row.get("venue")
            or row.get("place")
            or row.get("room")
            or source_raw.get("venue")
            or source_raw.get("place")
            or source_raw.get("location")
        )
        if venue_text:
            row["venue_name"] = venue_text
        elif not any(
            (
                clean_text(row.get("venue_address")),
                clean_text(row.get("address")),
                clean_text(row.get("place_address")),
            )
        ):
            row["venue_name"] = "장소 별도 안내"
            row["venue_source_omission"] = True
        schedule_text = clean_text(
            row.get("schedule_raw")
            or row.get("schedule")
            or row.get("time")
            or row.get("hours")
            or source_raw.get("schedule")
            or source_raw.get("time")
            or source_raw.get("hours")
        )
        if schedule_text:
            row["schedule_raw"] = schedule_text
        elif not any(
            (
                clean_text(row.get("schedule_time_start")),
                clean_text(row.get("schedule_time_end")),
                row.get("schedule_days"),
                row.get("schedule_dates"),
            )
        ):
            row["schedule_raw"] = "시간 별도 안내"
            row["schedule_source_omission"] = True
        category_text = next(
            (
                clean_text(row.get(field))
                for field in (
                    "category",
                    "category_raw",
                    "domain_category",
                    "collection_category",
                    "program_type",
                    "standard_category_label",
                )
                if clean_text(row.get(field))
            ),
            "",
        ) or clean_text(source_raw.get("category"))
        if category_text:
            row["category"] = category_text
        else:
            row["category"] = (
                clean_text(target.extra.get("domain_category"))
                or clean_text(target.extra.get("collection_category"))
                or "교육·강좌"
            )
            row["category_source_omission"] = True
        for field in (
            "raw_url",
            "application_url",
            "image_url",
            "branch_url",
            "website_url",
        ):
            value = row.get(field)
            if not clean_text(value):
                continue
            try:
                row[field] = normalize_http_url(
                    value,
                    required=True,
                    preserve_safe_fragment=field == "raw_url",
                )
            except ValueError:
                row.pop(field, None)
                invalid_urls += 1
        if not row.get("raw_url"):
            row["raw_url"] = target.url
        invalid_period = False
        for field in ("period", "apply_period"):
            value = row.get(field)
            if not clean_text(value):
                continue
            try:
                start_date, end_date = parse_date_range(value)
            except (TypeError, ValueError, OverflowError):
                start_date, end_date = None, None
            if start_date and end_date and start_date > end_date:
                invalid_period = True
                break
        if invalid_period:
            dropped += 1
            continue
        identity = _row_identity(row)
        if not identity.strip("|") or identity in seen_identities:
            dropped += 1
            continue
        seen_identities.add(identity)
        normalized.append(row)

    collision_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        collision_groups[provider_course_id_from_row(row)].append(row)
    for provider_course_id, group in collision_groups.items():
        if len(group) < 2:
            continue
        for row in group:
            digest = hashlib.sha256(_row_identity(row).encode("utf-8")).hexdigest()[:20]
            prefix = provider_course_id[:78].rstrip(":")
            row["provider_course_id"] = f"{prefix}:{digest}"[:100]

    raw_url_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        raw_url_groups[clean_text(row.get("raw_url"))].append(row)
    for raw_url, group in raw_url_groups.items():
        if not raw_url or len(group) < 2:
            continue
        parsed = urlparse(raw_url)
        for row in group:
            provider_course_id = provider_course_id_from_row(row)
            digest = hashlib.sha256(
                f"{target.provider}|{provider_course_id}".encode("utf-8")
            ).hexdigest()[:20]
            row["raw_url"] = urlunparse(
                parsed._replace(fragment=f"mooncen-item-{digest}")
            )
            row["shared_list_url_source"] = raw_url

    if len(normalized) > maximum_rows:
        dropped += len(normalized) - maximum_rows
        normalized = normalized[:maximum_rows]
    if dropped or invalid_urls:
        logger.warning(
            "Normalized generated rows provider=%s kept=%s dropped=%s invalid_urls=%s",
            target.provider,
            len(normalized),
            dropped,
            invalid_urls,
        )
    return normalized


@contextmanager
def _transaction_cursor(connection: Any, dict_cursor: bool = True) -> Iterator[Any]:
    cursor = connection.cursor(cursor_factory=RealDictCursor if dict_cursor else None)
    try:
        yield cursor
    finally:
        cursor.close()


def _persist_collection_results(
    results: list[_CollectionResult],
    *,
    mark_stale: bool,
    max_pages: int,
    per_target_limit: int,
    complete_providers: set[str],
    allow_partial_save: bool = False,
) -> None:
    grouped: dict[str, list[_CollectionResult]] = defaultdict(list)
    for result in results:
        result.persistence_succeeded = False
        grouped[result.target.provider].append(result)

    for provider, provider_results in grouped.items():
        if any(not result.report.success for result in provider_results):
            logger.warning(
                "Skipping generated YAML persistence for provider=%s because at least one sibling target failed.",
                provider,
            )
            continue
        provider_complete = all(
            result.collection_complete
            and not result.row_cap_reached
            and not result.page_cap_reached
            and not result.detail_cap_reached
            and not result.recursion_cap_reached
            for result in provider_results
        )
        partial_save_allowed = allow_partial_save and per_target_limit > 0
        if not provider_complete and not partial_save_allowed:
            for result in provider_results:
                if not result.collection_complete or any(
                    (
                        result.row_cap_reached,
                        result.page_cap_reached,
                        result.detail_cap_reached,
                        result.recursion_cap_reached,
                    )
                ):
                    result.report.success = False
                    result.report.error = result.report.error or (
                        "Incomplete collection was not persisted; use an explicit bounded "
                        "--allow-partial-save sample or complete the crawl."
                    )
            logger.warning(
                "Skipping incomplete generated YAML persistence for provider=%s partial_opt_in=%s limit=%s.",
                provider,
                allow_partial_save,
                per_target_limit,
            )
            continue
        should_mark_stale = (
            mark_stale
            and provider in complete_providers
            and per_target_limit == 0
            and provider_complete
        )
        if (
            not any(result.rows for result in provider_results)
            and not should_mark_stale
        ):
            for result in provider_results:
                result.persistence_succeeded = True
            continue
        with _DB_WRITE_LOCK:
            connection = None
            original_municipal_cursor = municipal_yaml_module.get_db_cursor
            original_lifecycle_cursor = course_lifecycle_module.get_db_cursor
            try:
                connection = get_db_connection()

                @contextmanager
                def shared_cursor(dict_cursor: bool = True) -> Iterator[Any]:
                    with _transaction_cursor(
                        connection, dict_cursor=dict_cursor
                    ) as cursor:
                        yield cursor

                municipal_yaml_module.get_db_cursor = shared_cursor
                course_lifecycle_module.get_db_cursor = shared_cursor
                writer = MunicipalDbWriter(provider)
                for result in provider_results:
                    if result.rows:
                        result.report.saved = writer.save_rows(result.rows)
                if should_mark_stale:
                    for result in provider_results:
                        source_endpoint = canonical_source_endpoint(result.target.url)
                        if not source_endpoint:
                            raise ValueError(
                                f"missing source endpoint for provider={provider}"
                            )
                        mark_stale_courses(
                            provider,
                            result.stale_cutoff,
                            source_endpoint=source_endpoint,
                        )
                connection.commit()
                for result in provider_results:
                    result.persistence_succeeded = True
            except Exception as exc:
                if connection is not None:
                    connection.rollback()
                error = redact_sensitive_text(f"{type(exc).__name__}: {exc}")
                for result in provider_results:
                    result.report.saved = 0
                    result.report.success = False
                    result.report.error = error
                    result.persistence_succeeded = False
                logger.error(
                    "Generated YAML DB transaction failed provider=%s error=%s",
                    provider,
                    error,
                    exc_info=True,
                )
            finally:
                municipal_yaml_module.get_db_cursor = original_municipal_cursor
                course_lifecycle_module.get_db_cursor = original_lifecycle_cursor
                if connection is not None:
                    connection.close()


def _collect_single_target(
    target: CrawlTarget,
    per_target_limit: int,
    max_depth: int,
    max_pages: int,
    detail_limit: int,
    timeout: int,
) -> _CollectionResult:
    report = ProviderReport(
        provider=target.provider, name=target.name, url=safe_log_url(target.url)
    )
    started_at = time.perf_counter()
    stale_cutoff = utc_now()
    rows: list[dict[str, Any]] = []
    row_cap_reached = False
    page_cap_reached = False
    detail_cap_reached = False
    recursion_cap_reached = False
    collection_complete = False
    try:
        collect_target = target
        if per_target_limit > 0:
            collect_target = CrawlTarget(
                provider=target.provider,
                name=target.name,
                branch=target.branch,
                url=target.url,
                source=target.source,
                priority=target.priority,
                region=target.region,
                extra={**target.extra, "per_target_limit": per_target_limit},
            )
        logical_request_limit = (
            max(1, max_pages) + max(0, detail_limit) + max(0, max_depth) * 5 + 10
        )
        request_limit = min(
            MAX_REQUESTS_PER_TARGET,
            logical_request_limit * REQUEST_HOPS_PER_LOGICAL_REQUEST,
        )
        with outbound_request_budget(request_limit):
            rows, parser, meta = collect_from_url(
                collect_target,
                timeout=timeout,
                max_depth=max_depth,
                max_pages=max_pages,
                detail_limit=detail_limit,
            )
        row_cap_reached = isinstance(rows, list) and len(rows) > MAX_ROWS_PER_TARGET
        rows = normalize_collected_rows(rows, target)
        if per_target_limit > 0:
            row_cap_reached = row_cap_reached or len(rows) > per_target_limit
            rows = rows[:per_target_limit]
        apply_target_metadata(rows, target)
        branch_normalizer = MunicipalDbWriter(target.provider)
        for row in rows:
            branch_normalizer.normalize_branch_split_row(row)
        report.parser = parser
        report.collected = len(rows)
        report.pages = int(meta.get("pages") or 0)
        report.detail_pages = int(meta.get("detail_pages") or 0)
        report.discovered_links = int(meta.get("discovered_links") or 0)
        report.reservation_discovery_links = int(
            meta.get("reservation_discovery_links") or 0
        )
        report.reservation_fallback_pages = int(
            meta.get("reservation_fallback_pages") or 0
        )
        report.main_discovery_pages = int(meta.get("main_discovery_pages") or 0)
        report.main_discovered_links = int(meta.get("main_discovered_links") or 0)
        report.main_candidate_pages = int(meta.get("main_candidate_pages") or 0)
        report.main_discovery_complete = bool(meta.get("main_discovery_complete"))
        report.configured_collection_error = clean_text(
            meta.get("configured_collection_error")
        )
        if report.configured_collection_error:
            report.error = redact_sensitive_text(report.configured_collection_error)
        report.main_discovery_error = clean_text(meta.get("main_discovery_error"))
        report.pagination_detected = bool(meta.get("pagination_detected"))
        report.recursion_depth = int(meta.get("recursion_depth") or 0)
        report.no_current_data = bool(meta.get("no_current_data"))
        report.no_current_reason = clean_text(meta.get("no_current_reason"))
        report.fields = score_fields(rows)
        report.samples = sample_rows(rows)
        pagination_complete = bool(
            meta.get("pagination_complete")
            or meta.get("pagination_exhausted")
            or meta.get("no_more_pages")
            or meta.get("full_snapshot_validated")
            or not report.pagination_detected
        )
        fanout_complete = not bool(
            meta.get("fanout_cap_reached") or meta.get("source_cap_reached")
        )
        detail_complete = bool(
            meta.get("detail_collection_complete")
            or meta.get("detail_enrichment_complete")
            or meta.get("snapshot_complete")
        )
        page_cap_reached = bool(meta.get("page_cap_reached")) or (
            report.pages >= max_pages and not pagination_complete
        )
        detail_cap_reached = bool(meta.get("detail_cap_reached")) or (
            detail_limit > 0
            and report.detail_pages >= detail_limit
            and not detail_complete
        )
        recursion_cap_reached = bool(meta.get("recursion_cap_reached"))
        collection_complete = (
            (bool(rows) or report.no_current_data)
            and pagination_complete
            and fanout_complete
            and not report.configured_collection_error
            and not row_cap_reached
            and not page_cap_reached
            and not detail_cap_reached
            and not recursion_cap_reached
        )
        if report.pages > max_pages:
            raise ValueError(
                f"collector exceeded max_pages ({report.pages}>{max_pages})"
            )
        if report.detail_pages > detail_limit:
            raise ValueError(
                f"collector exceeded detail_limit ({report.detail_pages}>{detail_limit})"
            )
        if report.recursion_depth > max_depth:
            raise ValueError(
                f"collector exceeded max_depth ({report.recursion_depth}>{max_depth})"
            )
        report.success = bool(rows) or report.no_current_data
    except Exception as exc:
        rows = []
        collection_complete = False
        report.error = redact_sensitive_text(f"{type(exc).__name__}: {exc}")
        logger.error(
            "Generated YAML target failed provider=%s name=%s url=%s error=%s",
            target.provider,
            target.name,
            safe_log_url(target.url),
            report.error,
            exc_info=True,
        )
    finally:
        report.duration_seconds = round(time.perf_counter() - started_at, 3)
    return _CollectionResult(
        target=target,
        report=report,
        rows=rows,
        stale_cutoff=stale_cutoff,
        row_cap_reached=row_cap_reached,
        page_cap_reached=page_cap_reached,
        detail_cap_reached=detail_cap_reached,
        recursion_cap_reached=recursion_cap_reached,
        collection_complete=collection_complete,
    )


def _retryable_zero_row_transport_marker(result: _CollectionResult) -> str:
    """Return a transport marker only for an atomic, fail-closed zero-row result."""

    if (
        result.rows
        or result.report.success
        or result.report.no_current_data
        or result.collection_complete
        or result.row_cap_reached
        or result.page_cap_reached
        or result.detail_cap_reached
        or result.recursion_cap_reached
    ):
        return ""
    error_text = " ".join(
        text
        for text in (
            clean_text(result.report.configured_collection_error),
            clean_text(result.report.error),
        )
        if text
    )
    if not error_text or _NON_RETRYABLE_CONTRACT_ERROR_PATTERN.search(error_text):
        return ""
    match = _RETRYABLE_TRANSPORT_ERROR_PATTERN.search(error_text)
    return clean_text(match.group(0)) if match else ""


def _retry_transient_zero_row_results(
    indexed_results: dict[int, _CollectionResult],
    *,
    per_target_limit: int,
    max_depth: int,
    max_pages: int,
    detail_limit: int,
    timeout: int,
    parallel_workers: int,
) -> None:
    """Retry only failed transport snapshots once, before any DB transaction starts.

    ``collect_from_url`` closes every session created during one top-level target
    crawl. Calling ``_collect_single_target`` again therefore creates a fresh
    session scope. A new executor also prevents reuse of an initial worker's
    context when this function is called by an aggregate crawler.
    """

    retryable = [
        (index, result, marker)
        for index, result in sorted(indexed_results.items())
        if (marker := _retryable_zero_row_transport_marker(result))
    ]
    if not retryable:
        return

    logger.warning(
        "Retrying transient zero-row target collections once. targets=%s backoff=%ss",
        len(retryable),
        TRANSIENT_ZERO_ROW_RETRY_BACKOFF_SECONDS,
    )
    time.sleep(TRANSIENT_ZERO_ROW_RETRY_BACKOFF_SECONDS)
    retry_workers = min(max(1, parallel_workers), len(retryable))
    with ThreadPoolExecutor(max_workers=retry_workers) as executor:
        futures = {
            executor.submit(
                _collect_single_target,
                result.target,
                per_target_limit,
                max_depth,
                max_pages,
                detail_limit,
                timeout,
            ): (index, result, marker)
            for index, result, marker in retryable
        }
        for future in as_completed(futures):
            index, original, marker = futures[future]
            try:
                retried = future.result()
            except Exception as exc:
                logger.error(
                    "Transient target retry failed unexpectedly. provider=%s error_type=%s",
                    original.target.provider,
                    type(exc).__name__,
                    exc_info=True,
                )
                continue
            retried.collection_attempts = original.collection_attempts + 1
            retried.report.duration_seconds = round(
                original.report.duration_seconds + retried.report.duration_seconds,
                3,
            )
            indexed_results[index] = retried
            logger.log(
                logging.INFO if retried.report.success else logging.WARNING,
                "Transient target retry completed. provider=%s recovered=%s marker=%s",
                retried.target.provider,
                bool(retried.report.success),
                marker,
            )


def run_single_target(
    target: CrawlTarget,
    per_target_limit: int,
    save_db: bool,
    mark_stale: bool,
    max_depth: int,
    max_pages: int,
    detail_limit: int,
    timeout: int,
    allow_partial_save: bool = False,
) -> ProviderReport:
    result = _collect_single_target(
        target, per_target_limit, max_depth, max_pages, detail_limit, timeout
    )
    if save_db:
        _persist_collection_results(
            [result],
            mark_stale=mark_stale,
            max_pages=max_pages,
            per_target_limit=per_target_limit,
            complete_providers=set(),
            allow_partial_save=allow_partial_save,
        )
    return result.report


def run_targets(
    targets: list[CrawlTarget],
    per_target_limit: int,
    save_db: bool,
    mark_stale: bool,
    max_depth: int,
    max_pages: int,
    detail_limit: int,
    timeout: int,
    parallel_workers: int = 1,
    complete_providers: Optional[set[str]] = None,
    allow_partial_save: bool = False,
) -> list[ProviderReport]:
    complete_providers = complete_providers or set()
    indexed_results: dict[int, _CollectionResult] = {}
    if parallel_workers > 1 and len(targets) > 1:
        with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
            futures = {
                executor.submit(
                    _collect_single_target,
                    target,
                    per_target_limit,
                    max_depth,
                    max_pages,
                    detail_limit,
                    timeout,
                ): (index, target)
                for index, target in enumerate(targets, start=1)
            }
            for future in as_completed(futures):
                index, target = futures[future]
                try:
                    indexed_results[index] = future.result()
                except Exception as exc:
                    report = ProviderReport(
                        provider=target.provider,
                        name=target.name,
                        url=safe_log_url(target.url),
                        error=redact_sensitive_text(f"{type(exc).__name__}: {exc}"),
                    )
                    indexed_results[index] = _CollectionResult(
                        target=target,
                        report=report,
                        rows=[],
                        stale_cutoff=utc_now(),
                    )
    else:
        for index, target in enumerate(targets, start=1):
            indexed_results[index] = _collect_single_target(
                target,
                per_target_limit=per_target_limit,
                max_depth=max_depth,
                max_pages=max_pages,
                detail_limit=detail_limit,
                timeout=timeout,
            )

    _retry_transient_zero_row_results(
        indexed_results,
        per_target_limit=per_target_limit,
        max_depth=max_depth,
        max_pages=max_pages,
        detail_limit=detail_limit,
        timeout=timeout,
        parallel_workers=parallel_workers,
    )

    ordered_results = [indexed_results[index] for index in sorted(indexed_results)]
    if save_db:
        _persist_collection_results(
            ordered_results,
            mark_stale=mark_stale,
            max_pages=max_pages,
            per_target_limit=per_target_limit,
            complete_providers=complete_providers,
            allow_partial_save=allow_partial_save,
        )
    _write_concrete_provider_result_manifest(
        ordered_results,
        save_db=save_db,
    )
    for index, result in enumerate(ordered_results, start=1):
        report = result.report
        logger.info(
            "[%s/%s] %s collected=%s saved=%s duration=%ss parser=%s error=%s",
            index,
            len(ordered_results),
            result.target.provider,
            report.collected,
            report.saved,
            report.duration_seconds,
            report.parser,
            report.error,
        )
    return [result.report for result in ordered_results]


def bounded_int(label: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{label} must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"{label} must be between {minimum} and {maximum}"
            )
        return parsed

    return parse


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generated crawler runner for every provider in collected_yaml_crawl_targets.yaml"
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--provider", action="append", help="Provider to run. Can be repeated."
    )
    selection.add_argument(
        "--all", action="store_true", help="Run all selected generated YAML providers"
    )
    parser.add_argument("--source", help="Filter by YAML source name")
    parser.add_argument(
        "--max-priority", type=bounded_int("max-priority", 1, 9), default=None
    )
    parser.add_argument("--offset", type=bounded_int("offset", 0, 1_000_000), default=0)
    parser.add_argument(
        "--target-limit",
        type=bounded_int("target-limit", 1, MAX_TARGETS_PER_RUN),
        default=None,
    )
    parser.add_argument(
        "--per-target-limit",
        type=bounded_int("per-target-limit", 0, MAX_ROWS_PER_TARGET),
        default=50,
    )
    parser.add_argument(
        "--limit",
        type=bounded_int("limit", 0, MAX_ROWS_PER_TARGET),
        default=None,
        help="Alias for --per-target-limit",
    )
    persistence = parser.add_mutually_exclusive_group()
    persistence.add_argument(
        "--save-db",
        action="store_true",
        help="Persist validated rows in one transaction per provider",
    )
    persistence.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly disable every database write (the default)",
    )
    parser.add_argument(
        "--allow-partial-save",
        action="store_true",
        help="Allow an explicitly bounded sample to be upserted without stale cleanup",
    )
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument(
        "--max-depth", type=bounded_int("max-depth", 0, MAX_RECURSION_DEPTH), default=1
    )
    parser.add_argument(
        "--max-pages", type=bounded_int("max-pages", 1, MAX_PAGES), default=20
    )
    parser.add_argument(
        "--detail-limit",
        type=bounded_int("detail-limit", 0, MAX_DETAIL_PAGES),
        default=30,
    )
    parser.add_argument(
        "--timeout",
        type=bounded_int("timeout", 1, MAX_REQUEST_TIMEOUT_SECONDS),
        default=20,
    )
    parser.add_argument(
        "--parallel-workers",
        type=bounded_int("parallel-workers", 1, MAX_PARALLEL_WORKERS),
        default=1,
        help="Run bounded YAML target collection concurrently; database writes remain serialized",
    )
    parser.add_argument("--write-registry", action="store_true")
    parser.add_argument(
        "--include-status",
        action="append",
        default=[],
        choices=sorted(REGISTRY_CRAWLER_STATUSES | RECHECKABLE_CRAWLER_STATUSES),
        help="Also include targets with this crawler_status/status. Can be repeated, e.g. --include-status needs_parser.",
    )
    args = parser.parse_args(argv)
    effective_limit = args.limit if args.limit is not None else args.per_target_limit
    if args.mark_stale and not args.save_db:
        parser.error("--mark-stale requires --save-db")
    if args.mark_stale and effective_limit != 0:
        parser.error(
            "--mark-stale requires --per-target-limit 0 so a sample cannot deactivate unseen rows"
        )
    if args.allow_partial_save and effective_limit == 0:
        parser.error("--allow-partial-save requires a positive --per-target-limit")
    if args.save_db and effective_limit > 0 and not args.allow_partial_save:
        parser.error("bounded database writes require explicit --allow-partial-save")
    disabled_includes = set(args.include_status) - (
        WORKING_CRAWLER_STATUSES | RECHECKABLE_CRAWLER_STATUSES
    )
    if args.save_db and disabled_includes:
        parser.error("disabled/unfinished statuses may only be included in a dry-run")
    if args.provider:
        try:
            args.provider = list(
                dict.fromkeys(validate_provider(provider) for provider in args.provider)
            )
        except ValueError as exc:
            parser.error(str(exc))
    return args


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    dedicated_provider: str = "",
) -> int:
    args = parse_args(argv)
    if dedicated_provider:
        dedicated_provider = validate_provider(dedicated_provider)
        if dedicated_provider not in DEDICATED_PROVIDER_NAMES:
            raise ValueError(
                f"Provider is not registered as dedicated: {dedicated_provider}"
            )
        if args.all or args.provider != [dedicated_provider]:
            raise ValueError(
                "A dedicated-provider run must select exactly its fixed provider"
            )
    if args.write_registry:
        path = write_registry()
        print(f"registry={path}")

    items = load_yaml_targets(
        extra_statuses={clean_text(status).lower() for status in args.include_status},
        dedicated_provider=dedicated_provider,
    )
    targets = select_targets(
        items,
        providers=args.provider,
        source=args.source,
        max_priority=args.max_priority,
        offset=args.offset,
        limit=args.target_limit,
    )

    if not args.all and not args.provider:
        print("No providers selected. Use --provider PROVIDER or --all.")
        print(f"available_targets={len(items)}")
        return 0 if args.write_registry else 2

    requested_providers = set(args.provider or [])
    selected_providers = {target.provider for target in targets}
    missing_providers = sorted(requested_providers - selected_providers)
    if missing_providers:
        print(
            f"Unknown or disabled generated YAML providers: {', '.join(missing_providers)}",
            file=sys.stderr,
        )
        return 2
    if not targets:
        print(
            "No generated YAML targets matched the supplied filters.", file=sys.stderr
        )
        return 2

    all_targets = select_targets(
        items,
        providers=list(selected_providers),
        source=None,
        max_priority=None,
        offset=0,
        limit=None,
    )
    selected_counts = Counter(target.provider for target in targets)
    all_counts = Counter(target.provider for target in all_targets)
    complete_providers = {
        provider
        for provider in selected_providers
        if selected_counts[provider] == all_counts[provider]
        and all(
            clean_text(
                target.extra.get("crawler_status") or target.extra.get("status")
            ).lower()
            == "ready"
            for target in targets
            if target.provider == provider
        )
    }
    effective_detail_limit = max(1, args.detail_limit)
    effective_per_target_limit = (
        args.limit if args.limit is not None else args.per_target_limit
    )

    reports = run_targets(
        targets,
        per_target_limit=effective_per_target_limit,
        save_db=args.save_db,
        mark_stale=args.mark_stale,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        detail_limit=effective_detail_limit,
        timeout=args.timeout,
        parallel_workers=args.parallel_workers,
        complete_providers=complete_providers,
        allow_partial_save=args.allow_partial_save,
    )
    print_table(reports)
    report_path = write_report(reports)
    print(f"\nreport={report_path}")
    return 0 if reports and all(report.success for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
