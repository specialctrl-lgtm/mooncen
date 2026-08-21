from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from threading import local
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from Crawler.Crawler_MunicipalYaml import (
    CrawlTarget,
    dedupe_rows,
    fetch_soup,
    normalize_link,
    normalize_mixed_date_range,
    session,
    should_skip_expired_course,
    stable_course_id,
)
from Crawler.Crawler_YamlSources import parse_date_range
from utils import clean_text


JNE_INTEGRATED_LIBRARY_HOST = "jnelib.jne.go.kr"
JNE_INTEGRATED_LIBRARY_CATALOGUES: dict[str, dict[str, str]] = {
    "/educationIntegration.es": {
        "mid": "d50401000000",
        "category": "독서문화행사",
        "parser": "jne_integrated_library_reading",
    },
    "/lectureIntegration.es": {
        "mid": "d50402000000",
        "category": "평생학습강좌",
        "parser": "jne_integrated_library_lecture",
    },
}
JNE_OUT_OF_SCOPE_TITLE_MARKERS = ("전집대출", "사물함")
JNE_DETAIL_WORKERS = 6


def _page_url(target_url: str, page: int) -> str:
    parsed = urlparse(target_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["nPage"] = [str(page)]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def _page_number(href: str) -> int:
    try:
        return int((parse_qs(urlparse(href).query).get("nPage") or ["0"])[0])
    except (TypeError, ValueError):
        return 0


def _declared_last_page(soup: Any) -> int:
    return max(
        (
            _page_number(clean_text(link.get("href")))
            for link in soup.select("a[href*='nPage=']")
        ),
        default=1,
    )


def _declared_total(soup: Any) -> int | None:
    node = soup.select_one(".page_info .txt_bold")
    text = clean_text(node.get_text(" ", strip=True)) if node else ""
    if not text:
        return None
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def _official_branch_registry(soup: Any) -> dict[str, str]:
    registry: dict[str, str] = {}
    for option in soup.select("select[name='selSid'] option[value]"):
        sid = clean_text(option.get("value"))
        name = clean_text(option.get_text(" ", strip=True))
        if sid and sid != "ALL" and name:
            registry[sid] = name
    return registry


def _excluded_branch_sids(target: CrawlTarget) -> set[str]:
    raw = target.extra.get("excluded_branch_sids")
    if not isinstance(raw, list):
        return set()
    return {clean_text(value) for value in raw if clean_text(value)}


def _catalogue_contract(target: CrawlTarget) -> dict[str, str]:
    parsed = urlparse(target.url)
    contract = JNE_INTEGRATED_LIBRARY_CATALOGUES.get(parsed.path)
    query_mid = clean_text((parse_qs(parsed.query).get("mid") or [""])[0])
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() != JNE_INTEGRATED_LIBRARY_HOST
        or contract is None
        or query_mid != contract["mid"]
    ):
        raise ValueError("target is not a canonical JNE integrated-library catalogue")
    return contract


def _has_reversed_date_range(value: str) -> bool:
    try:
        start_date, end_date = parse_date_range(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(start_date and end_date and start_date > end_date)


def _is_out_of_scope_title(value: str) -> str:
    compact = re.sub(r"\s+", "", clean_text(value))
    return next(
        (marker for marker in JNE_OUT_OF_SCOPE_TITLE_MARKERS if marker in compact),
        "",
    )


def _detail_pairs(soup: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = clean_text(cells[0].get_text(" ", strip=True))
        value = clean_text(cells[1].get_text(" ", strip=True))
        if label:
            pairs[label] = value
    return pairs


def _usable_detail_value(value: Any) -> str:
    text = clean_text(value)
    return "" if text in {"", "-", "0"} else text


def _normalized_detail_period(value: Any) -> str:
    text = clean_text(value)
    if not text or text == "~":
        return ""
    start_date, end_date = parse_date_range(text)
    if start_date and end_date:
        return f"{start_date:%Y-%m-%d} ~ {end_date:%Y-%m-%d}"
    if start_date:
        return f"{start_date:%Y-%m-%d}"
    return normalize_mixed_date_range(text)


def _detail_capacity(pairs: dict[str, str]) -> dict[str, int]:
    reading_numbers = [
        int(value.replace(",", ""))
        for value in re.findall(r"\d[\d,]*", pairs.get("수강인원", ""))
    ]
    if reading_numbers:
        result = {"capacity_current": reading_numbers[0]}
        if len(reading_numbers) > 1:
            result["capacity_total"] = reading_numbers[1]
        if len(reading_numbers) > 2:
            result["waitlist_total"] = reading_numbers[2]
        return result

    lecture_numbers = [
        int(value.replace(",", ""))
        for value in re.findall(r"\d[\d,]*", pairs.get("모집인원", ""))
    ]
    if not lecture_numbers:
        return {}
    result = {"capacity_total": lecture_numbers[0]}
    if len(lecture_numbers) > 1:
        result["waitlist_total"] = lecture_numbers[1]
    return result


def _enrich_detail_row(
    row: dict[str, Any],
    *,
    client: Any,
    timeout: int,
) -> None:
    detail_soup = fetch_soup(client, clean_text(row.get("raw_url")), timeout=timeout)
    pairs = _detail_pairs(detail_soup)
    if not pairs or "강좌명" not in pairs:
        raise ValueError("official detail table was not found")

    detail_title = _usable_detail_value(pairs.get("강좌명"))
    if detail_title:
        row["title"] = detail_title

    target_text = _usable_detail_value(pairs.get("대상"))
    if target_text:
        row["target"] = target_text

    period = _normalized_detail_period(
        pairs.get("운영기간") or pairs.get("수강기간")
    )
    if period:
        row["period"] = period
        row["start_date"], row["end_date"] = parse_date_range(period)

    time_text = _usable_detail_value(
        pairs.get("강의 시간") or pairs.get("수강시간")
    )
    weekday_text = _usable_detail_value(pairs.get("수강요일"))
    schedule_parts = [value for value in (period, weekday_text, time_text) if value]
    if schedule_parts:
        row["schedule_raw"] = " / ".join(schedule_parts)

    apply_period = _normalized_detail_period(
        pairs.get("신청기간") or pairs.get("인터넷 접수기간")
    )
    apply_time = _usable_detail_value(pairs.get("신청시간"))
    if apply_period:
        row["apply_period"] = " / ".join(
            value for value in (apply_period, apply_time) if value
        )

    venue_name = _usable_detail_value(pairs.get("교육장소"))
    if venue_name:
        row["venue_name"] = venue_name
    instructor = _usable_detail_value(pairs.get("강사명"))
    if instructor:
        row["instructor"] = instructor

    fee_text = _usable_detail_value(
        pairs.get("수강료") or pairs.get("참가비") or pairs.get("체험비")
    )
    if fee_text:
        row["fee"] = fee_text
    material_fee = _usable_detail_value(pairs.get("재료비"))
    if material_fee:
        row["material_fee"] = material_fee

    description = _usable_detail_value(pairs.get("내용") or pairs.get("비고"))
    if description:
        row["description"] = description[:4000]
    row.update(_detail_capacity(pairs))

    raw_fields = row.setdefault("raw_fields", {})
    raw_fields["detail_pairs"] = pairs
    raw_fields["detail_enriched"] = True


def collect_jne_integrated_library(
    target: CrawlTarget,
    *,
    timeout: int,
    max_pages: int,
    detail_limit: int = 0,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete JNE integrated catalogue and retain official branches.

    The central catalogue is the authoritative inventory for branches that do
    not already have a dedicated canonical crawler. ``excluded_branch_sids``
    keeps those existing owners out of this fallback without depending on a
    branch display name that can change.
    """

    contract = _catalogue_contract(target)
    client = session()
    rows: list[dict[str, Any]] = []
    source_rows = 0
    pages = 0
    declared_last = 1
    declared_count: int | None = None
    branch_registry: dict[str, str] = {}
    branch_sid_by_name: dict[str, str] = {}
    source_branch_counts: Counter[str] = Counter()
    retained_branch_counts: Counter[str] = Counter()
    excluded_branch_counts: Counter[str] = Counter()
    excluded_sids = _excluded_branch_sids(target)
    unknown_branches: Counter[str] = Counter()
    malformed_rows = 0
    invalid_date_rows = 0
    invalid_date_samples: list[dict[str, str]] = []
    out_of_scope_counts: Counter[str] = Counter()
    premature_empty_page = 0
    pagination_exhausted = False

    for page in range(1, max(0, max_pages) + 1):
        current_url = _page_url(target.url, page)
        soup = fetch_soup(client, current_url, timeout=timeout)
        pages += 1

        if page == 1:
            declared_last = _declared_last_page(soup)
            declared_count = _declared_total(soup)
            branch_registry = _official_branch_registry(soup)
            branch_sid_by_name = {
                branch_name: sid for sid, branch_name in branch_registry.items()
            }

        page_source_rows = 0
        for tr in soup.select("table tbody tr"):
            cells = tr.find_all("td", recursive=False)
            if not cells:
                continue
            if len(cells) == 1:
                # The page after the declared last page uses one colspan cell
                # as its empty-page sentinel, not as a course record.
                continue
            if len(cells) < 5:
                malformed_rows += 1
                continue

            branch = clean_text(cells[0].get_text(" ", strip=True))
            title_cell = cells[1]
            link = title_cell.select_one("a[href]")
            if not branch or link is None:
                malformed_rows += 1
                continue

            branch_sid = branch_sid_by_name.get(branch, "")
            if not branch_sid:
                unknown_branches[branch or "(empty)"] += 1

            raw_url = normalize_link(current_url, link.get("href")) or current_url
            schedule_node = title_cell.select_one(".text-day")
            schedule_raw = (
                clean_text(schedule_node.get_text(" ", strip=True))
                if schedule_node
                else ""
            )
            title = clean_text(link.get_text(" ", strip=True))
            if schedule_raw and title.endswith(schedule_raw):
                title = clean_text(title[: -len(schedule_raw)])

            target_text = clean_text(cells[2].get_text(" ", strip=True))
            apply_period = clean_text(cells[3].get_text(" ", strip=True))
            status = clean_text(cells[4].get_text(" ", strip=True))
            period = normalize_mixed_date_range(schedule_raw)
            start_date, end_date = parse_date_range(period)
            source_rows += 1
            page_source_rows += 1
            source_branch_counts[branch] += 1

            if branch_sid in excluded_sids:
                excluded_branch_counts[branch] += 1
                continue
            out_of_scope_marker = _is_out_of_scope_title(title)
            if out_of_scope_marker:
                out_of_scope_counts[out_of_scope_marker] += 1
                continue
            if _has_reversed_date_range(period) or _has_reversed_date_range(
                apply_period
            ):
                invalid_date_rows += 1
                if len(invalid_date_samples) < 10:
                    invalid_date_samples.append(
                        {
                            "title": title,
                            "branch": branch,
                            "period": period,
                            "apply_period": apply_period,
                            "raw_url": raw_url,
                        }
                    )
                continue

            rows.append(
                {
                    "provider": target.provider,
                    "provider_course_id": stable_course_id(
                        target.provider,
                        raw_url,
                        title,
                        branch_sid or branch,
                    ),
                    "title": title,
                    "branch": branch,
                    "branch_code": branch_sid,
                    "category": contract["category"],
                    "venue_name": branch,
                    "raw_url": raw_url,
                    "application_url": raw_url,
                    "status": status,
                    "period": period,
                    "start_date": start_date,
                    "end_date": end_date,
                    "apply_period": apply_period,
                    "schedule_raw": schedule_raw or period,
                    "target": target_text,
                    "description": clean_text(
                        " ".join(
                            value
                            for value in (
                                target_text,
                                schedule_raw,
                                apply_period,
                                status,
                            )
                            if value
                        )
                    ),
                    "reservation_available": status
                    in {"접수중", "대기자접수중"},
                    "raw_fields": {
                        "catalogue": contract["parser"],
                        "branch_sid": branch_sid,
                        "cells": [
                            clean_text(cell.get_text(" ", strip=True))
                            for cell in cells
                        ],
                    },
                }
            )
            retained_branch_counts[branch] += 1

        if page_source_rows == 0:
            if page <= declared_last:
                premature_empty_page = page
            else:
                pagination_exhausted = True
            break

    rows = dedupe_rows(rows)
    errors: list[str] = []
    if not branch_registry:
        errors.append("official branch registry was not found")
    missing_excluded_sids = sorted(excluded_sids - set(branch_registry))
    if missing_excluded_sids:
        errors.append(
            "configured excluded branch ids are absent: "
            + ",".join(missing_excluded_sids)
        )
    if unknown_branches:
        errors.append(
            "unregistered official branch rows: "
            + ",".join(sorted(unknown_branches))
        )
    if malformed_rows:
        errors.append(f"malformed table rows: {malformed_rows}")
    if premature_empty_page:
        errors.append(
            f"empty page {premature_empty_page} before declared page {declared_last}"
        )
    if declared_count is not None and declared_count != source_rows:
        errors.append(
            f"declared/source row mismatch: {declared_count}/{source_rows}"
        )

    detail_candidates = [
        row for row in rows if not should_skip_expired_course(row)
    ]
    detail_pages = 0
    detail_errors: list[str] = []
    details_requested = detail_limit > 0
    if details_requested and detail_limit < len(detail_candidates):
        errors.append(
            f"detail_limit cap allows {detail_limit} of "
            f"{len(detail_candidates)} current/future details"
        )
    allowed_details = detail_candidates[: max(0, detail_limit)]
    detail_attempts = len(allowed_details)
    if allowed_details:
        thread_state = local()

        def enrich(row: dict[str, Any]) -> None:
            client = getattr(thread_state, "client", None)
            if client is None:
                client = session()
                thread_state.client = client
            _enrich_detail_row(row, client=client, timeout=timeout)

        with ThreadPoolExecutor(
            max_workers=min(JNE_DETAIL_WORKERS, len(allowed_details))
        ) as executor:
            futures = {
                executor.submit(enrich, row): row for row in allowed_details
            }
            for future in as_completed(futures):
                row = futures[future]
                try:
                    future.result()
                    detail_pages += 1
                except Exception as exc:
                    if len(detail_errors) < 10:
                        detail_errors.append(
                            f"{clean_text(row.get('provider_course_id'))}: "
                            f"{type(exc).__name__}"
                        )
    if detail_errors:
        errors.append(
            f"detail fetch failed for {len(detail_errors)} current/future rows "
            f"({', '.join(detail_errors[:5])})"
        )

    pagination_complete = pagination_exhausted and not errors
    details_complete = (
        not details_requested
        or (
            detail_attempts == len(detail_candidates)
            and detail_pages == detail_attempts
            and not detail_errors
        )
    )
    meta = {
        "pages": pages,
        "detail_pages": detail_pages,
        "detail_attempts": detail_attempts,
        "detail_candidates": len(detail_candidates),
        "details_complete": details_complete,
        "discovered_links": len(rows),
        "pagination_detected": declared_last > 1,
        "pagination_exhausted": pagination_exhausted,
        "pagination_complete": pagination_complete,
        "recursion_depth": 0,
        "declared_pages": declared_last,
        "declared_rows": declared_count,
        "source_rows": source_rows,
        "official_branch_count": len(branch_registry),
        "official_branches": branch_registry,
        "source_branch_counts": dict(source_branch_counts),
        "branch_counts": dict(retained_branch_counts),
        "excluded_branch_counts": dict(excluded_branch_counts),
        "out_of_scope_counts": dict(out_of_scope_counts),
        "unknown_branch_counts": dict(unknown_branches),
        "invalid_date_rows": invalid_date_rows,
        "invalid_date_samples": invalid_date_samples,
        "configured_collection_error": "; ".join(errors),
        "no_current_data": not rows and source_rows == 0 and pagination_complete,
        "no_current_reason": (
            "no_current_jne_integrated_library_programs"
            if not rows and source_rows == 0 and pagination_complete
            else ""
        ),
    }
    return rows, contract["parser"], meta


__all__ = [
    "JNE_INTEGRATED_LIBRARY_CATALOGUES",
    "JNE_INTEGRATED_LIBRARY_HOST",
    "collect_jne_integrated_library",
]
