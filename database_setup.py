from flask import Flask
from models import db, User, WordSet, Word, TestResult
from flask_migrate import Migrate
from datetime import datetime
from config import get_config
import json
import os

def load_word_database():
    """word_database.py 파일에서 단어 데이터를 로드합니다."""
    words = []
    seen_english = set()  # 중복 체크를 위한 집합
    
    try:
        with open('word_database.py', 'r', encoding='utf-8') as file:
            for line in file:
                if "{'english':" in line:
                    try:
                        word_dict = eval(line.strip())
                        english = word_dict['english']
                        
                        # 중복된 영단어 건너뛰기
                        if english in seen_english:
                            continue
                            
                        seen_english.add(english)
                        word_dict['level'] = str(word_dict['level'])
                        words.append(word_dict)
                    except:
                        continue
    except Exception as e:
        print(f"Error loading word database: {e}")
    return words

def init_db():
    """
    이 함수는 Flask 애플리케이션 컨텍스트 내에서 실행되어야 합니다.
    예: with app.app_context(): init_db()
    """
    # 모든 테이블 생성
    db.create_all()
    
    # Word 테이블에 초기 단어 데이터 삽입
    words = load_word_database()
    for word_data in words:
        try:
            # 이미 존재하는 단어인지 확인
            existing_word = Word.query.filter_by(english=word_data['english']).first()
            if not existing_word:
                new_word = Word(
                    english=word_data['english'],
                    korean=word_data['korean'],
                    level=word_data['level'],
                    used=False
                )
                db.session.add(new_word)
        except Exception as e:
            db.session.rollback()
            print(f"Error inserting word {word_data['english']}: {e}")
    
    try:
        db.session.commit()
        print("Word data initialized successfully!")
    except Exception as e:
        db.session.rollback()
        print(f"Error committing word data: {e}")
    
    # 기본 관리자 계정 생성 (필요한 경우)
    try:
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            from werkzeug.security import generate_password_hash
            admin_user = User(
                username='admin',
                password=generate_password_hash('admin123'),  # 기본 비밀번호, 보안을 위해 변경 필요
                is_admin=True,
                level=10,
                exp=0,
                current_score=0,
                completed_tests=0,
                created_at=datetime.utcnow()
            )
            db.session.add(admin_user)
            db.session.commit()
            print("Admin user created successfully!")
    except Exception as e:
        db.session.rollback()
        print(f"Error creating admin user: {e}")
    
    # 기본 단어장 생성 (필요한 경우)
    try:
        active_word_set = WordSet.query.filter_by(is_active=True).first()
        if not active_word_set:
            # 랜덤 단어 30개 선택
            random_words = Word.query.order_by(db.func.random()).limit(30).all()
            
            if random_words:
                word_list = [
                    {'english': w.english, 'korean': w.korean, 'level': w.level} 
                    for w in random_words
                ]
                
                new_word_set = WordSet(
                    words=word_list,
                    is_active=True,
                    created_at=datetime.utcnow()
                )
                db.session.add(new_word_set)
                db.session.commit()
                print("Initial word set created successfully!")
    except Exception as e:
        db.session.rollback()
        print(f"Error creating initial word set: {e}")
    
    print("Database initialized successfully!")

if __name__ == "__main__":
    # 이 스크립트를 직접 실행할 때는 Flask 앱을 생성하고 컨텍스트를 설정해야 함
    app = Flask(__name__)
    app.config.from_object(get_config())
    db.init_app(app)
    migrate = Migrate(app, db)
    
    with app.app_context():
        init_db()
        print("Database initialized successfully!") 