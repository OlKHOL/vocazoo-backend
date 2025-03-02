#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
기존 계정의 관리자 권한 박탈 스크립트
사용법: python revoke_admin.py --username 사용자명
"""

import argparse
import os
import sys
from flask import Flask
from models import db, User
from config import get_config

def revoke_admin_privileges(username):
    """기존 관리자 계정에서 관리자 권한을 박탈합니다."""
    # Flask 앱 초기화
    app = Flask(__name__)
    app.config.from_object(get_config())
    db.init_app(app)
    
    with app.app_context():
        # 기존 사용자 확인
        user = User.query.filter_by(username=username).first()
        
        if user:
            # 관리자인지 확인
            if not user.is_admin:
                print(f"사용자 '{username}'는 관리자 권한이 없습니다.")
                return
                
            # 사용자의 관리자 권한 박탈 및 레벨 조정
            user.is_admin = False
            user.level = 10  # 일반 사용자 레벨로 조정 (필요에 따라 변경 가능)
            
            db.session.commit()
            print(f"사용자 '{username}'의 관리자 권한이 박탈되었습니다.")
            print(f"사용자 정보: ID={user.id}, 사용자명={user.username}, 레벨={user.level}")
            print("이 계정은 이제 일반 사용자로 랭킹에 등재됩니다.")
        else:
            print(f"오류: 사용자 '{username}'를 찾을 수 없습니다.")
            sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="기존 계정의 관리자 권한 박탈 도구")
    parser.add_argument("--username", required=True, help="관리자 권한을 박탈할 사용자명")
    
    args = parser.parse_args()
    
    print(f"관리자 권한 박탈 중: {args.username}")
    revoke_admin_privileges(args.username) 