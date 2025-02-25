import sqlite3

def make_admin():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    # 현재 사용자 확인
    print("\n현재 사용자 목록:")
    print("-------------------")
    c.execute('SELECT id, username, is_admin FROM users')
    users = c.fetchall()
    for user in users:
        print(f"ID: {user[0]}, 사용자명: {user[1]}, 어드민: {'예' if user[2] else '아니오'}")
    
    try:
        # 어드민으로 설정할 사용자 ID 입력 (정수로 변환)
        user_id = int(input("\n어드민으로 설정할 사용자 ID를 입력하세요: "))
        
        # 해당 사용자가 이미 어드민인지 확인
        c.execute('SELECT is_admin FROM users WHERE id = ?', (user_id,))
        result = c.fetchone()
        
        if not result:
            print("\n해당 ID의 사용자를 찾을 수 없습니다.")
            conn.close()
            return
            
        if result[0]:
            print("\n해당 사용자는 이미 어드민 권한을 가지고 있습니다.")
            conn.close()
            return
        
        # 해당 사용자를 어드민으로 설정
        c.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (user_id,))
        conn.commit()
        
        print("\n변경 후 사용자 목록:")
        print("-------------------")
        c.execute('SELECT id, username, is_admin FROM users')
        users = c.fetchall()
        for user in users:
            print(f"ID: {user[0]}, 사용자명: {user[1]}, 어드민: {'예' if user[2] else '아니오'}")
        
        print("\n변경이 완료되었습니다!")
    
    except ValueError:
        print("\n올바른 ID(숫자)를 입력해주세요.")
    finally:
        conn.close()

if __name__ == "__main__":
    make_admin() 