import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_message(text: str):
    """텔레그램 봇을 통해 텍스트 메시지를 전송합니다."""
    if not TOKEN or not CHAT_ID:
        print("❌ 오류: 텔레그램 토큰이나 CHAT_ID가 설정되지 않았습니다.")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        print("✅ 텔레그램 텍스트 발송 완료")
    except requests.exceptions.RequestException as e:
        print(f"❌ 텔레그램 메시지 발송 실패: {e}")


def send_photo(photo_path: str, caption: str = ""):
    """텔레그램 봇을 통해 차트 이미지(PNG)와 설명을 전송합니다."""
    if not TOKEN or not CHAT_ID:
        print("❌ 오류: 텔레그램 토큰이나 CHAT_ID가 설정되지 않았습니다.")
        return

    if not os.path.exists(photo_path):
        print(f"❌ 오류: 이미지 파일이 존재하지 않습니다 ({photo_path})")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as img_file:
            files = {"photo": img_file}
            data = {"chat_id": CHAT_ID, "caption": caption}
            response = requests.post(url, data=data, files=files, timeout=20)
            response.raise_for_status()
        print(f"✅ 텔레그램 이미지 발송 완료 ({photo_path})")
    except Exception as e:
        print(f"❌ 텔레그램 이미지 발송 실패 ({photo_path}): {e}")