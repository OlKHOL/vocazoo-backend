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
import io

app = Flask(__name__)
app.config.from_object(get_config())

# CORS 설정
CORS(app, 
     resources={r"/*": {
         "origins": ["https://vocazoo.co.kr", "http://vocazoo.co.kr", "http://api.vocazoo.co.kr", "https://api.vocazoo.co.kr"],
         "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
         "allow_headers": ["Content-Type", "Authorization", "Accept", "Origin", "Content-Length"],
         "supports_credentials": True,
         "expose_headers": ["Content-Type", "Authorization"]
     }})

# OPTIONS 메서드에 대한 전역 핸들러 추가
@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    return '', 200, {
        'Access-Control-Allow-Origin': request.headers.get('Origin', '*'),
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization, Accept, Origin, Content-Length',
        'Access-Control-Allow-Credentials': 'true',
        'Access-Control-Max-Age': '86400'
    }

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

# 요청 로깅 미들웨어 추가
@app.before_request
def log_request_info():
    print("Request Method:", request.method)
    print("Request URL:", request.url)
    print("Request Headers:", dict(request.headers))
    if request.is_json:
        print("Request JSON:", request.get_json())
    elif request.form:
        print("Request Form:", dict(request.form))
    if request.files:
        print("Request Files:", request.files.keys())

# 오류 핸들러 추가
@app.errorhandler(422)
def handle_unprocessable_entity(error):
    print("422 Error occurred:", str(error))
    return jsonify({"error": str(error)}), 422

def admin_required(f):
    @wraps(f)
    @jwt_required()
    def decorated(*args, **kwargs):
        try:
            current_user_id = get_jwt_identity()
            if not current_user_id:
                return jsonify({"error": "Invalid token"}), 401
                
            user = User.query.get(current_user_id)
            if not user:
                return jsonify({"error": "User not found"}), 404
                
            if not user.is_admin:
                return jsonify({"error": "Admin privileges required"}), 403
                
            return f(*args, **kwargs)
        except Exception as e:
            print(f"Error in admin_required: {str(e)}")
            return jsonify({"error": "Token verification failed"}), 422
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
                    file.write(f"{{ 'english': '{word['english']}', 'korean': '{word['korean']}' }},")
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
                'total_words': len(words) if words else 0
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

        # 생성자 정보 조회
        creator_name = None
        if hasattr(current_set, 'created_by') and current_set.created_by:
            creator = User.query.get(current_set.created_by)
            if creator:
                creator_name = creator.username

        return jsonify({
            "id": current_set.id,
            "words": words,
            "created_at": current_set.created_at.isoformat() if current_set.created_at else None,
            "created_by": creator_name,
            "total_count": len(words)
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
            {'english': w.english, 'korean': w.korean} 
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
        words_query = Word.query.order_by(Word.english).all()
        words = [
            {'english': w.english, 'korean': w.korean} 
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
            'korean': word['correctAnswer']
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
            {'english': w.english, 'korean': w.korean} 
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

        # 레벨 5 이상이고 관리자가 아닌 유저들의 랭킹 정보 가져오기
        ranked_users = db.session.query(
            User.username,
            User.current_score,
            User.level,
            db.func.row_number().over(order_by=User.current_score.desc()).label('rank')
        ).filter(User.level >= 5, User.is_admin == False).all()

        # 현재 사용자의 랭킹 정보 가져오기
        current_user = User.query.get(current_user_id)
        if not current_user:
            return jsonify({"error": "User not found"}), 404

        # 현재 사용자가 관리자인 경우 랭킹에 표시하지 않지만 점수는 보여줌
        if current_user.is_admin:
            return jsonify({
                "rankings": [{
                    "rank": rank,
                    "username": username,
                    "score": float(score),
                    "level": level
                } for username, score, level, rank in ranked_users],
                "currentUser": {
                    "rank": "관리자",
                    "username": current_user.username,
                    "score": float(current_user.current_score) if current_user.current_score else 0,
                    "level": current_user.level
                },
                "isQualified": False
            }), 200

        # 현재 사용자의 랭킹 계산 (관리자 제외)
        current_user_rank = db.session.query(
            db.func.count(User.id) + 1
        ).filter(
            User.current_score > current_user.current_score,
            User.level >= 5,
            User.is_admin == False
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

@app.route("/register", methods=["GET", "POST"])
def register_redirect():
    """
    /register 경로로 들어오는 요청을 /auth/register로 리다이렉트합니다.
    이는 기존 코드와의 호환성을 위해 유지됩니다.
    """
    print("register_redirect 함수 호출됨, 메소드:", request.method)
    print("요청 데이터:", request.get_json())

    if request.method == "GET":
        return jsonify({"message": "Register page - Please use /auth/register endpoint"}), 200

    try:
        # auth.views.register() 대신 직접 auth 모듈의 함수 호출
        from auth import register as auth_register
        print("auth_register 함수 호출 전")
        result = auth_register()
        print("auth_register 함수 호출 후, 결과:", result)
        return result
    except Exception as e:
        print("register_redirect 오류:", str(e))
        return jsonify({"message": f"회원가입 처리 중 오류가 발생했습니다: {str(e)}"}), 500

@app.route("/login", methods=["GET", "POST"])
def login_redirect():
    """
    /login 경로로 들어오는 요청을 /auth/login으로 리다이렉트합니다.
    이는 기존 코드와의 호환성을 위해 유지됩니다.
    """
    if request.method == "GET":
        return jsonify({"message": "Login page - Please use /auth/login endpoint"}), 200

    # auth.views.login() 대신 직접 auth 모듈의 함수 호출
    from auth import login as auth_login
    return auth_login()


@app.route("/admin/words", methods=["GET"])
@admin_required
def get_words():
    """단어 목록을 페이지네이션으로 가져오는 API"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '')
        
        query = Word.query
        
        # 검색어가 있으면 필터링
        if search:
            query = query.filter(
                db.or_(
                    Word.english.ilike(f'%{search}%'),
                    Word.korean.ilike(f'%{search}%')
                ))
        
        # 페이지네이션
        pagination = query.order_by(Word.english).paginate(page=page, per_page=per_page)
        
        words = [{
            'id': word.id,
            'english': word.english,
            'korean': word.korean,
            'used': word.used,
            'last_modified': word.last_modified.isoformat() if word.last_modified else None
        } for word in pagination.items]
        
        return jsonify({
            'words': words,
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page
        }), 200
    except Exception as e:
        print(f"Error in get_words: {str(e)}")
        return jsonify({"error": str(e)}), 500

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


    word.last_modified = datetime.utcnow()

    try:
        db.session.commit()
        return jsonify({
            'id': word.id,
            'english': word.english,
            'korean': word.korean,
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

@app.route("/admin/upload_words", methods=["POST"])
@admin_required
def upload_words():
    print("[Upload] Starting file upload process...")
    
    if 'file' not in request.files:
        print("[Upload] Error: No file in request")
        return jsonify({"error": "파일이 제공되지 않았습니다"}), 400
        
    file = request.files['file']
    print(f"[Upload] Received file: {file.filename}")
    print(f"[Upload] Content Type: {file.content_type}")
    print(f"[Upload] File Headers: {dict(file.headers)}")
    
    if file.filename == '':
        print("[Upload] Error: Empty filename")
        return jsonify({"error": "파일이 선택되지 않았습니다"}), 400
        
    if not (file.filename.endswith('.csv') or file.filename.endswith('.txt')):
        print(f"[Upload] Error: Invalid file extension: {file.filename}")
        return jsonify({"error": "CSV 또는 TXT 파일만 업로드 가능합니다"}), 400
    
    try:
        print("[Upload] Reading file content...")
        file_content = file.read()
        print(f"[Upload] File size: {len(file_content)} bytes")
        
        if len(file_content) == 0:
            print("[Upload] Error: Empty file")
            return jsonify({
                "error": "파일이 비어있습니다",
                "details": ["파일에 내용이 없습니다"]
            }), 422
        
        if len(file_content) > 50 * 1024 * 1024:
            print("[Upload] Error: File too large")
            return jsonify({
                "error": "파일이 너무 큽니다",
                "details": ["파일 크기는 50MB를 초과할 수 없습니다"]
            }), 422
        
        # Try different encodings
        encodings = ['utf-8', 'cp949', 'euc-kr']
        content = None
        
        for encoding in encodings:
            try:
                content = file_content.decode(encoding)
                print(f"[Upload] Successfully decoded as {encoding}")
                break
            except UnicodeDecodeError:
                continue
        
        if content is None:
            print("[Upload] Error: Failed to decode file content")
            return jsonify({
                "error": "파일 인코딩이 올바르지 않습니다",
                "details": [
                    "파일을 UTF-8 또는 CP949(한글 Windows) 인코딩으로 저장해주세요",
                    "메모장에서 '다른 이름으로 저장' 시 인코딩을 'UTF-8'로 선택하세요"
                ]
            }), 422
        
        # Remove BOM if present
        if content.startswith('\ufeff'):
            content = content[1:]
            print("[Upload] Removed BOM from file")
        
        import csv
        from io import StringIO
        
        csv_file = StringIO(content)
        csv_reader = csv.reader(csv_file, delimiter=',', quotechar='"')
        
        lines = list(csv_reader)
        print(f"[Upload] Total lines read: {len(lines)}")
        
        if not lines:
            print("[Upload] Error: No valid data in file")
            return jsonify({
                "error": "파일에 유효한 데이터가 없습니다",
                "details": ["파일이 비어있거나 모든 줄이 비어있습니다"]
            }), 422
        
        # Check for header
        first_line = [col.lower().strip().strip('"') for col in lines[0]]
        if any('english' in col for col in first_line) and any('korean' in col for col in first_line):
            print("[Upload] Found header, removing first line")
            lines = lines[1:]
        
        if not lines:
            print("[Upload] Error: No data after header")
            return jsonify({
                "error": "데이터가 없습니다",
                "details": ["헤더를 제외하고 데이터가 없습니다"]
            }), 422
        
        print("[Upload] Processing words...")
        words_to_add = []
        duplicates = []
        errors = []
        
        for line_num, row in enumerate(lines, 1):
            try:
                if len(row) != 2:
                    print(f"[Upload] Line {line_num} has wrong format: {row}")
                    error_msg = f"{line_num}번째 줄: 잘못된 형식입니다 (english,korean 형식이어야 합니다)"
                    errors.append(error_msg)
                    continue
                
                english, korean = [col.strip().strip('"').strip("'").strip() for col in row]
                
                if not english or not korean:
                    print(f"[Upload] Line {line_num} has empty values: {row}")
                    error_msg = f"{line_num}번째 줄: 영어 또는 한글이 비어있습니다"
                    errors.append(error_msg)
                    continue
                
                # 영어 단어 검증 (더 유연하게 변경)
                if not all(c.isalpha() or c.isspace() or c in '-,()' for c in english):
                    print(f"[Upload] Line {line_num} has invalid English characters: {english}")
                    error_msg = f"{line_num}번째 줄: 영어 단어에 허용되지 않는 문자가 있습니다"
                    errors.append(error_msg)
                    continue
                
                # 한글 단어 검증 (더 유연하게 변경)
                if not all(('\uAC00' <= c <= '\uD7A3') or c.isspace() or c in ',-()~.·' for c in korean):
                    print(f"[Upload] Line {line_num} has invalid Korean characters: {korean}")
                    error_msg = f"{line_num}번째 줄: 한글 단어에 허용되지 않는 문자가 있습니다"
                    errors.append(error_msg)
                    continue
                
                print(f"[Upload] Processing word: {english} -> {korean}")
                
                existing_word = Word.query.filter_by(english=english).first()
                if existing_word:
                    print(f"[Upload] Duplicate word found: {english}")
                    duplicates.append(english)
                    continue
                
                word = Word(english=english, korean=korean)
                words_to_add.append(word)
                
            except Exception as e:
                print(f"[Upload] Error processing line {line_num}: {str(e)}")
                error_msg = f"{line_num}번째 줄: 처리 중 오류 발생 - {str(e)}"
                errors.append(error_msg)
                continue
        
        if errors:
            print("[Upload] Found errors:", errors)
            return jsonify({
                "error": "파일 처리 중 오류가 발생했습니다",
                "details": errors
            }), 422
        
        if words_to_add:
            print(f"[Upload] Adding {len(words_to_add)} new words...")
            try:
                batch_size = 1000
                for i in range(0, len(words_to_add), batch_size):
                    batch = words_to_add[i:i + batch_size]
                    db.session.bulk_save_objects(batch)
                    db.session.commit()
                    print(f"[Upload] Committed batch {i//batch_size + 1}")
                
                print("[Upload] Words added successfully")
                
                return jsonify({
                    "message": f"{len(words_to_add)}개의 단어가 추가되었습니다",
                    "duplicates": duplicates if duplicates else None
                }), 200
            except Exception as e:
                db.session.rollback()
                print(f"[Upload] Database error: {str(e)}")
                return jsonify({
                    "error": "데이터베이스 저장 중 오류가 발생했습니다",
                    "details": [str(e)]
                }), 422
        else:
            print("[Upload] No new words to add")
            if duplicates:
                return jsonify({
                    "error": "추가된 단어가 없습니다",
                    "details": ["모든 단어가 이미 존재합니다"],
                    "duplicates": duplicates
                }), 422
            else:
                return jsonify({
                    "error": "추가된 단어가 없습니다",
                    "details": ["유효한 단어 데이터가 없습니다"]
                }), 422
        
    except Exception as e:
        print(f"[Upload] Unexpected error: {str(e)}")
        print(f"[Upload] Error type: {type(e)}")
        import traceback
        print("[Upload] Traceback:", traceback.format_exc())
        db.session.rollback()
        return jsonify({
            "error": "파일 처리 중 오류가 발생했습니다",
            "details": ["파일 형식과 인코딩을 확인해주세요", str(e)]
        }), 422

# 첫 요청시 테이블 생성 (Flask 2.0 이상에서는 권장되지 않음)
# 대신 애플리케이션 초기화 시 create_tables 함수를 직접 호출
with app.app_context():
    try:
        create_tables()
    except Exception as e:
        print(f"Error creating tables: {e}")

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)