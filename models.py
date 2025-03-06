from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    level = db.Column(db.Integer, default=1)
    exp = db.Column(db.Integer, default=0)
    current_score = db.Column(db.Float, default=0)
    completed_tests = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    badges = db.Column(db.JSON, default=list)

class WordSet(db.Model):
    __tablename__ = 'word_sets'
    
    id = db.Column(db.Integer, primary_key=True)
    words = db.Column(db.JSON, nullable=False)
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class TestResult(db.Model):
    __tablename__ = 'test_results'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    word_set_id = db.Column(db.Integer, db.ForeignKey('word_sets.id'))
    score = db.Column(db.Float, nullable=False)
    solved_count = db.Column(db.Integer, nullable=False)
    wrong_answers = db.Column(db.JSON)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)

class WrongAnswer(db.Model):
    __tablename__ = 'wrong_answers'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    question = db.Column(db.String(200), nullable=False)
    correct_answer = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# 단어 데이터베이스 모델 추가
class Word(db.Model):
    __tablename__ = 'words'
    
    id = db.Column(db.Integer, primary_key=True)
    english = db.Column(db.String(100), unique=True, nullable=False)
    korean = db.Column(db.String(200), nullable=False)
    used = db.Column(db.Boolean, default=False)
    last_modified = db.Column(db.DateTime, default=datetime.utcnow) 