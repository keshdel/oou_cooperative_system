import sqlite3
import psycopg2

# SQLite connection
sqlite_conn = sqlite3.connect("cooperative.db")
sqlite_conn.row_factory = sqlite3.Row
sqlite_cursor = sqlite_conn.cursor()

# PostgreSQL connection
pg_conn = psycopg2.connect(
    database="oou_accounting",
    user="postgres",
    password="Manager84",
    host="localhost",
    port="5432"
)

pg_cursor = pg_conn.cursor()

# Tables to migrate
tables = [
    "users",
    "members",
    "savings",
    "loans",
    "repayments",
    "investments",
    "honorarium",
    "expenses",
    "revenue",
    "settings",
    "notifications",
    "audit_log"
]

for table in tables:

    print(f"Migrating {table}...")

    sqlite_cursor.execute(f"SELECT * FROM {table}")
    rows = sqlite_cursor.fetchall()

    if not rows:
        print(f"{table} empty, skipping.")
        continue

    columns = rows[0].keys()
    column_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))

    insert_query = f"""
    INSERT INTO {table} ({column_list})
    VALUES ({placeholders})
    """

    for row in rows:
        pg_cursor.execute(insert_query, tuple(row))

    pg_conn.commit()

print("Migration completed successfully!")

sqlite_conn.close()
pg_conn.close()