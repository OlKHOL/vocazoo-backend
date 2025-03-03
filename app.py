from flask import Flask, jsonify
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from models import db
from auth import auth
from config import get_config
import os

def create_app():
    app = Flask(__name__)
    config = get_config()
    app.config.from_object(config)
    
    # CORS 설정
    CORS(app, resources={r"/*": {"origins": config.CORS_ORIGINS}})
    
    # JWT 설정
    jwt = JWTManager(app)
    
    # 데이터베이스 초기화
    # 내부 연결이 필요한 경우 INTERNAL_DATABASE_URI 사용
    if os.getenv('USE_INTERNAL_DB', 'false').lower() == 'true':
        app.config['SQLALCHEMY_DATABASE_URI'] = config.INTERNAL_DATABASE_URI
    
    db.init_app(app)
    
    # 블루프린트 등록
    app.register_blueprint(auth, url_prefix='/auth')
    
    # 에러 핸들러
    @app.errorhandler(401)
    def unauthorized(error):
        return jsonify({'error': '인증이 필요합니다'}), 401
        
    @app.errorhandler(403)
    def forbidden(error):
        return jsonify({'error': '접근 권한이 없습니다'}), 403
        
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': '요청한 리소스를 찾을 수 없습니다'}), 404
        
    @app.errorhandler(422)
    def validation_error(error):
        return jsonify({'error': '유효하지 않은 요청입니다'}), 422
        
    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': '서버 내부 오류가 발생했습니다'}), 500
    
    return app

if __name__ == '__main__':
    app = create_app()
    # 내부 연결을 위한 호스트 설정
    host = '0.0.0.0' if os.getenv('USE_INTERNAL_DB', 'false').lower() == 'true' else '127.0.0.1'
    app.run(host=host, debug=True)