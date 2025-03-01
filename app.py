from flask import Flask, jsonify, request
from flask_cors import CORS
from auth import auth, token_required, verify_token
import json
import os
from test_manager import TestState
from functools import wraps
import time
import random
from scheduler import init_scheduler
from datetime import datetime
from level_system import LevelSystem
from flask_jwt_extended import JWTManager, create_access_token, get_jwt_identity, jwt_required
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, WordSet, TestResult, WrongAnswer, Word
from config import get_config
from flask_migrate import Migrate
import csv

app = Flask(__name__)
app.config.from_object(get_config())

# CORS 설정
CORS(app, resources={
    r"/*": {
        "origins": [os.getenv('CORS_ORIGIN', 'https://vocazoo.co.kr')],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# JWT 설정
jwt = JWTManager(app)
db.init_app(app)
migrate = Migrate(app, db)

# 스케줄러 초기화
scheduler = init_scheduler(app)

# 전역 변수
test_started = False
test = None

# 데이터베이스 초기화 (개발 환경에서만)
if os.getenv('FLASK_ENV') != 'production':
    with app.app_context():
        db.create_all()

# 라우트 등록
app.register_blueprint(auth, url_prefix='/auth')

def admin_required(f):
    @wraps(f)
    @jwt_required()
    def decorated(*args, **kwargs):
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if not user or not user.is_admin:
            return jsonify({"msg": "Admin privileges required"}), 403
        return f(*args, **kwargs)
    return decorated

def get_or_create_active_word_set():
    try:
        # 현재 활성화된 단어장 찾기
        active_word_set = WordSet.query.filter_by(is_active=True).first()
        
        # 활성화된 단어장이 있으면 반환
        if active_word_set:
            return active_word_set.words
            
        # 활성화된 단어장이 없으면 빈 단어장 반환
        # 관리자가 직접 단어를 추가해야 함
        return []
    except Exception as e:
        db.session.rollback()
        print(f"Error in get_or_create_active_word_set: {str(e)}")
        # 오류 발생 시 빈 단어장 반환
        return []

@app.route("/start_test", methods=["POST", "OPTIONS"])
def start_test():
    if request.method == "OPTIONS":
        return "", 200
        
    global test, test_started
    data = request.get_json()
    word_set_id = data.get("word_set_id")
    
    if not word_set_id:
        return jsonify({"error": "word_set_id is required"}), 400
        
    test = TestState(word_set_id)
    
    # 오답노트 테스트인 경우 단어 목록을 직접 설정
    if word_set_id == "wrong_answers":
        words = data.get("words", [])
        if not words:
            return jsonify({"error": "words are required for wrong answers test"}), 400
        test.set_words(words)
    
    test_started = True
    test.start_test()
    
    return jsonify({"message": "Test started successfully"}), 200

@app.route("/get_question", methods=["GET"])
def get_question():
    global test
    if not test_started or not test or not test.start_time:
        return jsonify({}), 200

    if test.is_time_over():
        return jsonify({"test_completed": True}), 200
        
    if len(test.word_list) == 0:
        return jsonify({"test_completed": True}), 200
        
    question = test.get_next_question()
    return jsonify(question), 200

@app.route("/check_answer", methods=["POST", "OPTIONS"])
def check_answer():
    if request.method == "OPTIONS":
        return "", 200
        
    global test
    data = request.get_json()
    
    if not test_started or not test.start_time:
        return jsonify({"result": "time_over"}), 200
        
    if test.is_time_over():
        return jsonify({"result": "time_over"}), 200
    
    question = data.get("question")
    answer = data.get("answer")
    
    if not question or not answer:
        return jsonify({"result": "invalid"}), 400
    
    if not test.current_word:
        return jsonify({"result": "invalid"}), 400
        
    return test.check_answer(question, answer)

@app.route("/get_score", methods=["GET", "OPTIONS"])
def get_score():
    if request.method == "OPTIONS":
        return "", 200
        
    if not test.start_time:
        return jsonify({
            "score": 0,
            "remaining_time": test.time_limit
        })
    
    return jsonify({
        "score": test.score,
        "remaining_time": round(max(0, test.time_limit - (time.time() - test.start_time)), 2)
    })

@app.route("/admin/edit_word_set/<int:set_id>", methods=["PUT"])
@admin_required
def edit_word_set(set_id):
    data = request.get_json()
    words = data.get('words')
    
    if not words:
        return jsonify({"error": "단어 목록이 필요합니다"}), 400
        
    try:
        # 단어장 존재 여부 확인
        word_set = WordSet.query.get(set_id)
        if not word_set:
            return jsonify({"error": "단어장을 찾을 수 없습니다"}), 404
            
        # 단어장 업데이트
        word_set.words = words
        
        # 수정된 단어들 Word 테이블 업데이트
        for word_data in words:
            word = Word.query.filter_by(english=word_data['english']).first()
            if word:
                word.korean = word_data['korean']
                word.level = word_data['level']
                word.last_modified = datetime.utcnow()
        
        db.session.commit()
        return jsonify({
            "message": "단어장이 수정되었습니다",
            "updated_words": words
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/admin/export_word_sets", methods=["GET"])
@admin_required
def export_word_sets():
    try:
        word_sets = WordSet.query.order_by(WordSet.id).all()
        
        with open('word_sets.py', 'w', encoding='utf-8') as file:
            file.write("# 단어장 목록\n\n")
            for word_set in word_sets:
                file.write(f"# 단어장 #{word_set.id}\n")
                for word in word_set.words:
                    file.write(f"{{ 'english': '{word['english']}', 'korean': '{word['korean']}', 'level': '{word['level']}' }},")
                    file.write("\n")  # 각 단어 다음에 줄바꿈
                file.write("\n")  # 단어장 구분을 위한 빈 줄
        
        return jsonify({"message": "단어장이 word_sets.py 파일로 저장되었습니다"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/get_current_word_set", methods=["GET"])
@token_required
def get_current_word_set():
    try:
        words = get_or_create_active_word_set()
        return jsonify({
            "words": words,
            "total_count": len(words)
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/get_word_set_history", methods=["GET"])
@token_required
def get_word_set_history():
    try:
        # 최근 10개 단어장 조회
        word_sets = WordSet.query.order_by(WordSet.created_at.desc()).limit(10).all()
        
        history = [{
            'id': ws.id,
            'words': ws.words,
            'created_at': ws.created_at.isoformat() if ws.created_at else None,
            'is_active': ws.is_active
        } for ws in word_sets]
        
        return jsonify(history), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/word_set/<int:set_id>", methods=["GET"])
@token_required
def get_word_set(set_id):
    try:
        word_set = WordSet.query.get(set_id)
        
        if not word_set:
            return jsonify({"error": "단어장을 찾을 수 없습니다"}), 404
            
        return jsonify({
            "id": set_id,
            "words": word_set.words
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/word_sets", methods=["GET"])
@token_required
def get_word_sets_list():
    try:
        # 모든 단어장 조회
        word_sets_query = WordSet.query.order_by(WordSet.created_at.desc()).all()
        
        word_sets = []
        for ws in word_sets_query:
            words = ws.words
            word_sets.append({
                'id': ws.id,
                'preview': words[:5] if words else [],  # 미리보기로 5개만
                'created_at': ws.created_at.isoformat() if ws.created_at else None,
                'is_active': ws.is_active,
                'total_words': len(words) if words else 0,
                'level_distribution': {
                    'level1': len([w for w in words if w.get('level') == '1']),
                    'level2': len([w for w in words if w.get('level') == '2']),
                    'level3': len([w for w in words if w.get('level') == '3'])
                }
            })
        
        return jsonify(word_sets), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/delete_word_set/<int:set_id>", methods=["DELETE"])
@admin_required
def admin_delete_word_set(set_id):
    try:
        word_set = WordSet.query.get(set_id)
        if not word_set:
            return jsonify({"error": "단어장을 찾을 수 없습니다"}), 404
            
        db.session.delete(word_set)
        db.session.commit()
        return jsonify({'message': '단어장이 삭제되었습니다'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/get_final_score", methods=["GET", "OPTIONS"])
def get_final_score():
    if request.method == "OPTIONS":
        return "", 200
    
    final_score = test.get_final_score()
    remaining_time = max(0, test.time_limit - (time.time() - test.start_time))
    
    return jsonify({
        "final_score": round(final_score, 2),
        "base_score": test.score,
        "remaining_time": round(remaining_time, 2)
    })

@app.route("/restart_test", methods=["POST", "OPTIONS"])
def restart_test():
    if request.method == "OPTIONS":
        return "", 200
    
    global test, test_started
    test = TestState()
    test.start_test()
    test_started = True
    return jsonify({"message": "Test restarted"}), 200

@app.route("/save_test_result", methods=["POST"])
@token_required
def save_test_result():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    try:
        # 기존 테스트 결과 저장 로직
        test.save_result(user_id)
        
        # 경험치 계산 및 레벨 업데이트
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "사용자를 찾을 수 없습니다"}), 404
        
        # 점수에 따른 경험치 계산
        gained_exp = LevelSystem.calculate_test_exp(test.score, user.level)
        new_level, new_exp, level_up = LevelSystem.process_exp_gain(
            user.level, user.exp, gained_exp
        )
        
        # 새로운 뱃지 확인
        new_badge = None
        if level_up:
            new_badge = LevelSystem.check_badge_unlock(new_level)
            if new_badge:
                badges = user.badges if user.badges else []
                badges.append(new_badge)
                user.badges = badges
        
        # 레벨과 경험치 업데이트
        user.level = new_level
        user.exp = new_exp
        db.session.commit()
        
        return jsonify({
            "message": "테스트 결과가 저장되었습니다",
            "exp_gained": gained_exp,
            "level_up": level_up,
            "new_badge": new_badge
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Error saving test result: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/word_set/current", methods=["GET"])
@token_required
def get_current_word_set_detail():
    try:
        # 현재 활성화된 단어장 조회
        current_set = WordSet.query.filter_by(is_active=True).first()
        
        if not current_set:
            return jsonify({
                "message": "현재 활성화된 단어장이 없습니다"
            }), 404
        
        words = current_set.words
        # 단어 목록을 레벨별로 정렬
        sorted_words = sorted(words, key=lambda x: int(x['level']))
        
        # 생성자 정보 조회
        creator_name = None
        if hasattr(current_set, 'created_by') and current_set.created_by:
            creator = User.query.get(current_set.created_by)
            if creator:
                creator_name = creator.username
        
        return jsonify({
            "id": current_set.id,
            "words": sorted_words,
            "created_at": current_set.created_at.isoformat() if current_set.created_at else None,
            "created_by": creator_name,
            "total_count": len(words),
            "level_distribution": {
                "level1": len([w for w in words if w['level'] == '1']),
                "level2": len([w for w in words if w['level'] == '2']),
                "level3": len([w for w in words if w['level'] == '3'])
            }
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/create_word_set", methods=["POST"])
@admin_required
def create_word_set():
    try:
        # 현재 존재하는 단어장의 ID 목록 조회
        existing_word_sets = WordSet.query.order_by(WordSet.id).all()
        existing_ids = [word_set.id for word_set in existing_word_sets]
        
        # 사용 가능한 가장 작은 ID 찾기
        next_id = 1
        for id in existing_ids:
            if id != next_id:
                break
            next_id += 1
            
        # 현재 단어장에 사용된 단어들 확인
        used_words = set()
        for word_set in existing_word_sets:
            words = word_set.words
            used_words.update(w['english'] for w in words)
        
        # 사용되지 않은 단어 30개 선택 (가능한 경우)
        query = Word.query
        if used_words:
            query = query.filter(~Word.english.in_(used_words))
        
        unused_words = query.order_by(db.func.random()).limit(30).all()
        
        if len(unused_words) < 30:
            # 사용 가능한 단어가 부족하면 전체 단어에서 랜덤 선택
            unused_words = Word.query.order_by(db.func.random()).limit(30).all()
            
        # 단어 리스트 생성
        next_words = [
            {'english': w.english, 'korean': w.korean, 'level': str(w.level)} 
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

@app.route("/update_username", methods=["POST"])
@token_required
def update_username():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    data = request.get_json()
    new_username = data.get('username')
    
    if not new_username:
        return jsonify({"message": "새로운 사용자명이 필요합니다"}), 400
    
    try:
        # 이미 사용 중인 사용자명인지 확인
        existing_user = User.query.filter(User.username == new_username, User.id != user_id).first()
        if existing_user:
            return jsonify({"message": "이미 사용 중인 사용자명입니다"}), 400
        
        # 사용자 찾기
        user = User.query.get(user_id)
        if not user:
            return jsonify({"message": "사용자를 찾을 수 없습니다"}), 404
            
        # 사용자명 업데이트
        user.username = new_username
        db.session.commit()
        
        return jsonify({"message": "사용자명이 변경되었습니다"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error updating username: {e}")
        return jsonify({"message": "사용자명 변경 중 오류가 발생했습니다"}), 500

@app.route("/account/info", methods=["GET"])
@token_required
def get_account_info():
    try:
        token = request.headers.get('Authorization')
        user_id = verify_token(token)
        
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        # 사용자 정보 반환
        return jsonify({
            "username": user.username,
            "level": user.level,
            "exp": user.exp,
            "is_admin": user.is_admin,
            "badges": user.badges if user.badges else []
        }), 200
    except Exception as e:
        print(f"Error getting account info: {e}")
        return jsonify({"error": "Failed to get account info"}), 500

@app.route("/get_available_words", methods=["GET"])
@admin_required
def get_available_words():
    try:
        words_query = Word.query.order_by(Word.level, Word.english).all()
        words = [
            {'english': w.english, 'korean': w.korean, 'level': w.level} 
            for w in words_query
        ]
        return jsonify({'words': words}), 200
    except Exception as e:
        print(f"Error getting available words: {e}")
        return jsonify({"error": "Failed to get available words"}), 500

@app.route("/wrong_answers", methods=["GET"])
@token_required
def get_wrong_answers():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    try:
        # Check user level first
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        # Return empty list if user level is below 3
        if user.level < 3:
            return jsonify([]), 200
            
        # 현재 활성화된 단어장의 ID를 가져옴
        current_word_set = WordSet.query.filter_by(is_active=True).first()
        if not current_word_set:
            return jsonify([]), 200

        # 현재 단어장에 대한 모든 오답을 가져옴 (중복 제거)
        results = TestResult.query.filter_by(
            user_id=user_id, 
            word_set_id=current_word_set.id
        ).order_by(TestResult.completed_at.desc()).all()
        
        all_wrong_answers = set()
        
        for result in results:
            if result.wrong_answers:  # wrong_answers가 NULL이 아닌 경우
                wrong_answers = json.loads(result.wrong_answers)
                for wrong in wrong_answers:
                    # 튜플로 변환하여 set에 추가 (중복 제거)
                    wrong_tuple = (wrong['question'], wrong['correctAnswer'])
                    all_wrong_answers.add(wrong_tuple)
        
        # 다시 리스트 형태로 변환
        wrong_answers_list = [
            {'question': q, 'correctAnswer': a}
            for q, a in all_wrong_answers
        ]
        
        return jsonify(wrong_answers_list), 200
    except Exception as e:
        print(f"Error getting wrong answers: {e}")
        return jsonify({"error": "Failed to get wrong answers"}), 500

@app.route("/start_wrong_answers_test", methods=["POST"])
@token_required
def start_wrong_answers_test():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    data = request.get_json()
    
    try:
        # 클라이언트가 보낸 단어들로만 테스트 초기화
        test_words = data.get('words', [])
        if not test_words:
            return jsonify({"error": "테스트할 단어가 없습니다"}), 400
        
        global test
        test = TestState()
        test.word_list = [{
            'english': word['question'],
            'korean': word['correctAnswer'],
            'level': '3'
        } for word in test_words]
        test.start_test()
        return jsonify({"message": "테스트가 시작되었습니다"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/update_word_set", methods=["POST"])
@admin_required
def manual_update_word_set():
    try:
        # 랜덤하게 30개 단어 선택
        random_words = Word.query.order_by(db.func.random()).limit(30).all()
        
        # 단어 리스트 생성
        next_words = [
            {'english': w.english, 'korean': w.korean, 'level': w.level} 
            for w in random_words
        ]
        
        # 선택된 단어들 used 필드 업데이트
        for word in random_words:
            word.used = True
        
        # 기존 활성 단어장 비활성화
        WordSet.query.filter_by(is_active=True).update({WordSet.is_active: False})
        
        # 새 단어장 생성
        new_word_set = WordSet(
            words=next_words,
            is_active=True
        )
        db.session.add(new_word_set)
        db.session.commit()
        
        return jsonify({"message": "단어장이 성공적으로 교체되었습니다"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/score_reset_history", methods=["GET"])
@token_required
def get_score_reset_history():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    try:
        # 사용자의 최근 테스트 결과 중 점수가 0인 것을 가져옴 (스케줄러에 의한 점수 리셋)
        history_records = TestResult.query.filter_by(
            user_id=user_id, 
            score=0
        ).order_by(
            TestResult.completed_at.desc()
        ).limit(10).all()
        
        history = [{
            'reset_date': record.completed_at.isoformat() if record.completed_at else None,
            'previous_score': 0  # 점수 리셋 기록은 별도로 저장하지 않으므로 0으로 표시
        } for record in history_records]
        
        return jsonify(history), 200
    except Exception as e:
        print(f"Error getting score reset history: {e}")
        return jsonify({"error": "Failed to get score reset history"}), 500

@app.route("/admin/word_sets/<int:set_id>", methods=["DELETE"])
@admin_required
def delete_word_set(set_id):
    try:
        # 단어장 찾기
        word_set = WordSet.query.get(set_id)
        
        if not word_set:
            return jsonify({"error": "단어장을 찾을 수 없습니다"}), 404
            
        # 활성화된 단어장은 삭제 불가
        if word_set.is_active:
            return jsonify({"error": "활성화된 단어장은 삭제할 수 없습니다"}), 400
        
        # 단어장 삭제
        db.session.delete(word_set)
        db.session.commit()
        
        return jsonify({"message": "단어장이 삭제되었습니다"}), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting word set: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/update_wrong_answers", methods=["POST"])
@token_required
def update_wrong_answers():
    try:
        token = request.headers.get('Authorization')
        user_id = verify_token(token)
        data = request.get_json()
        
        if not data or 'wrong_answers' not in data:
            return jsonify({"error": "잘못된 요청입니다."}), 400
            
        # 현재 활성화된 단어장의 ID를 가져옴
        current_word_set = WordSet.query.filter_by(is_active=True).first()
        if not current_word_set:
            return jsonify({"error": "활성화된 단어장이 없습니다."}), 400

        # 가장 최근 테스트 결과의 wrong_answers 업데이트
        latest_test = TestResult.query.filter_by(
            user_id=user_id, 
            word_set_id=current_word_set.id
        ).order_by(TestResult.completed_at.desc()).first()
        
        if latest_test:
            latest_test.wrong_answers = json.dumps(data['wrong_answers'])
            db.session.commit()
            return jsonify({"message": "오답 정보가 저장되었습니다"}), 200
        else:
            return jsonify({"error": "테스트 결과를 찾을 수 없습니다."}), 404
        
    except Exception as e:
        db.session.rollback()
        print(f"Error in update_wrong_answers: {e}")
        return jsonify({"error": "서버 오류가 발생했습니다"}), 500

@app.route("/check_auth", methods=["GET"])
@token_required
def check_auth():
    return jsonify({"message": "Token is valid"}), 200

@app.route("/rankings", methods=["GET"])
@token_required
def get_rankings():
    try:
        token = request.headers.get('Authorization')
        current_user_id = verify_token(token)
        
        # 레벨 5 이상인 유저들의 랭킹 정보 가져오기
        ranked_users = db.session.query(
            User.username,
            User.current_score,
            User.level,
            db.func.row_number().over(order_by=User.current_score.desc()).label('rank')
        ).filter(User.level >= 5).all()
        
        # 현재 사용자의 랭킹 정보 가져오기
        current_user = User.query.get(current_user_id)
        if not current_user:
            return jsonify({"error": "User not found"}), 404
            
        # 현재 사용자의 랭킹 계산
        current_user_rank = db.session.query(
            db.func.count(User.id) + 1
        ).filter(
            User.current_score > current_user.current_score,
            User.level >= 5
        ).scalar()
        
        return jsonify({
            "rankings": [{
                "rank": rank,
                "username": username,
                "score": float(score),
                "level": level
            } for username, score, level, rank in ranked_users],
            "currentUser": {
                "rank": current_user_rank,
                "username": current_user.username,
                "score": float(current_user.current_score) if current_user.current_score else 0,
                "level": current_user.level
            },
            "isQualified": current_user.level >= 5
        }), 200
        
    except Exception as e:
        print(f"Error in get_rankings: {e}")
        return jsonify({"error": "랭킹 정보를 가져오는데 실패했습니다"}), 500

@app.route("/user/level", methods=["GET"])
@token_required
def get_user_level():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    try:
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        required_exp = LevelSystem.get_exp_for_level(user.level)
        progress = LevelSystem.get_level_progress(user.level, user.exp)
        
        return jsonify({
            "level": user.level,
            "current_exp": user.exp,
            "required_exp": required_exp,
            "progress": progress,
            "badges": user.badges if user.badges else []
        }), 200
    except Exception as e:
        print(f"Error getting user level: {e}")
        return jsonify({"error": "Failed to get user level"}), 500

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    
    if User.query.filter_by(username=data["username"]).first():
        return jsonify({"error": "Username already exists"}), 400
        
    hashed_password = generate_password_hash(data["password"])
    new_user = User(username=data["username"], password=hashed_password)
    
    db.session.add(new_user)
    db.session.commit()
    
    return jsonify({"message": "User created successfully"}), 201

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    user = User.query.filter_by(username=data["username"]).first()
    
    if user and check_password_hash(user.password, data["password"]):
        access_token = create_access_token(identity=user.id)
        return jsonify({
            "token": access_token,
            "is_admin": user.is_admin
        }), 200
    
    return jsonify({"error": "Invalid username or password"}), 401

@app.route("/admin/upload_words", methods=["POST"])
@admin_required
def upload_words():
    """CSV 파일에서 단어를 업로드하는 API"""
    if 'file' not in request.files:
        return jsonify({"error": "파일이 제공되지 않았습니다"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "선택된 파일이 없습니다"}), 400
        
    if not file.filename.endswith('.csv'):
        return jsonify({"error": "CSV 파일만 업로드 가능합니다"}), 400
    
    try:
        # 파일 내용 읽기
        content = file.read().decode('utf-8')
        csv_reader = csv.reader(content.splitlines())
        
        # 첫 번째 행은 헤더로 간주
        headers = next(csv_reader)
        
        # 필수 열 확인
        required_columns = ['english', 'korean', 'level']
        missing_columns = [col for col in required_columns if col not in headers]
        
        if missing_columns:
            return jsonify({
                "error": f"CSV 파일에 필수 열이 누락되었습니다: {', '.join(missing_columns)}"
            }), 400
            
        # 열 인덱스 찾기
        english_idx = headers.index('english')
        korean_idx = headers.index('korean')
        level_idx = headers.index('level')
        
        # 단어 추가
        added_count = 0
        updated_count = 0
        error_count = 0
        errors = []
        
        for row in csv_reader:
            if len(row) < max(english_idx, korean_idx, level_idx) + 1:
                error_count += 1
                continue
                
            english = row[english_idx].strip()
            korean = row[korean_idx].strip()
            
            try:
                level = int(row[level_idx].strip())
            except ValueError:
                level = 1  # 기본값
            
            if not english or not korean:
                error_count += 1
                continue
                
            try:
                # 기존 단어 확인
                existing_word = Word.query.filter_by(english=english).first()
                
                if existing_word:
                    # 기존 단어 업데이트
                    existing_word.korean = korean
                    existing_word.level = level
                    existing_word.last_modified = datetime.utcnow()
                    updated_count += 1
                else:
                    # 새 단어 추가
                    new_word = Word(
                        english=english,
                        korean=korean,
                        level=level,
                        used=False
                    )
                    db.session.add(new_word)
                    added_count += 1
                    
            except Exception as e:
                error_count += 1
                errors.append(f"{english}: {str(e)}")
        
        db.session.commit()
        
        return jsonify({
            "message": "단어 업로드 완료",
            "added": added_count,
            "updated": updated_count,
            "errors": error_count,
            "error_details": errors[:10]  # 처음 10개 오류만 반환
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/admin/words", methods=["GET"])
@admin_required
def get_words():
    """단어 목록을 페이지네이션으로 가져오는 API"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search = request.args.get('search', '')
    level = request.args.get('level', type=int)
    
    query = Word.query
    
    # 검색어가 있으면 필터링
    if search:
        query = query.filter(
            db.or_(
                Word.english.ilike(f'%{search}%'),
                Word.korean.ilike(f'%{search}%')
            )
        )
    
    # 레벨 필터링
    if level:
        query = query.filter_by(level=level)
    
    # 페이지네이션
    pagination = query.order_by(Word.english).paginate(page=page, per_page=per_page)
    
    words = [{
        'id': word.id,
        'english': word.english,
        'korean': word.korean,
        'level': word.level,
        'used': word.used,
        'last_modified': word.last_modified.isoformat() if word.last_modified else None
    } for word in pagination.items]
    
    return jsonify({
        'words': words,
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page
    }), 200

@app.route("/admin/words/<int:word_id>", methods=["PUT"])
@admin_required
def update_word(word_id):
    """단어 정보를 업데이트하는 API"""
    word = Word.query.get_or_404(word_id)
    data = request.get_json()
    
    if 'english' in data:
        # 영어 단어 변경 시 중복 체크
        if data['english'] != word.english:
            existing = Word.query.filter_by(english=data['english']).first()
            if existing:
                return jsonify({"error": "이미 존재하는 영어 단어입니다"}), 400
        word.english = data['english']
        
    if 'korean' in data:
        word.korean = data['korean']
        
    if 'level' in data:
        word.level = data['level']
        
    word.last_modified = datetime.utcnow()
    
    try:
        db.session.commit()
        return jsonify({
            'id': word.id,
            'english': word.english,
            'korean': word.korean,
            'level': word.level,
            'used': word.used,
            'last_modified': word.last_modified.isoformat()
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/admin/words/<int:word_id>", methods=["DELETE"])
@admin_required
def delete_word(word_id):
    """단어를 삭제하는 API"""
    word = Word.query.get_or_404(word_id)
    
    try:
        db.session.delete(word)
        db.session.commit()
        return jsonify({"message": "단어가 삭제되었습니다"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/get_word_sets", methods=["GET"])
@token_required
def get_word_sets():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    try:
        # 관리자 확인
        user = User.query.get(user_id)
        is_admin = user.is_admin if user else False
        
        # 단어장 조회
        if is_admin:
            word_sets = WordSet.query.order_by(WordSet.created_at.desc()).all()
        else:
            word_sets = WordSet.query.filter_by(is_active=True).limit(1).all()
            
        return jsonify([{
            'id': ws.id,
            'words': ws.words,
            'created_at': ws.created_at.isoformat() if ws.created_at else None,
            'is_active': ws.is_active
        } for ws in word_sets]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/test_history", methods=["GET"])
@token_required
def get_test_history():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    try:
        # 사용자의 테스트 결과 조회
        test_results = TestResult.query.filter_by(
            user_id=user_id
        ).order_by(
            TestResult.completed_at.desc()
        ).all()
        
        history = [{
            'id': result.id,
            'score': result.score,
            'solved_count': result.solved_count,
            'completed_at': result.completed_at.isoformat() if result.completed_at else None,
            'word_set_id': result.word_set_id
        } for result in test_results]
        
        return jsonify(history), 200
    except Exception as e:
        print(f"Error getting test history: {e}")
        return jsonify({"error": "Failed to get test history"}), 500

@app.route("/delete_test_record/<int:record_id>", methods=["DELETE"])
@token_required
def delete_test_record(record_id):
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    try:
        # 테스트 결과 조회
        test_result = TestResult.query.filter_by(
            id=record_id,
            user_id=user_id
        ).first()
        
        if not test_result:
            return jsonify({"error": "Test record not found"}), 404
            
        # 테스트 결과 삭제
        db.session.delete(test_result)
        db.session.commit()
        
        return jsonify({"message": "Test record deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting test record: {e}")
        return jsonify({"error": "Failed to delete test record"}), 500

@app.route("/check_admin", methods=["GET"])
@token_required
def check_admin():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({"is_admin": False}), 200
            
        return jsonify({"is_admin": user.is_admin}), 200
    except Exception as e:
        print(f"Error checking admin status: {e}")
        return jsonify({"error": "Failed to check admin status"}), 500

@app.route("/account", methods=["GET"])
@token_required
def get_account():
    # /account/info와 동일한 기능을 제공하는 엔드포인트
    return get_account_info()

# 자동 테이블 생성 (PostgreSQL 마이그레이션용)
def create_tables():
    with app.app_context():
        db.create_all()
        print("Tables created successfully!")
        
        # 관리자 계정 확인 및 생성
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            hashed_password = generate_password_hash('admin1234')
            admin = User(username='admin', password=hashed_password, is_admin=True, level=100)
            db.session.add(admin)
            db.session.commit()
            print("Admin account created successfully!")

# 첫 요청시 테이블 생성 (Flask 2.0 이상에서는 권장되지 않음)
# 대신 애플리케이션 초기화 시 create_tables 함수를 직접 호출
with app.app_context():
    try:
        create_tables()
    except Exception as e:
        print(f"Error creating tables: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)