"""
MoonCen 크롤러 설정 파일
각 문화센터별 URL 및 크롤링 설정 관리
"""

# 문화센터 제공자별 설정
import re
from urllib.parse import urlparse


PROVIDER_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")
REQUIRED_PROVIDER_URLS = ("base_url", "branch_list_url", "course_list_url", "course_detail_url")
CRAWLER_CONFIG_BOUNDS = {
    "delay_between_requests": (0, 60),
    "timeout": (1, 120),
    "max_retries": (0, 10),
    "retry_delay": (0, 300),
    "concurrent_requests": (1, 16),
}


def validate_config(providers=None, crawler_config=None, headers=None) -> None:
    providers = PROVIDERS if providers is None else providers
    crawler_config = CRAWLER_CONFIG if crawler_config is None else crawler_config
    headers = HEADERS if headers is None else headers
    errors = []

    if not isinstance(providers, dict) or not 1 <= len(providers) <= 100:
        errors.append("PROVIDERS must contain 1-100 provider mappings")
    else:
        for provider_id, provider in providers.items():
            if not isinstance(provider_id, str) or not PROVIDER_ID_PATTERN.fullmatch(provider_id):
                errors.append("provider id is invalid")
                continue
            if not isinstance(provider, dict):
                errors.append(f"{provider_id} must be a mapping")
                continue
            if not isinstance(provider.get("name"), str) or not provider["name"].strip() or len(provider["name"]) > 100:
                errors.append(f"{provider_id}.name is invalid")
            if not isinstance(provider.get("enabled"), bool):
                errors.append(f"{provider_id}.enabled must be boolean")
            for field in REQUIRED_PROVIDER_URLS:
                value = provider.get(field)
                try:
                    parsed = urlparse(value if isinstance(value, str) else "")
                    valid_url = (
                        isinstance(value, str)
                        and 1 <= len(value) <= 2_048
                        and parsed.scheme == "https"
                        and bool(parsed.hostname)
                        and parsed.username is None
                        and parsed.password is None
                    )
                except (TypeError, ValueError):
                    valid_url = False
                if not valid_url:
                    errors.append(f"{provider_id}.{field} must be an HTTPS URL without credentials")

    if not isinstance(crawler_config, dict):
        errors.append("CRAWLER_CONFIG must be a mapping")
    else:
        for field, (minimum, maximum) in CRAWLER_CONFIG_BOUNDS.items():
            value = crawler_config.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
                errors.append(f"CRAWLER_CONFIG.{field} must be between {minimum} and {maximum}")

    if not isinstance(headers, dict) or not headers:
        errors.append("HEADERS must be a non-empty mapping")
    else:
        for key, value in headers.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or not key
                or len(key) > 100
                or len(value) > 2_048
                or "\r" in key
                or "\n" in key
                or "\r" in value
                or "\n" in value
            ):
                errors.append("HEADERS contains an invalid name or value")
                break

    if errors:
        raise RuntimeError("Invalid crawler configuration: " + "; ".join(errors))


PROVIDERS = {
    'LOTTE': {
        'name': '롯데문화센터',
        'base_url': 'https://culture.lotteshopping.com',
        'main_url': 'https://culture.lotteshopping.com/index.do',
        'branch_list_url': 'https://culture.lotteshopping.com/index.do',  # 메인 페이지에서 지점 선택
        'course_list_url': 'https://culture.lotteshopping.com/lecture/lecturelist.do',
        'course_detail_url': 'https://culture.lotteshopping.com/lecture/lectureDetail.do',
        'enabled': True
    },
    'EMART': {
        'name': '이마트문화센터',
        'base_url': 'https://www.cultureclub.emart.com',
        'branch_list_url': 'https://www.cultureclub.emart.com/enrolment',
        'course_list_url': 'https://www.cultureclub.emart.com/enrolment',
        'course_detail_url': 'https://www.cultureclub.emart.com',
        'enabled': True
    },
    'HOMEPLUS': {
        'name': '홈플러스문화센터',
        'base_url': 'https://www.homeplus.co.kr/culture',
        'branch_list_url': 'https://www.homeplus.co.kr/culture/branch',
        'course_list_url': 'https://www.homeplus.co.kr/culture/lecture',
        'course_detail_url': 'https://www.homeplus.co.kr/culture/lecture/detail',
        'enabled': True
    }
}

# 크롤링 헤더 설정
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
}

# 크롤링 설정
CRAWLER_CONFIG = {
    'delay_between_requests': 1,  # 요청 간 대기 시간 (초)
    'timeout': 10,  # 요청 타임아웃 (초)
    'max_retries': 3,  # 최대 재시도 횟수
    'retry_delay': 2,  # 재시도 대기 시간 (초)
    'concurrent_requests': 5,  # 동시 요청 수
}

# 강좌 상태 매핑
COURSE_STATUS_MAP = {
    '접수중': 'OPEN',
    '접수예정': 'SCHEDULED',
    '마감': 'CLOSED',
    '대기접수': 'WAITING',
    '접수종료': 'CLOSED',
}

# AI 프롬프트 템플릿
AI_PROMPTS = {
    'summary': """
다음 강좌 정보를 20자 이내의 핵심 문장으로 요약해주세요.
강좌명: {title}
설명: {description}
대상: {target}

예시: "아이와 함께 만드는 첫 도자기 체험"
""",
    
    'tags': """
다음 강좌 정보를 분석하여 검색에 최적화된 태그를 3-5개 추출해주세요.
각 태그는 # 기호로 시작하며, 띄어쓰기 없이 작성합니다.

강좌명: {title}
설명: {description}
대상: {target}
시간: {schedule}

태그 예시: #아이동반 #직장인저녁 #원데이클래스 #가성비 #초보환영

태그만 반환하세요:
""",
    
    'extract_fees': """
다음 텍스트에서 재료비 정보를 추출해주세요.
텍스트: {text}

재료비가 명시되어 있으면 숫자만 반환하고, 없으면 0을 반환하세요.
예시: "15000" 또는 "0"
"""
}

# 로깅 설정
LOG_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'logs/crawler.log',
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5,
            'formatter': 'standard',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'loggers': {
        '': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': True
        }
    }
}


validate_config()
