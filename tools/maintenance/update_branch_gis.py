import sys
import os
import time
import re
from geopy.geocoders import ArcGIS

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_cursor
from Crawler.selenium_driver import build_chrome_driver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

# Initialize Geocoder
geolocator = ArcGIS(user_agent="mooncen_crawler")

def get_coordinates(address: str):
    try:
        location = geolocator.geocode(address, timeout=10)
        if location:
            return location.latitude, location.longitude
    except Exception as e:
        print(f"Geocoding error for {address}: {e}")
    return None, None

def update_branch_gis(provider: str, branch_name: str, lat: float, lon: float, address: str = None):
    """Update DB with GIS data"""
    try:
        with get_db_cursor() as cursor:
            # Try to match by provider and name (fuzzy match if needed)
            # Keep the '점' suffix because DB branch names include it.
            
            # Simple Exact Match first
            cursor.execute("""
                UPDATE branches 
                SET lat = %s, lon = %s, address = COALESCE(branches.address, %s)
                WHERE provider = %s AND name = %s
                RETURNING id
            """, (lat, lon, address, provider, branch_name))
            
            result = cursor.fetchone()
            if result:
                print(f"Updated {provider} {branch_name}: {lat}, {lon}")
                return True
            else:
                # Try adding '점' if missing
                if not branch_name.endswith('점'):
                    return update_branch_gis(provider, branch_name + '점', lat, lon, address)
                
                print(f"Branch not found in DB: {provider} {branch_name}")
                return False
                
    except Exception as e:
        print(f"DB Error for {branch_name}: {e}")
        return False

class GISCrawler:
    def __init__(self):
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--disable-dev-shm-usage')
        self.driver = build_chrome_driver(options)
        self.wait = WebDriverWait(self.driver, 10)

    def __del__(self):
        self.driver.quit()

    def crawl_emart(self):
        print("Crawling Emart GIS...")
        # 1. Get branches from DB
        branches = []
        with get_db_cursor() as cursor:
            cursor.execute("SELECT id, name FROM branches WHERE provider = 'EMART' AND lat IS NULL")
            branches = cursor.fetchall()
            
        print(f"Found {len(branches)} Emart branches to update.")
        
        url = "https://store.emart.com/branch/list.do"
        self.driver.get(url)
        time.sleep(3)
        
        for br in branches:
            name = br['name'].replace('이마트', '').replace('점', '').strip()
            print(f"Searching for {name}...")
            
            try:
                # Search Input
                # Try finding by placeholder or just standard potential IDs
                # Based on standard generic structure:
                search_input = None
                try:
                    search_input = self.driver.find_element(By.CSS_SELECTOR, "input[placeholder*='매장명']")
                except Exception:
                    # Fallback selectors
                    for sel in ["#searchKeyword", "#keyword", ".search-input", "input[name='keyword']"]:
                        try:
                            search_input = self.driver.find_element(By.CSS_SELECTOR, sel)
                            break
                        except Exception:
                            pass
                
                if search_input:
                    search_input.clear()
                    search_input.send_keys(name)
                    
                    # Search Button
                    try:
                        btn = self.driver.find_element(By.CSS_SELECTOR, ".search-btn, button[type='submit'], .btn-search")
                        btn.click()
                    except Exception:
                        search_input.submit()
                        
                    time.sleep(2)
                    
                    # Extract Coordinates
                    # Often hidden vars are updated: 'lat', 'lon', 'y_point', 'x_point'
                    lat = None
                    lon = None
                    
                    # Check Hidden Inputs
                    hidden_inputs = self.driver.find_elements(By.CSS_SELECTOR, "input[type='hidden']")
                    for h in hidden_inputs:
                        n = h.get_attribute('name')
                        v = h.get_attribute('value')
                        if n and 'lat' in n.lower() and v:
                            lat = float(v)
                        if n and 'lon' in n.lower() and v:
                            lon = float(v)
                        # Emart might use different names.
                        
                    if lat and lon:
                        update_branch_gis('EMART', br['name'], lat, lon)
                        continue
                        
            except Exception as e:
                print(f"Error searching {name}: {e}")
                
            # Fallback: Geocoding
            print(f"  > Fallback Geocoding for {br['name']}")
            
            # Clean name for better geocoding success
            # e.g. "강릉점경기)점" -> "강릉점"
            clean_name = br['name']
            match = re.search(r'([가-힣A-Za-z0-9]+점)', br['name'])
            if match:
                clean_name = match.group(1)
            
            # Emart prefix check
            query_name = clean_name
            if "이마트" not in query_name:
                query_name = f"이마트 {query_name}"
            
            print(f"    Querying: {query_name}")
            lat, lon = get_coordinates(query_name)
            
            print(f"    Geocode Result: {lat}, {lon}")
            
            if lat and lon:
                success = update_branch_gis('EMART', br['name'], lat, lon)
                print(f"    Update Success: {success}")
                time.sleep(1) # Rate limit protection

    def crawl_lotte(self):
        print("Crawling Lotte GIS...")
        with get_db_cursor() as cursor:
            cursor.execute("SELECT id, name FROM branches WHERE provider = 'LOTTE' AND lat IS NULL")
            branches = cursor.fetchall()
            
        print(f"Found {len(branches)} Lotte branches to update.")
        # Lotte Mart Search URL logic could be complex, so relying on Geocoding for now as primary for speed
        for br in branches:
            print(f"Processing Lotte {br['name']}...")
            # Fallback: Geocoding
            lat, lon = get_coordinates(f"롯데마트 {br['name']}")
            if lat and lon:
                update_branch_gis('LOTTE', br['name'], lat, lon)
                time.sleep(1)

    def crawl_homeplus(self):
        print("Crawling Homeplus GIS...")
        with get_db_cursor() as cursor:
            cursor.execute("SELECT id, name FROM branches WHERE provider = 'HOMEPLUS' AND lat IS NULL")
            branches = cursor.fetchall()

        print(f"Found {len(branches)} Homeplus branches to update.")
        for br in branches:
             print(f"Processing Homeplus {br['name']}...")
             lat, lon = get_coordinates(f"홈플러스 {br['name']}")
             if lat and lon:
                 update_branch_gis('HOMEPLUS', br['name'], lat, lon)
                 time.sleep(1)

    def run(self):
        self.crawl_emart()
        self.crawl_lotte()
        self.crawl_homeplus()

if __name__ == "__main__":
    crawler = GISCrawler()
    crawler.run()
