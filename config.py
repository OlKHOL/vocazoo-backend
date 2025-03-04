import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 기본 설정
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'vocazoo-secure-key-2024')
    JWT_ACCESS_TOKEN_EXPIRES = 30 * 24 * 60 * 60  # 30일로 통일
    
    # CORS 설정
    CORS_ORIGINS = [
        'http://localhost:3000',
        'http://127.0.0.1:3000',
        'http://vocazoo.co.kr',
        'https://vocazoo.co.kr',
        'https://www.vocazoo.co.kr',
        'http://www.vocazoo.co.kr',
        'https://api.vocazoo.co.kr'
    ]
    
    # CORS 추가 설정
    CORS_ALLOW_CREDENTIALS = True
    CORS_EXPOSE_HEADERS = ['Content-Type', 'Authorization']
    CORS_ALLOW_HEADERS = ['Content-Type', 'Authorization']
    CORS_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS']
    
    # 보안 설정
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True

class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.getenv('DEV_DATABASE_URL', 'postgresql://postgres:password@localhost:5432/vocazoo')
    JWT_COOKIE_SECURE = False
    JWT_COOKIE_SAMESITE = 'Lax'

class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.getenv('TEST_DATABASE_URL', 'postgresql://postgres:password@localhost:5432/vocazoo_test')
    JWT_COOKIE_SECURE = False
    JWT_COOKIE_SAMESITE = 'Lax'

class ProductionConfig(Config):
    DEBUG = False
    # 프로덕션 환경의 데이터베이스 연결
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'postgresql://postgres:password@svc.sel4.cloudtype.app:30648/vocazoo')
    # 내부 연결을 위한 데이터베이스 URI
    INTERNAL_DATABASE_URI = 'postgresql://postgres:password@postgresql:5432/vocazoo'
    JWT_COOKIE_SECURE = True
    JWT_COOKIE_SAMESITE = 'Strict'

def get_config():
    env = os.getenv('FLASK_ENV', 'development')
    config_map = {
        'development': DevelopmentConfig,
        'testing': TestingConfig,
        'production': ProductionConfig
    }
    return config_map.get(env, DevelopmentConfig) 