from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import sqlite3
from datetime import datetime, timedelta

def update_active_word_set():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 현재 활성화된 단어장 비활성화
        c.execute('UPDATE word_sets SET is_active = FALSE')
        
        # 아직 사용되지 않은 단어장 중 하나를 선택
        c.execute('''
            SELECT id FROM word_sets 
            WHERE visible_until IS NULL
            ORDER BY created_at ASC
            LIMIT 1
        ''')
        
        next_set = c.fetchone()
        if next_set:
            # 다음 교체 시간 계산
            now = datetime.now()
            next_time = now
            while True:
                next_time += timedelta(days=1)
                weekday = next_time.weekday()
                c.execute('SELECT time FROM word_set_schedule WHERE day_of_week = ?', 
                         (weekday,))
                if c.fetchone():
                    break
            
            # 선택된 단어장 활성화 및 공개 종료 시간 설정
            c.execute('''
                UPDATE word_sets 
                SET is_active = TRUE, visible_until = ?
                WHERE id = ?
            ''', (next_time.strftime('%Y-%m-%d %H:%M:%S'), next_set[0]))
            
        conn.commit()
    finally:
        conn.close()

def reset_user_scores():
    """매주 월요일 자정에 모든 사용자의 점수를 초기화"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # test_results 테이블에 reset_history 컬럼이 없다면 추가
        c.execute("""
            CREATE TABLE IF NOT EXISTS score_reset_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reset_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                previous_score FLOAT
            )
        """)
        
        # 현재 점수를 히스토리에 저장
        c.execute("""
            INSERT INTO score_reset_history (user_id, previous_score)
            SELECT user_id, score
            FROM test_results
            WHERE id IN (
                SELECT MAX(id)
                FROM test_results
                GROUP BY user_id
            )
        """)
        
        # 점수 초기화
        c.execute("""
            INSERT INTO test_results (user_id, score, solved_count, completed_at)
            SELECT DISTINCT user_id, 0, 0, CURRENT_TIMESTAMP
            FROM test_results
        """)
        
        conn.commit()
        print("User scores have been reset successfully")
    except Exception as e:
        print(f"Error resetting scores: {e}")
    finally:
        conn.close()

def init_scheduler():
    scheduler = BackgroundScheduler()
    
    # 매주 월요일 자정에 점수 초기화
    scheduler.add_job(
        reset_user_scores,
        CronTrigger(day_of_week='mon', hour=0, minute=0)
    )
    
    # 기존의 단어장 업데이트 스케줄
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT day_of_week, time FROM word_set_schedule')
    schedules = c.fetchall()
    conn.close()
    
    for day, time in schedules:
        hour, minute = map(int, time.split(':'))
        scheduler.add_job(
            update_active_word_set,
            CronTrigger(day_of_week=day, hour=hour, minute=minute)
        )
    
    scheduler.start() 