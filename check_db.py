import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

tables = cursor.execute("""
SELECT name FROM sqlite_master WHERE type='table'
""").fetchall()

print("TABLES:", tables)

for table in tables:
    print("\nTABLE:", table[0])
    schema = cursor.execute(f"PRAGMA table_info({table[0]})").fetchall()
    for col in schema:
        print(col)

conn.close()