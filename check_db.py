"""Quick MySQL check. Run: python check_db.py"""
import config
from db import ensure_database, get_db_connection

try:
    ensure_database()
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            print(f"Connected to MySQL {config.MYSQL_HOST}:{config.MYSQL_PORT}, database '{config.MYSQL_DATABASE}'")
            for table in ("users", "admins", "chat_logs", "chunks"):
                try:
                    c.execute(f"SELECT COUNT(*) AS n FROM {table}")
                    n = c.fetchone()["n"]
                    print(f"  {table}: {n} rows")
                except Exception:
                    print(f"  {table}: not created yet (start the app once with: python app.py)")
    finally:
        conn.close()
except Exception as e:
    print("FAILED:", e)