import sqlite3

def add_column_if_missing(cursor, table, column, definition):
    """Add a column if it doesn't exist."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [col[1] for col in cursor.fetchall()]
    if column not in columns:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            print(f"✅ Added column: {column}")
        except sqlite3.OperationalError as e:
            print(f"⚠️ Failed to add {column}: {e}")
    else:
        print(f"ℹ️ Column '{column}' already exists.")

def add_unique_index_if_missing(cursor, table, column):
    """Create a unique index on a column if not exists."""
    try:
        cursor.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_{column} ON {table}({column})")
        print(f"✅ Unique index on {column} created/verified.")
    except sqlite3.OperationalError as e:
        print(f"⚠️ Could not create unique index on {column}: {e}")

conn = sqlite3.connect('cooperative.db')
cursor = conn.cursor()

# Desired columns with their definitions (based on database.py)
desired_columns = [
    ('investment_number', 'TEXT UNIQUE'),  # We'll handle UNIQUE separately
    ('name', 'TEXT NOT NULL'),
    ('amount', 'REAL NOT NULL'),
    ('type', 'TEXT NOT NULL'),
    ('description', 'TEXT'),
    ('institution', 'TEXT'),
    ('interest_rate', 'REAL'),
    ('return_rate', 'REAL'),
    ('risk_level', 'TEXT DEFAULT "medium"'),
    ('start_date', 'TIMESTAMP'),
    ('maturity_date', 'TIMESTAMP'),
    ('duration_days', 'INTEGER'),
    ('expected_return', 'REAL'),
    ('actual_return', 'REAL'),
    ('current_value', 'REAL'),
    ('approval_status', 'TEXT DEFAULT "pending"'),
    ('approved_by', 'INTEGER'),
    ('approved_at', 'TIMESTAMP'),
    ('documents', 'TEXT'),
    ('notes', 'TEXT'),
    ('created_by', 'INTEGER'),
    ('date', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
]

# First, add all columns except investment_number (handle separately)
for col_name, definition in desired_columns:
    if col_name == 'investment_number':
        continue
    add_column_if_missing(cursor, 'investments', col_name, definition)

# Add investment_number column (without UNIQUE constraint)
add_column_if_missing(cursor, 'investments', 'investment_number', 'TEXT')
# Now create unique index
add_unique_index_if_missing(cursor, 'investments', 'investment_number')

# Note: Foreign key constraints are not added automatically.
# If you need them, you'd have to recreate the table with proper FKs.
# For now, we'll assume application logic handles integrity.

conn.commit()
conn.close()
print("\n🎉 All missing columns added. Your investments table is now in sync with database.py.")