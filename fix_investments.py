import sqlite3

conn = sqlite3.connect('cooperative.db')
cursor = conn.cursor()

# Check if the column already exists
cursor.execute("PRAGMA table_info(investments)")
columns = [col[1] for col in cursor.fetchall()]

if 'investment_number' not in columns:
    # 1. Add the column without UNIQUE constraint
    cursor.execute("ALTER TABLE investments ADD COLUMN investment_number TEXT")
    print("✅ Added 'investment_number' column.")

    # 2. Create a unique index to enforce uniqueness for non‑null values
    try:
        cursor.execute("CREATE UNIQUE INDEX idx_investment_number ON investments(investment_number)")
        print("✅ Created unique index on investment_number.")
    except sqlite3.OperationalError as e:
        print(f"⚠️ Could not create unique index: {e}. (This is OK if there are duplicate NULLs.)")
else:
    print("ℹ️ 'investment_number' column already exists.")

conn.commit()
conn.close()
print("\n🎉 Fix applied. You can now add investments.")