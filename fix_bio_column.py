import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cursor = conn.cursor()

for col in ["bio", "profile_image", "cover_image", "location", "website"]:
    cursor.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} TEXT")

conn.commit()

cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users'")
print([row[0] for row in cursor.fetchall()])

conn.close()
