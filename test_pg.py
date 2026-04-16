import psycopg2

conn = psycopg2.connect(
    database="oou_accounting",
    user="postgres",
    password="Manager84",
    host="localhost",
    port="5432"
)

print("PostgreSQL connection successful!")

conn.close()