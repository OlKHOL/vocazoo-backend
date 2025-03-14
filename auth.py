from flask import Blueprint, request, jsonify
import hashlib
import jwt
from datetime import datetime, timedelta
from functools import wraps
from models import db, User
from werkzeug.security import generate_password_hash, check_password_hash
from config import get_config

auth = Blueprint('auth', __name__)
SECRET_KEY = "your-secret-key-here"  # 실제 배포시에는 환경변수로 관리
config = get_config()
SECRET_KEY = config.JWT_SECRET_KEY

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
            user = User.query.get(user_id)

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
    print("auth.register 함수 호출됨")
    data = request.get_json()
    print("요청 데이터:", data)
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        print("사용자명 또는 비밀번호 누락")
        return jsonify({"message": "사용자명과 비밀번호를 모두 입력해주세요"}), 400

    try:
        # 이미 존재하는 사용자인지 확인
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            print(f"이미 존재하는 사용자명: {username}")
            return jsonify({'message': '이미 존재하는 사용자명입니다'}), 400

        # 비밀번호 해시화
        hashed_password = generate_password_hash(password)

        # 새 사용자 생성
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        print(f"새 사용자 생성 성공: {username}, ID: {new_user.id}")

        # 토큰 생성
        token = create_token(new_user.id)

        # 응답 형식 수정
        response = jsonify({
            'token': token,
            'message': '회원가입이 완료되었습니다'
        })

        print("회원가입 응답:", response)
        return response, 201

    except Exception as e:
        db.session.rollback()
        print(f"회원가입 오류: {str(e)}")
        return jsonify({'message': f'회원가입 중 오류가 발생했습니다: {str(e)}'}), 500

@auth.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"message": "사용자명과 비밀번호를 모두 입력해주세요"}), 400

    try:
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            token = create_token(user.id)
            return jsonify({
                'token': token,
                'is_admin': bool(user.is_admin)
            }), 200
        return jsonify({'message': '잘못된 사용자명 또는 비밀번호입니다'}), 401
    except Exception as e:
        return jsonify({'message': f'로그인 중 오류가 발생했습니다: {str(e)}'}), 500

@auth.route('/account', methods=['GET'])
@token_required
def get_account():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)

    try:
        user = User.query.get(user_id)

        if not user:
            return jsonify({'message': '사용자를 찾을 수 없습니다'}), 404

        return jsonify({
            'username': user.username,
            'level': user.level,
            'exp': user.exp,
            'is_admin': bool(user.is_admin),
            'badges': user.badges if user.badges else [],
            'current_score': float(user.current_score) if user.current_score else 0
        }), 200
    except Exception as e:
        return jsonify({'message': f'계정 정보 조회 중 오류가 발생했습니다: {str(e)}'}), 500

@auth.route('/account', methods=['PUT'])
@token_required
def update_account():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)
    data = request.get_json()
    new_username = data.get('username')

    if not new_username:
        return jsonify({'message': '새로운 사용자명을 입력해주세요'}), 400

    try:
        # 이미 존재하는 사용자명인지 확인
        existing_user = User.query.filter_by(username=new_username).first()
        if existing_user and existing_user.id != user_id:
            return jsonify({'message': '이미 존재하는 사용자명입니다'}), 400

        user = User.query.get(user_id)
        if not user:
            return jsonify({'message': '사용자를 찾을 수 없습니다'}), 404

        user.username = new_username
        db.session.commit()
        return jsonify({'message': '사용자명이 변경되었습니다'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': f'계정 정보 업데이트 중 오류가 발생했습니다: {str(e)}'}), 500

@auth.route('/check_admin', methods=['GET'])
@token_required
def check_admin():
    token = request.headers.get('Authorization')
    user_id = verify_token(token)

    try:
        user = User.query.get(user_id)

        if not user:
            return jsonify({'is_admin': False}), 200

        return jsonify({'is_admin': bool(user.is_admin)}), 200
    except Exception as e:
        print(f"Error checking admin status: {str(e)}")
        return jsonify({'message': f'관리자 권한 확인 중 오류가 발생했습니다: {str(e)}'}), 500

@auth.route('/check', methods=['GET'])
def check_auth_status():
    token = request.headers.get('Authorization')
    
    if not token:
        return jsonify({'authenticated': False}), 200
        
    try:
        if token.startswith('Bearer '):
            token = token.split(' ')[1]
            
        user_id = verify_token(token)
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({'authenticated': False}), 200
            
        return jsonify({
            'authenticated': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'is_admin': user.is_admin,
                'level': user.level
            }
        }), 200
    except Exception as e:
        print(f"Auth check error: {str(e)}")
        return jsonify({'authenticated': False}), 200  # 에러가 나도 200 반환