class LevelSystem:
    # 레벨별 필요 경험치 테이블
    LEVEL_EXP_TABLE = {
        range(1, 11): 30,    # 1-10
        range(11, 21): 60,   # 11-20
        range(21, 31): 90,   # 21-30
        range(31, 41): 150,  # 31-40
        range(41, 51): 250,  # 41-50
        range(51, 61): 400,  # 51-60
        range(61, 71): 600,  # 61-70
        range(71, 81): 900,  # 71-80
        range(81, 91): 1300, # 81-90
        range(91, 101): 1800 # 91-100
    }

    # 레벨별 획득 경험치 테이블
    LEVEL_GAIN_TABLE = {
        range(1, 11): 5,     # 1-10: 5exp
        range(11, 21): 8,    # 11-20: 8exp
        range(21, 31): 10,   # 21-30: 10exp
        range(31, 41): 12,   # 31-40: 12exp
        range(41, 51): 15,   # 41-50: 15exp
        range(51, 61): 20,   # 51-60: 20exp
        range(61, 71): 25,   # 61-70: 25exp
        range(71, 100): 30,  # 71-99: 30exp
    }

    @staticmethod
    def get_exp_for_level(level):
        """해당 레벨에서 다음 레벨로 가기 위해 필요한 경험치를 반환"""
        for level_range, exp in LevelSystem.LEVEL_EXP_TABLE.items():
            if level in level_range:
                return exp
        return None

    @staticmethod
    def calculate_test_exp(score, level):
        """테스트 점수와 현재 레벨을 기반으로 획득할 경험치 계산"""
        if level >= 100:  # 100레벨 이상은 경험치 획득 없음
            return 0
            
        # 레벨에 따른 기본 경험치 계산
        base_exp = 0
        for level_range, exp in LevelSystem.LEVEL_GAIN_TABLE.items():
            if level in level_range:
                base_exp = exp
                break
                
        return base_exp

    @staticmethod
    def process_exp_gain(current_level, current_exp, gained_exp):
        """경험치 획득 처리 및 레벨업 여부 확인"""
        if current_level >= 100:  # 최대 레벨
            return current_level, current_exp, False

        total_exp = current_exp + gained_exp
        level_up = False
        required_exp = LevelSystem.get_exp_for_level(current_level)

        while total_exp >= required_exp and current_level < 100:
            total_exp -= required_exp
            current_level += 1
            level_up = True
            required_exp = LevelSystem.get_exp_for_level(current_level)

        return current_level, total_exp, level_up

    @staticmethod
    def get_level_progress(level, exp):
        """현재 레벨에서의 진행도를 백분율로 반환"""
        if level >= 100:
            return 100
            
        required_exp = LevelSystem.get_exp_for_level(level)
        if required_exp is None:
            return 0
            
        return (exp / required_exp) * 100

    @staticmethod
    def check_badge_unlock(level):
        """새로운 뱃지 획득 여부 확인"""
        # 10레벨 단위로 뱃지 부여
        if level % 10 == 0:
            return f"level_{level}_badge"
        return None 