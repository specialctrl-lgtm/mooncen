#!/usr/bin/env python3
"""
fill_missing_branch_addresses.py
---------------------------------
Fills missing branch addresses in PostgreSQL database using Kakao Local Keyword Search API.
Features:
  - Strong region/locality verification to avoid cross-region false positives
  - Smart queries for retail culture centers and municipal platforms
  - Full support for updating branches (address, lat, lon, geocode_status='resolved', etc.)
  - Clear reporting of unresolved branches with their course counts and URLs
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
import requests
import psycopg2

def load_kakao_key() -> str:
    key = os.getenv("KAKAO_MAPS_REST_API_KEY", "")
    if not key and os.path.exists("/etc/mooncen/crawler.env"):
        with open("/etc/mooncen/crawler.env") as f:
            for line in f:
                if line.startswith("KAKAO_MAPS_REST_API_KEY="):
                    key = line.strip().split("=", 1)[1].strip("\"'")
    return key

PROVIDER_REGION_HINTS: list[tuple[str, str, str]] = [
    ("SEOUL", "서울특별시", "서울"),
    ("BUSAN", "부산광역시", "부산"),
    ("DAEGU", "대구광역시", "대구"),
    ("INCHEON", "인천광역시", "인천"),
    ("GWANGJU", "광주광역시", "광주"),
    ("DAEJEON", "대전광역시", "대전"),
    ("ULSAN", "울산광역시", "울산"),
    ("SEJONG", "세종특별자치시", "세종"),
    ("JEJU", "제주특별자치도", "제주"),
    ("JJE", "제주특별자치도", "제주"),
    ("YONGIN", "경기도", "용인시"),
    ("YIYF", "경기도", "용인시"),
    ("SEONGNAM", "경기도", "성남시"),
    ("GURI", "경기도", "구리시"),
    ("SUWON", "경기도", "수원시"),
    ("ANYANG", "경기도", "안양시"),
    ("ASAN", "충청남도", "아산시"),
    ("PAJU", "경기도", "파주시"),
    ("GJCITY", "경기도", "광주시"),
    ("GJCF", "광주광역시", "광주"),
    ("GWANGJIN", "서울특별시", "광진구"),
    ("SEOCHO", "서울특별시", "서초구"),
    ("SONGPA", "서울특별시", "송파구"),
    ("NOWON", "서울특별시", "노원구"),
    ("GURO", "서울특별시", "구로구"),
    ("YANGCHEON", "서울특별시", "양천구"),
    ("YEONSU", "인천광역시", "연수구"),
    ("GYEYANG", "인천광역시", "계양구"),
    ("SEOHAE", "인천광역시", "서구"),
    ("HANAM", "경기도", "하남시"),
    ("GIMHAE", "경상남도", "김해시"),
    ("ANSEONG", "경기도", "안성시"),
    ("HAEUNDAE", "부산광역시", "해운대구"),
    ("BSSEOGU", "부산광역시", "서구"),
    ("YEONJE", "부산광역시", "연제구"),
    ("DONGGU", "부산광역시", "동구"),
    ("GEUMSAN", "충청남도", "금산군"),
    ("CWG", "강원특별자치도", "철원군"),
    ("INJE", "강원특별자치도", "인제군"),
    ("SAMCHEOK", "강원특별자치도", "삼척시"),
    ("MOKPO", "전라남도", "목포시"),
    ("SANCHEONG", "경상남도", "산청군"),
    ("HSCITY", "경기도", "화성시"),
    ("HADONG", "경상남도", "하동군"),
    ("DAEDEOK", "대전광역시", "대덕구"),
    ("YANGJU", "경기도", "양주시"),
    ("GONGJU", "충청남도", "공주시"),
    ("DANGJIN", "충청남도", "당진시"),
    ("POHANG", "경상북도", "포항시"),
    ("SEOSAN", "충청남도", "서산시"),
    ("JNE", "전라남도", ""),
    ("MIRYANG", "경상남도", "밀양시"),
    ("HAMAN", "경상남도", "함안군"),
    ("BOKJI", "서울특별시", ""),
]

def get_region_expectation(provider: str, sido: str, sigungu: str) -> tuple[str, str]:
    exp_sido = sido.strip()
    exp_sigungu = sigungu.strip()
    
    # If already in branch record, return normalized
    if exp_sido:
        return exp_sido, exp_sigungu
        
    prov_upper = provider.upper()
    for token, s, sg in PROVIDER_REGION_HINTS:
        if token in prov_upper:
            return s, sg or exp_sigungu

    return exp_sido, exp_sigungu

def clean_branch_name_for_search(name: str) -> tuple[str, str]:
    # Extract embedded address if in parentheses, e.g. "신중년더채움학습관(금정로 29-6)"
    embedded_addr = ""
    m = re.search(r"\(([^)]*(?:로|길|동|리)\s*\d+[^)]*)\)", name)
    if m:
        embedded_addr = m.group(1).strip()

    # Strip room numbers and common prefixes/suffixes
    s = name
    s = s.replace("전남광주통합특별시", "전라남도")
    s = re.sub(r"\([^)]*(?:실|호|층|강당|룸|관|교실|방|체육|축구|야구|테니스|수영)[^)]*\)", "", s)
    s = re.sub(r"\s+\d+층(?:\s*\d+호)?", "", s)
    s = re.sub(r"\s+(?:제?\d+호실?|(?:대|소)?강당|세미나실|다목적실)$", "", s)
    s = s.strip()
    return s, embedded_addr

def build_queries(provider: str, name: str, sido: str, sigungu: str) -> tuple[list[str], str, str]:
    exp_sido, exp_sigungu = get_region_expectation(provider, sido, sigungu)
    clean_name, embedded_addr = clean_branch_name_for_search(name)
    queries = []

    locality = f"{exp_sido} {exp_sigungu}".strip()

    if embedded_addr:
        if locality:
            queries.append(f"{locality} {embedded_addr}")
        queries.append(embedded_addr)

    if provider == "LOTTE":
        sub_name = clean_name.replace("롯데문화센터", "").replace("롯데백화점", "").strip()
        if "타임빌라스" in clean_name:
            queries.append(clean_name)
            queries.append(f"롯데백화점 {sub_name}")
        else:
            queries.append(f"롯데백화점 {sub_name}")
            queries.append(f"롯데몰 {sub_name}")
            queries.append(f"롯데문화센터 {sub_name}")
    elif provider == "SHINSEGAE_ACADEMY":
        sub_name = clean_name.replace("신세계백화점", "").replace("신세계아카데미", "").replace("& ON", "").strip()
        if "사우스시티" in sub_name:
            queries.append("신세계 사우스시티")
            queries.append("신세계백화점 경기점")
        elif "스타필드" in sub_name:
            queries.append(f"스타필드 {sub_name}")
        else:
            queries.append(f"신세계백화점 {sub_name}")
            queries.append(f"신세계 {sub_name}")
    elif provider == "LOTTE_MART":
        sub_name = clean_name.replace("MAXX", "맥스 ").replace("롯데마트", "").strip()
        queries.append(f"롯데마트 맥스 {sub_name}")
        queries.append(f"롯데마트 {sub_name}")
    elif provider == "EMART":
        sub_name = clean_name.replace("스타필드시티", "스타필드시티 ").replace("이마트", "").strip()
        queries.append(f"스타필드시티 {sub_name}")
        queries.append(f"이마트 {sub_name}")
    elif provider == "ELAND_RETAIL":
        sub_name = clean_name.replace("이랜드", "").replace("NC", "").replace("뉴코아", "").strip()
        queries.append(f"NC백화점 {sub_name}")
        queries.append(f"뉴코아아울렛 {sub_name}")
        queries.append(f"2001아울렛 {sub_name}")
    elif provider == "NATIONAL_FOREST_EDUCATION_CENTER":
        queries.append(clean_name)
        queries.append(f"국립 {clean_name}")
    elif "YIYF" in provider.upper():
        queries.append(f"용인시 {clean_name}")
        queries.append(clean_name)
    else:
        # Municipal providers
        if locality:
            queries.append(f"{locality} {clean_name}")
        if exp_sigungu and exp_sigungu not in clean_name:
            queries.append(f"{exp_sigungu} {clean_name}")
        if "노원구립" in clean_name:
            queries.append(clean_name.replace("노원구립", "").strip())
        queries.append(clean_name)

    seen = set()
    deduped = []
    for q in queries:
        q_strip = q.strip()
        if q_strip and q_strip not in seen:
            seen.add(q_strip)
            deduped.append(q_strip)

    return deduped, exp_sido, exp_sigungu

def address_matches_expected_region(addr: str, exp_sido: str, exp_sigungu: str) -> bool:
    if not exp_sido and not exp_sigungu:
        return True
    
    # Check sido
    if exp_sido:
        sido_prefix = exp_sido[:2] # e.g. 서울, 부산, 대구, 인천, 경기, 강원, 충남, 충북, 전남, 전북, 경남, 경북, 제주, 세종
        if not addr.startswith(sido_prefix):
            return False

    # Check sigungu if provided
    if exp_sigungu:
        sg_core = exp_sigungu.replace("시", "").replace("군", "").replace("구", "").strip()
        if len(sg_core) >= 2 and sg_core not in addr:
            return False

    return True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max branches to process (0 = all)")
    parser.add_argument("--apply", action="store_true", help="Apply updates to database")
    args = parser.parse_args()

    kakao_key = load_kakao_key()
    if not kakao_key:
        print("ERROR: Kakao REST API key not found in environment or /etc/mooncen/crawler.env")
        sys.exit(1)

    headers = {"Authorization": f"KakaoAK {kakao_key}"}
    conn = psycopg2.connect(dbname="mooncen")
    cur = conn.cursor()

    limit_sql = f"LIMIT {args.limit}" if args.limit > 0 else ""
    sql = f"""
        SELECT 
            b.id,
            b.provider,
            b.branch_code,
            b.name,
            COALESCE(b.region_sido, ''),
            COALESCE(b.region_sigungu, ''),
            b.website_url,
            COUNT(c.id) AS course_count,
            (
                SELECT c2.raw_url 
                FROM courses c2 
                WHERE c2.branch_id = b.id AND c2.is_active = true AND btrim(COALESCE(c2.raw_url, '')) <> '' 
                LIMIT 1
            ) AS sample_course_url
        FROM branches b
        JOIN courses c ON c.branch_id = b.id AND c.is_active = true
        WHERE (b.address IS NULL OR btrim(b.address) = '')
        GROUP BY b.id, b.provider, b.branch_code, b.name, b.region_sido, b.region_sigungu, b.website_url
        ORDER BY course_count DESC
        {limit_sql};
    """

    cur.execute(sql)
    rows = cur.fetchall()
    total = len(rows)
    print(f"Loaded {total} branches with active courses missing address (mode: {'APPLY' if args.apply else 'DRY-RUN'})")

    resolved_count = 0
    resolved_courses = 0
    unresolved = []

    update_sql = """
        UPDATE branches
        SET address = %s,
            lat = %s,
            lon = %s,
            address_source = 'KAKAO_LOCAL_KEYWORD',
            coordinate_source = 'KAKAO_LOCAL_KEYWORD',
            location_confidence = 90,
            location_verified = TRUE,
            location_checked_at = NOW(),
            location_query = %s,
            geocode_status = 'resolved',
            geocode_reason_code = 'kakao_keyword_resolved',
            geocode_attempt_count = geocode_attempt_count + 1,
            geocode_last_attempt_at = NOW()
        WHERE id = %s;
    """

    for idx, r in enumerate(rows, 1):
        bid, provider, code, name, sido, sigungu, web_url, cnt, sample_url = r
        queries, exp_sido, exp_sigungu = build_queries(provider, name, sido, sigungu)
        
        found = False
        matched_addr = None
        matched_lat = None
        matched_lon = None
        used_query = None

        for q in queries:
            try:
                resp = requests.get(
                    "https://dapi.kakao.com/v2/local/search/keyword.json",
                    headers=headers,
                    params={"query": q, "size": 5},
                    timeout=5,
                )
                time.sleep(0.04) # rate-limit protection
                if resp.status_code == 200:
                    docs = resp.json().get("documents", [])
                    for doc in docs:
                        addr = doc.get("road_address_name") or doc.get("address_name")
                        if addr and doc.get("y") and doc.get("x"):
                            # Region verification
                            if address_matches_expected_region(addr, exp_sido, exp_sigungu):
                                found = True
                                matched_addr = addr
                                matched_lat = float(doc["y"])
                                matched_lon = float(doc["x"])
                                used_query = q
                                break
                    if found:
                        break
            except Exception:
                time.sleep(0.5)

        target_url = web_url or sample_url or ""
        if found and matched_addr:
            resolved_count += 1
            resolved_courses += cnt
            if args.apply:
                cur.execute(update_sql, (matched_addr, matched_lat, matched_lon, used_query, bid))
                if resolved_count % 50 == 0:
                    conn.commit()
            if idx <= 30 or idx % 100 == 0:
                print(f"[{idx}/{total}] RESOLVED: [{provider}] {name} -> {matched_addr} (courses: {cnt})")
        else:
            unresolved.append({
                "provider": provider,
                "branch_code": code,
                "name": name,
                "active_courses": cnt,
                "url": target_url,
                "tried_queries": queries,
            })
            if idx <= 30 or idx % 100 == 0:
                print(f"[{idx}/{total}] UNRESOLVED: [{provider}] {name} (courses: {cnt}) URL: {target_url}")

    if args.apply:
        conn.commit()
    cur.close()
    conn.close()

    print("\n" + "=" * 70)
    print("                      결과 요약 (SUMMARY)                        ")
    print("=" * 70)
    print(f"총 처리 지점 수: {total}")
    print(f"주소 해결 완료: {resolved_count}개 ({resolved_count/total*100:.1f}%) -> 적용 강좌 수: {resolved_courses:,}개")
    print(f"미해결(못찾음): {len(unresolved)}개 ({len(unresolved)/total*100:.1f}%)")
    print("=" * 70)

    # Save unresolved to JSON
    out_file = Path("/tmp/unresolved_branches.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(unresolved, f, ensure_ascii=False, indent=2)
    print(f"미해결 지점 상세 JSON 저장: {out_file}\n")

    print(f"--- [미해결 지점 목록 (URL 포함)] (총 {len(unresolved)}건) ---")
    for u in sorted(unresolved, key=lambda x: x["active_courses"], reverse=True):
        print(f"• [{u['provider']}] {u['name']} (활성강좌: {u['active_courses']}개)")
        print(f"  URL: {u['url']}")

if __name__ == "__main__":
    main()
