from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "CNALL_LECTURE"
BASE_URL = "https://www.cnall.or.kr"
LIST_PATH = "/lecture/lectureList.do"
DETAIL_PATH = "/lecture/lectureDetail.do"
ORGAN_LIST_URL = f"{BASE_URL}/organ/organList.do"
LIST_URL = (
    f"{BASE_URL}{LIST_PATH}?organAllYn=Y&areaCd=&targetCd=&lectureDiv1Cd="
    "&lectureDiv2Cd=&costYn=&applyTypeCd=&organAllChk=Y&searchCondition=&searchKeyword="
)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_id(*parts: object) -> str:
    raw = "|".join(clean_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return s


def fetch_soup(s: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = s.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def compact_branch_name(value: str) -> str:
    text = clean_text(value)
    replacements = [
        "충청남도교육청",
        "충청남도",
        "교육지원청",
    ]
    for token in replacements:
        text = text.replace(token, "")
    return clean_text(text) or clean_text(value)


def branch_aliases(value: str) -> set[str]:
    text = clean_text(value)
    compact = compact_branch_name(text)
    aliases = {text, compact}
    for marker in ("교육지원청", "교육청"):
        if marker in text:
            aliases.add(clean_text(text.split(marker)[-1]))
    if compact.endswith("도서관"):
        aliases.add(compact.replace("도서관", "").strip())
    if compact.endswith("평생교육원"):
        aliases.add(compact.replace("평생교육원", "").strip())
    return {alias for alias in aliases if alias}


def parse_money(value: object) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    if "무료" in text:
        return 0
    match = re.search(r"\d[\d,]*", text)
    return int(match.group(0).replace(",", "")) if match else None


def parse_date(value: str) -> str | None:
    match = re.search(r"(\d{4})[. -]*(\d{1,2})[. -]*(\d{1,2})", clean_text(value))
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def parse_date_range(value: str) -> tuple[str | None, str | None]:
    dates = re.findall(r"\d{4}[. -]*\d{1,2}[. -]*\d{1,2}", clean_text(value))
    parsed = [parse_date(item) for item in dates]
    parsed = [item for item in parsed if item]
    if not parsed:
        return None, None
    return parsed[0], parsed[-1]


def db_status(value: str) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if any(token in text for token in ("접수중", "모집중", "신청가능")) and "접수예정" not in text:
        return "OPEN"
    if any(token in text for token in ("접수예정", "예정")):
        return "SCHEDULED"
    if any(token in text for token in ("대기")):
        return "WAITING"
    if any(token in text for token in ("마감", "종료", "진행중", "완료")):
        return "CLOSED"
    return "SCHEDULED"


def max_page_from_soup(soup: BeautifulSoup) -> int:
    max_page = 1
    for element in soup.select("a[onclick*='fnList']"):
        match = re.search(r"fnList\((\d+)\)", element.get("onclick") or "")
        if match:
            max_page = max(max_page, int(match.group(1)))
        text = clean_text(element.get_text(" ", strip=True))
        if text.isdigit():
            max_page = max(max_page, int(text))
    return max_page


def parse_organ_list(s: requests.Session, timeout: int) -> tuple[dict[str, dict], dict[str, dict]]:
    soup = fetch_soup(s, ORGAN_LIST_URL, timeout)
    by_code: dict[str, dict] = {}
    by_alias: dict[str, dict] = {}
    for tr in soup.select("table tbody tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue
        region = clean_text(cells[0].get_text(" ", strip=True))
        name = clean_text(cells[1].get_text(" ", strip=True))
        address = clean_text(cells[2].get_text(" ", strip=True))
        phone = clean_text(cells[3].get_text(" ", strip=True))
        course_link = cells[4].find("a", href=True)
        if not name or not course_link:
            continue
        query = parse_qs(urlparse(course_link["href"]).query)
        organ_id = (query.get("organIdxArr") or [""])[0]
        branch = {
            "branch_code": f"organ_{organ_id}" if organ_id else f"branch_{stable_id(name)[:12]}",
            "name": compact_branch_name(name),
            "full_name": name,
            "region": region,
            "address": address,
            "phone": phone,
            "homepage_url": "",
            "organ_id": organ_id,
        }
        homepage = cells[1].find("a", href=True)
        if homepage and "lectureList.do" not in homepage["href"]:
            branch["homepage_url"] = clean_text(homepage["href"])
        by_code[branch["branch_code"]] = branch
        for alias in branch_aliases(name) | branch_aliases(branch["name"]):
            by_alias[alias] = branch
    return by_code, by_alias


def detail_url(lecture_idx: str, page: int = 1) -> str:
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode({'organAllYn': 'Y', 'organAllChk': 'Y', 'currentPageNo': page, 'recordCountPerPage': 20, 'lectureIdx': lecture_idx})}"


def list_url(page: int = 1) -> str:
    params = {
        "organAllYn": "Y",
        "organAllChk": "Y",
        "currentPageNo": page,
        "recordCountPerPage": 20,
    }
    return f"{BASE_URL}{LIST_PATH}?{urlencode(params)}"


def labeled_span(li: Tag, label: str) -> str:
    for span in li.select(".info span"):
        text = clean_text(span.get_text(" ", strip=True))
        if text.startswith(label):
            return clean_text(text.split(":", 1)[-1])
    return ""


def parse_capacity(value: str) -> tuple[int | None, int | None, int | None]:
    text = clean_text(value)
    match = re.search(r"온라인신청자수\s*:\s*(\d+)\s*/\s*(\d+)", text)
    if not match:
        match = re.search(r"신청자수\s*:\s*(\d+)\s*/\s*(\d+)", text)
    if not match:
        return None, None, None
    current = int(match.group(1))
    total = int(match.group(2))
    return total, current, max(total - current, 0)


def parse_card(li: Tag, page: int, branches_by_alias: dict[str, dict]) -> dict | None:
    title_link = li.select_one(".title a")
    if not title_link:
        return None
    title = clean_text(title_link.get_text(" ", strip=True))
    href = title_link.get("href") or ""
    match = re.search(r"fnDetail\('([^']+)'\)", href)
    lecture_idx = match.group(1) if match else stable_id(title)
    branch_name = labeled_span(li, "기관") or ""
    branch = branches_by_alias.get(branch_name) or branches_by_alias.get(compact_branch_name(branch_name))
    branch_code = branch["branch_code"] if branch else f"branch_{stable_id(branch_name)[:12]}"
    region_node = li.select_one(".title .icon.etc")
    target_node = li.select_one(".title .icon.target")
    fee_node = li.select_one(".title .icon.nCharge, .title .icon.charge")
    info_spans = [clean_text(span.get_text(" ", strip=True)) for span in li.select(".info span")]
    period_raw = labeled_span(li, "강좌기간")
    apply_period = labeled_span(li, "접수기간")
    days = ""
    time_text = ""
    for value in info_spans:
        if re.fullmatch(r"[월화수목금토일, ]+", value):
            days = value
        if re.search(r"\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}", value):
            time_text = value
    start_date, end_date = parse_date_range(period_raw)
    capacity_total, capacity_current, capacity_remaining = parse_capacity(" ".join(info_spans))
    status_raw = clean_text(li.select_one(".info_r").get_text(" ", strip=True)) if li.select_one(".info_r") else ""
    return {
        "provider": PROVIDER,
        "provider_course_id": lecture_idx,
        "title": title,
        "branch": branch["name"] if branch else compact_branch_name(branch_name),
        "branch_code": branch_code,
        "address": branch.get("address", "") if branch else "",
        "phone": branch.get("phone", "") if branch else "",
        "website_url": branch.get("homepage_url", BASE_URL) if branch else BASE_URL,
        "region": clean_text(region_node.get_text(" ", strip=True)) if region_node else branch.get("region", "") if branch else "",
        "raw_url": detail_url(lecture_idx, page),
        "application_url": detail_url(lecture_idx, page),
        "application_type": "ONLINE_RESERVATION",
        "application_method_raw": status_raw,
        "reservation_available": db_status(status_raw) in {"OPEN", "SCHEDULED", "WAITING"},
        "period": period_raw,
        "start_date": start_date,
        "end_date": end_date,
        "schedule_raw": clean_text(" ".join(part for part in (days, time_text) if part)),
        "schedule_days": [day.strip() for day in days.split(",") if day.strip()],
        "apply_period": apply_period,
        "target": clean_text(target_node.get_text(" ", strip=True)) if target_node else "",
        "fee_raw": labeled_span(li, "수강료") or (clean_text(fee_node.get_text(" ", strip=True)) if fee_node else ""),
        "fee": parse_money(labeled_span(li, "수강료") or (fee_node.get_text(" ", strip=True) if fee_node else "")),
        "status": db_status(status_raw),
        "status_raw": status_raw,
        "capacity_total": capacity_total,
        "capacity_current": capacity_current,
        "capacity_remaining": capacity_remaining,
        "description": "",
        "image_url": "",
        "venue_name": branch["name"] if branch else compact_branch_name(branch_name),
        "venue_address": branch.get("address", "") if branch else "",
        "collection_category": "OTHER",
        "domain_category": "도서관",
        "source_group": "library_lifelong_learning",
        "operator_type": "education_office",
        "collection_type": "integrated_reservation",
        "program_type": "강좌",
    }


def detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table tr"):
        headers = [clean_text(th.get_text(" ", strip=True)) for th in tr.find_all("th")]
        cells = [clean_text(td.get_text(" ", strip=True)) for td in tr.find_all("td")]
        if not headers and cells:
            continue
        for index, key in enumerate(headers):
            if key and index < len(cells):
                pairs[key] = cells[index]
    return pairs


def extract_description(soup: BeautifulSoup) -> str:
    for table in soup.select("table"):
        if table.find("th"):
            continue
        text = clean_text(table.get_text(" ", strip=True))
        if len(text) > 20:
            return text[:4000]
    content = soup.select_one("td.content, .content, .board-view")
    return clean_text(content.get_text(" ", strip=True))[:4000] if content else ""


def enrich_detail(s: requests.Session, row: dict, timeout: int) -> bool:
    soup = fetch_soup(s, row["raw_url"], timeout)
    pairs = detail_pairs(soup)
    if pairs.get("대상"):
        row["target"] = clean_text(pairs["대상"].replace("/", " "))
    if pairs.get("수강료"):
        row["fee_raw"] = pairs["수강료"]
        row["fee"] = parse_money(pairs["수강료"])
    if pairs.get("강좌기간"):
        row["period"] = pairs["강좌기간"]
        row["start_date"], row["end_date"] = parse_date_range(pairs["강좌기간"])
        bits = [part.strip() for part in pairs["강좌기간"].split("/") if part.strip()]
        if len(bits) >= 2:
            row["schedule_raw"] = clean_text(" ".join(bits[1:]))
    if pairs.get("접수기간"):
        row["apply_period"] = pairs["접수기간"]
    if pairs.get("신청/대기"):
        total, current, remaining = parse_capacity(pairs["신청/대기"])
        row["capacity_total"], row["capacity_current"], row["capacity_remaining"] = total, current, remaining
    if pairs.get("기관정보"):
        row["branch"] = compact_branch_name(pairs["기관정보"].split("|", 1)[0])
        row["venue_name"] = row["branch"]
    row["description"] = extract_description(soup)
    for img in soup.select("img[src]"):
        src = img.get("src") or ""
        if "ico_" in src or "mark_" in src:
            continue
        row["image_url"] = urljoin(row["raw_url"], src)
        break
    return True


def collect(limit: int, max_pages: int, detail_limit: int, timeout: int) -> tuple[list[dict], dict]:
    s = session()
    _, branches_by_alias = parse_organ_list(s, timeout)
    first = fetch_soup(s, LIST_URL, timeout)
    total_pages = max_page_from_soup(first)
    pages_to_fetch = min(max_pages, total_pages)
    rows: list[dict] = []
    detail_count = 0
    for page in range(1, pages_to_fetch + 1):
        soup = first if page == 1 else fetch_soup(s, list_url(page), timeout)
        for li in soup.select(".lecture-list > li"):
            row = parse_card(li, page, branches_by_alias)
            if not row:
                continue
            if detail_limit <= 0 or detail_count < detail_limit:
                try:
                    if enrich_detail(s, row, timeout):
                        detail_count += 1
                except Exception:
                    pass
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows, {"pages": page, "detail_pages": detail_count, "total_pages": total_pages}
    return rows, {"pages": pages_to_fetch, "detail_pages": detail_count, "total_pages": total_pages}


def save_db(rows: list[dict], skip_expired: bool = True) -> int:
    if not rows:
        return 0
    from DB.db_utils import get_db_cursor

    today = date.today().isoformat()
    saved = 0
    branch_ids: dict[str, str] = {}
    with get_db_cursor() as cur:
        for row in rows:
            if skip_expired and row.get("end_date") and row["end_date"] < today:
                continue
            branch_code = clean_text(row.get("branch_code"))[:50]
            if branch_code not in branch_ids:
                cur.execute(
                    """
                    INSERT INTO branches(provider, branch_code, name, address, phone, website_url, address_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider, branch_code)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        address = COALESCE(NULLIF(EXCLUDED.address, ''), branches.address),
                        phone = COALESCE(NULLIF(EXCLUDED.phone, ''), branches.phone),
                        website_url = COALESCE(NULLIF(EXCLUDED.website_url, ''), branches.website_url),
                        updated_at = now()
                    RETURNING id
                    """,
                    (
                        PROVIDER,
                        branch_code,
                        clean_text(row.get("branch"))[:100],
                        clean_text(row.get("address")),
                        clean_text(row.get("phone")),
                        clean_text(row.get("website_url")) or BASE_URL,
                        "crawler" if row.get("address") else None,
                    ),
                )
                branch_ids[branch_code] = str(cur.fetchone()["id"])
            cur.execute(
                """
                INSERT INTO courses(
                    provider, provider_course_id, branch_id, title, target, category_raw,
                    collection_category, domain_category, source_group, operator_type, collection_type,
                    fee, schedule_raw, schedule_days, start_date, end_date, apply_period_raw,
                    capacity_total, capacity_current, capacity_remaining,
                    venue_name, venue_address, application_url, application_type, application_method_raw,
                    reservation_available, discovery_status, program_type, raw_fields,
                    status, raw_url, description, image_url, is_active, last_seen_at
                )
                VALUES (
                    %(provider)s, %(provider_course_id)s, %(branch_id)s, %(title)s, %(target)s, %(category_raw)s,
                    %(collection_category)s, %(domain_category)s, %(source_group)s, %(operator_type)s, %(collection_type)s,
                    %(fee)s, %(schedule_raw)s, %(schedule_days)s, %(start_date)s, %(end_date)s, %(apply_period_raw)s,
                    %(capacity_total)s, %(capacity_current)s, %(capacity_remaining)s,
                    %(venue_name)s, %(venue_address)s, %(application_url)s, %(application_type)s, %(application_method_raw)s,
                    %(reservation_available)s, %(discovery_status)s, %(program_type)s, %(raw_fields)s::jsonb,
                    %(status)s, %(raw_url)s, %(description)s, %(image_url)s, TRUE, now()
                )
                ON CONFLICT (provider, provider_course_id)
                DO UPDATE SET
                    branch_id = EXCLUDED.branch_id,
                    title = EXCLUDED.title,
                    target = EXCLUDED.target,
                    category_raw = EXCLUDED.category_raw,
                    collection_category = EXCLUDED.collection_category,
                    domain_category = EXCLUDED.domain_category,
                    source_group = EXCLUDED.source_group,
                    operator_type = EXCLUDED.operator_type,
                    collection_type = EXCLUDED.collection_type,
                    fee = EXCLUDED.fee,
                    schedule_raw = EXCLUDED.schedule_raw,
                    schedule_days = EXCLUDED.schedule_days,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    apply_period_raw = EXCLUDED.apply_period_raw,
                    capacity_total = EXCLUDED.capacity_total,
                    capacity_current = EXCLUDED.capacity_current,
                    capacity_remaining = EXCLUDED.capacity_remaining,
                    venue_name = EXCLUDED.venue_name,
                    venue_address = EXCLUDED.venue_address,
                    application_url = EXCLUDED.application_url,
                    application_type = EXCLUDED.application_type,
                    application_method_raw = EXCLUDED.application_method_raw,
                    reservation_available = EXCLUDED.reservation_available,
                    discovery_status = EXCLUDED.discovery_status,
                    program_type = EXCLUDED.program_type,
                    raw_fields = EXCLUDED.raw_fields,
                    status = EXCLUDED.status,
                    raw_url = EXCLUDED.raw_url,
                    description = EXCLUDED.description,
                    image_url = EXCLUDED.image_url,
                    is_active = TRUE,
                    last_seen_at = now()
                """,
                {
                    **row,
                    "branch_id": branch_ids[branch_code],
                    "category_raw": row.get("domain_category"),
                    "apply_period_raw": row.get("apply_period"),
                    "discovery_status": "cnall_lecture_cards+detail",
                    "raw_fields": json.dumps(
                        {
                            "fee_raw": row.get("fee_raw"),
                            "status_raw": row.get("status_raw"),
                            "period_raw": row.get("period"),
                            "region": row.get("region"),
                        },
                        ensure_ascii=False,
                    ),
                },
            )
            saved += 1
    return saved


def field_counts(rows: list[dict]) -> dict[str, int]:
    keys = ["title", "branch", "address", "period", "schedule_raw", "target", "fee_raw", "status", "description", "image_url", "application_url"]
    return {key: sum(1 for row in rows if clean_text(row.get(key))) for key in keys}


def print_report(rows: list[dict], meta: dict, saved: int) -> None:
    fields = field_counts(rows)
    print(f"provider={PROVIDER} rows={len(rows)} saved={saved} parser=cnall_lecture_cards+detail")
    print(
        "field_counts "
        f"title={fields['title']} "
        f"branch={fields['branch']} "
        f"raw_url={fields['application_url']} "
        f"period={fields['period']} "
        f"schedule_raw={fields['schedule_raw']} "
        f"fee_raw={fields['fee_raw']} "
        f"target={fields['target']} "
        f"status={fields['status']} "
        f"description={fields['description']} "
        f"image_url={fields['image_url']} "
        f"application_url={fields['application_url']}"
    )
    print("| provider | ok | rows | saved | pages | detail | parser | title | branch | address | period | schedule | fee | target | desc | image | apply |")
    print("| -------- | -- | ---- | ----- | ----- | ------ | ------ | ----- | ------ | ------- | ------ | -------- | --- | ------ | ---- | ----- | ----- |")
    print(
        f"| {PROVIDER} | {'Y' if rows else 'N'} | {len(rows)} | {saved} | {meta.get('pages', 0)} | {meta.get('detail_pages', 0)} | "
        f"cnall_lecture_cards+detail | {fields['title']} | {fields['branch']} | {fields['address']} | {fields['period']} | "
        f"{fields['schedule_raw']} | {fields['fee_raw']} | {fields['target']} | {fields['description']} | {fields['image_url']} | {fields['application_url']} |"
    )
    for row in rows[:5]:
        print(f"- {row.get('branch')} / {row.get('title')} / {row.get('period')} / {row.get('schedule_raw')} / {row.get('raw_url')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawl CNALL lecture portal.")
    parser.add_argument("--limit", "--per-target-limit", dest="limit", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--detail-limit", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    args = parser.parse_args()

    rows, meta = collect(args.limit, args.max_pages, args.detail_limit, args.timeout)
    saved = save_db(rows, skip_expired=not args.include_expired) if args.save_db else 0
    if args.save_db and args.mark_stale and saved:
        from DB.course_lifecycle import mark_stale_courses, utc_now

        mark_stale_courses(PROVIDER, utc_now())
    print_report(rows, meta, saved)
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
