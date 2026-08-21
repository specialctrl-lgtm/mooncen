"""
롯데문화센터 크롤러 - 실제 구현
URL: https://culture.lotteshopping.com/index.do
"""
import sys
import os
import time
import re
import requests
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional, Tuple
from datetime import date, datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_cursor
from DB.course_lifecycle import enrich_course_lifecycle, mark_stale_courses, should_skip_expired_course, utc_now
from DB.course_upsert_guards import coalesce_provider_course_id_by_raw_url, normalize_course_raw_url
from utils.course_semantic_eligibility import (
    CourseSemanticEligibilityError,
    guard_course_before_upsert,
)
from utils import setup_logger, parse_date, extract_number, extract_material_fee_amount, infer_course_status, clean_instructor_name, clean_text
from data_parser import TargetParser, ScheduleParser, parse_crawler_target
from title_cleaner import clean_course_title
from target_cleaner import extract_target_text
from description_cleaner import clean_lotte_description_text
from Crawler.reception_period import extract_reception_period, parse_reception_period_text
from Crawler.Config import PROVIDERS, HEADERS, COURSE_STATUS_MAP
from Crawler.selenium_driver import build_chrome_driver
from utils.outbound_http import SafeSession
from utils.url_security import safe_external_http_url, sanitize_course_payload
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

logger = setup_logger(__name__, 'logs/crawler_lotte.log')
parse_error_logger = setup_logger('parse_errors', 'logs/parse_errors.log')
MAX_COURSE_LIMIT = 100_000
MAX_BROWSER_HTML_BYTES = 8 * 1024 * 1024
MAX_TOLERATED_UNAVAILABLE_DETAILS = 5
MAX_TOLERATED_UNAVAILABLE_DETAIL_RATIO = 0.001
LOTTE_UNAVAILABLE_DETAIL_MARKERS = (
    "\uc694\uccad\ud558\uc2e0 \ud398\uc774\uc9c0\uac00 \uc874\uc7ac\ud558\uc9c0 \uc54a\uac70\ub098",
    "\uc8fc\uc18c\uac00 \uc774\ub3d9, \uc0ad\uc81c\ub418\uc5c8\uac70\ub098",
)
MAX_NOTICE_ITEMS = 500
DEFAULT_NOTICE_ITEMS = 200
DEFAULT_NOTICE_LOOKBACK_DAYS = 210
DEFAULT_DETAIL_REQUEST_DELAY_SECONDS = 0.2
MAX_DETAIL_REQUEST_DELAY_SECONDS = 5.0
DEFAULT_DETAIL_WORKERS = 4
MAX_DETAIL_WORKERS = 8
LOTTE_BRANCH_RETRY_DELAYS_SECONDS = (5.0, 10.0)

LOTTE_RECEPTION_NOTICE_RE = re.compile(
    r"(?:회원\s*모집|추가\s*모집|(?:접수|수강\s*신청)\s*(?:일정|기간|안내))"
)
LOTTE_FLEX_DATE_RE = re.compile(
    r"(?:(?P<year>\d{2}|20\d{2})\s*(?:년|[.\-/])\s*)?"
    r"(?P<month>1[0-2]|0?[1-9])\s*(?:월|[.\-/])\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])\s*일?"
)


def _trusted_lotte_url(value: object) -> str:
    raw_value = str(value or "")
    if "\\" in raw_value:
        return ""
    candidate = safe_external_http_url(urljoin("https://culture.lotteshopping.com", raw_value))
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "culture.lotteshopping.com"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))

# 롯데문화센터 강좌 구분 (나이별 분류)
LOTTE_COURSE_CATEGORIES = {
    'adult': {
        'name': '성인강좌', 
        'age_group': 'ADULT', 
        'lrclsCtegryCd': '01', 
        'sub_categories': [
            '0101', '0102', '0103', '0104', '0105', '0106', 
            '0107', '0108', '0109', '0110', '0111', '0112'
        ]
    },
    'child': {'name': '아동강좌', 'age_group': 'CHILD', 'lrclsCtegryCd': '03', 'sub_categories': ['0301', '0302', '0303', '0304']},
    'infant': {'name': '영·유아강좌', 'age_group': 'TODDLER', 'lrclsCtegryCd': '02', 'sub_categories': ['0201', '0202', '0203', '0204', '0205']},
}

LOTTE_DESCRIPTION_SELECTORS = [
    '.flow_txt_area .info_img_txt .txt_box',
    '.flow_txt_area .info_img_txt',
    '.info_img_inner .txt_box',
    '.info_img_inner .info_img_txt',
    '.lectExpl',
    'p.desc.lectExpl',
    '.lecture_description',
]

LOTTE_DESCRIPTION_REMOVE_SELECTORS = [
    'script',
    'style',
    'noscript',
    'iframe',
    'button',
    'a',
    'select',
    'option',
    'input',
    '.more_btn_wrap',
    '.content_border',
]

def extract_lotte_description_from_soup(soup: BeautifulSoup) -> Optional[str]:
    for selector in LOTTE_DESCRIPTION_SELECTORS:
        description_elem = soup.select_one(selector)
        if not description_elem:
            continue

        fragment = BeautifulSoup(str(description_elem), 'html.parser')
        for remove_selector in LOTTE_DESCRIPTION_REMOVE_SELECTORS:
            for node in fragment.select(remove_selector):
                node.decompose()

        description = clean_lotte_description_text(fragment.get_text(" ", strip=True))
        if description:
            return description

    return clean_lotte_description_text(soup.get_text(" ", strip=True))


class LotteCrawler:
    """롯데문화센터 크롤러 (Selenium 기반)"""
    
    def __init__(self):
        self.config = PROVIDERS['LOTTE']
        self.base_url = self.config['base_url']
        self.target_parser = TargetParser()
        self.schedule_parser = ScheduleParser()
        self.driver = None
        self.http_session = SafeSession()
        self.had_errors = False
        self.crawl_complete = True
        self._existing_course_ids_by_raw_url: Optional[dict[str, str]] = None
        self._cached_identity_reuse_count = 0
        self._detail_http_success_count = 0
        self._detail_browser_fallback_count = 0
        self._terminal_unavailable_detail_count = 0

    def _terminal_unavailable_detail_is_tolerable(self) -> bool:
        count = self._terminal_unavailable_detail_count
        if count <= 0:
            return True
        attempts = (
            self._detail_http_success_count
            + self._detail_browser_fallback_count
        )
        return bool(
            attempts > 0
            and count <= MAX_TOLERATED_UNAVAILABLE_DETAILS
            and count / attempts <= MAX_TOLERATED_UNAVAILABLE_DETAIL_RATIO
        )

    def _category_to_age_group(self, value: Optional[str]) -> Optional[str]:
        mapping = {
            'adult': 'ADULT',
            'ADULT': 'ADULT',
            'child': 'CHILD',
            'CHILD': 'CHILD',
            'infant': 'TODDLER',
            'INFANT': 'TODDLER',
            'TODDLER': 'TODDLER',
        }
        return mapping.get(value)
    
    def _init_driver(self):
        """Selenium WebDriver 초기화"""
        if self.driver:
            return
        
        try:
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.support.ui import WebDriverWait
            
            options = Options()
            options.add_argument('--headless')  # 창 표시하려면 주석 처리
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            self.driver = build_chrome_driver(options)
            self.driver.set_page_load_timeout(60)  # 페이지 로딩 타임아웃
            self.wait = WebDriverWait(self.driver, 20)
            logger.info("Selenium WebDriver initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize Selenium: {e}")
            raise
    
    def _close_driver(self):
        """WebDriver 종료"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Selenium WebDriver closed")
            except Exception as e:
                logger.error(f"Failed to close driver: {e}")
            finally:
                self.driver = None

    def _navigate(self, value: object) -> str:
        target = _trusted_lotte_url(value)
        if not target:
            raise ValueError("refusing an untrusted LOTTE browser URL")
        self.driver.get(target)
        final_url = _trusted_lotte_url(self.driver.current_url)
        if not final_url:
            raise RuntimeError("LOTTE browser navigation left the approved origin")
        html = self.driver.page_source
        if len(html.encode("utf-8", errors="ignore")) > MAX_BROWSER_HTML_BYTES:
            raise RuntimeError("LOTTE browser page exceeded the HTML size limit")
        return html

    def _http_request_with_retry(self, method: str, url: str, **kwargs):
        kwargs.setdefault("allow_redirects", False)
        for attempt in range(2):
            try:
                response = self.http_session.request(method, url, **kwargs)
                if 300 <= getattr(response, "status_code", 200) < 400:
                    raise requests.TooManyRedirects("LOTTE provider redirects are not allowed")
                response.raise_for_status()
                return response
            except requests.RequestException:
                if attempt:
                    raise
                time.sleep(0.2)
        raise AssertionError("unreachable")
    
    def _get_page_once(self, url: str, wait_time: int = 5) -> Optional[str]:
        """
        Selenium으로 페이지 가져오기
        
        Args:
            url: 페이지 URL
            wait_time: 페이지 로딩 대기 시간 (초)
        
        Returns:
            HTML 소스 또는 None
        """
        try:
            self._init_driver()
            logger.info(f"Loading URL: {url}")
            
            self._navigate(url)
            
            # 페이지 로딩 대기
            import time
            time.sleep(wait_time)
            
            # NetFunnel 대기열 체크 및 대기
            from selenium.webdriver.common.by import By
            
            try:
                # NetFunnel 요소가 있는지 확인
                netfunnel_element = self.driver.find_elements(By.ID, "NetFunnel_Skin_Top")
                if netfunnel_element:
                    logger.info("NetFunnel detected, waiting...")
                    time.sleep(10)  # 추가 대기
            except Exception as exc:
                logger.debug("LOTTE NetFunnel probe failed: %s", exc)
            
            html = self.driver.page_source
            if len(html.encode("utf-8", errors="ignore")) > MAX_BROWSER_HTML_BYTES:
                raise RuntimeError("LOTTE browser page exceeded the HTML size limit")
            logger.info(f"Page loaded successfully (size: {len(html)} bytes)")
            
            return html
            
        except Exception as e:
            logger.error("LOTTE page load failed. url=%s error=%s", url, e)
            if "no such window" in str(e) or "not reached" in str(e) or "web view not found" in str(e):
                logger.warning("Browser crash suspected. Restarting driver...")
                try:
                    self._close_driver()
                except Exception:
                    self.driver = None
                
                # 잠시 대기
                import time
                time.sleep(3)
                
                # 재시도 (최대 1번만 더 재귀 호출한다고 가정하거나 여기서 다시 시도)
                # 여기서는 간단히 init driver만 하고 None 리턴 (호출자가 재시도하거나 다음으로 넘어감)
                return None
            return None

    def _get_page(self, url: str, wait_time: int = 5) -> Optional[str]:
        for attempt in range(2):
            html = self._get_page_once(url, wait_time=wait_time)
            if html:
                return html
            self._close_driver()
            if attempt == 0:
                time.sleep(0.2)
        self.had_errors = True
        self.crawl_complete = False
        return None

    def get_page(self, url: str, wait_time: int = 5) -> Optional[str]:
        return self._get_page(url, wait_time=wait_time)

    def _normalize_image_url(self, image_url: Optional[str]) -> Optional[str]:
        if not image_url:
            return None
        image_url = image_url.strip()
        if not image_url:
            return None
        blocked_fragments = (
            "img-sns-share-thumbnail",
            "img-gnb-",
            "favicon",
            "logo",
            "icon_",
        )
        if any(fragment in image_url for fragment in blocked_fragments):
            return None
        if image_url.startswith("//"):
            return f"https:{image_url}"
        if image_url.startswith("/"):
            return f"{self.base_url}{image_url}"
        return image_url
     
    def scrape_branches(self) -> List[Dict]:
        """
        강좌 구분 정보를 branch로 저장
        롯데는 강좌 구분(성인/아동/영유아)별로 검색
        """
        logger.info("Loading course category information")
        branches = []
        
        for category_code, category_info in LOTTE_COURSE_CATEGORIES.items():
            branches.append({
                'provider': 'LOTTE',
                'branch_code': category_code,
                'name': f"롯데문화센터 {category_info['name']}",
                'address': '',
                'phone': ''
            })
        
        logger.info(f"Loaded {len(branches)} course categories")
        return branches
    
    def scrape_real_branches(self) -> List[Dict]:
        """Load LOTTE store branches from the public index page."""
        branch_list_url = self.config.get('branch_list_url') or f"{self.base_url}/index.do"
        logger.info(f"Loading LOTTE real branches: {branch_list_url}")

        try:
            if not self.get_page(branch_list_url):
                raise RuntimeError("Selenium failed to load branch list page")
            time.sleep(2)
        except Exception as e:
            logger.error("LOTTE branch list failed. url=%s error=%s", branch_list_url, e)
            return []

        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
        branch_by_code = {}

        branch_links = soup.select('a[href*="list.do?type=branch"][href*="brchCd="], a.brchBtn[data-brch-cd]')
        for link in branch_links:
            branch_code = link.get('data-brch-cd')
            if not branch_code:
                match = re.search(r'brchCd=([^&]+)', link.get('href', ''))
                branch_code = match.group(1) if match else None

            branch_name = clean_text(link.get_text(" ", strip=True))
            if not branch_code or not branch_code.isdigit() or not branch_name:
                continue
            provider_name = self.config.get('name') or 'LOTTE'
            display_name = (
                branch_name
                if branch_name.startswith(provider_name)
                else f"{provider_name} {branch_name}"
            )

            branch_by_code[branch_code] = {
                'provider': 'LOTTE',
                'branch_code': branch_code,
                'name': display_name,
                'address': '',
                'phone': '',
            }

        branches = list(branch_by_code.values())
        logger.info(f"Loaded {len(branches)} LOTTE real branches")
        return branches

    def scrape_courses(self, category_code: str) -> List[Dict]:
        """
        특정 강좌 구분의 강좌 목록 스크래핑 (Selenium)
        중분류가 있는 경우 반복 처리
        """
        all_courses = []
        
        category_info = LOTTE_COURSE_CATEGORIES.get(category_code)
        if not category_info:
            logger.error(f"Unknown category code: {category_code}")
            return all_courses
        
        category_lrcls = category_info['lrclsCtegryCd']
        sub_categories = category_info.get('sub_categories', [])
        
        # 검색 대상 코드 목록 (중분류가 없으면 빈 문자열 하나)
        target_codes = sub_categories if sub_categories else ['']
        
        seen_urls = set()

        for mdcls_code in target_codes:
            logger.info(f"Scraping category {category_code} - sub: {mdcls_code if mdcls_code else 'ALL'}")

            empty_pages = 0
            for page_index in range(1, 101):
                list_url = (
                    f"{self.base_url}/application/search/list.do"
                    f"?type=category&lrclsCtegryCd={category_lrcls}"
                    f"&mdclsCtegryCd={mdcls_code}"
                    f"&orderSet=C&pageIndex={page_index}&initIndex=1&listCnt=100"
                )

                html = self._get_page(list_url, wait_time=8)
                if not html:
                    logger.error(f"Failed to get course list for {category_code}-{mdcls_code} page={page_index}")
                    empty_pages += 1
                    if empty_pages >= 2:
                        break
                    continue

                try:
                    import time

                    time.sleep(2)
                    html = self.driver.page_source
                    soup = BeautifulSoup(html, 'html.parser')
                    course_links = soup.select('a.lec_list')

                    if not course_links:
                        logger.info(f"No LOTTE courses for {category_code}-{mdcls_code} page={page_index}")
                        break

                    before_count = len(all_courses)
                    logger.info(
                        "Found %s LOTTE links in category=%s sub=%s page=%s",
                        len(course_links),
                        category_code,
                        mdcls_code,
                        page_index,
                    )

                    page_added = 0
                    for link in course_links:
                        try:
                            href = link.get('href')
                            if not href:
                                continue

                            course_url = _trusted_lotte_url(href)
                            if not course_url:
                                self.crawl_complete = False
                                logger.warning("Skipping an untrusted LOTTE course link")
                                continue

                            if course_url in seen_urls:
                                continue
                            seen_urls.add(course_url)

                            title = clean_text(link.get_text()) if link.get_text(strip=True) else "제목 없음"
                            if title and len(title) > 2:
                                all_courses.append({
                                    'url': course_url,
                                    'title': title,
                                    'category': category_info['age_group']
                                })
                                page_added += 1
                        except Exception as e:
                            logger.warning(f"Failed to parse link: {e}")
                            continue

                    if page_added == 0 or len(all_courses) == before_count:
                        logger.info(
                            "LOTTE pagination reached duplicate/empty page for category=%s sub=%s page=%s",
                            category_code,
                            mdcls_code,
                            page_index,
                        )
                        break

                    if len(course_links) < 100:
                        logger.info(
                            "LOTTE pagination reached last short page for category=%s sub=%s page=%s",
                            category_code,
                            mdcls_code,
                            page_index,
                        )
                        break
                except Exception as e:
                    logger.error(f"Error scraping sub-category {mdcls_code} page={page_index}: {e}")
                    break

        logger.info(f"Total {len(all_courses)} courses specific to category {category_code}")
        return all_courses

    def _scrape_branch_courses_with_browser(self, branch: Dict, limit: Optional[int] = None) -> List[Dict]:
        """Scrape one real LOTTE branch page after expanding the site's "more" button."""
        branch_code = branch.get("branch_code")
        branch_name = branch.get("name") or branch_code
        all_courses = []
        seen_urls = set()

        if not branch_code:
            return all_courses

        list_url = f"{self.base_url}/application/search/list.do?type=branch&brchCd={branch_code}"
        logger.info(f"Scraping LOTTE branch {branch_name} ({branch_code}): {list_url}")

        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

            if not self._get_page(list_url, wait_time=1):
                return all_courses
            wait = WebDriverWait(self.driver, 20)

            try:
                wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "a.lec_list")) > 0)
            except TimeoutException:
                self.crawl_complete = False
                logger.info(f"No LOTTE course links loaded for branch {branch_name} ({branch_code})")
                return all_courses

            more_clicks = 0
            while limit is None or len(self.driver.find_elements(By.CSS_SELECTOR, "a.lec_list")) < limit:
                more_buttons = self.driver.find_elements(By.CSS_SELECTOR, "a.more_btn.no_motion")
                if not more_buttons:
                    break

                more_button = more_buttons[0]
                try:
                    if not more_button.is_displayed():
                        break
                except StaleElementReferenceException:
                    continue

                before_count = len(self.driver.find_elements(By.CSS_SELECTOR, "a.lec_list"))
                try:
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", more_button)
                    time.sleep(0.2)
                    self.driver.execute_script("arguments[0].click();", more_button)
                except Exception as e:
                    logger.warning("LOTTE more click failed. branch=%s branch_code=%s url=%s error=%s", branch_name, branch_code, list_url, e)
                    break

                more_clicks += 1
                try:
                    wait.until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "a.lec_list")) > before_count
                        or not d.find_elements(By.CSS_SELECTOR, "a.more_btn.no_motion")
                        or not d.find_elements(By.CSS_SELECTOR, "a.more_btn.no_motion")[0].is_displayed()
                    )
                except TimeoutException:
                    logger.info(
                        "LOTTE more button produced no new rows for branch=%s after %s rows",
                        branch_code,
                        before_count,
                    )
                    break

                if len(self.driver.find_elements(By.CSS_SELECTOR, "a.lec_list")) == before_count:
                    break
                if more_clicks >= 200:
                    logger.warning(f"LOTTE more button click cap reached for branch {branch_name}")
                    self.crawl_complete = False
                    break
                time.sleep(0.4)

            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            for link in soup.select("a.lec_list"):
                href = link.get("href")
                if not href:
                    continue

                course_url = _trusted_lotte_url(href)
                if not course_url:
                    self.crawl_complete = False
                    logger.warning("Skipping an untrusted LOTTE course link")
                    continue
                if course_url in seen_urls:
                    continue
                seen_urls.add(course_url)

                title = clean_text(link.get_text(" ", strip=True)) or "제목 없음"
                all_courses.append({
                    "url": course_url,
                    "title": title,
                    "branch_code": branch_code,
                    "branch_name": branch_name,
                })

                if limit is not None and len(all_courses) >= limit:
                    break

            logger.info(
                "Found %s LOTTE courses for branch %s (%s), more_clicks=%s",
                len(all_courses),
                branch_name,
                branch_code,
                more_clicks,
            )
        except Exception as e:
            self.had_errors = True
            self.crawl_complete = False
            logger.error("LOTTE branch crawl failed. branch=%s branch_code=%s url=%s error=%s", branch_name, branch_code, list_url, e)

        return all_courses

    def scrape_branch_courses(self, branch: Dict, limit: Optional[int] = None) -> List[Dict]:
        """Load the complete branch result set through LOTTE's paginated endpoint."""
        previous_complete = self.crawl_complete
        previous_had_errors = self.had_errors

        for attempt in range(len(LOTTE_BRANCH_RETRY_DELAYS_SECONDS) + 1):
            self.crawl_complete = True
            self.had_errors = previous_had_errors
            courses = self.scrape_branch_courses_by_query(branch, "", limit=limit)
            attempt_complete = self.crawl_complete

            self.crawl_complete = previous_complete
            self.had_errors = previous_had_errors
            if attempt_complete:
                return courses

            if attempt < len(LOTTE_BRANCH_RETRY_DELAYS_SECONDS):
                delay_seconds = LOTTE_BRANCH_RETRY_DELAYS_SECONDS[attempt]
                logger.warning(
                    "LOTTE branch AJAX crawl was incomplete; retrying. "
                    "branch=%s attempt=%s/%s delay_seconds=%s",
                    branch.get("branch_code"),
                    attempt + 2,
                    len(LOTTE_BRANCH_RETRY_DELAYS_SECONDS) + 1,
                    delay_seconds,
                )
                time.sleep(delay_seconds)

        logger.warning(
            "LOTTE paginated branch endpoint failed; using browser fallback. "
            "branch=%s",
            branch.get("branch_code"),
        )
        self.crawl_complete = True
        self.had_errors = previous_had_errors
        courses = self._scrape_branch_courses_with_browser(branch, limit=limit)
        browser_complete = self.crawl_complete
        if courses and browser_complete:
            self.crawl_complete = previous_complete
            self.had_errors = previous_had_errors
            logger.info(
                "LOTTE branch browser fallback recovered the branch. branch=%s",
                branch.get("branch_code"),
            )
            return courses

        self.crawl_complete = False
        self.had_errors = True
        return courses

    def _lotte_request_headers(self, ajax: bool = False, referer: Optional[str] = None) -> Dict:
        headers = dict(HEADERS)
        headers.pop("Accept-Encoding", None)
        if ajax:
            headers["Accept"] = "text/html, */*; q=0.01"
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Origin"] = self.base_url
        if referer:
            headers["Referer"] = referer
        return headers

    def _detail_request_delay_seconds(self) -> float:
        raw_value = clean_text(
            os.getenv(
                "LOTTE_DETAIL_REQUEST_DELAY_SECONDS",
                str(DEFAULT_DETAIL_REQUEST_DELAY_SECONDS),
            )
        )
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_DETAIL_REQUEST_DELAY_SECONDS
        return max(0.0, min(value, MAX_DETAIL_REQUEST_DELAY_SECONDS))

    def _get_course_detail_html(
        self,
        course_url: str,
        *,
        browser_fallback: bool = True,
    ) -> Optional[str]:
        """Fetch static LOTTE detail HTML without a browser wait per course."""
        try:
            response = self._http_request_with_retry(
                "GET",
                course_url,
                headers=self._lotte_request_headers(
                    referer=f"{self.base_url}/application/search/list.do"
                ),
                timeout=30,
            )
            response.encoding = "utf-8"
            html = response.text
            if not html or not BeautifulSoup(html, "html.parser").select_one(
                ".lectNm, p.tit.lectNm"
            ):
                raise RuntimeError("LOTTE HTTP detail response did not contain a course")
            self._detail_http_success_count += 1
            return html
        except Exception as exc:
            if not browser_fallback:
                logger.debug(
                    "LOTTE parallel HTTP detail fetch failed. url=%s error_type=%s",
                    course_url,
                    type(exc).__name__,
                )
                return None
            self._detail_browser_fallback_count += 1
            logger.warning(
                "LOTTE HTTP detail fetch failed; using browser fallback. "
                "url=%s error_type=%s",
                course_url,
                type(exc).__name__,
            )
            return self._get_page(course_url, wait_time=5)

    def _detail_worker_count(self) -> int:
        raw_value = clean_text(
            os.getenv("LOTTE_DETAIL_WORKERS", str(DEFAULT_DETAIL_WORKERS))
        )
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_DETAIL_WORKERS
        return max(1, min(value, MAX_DETAIL_WORKERS))

    def scrape_course_details(
        self,
        course_list: List[Dict],
    ) -> List[Optional[Dict | List[Dict]]]:
        workers = min(self._detail_worker_count(), len(course_list))
        if workers <= 1:
            return [
                self.scrape_course_detail(course_info.get("url", ""))
                for course_info in course_list
            ]

        worker_local = threading.local()
        worker_crawlers: list[LotteCrawler] = []
        worker_lock = threading.Lock()
        delay_seconds = self._detail_request_delay_seconds()

        def scrape(course_info: Dict) -> Optional[Dict | List[Dict]]:
            worker = getattr(worker_local, "crawler", None)
            if worker is None:
                worker = LotteCrawler()
                worker_local.crawler = worker
                with worker_lock:
                    worker_crawlers.append(worker)
            result = worker.scrape_course_detail(
                course_info.get("url", ""),
                browser_fallback=False,
            )
            if delay_seconds:
                time.sleep(delay_seconds)
            return result

        try:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="lotte-detail",
            ) as executor:
                results = list(executor.map(scrape, course_list))
        finally:
            for worker in worker_crawlers:
                self._detail_http_success_count += (
                    worker._detail_http_success_count
                )
                worker.http_session.close()

        for index, result in enumerate(results):
            if result is None:
                results[index] = self.scrape_course_detail(
                    course_list[index].get("url", "")
                )
        return results

    def _load_existing_course_ids_by_raw_url(self) -> dict[str, str]:
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT raw_url, provider_course_id
                  FROM courses
                 WHERE provider = 'LOTTE'
                   AND raw_url IS NOT NULL
                   AND BTRIM(raw_url) <> ''
                 ORDER BY last_seen_at DESC NULLS LAST, updated_at DESC NULLS LAST
                """
            )
            rows = cursor.fetchall()

        identities: dict[str, str] = {}
        for row in rows:
            raw_url = row["raw_url"] if isinstance(row, dict) else row[0]
            provider_course_id = (
                row["provider_course_id"] if isinstance(row, dict) else row[1]
            )
            normalized_url = normalize_course_raw_url(raw_url)
            identity = clean_text(provider_course_id)
            if normalized_url and identity:
                identities.setdefault(normalized_url, identity)
        logger.info("Loaded %s existing LOTTE course URL identities.", len(identities))
        return identities

    def _coalesce_course_identity_from_cache(self, course_data: Dict) -> bool:
        identities = self._existing_course_ids_by_raw_url
        if identities is None:
            return False
        normalized_url = normalize_course_raw_url(course_data.get("raw_url"))
        if normalized_url:
            course_data["raw_url"] = normalized_url
            existing_identity = identities.get(normalized_url)
            if (
                existing_identity
                and existing_identity != course_data.get("provider_course_id")
            ):
                course_data["provider_course_id"] = existing_identity
                self._cached_identity_reuse_count += 1
        return True

    def _remember_course_identity(self, course_data: Dict) -> None:
        identities = self._existing_course_ids_by_raw_url
        if identities is None:
            return
        normalized_url = normalize_course_raw_url(course_data.get("raw_url"))
        identity = clean_text(course_data.get("provider_course_id"))
        if normalized_url and identity:
            identities[normalized_url] = identity

    def _normalize_lotte_branch_token(self, value: Optional[str]) -> str:
        value = clean_text(value or "")
        for token in (
            "롯데문화센터",
            "롯데백화점",
            "문화센터",
            "백화점",
            "롯데몰",
            "타임빌라스",
        ):
            value = value.replace(token, "")
        value = re.sub(r"[\s\[\]\(\)·ㆍ|:/_-]+", "", value)
        value = re.sub(r"점$", "", value)
        aliases = {
            "건대스타시티": "건대",
            "김포공항": "김포",
            "센텀시티": "센텀",
        }
        return aliases.get(value, value)

    def _notice_branch_from_text(self, title: str, body_text: str, branches: List[Dict]) -> Optional[Dict]:
        ignored = {"본사", "롯데쇼핑"}
        candidates = []
        candidates.extend(re.findall(r"\[([^\]]+)\]", title or ""))
        candidates.extend(re.findall(r"→\s*([^→\n]{1,30}?점)\s*선택", body_text or ""))
        candidates.extend(re.findall(r"([^\n]{1,30}?점)\s*문화센터", body_text or ""))

        best_branch = None
        best_score = -1
        for candidate in candidates:
            candidate_norm = self._normalize_lotte_branch_token(candidate)
            if not candidate_norm or candidate_norm in ignored:
                continue

            for branch in branches:
                branch_norm = self._normalize_lotte_branch_token(branch.get("name"))
                if not branch_norm:
                    continue

                score = -1
                if candidate_norm == branch_norm:
                    score = 1000 + len(candidate_norm)
                elif branch_norm.endswith(candidate_norm):
                    score = 800 + len(candidate_norm)
                elif candidate_norm in branch_norm:
                    score = 600 + len(candidate_norm)
                elif branch_norm in candidate_norm:
                    score = 400 + len(branch_norm)

                if score > best_score:
                    best_score = score
                    best_branch = branch

        return best_branch

    def _parse_notice_rows(self, html: str) -> List[Dict]:
        soup = BeautifulSoup(html or "", "html.parser")
        rows = []

        for link in soup.select("a.notice_list"):
            seq = link.get("data-seq")
            if not seq:
                match = re.search(r"noticeCtrl\.detail\([^,]+,\s*(\d+)", link.get("href", ""))
                seq = match.group(1) if match else None

            title_elem = link.select_one(".title p")
            date_elem = link.select_one(".type_div .type")
            total_count = link.get("data-tot-cnt")

            if not seq or not str(seq).isdigit():
                continue

            rows.append({
                "seq": seq,
                "title": clean_text(title_elem.get_text(" ", strip=True)) if title_elem else "",
                "date": clean_text(date_elem.get_text(" ", strip=True)) if date_elem else None,
                "total_count": int(total_count) if total_count and total_count.isdigit() else None,
            })

        return rows

    def _notice_item_limit(self) -> int:
        raw_value = clean_text(os.getenv("LOTTE_NOTICE_LIMIT", str(DEFAULT_NOTICE_ITEMS)))
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_NOTICE_ITEMS
        return max(1, min(value, MAX_NOTICE_ITEMS))

    def _notice_lookback_days(self) -> int:
        raw_value = clean_text(
            os.getenv("LOTTE_NOTICE_LOOKBACK_DAYS", str(DEFAULT_NOTICE_LOOKBACK_DAYS))
        )
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_NOTICE_LOOKBACK_DAYS
        return max(30, min(value, 730))

    def scrape_notice_rows(self, list_cnt: int = 100, max_items: Optional[int] = None) -> List[Dict]:
        """Load LOTTE notice rows from /community/notice/list.ajax."""
        item_limit = self._notice_item_limit() if max_items is None else max_items
        if not 1 <= item_limit <= MAX_NOTICE_ITEMS:
            raise ValueError(f"max_items must be between 1 and {MAX_NOTICE_ITEMS}")
        list_cnt = max(1, min(list_cnt, 100, item_limit))
        rows = []
        seen = set()
        params = {
            "brchCd": "",
            "clCd": "8",
            "notcSeqno": "",
            "q": "",
            "pageIndex": "1",
            "initIndex": "1",
            "listCnt": str(list_cnt),
        }

        try:
            for page_index in range(1, 20):
                params["pageIndex"] = str(page_index)
                params["initIndex"] = "1" if page_index == 1 else str(page_index)
                response = self._http_request_with_retry(
                    "GET",
                    f"{self.base_url}/community/notice/list.ajax",
                    params=params,
                    headers=self._lotte_request_headers(),
                    timeout=30,
                )
                response.raise_for_status()
                response.encoding = "utf-8"

                page_rows = self._parse_notice_rows(response.text)
                if not page_rows:
                    break

                for row in page_rows:
                    if row["seq"] in seen:
                        continue
                    seen.add(row["seq"])
                    rows.append(row)
                    if len(rows) >= item_limit:
                        break

                total_count = page_rows[0].get("total_count")
                if len(rows) >= item_limit:
                    break
                if total_count and len(rows) >= total_count:
                    break
                if len(page_rows) < list_cnt:
                    break
            else:
                self.crawl_complete = False
                logger.warning("LOTTE notice pagination stopped at its page cap")

        except Exception as e:
            self.crawl_complete = False
            logger.warning("LOTTE notice list fetch failed: %s", e)

        logger.info("Loaded %s LOTTE notices", len(rows))
        return rows

    def scrape_notice_detail(self, seq: str) -> Tuple[Optional[BeautifulSoup], str]:
        seq = clean_text(seq)
        if not re.fullmatch(r"\d{1,10}", seq):
            raise ValueError("invalid LOTTE notice sequence")
        params = {
            "notcSeqno": seq,
            "brchCd": "",
            "clCd": "8",
            "pageIndex": "1",
            "initIndex": "1",
            "listCnt": "10",
            "q": "",
        }
        url = f"{self.base_url}/community/notice/view.do?{urlencode(params)}"

        try:
            response = self._http_request_with_retry(
                "GET", url, headers=self._lotte_request_headers(), timeout=30
            )
            response.encoding = "utf-8"
            soup = BeautifulSoup(response.text, "html.parser")
            body = (
                soup.select_one(".view_con .content")
                or soup.select_one(".view_con_w")
                or soup.select_one(".view_con")
            )
            body_text = clean_text(body.get_text(" ", strip=True)) if body else clean_text(soup.get_text(" ", strip=True))
            return soup, body_text
        except Exception as e:
            self.crawl_complete = False
            logger.warning("LOTTE notice detail fetch failed. seq=%s error=%s", seq, e)
            return None, ""

    def _is_reception_notice_candidate(self, title: Optional[str]) -> bool:
        text = clean_text(title or "")
        if not text or "강사" in text:
            return False
        if "[본사]" in text:
            return "학기" in text and "안내" in text
        return bool(LOTTE_RECEPTION_NOTICE_RE.search(text))

    def _notice_term(self, value: Optional[str]) -> Optional[str]:
        text = clean_text(value or "")
        for term in ("봄학기", "여름학기", "가을학기", "겨울학기"):
            if term in text:
                return term
        return None

    def _infer_lotte_notice_date(self, match: re.Match, reference_date: date) -> Optional[date]:
        try:
            raw_year = match.group("year")
            year = int(raw_year) if raw_year else reference_date.year
            if raw_year and year < 100:
                year += 2000
            month = int(match.group("month"))
            day = int(match.group("day"))
            value = date(year, month, day)
            if not raw_year and value < reference_date - timedelta(days=45):
                value = date(year + 1, month, day)
            return value
        except (TypeError, ValueError):
            return None

    def _parse_lotte_notice_date_range(self, value: str, reference_date: date):
        matches = list(LOTTE_FLEX_DATE_RE.finditer(value or ""))
        if not matches:
            return None, None
        start_date = self._infer_lotte_notice_date(matches[0], reference_date)
        if not start_date or len(matches) < 2:
            return start_date, None

        end_match = matches[1]
        try:
            raw_year = end_match.group("year")
            end_year = int(raw_year) if raw_year else start_date.year
            if raw_year and end_year < 100:
                end_year += 2000
            end_date = date(
                end_year,
                int(end_match.group("month")),
                int(end_match.group("day")),
            )
            if not raw_year and end_date < start_date:
                end_date = date(
                    end_year + 1,
                    int(end_match.group("month")),
                    int(end_match.group("day")),
                )
            return start_date, end_date
        except (TypeError, ValueError):
            return start_date, None

    def _lotte_notice_source_url(self, seq: str) -> str:
        return f"{self.base_url}/community/notice/view.do?{urlencode({'notcSeqno': seq})}"

    def _extract_lotte_class_period(self, body_text: str, reference_date: date):
        match = re.search(r"강\s*좌\s*기\s*간", body_text or "")
        if not match:
            return None, None, ""
        segment = (body_text or "")[match.start():match.start() + 300]
        start_date, end_date = self._parse_lotte_notice_date_range(segment, reference_date)
        return start_date, end_date, segment

    def _extract_lotte_new_member_segment(self, body_text: str) -> str:
        text = body_text or ""
        reception = re.search(r"접\s*수\s*기\s*간", text)
        section_start = reception.end() if reception else 0
        next_section = re.search(
            r"(?:강\s*좌\s*기\s*간|대\s*상\s*강\s*좌|강\s*의\s*일\s*정)",
            text[section_start:],
        )
        section_end = (
            section_start + next_section.start()
            if next_section
            else min(len(text), section_start + 1000)
        )
        section = text[section_start:section_end]
        new_member = re.search(r"신\s*규(?:\s*회\s*원)?\s*:? ?", section)
        if new_member:
            segment = section[new_member.start():]
            marker = re.search(r"※|(?:♥|■)\s*강\s*좌", segment[1:])
            if marker:
                segment = segment[:marker.start() + 1]
            return clean_text(segment[:400])
        return clean_text(section[:400])

    def _extract_main_new_member_starts(self, body_text: str, reference_date: date) -> Dict[str, date]:
        text = body_text or ""
        start = re.search(r"신\s*규\s*회\s*원\s*수\s*강\s*신\s*청", text)
        if not start:
            return {}
        tail = text[start.end():]
        end = re.search(r"확\s*인\s*해\s*주\s*세\s*요", tail)
        section = tail[:end.start()] if end else tail[:600]
        schedules = {}
        patterns = {
            "METRO": r"수\s*도\s*권\s*점\s*\|",
            "SEOUL_LOCAL": r"서\s*울\s*[·ㆍ]?\s*지\s*방\s*점\s*\|",
        }
        for group, pattern in patterns.items():
            match = re.search(pattern, section)
            if not match:
                continue
            apply_start, _ = self._parse_lotte_notice_date_range(
                section[match.end():match.end() + 100],
                reference_date,
            )
            if apply_start:
                schedules[group] = apply_start
        return schedules

    def _extract_main_branch_groups(self, body_text: str) -> Dict[str, set]:
        text = body_text or ""
        confirmation = re.search(r"확\s*인\s*해\s*주\s*세\s*요", text)
        section = text[confirmation.end():] if confirmation else text
        patterns = {
            "METRO": r"수\s*도\s*권\s*점\s*\|(?P<names>.*?)서\s*울\s*점\s*\|",
            "SEOUL": r"서\s*울\s*점\s*\|(?P<names>.*?)지\s*방\s*점\s*\|",
            "LOCAL": r"지\s*방\s*점\s*\|(?P<names>.*?)(?:문\s*의|※|$)",
        }
        groups = {}
        for group, pattern in patterns.items():
            match = re.search(pattern, section, re.DOTALL)
            if not match:
                continue
            groups[group] = {
                self._normalize_lotte_branch_token(token)
                for token in match.group("names").split("/")
                if self._normalize_lotte_branch_token(token)
            }
        return groups

    def _main_schedule_group_for_branch(self, branch_name: str, groups: Dict[str, set]) -> Optional[str]:
        branch_token = self._normalize_lotte_branch_token(branch_name)
        if branch_token in groups.get("METRO", set()):
            return "METRO"
        if branch_token in groups.get("SEOUL", set()) or branch_token in groups.get("LOCAL", set()):
            return "SEOUL_LOCAL"
        return None

    def parse_lotte_reception_notice(
        self,
        soup: Optional[BeautifulSoup],
        body_text: str,
        row: Dict,
        branches: List[Dict],
    ) -> Dict:
        title = clean_text(row.get("title"))
        published_on = parse_date(clean_text(row.get("date")).replace(".", "-"))
        published_on = published_on or datetime.now().date()
        # The body describes the previous term when defining an existing
        # member.  The notice title is therefore authoritative for its term.
        term = self._notice_term(title) or self._notice_term(body_text)
        is_main = "[본사]" in title
        title_branch = None if is_main else self._notice_branch_from_text(title, "", branches)
        body_branch = None if is_main else self._notice_branch_from_text("", body_text, branches)
        branch = title_branch or body_branch
        result = {
            **row,
            "title": title,
            "published_on": published_on,
            "term": term,
            "scope": "MAIN" if is_main else "BRANCH",
            "branch_code": (branch or {}).get("branch_code"),
            "branch_name": (branch or {}).get("name"),
            "source_url": self._lotte_notice_source_url(str(row.get("seq"))),
            "status": "UNPARSEABLE",
            "failure_reason": None,
        }
        if not soup or not body_text:
            result["failure_reason"] = "NOTICE_BODY_NOT_FOUND"
            return result
        if not term:
            result["failure_reason"] = "NOTICE_TERM_NOT_FOUND"
            return result
        if (
            title_branch
            and body_branch
            and title_branch.get("branch_code") != body_branch.get("branch_code")
        ):
            result["failure_reason"] = "NOTICE_BRANCH_MISMATCH"
            return result
        if not is_main and not branch:
            result["failure_reason"] = "NOTICE_BRANCH_NOT_FOUND"
            return result

        class_start, class_end, class_segment = self._extract_lotte_class_period(
            body_text,
            published_on,
        )
        if not class_start or not class_end:
            result["failure_reason"] = "COURSE_PERIOD_NOT_IN_TEXT"
            return result

        if is_main:
            main_starts = self._extract_main_new_member_starts(body_text, published_on)
            branch_groups = self._extract_main_branch_groups(body_text)
            if not main_starts:
                result["failure_reason"] = "MAIN_RECEPTION_GROUPS_NOT_FOUND"
                return result
            if not branch_groups:
                result["failure_reason"] = "MAIN_BRANCH_GROUPS_NOT_FOUND"
                return result
            result.update({
                "main_starts": main_starts,
                "main_branch_groups": branch_groups,
                "reception_segment": clean_text(body_text)[:1200],
            })
        else:
            reception_segment = self._extract_lotte_new_member_segment(body_text)
            apply_start, apply_end = self._parse_lotte_notice_date_range(
                reception_segment,
                published_on,
            )
            if not apply_start:
                result["failure_reason"] = "RECEPTION_PERIOD_NOT_IN_TEXT"
                return result
            if "선착순" in reception_segment:
                apply_end = None
            result.update({
                "apply_start": apply_start,
                "apply_end": apply_end,
                "reception_segment": reception_segment,
            })

        result.update({
            "class_start": class_start,
            "class_end": class_end,
            "class_segment": clean_text(class_segment),
            "status": "PARSED",
            "failure_reason": None,
        })
        return result

    def apply_lotte_reception_notice(self, notice: Dict) -> int:
        if notice.get("status") != "PARSED" or not notice.get("branch_code"):
            return 0
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                UPDATE courses AS course
                SET apply_start = %(apply_start)s,
                    apply_end = %(apply_end)s,
                    apply_period_raw = %(apply_period_raw)s,
                    updated_at = CURRENT_TIMESTAMP
                FROM branches AS branch
                WHERE course.branch_id = branch.id
                  AND course.provider = 'LOTTE'
                  AND branch.provider = 'LOTTE'
                  AND branch.branch_code = %(branch_code)s
                  AND course.is_active IS TRUE
                  AND COALESCE(course.end_date, course.start_date) >= %(class_start)s
                  AND COALESCE(course.start_date, course.end_date) <= %(class_end)s
                  AND (
                      course.apply_start IS DISTINCT FROM %(apply_start)s
                      OR course.apply_end IS DISTINCT FROM %(apply_end)s
                      OR course.apply_period_raw IS DISTINCT FROM %(apply_period_raw)s
                  )
                RETURNING course.id
                """,
                notice,
            )
            return len(cursor.fetchall())

    def monitor_reception_notices(self, branches: List[Dict]) -> Dict[str, int]:
        summary = {
            "seen": 0,
            "candidates": 0,
            "branch_overrides": 0,
            "main_fallbacks": 0,
            "unparseable": 0,
            "applied_courses": 0,
        }
        if not branches:
            return summary
        rows = self.scrape_notice_rows()
        summary["seen"] = len(rows)
        cutoff = datetime.now().date() - timedelta(days=self._notice_lookback_days())
        branch_records = {}
        main_records = {}

        for row in rows:
            if not self._is_reception_notice_candidate(row.get("title")):
                continue
            published_on = parse_date(clean_text(row.get("date")).replace(".", "-"))
            if published_on and published_on < cutoff:
                continue
            summary["candidates"] += 1
            soup, body_text = self.scrape_notice_detail(str(row.get("seq")))
            notice = self.parse_lotte_reception_notice(soup, body_text, row, branches)
            if notice.get("status") != "PARSED":
                summary["unparseable"] += 1
                logger.warning(
                    "LOTTE reception notice not applied. seq=%s branch=%s reason=%s url=%s",
                    notice.get("seq"),
                    notice.get("branch_name"),
                    notice.get("failure_reason"),
                    notice.get("source_url"),
                )

            if notice.get("scope") == "MAIN":
                if notice.get("term"):
                    main_records.setdefault(notice["term"], []).append(notice)
                continue
            if notice.get("branch_code") and notice.get("term"):
                key = (notice["branch_code"], notice["term"])
                branch_records.setdefault(key, []).append(notice)

        effective_notices = []
        for records in branch_records.values():
            latest = max(
                records,
                key=lambda item: (item.get("published_on") or date.min, int(item.get("seq") or 0)),
            )
            if latest.get("status") == "PARSED":
                effective_notices.append(latest)
                summary["branch_overrides"] += 1

        for term, records in main_records.items():
            main_notice = max(
                records,
                key=lambda item: (item.get("published_on") or date.min, int(item.get("seq") or 0)),
            )
            if main_notice.get("status") != "PARSED":
                continue
            for branch in branches:
                branch_code = branch.get("branch_code")
                if not branch_code or (branch_code, term) in branch_records:
                    continue
                schedule_group = self._main_schedule_group_for_branch(
                    branch.get("name") or "",
                    main_notice.get("main_branch_groups") or {},
                )
                apply_start = (main_notice.get("main_starts") or {}).get(schedule_group)
                if not apply_start:
                    summary["unparseable"] += 1
                    logger.warning(
                        "LOTTE main reception fallback unavailable. seq=%s branch=%s term=%s",
                        main_notice.get("seq"),
                        branch.get("name"),
                        term,
                    )
                    continue
                fallback = {
                    **main_notice,
                    "branch_code": branch_code,
                    "branch_name": branch.get("name"),
                    "apply_start": apply_start,
                    "apply_end": None,
                }
                effective_notices.append(fallback)
                summary["main_fallbacks"] += 1

        effective_notices.sort(
            key=lambda item: (
                item.get("class_start") or date.min,
                item.get("published_on") or date.min,
                1 if item.get("scope") == "BRANCH" else 0,
            )
        )
        for notice in effective_notices:
            notice["apply_period_raw"] = (
                f"[LOTTE_NOTICE:{notice.get('seq')}:{notice.get('scope')}] "
                f"신규회원 {notice.get('apply_start')} ~ "
                f"{'선착순 마감' if not notice.get('apply_end') else notice.get('apply_end')} "
                f"| 강좌범위: {notice.get('class_start')}~{notice.get('class_end')} "
                f"| {notice.get('source_url')}"
            )[:2000]
            affected = self.apply_lotte_reception_notice(notice)
            summary["applied_courses"] += affected
            logger.info(
                "LOTTE reception notice applied. seq=%s scope=%s branch=%s courses=%s",
                notice.get("seq"),
                notice.get("scope"),
                notice.get("branch_name"),
                affected,
            )
        logger.info("LOTTE reception notice monitor summary: %s", summary)
        return summary

    def _notice_title_may_have_courses(self, title: str) -> bool:
        text = title or ""
        if any(token in text for token in ("약관", "휴관", "오시는 길", "이동 경로", "무료주차", "환불규정", "차량 등록", "강의실 명칭")):
            return False
        return any(token in text for token in ("추가모집", "추가 회원모집", "중간접수", "회원모집", "수강", "강좌"))

    def _extract_notice_search_terms(self, title: str, body_text: str) -> List[str]:
        source = f"{title or ''}\n{body_text or ''}"
        terms = []

        def add_term(value: Optional[str]) -> None:
            if not value:
                return
            term = clean_text(value)
            term = re.sub(r"[\"'<>=;\s]", "", term)
            if len(term) < 2 or len(term) > 20:
                return
            if not re.search(r"[가-힣]", term):
                return
            if any(ch in term for ch in (":", "/", "\\")):
                return
            if term in {"검색창", "상세검색", "강좌보기", "문화센터", "롯데문화센터"}:
                return
            if term not in terms:
                terms.append(term)

        for quoted in re.findall(r"[\"“”'‘’「」『』]\s*([^\"“”'‘’「」『』]{1,30}?)\s*[\"“”'‘’「」『』]", source):
            add_term(quoted)

        if "중간접수" in source or "중간 접수" in source:
            add_term("중간접수")
            add_term("중간")
        if "추가모집" in source or "추가 모집" in source:
            add_term("추가모집")

        return terms

    def _extract_notice_course_links(self, soup: Optional[BeautifulSoup], title: str) -> List[Dict]:
        if not soup:
            return []

        course_infos = []
        seen = set()
        pattern = re.compile(r"(/application/search/view\.do\?[^'\"\s)]+)")

        candidates = []
        for link in soup.select("a[href]"):
            href = link.get("href") or ""
            if "/application/search/view.do" in href:
                candidates.append((href, clean_text(link.get_text(" ", strip=True))))
            for match in pattern.finditer(href):
                candidates.append((match.group(1), clean_text(link.get_text(" ", strip=True))))
            onclick = link.get("onclick") or ""
            for match in pattern.finditer(onclick):
                candidates.append((match.group(1), clean_text(link.get_text(" ", strip=True))))

        for href, link_text in candidates:
            course_url = _trusted_lotte_url(href)
            if not course_url:
                self.crawl_complete = False
                logger.warning("Skipping an untrusted LOTTE notice course link")
                continue
            if course_url in seen:
                continue
            seen.add(course_url)
            branch_match = re.search(r"brchCd=([^&]+)", course_url)
            branch_code = branch_match.group(1) if branch_match else None
            course_infos.append({
                "url": course_url,
                "title": link_text or title,
                "branch_code": branch_code,
                "branch_name": None,
                "source": "lotte_notice_link",
            })

        return course_infos

    def scrape_branch_courses_by_query(self, branch: Dict, query: str, limit: Optional[int] = None) -> List[Dict]:
        """Scrape LOTTE branch search results through the site's AJAX endpoint."""
        branch_code = branch.get("branch_code")
        branch_name = branch.get("name") or branch_code
        if not branch_code:
            return []

        list_params = {"type": "branch", "brchCd": branch_code, "q": query}
        list_url = f"{self.base_url}/application/search/list.do?{urlencode(list_params)}"
        list_cnt = 100
        courses = []
        seen_urls = set()
        scanned_count = 0
        ended_skipped = 0

        try:
            response = self._http_request_with_retry(
                "GET",
                list_url,
                headers=self._lotte_request_headers(referer=f"{self.base_url}/index.do"),
                timeout=30,
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            soup = BeautifulSoup(response.text, "html.parser")
            form = soup.select_one("#searchBranch")
            if not form:
                logger.warning("LOTTE branch search form missing. branch=%s query=%s", branch_code, query)
                return []

            form_data = {}
            for field in form.select("input"):
                name = field.get("name")
                if name:
                    form_data[name] = field.get("value", "")

            form_data.update({
                "type": "branch",
                "brchCd": branch_code,
                "q": query,
                "listCnt": str(list_cnt),
                "orderSet": form_data.get("orderSet") or "C",
            })

            total_count = None
            for page_index in range(1, 101):
                if limit is not None and len(courses) >= limit:
                    break

                form_data["pageIndex"] = str(page_index)
                form_data["initIndex"] = "1" if page_index == 1 else str(page_index)

                response = self._http_request_with_retry(
                    "POST",
                    f"{self.base_url}/search/list.ajax",
                    data=form_data,
                    headers=self._lotte_request_headers(ajax=True, referer=list_url),
                    timeout=30,
                )
                response.raise_for_status()
                response.encoding = "utf-8"
                page_soup = BeautifulSoup(response.text, "html.parser")
                course_links = page_soup.select("a.lec_list")
                if not course_links:
                    break
                scanned_count += len(course_links)

                if total_count is None:
                    total_elem = page_soup.select_one("[data-tot-cnt]")
                    total_value = total_elem.get("data-tot-cnt") if total_elem else None
                    total_count = int(total_value) if total_value and total_value.isdigit() else None

                page_unique = 0
                for link in course_links:
                    href = link.get("href")
                    if not href:
                        continue
                    course_url = _trusted_lotte_url(href)
                    if not course_url:
                        self.crawl_complete = False
                        logger.warning("Skipping an untrusted LOTTE search course link")
                        continue
                    if course_url in seen_urls:
                        continue
                    seen_urls.add(course_url)
                    page_unique += 1

                    status_elem = link.select_one(".label_div .label")
                    status_text = (
                        clean_text(status_elem.get_text(" ", strip=True))
                        if status_elem
                        else ""
                    )
                    if status_text == "강의종료":
                        ended_skipped += 1
                        continue

                    title_elem = link.select_one(".tit")
                    courses.append({
                        "url": course_url,
                        "title": clean_text(
                            title_elem.get_text(" ", strip=True)
                            if title_elem
                            else link.get_text(" ", strip=True)
                        ),
                        "branch_code": branch_code,
                        "branch_name": branch_name,
                        "source": (
                            "lotte_notice_search" if query else "lotte_branch_ajax"
                        ),
                        "notice_search_term": query or None,
                        "list_status_raw": status_text or None,
                    })
                    if limit is not None and len(courses) >= limit:
                        break

                if page_unique == 0:
                    self.crawl_complete = False
                    logger.warning(
                        "LOTTE branch pagination repeated a page. "
                        "branch=%s page=%s",
                        branch_code,
                        page_index,
                    )
                    break
                if total_count and scanned_count >= total_count:
                    break
                if len(course_links) < list_cnt:
                    break
            else:
                if limit is None:
                    self.crawl_complete = False
                    logger.warning("LOTTE branch search pagination stopped at its page cap")

        except Exception as e:
            self.crawl_complete = False
            logger.warning(
                "LOTTE branch search failed. branch=%s branch_code=%s query=%s error=%s",
                branch_name,
                branch_code,
                query,
                e,
            )

        logger.info(
            "LOTTE branch search found %s courses. branch=%s branch_code=%s "
            "query=%s scanned=%s ended_skipped=%s",
            len(courses),
            branch_name,
            branch_code,
            query,
            scanned_count,
            ended_skipped,
        )
        return courses

    def scrape_notice_course_map(self, branches: List[Dict], limit: Optional[int] = None) -> Dict[str, List[Dict]]:
        """Collect extra LOTTE courses announced only through notice posts."""
        course_map = {branch["branch_code"]: [] for branch in branches if branch.get("branch_code")}
        if not course_map:
            return course_map

        seen_urls = set()
        searched = set()
        notices = self.scrape_notice_rows()
        total_added = 0

        for notice in notices:
            if limit is not None and total_added >= limit:
                break

            title = notice.get("title") or ""
            if not self._notice_title_may_have_courses(title):
                continue

            soup, body_text = self.scrape_notice_detail(str(notice["seq"]))
            branch = self._notice_branch_from_text(title, body_text, branches)

            for course_info in self._extract_notice_course_links(soup, title):
                branch_code = course_info.get("branch_code") or (branch or {}).get("branch_code")
                if not branch_code or branch_code not in course_map:
                    continue
                if course_info["url"] in seen_urls:
                    continue
                seen_urls.add(course_info["url"])
                course_info["branch_code"] = branch_code
                course_map[branch_code].append(course_info)
                total_added += 1
                if limit is not None and total_added >= limit:
                    break

            if limit is not None and total_added >= limit:
                break
            if not branch:
                continue

            branch_code = branch.get("branch_code")
            terms = self._extract_notice_search_terms(title, body_text)
            for term in terms:
                if limit is not None and total_added >= limit:
                    break
                search_key = (branch_code, term)
                if search_key in searched:
                    continue
                searched.add(search_key)

                remaining = None if limit is None else max(limit - total_added, 0)
                for course_info in self.scrape_branch_courses_by_query(branch, term, limit=remaining):
                    if course_info["url"] in seen_urls:
                        continue
                    seen_urls.add(course_info["url"])
                    course_map[branch_code].append(course_info)
                    total_added += 1
                    if limit is not None and total_added >= limit:
                        break

        logger.info(
            "LOTTE notice course collection completed. notices=%s searches=%s courses=%s",
            len(notices),
            len(searched),
            total_added,
        )
        return course_map
    
    def _parse_group_course_options(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract LOTTE grouped course rows from the class selection popup."""
        options = []
        seen = set()
        pattern = re.compile(
            r"search\.classInfoSet\('([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'"
        )

        for link in soup.find_all("a", href=pattern):
            text = clean_text(link.get_text(" ", strip=True))
            if not text or text == "선택하세요":
                continue

            match = pattern.search(link.get("href", ""))
            if not match:
                continue

            brch_cd, year, semester, lect_cd = match.groups()
            key = (brch_cd, year, semester, lect_cd)
            if key in seen:
                continue
            seen.add(key)

            options.append({
                "branch_code": brch_cd,
                "year": year,
                "semester": semester,
                "lect_cd": lect_cd,
                "option_text": text,
            })

        return options

    def _apply_group_course_option(self, course_data: Dict, option: Dict) -> Dict:
        option_data = course_data.copy()
        brch_cd = option["branch_code"]
        year = option["year"]
        semester = option["semester"]
        lect_cd = option["lect_cd"]
        option_text = option.get("option_text") or ""

        option_data["branch_code"] = brch_cd
        option_data["provider_course_id"] = f"{brch_cd}-{year}-{semester}-{lect_cd}"
        option_data["raw_url"] = re.sub(r"lectCd=[^&]+", f"lectCd={lect_cd}", course_data.get("raw_url", ""))

        schedule_match = re.search(
            r"(?:(?P<month>\d{1,2})/(?P<day>\d{1,2})\s*)?"
            r"\(?(?P<weekday>[월화수목금토일])\)?\s+"
            r"(?P<start>\d{1,2}:\d{2})\s*[~～-]\s*"
            r"(?P<end>\d{1,2}:\d{2})",
            option_text,
        )
        if schedule_match:
            option_data["schedule_raw"] = (
                f"{schedule_match.group('weekday')} "
                f"{schedule_match.group('start')}~{schedule_match.group('end')}"
            )
            if schedule_match.group("month") and re.fullmatch(r"\d{4}", year):
                try:
                    option_date = date(
                        int(year),
                        int(schedule_match.group("month")),
                        int(schedule_match.group("day")),
                    )
                    option_data["start_date"] = option_date
                    option_data["end_date"] = option_date
                except ValueError:
                    pass

        target_parts = []
        for pattern in (
            r"\d{2,4}\s*[~～-]\s*\d{2,4}\s*년생",
            r"\d+\s*[~～-]\s*\d+\s*개월",
            r"(?:만\s*)?\d+\s*[~～-]\s*\d+\s*세",
            r"(?:만\s*)?\d+\s*세",
        ):
            target_match = re.search(pattern, option_text)
            if target_match:
                target_parts.append(target_match.group(0))

        if target_parts:
            option_data["target"] = " ".join(dict.fromkeys(target_parts))

        sessions_match = re.search(r"(\d+)\s*회", option_text)
        if sessions_match:
            option_data["schedule_raw"] = (
                f"{option_data.get('schedule_raw') or ''} "
                f"{sessions_match.group(1)}회"
            ).strip()

        return option_data

    def scrape_course_detail(
        self,
        course_url: str,
        *,
        browser_fallback: bool = True,
    ) -> Optional[Dict | List[Dict]]:
        """
        강좌 상세 정보 스크래핑 (Selenium)
        
        Args:
            course_url: 강좌 상세 페이지 URL
        
        Returns:
            강좌 상세 정보 딕셔너리
        """
        try:
            course_url = _trusted_lotte_url(course_url)
            if not course_url:
                raise ValueError("refusing an untrusted LOTTE course URL")
            logger.debug("Scraping a LOTTE course detail")
            
            # Selenium으로 페이지 가져오기
            html = self._get_course_detail_html(
                course_url,
                browser_fallback=browser_fallback,
            )
            
            if not html:
                if browser_fallback:
                    self.crawl_complete = False
                    logger.error("LOTTE detail page was empty")
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            body_text = clean_text(soup.get_text(" ", strip=True))
            
            # URL에서 고유 강좌 키 추출
            query = parse_qs(urlsplit(course_url).query)
            lect_cd = clean_text((query.get("lectCd") or [""])[0])
            brch_cd = clean_text((query.get("brchCd") or [""])[0])
            year = clean_text((query.get("yy") or [""])[0])
            semester = clean_text((query.get("lectSmsterCd") or [""])[0])
            
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", lect_cd) or not re.fullmatch(r"\d{1,12}", brch_cd):
                self.had_errors = True
                self.crawl_complete = False
                logger.warning("LOTTE detail was missing a valid course or branch identifier")
                return None

            year = year if re.fullmatch(r"\d{4}", year) else "UNKNOWN_YEAR"
            semester = semester if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", semester) else "UNKNOWN_SEMESTER"
            provider_course_id = f"{brch_cd}-{year}-{semester}-{lect_cd}"
            
            # 제목 - 정확한 셀렉터
            title_elem = soup.select_one('.lectNm, p.tit.lectNm')
            title = clean_text(title_elem.text) if title_elem else ""
            if not title:
                if all(
                    marker in body_text
                    for marker in LOTTE_UNAVAILABLE_DETAIL_MARKERS
                ):
                    self._terminal_unavailable_detail_count += 1
                    logger.warning(
                        "LOTTE detail is no longer available; treating it as a "
                        "stale list entry. provider_course_id=%s url=%s",
                        provider_course_id,
                        course_url,
                    )
                    return None
                self.had_errors = True
                self.crawl_complete = False
                logger.warning("LOTTE detail was missing a title")
                return None
            
            # 강사
            instructor_elem = soup.select_one('.tcNm, span.tcNm')
            instructor = clean_instructor_name(instructor_elem.text) if instructor_elem else None
            
            # 대상
            target_elem = soup.select_one('.objClNm, dd.objClNm')
            target = clean_text(target_elem.text) if target_elem else None
            
            category_elem = soup.select_one("dd.lectClNm, .lectClNm")
            category = (
                clean_text(category_elem.get_text(" ", strip=True))
                if category_elem
                else None
            )
            
            # 수강료
            fee_elem = soup.select_one('.lectAmt, dd.lectAmt')
            fee = extract_number(fee_elem.text) if fee_elem else 0
            
            # 재료비 (일단 0)
            material_fee = 0
            
            # 일정 - 강의시간
            schedule_elem = soup.select_one('.lectTime, dd.lectTime')
            schedule = clean_text(schedule_elem.text) if schedule_elem else None
            
            description = extract_lotte_description_from_soup(soup)
            
            # 상태
            status_elem = soup.select_one('.lectStatNm, p.label.lectStatNm')
            status_text = clean_text(status_elem.text) if status_elem else '접수중'
            status = COURSE_STATUS_MAP.get(status_text, infer_course_status(status_text))
            
            # 이미지
            image_url = None
            image_selectors = [
                'img.webPath',
                'p.thum_img img',
                '.lect_img img',
                '.info_img img',
                '.detail_img img',
                '.top_visual img',
                'img[src*="upload"]',
                'img[data-src*="upload"]',
                'img[src*="/files/"]',
                'img[src*="/editor/images/"]',
                'meta[property="og:image"]',
            ]
            for selector in image_selectors:
                image_elem = soup.select_one(selector)
                if not image_elem:
                    continue
                image_url = self._normalize_image_url(
                    image_elem.get('content') or image_elem.get('src') or image_elem.get('data-src')
                )
                if image_url:
                    break
            
            # 날짜 정보 - 강의기간
            date_elem = soup.select_one('.lectStDtm, dd.lectStDtm')
            start_date = None
            end_date = None
            
            if date_elem:
                text = clean_text(date_elem.text)
                dates = re.findall(r'\d{4}\.\d{1,2}\.\d{1,2}', text)
                if len(dates) >= 2:
                    start_date = parse_date(dates[0].replace('.', '-'))
                    end_date = parse_date(dates[1].replace('.', '-'))
                elif len(dates) == 1:
                    start_date = parse_date(dates[0].replace('.', '-'))

            reception_node = soup.select_one(".rceptPrdStDt")
            reception_period = parse_reception_period_text(
                reception_node.get_text(" ", strip=True) if reception_node else "",
                start_date,
            )
            if not reception_period:
                reception_period = extract_reception_period(body_text, start_date)
            
            course_data = {
                'provider': 'LOTTE',
                'provider_course_id': provider_course_id,
                'branch_code': brch_cd,
                'title': title,
                'instructor': instructor,
                'target': target,
                'category_raw': category,
                'fee': fee,
                'material_fee': material_fee,
                'schedule_raw': schedule,
                'description': description,
                'status': status,
                'raw_url': course_url,
                'image_url': image_url,
                'start_date': start_date,
                'end_date': end_date,
                'apply_start': reception_period.get('apply_start'),
                'apply_end': reception_period.get('apply_end'),
                'apply_period_raw': reception_period.get('apply_period_raw'),
            }

            group_options = self._parse_group_course_options(soup)
            if group_options:
                logger.info(f"Found {len(group_options)} LOTTE grouped course options: {provider_course_id}")
                return [self._apply_group_course_option(course_data, option) for option in group_options]

            return course_data
            
        except Exception as e:
            if browser_fallback:
                self.had_errors = True
                self.crawl_complete = False
                logger.error("LOTTE detail parse failed: %s", e)
            else:
                logger.debug("LOTTE parallel detail parse failed: %s", e)
            return None
    
    def save_branch(self, branch_code: str, branch_info: Dict) -> Optional[int]:
        """지점 정보를 DB에 저장하고 branch_id 반환"""
        try:
            branch_data = {
                'provider': 'LOTTE',
                'branch_code': clean_text(branch_code)[:50],
                'name': clean_text(branch_info.get('name'))[:100],
                'address': clean_text(branch_info.get('address'))[:2_000],
                'phone': clean_text(branch_info.get('phone'))[:100],
                'location': None
            }
            if not branch_data['branch_code'] or not branch_data['name']:
                raise ValueError("LOTTE branch code and name are required")
            
            branch_id = None
            with get_db_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO branches (
                        provider, branch_code, name, address, phone, location
                    )
                    VALUES (
                        %(provider)s, %(branch_code)s, %(name)s,
                        %(address)s, %(phone)s, %(location)s
                    )
                    ON CONFLICT (provider, branch_code)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        address = EXCLUDED.address,
                        phone = EXCLUDED.phone
                    RETURNING id
                """, branch_data)
                
                result = cursor.fetchone()
                if result:
                    branch_id = result['id']
            
            return branch_id
        except Exception as e:
            self.had_errors = True
            logger.error("LOTTE branch save failed. branch_code=%s branch=%s error=%s", branch_code, branch_info.get("name"), e)
            return None
    
    def save_course(self, course_data: Dict, branch_id: str) -> bool:
        """강좌 정보를 DB에 저장 (파싱 포함)"""
        try:
            sanitize_course_payload(course_data)
            course_data['branch_id'] = branch_id
            course_data.setdefault('apply_start', None)
            course_data.setdefault('apply_end', None)
            course_data.setdefault('apply_period_raw', None)
            for start_field, end_field in (("start_date", "end_date"), ("apply_start", "apply_end")):
                start_value = course_data.get(start_field)
                end_value = course_data.get(end_field)
                if start_value and end_value and end_value < start_value:
                    raise ValueError(f"{end_field} cannot precede {start_field}")
            course_data['instructor'] = clean_instructor_name(course_data.get('instructor'))
            course_url = course_data.get('raw_url', 'Unknown')
            if not course_data.get('category_raw'):
                category = course_data.get('category')
                category_map = {
                    'adult': 'ADULT',
                    'child': 'CHILD',
                    'infant': 'TODDLER',
                }
                course_data['category_raw'] = category_map.get(category, category)

            raw_title = course_data.get('title') or ''
            clean_title, removed_title_prefix = clean_course_title(raw_title)
            course_data['title_raw'] = raw_title
            course_data['title'] = clean_title
            course_data['title_prefix_removed'] = removed_title_prefix or None

            explicit_target = extract_target_text(raw_title)
            if explicit_target:
                course_data['target'] = explicit_target
            
            # Target 파싱
            target_text = course_data.get('target', '')
            
            # 의미 없는 target 값 필터링
            meaningless_targets = ['1인강좌', '2인강좌', '1인', '2인', '강좌', '수강']
            is_meaningful = (target_text and 
                           target_text.strip() and 
                           target_text.strip() not in meaningless_targets)
            
            if is_meaningful:
                try:
                    parsed_target = parse_crawler_target(target_text, self.target_parser)
                    course_data['target_age_group'] = parsed_target['age_group']
                    course_data['target_min_age'] = parsed_target['min_age']
                    course_data['target_max_age'] = parsed_target['max_age']
                    course_data['target_with_parent'] = parsed_target['with_parent']
                    course_data['target_tags'] = parsed_target['tags']
                    course_data['target_age_is_explicit'] = parsed_target.get('age_is_explicit', False)
                except Exception as e:
                    parse_error_logger.error(
                        f"[TARGET_PARSE_FAILED] URL: {course_url} | "
                        f"Target: '{target_text}' | Error: {e}"
                    )
                    # 파싱 실패해도 계속 진행 (NULL로 저장)
                    course_data['target_age_group'] = None
                    course_data['target_min_age'] = None
                    course_data['target_max_age'] = None
                    course_data['target_with_parent'] = False
                    course_data['target_tags'] = []
                    course_data['target_age_is_explicit'] = False
            else:
                # target 정보 없거나 의미 없음 -> title과 description에서 파싱 시도
                title = course_data.get('title', '')
                description = course_data.get('description', '')
                fallback_text = f"{title} {description}"
                
                try:
                    parsed_target = parse_crawler_target(fallback_text, self.target_parser)
                    course_data['target_age_group'] = parsed_target['age_group']
                    course_data['target_min_age'] = parsed_target['min_age']
                    course_data['target_max_age'] = parsed_target['max_age']
                    course_data['target_with_parent'] = parsed_target['with_parent']
                    course_data['target_tags'] = parsed_target['tags']
                    course_data['target_age_is_explicit'] = parsed_target.get('age_is_explicit', False)
                except Exception:
                    # 파싱 실패 시 NULL로 저장
                    course_data['target_age_group'] = None
                    course_data['target_min_age'] = None
                    course_data['target_max_age'] = None
                    course_data['target_with_parent'] = False
                    course_data['target_tags'] = []
                    course_data['target_age_is_explicit'] = False
                
                # 파싱 실패 시 강좌 구분의 기본 연령대 사용
                fallback_age_group = self._category_to_age_group(course_data.get('category'))
                if not fallback_age_group:
                    fallback_age_group = self._category_to_age_group(course_data.get('category_raw'))

                if not course_data['target_age_group'] and fallback_age_group:
                    course_data['target_age_group'] = fallback_age_group
                    if not course_data.get('target_age_is_explicit'):
                        course_data['target_min_age'] = None
                        course_data['target_max_age'] = None
                    if not course_data.get('target_tags'):
                        defaults = self.target_parser.GROUP_DEFAULTS.get(fallback_age_group, {})
                        course_data['target_tags'] = defaults.get('tags', [])
                    logger.info(f"Using category age group fallback: {fallback_age_group}")
            
            # Schedule 파싱
            schedule_text = course_data.get('schedule_raw', '')
            if schedule_text:
                try:
                    parsed_schedule = self.schedule_parser.parse(schedule_text)
                    course_data['schedule_days'] = parsed_schedule['days']
                    course_data['schedule_time_start'] = parsed_schedule['time_start']
                    course_data['schedule_time_end'] = parsed_schedule['time_end']
                    course_data['schedule_frequency'] = parsed_schedule['frequency']
                    course_data['schedule_duration_minutes'] = parsed_schedule['duration_minutes']
                    if course_data.get('schedule_duration_minutes') is not None and course_data.get('schedule_duration_minutes') <= 0:
                        course_data['schedule_duration_minutes'] = None
                    if course_data.get('schedule_time_start') == course_data.get('schedule_time_end'):
                        course_data['schedule_time_start'] = None
                        course_data['schedule_time_end'] = None
                except Exception as e:
                    parse_error_logger.error(
                        f"[SCHEDULE_PARSE_FAILED] URL: {course_url} | "
                        f"Schedule: '{schedule_text}' | Error: {e}"
                    )
                    # 파싱 실패해도 계속 진행
                    course_data['schedule_days'] = []
                    course_data['schedule_time_start'] = None
                    course_data['schedule_time_end'] = None
                    course_data['schedule_frequency'] = 'WEEKLY'
                    course_data['schedule_duration_minutes'] = None
            else:
                # schedule 정보 없음
                course_data['schedule_days'] = []
                course_data['schedule_time_start'] = None
                course_data['schedule_time_end'] = None
                course_data['schedule_frequency'] = 'WEEKLY'
                course_data['schedule_duration_minutes'] = None
            if not course_data.get('material_fee'):
                course_data['material_fee'] = extract_material_fee_amount(
                    course_data.get('material_note'),
                    course_data.get('description'),
                )
            course_data['application_url'] = course_data.get('application_url') or course_data.get('raw_url')
            if should_skip_expired_course(course_data):
                logger.info("Skipping expired LOTTE course: %s", course_data.get('title'))
                return False
             
            enrich_course_lifecycle(course_data)
            guard_course_before_upsert(course_data)

            with get_db_cursor() as cursor:
                if not self._coalesce_course_identity_from_cache(course_data):
                    coalesce_provider_course_id_by_raw_url(cursor, course_data, logger)
                cursor.execute("""
                    INSERT INTO courses (
                        branch_id, provider, provider_course_id, title, title_raw, title_prefix_removed, instructor,
                        target, category_raw, fee, material_fee, schedule_raw,
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
                        %(material_fee)s, %(schedule_raw)s, %(start_date)s,
                        %(end_date)s, %(apply_start)s, %(apply_end)s, %(apply_period_raw)s, %(status)s,
                        %(application_url)s, %(raw_url)s,
                        %(description)s, %(image_url)s,
                        TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, %(content_hash)s, NULL,
                        %(target_age_group)s, %(target_min_age)s, %(target_max_age)s,
                        %(target_with_parent)s, %(target_tags)s, %(target_age_is_explicit)s,
                        %(schedule_days)s, %(schedule_time_start)s, %(schedule_time_end)s,
                        %(schedule_frequency)s, %(schedule_duration_minutes)s
                    )
                    ON CONFLICT (provider, provider_course_id)
                    DO UPDATE SET
                        branch_id = EXCLUDED.branch_id,
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
                        material_fee = EXCLUDED.material_fee,
                        schedule_raw = EXCLUDED.schedule_raw,
                        start_date = EXCLUDED.start_date,
                        end_date = EXCLUDED.end_date,
                        apply_start = EXCLUDED.apply_start,
                        apply_end = EXCLUDED.apply_end,
                        apply_period_raw = EXCLUDED.apply_period_raw,
                        status = EXCLUDED.status,
                        application_url = COALESCE(EXCLUDED.application_url, courses.application_url),
                        raw_url = EXCLUDED.raw_url,
                        description = EXCLUDED.description,
                        image_url = EXCLUDED.image_url,
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
                              OR EXCLUDED.description IS DISTINCT FROM courses.description
                            THEN NULL
                            ELSE courses.ai_category
                        END,
                        ai_tags = CASE
                            WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                              OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                              OR EXCLUDED.description IS DISTINCT FROM courses.description
                            THEN NULL
                            ELSE courses.ai_tags
                        END,
                        ai_summary = CASE
                            WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                              OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                              OR EXCLUDED.description IS DISTINCT FROM courses.description
                            THEN NULL
                            ELSE courses.ai_summary
                        END,
                        is_ai_processed = CASE
                            WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                              OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                              OR EXCLUDED.description IS DISTINCT FROM courses.description
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
                result = cursor.fetchone()
                
                if result:
                    self._remember_course_identity(course_data)
                    logger.debug(
                        "Saved LOTTE course: %s | Age: %s | Days: %s | ID: %s",
                        course_data["title"],
                        course_data["target_age_group"],
                        course_data["schedule_days"],
                        result["id"],
                    )
                    success = True
                else:
                    success = False
            
            return success
        except CourseSemanticEligibilityError as e:
            logger.warning(
                "Rejected non-course LOTTE row. course_id=%s reason=%s evidence=%s",
                course_data.get("provider_course_id"),
                e.reason,
                ",".join(e.evidence),
            )
            return False
        except Exception as e:
            self.had_errors = True
            logger.error(
                "LOTTE course save failed. branch_id=%s branch_code=%s url=%s title=%s error=%s",
                branch_id,
                course_data.get("branch_code"),
                course_data.get("raw_url"),
                course_data.get("title"),
                e,
            )
            parse_error_logger.error(
                f"[DB_SAVE_FAILED] URL: {course_data.get('raw_url', 'Unknown')} | "
                f"Title: '{course_data.get('title', 'Unknown')}' | Error: {e}"
            )
            return False
    
    def _run(
        self,
        target_categories: List[str] = None,
        test_mode: bool = False,
        limit: Optional[int] = None,
        branch_code: Optional[str] = None,
        branch_name: Optional[str] = None,
    ) -> bool:
        """
        크롤러 실행 (Selenium 기반)
        
        Args:
            target_categories: 크롤링할 강좌 구분 리스트. None이면 전체 구분
            test_mode: 테스트 모드 (True면 20개만 수집)
            limit: 전체 저장 강좌 수 제한
        """
        if limit is not None and not 1 <= limit <= MAX_COURSE_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_COURSE_LIMIT}")
        self.had_errors = False
        self.crawl_complete = True
        self._cached_identity_reuse_count = 0
        self._detail_http_success_count = 0
        self._detail_browser_fallback_count = 0
        self._terminal_unavailable_detail_count = 0
        logger.info("=" * 50)
        logger.info("Lotte Culture Center Crawler Started (Selenium)")
        logger.info("=" * 50)
        crawl_started_at = utc_now()
        
        try:
            # WebDriver 초기화
            self._init_driver()

            real_branches = self.scrape_real_branches()
            if not real_branches:
                logger.error("No LOTTE real branches were scraped. Skipping course crawl and stale cleanup.")
                return False
            branch_ids_by_code = {}
            for branch in real_branches:
                branch_id = self.save_branch(branch['branch_code'], branch)
                if branch_id:
                    branch_ids_by_code[branch['branch_code']] = branch_id
            logger.info(f"Saved {len(branch_ids_by_code)} LOTTE real branch mappings")
            if not branch_ids_by_code:
                logger.error("No LOTTE real branches were saved. Skipping course crawl and stale cleanup.")
                return False

            try:
                self._existing_course_ids_by_raw_url = (
                    self._load_existing_course_ids_by_raw_url()
                )
            except Exception as exc:
                self._existing_course_ids_by_raw_url = None
                logger.warning(
                    "Failed to preload LOTTE course URL identities; using per-row "
                    "lookup. error_type=%s",
                    type(exc).__name__,
                )

            total_courses = 0
            notice_course_map = {}
            reception_branch_targets = []

            use_legacy_category_crawl = bool(target_categories) and all(
                category_code in LOTTE_COURSE_CATEGORIES for category_code in target_categories
            )

            if use_legacy_category_crawl:
                logger.info("Using LOTTE legacy category crawl for requested category filters: %s", target_categories)
                crawl_targets = []
                categories = [c for c in self.scrape_branches() if c["branch_code"] in target_categories]
                for category in categories:
                    category_id = self.save_branch(category["branch_code"], category)
                    if not category_id:
                        logger.error(f"Failed to save category: {category['name']}")
                        continue
                    course_list = self.scrape_courses(category["branch_code"])
                    crawl_targets.append((category, course_list, category_id))
            else:
                branch_targets = real_branches
                requested_filters = [value for value in [*(target_categories or []), branch_code, branch_name] if value]
                if requested_filters:
                    requested = set(requested_filters)
                    branch_targets = [
                        branch for branch in real_branches
                        if branch["branch_code"] in requested or branch["name"] in requested
                    ]
                    logger.info("Filtered LOTTE real branches to %s requested targets", len(branch_targets))

                logger.info("Using LOTTE real-branch crawl for %s branches", len(branch_targets))
                reception_branch_targets = branch_targets
                notice_limit = None if limit is None else max(limit, 0)
                notice_course_map = self.scrape_notice_course_map(branch_targets, limit=notice_limit)
                crawl_targets = []
                for branch in branch_targets:
                    if limit is not None and total_courses >= limit:
                        break
                    crawl_targets.append((branch, None, branch_ids_by_code.get(branch["branch_code"])))

            for crawl_target, course_list, fallback_branch_id in crawl_targets:
                target_code = crawl_target['branch_code']
                target_name = crawl_target['name']

                logger.info(f"\n--- LOTTE target: {target_name} ({target_code}) ---")

                if course_list is None:
                    remaining = None if limit is None else max(limit - total_courses, 0)
                    course_list = self.scrape_branch_courses(crawl_target, limit=remaining)
                    notice_courses = notice_course_map.get(target_code, [])
                    if notice_courses:
                        seen_course_urls = {course.get("url") for course in course_list if course.get("url")}
                        notice_added = 0
                        for notice_course in notice_courses:
                            notice_url = notice_course.get("url")
                            if not notice_url or notice_url in seen_course_urls:
                                continue
                            seen_course_urls.add(notice_url)
                            course_list.append(notice_course)
                            notice_added += 1
                        logger.info(
                            "Merged %s notice-driven LOTTE courses for target %s (%s)",
                            notice_added,
                            target_name,
                            target_code,
                        )

                if test_mode:
                    course_list = course_list[:20]

                if not course_list:
                    logger.warning(f"No courses found for LOTTE target {target_name}")
                    continue

                logger.info(f"Found {len(course_list)} courses for LOTTE target {target_name}")

                saved_count = 0
                detail_results = self.scrape_course_details(course_list)
                for i, (course_info, course_data) in enumerate(
                    zip(course_list, detail_results),
                    1,
                ):
                    if limit is not None and total_courses >= limit:
                        logger.info(f"Limit reached: {total_courses}/{limit}")
                        break

                    course_url = course_info.get("url", "")
                    try:
                        if not course_url:
                            raise ValueError("missing course url")
                        if i == 1 or i % 25 == 0 or i == len(course_list):
                            logger.info(
                                "LOTTE progress target=%s course=%s/%s saved=%s",
                                target_name,
                                i,
                                len(course_list),
                                saved_count,
                            )
                        else:
                            logger.debug(
                                "Processing LOTTE course %s/%s: %s",
                                i,
                                len(course_list),
                                course_info.get("title", "Unknown"),
                            )
                        
                        if course_data:
                            course_items = course_data if isinstance(course_data, list) else [course_data]
                            for item_data in course_items:
                                if limit is not None and total_courses >= limit:
                                    break

                                # 기본 정보와 상세 정보 병합
                                item_data['raw_url'] = item_data.get('raw_url') or course_url
                                # 강좌 구분 정보 추가
                                if course_info.get('category'):
                                    item_data['category'] = course_info.get('category')
                                
                                course_branch_code = item_data.get('branch_code')
                                course_branch_id = branch_ids_by_code.get(course_branch_code)
                                if not course_branch_id:
                                    logger.warning(
                                        f"Missing LOTTE real branch for brchCd={course_branch_code}; "
                                        f"falling back to current target {target_code}"
                                    )
                                    course_branch_id = fallback_branch_id

                                # 저장
                                if course_branch_id and self.save_course(item_data, course_branch_id):
                                    saved_count += 1
                                    total_courses += 1
                        
                        # 테스트 모드: 20개만
                        if test_mode and saved_count >= 20:
                            logger.info("Test mode: stopping after 20 courses")
                            break
                    
                    except Exception as e:
                        logger.error(
                            "LOTTE course processing failed. target=%s branch_code=%s url=%s title=%s error=%s",
                            target_name,
                            target_code,
                            course_url,
                            course_info.get("title", "Unknown"),
                            e,
                        )
                        continue
                
                logger.info(f"Saved {saved_count} courses for LOTTE target {target_name}")
                logger.info(f"Completed LOTTE target: {target_name} - {len(course_list)} courses")

                if limit is not None and total_courses >= limit:
                    break
            
            logger.info("=" * 50)
            logger.info(f"Crawling Completed! Total courses saved: {total_courses}")
            logger.info(
                "LOTTE detail transport summary: http=%s browser_fallback=%s "
                "terminal_unavailable=%s cached_identity_reuse=%s",
                self._detail_http_success_count,
                self._detail_browser_fallback_count,
                self._terminal_unavailable_detail_count,
                self._cached_identity_reuse_count,
            )
            if total_courses <= 0:
                logger.error("LOTTE saved 0 courses.")
                return False
            if reception_branch_targets:
                try:
                    self.monitor_reception_notices(reception_branch_targets)
                except Exception as exc:
                    self.had_errors = True
                    logger.error("LOTTE reception notice monitor failed: %s", exc)
            full_scope = bool(
                limit is None
                and not test_mode
                and not target_categories
                and not branch_code
                and not branch_name
            )
            if full_scope:
                if not self._terminal_unavailable_detail_is_tolerable():
                    self.had_errors = True
                    self.crawl_complete = False
                    logger.error(
                        "LOTTE unavailable detail threshold exceeded. "
                        "count=%s attempts=%s max_count=%s max_ratio=%s",
                        self._terminal_unavailable_detail_count,
                        self._detail_http_success_count
                        + self._detail_browser_fallback_count,
                        MAX_TOLERATED_UNAVAILABLE_DETAILS,
                        MAX_TOLERATED_UNAVAILABLE_DETAIL_RATIO,
                    )
                if self.had_errors or not self.crawl_complete:
                    logger.warning("Skipping stale cleanup because the LOTTE crawl was partial")
                else:
                    stale_count = mark_stale_courses('LOTTE', crawl_started_at)
                    logger.info(f"Marked stale LOTTE courses inactive: {stale_count}")
            logger.info("=" * 50)
            return not self.had_errors and (not full_scope or self.crawl_complete)
        
        except Exception as e:
            self.had_errors = True
            self.crawl_complete = False
            logger.exception("LOTTE crawler failed: %s", e)
            return False
        
        finally:
            # WebDriver 종료
            self._close_driver()
            self.http_session.close()

    def run(
        self,
        target_categories: List[str] = None,
        test_mode: bool = False,
        limit: Optional[int] = None,
        branch_code: Optional[str] = None,
        branch_name: Optional[str] = None,
    ) -> bool:
        try:
            return self._run(
                target_categories=target_categories,
                test_mode=test_mode,
                limit=limit,
                branch_code=branch_code,
                branch_name=branch_name,
            )
        finally:
            self._close_driver()
            self.http_session.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Lotte Culture Center Crawler')
    parser.add_argument('--categories', nargs='+', help='Specific category codes (e.g., adult child infant)')
    parser.add_argument('--test', action='store_true', help='Test mode: crawl 20 courses per category')
    parser.add_argument('--limit', type=int, help='Maximum number of courses to save')
    parser.add_argument('--branch-code', help='Only crawl one LOTTE branch code')
    parser.add_argument('--branch-name', help='Only crawl one LOTTE branch name')
    
    args = parser.parse_args()
    if args.limit is not None and not 1 <= args.limit <= MAX_COURSE_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_COURSE_LIMIT}")
    
    crawler = LotteCrawler()
    
    if args.test:
        logger.info("Running in TEST mode")
        success = crawler.run(test_mode=True, limit=args.limit, branch_code=args.branch_code, branch_name=args.branch_name)
    elif args.categories:
        logger.info(f"Crawling specific categories: {args.categories}")
        success = crawler.run(target_categories=args.categories, limit=args.limit, branch_code=args.branch_code, branch_name=args.branch_name)
    else:
        logger.info("Crawling ALL categories")
        success = crawler.run(limit=args.limit, branch_code=args.branch_code, branch_name=args.branch_name)
    sys.exit(0 if success else 1)
