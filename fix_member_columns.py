import sqlite3

conn = sqlite3.connect('cooperative.db')
cursor = conn.cursor()

# List of columns to add (if missing)
columns_to_add = [
    ('member_number', 'TEXT UNIQUE'),
    ('card_path', 'TEXT'),
    ('card_token', 'TEXT'),
    ('photo_path', 'TEXT')
]

for col_name, col_type in columns_to_add:
    try:
        cursor.execute(f"ALTER TABLE members ADD COLUMN {col_name} {col_type}")
        print(f"✅ Added column: {col_name}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print(f"ℹ️ Column {col_name} already exists")
        else:
            print(f"⚠️ Error adding {col_name}: {e}")

conn.commit()
conn.close()
print("🎉 All missing columns added.")