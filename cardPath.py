import sqlite3
conn = sqlite3.connect('cooperative.db')
c = conn.cursor()
c.execute("ALTER TABLE members ADD COLUMN card_path TEXT")
c.execute("ALTER TABLE members ADD COLUMN card_token TEXT")
conn.commit()
conn.close()
print("Columns added.")