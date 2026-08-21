import os.path
import datetime
import re
import time
import pandas as pd
import requests
from bs4 import BeautifulSoup

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Google Calendar API의 범위(scope) 설정. 수정 없이 사용.
SCOPES = ['https://www.googleapis.com/auth/calendar']

# 캘린더 ID 설정. 'primary'는 기본 캘린더를 의미합니다.
CALENDAR_ID = 'primary'

def get_calendar_service():
    """Google Calendar API 서비스 객체를 생성하고 반환합니다."""
    
    # --- 이 부분이 추가되었습니다 ---
    # 현재 스크립트 파일이 있는 디렉토리의 절대 경로를 가져옵니다.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 절대 경로를 사용하여 credentials.json과 token.json 파일의 전체 경로를 만듭니다.
    credentials_path = os.path.join(script_dir, 'credentials.json')
    token_path = os.path.join(script_dir, 'token.json')
    # --- 여기까지 ---

    creds = None
    # [수정됨] 'token.json' 대신 token_path 변수 사용
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # [수정됨] 'credentials.json' 대신 credentials_path 변수 사용
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        # [수정됨] 'token.json' 대신 token_path 변수 사용
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
            
    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except HttpError as error:
        print(f'An error occurred: {error}')
        return None

def _parse_table_to_dict(table_soup, key_color):
    """지정된 색상의 key 셀을 찾아 테이블을 파싱하는 헬퍼 함수"""
    if not table_soup:
        return {}
    info_dict = {}
    key_cells = table_soup.find_all('td', attrs={'bgcolor': key_color})
    for cell in key_cells:
        key = cell.get_text(separator=' ', strip=True)
        value_cell = cell.find_next_sibling('td')
        if value_cell:
            value = value_cell.get_text(strip=True)
            info_dict[key] = value
    return info_dict

def scrape_ipo_detail_page(url):
    """[수정됨] 두 가지 다른 형식의 상세 페이지를 모두 처리합니다."""
    try:
        print(f"상세 정보 수집 중: {url}")
        response = requests.get(url)
        response.encoding = 'euc-kr'
        soup = BeautifulSoup(response.text, 'html.parser')

        # 최종 데이터를 담을 딕셔너리
        overview_data, ipo_info_data, schedule_data = {}, {}, {}

        # 공통: '기업개요' 테이블은 항상 존재한다고 가정하고 종목명 추출
        overview_table = soup.find('table', summary='기업개요')
        if overview_table:
            overview_data = _parse_table_to_dict(overview_table, key_color='#F1F4F7')

        # 로직 분기: 새로운 통합 테이블 형식인지, 기존 분리된 형식인지 확인
        integrated_table = soup.find('table', summary='공모청약일정')
        if integrated_table:
            # 1. 새로운 형식: 통합 테이블에서 모든 정보를 한 번에 추출 (key 색상: #F5F5F2)
            print(" -> 통합 테이블 형식 페이지 발견")
            all_data = _parse_table_to_dict(integrated_table, key_color='#F5F5F2')
            ipo_info_data = all_data
            schedule_data = all_data
        else:
            # 2. 기존 형식: 분리된 테이블에서 각각 정보 추출 (key 색상: #F1F4F7)
            print(" -> 분리된 테이블 형식 페이지 발견")
            ipo_info_table = soup.find('table', summary='공모정보')
            ipo_info_data = _parse_table_to_dict(ipo_info_table, key_color='#F1F4F7')

            schedule_table = soup.find('table', summary='주요일정')
            schedule_data = _parse_table_to_dict(schedule_table, key_color='#F1F4F7')

        # 최종적으로 필요한 정보들을 취합
        final_data = {
            '종목명': overview_data.get('종목명'),
            '확정공모가': ipo_info_data.get('확정공모가', ipo_info_data.get('희망공모가액', '미정')),
            '주간사': ipo_info_data.get('주간사'),
            '공모청약일': schedule_data.get('공모청약일'),
            '상장일': schedule_data.get('상장일')
        }
        
        # '희망공모가액'에서 불필요한 텍스트 제거 (예: '11,400 ~ 14,000 원' -> '11,400~14,000')
        if '희망공모가액' in final_data:
            price_text = final_data['희망공모가액']
            cleaned_price = re.sub(r'[^0-9~,]', '', price_text).strip()
            final_data['확정공모가'] = cleaned_price

        return final_data

    except Exception as e:
        print(f"상세 페이지({url}) 스크래핑 중 오류 발생: {e}")
        return None


def scrape_ipo_data():
    """38커뮤니케이션 목록에서 상세 페이지 링크를 가져와 각 페이지를 스크래핑합니다."""
    print("38커뮤니케이션에서 공모주 목록을 가져옵니다...")
    main_url = "http://www.38.co.kr/html/fund/index.htm?o=k"
    base_url = "http://www.38.co.kr"
    
    try:
        response = requests.get(main_url)
        response.encoding = 'euc-kr'
        soup = BeautifulSoup(response.text, 'html.parser')
        
        main_table = soup.find('table', {'summary': '공모주 청약일정'})
        detail_links = []
        for row in main_table.find_all('tr'):
            first_cell = row.find('td')
            if not first_cell or not first_cell.a: continue
            
            # 스팩 제외
            if '스팩' in first_cell.get_text(strip=True):
                continue

            link = first_cell.a['href']
            if not link.startswith('http'):
                link = base_url + link
            detail_links.append(link)

        print(f"{len(detail_links)}개의 공모주 정보를 찾았습니다. 각 종목의 상세 정보를 수집합니다.")
        
        all_ipo_data = []
        for link in detail_links:
            ipo_info = scrape_ipo_detail_page(link)
            if ipo_info and ipo_info.get('종목명'): # 종목명이 있는 유효한 데이터만 추가
                all_ipo_data.append(ipo_info)
            time.sleep(0.2) # 서버 부하를 줄이기 위한 지연 시간
        
        if not all_ipo_data: return None

        df = pd.DataFrame(all_ipo_data)
        print("공모주 상세 정보 스크래핑 완료.")
        return df

    except Exception as e:
        print(f"메인 페이지 스크래핑 중 오류 발생: {e}")
        return None

def check_event_exists(service, event_summary):
    """캘린더에 동일한 이름의 이벤트가 있는지 확인합니다."""
    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        q=event_summary,
        singleEvents=True
    ).execute()
    return len(events_result.get('items', [])) > 0
# [수정됨] 이 함수만 아래 내용으로 교체해주세요.
def add_ipo_to_calendar(service, ipo_data):
    """스크래핑한 공모주 정보를 구글 캘린더에 추가합니다. (지정된 시간에 알림 설정)"""
    if ipo_data is None or ipo_data.empty:
        print("추가할 공모주 정보가 없습니다.")
        return
    
    today = datetime.date.today()
    TIMEZONE = 'Asia/Seoul' # 시간대 설정

    for index, row in ipo_data.iterrows():
        if pd.isna(row.get('종목명')): continue
        company_name = row['종목명']
        
        # 1. 청약일 이벤트 생성 (알림 시간: 오전 8시 50분)
        if pd.notna(row.get('공모청약일')):
            sub_dates = re.findall(r'(\d{4}\.\d{2}\.\d{2})', str(row['공모청약일']))
            if len(sub_dates) == 2:
                start_date = datetime.datetime.strptime(sub_dates[1], '%Y.%m.%d').date()
                
                if start_date < today:
                    continue

                # [수정됨] 시작 날짜와 알림 시간(10)을 결합
                start_datetime = datetime.datetime.combine(start_date, datetime.time(10, 00))
                # [수정됨] 이벤트 종료 시간은 시작 시간으로부터 1시간 뒤로 설정
                end_datetime = start_datetime + datetime.timedelta(hours=1)
                
                event_summary = f"[공모주 청약] {company_name}"

                if check_event_exists(service, event_summary):
                    print(f"이미 등록된 청약 일정입니다: {event_summary}")
                else:
                    description = f" 공모가: {row.get('확정공모가', '미정')}\n 주관사: {row.get('주간사', '미정')}"
                    event = {
                        'summary': event_summary,
                        'description': description,
                        # [수정됨] 'date' 대신 'dateTime'과 'timeZone'을 사용
                        'start': {'dateTime': start_datetime.isoformat(), 'timeZone': TIMEZONE},
                        'end': {'dateTime': end_datetime.isoformat(), 'timeZone': TIMEZONE},
                        # [수정됨] 이벤트 시작 정각(0분 전)에 팝업 알림 설정
                        'reminders': {
                            'useDefault': False,
                            'overrides': [{'method': 'popup', 'minutes': 0}],
                        },
                    }
                    service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
                    print(f" 청약 일정 추가 완료: {event_summary} (알림: 10:00)")

        # 2. 상장일 이벤트 생성 (알림 시간: 오전 8시50)
        
        if pd.notna(row.get('상장일')):
            
            listing_date_str = re.search(r'(\d{4}[./]\d{2}[./]\d{2})', str(row['상장일']))
            print(row['상장일'])
            if listing_date_str:
                listing_date_str_cleaned = listing_date_str.group(1).replace('/', '.')
                listing_date = datetime.datetime.strptime(listing_date_str_cleaned, '%Y.%m.%d').date()
                
                if listing_date < today:
                    continue
                
                # [수정됨] 시작 날짜와 알림 시간(10:00)을 결합
                start_datetime = datetime.datetime.combine(listing_date, datetime.time(8, 50))
                # [수정됨] 이벤트 종료 시간은 시작 시간으로부터 1시간 뒤로 설정
                end_datetime = start_datetime + datetime.timedelta(hours=1)

                event_summary = f"[공모주 상장] {company_name}"
                if check_event_exists(service, event_summary):
                     print(f"이미 등록된 상장 일정입니다: {event_summary}")
                else:
                    description = f" 공모가: {row.get('확정공모가', '미정')}\n 주관사: {row.get('주간사', '미정')}"
                    event = {
                        'summary': event_summary,
                        'description': description,
                        # [수정됨] 'date' 대신 'dateTime'과 'timeZone'을 사용
                        'start': {'dateTime': start_datetime.isoformat(), 'timeZone': TIMEZONE},
                        'end': {'dateTime': end_datetime.isoformat(), 'timeZone': TIMEZONE},
                        # [수정됨] 이벤트 시작 정각(0분 전)에 팝업 알림 설정
                        'reminders': {
                            'useDefault': False,
                            'overrides': [{'method': 'popup', 'minutes': 0}],
                        },
                    }
                    service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
                    print(f" 상장 일정 추가 완료: {event_summary} (알림: 10:00)")

def main():
    """메인 실행 함수"""
    service = get_calendar_service()
    if service:
        ipo_data = scrape_ipo_data()
        if ipo_data is not None:
            add_ipo_to_calendar(service, ipo_data)

if __name__ == '__main__':
    main()
