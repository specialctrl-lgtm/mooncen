"""
Fix Branch GIS for branches that actually have courses
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from geopy.geocoders import ArcGIS
from DB.db_utils import get_db_cursor
import re
import time

geolocator = ArcGIS(user_agent="mooncen_fix")

def get_coordinates(query):
    try:
        time.sleep(0.5)  # Rate limit
        loc = geolocator.geocode(query)
        if loc:
            return loc.latitude, loc.longitude
    except Exception as e:
        print(f"  Error: {e}")
    return None, None

def fix_gis():
    with get_db_cursor() as cursor:
        # Get branches linked to courses that are missing lat/lon
        cursor.execute("""
            SELECT DISTINCT b.id, b.name, b.provider, b.address
            FROM branches b
            INNER JOIN courses c ON c.branch_id = b.id
            WHERE b.lat IS NULL
        """)
        rows = cursor.fetchall()
        print(f"Found {len(rows)} branches with courses but no coords")
        
        for row in rows:
            branch_id = row['id']
            name = row['name']
            provider = row['provider']
            address = row.get('address')
            
            print(f"\nProcessing: [{provider}] {name}")
            
            # Try address first if available
            if address:
                print(f"  Trying address: {address}")
                lat, lon = get_coordinates(address)
                if lat:
                    cursor.execute("UPDATE branches SET lat = %s, lon = %s WHERE id = %s", (lat, lon, branch_id))
                    print(f"  Updated via address: {lat}, {lon}")
                    continue
            
            # Clean and construct query
            clean_name = name
            match = re.search(r'([가-힣A-Za-z0-9]+점)', name)
            if match:
                clean_name = match.group(1)
            
            # Build provider-specific query
            if provider == 'EMART':
                query = f"이마트 {clean_name}"
            elif provider == 'LOTTE':
                query = f"롯데마트 {clean_name}"
            elif provider == 'HOMEPLUS':
                query = f"홈플러스 {clean_name}"
            else:
                query = clean_name
                
            print(f"  Query: {query}")
            lat, lon = get_coordinates(query)
            
            if lat:
                cursor.execute("UPDATE branches SET lat = %s, lon = %s WHERE id = %s", (lat, lon, branch_id))
                print(f"  SUCCESS: {lat}, {lon}")
            else:
                print("  FAILED to geocode")

if __name__ == "__main__":
    fix_gis()
    print("\nDone!")
