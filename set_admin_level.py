import sqlite3
import os

def set_admin_level():
    db_path = os.path.join(os.path.dirname(__file__), 'users.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    try:
        c.execute('UPDATE users SET level = 100, exp = 0 WHERE is_admin = 1')
        conn.commit()
        print("Successfully updated admin level to 100")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    set_admin_level() 