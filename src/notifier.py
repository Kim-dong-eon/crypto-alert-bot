import os
import requests
from dotenv import load_dotenv

# .env 파일의 환경변수 로드
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_message(text: str):
    """텔레그램 봇을 통해 지정된 방으로 메시지를 전송합니다."""
    if not TOKEN or not CHAT_ID:
        print("❌ 오류: 텔레그램 토큰이나 CHAT_ID가 설정되지 않았습니다.")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown" # 굵은 글씨 등 마크다운 서식 지원
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status() # 전송 실패 시 에러 발생
        print("✅ 텔레그램 메시지 발송 완료")
    except requests.exceptions.RequestException as e:
        print(f"❌ 텔레그램 발송 실패: {e}")

# 이 파일만 단독으로 실행했을 때 통신망 테스트
if __name__ == "__main__":
    test_msg = "🤖 *봇 테스트*\n통신망 연결이 정상적으로 완료되었습니다!"
    send_message(test_msg)