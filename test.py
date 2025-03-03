from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import jwt_required, get_jwt_identity
import random
import time
from models import db, User, WordSet, TestResult, WrongAnswer, Word
import json
import datetime
import os
from functools import wraps
from difflib import SequenceMatcher
from config import get_config
import pandas as pd

# 설정 로드
config = get_config()

app = Flask(__name__)
app.config.from_object(config)

# CORS 설정
CORS(app, resources={r"/*": {"origins": config.CORS_ORIGINS}})

# 데이터베이스 초기화
db.init_app(app)

test_started = False
test = None

def admin_required(f):
    @wraps(f)
    @jwt_required()
    def decorated(*args, **kwargs):
        try:
            user_id = get_jwt_identity()
            user = User.query.get(user_id)
            
            if not user or not user.is_admin:
                return jsonify({'error': '관리자 권한이 필요합니다'}), 403
                
            return f(*args, **kwargs)
        except Exception as e:
            return jsonify({'error': str(e)}), 422
    return decorated

# 단어 데이터베이스 로드 함수
def load_word_database():
    words = []
    seen_english = set()  # 중복 체크를 위한 집합
    
    # word_database.py 파일이 존재하는지 확인
    if not os.path.exists('word_database.py'):
        print("Warning: word_database.py file not found. Using empty word list.")
        return words
    
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

# 전역 변수로 단어 데이터베이스 로드
word_database = load_word_database()

def initialize_word_status():
    """word_database.py의 단어들을 Word 테이블에 초기화"""
    # word_database가 비어있으면 초기화 건너뛰기
    if not word_database:
        print("No words to initialize. Skipping word status initialization.")
        return
        
    try:
        with app.app_context():
            # 모든 단어 새로 추가
            for word in word_database:
                # 이미 존재하는 단어인지 확인
                existing_word = Word.query.filter_by(english=word['english']).first()
                if not existing_word:
                    new_word = Word(
                        english=word['english'],
                        korean=word['korean'],
                        level=word['level'],
                        used=False
                    )
                    db.session.add(new_word)
            
            db.session.commit()
            print("Word status initialized successfully")
    except Exception as e:
        db.session.rollback()
        print(f"Error initializing word status: {e}")

def get_or_create_active_word_set():
    try:
        with app.app_context():
            # 현재 활성화된 단어장 확인
            active_word_set = WordSet.query.filter_by(is_active=True).first()
            if active_word_set:
                return json.loads(active_word_set.words)
            
            # 사용되지 않은 단어 30개 선택
            unused_words = Word.query.filter_by(used=False).order_by(db.func.random()).limit(30).all()
            
            if len(unused_words) < 30:
                # 모든 단어가 사용됐으면 리셋
                Word.query.update({Word.used: False})
                db.session.commit()
                
                unused_words = Word.query.order_by(db.func.random()).limit(30).all()
            
            # 단어 리스트 생성
            next_words = [
                {'english': w.english, 'korean': w.korean, 'level': w.level} 
                for w in unused_words
            ]
            
            # 선택된 단어들 used 표시
            for word in unused_words:
                word.used = True
            
            # 새 단어장 생성
            # 기존 활성 단어장 비활성화
            WordSet.query.filter_by(is_active=True).update({WordSet.is_active: False})
            
            # 새 단어장 생성
            new_word_set = WordSet(
                words=next_words,
                is_active=True,
                created_at=datetime.datetime.utcnow()
            )
            db.session.add(new_word_set)
            db.session.commit()
            
            return next_words
    except Exception as e:
        db.session.rollback()
        print(f"Error creating active word set: {e}")
        return []

class TestState:
    def __init__(self, word_set_id=None):
        self.score = 0
        self.start_time = None
        self.time_limit = 60
        self.current_word = None
        self.word_list = []
        
        if word_set_id:  # word_set_id가 있을 때만 단어장 불러오기
            try:
                with app.app_context():
                    word_set = WordSet.query.get(word_set_id)
                    
                    if not word_set:
                        raise Exception("단어장을 찾을 수 없습니다")
                        
                    word_set_data = word_set.words
                    if len(word_set_data) < 10:
                        raise Exception("단어장에 충분한 단어가 없습니다")
                        
                    self.word_list = random.sample(word_set_data, 10)  # 10개 랜덤 선택
            except Exception as e:
                print(f"Error loading word set: {e}")

    def start_test(self):
        self.start_time = time.time()
        self.score = 0
        self.current_word = None

    def get_next_question(self):
        if self.word_list:
            self.current_word = self.word_list.pop()
            return self.current_word
        return None

    def similar(self, a, b):
        # 문자열 유사도 계산 (0.0 ~ 1.0)
        return SequenceMatcher(None, a, b).ratio()

    def check_answer(self, question, answer):
        if not self.current_word:
            return jsonify({"result": "invalid"}), 400
            
        correct_answers = self.current_word["korean"].split(", ")  # 쉼표로 구분된 여러 정답
        user_answer = answer.strip()
        
        # 각 정답에 대해 검사
        for correct_answer in correct_answers:
            # 정확히 일치하는 경우
            if user_answer == correct_answer.strip():
                self.score += int(self.current_word["level"])
                return jsonify({"result": "correct"}), 200
                
            # 유사도 검사 (85% 이상 유사하면 정답 처리)
            similarity = self.similar(user_answer, correct_answer.strip())
            if similarity >= 0.85:
                self.score += int(self.current_word["level"])
                return jsonify({
                    "result": "correct",
                    "message": f"오타가 있지만 정답으로 인정됩니다! (정확한 답: {correct_answer})"
                }), 200
        
        # 모든 정답과 일치하지 않는 경우
        return jsonify({
            "result": "wrong",
            "correct_answer": self.current_word["korean"]  # 모든 가능한 정답 표시
        }), 200

    def get_final_score(self):
        elapsed_time = time.time() - self.start_time
        remaining_time = max(0, self.time_limit - elapsed_time)
        
        # 최종 점수 계산 시에만 보너스 배율 적용
        if remaining_time >= 10:
            return self.score * 1.5  # 10초 이상 남으면 1.5배
        elif remaining_time >= 5:
            return self.score * 1.2  # 5초 이상 남으면 1.2배
        return self.score  # 5초 미만이면 보너스 없음

    def is_time_over(self):
        return time.time() - self.start_time > self.time_limit

@app.route("/quiz/start", methods=["POST"])
@jwt_required()
def start_test():
    global test_started, test
    data = request.get_json()
    
    if not data or 'word_set_id' not in data:
        return jsonify({"error": "word_set_id가 필요합니다"}), 400
        
    word_set_id = data.get('word_set_id')
    
    try:
        test = TestState(word_set_id=word_set_id)
        test.start_test()
        test_started = True
        return jsonify({"message": "테스트가 시작되었습니다"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/quiz/question", methods=["GET"])
@jwt_required()
def get_question():
    global test_started, test
    
    if not test_started or not test:
        return jsonify({"error": "테스트가 시작되지 않았습니다"}), 400
        
    if test.is_time_over():
        return jsonify({"error": "시간이 초과되었습니다"}), 400
        
    question = test.get_next_question()
    if not question:
        return jsonify({"error": "더 이상 문제가 없습니다"}), 400
        
    return jsonify({
        "english": question["english"],
        "level": question["level"]
    }), 200

@app.route("/quiz/check", methods=["POST"])
@jwt_required()
def check_answer():
    global test_started, test
    
    if not test_started or not test:
        return jsonify({"error": "테스트가 시작되지 않았습니다"}), 400
        
    if test.is_time_over():
        return jsonify({"error": "시간이 초과되었습니다"}), 400
        
    data = request.get_json()
    if not data or 'answer' not in data or 'question' not in data:
        return jsonify({"error": "answer와 question이 필요합니다"}), 400
        
    return test.check_answer(data["question"], data["answer"])

@app.route("/quiz/end", methods=["POST"])
@jwt_required()
def end_test():
    global test_started, test
    
    if not test_started or not test:
        return jsonify({"error": "테스트가 시작되지 않았습니다"}), 400
        
    final_score = test.get_final_score()
    
    try:
        with app.app_context():
            # 사용자 정보 가져오기
            user_id = get_jwt_identity()
            user = User.query.get(user_id)
            if not user:
                return jsonify({"error": "사용자를 찾을 수 없습니다"}), 404
                
            # 테스트 결과 저장
            test_result = TestResult(
                user_id=user_id,
                score=final_score,
                solved_count=10,  # 10문제 고정
                completed_at=datetime.datetime.utcnow()
            )
            db.session.add(test_result)
            
            # 사용자 점수 업데이트
            user.current_score += final_score
            user.completed_tests += 1
            
            db.session.commit()
            
            test_started = False
            test = None
            
            return jsonify({
                "score": final_score,
                "message": "테스트가 종료되었습니다"
            }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/quiz/score", methods=["GET"])
@jwt_required()
def get_score():
    global test_started, test
    
    if not test_started or not test:
        return jsonify({"error": "테스트가 시작되지 않았습니다"}), 400
        
    if test.is_time_over():
        return jsonify({"error": "시간이 초과되었습니다"}), 400
        
    elapsed_time = time.time() - test.start_time
    remaining_time = max(0, test.time_limit - elapsed_time)
    
    return jsonify({
        "score": test.score,
        "remaining_time": remaining_time
    }), 200

@app.route("/admin/create_word_set", methods=["POST"])
@admin_required
def admin_create_word_set():
    try:
        with app.app_context():
            # 현재 존재하는 단어장의 ID 목록 조회
            existing_word_sets = WordSet.query.order_by(WordSet.id).all()
            existing_ids = [word_set.id for word_set in existing_word_sets]
            
            # 사용 가능한 가장 작은 ID 찾기
            next_id = 1
            for id in existing_ids:
                if id != next_id:
                    break
                next_id += 1
                
            # 현재 존재하는 단어장의 단어들만 체크
            used_words = set()
            for word_set in existing_word_sets:
                words = word_set.words
                used_words.update(w['english'] for w in words)
            
            # 사용되지 않은 단어 30개 선택
            unused_words = Word.query.filter(~Word.english.in_(used_words)).order_by(db.func.random()).limit(30).all()
            
            if len(unused_words) < 30:
                # 사용 가능한 단어가 부족하면 전체 단어에서 랜덤 선택
                unused_words = Word.query.order_by(db.func.random()).limit(30).all()
                
            # 단어 리스트 생성
            next_words = [
                {'english': w.english, 'korean': w.korean, 'level': w.level} 
                for w in unused_words
            ]
            
            # 새 단어장 생성
            new_word_set = WordSet(
                id=next_id,
                words=next_words,
                is_active=False
            )
            db.session.add(new_word_set)
            db.session.commit()
            
            return jsonify({"message": "새로운 단어장이 생성되었습니다", "id": next_id}), 200
            
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/admin/word_sets", methods=["GET"])
@admin_required
def get_word_sets():
    try:
        with app.app_context():
            word_sets = WordSet.query.order_by(WordSet.id).all()
            result = []
            
            for word_set in word_sets:
                result.append({
                    "id": word_set.id,
                    "words": word_set.words,
                    "is_active": word_set.is_active,
                    "created_at": word_set.created_at.isoformat()
                })
                
            return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/word_sets/<int:word_set_id>", methods=["DELETE"])
@admin_required
def delete_word_set(word_set_id):
    try:
        with app.app_context():
            word_set = WordSet.query.get(word_set_id)
            
            if not word_set:
                return jsonify({"error": "단어장을 찾을 수 없습니다"}), 404
                
            db.session.delete(word_set)
            db.session.commit()
            
            return jsonify({"message": "단어장이 삭제되었습니다"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/admin/upload_words", methods=["POST"])
@admin_required
def upload_words():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "파일이 없습니다"}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "선택된 파일이 없습니다"}), 400
            
        # 파일 확장자 확인
        if not (file.filename.endswith('.csv') or file.filename.endswith('.xlsx')):
            return jsonify({"error": "CSV 또는 Excel 파일만 업로드 가능합니다"}), 400
            
        # 파일 읽기
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
            
        # 필수 열 확인
        required_columns = ['english', 'korean', 'level']
        if not all(col in df.columns for col in required_columns):
            return jsonify({"error": "필수 열(english, korean, level)이 없습니다"}), 400
            
        # 데이터 검증 및 처리
        added = 0
        updated = 0
        
        for _, row in df.iterrows():
            try:
                # 데이터 검증
                if pd.isna(row['english']) or pd.isna(row['korean']) or pd.isna(row['level']):
                    continue
                    
                english = str(row['english']).strip()
                korean = str(row['korean']).strip()
                level = int(row['level'])
                
                if not english or not korean or not (1 <= level <= 5):
                    continue
                    
                # 단어 업데이트 또는 추가
                existing_word = Word.query.filter_by(english=english).first()
                if existing_word:
                    existing_word.korean = korean
                    existing_word.level = level
                    existing_word.last_modified = datetime.datetime.utcnow()
                    updated += 1
                else:
                    new_word = Word(
                        english=english,
                        korean=korean,
                        level=level,
                        used=False
                    )
                    db.session.add(new_word)
                    added += 1
                    
            except Exception as e:
                print(f"Error processing row: {e}")
                continue
                
        db.session.commit()
        
        return jsonify({
            "message": "단어 업로드가 완료되었습니다",
            "added": added,
            "updated": updated
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"파일 처리 중 오류가 발생했습니다: {str(e)}"}), 500

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", debug=True)
