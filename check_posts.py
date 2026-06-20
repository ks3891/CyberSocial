import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

print("USERS:")
cursor.execute("SELECT * FROM users")
print(cursor.fetchall())

print("\nPOSTS:")
cursor.execute("SELECT * FROM posts")
print(cursor.fetchall())

conn.close()