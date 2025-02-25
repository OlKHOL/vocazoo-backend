import sqlite3
import os

def add_profile_image_column():
    db_path = os.path.join(os.path.dirname(__file__), 'users.db')
    print(f"Trying to connect to database at: {db_path}")
    
    if not os.path.exists(db_path):
        print(f"Database file not found at: {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    try:
        # Check if column exists
        c.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in c.fetchall()]
        
        if 'profile_image' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN profile_image TEXT')
            conn.commit()
            print("Successfully added profile_image column")
        else:
            print("profile_image column already exists")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    add_profile_image_column() 