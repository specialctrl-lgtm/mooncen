from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_JINCHEON_GO_KR_1CD1E7D2"
PROVIDER_NAME = "진천군평생학습관 읍면별 강좌"
BASE_URL = "https://www.jincheon.go.kr"
LIST_PATH = "/jclll/sub.do"
MENU_KEY = "3237"
DEFAULT_BRANCH = "진천군평생학습관"
DEFAULT_ADDRESS = "충청북도 진천군"
DEFAULT_PHONE = "043-539-3743"
SEARCH_EMD = "EMD5"
EMD_CODES = ["EMD1", "EMD2", "EMD09", "EMD3", "EMD4", "EMD5", "EMD6", "EMD7", "EMD8"]
LIST_URL = f"{BASE_URL}{LIST_PATH}?{urlencode({'menukey': MENU_KEY, 'mode': 'list', 'searchCnteduEmd': SEARCH_EMD, 'searchKrwd': ''})}"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_JincheonLifelong")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "identity",
            "Referer": LIST_URL,
        }
    )
    return session


def normalize_space(value: Any) -> str:
    text = clean_text(value).replace("\xa0", " ").replace("\r", " ").replace("\n", " ")
    return clean_text(re.sub(r"\s+", " ", text))


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def list_url(page: int = 1, emd_code: str = SEARCH_EMD) -> str:
    query = {
        "menukey": MENU_KEY,
        "pageUnit": "10",
        "searchCnd": "",
        "searchKrwd": "",
        "cnteduEclstNo": "0",
        "searchCnteduEmd": emd_code,
        "cnteduBgnde": "",
        "cnteduEndde": "",
        "pageIndex": str(page),
        "mode": "list",
    }
    return f"{BASE_URL}{LIST_PATH}?{urlencode(query)}"


def detail_url(cntedu_no: str) -> str:
    return f"{BASE_URL}{LIST_PATH}?{urlencode({'menukey': MENU_KEY, 'mode': 'view', 'cnteduNo': cntedu_no})}"


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def normalize_date_text(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(
        r"(\d{4})[.](\d{1,2})[.](\d{1,2})\.?",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"(\d{1,2})\s*:\s*(\d{1,2})", lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", text)
    text = re.sub(r"\s*[~∼]\s*", " ~ ", text)
    return normalize_space(text)


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text:
        return "무료"
    amount = extract_krw_amount(text)
    if amount is not None:
        return f"{amount:,}원"
    return text


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["교육종료", "신청마감", "마감", "종료", "폐강", "취소"]):
        return "CLOSED"
    if any(token in text for token in ["접수예정", "대기"]):
        return "SCHEDULED"
    if any(token in text for token in ["온라인신청", "신청하기", "접수중"]):
        return "OPEN"
    return text or "OPEN"


def extract_cntedu_no(href: Any) -> str:
    match = re.search(r"cnteduNo=(\d+)", normalize_space(href))
    return match.group(1) if match else ""


def labeled_items(container: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in container.select("li"):
        key_node = item.select_one("strong")
        key = normalize_space(key_node.get_text(" ", strip=True) if key_node else "").rstrip(":")
        if not key:
            continue
        value = normalize_space(item.get_text(" ", strip=True))
        value = re.sub(rf"^{re.escape(key)}\s*:?", "", value).strip()
        pairs[key] = normalize_space(value)
    return pairs


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", text)]
    if len(nums) >= 2:
        return nums[0], nums[1], None
    if len(nums) == 1:
        return nums[0], None, None
    return None, None, None


def split_fee_material(value: Any) -> tuple[str, str]:
    text = normalize_space(value)
    if "/" in text:
        fee, material = [normalize_space(part) for part in text.split("/", 1)]
        return normalize_fee(fee), material
    return normalize_fee(text), ""


def address_from_place(place: str, detail_text: str = "") -> str:
    text = normalize_space(f"{place} {detail_text}")
    map_match = re.search(r'innerMap\("([^"]+)"', detail_text)
    if map_match:
        address = normalize_space(map_match.group(1))
        if address.startswith("충북"):
            return address.replace("충북", "충청북도", 1)
        return address
    paren_values = re.findall(r"\(([^)]*(?:로|길|읍|면|동|군)[^)]*)\)", text)
    if paren_values:
        address = normalize_space(paren_values[-1])
        if address.startswith("충북"):
            return address.replace("충북", "충청북도", 1)
        if not address.startswith(("충청북도", "진천군")):
            address = f"충청북도 진천군 {address}"
        elif address.startswith("진천군"):
            address = f"충청북도 {address}"
        return address
    return DEFAULT_ADDRESS


def branch_from_place(place: str, institution: str) -> str:
    text = normalize_space(place)
    if text:
        base = text.split("(", 1)[0].strip()
        base = re.sub(r"^☏", "", base)
        base = re.sub(r"\b0\d{1,2}-\d{3,4}-\d{4}\b", "", base)
        base = re.sub(r"생거진천평생학습관-\d+", "생거진천평생학습관", base)
        base = re.sub(r"진천군평생학습관-\d+", "진천군평생학습관", base)
        base = base.split("/", 1)[0].strip()
        base = re.sub(r"\s*(강의실|체험실|다목적실)\s*\d*호?.*$", "", base).strip()
        if base:
            return base
    inst = re.sub(r"^☏", "", normalize_space(institution))
    inst = re.sub(r"\([^)]*\)", "", inst).strip()
    inst = re.sub(r"생거진천평생학습관-\d+", "생거진천평생학습관", inst)
    inst = re.sub(r"진천군평생학습관-\d+", "진천군평생학습관", inst)
    return inst or DEFAULT_BRANCH


def detail_sections(soup: BeautifulSoup) -> str:
    sections: list[str] = []
    headings = soup.select("h4.conH3")
    for heading in headings:
        title = normalize_space(heading.get_text(" ", strip=True))
        box = heading.find_next_sibling("div", class_="conBox")
        text = normalize_space(box.get_text(" ", strip=True) if box else "")
        if title and text:
            sections.append(f"{title}: {text}")
    return "\n".join(sections)


def parse_list_item(item: Tag) -> dict[str, Any] | None:
    title_node = item.select_one("a.tit[href*='cnteduNo']")
    title = normalize_space(title_node.get_text(" ", strip=True) if title_node else "")
    cntedu_no = extract_cntedu_no(title_node.get("href") if title_node else "")
    if not title or not cntedu_no:
        return None
    pairs = labeled_items(item)
    fee, material = split_fee_material(pairs.get("교육비/재료비"))
    current, total, waitlist = parse_capacity(pairs.get("신청/정원"))
    place = normalize_space(pairs.get("교육장소"))
    institution = normalize_space(pairs.get("운영기관"))
    status_text = normalize_space(item.select_one(".btn_edu").get_text(" ", strip=True) if item.select_one(".btn_edu") else "")
    period = normalize_date_text(pairs.get("교육기간"))
    schedule_detail = normalize_date_text(pairs.get("교육요일/교육시간"))
    branch = branch_from_place(place, institution)
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": cntedu_no,
        "provider_course_id": cntedu_no,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": address_from_place(place),
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": normalize_space(" ".join(part for part in [period, schedule_detail] if part)),
        "target": normalize_space(pairs.get("교육대상")),
        "age_group": "ADULT" if any(token in normalize_space(pairs.get("교육대상")) for token in ["성인", "어르신"]) else "",
        "category_raw": "읍면별 강좌",
        "fee": fee,
        "material_fee": extract_material_fee_amount(material),
        "material_note": material,
        "status": normalize_status(status_text),
        "raw_url": detail_url(cntedu_no),
        "application_url": detail_url(cntedu_no),
        "application_type": "ONLINE" if "온라인신청" in status_text else "",
        "description": normalize_space(item.get_text(" ", strip=True)),
        "image_url": "",
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": waitlist,
        "apply_period": normalize_date_text(pairs.get("접수기간")),
        "venue_name": place,
        "room": place,
        "collection_category": "교육·체험",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_html",
        "program_type": "OFFLINE",
        "raw_fields": {"list_pairs": pairs, "parser": "jincheon_lifelong_card_detail"},
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        soup = fetch_soup(session, str(row["raw_url"]), timeout)
    except requests.RequestException as exc:
        logger.warning("%s detail failed: %s", row.get("raw_url"), exc)
        return row
    row = dict(row)
    main = soup.select_one(".bbs_edu_view")
    if main:
        pairs = labeled_items(main)
        fee = normalize_fee(pairs.get("교육비") or row.get("fee"))
        material = normalize_space(pairs.get("재료비") or row.get("material_note"))
        place = normalize_space(pairs.get("교육장소") or row.get("venue_name"))
        institution = normalize_space(pairs.get("운영기관") or "")
        period = normalize_date_text(pairs.get("교육기간") or row.get("period"))
        schedule_detail = normalize_date_text(pairs.get("교육요일/교육시간") or "")
        branch = branch_from_place(place, institution)
        row.update(
            {
                "branch": branch,
                "branch_code": branch_code(branch),
                "period": period,
                "schedule_raw": normalize_space(" ".join(part for part in [period, schedule_detail] if part)),
                "target": normalize_space(pairs.get("교육대상") or row.get("target")),
                "fee": fee,
                "material_note": material,
                "material_fee": extract_material_fee_amount(material),
                "venue_name": place,
                "room": place,
                "apply_period": normalize_date_text(pairs.get("접수기간") or row.get("apply_period")),
            }
        )
        current, total, waitlist = parse_capacity(pairs.get("신청/정원") or "")
        row["capacity_current"] = current or row.get("capacity_current")
        row["capacity_total"] = total or row.get("capacity_total")
        row["waitlist_total"] = waitlist or row.get("waitlist_total")
    sections = detail_sections(soup)
    page_text = soup.get_text("\n", strip=True)
    row["address"] = address_from_place(row.get("venue_name", ""), page_text)
    if sections:
        row["description"] = sections
    phone_match = re.search(r"(?:전화번호|문의).*?(0\d{1,2}-\d{3,4}-\d{4})", page_text)
    if phone_match:
        row["phone"] = phone_match.group(1)
    return row


def is_expired(row: dict[str, Any]) -> bool:
    _start, end = parse_date_range(row.get("period"))
    return bool(end and end < date.today())


def collect(limit: int | None = None, max_pages: int = 20, timeout: int = 20, include_expired: bool = False) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for emd_code in EMD_CODES:
        for page in range(1, max_pages + 1):
            soup = fetch_soup(session, list_url(page, emd_code), timeout)
            items = soup.select("ul.tb_edu > li.item")
            logger.info("%s emd=%s page=%s items=%s", PROVIDER, emd_code, page, len(items))
            if not items:
                break
            page_added = 0
            for item in items:
                row = parse_list_item(item)
                if not row:
                    continue
                row.setdefault("raw_fields", {})["emd_code"] = emd_code
                key = normalize_space(row.get("provider_course_id"))
                if key in seen:
                    continue
                seen.add(key)
                row = enrich_detail(session, row, timeout)
                if not include_expired and is_expired(row):
                    continue
                rows.append(row)
                page_added += 1
                if limit and len(rows) >= limit:
                    return rows
            if page_added == 0 and page > 5:
                break
    return rows


def quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "raw_url"]
    counts = {field: sum(1 for row in rows if normalize_space(row.get(field))) for field in fields}
    score = round((sum(counts.values()) / (len(fields) * max(1, len(rows)))) * 100, 1)
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    return {
        "provider": PROVIDER,
        "collected": len(rows),
        "score": score,
        "grade": grade,
        "field_counts": counts,
        "sample_titles": [row.get("title") for row in rows[:5]],
    }


def save_branch_with_address(row: dict[str, Any]) -> str:
    branch = {
        "provider": PROVIDER,
        "branch_code": (normalize_space(row.get("branch_code")) or branch_code(row.get("branch")))[:50],
        "name": (normalize_space(row.get("branch")) or DEFAULT_BRANCH)[:100],
        "address": normalize_space(row.get("address") or DEFAULT_ADDRESS),
        "phone": normalize_space(row.get("phone") or DEFAULT_PHONE),
        "website_url": LIST_URL,
        "address_source": "crawler_detail_map",
    }
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO branches (provider, branch_code, name, address, phone, website_url, address_source)
            VALUES (%(provider)s, %(branch_code)s, %(name)s, %(address)s, %(phone)s, %(website_url)s, %(address_source)s)
            ON CONFLICT (provider, branch_code)
            DO UPDATE SET
                name = EXCLUDED.name,
                address = COALESCE(NULLIF(EXCLUDED.address, ''), branches.address),
                phone = COALESCE(NULLIF(EXCLUDED.phone, ''), branches.phone),
                website_url = EXCLUDED.website_url,
                address_source = COALESCE(NULLIF(EXCLUDED.address_source, ''), branches.address_source),
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            branch,
        )
        return str(cursor.fetchone()["id"])


def save_rows(rows: list[dict[str, Any]]) -> int:
    crawler = YamlSourceCrawler.__new__(YamlSourceCrawler)
    crawler.provider = PROVIDER
    crawler.target_parser = TargetParser()
    crawler.schedule_parser = ScheduleParser()
    saved = 0
    branch_ids: dict[str, str] = {}
    for row in rows:
        code = normalize_space(row.get("branch_code")) or branch_code(row.get("branch"))
        if code not in branch_ids:
            branch_ids[code] = save_branch_with_address(row)
        course = crawler.normalize_course(row, branch_ids[code])
        if crawler.save_course(course):
            saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROVIDER_NAME} crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    parser.add_argument("--no-detail", action="store_true")
    args = parser.parse_args()

    rows = collect(
        limit=args.limit or args.per_target_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
    )
    saved = save_rows(rows) if args.save_db else 0
    report = quality_report(rows)
    report["saved"] = saved
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for row in rows[: min(10, len(rows))]:
        print(
            "SAMPLE\t{title}\t{branch}\t{period}\t{schedule}\t{target}\t{fee}\t{status}".format(
                title=row.get("title", ""),
                branch=row.get("branch", ""),
                period=row.get("period", ""),
                schedule=row.get("schedule_raw", ""),
                target=row.get("target", ""),
                fee=row.get("fee", ""),
                status=row.get("status", ""),
            )
        )
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
