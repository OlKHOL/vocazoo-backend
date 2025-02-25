from flask import Flask, jsonify, request
from flask_cors import CORS
import random
import time
from auth import auth, token_required, verify_token
from database_setup import init_db  # database.py 대신 database_setup에서 import
from database import get_db  # 데이터베이스 연결용
import json
import sqlite3
import datetime  # datetime import 추가
from functools import wraps
from difflib import SequenceMatcher  # 문자열 유사도 검사를 위해 추가

# 2. 단어 데이터베이스 로드 함수
def load_word_database():
    words = []
    seen_english = set()  # 중복 체크를 위한 집합
    
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
    return words

# 3. 전역 변수로 단어 데이터베이스 로드
word_database = load_word_database()

def initialize_word_status():
    """word_database.py의 단어들을 word_status 테이블에 초기화"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 테이블을 비우고 다시 시작
        c.execute('DELETE FROM word_status')
        
        # 모든 단어 새로 추가
        for word in word_database:
            c.execute('''
                INSERT INTO word_status (english, korean, level, used)
                VALUES (?, ?, ?, FALSE)
            ''', (word['english'], word['korean'], word['level']))
        
        conn.commit()
    finally:
        conn.close()

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": ["http://localhost:3000", "http://192.168.*.*:3000"],  # 로컬 IP 허용
        "methods": ["GET", "POST", "OPTIONS", "DELETE"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# 데이터베이스 초기화
init_db()

test_started = False
test = None

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'message': '토큰이 필요합니다'}), 401
            
        user_id = verify_token(token)
        
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute('SELECT is_admin FROM users WHERE id = ?', (user_id,))
        user = c.fetchone()
        conn.close()
        
        if not user or not user[0]:
            return jsonify({'message': '관리자 권한이 필요합니다'}), 403
            
        return f(*args, **kwargs)
    return decorated

def get_or_create_active_word_set():
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
        return next_words
        
    finally:
        conn.close()

class TestState:
    def __init__(self, word_set_id=None):
        self.score = 0
        self.start_time = None
        self.time_limit = 60
        self.current_word = None
        self.word_list = []
        
        if word_set_id:  # word_set_id가 있을 때만 단어장 불러오기
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute('SELECT words FROM word_sets WHERE id = ?', (word_set_id,))
            result = c.fetchone()
            conn.close()
            
            if not result:
                raise Exception("단어장을 찾을 수 없습니다")
                
            word_set = json.loads(result[0])
            if len(word_set) < 10:
                raise Exception("단어장에 충분한 단어가 없습니다")
                
            self.word_list = random.sample(word_set, 10)  # 10개 랜덤 선택

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

@app.route("/start_test", methods=["POST"])
@token_required
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

@app.route("/get_question", methods=["GET"])
def get_question():
    global test  # test를 global로 선언
    if not test_started or not test or not test.start_time:
        return jsonify({}), 200

    if test.is_time_over():
        return jsonify({"test_completed": True}), 200
        
    # 10문제 제한 체크 추가
    if len(test.word_list) == 0:  # 문제를 다 풀었을 때
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
        
    # start_time이 None인 경우 처리 추가
    if not test.start_time:
        return jsonify({
            "score": 0,
            "remaining_time": test.time_limit
        })
    
    # 게임 진행 중에는 기본 점수만 반환
    return jsonify({
        "score": test.score,
        "remaining_time": round(max(0, test.time_limit - (time.time() - test.start_time)), 2)
    })

@app.route("/get_final_score", methods=["GET", "OPTIONS"])
def get_final_score():
    if request.method == "OPTIONS":
        return "", 200
        
    # 게임 종료 시에만 보너스가 적용된 최종 점수 반환
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
        
    global test, test_started  # 전역 변수를 다시 초기화
    test = TestState()  # 새로운 게임 객체로 리셋
    test.start_test()  # 새로운 게임 시작
    test_started = True
    return jsonify({"message": "Test restarted"}), 200

@app.route("/save_test_result", methods=["POST"])
@token_required
def save_test_result():
    data = request.get_json()
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    score = data.get('score')
    solved_count = data.get('solved_count')
    wrong_answers = json.dumps(data.get('wrong_answers', []))
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO test_results 
        (user_id, score, solved_count, wrong_answers)
        VALUES (?, ?, ?, ?)
    ''', (user_id, score, solved_count, wrong_answers))
    conn.commit()
    conn.close()
    
    return jsonify({"message": "결과가 저장되었습니다"}), 200

@app.route("/get_test_history", methods=["GET"])
@token_required
def get_test_history():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        SELECT id, score, solved_count, wrong_answers, test_date
        FROM test_results
        WHERE user_id = ?
        ORDER BY test_date DESC
        LIMIT 10
    ''', (user_id,))
    results = c.fetchall()
    conn.close()
    
    history = [{
        'id': r[0],
        'score': r[1],
        'solved_count': r[2],
        'wrong_answers': json.loads(r[3]),
        'test_date': r[4]
    } for r in results]
    
    return jsonify(history), 200

@app.route("/delete_test_record/<int:record_id>", methods=["DELETE"])
@token_required
def delete_test_record(record_id):
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    # 해당 사용자의 특정 기록만 삭제
    c.execute('DELETE FROM test_results WHERE id = ? AND user_id = ?', (record_id, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({"message": "기록이 삭제되었습니다"}), 200

@app.route("/get_current_word_set", methods=["GET"])
@token_required
def get_current_word_set():
    words = get_or_create_active_word_set()
    return jsonify({
        "words": words,
        "total_count": len(words)
    }), 200

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

@app.route("/admin/create_word_set", methods=["POST"])
@admin_required
def admin_create_word_set():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    data = request.get_json()
    selected_words = data.get('words', [])
    
    if not selected_words or len(selected_words) != 30:
        return jsonify({'message': '정확히 30개의 단어가 필요합니다'}), 400
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    # 기존 활성 단어장 비활성화
    c.execute('UPDATE word_sets SET is_active = FALSE WHERE is_active = TRUE')
    
    # 새 단어장 생성
    words_json = json.dumps(selected_words)
    c.execute('''
        INSERT INTO word_sets (words, created_at, is_active, created_by)
        VALUES (?, CURRENT_TIMESTAMP, TRUE, ?)
    ''', (words_json, user_id))
    
    conn.commit()
    conn.close()
    
    return jsonify({'message': '새로운 단어장이 생성되었습니다'}), 200

@app.route("/admin/delete_word_set/<int:set_id>", methods=["DELETE"])
@admin_required
def admin_delete_word_set(set_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute('DELETE FROM word_sets WHERE id = ?', (set_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'message': '단어장이 삭제되었습니다'}), 200

@app.route("/word_set/current", methods=["GET"])
@token_required
def get_current_word_set_detail():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    # 현재 활성화된 단어장 조회
    c.execute('''
        SELECT w.id, w.words, w.created_at, u.username
        FROM word_sets w
        LEFT JOIN users u ON w.created_by = u.id
        WHERE w.is_active = TRUE
    ''')
    current_set = c.fetchone()
    conn.close()
    
    if not current_set:
        return jsonify({
            "message": "현재 활성화된 단어장이 없습니다"
        }), 404
    
    words = json.loads(current_set[1])
    # 단어 목록을 레벨별로 정렬
    sorted_words = sorted(words, key=lambda x: int(x['level']))
    
    return jsonify({
        "id": current_set[0],
        "words": sorted_words,
        "created_at": current_set[2],
        "created_by": current_set[3],
        "total_count": len(words),
        "level_distribution": {
            "level1": len([w for w in words if w['level'] == '1']),
            "level2": len([w for w in words if w['level'] == '2']),
            "level3": len([w for w in words if w['level'] == '3'])
        }
    }), 200

@app.route("/word_sets", methods=["GET"])
@token_required
def get_word_sets():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute('''
        SELECT id, words, created_at, is_active
        FROM word_sets
        ORDER BY created_at DESC
    ''')
    
    results = c.fetchall()
    conn.close()
    
    word_sets = []
    for r in results:
        words = json.loads(r[1])
        word_sets.append({
            'id': r[0],
            'preview': words[:5],  # 미리보기로 5개만
            'created_at': r[2],
            'is_active': r[3],
            'total_words': len(words),
            'level_distribution': {
                'level1': len([w for w in words if w['level'] == '1']),
                'level2': len([w for w in words if w['level'] == '2']),
                'level3': len([w for w in words if w['level'] == '3'])
            }
        })
    
    return jsonify(word_sets), 200

@app.route("/word_set/<int:set_id>", methods=["GET"])
@token_required
def get_word_set_detail(set_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute('''
        SELECT w.id, w.words, w.created_at, w.is_active, u.username
        FROM word_sets w
        LEFT JOIN users u ON w.created_by = u.id
        WHERE w.id = ?
    ''', (set_id,))
    
    result = c.fetchone()
    conn.close()
    
    if not result:
        return jsonify({"message": "단어장을 찾을 수 없습니다"}), 404
    
    words = json.loads(result[1])
    # 단어 목록을 레벨별로 정렬
    sorted_words = sorted(words, key=lambda x: int(x['level']))
    
    return jsonify({
        'id': result[0],
        'words': sorted_words,
        'created_at': result[2],
        'is_active': result[3],
        'created_by': result[4],
        'total_count': len(words),
        'level_distribution': {
            "level1": len([w for w in words if w['level'] == '1']),
            "level2": len([w for w in words if w['level'] == '2']),
            "level3": len([w for w in words if w['level'] == '3'])
        }
    }), 200

@app.route("/check_admin", methods=["GET"])
@token_required
def check_admin():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT is_admin FROM users WHERE id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    
    print(f"Checking admin status for user {user_id}: {result}")  # 디버깅용 로그 추가
    
    return jsonify({"is_admin": bool(result[0] if result else False)}), 200

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
    
    # 중복 확인
    c.execute('SELECT id FROM users WHERE username = ? AND id != ?', (new_username, user_id))
    if c.fetchone():
        conn.close()
        return jsonify({"message": "이미 사용 중인 사용자명입니다"}), 400
    
    # 사용자명 업데이트
    c.execute('UPDATE users SET username = ? WHERE id = ?', (new_username, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({"message": "사용자명이 변경되었습니다"}), 200

@app.route("/user_info", methods=["GET"])
@token_required
def get_user_info():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result:
        return jsonify({"username": result[0]}), 200
    return jsonify({"message": "사용자를 찾을 수 없습니다"}), 404

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
        # 단어장 존재 여부 확인
        c.execute('SELECT id FROM word_sets WHERE id = ?', (set_id,))
        if not c.fetchone():
            return jsonify({"error": "단어장을 찾을 수 없습니다"}), 404
            
        # 단어장 전체 업데이트
        words_json = json.dumps(words)
        c.execute('UPDATE word_sets SET words = ? WHERE id = ?', (words_json, set_id))
        
        # 수정된 단어들 word_status 업데이트
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
        c.execute('SELECT id, words FROM word_sets ORDER BY id')
        word_sets = c.fetchall()
        
        with open('word_sets.py', 'w', encoding='utf-8') as file:
            file.write("# 단어장 목록\n\n")
            for set_id, words_json in word_sets:
                words = json.loads(words_json)
                file.write(f"# 단어장 #{set_id}\n")
                for word in words:
                    file.write(f"{{ 'english': '{word['english']}', 'korean': '{word['korean']}', 'level': '{word['level']}' }},")
                    file.write("\n")  # 각 단어 다음에 줄바꿈
                file.write("\n")  # 단어장 구분을 위한 빈 줄
        
        return jsonify({"message": "단어장이 word_sets.py 파일로 저장되었습니다"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

app.register_blueprint(auth)

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)  # 다시 5000 포트로
