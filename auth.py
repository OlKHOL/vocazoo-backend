from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from functools import wraps
from models import db, User
from werkzeug.security import generate_password_hash, check_password_hash
from config import get_config

auth = Blueprint('auth', __name__)
config = get_config()

def create_token(user_id):
    """사용자 ID로 JWT 토큰 생성"""
    expires_delta = timedelta(seconds=config.JWT_ACCESS_TOKEN_EXPIRES)
    token = create_access_token(
        identity=user_id,
        expires_delta=expires_delta
    )
    return token

def token_required(f):
    """토큰 필요한 API용 데코레이터"""
    @wraps(f)
    @jwt_required()
    def decorated(*args, **kwargs):
        try:
            user_id = get_jwt_identity()
            user = User.query.get(user_id)
            
            if not user:
                return jsonify({'error': '유효하지 않은 사용자입니다'}), 401
                
            return f(*args, **kwargs)
        except Exception as e:
            return jsonify({'error': str(e)}), 422
    return decorated

@auth.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"error": "사용자명과 비밀번호를 모두 입력해주세요"}), 400
    
    try:
        # 이미 존재하는 사용자인지 확인
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            return jsonify({'error': '이미 존재하는 사용자명입니다'}), 400
        
        # 비밀번호 해시화
        hashed_password = generate_password_hash(password)
        
        # 새 사용자 생성
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        
        # 토큰 생성
        token = create_token(new_user.id)
        
        return jsonify({
            'token': token,
            'message': '회원가입이 완료되었습니다'
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'회원가입 중 오류가 발생했습니다: {str(e)}'}), 500

@auth.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"error": "사용자명과 비밀번호를 모두 입력해주세요"}), 400
    
    try:
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            token = create_token(user.id)
            return jsonify({
                'token': token,
                'is_admin': bool(user.is_admin)
            }), 200
        return jsonify({'error': '잘못된 사용자명 또는 비밀번호입니다'}), 401
    except Exception as e:
        return jsonify({'error': f'로그인 중 오류가 발생했습니다: {str(e)}'}), 500

@auth.route('/account', methods=['GET'])
@token_required
def get_account():
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({'error': '사용자를 찾을 수 없습니다'}), 404
            
        return jsonify({
            'username': user.username,
            'is_admin': bool(user.is_admin)
        }), 200
    except Exception as e:
        return jsonify({'error': f'계정 정보 조회 중 오류가 발생했습니다: {str(e)}'}), 500

@auth.route('/account', methods=['PUT'])
@token_required
def update_account():
    user_id = get_jwt_identity()
    data = request.get_json()
    new_username = data.get('username')
    
    if not new_username:
        return jsonify({'error': '새로운 사용자명을 입력해주세요'}), 400
        
    try:
        # 이미 존재하는 사용자명인지 확인
        existing_user = User.query.filter_by(username=new_username).first()
        if existing_user and existing_user.id != user_id:
            return jsonify({'error': '이미 존재하는 사용자명입니다'}), 400
        
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': '사용자를 찾을 수 없습니다'}), 404
            
        user.username = new_username
        db.session.commit()
        return jsonify({'message': '사용자명이 변경되었습니다'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'계정 정보 업데이트 중 오류가 발생했습니다: {str(e)}'}), 500

@auth.route('/check_admin', methods=['GET'])
@token_required
def check_admin():
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({'error': '사용자를 찾을 수 없습니다'}), 404
            
        return jsonify({'is_admin': bool(user.is_admin)}), 200
    except Exception as e:
        return jsonify({'error': f'관리자 권한 확인 중 오류가 발생했습니다: {str(e)}'}), 500 