import sqlite3
import json

def update_word_database():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 기존 단어 상태 백업
        c.execute('CREATE TABLE IF NOT EXISTS word_status_backup AS SELECT * FROM word_status')
        
        # word_status 테이블 초기화
        c.execute('DELETE FROM word_status')
        
        # 새로운 단어 데이터베이스 로드
        from word_database import load_word_database
        words = load_word_database()
        
        # 중복 체크를 위한 집합
        seen_english = set()
        
        # 단어 추가 (중복 제외)
        for word in words:
            if word['english'].lower() not in seen_english:
                seen_english.add(word['english'].lower())
                c.execute('''
                    INSERT INTO word_status (english, korean, level, used)
                    VALUES (?, ?, ?, FALSE)
                ''', (word['english'], word['korean'], word['level']))
        
        # 변경사항 저장
        conn.commit()
        
        # 결과 확인
        c.execute('SELECT COUNT(*) FROM word_status')
        count = c.fetchone()[0]
        print(f"총 {count}개의 단어가 데이터베이스에 저장되었습니다.")
        
    except Exception as e:
        print(f"Error updating word database: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    update_word_database() 