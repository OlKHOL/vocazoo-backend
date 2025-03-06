import time
from difflib import SequenceMatcher
import json
import random
from flask import jsonify, current_app
from datetime import datetime
from models import db, User, WordSet, TestResult, WrongAnswer

class TestState:
    def __init__(self, word_set_id=None):
        self.word_set_id = word_set_id
        self.word_list = []
        self.current_word = None
        self.score = 0
        self.start_time = None
        self.time_limit = 30  # 30초로 변경
        self.wrong_answers = []
        self.is_wrong_answers_test = False
        self.load_words()

    def load_words(self):
        if self.word_set_id == "wrong_answers":
            self.is_wrong_answers_test = True
            return
            
        try:
            with current_app.app_context():
                word_set = WordSet.query.get(self.word_set_id)
                if word_set:
                    self.word_list = word_set.words.copy()
                    random.shuffle(self.word_list)
        except Exception as e:
            print(f"Error loading words: {e}")

    def set_words(self, words):
        self.word_list = words.copy()
        random.shuffle(self.word_list)

    def start_test(self):
        self.start_time = time.time()

    def is_time_over(self):
        if not self.start_time:
            return False
        return time.time() - self.start_time > self.time_limit

    def get_next_question(self):
        if not self.word_list:
            return None
        
        self.current_word = self.word_list.pop(0)
        return {
            "english": self.current_word["english"]
        }

    def normalize_answer(self, answer):
        """특수문자와 불필요한 기호들을 제거하고 정규화"""
        # 제거할 문자들
        remove_chars = "()[]{}~·,에서로의을를이가은는과"
        for char in remove_chars:
            answer = answer.replace(char, " ")
        
        # 여러 개의 공백을 하나로 치환하고 앞뒤 공백 제거
        return " ".join(answer.split()).strip()

    def similar(self, a, b):
        """두 문자열의 유사도를 계산"""
        # 정규화된 문자열로 비교
        a = self.normalize_answer(a)
        b = self.normalize_answer(b)
        return SequenceMatcher(None, a, b).ratio()

    def check_answer(self, question, answer):
        if not self.current_word:
            return jsonify({"result": "invalid"}), 400

        # 사용자 답안 정규화
        user_answer = self.normalize_answer(answer.strip())
        
        # 쉼표로 구분된 여러 정답 처리
        correct_answers = [ans.strip() for ans in self.current_word["korean"].split(',')]
        
        # 각 정답과 비교
        for correct_answer in correct_answers:
            # 정규화된 정답
            norm_correct = self.normalize_answer(correct_answer)
            
            # 완전 일치하는 경우
            if user_answer == norm_correct:
                self.score += self.score+10
                return jsonify({
                    "result": "correct",
                    "message": "정답입니다!"
                }), 200
            
            # 유사도가 80% 이상인 경우
            similarity = self.similar(user_answer, norm_correct)
            if similarity >= 0.8:
                self.score == self.score+10
                return jsonify({
                    "result": "correct",
                    "message": f"유사한 답안이 인정되었습니다! (정확한 답: {correct_answer})"
                }), 200

        # 오답인 경우
        self.wrong_answers.append({
            "question": self.current_word["english"],
            "userAnswer": answer.strip(),
            "correctAnswer": self.current_word["korean"]
        })

        return jsonify({
            "result": "wrong",
            "message": f"오답입니다. 정답은 '{self.current_word['korean']}' 입니다.",
            "correct_answer": self.current_word["korean"]
        }), 200

    def save_result(self, user_id):
        try:
            with current_app.app_context():
                # Get user's level first
                user = User.query.get(user_id)
                if not user:
                    print(f"User {user_id} not found")
                    return
                
                # Only save results if user is level 5 or higher
                if user.level >= 5:
                    final_score = self.score
                    # 오답노트 테스트인 경우 오답을 저장하지 않음
                    wrong_answers_json = json.dumps(self.wrong_answers) if not self.is_wrong_answers_test else json.dumps([])
                    
                    # 결과 저장
                    test_result = TestResult(
                        user_id=user_id,
                        score=round(final_score, 2),
                        solved_count=len(self.wrong_answers),
                        wrong_answers=wrong_answers_json,
                        word_set_id=self.word_set_id,
                        completed_at=datetime.utcnow()
                    )
                    db.session.add(test_result)
                    
                    # 오답노트 테스트가 아닌 경우에만 점수 업데이트
                    if not self.is_wrong_answers_test:
                        user.current_score += round(final_score, 2)
                        user.completed_tests += 1
                    
                    db.session.commit()
            
        except Exception as e:
            db.session.rollback()
            print(f"Error saving test result: {e}")
            raise

    def get_final_score(self):
        elapsed_time = time.time() - self.start_time
        remaining_time = max(0, self.time_limit - elapsed_time)
        
        # 최종 점수 계산 시에만 보너스 배율 적용
        if remaining_time >= 5:
            return self.score * 1.5  # 5초 이상 남으면 1.5배
        elif remaining_time >= 3:
            return self.score * 1.2  # 3초 이상 남으면 1.2배
        return self.score  # 3초 미만이면 보너스 없음 