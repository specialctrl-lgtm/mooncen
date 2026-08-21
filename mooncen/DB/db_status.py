"""
Simple DB table check
"""
import sys
import os

from psycopg2 import sql

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_cursor

print("Checking tables...")

with get_db_cursor() as cursor:
    # Check extensions
    cursor.execute("SELECT extname FROM pg_extension ORDER BY extname;")
    extensions = cursor.fetchall()
    print("\nExtensions:")
    for ext in extensions:
        print(f"  - {ext['extname']}")
    
    # Check tables
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)
    tables = cursor.fetchall()
    print(f"\nTables ({len(tables)}):")
    for table in tables:
        print(f"  - {table['table_name']}")
        
        # Count rows
        cursor.execute(
            sql.SQL("SELECT COUNT(*) AS cnt FROM {}").format(
                sql.Identifier(table["table_name"]),
            )
        )
        count = cursor.fetchone()
        print(f"    Rows: {count['cnt']}")

print("\nDone!")
