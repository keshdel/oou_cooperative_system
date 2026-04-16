import sqlite3

conn = sqlite3.connect('cooperative.db')
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE members ADD COLUMN user_id INTEGER REFERENCES users(id)")
    conn.commit()
    print("✅ Column 'user_id' added successfully to the members table.")
except sqlite3.OperationalError as e:
    if "duplicate column name" in str(e):
        print("ℹ️ Column 'user_id' already exists. No change needed.")
    else:
        print(f"❌ Error: {e}")

conn.close()