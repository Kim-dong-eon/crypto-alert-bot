import ccxt
import pandas as pd
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

def fetch_ohlcv(symbol, timeframe, limit=1000):
    """지정된 거래소에서 코인의 캔들(OHLCV) 데이터를 가져옵니다."""
    try:
        exchange_class = getattr(ccxt, settings.EXCHANGE_ID)
        
        # ★ 비트겟(Bitget) 또는 MEXC는 무기한 선물을 'swap'이라고 부릅니다.
        exchange = exchange_class({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap'  
            }
        })
        
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
    print(f"[{settings.EXCHANGE_ID}] {test_symbol} 차트 데이터 수집 테스트 중...\n")
    
    df_high = fetch_ohlcv(test_symbol, settings.TIMEFRAME_HIGH, limit=5)
    if df_high is not None:
        print(f"✅ 상위 프레임 ({settings.TIMEFRAME_HIGH}) 캔들 데이터:")
        print(df_high[['datetime', 'open', 'close']])
        print("-" * 40)
        
    df_low = fetch_ohlcv(test_symbol, settings.TIMEFRAME_LOW, limit=5)
    if df_low is not None:
        print(f"✅ 하위 프레임 ({settings.TIMEFRAME_LOW}) 캔들 데이터:")
        print(df_low[['datetime', 'open', 'close']])