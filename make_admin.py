from models import db, User
from flask import Flask
import os

def create_app():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/vocazoo')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    return app

def make_admin():
    app = create_app()
    with app.app_context():
        # 현재 사용자 확인
        print("\n현재 사용자 목록:")
        print("-------------------")
        users = User.query.all()
        for user in users:
            print(f"ID: {user.id}, 사용자명: {user.username}, 어드민: {'예' if user.is_admin else '아니오'}")
        
        try:
            # 어드민으로 설정할 사용자 ID 입력 (정수로 변환)
            user_id = int(input("\n어드민으로 설정할 사용자 ID를 입력하세요: "))
            
            # 해당 사용자 조회
            user = User.query.get(user_id)
            
            if not user:
                print("\n해당 ID의 사용자를 찾을 수 없습니다.")
                return
                
            if user.is_admin:
                print("\n해당 사용자는 이미 어드민 권한을 가지고 있습니다.")
                return
            
            # 해당 사용자를 어드민으로 설정
            user.is_admin = True
            db.session.commit()
            
            print("\n변경 후 사용자 목록:")
            print("-------------------")
            users = User.query.all()
            for user in users:
                print(f"ID: {user.id}, 사용자명: {user.username}, 어드민: {'예' if user.is_admin else '아니오'}")
            
            print("\n변경이 완료되었습니다!")
        
        except ValueError:
            print("\n올바른 ID(숫자)를 입력해주세요.")
            db.session.rollback()

if __name__ == "__main__":
    make_admin() 