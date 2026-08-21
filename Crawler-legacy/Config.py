"""
MoonCen 크롤러 설정 파일
각 문화센터별 URL 및 크롤링 설정 관리
"""

# 문화센터 제공자별 설정
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
