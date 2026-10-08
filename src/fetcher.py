import ccxt
import pandas as pd
import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

# ★ 1. 거래소 객체 1회 생성 및 속도 제한(RateLimit) 보호 켜기
exchange_class = getattr(ccxt, settings.EXCHANGE_ID)
exchange = exchange_class({
    'enableRateLimit': True,  # 거래소 차단 방지용 미세 간격 유지
    'options': {
        'defaultType': 'swap'
    }
})

# ★ 2. 스레드 실행 전 마켓 정보를 미리 딱 1번만 불러와서 중복 요청 폭주 방지
try:
    exchange.load_markets()
except Exception as e:
    print(f"⚠️ 마켓 초기 로딩 경고: {e}")

def fetch_ohlcv(symbol, timeframe, limit=100):
    """캔들 데이터를 가져오되, 429(요청 과다) 발생 시 자동으로 재시도합니다."""
    for attempt in range(3):  # 최대 3번까지 자동 재시도
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # 한국 시간(KST)으로 변환 (+9시간)
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms') + pd.Timedelta(hours=9)        
            return df
            
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(0.5 * (attempt + 1))  # 잠깐 숨 고르기 후 재시도
                continue
            print(f"❌ 데이터 수집 실패 ({symbol} - {timeframe}): {e}")
            return None