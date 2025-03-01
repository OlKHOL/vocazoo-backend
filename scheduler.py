from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
from flask import current_app
from models import db, WordSet, TestResult, User

def update_active_word_set():
    with current_app.app_context():
        try:
            # 현재 활성화된 단어장 비활성화
            WordSet.query.filter_by(is_active=True).update({WordSet.is_active: False})
            
            # 아직 사용되지 않은 단어장 중 하나를 선택
            next_set = WordSet.query.filter(
                WordSet.visible_until.is_(None)
            ).order_by(WordSet.created_at.asc()).first()
            
            if next_set:
                # 다음 교체 시간 계산
                now = datetime.now()
                next_time = now
                
                # 스케줄 정보를 데이터베이스에서 가져오는 로직은 별도로 구현 필요
                # 여기서는 간단히 하루 후로 설정
                next_time += timedelta(days=1)
                
                # 선택된 단어장 활성화 및 공개 종료 시간 설정
                next_set.is_active = True
                next_set.visible_until = next_time
                
            db.session.commit()
            print("Word set updated successfully")
        except Exception as e:
            db.session.rollback()
            print(f"Error updating word set: {e}")

def reset_user_scores():
    """매주 월요일 자정에 모든 사용자의 점수를 초기화"""
    with current_app.app_context():
        try:
            # 모든 사용자의 점수를 0으로 초기화
            users = User.query.all()
            for user in users:
                # 점수 초기화 전 현재 점수 저장
                previous_score = user.current_score
                user.current_score = 0
                
                # 새로운 테스트 결과 생성 (점수 초기화 기록)
                new_result = TestResult(
                    user_id=user.id,
                    score=0,
                    solved_count=0,
                    completed_at=datetime.utcnow()
                )
                db.session.add(new_result)
            
            db.session.commit()
            print("User scores have been reset successfully")
        except Exception as e:
            db.session.rollback()
            print(f"Error resetting scores: {e}")

def init_scheduler(app):
    scheduler = BackgroundScheduler()
    
    # 매주 월요일 자정에 점수 초기화
    scheduler.add_job(
        reset_user_scores,
        CronTrigger(day_of_week='mon', hour=0, minute=0)
    )
    
    # 단어장 업데이트 스케줄 설정
    # 여기서는 간단히 매일 자정에 업데이트하도록 설정
    scheduler.add_job(
        update_active_word_set,
        CronTrigger(hour=0, minute=0)
    )
    
    scheduler.start()
    return scheduler 