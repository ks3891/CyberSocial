import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

columns = {
    "bio": "TEXT DEFAULT ''",
    "profile_image": "TEXT DEFAULT 'default_profile.png'",
    "cover_image": "TEXT DEFAULT 'default_cover.jpg'",
    "location": "TEXT DEFAULT ''",
    "website": "TEXT DEFAULT ''",
    "joined_date": "TEXT DEFAULT CURRENT_TIMESTAMP"
}

for column, datatype in columns.items():
    try:
        cursor.execute(f"ALTER TABLE users ADD COLUMN {column} {datatype}")
        print(f"✅ Added {column}")
    except sqlite3.OperationalError:
        print(f"⚠ {column} already exists")

conn.commit()
conn.close()

print("\nDatabase Updated Successfully!")