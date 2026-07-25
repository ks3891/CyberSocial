import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

try:
    cursor.execute("""
        ALTER TABLE posts
        ADD COLUMN image TEXT
    """)
    print("✅ Image column added successfully!")
except sqlite3.OperationalError as e:
    print("ℹ️", e)

conn.commit()
conn.close()