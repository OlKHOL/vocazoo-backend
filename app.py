from flask import Flask, jsonify, request
from flask_cors import CORS
from auth import auth, token_required, verify_token
from database_setup import init_db
import json
import sqlite3
import os
from test_manager import TestState
from functools import wraps
import time
from database import get_db
import random
from scheduler import update_active_word_set
from datetime import datetime
import base64
from level_system import LevelSystem
from flask_jwt_extended import JWTManager, create_access_token, get_jwt_identity, jwt_required
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, WordSet, TestResult, WrongAnswer, Word
from config import get_config
from flask_migrate import Migrate

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

# 전역 변수
test_started = False
test = None

# 데이터베이스 초기화 (개발 환경에서만)
if os.getenv('FLASK_ENV') != 'production':
    if not os.path.exists('users.db'):
        init_db()
        from word_database import load_word_database
        words = load_word_database()
        with app.app_context():
            # 초기 단어장 생성
            word_set = WordSet(
                words=words[:30],
                is_active=True
            )
            db.session.add(word_set)
            db.session.commit()

# word_set_id 컬럼 추가
conn = sqlite3.connect('users.db')
c = conn.cursor()
try:
    # 컬럼이 존재하는지 확인
    c.execute("PRAGMA table_info(test_results)")
    columns = [column[1] for column in c.fetchall()]
    
    # word_set_id 컬럼이 없으면 추가
    if 'word_set_id' not in columns:
        c.execute('''
            ALTER TABLE test_results
            ADD COLUMN word_set_id INTEGER
            REFERENCES word_sets(id)
        ''')
        conn.commit()

    # score_reset_history 테이블이 없으면 생성
    c.execute('''
        CREATE TABLE IF NOT EXISTS score_reset_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reset_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            previous_score FLOAT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
except Exception as e:
    print(f"Error in database setup: {e}")
finally:
    conn.close()

app.register_blueprint(auth)

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
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 이전 단어장 ID 저장
        c.execute('SELECT id FROM word_sets WHERE is_active = TRUE')
        prev_active_set = c.fetchone()

        # 사용되지 않은 단어 30개 선택
        c.execute('''
            SELECT english, COALESCE(modified_korean, korean), level 
            FROM word_status 
            WHERE used = FALSE 
            ORDER BY RANDOM() 
            LIMIT 30
        ''')
        words = c.fetchall()
        
        if len(words) < 30:
            # 모든 단어가 사용됐으면 리셋
            c.execute('UPDATE word_status SET used = FALSE')
            conn.commit()
            
            c.execute('''
                SELECT english, COALESCE(modified_korean, korean), level 
                FROM word_status 
                ORDER BY RANDOM() 
                LIMIT 30
            ''')
            words = c.fetchall()
        
        # 단어 리스트 생성
        next_words = [
            {'english': w[0], 'korean': w[1], 'level': w[2]} 
            for w in words
        ]
        
        # 선택된 단어들 used 표시
        for word in next_words:
            c.execute('UPDATE word_status SET used = TRUE WHERE english = ?', 
                     (word['english'],))
        
        # 새 단어장 생성
        words_json = json.dumps(next_words)
        c.execute('UPDATE word_sets SET is_active = FALSE')
        c.execute('''
            INSERT INTO word_sets (words, created_at, is_active)
            VALUES (?, CURRENT_TIMESTAMP, TRUE)
        ''', (words_json,))
        
        # 새로운 단어장 ID 가져오기
        c.execute('SELECT id FROM word_sets WHERE is_active = TRUE')
        new_active_set = c.fetchone()

        if prev_active_set:
            # 이전 단어장의 오답노트 초기화
            c.execute('''
                UPDATE test_results
                SET wrong_answers = '[]'
                WHERE word_set_id = ?
            ''', (prev_active_set[0],))
        
        conn.commit()
        return next_words
    finally:
        conn.close()

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
        
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('SELECT id FROM word_sets WHERE id = ?', (set_id,))
        if not c.fetchone():
            return jsonify({"error": "단어장을 찾을 수 없습니다"}), 404
            
        words_json = json.dumps(words)
        c.execute('UPDATE word_sets SET words = ? WHERE id = ?', (words_json, set_id))
        
        for word in words:
            c.execute('''
                UPDATE word_status 
                SET modified_korean = ?, level = ?, last_modified = CURRENT_TIMESTAMP
                WHERE english = ?
            ''', (word['korean'], word['level'], word['english']))
        
        conn.commit()
        return jsonify({
            "message": "단어장이 수정되었습니다",
            "updated_words": words
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/admin/export_word_sets", methods=["GET"])
@admin_required
def export_word_sets():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 먼저 word_status 테이블에서 모든 단어의 최신 상태를 가져옴
        c.execute('''
            SELECT english, 
                   COALESCE(modified_korean, korean) as current_korean,
                   level,
                   last_modified
            FROM word_status
            ORDER BY last_modified DESC
        ''')
        word_status = {row[0]: {'korean': row[1], 'level': row[2], 'last_modified': row[3]} 
                      for row in c.fetchall()}
        
        # 단어장 데이터 가져오기
        c.execute('''
            SELECT ws.id, ws.words, ws.created_at, u.username
            FROM word_sets ws
            LEFT JOIN users u ON ws.created_by = u.id
            ORDER BY ws.id
        ''')
        word_sets = c.fetchall()
        
        import os
        import shutil
        from datetime import datetime
        
        # 원본 파일 백업 (존재하는 경우)
        original_file = 'word_database.py'
        backup_file = f'word_database_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.py'
        temp_file = 'word_database_temp.py'
        
        if os.path.exists(original_file):
            shutil.copy2(original_file, backup_file)
        
        # 임시 파일에 먼저 쓰기
        with open(temp_file, 'w', encoding='utf-8') as file:
            file.write("# 단어 데이터베이스\n")
            file.write(f"# 마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            file.write("word_database = [\n")
            
            # 모든 단어장의 단어들을 중복 없이 저장하되, 최신 수정사항 반영
            seen_words = set()
            for _, words_json, created_at, username in word_sets:
                words = json.loads(words_json)
                for word in words:
                    english = word['english']
                    if english not in seen_words:
                        seen_words.add(english)
                        # word_status의 최신 정보 사용
                        current_status = word_status.get(english, word)
                        file.write(f"    {{'english': '{english}', "
                                 f"'korean': '{current_status['korean']}', "
                                 f"'level': '{current_status['level']}'}},\n")
            
            file.write("]\n")
        
        # 임시 파일을 원본 파일로 이동 (안전한 덮어쓰기)
        shutil.move(temp_file, original_file)
        
        message = "단어장이 word_database.py 파일로 저장되었습니다."
        if os.path.exists(backup_file):
            message += f" 이전 버전이 {backup_file}로 백업되었습니다."
        
        return jsonify({"message": message}), 200
    except Exception as e:
        # 오류 발생 시 임시 파일 삭제
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/get_current_word_set", methods=["GET"])
@token_required
def get_current_word_set():
    try:
        words = get_or_create_active_word_set()
        return jsonify({
            "words": [{'english': w['english'], 'korean': w['korean']} for w in words],
            "total_count": len(words)
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/get_word_set_history", methods=["GET"])
@token_required
def get_word_set_history():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute('''
        SELECT id, words, created_at, is_active
        FROM word_sets
        ORDER BY created_at DESC
        LIMIT 10
    ''')
    
    results = c.fetchall()
    conn.close()
    
    history = [{
        'id': r[0],
        'words': json.loads(r[1]),
        'created_at': r[2],
        'is_active': r[3]
    } for r in results]
    
    return jsonify(history), 200

@app.route("/word_set/<int:set_id>", methods=["GET"])
@token_required
def get_word_set(set_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT words FROM word_sets WHERE id = ?', (set_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return jsonify({"error": "단어장을 찾을 수 없습니다"}), 404
        
    words = json.loads(result[0])
    return jsonify({
        "id": set_id,
        "words": words
    }), 200

@app.route("/get_word_sets", methods=["GET"])
@token_required
def get_word_sets():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 관리자 확인
        c.execute('SELECT is_admin FROM users WHERE id = ?', (user_id,))
        is_admin = bool(c.fetchone()[0])
        
        # 단어장 조회
        if is_admin:
            c.execute('SELECT * FROM word_sets ORDER BY created_at DESC')
        else:
            c.execute('''
                SELECT * FROM word_sets 
                WHERE is_active = TRUE 
                LIMIT 1
            ''')
            
        word_sets = c.fetchall()
        return jsonify([{
            'id': ws[0],
            'words': json.loads(ws[1]),
            'created_at': ws[2],
            'is_active': bool(ws[3])
        } for ws in word_sets]), 200
    finally:
        conn.close()

@app.route("/admin/delete_word_set/<int:set_id>", methods=["DELETE"])
@admin_required
def admin_delete_word_set(set_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('DELETE FROM word_sets WHERE id = ?', (set_id,))
        conn.commit()
        return jsonify({'message': '단어장이 삭제되었습니다'}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

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
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        
        c.execute('SELECT level, exp FROM users WHERE id = ?', (user_id,))
        current_level, current_exp = c.fetchone()
        
        # 점수에 따른 경험치 계산
        gained_exp = LevelSystem.calculate_test_exp(test.score, current_level)
        new_level, new_exp, level_up = LevelSystem.process_exp_gain(
            current_level, current_exp, gained_exp
        )
        
        # 새로운 뱃지 확인
        new_badge = None
        if level_up:
            new_badge = LevelSystem.check_badge_unlock(new_level)
            if new_badge:
                c.execute('SELECT badges FROM users WHERE id = ?', (user_id,))
                badges = json.loads(c.fetchone()[0])
                badges.append(new_badge)
                c.execute('UPDATE users SET badges = ? WHERE id = ?', 
                         (json.dumps(badges), user_id))
        
        # 레벨과 경험치 업데이트
        c.execute('''
            UPDATE users 
            SET level = ?, exp = ?
            WHERE id = ?
        ''', (new_level, new_exp, user_id))
        
        conn.commit()
        
        return jsonify({
            "message": "테스트 결과가 저장되었습니다",
            "exp_gained": gained_exp,
            "level_up": level_up,
            "new_badge": new_badge
        }), 200
        
    except Exception as e:
        print(f"Error saving test result: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if 'conn' in locals():
            conn.close()

@app.route("/word_set/current", methods=["GET"])
@token_required
def get_current_word_set_detail():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('''
            SELECT w.id, w.words, w.created_at, u.username
            FROM word_sets w
            LEFT JOIN users u ON w.created_by = u.id
            WHERE w.is_active = TRUE
        ''')
        current_set = c.fetchone()
        
        if not current_set:
            return jsonify({
                "message": "현재 활성화된 단어장이 없습니다"
            }), 404
        
        words = json.loads(current_set[1])
        
        return jsonify({
            "id": current_set[0],
            "words": [{'english': w['english'], 'korean': w['korean']} for w in words],
            "created_at": current_set[2],
            "created_by": current_set[3],
            "total_count": len(words)
        }), 200
    finally:
        conn.close()

@app.route("/admin/create_word_set", methods=["POST"])
@admin_required
def create_word_set():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 현재 존재하는 단어장의 ID 목록 조회
        c.execute('SELECT id FROM word_sets ORDER BY id')
        existing_ids = [row[0] for row in c.fetchall()]
        
        # 사용 가능한 가장 작은 ID 찾기
        next_id = 1
        for id in existing_ids:
            if id != next_id:
                break
            next_id += 1
            
        # 현재 존재하는 단어장의 단어들만 체크
        c.execute('SELECT words FROM word_sets')
        used_words = set()
        for result in c.fetchall():
            words = json.loads(result[0])
            used_words.update(w['english'] for w in words)
        
        # 사용되지 않은 단어 30개 선택
        c.execute('''
            SELECT english, COALESCE(modified_korean, korean), level 
            FROM word_status 
            WHERE english NOT IN ({})
            ORDER BY RANDOM() 
            LIMIT 30
        '''.format(','.join('?' * len(used_words))), tuple(used_words))
        
        words = c.fetchall()
        
        if len(words) < 30:
            # 사용 가능한 단어가 부족하면 전체 단어에서 랜덤 선택
            c.execute('''
                SELECT english, COALESCE(modified_korean, korean), level 
                FROM word_status 
                ORDER BY RANDOM() 
                LIMIT 30
            ''')
            words = c.fetchall()
            
        # 단어 리스트 생성
        next_words = [
            {'english': w[0], 'korean': w[1], 'level': w[2]} 
            for w in words
        ]
        
        # 새 단어장 생성 (ID 직접 지정)
        words_json = json.dumps(next_words)
        c.execute('''
            INSERT INTO word_sets (id, words, created_at, is_active)
            VALUES (?, ?, CURRENT_TIMESTAMP, FALSE)
        ''', (next_id, words_json))
        
        conn.commit()
        return jsonify({"message": "새로운 단어장이 생성되었습니다", "id": next_id}), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/update_username", methods=["POST"])
@token_required
def update_username():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    data = request.get_json()
    new_username = data.get('username')
    
    if not new_username:
        return jsonify({"message": "새로운 사용자명이 필요합니다"}), 400
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('SELECT id FROM users WHERE username = ? AND id != ?', (new_username, user_id))
        if c.fetchone():
            return jsonify({"message": "이미 사용 중인 사용자명입니다"}), 400
        
        c.execute('UPDATE users SET username = ? WHERE id = ?', (new_username, user_id))
        conn.commit()
        return jsonify({"message": "사용자명이 변경되었습니다"}), 200
    finally:
        conn.close()

@app.route("/update_profile_image", methods=["POST", "OPTIONS"])
@token_required
def update_profile_image():
    if request.method == "OPTIONS":
        return jsonify({"message": "OK"}), 200
        
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    if 'profileImage' not in request.files:
        return jsonify({"error": "No file provided"}), 400
        
    file = request.files['profileImage']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
        
    if file:
        try:
            # 이미지를 base64로 인코딩
            file_data = file.read()
            encoded_image = base64.b64encode(file_data).decode('utf-8')
            
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            
            # 프로필 이미지 업데이트
            c.execute('UPDATE users SET profile_image = ? WHERE id = ?',
                     (encoded_image, user_id))
            conn.commit()
            
            return jsonify({
                "message": "Profile image updated successfully",
                "profileImage": encoded_image
            }), 200
        except Exception as e:
            print(f"Error updating profile image: {e}")
            return jsonify({"error": "Failed to update profile image"}), 500
        finally:
            if 'conn' in locals():
                conn.close()

@app.route("/account/info", methods=["GET"])
@token_required
def get_account_info():
    try:
        token = request.headers.get('Authorization')
        user_id = verify_token(token)
        
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        
        c.execute('SELECT username, created_at, COALESCE(current_score, 0), COALESCE(completed_tests, 0) FROM users WHERE id = ?', (user_id,))
        user_info = c.fetchone()
        
        if not user_info:
            return jsonify({"error": "사용자를 찾을 수 없습니다"}), 404

        avg_score = user_info[2] / user_info[3] if user_info[3] > 0 else 0
        
        created_at = user_info[1]
        if created_at:
            try:
                created_at = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S').isoformat()
            except Exception as e:
                print(f"Date conversion error: {e}")
                created_at = None
            
        response_data = {
            "username": user_info[0],
            "createdAt": created_at,
            "stats": {
                "currentScore": float(user_info[2]),
                "totalScore": float(user_info[2]),
                "totalTests": user_info[3],
                "averageScore": round(float(avg_score), 2)
            }
        }
        
        return jsonify(response_data), 200
        
    except Exception as e:
        print(f"Error in get_account_info: {e}")
        return jsonify({"error": "서버 오류가 발생했습니다"}), 500
        
    finally:
        if 'conn' in locals():
            conn.close()

@app.route("/get_available_words", methods=["GET"])
@admin_required
def get_available_words():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('''
            SELECT english, COALESCE(modified_korean, korean), level 
            FROM word_status 
            ORDER BY level, english
        ''')
        words = [
            {'english': w[0], 'korean': w[1], 'level': w[2]} 
            for w in c.fetchall()
        ]
        return jsonify({'words': words}), 200
    finally:
        conn.close()

@app.route("/wrong_answers", methods=["GET"])
@token_required
def get_wrong_answers():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # Check user level first
        c.execute('SELECT level FROM users WHERE id = ?', (user_id,))
        user_level = c.fetchone()[0]
        
        # Return empty list if user level is below 3
        if user_level < 3:
            return jsonify([]), 200
            
        # 현재 활성화된 단어장의 ID를 가져옴
        c.execute('SELECT id FROM word_sets WHERE is_active = TRUE')
        current_word_set = c.fetchone()
        if not current_word_set:
            return jsonify([]), 200

        # 현재 단어장에 대한 모든 오답을 가져옴 (중복 제거)
        c.execute('''
            SELECT DISTINCT wrong_answers
            FROM test_results
            WHERE user_id = ? AND word_set_id = ?
            ORDER BY completed_at DESC
        ''', (user_id, current_word_set[0]))
        
        results = c.fetchall()
        all_wrong_answers = set()
        
        for result in results:
            if result[0]:  # wrong_answers가 NULL이 아닌 경우
                wrong_answers = json.loads(result[0])
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
    finally:
        conn.close()

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
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 사용되지 않은 단어 30개 선택
        c.execute('''
            SELECT english, COALESCE(modified_korean, korean), level 
            FROM word_status 
            WHERE used = FALSE 
            ORDER BY RANDOM() 
            LIMIT 30
        ''')
        words = c.fetchall()
        
        if len(words) < 30:
            # 모든 단어가 사용됐으면 리셋
            c.execute('UPDATE word_status SET used = FALSE')
            conn.commit()
            
            c.execute('''
                SELECT english, COALESCE(modified_korean, korean), level 
                FROM word_status 
                ORDER BY RANDOM() 
                LIMIT 30
            ''')
            words = c.fetchall()
        
        # 단어 리스트 생성
        next_words = [
            {'english': w[0], 'korean': w[1], 'level': w[2]} 
            for w in words
        ]
        
        # 선택된 단어들 used 표시
        for word in next_words:
            c.execute('UPDATE word_status SET used = TRUE WHERE english = ?', 
                     (word['english'],))
        
        # 새 단어장 생성
        words_json = json.dumps(next_words)
        c.execute('UPDATE word_sets SET is_active = FALSE')
        c.execute('''
            INSERT INTO word_sets (words, created_at, is_active)
            VALUES (?, CURRENT_TIMESTAMP, TRUE)
        ''', (words_json,))
        
        conn.commit()
        return jsonify({"message": "단어장이 성공적으로 교체되었습니다"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/score_reset_history", methods=["GET"])
@token_required
def get_score_reset_history():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('''
            SELECT reset_date, previous_score
            FROM score_reset_history
            WHERE user_id = ?
            ORDER BY reset_date DESC
            LIMIT 10
        ''', (user_id,))
        history = [{
            'reset_date': row[0],
            'previous_score': row[1]
        } for row in c.fetchall()]
        return jsonify(history), 200
    finally:
        conn.close()

@app.route("/admin/word_sets/<int:set_id>", methods=["DELETE"])
@admin_required
def delete_word_set(set_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 활성화된 단어장은 삭제 불가
        c.execute('SELECT is_active FROM word_sets WHERE id = ?', (set_id,))
        result = c.fetchone()
        
        if not result:
            return jsonify({"error": "단어장을 찾을 수 없습니다"}), 404
            
        if result[0]:
            return jsonify({"error": "활성화된 단어장은 삭제할 수 없습니다"}), 400
        
        # 단어장 삭제
        c.execute('DELETE FROM word_sets WHERE id = ?', (set_id,))
        conn.commit()
        
        return jsonify({"message": "단어장이 삭제되었습니다"}), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/update_wrong_answers", methods=["POST"])
@token_required
def update_wrong_answers():
    try:
        token = request.headers.get('Authorization')
        user_id = verify_token(token)
        data = request.get_json()
        
        if not data or 'wrong_answers' not in data:
            return jsonify({"error": "잘못된 요청입니다."}), 400
            
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        
        # 현재 활성화된 단어장의 ID를 가져옴
        c.execute('SELECT id FROM word_sets WHERE is_active = TRUE')
        current_word_set = c.fetchone()
        if not current_word_set:
            return jsonify({"error": "활성화된 단어장이 없습니다."}), 400

        # 가장 최근 테스트 결과의 wrong_answers 업데이트
        c.execute('''
            UPDATE test_results 
            SET wrong_answers = ? 
            WHERE user_id = ? AND word_set_id = ?
            ORDER BY completed_at DESC 
            LIMIT 1
        ''', (json.dumps(data['wrong_answers']), user_id, current_word_set[0]))
        
        conn.commit()
        return jsonify({"message": "오답 정보가 저장되었습니다"}), 200
        
    except Exception as e:
        print(f"Error in update_wrong_answers: {e}")
        return jsonify({"error": "서버 오류가 발생했습니다"}), 500
        
    finally:
        if 'conn' in locals():
            conn.close()

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
        
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        
        # 레벨 5 이상인 유저들의 랭킹 정보 가져오기
        c.execute('''
            WITH RankedUsers AS (
                SELECT 
                    username,
                    current_score,
                    level,
                    ROW_NUMBER() OVER (ORDER BY current_score DESC) as rank
                FROM users
                WHERE level >= 5
            )
            SELECT username, current_score, level, rank
            FROM RankedUsers
            ORDER BY rank
        ''')
        rankings = c.fetchall()
        
        # 현재 사용자의 랭킹 정보 가져오기
        c.execute('''
            SELECT 
                username,
                current_score,
                level,
                (
                    SELECT COUNT(*) + 1 
                    FROM users AS u2 
                    WHERE u2.current_score > u1.current_score
                    AND u2.level >= 5
                ) as rank
            FROM users AS u1
            WHERE id = ?
        ''', (current_user_id,))
        current_user = c.fetchone()
        
        return jsonify({
            "rankings": [{
                "rank": rank,
                "username": username,
                "score": float(score),
                "level": level
            } for username, score, level, rank in rankings],
            "currentUser": {
                "rank": current_user[3],
                "username": current_user[0],
                "score": float(current_user[1]),
                "level": current_user[2]
            } if current_user else None,
            "isQualified": current_user[2] >= 5 if current_user else False
        }), 200
        
    except Exception as e:
        print(f"Error in get_rankings: {e}")
        return jsonify({"error": "랭킹 정보를 가져오는데 실패했습니다"}), 500
    finally:
        if 'conn' in locals():
            conn.close()

@app.route("/user/level", methods=["GET"])
@token_required
def get_user_level():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('SELECT level, exp, badges FROM users WHERE id = ?', (user_id,))
        user_data = c.fetchone()
        
        if not user_data:
            return jsonify({"error": "User not found"}), 404
            
        level, exp, badges = user_data
        required_exp = LevelSystem.get_exp_for_level(level)
        progress = LevelSystem.get_level_progress(level, exp)
        
        return jsonify({
            "level": level,
            "current_exp": exp,
            "required_exp": required_exp,
            "progress": progress,
            "badges": json.loads(badges)
        }), 200
    finally:
        conn.close()

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

# 자동 테이블 생성 (PostgreSQL 마이그레이션용)
def create_tables():
    with app.app_context():
        db.create_all()
        print("Tables created successfully!")
        
        # 관리자 계정 확인 및 생성
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            hashed_password = generate_password_hash('admin1234')
            admin = User(username='admin', password=hashed_password, is_admin=True)
            db.session.add(admin)
            db.session.commit()
            print("Admin account created successfully!")
        
        # 단어 데이터베이스 로드 및 단어장 생성
        from word_database import load_word_database
        word_database = load_word_database()
        
        # 단어장 확인
        word_set = WordSet.query.first()
        if not word_set:
            # 단어 데이터베이스에서 30개 단어 선택
            selected_words = random.sample(word_database, 30)
            
            # 새 단어장 생성
            new_word_set = WordSet(
                words=selected_words,
                is_active=True
            )
            db.session.add(new_word_set)
            db.session.commit()
            print("Initial word set created successfully!")
            
        # 전체 단어 데이터베이스를 PostgreSQL에 로드
        load_full_word_database()

# 전체 단어 데이터베이스를 PostgreSQL에 로드하는 함수
def load_full_word_database():
    try:
        # 단어 데이터베이스 로드
        from word_database import load_word_database
        word_database = load_word_database()
        
        # 단어 테이블 생성 (없는 경우)
        with app.app_context():
            # 이미 단어가 있는지 확인
            word_count = Word.query.count()
            
            # 단어가 없으면 전체 단어 데이터베이스 로드
            if word_count == 0:
                for word in word_database:
                    new_word = Word(
                        english=word['english'],
                        korean=word['korean'],
                        level=int(word['level']) if isinstance(word['level'], str) else word['level'],
                        used=False
                    )
                    db.session.add(new_word)
                
                db.session.commit()
                print(f"전체 단어 데이터베이스 {len(word_database)}개 단어가 성공적으로 로드되었습니다.")
            else:
                print(f"단어 데이터베이스가 이미 로드되어 있습니다. 현재 {word_count}개 단어가 있습니다.")
    
    except Exception as e:
        print(f"단어 데이터베이스 로드 중 오류 발생: {e}")

# 첫 요청시 테이블 생성
@app.before_first_request
def before_first_request():
    if os.getenv('FLASK_ENV') == 'production':
        try:
            create_tables()
        except Exception as e:
            print(f"Error creating tables: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)