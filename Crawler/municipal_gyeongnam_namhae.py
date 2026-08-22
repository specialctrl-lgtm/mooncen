"""Fail-closed collector for Namhae-gun's complete education catalogue.

The configured ``pageCd=RE0105000000`` URL looks like one facility page, but
without an ``splace`` parameter it is the official unfiltered catalogue for
every education facility in the Namhae integrated-reservation service.  The
portal root is therefore a discovery alias, not a second course source.

Every advertised page is reconciled with the declared total.  Namhae clamps
an immediate post-last request to its final page, so the clamp is accepted
only when its displayed/form page and exact row signature match the real last
page.  The first and last pages are then re-read.  Only current/future records
receive detail requests, and application-form pages are never fetched.

Detail output is an explicit public-summary allowlist.  The contact row,
attachments, preparation/free-text content, applicant data and application
form fields are deliberately excluded from returned rows.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


NAMHAE_HOST = "www.namhae.go.kr"
NAMHAE_LIST_PATH = "/modules/life/edu.do"
NAMHAE_PAGE_CD = "RE0105000000"
NAMHAE_PROVIDER = "MUNI_WWW_NAMHAE_GO_KR_0ABE95E1"
NAMHAE_MUNICIPALITY_CODE = "4884000000"
NAMHAE_MUNICIPALITY_NAME = "경상남도 남해군"
NAMHAE_PAGE_SIZE = 10
NAMHAE_MAX_WORKERS = 4
NAMHAE_FETCH_ATTEMPTS = 2
NAMHAE_URL = (
    f"https://{NAMHAE_HOST}{NAMHAE_LIST_PATH}?"
    + urlencode((("pageCd", NAMHAE_PAGE_CD),))
)
NAMHAE_PARSER = (
    "gyeongnam_namhae_complete_unfiltered_catalogue+declared_total+"
    "clamped_last_sentinel+stable_boundaries+current_detail_summary_only+"
    "application_control_no_form_fetch+pii_allowlist"
)
NAMHAE_OWNERSHIP_SCOPE = (
    "official_namhae_integrated_reservation_all_education_facilities"
)

NAMHAE_CANDIDATE_IDS: Mapping[str, str] = {
    "canonical_complete_catalogue": "MUNI_IR_13A401B839CA",
    "rejected_wikipedia": "MUNI_IR_896653A02C78",
}
NAMHAE_CANDIDATE_DECISIONS: Mapping[str, str] = {
    "MUNI_IR_13A401B839CA": "schedule_existing_as_complete_unfiltered_catalogue",
    "MUNI_IR_896653A02C78": "reject_low_value_unverified_wikipedia",
}


@dataclass(frozen=True)
class NamhaeAlias:
    provider: str
    url: str
    relationship: str


NAMHAE_ALIASES = (
    NamhaeAlias(
        "MUNI_WWW_NAMHAE_GO_KR_53DE81FD",
        "https://www.namhae.go.kr/reserve/Index.do",
        "integrated-reservation discovery shell; its education links are "
        "partitions of the canonical unfiltered catalogue",
    ),
)

# These existing provincial records are independent sources for other
# municipalities.  They are documentation only and are intentionally neither
# aliases nor fan-in inputs for this municipal collector.
NAMHAE_SEPARATE_PROVINCIAL_PROVIDERS = (
    "MUNI_WWW_GNDAMOA_OR_KR_8127C6EE",
    "MUNI_WWW_GNDAMOA_OR_KR_CBAEF94B",
)

Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_TOTAL_RE = re.compile(
    r"총\s*([\d,]+)\s*건의\s*교육이\s*있습니다\.\s*"
    r"\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)"
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.-](\d{2})[.-](\d{2})(?!\d)")
_TITLE_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4}|0\d{8,11})(?!\d)"
)
_LOCAL_PHONE_RE = re.compile(r"(?<!\d)\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MASKED_NAME_RE = re.compile(r"(?<![가-힣])[가-힣][OoＯ○*]{2}(?![가-힣])")
_CANCELLED_RE = re.compile(r"(?:^|[<\[(])\s*(?:폐강|취소)\s*(?:$|[>\])])")

_LIST_LABELS = (
    "교육기간",
    "모집기간",
    "모집인원",
    "접수방법",
    "교육시간",
    "모집대상",
    "선정방식",
)
_CURRENT_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "신청하기": "OPEN",
    "대기자신청": "OPEN",
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
}
_ACTIVE_CONTROLS = {"신청하기", "대기자신청"}
_DETAIL_REQUIRED = {
    "교육기간",
    "교육시간",
    "수강료",
    "접수기간",
    "모집대상",
    "모집인원",
    "접수방법",
    "이용문의",
    "선정방식",
}
_DETAIL_ALLOWED = _DETAIL_REQUIRED | {"준비물", "모집지역", "교육장소"}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def is_gyeongnam_namhae_education_target(target: Any) -> bool:
    """Match only the existing provider's exact unfiltered catalogue URL."""

    parsed = urlparse(_target_url(target))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        _provider(target) == NAMHAE_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == NAMHAE_HOST
        and parsed.port is None
        and parsed.path == NAMHAE_LIST_PATH
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
        and set(query) == {"pageCd"}
        and query.get("pageCd") == [NAMHAE_PAGE_CD]
    )


is_target = is_gyeongnam_namhae_education_target


def is_gyeongnam_namhae_alias_target(target: Any) -> bool:
    provider = _provider(target)
    url = _target_url(target)
    return any(provider == alias.provider and url == alias.url for alias in NAMHAE_ALIASES)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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


def namhae_list_url(page: int = 1) -> str:
    if page < 1:
        return ""
    values: list[tuple[str, str]] = [("pageCd", NAMHAE_PAGE_CD)]
    if page > 1:
        values.append(("cpage", str(page)))
    return f"https://{NAMHAE_HOST}{NAMHAE_LIST_PATH}?{urlencode(values)}"


def namhae_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return (
        f"https://{NAMHAE_HOST}{NAMHAE_LIST_PATH}?"
        + urlencode(
            (("amode", "view"), ("idx", value), ("pageCd", NAMHAE_PAGE_CD))
        )
    )


def namhae_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return (
        f"https://{NAMHAE_HOST}{NAMHAE_LIST_PATH}?"
        + urlencode(
            (("amode", "ins"), ("lecIdx", value), ("pageCd", NAMHAE_PAGE_CD))
        )
    )


def _response_soup(response: Any) -> tuple[BeautifulSoup, str]:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    parsed = urlparse(final_url)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != NAMHAE_HOST
        or parsed.port is not None
        or parsed.path != NAMHAE_LIST_PATH
        or parsed.username
        or parsed.password
    ):
        raise ValueError("source response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    return BeautifulSoup(content, "lxml"), final_url


def _request_soup(
    current: Any,
    url: str,
    *,
    timeout: int,
    fetcher: Optional[Fetcher] = None,
) -> tuple[BeautifulSoup, str]:
    messages: list[str] = []
    for attempt in range(1, NAMHAE_FETCH_ATTEMPTS + 1):
        try:
            if fetcher is not None:
                result = fetcher(current, "GET", url, timeout=timeout, data={})
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and isinstance(result[0], BeautifulSoup)
                ):
                    soup, final_url = result
                    parsed = urlparse(_clean(final_url or url))
                    if (
                        parsed.scheme.lower() != "https"
                        or (parsed.hostname or "").rstrip(".").lower()
                        != NAMHAE_HOST
                        or parsed.path != NAMHAE_LIST_PATH
                        or parsed.port is not None
                    ):
                        raise ValueError("source response URL changed")
                    return soup, _clean(final_url or url)
                if isinstance(result, BeautifulSoup):
                    return result, url
                if isinstance(result, (str, bytes, bytearray)):
                    if not result:
                        raise ValueError("empty HTML response")
                    return BeautifulSoup(result, "lxml"), url
                return _response_soup(result)
            return _response_soup(current.get(url, timeout=timeout))
        except Exception as exc:
            messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
    raise ValueError("; ".join(messages))


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError:
            return []
    return result


def _selected_value(select: Any) -> str:
    selected = select.select_one("option[selected]")
    if selected is not None:
        return _clean(selected.get("value"))
    first = select.select_one("option")
    return _clean(first.get("value")) if first is not None else ""


def _form_and_total(
    soup: BeautifulSoup, *, expected_display_page: int
) -> tuple[int, int, list[str]]:
    errors: list[str] = []
    forms = soup.select("form#frmLecture[name='frmLecture']")
    if len(forms) != 1:
        return 0, 0, ["expected one unfiltered frmLecture"]
    form = forms[0]
    action = urlparse(urljoin(f"https://{NAMHAE_HOST}/", _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "get"
        or action.path != NAMHAE_LIST_PATH
        or parse_qs(action.query, keep_blank_values=True).get("pageCd")
        != [NAMHAE_PAGE_CD]
    ):
        errors.append("unexpected frmLecture method/action")
    expected_fields = {
        "amode": "",
        "_url": "?",
        "cpage": str(expected_display_page),
        "pageCd": NAMHAE_PAGE_CD,
        "siteGubun": "",
        "facCode": "",
        "orderGb": "",
        "sstring": "",
    }
    for key, expected in expected_fields.items():
        fields = form.select(f"input[name='{key}']")
        if len(fields) != 1 or _clean(fields[0].get("value")) != expected:
            errors.append(f"frmLecture {key} mismatch")
    for name in ("starget", "scategory", "splace"):
        fields = form.select(f"select[name='{name}']")
        if len(fields) != 1 or _selected_value(fields[0]):
            errors.append(f"frmLecture {name} is not the unfiltered scope")
    stype = form.select("select[name='stype']")
    if len(stype) != 1 or _selected_value(stype[0]) != "title":
        errors.append("frmLecture stype mismatch")
    if not form.select("select[name='splace'] option[value]:not([value=''])"):
        errors.append("frmLecture facility vocabulary is empty")

    totals = []
    for node in soup.select("div.info1"):
        match = _TOTAL_RE.fullmatch(_clean(node.get_text(" ", strip=True)))
        if match:
            totals.append(match)
    if len(totals) != 1:
        return 0, 0, [*errors, "expected one declared education total"]
    total, displayed, last = (
        int(value.replace(",", "")) for value in totals[0].groups()
    )
    if displayed != expected_display_page:
        errors.append("declared current page mismatch")
    expected_last = max(1, math.ceil(total / NAMHAE_PAGE_SIZE))
    if last != expected_last:
        errors.append("declared last page does not match total/page size")
    return total, last, errors


def _recognized_status(labels: Iterable[str]) -> str:
    for label in labels:
        if label in _CURRENT_STATUS_MAP:
            return label
    return ""


def _parse_list_page(
    soup: BeautifulSoup, *, source_page: int
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    cards = soup.select("div.list1f1t2b2 > ul.lst1 > li.li1")
    all_anchors = soup.select("div#body_content a.col.a1[href*='amode=view']")
    if len(cards) != len(all_anchors):
        errors.append("course cards are outside the canonical list scope")
    for index, card in enumerate(cards, 1):
        item_errors: list[str] = []
        anchors = card.select("a.col.a1[href]")
        anchor = anchors[0] if len(anchors) == 1 else None
        if anchor is None:
            item_errors.append("expected one course detail anchor")
            query: dict[str, list[str]] = {}
        else:
            parsed = urlparse(urljoin(NAMHAE_URL, _clean(anchor.get("href"))))
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (
                parsed.path != NAMHAE_LIST_PATH
                or query.get("amode") != ["view"]
                or query.get("pageCd") != [NAMHAE_PAGE_CD]
            ):
                item_errors.append("malformed course detail route")
        identity = (query.get("idx") or [""])[0]
        if not _IDENTITY_RE.fullmatch(identity):
            item_errors.append("missing source identity")

        title_node = anchor.select_one("span.texts > strong.t1") if anchor else None
        displayed_title = _clean(
            title_node.get_text(" ", strip=True) if title_node is not None else ""
        )
        title_match = _TITLE_RE.fullmatch(displayed_title)
        if not title_match:
            item_errors.append("title does not expose a facility prefix")
            facility = title = ""
        else:
            facility, title = (_clean(value) for value in title_match.groups())
            if not facility or not title:
                item_errors.append("facility or title is empty")

        definitions = anchor.select("span.texts > span.t2") if anchor else []
        fields: dict[str, str] = {}
        for node in definitions:
            text = _clean(node.get_text(" ", strip=True))
            label, separator, value = text.partition(":")
            key = _clean(label)
            if not separator or not key or key in fields:
                item_errors.append("duplicate or malformed list field")
                continue
            fields[key] = _clean(value)
        if set(fields) != set(_LIST_LABELS):
            item_errors.append("list field vocabulary changed")
        period_dates = _dates(fields.get("교육기간"))
        apply_dates = _dates(fields.get("모집기간"))
        if len(period_dates) != 2:
            item_errors.append("education period is malformed")
        if len(apply_dates) != 2:
            item_errors.append("application period is malformed")

        labels = [_clean(node.get_text(" ", strip=True)) for node in card.select("div.btns a")]
        source_status = _recognized_status(labels)
        if item_errors:
            errors.extend(
                f"page {source_page} row {index}: {message}"
                for message in item_errors
            )
            continue
        rows.append(
            {
                "provider": NAMHAE_PROVIDER,
                "provider_course_id": f"{NAMHAE_PROVIDER}:education:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": facility,
                "branch_code": (
                    "gyeongnam-namhae-"
                    + hashlib.sha256(facility.encode("utf-8")).hexdigest()[:12]
                ),
                "municipality_code": NAMHAE_MUNICIPALITY_CODE,
                "municipality_name": NAMHAE_MUNICIPALITY_NAME,
                "sido": "경상남도",
                "sigungu": "남해군",
                "provider_organizer": facility,
                "venue_name": facility,
                "category": "평생학습",
                "program_type": "강좌",
                "raw_url": namhae_detail_url(identity),
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": _CURRENT_STATUS_MAP.get(source_status, "ARCHIVED"),
                "period": fields["교육기간"],
                "start_date": period_dates[0].isoformat(),
                "end_date": period_dates[1].isoformat(),
                "apply_period": fields["모집기간"],
                "apply_start": apply_dates[0].isoformat(),
                "apply_end": apply_dates[1].isoformat(),
                "schedule_raw": fields["교육시간"],
                "fee": "",
                "target": fields["모집대상"],
                "description": title,
                "source_group": "lifelong_learning",
                "collection_category": "평생학습",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_html_pages+current_detail_summary",
                "raw_fields": {
                    "parser": NAMHAE_PARSER,
                    "source_catalog": "namhae_integrated_reservation_education",
                    "source_education_id": identity,
                    "source_page": source_page,
                    "source_facility": facility,
                    "source_status": source_status,
                    "source_control_labels": labels,
                    "source_application_method": fields["접수방법"],
                    "source_selection_method": fields["선정방식"],
                },
            }
        )
    return rows, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("branch")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _detail_pairs(root: Any) -> tuple[dict[str, str], list[str]]:
    tables = root.select("div.texts > div.info1 > table.t3.ttvam")
    if len(tables) != 1:
        return {}, ["expected one public course summary table"]
    pairs: dict[str, str] = {}
    for row in tables[0].select("tbody > tr"):
        heading = row.find("th", recursive=False)
        value = row.find("td", recursive=False)
        if heading is None or value is None:
            continue
        key = _clean(heading.get_text(" ", strip=True))
        if not key or key in pairs:
            return {}, ["duplicate or empty course summary label"]
        pairs[key] = _clean(value.get_text(" ", strip=True))
    if not _DETAIL_REQUIRED.issubset(pairs) or not set(pairs).issubset(_DETAIL_ALLOWED):
        return pairs, ["course summary field vocabulary changed"]
    return pairs, []


def _capacity(value: Any) -> Optional[int]:
    match = re.search(r"(?<!\d)(\d[\d,]*)\s*명", _clean(value))
    return int(match.group(1).replace(",", "")) if match else None


def _detail_row(
    parent: dict[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any], list[str]]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_education_id"))
    source_status = _clean(raw.get("source_status"))
    errors: list[str] = []
    roots = soup.select("div.view1pic1info1.panel5")
    if len(roots) != 1:
        return dict(parent), [f"detail {identity}: expected one course detail root"]
    root = roots[0]
    headings = root.select("div.texts > h1.h1")
    if len(headings) != 1:
        errors.append(f"detail {identity}: expected one title")
    elif _clean(headings[0].get_text(" ", strip=True)) != _clean(parent.get("title")):
        errors.append(f"detail {identity}: list/detail title mismatch")

    pairs, pair_errors = _detail_pairs(root)
    errors.extend(f"detail {identity}: {message}" for message in pair_errors)
    expected_period = [
        date.fromisoformat(_clean(parent.get("start_date"))),
        date.fromisoformat(_clean(parent.get("end_date"))),
    ]
    expected_apply = [
        date.fromisoformat(_clean(parent.get("apply_start"))),
        date.fromisoformat(_clean(parent.get("apply_end"))),
    ]
    if _dates(pairs.get("교육기간")) != expected_period:
        errors.append(f"detail {identity}: list/detail education period mismatch")
    if _dates(pairs.get("접수기간")) != expected_apply:
        errors.append(f"detail {identity}: list/detail application period mismatch")
    capacity = _capacity(pairs.get("모집인원"))
    if capacity is None:
        errors.append(f"detail {identity}: capacity is malformed")
    fee = _clean(pairs.get("수강료"))
    target = _clean(pairs.get("모집대상"))
    method = _clean(pairs.get("접수방법"))
    if not fee or not target or not method:
        errors.append(f"detail {identity}: required public summary is empty")

    # The application control is rendered in a sibling ``infomenu1`` block,
    # not inside the visual course-summary panel.
    controls = soup.select("a.button.primary.large.radius[href]")
    if len(controls) > 1:
        errors.append(f"detail {identity}: multiple primary application controls")
    control_label = _clean(controls[0].get_text(" ", strip=True)) if controls else ""
    control_url = ""
    if controls:
        parsed = urlparse(urljoin(NAMHAE_URL, _clean(controls[0].get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").rstrip(".").lower() != NAMHAE_HOST
            or parsed.path != NAMHAE_LIST_PATH
            or set(query) != {"amode", "lecIdx", "pageCd"}
            or query.get("amode") != ["ins"]
            or query.get("lecIdx") != [identity]
            or query.get("pageCd") != [NAMHAE_PAGE_CD]
        ):
            errors.append(f"detail {identity}: malformed application control")
        else:
            control_url = namhae_application_url(identity)

    if source_status not in _CURRENT_STATUS_MAP:
        errors.append(f"detail {identity}: unknown current source status {source_status!r}")
    expected_active = source_status in _ACTIVE_CONTROLS
    if expected_active:
        if control_label != source_status or not control_url:
            errors.append(f"detail {identity}: list/detail application-control mismatch")
        if "온라인접수" not in method:
            errors.append(f"detail {identity}: active control lacks online application method")
    elif controls:
        errors.append(f"detail {identity}: closed/pending record exposes an active control")

    normalized_status = _CURRENT_STATUS_MAP.get(source_status, "")
    application_type = "INFO_ONLY"
    reservation_available = False
    if expected_active and control_url:
        application_type = (
            "WAITLIST_APPLY" if source_status == "대기자신청" else "ONLINE_RESERVATION"
        )
        reservation_available = True

    venue = _clean(pairs.get("교육장소")) or _clean(parent.get("branch"))
    row = dict(parent)
    row.update(
        {
            "application_url": control_url if reservation_available else "",
            "application_type": application_type,
            "reservation_available": reservation_available,
            "status": normalized_status,
            "period": _clean(pairs.get("교육기간")),
            "apply_period": _clean(pairs.get("접수기간")),
            "schedule_raw": _clean(pairs.get("교육시간")),
            "fee": fee,
            "target": target,
            "venue_name": venue,
            "capacity": capacity,
        }
    )
    row["raw_fields"] = {
        **raw,
        "source_application_method": method,
        "source_selection_method": _clean(pairs.get("선정방식")),
        "source_application_control": control_label,
        "detail_validated": not errors,
        "application_form_fetched": False,
        "contact_excluded": True,
        "preparation_free_text_excluded": True,
        "attachments_excluded": True,
        "applicant_data_excluded": True,
    }
    return row, errors


def _fetch_page(
    page: int,
    *,
    session_factory: SessionFactory,
    timeout: int,
    fetcher: Optional[Fetcher],
) -> tuple[BeautifulSoup, str]:
    current = session_factory()
    try:
        return _request_soup(
            current, namhae_list_url(page), timeout=timeout, fetcher=fetcher
        )
    finally:
        _close_quietly(current)


def _fetch_detail(
    row: dict[str, Any],
    *,
    session_factory: SessionFactory,
    timeout: int,
    fetcher: Optional[Fetcher],
) -> tuple[dict[str, Any], list[str]]:
    current = session_factory()
    try:
        identity = _clean(row.get("raw_fields", {}).get("source_education_id"))
        soup, _ = _request_soup(
            current, namhae_detail_url(identity), timeout=timeout, fetcher=fetcher
        )
        return _detail_row(row, soup)
    finally:
        _close_quietly(current)


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _privacy_violations(rows: Iterable[Mapping[str, Any]]) -> int:
    violations = 0
    forbidden = {
        "phone",
        "email",
        "instructor",
        "teacher",
        "applicant",
        "contact",
        "preparation",
    }
    for row in rows:
        # Do not scan opaque IDs or URLs: ``RE0105000000`` legitimately looks
        # like a Korean mobile number and branch hashes may look like local
        # numbers.  Scan every user-visible value that can reach course output
        # plus the small allowlisted set of source labels instead.
        visible = " ".join(
            _clean(row.get(key))
            for key in (
                "title",
                "branch",
                "provider_organizer",
                "venue_name",
                "category",
                "target",
                "description",
                "fee",
                "schedule_raw",
            )
        )
        violations += len(_PHONE_RE.findall(visible))
        violations += len(_LOCAL_PHONE_RE.findall(visible))
        violations += len(_EMAIL_RE.findall(visible))
        violations += len(_MASKED_NAME_RE.findall(visible))
        violations += sum(key in row for key in forbidden)
        raw = row.get("raw_fields", {})
        if isinstance(raw, Mapping):
            violations += sum(key in raw for key in forbidden)
            source_labels = " ".join(
                _clean(raw.get(key))
                for key in (
                    "source_facility",
                    "source_status",
                    "source_application_method",
                    "source_selection_method",
                    "source_application_control",
                )
            )
            violations += len(_PHONE_RE.findall(source_labels))
            violations += len(_LOCAL_PHONE_RE.findall(source_labels))
            violations += len(_EMAIL_RE.findall(source_labels))
            violations += len(_MASKED_NAME_RE.findall(source_labels))
    return violations


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "cancelled_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "sentinel_mode": "",
        "sentinel_count": None,
        "stable_rechecks": {},
        "duplicate_source_id_count": 0,
        "privacy_violations": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": NAMHAE_MUNICIPALITY_CODE,
        "municipality_name": NAMHAE_MUNICIPALITY_NAME,
        "ownership_scope": NAMHAE_OWNERSHIP_SCOPE,
        "candidate_ids": dict(NAMHAE_CANDIDATE_IDS),
        "candidate_decisions": dict(NAMHAE_CANDIDATE_DECISIONS),
        "ownership_aliases": [
            {
                "provider": alias.provider,
                "url": alias.url,
                "relationship": alias.relationship,
            }
            for alias in NAMHAE_ALIASES
        ],
        "separate_provincial_providers": list(NAMHAE_SEPARATE_PROVINCIAL_PROVIDERS),
    }


def collect_gyeongnam_namhae_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 200,
    detail_limit: int = 200,
    *,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = NAMHAE_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future Namhae education snapshot."""

    meta = _base_meta()
    if not is_gyeongnam_namhae_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the canonical Gyeongnam Namhae education route"
        )
        return [], NAMHAE_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], NAMHAE_PARSER, meta
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        request_timeout = max(1, int(timeout))
        workers = min(max(1, int(max_workers)), NAMHAE_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], NAMHAE_PARSER, meta

    errors: list[str] = []
    source_cap_reached = False
    first_soup: Optional[BeautifulSoup] = None
    declared_total = last_page = 0
    try:
        first_soup, _ = _fetch_page(
            1,
            session_factory=session_factory,
            timeout=request_timeout,
            fetcher=fetcher,
        )
        meta["pages"] += 1
        meta["list_requests"] += 1
    except Exception as exc:
        errors.append(f"first page: {type(exc).__name__}: {_clean(exc)}")

    first_rows: list[dict[str, Any]] = []
    if first_soup is not None:
        declared_total, last_page, item_errors = _form_and_total(
            first_soup, expected_display_page=1
        )
        errors.extend(item_errors)
        first_rows, item_errors = _parse_list_page(first_soup, source_page=1)
        errors.extend(item_errors)
        if declared_total and not first_rows:
            errors.append("first page contains no course rows")

    boundary_count = 1 if last_page == 1 else 2
    required_list_requests = last_page + 1 + boundary_count if last_page else 0
    meta["required_list_requests"] = required_list_requests
    if required_list_requests > allowed_pages:
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of {required_list_requests} "
            "required list/clamp/recheck requests"
        )

    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    sentinel_count: Optional[int] = None
    sentinel_mode = ""
    stable_rechecks: dict[str, bool] = {}
    if not errors:
        try:
            if last_page > 1:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            _fetch_page,
                            page,
                            session_factory=session_factory,
                            timeout=request_timeout,
                            fetcher=fetcher,
                        ): page
                        for page in range(2, last_page + 1)
                    }
                    for future in as_completed(futures):
                        page = futures[future]
                        soup, _ = future.result()
                        meta["pages"] += 1
                        meta["list_requests"] += 1
                        total, found_last, item_errors = _form_and_total(
                            soup, expected_display_page=page
                        )
                        errors.extend(f"page {page}: {value}" for value in item_errors)
                        if total != declared_total or found_last != last_page:
                            errors.append(f"page {page}: declared pagination changed")
                        parsed, item_errors = _parse_list_page(soup, source_page=page)
                        errors.extend(item_errors)
                        pages[page] = parsed

            sentinel_page = last_page + 1
            sentinel_soup, _ = _fetch_page(
                sentinel_page,
                session_factory=session_factory,
                timeout=request_timeout,
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
            total, found_last, item_errors = _form_and_total(
                sentinel_soup, expected_display_page=last_page
            )
            errors.extend(f"clamped sentinel: {value}" for value in item_errors)
            sentinel_rows, item_errors = _parse_list_page(
                sentinel_soup, source_page=sentinel_page
            )
            errors.extend(item_errors)
            sentinel_count = len(sentinel_rows)
            if (
                total == declared_total
                and found_last == last_page
                and _page_signature(sentinel_rows)
                == _page_signature(pages.get(last_page, []))
            ):
                sentinel_mode = "clamped_last"
            else:
                errors.append("immediate post-last clamp differs from the exact last page")

            for page in dict.fromkeys((1, last_page)):
                soup, _ = _fetch_page(
                    page,
                    session_factory=session_factory,
                    timeout=request_timeout,
                    fetcher=fetcher,
                )
                meta["pages"] += 1
                meta["list_requests"] += 1
                total, found_last, item_errors = _form_and_total(
                    soup, expected_display_page=page
                )
                errors.extend(f"page {page} recheck: {value}" for value in item_errors)
                parsed, item_errors = _parse_list_page(soup, source_page=page)
                errors.extend(item_errors)
                stable = bool(
                    total == declared_total
                    and found_last == last_page
                    and _page_signature(parsed) == _page_signature(pages.get(page, []))
                )
                stable_rechecks[str(page)] = stable
                if not stable:
                    errors.append(f"page {page}: stable boundary recheck changed")
        except Exception as exc:
            errors.append(f"catalogue traversal: {type(exc).__name__}: {_clean(exc)}")

    source_rows = [
        row for page in range(1, last_page + 1) for row in pages.get(page, [])
    ]
    for page in range(1, last_page + 1):
        expected_count = max(
            0,
            min(NAMHAE_PAGE_SIZE, declared_total - ((page - 1) * NAMHAE_PAGE_SIZE)),
        )
        if len(pages.get(page, [])) != expected_count:
            errors.append(
                f"page {page}: expected {expected_count} rows, got {len(pages.get(page, []))}"
            )
    if declared_total != len(source_rows):
        errors.append(f"declared total {declared_total} != parsed total {len(source_rows)}")
    identities = [_clean(row.get("provider_course_id")) for row in source_rows]
    duplicate_source_ids = len(identities) - len(set(identities))
    if duplicate_source_ids:
        errors.append(f"{duplicate_source_ids} duplicate source identities")
    # The portal advertises "latest" ordering, but a few historical rows were
    # migrated with IDs that do not reflect their registration timestamp.
    # Completeness therefore relies on total/cardinality/unique IDs and stable
    # boundary signatures, not an invalid numeric-ID monotonicity assumption.

    current_rows: list[dict[str, Any]] = []
    expired_count = cancelled_count = 0
    for row in source_rows:
        ended = date.fromisoformat(_clean(row.get("end_date")))
        if ended < cutoff:
            expired_count += 1
        elif _CANCELLED_RE.search(_clean(row.get("title"))):
            cancelled_count += 1
        else:
            source_status = _clean(row.get("raw_fields", {}).get("source_status"))
            if source_status not in _CURRENT_STATUS_MAP:
                errors.append(
                    f"{_clean(row.get('provider_course_id'))}: "
                    f"unknown current source status {source_status!r}"
                )
            current_rows.append(row)

    if len(current_rows) > allowed_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {len(current_rows)} "
            "required current/future details"
        )

    detailed: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    detail_attempts = 0
    if not errors and current_rows:
        found: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _fetch_detail,
                    row,
                    session_factory=session_factory,
                    timeout=request_timeout,
                    fetcher=fetcher,
                ): row
                for row in current_rows
            }
            for future in as_completed(futures):
                parent = futures[future]
                identity = _clean(
                    parent.get("raw_fields", {}).get("source_education_id")
                )
                detail_attempts += 1
                try:
                    row, item_errors = future.result()
                    if item_errors:
                        detail_errors.extend(item_errors)
                    else:
                        found[identity] = row
                except Exception as exc:
                    detail_errors.append(
                        f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                    )
        detailed = [
            found[_clean(row.get("raw_fields", {}).get("source_education_id"))]
            for row in current_rows
            if _clean(row.get("raw_fields", {}).get("source_education_id")) in found
        ]
    errors.extend(detail_errors)
    details_complete = bool(
        not detail_errors
        and detail_attempts == len(current_rows)
        and len(detailed) == len(current_rows)
    )

    result: list[dict[str, Any]] = []
    if not errors and details_complete:
        result = list((dedupe_rows or _default_dedupe)(detailed))
        if len(result) != len(detailed):
            errors.append(
                f"dedupe changed complete row count {len(detailed)} to {len(result)}"
            )
            result = []
    result.sort(
        key=lambda row: (
            _clean(row.get("start_date")),
            _clean(row.get("title")),
            _clean(row.get("provider_course_id")),
        )
    )

    privacy_violations = _privacy_violations(result)
    if privacy_violations:
        errors.append(f"{privacy_violations} PII allowlist violations")
        result = []

    expected_rechecks = 1 if last_page == 1 else 2
    pagination_complete = bool(
        not errors
        and sentinel_mode == "clamped_last"
        and len(stable_rechecks) == expected_rechecks
        and all(stable_rechecks.values())
        and meta["list_requests"] == required_list_requests
    )
    snapshot_complete = bool(pagination_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    facility_counts = Counter(_clean(row.get("branch")) for row in result)
    status_counts = Counter(_clean(row.get("status")) for row in result)
    application_counts = Counter(_clean(row.get("application_type")) for row in result)
    meta.update(
        {
            "source_total": len(source_rows),
            "source_rows": len(source_rows),
            "declared_total": declared_total,
            "data_pages": last_page,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "expired_count": expired_count,
            "cancelled_count": cancelled_count,
            "detail_attempts": detail_attempts,
            "detail_pages": len(detailed),
            "detail_errors": len(detail_errors),
            "sentinel_mode": sentinel_mode,
            "sentinel_count": sentinel_count,
            "stable_rechecks": stable_rechecks,
            "duplicate_source_id_count": duplicate_source_ids,
            "privacy_violations": privacy_violations,
            "facility_count": len(facility_counts),
            "facility_counts": dict(facility_counts),
            "status_counts": dict(status_counts),
            "application_type_counts": dict(application_counts),
            "pagination_detected": last_page > 1,
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "complete Namhae catalogue contains only ended/cancelled courses"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, NAMHAE_PARSER, meta


collect = collect_gyeongnam_namhae_education_courses


__all__ = [
    "NAMHAE_ALIASES",
    "NAMHAE_CANDIDATE_DECISIONS",
    "NAMHAE_CANDIDATE_IDS",
    "NAMHAE_MUNICIPALITY_CODE",
    "NAMHAE_MUNICIPALITY_NAME",
    "NAMHAE_OWNERSHIP_SCOPE",
    "NAMHAE_PAGE_CD",
    "NAMHAE_PARSER",
    "NAMHAE_PROVIDER",
    "NAMHAE_SEPARATE_PROVINCIAL_PROVIDERS",
    "NAMHAE_URL",
    "NamhaeAlias",
    "collect",
    "collect_gyeongnam_namhae_education_courses",
    "is_gyeongnam_namhae_alias_target",
    "is_gyeongnam_namhae_education_target",
    "is_target",
    "namhae_application_url",
    "namhae_detail_url",
    "namhae_list_url",
]
