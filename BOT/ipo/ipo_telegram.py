import os
import datetime
import re
import time
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler

# 환경 변수 로드
load_dotenv()

# Telegram Bot 설정
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    """Telegram으로 메시지를 전송합니다."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram Bot Token 또는 Chat ID가 설정되지 않았습니다.")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    }
    
    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            print(f"✅ Telegram 메시지 전송 성공")
            return True
        else:
            print(f"❌ Telegram 메시지 전송 실패: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Telegram 메시지 전송 중 오류: {e}")
        return False

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
    """두 가지 다른 형식의 상세 페이지를 모두 처리합니다."""
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

        # 공통: '공모정보' 테이블에서 주간사 정보 추출 (항상 #F1F4F7 색상)
        ipo_info_table = soup.find('table', summary='공모정보')
        if ipo_info_table:
            ipo_info_data = _parse_table_to_dict(ipo_info_table, key_color='#F1F4F7')

        # 로직 분기: 새로운 통합 테이블 형식인지, 기존 분리된 형식인지 확인
        integrated_table = soup.find('table', summary='공모청약일정')
        if integrated_table:
            # 1. 새로운 형식: 통합 테이블에서 일정 정보 추출 (key 색상: #F5F5F2)
            print(" -> 통합 테이블 형식 페이지 발견")
            schedule_data = _parse_table_to_dict(integrated_table, key_color='#F5F5F2')
            # 통합 테이블의 데이터를 ipo_info_data에 병합 (주간사는 유지)
            ipo_info_data.update(_parse_table_to_dict(integrated_table, key_color='#F5F5F2'))
        else:
            # 2. 기존 형식: 분리된 테이블에서 일정 정보 추출 (key 색상: #F1F4F7)
            print(" -> 분리된 테이블 형식 페이지 발견")
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
        
        # '희망공모가액'에서 불필요한 텍스트 제거
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
            if ipo_info and ipo_info.get('종목명'):
                all_ipo_data.append(ipo_info)
            time.sleep(0.2)
        
        if not all_ipo_data: return None

        df = pd.DataFrame(all_ipo_data)
        print("공모주 상세 정보 스크래핑 완료.")
        return df

    except Exception as e:
        print(f"메인 페이지 스크래핑 중 오류 발생: {e}")
        return None

def check_and_send_notifications():
    """오늘 청약일 또는 상장일인 공모주를 확인하고 Telegram으로 알림을 보냅니다."""
    print(f"\n{'='*50}")
    print(f"🔔 알림 체크 시작: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")
    
    ipo_data = scrape_ipo_data()
    if ipo_data is None or ipo_data.empty:
        print("확인할 공모주 정보가 없습니다.")
        return
    
    today = datetime.date.today()
    notifications_sent = 0
    
    for index, row in ipo_data.iterrows():
        if pd.isna(row.get('종목명')): 
            continue
        
        company_name = row['종목명']
        price = row.get('확정공모가', '미정')
        underwriter = row.get('주간사', '미정')
        
        # 청약일 체크
        if pd.notna(row.get('공모청약일')):
            sub_dates = re.findall(r'(\d{4}\.\d{2}\.\d{2})', str(row['공모청약일']))
            if len(sub_dates) == 2:
                start_date = datetime.datetime.strptime(sub_dates[1], '%Y.%m.%d').date()
                
                if start_date == today:
                    message = f"""🎯 <b>[공모주 청약일]</b>

📌 종목명: {company_name}
💰 공모가: {price}
🏢 주관사: {underwriter}
📅 청약일: {row['공모청약일']}

오늘이 청약일입니다! 놓치지 마세요! 🚀"""
                    
                    if send_telegram_message(message):
                        notifications_sent += 1
        
        # 상장일 체크
        if pd.notna(row.get('상장일')):
            listing_date_str = re.search(r'(\d{4}[./]\d{2}[./]\d{2})', str(row['상장일']))
            if listing_date_str:
                listing_date_str_cleaned = listing_date_str.group(1).replace('/', '.')
                listing_date = datetime.datetime.strptime(listing_date_str_cleaned, '%Y.%m.%d').date()
                
                if listing_date == today:
                    message = f"""📈 <b>[공모주 상장일]</b>

📌 종목명: {company_name}
💰 공모가: {price}
🏢 주관사: {underwriter}
📅 상장일: {row['상장일']}

오늘 상장됩니다! 시세를 확인하세요! 📊"""
                    
                    if send_telegram_message(message):
                        notifications_sent += 1
    
    print(f"\n{'='*50}")
    print(f"✅ 총 {notifications_sent}개의 알림을 전송했습니다.")
    print(f"{'='*50}\n")

def main():
    """메인 실행 함수 - 스케줄러 설정"""
    print("🤖 IPO Telegram 알림 봇을 시작합니다...")
    print(f"📱 Chat ID: {TELEGRAM_CHAT_ID}")
    print(f"⏰ 청약일 알림: 매일 오전 9시")
    print(f"⏰ 상장일 알림: 매일 오전 8시 50분\n")
    
    # 즉시 한 번 실행 (테스트용)
    check_and_send_notifications()
    
    # 스케줄러 설정
    scheduler = BlockingScheduler()
    
    # 매일 오전 8시 50분에 실행 (상장일 체크)
    scheduler.add_job(check_and_send_notifications, 'cron', hour=8, minute=50)
    
    # 매일 오전 9시에 실행 (청약일 체크)
    scheduler.add_job(check_and_send_notifications, 'cron', hour=9, minute=0)
    
    print("⏰ 스케줄러가 시작되었습니다. (Ctrl+C로 종료)")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 프로그램을 종료합니다.")

if __name__ == '__main__':
    main()
