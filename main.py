import time
from config import settings
from src.fetcher import fetch_ohlcv
from src.indicators import add_indicators
from src.strategy import check_signals
from src.notifier import send_message

def main():
    print("🚀 다중 MTF 알림 봇 실행 시작...")
    
    for symbol in settings.SYMBOLS:
        print(f"\n🔍 [{symbol}] 분석 중...")
        
        # 3가지 전략 조합(1d-2h, 4h-15m, 1h-5m)을 순서대로 검사
        for tf_high, tf_low in settings.STRATEGY_PAIRS:
            df_high = fetch_ohlcv(symbol, tf_high, limit=1000)
            df_low = fetch_ohlcv(symbol, tf_low, limit=1000)
            
            if df_high is None or df_low is None:
                print(f"  -> ⚠️ {tf_high} & {tf_low} 데이터 로드 실패.")
                continue
                
            df_high = add_indicators(df_high)
            df_low = add_indicators(df_low)
            
            # strategy.py에 변경된 시간대 변수(tf_high, tf_low)를 함께 넘겨줌
            result = check_signals(symbol, df_high, df_low, tf_high, tf_low)
            
            if result["signal"]:
                print(f"  -> 🎯 타점 발견! ({tf_high} & {tf_low}) 텔레그램 발송")
                send_message(result["message"])
            else:
                print(f"  -> ⏳ {tf_high} & {tf_low} 조건 미달")
            
            time.sleep(1)
            
    print("\n✅ 모든 코인 분석 완료!")

if __name__ == "__main__":
    main()
