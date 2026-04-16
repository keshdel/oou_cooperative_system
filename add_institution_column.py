import sqlite3

conn = sqlite3.connect('cooperative.db')
cursor = conn.cursor()

# Check if column exists
cursor.execute("PRAGMA table_info(investments)")
columns = [col[1] for col in cursor.fetchall()]

if 'institution' not in columns:
    cursor.execute("ALTER TABLE investments ADD COLUMN institution TEXT")
    print("✅ Added 'institution' column.")
else:
    print("ℹ️ 'institution' column already exists.")

conn.commit()
conn.close()