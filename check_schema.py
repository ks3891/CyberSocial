import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

tables = ["users", "posts", "comments"]

for table in tables:
    print(f"\n===== {table.upper()} =====")
    cursor.execute(f"PRAGMA table_info({table})")
    for row in cursor.fetchall():
        print(row)

conn.close()
