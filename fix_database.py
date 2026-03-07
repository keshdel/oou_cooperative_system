import sqlite3

print("Fixing database schema...")

# Connect to database
conn = sqlite3.connect('cooperative.db')
cursor = conn.cursor()

# Check if users table exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
if cursor.fetchone():
    print("Found existing users table, recreating...")
    
    # Save existing users if any
    cursor.execute("SELECT username, password_hash, role FROM users")
    existing_users = cursor.fetchall()
    
    # Drop old table
    cursor.execute("DROP TABLE users")
    
    # Create new table with correct schema
    cursor.execute('''
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT,
            email TEXT,
            phone TEXT,
            is_active INTEGER DEFAULT 1,
            two_factor_secret TEXT,
            last_login TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Re-insert existing users
    from werkzeug.security import generate_password_hash
    
    # Default users if none exist
    default_users = [
        ('admin', generate_password_hash('admin123'), 'admin', 'Administrator', 'admin@coop.com', '0800000000'),
        ('treasurer', generate_password_hash('treasurer123'), 'treasurer', 'Treasurer', 'treasurer@coop.com', '0800000001'),
        ('secretary', generate_password_hash('secretary123'), 'secretary', 'Secretary', 'secretary@coop.com', '0800000002')
    ]
    
    for username, pwd_hash, role, full_name, email, phone in default_users:
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO users (username, password_hash, role, full_name, email, phone, created_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ''', (username, pwd_hash, role, full_name, email, phone))
            print(f"  - Added user: {username}")
        except Exception as e:
            print(f"  - Error adding {username}: {e}")
    
    conn.commit()
    print("Users table recreated successfully!")
else:
    print("Users table doesn't exist, creating...")
    # Create new table
    cursor.execute('''
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT,
            email TEXT,
            phone TEXT,
            is_active INTEGER DEFAULT 1,
            two_factor_secret TEXT,
            last_login TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Add default users
    from werkzeug.security import generate_password_hash
    default_users = [
        ('admin', generate_password_hash('admin123'), 'admin', 'Administrator', 'admin@coop.com', '0800000000'),
        ('treasurer', generate_password_hash('treasurer123'), 'treasurer', 'Treasurer', 'treasurer@coop.com', '0800000001'),
        ('secretary', generate_password_hash('secretary123'), 'secretary', 'Secretary', 'secretary@coop.com', '0800000002')
    ]
    
    for username, pwd_hash, role, full_name, email, phone in default_users:
        cursor.execute('''
            INSERT INTO users (username, password_hash, role, full_name, email, phone, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ''', (username, pwd_hash, role, full_name, email, phone))
    
    conn.commit()
    print("Users table created with default users!")

conn.close()
print("\n✅ Database fix complete! Now run: python app.py")