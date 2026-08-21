
import sys
import os
import time
import re
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode
from bs4 import BeautifulSoup
import requests

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_cursor
from DB.course_lifecycle import enrich_course_lifecycle, mark_stale_courses, should_skip_expired_course, utc_now
from utils import setup_logger, extract_number, extract_krw_amount, extract_material_fee_amount, infer_course_status, clean_text, clean_instructor_name, parse_date
from data_parser import TargetParser, ScheduleParser, parse_crawler_target
from target_category_fallback import infer_age_group_from_category
from title_cleaner import clean_course_title
from target_cleaner import extract_target_text
from Crawler.reception_period import extract_reception_period
from Crawler.Config import PROVIDERS

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from Crawler.selenium_driver import build_chrome_driver

# 로거 설정
logger = setup_logger(__name__, 'logs/crawler_homeplus.log')

HOMEPLUS_LEVEL_RE = re.compile(r"\[(Kids|Adult|Baby|Toddler|Child|Senior)\]", re.IGNORECASE)
HOMEPLUS_OPEN_LABEL_RE = re.compile(
    r"^\s*\[(?:Kids|Adult|Baby|Toddler|Child|Senior)\]\s*"
    r"\d{1,2}\s*/\s*\d{1,2}(?:\s*\([^)]+\))?.*$",
    re.IGNORECASE,
)

class HomeplusCrawler:
    """홈플러스 문화센터 크롤러 (Selenium)"""
    
    def __init__(self, use_selenium: bool = True):
        self.config = PROVIDERS.get('HOMEPLUS', {})
        self.base_url = "https://mschool.homeplus.co.kr"
        self.list_url = "https://mschool.homeplus.co.kr/Lecture/SearchResult"
        self.search_api_url = f"{self.base_url}/Lecture/GetSearchResult"
        self.session = requests.Session()
        self.store_lookup = None
        self.branch_reception_cache = {}
        
        self.target_parser = TargetParser()
        self.schedule_parser = ScheduleParser()
        self.driver = None
        self.wait = None
        if use_selenium:
            self._init_driver()
        logger.info("Homeplus Crawler initialized")

    def _init_driver(self):
        try:
            options = Options()
            options.add_argument('--headless') # 디버깅 시 주석 처리
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            
            self.driver = build_chrome_driver(options)
            self.wait = WebDriverWait(self.driver, 15)
        except Exception as e:
            logger.error(f"Failed to init Selenium: {e}")
            raise

    def __del__(self):
        if getattr(self, 'driver', None):
            try:
                self.driver.quit()
            except:
                pass

    def _extract_target_from_text(self, text: str) -> Optional[str]:
        if not text:
            return None

        explicit_target = extract_target_text(text)
        if explicit_target:
            return explicit_target

        patterns = [
            r"\(([^)]*(?:성인|유아|아동|초등|중등|고등|보호자|개월|년생)[^)]*)\)",
            r"(성인|유아|아동|초등생?|중학생|고등학생|보호자\s*\d+인)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return clean_text(match.group(1))
        return None

    def _clean_target_value(self, value: Optional[str]) -> Optional[str]:
        target = clean_text(value or "")
        if not target:
            return None
        if HOMEPLUS_OPEN_LABEL_RE.match(target):
            return None
        if HOMEPLUS_LEVEL_RE.search(target) and re.search(r"\d{1,2}\s*/\s*\d{1,2}", target):
            return None
        if HOMEPLUS_LEVEL_RE.fullmatch(target):
            return None
        return target

    def _extract_date_range_from_text(self, text: str):
        if not text:
            return None, None

        current_year = datetime.now().year
        match = re.search(r"(\d{4})[.\-](\d{2})[.\-](\d{2})\s*[~\-]\s*(\d{4})[.\-](\d{2})[.\-](\d{2})", text)
        if match:
            sy, sm, sd, ey, em, ed = match.groups()
            return (
                parse_date(f"{sy}-{sm}-{sd}"),
                parse_date(f"{ey}-{em}-{ed}"),
            )

        match = re.search(r"(\d{2})[.\-](\d{2})\s*[~\-]\s*(\d{2})[.\-](\d{2})", text)
        if match:
            sm, sd, em, ed = match.groups()
            return (
                parse_date(f"{current_year}-{sm}-{sd}"),
                parse_date(f"{current_year}-{em}-{ed}"),
            )

        return None, None

    def _normalize_homeplus_url(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        value = value.strip()
        if value.startswith("//"):
            return f"https:{value}"
        if value.startswith("/"):
            return f"{self.base_url}{value}"
        return value

    def _course_raw_url(self, detail_url: str, provider_course_id: str) -> str:
        # Homeplus can expose the same LectureMasterID in multiple branches.
        # Keep raw_url branch-scoped so branch reception dates do not collapse.
        separator = "&" if "?" in detail_url else "?"
        return f"{detail_url}{separator}{urlencode({'mooncen_course_id': provider_course_id})}"

    def _homeplus_headers(self, ajax: bool = False, referer: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if ajax:
            headers.update({
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/json; charset=utf-8",
                "X-Requested-With": "XMLHttpRequest",
            })
        if referer:
            headers["Referer"] = referer
        return headers

    def _normalize_store_name(self, value: Optional[str]) -> str:
        value = clean_text(value or "")
        for token in ("\ud648\ud50c\ub7ec\uc2a4", "\ubb38\ud654\uc13c\ud130"):
            value = value.replace(token, "")
        return re.sub(r"[\s\[\]\(\)·ㆍ/_-]+", "", value)

    def fetch_store_list(self) -> List[Dict]:
        try:
            response = self.session.post(
                f"{self.base_url}/Store/GetStoreList",
                data="{}",
                headers=self._homeplus_headers(
                    ajax=True,
                    referer=f"{self.base_url}/Store/FindStore",
                ),
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            stores = payload.get("Data", {}).get("StoreList", [])
            return stores if isinstance(stores, list) else []
        except Exception as e:
            logger.warning("HOMEPLUS store list fetch failed: %s", e)
            return []

    def _get_store_lookup(self) -> Dict[str, Dict]:
        if self.store_lookup is not None:
            return self.store_lookup

        lookup = {}
        for store in self.fetch_store_list():
            store_code = clean_text(store.get("StoreCode"))
            store_name = clean_text(store.get("StoreName"))
            if store_code:
                lookup[store_code] = store
            if store_name:
                lookup[store_name] = store
                normalized = self._normalize_store_name(store_name)
                if normalized:
                    lookup[normalized] = store

        self.store_lookup = lookup
        logger.info("Loaded %s HOMEPLUS store lookup entries", len(lookup))
        return lookup

    def _resolve_store_info(self, branch_code: Optional[str] = None, branch_name: Optional[str] = None) -> Optional[Dict]:
        lookup = self._get_store_lookup()
        for value in (branch_code, branch_name):
            key = clean_text(value or "")
            if key and key in lookup:
                return lookup[key]
            normalized = self._normalize_store_name(key)
            if normalized and normalized in lookup:
                return lookup[normalized]

        normalized_name = self._normalize_store_name(branch_name)
        if normalized_name:
            for store in lookup.values():
                store_name = self._normalize_store_name(store.get("StoreName"))
                if store_name and (store_name == normalized_name or store_name in normalized_name or normalized_name in store_name):
                    return store
        return None

    def _parse_branch_reception_table(self, html: str) -> Dict:
        soup = BeautifulSoup(html or "", "html.parser")
        rows = []
        current_group = ""
        raw_parts = []

        for tr in soup.select(".receipt_guide table tr"):
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in tr.select("th,td")]
            cells = [cell for cell in cells if cell]
            if not cells:
                continue

            if len(cells) >= 3:
                current_group = cells[0]
                label = cells[1]
                value = cells[2]
            elif len(cells) >= 2:
                label = cells[0]
                value = cells[1]
            else:
                continue

            if "\uc811\uc218\uae30\uac04" not in current_group:
                continue
            start_date, end_date = self._extract_date_range_from_text(value)
            if not start_date and not end_date:
                continue
            rows.append((label, start_date, end_date, value))
            raw_parts.append(f"{label}: {value}")

        if not rows:
            return {}

        selected = None
        for row in rows:
            if "\uc2e0\uaddc\ud68c\uc6d0" in row[0]:
                selected = row
                break
        if selected is None:
            selected = rows[0]

        return {
            "apply_start": selected[1],
            "apply_end": selected[2],
            "apply_period_raw": " / ".join(raw_parts),
        }

    def scrape_branch_reception_period(self, store_code: Optional[str]) -> Dict:
        store_code = clean_text(store_code or "")
        if not store_code:
            return {}
        if store_code in self.branch_reception_cache:
            return self.branch_reception_cache[store_code]

        result = {}
        try:
            response = self.session.get(
                f"{self.base_url}/OperationGuide/BranchStoreDetail",
                params={"reqStoreCode": store_code, "reqThrowType": "1"},
                headers=self._homeplus_headers(referer=f"{self.base_url}/Store/FindStore"),
                timeout=30,
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            result = self._parse_branch_reception_table(response.text)
            if result:
                logger.info(
                    "HOMEPLUS branch reception period. store_code=%s period=%s",
                    store_code,
                    result.get("apply_period_raw"),
                )
        except Exception as e:
            logger.warning("HOMEPLUS branch reception fetch failed. store_code=%s error=%s", store_code, e)

        self.branch_reception_cache[store_code] = result
        return result

    def apply_branch_reception_period(self, course_data: Dict, branch_code: Optional[str] = None, branch_name: Optional[str] = None) -> None:
        if course_data.get("apply_start") and course_data.get("apply_end"):
            return

        store = self._resolve_store_info(
            branch_code or course_data.get("store_code") or course_data.get("branch_code"),
            branch_name or course_data.get("branch"),
        )
        if not store:
            return

        store_code = clean_text(store.get("StoreCode"))
        reception_period = self.scrape_branch_reception_period(store_code)
        if not reception_period:
            return

        if not course_data.get("apply_period_raw"):
            for key in ("apply_start", "apply_end", "apply_period_raw"):
                if reception_period.get(key):
                    course_data[key] = reception_period[key]
            return

        for key in ("apply_start", "apply_end", "apply_period_raw"):
            if reception_period.get(key) and not course_data.get(key):
                course_data[key] = reception_period[key]

    def _parse_course_item(self, item, branch_cache: Dict[str, str]) -> Optional[Dict]:
        lecture_id = (item.get("id") or "").replace("liLecture_", "").strip()
        if not lecture_id:
            hidden_id = item.select_one('input[name="LectureMasterID"]')
            lecture_id = hidden_id.get("value", "").strip() if hidden_id else ""
        if not lecture_id:
            return None

        office_elem = item.select_one(".office_name")
        office = clean_text(office_elem.get_text(" ", strip=True)) if office_elem else "HOMEPLUS"
        store_info = self._resolve_store_info(branch_name=office)
        branch_code = clean_text((store_info or {}).get("StoreCode")) or office[:50] or "HOMEPLUS"
        if branch_code not in branch_cache:
            branch_cache[branch_code] = self.save_branch({
                "provider": "HOMEPLUS",
                "branch_code": branch_code,
                "name": office,
                "address": clean_text((store_info or {}).get("Address1")) if store_info else "",
                "phone": clean_text((store_info or {}).get("OperatorMobileNumber") or (store_info or {}).get("PhoneNumber")) if store_info else "",
            })
        branch_id = branch_cache.get(branch_code)
        if not branch_id:
            return None

        title_1_elem = item.select_one(".title_1")
        title_1 = clean_text(title_1_elem.get_text(" ", strip=True)) if title_1_elem else ""
        title_parts = [clean_text(elem.get_text(" ", strip=True)) for elem in item.select(".title_2")]
        full_title = " ".join(part for part in title_parts if part).strip() or title_1 or f"홈플러스 강좌 {lecture_id}"
        sub_texts = [clean_text(elem.get_text(" ", strip=True)) for elem in item.select(".sub_info_wrap .sub_txt")]
        schedule_text = " ".join(text for text in sub_texts if re.search(r"\d{1,2}:\d{2}", text))
        fee_text = next((text for text in sub_texts if "원" in text), "")
        date_text = next((text for text in sub_texts if re.search(r"\d{4}[.\-]\d{2}[.\-]\d{2}", text)), "")
        instructor_text = next((text for text in sub_texts if "강사" in text), "")
        instructor = clean_instructor_name(instructor_text)
        sessions = extract_number(next((text for text in sub_texts if "회" in text), "")) or None
        start_date, end_date = self._extract_date_range_from_text(date_text)
        reception_period = extract_reception_period(" ".join(sub_texts), start_date)

        category_match = re.search(r"\[([^\]]+)\]", title_1)
        category_raw = category_match.group(1) if category_match else None
        status_source = " ".join([title_1, full_title, *sub_texts])
        status = infer_course_status(status_source)

        provider_course_id = f"{branch_code}:{lecture_id}"
        detail_url = f"{self.base_url}/Lecture/Detail?LectureMasterID={lecture_id}"
        return {
            "branch_id": branch_id,
            "branch": office,
            "branch_code": branch_code,
            "store_code": branch_code,
            "provider": "HOMEPLUS",
            "provider_course_id": provider_course_id,
            "title": full_title,
            "instructor": instructor,
            "target": self._extract_target_from_text(full_title) or self._extract_target_from_text(title_1),
            "category_raw": category_raw,
            "fee": extract_krw_amount(fee_text),
            "material_fee": 0,
            "sessions": sessions,
            "schedule_raw": schedule_text,
            "start_date": start_date,
            "end_date": end_date,
            "apply_start": reception_period.get("apply_start"),
            "apply_end": reception_period.get("apply_end"),
            "apply_period_raw": reception_period.get("apply_period_raw"),
            "status": status,
            "raw_url": self._course_raw_url(detail_url, provider_course_id),
            "detail_url": detail_url,
            "description": title_1 or full_title,
            "image_url": None,
        }

    def scrape_course_detail_http(self, detail_url: str) -> Dict:
        result = {}
        try:
            response = self.session.get(detail_url, timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            body_text = clean_text(soup.get_text(" ", strip=True))

            for selector in [".newCon_tit", ".lecture_title", ".lect_title"]:
                elem = soup.select_one(selector)
                title = clean_text(elem.get_text(" ", strip=True)) if elem else ""
                if title:
                    result["title"] = title
                    break

            if not result.get("title"):
                for selector in [".newCon_Swiper img[alt]", ".swiper-slide img[alt]", 'img[src*="LectureUploadfiles"][alt]']:
                    elem = soup.select_one(selector)
                    title = clean_text(elem.get("alt", "")) if elem else ""
                    title = re.sub(r"\s*\uC774\uBBF8\uC9C0\s*$", "", title).strip()
                    if title:
                        result["title"] = title
                        break

            for selector in [".newCon_lecture_introduce", "#tab02 .newCon_lecture_introduce", "#tab02"]:
                elem = soup.select_one(selector)
                description = clean_text(elem.get_text(" ", strip=True)) if elem else ""
                if len(description) > 10:
                    result["description"] = description
                    break

            for selector in [
                'img[src*="LectureUploadfiles"]',
                'img[src*="Upload"]',
                'img[src*="upload"]',
                ".swiper-slide img",
                ".newCon_visual img",
                ".lecture_img img",
                'meta[property="og:image"]',
            ]:
                elem = soup.select_one(selector)
                image_url = (elem.get("content") or elem.get("src") or elem.get("data-src")) if elem else None
                image_url = self._normalize_homeplus_url(image_url)
                if image_url and "OtherMobileAppImage" not in image_url and "header_logo" not in image_url:
                    result["image_url"] = image_url
                    break

            category_elems = soup.select(".newCon_top_info span")
            if len(category_elems) >= 2:
                category = clean_text(category_elems[1].get_text(" ", strip=True))
                if category:
                    result["category_raw"] = category

            status_candidates = []
            for selector in [
                ".lectStatNm",
                ".lecture_status",
                ".status",
                ".btn_apply",
                ".btn_cart",
                ".btn_area a",
                ".newCon_btn_wrap a",
            ]:
                status_candidates.extend(
                    clean_text(elem.get_text(" ", strip=True))
                    for elem in soup.select(selector)
                    if clean_text(elem.get_text(" ", strip=True))
                )
            status = infer_course_status(*status_candidates, default="")
            if status:
                result["status"] = status

            reception_period = extract_reception_period(body_text, result.get("start_date"))
            if reception_period:
                result.update(reception_period)

            for dt_elem, dd_elem in zip(soup.select(".newCon_lecture_data dt"), soup.select(".newCon_lecture_data dd")):
                label = clean_text(dt_elem.get_text(" ", strip=True))
                value = clean_text(dd_elem.get_text(" ", strip=True))
                reception_period = extract_reception_period(f"{label} {value}", result.get("start_date"))
                if reception_period:
                    result.update(reception_period)
                if "강사" in label and value:
                    result["instructor"] = clean_instructor_name(value)
                elif "기간" in label:
                    start_date, end_date = self._extract_date_range_from_text(value)
                    if start_date:
                        result["start_date"] = start_date
                    if end_date:
                        result["end_date"] = end_date
                elif "수강료" in label:
                    result["fee"] = extract_krw_amount(value)
                elif "재료" in label or "교구" in label:
                    result["material_fee"] = extract_krw_amount(value)
        except Exception as e:
            logger.warning(f"Failed to fetch Homeplus detail by HTTP {detail_url}: {e}")
        return result

    def scrape_courses_api(
        self,
        limit: Optional[int] = None,
        branch_code_filter: Optional[str] = None,
        branch_name_filter: Optional[str] = None,
    ) -> int:
        self.session.get(self.list_url, timeout=25)
        branch_cache = {}
        seen = set()
        saved = 0
        page = 1

        max_pages = int(os.getenv("HOMEPLUS_MAX_PAGES", "500"))
        empty_pages = 0
        while (limit is None or saved < limit) and page <= max_pages:
            response = self.session.post(
                self.search_api_url,
                data={"page": page, "pageSize": 20, "prm": "[]", "word": "", "sort": 1},
                timeout=30,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            items = soup.select('li[id^="liLecture_"]')
            if not items:
                empty_pages += 1
                logger.info(f"Homeplus API page {page}: no items")
                if empty_pages >= 2:
                    break
                page += 1
                continue

            page_saved_before = saved
            empty_pages = 0

            for item in items:
                if limit is not None and saved >= limit:
                    break
                lecture_id = (item.get("id") or "").replace("liLecture_", "").strip()
                if not lecture_id:
                    continue

                course_data = self._parse_course_item(item, branch_cache)
                if not course_data:
                    continue
                if branch_code_filter and clean_text(course_data.get("branch_code")) != clean_text(branch_code_filter):
                    continue
                if branch_name_filter and clean_text(course_data.get("branch")) != clean_text(branch_name_filter):
                    continue
                seen_key = course_data.get("provider_course_id") or lecture_id
                if seen_key in seen:
                    continue
                seen.add(seen_key)

                detail_info = self.scrape_course_detail_http(course_data.get("detail_url") or course_data["raw_url"])
                course_data.update({key: value for key, value in detail_info.items() if value})
                self.apply_branch_reception_period(
                    course_data,
                    branch_code=course_data.get("branch_code"),
                    branch_name=course_data.get("branch"),
                )

                if self.save_course(course_data):
                    saved += 1

            logger.info(f"Homeplus API page {page}: saved {saved}")
            if saved == page_saved_before:
                logger.info(f"Homeplus API page {page}: duplicate/no new rows, stopping pagination.")
                break
            page += 1
            time.sleep(0.1)

        if page > max_pages:
            logger.warning("Homeplus API pagination stopped at max_pages=%s", max_pages)
        return saved

    def scrape_branches(self) -> List[Dict]:
        """지점 목록 수집 (검색 필터에서 추출)"""
        logger.info("Scraping branches...")
        branches = []
        stores = self.fetch_store_list()
        if stores:
            for store in stores:
                store_code = clean_text(store.get("StoreCode"))
                store_name = clean_text(store.get("StoreName"))
                if not store_code or not store_name:
                    continue
                branches.append({
                    'provider': 'HOMEPLUS',
                    'branch_code': store_code,
                    'name': store_name,
                    'address': clean_text(" ".join(part for part in [store.get("Address1"), store.get("Address2")] if part)),
                    'phone': clean_text(store.get("OperatorMobileNumber") or store.get("PhoneNumber") or ''),
                })
            logger.info("Found %s HOMEPLUS branches from store API", len(branches))
            return branches

        if not getattr(self, "driver", None):
            return branches
        try:
            self.driver.get(self.list_url)
            time.sleep(2)
            
            # 지점 선택 셀렉트 박스 찾기 (ID 추정)
            # 보통 지역 선택 -> 지점 선택 구조일 수 있음
            # HTML 분석이 안되었으므로, source를 보고 판단하거나 일반적인 select 탐색
            
            # 1. 페이지 소스에서 지점 정보 찾기 시도 (Select box or hidden input)
            # 여기서는 '서울남현점', '강동점' 등이 존재하는 것으로 보아 지점 필터가 있을 것임.
            # 일단 '전체 지점'을 수집하는 로직보다, 우선 테스트된 지점들 위주로 하거나
            # UI에서 지점 목록을 추출해야 함.
            
            # (임시) HTML 구조를 모르므로, 화면 진입 후 
            # 검색 조건의 지점 Select를 찾아본다.
            # 만약 못 찾으면 기본 3개 지점만 하드코딩 리턴 (테스트용)
            
            try:
                # StoreCode 라는 이름이나 ID를 가진 select가 있는지 확인
                selects = self.driver.find_elements(By.TAG_NAME, "select")
                store_select = None
                for s in selects:
                    if "Store" in s.get_attribute("name") or "Store" in s.get_attribute("id"):
                        store_select = s
                        break
                
                if store_select:
                    options = store_select.find_elements(By.TAG_NAME, "option")
                    for opt in options:
                        val = opt.get_attribute("value")
                        txt = opt.text.strip()
                        if val and txt and "선택" not in txt:
                            branches.append({
                                'provider': 'HOMEPLUS',
                                'branch_code': val,
                                'name': txt,
                                'address': '',
                                'phone': ''
                            })
                    logger.info(f"Found {len(branches)} branches from select box")
            except Exception as e:
                logger.warning(f"Failed to extract branches from UI: {e}")

            if not branches:
                # Fallback: 샘플에 있던 지점들 하드코딩
                defaults = [
                    {'branch_code': '001', 'name': '서울남현점'},
                    {'branch_code': '002', 'name': '강동점'},
                    {'branch_code': '003', 'name': '잠실점'},
                    {'branch_code': '004', 'name': '동대문점'} 
                ]
                for d in defaults:
                    d['provider'] = 'HOMEPLUS'
                    branches.append(d)
                logger.info(f"Using default fallback branches: {len(branches)}")

        except Exception as e:
            logger.error("HOMEPLUS branch crawl failed. url=%s error=%s", self.list_url, e)
            
        return branches

    def save_branch(self, branch_data: Dict) -> Optional[str]:
        try:
            with get_db_cursor() as cursor:
                # Ensure address and phone are not None if table requires them (though text usually allows null)
                # But here we pass them as parameters.
                if 'address' not in branch_data:
                    branch_data['address'] = ''
                if 'phone' not in branch_data:
                    branch_data['phone'] = ''
                
                cursor.execute("""
                    INSERT INTO branches (provider, branch_code, name, address, phone)
                    VALUES (%(provider)s, %(branch_code)s, %(name)s, %(address)s, %(phone)s)
                    ON CONFLICT (provider, branch_code) 
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        address = EXCLUDED.address,
                        phone = EXCLUDED.phone
                    RETURNING id
                """, branch_data)
                return str(cursor.fetchone()['id'])
        except Exception as e:
            logger.error(
                "HOMEPLUS branch save failed. branch=%s branch_code=%s error=%s",
                branch_data.get("name"),
                branch_data.get("branch_code"),
                e,
            )
            return None

    def scrape_courses(self, branch_code: str, branch_id: str, max_courses: Optional[int] = None) -> int:
        """지점별 강좌 수집"""
        if not getattr(self, "driver", None):
            logger.error("HOMEPLUS Selenium driver is not initialized for branch=%s", branch_code)
            return 0
        logger.info(f"Scraping courses for branch: {branch_code}")
        count = 0
        try:
            # 1. 해당 지점으로 필터링하여 검색 (GET 파라미터로 가능할지 확인)
            # https://mschool.homeplus.co.kr/Lecture/SearchResult?StoreCode=...
            target_url = f"{self.list_url}?StoreCode={branch_code}"
            self.driver.get(target_url)
            time.sleep(3)
            
            # Items (li[id^="liLecture_"])
            items_len = len(self.driver.find_elements(By.CSS_SELECTOR, 'li[id^="liLecture_"]'))
            logger.info(f"Found {items_len} items")
            
            items_limit = items_len
            
            for i in range(items_limit):
                if max_courses is not None and count >= max_courses:
                    logger.info(f"Limit reached for branch {branch_code}: {count}/{max_courses}")
                    break

                try:
                    # Re-fetch items to avoid StaleElementReferenceException
                    current_items = self.driver.find_elements(By.CSS_SELECTOR, 'li[id^="liLecture_"]')
                    if i >= len(current_items):
                        break
                    
                    item = current_items[i]
                    
                    # Parse List Item directly (as per analysis)
                    # Title
                    try:
                        title1 = item.find_element(By.CSS_SELECTOR, '.title_1').text.strip()
                        title2_elems = item.find_elements(By.CSS_SELECTOR, '.title_2')
                        main_title = title2_elems[0].text.strip() if title2_elems else ""
                        sub_title = title2_elems[1].text.strip() if len(title2_elems) > 1 else ""
                        full_title = f"{main_title} {sub_title}".strip()
                    except:
                        full_title = "Unknown Title"

                    # Fee & Schedule
                    fee = 0
                    schedule_text = ""
                    lecturer = ""
                    
                    try:
                        sub_texts = item.find_elements(By.CSS_SELECTOR, '.sub_info_wrap .sub_txt')
                        for st in sub_texts:
                            txt = st.text.strip()
                            if '원' in txt:
                                fee = extract_krw_amount(txt)
                            elif '~' in txt and re.search(r'\d{2}:\d{2}', txt):
                                 schedule_text += " " + txt
                            elif '강사' in txt:
                                lecturer = clean_instructor_name(txt) or ""
                    except:
                        pass
                    
                    # ID extraction
                    li_id = item.get_attribute('id') # liLecture_9917979
                    lecture_master_id = li_id.replace('liLecture_', '')
                    provider_course_id = f"{branch_code}:{lecture_master_id}"
                    
                    # 올바른 상세 URL 생성
                    detail_url = f"{self.base_url}/Lecture/Detail?LectureMasterID={lecture_master_id}"
                    
                    course_data = {
                        'branch_id': branch_id,
                        'branch_code': branch_code,
                        'store_code': branch_code,
                        'provider': 'HOMEPLUS',
                        'provider_course_id': provider_course_id,
                        'title': full_title,
                        'instructor': lecturer or None,
                        'target': self._extract_target_from_text(full_title),
                        'category_raw': title1 if 'title1' in locals() and title1 else None,
                        'fee': fee,
                        'schedule_raw': schedule_text.strip(),
                        'raw_url': self._course_raw_url(detail_url, provider_course_id),
                        'detail_url': detail_url,
                        'status': 'OPEN'
                    }
                    
                    # Scrape detail page for description and other fields
                    detail_info = self.scrape_course_detail(detail_url)
                    
                    # 상세 페이지 다녀오면 뒤로가기 해야 함 (scrape_course_detail 내부에서 처리됨)
                    # 하지만 리스트가 reset 되었을 수 있으므로 index loop가 필수적임.
                    
                    if detail_info:
                        if 'description' in detail_info:
                            course_data['description'] = detail_info['description']
                        if 'image_url' in detail_info:
                            course_data['image_url'] = detail_info['image_url']
                        if 'category_raw' in detail_info:
                            course_data['category_raw'] = detail_info['category_raw']
                        if 'instructor' in detail_info:
                            course_data['instructor'] = detail_info['instructor']
                        if 'target' in detail_info:
                            course_data['target'] = detail_info['target']
                        if 'start_date' in detail_info:
                            course_data['start_date'] = detail_info['start_date']
                        if 'end_date' in detail_info:
                            course_data['end_date'] = detail_info['end_date']
                        if 'apply_start' in detail_info:
                            course_data['apply_start'] = detail_info['apply_start']
                        if 'apply_end' in detail_info:
                            course_data['apply_end'] = detail_info['apply_end']
                        if 'apply_period_raw' in detail_info:
                            course_data['apply_period_raw'] = detail_info['apply_period_raw']
                        if 'fee' in detail_info and detail_info['fee']:
                            course_data['fee'] = detail_info['fee']
                        if 'material_fee' in detail_info:
                            course_data['material_fee'] = detail_info['material_fee']

                    self.apply_branch_reception_period(course_data, branch_code=branch_code)
                    
                    if self.save_course(course_data):
                        count += 1
                        
                except Exception as e:
                    logger.error(
                        "HOMEPLUS course item failed. branch_code=%s list_url=%s item_index=%s error=%s",
                        branch_code,
                        target_url,
                        i,
                        e,
                    )
                    # 페이지 복구 시도
                    try:
                        if self.driver.current_url != target_url:
                             self.driver.get(target_url)
                             time.sleep(2)
                    except:
                        pass
                    continue
                    
        except Exception as e:
            logger.error("HOMEPLUS branch course crawl failed. branch_code=%s url=%s error=%s", branch_code, target_url, e)
            
        return count

    def scrape_course_detail(self, detail_url: str) -> Optional[Dict]:
        """상세 페이지에서 description, image, category, date 추출"""
        try:
            # 현재 URL 저장
            current_url = self.driver.current_url
            
            # 상세 페이지로 이동
            self.driver.get(detail_url)
            time.sleep(2)
            
            result = {}
            
            # Description 추출 시도
            description = None
            desc_selectors = [
                '.newCon_lecture_introduce',  # 실제 description 위치
                '#tab02 .newCon_lecture_introduce',
                '.lecture_info',
                '.detail_content',
            ]
            
            for selector in desc_selectors:
                try:
                    elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                    description = clean_text(elem.text)
                    if description and len(description) > 10:
                        break
                except:
                    continue
            
            if description:
                result['description'] = description
            
            # Image URL 추출
            image_selectors = [
                'meta[property="og:image"]',
                '.swiper-slide img',
                '.newCon_visual img',
                '.lecture_img img',
                'img[src*="Lecture"]',
                'img[src*="upload"]',
            ]
            for selector in image_selectors:
                try:
                    img_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                    image_url = img_elem.get_attribute('content') or img_elem.get_attribute('src') or img_elem.get_attribute('data-src')
                    if image_url:
                        if image_url.startswith('/'):
                            image_url = f"{self.base_url}{image_url}"
                        result['image_url'] = image_url
                        break
                except:
                    continue
            
            # Category 추출
            try:
                category_elems = self.driver.find_elements(By.CSS_SELECTOR, '.newCon_top_info span')
                if len(category_elems) >= 2:
                    # 두 번째 span이 카테고리 (첫 번째는 지점명)
                    category = clean_text(category_elems[1].text)
                    if category:
                        result['category_raw'] = category
            except:
                pass

            # 강사명 / 강의기간 / 취소환불 정보는 dt/dd 쌍으로 내려온다.
            try:
                dt_elems = self.driver.find_elements(By.CSS_SELECTOR, '.newCon_lecture_data dt')
                dd_elems = self.driver.find_elements(By.CSS_SELECTOR, '.newCon_lecture_data dd')
                for dt_elem, dd_elem in zip(dt_elems, dd_elems):
                    label = clean_text(dt_elem.text)
                    value = clean_text(dd_elem.text)
                    if not label or not value:
                        continue

                    reception_period = extract_reception_period(f"{label} {value}", result.get("start_date"))
                    if reception_period:
                        result.update(reception_period)

                    if label == '강사명' and value:
                        result['instructor'] = clean_instructor_name(value)
                    elif label == '강의기간':
                        start_date, end_date = self._extract_date_range_from_text(value)
                        if start_date:
                            result['start_date'] = start_date
                        if end_date:
                            result['end_date'] = end_date
                    elif label == '취소, 환불':
                        deadline_match = re.search(r'개강일\s*(\d+)일전,\s*(\d{1,2})시', value)
                        base_start = result.get('start_date')
                        if deadline_match and base_start:
                            days_before = int(deadline_match.group(1))
                            apply_end = base_start - timedelta(days=days_before)
                            result['apply_end'] = apply_end
            except:
                pass
            
            # Start/End Date 추출
            try:
                date_elems = self.driver.find_elements(By.CSS_SELECTOR, '.newCon_lecture_data dd')
                for elem in date_elems:
                    text = clean_text(elem.text)
                    # "2026.01.31 ~ 2026.01.31" 형식 찾기
                    if '~' in text and ('2025' in text or '2026' in text or '2027' in text):
                        dates = text.split('~')
                        if len(dates) == 2:
                            start_str = dates[0].strip().replace('.', '-')
                            end_str = dates[1].strip().replace('.', '-')
                            result['start_date'] = parse_date(start_str)
                            result['end_date'] = parse_date(end_str)
                            break
            except:
                pass

            try:
                body_text = clean_text(self.driver.find_element(By.TAG_NAME, "body").text)
                reception_period = extract_reception_period(body_text, result.get("start_date"))
                if reception_period:
                    result.update(reception_period)
                target_source = body_text.split('강좌소개', 1)[0]
                target = self._extract_target_from_text(target_source)
                if target:
                    result['target'] = target
            except:
                pass
            
            # 원래 페이지로 돌아가기
            self.driver.get(current_url)
            time.sleep(1)
            
            return result if result else None
            
        except Exception as e:
            logger.error(f"Failed to scrape detail page {detail_url}: {e}")
            return None

    def save_course(self, course_data: Dict) -> bool:
        """DB 저장 (공통 로직 복사/참조)"""
        try:
             # Basic Defaults
            defaults = {
                'instructor': None, 'target': None, 'category_raw': None,
                'fee': 0, 'material_fee': 0, 'sessions': 0, 'schedule_raw': None,
                'start_date': None, 'end_date': None, 'apply_start': None,
                'apply_end': None, 'apply_period_raw': None, 'description': None, 'image_url': None,
                'target_age_group': None, 'target_min_age': None, 'target_max_age': None,
                'target_with_parent': False, 'target_tags': [],
                'schedule_days': [], 'schedule_time_start': None, 'schedule_time_end': None,
                'schedule_frequency': 'WEEKLY', 'schedule_duration_minutes': None
            }
            for k, v in defaults.items():
                if k not in course_data:
                    course_data[k] = v
            course_data['instructor'] = clean_instructor_name(course_data.get('instructor'))

            raw_title = course_data.get('title') or ''
            clean_title, removed_title_prefix = clean_course_title(raw_title)
            course_data['title_raw'] = raw_title
            course_data['title'] = clean_title
            course_data['title_prefix_removed'] = removed_title_prefix or None

            explicit_target = extract_target_text(raw_title)
            if explicit_target:
                course_data['target'] = explicit_target
            else:
                course_data['target'] = self._clean_target_value(course_data.get('target'))

            # Description often contains dates, notices, or unrelated age-like text.
            # Keep age parsing to title/target/category so explicit month or birth-year
            # values are stored as months and broad category defaults do not leak into
            # target_min_age/target_max_age.
            target_source = " ".join(
                part for part in [
                    raw_title,
                    course_data['title'],
                    course_data.get('target') or '',
                    course_data.get('category_raw') or '',
                ] if part
            )
            parsed_target = parse_crawler_target(target_source, self.target_parser)
            if not parsed_target.get('age_group'):
                parsed_target['age_group'] = infer_age_group_from_category(course_data.get('category_raw'))
            if parsed_target.get('age_group') == 'ADULT' and not parsed_target.get('age_is_explicit'):
                parsed_target['min_age'] = None
                parsed_target['max_age'] = None
            course_data.update({
                'target_age_group': parsed_target['age_group'],
                'target_min_age': parsed_target['min_age'],
                'target_max_age': parsed_target['max_age'],
                'target_with_parent': parsed_target['with_parent'],
                'target_tags': parsed_target['tags'],
                'target_age_is_explicit': parsed_target.get('age_is_explicit', False)
            })
            
            if course_data.get('schedule_raw'):
                parsed_schedule = self.schedule_parser.parse(course_data['schedule_raw'])
                course_data.update({
                    'schedule_days': parsed_schedule['days'],
                    'schedule_time_start': parsed_schedule['time_start'],
                    'schedule_time_end': parsed_schedule['time_end'],
                    'schedule_frequency': parsed_schedule['frequency'],
                    'schedule_duration_minutes': parsed_schedule['duration_minutes']
                })
            if course_data.get('schedule_duration_minutes') is not None and course_data.get('schedule_duration_minutes') <= 0:
                course_data['schedule_duration_minutes'] = None
            if course_data.get('schedule_time_start') == course_data.get('schedule_time_end'):
                course_data['schedule_time_start'] = None
                course_data['schedule_time_end'] = None
            if not course_data.get('material_fee'):
                course_data['material_fee'] = extract_material_fee_amount(
                    course_data.get('material_note'),
                    course_data.get('description'),
                )
            course_data['application_url'] = course_data.get('application_url') or course_data.get('raw_url')
            if should_skip_expired_course(course_data):
                logger.info("Skipping expired HOMEPLUS course: %s", course_data.get('title'))
                return False

            enrich_course_lifecycle(course_data)

            with get_db_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO courses (
                        branch_id, provider, provider_course_id, title, title_raw, title_prefix_removed, instructor,
                        target, category_raw, fee, material_fee, sessions, schedule_raw,
                        start_date, end_date, apply_start, apply_end, apply_period_raw, status, application_url, raw_url,
                        description, image_url,
                        is_active, first_seen_at, last_seen_at, removed_at, content_hash, change_detected_at,
                        target_age_group, target_min_age, target_max_age, 
                        target_with_parent, target_tags, target_age_is_explicit,
                        schedule_days, schedule_time_start, schedule_time_end,
                        schedule_frequency, schedule_duration_minutes
                    )
                    VALUES (
                        %(branch_id)s, %(provider)s, %(provider_course_id)s, %(title)s,
                        %(title_raw)s, %(title_prefix_removed)s,
                        %(instructor)s, %(target)s, %(category_raw)s, %(fee)s,
                        %(material_fee)s, %(sessions)s, %(schedule_raw)s, %(start_date)s,
                        %(end_date)s, %(apply_start)s, %(apply_end)s, %(apply_period_raw)s, %(status)s,
                        %(application_url)s, %(raw_url)s, %(description)s, %(image_url)s,
                        TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, %(content_hash)s, NULL,
                        %(target_age_group)s, %(target_min_age)s, %(target_max_age)s,
                        %(target_with_parent)s, %(target_tags)s, %(target_age_is_explicit)s,
                        %(schedule_days)s, %(schedule_time_start)s, %(schedule_time_end)s,
                        %(schedule_frequency)s, %(schedule_duration_minutes)s
                    )
                    ON CONFLICT (provider, provider_course_id)
                    DO UPDATE SET
                        title = CASE
                            WHEN COALESCE(courses.ai_title_processed, FALSE)
                             AND courses.title_raw IS NOT DISTINCT FROM EXCLUDED.title_raw
                            THEN courses.title
                            ELSE EXCLUDED.title
                        END,
                        title_raw = EXCLUDED.title_raw,
                        title_prefix_removed = CASE
                            WHEN COALESCE(courses.ai_title_processed, FALSE)
                             AND courses.title_raw IS NOT DISTINCT FROM EXCLUDED.title_raw
                            THEN courses.title_prefix_removed
                            ELSE EXCLUDED.title_prefix_removed
                        END,
                        instructor = EXCLUDED.instructor,
                        target = CASE
                            WHEN %(target_age_is_explicit)s AND EXCLUDED.target IS NOT NULL
                            THEN EXCLUDED.target
                            WHEN COALESCE(courses.ai_title_processed, FALSE)
                             AND courses.title_raw IS NOT DISTINCT FROM EXCLUDED.title_raw
                            THEN courses.target
                            ELSE EXCLUDED.target
                        END,
                        category_raw = EXCLUDED.category_raw,
                        fee = EXCLUDED.fee,
                        schedule_raw = EXCLUDED.schedule_raw,
                        start_date = EXCLUDED.start_date,
                        end_date = EXCLUDED.end_date,
                        apply_start = EXCLUDED.apply_start,
                        apply_end = EXCLUDED.apply_end,
                        apply_period_raw = EXCLUDED.apply_period_raw,
                        status = EXCLUDED.status,
                        application_url = COALESCE(EXCLUDED.application_url, courses.application_url),
                        raw_url = EXCLUDED.raw_url,
                        description = COALESCE(EXCLUDED.description, courses.description),
                        image_url = COALESCE(EXCLUDED.image_url, courses.image_url),
                        is_active = TRUE,
                        last_seen_at = CURRENT_TIMESTAMP,
                        removed_at = NULL,
                        change_detected_at = CASE
                            WHEN courses.content_hash IS DISTINCT FROM EXCLUDED.content_hash THEN CURRENT_TIMESTAMP
                            ELSE courses.change_detected_at
                        END,
                        content_hash = EXCLUDED.content_hash,
                        target_age_group = CASE WHEN %(target_age_is_explicit)s OR COALESCE(courses.target_age_is_explicit, FALSE) THEN EXCLUDED.target_age_group ELSE COALESCE(courses.target_age_group, EXCLUDED.target_age_group) END,
                        target_min_age = CASE WHEN %(target_age_is_explicit)s OR COALESCE(courses.target_age_is_explicit, FALSE) THEN EXCLUDED.target_min_age ELSE COALESCE(courses.target_min_age, EXCLUDED.target_min_age) END,
                        target_max_age = CASE WHEN %(target_age_is_explicit)s OR COALESCE(courses.target_age_is_explicit, FALSE) THEN EXCLUDED.target_max_age ELSE COALESCE(courses.target_max_age, EXCLUDED.target_max_age) END,
                        target_with_parent = CASE WHEN %(target_age_is_explicit)s OR COALESCE(courses.target_age_is_explicit, FALSE) THEN EXCLUDED.target_with_parent ELSE COALESCE(courses.target_with_parent, EXCLUDED.target_with_parent) END,
                        target_tags = CASE WHEN %(target_age_is_explicit)s OR COALESCE(courses.target_age_is_explicit, FALSE) THEN EXCLUDED.target_tags ELSE COALESCE(courses.target_tags, EXCLUDED.target_tags) END,
                        target_age_is_explicit = EXCLUDED.target_age_is_explicit,
                        ai_category = CASE
                            WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                              OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                              OR COALESCE(EXCLUDED.description, courses.description) IS DISTINCT FROM courses.description
                            THEN NULL
                            ELSE courses.ai_category
                        END,
                        ai_tags = CASE
                            WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                              OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                              OR COALESCE(EXCLUDED.description, courses.description) IS DISTINCT FROM courses.description
                            THEN NULL
                            ELSE courses.ai_tags
                        END,
                        ai_summary = CASE
                            WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                              OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                              OR COALESCE(EXCLUDED.description, courses.description) IS DISTINCT FROM courses.description
                            THEN NULL
                            ELSE courses.ai_summary
                        END,
                        is_ai_processed = CASE
                            WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                              OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                              OR COALESCE(EXCLUDED.description, courses.description) IS DISTINCT FROM courses.description
                            THEN FALSE
                            ELSE courses.is_ai_processed
                        END,
                        ai_title_processed = CASE
                            WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                              OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                            THEN FALSE
                            ELSE courses.ai_title_processed
                        END,
                        ai_title_confidence = CASE
                            WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                              OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                            THEN NULL
                            ELSE courses.ai_title_confidence
                        END,
                        ai_title_result = CASE
                            WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                              OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                            THEN NULL
                            ELSE courses.ai_title_result
                        END,
                        schedule_days = EXCLUDED.schedule_days,
                        schedule_time_start = EXCLUDED.schedule_time_start,
                        schedule_time_end = EXCLUDED.schedule_time_end,
                        schedule_frequency = EXCLUDED.schedule_frequency,
                        schedule_duration_minutes = EXCLUDED.schedule_duration_minutes,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id
                """, course_data)
                
            return True
        except Exception as e:
            logger.error(
                "HOMEPLUS course save failed. branch_id=%s branch_code=%s url=%s title=%s error=%s",
                course_data.get("branch_id"),
                course_data.get("branch_code"),
                course_data.get("raw_url"),
                course_data.get("title"),
                e,
            )
            return False

    def run(self, limit: Optional[int] = None, branch_code: Optional[str] = None, branch_name: Optional[str] = None):
        crawl_started_at = utc_now()
        if not getattr(self, "driver", None):
            logger.error("HOMEPLUS Selenium driver is not initialized. Skipping crawl.")
            return

        # 1. Get Branches
        branches = self.scrape_branches()
        if not branches:
            logger.error("No HOMEPLUS branches were scraped. Skipping course crawl and stale cleanup.")
            return
        
        # 2. Scrape all branches
        saved_branch_ids = []
        for branch in branches:
            if branch_code and clean_text(branch.get("branch_code")) != clean_text(branch_code):
                continue
            if branch_name and clean_text(branch.get("name")) != clean_text(branch_name):
                continue
            bid = self.save_branch(branch)
            if bid:
                 saved_branch_ids.append((branch['branch_code'], bid))
        if not saved_branch_ids:
            logger.error("No HOMEPLUS branches were saved. Skipping course crawl and stale cleanup.")
            return

        total_saved = 0
        for code, bid in saved_branch_ids:
             remaining = None if limit is None else max(limit - total_saved, 0)
             if remaining == 0:
                 break
             total_saved += self.scrape_courses(code, bid, max_courses=remaining)

        if limit is None and not branch_code and not branch_name:
            if total_saved <= 0:
                logger.error("HOMEPLUS saved 0 courses. Skipping stale cleanup to avoid deactivating valid data.")
                return
            stale_count = mark_stale_courses('HOMEPLUS', crawl_started_at)
            logger.info(f"Marked stale HOMEPLUS courses inactive: {stale_count}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Homeplus Culture Center Crawler')
    parser.add_argument('--limit', type=int, help='Maximum number of courses to save')
    parser.add_argument('--branch-code', help='Only crawl one branch code')
    parser.add_argument('--branch-name', help='Only crawl one branch name')
    args = parser.parse_args()

    crawler = HomeplusCrawler()
    crawler.run(limit=args.limit, branch_code=args.branch_code, branch_name=args.branch_name)
