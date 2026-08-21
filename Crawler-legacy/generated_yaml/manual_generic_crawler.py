from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
import urllib3
import yaml
from bs4 import BeautifulSoup, Tag


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
    try:
        response = s.get(url, timeout=timeout)
    except requests.exceptions.SSLError:
        try:
            response = s.get(url, timeout=timeout, verify=False)
        except requests.exceptions.SSLError:
            if url.startswith("https://"):
                response = s.get("http://" + url[len("https://") :], timeout=timeout)
            else:
                raise
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def is_pagination_terminal_error(error: requests.exceptions.HTTPError) -> bool:
    response = getattr(error, "response", None)
    return bool(response is not None and response.status_code in {400, 404})


def parse_money(value: object) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    if "??" in text or "???" in text:
        return 0
    match = re.search(r"\d[\d,]*", text)
    return int(match.group(0).replace(",", "")) if match else None


def parse_date(value: str) -> str | None:
    match = re.search(r"(\d{4})[.년/\- ]+(\d{1,2})[.월/\- ]+(\d{1,2})", clean_text(value))
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def parse_date_range(value: str) -> tuple[str | None, str | None]:
    dates = re.findall(r"\d{4}[.년/\- ]+\d{1,2}[.월/\- ]+\d{1,2}", clean_text(value))
    parsed = [parse_date(item) for item in dates]
    parsed = [item for item in parsed if item]
    if not parsed:
        return None, None
    return parsed[0], parsed[-1]


def normalize_status(value: str) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if any(token in text for token in ("접수중", "예약가능", "신청가능", "모집중")) and "예정" not in text:
        return "OPEN"
    if any(token in text for token in ("예정", "준비")):
        return "SCHEDULED"
    if "대기" in text:
        return "WAITING"
    if any(token in text for token in ("마감", "종료", "완료", "폐강")):
        return "CLOSED"
    return "SCHEDULED"


def provider_meta(provider: str) -> dict:
    registry = ROOT / "config" / "generated_yaml_crawler_registry.yaml"
    if registry.exists():
        data = yaml.safe_load(registry.read_text(encoding="utf-8", errors="ignore")) or {}
        for row in data.get("targets") or []:
            if isinstance(row, dict) and clean_text(row.get("provider")) == provider:
                return row
    for path in (ROOT / "config" / "crawl_targets").glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="ignore")) or []
        targets = data.get("targets") if isinstance(data, dict) else data
        for row in targets or []:
            if isinstance(row, dict) and clean_text(row.get("provider")) == provider:
                return row
    return {"provider": provider, "name": provider, "url": ""}


def make_page_url(url: str, page: int) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("pageIndex", "currentPageNo", "page", "pageNo", "pageNum", "cPage"):
        if key in qs:
            qs[key] = [str(page)]
            return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))
    if page <= 1:
        return url
    qs["pageIndex"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


def score_anchor(text: str, href: str) -> int:
    score = 0
    if re.search(r"(교육|강좌|체험|프로그램|예약|신청|해설|탐방)", text):
        score += 4
    if re.search(r"(detail|view|read|select|edu|expr|lect|program|course|reservation|bbs)", href, re.I):
        score += 3
    if re.search(r"(로그인|회원|개인정보|사이트맵|목록|검색|첨부|다운로드)", text):
        score -= 5
    if len(text) < 4 or len(text) > 160:
        score -= 2
    return score


def candidate_items(soup: BeautifulSoup, base_url: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    containers = soup.select(
        ".lecture-list > li, .program-list > li, .edu-list > li, .board-list > li, "
        ".reserve-list > li, .list-body > li, tbody tr, .card, .program, .item"
    )
    for container in containers:
        if not isinstance(container, Tag):
            continue
        if container.find_parent(["header", "nav", "footer"]):
            continue
        if container.find_parent(id=re.compile(r"(header|gnb|lnb|footer|menu|nav)", re.I)):
            continue
        if container.find_parent(class_=re.compile(r"(header|gnb|lnb|footer|menu|nav|sitemap)", re.I)):
            continue
        anchors = container.select("a[href]")
        best = None
        best_score = -99
        for anchor in anchors:
            text = clean_text(anchor.get_text(" ", strip=True))
            href = anchor.get("href") or ""
            if href.startswith("#"):
                continue
            if href.startswith("javascript") and not re.search(r"(detail|view|select|fnDetail|goView|moveView|edu)", href, re.I):
                continue
            score = score_anchor(text, href)
            if score > best_score:
                best = anchor
                best_score = score
        if not best or best_score < 0:
            continue
        title = clean_text(best.get_text(" ", strip=True)) or clean_text(container.get_text(" ", strip=True))[:120]
        url = urljoin(base_url, best.get("href") or "")
        key = f"{title}|{url}"
        if key in seen:
            continue
        seen.add(key)
        text = clean_text(container.get_text(" ", strip=True))
        rows.append({"title": title, "raw_url": url, "container_text": text})
    return rows


def pairs_from_detail(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("tr"):
        headers = [clean_text(x.get_text(" ", strip=True)) for x in tr.find_all("th")]
        cells = [clean_text(x.get_text(" ", strip=True)) for x in tr.find_all("td")]
        for i, key in enumerate(headers):
            if key and i < len(cells):
                pairs[key] = cells[i]
    for dl in soup.select("dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if dt and dd:
            pairs[clean_text(dt.get_text(" ", strip=True))] = clean_text(dd.get_text(" ", strip=True))
    return pairs


def value_by_keywords(pairs: dict[str, str], *keywords: str) -> str:
    for key, value in pairs.items():
        if any(keyword in key for keyword in keywords):
            return clean_text(value)
    return ""


def infer_from_text(text: str) -> dict:
    period = ""
    date_match = re.search(r"\d{4}[.년/\- ]+\d{1,2}[.월/\- ]+\d{1,2}.*?(?:~|-|부터|까지).*?\d{4}[.년/\- ]+\d{1,2}[.월/\- ]+\d{1,2}", text)
    if date_match:
        period = clean_text(date_match.group(0))
    elif re.search(r"\d{4}[.년/\- ]+\d{1,2}[.월/\- ]+\d{1,2}", text):
        period = clean_text(re.search(r"\d{4}[.년/\- ]+\d{1,2}[.월/\- ]+\d{1,2}", text).group(0))
    time_match = re.search(r"[월화수목금토일, ]*\s*\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}", text)
    fee_match = re.search(r"(무료|수강료\s*[:：]?\s*[\d,]+원|참가비\s*[:：]?\s*[\d,]+원|[\d,]+원)", text)
    target_match = re.search(r"(유아|초등|중등|고등|청소년|성인|가족|누구나|어린이|학생)[^,./|]{0,30}", text)
    return {
        "period": period,
        "schedule_raw": clean_text(time_match.group(0)) if time_match else "",
        "fee_raw": clean_text(fee_match.group(0)) if fee_match else "",
        "target": clean_text(target_match.group(0)) if target_match else "",
    }


def enrich_detail(s: requests.Session, row: dict, timeout: int) -> None:
    soup = fetch_soup(s, row["raw_url"], timeout)
    pairs = pairs_from_detail(soup)
    body = clean_text(soup.get_text(" ", strip=True))
    inferred = infer_from_text(body)
    period = value_by_keywords(pairs, "기간", "일시", "교육일", "운영일") or inferred["period"]
    start_date, end_date = parse_date_range(period)
    row.update(
        {
            "period": period,
            "start_date": start_date,
            "end_date": end_date,
            "schedule_raw": value_by_keywords(pairs, "시간", "요일") or inferred["schedule_raw"],
            "target": value_by_keywords(pairs, "대상") or inferred["target"],
            "fee_raw": value_by_keywords(pairs, "수강료", "참가비", "이용료", "금액") or inferred["fee_raw"],
            "status_raw": value_by_keywords(pairs, "상태", "접수", "모집"),
            "description": body[:3000],
        }
    )
    row["fee"] = parse_money(row.get("fee_raw"))
    row["status"] = normalize_status(row.get("status_raw") or body)
    image = soup.select_one("meta[property='og:image']")
    if image and image.get("content"):
        row["image_url"] = urljoin(row["raw_url"], image["content"])
    else:
        for img in soup.select("img[src]"):
            src = img.get("src") or ""
            if re.search(r"(logo|icon|btn|mark|banner)", src, re.I):
                continue
            row["image_url"] = urljoin(row["raw_url"], src)
            break


def collect(provider: str, limit: int, max_pages: int, detail_limit: int, timeout: int) -> tuple[list[dict], dict]:
    meta = provider_meta(provider)
    status = clean_text(meta.get("crawler_status") or meta.get("status")).lower()
    if status == "blocked":
        return [], {
            "error": f"blocked:{clean_text(meta.get('blocked_reason'))}",
            "pages": 0,
            "detail_pages": 0,
            "parser": "blocked",
        }
    url = clean_text(meta.get("url"))
    if not url:
        return [], {"error": "missing_url"}
    s = session()
    rows: list[dict] = []
    details = 0
    for page in range(1, max_pages + 1):
        page_url = make_page_url(url, page)
        try:
            soup = fetch_soup(s, page_url, timeout)
        except requests.exceptions.HTTPError as error:
            if page > 1 and is_pagination_terminal_error(error):
                break
            raise
        items = candidate_items(soup, page_url)
        if not items:
            if page == 1:
                single = {
                    "title": clean_text(soup.title.get_text(" ", strip=True)) if soup.title else clean_text(meta.get("name") or provider),
                    "raw_url": page_url,
                    "container_text": clean_text(soup.get_text(" ", strip=True)),
                }
                items = [single]
            else:
                break
        for item in items:
            text = item.pop("container_text", "")
            inferred = infer_from_text(text)
            row = {
                "provider": provider,
                "provider_course_id": stable_id(provider, item["raw_url"], item["title"]),
                "title": item["title"],
                "branch": clean_text(meta.get("name") or provider)[:100],
                "branch_code": f"manual_{stable_id(provider, meta.get('url'))[:16]}",
                "address": "",
                "raw_url": item["raw_url"],
                "application_url": item["raw_url"],
                "application_type": "ONLINE_RESERVATION",
                "reservation_available": True,
                "period": inferred["period"],
                "start_date": parse_date_range(inferred["period"])[0],
                "end_date": parse_date_range(inferred["period"])[1],
                "schedule_raw": inferred["schedule_raw"],
                "target": inferred["target"],
                "fee_raw": inferred["fee_raw"],
                "fee": parse_money(inferred["fee_raw"]),
                "status": None,
                "status_raw": "",
                "description": "",
                "image_url": "",
                "venue_name": clean_text(meta.get("name") or provider)[:150],
                "venue_address": "",
                "collection_category": clean_text(meta.get("collection_category") or "OTHER"),
                "domain_category": clean_text(meta.get("category") or meta.get("domain_category") or "기타"),
                "source_group": "manual_target",
                "operator_type": "public",
                "collection_type": "manual_generic",
                "program_type": "강좌",
            }
            if detail_limit <= 0 or details < detail_limit:
                try:
                    enrich_detail(s, row, timeout)
                    details += 1
                except Exception:
                    pass
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows, {"pages": page, "detail_pages": details, "parser": "manual_generic"}
    return rows, {"pages": max_pages, "detail_pages": details, "parser": "manual_generic"}


def save_db(rows: list[dict], skip_expired: bool = True) -> int:
    if not rows:
        return 0
    from DB.db_utils import get_db_cursor

    today = date.today().isoformat()
    saved = 0
    branch_ids: dict[tuple[str, str], str] = {}
    with get_db_cursor() as cur:
        for row in rows:
            if skip_expired and row.get("end_date") and row["end_date"] < today:
                continue
            bkey = (row["provider"], row["branch_code"])
            if bkey not in branch_ids:
                cur.execute(
                    """
                    INSERT INTO branches(provider, branch_code, name, address, website_url, address_source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider, branch_code)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        address = COALESCE(NULLIF(EXCLUDED.address, ''), branches.address),
                        website_url = EXCLUDED.website_url,
                        updated_at = now()
                    RETURNING id
                    """,
                    (row["provider"], row["branch_code"], row["branch"][:100], row.get("address") or "", row["raw_url"], "crawler" if row.get("address") else None),
                )
                branch_ids[bkey] = str(cur.fetchone()["id"])
            cur.execute(
                """
                INSERT INTO courses(
                    provider, provider_course_id, branch_id, title, target, category_raw,
                    collection_category, domain_category, source_group, operator_type, collection_type,
                    fee, schedule_raw, start_date, end_date, venue_name, venue_address,
                    application_url, application_type, reservation_available, discovery_status,
                    program_type, raw_fields, status, raw_url, description, image_url,
                    is_active, last_seen_at
                )
                VALUES (
                    %(provider)s, %(provider_course_id)s, %(branch_id)s, %(title)s, %(target)s, %(category_raw)s,
                    %(collection_category)s, %(domain_category)s, %(source_group)s, %(operator_type)s, %(collection_type)s,
                    %(fee)s, %(schedule_raw)s, %(start_date)s, %(end_date)s, %(venue_name)s, %(venue_address)s,
                    %(application_url)s, %(application_type)s, %(reservation_available)s, %(discovery_status)s,
                    %(program_type)s, %(raw_fields)s::jsonb, %(status)s, %(raw_url)s, %(description)s, %(image_url)s,
                    TRUE, now()
                )
                ON CONFLICT (provider, provider_course_id)
                DO UPDATE SET
                    branch_id = EXCLUDED.branch_id,
                    title = EXCLUDED.title,
                    target = EXCLUDED.target,
                    category_raw = EXCLUDED.category_raw,
                    fee = EXCLUDED.fee,
                    schedule_raw = EXCLUDED.schedule_raw,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    venue_name = EXCLUDED.venue_name,
                    venue_address = EXCLUDED.venue_address,
                    application_url = EXCLUDED.application_url,
                    application_type = EXCLUDED.application_type,
                    reservation_available = EXCLUDED.reservation_available,
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
                    "branch_id": branch_ids[bkey],
                    "category_raw": row.get("domain_category"),
                    "discovery_status": "manual_generic",
                    "raw_fields": json.dumps({"fee_raw": row.get("fee_raw"), "status_raw": row.get("status_raw"), "period_raw": row.get("period")}, ensure_ascii=False),
                },
            )
            saved += 1
    return saved


def field_counts(rows: list[dict]) -> dict[str, int]:
    keys = ["title", "branch", "period", "schedule_raw", "target", "fee_raw", "status", "description", "image_url", "application_url"]
    return {key: sum(1 for row in rows if clean_text(row.get(key))) for key in keys}


def run_cli(provider: str) -> int:
    parser = argparse.ArgumentParser(description=f"Manual generic crawler for {provider}")
    parser.add_argument("--limit", "--per-target-limit", dest="limit", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--detail-limit", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    args = parser.parse_args()
    rows, meta = collect(provider, args.limit, args.max_pages, args.detail_limit, args.timeout)
    saved = save_db(rows, skip_expired=not args.include_expired) if args.save_db else 0
    if args.save_db and args.mark_stale and saved:
        from DB.course_lifecycle import mark_stale_courses, utc_now

        mark_stale_courses(provider, utc_now())
    fields = field_counts(rows)
    print(f"provider={provider} rows={len(rows)} saved={saved} parser={meta.get('parser')} pages={meta.get('pages')} detail={meta.get('detail_pages')}")
    print("field_counts " + " ".join(f"{k}={v}" for k, v in fields.items()))
    for row in rows[:5]:
        print(f"- {row.get('title')} / {row.get('period') or '-'} / {row.get('schedule_raw') or '-'} / {row.get('raw_url')}")
    if meta.get("parser") == "blocked":
        return 0
    return 0 if rows else 2
