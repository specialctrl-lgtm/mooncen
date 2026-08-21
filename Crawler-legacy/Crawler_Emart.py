"""
이마트문화센터 크롤러 - Selenium 기반 재구현
URL: https://www.cultureclub.emart.com/enrolment
구조: React SPA, Checkbox 필터, Infinite Scroll/Pagination (추정), NetFunnel 존재 가능성
"""
import sys
import os
import time
import re
import json
from typing import List, Dict, Optional
from datetime import datetime

import requests

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_cursor
from DB.course_lifecycle import enrich_course_lifecycle, mark_stale_courses, should_skip_expired_course, utc_now
from DB.course_upsert_guards import coalesce_provider_course_id_by_raw_url
from utils import setup_logger, parse_date, extract_number, extract_material_fee_amount, infer_course_status, clean_instructor_name, clean_text
from data_parser import TargetParser, ScheduleParser, parse_crawler_target
from title_cleaner import clean_course_title
from target_cleaner import extract_target_text
from target_category_fallback import infer_age_group_from_category
from Crawler.reception_period import format_apply_period_raw
from Crawler.Config import PROVIDERS, HEADERS, CRAWLER_CONFIG, COURSE_STATUS_MAP

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from Crawler.selenium_driver import build_chrome_driver

# 로거 설정
logger = setup_logger(__name__, 'logs/crawler_emart.log')
parse_error_logger = setup_logger('parse_errors', 'logs/parse_errors.log')

EMART_GRAPHQL_ENDPOINT = os.getenv(
    "EMART_GRAPHQL_ENDPOINT",
    "https://tjcdarnuonge5epm44y2nvckk4.appsync-api.ap-northeast-2.amazonaws.com/graphql",
)
EMART_GRAPHQL_API_KEY = os.getenv("EMART_GRAPHQL_API_KEY", "da2-boxa6uma4vgkjewn7ayx45tl4i")
EMART_CLOUDFRONT_BASE = os.getenv("EMART_CLOUDFRONT_BASE", "https://d24y2yfxh2iebm.cloudfront.net")
EMART_DEFAULT_SEMESTER_URL = f"{EMART_CLOUDFRONT_BASE}/public/default/defaultSemester.json"
EMART_GRAPHQL_PAGE_SIZE = int(os.getenv("EMART_GRAPHQL_PAGE_SIZE", "500"))
EMART_GRAPHQL_MAX_PAGES = int(os.getenv("EMART_GRAPHQL_MAX_PAGES", "30"))

EMART_CLASS_FILTER_QUERY = """
query getClassByFiltering($keyword: String, $filterData: [FilterData], $sortKey: String, $from: Int, $size: Int) {
  getClassByFiltering(keyword: $keyword, filterData: $filterData, sortKey: $sortKey, from: $from, size: $size) {
    total
    data {
      classId
      initialClassId
      instructorId
      classStatus
      classStatusBO
      classStatusTeacher
      classFlag
      classTitle
      classDay
      classTime {
        startTime
        endTime
      }
      mainCategory {
        categoryCode
        categoryName
      }
      subCategory {
        categoryCode
        categoryName
      }
      mainStoreInfo {
        storeName
        storeNickName
        storeCode
        storeCenter
      }
      classroom
      classTimes
      semesterYear
      semester
      classOriginalFee
      classFee
      classMaterialFee
      classType
      occupiedFullFlag
      channel {
        online
        offline
      }
      classDateInfo {
        classStartDate
        classEndDate
        classClosedDate
        classRegisterStartDate
        classRegisterEndDate
        classCancelStartDate
        classCancelEndDate
      }
      classDetail {
        classDetailInfo {
          classDetailInfoTitle
          classDetailInfoContent
        }
      }
      mainImage {
        bucket
        region
        key
      }
      categoryImage {
        bucket
        region
        key
      }
      materialCalculate {
        materialFee
      }
    }
  }
}
"""

class EmartCrawler:
    """이마트문화센터 크롤러 (Selenium)"""
    
    def __init__(self):
        self.config = PROVIDERS['EMART']
        self.base_url = self.config['base_url']
        self.list_url = self.config['course_list_url']
        self.target_parser = TargetParser()
        self.schedule_parser = ScheduleParser()
        
        # Selenium Driver 초기화
        self._init_driver()
        logger.info("Emart Crawler initialized (Selenium)")

    @staticmethod
    def _as_dict(value: object) -> Dict:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _graphql_label(value: object, *keys: str) -> str:
        if isinstance(value, dict):
            for key in keys:
                text = clean_text(value.get(key))
                if text:
                    return text
            return ""
        return clean_text(value)

    def _init_driver(self):
        """Selenium WebDriver 설정"""
        try:
            options = Options()
            options.add_argument('--headless') # 디버깅 중엔 주석 처리 권장
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
            # 봇 탐지 우회
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)

            self.driver = build_chrome_driver(options)
            self.wait = WebDriverWait(self.driver, 20)
            
        except Exception as e:
            logger.error(f"Failed to initialize Selenium: {e}")
            raise

    def __del__(self):
        """소멸자: 브라우저 종료"""
        if hasattr(self, 'driver'):
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
            r"\(([^)]*(?:\d{2}~\d{2}년생|보호자\s*\d+인|성인|유아|아동|초등|중등|고등)[^)]*)\)",
            r"(\d{2}~\d{2}년생(?:,\s*보호자\s*\d+인)?)",
            r"(성인|유아|아동|초등생?|중학생|고등학생|보호자\s*\d+인)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return clean_text(match.group(1))
        return None

    def _extract_date_range_from_text(self, text: str):
        if not text:
            return None, None

        current_year = datetime.now().year

        match = re.search(r"\((\d{2})\.(\d{2})-(\d{2})\.(\d{2})\)", text)
        if match:
            sm, sd, em, ed = match.groups()
            return (
                parse_date(f"{current_year}-{sm}-{sd}"),
                parse_date(f"{current_year}-{em}-{ed}"),
            )

        match = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\s*[~-]\s*(\d{4})\.(\d{2})\.(\d{2})", text)
        if match:
            sy, sm, sd, ey, em, ed = match.groups()
            return (
                parse_date(f"{sy}-{sm}-{sd}"),
                parse_date(f"{ey}-{em}-{ed}"),
            )

        return None, None

    def _wait_for_loading(self):
        """로딩 인디케이터나 대기열 대기"""
        try:
            # NetFunnel 대기 (화면에 존재할 경우)
            # 이마트는 별도의 로딩 오버레이가 있을 수 있음
            time.sleep(1) # 기본 대기
        except:
            pass

    def _parse_yyyymmdd(self, value):
        if value is None:
            return None
        text = str(value).strip()
        if re.fullmatch(r"\d{8}(?:\d{4,6})?", text):
            try:
                return datetime.strptime(text[:8], "%Y%m%d").date()
            except ValueError:
                return None
        return parse_date(text)

    def _format_yyyymmdd(self, value: object) -> str:
        text = str(value or "").strip()
        if re.fullmatch(r"\d{8}", text):
            return f"{text[4:6]}.{text[6:8]}"
        return text

    def _format_hhmm(self, value: object) -> str:
        text = re.sub(r"\D", "", str(value or ""))
        if not text:
            return ""
        if len(text) == 3:
            text = f"0{text}"
        if len(text) >= 4:
            return f"{text[:2]}:{text[2:4]}"
        return text

    def _get_current_semester_filters(self) -> list[dict]:
        try:
            response = requests.get(EMART_DEFAULT_SEMESTER_URL, timeout=15)
            response.raise_for_status()
            semester = json.loads(response.content.decode("utf-8"))
            semester_name = str(semester.get("semester") or "").strip()
            semester_year = str(semester.get("semesterYear") or "").strip()
            filters = []
            if semester_name:
                filters.append({"type": "semester", "data": [semester_name]})
            if semester_year:
                filters.append({"type": "semesterYear", "data": [semester_year]})
            return filters
        except Exception as exc:
            logger.warning("Failed to load EMART current semester metadata. url=%s error=%s", EMART_DEFAULT_SEMESTER_URL, exc)
            return []

    def _fetch_graphql_courses(self, branch_code: str, offset: int, size: int) -> Optional[dict]:
        filter_data = [{"type": "mainStoreInfo.storeCode", "data": [str(branch_code)]}]
        filter_data.extend(self._get_current_semester_filters())
        payload = {
            "query": EMART_CLASS_FILTER_QUERY,
            "variables": {
                "keyword": "",
                "filterData": filter_data,
                "sortKey": "deadline",
                "from": offset,
                "size": size,
            },
        }
        headers = {
            "content-type": "application/json",
            "x-api-key": EMART_GRAPHQL_API_KEY,
            "user-agent": HEADERS.get("User-Agent", "Mozilla/5.0"),
        }
        response = requests.post(EMART_GRAPHQL_ENDPOINT, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            raise RuntimeError(body["errors"])
        return (body.get("data") or {}).get("getClassByFiltering")

    def _image_url_from_graphql(self, row: Dict) -> Optional[str]:
        for field, prefix in (("mainImage", "resized/thumbnail"), ("categoryImage", "resized/thumbnail")):
            image = self._as_dict(row.get(field))
            key = image.get("key")
            if key:
                if str(key).startswith("http"):
                    return str(key)
                return f"{EMART_CLOUDFRONT_BASE}/{prefix}/{str(key).lstrip('/')}"
        return None

    def _description_from_graphql(self, row: Dict) -> Optional[str]:
        detail = self._as_dict(row.get("classDetail"))
        sections = detail.get("classDetailInfo") or []
        if isinstance(sections, dict):
            sections = [sections]
        elif isinstance(sections, str):
            return clean_text(sections) or None
        elif not isinstance(sections, list):
            sections = []
        lines = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            title = clean_text(section.get("classDetailInfoTitle"))
            content = clean_text(section.get("classDetailInfoContent"))
            if title and content:
                lines.append(f"{title}: {content}")
            elif content:
                lines.append(content)
        return "\n".join(lines) or None

    def _schedule_from_graphql(self, row: Dict) -> str:
        days = row.get("classDay") or []
        if isinstance(days, str):
            day_text = days
        else:
            day_text = ",".join(str(day) for day in days if day)

        class_time = self._as_dict(row.get("classTime"))
        start_time = self._format_hhmm(class_time.get("startTime"))
        end_time = self._format_hhmm(class_time.get("endTime"))
        time_text = f"{start_time}-{end_time}" if start_time and end_time else start_time or end_time

        date_info = self._as_dict(row.get("classDateInfo"))
        start_date = self._format_yyyymmdd(date_info.get("classStartDate"))
        end_date = self._format_yyyymmdd(date_info.get("classEndDate"))
        period_text = ""
        if start_date and end_date:
            period_text = f"({start_date}-{end_date})"
        elif start_date:
            period_text = f"({start_date})"

        sessions = extract_number(row.get("classTimes"))
        session_text = f"{sessions}회" if sessions else ""
        return clean_text(" ".join(part for part in (day_text, time_text, period_text, session_text) if part))

    def _course_data_from_graphql(self, row: Dict, branch_id: str, branch_code: str) -> Optional[Dict]:
        class_id = row.get("classId")
        title = clean_text(row.get("classTitle"))
        if not class_id or not title:
            return None

        main_category = row.get("mainCategory")
        sub_category = row.get("subCategory")
        date_info = self._as_dict(row.get("classDateInfo"))
        material_calculate = self._as_dict(row.get("materialCalculate"))
        category = self._graphql_label(sub_category, "categoryName") or self._graphql_label(main_category, "categoryName")
        status_text = " ".join(
            str(value or "")
            for value in (
                row.get("classStatus"),
                row.get("classStatusBO"),
                row.get("classStatusTeacher"),
                row.get("occupiedFullFlag"),
            )
        )
        material_fee = extract_number(row.get("classMaterialFee")) or extract_number(material_calculate.get("materialFee"))
        description = self._description_from_graphql(row)
        apply_start_raw = date_info.get("classRegisterStartDate")
        apply_end_raw = date_info.get("classRegisterEndDate")

        return {
            "branch_id": branch_id,
            "provider": "EMART",
            "provider_course_id": f"{branch_code}:{class_id}",
            "title": title,
            "raw_url": f"{self.base_url}/class/{class_id}",
            "category_raw": category,
            "fee": extract_number(row.get("classFee")),
            "material_fee": material_fee,
            "sessions": extract_number(row.get("classTimes")),
            "schedule_raw": self._schedule_from_graphql(row),
            "description": description,
            "image_url": self._image_url_from_graphql(row),
            "status": infer_course_status(status_text, title, default="OPEN"),
            "start_date": self._parse_yyyymmdd(date_info.get("classStartDate")),
            "end_date": self._parse_yyyymmdd(date_info.get("classEndDate")),
            "apply_start": self._parse_yyyymmdd(apply_start_raw),
            "apply_end": self._parse_yyyymmdd(apply_end_raw),
            "apply_period_raw": format_apply_period_raw(apply_start_raw, apply_end_raw),
        }

    def _existing_branches_from_db(self) -> List[Dict]:
        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT provider, branch_code, name, address, phone
                    FROM branches
                    WHERE provider = 'EMART'
                      AND branch_code IS NOT NULL
                    ORDER BY branch_code
                    """
                )
                rows = cursor.fetchall()
            branches = [dict(row) for row in rows]
            if branches:
                logger.warning("Using %s existing EMART branches from DB as branch-list fallback.", len(branches))
            return branches
        except Exception as exc:
            logger.warning("Failed to load existing EMART branches from DB: %s", exc)
            return []

    def scrape_courses_via_graphql(
        self,
        branch_code: str,
        branch_id: str,
        max_courses: Optional[int] = None,
    ) -> Optional[int]:
        total_saved = 0
        offset = 0
        page_size = max(1, min(EMART_GRAPHQL_PAGE_SIZE, max_courses or EMART_GRAPHQL_PAGE_SIZE))

        try:
            for page_index in range(EMART_GRAPHQL_MAX_PAGES):
                if max_courses is not None and total_saved >= max_courses:
                    break
                remaining = None if max_courses is None else max_courses - total_saved
                current_size = page_size if remaining is None else min(page_size, remaining)
                result = self._fetch_graphql_courses(branch_code, offset, current_size)
                rows = (result or {}).get("data") or []
                total = int((result or {}).get("total") or 0)
                logger.info(
                    "EMART GraphQL branch=%s offset=%s size=%s rows=%s total=%s",
                    branch_code,
                    offset,
                    current_size,
                    len(rows),
                    total,
                )
                if not rows:
                    break

                for row in rows:
                    course_data = self._course_data_from_graphql(row, branch_id, branch_code)
                    if not course_data:
                        continue
                    if self.save_course(course_data):
                        total_saved += 1
                        if max_courses is not None and total_saved >= max_courses:
                            break

                offset += len(rows)
                if offset >= total or len(rows) < current_size:
                    break

            return total_saved
        except Exception as exc:
            logger.warning("EMART GraphQL crawl failed for branch %s. Falling back to Selenium DOM. error=%s", branch_code, exc)
            return None

    def _looks_like_maintenance_page(self) -> bool:
        try:
            page_text = f"{self.driver.title}\n{self.driver.page_source}".lower()
        except Exception:
            return False

        maintenance_tokens = (
            "maintenance",
            "inspection",
            "temporarily unavailable",
            "service unavailable",
            "점검",
            "서비스 점검",
            "시스템 점검",
            "잠시 후",
        )
        return any(token in page_text for token in maintenance_tokens)

    def _expand_course_list(self, max_courses: Optional[int] = None, max_rounds: int = 80) -> int:
        """Load additional EMART rows by clicking more/next controls or scrolling."""
        selectors = [
            "button.btn-more",
            ".btn-more",
            "button.more",
            ".more button",
            "button[aria-label*='더보기']",
            "a[aria-label*='다음']",
            "button[aria-label*='다음']",
            ".pagination .next",
        ]
        last_count = 0
        stable_rounds = 0

        for round_index in range(max_rounds):
            items = self.driver.find_elements(By.CSS_SELECTOR, ".cls-item")
            current_count = len(items)
            if max_courses is not None and current_count >= max_courses:
                break
            if current_count == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_count = current_count

            clicked = False
            for selector in selectors:
                try:
                    buttons = [
                        elem for elem in self.driver.find_elements(By.CSS_SELECTOR, selector)
                        if elem.is_displayed() and elem.is_enabled()
                    ]
                    if not buttons:
                        continue
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", buttons[0])
                    time.sleep(0.2)
                    self.driver.execute_script("arguments[0].click();", buttons[0])
                    clicked = True
                    logger.info("EMART clicked pagination control %s at round %s", selector, round_index + 1)
                    break
                except Exception:
                    continue

            if not clicked:
                try:
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                except Exception:
                    pass

            time.sleep(1.2)
            new_count = len(self.driver.find_elements(By.CSS_SELECTOR, ".cls-item"))
            if new_count > current_count:
                logger.info("EMART loaded rows: %s -> %s", current_count, new_count)
                continue

            if stable_rounds >= 3 and not clicked:
                break

        final_count = len(self.driver.find_elements(By.CSS_SELECTOR, ".cls-item"))
        logger.info("EMART expanded visible course rows to %s", final_count)
        return final_count

    def _snapshot_course_items(self, max_courses: Optional[int] = None) -> List[Dict]:
        """Read visible EMART list rows before visiting detail pages resets the SPA."""
        snapshots = []
        seen = set()
        for item in self.driver.find_elements(By.CSS_SELECTOR, ".cls-item"):
            if max_courses is not None and len(snapshots) >= max_courses:
                break
            try:
                title_elem = item.find_element(By.CSS_SELECTOR, ".cls-title a")
                detail_url = title_elem.get_attribute("href")
                title = title_elem.text.strip()
                if not detail_url or detail_url in seen:
                    continue
                seen.add(detail_url)

                try:
                    category = item.find_element(By.CSS_SELECTOR, ".cls-cate").text.strip()
                except Exception:
                    category = "Unknown"

                try:
                    fee_text = item.find_element(By.CSS_SELECTOR, ".cls-price").text.strip()
                    fee = extract_number(fee_text)
                except Exception:
                    fee = 0

                try:
                    info_values = [elem.text.strip() for elem in item.find_elements(By.CSS_SELECTOR, ".cls-inf-des")]
                    list_branch_name = info_values[0] if info_values else ""
                    schedule_text = info_values[1] if len(info_values) > 1 else ""
                except Exception:
                    list_branch_name = ""
                    schedule_text = ""

                try:
                    img_url = item.find_element(By.CSS_SELECTOR, "img.cls-img").get_attribute("src")
                    if img_url and img_url.startswith("/"):
                        img_url = f"{self.base_url}{img_url}"
                except Exception:
                    img_url = None

                snapshots.append({
                    "detail_url": detail_url,
                    "title": title,
                    "category": category,
                    "branch_name": clean_text(list_branch_name),
                    "fee": fee,
                    "schedule_raw": schedule_text,
                    "image_url": img_url,
                })
            except Exception as exc:
                logger.warning("Failed to snapshot EMART list item: %s", exc)
        logger.info("EMART snapshotted %s list rows before detail crawl", len(snapshots))
        return snapshots

    def _select_only_branch(self, branch_code: str) -> bool:
        """Ensure a single EMART branch checkbox is selected before reading results."""
        try:
            for checkbox in self.driver.find_elements(By.NAME, "storeChk"):
                code = checkbox.get_attribute("data-code")
                if code != branch_code and checkbox.is_selected():
                    self.driver.execute_script("arguments[0].click();", checkbox)
                    time.sleep(0.1)

            target = self.driver.find_element(By.CSS_SELECTOR, f"input[name='storeChk'][data-code='{branch_code}']")
            if target is None:
                return False

            if not target.is_selected():
                self.driver.execute_script("arguments[0].click();", target)
            self.driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", target)
            return True
        except Exception as exc:
            logger.warning("Failed to select only EMART branch %s: %s", branch_code, exc)
            return False

    def scrape_branches(self) -> List[Dict]:
        """지점 정보 수집 (체크박스 스캔)"""
        logger.info("Scraping branches...")
        branches = []
        try:
            self.driver.get(self.list_url)
            self._wait_for_loading()
            
            # 지점 탭이 열려있는지 확인, 안 열려있으면 클릭할 수도 있으나 기본적으로 열려있는 듯함
            # 체크박스 찾기
            checkboxes = self.wait.until(EC.presence_of_all_elements_located((By.NAME, "storeChk")))
            
            for cb in checkboxes:
                try:
                    code = cb.get_attribute("data-code")
                    name = cb.get_attribute("value")
                    
                    if code and name:
                        branches.append({
                            'provider': 'EMART',
                            'branch_code': code,
                            'name': name.strip() + "점", # value에 '점'이 빠져있는 경우가 많음 (예: "구로")
                            'address': '', # 목록에서는 알 수 없음
                            'phone': ''
                        })
                except Exception as e:
                    continue
                    
            logger.info(f"Found {len(branches)} branches")
            
        except Exception as e:
            if self._looks_like_maintenance_page():
                logger.error("EMART site appears to be under maintenance. Branch scraping skipped.")
            else:
                logger.error("EMART branch crawl failed. url=%s error=%s", self.list_url, e)
            branches = self._existing_branches_from_db()
            
        return branches

    def save_branch(self, branch_data: Dict) -> Optional[str]:
        """지점 저장 (crawler_Lotte.py와 동일 로직)"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO branches (provider, branch_code, name, address, phone)
                    VALUES (%(provider)s, %(branch_code)s, %(name)s, %(address)s, %(phone)s)
                    ON CONFLICT (provider, branch_code) 
                    DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                """, branch_data)
                result = cursor.fetchone()
                return str(result['id'])
        except Exception as e:
            logger.error(
                "EMART branch save failed. branch=%s branch_code=%s error=%s",
                branch_data.get("name"),
                branch_data.get("branch_code"),
                e,
            )
            return None

    def scrape_courses(
        self,
        branch_code: str,
        branch_id: str,
        branch_name: Optional[str] = None,
        max_courses: Optional[int] = None,
    ) -> int:
        """특정 지점의 강좌 수집"""
        logger.info(f"Scraping courses for branch code: {branch_code}")
        count = 0
        try:
            self.driver.get(self.list_url)
            self._wait_for_loading()
            
            # 1. 모든 체크박스 해제 (기본적으로 전체 선택되어 있거나 할 수 있음)
            # 페이지 로드 시 아무것도 선택 안된 상태가 기본이면 생략 가능
            # 이마트는 '지점' 탭에서 원하는 지점을 클릭해야 함
            
            # 2. 해당 지점 체크박스 찾기 및 클릭
            # JavaScript로 클릭하는 것이 더 안정적일 수 있음
            try:
                if not self._select_only_branch(branch_code):
                    raise NoSuchElementException(f"Branch checkbox not found or could not be selected: {branch_code}")
                logger.info(f"Selected branch {branch_code}")
                time.sleep(2) # 리스트 갱신 대기
            except NoSuchElementException:
                logger.warning("EMART branch checkbox not found. branch_code=%s url=%s", branch_code, self.list_url)
                return 0

            # 3. 강좌 리스트 로딩 대기
            try:
                self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".cls-item")))
            except TimeoutException:
                logger.warning("EMART no courses found. branch_code=%s branch=%s url=%s", branch_code, branch_name, self.list_url)
                return 0

            items_len = self._expand_course_list(max_courses)
            logger.info(f"Found {items_len} items (visible)")

            course_snapshots = self._snapshot_course_items(max_courses)

            for i, snapshot in enumerate(course_snapshots):
                if max_courses is not None and count >= max_courses:
                    logger.info(f"Limit reached for branch {branch_code}: {count}/{max_courses}")
                    break

                try:
                    detail_url = snapshot["detail_url"]
                    title = snapshot["title"]
                    category = snapshot["category"]
                    schedule_text = snapshot["schedule_raw"]
                    list_branch_name = snapshot.get("branch_name") or ""
                    if branch_name and list_branch_name:
                        expected = branch_name.replace("점점", "점").strip()
                        actual = list_branch_name.replace("점점", "점").strip()
                        if expected not in actual and actual not in expected:
                            logger.warning(
                                "Skipping EMART course with mismatched branch. selected=%s list=%s title=%s url=%s",
                                expected,
                                actual,
                                title,
                                detail_url,
                            )
                            continue

                    if detail_url:
                        # provider_course_id 추출
                        match = re.search(r'/class/([^/?]+)', detail_url)
                        class_id = match.group(1) if match else "UNKNOWN"
                        provider_course_id = f"{branch_code}:{class_id}"

                        course_data = {
                            'branch_id': branch_id,
                            'provider': 'EMART',
                            'provider_course_id': provider_course_id,
                            'title': title,
                            'raw_url': detail_url,
                            'category_raw': category,
                            'fee': snapshot["fee"],
                            'schedule_raw': schedule_text,
                            'image_url': snapshot["image_url"],
                            'status': infer_course_status(title, category, schedule_text)
                        }

                        course_data['target'] = self._extract_target_from_text(title)
                        start_date, end_date = self._extract_date_range_from_text(schedule_text)
                        if start_date:
                            course_data['start_date'] = start_date
                        if end_date:
                            course_data['end_date'] = end_date
                        
                        # Scrape detail page for description and instructor
                        # 저장 전 상세 페이지 진입 시 목록 페이지가 리셋될 수 있음.
                        # 상세 페이지 수집 후 뒤로가기 하면 DOM이 새로 생성되므로
                        # 반드시 루프 시작에서 find_elements를 다시 해야 함.
                        detail_info = self.get_course_detail_info(detail_url)
                        if detail_info:
                            if 'description' in detail_info:
                                course_data['description'] = detail_info['description']
                            if 'instructor' in detail_info:
                                course_data['instructor'] = detail_info['instructor']
                            if 'status' in detail_info:
                                course_data['status'] = detail_info['status']
                        
                        if self.save_course(course_data):
                            count += 1

                except Exception as e:
                    logger.error(
                        "EMART course item failed. branch_code=%s branch=%s item_index=%s url=%s title=%s error=%s",
                        branch_code,
                        branch_name,
                        i,
                        snapshot.get("detail_url") if "snapshot" in locals() else "",
                        snapshot.get("title") if "snapshot" in locals() else "",
                        e,
                    )
                    # 복구 시도 (뒤로가기 등)
                    try:
                        if self.driver.current_url != self.list_url:
                            self.driver.get(self.list_url)
                            self._wait_for_loading()
                            # 체크박스 다시 선택해야 할 수도 있음... 
                            # 이마트 구조상 URL 파라미터로 유지가 안된다면 복잡해짐.
                            # 여기서는 단순 continue
                    except:
                        pass
                    continue
                    
        except Exception as e:
            logger.error("EMART branch course crawl failed. branch_code=%s branch=%s url=%s error=%s", branch_code, branch_name, self.list_url, e)
            
        return count

    def get_course_detail_info(self, detail_url: str) -> Optional[Dict]:
        """상세 페이지에서 description, instructor, target, date 추출"""
        try:
            # 현재 URL 저장
            current_url = self.driver.current_url
            
            # 상세 페이지로 이동
            self.driver.get(detail_url)
            time.sleep(2)
            
            # 폐강/마감 확인
            try:
                alert = self.driver.switch_to.alert
                alert_text = alert.text
                if "폐강" in alert_text or "마감" in alert_text:
                    logger.info(f"Course cancelled (Alert): {alert_text} - {detail_url}")
                    alert.accept()
                    self.driver.get(current_url)
                    return None
            except:
                pass

            # 페이지 내 폐강 텍스트 확인
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            if "폐강된 강좌" in body_text:
                logger.info(f"Course cancelled (Text): {detail_url}")
                self.driver.get(current_url)
                return None
            
            result = {
                "status": infer_course_status(body_text, default="OPEN")
            }
            
            # Description 추출 시도
            description = None
            desc_selectors = [
                '.clsdtl-itr-cont',  # 실제 description 위치
                '.cls-detail-content',
                '.course-description',
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

            image_url = None
            image_selectors = [
                'meta[property="og:image"]',
                '.clsdtl-thumb img',
                '.clsdtl-img img',
                '.cls-detail img',
                'img.cls-img',
                'img[src*="upload"]',
            ]
            for selector in image_selectors:
                try:
                    elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                    image_url = elem.get_attribute('content') or elem.get_attribute('src') or elem.get_attribute('data-src')
                    if image_url:
                        if image_url.startswith('/'):
                            image_url = f"{self.base_url}{image_url}"
                        result['image_url'] = image_url
                        break
                except:
                    continue
            
            # Instructor 추출 시도
            instructor = None
            instructor_selectors = [
                '.clsdtl-lecr-name',  # 실제 instructor 위치
                '.lecturer-name',
                '.instructor-name',
            ]
            
            for selector in instructor_selectors:
                try:
                    elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                    instructor_text = clean_text(elem.text)
                    # "이수연 강사님" -> "이수연"
                    instructor = clean_instructor_name(instructor_text)
                    if instructor:
                        break
                except:
                    continue
            
            if instructor:
                result['instructor'] = instructor

            target = None
            target_selectors = [
                '.clsdtl-info-target',
                '.clsdtl-target',
                '.course-target',
            ]
            for selector in target_selectors:
                try:
                    elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                    target_text = clean_text(elem.text)
                    if target_text:
                        target = target_text.replace('대상', '').strip(' :')
                        break
                except:
                    continue

            if not target:
                target = self._extract_target_from_text(body_text)

            if target:
                result['target'] = target

            start_date, end_date = self._extract_date_range_from_text(body_text)
            if start_date:
                result['start_date'] = start_date
            if end_date:
                result['end_date'] = end_date
            
            # 원래 페이지로 돌아가기
            self.driver.get(current_url)
            time.sleep(1)
            
            return result if result else None
            
        except Exception as e:
            logger.error("EMART detail scrape failed. url=%s error=%s", detail_url, e)
            # 오류 발생 시 원래 페이지로 돌아가기 시도
            try:
                self.driver.get(current_url)
            except:
                pass
            return None

    def scrape_course_detail(self, url: str, branch_id: str) -> bool:
        """상세 페이지 수집"""
        try:
            self.driver.get(url)
            time.sleep(1)
            
            # 폐강/마감 확인
            # 1. Alert 확인 (일부 사이트는 진입 시 alert 띄우고 뒤로가기 시킴)
            try:
                alert = self.driver.switch_to.alert
                alert_text = alert.text
                if "폐강" in alert_text or "마감" in alert_text:
                    logger.info(f"Course cancelled (Alert): {alert_text} - {url}")
                    alert.accept()
                    return False
            except:
                pass

            # 2. 페이지 내 텍스트 확인
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            if "폐강된 강좌" in body_text:
                logger.info(f"Course cancelled (Text): {url}")
                return False
            
            # 기본 데이터 추출
            try:
                title_elem = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "h3.tit, .cls-title, .title")))
                title = title_elem.text
            except:
                # 타이틀을 못 찾았는데 폐강 문구도 없다면? 
                # 구조가 다를 수 있음. 일단 Unknown 처리하거나 return False
                # 폐강 강좌 페이지는 타이틀이 없을 수도 있음.
                if "폐강" in body_text: 
                     return False
                title = "Unknown"

            # provider_course_id 추출 (URL에서)
            # URL format: .../class/CODE
            match = re.search(r'/class/([^/?]+)', url)
            provider_course_id = match.group(1) if match else "UNKNOWN"
            
            # Selector 조정 필요 (emart_new_test.html에는 상세페이지 내용은 없음)
            # 여기서는 일반적인 추정 selector 사용 후, 실패 시 html 덤프 떠서 확인 필요
            
            course_data = {
                'branch_id': branch_id,
                'provider': 'EMART',
                'provider_course_id': provider_course_id,
                'title': title,
                'raw_url': url,
                'status': infer_course_status(body_text, title)
            }
            
            # 추가 필드들 (가격, 등등)
            # ... 상세 페이지 구조를 아직 모르므로 최소 정보만 저장
            
            return self.save_course(course_data)
            
        except Exception as e:
            logger.error("EMART standalone detail scrape failed. url=%s error=%s", url, e)
            return False

    def save_course(self, course_data: Dict) -> bool:
        """DB 저장"""
        try:
            # 필수 필드 채우기
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
            # 기존 데이터 유지하며 defaults 병합
            for k, v in defaults.items():
                if k not in course_data:
                    course_data[k] = v
            course_data['instructor'] = clean_instructor_name(course_data.get('instructor'))

            if not course_data.get('description'):
                course_data['description'] = course_data.get('target') or course_data.get('title')

            raw_title = course_data.get('title') or ''
            clean_title, removed_title_prefix = clean_course_title(raw_title)
            course_data['title_raw'] = raw_title
            course_data['title'] = clean_title
            course_data['title_prefix_removed'] = removed_title_prefix or None

            explicit_target = extract_target_text(raw_title)
            if explicit_target:
                course_data['target'] = explicit_target

            # 1. Target Parsing
            # 제목과 타겟 텍스트를 조합하여 분석
            target_source = f"{course_data['title']} {course_data.get('target', '')}"
            parsed_target = parse_crawler_target(target_source, self.target_parser)
            course_data.update({
                'target_age_group': parsed_target['age_group'],
                'target_min_age': parsed_target['min_age'],
                'target_max_age': parsed_target['max_age'],
                'target_with_parent': parsed_target['with_parent'],
                'target_tags': parsed_target['tags'],
                'target_age_is_explicit': parsed_target.get('age_is_explicit', False)
            })
            if not course_data.get('target_age_group'):
                course_data['target_age_group'] = infer_age_group_from_category(course_data.get('category_raw'))

            # 2. Schedule Parsing
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
                logger.info("Skipping expired EMART course: %s", course_data.get('title'))
                return False
 
            enrich_course_lifecycle(course_data)

            with get_db_cursor() as cursor:
                coalesce_provider_course_id_by_raw_url(cursor, course_data, logger)
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
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id
                """, course_data)
                logger.info(f"Saved course: {course_data['title']} (Age: {course_data['target_age_group']})")
                return True

        except Exception as e:
            logger.error(
                "EMART course save failed. branch_id=%s branch_code=%s url=%s title=%s error=%s",
                course_data.get("branch_id"),
                course_data.get("branch_code"),
                course_data.get("raw_url"),
                course_data.get("title"),
                e,
            )
            return False

    def run(self, limit: Optional[int] = None, branch_code: Optional[str] = None, branch_name: Optional[str] = None):
        """실행"""
        # 지점 수집
        crawl_started_at = utc_now()
        branches = self.scrape_branches()
        if not branches:
            logger.error("No EMART branches were scraped. Skipping course crawl and stale cleanup.")
            return False
        
        # 모든 지점 수집
        saved_branch_ids = []
        for branch in branches:
            if branch_code and clean_text(branch.get("branch_code")) != clean_text(branch_code):
                continue
            if branch_name and clean_text(branch.get("name")) != clean_text(branch_name):
                continue
            branch_id = self.save_branch(branch)
            if branch_id:
                saved_branch_ids.append((branch['branch_code'], branch_id, branch.get('name')))
        if not saved_branch_ids:
            logger.error("No EMART branches were saved. Skipping course crawl and stale cleanup.")
            return False
        
        # 강좌 수집
        total_saved = 0
        for code, bid, name in saved_branch_ids:
             remaining = None if limit is None else max(limit - total_saved, 0)
             if remaining == 0:
                 break
             total_saved += self.scrape_courses(code, bid, branch_name=name, max_courses=remaining)

        if limit is None and not branch_code and not branch_name:
            if total_saved <= 0:
                logger.error("EMART saved 0 courses. Skipping stale cleanup to avoid deactivating valid data.")
                return False
            stale_count = mark_stale_courses('EMART', crawl_started_at)
            logger.info(f"Marked stale EMART courses inactive: {stale_count}")
        return True

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Emart Culture Center Crawler')
    parser.add_argument('--limit', type=int, help='Maximum number of courses to save')
    parser.add_argument('--branch-code', help='Only crawl one branch code')
    parser.add_argument('--branch-name', help='Only crawl one branch name')
    args = parser.parse_args()

    crawler = EmartCrawler()
    success = crawler.run(limit=args.limit, branch_code=args.branch_code, branch_name=args.branch_name)
    sys.exit(0 if success else 2)
