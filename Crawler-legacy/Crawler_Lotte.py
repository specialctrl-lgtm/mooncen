"""
롯데문화센터 크롤러 - 실제 구현
URL: https://culture.lotteshopping.com/index.do
"""
import sys
import os
import time
import re
from typing import List, Dict, Optional, Tuple
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_cursor
from DB.course_lifecycle import enrich_course_lifecycle, mark_stale_courses, should_skip_expired_course, utc_now
from DB.course_upsert_guards import coalesce_provider_course_id_by_raw_url
from utils import setup_logger, parse_date, extract_number, extract_material_fee_amount, infer_course_status, clean_instructor_name, clean_text
from data_parser import TargetParser, ScheduleParser, parse_crawler_target
from title_cleaner import clean_course_title
from target_cleaner import extract_target_text
from description_cleaner import clean_lotte_description_text
from Crawler.reception_period import extract_reception_period
from Crawler.Config import PROVIDERS, HEADERS, CRAWLER_CONFIG, COURSE_STATUS_MAP
from Crawler.selenium_driver import build_chrome_driver
from bs4 import BeautifulSoup
import requests
from urllib.parse import urlencode, urljoin

logger = setup_logger(__name__, 'logs/crawler_lotte.log')
parse_error_logger = setup_logger('parse_errors', 'logs/parse_errors.log')

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
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            
            options = Options()
            options.add_argument('--headless')  # 창 표시하려면 주석 처리
            options.add_argument('--no-sandbox')
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
                self.driver = None
                logger.info("Selenium WebDriver closed")
            except Exception as e:
                logger.error(f"Failed to close driver: {e}")
    
    def _get_page(self, url: str, wait_time: int = 5) -> str:
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
            
            self.driver.get(url)
            
            # 페이지 로딩 대기
            import time
            time.sleep(wait_time)
            
            # NetFunnel 대기열 체크 및 대기
            from selenium.webdriver.common.by import By
            from selenium.common.exceptions import TimeoutException
            
            try:
                # NetFunnel 요소가 있는지 확인
                netfunnel_element = self.driver.find_elements(By.ID, "NetFunnel_Skin_Top")
                if netfunnel_element:
                    logger.info("NetFunnel detected, waiting...")
                    time.sleep(10)  # 추가 대기
            except:
                pass
            
            html = self.driver.page_source
            logger.info(f"Page loaded successfully (size: {len(html)} bytes)")
            
            return html
            
        except Exception as e:
            logger.error("LOTTE page load failed. url=%s error=%s", url, e)
            if "no such window" in str(e) or "not reached" in str(e) or "web view not found" in str(e):
                logger.warning("Browser crash suspected. Restarting driver...")
                try:
                    self._close_driver()
                except:
                    self.driver = None
                
                # 잠시 대기
                import time
                time.sleep(3)
                
                # 재시도 (최대 1번만 더 재귀 호출한다고 가정하거나 여기서 다시 시도)
                # 여기서는 간단히 init driver만 하고 None 리턴 (호출자가 재시도하거나 다음으로 넘어감)
                return None
            return None

    def get_page(self, url: str, wait_time: int = 5) -> str:
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

                            if href.startswith('/'):
                                course_url = f"{self.base_url}{href}"
                            elif href.startswith('http'):
                                course_url = href
                            else:
                                course_url = f"{self.base_url}/{href}"

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

    def scrape_branch_courses(self, branch: Dict, limit: Optional[int] = None) -> List[Dict]:
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

            self._init_driver()
            self.driver.get(list_url)
            wait = WebDriverWait(self.driver, 20)

            try:
                wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "a.lec_list")) > 0)
            except TimeoutException:
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
                    break
                time.sleep(0.4)

            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            for link in soup.select("a.lec_list"):
                href = link.get("href")
                if not href:
                    continue

                course_url = urljoin(self.base_url, href)
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
            logger.error("LOTTE branch crawl failed. branch=%s branch_code=%s url=%s error=%s", branch_name, branch_code, list_url, e)

        return all_courses

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

    def _normalize_lotte_branch_token(self, value: Optional[str]) -> str:
        value = clean_text(value or "")
        for token in ("롯데문화센터", "롯데백화점", "문화센터", "백화점"):
            value = value.replace(token, "")
        return re.sub(r"[\s\[\]\(\)·ㆍ/_-]+", "", value)

    def _notice_branch_from_text(self, title: str, body_text: str, branches: List[Dict]) -> Optional[Dict]:
        ignored = {"본사", "롯데쇼핑"}
        candidates = []
        candidates.extend(re.findall(r"\[([^\]]+)\]", title or ""))
        candidates.extend(re.findall(r"→\s*([^→\n]{1,30}?점)\s*선택", body_text or ""))

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

            if not seq:
                continue

            rows.append({
                "seq": seq,
                "title": clean_text(title_elem.get_text(" ", strip=True)) if title_elem else "",
                "date": clean_text(date_elem.get_text(" ", strip=True)) if date_elem else None,
                "total_count": int(total_count) if total_count and total_count.isdigit() else None,
            })

        return rows

    def scrape_notice_rows(self, list_cnt: int = 100) -> List[Dict]:
        """Load LOTTE notice rows from /community/notice/list.ajax."""
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
            session = requests.Session()
            for page_index in range(1, 20):
                params["pageIndex"] = str(page_index)
                params["initIndex"] = "1" if page_index == 1 else str(page_index)
                response = session.get(
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

                total_count = page_rows[0].get("total_count")
                if total_count and len(rows) >= total_count:
                    break
                if len(page_rows) < list_cnt:
                    break

        except Exception as e:
            logger.warning("LOTTE notice list fetch failed: %s", e)

        logger.info("Loaded %s LOTTE notices", len(rows))
        return rows

    def scrape_notice_detail(self, seq: str) -> Tuple[Optional[BeautifulSoup], str]:
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
            response = requests.get(url, headers=self._lotte_request_headers(), timeout=30)
            response.raise_for_status()
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
            logger.warning("LOTTE notice detail fetch failed. seq=%s error=%s", seq, e)
            return None, ""

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
            course_url = urljoin(self.base_url, href)
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
        if not branch_code or not query:
            return []

        list_params = {"type": "branch", "brchCd": branch_code, "q": query}
        list_url = f"{self.base_url}/application/search/list.do?{urlencode(list_params)}"
        list_cnt = 100
        courses = []
        seen_urls = set()

        try:
            session = requests.Session()
            response = session.get(
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

                response = session.post(
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

                if total_count is None:
                    total_elem = page_soup.select_one("[data-tot-cnt]")
                    total_value = total_elem.get("data-tot-cnt") if total_elem else None
                    total_count = int(total_value) if total_value and total_value.isdigit() else None

                page_added = 0
                for link in course_links:
                    href = link.get("href")
                    if not href:
                        continue
                    course_url = urljoin(self.base_url, href)
                    if course_url in seen_urls:
                        continue
                    seen_urls.add(course_url)
                    courses.append({
                        "url": course_url,
                        "title": clean_text(link.get_text(" ", strip=True)),
                        "branch_code": branch_code,
                        "branch_name": branch_name,
                        "source": "lotte_notice_search",
                        "notice_search_term": query,
                    })
                    page_added += 1
                    if limit is not None and len(courses) >= limit:
                        break

                if page_added == 0:
                    break
                if total_count and len(courses) >= total_count:
                    break
                if len(course_links) < list_cnt:
                    break

        except Exception as e:
            logger.warning(
                "LOTTE notice search failed. branch=%s branch_code=%s query=%s error=%s",
                branch_name,
                branch_code,
                query,
                e,
            )

        logger.info(
            "LOTTE notice search found %s courses. branch=%s branch_code=%s query=%s",
            len(courses),
            branch_name,
            branch_code,
            query,
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
            r"([월화수목금토일])\s+(\d{1,2}:\d{2}\s*[~-]\s*\d{1,2}:\d{2})",
            option_text,
        )
        if schedule_match:
            option_data["schedule_raw"] = f"{schedule_match.group(1)} {schedule_match.group(2)}"

        target_parts = []
        birth_match = re.search(r"\d{2,4}\s*[~-]\s*\d{2,4}\s*년생", option_text)
        if birth_match:
            target_parts.append(birth_match.group(0))

        age_match = re.search(r"\d+\s*[~-]\s*\d+\s*세", option_text)
        if age_match:
            target_parts.append(age_match.group(0))

        month_match = re.search(r"\d+\s*[~-]\s*\d+\s*개월", option_text)
        if month_match:
            target_parts.append(month_match.group(0))

        if target_parts:
            option_data["target"] = " ".join(dict.fromkeys(target_parts))

        sessions_match = re.search(r"(\d+)\s*회", option_text)
        if sessions_match:
            option_data["schedule_raw"] = f"{option_data.get('schedule_raw') or ''} {sessions_match.group(1)}회".strip()

        return option_data

    def scrape_course_detail(self, course_url: str) -> Optional[Dict | List[Dict]]:
        """
        강좌 상세 정보 스크래핑 (Selenium)
        
        Args:
            course_url: 강좌 상세 페이지 URL
        
        Returns:
            강좌 상세 정보 딕셔너리
        """
        try:
            logger.info(f"Scraping course detail: {course_url}")
            
            # Selenium으로 페이지 가져오기
            html = self._get_page(course_url, wait_time=5)
            
            if not html:
                logger.error("LOTTE detail page empty. url=%s", course_url)
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            body_text = clean_text(soup.get_text(" ", strip=True))
            
            # URL에서 고유 강좌 키 추출
            lect_cd_match = re.search(r'lectCd=([^&]+)', course_url)
            brch_cd_match = re.search(r'brchCd=([^&]+)', course_url)
            year_match = re.search(r'yy=([^&]+)', course_url)
            semester_match = re.search(r'lectSmsterCd=([^&]+)', course_url)
            lect_cd = lect_cd_match.group(1) if lect_cd_match else None
            
            if not lect_cd:
                logger.warning("LOTTE detail missing lectCd. url=%s", course_url)
                return None

            brch_cd = brch_cd_match.group(1) if brch_cd_match else "UNKNOWN_BRANCH"
            year = year_match.group(1) if year_match else "UNKNOWN_YEAR"
            semester = semester_match.group(1) if semester_match else "UNKNOWN_SEMESTER"
            provider_course_id = f"{brch_cd}-{year}-{semester}-{lect_cd}"
            
            # 제목 - 정확한 셀렉터
            title_elem = soup.select_one('.lectNm, p.tit.lectNm')
            title = clean_text(title_elem.text) if title_elem else 'Unknown'
            
            # 강사
            instructor_elem = soup.select_one('.tcNm, span.tcNm')
            instructor = clean_instructor_name(instructor_elem.text) if instructor_elem else None
            
            # 대상
            target_elem = soup.select_one('.objClNm, dd.objClNm')
            target = clean_text(target_elem.text) if target_elem else None
            
            # 카테고리 (찾기 어려우므로 일단 None)
            category = None
            
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
            logger.error("LOTTE detail parse failed. url=%s error=%s", course_url, e)
            return None
    
    def save_branch(self, branch_code: str, branch_info: Dict) -> Optional[int]:
        """지점 정보를 DB에 저장하고 branch_id 반환"""
        try:
            branch_data = {
                'provider': 'LOTTE',
                'branch_code': branch_code,  # 수정: provider_branch_id → branch_code
                'name': branch_info['name'],
                'address': branch_info.get('address', ''),
                'phone': branch_info.get('phone', ''),
                'location': None
            }
            
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
            logger.error("LOTTE branch save failed. branch_code=%s branch=%s error=%s", branch_code, branch_info.get("name"), e)
            return None
    
    def save_course(self, course_data: Dict, branch_id: str) -> bool:
        """강좌 정보를 DB에 저장 (파싱 포함)"""
        try:
            course_data['branch_id'] = branch_id
            course_data.setdefault('apply_start', None)
            course_data.setdefault('apply_end', None)
            course_data.setdefault('apply_period_raw', None)
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
                except Exception as e:
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

            with get_db_cursor() as cursor:
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
                    logger.info(
                        f"Saved course: {course_data['title']} | "
                        f"Age: {course_data['target_age_group']} | "
                        f"Days: {course_data['schedule_days']} | "
                        f"ID: {result['id']}"
                    )
                    success = True
                else:
                    success = False
            
            return success
        except Exception as e:
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
    
    def run(
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

            total_courses = 0
            notice_course_map = {}

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

                if not course_list:
                    logger.warning(f"No courses found for LOTTE target {target_name}")
                    continue

                logger.info(f"Found {len(course_list)} courses for LOTTE target {target_name}")

                saved_count = 0
                for i, course_info in enumerate(course_list, 1):
                    if limit is not None and total_courses >= limit:
                        logger.info(f"Limit reached: {total_courses}/{limit}")
                        break

                    course_url = course_info.get("url", "")
                    try:
                        if not course_url:
                            raise ValueError("missing course url")
                        logger.info(f"Processing course {i}/{len(course_list)}: {course_info.get('title', 'Unknown')}")
                        
                        # 상세 정보 스크래핑
                        logger.debug("Scraping LOTTE detail %s/%s url=%s", i, len(course_list), course_url)
                        course_data = self.scrape_course_detail(course_url)
                        logger.debug("Scraped LOTTE detail %s/%s url=%s", i, len(course_list), course_url)
                        
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
                        
                        # 요청 간 딜레이
                        time.sleep(CRAWLER_CONFIG['delay_between_requests'])
                        
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
            if total_courses <= 0:
                logger.error("LOTTE saved 0 courses.")
                return False
            if limit is None and not test_mode:
                stale_count = mark_stale_courses('LOTTE', crawl_started_at)
                logger.info(f"Marked stale LOTTE courses inactive: {stale_count}")
            logger.info("=" * 50)
            return True
        
        except Exception as e:
            logger.error(f"Crawler failed: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        finally:
            # WebDriver 종료
            self._close_driver()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Lotte Culture Center Crawler')
    parser.add_argument('--categories', nargs='+', help='Specific category codes (e.g., adult child infant)')
    parser.add_argument('--test', action='store_true', help='Test mode: crawl 20 courses per category')
    parser.add_argument('--limit', type=int, help='Maximum number of courses to save')
    parser.add_argument('--branch-code', help='Only crawl one LOTTE branch code')
    parser.add_argument('--branch-name', help='Only crawl one LOTTE branch name')
    
    args = parser.parse_args()
    
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
