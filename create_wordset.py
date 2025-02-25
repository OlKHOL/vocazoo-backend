import sqlite3
import json
import random
from word_database import load_word_database

def initialize_db():
    """데이터베이스 초기화 및 필요한 테이블 생성"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # word_sets 테이블 생성
        c.execute('''
            CREATE TABLE IF NOT EXISTS word_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                words TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT FALSE,
                created_by INTEGER,
                FOREIGN KEY (created_by) REFERENCES users (id)
            )
        ''')
        
        conn.commit()
        print("데이터베이스가 초기화되었습니다.")
        return True
        
    except Exception as e:
        print(f"Error initializing database: {e}")
        conn.rollback()
        return False
        
    finally:
        conn.close()

def create_word_set():
    """새로운 단어장 생성"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 현재 사용 중인 단어들 확인
        c.execute('SELECT words FROM word_sets')
        used_words = set()
        for result in c.fetchall():
            words = json.loads(result[0])
            used_words.update(w['english'] for w in words)
        
        # word_database에서 단어 가져오기
        all_words = load_word_database()
        
        # 사용되지 않은 단어 30개 선택
        if used_words:
            available_words = [w for w in all_words if w['english'] not in used_words]
            # 사용 가능한 단어가 30개 미만이면 모든 단어 재사용
            if len(available_words) < 30:
                available_words = all_words
            
            selected_words = random.sample(available_words, 30)
        else:
            selected_words = random.sample(all_words, 30)
        
        # 새 단어장 생성
        words_json = json.dumps(selected_words)
        c.execute('UPDATE word_sets SET is_active = FALSE')
        c.execute('''
            INSERT INTO word_sets (words, created_at, is_active)
            VALUES (?, CURRENT_TIMESTAMP, TRUE)
        ''', (words_json,))
        
        conn.commit()
        print("새로운 단어장이 생성되었습니다.")
        return True
        
    except Exception as e:
        print(f"Error creating word set: {e}")
        conn.rollback()
        return False
        
    finally:
        conn.close()

if __name__ == "__main__":
    initialize_db() 