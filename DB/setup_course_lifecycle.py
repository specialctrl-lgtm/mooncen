from pathlib import Path

from db_utils import get_db_connection


def main():
    sql = Path(__file__).with_name("course_lifecycle.sql").read_text(encoding="utf-8")
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
        conn.commit()
    finally:
        conn.close()
    print("Course lifecycle schema applied.")


if __name__ == "__main__":
    main()
