from flask import Blueprint, request, jsonify
import sqlite3
import hashlib
import jwt
from datetime import datetime, timedelta
from functools import wraps

auth = Blueprint('auth', __name__)
SECRET_KEY = "your-secret-key-here"  # 실제 배포시에는 환경변수로 관리

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def create_token(user_id):
    """사용자 ID로 JWT 토큰 생성"""
    token = jwt.encode({
        'user_id': user_id,
        'exp': datetime.utcnow() + timedelta(days=30)  # 30일 유효
    }, SECRET_KEY, algorithm='HS256')
    return token

def verify_token(token):
    """토큰 검증 및 user_id 반환"""
    try:
        # Bearer 접두사가 있으면 제거
        if token.startswith('Bearer '):
            token = token.split(' ')[1]
            
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        return payload['user_id']
    except jwt.ExpiredSignatureError:
        raise Exception('토큰이 만료되었습니다')
    except jwt.InvalidTokenError:
        raise Exception('유효하지 않은 토큰입니다')

def token_required(f):
    """토큰 필요한 API용 데코레이터"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'message': '토큰이 필요합니다'}), 401
            
        try:
            # Bearer 접두사 제거
            if token.startswith('Bearer '):
                token = token.split(' ')[1]
                
            # 토큰 검증
            payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
            user_id = payload['user_id']
            
            # 사용자 존재 여부 확인
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute('SELECT id FROM users WHERE id = ?', (user_id,))
            user = c.fetchone()
            conn.close()
            
            if not user:
                return jsonify({'message': '유효하지 않은 사용자입니다'}), 401
                
            return f(*args, **kwargs)
        except jwt.ExpiredSignatureError:
            return jsonify({'message': '만료된 토큰입니다'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': '유효하지 않은 토큰입니다'}), 401
    return decorated

@auth.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"message": "사용자명과 비밀번호를 모두 입력해주세요"}), 400
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        # 비밀번호 해시화
        hashed_password = hash_password(password)
        
        # 사용자 추가 전에 테이블 존재 확인
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # 사용자 추가
        c.execute('INSERT INTO users (username, password) VALUES (?, ?)', 
                 (username, hashed_password))
        conn.commit()
        
        # 새로 생성된 사용자의 ID 가져오기
        user_id = c.lastrowid
        
        # 토큰 생성
        token = create_token(user_id)
        
        return jsonify({
            'token': token,
            'message': '회원가입이 완료되었습니다'
        }), 201
        
    except sqlite3.IntegrityError:
        return jsonify({'message': '이미 존재하는 사용자명입니다'}), 400
    except Exception as e:
        return jsonify({'message': f'회원가입 중 오류가 발생했습니다: {str(e)}'}), 500
    finally:
        conn.close()

@auth.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"message": "사용자명과 비밀번호를 모두 입력해주세요"}), 400
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('SELECT id, password, is_admin FROM users WHERE username = ?', (username,))
        user = c.fetchone()
        
        if user and user[1] == hash_password(password):
            token = create_token(user[0])
            return jsonify({
                'token': token,
                'is_admin': bool(user[2])
            }), 200
        return jsonify({'message': '잘못된 사용자명 또는 비밀번호입니다'}), 401
    finally:
        conn.close()

@auth.route('/account', methods=['GET'])
@token_required
def get_account():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('SELECT username, is_admin FROM users WHERE id = ?', (user_id,))
        user = c.fetchone()
        
        if not user:
            return jsonify({'message': '사용자를 찾을 수 없습니다'}), 404
            
        return jsonify({
            'username': user[0],
            'is_admin': bool(user[1])
        }), 200
    finally:
        conn.close()

@auth.route('/account', methods=['PUT'])
@token_required
def update_account():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    data = request.get_json()
    new_username = data.get('username')
    
    if not new_username:
        return jsonify({'message': '새로운 사용자명을 입력해주세요'}), 400
        
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('UPDATE users SET username = ? WHERE id = ?', 
                 (new_username, user_id))
        conn.commit()
        return jsonify({'message': '사용자명이 변경되었습니다'}), 200
    except sqlite3.IntegrityError:
        return jsonify({'message': '이미 존재하는 사용자명입니다'}), 400
    finally:
        conn.close()

@auth.route('/check_admin', methods=['GET'])
@token_required
def check_admin():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
        c.execute('SELECT is_admin FROM users WHERE id = ?', (user_id,))
        user = c.fetchone()
        
        if not user:
            return jsonify({'is_admin': False}), 200
            
        return jsonify({'is_admin': bool(user[0])}), 200
    finally:
        conn.close() 