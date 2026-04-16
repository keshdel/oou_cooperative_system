import sqlite3

conn = sqlite3.connect('cooperative.db')
cursor = conn.cursor()

# 1. Add member_number column (without UNIQUE)
try:
    cursor.execute("ALTER TABLE members ADD COLUMN member_number TEXT")
    print("✅ Added member_number column")
except sqlite3.OperationalError as e:
    if "duplicate column name" in str(e):
        print("ℹ️ member_number column already exists")
    else:
        print(f"⚠️ Error: {e}")

# 2. Populate missing member numbers
cursor.execute("SELECT id FROM members WHERE member_number IS NULL")
rows = cursor.fetchall()
for (member_id,) in rows:
    new_number = f"OOU/{member_id:04d}"
    cursor.execute("UPDATE members SET member_number = ? WHERE id = ?", (new_number, member_id))
    print(f"Updated member {member_id} -> {new_number}")

# 3. Add unique index (enforces uniqueness without column constraint)
try:
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_member_number ON members(member_number)")
    print("✅ Created unique index on member_number")
except sqlite3.OperationalError as e:
    print(f"⚠️ Index error: {e}")

conn.commit()
conn.close()
print("🎉 Done. Member numbers are ready.")