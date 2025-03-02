#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
기존 계정에 관리자 권한 부여 스크립트
사용법: python create_admin.py --username 사용자명
"""

import argparse
import os
import sys
from flask import Flask
from models import db, User
from config import get_config

def grant_admin_privileges(username):
    """기존 사용자에게 관리자 권한을 부여합니다."""
    # Flask 앱 초기화
    app = Flask(__name__)
    app.config.from_object(get_config())
    db.init_app(app)
    
    with app.app_context():
        # 기존 사용자 확인
        user = User.query.filter_by(username=username).first()
        
        if user:
            # 이미 관리자인지 확인
            if user.is_admin:
                print(f"사용자 '{username}'는 이미 관리자 권한을 가지고 있습니다.")
                return
                
            # 사용자에게 관리자 권한 부여
            user.is_admin = True
            db.session.commit()
            print(f"사용자 '{username}'에게 관리자 권한이 부여되었습니다.")
            print(f"관리자 정보: ID={user.id}, 사용자명={user.username}")
        else:
            print(f"오류: 사용자 '{username}'를 찾을 수 없습니다.")
            print("먼저 일반 회원가입을 통해 계정을 생성한 후 이 스크립트를 실행하세요.")
            sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="기존 계정에 관리자 권한 부여 도구")
    parser.add_argument("--username", required=True, help="관리자 권한을 부여할 사용자명")
    
    args = parser.parse_args()
    
    print(f"관리자 권한 부여 중: {args.username}")
    grant_admin_privileges(args.username) 