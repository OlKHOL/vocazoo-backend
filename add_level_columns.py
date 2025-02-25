import sqlite3
import os

def add_level_columns():
    db_path = os.path.join(os.path.dirname(__file__), 'users.db')
    print(f"Trying to connect to database at: {db_path}")
    
    if not os.path.exists(db_path):
        print(f"Database file not found at: {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    try:
        # Check if columns exist
        c.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in c.fetchall()]
        
        # Add level column if not exists
        if 'level' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN level INTEGER DEFAULT 1')
            
        # Add exp column if not exists
        if 'exp' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN exp INTEGER DEFAULT 0')
            
        # Add badges column if not exists (JSON string to store badge IDs)
        if 'badges' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN badges TEXT DEFAULT "[]"')
            
        conn.commit()
        print("Successfully added level system columns")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    add_level_columns() 