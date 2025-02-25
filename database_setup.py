import sqlite3
import json
from word_database import load_word_database

def load_word_database():
    words = []
    with open('word_database.py', 'r', encoding='utf-8') as file:
        for line in file:
            if "{'english':" in line:
                try:
                    word_dict = eval(line.strip())
                    word_dict['level'] = str(word_dict['level'])
                    words.append(word_dict)
                except:
                    continue
    return words

def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    # users 테이블
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            current_score FLOAT DEFAULT 0,
            completed_tests INTEGER DEFAULT 0
        )
    ''')
    
    # word_sets 테이블
    c.execute('''
        CREATE TABLE IF NOT EXISTS word_sets (
            id INTEGER PRIMARY KEY,
            words TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT FALSE,
            created_by INTEGER,
            visible_until TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users (id)
        )
    ''')
    
    # word_status 테이블
    c.execute('''
        CREATE TABLE IF NOT EXISTS word_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            english TEXT UNIQUE NOT NULL,
            korean TEXT NOT NULL,
            modified_korean TEXT,
            level INTEGER DEFAULT 1,
            used BOOLEAN DEFAULT FALSE,
            last_modified TIMESTAMP
        )
    ''')
    
    # test_results 테이블
    c.execute('''
        CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            solved_count INTEGER NOT NULL,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            wrong_answers TEXT,
            word_set_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (word_set_id) REFERENCES word_sets (id)
        )
    ''')
    
    # word_status 테이블에 초기 단어 데이터 삽입
    words = load_word_database()
    for word in words:
        try:
            c.execute('''
                INSERT OR IGNORE INTO word_status (english, korean, level)
                VALUES (?, ?, ?)
            ''', (word['english'], word['korean'], word['level']))
        except Exception as e:
            print(f"Error inserting word {word['english']}: {e}")
    
    # 단어장 공개 스케줄 테이블 추가
    c.execute('''
        CREATE TABLE IF NOT EXISTS word_set_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day_of_week INTEGER NOT NULL,
            time TEXT NOT NULL
        )
    ''')
    
    # 점수 초기화 히스토리 테이블 추가
    c.execute('''
        CREATE TABLE IF NOT EXISTS score_reset_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reset_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            previous_score FLOAT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # 기본 스케줄 추가
    schedule = [(1, '00:00'),  # 화요일
               (3, '00:00'),   # 목요일
               (4, '00:00'),   # 금요일
               (6, '00:00')]   # 일요일
    
    c.executemany('''
        INSERT OR IGNORE INTO word_set_schedule (day_of_week, time)
        VALUES (?, ?)
    ''', schedule)
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully!") 