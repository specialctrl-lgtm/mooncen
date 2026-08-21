from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import requests
import yaml
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_cursor


KAKAO_ADDRESS_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_KEYWORD_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_COORD2ADDRESS_URL = "https://dapi.kakao.com/v2/local/geo/coord2address.json"
KAKAO_DEFAULT_MAX_REQUESTS = 1000
KAKAO_FATAL_STATUS_CODES = frozenset({401, 403, 429})
NAVER_LOCAL_URL = "https://openapi.naver.com/v1/search/local.json"
RETAIL_PROVIDERS = {
    "AK_PLAZA",
    "ELAND_RETAIL",
    "EMART",
    "GALLERIA",
    "HOMEPLUS",
    "HYUNDAI_DEPT",
    "LOTTE",
    "LOTTE_MART",
    "SHINSEGAE_ACADEMY",
}
RETAIL_QUERY_PREFIXES = {
    "AK_PLAZA": "AK플라자",
    "ELAND_RETAIL": "이랜드리테일 NC 뉴코아",
    "EMART": "이마트",
    "GALLERIA": "갤러리아백화점",
    "HOMEPLUS": "홈플러스",
    "HYUNDAI_DEPT": "현대백화점",
    "LOTTE": "롯데백화점 롯데문화센터",
    "LOTTE_MART": "롯데마트",
    "SHINSEGAE_ACADEMY": "신세계백화점",
}
RETAIL_BRAND_ALIASES = {
    "AK_PLAZA": ("AK플라자", "AK PLAZA"),
    "ELAND_RETAIL": ("이랜드", "뉴코아", "NC백화점", "NC"),
    "EMART": ("이마트",),
    "GALLERIA": ("갤러리아",),
    "HOMEPLUS": ("홈플러스",),
    "HYUNDAI_DEPT": ("현대백화점", "현대아울렛", "현대"),
    "LOTTE": ("롯데백화점", "롯데몰", "타임빌라스", "롯데"),
    "LOTTE_MART": ("롯데마트맥스", "롯데마트", "MAXX"),
    "SHINSEGAE_ACADEMY": ("신세계백화점", "신세계"),
}
PROVINCE_LEVEL_NAMES = {
    "강원도",
    "강원특별자치도",
    "경기도",
    "경상남도",
    "경상북도",
    "광주광역시",
    "대구광역시",
    "대전광역시",
    "부산광역시",
    "서울특별시",
    "세종특별자치시",
    "울산광역시",
    "인천광역시",
    "전라남도",
    "전라북도",
    "전북특별자치도",
    "전남광주통합특별시",
    "제주도",
    "제주특별자치도",
    "충청남도",
    "충청북도",
}
ADMIN_AREA_ALIASES = {
    "강원도": ("강원도", "강원"),
    "강원특별자치도": ("강원특별자치도", "강원"),
    "경기도": ("경기도", "경기"),
    "경상남도": ("경상남도", "경남"),
    "경상북도": ("경상북도", "경북"),
    "광주광역시": ("광주광역시", "광주"),
    "대구광역시": ("대구광역시", "대구"),
    "대전광역시": ("대전광역시", "대전"),
    "부산광역시": ("부산광역시", "부산"),
    "서울특별시": ("서울특별시", "서울"),
    "세종특별자치시": ("세종특별자치시", "세종"),
    "울산광역시": ("울산광역시", "울산"),
    "인천광역시": ("인천광역시", "인천"),
    "전라남도": ("전라남도", "전남"),
    "전라북도": ("전라북도", "전북"),
    "전북특별자치도": ("전북특별자치도", "전북"),
    "전남광주통합특별시": (
        "전남광주통합특별시",
        "전라남도",
        "광주광역시",
        "전남",
        "광주",
    ),
    "제주도": ("제주도", "제주"),
    "제주특별자치도": ("제주특별자치도", "제주"),
    "충청남도": ("충청남도", "충남"),
    "충청북도": ("충청북도", "충북"),
}
LOCALITY_TOKEN_ALIASES = {
    "검단구": ("검단구", "서구"),
    "서해구": ("서해구", "서구"),
    "영종구": ("영종구", "중구"),
    "제물포구": ("제물포구", "동구", "중구"),
    "동탄구": ("동탄구", "화성시"),
    "만세구": ("만세구", "화성시"),
    "병점구": ("병점구", "화성시"),
    "효행구": ("효행구", "화성시"),
}
SEARCH_TEXT_REPLACEMENTS = (
    ("전남광주통합특별시교육청", "전라남도교육청"),
    ("전남광주통합특별시", "전라남도"),
    ("검단구", "서구"),
    ("서해구", "서구"),
    ("영종구", "중구"),
    ("제물포구", "중구"),
    ("동탄구", "화성시"),
    ("만세구", "화성시"),
    ("병점구", "화성시"),
    ("효행구", "화성시"),
)
NON_PHYSICAL_TOKENS = {
    "zoom",
    "각가정",
    "각자자택",
    "비대면",
    "온라인",
    "유튜브",
    "인터넷",
    "자택",
    "평생학습포털",
}
GENERIC_FACILITY_STEMS = {
    "교육장",
    "도서관",
    "문화센터",
    "복지관",
    "시민대학",
    "자치회관",
    "주민센터",
    "체육관",
    "평생교육원",
    "평생학습관",
    "평생학습센터",
}
INSTITUTION_SUFFIXES = tuple(
    sorted(
        {
            "근로자종합복지관",
            "노인종합복지관",
            "육아종합지원센터",
            "종합사회복지관",
            "청소년문화의집",
            "청소년수련관",
            "주민자치센터",
            "주민체육센터",
            "행정복지센터",
            "교육문화회관",
            "문화예술회관",
            "생활문화센터",
            "평생학습센터",
            "평생교육센터",
            "평생학습관",
            "평생교육원",
            "어울림센터",
            "스포츠센터",
            "체육센터",
            "복지회관",
            "노인복지관",
            "사회복지관",
            "주민센터",
            "자치회관",
            "문화센터",
            "문화회관",
            "여성회관",
            "가족센터",
            "시민대학",
            "초등학교",
            "고등학교",
            "중학교",
            "대학교",
            "도서관",
            "미술관",
            "박물관",
            "과학관",
            "복지관",
            "체육관",
            "수영장",
            "보건소",
            "이음터",
            "문화원",
            "공방",
            "스튜디오",
            "시청",
            "구청",
            "군청",
            "학교",
            "센터",
        },
        key=len,
        reverse=True,
    )
)
ROOM_ONLY_PATTERN = re.compile(
    r"^(?:(?:지하|b)?\s*\d+\s*층\s*)?"
    r"(?:제?\d+\s*)?"
    r"(?:강의실|강연실|교육실|교실|다목적실|회의실|프로그램실|문화실|"
    r"체육실|음악실|미술실|컴퓨터실|정보화실|요리실|재봉실|연습실|"
    r"세미나실|열람실|동아리실|창작실|배움실|배움터|강당)"
    r"(?:\s*#?\d+)?$",
    re.IGNORECASE,
)
ROAD_ADDRESS_PATTERN = re.compile(
    r"((?:(?:[가-힣]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동))\s+){0,3}"
    r"[가-힣A-Za-z0-9·.-]+(?:대로|로|길)\s*\d+"
    r"(?:(?:번길)\s*\d+(?:-\d+)?|-\d+)?)"
)
GENERIC_BRANCH_PATTERN = re.compile(
    r"(?:\(\s*(?:공통|통합)\s*\)|(?:도서관|센터|시설)\s+(?:공통|통합)(?:\s|$))"
)
ADMINISTRATIVE_UNIT_PATTERN = re.compile(r"^[가-힣]+(?:\d+)?(?:동|읍|면)$")
CURATED_BRANCH_LOCATIONS = {
    ("ANYANG_LIFELONG_LEARNING", "만안평생학습센터"): {
        "address": "경기도 안양시 만안구 냉천로 39",
        "lat": 37.3849761,
        "lon": 126.9269738,
        "source_url": "https://learning.anyang.go.kr/MW/",
    },
    ("ANYANG_LIFELONG_LEARNING", "동안평생학습센터"): {
        "address": "경기도 안양시 동안구 동안로 153",
        "lat": 37.3919601,
        "lon": 126.9491645,
        "source_url": "https://learning.anyang.go.kr/DW/front.asp",
    },
    ("ANYANG_LIFELONG_LEARNING", "만안노인복지회관"): {
        "address": "경기도 안양시 만안구 냉천로153번길 15",
        "lat": 37.3937003,
        "lon": 126.9214779,
        "source_url": "https://learning.anyang.go.kr/ay_network/Lecture_Search/list.asp",
    },
    ("ANYANG_LIFELONG_LEARNING", "동안노인복지회관"): {
        "address": "경기도 안양시 동안구 동안로 151",
        "lat": 37.391415,
        "lon": 126.9496369,
        "source_url": "https://learning.anyang.go.kr/ay_network/Lecture_Search/list.asp",
    },
    ("MUNI_WWW_ICHEON_GO_KR_1B4316ED", "여성회관"): {
        "address": "경기도 이천시 남천로 31",
        "lat": 37.2780432,
        "lon": 127.4411605,
        "coordinate_source": "GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
        "source_url": "https://www.icheon.go.kr/welfare/contents.do?mid=0203000000",
    },
    (
        "MUNI_EDU_EUMSEONG_GO_KR_DEC266D9",
        "충북혁신도시 공유평생학습관",
    ): {
        "address": "충청북도 진천군 덕산읍 대하로 203",
        "lat": 36.9084166,
        "lon": 127.537954,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "locality": "충청북도 진천군",
        "region_sido": "충청북도",
        "region_sigungu": "진천군",
        "source_url": "https://edu.eumseong.go.kr/www/selectEduLctreWebList.do?key=61",
    },
    ("DAEJEON_OK_RESERVATION", "여성가족원 본원"): {
        "address": "대전광역시 서구 배재로 181",
        "lat": 36.3246218,
        "lon": 127.3698784,
        "coordinate_source": "GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
        "source_url": "https://www.daejeon.go.kr/lif/LifContentsHtmlView.do?menuSeq=588",
    },
    ("MUNI_LLLCITY_NONSAN_GO_KR_4109B04C", "논산시평생학습관"): {
        "address": "충청남도 논산시 관촉로 113-23",
        "lat": 36.1966482,
        "lon": 127.1072211,
        "coordinate_source": "GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
        "source_url": "https://www.nonsan.go.kr/kor/html/sub05/05050501.html",
    },
    ("MUNI_JUMIN_NYJ_GO_KR_4D92ADDF", "수동면 주민자치센터"): {
        "address": "경기도 남양주시 수동면 비룡로 729",
        "lat": 37.7034011,
        "lon": 127.3259917,
        "coordinate_source": "GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
        "source_url": "https://www.nyj.go.kr/www/contents.do?key=2691",
    },
    ("MUNI_WWW_SDM_GO_KR_A9E2A1F7", "평생학습관"): {
        "address": "서울특별시 서대문구 연희로36길 49",
        "lat": 37.5782979,
        "lon": 126.9382669,
        "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
        "source_url": "https://sdm.go.kr/lll/user/home.do",
    },
    (
        "JNE_LIBRARY_LECTURE_INTEGRATED",
        "전남광주통합특별시교육청순천만생태문화교육원",
    ): {
        "address": "전라남도 순천시 생태배움길 22",
        "lat": 34.9308841,
        "lon": 127.5135625,
        "coordinate_source": "GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
        "source_url": "https://jseco.jne.go.kr/",
    },
    ("MUNI_WWW_GEUMSAN_GO_KR_3E799FCC", "금산군 청소년수련관"): {
        "address": "충청남도 금산군 금산읍 금산로 1559",
        "lat": 36.1129888,
        "lon": 127.4914838,
        "coordinate_source": "GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
        "source_url": "https://www.geumsan.go.kr/youthcenter/html/sub01/0103.html",
    },
    ("MUNI_WWW_JINJU_GO_KR_AC4F2628", "진주미래인재학습지원센터"): {
        "address": "경상남도 진주시 남강로651번길 12",
        "lat": 35.1918105,
        "lon": 128.0830398,
        "coordinate_source": "GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
        "source_url": "https://www.jinju.go.kr/06225/06285.web",
    },
    ("MUNI_YEYAK_HSCITY_GO_KR_2DFD650A", "화성남부종합사회복지관"): {
        "address": "경기도 화성시 향남읍 행정서로3길 50",
        "lat": 37.1304161,
        "lon": 126.9195953,
        "coordinate_source": "GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
        "source_url": "https://www.hscity.go.kr/www/partInfo/femaleFamily/Welfare8/Welfare8_4.jsp",
    },
    ("MUNI_WWW_GYERYONG_GO_KR_42F86CD2", "계룡시평생학습관"): {
        "address": "충청남도 계룡시 엄사면 번영5길 14",
        "lat": 36.2879213,
        "lon": 127.2380944,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "source_url": "https://gyeryong.go.kr/lll/html/sub04/0402.html",
    },
    ("MUNI_HS_CULTURE_OR_KR_B2E1E14F", "횡성문화원 문화학교"): {
        "address": "강원특별자치도 횡성군 횡성읍 앞들서3로 6",
        "lat": 37.4914043,
        "lon": 127.9784837,
        "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
        "source_url": "https://hs-culture.or.kr/page/doc.php?m_id=21",
    },
    ("ULSAN_BUKGU_PUBLIC_RESERVATION", "무룡테니스장"): {
        "address": "울산광역시 북구 신현동 산210-3",
        "lat": 35.5913512,
        "lon": 129.4193387,
        "coordinate_source": "GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
        "source_url": "https://www.bukgu.ulsan.kr/lay1/S1T209C322/contents.do",
    },
    (
        "MUNI_GOODEDU_CHUNGJU_GO_KR_66F13E51",
        "한국교통대학교 부설 평생교육원",
    ): {
        "address": "충청북도 충주시 대소원면 대학로 50",
        "lat": 36.9690564,
        "lon": 127.8708642,
        "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
        "source_url": "https://www.ut.ac.kr/",
    },
    ("MUNI_WWW_GCCITY_GO_KR_854A9E81", "중앙동문화교육센터"): {
        "address": "경기도 과천시 관문로 136",
        "lat": 37.433191,
        "lon": 126.99341,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "source_url": "https://www.gccity.go.kr/csc/jungang/main.do?mId=0100000000",
    },
    ("CULTURE_ARTS_CENTER_01A833BE43", "나주시평생학습관"): {
        "address": "전남광주통합특별시 나주시 상야2길 17",
        "lat": 35.0215788,
        "lon": 126.7886284,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "locality": "전남광주통합특별시 나주시",
        "region_sido": "전남광주통합특별시",
        "region_sigungu": "나주시",
        "source_url": "https://www.naju.go.kr/edu/",
    },
    ("HOMEPLUS", "인천송도점"): {
        "address": "인천광역시 연수구 송도국제대로 165",
        "lat": 37.3802255,
        "lon": 126.6563721,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "locality": "인천광역시 연수구",
        "region_sido": "인천광역시",
        "region_sigungu": "연수구",
        "source_url": (
            "https://mschool.homeplus.co.kr/OperationGuide/"
            "BranchStoreDetail?reqStoreCode=0113"
        ),
    },
    ("HOMEPLUS", "인천연수점"): {
        "address": "인천광역시 연수구 청능대로 210",
        "lat": 37.4059982,
        "lon": 126.6836221,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "locality": "인천광역시 연수구",
        "region_sido": "인천광역시",
        "region_sigungu": "연수구",
        "source_url": (
            "https://mschool.homeplus.co.kr/OperationGuide/"
            "BranchStoreDetail?reqStoreCode=0103"
        ),
    },
    (
        "INCHEON_DISABLED_WELFARE_NOTICE",
        "인천광역시장애인종합복지관",
    ): {
        "address": "인천광역시 연수구 앵고개로 130",
        "lat": 37.4124316,
        "lon": 126.659467,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "locality": "인천광역시 연수구",
        "region_sido": "인천광역시",
        "region_sigungu": "연수구",
        "source_url": "https://icjb.or.kr/",
    },
    (
        "MUNI_DYLIB_JNE_GO_KR_A99A023A",
        "전남광주통합특별시교육청담양도서관",
    ): {
        "address": "전라남도 담양군 담양읍 미리산길 31-48",
        "lat": 35.3120825,
        "lon": 126.9839062,
        "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
        "locality": "전남광주통합특별시 담양군",
        "region_sido": "전남광주통합특별시",
        "region_sigungu": "담양군",
        "source_url": "https://dylib.jne.go.kr/menu.es?mid=a60100000000",
    },
    ("MUNI_WWW_GN_GO_KR_E6671160", "강릉컬링센터"): {
        "address": "강원특별자치도 강릉시 종합운동장길 32",
        "lat": 37.7728814,
        "lon": 128.8937336,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "locality": "강원특별자치도 강릉시",
        "region_sido": "강원특별자치도",
        "region_sigungu": "강릉시",
        "source_url": (
            "https://www.gn.go.kr/www/selectBbsNttView.do"
            "?bbsNo=128&key=1150&nttNo=202967"
        ),
    },
    ("ULSAN_EDU_BOOKING", "울산직업교육복합센터"): {
        "address": "울산광역시 남구 중앙로204번길 49",
        "lat": 35.5429749,
        "lon": 129.317688,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "locality": "울산광역시 남구",
        "region_sido": "울산광역시",
        "region_sigungu": "남구",
        "source_url": "https://use.go.kr/vocation-edu/center/infor.jsp",
    },
    ("ULSAN_EDU_BOOKING", "울산창의융합교육센터"): {
        "address": "울산광역시 남구 남부순환도로 111",
        "lat": 35.540048,
        "lon": 129.268542,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "locality": "울산광역시 남구",
        "region_sido": "울산광역시",
        "region_sigungu": "남구",
        "source_url": "https://use.go.kr/cce/index.do",
    },
    ("ULSAN_EDU_BOOKING", "울산특수교육지원센터"): {
        "address": "울산광역시 울주군 언양읍 언양로 594",
        "lat": 35.5575339,
        "lon": 129.1833149,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "locality": "울산광역시 울주군",
        "region_sido": "울산광역시",
        "region_sigungu": "울주군",
        "source_url": (
            "https://use.go.kr/special/centerInfo/introduce/"
            "introduce_1.jsp"
        ),
    },
    ("ULSAN_EDU_BOOKING", "학생교육문화회관"): {
        "address": "울산광역시 중구 곽남길 95",
        "lat": 35.5734245,
        "lon": 129.3380371,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_ADDRESS",
        "locality": "울산광역시 중구",
        "region_sido": "울산광역시",
        "region_sigungu": "중구",
        "source_url": "https://use.go.kr/usecc/intro/location.jsp",
    },
    ("MUNI_WWW_GURI_GO_KR_E0C65498", "장자호수생태체험관"): {
        "address": "경기도 구리시 장자호수길 76-42",
        "lat": 37.583029,
        "lon": 127.1385009,
        "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
        "locality": "경기도 구리시",
        "region_sido": "경기도",
        "region_sigungu": "구리시",
        "source_url": "https://guri.go.kr/ecoedu/contents.do?key=976",
    },
    ("MUNI_WWW_GDLIBRARY_OR_KR_7E7ADF81", "성내도서관"): {
        "address": "서울특별시 강동구 성안로 106-1",
        "lat": 37.5328621,
        "lon": 127.1333521,
        "coordinate_source": "GOOGLE_PLACES_TEXT_SEARCH",
        "locality": "서울특별시 강동구",
        "region_sido": "서울특별시",
        "region_sigungu": "강동구",
        "source_url": "https://www.gdlibrary.or.kr/sn/menu/120/content",
    },
    ("MUNI_WWW_SONGPA_GO_KR_982793EC", "뮤직스튜디오"): {
        "address": "서울특별시 송파구 올림픽로 326",
        "lat": 37.5144533,
        "lon": 127.1059047,
        "coordinate_source": "GOOGLE_PLACES_TEXT_SEARCH",
        "locality": "서울특별시 송파구",
        "region_sido": "서울특별시",
        "region_sigungu": "송파구",
        "source_url": (
            "https://www.songpa.go.kr/learn/youth/campus/"
            "instrum_lib_intro.do"
        ),
    },
    ("MUNI_GEUMCHEONLIB_SEOUL_KR_E6151FD4", "해오름작은도서관"): {
        "address": "서울특별시 금천구 시흥대로123길 11, 4층",
        "lat": 37.47019,
        "lon": 126.89702,
        "coordinate_source": "NAVER_LOCAL_SEARCH",
        "locality": "서울특별시 금천구",
        "region_sido": "서울특별시",
        "region_sigungu": "금천구",
        "source_url": (
            "https://geumcheonlib.seoul.kr/geumcheonlib/uce/content/"
            "contentList.do?selfId=1123"
        ),
    },
    ("MUNI_WWW_OSANEDU_GO_KR_8A50CEDC", "하천녹지과"): {
        "address": "경기도 오산시 오산천로 52",
        "lat": 37.1380974,
        "lon": 127.0648497,
        "coordinate_source": "NAVER_LOCAL_SEARCH",
        "locality": "경기도 오산시",
        "region_sido": "경기도",
        "region_sigungu": "오산시",
        "source_url": (
            "https://www.osanedu.go.kr/app/app0101/selectEdcDtls.do"
            "?edcCode=LFT0029322&edcTy=LFT"
        ),
    },
    ("MUNI_WWW_OSANEDU_GO_KR_8A50CEDC", "기획예산과"): {
        "address": "경기도 오산시 성호대로 141",
        "lat": 37.1497727,
        "lon": 127.0770233,
        "coordinate_source": "GOOGLE_PLACES_TEXT_SEARCH",
        "locality": "경기도 오산시",
        "region_sido": "경기도",
        "region_sigungu": "오산시",
        "source_url": (
            "https://www.osanedu.go.kr/app/app0101/selectEdcDtls.do"
            "?edcCode=LFT0029344&edcTy=LFT"
        ),
    },
    ("MUNI_WWW_GOESAN_GO_KR_EAE2C3E3", "괴산군평생학습관"): {
        "address": "충청북도 괴산군 괴산읍 읍내로 184, 괴산군립도서관 3층",
        "lat": 36.8003482,
        "lon": 127.7891007,
        "coordinate_source": "NAVER_LOCAL_SEARCH_BY_FACILITY_NAME",
        "locality": "충청북도 괴산군",
        "region_sido": "충청북도",
        "region_sigungu": "괴산군",
        "source_url": "https://www.goesan.go.kr/gslll/contents.do?key=1891",
    },
    ("MUNI_WWW_NOWON_KR_FBD1F92A", "노원어르신상담센터"): {
        "address": (
            "서울특별시 노원구 수락산로 214, "
            "구립수락노인종합복지관 4층"
        ),
        "lat": 37.6709103,
        "lon": 127.0548074,
        "coordinate_source": "NAVER_LOCAL_SEARCH",
        "locality": "서울특별시 노원구",
        "region_sido": "서울특별시",
        "region_sigungu": "노원구",
        "source_url": "https://www.nowonsangdam.com/main/sub.html?pageCode=5",
    },
    ("MUNI_YEYAK_HSCITY_GO_KR_2DFD650A", "화성시민대학"): {
        "address": "경기도 화성시 효행구 봉담읍 효행로 212 4층",
        "lat": 37.2285182,
        "lon": 126.9686585,
        "coordinate_source": "NAVER_LOCAL_SEARCH",
        "locality": "경기도 화성시 효행구",
        "region_sido": "경기도",
        "region_sigungu": "화성시 효행구",
        "source_url": "https://yeyak.hscity.go.kr/1002/3001/lectureAllList.do",
    },
}
CURATED_BRANCH_LOCATIONS.update(
    {
        (
            "MUNI_WWW_BSDONGGU_GO_KR_6798361C",
            "부산광역시 동구",
        ): {
            "address": "부산광역시 동구 초량중로 38",
            "lat": 35.1153645,
            "lon": 129.0375698,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.bsdonggu.go.kr/lll/index.donggu",
        },
        ("DAEJEON_OK_RESERVATION", "남부여성가족원"): {
            "address": "대전광역시 동구 동구청로 36",
            "lat": 36.3018762,
            "lon": 127.4603077,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.daejeon.go.kr/lif/index.do",
        },
        ("DAEJEON_OK_RESERVATION", "북부여성가족원"): {
            "address": "대전광역시 유성구 대덕대로 1175",
            "lat": 36.4319672,
            "lon": 127.3869404,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.daejeon.go.kr/lif/index.do",
        },
        (
            "MUNI_WWW_CHEONGJU_GO_KR_AB0C903B",
            "충청북도 청주시 흥덕구",
        ): {
            "address": "충청북도 청주시 상당구 상당로69번길 38",
            "lat": 36.634745,
            "lon": 127.488601,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.cheongju.go.kr/",
        },
        (
            "MUNI_WWW_CHEONGJU_GO_KR_D84B5214",
            "충청북도 청주시 청원구",
        ): {
            "address": "충청북도 청주시 상당구 상당로69번길 38",
            "lat": 36.634745,
            "lon": 127.488601,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.cheongju.go.kr/",
        },
        (
            "MUNI_LLL_CHEONGJU_GO_KR_50067AE6",
            "충청북도 청주시 서원구",
        ): {
            "address": "충청북도 청주시 흥덕구 월명로13번길 52",
            "lat": 36.6361435,
            "lon": 127.4502476,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://lll.cheongju.go.kr/",
        },
        (
            "MUNI_LLL_CHEONGJU_GO_KR_A90C7827",
            "충청북도 청주시",
        ): {
            "address": "충청북도 청주시 흥덕구 월명로13번길 52",
            "lat": 36.6361435,
            "lon": 127.4502476,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://lll.cheongju.go.kr/",
        },
        (
            "MUNI_LEARNING_SUWON_GO_KR_3AF2DB76",
            "경기도 수원시 권선구",
        ): {
            "address": "경기도 수원시 팔달구 월드컵로381번길 2",
            "lat": 37.2914644,
            "lon": 127.0278807,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://learning.suwon.go.kr/",
        },
        (
            "MUNI_LEARNING_SUWON_GO_KR_402954DA",
            "경기도 수원시",
        ): {
            "address": "경기도 수원시 팔달구 월드컵로381번길 2",
            "lat": 37.2914644,
            "lon": 127.0278807,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://learning.suwon.go.kr/",
        },
        (
            "MUNI_LEARNING_SUWON_GO_KR_A915395E",
            "경기도 수원시",
        ): {
            "address": "경기도 수원시 팔달구 월드컵로381번길 2",
            "lat": 37.2914644,
            "lon": 127.0278807,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://learning.suwon.go.kr/",
        },
        (
            "MUNI_GWSYED_GWE_GO_KR_0D269FFD",
            "강원특별자치도 양양군",
        ): {
            "address": "강원특별자치도 속초시 미시령로 3336",
            "lat": 38.2048137,
            "lon": 128.5725003,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://gwsed.gwe.go.kr/",
        },
        (
            "MUNI_CNLIB_GNE_GO_KR_A3514402",
            "경상남도 창녕군",
        ): {
            "address": "경상남도 창녕군 창녕읍 남창녕로 52",
            "lat": 35.5357546,
            "lon": 128.5033884,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnlib.gne.go.kr/",
        },
        (
            "MUNI_DYLIB_JNE_GO_KR_A2AEEC45",
            "전라남도 담양군",
        ): {
            "address": "전라남도 담양군 담양읍 미리산길 31-48",
            "lat": 35.3120825,
            "lon": 126.9839062,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://dylib.jne.go.kr/",
        },
        (
            "MUNI_RESERVE_ANSAN_GO_KR_5D6B8309",
            "선부3동",
        ): {
            "address": "경기도 안산시 단원구 선삼로 47",
            "lat": 37.3450842,
            "lon": 126.8125687,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.ansan.go.kr/danwongu/",
        },
        (
            "MUNI_WWW_DONGGU_GO_KR_9A7A5E6F",
            "성남동행정복지센터",
        ): {
            "address": "대전광역시 동구 계족로368번길 70",
            "lat": 36.3445545,
            "lon": 127.4374447,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.donggu.go.kr/",
        },
        (
            "MUNI_EDU_ICJG_GO_KR_99553B01",
            "인천광역시 중구",
        ): {
            "address": "인천광역시 제물포구 신포로27번길 80",
            "lat": 37.473781,
            "lon": 126.621588,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "region_sido": "인천광역시",
            "region_sigungu": "제물포구",
            "source_url": "https://www.icjg.go.kr/",
        },
        (
            "MUNI_JHED_JNE_GO_KR_16474ED5",
            "전남광주통합특별시교육청전남유아교육진흥원",
        ): {
            "address": "전남광주통합특별시 순천시 서면 둔대수계길 35",
            "lat": 35.0230474,
            "lon": 127.4527124,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "region_sido": "전남광주통합특별시",
            "region_sigungu": "순천시",
            "source_url": "https://iedu.jne.go.kr/",
        },
        (
            "MUNI_JHED_JNE_GO_KR_16474ED5",
            "전남광주통합특별시교육청백운학생수련장",
        ): {
            "address": "전남광주통합특별시 광양시 옥룡면 신재로 1549-8",
            "lat": 35.0865092,
            "lon": 127.597961,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "region_sido": "전남광주통합특별시",
            "region_sigungu": "광양시",
            "source_url": "https://yeyak.jne.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "논산복합문화체육센터 온담",
        ): {
            "address": "충청남도 논산시 관촉로 113-23",
            "lat": 36.1967832,
            "lon": 127.1075559,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "도고 옹기발효음식전시체험관",
        ): {
            "address": "충청남도 아산시 도고면 도고산로 810",
            "lat": 36.7549389,
            "lon": 126.8802007,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "바드챔버하우스",
        ): {
            "address": "충청남도 공주시 유구읍 중앙2길 92-32",
            "lat": 36.5538993,
            "lon": 126.9539117,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "보령문화의전당 대강당",
        ): {
            "address": "충청남도 보령시 대흥로 63",
            "lat": 36.3507282,
            "lon": 126.5903957,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "서천문예의전당 대강당",
        ): {
            "address": "충청남도 서천군 서천읍 서천로14번길 20",
            "lat": 36.0763968,
            "lon": 126.6983529,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "예산문예회관",
        ): {
            "address": "충청남도 예산군 예산읍 아리랑로 185-14",
            "lat": 36.6854352,
            "lon": 126.8495594,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "외암민속나을 한옥갤러리",
        ): {
            "address": "충청남도 아산시 송악면 외암민속길 5",
            "lat": 36.7303755,
            "lon": 127.0161335,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "천안어린이꿈누리터 (구. 천안시 어린이 회관) 꿈누리대공연장",
        ): {
            "address": "충청남도 천안시 동남구 옛시청길 39",
            "lat": 36.8066437,
            "lon": 127.1505416,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "천안어린이꿈누리터 (구. 천안시 어린이 회관) 자유소극장",
        ): {
            "address": "충청남도 천안시 동남구 옛시청길 39",
            "lat": 36.8066437,
            "lon": 127.1505416,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "천안예술의전당 대공연장",
        ): {
            "address": "충청남도 천안시 동남구 성남면 종합휴양지로 185",
            "lat": 36.7560811,
            "lon": 127.2252896,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "천안예술의전당 소공연장",
        ): {
            "address": "충청남도 천안시 동남구 성남면 종합휴양지로 185",
            "lat": 36.7560811,
            "lon": 127.2252896,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://cnc.cacf.or.kr/",
        },
    }
)

CURATED_BRANCH_LOCATIONS.update(
    {
        ("CULTURE_FACILITY", "백제역사문화관"): {
            "address": "충청남도 부여군 규암면 백제문로 455",
            "lat": 36.305228,
            "lon": 126.9053763,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.bhm.or.kr/html/kr/view/view_03_05.html",
        },
        ("CULTURE_FACILITY", "반다비체육문화센터"): {
            "address": "경상남도 고성군 고성읍 기월리 95-28",
            "lat": 34.980179,
            "lon": 128.3130146,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.goseong.go.kr/",
        },
        (
            "MUNI_EDU_ICJG_GO_KR_3C098A20",
            "인천광역시 중구",
        ): {
            "address": "인천광역시 제물포구 신포로27번길 80",
            "lat": 37.473781,
            "lon": 126.621588,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.icjg.go.kr/",
        },
        (
            "MUNI_EDU_ICJG_GO_KR_C0E20FBE",
            "인천광역시 중구",
        ): {
            "address": "인천광역시 제물포구 신포로27번길 80",
            "lat": 37.473781,
            "lon": 126.621588,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.icjg.go.kr/",
        },
        (
            "MUNI_DYLIB_JNE_GO_KR_1412DDEF",
            "전라남도 담양군",
        ): {
            "address": "전남광주통합특별시 담양군 담양읍 미리산길 31-48",
            "lat": 35.3120825,
            "lon": 126.9839062,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://dylib.jne.go.kr/",
        },
        (
            "MUNI_GWANGYANG_GO_KR_900203DD",
            "전라남도 광양시",
        ): {
            "address": "전남광주통합특별시 광양시 시청로 33",
            "lat": 34.9406575,
            "lon": 127.6958987,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.gwangyang.go.kr/",
        },
        (
            "MUNI_LEARNING_SUWON_GO_KR_6ABE3488",
            "경기도 수원시",
        ): {
            "address": "경기도 수원시 팔달구 월드컵로381번길 2",
            "lat": 37.2914644,
            "lon": 127.0278807,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://learning.suwon.go.kr/",
        },
        (
            "MUNI_GYLIFE_JNE_GO_KR_AFB03FB8",
            "전라남도 광양시",
        ): {
            "address": "전남광주통합특별시 광양시 안산길 23",
            "lat": 34.9617681,
            "lon": 127.7174052,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://gylife.jne.go.kr/",
        },
        (
            "MUNI_GSLIB_JNE_GO_KR_80914C01",
            "전라남도 곡성군",
        ): {
            "address": "전남광주통합특별시 곡성군 곡성읍 읍내7길 29",
            "lat": 35.2821601,
            "lon": 127.289732,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://gslib.jne.go.kr/",
        },
        (
            "MUNI_JHED_JNE_GO_KR_16474ED5",
            "전라남도 장흥군",
        ): {
            "address": "전남광주통합특별시 장흥군 장흥읍 동교로 64-17",
            "lat": 34.6864491,
            "lon": 126.9050293,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://jhed.jne.go.kr/jhed/main.do?sysId=jhed",
        },
        ("MUNI_RESERVE_ANSAN_GO_KR_5D6B8309", "고잔동"): {
            "address": "경기도 안산시 단원구 화정천동로 252",
            "lat": 37.3271114,
            "lon": 126.8191528,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://reserve.ansan.go.kr/",
        },
        ("MUNI_RESERVE_ANSAN_GO_KR_5D6B8309", "반월동"): {
            "address": "경기도 안산시 상록구 건건로 51",
            "lat": 37.306075,
            "lon": 126.902263,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://reserve.ansan.go.kr/",
        },
        ("MUNI_RESERVE_ANSAN_GO_KR_5D6B8309", "백운동"): {
            "address": "경기도 안산시 단원구 원선로 31",
            "lat": 37.3279708,
            "lon": 126.7970248,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://reserve.ansan.go.kr/",
        },
        ("MUNI_RESERVE_ANSAN_GO_KR_5D6B8309", "부곡동"): {
            "address": "경기도 안산시 상록구 성호로 326",
            "lat": 37.3319446,
            "lon": 126.8610894,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://reserve.ansan.go.kr/",
        },
        ("MUNI_RESERVE_ANSAN_GO_KR_5D6B8309", "신길동"): {
            "address": "경기도 안산시 단원구 삼일로 42-7",
            "lat": 37.3348413,
            "lon": 126.7831366,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://reserve.ansan.go.kr/",
        },
        (
            "MUNI_SUGANG_SEONGNAM_GO_KR_4D24781E",
            "경기도 성남시 중원구",
        ): {
            "address": "경기도 성남시 중원구 제일로 36",
            "lat": 37.4305232,
            "lon": 127.1372097,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://sugang.seongnam.go.kr/",
        },
        (
            "MUNI_WWW_HFCT_OR_KR_05C08858",
            "1기: 능주 한아름회경로당/ 2기: 화순 어울림센터",
        ): {
            "address": (
                "전남광주통합특별시 화순군 능주면 잠정햇살길 2 / "
                "전남광주통합특별시 화순군 화순읍 쌍충로 38"
            ),
            "lat": 34.9847451,
            "lon": 126.9615643,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.hfct.or.kr/edu.do?M=0502020000&S=S01",
        },
        (
            "MUNI_USBL_BUKGU_ULSAN_KR_A68023CB",
            "울산광역시 북구",
        ): {
            "address": "울산광역시 북구 두부곡1길 9",
            "lat": 35.584182,
            "lon": 129.3672,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": (
                "https://usbl.bukgu.ulsan.kr/main/contents.do?idx=4460"
            ),
        },
        (
            "MUNI_WWW_BCL_GO_KR_DDAE2544",
            "경기도 부천시",
        ): {
            "address": "경기도 부천시 원미구 상이로 12",
            "lat": 37.490188,
            "lon": 126.7446807,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.bcl.go.kr/",
        },
        (
            "MUNI_WWW_DONGGU_KR_F3EB5A73",
            "광주광역시 동구",
        ): {
            "address": "전남광주통합특별시 동구 서남로 1",
            "lat": 35.1460818,
            "lon": 126.9232859,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.donggu.kr/",
        },
        ("MUNI_WWW_YANGJU_GO_KR_E168EB3A", "도시환경사업소"): {
            "address": "경기도 양주시 평화로 1920",
            "lat": 37.8597276,
            "lon": 127.0573497,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.yangju.go.kr/",
            "skip_course_address_backfill": True,
        },
        ("MUNI_WWW_YJLIB_GO_KR_ED9BCD30", "여주"): {
            "address": "경기도 여주시 여양로 190-17",
            "lat": 37.2996997,
            "lon": 127.6509998,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.yjlib.go.kr/",
        },
        ("MUNI_WWW_YC_GO_KR_54558363", "부모아카데미"): {
            "address": "경상북도 영천시 최무선로 243",
            "lat": 35.9653024,
            "lon": 128.9265589,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.yc.go.kr/edu/",
        },
        ("MUNI_WWW_GBGS_GO_KR_4D7732DD", "중앙동"): {
            "address": "경상북도 경산시 경안로30길 18",
            "lat": 35.819795,
            "lon": 128.7402826,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.gbgs.go.kr/",
        },
        (
            "MUNI_WWW_HONGCHEON_GO_KR_F5083BE8",
            "홍천생명건강과학관",
        ): {
            "address": "강원특별자치도 홍천군 홍천읍 생명과학관길 78",
            "lat": 37.6790513,
            "lon": 127.8805375,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.hongcheon.go.kr/sciencecenter/index.do",
        },
        (
            "MUNI_WWW_HONGCHEON_GO_KR_F5083BE8",
            "홍천생명건강과학관 1층",
        ): {
            "address": "강원특별자치도 홍천군 홍천읍 생명과학관길 78",
            "lat": 37.6790513,
            "lon": 127.8805375,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.hongcheon.go.kr/sciencecenter/index.do",
        },
        ("SASANG_RESERVATION", "동주민센터"): {
            "address": "부산광역시 사상구 냉정로 10",
            "lat": 35.1473489,
            "lon": 129.0014,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.sasang.go.kr/",
        },
        (
            "MUNI_WWW_YEONGDO_GO_KR_33400564",
            "국내 명문대 캠퍼스 탐방",
        ): {
            "address": "부산광역시 영도구 태종로 423",
            "lat": 35.0911989,
            "lon": 129.0678749,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": (
                "https://www.yeongdo.go.kr/reserve/01785/01791.web"
            ),
        },
        (
            "MUNI_WWW_YEONGDO_GO_KR_33400564",
            "영도 아카데미",
        ): {
            "address": "부산광역시 영도구 태종로 423",
            "lat": 35.0911989,
            "lon": 129.0678749,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": (
                "https://www.yeongdo.go.kr/reserve/01785/01791.web"
            ),
        },
        (
            "MUNI_WWW_YEONGDO_GO_KR_33400564",
            "유럽예술여행 강좌 신청",
        ): {
            "address": "부산광역시 영도구 함지로79번길 6",
            "lat": 35.0753577,
            "lon": 129.0668364,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": (
                "https://www.yeongdo.go.kr/reserve/01785/01791.web"
            ),
        },
        (
            "MUNI_WWW_YEONGDO_GO_KR_33400564",
            "영도도서관",
        ): {
            "address": (
                "부산광역시 영도구 함지로79번길 6 "
                "(동삼동) (영도어울림문화공원 내) 영도도서관"
            ),
            "lat": 35.0747052,
            "lon": 129.0652936,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": (
                "https://www.yeongdo.go.kr/reserve/01785/01791.web"
            ),
        },
        (
            "MUNI_WWW_YEONGDO_GO_KR_33400564",
            "영도도서관남항분관",
        ): {
            "address": (
                "부산광역시 영도구 절영로 71 "
                "(남항동2가) 영도어린이영어도서관"
            ),
            "lat": 35.0886081,
            "lon": 129.0389276,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": (
                "https://www.yeongdo.go.kr/reserve/01785/01791.web"
            ),
        },
        (
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "충청남도 천안시 서북구",
        ): {
            "address": "충청남도 예산군 삽교읍 예학로 10-22",
            "lat": 36.6614051,
            "lon": 126.6717637,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.cnctf.or.kr/",
            "skip_course_address_backfill": True,
        },
        (
            "MUNI_HOME_PEN_GO_KR_92635850",
            "부산광역시 동래구",
        ): {
            "address": "부산광역시 동래구 동래로179번길 31",
            "lat": 35.2052831,
            "lon": 129.0903586,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://home.pen.go.kr/dongnae/",
            "skip_course_address_backfill": True,
        },
        (
            "MUNI_LIB_GWE_GO_KR_303FFE72",
            "강원특별자치도 삼척시",
        ): {
            "address": "강원특별자치도 삼척시 진주로 48-41",
            "lat": 37.4452105,
            "lon": 129.1646444,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://lib.gwe.go.kr/samecc/main",
            "skip_course_address_backfill": True,
        },
        (
            "MUNI_LIB_GWE_GO_KR_5D9C27C1",
            "강원특별자치도 원주시",
        ): {
            "address": "강원특별자치도 원주시 북원로 2312",
            "lat": 37.3511408,
            "lon": 127.9355165,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://lib.gwe.go.kr/wjecc/main",
            "skip_course_address_backfill": True,
        },
        (
            "MUNI_LIB_GWE_GO_KR_BF2CA306",
            "강원특별자치도 태백시",
        ): {
            "address": "강원특별자치도 태백시 태백로 1126",
            "lat": 37.1579159,
            "lon": 128.989302,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://lib.gwe.go.kr/tblib/main",
            "skip_course_address_backfill": True,
        },
        (
            "MUNI_ULLEUNGGUN_FAMILYNET_OR_KR_10E2058E",
            "경상북도 울릉군",
        ): {
            "address": "경상북도 울릉군 울릉읍 봉래2길 31",
            "lat": 37.4936249,
            "lon": 130.9089237,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://ulleunggun.familynet.or.kr/center/",
            "skip_course_address_backfill": True,
        },
        (
            "MUNI_YEYAK_SYF_OR_KR_7D3E2EF5",
            "경기도 수원시 장안구",
        ): {
            "address": "경기도 수원시 팔달구 권광로 293",
            "lat": 37.2739402,
            "lon": 127.0349868,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://yeyak.syf.or.kr/",
            "skip_course_address_backfill": True,
        },
        (
            "MUNI_PAJU_PCY_OR_KR_412053A6",
            "경기도 파주시",
        ): {
            "address": "경기도 파주시 문산읍 통일로 1680",
            "lat": 37.8563175,
            "lon": 126.7895252,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.pcy.or.kr/pyc",
            "skip_course_address_backfill": True,
        },
        (
            "MUNI_LLL_BUSAN_GO_KR_944C621B",
            "부산광역시 서구",
        ): {
            "address": "부산광역시 북구 효열로 256",
            "lat": 35.2671653,
            "lon": 129.0216049,
            "coordinate_source": "NAVER_LOCAL_SEARCH_VERIFIED",
            "source_url": "https://www.bgli.re.kr/",
            "skip_course_address_backfill": True,
        },
    }
)

CURATED_BRANCH_PATTERN_LOCATIONS = (
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": (
            r"^(?:광주)?롯데아울렛(?:\s*광주월드컵점)?\s*앞\s*광장$"
        ),
        "address": "전남광주통합특별시 서구 금화로 240",
        "lat": 35.1337231,
        "lon": 126.8748915,
        "source_url": (
            "https://www.gjcf.or.kr/cf/cultureart/list/calendar/21582/view.do"
        ),
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^광주\s*예술의\s*전당 광주공연마루$",
        "address": "전남광주통합특별시 서구 상무시민로 3",
        "lat": 35.1557814,
        "lon": 126.8397316,
        "source_url": "https://www.gjart.go.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^광주\s*예술의\s*전당",
        "address": "전남광주통합특별시 북구 북문대로 60",
        "lat": 35.1779509,
        "lon": 126.8813619,
        "source_url": "https://www.gjart.go.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^국립아시아문화전당",
        "address": "전남광주통합특별시 동구 문화전당로 38",
        "lat": 35.1476072,
        "lon": 126.9212433,
        "source_url": "https://www.acc.go.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^소극장 공연일번지$",
        "address": "전남광주통합특별시 동구 금남로 218-9",
        "lat": 35.1492776,
        "lon": 126.9157185,
        "source_url": "https://dmgj.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^빛고을국악전수관",
        "address": "전남광주통합특별시 서구 풍금로 182",
        "lat": 35.1320071,
        "lon": 126.8597625,
        "source_url": "https://dmgj.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^광산구 소촌아트팩토리",
        "address": "전남광주통합특별시 광산구 소촌로85번길 14-9",
        "lat": 35.1523504,
        "lon": 126.7909322,
        "source_url": "https://www.gwangsan.go.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"(?:3·1만세운동길 6|이강하미술관)",
        "address": "전남광주통합특별시 남구 3·1만세운동길 6",
        "lat": 35.1368655,
        "lon": 126.9155804,
        "source_url": "https://www.lkh-artmuseum.com/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^광주시립미술관",
        "address": "전남광주통합특별시 북구 하서로 52",
        "lat": 35.183233,
        "lon": 126.8857357,
        "source_url": "https://artmuse.gwangju.go.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^광주\s*보헤미안",
        "address": "전남광주통합특별시 동구 문화전당로 43 지하 1층",
        "lat": 35.1462889,
        "lon": 126.9185446,
        "source_url": "https://dmgj.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^무형유산 전수관",
        "address": "전남광주통합특별시 동구 의재로 222",
        "lat": 35.1336416,
        "lon": 126.9523624,
        "source_url": "https://www.gtcc.or.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^나주혁신 빛가람호수공원",
        "address": "전남광주통합특별시 나주시 중야2길 26",
        "lat": 35.0185225,
        "lon": 126.7868537,
        "locality": "전남광주통합특별시 나주시",
        "source_url": "https://dmgj.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^담양 해동문화예술촌",
        "address": "전남광주통합특별시 담양군 담양읍 지침1길 10",
        "lat": 35.3179735,
        "lon": 126.983992,
        "locality": "전남광주통합특별시 담양군",
        "source_url": "https://www.dycf.or.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"민들레소극장$",
        "address": "전남광주통합특별시 동구 동계천로 111",
        "lat": 35.1508189,
        "lon": 126.9226962,
        "source_url": "https://dmgj.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^서울 경인미술관.+광주비움박물관",
        "address": "전남광주통합특별시 동구 제봉로 143-1",
        "lat": 35.1504084,
        "lon": 126.9202913,
        "source_url": "https://www.gjcf.or.kr/cf/cultureart/list/calendar/21485/view.do",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^광주월드컵경기장",
        "address": "전남광주통합특별시 서구 금화로 240",
        "lat": 35.1337231,
        "lon": 126.8748915,
        "source_url": "https://dmgj.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"농성2동 행정복지센터",
        "address": "전남광주통합특별시 서구 화정로 314",
        "lat": 35.150349,
        "lon": 126.89025,
        "source_url": "https://www.seogu.gwangju.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^전시실$",
        "address": "전남광주통합특별시 남구 천변좌로338번길 7",
        "lat": 35.1480269,
        "lon": 126.9087398,
        "source_url": "https://dmgj.kr/event.es?mid=a10301000000&seq=8774&act=view",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^박물관 정원",
        "address": "전남광주통합특별시 북구 하서로 110",
        "lat": 35.189051,
        "lon": 126.88305,
        "source_url": "https://gwangju.museum.go.kr/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^아트스페이스 홍학관$",
        "address": "전남광주통합특별시 동구 문화전당로35번길 16-4 지하 1층",
        "lat": 35.1454858,
        "lon": 126.9175599,
        "source_url": "https://artspacehhg.com/",
    },
    {
        "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
        "pattern": r"^프리마아트홀$",
        "address": "전남광주통합특별시 북구 첨단연신로77번길 20 1층",
        "lat": 35.205464,
        "lon": 126.8637368,
        "source_url": "https://dmgj.kr/event.es?mid=a10301000000&seq=9536&act=view",
    },
)

INVALID_ADDRESS_PROVIDER_LOCALITIES = {
    "MUNI_CNC_CACF_OR_KR_7A12B48E": "충청남도",
    "MUNI_WWW_GJCF_OR_KR_F9585EF3": "광주광역시",
    "MUNI_HOME_PEN_GO_KR_92635850": "부산광역시",
}
TARGET_NAME_TRAILING_NOISE_RE = re.compile(
    r"\s+(?:정규프로그램|중복|부분|이전 URL|전체 교육강좌|신청 안내|"
    r"홈페이지에 오신|부분수집|부분 목록).*$",
    re.IGNORECASE,
)
PHYSICAL_OPERATOR_NAME_RE = re.compile(
    r"^(.+(?:"
    r"평생학습관|평생학습원|평생교육원|글로벌평생학습관|"
    r"도서관|여성회관|여성가족원|교육지원청|교육청|"
    r"청소년수련관|문화관광재단|문화재단|문화원|문화회관|"
    r"시설관리공단|도시공사|체육센터|시청|군청|구청"
    r"))(?:\s|$)"
)


@dataclass(frozen=True)
class AddressCandidate:
    address: str
    lat: float | None
    lon: float | None
    address_source: str
    coordinate_source: str | None
    confidence: int
    verified: bool
    query: str
    matched_name: str = ""
    region_sido: str = ""
    region_sigungu: str = ""


@dataclass(frozen=True)
class Resolution:
    branch: dict[str, Any]
    candidate: AddressCandidate
    method: str


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", clean_text(value)).casefold()


def is_usable_address(value: Any) -> bool:
    text = clean_text(value)
    if not text or text.casefold() in {"대한민국", "korea", "south korea"}:
        return False
    return bool(
        len(text) >= 7
        and (
            re.search(r"(?:대로|로|길)\s*\d+(?:-\d+)?", text)
            or re.search(r"(?:읍|면|동|리)\s+(?:산\s*)?\d+(?:-\d+)?", text)
        )
    )


def normalize_stored_address(value: Any) -> str:
    text = clean_text(value)
    text = re.sub(r"^(?:대한민국|South Korea)\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\s*/\s*(?:전화\s*)?0\d{1,2}-\d{3,4}-\d{4}\s*$",
        "",
        text,
    )
    text = re.sub(
        r"\s+(?:전화|문의)\s*[:：]?\s*0\d{1,2}-\d{3,4}-\d{4}\s*$",
        "",
        text,
    )
    return text


def text_conflicts_locality(value: Any, locality: Any) -> bool:
    locality_parts = clean_text(locality).split()
    if not locality_parts:
        return False
    target_area = locality_parts[0]
    allowed = {
        compact(alias)
        for alias in ADMIN_AREA_ALIASES.get(target_area, (target_area,))
        if len(compact(alias)) >= 2
    }
    prefix = compact(clean_text(value)[:40])
    for area, aliases in ADMIN_AREA_ALIASES.items():
        if area == target_area:
            continue
        for alias in aliases:
            alias_key = compact(alias)
            if len(alias_key) >= 2 and prefix.startswith(alias_key) and alias_key not in allowed:
                return True
    return False


def is_non_physical_name(value: Any) -> bool:
    normalized = compact(value)
    return bool(normalized and any(token in normalized for token in NON_PHYSICAL_TOKENS))


def has_multiple_venues(value: Any) -> bool:
    text = clean_text(value)
    comma_separates_venues = False
    if "," in text:
        parts = [clean_text(part).strip("()") for part in text.split(",")]
        stems = {
            compact(facility_stem("", part))
            for part in parts
            if compact(facility_stem("", part))
        }
        trailing_parts_are_address_details = all(
            not part
            or bool(
                re.fullmatch(
                    (
                        r"(?:\S+(?:빌딩|건물|상가)\s*)?"
                        r"(?:(?:지하|b)\s*)?\d+\s*층(?:\s+.*)?"
                    ),
                    part,
                    flags=re.IGNORECASE,
                )
            )
            for part in parts[1:]
        )
        trailing_parts_are_room_details = all(
            not part
            or bool(ROOM_ONLY_PATTERN.fullmatch(part))
            or bool(
                re.fullmatch(
                    r"(?:(?:지하|b)\s*)?\d+\s*층(?:\s+.*)?",
                    part,
                    flags=re.IGNORECASE,
                )
            )
            for part in parts[1:]
        )
        address_with_trailing_details = bool(
            ROAD_ADDRESS_PATTERN.search(text)
            and trailing_parts_are_address_details
        )
        comma_separates_venues = not address_with_trailing_details and (
            len(stems) > 1
            or (
                not ROAD_ADDRESS_PATTERN.search(text)
                and not trailing_parts_are_room_details
            )
        )
    return bool(
        comma_separates_venues
        or "/" in text
        or " 및 " in text
        or " 또는 " in text
        or " 혹은 " in text
        or " 외 " in text
        or re.search(r"\S+\s*[·ㆍ]\s*\S+", text)
    )


def strip_locality_prefix(value: Any, locality: Any = "") -> str:
    text = clean_text(value)
    match = re.fullmatch(r"(.+?)\s*(?:·|/)\s*(.+)", text)
    if not match:
        return text
    prefix = clean_text(match.group(1))
    venue = clean_text(match.group(2))
    prefix_key = compact(prefix)
    locality_key = compact(locality)
    looks_like_locality = bool(
        re.fullmatch(
            r"(?:[가-힣]+(?:특별시|광역시|특별자치시|특별자치도|도)\s*)?"
            r"[가-힣]+(?:시|군|구)",
            prefix,
        )
    )
    if (
        looks_like_locality
        or (
            prefix_key
            and locality_key
            and (prefix_key in locality_key or locality_key in prefix_key)
        )
    ):
        return venue
    return text


def embedded_road_address(value: Any) -> str:
    matches = [
        clean_text(match.group(1))
        for match in ROAD_ADDRESS_PATTERN.finditer(clean_text(value))
    ]
    usable = [address for address in matches if is_usable_address(address)]
    candidates = usable or matches
    return max(candidates, key=lambda address: len(compact(address))) if candidates else ""


def road_address_key(value: Any) -> str:
    address = embedded_road_address(value)
    return compact(address)


def road_address_geocode_key(value: Any) -> str:
    """Return the street-and-building-number portion used to verify geocoding."""
    address = embedded_road_address(value)
    match = re.search(
        r"([가-힣A-Za-z0-9·.-]+(?:대로|로|길)\s*\d+"
        r"(?:(?:번길)\s*\d+(?:-\d+)?|-\d+)?)",
        address,
    )
    return compact(match.group(1)) if match else ""


def unique_address(values: Iterable[Any], locality: Any = "") -> str:
    groups: dict[str, list[str]] = defaultdict(list)
    for value in values:
        address = normalize_stored_address(value)
        if not is_usable_address(address):
            continue
        if locality and not address_matches_locality(address, locality):
            address_localities = locality_tokens(address)
            required_localities = locality_tokens(locality)
            if address_localities:
                required_aliases = (
                    LOCALITY_TOKEN_ALIASES.get(
                        required_localities[-1],
                        (required_localities[-1],),
                    )
                    if required_localities
                    else ()
                )
                address_key = compact(address)
                if not any(
                    compact(alias) in address_key for alias in required_aliases
                ):
                    continue
                locality_parts = clean_text(locality).split()
                prefix_parts = locality_parts[:-1] or locality_parts[:1]
                address = normalize_stored_address(
                    f"{' '.join(prefix_parts)} {address}"
                )
                if not address_matches_locality(address, locality):
                    continue
                key = compact(address)
                if key:
                    groups[key].append(address)
                continue
            prefix = compact(address[:20])
            locality_prefix = compact(clean_text(locality).split()[0] if clean_text(locality) else "")
            conflicting_prefix = any(
                compact(alias) in prefix
                for area, aliases in ADMIN_AREA_ALIASES.items()
                if compact(area) != locality_prefix
                for alias in aliases
                if len(compact(alias)) >= 2
            )
            if conflicting_prefix:
                continue
            address = normalize_stored_address(f"{clean_text(locality)} {address}")
            if not address_matches_locality(address, locality):
                continue
        key = compact(address)
        if key:
            groups[key].append(address)
    if len(groups) != 1:
        return ""
    return max(next(iter(groups.values())), key=len)


def embedded_course_address(branch: dict[str, Any], locality: Any = "") -> str:
    normalized_name = strip_locality_prefix(branch.get("name"), locality)
    admin_name = administrative_center_search_name(normalized_name)
    if is_non_physical_name(normalized_name):
        return ""
    if is_generic_branch_name(normalized_name) and not admin_name:
        return ""

    venue_names = [
        clean_text(value)
        for value in branch.get("course_venue_names") or []
        if clean_text(value) and not is_non_physical_name(value)
    ]
    source_texts = [
        *venue_names,
        *[
            clean_text(value)
            for value in branch.get("course_raw_address_texts") or []
            if clean_text(value)
        ],
    ]
    addresses = []
    address_texts = []
    for value in source_texts:
        if text_conflicts_locality(value, locality):
            continue
        found = False
        addresses.extend(
            clean_text(match.group(1))
            for match in ROAD_ADDRESS_PATTERN.finditer(clean_text(value))
        )
        found = bool(ROAD_ADDRESS_PATTERN.search(clean_text(value)))
        if found:
            address_texts.append(value)
    address = unique_address(addresses, locality)
    if not address:
        return ""
    if len(set(venue_names)) <= 1:
        return address

    stem = searchable_facility_stem(branch, locality)
    stem_key = canonical_facility_name(stem)
    admin_unit = administrative_center_unit(admin_name)
    for value in address_texts:
        value_key = canonical_facility_name(value)
        if stem_key and len(stem_key) >= 4 and stem_key in value_key:
            return address
        if admin_unit and admin_unit in value_key:
            return address
    return ""


def search_query_variants(locality: Any, query_name: Any) -> tuple[str, ...]:
    locality_text = clean_text(locality)
    name_text = clean_text(query_name)
    stripped_name = name_text
    prefix_candidates = []
    locality_parts = locality_text.split()
    if locality_parts:
        prefix_candidates.extend(
            ADMIN_AREA_ALIASES.get(locality_parts[0], (locality_parts[0],))
        )
    for token in locality_tokens(locality_text):
        prefix_candidates.extend(LOCALITY_TOKEN_ALIASES.get(token, (token,)))
    for prefix in sorted(
        {
            re.sub(r"(?:특별시|광역시|특별자치시|특별자치도|시|군|구)$", "", clean_text(value))
            for value in prefix_candidates
            if clean_text(value)
        },
        key=len,
        reverse=True,
    ):
        if len(prefix) < 2:
            continue
        candidate = re.sub(
            rf"^{re.escape(prefix)}(?:특별시|광역시|특별자치시|특별자치도|시|군|구)?(?:립)?\s*",
            "",
            stripped_name,
        )
        if candidate != stripped_name:
            stripped_name = clean_text(candidate)
            break

    candidates = [
        clean_text(f"{locality_text} {name_text}"),
    ]
    if len(locality_parts) >= 2:
        candidates.append(clean_text(f"{locality_parts[-1]} {name_text}"))
    candidates.append(name_text)
    if stripped_name and stripped_name != name_text:
        candidates.append(clean_text(f"{locality_text} {stripped_name}"))
        if len(locality_parts) >= 2:
            candidates.append(clean_text(f"{locality_parts[-1]} {stripped_name}"))
        candidates.append(stripped_name)

    for source, target in SEARCH_TEXT_REPLACEMENTS:
        for value in tuple(candidates):
            replaced = value.replace(source, target)
            if replaced != value:
                candidates.append(clean_text(replaced))

    return tuple(dict.fromkeys(value for value in candidates if value))


def facility_stem(provider: Any, value: Any) -> str:
    provider_text = clean_text(provider).upper()
    text = clean_text(value)
    if not text or is_non_physical_name(text):
        return ""
    text = re.sub(
        r"\(\s*(본원|본관|신관|동부|서부|남부|북부|제\d+(?:관|센터)?)\s*\)",
        r" \1 ",
        text,
    )
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,/-")
    if provider_text in RETAIL_PROVIDERS:
        return text
    if re.match(r"^(?:(?:지하|b)\s*)?\d+\s*층\b", text, flags=re.IGNORECASE):
        return ""

    matches: list[tuple[int, int]] = []
    for suffix in INSTITUTION_SUFFIXES:
        for match in re.finditer(re.escape(suffix), text):
            if suffix in {"시청", "구청", "군청"}:
                following = text[match.end() : match.end() + 2]
                if following.startswith(("소년", "각")):
                    continue
            matches.append((match.end(), len(suffix)))
    if matches:
        end, _suffix_length = max(matches, key=lambda item: (item[0], item[1]))
        stem = text[:end].strip(" ,/-")
        if len(compact(stem)) >= 3:
            return stem

    trimmed = re.sub(
        r"\s+(?:(?:지하|b)?\s*\d+\s*층|\d+\s*호|제?\d+\s*(?:강의실|실|관|홀)|"
        r"강의실|강연실|교육실|교실|다목적실|회의실|프로그램실|강당).*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    if trimmed != text and len(compact(trimmed)) >= 4:
        return trimmed
    if ROOM_ONLY_PATTERN.fullmatch(text):
        return ""
    return text if len(compact(text)) >= 4 else ""


def locality_tokens(value: Any) -> tuple[str, ...]:
    tokens = []
    for token in clean_text(value).split():
        token = token.strip("(),")
        if token in PROVINCE_LEVEL_NAMES:
            continue
        if token.endswith(("시", "군", "구")) and len(token) >= 2:
            tokens.append(token)
    return tuple(tokens[-2:])


def locality_label(value: Any) -> str:
    text = clean_text(value)
    parts = text.split()
    if not parts or len(parts) > 3:
        return ""
    if parts[0] in PROVINCE_LEVEL_NAMES:
        return text if len(parts) >= 2 and parts[-1].endswith(("시", "군", "구")) else ""
    return text if len(parts) == 1 and parts[0].endswith(("시", "군", "구")) else ""


def address_matches_locality(address: Any, locality: Any) -> bool:
    locality_parts = clean_text(locality).split()
    normalized = compact(address)
    if locality_parts:
        aliases = ADMIN_AREA_ALIASES.get(locality_parts[0])
        if aliases and not any(compact(alias) in normalized for alias in aliases):
            return False
    required = locality_tokens(locality)
    if not required:
        return True
    aliases = LOCALITY_TOKEN_ALIASES.get(required[-1], (required[-1],))
    return any(compact(alias) in normalized for alias in aliases)


def is_generic_branch_name(value: Any) -> bool:
    text = clean_text(value)
    if not text:
        return True
    if GENERIC_BRANCH_PATTERN.search(text):
        return True
    if any(
        token in text
        for token in (
            "교육포털",
            "예약시스템",
            "통합예약",
            "평생교육포털",
            "평생학습포털",
        )
    ):
        return True
    if text.endswith(("팀", "사업본부")):
        return True
    if text.endswith("과"):
        stem = facility_stem("", text)
        if not stem or compact(stem) == compact(text):
            return True
    if re.fullmatch(r"[0-9A-Za-z가-힣]+(?:시|군|구|읍|면|동)", text):
        return True
    parts = text.split()
    return bool(
        len(parts) <= 3
        and parts
        and parts[0] in PROVINCE_LEVEL_NAMES
        and parts[-1].endswith(("시", "군", "구"))
    )


def is_ambiguous_facility_name(value: Any) -> bool:
    text = canonical_facility_name(value)
    for suffix in ("주민센터", "도서관", "문화센터", "복지관", "체육관", "교육장"):
        if not text.endswith(suffix):
            continue
        distinctive = text[: -len(suffix)]
        return len(distinctive) < 2 or distinctive in {"동", "시", "군", "구"}
    return False


def canonical_facility_name(value: Any) -> str:
    text = compact(value)
    replacements = (
        ("전남광주통합특별시교육청", ""),
        ("전라남도교육청", ""),
        ("전남교육청", ""),
        ("특별자치시", ""),
        ("특별자치도", ""),
        ("광역시", ""),
        ("특례시", "시"),
        ("평생학습원", "평생학습관"),
        ("평생교육센터", "평생학습센터"),
        ("문화체육센터", "체육관"),
        ("행정복지센터", "주민센터"),
        ("주민자치센터", "주민센터"),
        ("주민자치회", "주민센터"),
        ("자치회관", "주민센터"),
        ("기적의도서관", "기적도서관"),
        ("작은도서관", "도서관"),
        ("제1동", "1동"),
        ("제2동", "2동"),
        ("제3동", "3동"),
        ("제4동", "4동"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    text = re.sub(r"(?<=[가-힣]{2})(?:읍|면)(?=주민센터)", "", text)
    text = re.sub(
        r"(?<=[가-힣]{2})(?:시|군|구)(?=(?:청소년|노인|여성|육아|농업|보건|"
        r"평생|문화|체육|종합|사회|도서|미술|박물|과학))",
        "",
        text,
    )
    return text.replace("시립", "").replace("군립", "").replace("구립", "")


def _numbers_are_consistent(left: str, right: str) -> bool:
    left_numbers = re.findall(r"\d+", left)
    right_numbers = re.findall(r"\d+", right)
    return not (left_numbers or right_numbers) or left_numbers == right_numbers


def names_overlap(left: Any, right: Any) -> bool:
    left_key = canonical_facility_name(left)
    right_key = canonical_facility_name(right)
    if min(len(left_key), len(right_key)) < 3:
        return False
    if not _numbers_are_consistent(left_key, right_key):
        return False
    if left_key == right_key:
        return True
    if left_key in right_key or right_key in left_key:
        shorter_key, longer_key = (
            (left_key, right_key)
            if len(left_key) <= len(right_key)
            else (right_key, left_key)
        )
        if len(shorter_key) < 4 or not longer_key.startswith(shorter_key):
            return False
        detail = longer_key[len(shorter_key) :]
        return bool(
            re.fullmatch(
                r"(?:(?:본관|신관|별관|분관|강당|교육장|체육관|다목적체육관|"
                r"회의실|강의실|프로그램실|주차장|전기차충전소|도서관|"
                r"문화학교)\d*)+",
                detail,
            )
        )
    return False


def names_overlap_in_locality(left: Any, right: Any, locality: Any) -> bool:
    if names_overlap(left, right):
        return True
    left_key = canonical_facility_name(left)
    right_key = canonical_facility_name(right)
    prefixes = set()
    locality_parts = clean_text(locality).split()
    if locality_parts:
        for alias in ADMIN_AREA_ALIASES.get(locality_parts[0], (locality_parts[0],)):
            alias_key = canonical_facility_name(alias)
            if len(alias_key) >= 2:
                prefixes.add(alias_key)
    for token in locality_tokens(locality):
        for alias in LOCALITY_TOKEN_ALIASES.get(token, (token,)):
            alias_key = canonical_facility_name(alias)
            if len(alias_key) >= 2:
                prefixes.add(alias_key)
            short_key = re.sub(r"(?:시|군|구)$", "", alias_key)
            if len(short_key) >= 2:
                prefixes.add(short_key)
    for prefix in sorted(prefixes, key=len, reverse=True):
        if right_key.startswith(prefix) and names_overlap(left_key, right_key[len(prefix) :]):
            return True
        if left_key.startswith(prefix) and names_overlap(left_key[len(prefix) :], right_key):
            return True
    shorter_key, longer_key = (
        (left_key, right_key)
        if len(left_key) <= len(right_key)
        else (right_key, left_key)
    )
    if longer_key.endswith(shorter_key):
        wrapper = longer_key[: -len(shorter_key)]
        if re.search(
            r"(?:시청소년재단|청소년재단|평생교육원|평생학습원|무더위쉼터)$",
            wrapper,
        ):
            return True
    return False


def administrative_center_unit(value: Any) -> str:
    key = canonical_facility_name(value)
    match = re.search(r"([가-힣0-9·,.]+(?:동|읍|면))주민센터", key)
    return match.group(1) if match else ""


def retail_branch_key(provider: Any, value: Any) -> str:
    provider_text = clean_text(provider).upper()
    text = compact(re.sub(r"\([^)]*\)", "", clean_text(value)))
    for token in RETAIL_BRAND_ALIASES.get(provider_text, ()):
        text = text.replace(compact(token), "")
    for token in (
        "롯데문화센터",
        "문화센터",
        "아카데미",
        "백화점",
        "아울렛",
        "쇼핑몰",
        "마트맥스",
        "마트",
        "패션",
    ):
        text = text.replace(compact(token), "")
    text = text.replace("타임빌라스", "")
    text = text.replace("artscience", "")
    text = re.sub(r"(?:신관|본관)$", "", text)
    text = re.sub(r"(?:점|점포)$", "", text)
    text = re.sub(r"(?:&?on)$", "", text, flags=re.IGNORECASE)
    return text


def retail_names_overlap(provider: Any, target: Any, candidate: Any) -> bool:
    provider_text = clean_text(provider).upper()
    candidate_key = compact(candidate)
    if provider_text == "LOTTE" and any(
        token in candidate_key for token in ("롯데마트", "롯데슈퍼")
    ):
        return False
    if provider_text == "LOTTE_MART" and not any(
        token in candidate_key for token in ("롯데마트", "마트맥스", "maxx")
    ):
        return False
    if not any(
        compact(alias) in candidate_key
        for alias in RETAIL_BRAND_ALIASES.get(provider_text, ())
    ):
        return False
    target_branch = retail_branch_key(provider_text, target)
    candidate_branch = retail_branch_key(provider_text, candidate)
    if not target_branch or not candidate_branch:
        return False
    if not _numbers_are_consistent(target_branch, candidate_branch):
        return False
    return target_branch == candidate_branch


def kakao_query_name(provider: Any, stem: Any) -> str:
    provider_text = clean_text(provider).upper()
    stem_text = clean_text(stem)
    if provider_text == "LOTTE":
        branch = retail_branch_key(provider_text, stem_text)
        return clean_text(f"롯데백화점 {branch}점")
    if provider_text == "ELAND_RETAIL":
        return clean_text(f"뉴코아 NC백화점 {stem_text}")
    if provider_text == "SHINSEGAE_ACADEMY":
        stem_text = re.sub(r"\s*&\s*ON\s*$", "", stem_text, flags=re.IGNORECASE)
    prefix = RETAIL_QUERY_PREFIXES.get(provider_text)
    if not prefix:
        return stem_text
    if any(compact(token) in compact(stem_text) for token in prefix.split()):
        return stem_text
    return clean_text(f"{prefix} {stem_text}")


def google_query_name(provider: Any, stem: Any) -> str:
    """Deprecated compatibility alias for callers that built place query names."""
    return kakao_query_name(provider, stem)


def place_candidate_score(
    target_name: Any,
    locality: Any,
    candidate_name: Any,
    address: Any,
    types: Iterable[str] = (),
    provider: Any = "",
) -> int:
    if not is_usable_address(address) or not address_matches_locality(address, locality):
        return 0
    target_key = compact(target_name)
    candidate_key = compact(candidate_name)
    if not target_key or not candidate_key:
        return 0

    score = 35
    provider_text = clean_text(provider).upper()
    if provider_text in RETAIL_PROVIDERS:
        if not retail_names_overlap(provider_text, target_name, candidate_name):
            return 0
        score += 37
    else:
        target_admin_unit = administrative_center_unit(target_name)
        candidate_admin_unit = administrative_center_unit(candidate_name)
        if (
            target_admin_unit
            and candidate_admin_unit
            and target_admin_unit != candidate_admin_unit
        ):
            return 0
    if provider_text not in RETAIL_PROVIDERS and target_key == candidate_key:
        score += 40
    elif provider_text not in RETAIL_PROVIDERS and names_overlap_in_locality(
        target_name,
        candidate_name,
        locality,
    ):
        score += 32
    elif provider_text not in RETAIL_PROVIDERS:
        return 0
    if locality_tokens(locality):
        score += 15
    if {"establishment", "point_of_interest", "library", "local_government_office"} & set(types):
        score += 10
    if provider_text in RETAIL_PROVIDERS:
        score += 10
    return min(score, 100)


def source_priority(row: dict[str, Any]) -> tuple[int, int, int, int, str]:
    return (
        int(bool(row.get("location_verified"))),
        int(row.get("location_confidence") or 0),
        int(row.get("lat") is not None and row.get("lon") is not None),
        int(row.get("provider") == "CULTURE_FACILITY"),
        clean_text(row.get("id")),
    )


def choose_unique_source(
    sources: Iterable[dict[str, Any]],
    locality: Any,
) -> dict[str, Any] | None:
    candidates = [
        row
        for row in sources
        if is_usable_address(row.get("address"))
        and address_matches_locality(row.get("address"), locality)
    ]
    if not candidates:
        return None
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        key = compact(normalize_stored_address(row.get("address")))
        if key:
            groups[key].append(row)
    if len(groups) != 1:
        return None
    return max(next(iter(groups.values())), key=source_priority)


def load_provider_localities() -> dict[str, str]:
    values: dict[str, set[str]] = defaultdict(set)
    target_dir = ROOT / "config" / "crawl_targets"
    for path in sorted(target_dir.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for row in document.get("targets") or []:
            if not isinstance(row, dict):
                continue
            provider = clean_text(row.get("provider")).upper()
            locality = clean_text(row.get("municipality_full_name"))
            if not locality:
                locality = locality_label(row.get("branch"))
            if not locality:
                locality = clean_text(row.get("region"))
            if provider and locality and locality != "전국":
                values[provider].add(locality)

    try:
        from tools.report_scope_region_coverage import (
            load_municipality_index,
            load_provider_municipalities,
        )

        municipality_index = load_municipality_index()
        for provider, mapped in load_provider_municipalities(municipality_index).items():
            values[provider].update(mapped)
    except (OSError, RuntimeError, ValueError, yaml.YAMLError):
        pass

    result: dict[str, str] = {}
    for provider, localities in values.items():
        if len(localities) == 1:
            result[provider] = next(iter(localities))
            continue
        parents = [
            locality
            for locality in localities
            if all(locality == candidate or candidate.startswith(f"{locality} ") for candidate in localities)
        ]
        if parents:
            result[provider] = max(parents, key=len)
            continue
        split_values = [locality.split() for locality in localities]
        common: list[str] = []
        for parts in zip(*split_values):
            if len(set(parts)) != 1:
                break
            common.append(parts[0])
        if common:
            result[provider] = " ".join(common)
    return result


def load_provider_target_names() -> dict[str, tuple[str, ...]]:
    values: dict[str, set[str]] = defaultdict(set)
    target_dir = ROOT / "config" / "crawl_targets"
    for path in sorted(target_dir.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for row in document.get("targets") or []:
            if not isinstance(row, dict):
                continue
            provider = clean_text(row.get("provider")).upper()
            name = clean_text(row.get("name"))
            if provider and name:
                values[provider].add(name)
    return {
        provider: tuple(sorted(names, key=lambda value: (len(value), value)))
        for provider, names in values.items()
    }


def administrative_operator_name(locality: Any) -> str:
    parts = clean_text(locality).split()
    if not parts:
        return ""
    for token in reversed(parts):
        if token.endswith(("시", "군", "구")) and len(token) >= 2:
            return f"{token}청"
    province = parts[0]
    if province in PROVINCE_LEVEL_NAMES:
        return f"{province}청"
    return ""


def broader_municipality_locality(locality: Any) -> str:
    parts = clean_text(locality).split()
    if len(parts) >= 3 and parts[-1].endswith("구"):
        for index in range(len(parts) - 2, -1, -1):
            if parts[index].endswith("시"):
                return " ".join(parts[: index + 1])
    return clean_text(locality)


def provider_operator_search_name(
    branch: dict[str, Any],
    locality: Any,
    provider_target_names: dict[str, tuple[str, ...]],
) -> str:
    provider = clean_text(branch.get("provider")).upper()
    locality_key = compact(locality)
    branch_key = compact(branch.get("name"))
    candidates: list[tuple[int, int, str]] = []
    for configured_name in provider_target_names.get(provider, ()):
        name = TARGET_NAME_TRAILING_NOISE_RE.sub("", configured_name).strip()
        if not name or len(name) > 60:
            continue
        name_key = compact(name)
        if not name_key or name_key in {locality_key, branch_key}:
            continue
        match = PHYSICAL_OPERATOR_NAME_RE.search(name)
        if not match:
            continue
        physical_name = clean_text(match.group(1))
        score = 100
        if physical_name.endswith(("시청", "군청", "구청")):
            score = 90
        if "도서관" in physical_name or "평생" in physical_name:
            score += 5
        candidates.append((score, -len(physical_name), physical_name))
    if candidates:
        return max(candidates)[2]
    return administrative_operator_name(locality)


def branch_locality(branch: dict[str, Any], provider_localities: dict[str, str]) -> str:
    values = {
        clean_text(value)
        for value in branch.get("course_localities") or []
        if clean_text(value)
    }
    provider_locality = provider_localities.get(
        clean_text(branch.get("provider")).upper(),
        "",
    )
    stored_locality = clean_text(
        " ".join(
            value
            for value in (
                clean_text(branch.get("region_sido")),
                clean_text(branch.get("region_sigungu")),
            )
            if value
        )
    )
    if stored_locality:
        comparable = [
            value
            for value in values
            if (
                stored_locality == value
                or stored_locality.startswith(f"{value} ")
                or value.startswith(f"{stored_locality} ")
            )
        ]
        if len(comparable) == len(values):
            return max((stored_locality, *values), key=lambda value: len(value.split()))
        values.add(stored_locality)
    if len(values) == 1:
        course_locality = next(iter(values))
        if (
            provider_locality
            and len(provider_locality.split()) > len(course_locality.split())
            and provider_locality.startswith(course_locality)
        ):
            return provider_locality
        return course_locality
    if values:
        split_values = [value.split() for value in values]
        common: list[str] = []
        for parts in zip(*split_values):
            if len(set(parts)) != 1:
                break
            common.append(parts[0])
        if common:
            return " ".join(common)
    if provider_locality:
        return provider_locality
    branch_name_locality = locality_label(branch.get("name"))
    if branch_name_locality:
        return branch_name_locality
    return locality_label(
        normalize_stored_address(branch.get("address"))
    )


def fetch_missing_branches(
    provider: str | None,
    active_only: bool,
    limit: int | None,
    repair_invalid_crawler_addresses: bool = False,
    repair_invalid_addresses: bool = False,
) -> list[dict[str, Any]]:
    provider_filter = "AND b.provider = %(provider)s" if provider else ""
    active_filter = """
      AND EXISTS (
            SELECT 1 FROM courses active_course
            WHERE active_course.branch_id = b.id
              AND active_course.is_active IS TRUE
      )
    """ if active_only else ""
    address_filter = (
        "TRUE"
        if repair_invalid_addresses
        else (
        """
        (
            NULLIF(btrim(b.address), '') IS NULL
            OR (
                b.address_source = 'crawler'
                AND COALESCE(b.location_verified, FALSE) IS FALSE
            )
        )
        """
        if repair_invalid_crawler_addresses
        else "NULLIF(btrim(b.address), '') IS NULL"
        )
    )
    limit_sql = (
        "LIMIT %(limit)s"
        if limit and not (
            repair_invalid_crawler_addresses or repair_invalid_addresses
        )
        else ""
    )
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT b.id, b.provider, b.branch_code, b.name, b.address,
                   b.lat, b.lon, b.location_confidence, b.location_verified,
                   b.website_url, b.region_sido, b.region_sigungu,
                   COUNT(c.id) FILTER (WHERE c.is_active IS TRUE) AS active_courses,
                   ARRAY_REMOVE(
                        ARRAY_AGG(DISTINCT NULLIF(btrim(c.venue_address), '')),
                        NULL
                   ) AS course_addresses,
                   ARRAY_REMOVE(
                        ARRAY_AGG(
                            DISTINCT NULLIF(
                                btrim(
                                    COALESCE(
                                        c.raw_fields ->> 'branch_code',
                                        substring(
                                            c.raw_url
                                            from 'srchRsSysId=([^&]+)'
                                        )
                                    )
                                ),
                                ''
                            )
                        ),
                        NULL
                   ) AS course_branch_codes,
                   ARRAY_REMOVE(
                        ARRAY_AGG(
                            DISTINCT NULLIF(
                                btrim(
                                    COALESCE(
                                        c.raw_fields ->> 'municipality_full_name',
                                        c.raw_fields ->> 'municipality_name',
                                        c.raw_fields ->> 'region'
                                    )
                                ),
                                ''
                            )
                        ),
                        NULL
                   ) AS course_localities,
                    ARRAY_REMOVE(
                         ARRAY_AGG(DISTINCT NULLIF(btrim(c.venue_name), '')),
                         NULL
                    ) AS course_venue_names,
                    ARRAY_REMOVE(
                         ARRAY_AGG(
                             DISTINCT NULLIF(
                                 btrim(
                                     COALESCE(
                                         c.raw_fields #>> '{{raw_fields,institution_pairs}}',
                                         c.raw_fields #>> '{{raw_fields,address}}',
                                         c.raw_fields ->> 'venue_address',
                                         c.raw_fields ->> 'address'
                                     )
                                 ),
                                 ''
                             )
                         ),
                         NULL
                    ) AS course_raw_address_texts
            FROM branches b
            LEFT JOIN courses c ON c.branch_id = b.id
            WHERE {address_filter}
              {provider_filter}
              {active_filter}
            GROUP BY b.id
            ORDER BY
                COUNT(c.id) FILTER (WHERE c.is_active IS TRUE) DESC,
                b.provider,
                b.name
            {limit_sql}
            """,
            {"provider": provider, "limit": limit},
        )
        rows = [dict(row) for row in cursor.fetchall()]
    if repair_invalid_crawler_addresses or repair_invalid_addresses:
        rows = [
            row
            for row in rows
            if not clean_text(row.get("address"))
            or not is_usable_address(row.get("address"))
        ]
        if limit:
            rows = rows[:limit]
    return rows


def fetch_address_sources() -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, provider, branch_code, name, address, lat, lon,
                   address_source, coordinate_source,
                   location_confidence, location_verified
            FROM branches
            WHERE NULLIF(btrim(address), '') IS NOT NULL
              AND (
                    provider = 'CULTURE_FACILITY'
                    OR (
                        location_verified IS TRUE
                        AND location_confidence >= 82
                    )
              )
            ORDER BY provider, name
            """
        )
        return [dict(row) for row in cursor.fetchall()]


def build_source_indexes(
    sources: Iterable[dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_stem: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_locality: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sources:
        name_key = compact(row.get("name"))
        if name_key:
            by_name[name_key].append(row)
        stem_key = compact(facility_stem(row.get("provider"), row.get("name")))
        if stem_key:
            by_stem[stem_key].append(row)
        for token in locality_tokens(row.get("address")):
            by_locality[compact(token)].append(row)
    return by_name, by_stem, by_locality


def source_candidate(source: dict[str, Any], method: str) -> AddressCandidate:
    has_coordinates = source.get("lat") is not None and source.get("lon") is not None
    source_id = clean_text(source.get("id"))
    return AddressCandidate(
        address=normalize_stored_address(source.get("address")),
        lat=float(source["lat"]) if has_coordinates else None,
        lon=float(source["lon"]) if has_coordinates else None,
        address_source=f"{method}:{source_id}",
        coordinate_source=f"{method}:{source_id}" if has_coordinates else None,
        confidence=max(75, min(95, int(source.get("location_confidence") or 80))),
        verified=bool(source.get("location_verified")),
        query=f"{method} {clean_text(source.get('provider'))} {clean_text(source.get('name'))}",
        matched_name=clean_text(source.get("name")),
    )


def curated_branch_resolution(
    branch: dict[str, Any],
    locality: str,
) -> Resolution | None:
    provider = clean_text(branch.get("provider")).upper()
    search_texts: list[str] = []
    for value in (
        branch.get("name"),
        branch.get("_repair_search_name"),
        branch.get("address"),
    ):
        text = clean_text(value)
        if text and text not in search_texts:
            search_texts.append(text)
        normalized = strip_locality_prefix(text, locality)
        if normalized and normalized not in search_texts:
            search_texts.append(normalized)

    search_keys: set[str] = set()
    for text in search_texts:
        search_keys.add(compact(text))
        stem = facility_stem(provider, text)
        if stem:
            search_keys.add(compact(stem))
    search_keys.discard("")

    matched_name = ""
    matched_method = ""
    matched_location: dict[str, Any] | None = None
    for (curated_provider, curated_name), location in CURATED_BRANCH_LOCATIONS.items():
        if provider == curated_provider and compact(curated_name) in search_keys:
            matched_name = curated_name
            matched_method = "curated_location"
            matched_location = location
            break

    if not matched_location:
        for location in CURATED_BRANCH_PATTERN_LOCATIONS:
            if provider != clean_text(location.get("provider")).upper():
                continue
            pattern = clean_text(location.get("pattern"))
            matched_text = next(
                (
                    text
                    for text in search_texts
                    if pattern and re.search(pattern, text, flags=re.IGNORECASE)
                ),
                "",
            )
            if matched_text:
                matched_name = matched_text
                matched_method = "curated_pattern_location"
                matched_location = location
                break

    if not matched_location:
        return None
    address = clean_text(matched_location.get("address"))
    if not is_usable_address(address):
        return None
    if matched_location.get("skip_course_address_backfill"):
        branch["_skip_course_address_backfill"] = True
    return Resolution(
        branch,
        AddressCandidate(
            address=address,
            lat=float(matched_location["lat"]),
            lon=float(matched_location["lon"]),
            address_source="CURATED_OFFICIAL_LOCATION",
            coordinate_source=clean_text(
                matched_location.get("coordinate_source")
            )
            or "NAVER_LOCAL_SEARCH_VERIFIED",
            confidence=100,
            verified=True,
            query=clean_text(matched_location.get("source_url"))
            or f"curated {provider} {matched_name}",
            matched_name=matched_name,
            region_sido=clean_text(matched_location.get("region_sido")),
            region_sigungu=clean_text(matched_location.get("region_sigungu")),
        ),
        matched_method,
    )


def deterministic_resolution(
    branch: dict[str, Any],
    locality: str,
    by_name: dict[str, list[dict[str, Any]]],
    by_stem: dict[str, list[dict[str, Any]]],
    by_locality: dict[str, list[dict[str, Any]]],
) -> Resolution | None:
    normalized_name = strip_locality_prefix(branch.get("name"), locality)
    generic_branch = is_generic_branch_name(normalized_name)
    official_location = official_provider_branch_location(branch)
    if official_location:
        return Resolution(
            branch,
            AddressCandidate(
                address=clean_text(official_location["address"]),
                lat=float(official_location["lat"]),
                lon=float(official_location["lon"]),
                address_source="OFFICIAL_INSTITUTION_LOCATION",
                coordinate_source="NAVER_LOCAL_SEARCH_BY_OFFICIAL_ADDRESS",
                confidence=100,
                verified=True,
                query=clean_text(official_location.get("source_url")),
                matched_name=clean_text(branch.get("name")),
                region_sido="부산광역시",
                region_sigungu="",
            ),
            "official_provider_branch_location",
        )
    curated_resolution = curated_branch_resolution(branch, locality)
    if curated_resolution:
        return curated_resolution
    if has_multiple_venues(normalized_name):
        return None
    course_address = unique_address(branch.get("course_addresses") or [], locality)
    embedded_course_value = embedded_road_address(course_address)
    if (
        course_address
        and embedded_course_value
        and compact(course_address).endswith(compact(branch.get("name")))
        and compact(embedded_course_value) != compact(course_address)
    ):
        # Some collectors previously copied the complete venue label into the
        # address field. Let the external address resolver expand the embedded
        # road fragment instead of persisting that label as an address.
        course_address = ""
    admin_name = administrative_center_search_name(normalized_name)
    if course_address and (not generic_branch or admin_name):
        return Resolution(
            branch,
            AddressCandidate(
                address=course_address,
                lat=None,
                lon=None,
                address_source="COURSE_VENUE_ADDRESS",
                coordinate_source=None,
                confidence=90,
                verified=True,
                query="unique course.venue_address",
            ),
            "course_venue_address",
        )

    if not generic_branch:
        name_key = compact(normalized_name)
        source = choose_unique_source(by_name.get(name_key, []), locality)
        if source:
            return Resolution(
                branch,
                source_candidate(source, "EXACT_BRANCH_NAME"),
                "exact_branch_name",
            )

    stem = facility_stem(branch.get("provider"), normalized_name)
    stem_key = compact(stem)
    if (
        not generic_branch
        and stem_key
        and stem_key not in {compact(value) for value in GENERIC_FACILITY_STEMS}
    ):
        source = choose_unique_source(by_stem.get(stem_key, []), locality)
        if source:
            return Resolution(
                branch,
                source_candidate(source, "FACILITY_STEM_MATCH"),
                "facility_stem_match",
            )
        locality_keys = locality_tokens(locality)
        if locality_keys:
            trusted_matches = [
                row
                for row in by_locality.get(compact(locality_keys[-1]), [])
                if names_overlap(stem, row.get("name"))
            ]
            source = choose_unique_source(trusted_matches, locality)
            if source:
                return Resolution(
                    branch,
                    source_candidate(source, "TRUSTED_LOCALITY_NAME_MATCH"),
                    "trusted_locality_name_match",
                )
    return None


def official_provider_branch_location(
    branch: dict[str, Any],
) -> dict[str, Any] | None:
    if clean_text(branch.get("provider")).upper() != "MUNI_HOME_PEN_GO_KR_92635850":
        return None

    # Kept with the collector so future collection and one-time repair use the
    # same official institution directory.
    from Crawler.Crawler_MunicipalYaml import (
        HOME_PEN_EXPERIENCE_BRANCH_LOCATIONS,
    )

    branch_codes = [
        clean_text(branch.get("branch_code")),
        *(
            clean_text(value)
            for value in branch.get("course_branch_codes") or []
        ),
    ]
    for branch_code in dict.fromkeys(value for value in branch_codes if value):
        location = HOME_PEN_EXPERIENCE_BRANCH_LOCATIONS.get(branch_code)
        if location:
            return location
    return None


class KakaoResolver:
    def __init__(
        self,
        api_key: str,
        *,
        timeout: int,
        delay: float,
        min_score: int,
        max_requests: int,
    ) -> None:
        if not clean_text(api_key):
            raise ValueError("Kakao Maps REST API key must not be empty.")
        self.api_key = api_key
        self.timeout = timeout
        self.delay = delay
        self.min_score = min_score
        self.max_requests = max(0, int(max_requests))
        self._requests = 0
        self._blocked_status: int | None = None
        self.place_cache: dict[str, AddressCandidate | None] = {}
        self.reverse_cache: dict[tuple[float, float], AddressCandidate | None] = {}
        self.address_cache: dict[str, AddressCandidate | None] = {}
        self._request_lock = threading.Lock()
        self._pacing_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._response_cache: dict[
            tuple[str, tuple[tuple[str, str], ...]],
            dict[str, Any] | None,
        ] = {}
        self._inflight: dict[
            tuple[str, tuple[tuple[str, str], ...]],
            threading.Event,
        ] = {}

    @property
    def requests(self) -> int:
        with self._request_lock:
            return self._requests

    @property
    def blocked_status(self) -> int | None:
        with self._request_lock:
            return self._blocked_status

    @staticmethod
    def _coordinates(result: dict[str, Any]) -> tuple[float, float] | None:
        try:
            lat = float(result["y"])
            lon = float(result["x"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (32.5 <= lat <= 39.5 and 124.0 <= lon <= 132.5):
            return None
        return lat, lon

    @staticmethod
    def _formatted_address(result: dict[str, Any]) -> str:
        road_address = result.get("road_address")
        address = result.get("address")
        values = (
            result.get("road_address_name"),
            road_address.get("address_name")
            if isinstance(road_address, dict)
            else None,
            result.get("address_name"),
            address.get("address_name") if isinstance(address, dict) else None,
        )
        return next(
            (
                normalize_stored_address(value)
                for value in values
                if clean_text(value)
            ),
            "",
        )

    def _request(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        cache_key = (
            url,
            tuple(sorted((str(key), str(value)) for key, value in params.items())),
        )
        with self._cache_lock:
            if cache_key in self._response_cache:
                return self._response_cache[cache_key]
            event = self._inflight.get(cache_key)
            request_owner = event is None
            if event is None:
                event = threading.Event()
                self._inflight[cache_key] = event

        if not request_owner:
            event.wait()
            with self._cache_lock:
                return self._response_cache.get(cache_key)

        payload: dict[str, Any] | None = None
        try:
            # Serialize request starts and their response handling. Besides global
            # pacing, this guarantees a fatal response opens the circuit before a
            # queued worker can issue another billable request.
            with self._pacing_lock:
                with self._request_lock:
                    if (
                        self._blocked_status is not None
                        or self._requests >= self.max_requests
                    ):
                        return None
                    self._requests += 1
                try:
                    response = requests.get(
                        url,
                        headers={"Authorization": f"KakaoAK {self.api_key}"},
                        params=params,
                        timeout=self.timeout,
                    )
                    response.raise_for_status()
                    response_payload = response.json()
                    if isinstance(response_payload, dict):
                        payload = response_payload
                except (requests.RequestException, ValueError) as exc:
                    status_code = getattr(
                        getattr(exc, "response", None),
                        "status_code",
                        None,
                    )
                    if status_code in KAKAO_FATAL_STATUS_CODES:
                        # Authentication/authorization failures and quota
                        # exhaustion are process-wide conditions. Stop every later
                        # unique query instead of consuming the entire budget.
                        with self._request_lock:
                            self._blocked_status = status_code
                    print(
                        f"kakao_request_failed type={type(exc).__name__} "
                        f"status={status_code if status_code is not None else '-'}"
                    )
                if self.delay > 0:
                    time.sleep(self.delay)
            return payload
        finally:
            with self._cache_lock:
                self._response_cache[cache_key] = payload
                self._inflight.pop(cache_key, None)
                event.set()

    def _documents(self, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = self._request(url, params)
        if not payload or not isinstance(payload.get("documents"), list):
            return []
        return [
            item for item in payload["documents"] if isinstance(item, dict)
        ]

    def reverse(self, lat: Any, lon: Any) -> AddressCandidate | None:
        try:
            key = round(float(lat), 7), round(float(lon), 7)
        except (TypeError, ValueError):
            return None
        if key in self.reverse_cache:
            return self.reverse_cache[key]
        documents = self._documents(
            KAKAO_COORD2ADDRESS_URL,
            {
                "x": key[1],
                "y": key[0],
                "input_coord": "WGS84",
            },
        )
        candidate = None
        for result in documents:
            address = self._formatted_address(result)
            if not is_usable_address(address):
                continue
            candidate = AddressCandidate(
                address=address,
                lat=key[0],
                lon=key[1],
                address_source="KAKAO_LOCAL_COORD2ADDRESS",
                coordinate_source=None,
                confidence=90,
                verified=True,
                query=f"{key[0]},{key[1]}",
            )
            break
        self.reverse_cache[key] = candidate
        return candidate

    def geocode_address(self, address: str, locality: str) -> AddressCandidate | None:
        normalized_address = clean_text(address)
        expected_address_key = road_address_geocode_key(normalized_address)
        if not expected_address_key:
            return None
        query = (
            normalized_address
            if address_matches_locality(normalized_address, locality)
            else clean_text(f"{locality} {normalized_address}")
        )
        if query in self.address_cache:
            return self.address_cache[query]
        documents = self._documents(
            KAKAO_ADDRESS_SEARCH_URL,
            {
                "query": query,
                "analyze_type": "similar",
                "size": 5,
            },
        )
        candidate = None
        for result in documents:
            formatted = self._formatted_address(result)
            coordinates = self._coordinates(result)
            if (
                not is_usable_address(formatted)
                or not address_matches_locality(formatted, locality)
                or road_address_geocode_key(formatted) != expected_address_key
                or coordinates is None
            ):
                continue
            candidate = AddressCandidate(
                address=formatted,
                lat=coordinates[0],
                lon=coordinates[1],
                address_source="KAKAO_LOCAL_ADDRESS",
                coordinate_source="KAKAO_LOCAL_ADDRESS",
                confidence=95,
                verified=True,
                query=query,
            )
            break
        self.address_cache[query] = candidate
        return candidate

    def place(
        self,
        provider: str,
        target_name: str,
        locality: str,
    ) -> AddressCandidate | None:
        query = clean_text(f"{locality} {target_name}")
        if query in self.place_cache:
            return self.place_cache[query]
        documents = self._documents(
            KAKAO_KEYWORD_SEARCH_URL,
            {
                "query": query,
                "size": 5,
                "sort": "accuracy",
            },
        )
        candidates: list[tuple[int, AddressCandidate]] = []
        for result in documents[:5]:
            name = clean_text(result.get("place_name"))
            address = self._formatted_address(result)
            coordinates = self._coordinates(result)
            score = place_candidate_score(
                target_name,
                locality,
                name,
                address,
                {"establishment"},
                provider,
            )
            if score < self.min_score or coordinates is None:
                continue
            candidates.append(
                (
                    score,
                    AddressCandidate(
                        address=address,
                        lat=coordinates[0],
                        lon=coordinates[1],
                        address_source="KAKAO_LOCAL_KEYWORD",
                        coordinate_source="KAKAO_LOCAL_KEYWORD",
                        confidence=score,
                        verified=True,
                        query=query,
                        matched_name=name,
                    ),
                )
            )
        candidate = max(candidates, key=lambda item: item[0])[1] if candidates else None
        self.place_cache[query] = candidate
        return candidate


def load_kakao_api_key() -> str:
    load_dotenv(ROOT / ".env")
    key = (
        os.getenv("KAKAO_MAPS_REST_API_KEY")
        or os.getenv("MoonCenKakaoMapsRestApiKey")
    )
    if not key:
        raise RuntimeError(
            "Kakao Maps REST API key is missing. Set "
            "KAKAO_MAPS_REST_API_KEY in the server environment."
        )
    return key


def load_naver_api_credentials() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    client_id = os.getenv("NAVER_SEARCH_CLIENT_ID") or os.getenv("NAVER_OAUTH_CLIENT_ID")
    client_secret = (
        os.getenv("NAVER_SEARCH_CLIENT_SECRET")
        or os.getenv("NAVER_OAUTH_CLIENT_SECRET")
    )
    if not client_id or not client_secret:
        raise RuntimeError("Naver Local Search API credentials are missing.")
    return client_id, client_secret


def naver_query_name(provider: Any, stem: Any) -> str:
    provider_text = clean_text(provider).upper()
    stem_text = clean_text(stem)
    if provider_text == "LOTTE":
        return stem_text
    if provider_text == "ELAND_RETAIL":
        return clean_text(
            f"뉴코아 NC백화점 {retail_branch_key(provider_text, stem_text)}점"
        )
    return kakao_query_name(provider_text, stem_text)


class NaverResolver:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        timeout: int,
        delay: float,
        min_score: int,
        max_requests: int,
    ) -> None:
        self.headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        }
        self.timeout = timeout
        self.delay = delay
        self.min_score = min_score
        self.max_requests = max_requests
        self.requests = 0
        self.cache: dict[str, AddressCandidate | None] = {}
        self._request_lock = threading.Lock()

    def _request_payload(self, query: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            with self._request_lock:
                if self.requests >= self.max_requests:
                    return {}
                self.requests += 1
            try:
                response = requests.get(
                    NAVER_LOCAL_URL,
                    headers=self.headers,
                    params={"query": query, "display": 5},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                status_code = getattr(
                    getattr(exc, "response", None),
                    "status_code",
                    None,
                )
                wait_seconds = (
                    max(self.delay, float(2**attempt))
                    if status_code == 429
                    else self.delay
                )
                time.sleep(wait_seconds)
                if status_code == 429:
                    continue
                break
            time.sleep(self.delay)
            return payload if isinstance(payload, dict) else {}
        if last_error:
            status_code = getattr(
                getattr(last_error, "response", None),
                "status_code",
                None,
            )
            print(
                f"naver_request_failed type={type(last_error).__name__} "
                f"status={status_code if status_code is not None else '-'}"
            )
        return {}

    def local(
        self,
        provider: str,
        target_name: str,
        locality: str,
        query_name: str,
    ) -> AddressCandidate | None:
        queries = search_query_variants(locality, query_name)
        cache_key = f"{provider}|{target_name}|{'|'.join(queries)}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        candidates: list[tuple[int, AddressCandidate]] = []
        for query in queries:
            payload = self._request_payload(query)

            for result in (payload.get("items") or [])[:5]:
                name = clean_text(
                    html.unescape(re.sub(r"<[^>]+>", "", clean_text(result.get("title"))))
                )
                address = normalize_stored_address(
                    result.get("roadAddress") or result.get("address")
                )
                score = place_candidate_score(
                    target_name,
                    locality,
                    name,
                    address,
                    {"establishment"},
                    provider=provider,
                )
                try:
                    lon = float(result.get("mapx")) / 10_000_000
                    lat = float(result.get("mapy")) / 10_000_000
                except (TypeError, ValueError):
                    continue
                if (
                    score < self.min_score
                    or not (33.0 <= lat <= 39.5)
                    or not (124.0 <= lon <= 132.0)
                ):
                    continue
                candidates.append(
                    (
                        score,
                        AddressCandidate(
                            address=address,
                            lat=lat,
                            lon=lon,
                            address_source="NAVER_LOCAL_SEARCH",
                            coordinate_source="NAVER_LOCAL_SEARCH",
                            confidence=score,
                            verified=True,
                            query=query,
                            matched_name=name,
                        ),
                    )
                )
            if candidates:
                break
        candidate = max(candidates, key=lambda item: item[0])[1] if candidates else None
        self.cache[cache_key] = candidate
        return candidate

    def address(
        self,
        locality: str,
        address: str,
    ) -> AddressCandidate | None:
        queries = tuple(
            dict.fromkeys(
                value
                for value in (
                    clean_text(address),
                    clean_text(f"{locality} {address}"),
                )
                if value
            )
        )
        cache_key = f"ADDRESS|{locality}|{address}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        expected_key = road_address_key(address)
        candidate = None
        for query in queries:
            payload = self._request_payload(query)

            for result in (payload.get("items") or [])[:5]:
                result_address = normalize_stored_address(
                    result.get("roadAddress") or result.get("address")
                )
                if (
                    not expected_key
                    or road_address_key(result_address) != expected_key
                    or not address_matches_locality(result_address, locality)
                ):
                    continue
                try:
                    lon = float(result.get("mapx")) / 10_000_000
                    lat = float(result.get("mapy")) / 10_000_000
                except (TypeError, ValueError):
                    continue
                if not (33.0 <= lat <= 39.5) or not (124.0 <= lon <= 132.0):
                    continue
                name = clean_text(
                    html.unescape(re.sub(r"<[^>]+>", "", clean_text(result.get("title"))))
                )
                candidate = AddressCandidate(
                    # Preserve a complete source address including its floor.
                    # For a road-only fragment embedded in a venue label, use
                    # Naver's full locality-qualified address instead.
                    address=(
                        normalize_stored_address(address)
                        if locality_tokens(address)
                        else result_address
                    ),
                    lat=lat,
                    lon=lon,
                    address_source="NAVER_LOCAL_SEARCH_BY_ADDRESS",
                    coordinate_source="NAVER_LOCAL_SEARCH_BY_ADDRESS",
                    confidence=95,
                    verified=True,
                    query=query,
                    matched_name=name,
                )
                break
            if candidate:
                break
        self.cache[cache_key] = candidate
        return candidate


def administrative_center_search_name(value: Any) -> str:
    text = clean_text(value).replace("_", " ")
    routed_match = re.search(
        r"(?:통합)?행정복지센터\s*/\s*([가-힣0-9·,.]+(?:동|읍|면))$",
        text,
    )
    if routed_match:
        return f"{routed_match.group(1)} 주민센터"
    bracket_match = re.search(r"(?:주민센터|자치회관)\s*[\[(]\s*([가-힣]+\d*동)\s*[\])]", text)
    if bracket_match:
        return f"{bracket_match.group(1)} 주민센터"
    prefix_match = re.fullmatch(r"주민센터\s+([가-힣0-9·,.]+동)", text)
    if prefix_match:
        return f"{prefix_match.group(1)} 주민센터"
    center_match = re.fullmatch(
        r"(.+?(?:동|읍|면))\s*(?:주민자치센터|주민자치회|자치회관|주민센터|행정복지센터)",
        text,
    )
    if center_match:
        unit_match = re.search(
            r"([가-힣0-9·,.]+(?:동|읍|면))$",
            center_match.group(1),
        )
        if unit_match:
            return f"{unit_match.group(1)} 주민센터"
    merged_unit = re.fullmatch(r"([가-힣0-9]+(?:[·,.][가-힣0-9]+)+동)", text)
    if merged_unit:
        return f"{merged_unit.group(1)} 주민센터"
    if ADMINISTRATIVE_UNIT_PATTERN.fullmatch(text):
        return f"{text} 행정복지센터"
    return ""


def searchable_facility_stem(branch: dict[str, Any], locality: Any = "") -> str:
    search_name = branch.get("_repair_search_name") or branch.get("name")
    normalized_name = strip_locality_prefix(search_name, locality)
    admin_name = administrative_center_search_name(normalized_name)
    if admin_name:
        return admin_name
    if clean_text(branch.get("provider")).upper() == "NATIONAL_MUSEUM_OF_MODERN_ART":
        name = normalized_name
        if name:
            return f"국립현대미술관 {name}"
    stem = facility_stem(branch.get("provider"), normalized_name)
    if stem:
        return stem
    venue_stems = {
        facility_stem(branch.get("provider"), value)
        for value in branch.get("course_venue_names") or []
        if not has_multiple_venues(value)
    }
    venue_stems.discard("")
    return next(iter(venue_stems)) if len(venue_stems) == 1 else ""


def invalid_address_search_name(
    branch: dict[str, Any],
    locality: Any,
) -> str:
    name = clean_text(branch.get("name"))
    address = clean_text(branch.get("address"))
    if not address or compact(address) in {"", "[]"}:
        return name
    address_key = compact(address.removeprefix("대한민국"))
    locality_key = compact(locality)
    if (
        not address_key
        or address_key == locality_key
        or (locality_key and locality_key.endswith(address_key))
        or (locality_key and address_key.endswith(locality_key))
        or is_non_physical_name(address)
    ):
        return name
    return address if compact(address) != compact(name) else name


def external_resolution(
    branch: dict[str, Any],
    locality: str,
    resolver: KakaoResolver | None,
    naver_resolver: NaverResolver | None,
) -> tuple[Resolution | None, str]:
    embedded = embedded_road_address(branch.get("name"))
    if embedded:
        if naver_resolver:
            candidate = naver_resolver.address(locality, embedded)
            if candidate:
                return Resolution(branch, candidate, "embedded_address_naver"), ""
        if resolver:
            candidate = resolver.geocode_address(embedded, locality)
            if candidate:
                return Resolution(branch, candidate, "embedded_address_kakao"), ""

    search_name = branch.get("_repair_search_name") or branch.get("name")
    normalized_name = strip_locality_prefix(search_name, locality)
    admin_name = administrative_center_search_name(normalized_name)
    course_address = embedded_course_address(branch, locality)
    if course_address:
        if naver_resolver:
            candidate = naver_resolver.address(locality, course_address)
            if candidate:
                return Resolution(branch, candidate, "course_embedded_address_naver"), ""
        if resolver:
            candidate = resolver.geocode_address(course_address, locality)
            if candidate:
                return Resolution(branch, candidate, "course_embedded_address_kakao"), ""
    if has_multiple_venues(normalized_name) and not admin_name:
        return None, "multiple_venue_name_needs_split"

    if is_non_physical_name(search_name):
        return None, "non_physical_venue"
    stem = searchable_facility_stem(branch, locality)
    if not stem:
        return None, "room_only_or_unusable_name"
    if (
        is_generic_branch_name(normalized_name)
        and not admin_name
    ):
        return None, "generic_name_without_locality"
    if (
        compact(stem) in {compact(value) for value in GENERIC_FACILITY_STEMS}
        and not locality_tokens(locality)
    ):
        return None, "generic_name_without_locality"
    if is_ambiguous_facility_name(stem):
        return None, "generic_name_without_locality"

    provider = clean_text(branch.get("provider")).upper()
    if naver_resolver:
        candidate = naver_resolver.local(
            provider,
            stem,
            locality,
            naver_query_name(provider, stem),
        )
        if candidate:
            return Resolution(branch, candidate, "naver_local"), ""

    if resolver:
        query_name = kakao_query_name(provider, stem)
        candidate = resolver.place(provider, query_name, locality)
        if candidate:
            return Resolution(branch, candidate, "kakao_keyword"), ""
    if (
        (resolver and resolver.requests >= resolver.max_requests)
        or (
            naver_resolver
            and naver_resolver.requests >= naver_resolver.max_requests
            and not resolver
        )
    ):
        return None, "kakao_request_limit_reached"
    return None, "no_verified_external_match"


def persist_resolutions(
    resolutions: list[Resolution],
    *,
    repair_invalid_crawler_addresses: bool = False,
    repair_invalid_addresses: bool = False,
) -> tuple[int, int]:
    updated_branches = 0
    updated_courses = 0
    with get_db_cursor() as cursor:
        for resolution in resolutions:
            candidate = resolution.candidate
            cursor.execute(
                """
                UPDATE branches
                SET address = %(address)s,
                    address_source = %(address_source)s,
                    region_sido = COALESCE(NULLIF(%(region_sido)s, ''), region_sido),
                    region_sigungu = COALESCE(
                        NULLIF(%(region_sigungu)s, ''),
                        region_sigungu
                    ),
                    lat = COALESCE(%(lat)s, lat),
                    lon = COALESCE(%(lon)s, lon),
                    coordinate_source = CASE
                        WHEN %(lat)s IS NOT NULL AND %(lon)s IS NOT NULL
                            THEN %(coordinate_source)s
                        ELSE coordinate_source
                    END,
                    location_confidence = GREATEST(
                        COALESCE(location_confidence, 0),
                        %(confidence)s
                    ),
                    location_verified = location_verified OR %(verified)s,
                    location_checked_at = CURRENT_TIMESTAMP,
                    location_query = %(query)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(id)s
                  AND (
                        NULLIF(btrim(address), '') IS NULL
                        OR (
                            %(repair_invalid_crawler)s
                            AND address = %(previous_address)s
                            AND address_source = 'crawler'
                            AND COALESCE(location_verified, FALSE) IS FALSE
                        )
                        OR (
                            %(repair_invalid_all)s
                            AND address = %(previous_address)s
                        )
                  )
                """,
                {
                    "id": resolution.branch["id"],
                    "address": candidate.address,
                    "address_source": candidate.address_source,
                    "region_sido": candidate.region_sido,
                    "region_sigungu": candidate.region_sigungu,
                    "lat": candidate.lat,
                    "lon": candidate.lon,
                    "coordinate_source": candidate.coordinate_source,
                    "confidence": candidate.confidence,
                    "verified": candidate.verified,
                    "query": candidate.query,
                    "repair_invalid_crawler": repair_invalid_crawler_addresses,
                    "repair_invalid_all": repair_invalid_addresses,
                    "previous_address": clean_text(
                        resolution.branch.get("address")
                    ),
                },
            )
            if not cursor.rowcount:
                continue
            updated_branches += cursor.rowcount
            if resolution.branch.get("_skip_course_address_backfill"):
                continue
            cursor.execute(
                """
                UPDATE courses
                SET venue_address = %(address)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE branch_id = %(branch_id)s
                  AND (
                        NULLIF(btrim(venue_address), '') IS NULL
                        OR (
                            %(repair_invalid)s
                            AND btrim(venue_address) = %(previous_address)s
                        )
                  )
                  AND (
                        NULLIF(btrim(venue_name), '') IS NULL
                        OR regexp_replace(
                            lower(venue_name),
                            '[[:space:]]+',
                            '',
                            'g'
                        ) LIKE (
                            '%%'
                            || regexp_replace(
                                lower(%(branch_name)s),
                                '[[:space:]]+',
                                '',
                                'g'
                            )
                            || '%%'
                        )
                        OR regexp_replace(
                            lower(%(branch_name)s),
                            '[[:space:]]+',
                            '',
                            'g'
                        ) LIKE (
                            '%%'
                            || regexp_replace(
                                lower(venue_name),
                                '[[:space:]]+',
                                '',
                                'g'
                            )
                            || '%%'
                        )
                  )
                """,
                {
                    "branch_id": resolution.branch["id"],
                    "branch_name": clean_text(resolution.branch.get("name")),
                    "address": candidate.address,
                    "previous_address": clean_text(
                        resolution.branch.get("address")
                    ),
                    "repair_invalid": (
                        repair_invalid_crawler_addresses
                        or repair_invalid_addresses
                    ),
                },
            )
            updated_courses += cursor.rowcount
    return updated_branches, updated_courses


def invalid_external_report_rows(report_path: Path) -> list[dict[str, str]]:
    with report_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    invalid: list[dict[str, str]] = []
    for row in rows:
        method = clean_text(row.get("method"))
        if method not in {"kakao_keyword", "google_places", "naver_local"}:
            continue
        provider = clean_text(row.get("provider")).upper()
        name = clean_text(row.get("name"))
        admin_name = administrative_center_search_name(name)
        if (
            (is_generic_branch_name(name) and not admin_name)
            or is_non_physical_name(name)
        ):
            invalid.append(row)
            continue
        stem = searchable_facility_stem({"provider": provider, "name": name}) or name
        if is_ambiguous_facility_name(stem):
            invalid.append(row)
            continue
        target_name = (
            kakao_query_name(provider, stem)
            if method in {"kakao_keyword", "google_places"}
            else stem
        )
        locality = clean_text(row.get("locality")) or " ".join(
            locality_tokens(row.get("address"))
        )
        score = place_candidate_score(
            target_name,
            locality,
            row.get("matched_name"),
            row.get("address"),
            {"establishment"},
            provider,
        )
        if score == 0:
            invalid.append(row)
    return invalid


def rollback_invalid_external_rows(
    rows: list[dict[str, str]],
    *,
    apply: bool,
) -> tuple[int, int]:
    if not apply or not rows:
        return 0, 0
    branch_count = 0
    course_count = 0
    with get_db_cursor() as cursor:
        for row in rows:
            method = clean_text(row.get("method"))
            source = {
                "kakao_keyword": "KAKAO_LOCAL_KEYWORD",
                "google_places": "GOOGLE_PLACES_TEXT_SEARCH",
                "naver_local": "NAVER_LOCAL_SEARCH",
            }.get(method)
            if not source:
                continue
            cursor.execute(
                """
                SELECT id
                FROM branches
                WHERE provider = %(provider)s
                  AND branch_code = %(branch_code)s
                  AND address = %(address)s
                  AND address_source = %(source)s
                FOR UPDATE
                """,
                {**row, "source": source},
            )
            branch = cursor.fetchone()
            if not branch:
                continue
            cursor.execute(
                """
                UPDATE courses
                SET venue_address = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE branch_id = %(branch_id)s
                  AND venue_address = %(address)s
                """,
                {"branch_id": branch["id"], "address": row["address"]},
            )
            course_count += cursor.rowcount
            cursor.execute(
                """
                UPDATE branches
                SET address = NULL,
                    address_source = NULL,
                    lat = NULL,
                    lon = NULL,
                    coordinate_source = NULL,
                    location_confidence = 0,
                    location_verified = FALSE,
                    location_checked_at = CURRENT_TIMESTAMP,
                    location_query = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(branch_id)s
                """,
                {"branch_id": branch["id"]},
            )
            branch_count += cursor.rowcount
    return branch_count, course_count


def write_reports(
    output_dir: Path,
    resolutions: list[Resolution],
    unresolved: list[dict[str, Any]],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    resolved_path = output_dir / f"branch_address_resolved_{stamp}.csv"
    unresolved_path = output_dir / f"branch_address_unresolved_{stamp}.csv"

    provider_localities = load_provider_localities()
    with resolved_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "provider",
                "branch_code",
                "name",
                "previous_address",
                "active_courses",
                "locality",
                "region_sido",
                "region_sigungu",
                "method",
                "matched_name",
                "address",
                "lat",
                "lon",
                "confidence",
                "verified",
                "address_source",
                "coordinate_source",
                "query",
            ],
        )
        writer.writeheader()
        for item in resolutions:
            writer.writerow(
                {
                    "provider": item.branch.get("provider"),
                    "branch_code": item.branch.get("branch_code"),
                    "name": item.branch.get("name"),
                    "previous_address": item.branch.get("address"),
                    "active_courses": item.branch.get("active_courses"),
                    "locality": branch_locality(item.branch, provider_localities),
                    "region_sido": item.candidate.region_sido
                    or item.branch.get("region_sido"),
                    "region_sigungu": item.candidate.region_sigungu
                    or item.branch.get("region_sigungu"),
                    "method": item.method,
                    "matched_name": item.candidate.matched_name,
                    "address": item.candidate.address,
                    "lat": item.candidate.lat,
                    "lon": item.candidate.lon,
                    "confidence": item.candidate.confidence,
                    "verified": item.candidate.verified,
                    "address_source": item.candidate.address_source,
                    "coordinate_source": item.candidate.coordinate_source,
                    "query": item.candidate.query,
                }
            )

    with unresolved_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "provider",
                "branch_code",
                "name",
                "active_courses",
                "locality",
                "reason",
                "website_url",
            ],
        )
        writer.writeheader()
        writer.writerows(unresolved)
    return resolved_path, unresolved_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing branch addresses from trusted DB matches and verified map results."
    )
    parser.add_argument("--provider", help="Optional provider code.")
    parser.add_argument(
        "--rollback-report",
        type=Path,
        help="Audit a prior resolved CSV and roll back invalid external name matches.",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument(
        "--repair-invalid-crawler-addresses",
        action="store_true",
        help=(
            "Also replace unverified crawler values that are not road or "
            "lot-number addresses."
        ),
    )
    parser.add_argument(
        "--repair-invalid-addresses",
        action="store_true",
        help=(
            "Replace every stored value that is not a usable road or "
            "lot-number address, regardless of its original source."
        ),
    )
    parser.add_argument(
        "--kakao",
        action="store_true",
        help="Use Kakao Local address, keyword, and coordinate search.",
    )
    parser.add_argument(
        "--google",
        action="store_true",
        help="Deprecated alias for --kakao; no Google API is called.",
    )
    parser.add_argument("--naver", action="store_true", help="Use Naver Local Search.")
    parser.add_argument("--apply", action="store_true", help="Persist resolved addresses. Default is dry-run.")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--min-score", type=int, default=77)
    parser.add_argument(
        "--max-kakao-requests",
        type=int,
        default=KAKAO_DEFAULT_MAX_REQUESTS,
    )
    parser.add_argument(
        "--max-google-requests",
        type=int,
        default=None,
        help=(
            "Deprecated alias for --max-kakao-requests; the budget applies "
            "only to Kakao Local requests."
        ),
    )
    parser.add_argument("--max-naver-requests", type=int, default=5000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "logs" / "branch_address_backfill",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    use_kakao = bool(args.kakao or args.google)
    max_kakao_requests = args.max_kakao_requests
    if args.google:
        print("deprecated_option=--google replacement=--kakao provider=kakao")
    if args.max_google_requests is not None:
        print(
            "deprecated_option=--max-google-requests "
            "replacement=--max-kakao-requests provider=kakao"
        )
        max_kakao_requests = args.max_google_requests
    if args.rollback_report:
        invalid = invalid_external_report_rows(args.rollback_report)
        print(
            f"rollback_report={args.rollback_report} invalid_external_matches={len(invalid)} "
            f"apply={args.apply}"
        )
        for row in invalid:
            print(
                f"invalid provider={row.get('provider')} branch={row.get('name')} "
                f"matched={row.get('matched_name')} address={row.get('address')}"
            )
        branches, courses = rollback_invalid_external_rows(invalid, apply=args.apply)
        print(f"rolled_back_branches={branches} rolled_back_courses={courses}")
        return 0

    provider = clean_text(args.provider).upper() or None
    localities = load_provider_localities()
    provider_target_names = load_provider_target_names()
    branches = fetch_missing_branches(
        provider,
        active_only=args.active_only,
        limit=args.limit or None,
        repair_invalid_crawler_addresses=(
            args.repair_invalid_crawler_addresses
        ),
        repair_invalid_addresses=args.repair_invalid_addresses,
    )
    if (
        args.repair_invalid_crawler_addresses
        or args.repair_invalid_addresses
    ):
        for branch in branches:
            provider_key = clean_text(branch.get("provider")).upper()
            locality = INVALID_ADDRESS_PROVIDER_LOCALITIES.get(
                provider_key,
                branch_locality(branch, localities),
            )
            branch["_repair_locality"] = locality
            branch["_repair_search_name"] = invalid_address_search_name(
                branch,
                locality,
            )
            if args.repair_invalid_addresses and (
                is_generic_branch_name(branch.get("name"))
                or clean_text(branch.get("name")).endswith("전역")
            ) and not administrative_center_search_name(branch.get("name")):
                operator_name = provider_operator_search_name(
                    branch,
                    locality,
                    provider_target_names,
                )
                if operator_name:
                    branch["_repair_search_name"] = operator_name
                    branch["_skip_course_address_backfill"] = True
                    if operator_name != administrative_operator_name(locality):
                        branch["_repair_locality"] = broader_municipality_locality(
                            locality
                        )
    sources = fetch_address_sources()
    by_name, by_stem, by_locality = build_source_indexes(sources)
    print(
        f"targets={len(branches)} provider={provider or 'ALL'} "
        f"active_only={args.active_only} naver={args.naver} "
        f"kakao={use_kakao} "
        f"repair_invalid_crawler={args.repair_invalid_crawler_addresses} "
        f"repair_invalid_all={args.repair_invalid_addresses} "
        f"apply={args.apply}"
    )

    resolutions: list[Resolution] = []
    pending: list[tuple[dict[str, Any], str]] = []
    for branch in branches:
        locality = clean_text(branch.get("_repair_locality")) or branch_locality(
            branch,
            localities,
        )
        resolution = deterministic_resolution(
            branch,
            locality,
            by_name,
            by_stem,
            by_locality,
        )
        if resolution:
            resolutions.append(resolution)
        else:
            pending.append((branch, locality))

    resolver = None
    if use_kakao:
        resolver = KakaoResolver(
            load_kakao_api_key(),
            timeout=args.timeout,
            delay=args.delay,
            min_score=args.min_score,
            max_requests=max(0, max_kakao_requests),
        )
    naver_resolver = None
    if args.naver:
        client_id, client_secret = load_naver_api_credentials()
        naver_resolver = NaverResolver(
            client_id,
            client_secret,
            timeout=args.timeout,
            delay=args.delay,
            min_score=args.min_score,
            max_requests=max(0, args.max_naver_requests),
        )

    unresolved: list[dict[str, Any]] = []

    def add_unresolved(branch: dict[str, Any], locality: str, reason: str) -> None:
        unresolved.append(
            {
                "provider": branch.get("provider"),
                "branch_code": branch.get("branch_code"),
                "name": branch.get("name"),
                "active_courses": branch.get("active_courses"),
                "locality": locality,
                "reason": reason,
                "website_url": branch.get("website_url"),
            }
        )

    if not resolver and not naver_resolver:
        for branch, locality in pending:
            add_unresolved(branch, locality, "no_trusted_database_match")
    else:
        grouped: dict[tuple[str, str, str, str], list[tuple[dict[str, Any], str]]] = defaultdict(list)
        for branch, locality in pending:
            branch_provider = clean_text(branch.get("provider")).upper()
            search_name = branch.get("_repair_search_name") or branch.get("name")
            normalized_name = strip_locality_prefix(search_name, locality)
            admin_name = administrative_center_search_name(normalized_name)
            if embedded := embedded_road_address(branch.get("name")):
                key = ("address", branch_provider, locality, embedded)
            elif has_multiple_venues(normalized_name) and not admin_name:
                key = (
                    "skip",
                    branch_provider,
                    "multiple_venue_name_needs_split",
                    clean_text(branch.get("id")),
                )
            elif is_non_physical_name(branch.get("name")):
                key = (
                    "skip",
                    branch_provider,
                    "non_physical_venue",
                    clean_text(branch.get("id")),
                )
            else:
                stem = searchable_facility_stem(branch, locality)
                query_name = naver_query_name(branch_provider, stem)
                key = (
                    "place",
                    branch_provider,
                    locality,
                    query_name or clean_text(branch.get("id")),
                )
            grouped[key].append((branch, locality))

        workers = max(1, min(16, int(args.workers)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    external_resolution,
                    items[0][0],
                    items[0][1],
                    resolver,
                    naver_resolver,
                ): items
                for items in grouped.values()
            }
            for index, future in enumerate(as_completed(futures), start=1):
                items = futures[future]
                try:
                    resolution, reason = future.result()
                except Exception as exc:
                    resolution = None
                    reason = f"resolver_error:{type(exc).__name__}"
                if resolution:
                    for branch, _locality in items:
                        resolutions.append(
                            Resolution(
                                branch=branch,
                                candidate=resolution.candidate,
                                method=resolution.method,
                            )
                        )
                else:
                    for branch, locality in items:
                        add_unresolved(branch, locality, reason)
                if index % 100 == 0:
                    print(
                        f"progress_groups={index}/{len(grouped)} resolved={len(resolutions)} "
                        f"unresolved={len(unresolved)} "
                        f"naver_requests={naver_resolver.requests if naver_resolver else 0} "
                        f"kakao_requests={resolver.requests if resolver else 0}"
                    )

    if resolver and resolver.blocked_status is not None:
        print(
            "fatal_kakao_request_blocked "
            f"status={resolver.blocked_status} apply_aborted={args.apply}"
        )
        return 2

    method_counts = Counter(item.method for item in resolutions)
    reason_counts = Counter(item["reason"] for item in unresolved)
    updated_branches = 0
    updated_courses = 0
    if args.apply:
        updated_branches, updated_courses = persist_resolutions(
            resolutions,
            repair_invalid_crawler_addresses=(
                args.repair_invalid_crawler_addresses
            ),
            repair_invalid_addresses=args.repair_invalid_addresses,
        )

    resolved_path, unresolved_path = write_reports(args.output_dir, resolutions, unresolved)
    print(
        f"resolved={len(resolutions)} unresolved={len(unresolved)} "
        f"updated_branches={updated_branches} updated_courses={updated_courses} "
        f"naver_requests={naver_resolver.requests if naver_resolver else 0} "
        f"kakao_requests={resolver.requests if resolver else 0}"
    )
    print(f"methods={dict(method_counts)}")
    print(f"reasons={dict(reason_counts)}")
    print(f"resolved_report={resolved_path}")
    print(f"unresolved_report={unresolved_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
