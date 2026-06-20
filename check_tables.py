import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

print("POSTS TABLE:")
cursor.execute("PRAGMA table_info(posts)")
print(cursor.fetchall())

print("\nCOMMENTS TABLE:")
cursor.execute("PRAGMA table_info(comments)")
print(cursor.fetchall())

conn.close()
