import ccxt
import pandas as pd
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

# ★ 핵심: 함수 바깥에서 거래소 객체를 딱 1번만 생성하고 마켓 정보를 미리 불러옵니다.
exchange_class = getattr(ccxt, settings.EXCHANGE_ID)
exchange = exchange_class({
    'enableRateLimit': False,  # 병렬(동시) 수집 시 내부 대기 시간 제거 (24개 요청은 제한에 안 걸림)
    'options': {
        'defaultType': 'swap'  # 비트겟 USDT 무기한 선물
    }
})

def fetch_ohlcv(symbol, timeframe, limit=100):
    """이미 연결된 거래소 객체를 재사용하여 캔들 데이터를 초고속으로 가져옵니다."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 한국 시간(KST)으로 변환 (+9시간)
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms') + pd.Timedelta(hours=9)        
        return df
        
    except Exception as e:
        print(f"❌ 데이터 수집 실패 ({symbol} - {timeframe}): {e}")
        return None

if __name__ == "__main__":
    test_symbol = settings.SYMBOLS[0]
    tf_high, tf_low = settings.STRATEGY_PAIRS[0]
    print(f"[{settings.EXCHANGE_ID}] {test_symbol} 차트 데이터 수집 테스트 중...\n")
    
    df_high = fetch_ohlcv(test_symbol, tf_high, limit=5)
    if df_high is not None:
        print(f"✅ 상위 프레임 ({tf_high}) 캔들 데이터:")
        print(df_high[['datetime', 'open', 'close']])
        print("-" * 40)
        
    df_low = fetch_ohlcv(test_symbol, tf_low, limit=5)
    if df_low is not None:
        print(f"✅ 하위 프레임 ({tf_low}) 캔들 데이터:")
        print(df_low[['datetime', 'open', 'close']])