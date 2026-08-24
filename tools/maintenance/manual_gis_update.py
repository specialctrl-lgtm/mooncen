"""
Direct update of remaining branch coordinates
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_cursor

# Manual coordinates for branches that failed geocoding
MANUAL_COORDS = {
    "가든5점": (37.478308, 127.11905),  # 이마트 가든파이브점
    "롯데문화센터 성인강좌": (37.5665, 126.9780),  # Default Seoul
    "롯데문화센터 영·유아강좌": (37.5665, 126.9780),  # Default Seoul
    "롯데문화센터 아동강좌": (37.5665, 126.9780),  # Default Seoul
    "롯데문화센터 시니어강좌": (37.5665, 126.9780),  # Default Seoul
}

def update_manual():
    with get_db_cursor() as cursor:
        for name, (lat, lon) in MANUAL_COORDS.items():
            cursor.execute(
                "UPDATE branches SET lat = %s, lon = %s WHERE name LIKE %s AND lat IS NULL",
                (lat, lon, f"%{name}%")
            )
            print(f"Updated {name}: {lat}, {lon}")
        
        # Check result
        cursor.execute("SELECT COUNT(*) FROM branches WHERE lat IS NOT NULL")
        count = cursor.fetchone()[0]
        print(f"\nTotal branches with coords: {count}")

if __name__ == "__main__":
    update_manual()
